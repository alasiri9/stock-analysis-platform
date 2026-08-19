"""
test_phase6_confidence_read.py — PHASE 6 F2 (Bulk Read: latest_confidence_map).

يقفل قراءة الثقة المجمّعة tracking.latest_confidence_map:
- أحدث لقطة فقط لكل سهم · عدة أسهم باستعلام **واحد** (لا N+1) · صياغة محمولة SQLite+PostgreSQL.
- SELECT يجلب ticker/snap_date/extra_json فقط (لا كائن كامل، لا lazy loading).
- tickers: تصفية + إزالة تكرار بأمان بلا تعديل المُدخل · فارغة/تالفة ⇒ {} بلا استعلام · سهم بلا لقطة غائب.
- _clean_tickers: str رمز واحد · dict/Mapping/bytes/bytearray ⇒ [] · generator صالح · غير النصي يُتجاهل.
- تمييز missing/corrupt يقع كاملاً في confidence_view.present_confidence_from_extra_json (لا في tracking).
- التنفيذ عبر select() صريح + db.session.execute(statement).all() (قابل للـmock)، استعلام واحد فقط.
- قراءة فقط: لا add/merge/delete/commit/flush · لا data_confidence · لا live_price/API · حتمية + JSON.
- ترجمة PostgreSQL دلالية (dialect compilation) + mock حقيقي لـdb.session.execute — بلا خادم/اتصال حيّ.

التشغيل:  python tests/test_phase6_confidence_read.py
          python -m pytest tests/test_phase6_confidence_read.py -q
"""

import json
import os
import sys
import tempfile
from datetime import date, timedelta

os.environ["APP_PASSWORD"] = "p6r"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.dialects import postgresql, sqlite  # noqa: E402
from services import screener, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockSnapshot  # noqa: E402
from services import tracking  # noqa: E402
from services.confidence import data_confidence, CONFIDENCE_TECHNICAL_INDICATOR_KEYS  # noqa: E402

_passed = 0
_failed = 0
RUN = date(2026, 8, 17)


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")
        raise AssertionError(label)


def _tech():
    return [{"label": k, "value": "x", "status": "bull"} for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]


def _record(**over):
    r = {
        "catalyst": 72, "catalyst_complete": True,
        "piotroski_computable": 9, "indicators": _tech(),
        "structure": {"trend": "up", "status": "bull"},
        "frames": {"weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull"},
        "analysis_date": "2026-08-17", "analysis_close": 100.0,
    }
    r.update(over)
    return r


def _dc_json(**over):
    """extra_json يحمل data_confidence حقيقياً (من النواة)."""
    return json.dumps({"data_confidence": data_confidence(_record(**over), RUN)}, ensure_ascii=False)


def _dc_json_for(dc):
    return json.dumps({"data_confidence": dc}, ensure_ascii=False)


def _seed_snap(ticker, snap, extra_json):
    db.session.merge(StockSnapshot(ticker=ticker, snap_date=snap, extra_json=extra_json))
    db.session.commit()


def _clear():
    StockSnapshot.query.delete()
    db.session.commit()


def _count_queries(fn):
    """يعدّ استعلامات SQL الفعلية أثناء تنفيذ fn (before_cursor_execute)."""
    counter = {"n": 0}

    def _before(conn, cursor, statement, params, ctx, many):
        counter["n"] += 1

    event.listen(db.engine, "before_cursor_execute", _before)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _before)
    return result, counter["n"]


# ═══════════════ 1) أحدث لقطة فقط + عدة أسهم باستعلام واحد ═══════════════
def test_1_latest_only_and_single_query():
    print("\n[1] أحدث لقطة فقط لكل سهم + عدة أسهم باستعلام واحد:")
    with app.app_context():
        _clear()
        # A: لقطتان — القديمة low (سعر مكسور) والأحدث high
        _seed_snap("A", RUN - timedelta(days=2), _dc_json(analysis_close=0.0))   # قديمة ⇒ low
        _seed_snap("A", RUN, _dc_json())                                          # أحدث ⇒ high
        _seed_snap("B", RUN - timedelta(days=1), _dc_json(analysis_date="2026-08-14"))  # medium (stale)
        cmap, nq = _count_queries(tracking.latest_confidence_map)
        check(cmap["A"]["band"] == "high", "A: أحدث لقطة (high) لا القديمة")
        check(cmap["B"]["band"] == "medium", "B: medium")
        check(set(cmap.keys()) == {"A", "B"}, "سهمان في الخريطة")
        check(nq == 1, f"استعلام واحد فقط (كان {nq})")


# ═══════════════ 2) tickers: تصفية + إزالة تكرار + عدم تعديل المُدخل ═══════════════
def test_2_ticker_filter_and_no_mutation():
    print("\n[2] tickers تصفية + إزالة تكرار بأمان + عدم تعديل القائمة:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        _seed_snap("B", RUN, _dc_json())
        _seed_snap("C", RUN, _dc_json())
        arg = ["A", "A", "B"]   # فيها تكرار
        snapshot = list(arg)
        cmap, nq = _count_queries(lambda: tracking.latest_confidence_map(arg))
        check(set(cmap.keys()) == {"A", "B"}, "C مُستبعَد (تصفية)")
        check(arg == snapshot, "القائمة المُدخلة لم تُعدَّل")
        check(nq == 1, f"استعلام واحد (كان {nq})")


# ═══════════════ 3) tickers فارغة ⇒ {} بلا استعلام ═══════════════
def test_3_empty_tickers_no_query():
    print("\n[3] tickers فارغة ⇒ {} بلا استعلام:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        res, nq = _count_queries(lambda: tracking.latest_confidence_map([]))
        check(res == {}, "خريطة فارغة")
        check(nq == 0, f"لا استعلام (كان {nq})")


# ═══════════════ 4) سهم بلا لقطة غائب ═══════════════
def test_4_ticker_without_snapshot_absent():
    print("\n[4] سهم بلا لقطة لا يظهر في الخريطة:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        cmap = tracking.latest_confidence_map(["A", "NOPE"])
        check("A" in cmap and "NOPE" not in cmap, "المطلوب بلا لقطة غائب")
        # None (كل الأسهم) لا يخترع أسهماً بلا لقطة
        check(set(tracking.latest_confidence_map().keys()) == {"A"}, "الكل = المتوفّر فقط")


# ═══════════════ 5) extra_json تالف/مفقود/غير dict/بلا مفتاح ⇒ غير متوفرة + تمييز missing/corrupt ═══════════════
def test_5_bad_extra_json_unavailable():
    print("\n[5] extra_json None/فارغ/تالف/JSON غير dict/بلا data_confidence ⇒ غير متوفرة + تمييز missing/corrupt:")
    with app.app_context():
        _clear()
        # (ticker, extra_json, reason متوقّع)
        cases = [
            ("N", None,                                 "missing"),   # لا بيانات
            ("E", "",                                   "missing"),   # نص فارغ
            ("W", "   ",                                "missing"),   # فراغات فقط
            ("K", json.dumps({"foo": "bar"}),           "missing"),   # dict صالح بلا data_confidence
            ("C", "{not json ::",                       "corrupt"),   # JSON لا يُفكّ
            ("L", "[1,2,3]",                            "corrupt"),   # JSON صالح لكنه ليس dict
            ("S", '"just a string"',                    "corrupt"),   # JSON صالح لكنه نص
            ("X", json.dumps({"data_confidence": 42}),  "corrupt"),   # المفتاح موجود لكنه ليس dict
        ]
        for t, ej, _ in cases:
            _seed_snap(t, RUN, ej)
        cmap = tracking.latest_confidence_map()
        for t, _ej, reason in cases:
            check(cmap[t]["available"] is False, f"{t} ⇒ غير متوفرة")
            check(cmap[t]["band_class"] == "conf-na", f"{t} ⇒ conf-na (بلا 500)")
            check(cmap[t]["reason_code"] == reason, f"{t} ⇒ reason={reason}")


# ═══════════════ 6) data_confidence صالح high/medium/low ═══════════════
def test_6_valid_bands():
    print("\n[6] data_confidence صالح ⇒ high/medium/low مع as_of من snap_date:")
    with app.app_context():
        _clear()
        _seed_snap("H", RUN, _dc_json())                                    # high
        _seed_snap("M", RUN, _dc_json(analysis_date="2026-08-14"))          # medium
        _seed_snap("L", RUN, _dc_json(analysis_close=0.0))                  # low
        cmap = tracking.latest_confidence_map()
        check(cmap["H"]["available"] and cmap["H"]["band"] == "high", "high")
        check(cmap["M"]["band"] == "medium", "medium")
        check(cmap["L"]["band"] == "low", "low")
        check(cmap["H"]["as_of"] == "2026-08-17", "as_of من snap_date")


# ═══════════════ 7) unavailable + schema غير مدعوم ═══════════════
def test_7_unavailable_and_unsupported_schema():
    print("\n[7] unavailable وschema غير مدعوم يُعالَجان عبر present_confidence:")
    with app.app_context():
        _clear()
        _seed_snap("U", RUN, _dc_json_for(
            {"schema_version": 1, "unavailable": True, "reason_code": "confidence_computation_failed"}))
        _seed_snap("S", RUN, _dc_json_for({"schema_version": 99, "score": 100, "band": "high"}))
        cmap = tracking.latest_confidence_map()
        check(cmap["U"]["available"] is False and cmap["U"]["reason_code"] == "confidence_computation_failed",
              "unavailable مع سببه")
        check(cmap["S"]["available"] is False and cmap["S"]["reason_code"] == "unsupported_schema",
              "schema=99 ⇒ unsupported_schema")


# ═══════════════ 8) no-recompute: data_confidence لا تُستدعى في القراءة ═══════════════
def test_8_no_recompute():
    print("\n[8] القراءة لا تستدعي data_confidence (monkeypatch يرفع لو استُدعيت):")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        orig = tracking.data_confidence

        def boom(*a, **k):
            raise AssertionError("data_confidence استُدعيت في القراءة!")

        tracking.data_confidence = boom
        try:
            cmap = tracking.latest_confidence_map()
        finally:
            tracking.data_confidence = orig
        check(cmap["A"]["available"] is True, "القراءة نجحت بلا إعادة حساب")


# ═══════════════ 9) write-safety: لا add/merge/delete/commit/flush ═══════════════
def test_9_no_writes():
    print("\n[9] القراءة لا تنفّذ add/merge/delete/commit/flush:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        _seed_snap("B", RUN, _dc_json(analysis_date="2026-08-14"))
        sess = db.session()   # الجلسة الأساسية (scoped_session تفوّض إليها)
        recorded = []
        orig = {}
        for m in ("add", "merge", "delete", "commit", "flush"):
            orig[m] = getattr(sess, m)
            setattr(sess, m, (lambda name: (lambda *a, **k: recorded.append(name)))(m))
        try:
            cmap = tracking.latest_confidence_map()
        finally:
            for m, f in orig.items():
                setattr(sess, m, f)
        check(recorded == [], f"لا كتابة (سُجّل: {recorded})")
        check(len(cmap) == 2, "والقراءة صحيحة")
        check(not sess.new and not sess.dirty and not sess.deleted, "لا كائنات معلّقة للكتابة")


# ═══════════════ 10) live_price لا يؤثّر (القراءة من اللقطة لا من record) ═══════════════
def test_10_no_live_price_effect():
    print("\n[10] وجود live_price داخل data_confidence المخزّن لا يغيّر الخرج:")
    with app.app_context():
        _clear()
        dc = data_confidence(_record(), RUN)
        _seed_snap("A", RUN, _dc_json_for(dc))
        dc2 = dict(dc); dc2["live_price"] = 999.0     # مفتاح دخيل
        _seed_snap("B", RUN, _dc_json_for(dc2))
        cmap = tracking.latest_confidence_map()
        a = dict(cmap["A"]); a.pop("as_of", None)
        b = dict(cmap["B"]); b.pop("as_of", None)
        check(a == b, "live_price في المخزّن لا يغيّر view-model")


# ═══════════════ 11) حتمية + JSON serialization ═══════════════
def test_11_deterministic_json():
    print("\n[11] الخرج حتمي وقابل للتسلسل JSON:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        _seed_snap("B", RUN, "{corrupt")
        m1 = tracking.latest_confidence_map()
        m2 = tracking.latest_confidence_map()
        check(m1 == m2, "نفس الخرج مرتين")
        try:
            json.dumps(m1); ok = True
        except (TypeError, ValueError):
            ok = False
        check(ok, "الخريطة قابلة للتسلسل JSON")


# ═══════════════ 12) قابلية النقل: يُترجَم SQL لـSQLite وPostgreSQL بلا تشغيل إنتاج ═══════════════
def test_12_portable_sql_compiles():
    print("\n[12] استعلام أحدث لقطة يُترجَم لـSQLite وPostgreSQL (بلا تشغيل إنتاج):")
    with app.app_context():
        for dialect, name in ((sqlite.dialect(), "SQLite"), (postgresql.dialect(), "PostgreSQL")):
            stmt = str(tracking._latest_snapshot_statement(None).compile(dialect=dialect))
            up = stmt.upper()
            check("GROUP BY" in up and "MAX(" in up and "JOIN" in up,
                  f"{name}: SQL فيه GROUP BY + MAX + JOIN")
        # مع تصفية tickers أيضاً يُترجَم
        stmt2 = str(tracking._latest_snapshot_statement(["A", "B"]).compile(
            dialect=postgresql.dialect()))
        check("IN (" in stmt2.upper() or "IN(" in stmt2.upper(), "تصفية tickers تُترجَم (IN)")


# ═══════════════ 13) tickers نص واحد "AAPL" ⇒ سهم واحد (لا تفكيك أحرف) ═══════════════
def test_13_single_string_is_one_ticker():
    print("\n[13] tickers='AAPL' يُعامَل كرمز واحد لا كأحرف A/A/P/L:")
    with app.app_context():
        _clear()
        _seed_snap("AAPL", RUN, _dc_json())
        for ch in ("A", "P", "L"):          # لو فُكِّك حرفاً حرفاً لالتقط هذه
            _seed_snap(ch, RUN, _dc_json())
        cmap, nq = _count_queries(lambda: tracking.latest_confidence_map("AAPL"))
        check(set(cmap.keys()) == {"AAPL"}, "رمز واحد فقط AAPL (لا A/P/L)")
        check(nq == 1, f"استعلام واحد (كان {nq})")


# ═══════════════ 14) قائمة مختلطة ⇒ AAPL وMSFT فقط · بلا استثناء · بلا تعديل ═══════════════
def test_14_mixed_list_normalizes_safely():
    print("\n[14] [' aapl ','AAPL','msft','',None,42,[],{}] ⇒ AAPL+MSFT فقط بلا استثناء وبلا تعديل:")
    with app.app_context():
        _clear()
        _seed_snap("AAPL", RUN, _dc_json())
        _seed_snap("MSFT", RUN, _dc_json())
        _seed_snap("TSLA", RUN, _dc_json())          # موجود لكن غير مطلوب
        arg = [" aapl ", "AAPL", "msft", "", None, 42, [], {}]
        snapshot = list(arg)
        cmap, nq = _count_queries(lambda: tracking.latest_confidence_map(arg))
        check(set(cmap.keys()) == {"AAPL", "MSFT"}, "AAPL وMSFT فقط (strip+upper+تفرّد)")
        check(arg == snapshot, "القائمة المُدخلة لم تُعدَّل")
        check(nq == 1, f"استعلام واحد رغم العناصر التالفة (كان {nq})")


# ═══════════════ 15) عناصر غير قابلة للـhash لا ترفع TypeError ⇒ {} بلا استعلام ═══════════════
def test_15_unhashable_elements_no_typeerror():
    print("\n[15] عناصر غير قابلة للـhash ([]، {}) لا ترفع TypeError:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        raised = False
        try:
            res, nq = _count_queries(lambda: tracking.latest_confidence_map([["x"], {"y": 1}, ["x"]]))
        except TypeError:
            raised = True
            res, nq = {}, -1
        check(not raised, "لا TypeError على العناصر غير القابلة للـhash")
        check(res == {}, "كلها غير صالحة ⇒ خريطة فارغة")
        check(nq == 0, f"لا استعلام (كان {nq})")


# ═══════════════ 16) scalar غير iterable (42) ⇒ {} بلا استعلام ═══════════════
def test_16_scalar_non_iterable_empty():
    print("\n[16] tickers=42 (scalar غير قابل للتكرار) ⇒ {} بلا استعلام:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        res, nq = _count_queries(lambda: tracking.latest_confidence_map(42))
        check(res == {}, "خريطة فارغة")
        check(nq == 0, f"لا استعلام (كان {nq})")


# ═══════════════ 17) generator من رموز صالحة يعمل ═══════════════
def test_17_generator_of_valid_symbols():
    print("\n[17] generator من رموز صالحة يعمل (يُستهلك مرّة واحدة):")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        _seed_snap("B", RUN, _dc_json())
        _seed_snap("C", RUN, _dc_json())
        gen = (s for s in ["a", "B", " a "])          # a مكرّر بعد التطبيع
        cmap, nq = _count_queries(lambda: tracking.latest_confidence_map(gen))
        check(set(cmap.keys()) == {"A", "B"}, "A وB فقط من الـgenerator")
        check(nq == 1, f"استعلام واحد (كان {nq})")


# ═══════════════ 18) قائمة كلها تالفة ⇒ {} وصفر استعلام ═══════════════
def test_18_all_invalid_list_no_query():
    print("\n[18] قائمة كلها عناصر تالفة [None,42,'',[],{}] ⇒ {} بصفر استعلام:")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        res, nq = _count_queries(lambda: tracking.latest_confidence_map([None, 42, "", [], {}]))
        check(res == {}, "خريطة فارغة (لا رمز صالح)")
        check(nq == 0, f"لا استعلام (كان {nq})")


# ═══════════════ 19) json.loads يرفع RecursionError ⇒ corrupt بلا استثناء ═══════════════
def test_19_recursionerror_unavailable():
    print("\n[19] RecursionError عند فكّ JSON (في موضع الاستخدام الفعلي) ⇒ corrupt بلا استثناء:")
    from unittest.mock import patch
    import services.confidence_view as confidence_view
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        # patch صريح على موضع الاستخدام الفعلي (confidence_view.present_confidence_from_extra_json)
        # بدل تعديل json.loads العام يدوياً؛ لا try/finally (patch يستعيد تلقائياً).
        with patch.object(confidence_view.json, "loads",
                          side_effect=RecursionError("maximum recursion depth exceeded")):
            cmap = tracking.latest_confidence_map()
        check(cmap["A"]["available"] is False, "RecursionError ⇒ غير متوفرة")
        check(cmap["A"]["reason_code"] == "corrupt", "reason_code=corrupt")
        check(cmap["A"]["band_class"] == "conf-na", "conf-na بلا 500")


# ═══════════════ 20) استعلام واحد يبقى واحداً مع رموز صالحة متعددة ═══════════════
def test_20_single_query_with_valid_symbols():
    print("\n[20] عدد استعلامات DB يبقى واحدًا عند وجود رموز صالحة متعددة:")
    with app.app_context():
        _clear()
        for t in ("A", "B", "C", "D"):
            _seed_snap(t, RUN, _dc_json())
        _, nq = _count_queries(lambda: tracking.latest_confidence_map(["A", "B", "C", "D"]))
        check(nq == 1, f"استعلام واحد لأربعة رموز (كان {nq})")


# ═══════════════ 21) الإسقاط الخارجي = الأعمدة الثلاثة فقط (فحص بنيوي قطعي) ═══════════════
def test_21_select_only_needed_columns():
    print("\n[21] بنية statement: الإسقاط الخارجي = ticker/snap_date/extra_json فقط (عدد + هوية):")
    with app.app_context():
        stmt = tracking._latest_snapshot_statement(None)
        # فحص بنيوي قطعي: أي عمود جديد يُضاف للإسقاط سيكسر هذا الاختبار (لا يعتمد على قائمة منع).
        selected = list(stmt.selected_columns)
        check(len(selected) == 3, f"الإسقاط الخارجي 3 أعمدة بالضبط (كان {len(selected)})")
        check([c.key for c in selected] == ["ticker", "snap_date", "extra_json"],
              "الإسقاط الخارجي = ticker/snap_date/extra_json فقط (بالترتيب)")
        # فحص إضافي (ليس بديلاً): نص SQL لا يحوي بقية أعمدة StockSnapshot
        sql = str(stmt.compile(dialect=sqlite.dialect())).lower()
        for col in ("analysis_price", "catalyst_complete", "piotroski", "piotroski_computable",
                    "measures_met", "tech_tilt_kind", "algomatix_score", "structure_status",
                    "state_code", "is_gem", "is_ready"):
            check(col not in sql, f"العمود {col} غير مسقط (فحص إضافي)")


# ═══════════════ 22) تنفيذ الـstatement عبر execute بلا استعلام إضافي / lazy loading ═══════════════
def test_22_rows_no_extra_queries():
    print("\n[22] db.session.execute(statement).all() + قراءة كل الحقول = استعلام واحد بلا lazy:")
    with app.app_context():
        _clear()
        for t in ("A", "B", "C"):
            _seed_snap(t, RUN, _dc_json())

        def read_all_fields():
            rows = db.session.execute(tracking._latest_snapshot_statement(None)).all()
            # الوصول لكل الحقول بعد التنفيذ يجب ألّا يُطلق SQL جديداً (Row أعمدة، لا كائن ORM كسول)
            return [(r.ticker, r.snap_date, r.extra_json) for r in rows]

        data, nq = _count_queries(read_all_fields)
        check(len(data) == 3, "ثلاثة صفوف")
        check(all(isinstance(ej, str) for _, _, ej in data), "extra_json مقروء مباشرة")
        check(nq == 1, f"استعلام واحد فقط رغم قراءة كل الحقول (كان {nq})")


# ═══════════════ 23) _clean_tickers: dict/Mapping ⇒ [] (لا تُعامل مفاتيحه كرموز) ═══════════════
def test_23_dict_mapping_not_tickers():
    print("\n[23] top-level dict ⇒ {} بلا استعلام (مفاتيحه ليست رموزاً):")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())      # لو عومِلت مفاتيح {'A':..} كرموز لالتُقط
        res, nq = _count_queries(lambda: tracking.latest_confidence_map({"A": 1, "B": 2}))
        check(res == {}, "dict ⇒ خريطة فارغة")
        check(nq == 0, f"لا استعلام (كان {nq})")
        # التحقّق المباشر من المطبّع
        check(tracking._clean_tickers({"A": 1}) == [], "_clean_tickers(dict) ⇒ []")


# ═══════════════ 24) _clean_tickers: bytes/bytearray ⇒ [] ═══════════════
def test_24_bytes_and_bytearray_empty():
    print("\n[24] bytes/bytearray ⇒ {} بلا استعلام (لا تُفكّك):")
    with app.app_context():
        _clear()
        _seed_snap("A", RUN, _dc_json())
        for val, name in ((b"AAPL", "bytes"), (bytearray(b"AAPL"), "bytearray")):
            res, nq = _count_queries(lambda v=val: tracking.latest_confidence_map(v))
            check(res == {}, f"{name} ⇒ خريطة فارغة")
            check(nq == 0, f"{name}: لا استعلام (كان {nq})")
        check(tracking._clean_tickers(b"AAPL") == [], "_clean_tickers(bytes) ⇒ []")
        check(tracking._clean_tickers(bytearray(b"AAPL")) == [], "_clean_tickers(bytearray) ⇒ []")
        # وتأكيد أن النص العادي ما زال رمزاً واحداً (لا تراجع)
        check(tracking._clean_tickers("AAPL") == ["AAPL"], "str عادي ⇒ رمز واحد")
        check(tracking._clean_tickers(None) is None, "None ⇒ None (كل الأسهم)")


# ═══════════════ 25) ترجمة PostgreSQL دلالية (بلا اتصال ولا driver جديد) ═══════════════
def test_25_postgresql_dialect_compilation():
    print("\n[25] statement يُترجَم بلهجة PostgreSQL: 3 أعمدة + MAX + GROUP BY + JOIN + فلتر tickers، لا SQLite:")
    with app.app_context():
        stmt = tracking._latest_snapshot_statement(["A", "B"])
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        up = sql.upper()
        low = sql.lower()
        # فحص دلالي (لا مقارنة نص كامل هشّة)
        check(up.count("SELECT") >= 2, "subquery + إسقاط خارجي (SELECT مزدوج)")
        check("MAX(" in up, "MAX(snap_date)")
        check("GROUP BY" in up, "GROUP BY ticker")
        check("JOIN" in up, "JOIN مع الـsubquery")
        check(" IN (" in up or " IN(" in up, "فلتر tickers (IN)")
        # الإسقاط الخارجي = الأعمدة الثلاثة فقط
        for col in ("ticker", "snap_date", "extra_json"):
            check(col in low, f"عمود مُسقط: {col}")
        for col in ("analysis_price", "catalyst_complete", "piotroski_computable", "measures_met",
                    "tech_tilt_kind", "algomatix_score", "structure_status", "state_code",
                    "is_gem", "is_ready"):
            check(col not in low, f"عمود غير مُسقط: {col}")
        # لا تركيب خاص بـSQLite في ناتج PostgreSQL
        for bad in ("AUTOINCREMENT", "`", "PRAGMA", "SQLITE_"):
            check(bad not in up, f"لا تركيب SQLite: {bad}")
        # قيد بيئي صريح: ترجمة لهجة فقط — لا خادم PostgreSQL حيّ ولا اتصال.


class _FakeRow:
    """صف مُسقَط شبيه بـSQLAlchemy Row (وصول بالاسم فقط، لا كائن ORM كسول)."""
    def __init__(self, ticker, snap_date, extra_json):
        self.ticker = ticker
        self.snap_date = snap_date
        self.extra_json = extra_json


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return list(self._rows)


# ═══════════════ 26) mock حقيقي لـdb.session.execute: استدعاء واحد للمدخل الصالح + التقاط الـstatement ═══════════════
def test_26_execute_mock_called_once_valid():
    print("\n[26] db.session.execute يُستدعى مرة واحدة للمدخل الصالح؛ الـstatement الملتقط يُترجَم بلهجة PG:")
    from unittest.mock import patch
    with app.app_context():
        _clear()
        for t in ("A", "B"):
            _seed_snap(t, RUN, _dc_json())
        sess = db.session()
        real = sess.execute
        captured = {"stmts": []}

        def spy(statement, *a, **k):
            captured["stmts"].append(statement)
            return real(statement, *a, **k)   # ينفّذ فعلياً فيثبت أن الصفوف تُستهلك وتتحوّل

        with patch.object(sess, "execute", side_effect=spy) as m:
            cmap = tracking.latest_confidence_map()
        check(m.call_count == 1, f"execute مرة واحدة فقط (كان {m.call_count})")
        check(set(cmap.keys()) == {"A", "B"} and cmap["A"]["available"] is True,
              "الصفوف تُحوَّل للخريطة النهائية بنجاح")
        # الـstatement الملتقط هو نفسه الذي يُفحَص ويُترجَم بلهجة PostgreSQL
        pg = str(captured["stmts"][0].compile(dialect=postgresql.dialect())).upper()
        check("GROUP BY" in pg and "MAX(" in pg and "JOIN" in pg, "الملتقط = statement المفحوص بلهجة PG")


# ═══════════════ 27) mock result يدعم .all() ويُستهلك بشكل كود الإنتاج ═══════════════
def test_27_execute_mock_result_all_consumed():
    print("\n[27] mock result.all() يعيد صفوفاً بشكل الإنتاج ⇒ تُحوَّل للخريطة (ticker/snap_date/extra_json):")
    from unittest.mock import patch
    with app.app_context():
        sess = db.session()
        rows = [_FakeRow("Z", RUN, _dc_json()),
                _FakeRow("Y", RUN, json.dumps({"foo": "bar"}))]   # الثاني ⇒ missing
        with patch.object(sess, "execute", return_value=_FakeResult(rows)) as m:
            cmap = tracking.latest_confidence_map()   # tickers=None ⇒ ينفّذ
        check(m.call_count == 1, "execute مرة واحدة")
        check(set(cmap.keys()) == {"Z", "Y"}, "صفوف mock تُحوَّل عبر .all()")
        check(cmap["Z"]["available"] is True, "Z صالح ⇒ available")
        check(cmap["Y"]["available"] is False and cmap["Y"]["reason_code"] == "missing", "Y ⇒ missing")


# ═══════════════ 28) صفر execute للمدخلات المرفوضة/المنظّفة إلى فارغة ═══════════════
def test_28_execute_zero_for_rejected():
    print("\n[28] Mapping/bytes/bytearray/[]/قائمة تالفة ⇒ صفر استدعاء لـexecute:")
    from unittest.mock import patch
    with app.app_context():
        sess = db.session()
        with patch.object(sess, "execute", wraps=sess.execute) as m:
            for bad in ({"A": 1}, b"AAPL", bytearray(b"AAPL"), [], [None, 42, "", [], {}]):
                res = tracking.latest_confidence_map(bad)
                check(res == {}, f"{type(bad).__name__} ⇒ خريطة فارغة")
        check(m.call_count == 0, f"صفر execute للمدخلات المرفوضة (كان {m.call_count})")


def main():
    print("=" * 60)
    print("PHASE 6 F2 — Bulk Read (latest_confidence_map)")
    print("=" * 60)
    tests = [
        test_1_latest_only_and_single_query, test_2_ticker_filter_and_no_mutation,
        test_3_empty_tickers_no_query, test_4_ticker_without_snapshot_absent,
        test_5_bad_extra_json_unavailable, test_6_valid_bands,
        test_7_unavailable_and_unsupported_schema, test_8_no_recompute,
        test_9_no_writes, test_10_no_live_price_effect,
        test_11_deterministic_json, test_12_portable_sql_compiles,
        test_13_single_string_is_one_ticker, test_14_mixed_list_normalizes_safely,
        test_15_unhashable_elements_no_typeerror, test_16_scalar_non_iterable_empty,
        test_17_generator_of_valid_symbols, test_18_all_invalid_list_no_query,
        test_19_recursionerror_unavailable, test_20_single_query_with_valid_symbols,
        test_21_select_only_needed_columns, test_22_rows_no_extra_queries,
        test_23_dict_mapping_not_tickers, test_24_bytes_and_bytearray_empty,
        test_25_postgresql_dialect_compilation,
        test_26_execute_mock_called_once_valid, test_27_execute_mock_result_all_consumed,
        test_28_execute_zero_for_rejected,
    ]
    for fn in tests:
        fn()
    print("\n" + "-" * 60)
    print(f"كل اختبارات F2 Read نجحت ✓ ({_passed} تحقّقاً).")
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
