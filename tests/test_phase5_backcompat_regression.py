"""
test_phase5_backcompat_regression.py — PHASE 5 (I: Backward Compatibility).

سجلّات قديمة/جزئية يجب أن تُصيّر HTTP 200 (لا 500) رغم غياب حقول PHASE 5:
- بلا state · بلا analysis_date · بلا analysis_close · بلا لقطة · بلا أحداث · بلا target/stop ·
  Catalyst/Piotroski جزئية (None).
وتتبّع PHASE 5 على سجل قديم لا يُنشئ حدثاً (بلا أساس) لكن يكتب لقطة بلا انهيار.

التشغيل:  python tests/test_phase5_backcompat_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone, date

# وضع مفتوح: بلا تسجيل دخول + صلاحيات مدير (مثل smoke_test)
os.environ.pop("APP_PASSWORD", None)
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import fmp_client, news_client  # noqa: E402
fmp_client.get_quote = lambda *a, **k: None
fmp_client.get_historical_prices = lambda *a, **k: None
news_client.get_market_news = lambda *a, **k: []
from services import screener  # noqa: E402
from app import app  # noqa: E402
from models import db, StockCache, StockSnapshot, StockStateEvent  # noqa: E402
from services import tracking  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _seed(rec):
    with app.app_context():
        key = screener._PREFIX + rec["ticker"]
        payload = json.dumps(rec, ensure_ascii=False)
        row = db.session.get(StockCache, key)
        if row:
            row.data_json = payload
        else:
            db.session.add(StockCache(ticker=key, data_json=payload,
                                      updated_at=datetime.now(timezone.utc)))
        db.session.commit()


# سجلّات قديمة/جزئية بلا أي حقول PHASE 5
OLD_MINIMAL = {"ticker": "OLDM", "name": "Old Minimal", "sector": "Technology",
               "price": 50.0, "analysis_price": 50.0, "catalyst": None, "piotroski": None,
               "indicators": []}
OLD_PARTIAL = {"ticker": "OLDP", "name": "Old Partial", "sector": "Technology",
               "price": 80.0, "analysis_price": 80.0, "catalyst": 85, "piotroski": 8,
               # piotroski_computable مفقود (توافق خلفي ⇒ 9)، لا state، لا analysis_date/close،
               "indicators": [{"label": "EMA", "status": "bull"}], "money_flow": None,
               "break_status": None, "sustained": None, "atr_plan": None}

PAGES = ["/", "/gems", "/leaders", "/prelaunch", "/algomatix", "/structure",
         "/frames", "/plans", "/flow", "/changes", "/signal-performance",
         "/stock/OLDM", "/stock/OLDP"]


def test_pages_render_200():
    print("\n[I] صفحات تُصيّر 200 مع سجلّات قديمة/جزئية:")
    _seed(OLD_MINIMAL)
    _seed(OLD_PARTIAL)
    client = app.test_client()
    ok_codes = (200, 301, 302, 303, 304, 308)
    for path in PAGES:
        try:
            code = client.get(path).status_code
        except Exception as e:  # noqa: BLE001
            code = None
            print(f"      استثناء عند {path}: {type(e).__name__}: {e}")
        check(code in ok_codes, f"{path} ⇒ {code}")


def test_tracking_old_record_no_event_no_crash():
    print("\n[I] تتبّع سجل قديم: لقطة بلا حدث، بلا انهيار:")
    with app.app_context():
        StockSnapshot.query.delete(); StockStateEvent.query.delete()
        db.session.commit()
        recs, _ = screener.load_records()
        r = tracking.record_nightly_tracking(recs, today=date(2026, 8, 7))
        check(r["errors"] == 0, "لا أخطاء في التتبّع")
        check(r["snapshots"] >= 2, "لقطات كُتبت للسجلات القديمة")
        check(StockStateEvent.query.count() == 0, "لا أحداث (السجلات القديمة بلا أساس)")


def test_changes_after_tracking_200():
    print("\n[I] /changes و /signal-performance بعد التتبّع ⇒ 200:")
    client = app.test_client()
    for path in ("/changes", "/signal-performance"):
        code = client.get(path).status_code
        check(code == 200, f"{path} ⇒ {code}")


def main():
    print("=" * 60)
    print("PHASE 5 — Backward Compatibility (I)")
    print("=" * 60)
    test_pages_render_200()
    test_tracking_old_record_no_event_no_crash()
    test_changes_after_tracking_200()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (I) نجحت ✓ ({_passed} تحقّقاً).")
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
