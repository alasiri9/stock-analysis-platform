"""
test_logic_audit_high3_regression.py — regression لـ HIGH#3 (فصل السعر الحي عن سعر التحليل)
+ تشديد تغطية HIGH#2 (ADX الهابط القوي في measures_met و_plan_strategy_scores).

HIGH#3: refresh_prices_intraday يكتب live_price في حقل مستقل ولا يمسّ price/analysis_price
(أساس الخطة والمستويات والمؤشرات). العرض يستعمل current_price (حي إن توفّر) والتحليل يبقى
على analysis_price. توافق خلفي مع سجلّات قديمة. لا استهلاك FMP.

التشغيل:  python tests/test_logic_audit_high3_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone

os.environ["APP_PASSWORD"] = "high3-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import screener, indicators, finnhub_client, fmp_client, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockCache  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


# ---- أدوات ADX (نفس منطق ترتيب FMP: الأحدث أولاً، _clean يعكسها) ----
def _fmp_candles(n, direction):
    rows = []
    for i in range(n):  # الأقدم أولاً
        c = 100.0 + i if direction == "up" else 100.0 + (n - i)
        rows.append({"open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1_000_000, "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"})
    return list(reversed(rows))  # الأحدث أولاً (ترتيب FMP)


# ==================== تشديد HIGH#2 ====================
def test_adx_down_not_in_measures_met():
    print("\n[HIGH#2+] measures_met لا يعدّ ADX الهابط القوي إشارةً صاعدة:")
    inds = indicators.build_indicators(_fmp_candles(80, "down"))
    adx = next((b for b in inds if b["label"] == "ADX"), None)
    check(adx is not None and adx["status"] == "bear", "بناء اتجاه هابط قوي → شارة ADX bear")
    n_bear = screener.measures_met({"indicators": inds})
    # لو كان ADX bull (السلوك الخاطئ القديم) لزاد العدّ نقطة واحدة
    inds_if_bull = [dict(b, status="bull") if b["label"] == "ADX" else b for b in inds]
    n_if_bull = screener.measures_met({"indicators": inds_if_bull})
    check(n_if_bull == n_bear + 1,
          f"ADX الهابط لا يُحسب ضمن measures_met (bear={n_bear}, لو-bull={n_if_bull})")


def test_adx_down_plan_strategy_bear():
    print("\n[HIGH#2+] _plan_strategy_scores يمثّل ADX الهابط القوي bear لا bull:")
    rec = {"indicators": [{"label": "ADX", "value": "30", "status": "bear"}], "break_status": {}}
    scores = screener._plan_strategy_scores(rec)
    adx_s = next((s for s in scores if s["name"].startswith("قوة الاتجاه")), None)
    check(adx_s is not None and adx_s["status"] == "bear" and adx_s["score"] == 2,
          "ADX status=bear → استراتيجية bear بدرجة 2 (لا bull/8)")
    # وعند صاعد قوي → bull/8 (عدم انحدار)
    rec_up = {"indicators": [{"label": "ADX", "value": "30", "status": "bull"}], "break_status": {}}
    adx_up = next(s for s in screener._plan_strategy_scores(rec_up) if s["name"].startswith("قوة الاتجاه"))
    check(adx_up["status"] == "bull" and adx_up["score"] == 8, "ADX status=bull → bull/8 (عدم انحدار)")


# ==================== HIGH#3 ====================
def _seed_record(**over):
    rec = {
        "ticker": "AAPL", "name": "Apple", "sector": "Technology",
        "price": 100.0, "analysis_price": 100.0, "change_percent": 1.0,
        "catalyst": 85, "piotroski": 6, "market_cap": 3e12,
        "atr_plan": {"entry": 100.0, "stop": 95.0, "target": 110.0, "atr": 2.0, "period": 14},
        "indicators": [{"label": "EMA", "status": "bull"},
                       {"label": "ADX", "status": "bull", "value": "30"}],
        "break_status": {"confirmed": True, "dir": "breakout"},
        "money_flow": {"status": "bull", "score": 70}, "rel_strength": 5.0,
    }
    rec.update(over)
    return rec


def _put_record(rec, ticker="AAPL"):
    with app.app_context():
        db.session.merge(StockCache(ticker=screener._PREFIX + ticker,
                                    data_json=json.dumps(rec),
                                    updated_at=datetime.now(timezone.utc)))
        db.session.commit()


def _get_record(ticker="AAPL"):
    with app.app_context():
        return json.loads(db.session.get(StockCache, screener._PREFIX + ticker).data_json)


def test_intraday_separates_live_from_analysis():
    print("\n[HIGH#3] تحديث live_price لا يمسّ سعر التحليل ولا الخطة ولا المؤشرات:")
    screener.UNIVERSE = ["AAPL"]
    original = _seed_record()
    _put_record(original)
    finnhub_client.get_quote = lambda t: {"price": 120.0, "change_percent": 5.0}
    with app.app_context():
        n = screener.refresh_prices_intraday()
    stored = _get_record()

    check(n == 1, "حُدّث سهم واحد (السعر الحي)")
    check(stored.get("analysis_price") == 100.0, "analysis_price لم يتغيّر (100) رغم وصول سعر حي")
    check(stored.get("price") == 100.0, "price (أساس التحليل) لم يُستبدَل (100)")
    check(stored.get("live_price") == 120.0, "live_price حُفظ في حقل مستقل (120)")
    check(stored.get("atr_plan") == original["atr_plan"],
          "خطة الدخول/الوقف/الهدف تبقى مرتبطة بسعر التحليل (لم تتغيّر)")
    check(stored.get("indicators") == original["indicators"],
          "المؤشرات الفنية لم تتغيّر بتحديث السعر الحي")
    check(screener.measures_met(stored) == screener.measures_met(original),
          "قوة التأكيد (measures_met) لم تتغيّر بتحديث السعر الحي")
    check(screener.current_price(stored) == 120.0, "السعر الحالي المعروض = live_price (120)")
    check(screener.analysis_price(stored) == 100.0, "سعر التحليل المعروض للخطة = 100")


def test_current_price_follows_new_live():
    print("\n[HIGH#3] السعر الحالي المعروض يتغيّر عند وصول live_price جديد:")
    screener.UNIVERSE = ["AAPL"]
    _put_record(_seed_record())
    finnhub_client.get_quote = lambda t: {"price": 120.0}
    with app.app_context():
        screener.refresh_prices_intraday()
    check(screener.current_price(_get_record()) == 120.0, "بعد أول تحديث: current_price=120")
    finnhub_client.get_quote = lambda t: {"price": 131.5}
    with app.app_context():
        screener.refresh_prices_intraday()
    r2 = _get_record()
    check(screener.current_price(r2) == 131.5, "بعد تحديث جديد: current_price=131.5 (تتبع الحي)")
    check(screener.analysis_price(r2) == 100.0, "سعر التحليل ما زال ثابتاً (100)")


def test_backward_compat_old_records():
    print("\n[HIGH#3] سجلّات قديمة بلا live_price/analysis_price لا تكسر شيئاً:")
    old = {"ticker": "OLD", "name": "Old Co", "sector": "Technology", "price": 88.0,
           "catalyst": 50, "piotroski": 5,
           "indicators": [{"label": "EMA", "status": "bull"}]}
    check(screener.current_price(old) == 88.0, "current_price(سجل قديم) → price (توافق خلفي)")
    check(screener.analysis_price(old) == 88.0, "analysis_price(سجل قديم) → price (توافق خلفي)")
    check(screener.current_price({}) is None and screener.current_price(None) is None,
          "سجل فارغ/None → None (لا استثناء)")
    # عرض البطاقة لسجل قديم بلا الحقول الجديدة — بلا كسر
    with app.test_request_context():
        html = app.jinja_env.get_template("_scard.html").render(r=old, rank=1)
    check('88.00 $' in html, "بطاقة سجل قديم تعرض السعر (88.00 $) بلا كسر")


def test_intraday_no_fmp_usage():
    print("\n[HIGH#3] refresh_prices_intraday لا يستهلك حصّة FMP (Finnhub فقط):")
    screener.UNIVERSE = ["AAPL"]
    _put_record(_seed_record())
    calls = {"n": 0}
    _orig_get = fmp_client._get
    fmp_client._get = lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or None
    finnhub_client.get_quote = lambda t: {"price": 120.0}
    try:
        with app.app_context():
            screener.refresh_prices_intraday()
    finally:
        fmp_client._get = _orig_get
    check(calls["n"] == 0, "لم يُستدعَ fmp_client._get ولا مرة (لا استهلاك FMP)")


def main():
    print("=" * 62)
    print("regression — HIGH#3 (فصل السعر الحي/التحليل) + تشديد HIGH#2")
    print("=" * 62)
    test_adx_down_not_in_measures_met()
    test_adx_down_plan_strategy_bear()
    test_intraday_separates_live_from_analysis()
    test_current_price_follows_new_live()
    test_backward_compat_old_records()
    test_intraday_no_fmp_usage()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات HIGH#3/HIGH#2+ نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
