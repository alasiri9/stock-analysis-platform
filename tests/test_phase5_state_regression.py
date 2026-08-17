"""
test_phase5_state_regression.py — PHASE 5 (A: State Engine · B: Lifecycle resolution).

يقفل قواعد المحرك الحتمي بلا threshold رقمي جديد:
- READY = HIGH #1 (catalyst≥80 ∧ tilt∉{neg1,neg2}).
- المفقود ≠ إيجابي. تصنيف حتمي. ADX هابط لا يصبح bullish.
- INVALIDATED/LOSING_MOMENTUM لا يظهران بلا lifecycle صاعد (context).
- live_price لا يغيّر الحالة.

التشغيل:  python tests/test_phase5_state_regression.py
"""

import os
import sys
import tempfile

os.environ.setdefault("APP_PASSWORD", "p5")
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import state as S  # noqa: E402
from services.state import classify_setup, resolve_lifecycle_state, stock_state  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _tilt(kind):
    """قائمة مؤشرات تعطي tech_tilt.kind المطلوب (بلا اعتماد على تسميات محددة)."""
    table = {  # (bull, neutral, bear) → frac
        "neu":  (2, 1, 2),   # 0.50
        "pos1": (3, 0, 2),   # 0.60
        "pos2": (4, 0, 1),   # 0.80
        "neg1": (2, 0, 3),   # 0.40
        "neg2": (1, 0, 4),   # 0.20
    }
    b, n, r = table[kind]
    out = []
    out += [{"label": f"b{i}", "status": "bull"} for i in range(b)]
    out += [{"label": f"n{i}", "status": "neutral"} for i in range(n)]
    out += [{"label": f"r{i}", "status": "bear"} for i in range(r)]
    return out


def _rec(catalyst=50, tilt="neu", **extra):
    r = {"ticker": "T", "catalyst": catalyst, "indicators": _tilt(tilt) if tilt else []}
    r.update(extra)
    return r


# ==================== A) State Engine ====================
def test_ready_high1():
    print("\n[A] READY = HIGH #1 (catalyst≥80 ∧ الميل غير سلبي):")
    from services import screener
    # تأكيد أن مولّد الميل يعطي النوع الصحيح فعلاً
    check(screener.tech_tilt(_rec(tilt="neu"))["kind"] == "neu", "مولّد الميل: neu صحيح")
    check(screener.tech_tilt(_rec(tilt="neg1"))["kind"] == "neg1", "مولّد الميل: neg1 صحيح")
    check(screener.tech_tilt(_rec(tilt="neg2"))["kind"] == "neg2", "مولّد الميل: neg2 صحيح")

    check(classify_setup(_rec(85, "neg1")) != "READY", "catalyst≥80 + neg1 ⇒ ليست READY")
    check(classify_setup(_rec(85, "neg1")) == "NEAR_READY", "catalyst≥80 + neg1 ⇒ NEAR_READY")
    check(classify_setup(_rec(85, "neg2")) != "READY", "catalyst≥80 + neg2 ⇒ ليست READY")
    check(classify_setup(_rec(85, "neu")) == "READY", "catalyst≥80 + neu ⇒ READY (المحايد كافٍ)")
    check(classify_setup(_rec(85, "pos1")) == "READY", "catalyst≥80 + pos1 ⇒ READY")
    check(classify_setup(_rec(70, "pos2")) != "READY", "catalyst<80 ⇒ ليست READY حتى مع ميل قوي")


def test_missing_not_positive():
    print("\n[A] المفقود ≠ إيجابي:")
    r = {"ticker": "T", "catalyst": 85, "indicators": []}  # لا مؤشرات ⇒ tilt None
    from services import screener
    check(screener.tech_tilt(r) is None, "بلا مؤشرات ⇒ tech_tilt None")
    check(classify_setup(r) != "READY", "catalyst≥80 لكن tilt مفقود ⇒ ليست READY")
    check(classify_setup(r) == "WATCH", "tilt مفقود + لا بناء ⇒ WATCH")


def test_bearish_adx_not_bullish():
    print("\n[A] ADX هابط قوي لا يصبح صاعداً:")
    r = _rec(85, tilt=None)
    r["indicators"] = [{"label": "ADX", "status": "bear"},
                       {"label": "MACD", "status": "bear"},
                       {"label": "RSI", "status": "bear"},
                       {"label": "EMA", "status": "bear"}]
    from services import screener
    t = screener.tech_tilt(r)
    check(t["kind"] in ("neg1", "neg2"), "مؤشرات هابطة (منها ADX) ⇒ ميل سلبي")
    check(classify_setup(r) != "READY", "ADX هابط + بقية هابطة ⇒ ليست READY")


def test_launched_extended():
    print("\n[A] LAUNCHED / EXTENDED من إشارات الاختراق/الاستمرار:")
    r = _rec(85, "pos1", break_status={"confirmed": True, "dir": "breakout"})
    check(classify_setup(r) == "LAUNCHED", "اختراق مؤكّد ⇒ LAUNCHED (يغلب READY)")
    r2 = _rec(85, "pos1", sustained={"sustained": True, "entry_zone": "extended"})
    check(classify_setup(r2) == "EXTENDED", "اختراق مستمر ممتد ⇒ EXTENDED")


def test_deterministic():
    print("\n[A] تصنيف حتمي (نفس الدخل ⇒ نفس الخرج):")
    r = _rec(85, "neu", structure={"trend": "up"})
    a = classify_setup(r)
    b = classify_setup(r)
    check(a == b, f"تكرار التصنيف ثابت ({a})")
    s1 = stock_state(r)["code"]
    s2 = stock_state(r)["code"]
    check(s1 == s2, "stock_state ثابت")


# ==================== B) Lifecycle resolution ====================
def test_no_lifecycle_no_invalidated():
    print("\n[B] سهم بلا lifecycle: لا INVALIDATED ولا LOSING رغم إشارات هبوط:")
    down = _rec(50, "neg2", break_status={"confirmed": True, "dir": "breakdown"})
    setup = classify_setup(down)
    r_no = resolve_lifecycle_state(setup, down, {"open_bullish": False})
    check(r_no != "INVALIDATED", "كسر مؤكّد بلا lifecycle ⇒ ليست INVALIDATED")
    weak = _rec(50, "neg1", structure={"trend": "up"})
    r_w = resolve_lifecycle_state(classify_setup(weak), weak, {"open_bullish": False})
    check(r_w != "LOSING_MOMENTUM", "ضعف بلا lifecycle ⇒ ليست LOSING_MOMENTUM")


def test_lifecycle_invalidated():
    print("\n[B] lifecycle صاعد مفتوح + كسر مؤكّد ⇒ INVALIDATED:")
    down = _rec(50, "neg2", break_status={"confirmed": True, "dir": "breakdown"})
    r = resolve_lifecycle_state(classify_setup(down), down, {"open_bullish": True})
    check(r == "INVALIDATED", "open_bullish + brk_down_confirmed ⇒ INVALIDATED")
    choch = _rec(50, "neg1", structure={"event": "CHOCH", "event_dir": "down", "trend": "down"})
    r2 = resolve_lifecycle_state(classify_setup(choch), choch, {"open_bullish": True})
    check(r2 == "INVALIDATED", "open_bullish + CHOCH هابط ⇒ INVALIDATED")


def test_lifecycle_losing():
    print("\n[B] lifecycle صاعد مفتوح + تراجع + ضعف ⇒ LOSING_MOMENTUM:")
    weak = _rec(50, "neg1", structure={"trend": "up"})
    check(classify_setup(weak) == "WATCH", "التصنيف النقي WATCH (تراجع عن حالة سابقة)")
    r = resolve_lifecycle_state(classify_setup(weak), weak, {"open_bullish": True})
    check(r == "LOSING_MOMENTUM", "open_bullish + setup غير متقدّم + neg1 ⇒ LOSING_MOMENTUM")
    # ميل غير سلبي ⇒ لا LOSING (تماسك عادي)
    calm = _rec(50, "neu", structure={"trend": "up"})
    r2 = resolve_lifecycle_state(classify_setup(calm), calm, {"open_bullish": True})
    check(r2 != "LOSING_MOMENTUM", "بلا ضعف ⇒ ليست LOSING_MOMENTUM")


def test_pure_layer_never_lifecycle_states():
    print("\n[B] الطبقة النقية (context=None) لا تُظهر INVALIDATED/LOSING أبداً:")
    down = _rec(50, "neg2", break_status={"confirmed": True, "dir": "breakdown"})
    code = stock_state(down, context=None)["code"]
    check(code not in ("INVALIDATED", "LOSING_MOMENTUM"),
          f"context=None ⇒ ليست حالة دورة حياة ({code})")


def test_live_price_no_effect():
    print("\n[B] live_price لا يغيّر الحالة (فصل HIGH #3):")
    base = _rec(85, "neu", structure={"trend": "up"})
    withlive = dict(base); withlive["live_price"] = 999.0
    c1 = stock_state(base)["code"]
    c2 = stock_state(withlive)["code"]
    check(c1 == c2, f"نفس الحالة مع/بلا live_price ({c1})")


def main():
    print("=" * 60)
    print("PHASE 5 — State Engine + Lifecycle (A + B)")
    print("=" * 60)
    test_ready_high1()
    test_missing_not_positive()
    test_bearish_adx_not_bullish()
    test_launched_extended()
    test_deterministic()
    test_no_lifecycle_no_invalidated()
    test_lifecycle_invalidated()
    test_lifecycle_losing()
    test_pure_layer_never_lifecycle_states()
    test_live_price_no_effect()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (A+B) نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
