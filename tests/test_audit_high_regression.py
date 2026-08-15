"""
test_audit_high_regression.py — اختبارات regression للمشاكل المرتفعة الثلاث (تدقيق Codex).

الغرض: **إثبات وقفل** السلوك الصحيح للثلاث على النسخة الحالية (المدموجة في main)،
والحماية من أي انزلاق مستقبلي. لا يغيّر أي منطق — يتحقّق فقط.

المشاكل الثلاث (كلها مُعالَجة أصلاً في الكود الحالي):
  1) معادلة Accruals في Piotroski (services/scoring.py:piotroski_score).
  2) اكتمال البيانات المالية — لا يُحفَظ/لا يستبدل السليم إلا تقرير مكتمل فعلاً
     (services/fmp_client.py:financials_complete + screener._build_record/refresh_cache
      + analysis.build_stock_report + app.stock_report).
  3) حماية حصّة FMP مركزياً وذرّياً وfail-closed + صلاحية مسارات التحديث
     (services/fmp_client.py:_reserve_atomic/reserve_operation/_get + app: screener_refresh/stock_report).

التشغيل:  python tests/test_audit_high_regression.py
لو طبع «كل اختبارات regression نجحت ✓» فالثلاث مقفولة صحيحة. أي ✗ = انزلاق يجب إصلاحه.
يعمل على قاعدة SQLite مؤقتة ويعزل الشبكة (لا يستهلك أي باقة).
"""

import os
import sys
import copy
import tempfile
import threading

# --- بيئة معزولة (وضع محمي حتى نختبر صلاحية المسارات) ---
os.environ["APP_PASSWORD"] = "regress-test-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import (fmp_client, finnhub_client, edgar_client, news_client,  # noqa: E402
                      indicators, scoring, screener, analysis)
news_client.get_market_news = lambda *a, **k: []
finnhub_client.get_quote = lambda *a, **k: None
edgar_client.get_insider_transactions = lambda *a, **k: []

from app import app  # noqa: E402
from models import db, StockCache, AppSetting, Subscriber  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from datetime import date, datetime, timezone, timedelta  # noqa: E402

app.config["WTF_CSRF_ENABLED"] = False

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


# قوائم مالية كاملة فعلياً (كل الحقول الإلزامية بقيم حقيقية)
FULL_FIN = {
    "income": [{"netIncome": 10, "revenue": 100, "grossProfit": 40, "weightedAverageShsOut": 50,
                "operatingIncome": 20, "eps": 2},
               {"netIncome": 8, "revenue": 90, "grossProfit": 35, "weightedAverageShsOut": 50}],
    "balance": [{"totalAssets": 200, "totalCurrentAssets": 80, "totalCurrentLiabilities": 40,
                 "longTermDebt": 30, "totalStockholdersEquity": 120},
                {"totalAssets": 190, "totalCurrentAssets": 75, "totalCurrentLiabilities": 38,
                 "longTermDebt": 35}],
    "cashflow": [{"operatingCashFlow": 25}],
}


# ==================== المشكلة 1: معادلة Accruals ====================
def test_accruals():
    print("\n[1] Piotroski Accruals — الاتجاهان المتعاكسان (الصيغة القياسية ROA − CFO/Assets < 0):")

    def accr_passed(ni, assets, cfo):
        F = {"income": [{"netIncome": ni}], "balance": [{"totalAssets": assets}],
             "cashflow": [{"operatingCashFlow": cfo}]}
        comp = next(c for c in scoring.piotroski_score(F)["components"] if c["n"] == 4)
        return comp["passed"]

    # CFO/Assets > ROA  (نقد فعلي يفوق الربح المحاسبي = جودة عالية) → النقطة تُمنح
    check(accr_passed(10, 100, 25) is True, "CFO/Assets(0.25) > ROA(0.10) → النقطة تُمنح (passed=True)")
    # CFO/Assets < ROA  (ربح ورقي يفوق النقد = جودة أضعف) → النقطة تُحجب
    check(accr_passed(20, 100, 5) is False, "CFO/Assets(0.05) < ROA(0.20) → النقطة تُحجب (passed=False)")
    # حالة حدّية: تساوٍ (accr=0، ليست <0) → تُحجب
    check(accr_passed(10, 100, 10) is False, "CFO/Assets = ROA (accr=0، ليست <0) → تُحجب")
    # بيانات ناقصة → None (لا تُحسب، لا صفر ملفّق)
    check(next(c for c in scoring.piotroski_score({"income": [{"netIncome": 1}], "balance": [{}],
               "cashflow": [{}]})["components"] if c["n"] == 4)["passed"] is None,
          "بلا CFO/أصول → passed=None (بيانات غير متوفّرة)")


# ==================== المشكلة 2: اكتمال البيانات المالية ====================
def test_financial_completeness():
    print("\n[2] اكتمال البيانات المالية — لا اكتمال إلا بكل الحقول الفعلية:")
    fc = fmp_client.financials_complete

    check(fc(FULL_FIN) is True, "بيانات كاملة فعلياً → True")
    # مثال Codex الحرفي: صفوف موجودة لكن فارغة
    check(fc({"income": [{}, {}], "balance": [{}, {}], "cashflow": [{}]}) is False,
          "صفوف فارغة {} (Codex) → False")

    # فشل/نقص كل مصدر مالي على حدة → False
    check(fc({**FULL_FIN, "income": None}) is False, "قائمة الدخل مفقودة → False")
    check(fc({**FULL_FIN, "balance": None}) is False, "الميزانية مفقودة → False")
    check(fc({**FULL_FIN, "cashflow": None}) is False, "التدفق النقدي مفقود → False")
    # نجاح واحدة فقط من الثلاث → False (جوهر ملاحظة Codex)
    check(fc({"income": FULL_FIN["income"], "balance": None, "cashflow": None}) is False,
          "نجاح 1/3 فقط (الدخل) → False")
    check(fc({"income": FULL_FIN["income"], "balance": FULL_FIN["balance"], "cashflow": None}) is False,
          "نجاح 2/3 فقط → False")

    # حذف كل حقل إلزامي واحداً تلو الآخر → False
    REQ = ([("income", 0, k) for k in fmp_client._INC_FIELDS_CUR]
           + [("income", 1, k) for k in fmp_client._INC_FIELDS_PRV]
           + [("balance", 0, k) for k in fmp_client._BAL_FIELDS_CUR]
           + [("balance", 1, k) for k in fmp_client._BAL_FIELDS_PRV]
           + [("cashflow", 0, k) for k in fmp_client._CF_FIELDS_CUR])
    ok = True
    for stmt, idx, key in REQ:
        d = copy.deepcopy(FULL_FIN); d[stmt][idx].pop(key)
        if fc(d) is not False:
            ok = False
        d2 = copy.deepcopy(FULL_FIN); d2[stmt][idx][key] = None
        if fc(d2) is not False:
            ok = False
    check(ok, f"حذف/None لأيّ من الحقول الإلزامية الـ{len(REQ)} → False (كلها)")

    # القيمة 0 بيانات صحيحة (لا تُرفض)
    z = copy.deepcopy(FULL_FIN)
    z["income"][0]["netIncome"] = 0; z["cashflow"][0]["operatingCashFlow"] = 0
    check(fc(z) is True, "قيم 0 حقيقية (netIncome/CFO) → True")


def test_completeness_propagation():
    print("\n[2ب] علَم الاكتمال يمنع حفظ/استبدال بسجل ناقص:")
    # نعزل الحساب الثقيل
    for n in ["money_flow", "reversal_pattern", "market_structure", "multi_timeframe",
              "squeeze_breakout", "golden_cross", "trend_pullback", "atr", "resistance_warning",
              "fibonacci_levels", "volume_profile", "sustained_breakout"]:
        setattr(indicators, n, lambda *a, **k: None)
    indicators.build_indicators = lambda c: ([{"label": "EMA"}] if c else [])
    scoring.break_status = lambda *a, **k: None
    scoring.atr_trade_plan = lambda *a, **k: None
    screener._save_price_history = lambda *a, **k: None
    screener._period_return = lambda *a, **k: 0.0
    cand = [{"date": f"2026-01-{i+1:02d}", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}
            for i in range(60)]
    Q = {"price": 100, "name": "X"}; P = {"sector": "Tech", "name": "X"}
    fmp_client.get_quote = lambda t: Q
    fmp_client.get_profile = lambda t: P
    fmp_client.get_historical_prices = lambda t, limit=60: cand

    # قوائم كاملة → _complete=True ؛ قوائم بصفوف فارغة → False
    fmp_client.get_financials = lambda t, years=2: copy.deepcopy(FULL_FIN)
    check(screener._build_record("X")["_complete"] is True, "_build_record: كامل → _complete=True")
    fmp_client.get_financials = lambda t, years=2: {"income": [{}, {}], "balance": [{}, {}], "cashflow": [{}]}
    check(screener._build_record("X")["_complete"] is False, "_build_record: صفوف فارغة → _complete=False")

    # refresh_cache: سجل ناقص لا يستبدل السجل السليم ولا يُعلَّم محدثاً لليوم
    screener._refresh_spy_history = lambda *a, **k: None
    screener._benchmark_return = lambda *a, **k: None
    screener._upcoming_earnings = lambda *a, **k: {}
    screener._shares_float_map = lambda *a, **k: {}
    screener.UNIVERSE = ["AAPL"]
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    GOOD = '{"ticker":"AAPL","good":true}'
    with app.app_context():
        db.session.merge(StockCache(ticker=screener._PREFIX + "AAPL", data_json=GOOD, updated_at=yest))
        with Session(db.engine) as s:
            if s.get(AppSetting, fmp_client._usage_key()) is None:
                s.add(AppSetting(key=fmp_client._usage_key(), value="0")); s.commit()
        db.session.commit()
        screener.refresh_cache()
        row = db.session.get(StockCache, screener._PREFIX + "AAPL")
        check(row.data_json == GOOD and row.updated_at.date() == yest.date(),
              "refresh_cache: سجل ناقص → السليم محفوظ ولم يُعلَّم محدثاً (يُعاد لاحقاً)")

    # build_stock_report: صفوف فارغة → _complete=False (لا يُحفَظ كتقرير صالح)
    finnhub_client.get_quote = lambda t: None
    analysis.price_chart = lambda *a, **k: None
    scoring.piotroski_score = lambda f: {"score": 5}
    scoring.catalyst_score = lambda f: {"score": 50}
    fmp_client.get_historical_prices = lambda t, limit=250: cand
    fmp_client.get_financials = lambda t: {"income": [{}, {}], "balance": [{}, {}], "cashflow": [{}]}
    check(analysis.build_stock_report("X")["_complete"] is False,
          "build_stock_report: صفوف فارغة → _complete=False")


# ==================== المشكلة 3: حماية حصّة FMP مركزياً ====================
def test_fmp_central_protection():
    print("\n[3] حماية حصّة FMP — مركزية + ذرّية + fail-closed + صلاحية المسارات:")
    fmp_client.FMP_API_KEY = "PK"

    class FR:
        status_code = 200
        text = ""
        def json(self):
            return {}
    net = {"n": 0}
    fmp_client.requests.get = lambda *a, **k: (net.__setitem__("n", net["n"] + 1) or FR())

    def setc(v):
        with app.app_context():
            with Session(db.engine) as s:
                r = s.get(AppSetting, fmp_client._usage_key())
                if r:
                    r.value = str(v)
                else:
                    s.add(AppSetting(key=fmp_client._usage_key(), value=str(v)))
                s.commit()

    def getc():
        with app.app_context():
            return fmp_client.get_today_usage() or 0

    LIM = fmp_client.CIRCUIT_LIMIT

    # (أ) الحجز الذرّي: N طلباً متزامناً لا يفقد زيادة ولا يكرّر رقماً
    setc(0)
    def worker():
        with app.app_context():
            fmp_client._reserve_atomic(1)
    ts = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check(getc() == 40, "ذرّي: 40 حجزاً متزامناً → العدّاد=40 بالضبط (بلا فقد/تكرار)")

    # (ب) لا يُتجاوَز الحدّ تحت التزامن
    setc(LIM - 1)
    def one():
        with app.app_context():
            fmp_client._local.wallet = 0
            fmp_client._reserve_atomic(1)
    ts = [threading.Thread(target=one) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check(getc() == LIM, f"الحدّ {LIM} لا يُتجاوَز: 6 حجوزات متزامنة عند الحافة → العدّاد={LIM}")

    # (ج) حجز العملية كاملة (6) ذرّياً؛ لا يبدأ إن لم تتّسع
    setc(LIM - 6); fmp_client._local.wallet = 0
    with app.app_context():
        check(fmp_client.reserve_operation(6) is True, "reserve_operation(6): تتّسع بالضبط → True")
    setc(LIM - 5); fmp_client._local.wallet = 0
    with app.app_context():
        check(fmp_client.reserve_operation(6) is False and getc() == LIM - 5,
              "reserve_operation(6): لا تتّسع (يتبقّى 5) → False والعدّاد ثابت")

    # (د) fail-closed: خطأ قاعدة → لا يبدأ طلب FMP ولا يُتجاوَز الحدّ
    orig = fmp_client._reserve_atomic
    fmp_client._reserve_atomic = lambda n: None  # محاكاة خطأ/ضغط قاعدة
    fmp_client._local.wallet = 0
    with app.app_context():
        check(fmp_client.reserve_operation(6) is False, "fail-closed: خطأ قاعدة → reserve_operation=False")
    net["n"] = 0
    with app.app_context():
        r = fmp_client._get("quote/X")  # مفتاح المنصة
    check(r is None and net["n"] == 0, "fail-closed: خطأ قاعدة + مفتاح المنصة → لا يخرج طلب FMP")
    # مفتاح المشترك (حصّته الخاصة) غير متأثّر
    net["n"] = 0
    with app.app_context():
        fmp_client._get("quote/X", api_key="SUBKEY")
    check(net["n"] == 1, "مفتاح المشترك غير خاضع للقاطع (حصّته الخاصة)")
    fmp_client._reserve_atomic = orig

    # (هـ) صلاحية /screener/refresh: للمدير فقط (لا يستطيع المشترك استنزاف الحصّة)
    with app.app_context():
        sub = Subscriber(name="t", access_code="RC1",
                         start_date=date.today() - timedelta(days=1),
                         end_date=date.today() + timedelta(days=30),
                         disclaimer_accepted_at=datetime.now(timezone.utc))
        db.session.add(sub); db.session.commit()
        sid = sub.id
    called = {"v": False}
    screener.refresh_cache = lambda *a, **k: called.__setitem__("v", True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True; s["role"] = "sub"; s["sub_id"] = sid
    c.post("/screener/refresh")
    check(called["v"] is False, "/screener/refresh: المشترك محجوب (لم يُستدعَ refresh_cache)")
    called["v"] = False
    with c.session_transaction() as s:
        s["authed"] = True; s["role"] = "admin"; s.pop("sub_id", None)
    c.post("/screener/refresh")
    check(called["v"] is True, "/screener/refresh: المدير مسموح")

    # (و) /stock/<خارج القائمة>: لا يبني تقريراً حيّاً (لا يستهلك FMP لرموز عشوائية)
    built = []
    analysis.build_stock_report = lambda t: (built.append(t) or None)
    ca = app.test_client()
    with ca.session_transaction() as s:
        s["authed"] = True; s["role"] = "admin"
    setc(0)
    ca.get("/stock/ZZZZ")
    check("ZZZZ" not in built, "/stock/ZZZZ (خارج UNIVERSE) → لا يُبنى تقرير (لا استهلاك FMP)")
    ca.get("/stock/AAPL")
    check("AAPL" in built, "/stock/AAPL (داخل UNIVERSE) → يُبنى (بعد حجز الميزانية)")


def main():
    print("=" * 60)
    print("اختبارات regression للمشاكل المرتفعة الثلاث (تدقيق Codex)")
    print("=" * 60)
    test_accruals()
    test_financial_completeness()
    test_completeness_propagation()
    test_fmp_central_protection()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات regression نجحت ✓ ({_passed} تحقّقاً) — المشاكل الثلاث مقفولة صحيحة.")
        return 0
    print(f"✗ فشل {_failed} تحقّقاً (نجح {_passed}) — يوجد انزلاق يجب إصلاحه.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
