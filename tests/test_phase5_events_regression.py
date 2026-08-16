"""
test_phase5_events_regression.py — PHASE 5 (C: Events · D: Snapshots).

يقفل:
- READY يُنشئ حدثاً واحداً؛ التكرار لا يكرّره؛ READY→NEAR_READY→READY نفس lifecycle.
- INVALIDATED يغلق lifecycle؛ READY بعده = lifecycle جديد.
- حماية التكرار بـUNIQUE + SAVEPOINT (لا يُسقط اللقطة).
- live_price لا يولّد أحداثاً.
- ترتيب اللقطات: prev يستبعد يوم اليوم؛ retry نفس الليلة لا يكرّر؛ فشل السهم لا يترك نصف كتابة.

التشغيل:  python tests/test_phase5_events_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone, date

os.environ["APP_PASSWORD"] = "p5"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import screener, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockCache, StockSnapshot, StockStateEvent  # noqa: E402
from services import tracking  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _tilt(kind):
    table = {"neu": (2, 1, 2), "pos1": (3, 0, 2), "neg1": (2, 0, 3), "neg2": (1, 0, 4)}
    b, n, r = table[kind]
    out = [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


def _rec(ticker, catalyst=85, tilt="neu", adate="2026-08-01", aclose=100.0, **extra):
    r = {"ticker": ticker, "catalyst": catalyst, "indicators": _tilt(tilt) if tilt else [],
         "analysis_price": aclose, "analysis_date": adate, "analysis_close": aclose,
         "piotroski": 6}
    r.update(extra)
    return r


def _seed(rec):
    key = screener._PREFIX + rec["ticker"]
    payload = json.dumps(rec, ensure_ascii=False)
    row = db.session.get(StockCache, key)
    if row:
        row.data_json = payload
    else:
        db.session.add(StockCache(ticker=key, data_json=payload,
                                  updated_at=datetime.now(timezone.utc)))
    db.session.commit()


def _clear():
    StockCache.query.delete()
    StockSnapshot.query.delete()
    StockStateEvent.query.delete()
    db.session.commit()


def _run(today, only=None):
    recs, _ = screener.load_records()
    if only:
        recs = [r for r in recs if r["ticker"] == only]
    return tracking.record_nightly_tracking(recs, today=today)


def _events(ticker, state=None):
    q = StockStateEvent.query.filter_by(ticker=ticker)
    if state:
        q = q.filter_by(state_code=state)
    return q.count()


# ==================== C) Events ====================
def test_first_ready_one_event():
    print("\n[C] أول READY ⇒ حدث واحد:")
    with app.app_context():
        _clear()
        _seed(_rec("RDY", catalyst=85, tilt="neu"))
        _run(date(2026, 8, 2))
        check(_events("RDY", "READY") == 1, "READY واحد أُنشئ")


def test_repeated_ready_no_dup():
    print("\n[C] تكرار READY ⇒ بلا تكرار:")
    with app.app_context():
        _run(date(2026, 8, 3))  # نفس السهم ما زال READY يوماً آخر
        _run(date(2026, 8, 4))
        check(_events("RDY", "READY") == 1, "ما زال حدث READY واحداً بعد أيام")


def test_ready_dip_ready_same_lifecycle():
    print("\n[C] READY→NEAR_READY→READY ⇒ نفس lifecycle (لا حدث READY جديد):")
    with app.app_context():
        _seed(_rec("RDY", catalyst=85, tilt="neg1"))   # NEAR_READY
        _run(date(2026, 8, 5))
        _seed(_rec("RDY", catalyst=85, tilt="neu"))    # READY ثانيةً
        _run(date(2026, 8, 6))
        check(_events("RDY", "READY") == 1, "لا حدث READY جديد لنفس الدورة")


def test_invalidated_then_new_lifecycle():
    print("\n[C] INVALIDATED يغلق lifecycle ثم READY = دورة جديدة:")
    with app.app_context():
        # كسر مؤكّد مع lifecycle مفتوح (READY موجود) ⇒ INVALIDATED
        _seed(_rec("RDY", catalyst=50, tilt="neg2",
                   break_status={"confirmed": True, "dir": "breakdown"}))
        _run(date(2026, 8, 7))
        check(_events("RDY", "INVALIDATED") == 1, "INVALIDATED أُنشئ (أغلق الدورة)")
        check(tracking.lifecycle_id("RDY") == 2, "lifecycle_id ارتفع إلى 2")
        # setup جديد يعود READY ⇒ حدث READY جديد لأن lid=2
        _seed(_rec("RDY", catalyst=85, tilt="neu"))
        _run(date(2026, 8, 8))
        check(_events("RDY", "READY") == 2, "READY جديد مسموح بعد الإغلاق (دورة 2)")


def test_unique_constraint_blocks_retry():
    print("\n[C] UNIQUE(ticker,lifecycle_id,state_code) يمنع تكرار مباشر:")
    with app.app_context():
        _clear()
        db.session.add(StockStateEvent(ticker="UX", lifecycle_id=1, state_code="READY"))
        db.session.commit()
        raised = False
        try:
            db.session.add(StockStateEvent(ticker="UX", lifecycle_id=1, state_code="READY"))
            db.session.commit()
        except IntegrityError:
            raised = True
            db.session.rollback()
        check(raised, "إدراج مكرّر يرفع IntegrityError")
        check(StockStateEvent.query.filter_by(ticker="UX", state_code="READY").count() == 1,
              "صف واحد فقط بقي")


def test_dup_race_keeps_snapshot():
    print("\n[C] سباق تكرار الحدث (IntegrityError عند commit) لا يُسقط اللقطة:")
    with app.app_context():
        _clear()
        # حدث READY موجود مسبقاً لدورة 1
        db.session.add(StockStateEvent(ticker="RC", lifecycle_id=1, state_code="READY"))
        db.session.commit()
        _seed(_rec("RC", catalyst=85, tilt="neu"))
        # نُجبر _pending_event على إرجاع حدث مكرّر (يتجاوز pre-check) لمحاكاة السباق النادر
        orig = tracking._pending_event

        def force_dup(record, ticker, code, lid, prev_code, today):
            if ticker == "RC" and code == "READY":
                return StockStateEvent(ticker="RC", lifecycle_id=1, state_code="READY",
                                       baseline_date=None, performance_baseline_price=100.0)
            return orig(record, ticker, code, lid, prev_code, today)

        tracking._pending_event = force_dup
        try:
            _run(date(2026, 8, 9))
        finally:
            tracking._pending_event = orig
        check(StockStateEvent.query.filter_by(ticker="RC", state_code="READY").count() == 1,
              "لا حدث READY مكرّر (UNIQUE منعه)")
        check(StockSnapshot.query.filter_by(ticker="RC", snap_date=date(2026, 8, 9)).first() is not None,
              "اللقطة كُتبت رغم سباق التكرار (fallback)")


def test_live_price_zero_events():
    print("\n[C] live_price لا يولّد أحداثاً:")
    with app.app_context():
        _clear()
        _seed(_rec("LV", catalyst=85, tilt="neu"))
        _run(date(2026, 8, 2))
        before = _events("LV")
        # تحديث لحظي: نضيف live_price فقط (كما يفعل refresh_prices_intraday) ونعيد التتبّع
        rec = _rec("LV", catalyst=85, tilt="neu")
        rec["live_price"] = 500.0
        _seed(rec)
        _run(date(2026, 8, 2))  # نفس اليوم
        check(_events("LV") == before, "لا أحداث جديدة بسبب live_price")


# ==================== D) Snapshots ====================
def test_snapshot_first_and_prev_excludes_today():
    print("\n[D] لقطة أولى + prev يستبعد يوم اليوم:")
    with app.app_context():
        _clear()
        _seed(_rec("SN", catalyst=85, tilt="neu"))
        _run(date(2026, 8, 10))
        s1 = StockSnapshot.query.filter_by(ticker="SN").count()
        check(s1 == 1, "لقطة أولى أُنشئت")
        # اليوم التالي: prev يجب أن يكون لقطة أمس لا اليوم
        prev = (StockSnapshot.query
                .filter(StockSnapshot.ticker == "SN", StockSnapshot.snap_date < date(2026, 8, 11))
                .order_by(StockSnapshot.snap_date.desc()).first())
        check(prev is not None and prev.snap_date == date(2026, 8, 10),
              "prev (snap_date < today) = لقطة أمس")
        _run(date(2026, 8, 11))
        check(StockSnapshot.query.filter_by(ticker="SN").count() == 2, "لقطة ثانية ليوم مختلف")


def test_snapshot_retry_same_night_no_dup():
    print("\n[D] retry نفس الليلة ⇒ نفس المفتاح، لا تكرار:")
    with app.app_context():
        n1 = StockSnapshot.query.filter_by(ticker="SN", snap_date=date(2026, 8, 11)).count()
        _run(date(2026, 8, 11))  # إعادة نفس الليلة
        n2 = StockSnapshot.query.filter_by(ticker="SN", snap_date=date(2026, 8, 11)).count()
        check(n1 == 1 and n2 == 1, "صف لقطة واحد لليوم رغم إعادة التشغيل")


def test_failed_ticker_no_partial(monkeypatch=None):
    print("\n[D] فشل معاملة السهم ⇒ لا حدث ولا لقطة (لا نصف كتابة):")
    with app.app_context():
        _clear()
        _seed(_rec("BOOM", catalyst=85, tilt="neu"))
        _seed(_rec("OKS", catalyst=85, tilt="neu"))
        orig = tracking._persist_state_into_cache

        def boom(ticker, state):
            if ticker == "BOOM":
                raise RuntimeError("انفجار اختبار")
            return orig(ticker, state)

        tracking._persist_state_into_cache = boom
        try:
            _run(date(2026, 8, 12))
        finally:
            tracking._persist_state_into_cache = orig
        check(StockSnapshot.query.filter_by(ticker="BOOM").count() == 0, "لا لقطة للسهم الفاشل")
        check(_events("BOOM") == 0, "لا حدث للسهم الفاشل (تراجع كامل)")
        check(StockSnapshot.query.filter_by(ticker="OKS").count() == 1,
              "السهم السليم في نفس الدفعة عولج بنجاح")


def main():
    print("=" * 60)
    print("PHASE 5 — Events + Snapshots (C + D)")
    print("=" * 60)
    test_first_ready_one_event()
    test_repeated_ready_no_dup()
    test_ready_dip_ready_same_lifecycle()
    test_invalidated_then_new_lifecycle()
    test_unique_constraint_blocks_retry()
    test_dup_race_keeps_snapshot()
    test_live_price_zero_events()
    test_snapshot_first_and_prev_excludes_today()
    test_snapshot_retry_same_night_no_dup()
    test_failed_ticker_no_partial()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (C+D) نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
