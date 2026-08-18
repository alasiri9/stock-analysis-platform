"""
test_phase6_persistence.py — PHASE 6 F2 (Nightly Data Confidence Persistence).

يقفل حفظ ناتج data_confidence داخل StockSnapshot.extra_json أثناء التتبّع الليلي:
- read-merge-dict: يحافظ على مفاتيح extra_json الموجودة، ويعدّل data_confidence فقط، بخرج حتمي (sort_keys).
- سياسة صارمة للـJSON التالف/غير المطابق للعقد: يُبقى النص الأصلي حرفياً بلا استبدال.
- مرجع الجلسة = MAX(PricePoint SPY) مع حراسة الأغلبية (لا analysis_date كمرجع لنفسه، لا تقويم/مكتبة/API).
- فشل حساب الثقة لا يحجب اللقطة (unavailable + reason_code، بلا نص Exception في JSON).
- لا Migration، لا تعديل models، لا live_price، لا API، لا كتابة من GET، لا مساس state/events/outcomes.

التشغيل:  python tests/test_phase6_persistence.py
          python -m pytest tests/test_phase6_persistence.py -q
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, date, timedelta

os.environ["APP_PASSWORD"] = "p6"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import screener, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockCache, StockSnapshot, StockStateEvent, PricePoint  # noqa: E402
from services import tracking  # noqa: E402
from services.confidence import (  # noqa: E402
    CONFIDENCE_TECHNICAL_INDICATOR_KEYS, CAP_LOW_MAX, CAP_MEDIUM_MAX,
    REASON_EXPECTED_MISSING, REASON_DATE_FUTURE, SCHEMA_VERSION,
)

_passed = 0
_failed = 0
RUN = date(2026, 8, 17)          # يوم التشغيل الثابت (يُحقن — لا now())
SPYD = date(2026, 8, 17)         # جلسة SPY المطابقة (طازجة)


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")
        raise AssertionError(label)


def _full_indicators():
    return [{"label": k, "value": "x", "status": "bull"} for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]


def _rec(ticker, adate="2026-08-17", aclose=100.0, catalyst=72, pc=9, **extra):
    r = {
        "ticker": ticker, "catalyst": catalyst, "catalyst_complete": True,
        "piotroski": 6, "piotroski_computable": pc,
        "indicators": _full_indicators(),
        "analysis_price": aclose, "analysis_date": adate, "analysis_close": aclose,
        "structure": {"trend": "up", "status": "bull"},
        "frames": {"weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull"},
    }
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


def _spy(*dates):
    for d in dates:
        db.session.merge(PricePoint(ticker=screener.MARKET_BENCHMARK, date=d, price=400.0))
    db.session.commit()


def _clear():
    StockCache.query.delete()
    StockSnapshot.query.delete()
    StockStateEvent.query.delete()
    PricePoint.query.delete()
    db.session.commit()


def _run(today=RUN, only=None):
    recs, _ = screener.load_records()
    if only:
        recs = [r for r in recs if r["ticker"] == only]
    return tracking.record_nightly_tracking(recs, today=today)


def _snap(ticker, snap=RUN):
    return StockSnapshot.query.filter_by(ticker=ticker, snap_date=snap).first()


def _conf(ticker, snap=RUN):
    row = _snap(ticker, snap)
    if not row or not row.extra_json:
        return None
    try:
        parsed = json.loads(row.extra_json)
    except (ValueError, TypeError):
        return None
    return parsed.get("data_confidence") if isinstance(parsed, dict) else None


def _preset_extra(ticker, raw, snap=RUN):
    """يضع صف لقطة لليوم بقيمة extra_json خام (لاختبار read-merge/التالف)."""
    db.session.merge(StockSnapshot(ticker=ticker, snap_date=snap, extra_json=raw))
    db.session.commit()


# ═══════════════ 1) الحفظ الصحيح + schema_version ═══════════════
def test_1_saves_confidence():
    print("\n[1] حفظ Data Confidence داخل extra_json + schema_version=1:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A1"))
        _run()
        c = _conf("A1")
        check(isinstance(c, dict), "data_confidence dict محفوظ")
        check("score" in c and "band" in c and "factors" in c, "الحقول الأساسية موجودة")
        check(c.get("schema_version") == SCHEMA_VERSION, "schema_version = 1")
        check(c["band"] == "high" and c["score"] == 100, "سجل كامل وطازج ⇒ 100 High")


# ═══════════════ 2) read-merge-dict: حفظ المفاتيح + استبدال data_confidence فقط ═══════════════
def test_2_preserves_existing_key():
    print("\n[2] مفتاح قديم في extra_json يُحفظ عند إضافة data_confidence:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A2"))
        _preset_extra("A2", json.dumps({"foo": "bar"}))
        _run()
        raw = json.loads(_snap("A2").extra_json)
        check(raw.get("foo") == "bar", "المفتاح القديم foo محفوظ")
        check(isinstance(raw.get("data_confidence"), dict), "data_confidence أُضيف")


def test_3_replaces_only_data_confidence():
    print("\n[3] استبدال data_confidence القديم فقط (بلا مسّ المفاتيح الأخرى):")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A3"))
        _preset_extra("A3", json.dumps({"foo": "keep", "data_confidence": {"old": True}}))
        _run()
        raw = json.loads(_snap("A3").extra_json)
        check(raw.get("foo") == "keep", "foo باقٍ")
        check("old" not in raw["data_confidence"], "القيمة القديمة استُبدلت")
        check(raw["data_confidence"].get("schema_version") == SCHEMA_VERSION, "الجديدة محسوبة")


# ═══════════════ 3) None / فارغ ═══════════════
def test_4_extra_none():
    print("\n[4] extra_json=None ⇒ يبدأ dict جديد ويحفظ data_confidence:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A4"))
        _run()
        check(isinstance(_conf("A4"), dict), "data_confidence محفوظ من None")


def test_5_extra_empty_string():
    print("\n[5] extra_json='' ⇒ يبدأ dict جديد:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A5"))
        _preset_extra("A5", "")
        _run()
        check(isinstance(_conf("A5"), dict), "data_confidence محفوظ من نص فارغ")


# ═══════════════ 4) JSON التالف / غير المطابق للعقد ⇒ يبقى حرفياً ═══════════════
def test_6_corrupt_json_kept_verbatim():
    print("\n[6] JSON تالف ⇒ النص الخام يبقى مطابقاً حرفياً (بلا data_confidence):")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("A6"))
        corrupt = "{not valid json ::"
        _preset_extra("A6", corrupt)
        _run()
        check(_snap("A6").extra_json == corrupt, "النص التالف لم يتغيّر حرفياً")
        check(_conf("A6") is None, "لم يُضَف data_confidence لنص تالف")


def test_7_valid_non_dict_kept_verbatim():
    print("\n[7] JSON صالح لكنه list/number/string ⇒ يبقى مطابقاً (غير مطابق للعقد):")
    with app.app_context():
        _clear(); _spy(SPYD)
        for tk, raw in (("L", "[1, 2, 3]"), ("N", "42"), ("S", "\"hello\"")):
            _seed(_rec(tk))
            _preset_extra(tk, raw)
        _run()
        for tk, raw in (("L", "[1, 2, 3]"), ("N", "42"), ("S", "\"hello\"")):
            check(_snap(tk).extra_json == raw, f"{tk}: {raw} بقي حرفياً")
            check(_conf(tk) is None, f"{tk}: لا data_confidence")


# ═══════════════ 5) فشل حساب الثقة ⇒ unavailable (بلا نص Exception) ═══════════════
def test_8_confidence_failure_unavailable():
    print("\n[8] فشل data_confidence ⇒ unavailable + reason_code (بلا نص Exception في JSON):")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("F1"))
        orig = tracking.data_confidence

        def boom(*a, **k):
            raise ValueError("SECRET_BOOM_TOKEN")

        tracking.data_confidence = boom
        try:
            _run()
        finally:
            tracking.data_confidence = orig
        row = _snap("F1")
        c = _conf("F1")
        check(c and c.get("unavailable") is True, "unavailable=True")
        check(c.get("reason_code") == "confidence_computation_failed", "reason_code المعتمد")
        check(c.get("schema_version") == SCHEMA_VERSION, "schema_version موجود")
        check("SECRET_BOOM_TOKEN" not in row.extra_json, "نص Exception غير مسرَّب في JSON")


# ═══════════════ 6) same-day retry ═══════════════
def test_9_retry_single_snapshot():
    print("\n[9] same-day retry ⇒ لقطة واحدة فقط:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("R1"))
        _run(); _run(); _run()
        check(StockSnapshot.query.filter_by(ticker="R1", snap_date=RUN).count() == 1,
              "صف لقطة واحد رغم إعادة التشغيل")


def test_10_retry_deterministic_json():
    print("\n[10] same-day retry ⇒ ناتج JSON حتمي (متطابق حرفياً):")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("R2"))
        _run(); first = _snap("R2").extra_json
        _run(); second = _snap("R2").extra_json
        check(first == second, "extra_json متطابق بين تشغيلين")


# ═══════════════ 7) أول لقطة + لقطة اليوم ليست previous لنفسها ═══════════════
def test_11_first_snapshot_and_not_own_previous():
    print("\n[11] أول لقطة + prev (snap_date < today) لا يشمل اليوم:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("P1"))
        _run()
        check(_snap("P1") is not None, "لقطة اليوم أُنشئت")
        prev = (StockSnapshot.query
                .filter(StockSnapshot.ticker == "P1", StockSnapshot.snap_date < RUN)
                .order_by(StockSnapshot.snap_date.desc()).first())
        check(prev is None, "لا previous (لقطة اليوم ليست previous لنفسها)")


# ═══════════════ 8) فشل DB لسهم ⇒ rollback ولا يوقف البقية ═══════════════
def test_12_db_failure_isolated():
    print("\n[12] فشل معاملة سهم ⇒ لا لقطة له، والبقية تُعالَج:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("BOOM")); _seed(_rec("OKS"))
        orig = tracking._persist_state_into_cache

        def boom(ticker, state):
            if ticker == "BOOM":
                raise RuntimeError("انفجار اختبار")
            return orig(ticker, state)

        tracking._persist_state_into_cache = boom
        try:
            _run()
        finally:
            tracking._persist_state_into_cache = orig
        check(_snap("BOOM") is None, "لا لقطة للسهم الفاشل (rollback)")
        check(_snap("OKS") is not None and isinstance(_conf("OKS"), dict),
              "السهم السليم عولج وحُفظت ثقته")


# ═══════════════ 9) مرجع الجلسة SPY + حراسة الأغلبية (دالة نقية) ═══════════════
def test_13_spy_present_is_reference():
    print("\n[13] SPY موجود بلا أغلبية أحدث ⇒ هو المرجع:")
    recs = [{"analysis_date": "2026-08-17"}, {"analysis_date": "2026-08-17"}]
    check(tracking.resolve_expected_session(SPYD, recs, RUN) == SPYD, "expected = تاريخ SPY")


def test_14_spy_absent_none():
    print("\n[14] لا تاريخ SPY ⇒ expected=None:")
    check(tracking.resolve_expected_session(None, [{"analysis_date": "2026-08-17"}], RUN) is None,
          "None عند غياب SPY")


def test_15_spy_after_run_date_none():
    print("\n[15] SPY أحدث من run_date ⇒ مرجع غير صالح ⇒ None:")
    check(tracking.resolve_expected_session(date(2026, 8, 18), [], RUN) is None,
          "SPY > run_date ⇒ None")


def test_16_single_anomaly_keeps_spy():
    print("\n[16] سهم شاذّ واحد أحدث من SPY ⇒ SPY يبقى المرجع (الشاذّ Future/Low):")
    recs = [{"analysis_date": "2026-08-18"}] + [{"analysis_date": "2026-08-17"} for _ in range(4)]
    check(tracking.resolve_expected_session(SPYD, recs, RUN) == SPYD, "1/5 شاذّ لا يُبطل SPY")
    # إثبات end-to-end: السهم الأحدث يظهر Future ويأخذ Low
    with app.app_context():
        _clear(); _spy(SPYD)
        _seed(_rec("FUT", adate="2026-08-18"))   # أحدث من SPY
        for i in range(4):
            _seed(_rec(f"OK{i}", adate="2026-08-17"))
        _run()
        c = _conf("FUT")
        check(c["band"] == "low" and c["score"] <= CAP_LOW_MAX, "الشاذّ Future ⇒ Low")
        check(c["factors"]["freshness"]["points"] == 0, "حداثة 0 (مستقبلي)")
        check(any(REASON_DATE_FUTURE in r for cp in c["caps_applied"] for r in cp["reasons"]),
              "سبب مستقبلي مذكور")


def test_17_explicit_majority_newer_none_for_all():
    print("\n[17] أغلبية صريحة أحدث من SPY ⇒ expected=None للجميع (تحفّظ):")
    recs = [{"analysis_date": "2026-08-17"} for _ in range(3)] + [{"analysis_date": "2026-08-14"}]
    check(tracking.resolve_expected_session(date(2026, 8, 14), recs, RUN) is None,
          "3/4 أحدث ⇒ None")
    with app.app_context():
        _clear(); _spy(date(2026, 8, 14))
        for i in range(3):
            _seed(_rec(f"NW{i}", adate="2026-08-17"))
        _seed(_rec("OLD", adate="2026-08-14"))
        _run()
        c = _conf("NW0")
        check(c["band"] != "high" and c["score"] <= CAP_MEDIUM_MAX, "expected=None ⇒ ليست High")
        check(REASON_EXPECTED_MISSING in c["missing"], "سبب تعذّر التحقّق مذكور")


def test_18_tie_keeps_spy():
    print("\n[18] تعادل (50% تماماً) لا يُبطل SPY:")
    recs = [{"analysis_date": "2026-08-17"}, {"analysis_date": "2026-08-14"}]  # 1/2 أحدث = 50%
    check(tracking.resolve_expected_session(date(2026, 8, 14), recs, RUN) == date(2026, 8, 14),
          "50% ليست أغلبية صريحة ⇒ SPY يبقى")


def test_19_holiday_weekend_max_session():
    print("\n[19] عطلة/نهاية أسبوع ⇒ SPY يبقى آخر جلسة مخزّنة (بلا weekday algorithm):")
    with app.app_context():
        _clear()
        # جلسات SPY: خميس وجمعة فقط (لا صف للسبت/الأحد أو عطلة) — الأحدث = الجمعة
        _spy(date(2026, 8, 13), date(2026, 8, 14))
        check(tracking._spy_latest_date() == date(2026, 8, 14), "أحدث جلسة = الجمعة (تخطّى العطلة)")
        recs = [{"analysis_date": "2026-08-14"}]
        check(tracking.resolve_expected_session(tracking._spy_latest_date(), recs, date(2026, 8, 17))
              == date(2026, 8, 14), "المرجع = آخر جلسة فعلية")


# ═══════════════ 10) نقاء التكامل: لا live_price · لا كتابة GET · legacy ═══════════════
def test_20_live_price_no_effect():
    print("\n[20] live_price لا يغيّر الثقة المحفوظة:")
    with app.app_context():
        _clear(); _spy(SPYD)
        _seed(_rec("LV1"))
        _run(); base = _snap("LV1").extra_json
        _clear(); _spy(SPYD)
        r = _rec("LV1"); r["live_price"] = 999.0
        _seed(r)
        _run(); withlive = _snap("LV1").extra_json
        check(base == withlive, "extra_json متطابق مع/بلا live_price")


def test_21_read_path_does_not_rewrite():
    print("\n[21] مسار القراءة (latest_changes) لا يعيد كتابة extra_json:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("G1"))
        _run(); before = _snap("G1").extra_json
        tracking.latest_changes()  # قراءة فقط
        after = _snap("G1").extra_json
        check(before == after, "extra_json لم يتغيّر بالقراءة")


def test_22_legacy_snapshot_untouched():
    print("\n[22] لقطة قديمة بلا data_confidence لا تتغيّر عند تشغيل يوم جديد:")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("LG"))
        old_day = RUN - timedelta(days=5)
        db.session.merge(StockSnapshot(ticker="LG", snap_date=old_day, extra_json=None))
        db.session.commit()
        _run()  # اليوم فقط
        old = StockSnapshot.query.filter_by(ticker="LG", snap_date=old_day).first()
        check(old is not None and old.extra_json is None, "اللقطة القديمة بقيت extra_json=None")
        check(isinstance(_conf("LG", RUN), dict), "لقطة اليوم فقط اكتسبت الثقة")


# ═══════════════ 11) العقد التقني: عمود Text · JSON round-trip · بلا Migration ═══════════════
def test_23_extra_json_is_text_and_roundtrips():
    print("\n[23] extra_json عمود Text وقابل لـjson round-trip (Postgres+SQLite على مستوى العقد):")
    col = StockSnapshot.__table__.columns["extra_json"]
    check("TEXT" in str(col.type).upper(), "نوع العمود Text (محمول Postgres/SQLite)")
    with app.app_context():
        _clear(); _spy(SPYD); _seed(_rec("T1"))
        _run()
        raw = _snap("T1").extra_json
        check(isinstance(raw, str), "المحفوظ نص (str) — لا نوع خاص بمحرّك")
        check(isinstance(json.loads(raw), dict), "json.loads يُرجع dict (round-trip)")


def test_24_no_new_tables_or_columns():
    print("\n[24] لا Migration: أعمدة StockSnapshot ثابتة (extra_json موجود مسبقاً):")
    cols = set(StockSnapshot.__table__.columns.keys())
    expected = {"ticker", "snap_date", "analysis_price", "catalyst", "catalyst_complete",
                "piotroski", "piotroski_computable", "measures_met", "tech_tilt_kind",
                "algomatix_score", "structure_status", "state_code", "is_gem", "is_ready",
                "extra_json"}
    check(cols == expected, "لا عمود جديد أُضيف")


# ═══════════════ 12) لا مساس بالأحداث/الحالة عند إضافة الثقة (تحقّق صريح) ═══════════════
def test_25_events_untouched_by_confidence():
    print("\n[25] حفظ الثقة لا يمنع الحدث ولا يغيّر مسار الحالة (تحقّق صريح):")
    with app.app_context():
        _clear(); _spy(SPYD)
        _seed(_rec("EV", catalyst=85))  # READY (catalyst≥80 + ميل إيجابي قوي)
        r = _run()
        evs = StockStateEvent.query.filter_by(ticker="EV").all()
        check(r["snapshots"] == 1 and r["errors"] == 0, "لقطة واحدة بلا أخطاء")
        check(r["events"] == 1, "r['events'] = 1 (حدث واحد أُنشئ)")
        check(len(evs) == 1, "عدد الأحداث الفعلي في الجدول = 1 (يطابق r['events'])")
        check(evs[0].state_code == "READY", "الحدث الناتج = READY")
        check(_snap("EV").state_code == "READY", "state_code في اللقطة = READY (المسار لم يتغيّر)")
        check(isinstance(_conf("EV"), dict), "data_confidence محفوظ (لم يمنع الحدث)")


# ═══════════════ 13) صلابة recs: generator + عناصر تالفة ═══════════════
def test_26_generator_not_consumed():
    print("\n[26] recs كـgenerator بسهمين صالحين ⇒ تُحفظ اللقطتان (لا يُستهلك مبكراً):")
    with app.app_context():
        _clear(); _spy(SPYD)
        gen = (_rec(t) for t in ("GN1", "GN2"))   # generator أحادي المرور
        r = tracking.record_nightly_tracking(gen, today=RUN)
        check(r["snapshots"] == 2, "لقطتان محفوظتان (generator لم يُستهلك في resolve)")
        check(_snap("GN1") is not None and _snap("GN2") is not None, "كلا السهمين له لقطة")
        check(isinstance(_conf("GN1"), dict) and isinstance(_conf("GN2"), dict), "ثقة الاثنين محفوظة")


def test_27_corrupt_elements_do_not_stop_run():
    print("\n[27] [None, عنصر غير dict, سهم صالح] ⇒ الصحيح يُحفظ والتالفان في errors:")
    with app.app_context():
        _clear(); _spy(SPYD)
        recs = [None, 42, _rec("GOOD")]
        r = tracking.record_nightly_tracking(recs, today=RUN)
        check(r["errors"] == 2, "العنصران التالفان (None + int) محتسبان في errors")
        check(r["snapshots"] == 1, "لقطة واحدة (السهم الصحيح فقط)")
        check(_snap("GOOD") is not None and isinstance(_conf("GOOD"), dict),
              "السهم الصحيح حُفظ مع ثقته")


def test_28_resolve_ignores_non_dict_elements():
    print("\n[28] resolve_expected_session تتجاهل None/غير dict وتحسب من الصالحة فقط:")
    # عناصر تالفة مع صالحة ⇒ لا استثناء، والمرجع = SPY (لا أغلبية أحدث بين الصالحة)
    mixed = [None, 42, "x", {"analysis_date": "2026-08-17"}, {"analysis_date": "2026-08-17"}]
    check(tracking.resolve_expected_session(SPYD, mixed, RUN) == SPYD,
          "تجاهل التالف + المرجع = SPY (بلا استثناء)")
    # الأغلبية تُحسب من الصالحة فقط: 3 صالحة أحدث من SPY (None مُتجاهَل) ⇒ None
    maj = [None, {"analysis_date": "2026-08-17"}, {"analysis_date": "2026-08-17"},
           {"analysis_date": "2026-08-17"}]
    check(tracking.resolve_expected_session(date(2026, 8, 14), maj, RUN) is None,
          "أغلبية صريحة من الصالحة فقط ⇒ None")


def main():
    print("=" * 60)
    print("PHASE 6 F2 — Nightly Data Confidence Persistence")
    print("=" * 60)
    tests = [
        test_1_saves_confidence, test_2_preserves_existing_key, test_3_replaces_only_data_confidence,
        test_4_extra_none, test_5_extra_empty_string,
        test_6_corrupt_json_kept_verbatim, test_7_valid_non_dict_kept_verbatim,
        test_8_confidence_failure_unavailable,
        test_9_retry_single_snapshot, test_10_retry_deterministic_json,
        test_11_first_snapshot_and_not_own_previous, test_12_db_failure_isolated,
        test_13_spy_present_is_reference, test_14_spy_absent_none, test_15_spy_after_run_date_none,
        test_16_single_anomaly_keeps_spy, test_17_explicit_majority_newer_none_for_all,
        test_18_tie_keeps_spy, test_19_holiday_weekend_max_session,
        test_20_live_price_no_effect, test_21_read_path_does_not_rewrite,
        test_22_legacy_snapshot_untouched,
        test_23_extra_json_is_text_and_roundtrips, test_24_no_new_tables_or_columns,
        test_25_events_untouched_by_confidence,
        # صلابة recs
        test_26_generator_not_consumed, test_27_corrupt_elements_do_not_stop_run,
        test_28_resolve_ignores_non_dict_elements,
    ]
    for fn in tests:
        fn()
    print("\n" + "-" * 60)
    print(f"كل اختبارات F2 Persistence نجحت ✓ ({_passed} تحقّقاً).")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        try:
            os.unlink(_dbp)
        except OSError:
            pass
    os._exit(code)
