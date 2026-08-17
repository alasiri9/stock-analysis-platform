"""
test_phase5_forming_confirmation_regression.py — PHASE 5 (UNKNOWN في بوابة تأكيد FORMING→NEAR_READY).

بوابة التأكيد الفني (brk_up_confirmed OR retest_ok OR bos_up) تُقيَّم بدلالة ثلاثية:
- سهم FORMING عبر squeeze مع structure=None/break_status=None ⇒ التأكيد UNKNOWN (لا KNOWN-FALSE).
- المجهول لا يتحوّل إلى False؛ OR(F,F,U)=U · OR(F,T,U)=T · OR(F,F,F)=F.
لا تغيير لتعريف أي State ولا classify_setup.

التشغيل:  python tests/test_phase5_forming_confirmation_regression.py
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
from services.state import stock_state, classify_setup, _confirmation_gate_tristate  # noqa: E402

_passed = 0
_failed = 0
_CONF = "تأكيد فني بنيوي (إعادة اختبار مؤكّدة أو BOS صاعد)"
_CONF_UNK = "بيانات التأكيد الفني غير مكتملة"


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _tilt(kind):
    table = {"pos1": (3, 0, 2)}
    b, n, r = table[kind]
    out = [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


_FALSE_STRUCT = {"trend": "side", "event": None, "retest_state": None}
_FALSE_BRK = {"dir": "range", "confirmed": False}
_BOS = {"event": "BOS", "event_dir": "up", "trend": "up"}
_BRK_CONFIRMED = {"dir": "breakout", "confirmed": True}


def test_case1_forming_via_squeeze_unknown():
    print("\n[1] FORMING عبر squeeze + structure=None + break_status=None ⇒ UNKNOWN:")
    rec = {"ticker": "S1", "catalyst": 50, "indicators": _tilt("pos1"),
           "squeeze_breakout": True, "structure": None, "break_status": None}
    check(classify_setup(rec) == "FORMING", "التصنيف FORMING (عبر squeeze)")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "count=None (التأكيد مجهول)")
    check(_CONF_UNK in st["missing_conditions"], "نص «بيانات التأكيد الفني غير مكتملة»")
    check(_CONF not in st["missing_conditions"], "لا يدّعي أن التأكيد «غير متحقّق» كحقيقة")


def test_case2_missing_keys_unknown():
    print("\n[2] FORMING عبر squeeze + مفاتيح structure/break_status غائبة ⇒ UNKNOWN:")
    rec = {"ticker": "S2", "catalyst": 50, "indicators": _tilt("pos1"), "squeeze_breakout": True}
    check(classify_setup(rec) == "FORMING", "التصنيف FORMING")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "count=None (مفاتيح غائبة = مجهول)")


def test_case3_known_false_count_known():
    print("\n[3] structure وbreak_status موجودان ومعروفان (لا تأكيد) ⇒ KNOWN FALSE:")
    rec = {"ticker": "S3", "catalyst": 50, "indicators": _tilt("pos1"), "squeeze_breakout": True,
           "structure": _FALSE_STRUCT, "break_status": _FALSE_BRK}
    check(classify_setup(rec) == "FORMING", "التصنيف FORMING")
    st = stock_state(rec)
    check(st["missing_conditions_count"] == 1, "count=1 (التأكيد معروف أنه غير متحقّق)")
    check(_CONF in st["missing_conditions"], "الشرط = «تأكيد فني بنيوي...»")


def test_case4_helper_true_via_break_despite_structure_none():
    print("\n[4] structure=None لكن brk_up_confirmed=True ⇒ TRUE (مسار OR تحقّق):")
    g = _confirmation_gate_tristate({"structure": None, "break_status": _BRK_CONFIRMED})
    check(g == "true", "بوابة التأكيد = true رغم structure مجهولة")


def test_case5_helper_true_via_bos_despite_break_none():
    print("\n[5] break_status=None لكن bos_up=True ⇒ TRUE:")
    g = _confirmation_gate_tristate({"structure": _BOS, "break_status": None})
    check(g == "true", "بوابة التأكيد = true عبر BOS صاعد")


def test_case6_partial_unknown():
    print("\n[6] بعض False + بعض UNKNOWN بلا TRUE ⇒ UNKNOWN:")
    g = _confirmation_gate_tristate({"structure": _FALSE_STRUCT, "break_status": None})
    check(g == "unknown", "OR(F, U) = UNKNOWN")


def test_case7_all_false():
    print("\n[7] كلها False مع بيانات موجودة ⇒ FALSE:")
    g = _confirmation_gate_tristate({"structure": _FALSE_STRUCT, "break_status": _FALSE_BRK})
    check(g == "false", "OR(F,F) = FALSE")


def test_case8_real_confirmation_moves_to_near_ready():
    print("\n[8] إضافة تأكيد حقيقي (BOS صاعد) ⇒ FORMING ينتقل فعلياً إلى NEAR_READY:")
    before = {"ticker": "S8", "catalyst": 50, "indicators": _tilt("pos1"),
              "squeeze_breakout": True, "structure": None}
    check(classify_setup(before) == "FORMING", "قبل: FORMING")
    after = {"ticker": "S8", "catalyst": 50, "indicators": _tilt("pos1"),
             "squeeze_breakout": True, "structure": _BOS}
    check(classify_setup(after) == "NEAR_READY", "بعد التأكيد: NEAR_READY فعلاً")


def test_case9_catalyst_none_intact():
    print("\n[9] Catalyst UNKNOWN السابق لم ينكسر (NEAR_READY):")
    rec = {"ticker": "S9", "catalyst": None, "indicators": _tilt("pos1"), "structure": _BOS}
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "NEAR_READY + catalyst None ⇒ count None")
    check("بيانات النمو (Catalyst) غير متوفّرة" in st["missing_conditions"], "نص Catalyst مجهول باقٍ")


def test_case10_watch_tristate_intact():
    print("\n[10] tri-state لبوابة WATCH (من 89f6aab) لم ينكسر:")
    rec = {"ticker": "S10", "catalyst": 50, "indicators": _tilt("pos1"),
           "structure": None, "break_status": None, "squeeze_breakout": None}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "WATCH بمصادر بناء مجهولة ⇒ count None")


def test_ui_forming_unknown_no_false_number():
    print("\n[UI] بطاقة FORMING بتأكيد مجهول: «غير قابلة للتقييم» بلا رقم:")
    rec = {"ticker": "SU", "name": "S", "catalyst": 50, "piotroski": None,
           "indicators": _tilt("pos1"), "squeeze_breakout": True, "structure": None,
           "break_status": None}
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه" not in html, "لا «ينقصه 1 شرط» عند تأكيد مجهول")
    check("غير قابلة للتقييم" in html, "يظهر «بعض الشروط غير قابلة للتقييم»")


def main():
    print("=" * 60)
    print("PHASE 5 — FORMING→NEAR_READY confirmation UNKNOWN gate")
    print("=" * 60)
    test_case1_forming_via_squeeze_unknown()
    test_case2_missing_keys_unknown()
    test_case3_known_false_count_known()
    test_case4_helper_true_via_break_despite_structure_none()
    test_case5_helper_true_via_bos_despite_break_none()
    test_case6_partial_unknown()
    test_case7_all_false()
    test_case8_real_confirmation_moves_to_near_ready()
    test_case9_catalyst_none_intact()
    test_case10_watch_tristate_intact()
    test_ui_forming_unknown_no_false_number()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات FORMING-confirmation نجحت ✓ ({_passed} تحقّقاً).")
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
