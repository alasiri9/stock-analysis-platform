"""
test_phase5_watch_unknown_gate_regression.py — PHASE 5 (UNKNOWN gate في WATCH→FORMING).

بوابة «بداية البناء الصاعد» تُقيَّم بدلالة ثلاثية (TRUE/FALSE/UNKNOWN):
- المجهول (المصدر None/غائب) لا يتحوّل إلى FALSE.
- كلها معروفة False ⇒ شرط واحد فعلي (count=1).
- أي مسار مجهول بلا مسار متحقّق ⇒ UNKNOWN ⇒ count=None ونص صادق «بيانات بناء الاتجاه غير مكتملة».
لا تغيير لتعريف WATCH/FORMING ولا classify_setup.

التشغيل:  python tests/test_phase5_watch_unknown_gate_regression.py
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
from services.state import stock_state, classify_setup, _build_gate_tristate  # noqa: E402

_passed = 0
_failed = 0
_BUILD = "بداية بناء صاعد (هيكل صاعد أو انضغاط أو بداية اختراق)"
_BUILD_UNK = "بيانات بناء الاتجاه غير مكتملة"


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _tilt(kind):
    table = {"pos1": (3, 0, 2), "neg1": (2, 0, 3)}
    b, n, r = table[kind]
    out = [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


_FALSE_STRUCT = {"trend": "side", "event": None}
_FALSE_BRK = {"dir": "range", "confirmed": False}


def test_tristate_or_truth_table():
    print("\n[OR] جدول الحقيقة الثلاثي لبوابة البناء:")
    check(_build_gate_tristate({}) == "unknown", "كل المصادر غائبة ⇒ unknown")
    check(_build_gate_tristate({"structure": None, "squeeze_breakout": None,
                                "break_status": None}) == "unknown", "كلها None صريحة ⇒ unknown")
    check(_build_gate_tristate({"structure": _FALSE_STRUCT, "squeeze_breakout": False,
                                "break_status": _FALSE_BRK}) == "false", "كلها معروفة False ⇒ false")
    check(_build_gate_tristate({"structure": {"trend": "up"}}) == "true", "هيكل صاعد ⇒ true")
    check(_build_gate_tristate({"structure": _FALSE_STRUCT,
                                "break_status": _FALSE_BRK}) == "unknown",
          "F(structure)+F(break)+U(squeeze غائب) ⇒ unknown")   # OR(F,F,U)=U
    check(_build_gate_tristate({"structure": _FALSE_STRUCT,
                                "squeeze_breakout": True}) == "true",
          "F(structure)+T(squeeze)+U(break غائب) ⇒ true")       # OR(F,T,U)=T


def test_watch_all_none_unknown():
    print("\n[1] WATCH + كل مصادر البناء = None صريحة ⇒ UNKNOWN:")
    rec = {"ticker": "A", "catalyst": 50, "indicators": _tilt("pos1"),
           "structure": None, "break_status": None, "squeeze_breakout": None}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["next_state"] == "FORMING", "next_state = FORMING")
    check(st["missing_conditions_count"] is None, "count = None")
    check(_BUILD_UNK in st["missing_conditions"], "نص صادق عن نقص البيانات")
    check(_BUILD not in st["missing_conditions"], "لا يدّعي أن البناء «غير متحقّق» معروفاً")


def test_watch_missing_keys_unknown():
    print("\n[2] WATCH + مفاتيح البناء غير موجودة أصلاً ⇒ UNKNOWN:")
    rec = {"ticker": "B", "catalyst": 50, "indicators": _tilt("pos1")}  # لا مفاتيح بناء
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "count = None (مفاتيح غائبة = مجهول)")
    check(_BUILD_UNK in st["missing_conditions"], "نص نقص البيانات")


def test_watch_known_false_count1():
    print("\n[3] WATCH + كل مصادر البناء موجودة ومعروفة False ⇒ count=1:")
    rec = {"ticker": "C", "catalyst": 50, "indicators": _tilt("pos1"),
           "structure": _FALSE_STRUCT, "squeeze_breakout": False, "break_status": _FALSE_BRK}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] == 1, "count = 1 (معروف أن البناء غير متحقّق)")
    check(_BUILD in st["missing_conditions"], "الشرط = «بداية بناء صاعد...»")
    check(_BUILD_UNK not in st["missing_conditions"], "لا نص «غير مكتملة» (البيانات كافية)")


def test_watch_partial_false_unknown():
    print("\n[4] مساران False + مسار UNKNOWN ⇒ UNKNOWN (count=None):")
    rec = {"ticker": "D", "catalyst": 50, "indicators": _tilt("pos1"),
           "structure": _FALSE_STRUCT, "break_status": _FALSE_BRK}  # squeeze غائب = مجهول
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "OR(F,F,U)=UNKNOWN ⇒ count=None")
    check(_BUILD_UNK in st["missing_conditions"], "نص نقص البيانات")


def test_build_true_not_a_missing_condition():
    print("\n[5] مسار البناء متحقّق (TRUE) ⇒ لا يُعدّ شرطاً ناقصاً:")
    # ميل سلبي (⇒ WATCH لا FORMING) لكن البناء متحقّق (هيكل صاعد) + مصدر مجهول
    rec = {"ticker": "E", "catalyst": 50, "indicators": _tilt("neg1"),
           "structure": {"trend": "up"}, "squeeze_breakout": None}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH (الميل سلبي)")
    st = stock_state(rec)
    check(_BUILD not in st["missing_conditions"] and _BUILD_UNK not in st["missing_conditions"],
          "البناء متحقّق ⇒ لا شرط بناء (لا يُعتبر مجهولاً)")
    check(st["missing_conditions_count"] == 1, "الناقص = الميل فقط (count=1)")


def test_true_path_still_enters_forming():
    print("\n[6] مصدر بناء معروف يحقّق FORMING ⇒ الانتقال الفعلي محفوظ:")
    rec = {"ticker": "F", "catalyst": 50, "indicators": _tilt("pos1"), "structure": {"trend": "up"}}
    check(classify_setup(rec) == "FORMING", "هيكل صاعد + ميل غير سلبي ⇒ FORMING كما هو")


def test_template_none_vs_known():
    print("\n[7] القالب: count=None ⇒ «غير قابلة للتقييم» · count معروف ⇒ الرقم:")
    # NEAR_READY + catalyst=None ⇒ count None (حالة تعرض التلميح على البطاقة)
    unk = {"ticker": "T1", "name": "T", "catalyst": None, "piotroski": None,
           "indicators": _tilt("pos1"),
           "structure": {"event": "BOS", "event_dir": "up", "trend": "up"}}
    unk["state"] = stock_state(unk)
    # FORMING known ⇒ count 1
    kn = {"ticker": "T2", "name": "T", "catalyst": 79, "piotroski": 6,
          "indicators": _tilt("pos1"), "structure": {"trend": "up"},
          "break_status": {"dir": "range", "confirmed": False}}
    kn["state"] = stock_state(kn)
    with app.test_request_context("/"):
        h_unk = app.jinja_env.get_template("_scard.html").render(r=unk, rank=1)
        h_kn = app.jinja_env.get_template("_scard.html").render(r=kn, rank=1)
    check("غير قابلة للتقييم" in h_unk and "ينقصه" not in h_unk, "count=None ⇒ «غير قابلة»، بلا رقم")
    check("ينقصه 1 شرط" in h_kn, "count معروف ⇒ «ينقصه 1 شرط»")


def test_watch_card_shows_no_false_number():
    print("\n[7ب] بطاقة WATCH بمجهول لا تعرض رقماً زائفاً:")
    rec = {"ticker": "W", "name": "W", "catalyst": 50, "piotroski": None,
           "indicators": _tilt("pos1"), "structure": None, "break_status": None,
           "squeeze_breakout": None}
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه 1 شرط" not in html, "لا «ينقصه 1 شرط → يتكوّن» لسهم بيانات بنائه مجهولة")


def test_catalyst_unknown_still_ok():
    print("\n[8] Catalyst UNKNOWN السابق لم ينكسر (NEAR_READY):")
    rec = {"ticker": "K", "catalyst": None, "indicators": _tilt("pos1"),
           "structure": {"event": "BOS", "event_dir": "up", "trend": "up"}}
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "NEAR_READY + catalyst None ⇒ count None")
    check("بيانات النمو (Catalyst) غير متوفّرة" in st["missing_conditions"], "نص Catalyst مجهول باقٍ")


def main():
    print("=" * 60)
    print("PHASE 5 — WATCH→FORMING UNKNOWN gate (tri-state OR)")
    print("=" * 60)
    test_tristate_or_truth_table()
    test_watch_all_none_unknown()
    test_watch_missing_keys_unknown()
    test_watch_known_false_count1()
    test_watch_partial_false_unknown()
    test_build_true_not_a_missing_condition()
    test_true_path_still_enters_forming()
    test_template_none_vs_known()
    test_watch_card_shows_no_false_number()
    test_catalyst_unknown_still_ok()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات UNKNOWN-gate نجحت ✓ ({_passed} تحقّقاً).")
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
