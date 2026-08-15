"""
test_audit_high_v2_regression.py — اختبارات regression للإصلاحات (تدقيق Codex).

يغطّي السلوك المصحَّح بعد جولة Codex الثانية:
  ② المقارنة لا تُبنى/تُعرَض من قوائم مالية ناقصة  (analysis.build_quick_summary + علَم complete)
  ③ ROE لا يُعرض موجباً مضلِّلاً عند حقوق ملكية سالبة  (analysis + screener metrics)
  ④ تمييز فشل الجلب عن النجاح-الفارغ للأرباح/الأسهم الحرة:
     - فشل (None) → نُبقي القيم السليمة السابقة.
     - نجاح-فارغ ({}) → نُحدّث فعلاً (تُمسح المواعيد المنتهية).
  ⑤ تمييز فشل EDGAR (None) عن النجاح-الفارغ ([]):
     - فشل → نُبقي كاش المطلعين السابق.
     - نجاح-فارغ → نخزّن [] (تعكس الواقع الصحيح).
  ⑥ التنبيه السعري لا يُطفأ إلا عند نجاح إرسال تلغرام  (screener.check_price_alerts)
  ⑦ التحديث الليلي يكتمل بتحقّق فعلي من تحديث كل رموز UNIVERSE، لا بمجرّد عدّاد دفعات،
     ولا يدخل حلقة لا نهائية مع مصدر بطيء/متعطّل  (scheduler._auto_refresh)

(المشكلة ① حماية FMP المركزية — مُصلَحة أصلاً عبر _reserve_atomic؛ لا مسار يتجاوز _get.)

التشغيل:  python tests/test_audit_high_v2_regression.py
"""

import os
import sys
import copy
import json
import tempfile
import requests
from datetime import datetime, timezone, timedelta

os.environ["APP_PASSWORD"] = "regress-v2-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import (fmp_client, finnhub_client, edgar_client, news_client,  # noqa: E402
                      indicators, scoring, screener, analysis, radar, telegram_client)
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockCache, AppSetting, PriceAlert  # noqa: E402

# مراجع للدوال الأصلية قبل أي stub (لاختبار السلوك الحقيقي لطبقة العميل)
_REAL_INSIDER = edgar_client.get_insider_transactions

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


# قوائم كاملة فعلياً
FULL = {
    "income": [{"netIncome": 10, "revenue": 100, "grossProfit": 40, "weightedAverageShsOut": 50,
                "operatingIncome": 20, "eps": 2},
               {"netIncome": 8, "revenue": 90, "grossProfit": 35, "weightedAverageShsOut": 50}],
    "balance": [{"totalAssets": 200, "totalCurrentAssets": 80, "totalCurrentLiabilities": 40,
                 "longTermDebt": 30, "totalStockholdersEquity": 120},
                {"totalAssets": 190, "totalCurrentAssets": 75, "totalCurrentLiabilities": 38,
                 "longTermDebt": 35}],
    "cashflow": [{"operatingCashFlow": 25}],
}


def _stub_heavy():
    for n in ["money_flow", "reversal_pattern", "market_structure", "multi_timeframe",
              "squeeze_breakout", "golden_cross", "trend_pullback", "atr", "resistance_warning",
              "fibonacci_levels", "volume_profile", "sustained_breakout"]:
        setattr(indicators, n, lambda *a, **k: None)
    indicators.build_indicators = lambda c: ([{"label": "EMA"}] if c else [])
    scoring.break_status = lambda *a, **k: None
    scoring.atr_trade_plan = lambda *a, **k: None
    scoring.piotroski_score = lambda f: {"score": 5, "computable": 9, "components": []}
    scoring.catalyst_score = lambda f: {"score": 50}
    finnhub_client.get_quote = lambda *a, **k: None
    edgar_client.get_insider_transactions = lambda *a, **k: []
    analysis.price_chart = lambda *a, **k: None
    screener._save_price_history = lambda *a, **k: None
    screener._period_return = lambda *a, **k: 0.0


# ==================== ③ ROE مع حقوق ملكية سالبة ====================
def test_roe_negative_equity():
    print("\n[③] ROE لا يُعرض موجباً مضلِّلاً عند حقوق ملكية سالبة:")
    _stub_heavy()
    CAND = [{"date": f"2026-01-{i+1:02d}", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}
            for i in range(60)]
    # خسارة (netIncome سالب) + حقوق ملكية سالبة → القسمة تعطي موجباً خادعاً
    NEG = copy.deepcopy(FULL)
    NEG["income"][0]["netIncome"] = -10
    NEG["balance"][0]["totalStockholdersEquity"] = -50

    # (أ) build_quick_summary
    fmp_client.get_quote = lambda t: {"price": 100, "name": "X"}
    fmp_client.get_financials = lambda t, years=2: copy.deepcopy(NEG)
    check(analysis.build_quick_summary("X")["metrics"]["roe"] is None,
          "build_quick_summary: equity سالب → roe=None (لا نسبة موجبة خادعة)")
    fmp_client.get_financials = lambda t, years=2: copy.deepcopy(FULL)
    check(analysis.build_quick_summary("X")["metrics"]["roe"] is not None,
          "build_quick_summary: equity موجب → roe يُحسب طبيعياً")

    # (ب) build_stock_report
    fmp_client.get_profile = lambda t: {"sector": "T", "name": "X"}
    fmp_client.get_historical_prices = lambda t, limit=250: CAND
    fmp_client.get_financials = lambda t: copy.deepcopy(NEG)
    check(analysis.build_stock_report("X")["metrics"]["roe"] is None,
          "build_stock_report: equity سالب → roe=None")

    # (ج) screener._build_record
    fmp_client.get_historical_prices = lambda t, limit=5000: CAND
    fmp_client.get_financials = lambda t: copy.deepcopy(NEG)
    check(screener._build_record("X")["metrics"]["roe"] is None,
          "screener._build_record: equity سالب → metrics.roe=None")


# ==================== ② المقارنة من قوائم ناقصة + علَم complete ====================
def test_compare_incomplete():
    print("\n[②] المقارنة لا تُبنى/تُعرَض من قوائم مالية ناقصة:")
    fmp_client.get_quote = lambda t: {"price": 100, "name": "X"}
    # قوائم ناقصة (تدفق نقدي مفقود) → لا مقاييس/درجات
    PART = copy.deepcopy(FULL); PART["cashflow"] = None
    fmp_client.get_financials = lambda t, years=2: PART
    r = analysis.build_quick_summary("X")
    check(all(v is None for v in r["metrics"].values()), "قوائم ناقصة → كل المقاييس None")
    check(r["piotroski"]["score"] is None and r["catalyst"]["score"] is None,
          "قوائم ناقصة → Piotroski/Catalyst score=None (لا درجات جزئية)")
    check(r.get("complete") is False, "قوائم ناقصة → complete=False (القالب يُخفي الدرجات)")
    check(r["price"] == 100, "السعر يبقى معروضاً (من الاقتباس)")
    # قوائم كاملة → المقاييس والدرجات تظهر + complete=True
    fmp_client.get_financials = lambda t, years=2: copy.deepcopy(FULL)
    r2 = analysis.build_quick_summary("X")
    check(r2["metrics"]["roa"] is not None and r2["catalyst"]["score"] is not None,
          "قوائم كاملة → المقاييس والدرجات تظهر طبيعياً")
    check(r2.get("complete") is True, "قوائم كاملة → complete=True")


# ==================== ④ الأرباح/الأسهم الحرة: فشل مقابل نجاح-فارغ ====================
def test_earnings_float_source_semantics():
    print("\n[④] تمييز فشل الجلب عن النجاح-الفارغ للأرباح/الأسهم الحرة:")

    # (أ) طبقة عميل FMP: None (فشل) / [] (نجاح-فارغ) / قائمة (نجاح)
    _orig_get = fmp_client._get
    fmp_client._get = lambda *a, **k: None
    check(fmp_client.get_earnings_calendar("2026-01-01", "2026-01-10") is None,
          "get_earnings_calendar: فشل _get → None")
    check(fmp_client.get_shares_float_all() is None, "get_shares_float_all: فشل _get → None")
    fmp_client._get = lambda *a, **k: []
    check(fmp_client.get_earnings_calendar("2026-01-01", "2026-01-10") == [],
          "get_earnings_calendar: نجاح بقائمة فارغة → [] (لا None)")
    check(fmp_client.get_shares_float_all() == [], "get_shares_float_all: نجاح فارغ → []")
    fmp_client._get = lambda *a, **k: [{"symbol": "AAPL", "date": "2026-01-05"}]
    check(fmp_client.get_earnings_calendar("2026-01-01", "2026-01-10") == [{"symbol": "AAPL", "date": "2026-01-05"}],
          "get_earnings_calendar: نجاح ببيانات → القائمة كما هي")
    fmp_client._get = _orig_get

    # (ب) طبقة الخرائط في screener: None (فشل) / {} (نجاح-فارغ) / خريطة (نجاح)
    fmp_client.get_earnings_calendar = lambda f, t: None
    check(screener._upcoming_earnings() is None, "_upcoming_earnings: فشل المصدر → None")
    fmp_client.get_earnings_calendar = lambda f, t: []
    check(screener._upcoming_earnings() == {}, "_upcoming_earnings: نجاح-فارغ → {} (لا None)")
    fmp_client.get_shares_float_all = lambda: None
    check(screener._shares_float_map() is None, "_shares_float_map: فشل المصدر → None")
    fmp_client.get_shares_float_all = lambda: []
    check(screener._shares_float_map() == {}, "_shares_float_map: نجاح-فارغ → {}")


def _prep_refresh_stub():
    """يهيّئ refresh_cache لسهم واحد ببيانات كاملة (لاختبارات ④)."""
    screener._refresh_spy_history = lambda *a, **k: None
    screener._benchmark_return = lambda *a, **k: None
    screener.is_golden = lambda r: False
    screener._record_signal = lambda *a, **k: None
    screener.UNIVERSE = ["AAPL"]
    screener._build_record = lambda t: {"_complete": True, "ticker": t, "price": 100,
                                        "piotroski": None, "catalyst": None, "atr": None,
                                        "squeeze_breakout": False, "golden_cross": None,
                                        "trend_pullback": False, "break_status": None,
                                        "indicators": [{}], "days_to_earnings": None}


def test_earnings_float_preserve_on_failure():
    print("\n[④] فشل الجلب الجماعي → القيم السليمة السابقة محفوظة:")
    _prep_refresh_stub()
    # فشل الجلب = None (لا {} — تلك أصبحت «نجاح-فارغ»)
    screener._upcoming_earnings = lambda: None
    screener._shares_float_map = lambda: None
    OLD = {"ticker": "AAPL", "earnings_date": "2026-09-01", "days_to_earnings": 5,
           "float_shares": 1234, "free_float_pct": 80.0}
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=screener._PREFIX + "AAPL",
                                    data_json=json.dumps(OLD), updated_at=yest))
        db.session.merge(AppSetting(key=fmp_client._usage_key(), value="0"))
        db.session.commit()
        screener.refresh_cache()
        stored = json.loads(db.session.get(StockCache, screener._PREFIX + "AAPL").data_json)
    check(stored.get("earnings_date") == "2026-09-01",
          "فشل جلب الأرباح (None) → earnings_date السليم محفوظ (لم يُمسح)")
    check(stored.get("float_shares") == 1234 and stored.get("free_float_pct") == 80.0,
          "فشل جلب الأسهم الحرة (None) → القيم السليمة محفوظة (لم تُمسح)")


def test_earnings_float_clear_on_success_empty():
    print("\n[④] نجاح-فارغ → المواعيد المنتهية تُمسح (لا تُحمل قديمة خاطئة):")
    _prep_refresh_stub()
    # نجاح الجلب بلا نتائج لأسهمنا = {} (يجب ألا نحمل موعداً منتهياً قديماً)
    screener._upcoming_earnings = lambda: {}
    screener._shares_float_map = lambda: {}
    STALE = {"ticker": "AAPL", "earnings_date": "2020-01-01", "days_to_earnings": -900,
             "float_shares": 999, "free_float_pct": 10.0}
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=screener._PREFIX + "AAPL",
                                    data_json=json.dumps(STALE), updated_at=yest))
        db.session.merge(AppSetting(key=fmp_client._usage_key(), value="0"))
        db.session.commit()
        screener.refresh_cache()
        stored = json.loads(db.session.get(StockCache, screener._PREFIX + "AAPL").data_json)
    check(stored.get("earnings_date") is None and stored.get("days_to_earnings") is None,
          "نجاح-فارغ للأرباح → الموعد المنتهي القديم مُسِح (لم يُحمَل)")
    check(stored.get("float_shares") is None and stored.get("free_float_pct") is None,
          "نجاح-فارغ للأسهم الحرة → القيم القديمة لم تُحمَل (تحدّثت فعلاً)")


# ==================== ⑤ بيانات المطلعين: فشل EDGAR مقابل نجاح-فارغ ====================
def test_edgar_source_semantics():
    print("\n[⑤] EDGAR يُميّز الفشل (None) عن النجاح-الفارغ ([]):")
    # تعذّر تحديد CIK = فشل لا «لا-معاملات» → None (بلا شبكة)
    _orig_cik = edgar_client.get_cik
    edgar_client.get_cik = lambda t: None
    check(_REAL_INSIDER("ZZZZ") is None,
          "get_insider_transactions: لا CIK → None (فشل، لا [])")
    edgar_client.get_cik = _orig_cik


def test_edgar_preserve_on_failure():
    print("\n[⑤] فشل EDGAR (None) → كاش المطلعين السليم محفوظ:")
    radar.UNIVERSE = ["AAPL"]
    GOOD_TX = [{"name": "Insider A", "code": "P", "shares": 1000, "date": "2026-08-01"}]
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=radar._PREFIX + "AAPL",
                                    data_json=json.dumps(GOOD_TX), updated_at=yest))
        db.session.commit()
        # فشل EDGAR = None (لا [])
        edgar_client.get_insider_transactions = lambda *a, **k: None
        radar.refresh_radar()
        row = db.session.get(StockCache, radar._PREFIX + "AAPL")
        preserved = json.loads(row.data_json)
    check(preserved == GOOD_TX and row.updated_at.date() == yest.date(),
          "EDGAR يعيد None → البيانات السليمة محفوظة (لم تُمسح، لم يُعلَّم محدثاً)")


def test_edgar_store_on_success_empty():
    print("\n[⑤] نجاح EDGAR بلا معاملات ([]) → يُخزَّن الواقع الصحيح:")
    radar.UNIVERSE = ["AAPL"]
    GOOD_TX = [{"name": "Insider A", "code": "P", "shares": 1000, "date": "2026-08-01"}]
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=radar._PREFIX + "AAPL",
                                    data_json=json.dumps(GOOD_TX), updated_at=yest))
        db.session.commit()
        # نجاح فعلي بلا معاملات = [] (المطلع باع كل شيء/لا نشاط حديث) → يُخزَّن []
        edgar_client.get_insider_transactions = lambda *a, **k: []
        radar.refresh_radar()
        stored = json.loads(db.session.get(StockCache, radar._PREFIX + "AAPL").data_json)
    check(stored == [], "EDGAR ينجح بلا معاملات → يُخزَّن [] (يعكس الواقع، لا يُبقي قديماً)")
    # نجاح ببيانات جديدة → تُحدَّث (نُعيد الطابع الزمني للأمس حتى لا يتخطّاه «محدَّث اليوم»)
    NEW_TX = [{"name": "Insider B", "code": "P", "shares": 5000, "date": "2026-08-10"}]
    with app.app_context():
        row = db.session.get(StockCache, radar._PREFIX + "AAPL")
        row.updated_at = yest
        db.session.commit()
        edgar_client.get_insider_transactions = lambda *a, **k: NEW_TX
        radar.refresh_radar()
        updated = json.loads(db.session.get(StockCache, radar._PREFIX + "AAPL").data_json)
    check(updated == NEW_TX, "EDGAR ينجح ببيانات → تُحدَّث طبيعياً")


# ========== ⑤-HTTP فحص نجاح HTTP صراحةً في EDGAR (4xx/5xx فشل لا نجاح فارغ) ==========
_SUB_JSON = {"filings": {"recent": {
    "form": ["4"],
    "accessionNumber": ["0001-23-456789"],
    "primaryDocument": ["xslF345X05/wf-form4.xml"],
}}}
_VALID_EMPTY_XML = (
    '<?xml version="1.0"?><ownershipDocument>'
    '<reportingOwner><reportingOwnerId><rptOwnerName>Owner X</rptOwnerName></reportingOwnerId>'
    '<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>'
    '</reportingOwner></ownershipDocument>'
)
_VALID_TX_XML = (
    '<?xml version="1.0"?><ownershipDocument>'
    '<reportingOwner><reportingOwnerId><rptOwnerName>Owner X</rptOwnerName></reportingOwnerId>'
    '<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>'
    '</reportingOwner><nonDerivativeTable><nonDerivativeTransaction>'
    '<transactionDate><value>2026-08-01</value></transactionDate>'
    '<transactionCoding><transactionCode>P</transactionCode></transactionCoding>'
    '<transactionAmounts>'
    '<transactionShares><value>100</value></transactionShares>'
    '<transactionPricePerShare><value>10.5</value></transactionPricePerShare>'
    '<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>'
    '</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'
)


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("ليست JSON")
        return self._json


def _router(sub_resp, doc_resp):
    def _g(url, **kw):
        return sub_resp if "submissions" in url else doc_resp
    return _g


# سجل إيداعات فيه نموذجا Form 4 (لاختبار الفشل الجزئي: نجاح واحد + فشل آخر)
_SUB_JSON_2 = {"filings": {"recent": {
    "form": ["4", "4"],
    "accessionNumber": ["0001-23-000001", "0001-23-000002"],
    "primaryDocument": ["xslF345X05/a.xml", "xslF345X05/b.xml"],
}}}

# رد 200 لكن محتواه HTML (صفحة خطأ) — XML سليم الشكل لكنه ليس Form 4
_HTML_200 = "<html><body>SEC error page — not a form</body></html>"


def _router_multi(sub_resp, doc_map):
    """يوجّه طلب سجل الإيداعات لـ sub_resp، وكل نموذج حسب مفتاح يظهر في رابطه."""
    def _g(url, **kw):
        if "submissions" in url:
            return sub_resp
        for key, resp in doc_map.items():
            if key in url:
                return resp
        raise AssertionError("رابط غير متوقّع: " + url)
    return _g


def _run_insider_with(get_fn):
    _orig_get = edgar_client.requests.get
    _orig_cik = edgar_client.get_cik
    edgar_client.get_cik = lambda t: "0000000001"
    edgar_client.requests.get = get_fn
    try:
        return _REAL_INSIDER("AAPL", max_filings=2, max_rows=5)
    finally:
        edgar_client.requests.get = _orig_get
        edgar_client.get_cik = _orig_cik


def test_edgar_http_semantics():
    print("\n[⑤-HTTP] EDGAR يفحص نجاح HTTP صراحةً (4xx/5xx فشل لا نجاح فارغ):")
    # HTTP 500 على سجل الإيداعات → فشل (None) — لا يصل لمسار النجاح الفارغ
    check(_run_insider_with(_router(_FakeResp(500, text="err"), _FakeResp(200))) is None,
          "HTTP 500 على سجل الإيداعات → None (فشل، لا [])")

    # فشل اتصال (RequestException) → فشل
    def _raise(url, **kw):
        raise requests.RequestException("انقطاع اتصال")
    check(_run_insider_with(_raise) is None, "فشل الاتصال → None (فشل)")

    # HTTP 200 لكن استجابة سجل الإيداعات ليست JSON صالحة → فشل
    check(_run_insider_with(_router(_FakeResp(200, json_data=None, text="<html>"),
                                    _FakeResp(200))) is None,
          "استجابة سجل الإيداعات غير صالحة (ليست JSON) → None")

    # HTTP 500 على تحميل النموذج → فشل (لا يُحسب نجاحاً فارغاً)
    check(_run_insider_with(_router(_FakeResp(200, _SUB_JSON), _FakeResp(500, text="err"))) is None,
          "HTTP 500 على تحميل النموذج → None (فشل)")

    # XML غير صالح على النموذج → فشل تحليل (None) لا []
    check(_run_insider_with(_router(_FakeResp(200, _SUB_JSON), _FakeResp(200, text="<broken"))) is None,
          "XML غير صالح → None (فشل تحليل، لا نجاح فارغ)")

    # HTTP ناجح + نموذج صحيح بلا معاملات غير مشتقّة → [] (نجاح فارغ حقيقي)
    check(_run_insider_with(_router(_FakeResp(200, _SUB_JSON),
                                    _FakeResp(200, text=_VALID_EMPTY_XML))) == [],
          "HTTP ناجح + نموذج بلا معاملات → [] (نجاح فارغ)")

    # HTTP ناجح + نموذج بمعاملة → قائمة بمعاملة واحدة (نجاح ببيانات)
    r = _run_insider_with(_router(_FakeResp(200, _SUB_JSON), _FakeResp(200, text=_VALID_TX_XML)))
    check(isinstance(r, list) and len(r) == 1 and r[0].get("code") == "P",
          "HTTP ناجح + نموذج بمعاملة → قائمة بمعاملة واحدة (code=P)")


def test_edgar_partial_failure_and_invalid_content():
    print("\n[⑤-جزئي] فشل نموذج واحد لا يُعامَل كنجاح جزئي + محتوى 200 غير صالح:")

    # (أ) الفشل الجزئي: نموذج ينجح بمعاملة وآخر يفشل بـHTTP 500 → النتيجة كلّها فشل (None)،
    #     لا نتيجة جزئية بمعاملة النموذج الناجح.
    r = _run_insider_with(_router_multi(_FakeResp(200, _SUB_JSON_2), {
        "a.xml": _FakeResp(200, text=_VALID_TX_XML),  # نجح بمعاملة
        "b.xml": _FakeResp(500, text="خطأ خادم"),      # فشل HTTP
    }))
    check(r is None, "نجاح نموذج + فشل HTTP بآخر → None (لا نتيجة جزئية)")

    # الفشل الجزئي عبر XML غير صالح بالنموذج الثاني → فشل كامل (None)
    r = _run_insider_with(_router_multi(_FakeResp(200, _SUB_JSON_2), {
        "a.xml": _FakeResp(200, text=_VALID_TX_XML),
        "b.xml": _FakeResp(200, text="<broken"),       # XML غير صالح
    }))
    check(r is None, "نجاح نموذج + XML غير صالح بآخر → None (لا نتيجة جزئية)")

    # الفشل الجزئي عبر فشل اتصال بالنموذج الثاني → فشل كامل (None)
    def _get_partial_conn(url, **kw):
        if "submissions" in url:
            return _FakeResp(200, _SUB_JSON_2)
        if "a.xml" in url or "000123000001" in url:
            return _FakeResp(200, text=_VALID_TX_XML)
        raise requests.RequestException("انقطاع على النموذج الثاني")
    r = _run_insider_with(_get_partial_conn)
    check(r is None, "نجاح نموذج + فشل اتصال بآخر → None (لا نتيجة جزئية)")

    # (ب) HTTP 200 بمحتوى HTML (XML سليم الشكل لكنه ليس Form 4) → فشل (None) لا نجاح فارغ []
    r = _run_insider_with(_router(_FakeResp(200, _SUB_JSON), _FakeResp(200, text=_HTML_200)))
    check(r is None, "HTTP 200 بمحتوى HTML (ليس Form 4) → None (لا يتحوّل إلى [] فارغة)")

    # ضمان عدم الانحدار: نجاح كل النماذج المطلوبة كـForm 4 صحيحة بلا معاملات → [] (نجاح فارغ)
    r = _run_insider_with(_router_multi(_FakeResp(200, _SUB_JSON_2), {
        "a.xml": _FakeResp(200, text=_VALID_EMPTY_XML),
        "b.xml": _FakeResp(200, text=_VALID_EMPTY_XML),
    }))
    check(r == [], "كل النماذج Form 4 صحيحة بلا معاملات → [] (نجاح فارغ حقيقي)")


def test_edgar_error_json_body():
    print("\n[⑤-JSON] رد 200 بـJSON صحيح نحوياً لكنه رسالة خطأ/بنية ناقصة → فشل (None):")

    # (أ) طبقة العميل: 200 + {"error": ...} JSON صالح نحوياً لكنه رسالة خطأ → None
    check(_run_insider_with(_router(_FakeResp(200, {"error": "temporary upstream failure"}),
                                    _FakeResp(200))) is None,
          "HTTP 200 + JSON رسالة خطأ → None (لا يتحوّل إلى نجاح فارغ)")

    # بنية بلا filings.recent → فشل
    check(_run_insider_with(_router(_FakeResp(200, {"foo": "bar"}), _FakeResp(200))) is None,
          "HTTP 200 + JSON بلا filings.recent → None")

    # حقول متوازية غير متوافقة الطول (بنية غير صحيحة) → فشل
    check(_run_insider_with(_router(_FakeResp(200, {"filings": {"recent": {
              "form": ["4", "4"], "accessionNumber": ["x"], "primaryDocument": ["y", "z"]}}}),
              _FakeResp(200))) is None,
          "HTTP 200 + حقول غير متوافقة الطول → None")

    # استجابة ليست قاموساً أصلاً (JSON قائمة) → فشل
    check(_run_insider_with(_router(_FakeResp(200, ["not", "a", "dict"]), _FakeResp(200))) is None,
          "HTTP 200 + JSON ليس قاموساً → None")

    # ضمان عدم الانحدار: بنية SEC سليمة فعلاً بلا نماذج Form 4 → [] (نجاح فارغ حقيقي)
    check(_run_insider_with(_router(_FakeResp(200, {"filings": {"recent": {
              "form": [], "accessionNumber": [], "primaryDocument": []}}}),
              _FakeResp(200))) == [],
          "HTTP 200 + بنية سليمة بلا Form 4 → [] (نجاح فارغ حقيقي)")

    # (ب) end-to-end عبر radar: رد خطأ من EDGAR → الكاش السليم محفوظ (لا يُمسح بقائمة فارغة)
    radar.UNIVERSE = ["AAPL"]
    GOOD_TX = [{"name": "Insider A", "code": "P", "shares": 1000, "date": "2026-08-01"}]
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=radar._PREFIX + "AAPL",
                                    data_json=json.dumps(GOOD_TX), updated_at=yest))
        db.session.commit()
        _orig_get = edgar_client.requests.get
        _orig_cik = edgar_client.get_cik
        _orig_insider = edgar_client.get_insider_transactions
        edgar_client.get_cik = lambda t: "0000000001"
        edgar_client.get_insider_transactions = _REAL_INSIDER  # نمرّ بالدالة الحقيقية
        edgar_client.requests.get = _router(
            _FakeResp(200, {"error": "temporary upstream failure"}), _FakeResp(200))
        try:
            radar.refresh_radar()
        finally:
            edgar_client.requests.get = _orig_get
            edgar_client.get_cik = _orig_cik
            edgar_client.get_insider_transactions = _orig_insider
        row = db.session.get(StockCache, radar._PREFIX + "AAPL")
        preserved = json.loads(row.data_json)
    check(preserved == GOOD_TX and row.updated_at.date() == yest.date(),
          "radar: رد خطأ 200 من EDGAR → الكاش السليم محفوظ (لم يُمسح)")


# ==================== ⑥ التنبيه السعري وإرسال تلغرام ====================
def test_price_alert_telegram():
    print("\n[⑥] التنبيه السعري لا يُطفأ إلا عند نجاح إرسال تلغرام:")
    screener.load_records = lambda: ([{"ticker": "AAPL", "price": 50.0}], None)

    def run_with(send_result):
        with app.app_context():
            PriceAlert.query.delete()
            a = PriceAlert(ticker="AAPL", direction="below", target_price=60.0,
                           user_id="admin", active=True)
            db.session.add(a); db.session.commit()
            aid = a.id
            telegram_client.notify_price_alert = lambda *args, **kw: send_result
            screener.check_price_alerts()
            return db.session.get(PriceAlert, aid).active

    check(run_with(False) is True,
          "فشل إرسال تلغرام → التنبيه يبقى نشطاً (لا يُفقَد، يُعاد لاحقاً)")
    check(run_with(True) is False,
          "نجاح إرسال تلغرام → التنبيه يُطفأ (مرة واحدة)")


# ==================== ⑦ اكتمال التحديث الليلي (تحقّق فعلي، بلا حلقة لا نهائية) ====================
def _mark_fresh(sym):
    now = datetime.now(timezone.utc)
    db.session.merge(StockCache(ticker=screener._PREFIX + sym, data_json="{}", updated_at=now))
    db.session.commit()


def _clear_screen_cache():
    StockCache.query.filter(StockCache.ticker.like(screener._PREFIX + "%")).delete(
        synchronize_session=False)
    db.session.commit()


def _quiet_post_loop():
    """يُهدّئ ما بعد حلقتي التحديث في _auto_refresh (كلها ملفوفة بـtry أصلاً)."""
    radar.refresh_radar = lambda time_budget=90: 0
    screener.check_price_alerts = lambda: 0
    screener.notify_new_prelaunch = lambda: 0
    screener.load_records = lambda: ([], None)
    screener.market_mood = lambda recs: None
    telegram_client.is_configured = lambda: False


def _run_auto_refresh():
    from services import scheduler
    _quiet_post_loop()
    scheduler._auto_refresh(app)
    with app.app_context():
        c = db.session.get(AppSetting, "nightly_update_complete")
        f = db.session.get(AppSetting, "nightly_update_fresh")
        return (c.value if c else None), (f.value if f else None)


def test_nightly_complete_all():
    print("\n[⑦] كل رموز UNIVERSE محدَّثة → يُعلَّم مكتملاً بتحقّق فعلي:")
    _orig_univ = screener.UNIVERSE
    _orig_fresh = screener.universe_fresh_today
    _orig_refresh = screener.refresh_cache
    screener.UNIVERSE = ["A", "B", "C"]
    with app.app_context():
        _clear_screen_cache()

    calls = {"n": 0}

    def _refresh(time_budget=60):
        # كل دفعة تُحدّث حتى سهمين لم يُحدَّثا بعد (يلزم عدّة دفعات لتغطية الثلاثة)
        calls["n"] += 1
        with app.app_context():
            fresh = screener.universe_fresh_today()
            todo = [s for s in screener.UNIVERSE if s not in fresh][:2]
            for s in todo:
                _mark_fresh(s)
            return len(todo)

    screener.refresh_cache = _refresh
    complete, fresh = _run_auto_refresh()
    check(complete == "1", "كل الأسهم طازجة → nightly_update_complete=1 (اكتمل)")
    check(fresh == "3", f"عدد الطازج المسجّل = {fresh} (يساوي حجم UNIVERSE)")
    check(calls["n"] == 2, f"توقّف فور الاكتمال (دفعتان لا 15) — عدد الدفعات {calls['n']}")

    screener.UNIVERSE = _orig_univ
    screener.universe_fresh_today = _orig_fresh
    screener.refresh_cache = _orig_refresh


def test_nightly_incomplete_missing_symbol():
    print("\n[⑦] بقاء رمز غير محدَّث → لا يُعلَّم مكتملاً:")
    _orig_univ = screener.UNIVERSE
    _orig_refresh = screener.refresh_cache
    screener.UNIVERSE = ["A", "B", "C"]
    with app.app_context():
        _clear_screen_cache()

    def _refresh(time_budget=60):
        # C لا يُحدَّث أبداً (رمز عالق) → لا يكتمل
        with app.app_context():
            fresh = screener.universe_fresh_today()
            todo = [s for s in ["A", "B"] if s not in fresh][:2]
            for s in todo:
                _mark_fresh(s)
            return len(todo)

    screener.refresh_cache = _refresh
    complete, fresh = _run_auto_refresh()
    check(complete == "0", "رمز مفقود → nightly_update_complete=0 (غير مكتمل)")
    check(fresh == "2", f"الطازج المسجّل = {fresh} < 3 (يعكس النقص بوضوح)")

    screener.UNIVERSE = _orig_univ
    screener.refresh_cache = _orig_refresh


def test_nightly_no_infinite_loop_on_stall():
    print("\n[⑦] مصدر متعطّل (لا تقدّم) → يتوقّف فوراً بلا حلقة لا نهائية:")
    _orig_univ = screener.UNIVERSE
    _orig_refresh = screener.refresh_cache
    screener.UNIVERSE = ["A", "B", "C"]
    with app.app_context():
        _clear_screen_cache()

    calls = {"n": 0}

    def _refresh(time_budget=60):
        calls["n"] += 1  # لا يُحدّث أي سهم (حصة نفدت/أخطاء)
        return 0

    screener.refresh_cache = _refresh
    complete, fresh = _run_auto_refresh()
    check(complete == "0", "لا تقدّم → غير مكتمل")
    check(calls["n"] == 1, f"توقّف بعد دفعة واحدة بلا تقدّم (لا حلقة لا نهائية) — الدفعات {calls['n']}")

    screener.UNIVERSE = _orig_univ
    screener.refresh_cache = _orig_refresh


def test_nightly_safety_cap():
    print("\n[⑦] سقف أمان الدفعات يمنع اللانهاية حتى مع تقدّم بطيء جداً:")
    from services import scheduler
    _orig_univ = screener.UNIVERSE
    _orig_refresh = screener.refresh_cache
    # 20 سهماً وسهم واحد يُحدَّث كل دفعة → يتجاوز السقف قبل الاكتمال
    screener.UNIVERSE = [f"S{i}" for i in range(20)]
    with app.app_context():
        _clear_screen_cache()

    calls = {"n": 0}

    def _refresh(time_budget=60):
        calls["n"] += 1
        with app.app_context():
            fresh = screener.universe_fresh_today()
            todo = [s for s in screener.UNIVERSE if s not in fresh][:1]  # سهم واحد فقط/دفعة
            for s in todo:
                _mark_fresh(s)
            return len(todo)

    screener.refresh_cache = _refresh
    complete, fresh = _run_auto_refresh()
    check(calls["n"] == scheduler._MAX_REFRESH_ROUNDS,
          f"توقّف عند سقف الأمان {scheduler._MAX_REFRESH_ROUNDS} دفعة (لا لانهاية) — الدفعات {calls['n']}")
    check(complete == "0", "لم يكتمل ضمن السقف → مُعلَّم غير مكتمل بصدق")

    screener.UNIVERSE = _orig_univ
    screener.refresh_cache = _orig_refresh


def _run_auto_refresh_tracking(universe, mark_symbols):
    """يشغّل _auto_refresh مع تتبّع أي عمليات نُفّذت. mark_symbols = الرموز التي
    ستُصبح طازجة (لضبط الاكتمال). يُرجع (calls set, complete str)."""
    from services import scheduler, portfolio
    _orig_univ = screener.UNIVERSE
    _orig_refresh = screener.refresh_cache
    screener.UNIVERSE = universe
    with app.app_context():
        _clear_screen_cache()
    calls = set()

    def _refresh(time_budget=60):
        with app.app_context():
            fresh = screener.universe_fresh_today()
            todo = [s for s in mark_symbols if s not in fresh]
            for s in todo:
                _mark_fresh(s)
            return len(todo)

    screener.refresh_cache = _refresh
    radar.refresh_radar = lambda time_budget=90: 0
    # عمليات معتمدة على اكتمال بيانات السوق (يجب ألا تُنفَّذ عند incomplete)
    screener.check_price_alerts = lambda: (calls.add("price_alerts") or 0)
    screener.notify_new_prelaunch = lambda: (calls.add("prelaunch") or 0)
    portfolio.record_snapshot = lambda: calls.add("snapshot")
    screener.load_records = lambda: (calls.add("load_records") or ([], None))
    screener.market_mood = lambda recs: (calls.add("mood") or None)
    scheduler._send_daily_report = lambda: calls.add("daily_report")
    scheduler._send_weekly_report = lambda: calls.add("weekly_report")
    # عمليات مستقلة عن السوق (يجب أن تُنفَّذ دائماً)
    scheduler._notify_expiring_subs = lambda: (calls.add("expiring_subs") or 0)
    scheduler._notify_subs_expiry_inbox = lambda: (calls.add("subs_inbox") or 0)
    scheduler._cleanup_messages = lambda: (calls.add("cleanup") or 0)
    telegram_client.is_configured = lambda: False
    scheduler._auto_refresh(app)
    with app.app_context():
        c = db.session.get(AppSetting, "nightly_update_complete")
        complete = c.value if c else None
    screener.UNIVERSE = _orig_univ
    screener.refresh_cache = _orig_refresh
    return calls, complete


def test_nightly_gates_market_ops_on_incomplete():
    print("\n[⑦] تحديث جزئي (incomplete) → حجب العمليات النهائية المعتمدة على السوق:")
    MARKET_OPS = {"price_alerts", "prelaunch", "snapshot", "mood", "daily_report"}
    INDEP_OPS = {"expiring_subs", "subs_inbox", "cleanup"}

    # incomplete: C لا يُحدَّث أبداً
    calls, complete = _run_auto_refresh_tracking(["A", "B", "C"], ["A", "B"])
    check(complete == "0", "بقاء رمز غير محدَّث → complete=0")
    check(not (calls & MARKET_OPS),
          "incomplete → لم تُنفَّذ أي عملية سوق (تقرير/تنبيهات/لقطات/مزاج)")
    check(INDEP_OPS <= calls,
          "incomplete → العمليات المستقلة (اشتراكات/تنظيف) نُفّذت رغم النقص")

    # complete: كل الرموز تُحدَّث
    calls2, complete2 = _run_auto_refresh_tracking(["A", "B", "C"], ["A", "B", "C"])
    check(complete2 == "1", "اكتمال كل UNIVERSE → complete=1")
    check(MARKET_OPS <= calls2,
          "complete → كل عمليات السوق نُفّذت (المسار الطبيعي)")
    check(INDEP_OPS <= calls2, "complete → العمليات المستقلة نُفّذت أيضاً")


def main():
    print("=" * 62)
    print("regression للإصلاحات (تدقيق Codex)")
    print("=" * 62)
    test_roe_negative_equity()
    test_compare_incomplete()
    test_earnings_float_source_semantics()
    test_earnings_float_preserve_on_failure()
    test_earnings_float_clear_on_success_empty()
    test_edgar_source_semantics()
    test_edgar_http_semantics()
    test_edgar_partial_failure_and_invalid_content()
    test_edgar_error_json_body()
    test_edgar_preserve_on_failure()
    test_edgar_store_on_success_empty()
    test_price_alert_telegram()
    test_nightly_complete_all()
    test_nightly_incomplete_missing_symbol()
    test_nightly_no_infinite_loop_on_stall()
    test_nightly_safety_cap()
    test_nightly_gates_market_ops_on_incomplete()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات regression نجحت ✓ ({_passed} تحقّقاً) — الإصلاحات مقفولة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
