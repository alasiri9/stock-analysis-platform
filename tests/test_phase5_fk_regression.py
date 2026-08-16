"""
test_phase5_fk_regression.py — PHASE 5 ISSUE #3 (LOW).

StockStateOutcome.event_id مفتاح خارجي حقيقي على stock_state_event.id مع ON DELETE CASCADE.
- حدث حقيقي + نتائج 1/5/10/20 تعمل طبيعياً.
- Outcome يشير لحدث غير موجود ⇒ القاعدة ترفضه (FK مفعّل — PRAGMA على SQLite، أصلاً على PostgreSQL).
- حذف الحدث ⇒ نتائجه تُحذف تعاقبياً (لا يتيمة).
- performance_summary لا يرى نتائج يتيمة (لا يمكن وجودها أصلاً + JOIN).
- PK (event_id, horizon_days): يسمح بأربعة آفاق للحدث ويمنع تكرار الأفق نفسه.

التشغيل:  python tests/test_phase5_fk_regression.py
"""

import os
import sys
import tempfile
from datetime import date

os.environ["APP_PASSWORD"] = "p5"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockStateEvent, StockStateOutcome  # noqa: E402
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


def _reset():
    StockStateOutcome.query.delete(); StockStateEvent.query.delete()
    db.session.commit()


def _event(ticker="FK", state="READY"):
    ev = StockStateEvent(ticker=ticker, lifecycle_id=1, state_code=state)
    db.session.add(ev); db.session.commit()
    return ev.id


def test_fk_is_enforced():
    print("\n[#3] FK مفعّل فعلياً (اختبار PRAGMA على SQLite):")
    with app.app_context():
        res = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
        check(res == 1, f"PRAGMA foreign_keys=ON ({res})")


def test_valid_event_four_horizons():
    print("\n[#3] حدث حقيقي + أربعة آفاق تعمل طبيعياً:")
    with app.app_context():
        _reset()
        eid = _event()
        for h in (1, 5, 10, 20):
            db.session.merge(StockStateOutcome(event_id=eid, horizon_days=h, return_pct=float(h)))
        db.session.commit()
        check(StockStateOutcome.query.filter_by(event_id=eid).count() == 4,
              "أربع نتائج (1/5/10/20) للحدث نفسه")


def test_orphan_rejected():
    print("\n[#3] Outcome لحدث غير موجود ⇒ القاعدة ترفضه:")
    with app.app_context():
        _reset()
        rejected = False
        try:
            db.session.add(StockStateOutcome(event_id=999999, horizon_days=1, return_pct=1.0))
            db.session.commit()
        except IntegrityError:
            rejected = True
            db.session.rollback()
        check(rejected, "IntegrityError على event_id غير موجود (FK)")
        check(StockStateOutcome.query.filter_by(event_id=999999).count() == 0, "لا صف يتيم")


def test_cascade_delete():
    print("\n[#3] حذف الحدث ⇒ حذف نتائجه تعاقبياً (لا يتيمة):")
    with app.app_context():
        _reset()
        eid = _event("CAS")
        for h in (1, 5, 10, 20):
            db.session.merge(StockStateOutcome(event_id=eid, horizon_days=h, return_pct=1.0))
        db.session.commit()
        StockStateEvent.query.filter_by(id=eid).delete()  # DELETE خام ⇒ ON DELETE CASCADE
        db.session.commit()
        check(StockStateOutcome.query.filter_by(event_id=eid).count() == 0,
              "نتائج الحدث المحذوف اختفت (CASCADE)")
        # performance_summary لا يرى شيئاً يتيماً
        summ = tracking.performance_summary()
        check(all(summ["horizons"].get(h) is None for h in (1, 5, 10, 20)),
              "performance_summary بلا نتائج يتيمة")


def test_horizon_uniqueness():
    print("\n[#3] (event_id, horizon_days): يمنع تكرار الأفق نفسه:")
    with app.app_context():
        _reset()
        eid = _event("UNQ")
        db.session.add(StockStateOutcome(event_id=eid, horizon_days=5, return_pct=1.0))
        db.session.commit()
        dup = False
        try:
            db.session.add(StockStateOutcome(event_id=eid, horizon_days=5, return_pct=2.0))
            db.session.commit()
        except IntegrityError:
            dup = True
            db.session.rollback()
        check(dup, "تكرار (event_id, horizon=5) مرفوض (PK)")
        check(StockStateOutcome.query.filter_by(event_id=eid).count() == 1, "صف أفق واحد لكل قيمة")


def main():
    print("=" * 60)
    print("PHASE 5 ISSUE #3 — Foreign Key + CASCADE")
    print("=" * 60)
    test_fk_is_enforced()
    test_valid_event_four_horizons()
    test_orphan_rejected()
    test_cascade_delete()
    test_horizon_uniqueness()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات ISSUE #3 نجحت ✓ ({_passed} تحقّقاً).")
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
