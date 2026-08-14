"""
test_audit_high_v2_regression.py — اختبارات regression للإصلاحات الستّة (تدقيق Codex، الجولة الثانية).

يغطّي المشاكل المؤكَّدة التي أُصلحت:
  ② المقارنة لا تُبنى من قوائم مالية ناقصة  (analysis.build_quick_summary)
  ③ ROE لا يُعرض موجباً مضلِّلاً عند حقوق ملكية سالبة  (analysis + screener metrics)
  ④ لا تُفقَد بيانات الأرباح/الأسهم الحرة السليمة عند فشل الجلب الجماعي  (screener.refresh_cache)
  ⑤ لا تُمسح بيانات المطلعين السليمة عند فشل EDGAR  (radar.refresh_radar)
  ⑥ التنبيه السعري لا يُطفأ إلا عند نجاح إرسال تلغرام  (screener.check_price_alerts)
  ⑦ التحديث الليلي يكرّر الدفعات بما يكفي لتغطية الـ32  (scheduler._auto_refresh)

(المشكلتان ① حماية FMP المركزية — مُصلَحة أصلاً عبر _reserve_atomic؛ لا مسار يتجاوز _get.)

التشغيل:  python tests/test_audit_high_v2_regression.py
"""

import os
import sys
import copy
import json
import tempfile
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


# ==================== ② المقارنة من قوائم ناقصة ====================
def test_compare_incomplete():
    print("\n[②] المقارنة لا تُبنى من قوائم مالية ناقصة:")
    fmp_client.get_quote = lambda t: {"price": 100, "name": "X"}
    # قوائم ناقصة (تدفق نقدي مفقود) → لا مقاييس/درجات
    PART = copy.deepcopy(FULL); PART["cashflow"] = None
    fmp_client.get_financials = lambda t, years=2: PART
    r = analysis.build_quick_summary("X")
    check(all(v is None for v in r["metrics"].values()), "قوائم ناقصة → كل المقاييس None")
    check(r["piotroski"]["score"] is None and r["catalyst"]["score"] is None,
          "قوائم ناقصة → Piotroski/Catalyst score=None (لا درجات جزئية)")
    check(r["price"] == 100, "السعر يبقى معروضاً (من الاقتباس)")
    # قوائم كاملة → المقاييس والدرجات تظهر
    fmp_client.get_financials = lambda t, years=2: copy.deepcopy(FULL)
    r2 = analysis.build_quick_summary("X")
    check(r2["metrics"]["roa"] is not None and r2["catalyst"]["score"] is not None,
          "قوائم كاملة → المقاييس والدرجات تظهر طبيعياً")


# ==================== ④ الأرباح/الأسهم الحرة عند فشل الجلب ====================
def test_earnings_float_preserve():
    print("\n[④] لا تُفقَد الأرباح/الأسهم الحرة السليمة عند فشل الجلب الجماعي:")
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
    # فشل الجلب الجماعي (خرائط فارغة)
    screener._upcoming_earnings = lambda: {}
    screener._shares_float_map = lambda: {}
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
          "فشل جلب الأرباح → earnings_date السليم محفوظ (لم يُمسح)")
    check(stored.get("float_shares") == 1234 and stored.get("free_float_pct") == 80.0,
          "فشل جلب الأسهم الحرة → القيم السليمة محفوظة (لم تُمسح)")


# ==================== ⑤ بيانات المطلعين عند فشل EDGAR ====================
def test_edgar_preserve():
    print("\n[⑤] لا تُمسح بيانات المطلعين السليمة عند فشل EDGAR:")
    radar.UNIVERSE = ["AAPL"]
    GOOD_TX = [{"name": "Insider A", "code": "P", "shares": 1000, "date": "2026-08-01"}]
    yest = datetime.now(timezone.utc) - timedelta(days=1)
    with app.app_context():
        db.session.merge(StockCache(ticker=radar._PREFIX + "AAPL",
                                    data_json=json.dumps(GOOD_TX), updated_at=yest))
        db.session.commit()
        # فشل EDGAR يعيد [] بلا استثناء
        edgar_client.get_insider_transactions = lambda *a, **k: []
        radar.refresh_radar()
        row = db.session.get(StockCache, radar._PREFIX + "AAPL")
        preserved = json.loads(row.data_json)
    check(preserved == GOOD_TX and row.updated_at.date() == yest.date(),
          "EDGAR يعيد [] → البيانات السليمة محفوظة (لم تُستبدل بقائمة فارغة)")

    # عند نجاح EDGAR ببيانات جديدة → تُحدَّث
    NEW_TX = [{"name": "Insider B", "code": "P", "shares": 5000, "date": "2026-08-10"}]
    with app.app_context():
        edgar_client.get_insider_transactions = lambda *a, **k: NEW_TX
        radar.refresh_radar()
        updated = json.loads(db.session.get(StockCache, radar._PREFIX + "AAPL").data_json)
    check(updated == NEW_TX, "EDGAR ينجح ببيانات → تُحدَّث طبيعياً")


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


# ==================== ⑦ سقف دفعات التحديث الليلي ====================
def test_nightly_round_cap():
    print("\n[⑦] التحديث الليلي يكرّر الدفعات بما يكفي لتغطية الـ32:")
    from services import scheduler
    calls = {"n": 0}
    screener.refresh_cache = lambda time_budget=60: (calls.__setitem__("n", calls["n"] + 1) or 1)  # تقدّم دائم
    radar.refresh_radar = lambda time_budget=90: 0  # يوقف حلقة الرادار فوراً
    # نعزل ما بعد الحلقتين (كلها ملفوفة بـtry، لكن نُهدّئ الضجيج)
    screener.check_price_alerts = lambda: 0
    screener.notify_new_prelaunch = lambda: 0
    screener.load_records = lambda: ([], None)
    screener.market_mood = lambda recs: None
    scheduler._auto_refresh(app)
    # السقف الجديد 12 دفعة (كان 6 = يغطّي 24 فقط < 32)
    check(calls["n"] == 12, f"عدد دفعات refresh_cache = {calls['n']} (السقف 12 ≥ ما يكفي للـ32)")
    check(calls["n"] >= 11, "السقف يكفي لتغطية 32 سهماً حتى مع بطء FMP (كان 6 غير كافٍ)")


def main():
    print("=" * 62)
    print("regression للإصلاحات الستّة (تدقيق Codex — الجولة الثانية)")
    print("=" * 62)
    test_roe_negative_equity()
    test_compare_incomplete()
    test_earnings_float_preserve()
    test_edgar_preserve()
    test_price_alert_telegram()
    test_nightly_round_cap()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات regression نجحت ✓ ({_passed} تحقّقاً) — الإصلاحات الستّة مقفولة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
