"""
test_phase5_baseline_regression.py — PHASE 5 (F: Baseline Integrity).

يقفل:
- analysis_date = تاريخ أحدث شمعة EOD؛ analysis_close = إغلاقها.
- analysis_price يبقى سعر الاقتباس (quote) — منفصلاً، وقد يختلف عن إغلاق EOD (after-hours).
- event.analysis_price (تدقيق) ≠ event.performance_baseline_price (أساس الأداء) حيث يختلفان.
- live_price لا يغيّر أياً من الأساسين.
- سجل بلا analysis_date/analysis_close ⇒ لا حدث (بلا تخمين)، مع لقطة سليمة.

التشغيل:  python tests/test_phase5_baseline_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone, date, timedelta

os.environ["APP_PASSWORD"] = "p5"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import screener, fmp_client, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
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


def _candles():
    """60 شمعة newest-first؛ الأحدث 2026-08-06 إغلاقها 100.0 (≠ سعر الاقتباس 101.0)."""
    out = []
    d0 = date(2026, 8, 6)
    for i in range(60):
        d = d0 - timedelta(days=i)
        px = 100.0 - i * 0.1
        out.append({"date": d.isoformat(), "open": px, "high": px + 1,
                    "low": px - 1, "close": px, "volume": 1_000_000})
    return out


def _tilt(kind):
    table = {"neu": (2, 1, 2)}
    b, n, r = table[kind]
    out = [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


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


def _run(today):
    recs, _ = screener.load_records()
    return tracking.record_nightly_tracking(recs, today=today)


def test_build_record_separation():
    print("\n[F] _build_record يفصل analysis_price (quote) عن analysis_close (EOD):")
    cds = _candles()
    fmp_client.get_quote = lambda t, **k: {"ticker": t, "name": "X", "price": 101.0,
                                           "change_percent": 1.0, "market_cap": 1e9}
    fmp_client.get_profile = lambda t, **k: {"ticker": t, "name": "X Co", "sector": "Technology"}
    fmp_client.get_financials = lambda t, **k: {"income": None, "balance": None, "cashflow": None}
    fmp_client.get_historical_prices = lambda t, **k: cds
    with app.app_context():
        rec = screener._build_record("XX")
    check(rec is not None, "السجل بُني")
    check(rec["analysis_date"] == "2026-08-06", "analysis_date = تاريخ أحدث شمعة EOD")
    check(abs(rec["analysis_close"] - 100.0) < 1e-9, "analysis_close = إغلاق EOD (100.0)")
    check(abs(rec["analysis_price"] - 101.0) < 1e-9, "analysis_price يبقى سعر الاقتباس (101.0)")
    check(rec["analysis_price"] != rec["analysis_close"],
          "quote ≠ EOD close مدعوم (after-hours) — الفصل محفوظ")


def test_event_baseline_frozen_and_separate():
    print("\n[F] الحدث: analysis_price (تدقيق) ≠ performance_baseline_price (أداء)، ومجمّدان:")
    with app.app_context():
        StockCache.query.delete(); StockSnapshot.query.delete(); StockStateEvent.query.delete()
        db.session.commit()
        rec = {"ticker": "SEP", "catalyst": 85, "piotroski": 6, "indicators": _tilt("neu"),
               "analysis_price": 101.0, "analysis_close": 100.0, "analysis_date": "2026-08-06"}
        _seed(rec)
        _run(date(2026, 8, 7))
        ev = StockStateEvent.query.filter_by(ticker="SEP", state_code="READY").first()
        check(ev is not None, "حدث READY أُنشئ")
        check(abs(ev.analysis_price - 101.0) < 1e-9, "event.analysis_price = 101 (تدقيق)")
        check(abs(ev.performance_baseline_price - 100.0) < 1e-9,
              "event.performance_baseline_price = 100 (إغلاق EOD)")
        check(ev.analysis_price != ev.performance_baseline_price, "الأساسان مختلفان")
        check(ev.baseline_date == date(2026, 8, 6), "baseline_date = جلسة التحليل")

        # live_price لا يغيّر الأساسين
        rec2 = dict(rec); rec2["live_price"] = 555.0
        _seed(rec2)
        _run(date(2026, 8, 8))
        ev2 = StockStateEvent.query.filter_by(ticker="SEP", state_code="READY").first()
        check(abs(ev2.performance_baseline_price - 100.0) < 1e-9,
              "live_price لم يغيّر performance_baseline_price")
        check(abs(ev2.analysis_price - 101.0) < 1e-9, "live_price لم يغيّر analysis_price")


def test_old_record_no_baseline_no_event():
    print("\n[F] سجل بلا analysis_date/analysis_close ⇒ لا حدث (بلا تخمين)، لقطة سليمة:")
    with app.app_context():
        StockCache.query.delete(); StockSnapshot.query.delete(); StockStateEvent.query.delete()
        db.session.commit()
        # سجل قديم: READY لكن بلا مرساة أداء
        rec = {"ticker": "OLD", "catalyst": 85, "piotroski": 6, "indicators": _tilt("neu"),
               "price": 100.0, "analysis_price": 100.0}
        _seed(rec)
        r = _run(date(2026, 8, 7))
        check(StockStateEvent.query.filter_by(ticker="OLD").count() == 0,
              "لا حدث بلا baseline موثوق (لا تخمين)")
        check(StockSnapshot.query.filter_by(ticker="OLD").count() == 1,
              "اللقطة كُتبت رغم غياب الأساس (لا HTTP 500)")

        # analysis_date موجود لكن analysis_close مفقود ⇒ ما زال لا حدث
        rec2 = dict(rec); rec2["analysis_date"] = "2026-08-06"  # بلا analysis_close
        _seed(rec2)
        _run(date(2026, 8, 8))
        check(StockStateEvent.query.filter_by(ticker="OLD").count() == 0,
              "analysis_close مفقود ⇒ لا حدث")


def main():
    print("=" * 60)
    print("PHASE 5 — Baseline Integrity (F)")
    print("=" * 60)
    test_build_record_separation()
    test_event_baseline_frozen_and_separate()
    test_old_record_no_baseline_no_event()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (F) نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
