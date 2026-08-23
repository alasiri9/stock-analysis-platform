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
from services.confidence_view import present_confidence, EXPLANATION_TEXT  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


# ═══════════════ STEP 2: شارة البطاقة + CSS + cache key ═══════════════
def _card_rec(ticker):
    return {"ticker": ticker, "name": "Test Co", "sector": None,
            "catalyst": 70, "catalyst_complete": True, "piotroski": 6,
            "indicators": [], "change_percent": None}


_UNSET = object()


def _render_scard(rec, cmap=_UNSET):
    """يصيّر _scard.html وحده بسياق مُتحكَّم به — نعطّل دوال البطاقة الأخرى (globals) لعزل الشارة."""
    from flask import render_template_string
    env = app.jinja_env
    keys = ("is_gem", "measures_met", "current_price", "piotroski_computable",
            "tech_tilt", "is_golden", "bullish_reasons")
    saved = {k: env.globals.get(k) for k in keys}
    env.globals.update(is_gem=lambda r: False, measures_met=lambda r: 0,
                       current_price=lambda r: None, piotroski_computable=lambda r: 9,
                       tech_tilt=lambda r: None, is_golden=lambda r: False,
                       bullish_reasons=lambda r: [])
    try:
        with app.test_request_context("/"):
            ctx = {"r": rec}
            if cmap is not _UNSET:
                ctx["confidence_map"] = cmap
            return render_template_string("{% set rank=1 %}{% include '_scard.html' %}", **ctx)
    finally:
        env.globals.update(saved)


def _badge_attrs(html):
    """يستخرج سمات وسم conf-badge نفسه (data-tip/aria-label/class) — لا بحث عام في HTML."""
    import re
    m = re.search(r'<span class="conf-badge[^"]*"[^>]*>', html)
    if not m:
        return None
    tag = m.group(0)
    def _attr(name):
        a = re.search(name + r'="([^"]*)"', tag)
        return a.group(1) if a else None
    return {"class": _attr("class"), "data-tip": _attr("data-tip"), "aria-label": _attr("aria-label")}


def test_11_badge_available_renders_view_model():
    print("\n[11] الشارة تعرض view-model الجاهز (data-tip=explanation · aria-label يضمّه):")
    with app.app_context():
        vm = present_confidence(data_confidence(_record(), RUN), RUN)
        check(vm["available"] and vm["band"] == "high", "view-model متاح high (تحضير)")
        html = _render_scard(_card_rec("AAA"), {"AAA": vm})
        at = _badge_attrs(html)
        check(at is not None, "وسم conf-badge موجود")
        check(at["class"] == f"conf-badge {vm['band_class']}", f"class = conf-badge {vm['band_class']}")
        check(vm["score_text"] in html and "conf-score" in html, "score_text ظاهر (سطح المكتب)")
        # data-tip == explanation (مطابقة تامة، لا بحث عام)
        check(at["data-tip"] == vm["explanation"] == EXPLANATION_TEXT, "data-tip يساوي explanation تماماً")
        # aria-label يتضمّن band_label + score_text + explanation
        check(vm["band_label"] in at["aria-label"], "aria-label يتضمّن band_label")
        check(vm["score_text"] in at["aria-label"], "aria-label يتضمّن score_text")
        check(vm["explanation"] in at["aria-label"], "aria-label يتضمّن explanation")
        check("reason_code" not in html, "لا reason_code في HTML")


def test_12_badge_unavailable_safe():
    print("\n[12] الشارة عند unavailable: «غير متوفرة» بلا score وبلا reason_code وبلا 500:")
    with app.app_context():
        vm = present_confidence(None, RUN)   # fallback مركزي
        check(vm["available"] is False and vm["band_class"] == "conf-na", "unavailable/conf-na (تحضير)")
        html = _render_scard(_card_rec("BBB"), {"BBB": vm})
        check("conf-badge conf-na" in html, "class = conf-badge conf-na")
        check("درجة الثقة غير متوفرة" in html, "نص «غير متوفرة» ظاهر")
        check("conf-score" not in html, "لا score span (score_text=None)")
        check("reason_code" not in html and vm["reason_code"] not in html, "لا reason_code/سبب مسرّب")


def test_13_badge_safe_without_context():
    print("\n[13] غياب confidence_map ⇒ لا شارة، بلا 500 (يحافظ على fallback backend):")
    with app.app_context():
        html_none = _render_scard(_card_rec("CCC"))              # بلا confidence_map إطلاقاً
        check("conf-badge" not in html_none, "لا شارة عند غياب السياق")
        check("scard-ticker" in html_none, "والبطاقة تُصيّر طبيعياً (لا 500)")
        html_empty = _render_scard(_card_rec("DDD"), {})         # confidence_map فارغ
        check("conf-badge" not in html_empty, "لا شارة عند خريطة فارغة")


# ─── تباين WCAG على لون الحبة الصلب نفسه (حساب فعلي، لا نظر) ───
def _rel_lum(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def _lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    R, G, B = _lin(r), _lin(g), _lin(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def _contrast(fg, bg):
    l1, l2 = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def test_14_contrast_meets_aa():
    print("\n[14] تباين WCAG AA (≥4.5) على الخلفيات الصلبة المقفلة:")
    pairs = [
        ("high", "#a7c0e8", "#2b3f79"),
        ("medium", "#f5c451", "#314468"),
        ("low", "#cdd5e4", "#2b4371"),
        ("unavailable", "#cdd5e4", "#2b4371"),
    ]
    for name, fg, bg in pairs:
        ratio = _contrast(fg, bg)
        check(ratio >= 4.5, f"{name}: {fg} على {bg} = {ratio:.2f}:1 (≥4.5)")


def test_15_css_locked_colors_and_names():
    print("\n[15] CSS: أسماء conf-* بالألوان المقفلة · #7c6fe6 ليس نصاً · لا selector جديد .confidence:")
    import re
    css = open(os.path.join(_ROOT, "static", "style.css"), encoding="utf-8").read()
    for sel, fg, bg in [(".conf-high", "#a7c0e8", "#2b3f79"), (".conf-medium", "#f5c451", "#314468"),
                        (".conf-low", "#cdd5e4", "#2b4371"), (".conf-na", "#cdd5e4", "#2b4371")]:
        block = css[css.index(sel):css.index(sel) + 120]
        check(fg in block and bg in block, f"{sel}: لون النص {fg} والخلفية {bg}")
    # #7c6fe6 مسموح كحد/خلفية فقط، لا كلون نص. نلتقط تصريح لون نص فعلي فقط: color: مباشرة بعد { أو ;
    # (يستثني border-color / border-inline-start-color / background لأنها ليست تصريح color: مستقلاً)
    check(re.search(r'[{;]\s*color:\s*#7c6fe6', css) is None, "#7c6fe6 غير مستخدم كلون نص")
    # لا selector جديد باسم .confidence (الموجودان مسبقاً فقط: `.confidence` و`.confidence b`)
    check(len(re.findall(r'\.confidence\b', css)) == 2, "لم يُنشأ/يُعدَّل selector باسم .confidence")
    check(".conf-badge" in css, ".conf-badge موجود")


def test_16_cache_key_bumped():
    print("\n[16] cache key في base.html محدَّث (؟v الحالي موجود، وما قبل F2 غائب):")
    base = open(os.path.join(_ROOT, "templates", "base.html"), encoding="utf-8").read()
    check("?v=20260822a" in base, "المفتاح الحالي موجود")
    check("?v=20260816b" not in base, "مفتاح ما قبل F2 أُزيل")


def test_17_tooltip_js_binds_conf_badge():
    print("\n[17] نظام التلميحات: .conf-badge + فصل hover الفأرة عن اللمس + منع انتقال الرابط:")
    import re
    base = open(os.path.join(_ROOT, "templates", "base.html"), encoding="utf-8").read()
    m = re.search(r'querySelectorAll\(([\'"])(.*?)\1\)\.forEach', base)
    check(m is not None, "querySelectorAll(...).forEach موجود")
    selector = m.group(2)
    for cls in (".nav-eye", ".help", ".help-q", ".conf-badge"):
        check(cls in selector, f"{cls} ضمن محدد التلميح المشترك")
    block = base[m.start():m.start() + 900]
    # hover عبر pointerenter/pointerleave مقيّدة بـpointerType==='mouse' (لا إظهار على اللمس قبل click)
    check("pointerenter" in block and "pointerleave" in block, "hover عبر pointerenter/pointerleave")
    check(re.search(r"pointerenter[\s\S]{0,80}pointerType\s*===\s*['\"]mouse['\"]", block) is not None,
          "pointerenter يُظهر للفأرة فقط (pointerType==='mouse')")
    check(re.search(r"pointerleave[\s\S]{0,80}pointerType\s*===\s*['\"]mouse['\"]", block) is not None,
          "pointerleave يُخفي للفأرة فقط (pointerType==='mouse')")
    # لم يعد hover الاصطناعي (mouseenter) يُظهر التلميح
    check("addEventListener('mouseenter'" not in block and 'addEventListener("mouseenter"' not in block,
          "لا mouseenter يُظهر التلميح (منع hover الاصطناعي من اللمس)")
    # click وحده يبدّل ويمنع انتقال رابط البطاقة
    check("preventDefault" in block and "stopPropagation" in block,
          "click يمنع الانتقال (preventDefault + stopPropagation)")


# ═══════════════ STEP 3: لوحة ثقة البيانات في صفحة السهم ═══════════════
# مواصفة العوامل السبعة (تطابق services.confidence_view._SCHEMA1_FACTORS)
_FACTORS_SPEC = [
    ("catalyst_completeness", "اكتمال درجة النمو (Catalyst)", 20),
    ("piotroski_computability", "قابلية حساب Piotroski", 20),
    ("technical_indicators", "توفّر المؤشرات الفنية", 20),
    ("structure_availability", "توفّر هيكل السوق", 15),
    ("frames_availability", "توفّر الفريمات الإضافية (الأسبوعي والشهري)", 10),
    ("flow_availability", "توفّر تدفق السيولة", 5),
    ("freshness", "حداثة البيانات", 10),
]
_CRIT_KEYS = {"catalyst_completeness", "piotroski_computability", "technical_indicators"}
_BAND_LABELS = {"high": ("ثقة عالية", "conf-high"), "medium": ("ثقة متوسطة", "conf-medium"),
                "low": ("ثقة منخفضة", "conf-low")}


def _mk_factors(points):
    out = []
    for key, label, mx in _FACTORS_SPEC:
        p = points.get(key, mx)
        crit = key in _CRIT_KEYS
        out.append({"key": key, "label": label, "points": p, "max": mx,
                    "pct": int(round(p / mx * 100)) if mx else 0,
                    "critical": crit, "critical_below_half": bool(crit and p < 0.5 * mx)})
    return out


def _mk_vm(band="high", score=95, as_of="2026-08-21", points=None, missing=None, caps=None):
    bl, bc = _BAND_LABELS[band]
    return {"available": True, "score": score, "score_text": f"{score}/100", "band": band,
            "band_label": bl, "band_class": bc, "explanation": EXPLANATION_TEXT, "as_of": as_of,
            "factors": _mk_factors(points or {}), "missing": list(missing or []),
            "caps_applied": list(caps or []), "schema_version": 1, "reason_code": None}


def _seed_report():
    from models import StockCache
    from datetime import datetime as _dt, timezone as _tz
    report = {
        "ticker": "AAPL", "name": "Apple Inc", "sector": "Technology", "price": 123.45,
        "change": 1.2, "change_percent": 0.98, "analysis_price": 123.45,
        "piotroski": {"score": 6, "computable": 9, "components": []},
        "catalyst": {"score": 70, "complete": True, "components": []},
        "indicators": [{"label": "MACD", "status": "bull", "value": "x"},
                       {"label": "RSI", "status": "neutral", "value": "x"}],
        "metrics": {"gross_margin": 40, "op_margin": 25, "pe": 20, "peg": 1.2, "roa": 10, "roe": 30},
        "break_status": None, "sustained": None, "reversal": None, "insider_trades": [],
        "fibonacci": None, "volume_profile": None, "atr_plan": None, "chart": None,
    }
    with app.app_context():
        db.session.merge(StockCache(ticker="report:AAPL",
                                    data_json=json.dumps(report, ensure_ascii=False),
                                    updated_at=_dt.now(_tz.utc)))
        db.session.commit()


def _stock_html(vm=_UNSET, absent=False):
    """يصيّر /stock/AAPL فعلياً (تقرير مخزّن) مع التحكّم في confidence عبر ترقيع _confidence_view_map."""
    _seed_report()
    m = {} if absent else {"AAPL": vm}
    with patch.object(app_module, "_confidence_view_map", lambda tickers: m):
        with _admin_client() as c:
            r = c.get("/stock/AAPL")
    return r.status_code, r.get_data(as_text=True)


def test_18_panel_available_high():
    print("\n[18] لوحة available high: موضع + band/score/explanation/as_of + 7 عوامل بالترتيب:")
    st, html = _stock_html(_mk_vm(band="high", score=95, as_of="2026-08-21"))
    check(st == 200, f"200 (كان {st})")
    # حاوية section دلالية مربوطة بالعنوان
    check('<section class="dc-panel conf-high" aria-labelledby="dc-title">' in html, "section + aria-labelledby + conf-high")
    check('id="dc-title" class="dc-title">ثقة البيانات' in html, "العنوان يحمل id=dc-title الصحيح")
    # الموضع: بعد score-cards وقبل tmeter
    i_sc, i_dc, i_tm = html.index("score-cards"), html.index("dc-panel"), html.index("tmeter-wrap")
    check(i_sc < i_dc < i_tm, "اللوحة بعد score-cards وقبل tmeter-wrap")
    check("ثقة عالية" in html and "95/100" in html, "band_label + score_text")
    check(EXPLANATION_TEXT in html, "explanation")
    check('حتى تاريخ' in html and '<time datetime="2026-08-21" dir="ltr">2026-08-21</time>' in html,
          "as_of بعنصر time بـdir=ltr (يبقى 2026-08-21)")
    _nf = html.count('<progress class="dc-progress"')
    check(_nf == 7, f"7 عوامل (كان {_nf})")
    block = html[html.index('class="dc-factors"'):]   # داخل شبكة العوامل فقط (بعد أي نص سابق)
    idx = [block.find(label) for _k, label, _m in _FACTORS_SPEC]
    check(all(i >= 0 for i in idx) and idx == sorted(idx), f"العوامل بترتيب view-model ({idx})")
    check("20/20 · 100%" in html, "points/max · pct")
    check('<progress class="dc-progress" value="20" max="20"' in html, "progress دلالي value/max")
    check("reason_code" not in html and "schema_version" not in html, "لا حقول داخلية")


def test_19_panel_medium_low():
    print("\n[19] medium/low: class وlabel من view-model فقط:")
    _, hm = _stock_html(_mk_vm(band="medium", score=72))
    check('class="dc-panel conf-medium"' in hm and "ثقة متوسطة" in hm and "72/100" in hm, "medium")
    _, hl = _stock_html(_mk_vm(band="low", score=40))
    check('class="dc-panel conf-low"' in hl and "ثقة منخفضة" in hl and "40/100" in hl, "low")


def test_20_panel_critical_below_half():
    print("\n[20] العامل الجوهري المنخفض: تمييز بصري من الأعلام الجاهزة (بلا حساب عتبة):")
    # نُنقص عاملاً جوهرياً (technical_indicators) تحت النصف ⇒ critical_below_half=True
    vm = _mk_vm(band="low", score=45, points={"technical_indicators": 4})
    _, html = _stock_html(vm)
    check("dc-below-half" in html, "class dc-below-half للعامل الجوهري المنخفض")
    check(html.count("ركيزة جوهرية") == 3, "وسم «ركيزة جوهرية» للعوامل الجوهرية الثلاثة")
    # عامل جوهري مكتمل لا يأخذ التمييز المنخفض
    vm2 = _mk_vm(band="high", score=95)
    _, html2 = _stock_html(vm2)
    check("dc-below-half" not in html2, "لا dc-below-half عند اكتمال الجوهرية")


def test_21_panel_missing():
    print("\n[21] missing: عنوان ودّي + نصوص escaped · القائمة الفارغة لا تُنشئ صندوقاً:")
    vm = _mk_vm(band="medium", score=70, missing=["حجم التداول < المطلوب"])
    _, html = _stock_html(vm)
    check("بيانات تحتاج إلى استكمال" in html, "عنوان missing الودّي")
    check("حجم التداول &lt; المطلوب" in html, "النص escaped")
    check("حجم التداول < المطلوب" not in html, "لا حقن خام لـ<")
    _, html2 = _stock_html(_mk_vm(band="high", score=95, missing=[]))
    check("dc-missing" not in html2, "قائمة فارغة ⇒ لا صندوق missing")


def test_22_panel_caps():
    print("\n[22] caps_applied: max + reasons · القيمة الداخلية cap لا تظهر · تعدّد لا يكسر:")
    caps = [{"cap": "low", "max": 49, "reasons": ["بيانات ناقصة", "لقطة قديمة"]},
            {"cap": "medium", "max": 79, "reasons": ["سبب آخر"]}]
    vm = _mk_vm(band="low", score=49, caps=caps)
    _, html = _stock_html(vm)
    check("تم تطبيق حد أعلى للدرجة:" in html and "49/100" in html and "79/100" in html, "max لكل cap")
    check("بيانات ناقصة" in html and "لقطة قديمة" in html and "سبب آخر" in html, "reasons")
    # القيمة الداخلية low/medium يجب ألا تظهر كنص مستقل للمستخدم
    import re
    check(re.search(r">\s*(low|medium)\s*<", html) is None, "cap الداخلي (low/medium) لا يظهر كنص")
    check(st_ok(html), "الصفحة لم تُكسر بتعدّد caps/reasons")


def st_ok(html):
    return "dc-caps" in html and "</html>" in html


def test_23_panel_unavailable():
    print("\n[23] unavailable: «غير متوفرة» + conf-na بلا score/factors/missing/caps بلا 500:")
    vm = present_confidence(None, RUN)
    st, html = _stock_html(vm)
    check(st == 200, "200")
    check('class="dc-panel conf-na"' in html, "dc-panel conf-na")
    check("درجة الثقة غير متوفرة" in html, "نص غير متوفرة")
    check("dc-factors" not in html and "dc-missing" not in html and "dc-caps" not in html, "لا عوامل/missing/caps")
    check("reason_code" not in html and "schema_version" not in html, "لا حقول داخلية")


def test_24_panel_as_of_none():
    print("\n[24] as_of=None: لا تاريخ وهمي ولا عنصر time فارغ:")
    vm = _mk_vm(band="high", score=95, as_of=None)
    _, html = _stock_html(vm)
    check("حتى تاريخ" not in html, "لا سطر «حتى تاريخ»")
    check("<time" not in html, "لا عنصر time (فارغ أو غيره) في اللوحة")


def test_25_panel_confidence_absent():
    print("\n[25] غياب confidence من السياق: لا لوحة، بلا 500، بلا بيانات مصطنعة:")
    st, html = _stock_html(absent=True)
    check(st == 200, "200")
    check("dc-panel" not in html, "لا لوحة عند غياب confidence")
    check("score-cards" in html, "وبقية الصفحة سليمة")


def test_26_cache_key_step3():
    print("\n[26] cache key في base.html = ?v=20260822a:")
    base = open(os.path.join(_ROOT, "templates", "base.html"), encoding="utf-8").read()
    check("?v=20260822a" in base, "المفتاح الجديد موجود")
    check("?v=20260821a" not in base, "مفتاح STEP 2 أُزيل")


def test_27_panel_contrast_locked_pairs():
    print("\n[27] تباين WCAG محسوب فعلياً لألوان critical/warning على panel/track (داكن+فاتح):")
    # خلفيات اللوحة والمسار من متغيّرات الثيم: داكن --panel-2/--border · فاتح --panel-2/--border
    DK_PANEL, DK_TRACK = "#1b2f57", "#2c3f66"
    LT_PANEL, LT_TRACK = "#eef3fb", "#d6deec"
    DC_CRIT_DK, DC_WARN_DK = "#ff8a8a", "#f5c451"
    DC_CRIT_LT, DC_WARN_LT = "#b91c1c", "#92400e"
    # (اسم، نص، خلفية، الهدف المعلَن)
    pairs = [
        ("critical dark/panel", DC_CRIT_DK, DK_PANEL, 5.82),
        ("critical dark/track", DC_CRIT_DK, DK_TRACK, 4.60),
        ("critical light/panel", DC_CRIT_LT, LT_PANEL, 5.81),
        ("critical light/track", DC_CRIT_LT, LT_TRACK, 4.78),
        ("warning dark/panel", DC_WARN_DK, DK_PANEL, 8.11),
        ("warning light/panel", DC_WARN_LT, LT_PANEL, 6.36),
    ]
    for name, fg, bg, target in pairs:
        ratio = _contrast(fg, bg)   # نفس دالة WCAG المستخدمة في test_14
        check(ratio >= 4.5, f"{name}: {fg}/{bg} = {ratio:.2f}:1 (≥4.5)")
        check(abs(ratio - target) <= 0.1, f"{name}: يطابق الهدف المعلَن ~{target} (فعلي {ratio:.2f})")
    # الرموز الجديدة معرّفة في CSS كمتغيّرات مقفلة (داكن + فاتح)
    css = open(os.path.join(_ROOT, "static", "style.css"), encoding="utf-8").read()
    for hexv in (DC_CRIT_DK, DC_WARN_DK, DC_CRIT_LT, DC_WARN_LT):
        check(hexv in css, f"{hexv} معرّف في CSS")
    check("html.light .dc-panel" in css and "--dc-critical" in css and "--dc-warning" in css,
          "متغيّرات --dc-critical/--dc-warning للوضعين")


def test_28_integration_real_pipeline_no_patch():
    print("\n[28] تكامل فعلي بلا ترقيع _confidence_view_map (لقطة StockSnapshot → القالب):")
    with app.app_context():
        _clear()
        _seed_snap("AAPL", _dc_json())   # لقطة حقيقية بثقة صالحة (data_confidence من النواة)
    _seed_report()
    box = {}

    def _do():
        with _admin_client() as c:
            r = c.get("/stock/AAPL")     # لا ترقيع: المسار الحقيقي latest_confidence_map→present_confidence_from_extra_json→app.py→stock.html
        box["st"] = r.status_code
        box["html"] = r.get_data(as_text=True)

    with app.app_context():
        writes = _count_writes(_do)      # عدّ الكتابة أثناء العرض فقط
    st, html = box["st"], box["html"]
    check(st == 200, f"200 (كان {st})")
    check('<section class="dc-panel conf-high"' in html, "لوحة متاحة (conf-high من اللقطة الحقيقية)")
    check("ثقة عالية" in html, "band")
    import re
    check(re.search(r'\d+/100', html) is not None, "score_text")
    check('<time datetime="2026-08-21" dir="ltr">2026-08-21</time>' in html, "as_of من snap_date")
    check(html.count('<progress class="dc-progress"') == 7, "العوامل السبعة")
    check("reason_code" not in html and "schema_version" not in html, "لا حقول داخلية")
    check(writes == 0, f"لا كتابة أثناء العرض (كان {writes})")


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
        test_11_badge_available_renders_view_model,
        test_12_badge_unavailable_safe,
        test_13_badge_safe_without_context,
        test_14_contrast_meets_aa,
        test_15_css_locked_colors_and_names,
        test_16_cache_key_bumped,
        test_17_tooltip_js_binds_conf_badge,
        test_18_panel_available_high,
        test_19_panel_medium_low,
        test_20_panel_critical_below_half,
        test_21_panel_missing,
        test_22_panel_caps,
        test_23_panel_unavailable,
        test_24_panel_as_of_none,
        test_25_panel_confidence_absent,
        test_26_cache_key_step3,
        test_27_panel_contrast_locked_pairs,
        test_28_integration_real_pipeline_no_patch,
    ]
    print("=" * 64)
    print("PHASE 6 / F2 — STEP 1+2+3: Wiring + Card Badge + Stock Panel")
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
