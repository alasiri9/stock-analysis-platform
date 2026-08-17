"""
test_phase5_perf_filter_regression.py — PHASE 5 ISSUE #1 (HIGH).

INVALIDATED حدث دورة حياة، لكنه لا يدخل إحصاءات أداء READY/LAUNCHED.
- fill_outcomes لا يبني نتائج لأحداث INVALIDATED.
- performance_summary يعمل JOIN مع StockStateEvent ويصفّي state_code ∈ (READY,LAUNCHED) —
  فحتى Outcome INVALIDATED مُدرَج يدوياً (محاكاة جدول غير نظيف) لا يدخل الإحصاءات.
- INVALIDATED وحده ⇒ لا Success Rate وهمي.

التشغيل:  python tests/test_phase5_perf_filter_regression.py
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

from services import news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, PricePoint, StockStateEvent, StockStateOutcome  # noqa: E402
from services import tracking  # noqa: E402

_passed = 0
_failed = 0
BASE = date(2026, 8, 7)  # جمعة


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _reset():
    PricePoint.query.delete(); StockStateEvent.query.delete(); StockStateOutcome.query.delete()
    db.session.commit()


def _event(ticker, state, base_price=100.0):
    ev = StockStateEvent(ticker=ticker, lifecycle_id=1, state_code=state,
                         baseline_date=BASE, performance_baseline_price=base_price)
    db.session.add(ev); db.session.commit()
    return ev.id


def _pp(ticker, price_1d):
    # الجلسة التالية بعد الجمعة = الاثنين 08-10
    db.session.merge(PricePoint(ticker=ticker, date=date(2026, 8, 10), price=price_1d))


def test_invalidated_excluded_from_metrics():
    print("\n[#1] READY(+10) + LAUNCHED(+5) + INVALIDATED(-20) ⇒ الإحصاء يعتمد READY/LAUNCHED فقط:")
    with app.app_context():
        _reset()
        rid = _event("RDY", "READY", 100.0);  _pp("RDY", 110.0)   # +10%
        lid = _event("LAU", "LAUNCHED", 100.0); _pp("LAU", 105.0)  # +5%
        iid = _event("INV", "INVALIDATED", 100.0); _pp("INV", 80.0)  # -20%
        db.session.commit()
        filled = tracking.fill_outcomes()
        # fill لا يبني نتائج INVALIDATED
        check(db.session.get(StockStateOutcome, (iid, 1)) is None,
              "fill_outcomes لم يبنِ نتيجة لحدث INVALIDATED")
        check(db.session.get(StockStateOutcome, (rid, 1)) is not None, "نتيجة READY بُنيت")
        check(db.session.get(StockStateOutcome, (lid, 1)) is not None, "نتيجة LAUNCHED بُنيت")
        # نُدرج يدوياً Outcome لـINVALIDATED (محاكاة جدول غير نظيف) لاختبار JOIN filter
        db.session.merge(StockStateOutcome(event_id=iid, horizon_days=1, exit_date=date(2026, 8, 10),
                                           close_price=80.0, return_pct=-20.0))
        db.session.commit()

        summ = tracking.performance_summary()
        st1 = summ["horizons"].get(1)
        check(st1 is not None and st1["n"] == 2, f"n=2 (READY+LAUNCHED فقط) — {st1['n'] if st1 else None}")
        check(abs(st1["avg"] - 7.5) < 1e-6, "متوسط العائد = (10+5)/2 = 7.5 (لا يتأثر بـ-20)")
        check(abs(st1["win_rate"] - 100.0) < 1e-6, "نسبة النجاح = 100% (كلاهما موجب)")
        check(abs(st1["best"] - 10.0) < 1e-6 and abs(st1["worst"] - 5.0) < 1e-6,
              "الأفضل 10 والأسوأ 5 (لا -20 من INVALIDATED)")


def test_invalidated_only_no_fake_success():
    print("\n[#1] INVALIDATED وحده ⇒ لا Success Rate وهمي:")
    with app.app_context():
        _reset()
        iid = _event("ONLY", "INVALIDATED", 100.0)
        db.session.merge(StockStateOutcome(event_id=iid, horizon_days=1, return_pct=-15.0))
        db.session.commit()
        summ = tracking.performance_summary()
        check(summ["horizons"].get(1) is None, "لا إحصاء لأي أفق (INVALIDATED مُصفّى)")
        check(summ["has_mature"] is False, "has_mature=False ⇒ الصفحة تعرض «البيانات ما زالت تتجمع»")
        check(summ["invalidated_count"] == 1, "عدّاد INVALIDATED يبقى ظاهراً (دورة حياة)")


def main():
    print("=" * 60)
    print("PHASE 5 ISSUE #1 — INVALIDATED excluded from performance")
    print("=" * 60)
    test_invalidated_excluded_from_metrics()
    test_invalidated_only_no_fake_success()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات ISSUE #1 نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        try:
            os.unlink(_dbp)
        except OSError:
            pass
    os._exit(code)
