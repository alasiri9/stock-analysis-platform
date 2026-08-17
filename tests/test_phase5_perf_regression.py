"""
test_phase5_perf_regression.py — PHASE 5 (G: Trading-Day Horizons · H: SPY Alpha · E: Performance baseline).

يقفل:
- 1D/5D/10D/20D = الجلسة التداولية اللاحقة الفعلية (لا أيام تقويمية، لا أقرب-يوم).
- عبور عطلة نهاية الأسبوع/يوم مفقود ⇒ يُحسب من PricePoint الفعلي.
- نقص عدد الجلسات ⇒ pending/None (ليست Fail).
- الأساس = performance_baseline_price (إغلاق EOD)؛ live_price لا يغيّره؛ لا تسرّب بيانات ماضية.
- SPY: نفس baseline_date ونفس exit_date؛ alpha صحيح؛ SPY مفقود ⇒ pending، بلا استبدال تاريخ.

التشغيل:  python tests/test_phase5_perf_regression.py
"""

import os
import sys
import tempfile
from datetime import date, timedelta

os.environ["APP_PASSWORD"] = "p5"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import screener, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, PricePoint, StockStateEvent, StockStateOutcome  # noqa: E402
from services import tracking  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _bdays_after(start, n, skip=()):
    """أول n يوم عمل بعد start (تخطّي السبت/الأحد وأي أيام skip = عطل بلا صف PricePoint)."""
    out = []
    d = start
    while len(out) < n:
        d = d + timedelta(days=1)
        if d.weekday() >= 5:
            continue
        if d in skip:
            continue
        out.append(d)
    return out


def _reset():
    PricePoint.query.delete()
    StockStateEvent.query.delete()
    StockStateOutcome.query.delete()
    db.session.commit()


def _pp(ticker, d, price):
    db.session.merge(PricePoint(ticker=ticker, date=d, price=price))


def _event(ticker, baseline_date, base_price, state="READY"):
    ev = StockStateEvent(ticker=ticker, lifecycle_id=1, state_code=state,
                         baseline_date=baseline_date, performance_baseline_price=base_price,
                         analysis_price=base_price + 1)  # analysis_price مختلف (تدقيق) عن الأساس
    db.session.add(ev)
    db.session.commit()
    return ev.id


def _outcome(event_id, h):
    return db.session.get(StockStateOutcome, (event_id, h))


BASE_FRI = date(2026, 8, 7)  # جمعة — لاختبار عبور نهاية الأسبوع (1D يجب أن يكون الاثنين)


def test_horizons_trading_sessions():
    print("\n[G] الآفاق = جلسات تداول فعلية (عبور نهاية الأسبوع، بلا حساب تقويمي):")
    with app.app_context():
        _reset()
        # 20 يوم عمل بعد الجمعة، السعر = 100 + رقم الجلسة (i)
        days = _bdays_after(BASE_FRI, 20)
        for i, d in enumerate(days, start=1):
            _pp("PF", d, 100.0 + i)
        # صف قبل/عند الأساس يجب ألّا يُستخدم (لا تسرّب ماضٍ)
        _pp("PF", BASE_FRI, 999.0)
        _pp("PF", BASE_FRI - timedelta(days=7), 1.0)
        db.session.commit()
        eid = _event("PF", BASE_FRI, 100.0)
        tracking.fill_outcomes()

        o1 = _outcome(eid, 1)
        check(o1 is not None and abs(o1.return_pct - 1.0) < 1e-6, "1D = أول جلسة لاحقة (+1%)")
        check(o1.exit_date == date(2026, 8, 10),
              "1D exit = الاثنين 08-10 (تخطّى السبت/الأحد — لا حساب تقويمي)")
        o5 = _outcome(eid, 5)
        check(o5 is not None and abs(o5.return_pct - 5.0) < 1e-6, "5D = الجلسة الخامسة (+5%)")
        o10 = _outcome(eid, 10)
        check(o10 is not None and abs(o10.return_pct - 10.0) < 1e-6, "10D = الجلسة العاشرة (+10%)")
        o20 = _outcome(eid, 20)
        check(o20 is not None and abs(o20.return_pct - 20.0) < 1e-6, "20D = الجلسة العشرون (+20%)")
        # لا تسرّب: الأساس 100 (لا 999 عند الأساس، ولا 1 قبله)
        check(abs(o1.return_pct - 1.0) < 1e-6, "الأساس = performance_baseline_price (لا صف الأساس/الماضي)")


def test_pending_when_not_enough_sessions():
    print("\n[G] نقص الجلسات ⇒ pending/None (ليست Fail):")
    with app.app_context():
        _reset()
        days = _bdays_after(BASE_FRI, 3)  # 3 جلسات فقط
        for i, d in enumerate(days, start=1):
            _pp("PND", d, 100.0 + i)
        db.session.commit()
        eid = _event("PND", BASE_FRI, 100.0)
        tracking.fill_outcomes()
        check(_outcome(eid, 1) is not None, "1D متوفّر (3 جلسات ≥ 1)")
        check(_outcome(eid, 5) is None, "5D لم يُكتب (pending — 3 < 5)")
        check(_outcome(eid, 20) is None, "20D pending")


def test_missing_holiday_uses_actual_sessions():
    print("\n[G] يوم مفقود (عطلة بلا صف) ⇒ العدّ من PricePoint الفعلي لا التقويم:")
    with app.app_context():
        _reset()
        # عطلة = ثاني يوم عمل (بلا صف PricePoint). العدّ يتخطّاها ويعتمد الصفوف الفعلية.
        holiday = _bdays_after(BASE_FRI, 2)[1]
        days = _bdays_after(BASE_FRI, 6, skip=(holiday,))  # 6 صفوف فعلية، بلا العطلة
        for i, d in enumerate(days, start=1):
            _pp("PH", d, 200.0 + i)  # الأساس 200
        db.session.commit()
        eid = _event("PH", BASE_FRI, 200.0)
        tracking.fill_outcomes()
        o5 = _outcome(eid, 5)
        naive_5_bdays = _bdays_after(BASE_FRI, 5)[4]  # الجلسة الخامسة تقويمياً (تشمل العطلة)
        check(o5 is not None and o5.exit_date == days[4],
              "5D = خامس صف PricePoint فعلي (تخطّى يوم العطلة المفقود)")
        check(o5.exit_date != holiday and o5.exit_date != naive_5_bdays,
              "لم يُستخدم يوم العطلة ولا الحساب التقويمي الساذج")


def test_spy_alpha_alignment():
    print("\n[H] SPY: نفس baseline_date ونفس exit_date، alpha صحيح:")
    with app.app_context():
        _reset()
        days = _bdays_after(BASE_FRI, 5)
        for i, d in enumerate(days, start=1):
            _pp("PA", d, 100.0 + i)          # السهم: +i%
        # SPY: الأساس عند baseline_date + عند نفس تواريخ الخروج
        _pp(screener.MARKET_BENCHMARK, BASE_FRI, 400.0)
        for i, d in enumerate(days, start=1):
            _pp(screener.MARKET_BENCHMARK, d, 400.0 + 2.0 * i)  # SPY: +0.5i%
        db.session.commit()
        eid = _event("PA", BASE_FRI, 100.0)
        tracking.fill_outcomes()
        o1 = _outcome(eid, 1)
        # السهم 1% ، SPY (402-400)/400=0.5% ، alpha=0.5%
        check(o1.spy_return_pct is not None and abs(o1.spy_return_pct - 0.5) < 1e-6,
              "SPY 1D = 0.5% (نفس التواريخ)")
        check(o1.alpha_pct is not None and abs(o1.alpha_pct - 0.5) < 1e-6,
              "alpha 1D = 1% − 0.5% = 0.5%")


def test_spy_missing_pending():
    print("\n[H] SPY مفقود ⇒ alpha pending، بلا استبدال تاريخ:")
    with app.app_context():
        _reset()
        days = _bdays_after(BASE_FRI, 2)
        for i, d in enumerate(days, start=1):
            _pp("PM", d, 100.0 + i)
        # لا نضيف SPY على الإطلاق لهذا الأساس/الخروج
        db.session.commit()
        eid = _event("PM", BASE_FRI, 100.0)
        tracking.fill_outcomes()
        o1 = _outcome(eid, 1)
        check(o1 is not None and o1.return_pct is not None, "عائد السهم محسوب (لا يعتمد على SPY)")
        check(o1.spy_return_pct is None and o1.alpha_pct is None,
              "SPY/alpha = None (pending) عند غياب SPY — بلا استبدال")


def main():
    print("=" * 60)
    print("PHASE 5 — Trading Horizons + SPY (G + H)")
    print("=" * 60)
    test_horizons_trading_sessions()
    test_pending_when_not_enough_sessions()
    test_missing_holiday_uses_actual_sessions()
    test_spy_alpha_alignment()
    test_spy_missing_pending()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (G+H) نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
