"""
test_phase5_change_regression.py — PHASE 5 (E: Change Tracking).

يقفل build_change_summary:
- لا لقطة سابقة ⇒ لا مقارنة (رسالة صريحة).
- تحسّن/تراجع/دون تغيّر صحيحة.
- حقل مفقود (None→قيمة) لا يصبح تحسّناً زائفاً.
- تغيّر السعر وحده لا يُصنَّف تحسّناً في الجودة.

التشغيل:  python tests/test_phase5_change_regression.py
"""

import os
import sys
import tempfile

os.environ.setdefault("APP_PASSWORD", "p5")
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tracking import build_change_summary  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _snap(catalyst=80, algo=60, measures=8, piotroski=6, tilt="neu",
          structure="neutral", state="READY", gem=False, ready=True, price=100.0):
    return {"catalyst": catalyst, "algomatix_score": algo, "measures_met": measures,
            "piotroski": piotroski, "tech_tilt_kind": tilt, "structure_status": structure,
            "state_code": state, "is_gem": gem, "is_ready": ready, "analysis_price": price}


def test_no_previous():
    print("\n[E] لا لقطة سابقة ⇒ لا مقارنة:")
    r = build_change_summary(_snap(), None)
    check(r["has_previous"] is False, "has_previous = False")
    check("لا يوجد تاريخ سابق كافٍ للمقارنة" in r["note"], "رسالة صريحة بلا اختلاق مقارنة")
    check(r["classification"] is None, "لا تصنيف بلا سابق")


def test_positive():
    print("\n[E] تحسّن (نمو/مؤشر/تأكيد أعلى):")
    prev = _snap(catalyst=80, algo=60, measures=6)
    cur = _snap(catalyst=90, algo=72, measures=10)
    r = build_change_summary(cur, prev)
    check(r["classification"] == "improved", "التصنيف = تحسّن")
    check(r["net"] > 0, "net موجب")
    keys = {c["key"] for c in r["changes"]}
    check("catalyst" in keys and "algomatix_score" in keys, "التغيّرات تشمل النمو والمؤشر")


def test_negative():
    print("\n[E] تراجع:")
    prev = _snap(catalyst=90, algo=72, tilt="pos1", structure="bull", state="READY", ready=True)
    cur = _snap(catalyst=70, algo=55, tilt="neg1", structure="neutral", state="LOSING_MOMENTUM", ready=False)
    r = build_change_summary(cur, prev)
    check(r["classification"] == "declined", "التصنيف = تراجع")
    check(r["net"] < 0, "net سالب")


def test_unchanged():
    print("\n[E] دون تغيّر جوهري:")
    s = _snap()
    r = build_change_summary(dict(s), dict(s))
    check(r["classification"] == "unchanged", "التصنيف = دون تغيّر")
    check(r["changes"] == [], "لا تغيّرات مسجّلة")


def test_missing_not_false_improvement():
    print("\n[E] حقل مفقود (None→قيمة) لا يصبح تحسّناً زائفاً:")
    prev = _snap(catalyst=None, algo=None, measures=None, piotroski=None)
    cur = _snap(catalyst=90, algo=80, measures=12, piotroski=9)
    # كل الحقول الرقمية كانت None سابقاً؛ والباقي ثابت ⇒ لا تُحتسب كتحسّن
    r = build_change_summary(cur, prev)
    check(r["classification"] == "unchanged",
          "None→قيمة لا يُحتسب تحسّناً (المقارنة تتطلب طرفين معلومين)")
    num_keys = {c["key"] for c in r["changes"]} & {"catalyst", "algomatix_score", "measures_met", "piotroski"}
    check(not num_keys, "لا تغيّرات رقمية مسجّلة من قيم مفقودة")


def test_price_only_not_quality():
    print("\n[E] تغيّر السعر وحده لا يُصنَّف تحسّناً:")
    prev = _snap(price=100.0)
    cur = _snap(price=140.0)  # كل شيء ثابت عدا السعر
    r = build_change_summary(cur, prev)
    check(r["classification"] == "unchanged", "سعر أعلى وحده ⇒ دون تغيّر جوهري")
    check(r["price_info"] is not None, "معلومة السعر تُعرض")
    check(r["price_info"].get("quality") is False, "معلومة السعر معلّمة أنها ليست جودة")
    check(r["net"] == 0, "السعر لا يدخل net")


def main():
    print("=" * 60)
    print("PHASE 5 — Change Tracking (E)")
    print("=" * 60)
    test_no_previous()
    test_positive()
    test_negative()
    test_unchanged()
    test_missing_not_false_improvement()
    test_price_only_not_quality()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات PHASE 5 (E) نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
