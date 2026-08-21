"""
test_phase6_ui_wiring.py — PHASE 6 F2 (STEP 1: Backend Route Wiring).

يقفل ربط درجة الثقة بالبيانات بمسارات العرض دون المساس بحسابها أو حفظها:

- المُساعد _confidence_view_map:
  · قائمة فارغة ⇒ {} بلا أي استعلام وبلا استدعاء latest_confidence_map.
  · إزالة التكرار مع حفظ الترتيب · استعلام واحد كحد أقصى (لا N+1).
  · خريطة كثيفة: كل رمز معروض له view-model جاهز؛ الرمز بلا لقطة يُملأ مركزياً
    بـpresent_confidence(None) ⇒ available=False / band_class=conf-na (لا None، لا 500).
  · قراءة فقط: لا كتابة (add/merge/delete/commit/flush) ولا إعادة حساب data_confidence ولا API/live_price.
  · لا json.loads ولا تصنيف missing/corrupt داخل app.py أو القوالب. فكّ JSON مشروع ومقصود
    ويبقى مركزياً داخل confidence_view (عبر present_confidence_from_extra_json) لا في الربط.

- المسارات:
  · / تمرّر اتحاد رموز results + ready + breakouts فقط (دون أسهم إضافية) باستدعاء واحد.
  · /gems تمرّر رموز results فقط · /leaders تمرّر رموز results فقط.
  · /stock/<t> تطلب [ticker] واحداً عند وجود report فقط؛ report=None ⇒ صفر استدعاء للثقة.
  · القائمة الفارغة ⇒ صفر استدعاء للثقة.

الملفات المجمّدة (confidence.py / confidence_view.py / tracking.py / models.py) لا تُمَسّ؛
هذا الاختبار يقرأ منها فقط.

التشغيل:  python tests/test_phase6_ui_wiring.py
          python -m pytest tests/test_phase6_ui_wiring.py -q
"""

import json
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, Mock

# --- تهيئة بيئة اختبار معزولة (وضع مفتوح: بلا كلمة مرور فتصل الصفحات) ---
os.environ.pop("APP_PASSWORD", None)
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- عزل الشبكة قبل استيراد التطبيق (كما في smoke_test) ---
from services import fmp_client, news_client  # noqa: E402
fmp_client.get_quote = lambda *a, **k: None
fmp_client.get_profile = lambda *a, **k: None
fmp_client.get_financials = lambda *a, **k: {"income": None, "balance": None, "cashflow": None}
fmp_client.get_historical_prices = lambda *a, **k: None
news_client.get_market_news = lambda *a, **k: []

from sqlalchemy import event  # noqa: E402
import app as app_module  # noqa: E402
from app import app, _confidence_view_map  # noqa: E402
from models import db, StockSnapshot  # noqa: E402
from services import tracking, screener, analysis  # noqa: E402
from services.confidence import data_confidence, CONFIDENCE_TECHNICAL_INDICATOR_KEYS  # noqa: E402

_passed = 0
_failed = 0
RUN = date(2026, 8, 21)


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")
        raise AssertionError(label)


# ─────────── أدوات مساعدة ───────────
def _tech():
    return [{"label": k, "value": "x", "status": "bull"} for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]


def _record(**over):
    r = {
        "catalyst": 72, "catalyst_complete": True,
        "piotroski_computable": 9, "indicators": _tech(),
        "structure": {"trend": "up", "status": "bull"},
        "frames": {"weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull"},
        "analysis_date": "2026-08-21", "analysis_close": 100.0,
    }
    r.update(over)
    return r


def _dc_json(**over):
    return json.dumps({"data_confidence": data_confidence(_record(**over), RUN)}, ensure_ascii=False)


def _seed_snap(ticker, extra_json):
    db.session.merge(StockSnapshot(ticker=ticker, snap_date=RUN, extra_json=extra_json))
    db.session.commit()


def _clear():
    StockSnapshot.query.delete()
    db.session.commit()


def _count_queries(fn):
    counter = {"n": 0}

    def _before(conn, cursor, statement, params, ctx, many):
        counter["n"] += 1

    event.listen(db.engine, "before_cursor_execute", _before)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _before)
    return result, counter["n"]


def _count_writes(fn):
    """يعدّ عمليات الكتابة (INSERT/UPDATE/DELETE) وأي flush/commit أثناء fn."""
    counter = {"w": 0}

    def _before(conn, cursor, statement, params, ctx, many):
        head = (statement or "").lstrip().upper()[:6]
        if head.startswith(("INSERT", "UPDATE", "DELETE")):
            counter["w"] += 1

    event.listen(db.engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _before)
    return counter["w"]


# ═══════════════ المُساعد _confidence_view_map ═══════════════
def test_1_empty_list_no_query_no_call():
    print("\n[1] قائمة فارغة ⇒ {} بلا استعلام وبلا استدعاء latest_confidence_map:")
    with app.app_context():
        _clear()
        with patch.object(tracking, "latest_confidence_map") as spy:
            res, nq = _count_queries(lambda: _confidence_view_map([]))
        check(res == {}, "خريطة فارغة")
        check(nq == 0, f"صفر استعلام (كان {nq})")
        check(spy.call_count == 0, "لم تُستدعَ latest_confidence_map أصلاً")


def test_2_dedup_and_single_query():
    print("\n[2] إزالة التكرار مع حفظ الترتيب + استعلام واحد كحد أقصى:")
    with app.app_context():
        _clear()
        _seed_snap("A", _dc_json())
        _seed_snap("B", _dc_json())
        cmap, nq = _count_queries(lambda: _confidence_view_map(["A", "A", "B", "B", "A"]))
        check(list(cmap.keys()) == ["A", "B"], "المفاتيح فريدة وبترتيب الظهور الأول")
        check(nq == 1, f"استعلام واحد فقط (كان {nq})")


def test_3_dense_map_central_fallback():
    print("\n[3] خريطة كثيفة: الرمز بلا لقطة يُملأ مركزياً بـpresent_confidence(None):")
    with app.app_context():
        _clear()
        _seed_snap("H", _dc_json())          # لقطة صالحة ⇒ high
        cmap = _confidence_view_map(["H", "NOPE"])
        check(set(cmap.keys()) == {"H", "NOPE"}, "كل رمز معروض حاضر في الخريطة (كثيفة)")
        check(cmap["H"]["available"] is True and cmap["H"]["band"] == "high", "H: view-model حقيقي")
        check(cmap["NOPE"]["available"] is False, "NOPE: غير متوفرة (لا None)")
        check(cmap["NOPE"]["band_class"] == "conf-na", "NOPE: conf-na")
        check(cmap["NOPE"]["band_label"] == "درجة الثقة غير متوفرة", "NOPE: نص «غير متوفرة»")


def test_4_helper_is_read_only():
    print("\n[4] المُساعد قراءة فقط: لا add/merge/delete/commit/flush، ولا إعادة حساب، ولا API:")
    with app.app_context():
        _clear()
        _seed_snap("A", _dc_json())
        sess = db.session()   # الجلسة الأساسية (scoped_session تفوّض إليها)
        recorded = []
        orig = {}
        for m in ("add", "merge", "delete", "commit", "flush"):
            orig[m] = getattr(sess, m)
            setattr(sess, m, (lambda name: (lambda *a, **k: recorded.append(name)))(m))
        # منع إعادة الحساب على موضع الاستخدام الفعلي: tracking استورد data_confidence مباشرة
        # (from services.confidence import data_confidence) ⇒ نرقّع tracking.data_confidence نفسه.
        orig_dc = tracking.data_confidence

        def _boom_dc(*a, **k):
            raise AssertionError("data_confidence استُدعيت في الربط (إعادة حساب)!")

        tracking.data_confidence = _boom_dc
        try:
            # حارس السعر اللحظي/الشبكة + عدّ DML كحماية إضافية (لا بديلاً عن اعتراض الجلسة)
            with patch.object(fmp_client, "get_quote", side_effect=AssertionError("live price!")):
                writes = _count_writes(lambda: _confidence_view_map(["A", "B", "C"]))
        finally:
            for m, f in orig.items():
                setattr(sess, m, f)
            tracking.data_confidence = orig_dc
        check(recorded == [], f"لا استدعاء add/merge/delete/commit/flush (سُجّل: {recorded})")
        check(not sess.new and not sess.dirty and not sess.deleted, "لا كائنات معلّقة للكتابة (new/dirty/deleted)")
        check(writes == 0, f"لا DML (INSERT/UPDATE/DELETE) — حماية إضافية (كان {writes})")


# ═══════════════ ربط المسارات (تجسّس على الرموز الممرّرة) ═══════════════
def _spy_confidence():
    """يستبدل tracking.latest_confidence_map بجاسوس يسجّل الرموز، ويُرجع خريطة فارغة."""
    return patch.object(tracking, "latest_confidence_map",
                        Mock(return_value={}))


def _capture_render():
    """يستبدل render_template بمُسجّل يلتقط اسم القالب وسياقه ويُرجع نصاً."""
    calls = []

    def _rt(name, **ctx):
        calls.append((name, ctx))
        return "OK"

    return patch.object(app_module, "render_template", _rt), calls


def _rec(ticker, **over):
    r = {"ticker": ticker, "sector": None, "catalyst": None, "piotroski": None}
    r.update(over)
    return r


def _admin_client():
    """عميل اختبار بجلسة مدير — يمرّ من بوابة الدخول سواء كانت المنصة مفتوحة أو محمية.

    ملاحظة: app_password يُلتقط مرّة واحدة عند create_app؛ فحين تُشغَّل الحزمة كاملة قد تكون
    البوابة مفعّلة (وحدة شقيقة ضبطت APP_PASSWORD قبل الاستيراد). جلسة المدير تُبقي الاختبار
    ثابتاً في الحالتين (لا أثر في الوضع المفتوح).
    """
    c = app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["role"] = "admin"
    return c


def test_5_index_passes_union_only():
    print("\n[5] / تمرّر اتحاد results + ready + breakouts فقط باستدعاء واحد:")
    records = [
        _rec("R1", break_status={"dir": "breakout", "confirmed": True, "days_ago": 1}),
        _rec("R2"),
        _rec("B1", break_status={"dir": "breakout", "confirmed": True, "days_ago": 2}),
        _rec("K1"),
    ]
    results = [records[0], records[1]]        # R1, R2
    ready_cands = [{"ticker": "K1"}]          # K1
    # breakouts (داخل الراوت) تُحسب من records ⇒ R1, B1
    rt_patch, calls = _capture_render()
    with patch.object(screener, "load_records", return_value=(records, None)), \
         patch.object(screener, "filter_records", return_value=results), \
         patch.object(screener, "early_launch_candidates", return_value=ready_cands), \
         patch.object(screener, "launched_stocks", return_value=([], {"count": 0})), \
         patch.object(screener, "market_mood", return_value={}), \
         patch.object(screener, "market_direction", return_value={}), \
         patch.object(screener, "recent_signals", return_value=[]), \
         patch.object(screener, "is_gem", return_value=False), \
         patch.object(screener, "measures_met", return_value=0), \
         patch.object(screener, "current_price", return_value=None), \
         _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            r = c.get("/")
    check(r.status_code == 200, f"/ ترجع 200 (كانت {r.status_code})")
    check(spy.call_count == 1, f"استدعاء واحد للثقة (كان {spy.call_count})")
    passed = set(spy.call_args.args[0])
    check(passed == {"R1", "R2", "K1", "B1"}, f"اتحاد بلا أسهم إضافية (مُرّر {sorted(passed)})")
    cmap = dict(calls)["index.html"]["confidence_map"]
    check(set(cmap.keys()) == {"R1", "R2", "K1", "B1"}, "confidence_map = الاتحاد نفسه")
    # الجاسوس أرجع {} ⇒ يجب أن يملأ الراوت كل رمز بـfallback مركزي جاهز (لا None، لا مفتاح ناقص)
    check(all(v is not None and v.get("available") is False and v.get("band_class") == "conf-na"
              for v in cmap.values()),
          "قيم confidence_map = view-model كثيف جاهز (available=False / conf-na) لا None")


def test_6_gems_passes_results_only():
    print("\n[6] /gems تمرّر رموز results فقط باستدعاء واحد:")
    results = [_rec("A"), _rec("B")]
    rt_patch, calls = _capture_render()
    with patch.object(screener, "load_records", return_value=([], None)), \
         patch.object(screener, "filter_records", return_value=results), \
         _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            r = c.get("/gems")
    check(r.status_code == 200, "/gems ترجع 200")
    check(spy.call_count == 1, f"استدعاء واحد (كان {spy.call_count})")
    check(set(spy.call_args.args[0]) == {"A", "B"}, "رموز results فقط")
    cmap = dict(calls)["gems.html"]["confidence_map"]
    check(set(cmap.keys()) == {"A", "B"}, "confidence_map = results")
    check(all(v is not None and v.get("available") is False and v.get("band_class") == "conf-na"
              for v in cmap.values()),
          "قيم confidence_map = fallback مركزي جاهز (لا None)")


def test_7_leaders_passes_results_only():
    print("\n[7] /leaders تمرّر رموز results فقط باستدعاء واحد:")
    results = [_rec("X"), _rec("Y"), _rec("Z")]
    rt_patch, calls = _capture_render()
    with patch.object(screener, "load_records", return_value=([], None)), \
         patch.object(screener, "filter_records", return_value=results), \
         _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            r = c.get("/leaders")
    check(r.status_code == 200, "/leaders ترجع 200")
    check(spy.call_count == 1, f"استدعاء واحد (كان {spy.call_count})")
    check(set(spy.call_args.args[0]) == {"X", "Y", "Z"}, "رموز results فقط")
    cmap = dict(calls)["leaders.html"]["confidence_map"]
    check(all(v is not None and v.get("available") is False and v.get("band_class") == "conf-na"
              for v in cmap.values()),
          "قيم confidence_map = fallback مركزي جاهز (لا None)")


def test_8_empty_lists_zero_confidence_call():
    print("\n[8] قوائم فارغة ⇒ صفر استدعاء للثقة:")
    rt_patch, _calls = _capture_render()
    with patch.object(screener, "load_records", return_value=([], None)), \
         patch.object(screener, "filter_records", return_value=[]), \
         patch.object(screener, "early_launch_candidates", return_value=[]), \
         patch.object(screener, "launched_stocks", return_value=([], {"count": 0})), \
         patch.object(screener, "market_mood", return_value={}), \
         patch.object(screener, "market_direction", return_value={}), \
         patch.object(screener, "recent_signals", return_value=[]), \
         _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            c.get("/")
            c.get("/gems")
            c.get("/leaders")
    check(spy.call_count == 0, f"صفر استدعاء للثقة عبر الصفحات الثلاث (كان {spy.call_count})")


def test_9_stock_present_requests_single_ticker():
    print("\n[9] /stock/<t> تطلب [ticker] واحداً عند وجود report:")
    report = {"ticker": "AAPL", "name": "Apple", "sector": None, "price": 1.0,
              "indicators": [], "piotroski": {}}
    rt_patch, calls = _capture_render()
    with patch.object(analysis, "build_stock_report", return_value=dict(report, _complete=True)), \
         patch.object(analysis, "smart_summary", return_value={}), \
         patch.object(screener, "load_records", return_value=([], None)), \
         patch.object(fmp_client, "reserve_operation", return_value=True), \
         patch.object(fmp_client, "release_operation", return_value=None), \
         _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            r = c.get("/stock/AAPL")
    check(r.status_code == 200, f"/stock/AAPL ترجع 200 (كانت {r.status_code})")
    check(spy.call_count == 1, f"استدعاء واحد (كان {spy.call_count})")
    check(list(spy.call_args.args[0]) == ["AAPL"], "رمز واحد فقط [AAPL]")
    ctx = dict(calls)["stock.html"]
    check("confidence" in ctx, "stock.html يستقبل confidence")
    conf = ctx["confidence"]
    # الجاسوس أرجع {} ⇒ القيمة يجب أن تكون view-model كثيفاً جاهزاً (لا None، لا مفتاح فقط)
    check(isinstance(conf, dict) and conf is not None, "confidence = dict جاهز لا None")
    check(conf.get("available") is False and conf.get("band_class") == "conf-na",
          "confidence = fallback مركزي (available=False / conf-na)")


def test_10_stock_none_report_zero_confidence_call():
    print("\n[10] /stock/<t> بلا report (خارج UNIVERSE) ⇒ صفر استدعاء للثقة:")
    rt_patch, calls = _capture_render()
    with _spy_confidence() as spy, rt_patch:
        with _admin_client() as c:
            r = c.get("/stock/ZZZZ")   # خارج UNIVERSE وبلا كاش ⇒ report=None
    check(r.status_code == 200, "/stock/ZZZZ ترجع 200")
    check(spy.call_count == 0, f"صفر استدعاء للثقة عند report=None (كان {spy.call_count})")
    check(dict(calls)["stock.html"].get("report") is None, "report=None فعلاً")


# ═══════════════ التشغيل ═══════════════
def run():
    tests = [
        test_1_empty_list_no_query_no_call,
        test_2_dedup_and_single_query,
        test_3_dense_map_central_fallback,
        test_4_helper_is_read_only,
        test_5_index_passes_union_only,
        test_6_gems_passes_results_only,
        test_7_leaders_passes_results_only,
        test_8_empty_lists_zero_confidence_call,
        test_9_stock_present_requests_single_ticker,
        test_10_stock_none_report_zero_confidence_call,
    ]
    print("=" * 64)
    print("PHASE 6 / F2 — STEP 1: Backend Route Wiring")
    print("=" * 64)
    for t in tests:
        t()
    print("\n" + "=" * 64)
    print(f"النتيجة: {_passed} نجح · {_failed} فشل")
    print("=" * 64)
    return _failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
