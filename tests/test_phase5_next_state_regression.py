"""
test_phase5_next_state_regression.py — PHASE 5 ISSUE #2 (MEDIUM).

missing_conditions / missing_conditions_count تصف الشروط اللازمة فعلاً للوصول إلى next_state المعروض
(لا قائمة عامة بكل بوابات READY). لا تغيير لأي تعريف State.

التشغيل:  python tests/test_phase5_next_state_regression.py
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
from services.state import stock_state, classify_setup, conditions_for_next_state  # noqa: E402

_passed = 0
_failed = 0
_BELOW = "درجة النمو (Catalyst) دون العتبة"
_TILT = "يحتاج الميل الفني للعودة إلى محايد أو إيجابي"


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


def test_forming_next_is_one_technical():
    print("\n[1] FORMING (catalyst=79, ميل إيجابي, هيكل صاعد, بلا تأكيد) → NEAR_READY، شرط واحد:")
    # بيانات التأكيد الفني كاملة ومعروفة False (structure ليس retest/BOS، break_status ليس اختراقاً مؤكّداً)
    rec = {"ticker": "F1", "catalyst": 79, "indicators": _tilt("pos1"), "structure": {"trend": "up"},
           "break_status": {"dir": "range", "confirmed": False}}
    check(classify_setup(rec) == "FORMING", "التصنيف FORMING")
    st = stock_state(rec)
    check(st["next_state"] == "NEAR_READY", "next_state = NEAR_READY")
    check(st["missing_conditions_count"] == 1, "العدد = 1 (تأكيد فني فقط)")
    check(_BELOW not in st["missing_conditions"],
          "لا يدّعي أن Catalyst شرط للوصول إلى NEAR_READY في هذا المسار")


def test_adding_confirmation_moves_to_near_ready():
    print("\n[2] إضافة تأكيد بنيوي (BOS صاعد) ⇒ ينتقل فعلياً إلى NEAR_READY:")
    rec = {"ticker": "F2", "catalyst": 79, "indicators": _tilt("pos1"),
           "structure": {"event": "BOS", "event_dir": "up", "trend": "up"}}
    check(classify_setup(rec) == "NEAR_READY", "بعد التأكيد ⇒ NEAR_READY فعلاً")


def test_near_ready_high_catalyst_tilt_only():
    print("\n[3] NEAR_READY (catalyst≥80 + neg1) → READY، الناقص الميل فقط:")
    rec = {"ticker": "N3", "catalyst": 85, "indicators": _tilt("neg1")}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY")
    st = stock_state(rec)
    check(st["next_state"] == "READY", "next_state = READY")
    check(st["missing_conditions"] == [_TILT], "الشرط الوحيد = الميل الفني")
    check(st["missing_conditions_count"] == 1, "العدد = 1")


def test_near_ready_known_below_catalyst():
    print("\n[4] NEAR_READY (catalyst=79 + فني محقّق) → READY، الناقص Catalyst:")
    rec = {"ticker": "N4", "catalyst": 79, "indicators": _tilt("pos1"),
           "structure": {"event": "BOS", "event_dir": "up", "trend": "up"}}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY")
    st = stock_state(rec)
    check(_BELOW in st["missing_conditions"], "الشرط = «Catalyst دون العتبة»")
    check(st["missing_conditions_count"] == 1, "العدد = 1")


def test_watch_next_forming_not_ready_gates():
    print("\n[5] WATCH → FORMING: العدد يطابق شروط FORMING لا شروط READY:")
    # ميل سلبي + هيكل صاعد ⇒ WATCH (FORMING يحتاج ميلاً غير سلبي)
    rec = {"ticker": "W5", "catalyst": 50, "indicators": _tilt("neg1"), "structure": {"trend": "up"}}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["next_state"] == "FORMING", "next_state = FORMING")
    check(st["missing_conditions"] == [_TILT], "شرط FORMING فقط (عودة الميل) — البناء متحقّق")
    check(_BELOW not in st["missing_conditions"], "لا شرط Catalyst (ليس بوابة FORMING)")
    check(st["missing_conditions_count"] == 1, "العدد = 1")


def test_or_paths_not_summed():
    print("\n[6] مسارات OR لا تُجمع كأنها AND (بوابة البناء المعروفة False = شرط واحد):")
    # ميل إيجابي + كل مصادر البناء موجودة ومعروفة False (لا هيكل صاعد/BOS/انضغاط/بداية اختراق) ⇒ WATCH
    rec = {"ticker": "W6", "catalyst": 50, "indicators": _tilt("pos1"),
           "structure": {"trend": "side", "event": None}, "squeeze_breakout": False,
           "break_status": {"dir": "range", "confirmed": False}}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] == 1, "بوابة البناء (OR من أربعة) = شرط واحد لا أربعة")


def test_unknown_in_required_path_count_none():
    print("\n[7] حقل مجهول في المسار المطلوب ⇒ count=None:")
    # WATCH بلا مؤشرات (tilt مجهول) — الميل جوهري لمسار FORMING
    rec = {"ticker": "W7", "catalyst": 50, "indicators": []}
    check(classify_setup(rec) == "WATCH", "التصنيف WATCH")
    st = stock_state(rec)
    check(st["missing_conditions_count"] is None, "count=None (الميل مجهول في المسار)")


def test_template_number_matches():
    print("\n[8] القالب: الرقم الظاهر = conditions_for_next_state:")
    rec = {"ticker": "T8", "name": "T", "catalyst": 79, "piotroski": 6,
           "indicators": _tilt("pos1"), "structure": {"trend": "up"},
           "break_status": {"dir": "range", "confirmed": False}}
    _, count = conditions_for_next_state(rec, classify_setup(rec))
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check(count == 1, "الحساب المركزي = 1")
    check(f"ينقصه {count} شرط" in html, f"القالب يعرض «ينقصه {count} شرط» مطابقاً للحساب")


def main():
    print("=" * 60)
    print("PHASE 5 ISSUE #2 — next_state conditions")
    print("=" * 60)
    test_forming_next_is_one_technical()
    test_adding_confirmation_moves_to_near_ready()
    test_near_ready_high_catalyst_tilt_only()
    test_near_ready_known_below_catalyst()
    test_watch_next_forming_not_ready_gates()
    test_or_paths_not_summed()
    test_unknown_in_required_path_count_none()
    test_template_number_matches()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات ISSUE #2 نجحت ✓ ({_passed} تحقّقاً).")
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
