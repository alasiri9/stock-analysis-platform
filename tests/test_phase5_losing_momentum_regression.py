"""
test_phase5_losing_momentum_regression.py — PHASE 5 (LOSING_MOMENTUM → READY conditions).

قائمة الشروط لهذا الانتقال تُشتق حصراً من بوابتَي READY (Catalyst + tech_tilt) عبر مصدر الحقيقة
المشترك _ready_gate_conditions. structure ليست بوابة READY فلا تظهر إطلاقاً.

إثبات الانتقال end-to-end: بعد استيفاء الشروط المعروضة تصبح الحالة الفعلية READY.

التشغيل:  python tests/test_phase5_losing_momentum_regression.py
"""

import os
import sys
import tempfile

os.environ.pop("APP_PASSWORD", None)
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import fmp_client, news_client  # noqa: E402
fmp_client.get_quote = lambda *a, **k: None
fmp_client.get_historical_prices = lambda *a, **k: None
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from services.state import stock_state  # noqa: E402

_passed = 0
_failed = 0
_TILT = "يحتاج الميل الفني للعودة إلى محايد أو إيجابي"
_CAT_BELOW = "درجة النمو (Catalyst) دون العتبة"
_CAT_UNK = "بيانات النمو (Catalyst) غير متوفّرة"
_TILT_UNK = "بيانات المؤشرات الفنية غير متوفّرة"
_STRUCT_HINTS = ("هيكل", "structure", "كسر الهيكل", "بناء", "تأكيد فني")  # يجب ألّا يظهر أيّها
_CTX = {"open_bullish": True}


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _tilt(kind):
    table = {"neu": (2, 1, 2), "neg1": (2, 0, 3)}
    b, n, r = table[kind]
    out = [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


def _state(rec):
    return stock_state(rec, context=_CTX)


def _no_structure_hint(miss):
    return not any(any(h in m for h in _STRUCT_HINTS) for m in miss)


def _prove_reaches_ready(rec, label):
    """بعد استيفاء بوابتَي READY (catalyst≥80 + الميل غير سلبي) تصبح الحالة الفعلية READY."""
    fixed = dict(rec)
    fixed["catalyst"] = 85
    fixed["indicators"] = _tilt("neu")
    code = stock_state(fixed, context=_CTX)["code"]
    check(code == "READY", f"{label}: بعد استيفاء الشروط ⇒ READY فعلياً (كان {code})")


def test_case1_catalyst_low_tilt_neg():
    print("\n[1] LOSING: catalyst=50 + tilt=neg1 + structure=up ⇒ count=2 (Catalyst+tilt، بلا structure):")
    rec = {"ticker": "L1", "catalyst": 50, "indicators": _tilt("neg1"), "structure": {"trend": "up"}}
    st = _state(rec)
    check(st["code"] == "LOSING_MOMENTUM", "الحالة LOSING_MOMENTUM")
    check(st["next_state"] == "READY", "next_state = READY")
    check(_TILT in st["missing_conditions"] and _CAT_BELOW in st["missing_conditions"],
          "الشرطان: الميل + Catalyst دون العتبة")
    check(_no_structure_hint(st["missing_conditions"]), "لا شرط structure/هيكل إطلاقاً")
    check(st["missing_conditions_count"] == 2, "count = 2")
    _prove_reaches_ready(rec, "case1")


def test_case2_catalyst_high_tilt_neg_structure_none():
    print("\n[2] LOSING: catalyst=85 + tilt=neg1 + structure=None ⇒ count=1 (الميل فقط):")
    rec = {"ticker": "L2", "catalyst": 85, "indicators": _tilt("neg1"), "structure": None}
    st = _state(rec)
    check(st["code"] == "LOSING_MOMENTUM", "الحالة LOSING_MOMENTUM")
    check(st["missing_conditions"] == [_TILT], "الشرط الوحيد = الميل")
    check(_no_structure_hint(st["missing_conditions"]), "structure=None لا يظهر كشرط")
    check(st["missing_conditions_count"] == 1, "count = 1")
    _prove_reaches_ready(rec, "case2")


def test_case3_catalyst_low_tilt_neu_via_reversal():
    print("\n[3] LOSING عبر reversal (tilt=neu, catalyst=50) ⇒ count=1 (Catalyst فقط):")
    rec = {"ticker": "L3", "catalyst": 50, "indicators": _tilt("neu"),
           "reversal": {"status": "bear"}}
    st = _state(rec)
    check(st["code"] == "LOSING_MOMENTUM", "الحالة LOSING_MOMENTUM (ضعف عبر شمعة هابطة)")
    check(st["missing_conditions"] == [_CAT_BELOW], "الشرط الوحيد = Catalyst دون العتبة")
    check(st["missing_conditions_count"] == 1, "count = 1")
    # إثبات: رفع Catalyst إلى 80 (مع الميل المحايد) ⇒ READY
    fixed = dict(rec); fixed["catalyst"] = 80
    check(stock_state(fixed, context=_CTX)["code"] == "READY", "case3: catalyst=80 ⇒ READY فعلياً")


def test_case4_catalyst_unknown():
    print("\n[4] LOSING: catalyst=None + tilt=neg1 ⇒ count=None (Catalyst مجهول):")
    rec = {"ticker": "L4", "catalyst": None, "indicators": _tilt("neg1"), "structure": {"trend": "up"}}
    st = _state(rec)
    check(st["code"] == "LOSING_MOMENTUM", "الحالة LOSING_MOMENTUM")
    check(_CAT_UNK in st["missing_conditions"] and _TILT in st["missing_conditions"], "الميل + Catalyst مجهول")
    check(st["missing_conditions_count"] is None, "count = None")


def test_case5_tilt_unknown():
    print("\n[5] LOSING: catalyst=85 + tilt=None (عبر reversal) ⇒ count=None (الميل مجهول):")
    rec = {"ticker": "L5", "catalyst": 85, "indicators": [], "reversal": {"status": "bear"}}
    st = _state(rec)
    check(st["code"] == "LOSING_MOMENTUM", "الحالة LOSING_MOMENTUM")
    check(_TILT_UNK in st["missing_conditions"], "وصف نقص بيانات الميل")
    check(_CAT_BELOW not in st["missing_conditions"], "Catalyst≥80 متحقّق (لا يُضاف)")
    check(st["missing_conditions_count"] is None, "count = None")


def test_case6_structure_variants_same_conditions():
    print("\n[6] structure (up/None/غائب) لا يغيّر شروط READY المتطابقة:")
    base = {"ticker": "L6", "catalyst": 85, "indicators": _tilt("neg1")}
    variants = [dict(base, structure={"trend": "up"}),
                dict(base, structure=None),
                dict(base)]  # مفتاح structure غائب
    results = [_state(v) for v in variants]
    for i, st in enumerate(results):
        check(st["code"] == "LOSING_MOMENTUM", f"variant{i}: LOSING_MOMENTUM")
        check(st["missing_conditions"] == [_TILT] and st["missing_conditions_count"] == 1,
              f"variant{i}: الميل فقط، count=1 (structure لا يؤثّر)")


def test_case7_no_contradictory_losing_when_gates_met():
    print("\n[✔] catalyst=85 + tilt=neu (حتى مع reversal/open_bullish) ⇒ READY لا LOSING:")
    rec = {"ticker": "L7", "catalyst": 85, "indicators": _tilt("neu"),
           "reversal": {"status": "bear"}}
    st = _state(rec)
    check(st["code"] == "READY", "بوابتا READY متحققتان ⇒ READY (لا LOSING متناقض)")
    check(st["missing_conditions_count"] == 0, "count=0 (لا شروط ناقصة)")


def test_case8_template_pill_tooltip():
    print("\n[7] القالب: شارة LOSING تعرض العدد الصحيح في التلميح (أو لا رقم عند None):")
    r2 = {"ticker": "TL2", "name": "T", "catalyst": 50, "piotroski": None,
          "indicators": _tilt("neg1"), "structure": {"trend": "up"}}   # count=2
    r1 = {"ticker": "TL1", "name": "T", "catalyst": 85, "piotroski": None,
          "indicators": _tilt("neg1"), "structure": None}               # count=1
    rn = {"ticker": "TLN", "name": "T", "catalyst": None, "piotroski": None,
          "indicators": _tilt("neg1"), "structure": {"trend": "up"}}    # count=None
    for r in (r2, r1, rn):
        r["state"] = _state(r)
    with app.test_request_context("/"):
        h2 = app.jinja_env.get_template("_scard.html").render(r=r2, rank=1)
        h1 = app.jinja_env.get_template("_scard.html").render(r=r1, rank=1)
        hn = app.jinja_env.get_template("_scard.html").render(r=rn, rank=1)
    check("ينقصه 2 شرط" in h2, "count=2 ⇒ «ينقصه 2 شرط» في تلميح الشارة")
    check("ينقصه 1 شرط" in h1, "count=1 ⇒ «ينقصه 1 شرط»")
    check("ينقصه" not in hn, "count=None ⇒ لا رقم زائف")


def main():
    print("=" * 60)
    print("PHASE 5 — LOSING_MOMENTUM → READY conditions")
    print("=" * 60)
    test_case1_catalyst_low_tilt_neg()
    test_case2_catalyst_high_tilt_neg_structure_none()
    test_case3_catalyst_low_tilt_neu_via_reversal()
    test_case4_catalyst_unknown()
    test_case5_tilt_unknown()
    test_case6_structure_variants_same_conditions()
    test_case7_no_contradictory_losing_when_gates_met()
    test_case8_template_pill_tooltip()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات LOSING_MOMENTUM نجحت ✓ ({_passed} تحقّقاً).")
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
