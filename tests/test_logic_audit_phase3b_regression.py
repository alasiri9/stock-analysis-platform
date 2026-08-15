"""
test_logic_audit_phase3b_regression.py — regression لإصلاحَي Codex الحسابيين في Algomatix Score:

#8-A: الفريم اليومي (هيكل السوق اليومي) كان يُحتسب مرتين — في مدرسة structure المستقلّة، ثم
   عبر daily ضمن مدرسة frames. الآن مدرسة frames تعتمد الأسبوعي+الشهري فقط (اليومي مستبعَد،
   له مدرسته)، فلا يؤثّر المصدر نفسه مرتين. record["frames"] يبقى كما هو للعرض.

#8-B: المدارس متعدّدة المكوّنات (trend/momentum/fundamentals/liquidity) كانت تُمتوسِط على
   الموجود فقط، فيمنح نقص المكوّنات أفضلية (EMA صاعد وحده → 1.0). الآن المقام ثابت والمكوّن
   المفقود = 0.5 (حياد)؛ القيمة 0/bear الفعلية تبقى حقيقية.

التشغيل:  python tests/test_logic_audit_phase3b_regression.py
"""

import os
import sys
import tempfile

os.environ["APP_PASSWORD"] = "phase3b-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import screener  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _sub(record, key):
    return screener._algx_subscores(record)[key]


# ==================== #8-A: الفريم اليومي لا يُحتسب مرتين ====================
def test_daily_structure_not_double_counted():
    print("\n[#8-A] تغيير الهيكل اليومي وحده لا يؤثّر في مدرستين معاً:")
    frames = {"daily": "up", "weekly": "up", "monthly": "side", "up_count": 2, "down_count": 0}
    r_bull = {"structure": {"status": "bull", "retest_state": "confirmed"},
              "frames": dict(frames)}
    # نغيّر الهيكل اليومي فقط: الحالة اليومية + الفريم اليومي (الأسبوعي/الشهري كما هما)
    r_bear = {"structure": {"status": "bear"},
              "frames": {**frames, "daily": "down", "up_count": 1, "down_count": 1}}
    s_bull = screener._algx_subscores(r_bull)
    s_bear = screener._algx_subscores(r_bear)
    check(s_bull["structure"] != s_bear["structure"],
          "مدرسة الهيكل تتغيّر بتغيّر الهيكل اليومي (كما يجب)")
    check(abs(s_bull["frames"] - s_bear["frames"]) < 1e-9,
          "مدرسة الفريمات لا تتغيّر بتغيّر الهيكل اليومي (لا احتساب مزدوج)")


def test_frames_school_uses_weekly_monthly_only():
    print("\n[#8-A] مدرسة الفريمات = متوسط الأسبوعي+الشهري فقط (اليومي مستبعَد):")
    # يومي هابط لكن الأسبوعي والشهري صاعدان → المدرسة 1.0 (تجاهل اليومي)
    r = {"frames": {"daily": "down", "weekly": "up", "monthly": "up",
                    "up_count": 2, "down_count": 1}}
    check(abs(_sub(r, "frames") - 1.0) < 1e-9,
          "يومي هابط + أسبوعي/شهري صاعدان → 1.0 (اليومي لا يخفضها)")
    r2 = {"frames": {"daily": "up", "weekly": "down", "monthly": "side",
                     "up_count": 1, "down_count": 1}}
    check(abs(_sub(r2, "frames") - 0.25) < 1e-9,
          "أسبوعي هابط + شهري محايد → (0.0+0.5)/2=0.25 (اليومي الصاعد لا يرفعها)")
    check(abs(_sub({"frames": None}, "frames") - 0.5) < 1e-9, "بلا فريمات → 0.5")


# ==================== #8-B: المكوّن المفقود = 0.5 (مقام ثابت) ====================
def _trend(*st):
    keys = ("EMA", "تقاطع", "سوبرترند", "ADX")
    return {"indicators": [{"label": k, "status": s} for k, s in zip(keys, st) if s is not None]}


def test_trend_fixed_denominator():
    print("\n[#8-B] الاتجاه: مقام ثابت (4)، المفقود = 0.5:")
    check(_sub(_trend("bull", "bull", "bull", "bull"), "trend") == 1.0, "الكل صاعد → 1.0")
    check(_sub(_trend("bear", "bear", "bear", "bear"), "trend") == 0.0, "الكل هابط → 0.0")
    check(abs(_sub(_trend("bull", None, None, None), "trend") - 0.625) < 1e-9,
          "صاعد واحد + 3 مفقودة → 0.625 (لا يتضخّم إلى 1.0)")
    check(abs(_sub(_trend("bull", "bull", None, None), "trend") - 0.75) < 1e-9,
          "صاعدان + 2 مفقودة → 0.75 (مقام 4 ثابت)")
    check(_sub({}, "trend") == 0.5, "الكل مفقود → 0.5")
    # القيمة 0/bear الفعلية حقيقية (ليست missing): صاعد + 3 هابطة حاضرة → 0.25
    check(abs(_sub(_trend("bull", "bear", "bear", "bear"), "trend") - 0.25) < 1e-9,
          "صاعد + 3 هابطة حاضرة → 0.25 (bear قيمة حقيقية 0، لا تُعامَل missing)")
    # النقص لا يتضخّم: مفقود = محايد تماماً، وأقلّ من الاكتمال الإيجابي
    check(_sub(_trend("bull", None, None, None), "trend")
          == _sub(_trend("bull", "neutral", "neutral", "neutral"), "trend"),
          "3 مفقودة = 3 محايدة تماماً (النقص لا يمنح أفضلية فوق الحياد)")
    check(_sub(_trend("bull", None, None, None), "trend")
          < _sub(_trend("bull", "bull", "bull", "bull"), "trend"),
          "النقص (0.625) < الاكتمال الإيجابي (1.0)")


def _mom(*st):
    keys = ("MACD", "RSI")
    return {"indicators": [{"label": k, "status": s} for k, s in zip(keys, st) if s is not None]}


def test_momentum_fixed_denominator():
    print("\n[#8-B] الزخم: مقام ثابت (2)، المفقود = 0.5:")
    check(_sub(_mom("bull", "bull"), "momentum") == 1.0, "MACD+RSI صاعدان → 1.0")
    check(abs(_sub(_mom("bull", None), "momentum") - 0.75) < 1e-9,
          "MACD صاعد + RSI مفقود → 0.75 (لا 1.0)")
    check(_sub(_mom("bull", "bear"), "momentum") == 0.5, "صاعد + هابط حاضر → 0.5 (0 حقيقية)")
    check(_sub({}, "momentum") == 0.5, "الكل مفقود → 0.5")


def test_fundamentals_fixed_denominator():
    print("\n[#8-B] الجودة والنمو: مقام ثابت (2)، المفقود = 0.5:")
    check(_sub({"piotroski": 9, "catalyst": 100}, "fundamentals") == 1.0, "9 و100 → 1.0")
    check(abs(_sub({"piotroski": 9}, "fundamentals") - 0.75) < 1e-9,
          "Piotroski 9 + Catalyst مفقود → 0.75 (لا 1.0 — نقص لا يمنح أفضلية)")
    check(abs(_sub({"catalyst": 100}, "fundamentals") - 0.75) < 1e-9,
          "Catalyst 100 + Piotroski مفقود → 0.75")
    check(_sub({"piotroski": 9, "catalyst": 0}, "fundamentals") == 0.5,
          "9 و0 → 0.5 (Catalyst=0 قيمة حقيقية حاضرة)")
    check(_sub({}, "fundamentals") == 0.5, "الكل مفقود → 0.5")


def _liq(mf=None, obv=None, vp=None):
    r = {}
    if mf is not None:
        r["money_flow"] = {"status": mf}
    inds = []
    if obv is not None:
        inds.append({"label": "تراكم", "status": obv})
    if inds:
        r["indicators"] = inds
    if vp is not None:
        r["volume_profile"] = {"position": vp}
    return r


def test_liquidity_fixed_denominator():
    print("\n[#8-B] السيولة: مقام ثابت (3)، المفقود = 0.5:")
    check(abs(_sub(_liq("bull", "bull", "above"), "liquidity") - (1.0 + 1.0 + 0.70) / 3) < 1e-9,
          "الثلاثة حاضرة → متوسطها (لا حذف)")
    check(abs(_sub(_liq(mf="bull"), "liquidity") - (1.0 + 0.5 + 0.5) / 3) < 1e-9,
          "تدفق صاعد + مكوّنان مفقودان → 0.667 (لا 1.0)")
    check(_sub({}, "liquidity") == 0.5, "الكل مفقود → 0.5")
    check(_sub(_liq(mf="bear"), "liquidity") < _sub(_liq(mf="bull"), "liquidity"),
          "تدفق هابط حاضر < صاعد حاضر (bear قيمة حقيقية)")


def test_missing_never_beats_full_positive():
    print("\n[#8-B] النقص لا يبلغ أبداً درجة الاكتمال الإيجابي (لكل المدارس):")
    check(_sub(_trend("bull", None, None, None), "trend")
          < _sub(_trend("bull", "bull", "bull", "bull"), "trend"), "الاتجاه")
    check(_sub(_mom("bull", None), "momentum")
          < _sub(_mom("bull", "bull"), "momentum"), "الزخم")
    check(_sub({"piotroski": 9}, "fundamentals")
          < _sub({"piotroski": 9, "catalyst": 100}, "fundamentals"), "الجودة والنمو")


def main():
    print("=" * 62)
    print("regression — Algomatix #8-A (الفريم اليومي) + #8-B (المقام الثابت)")
    print("=" * 62)
    test_daily_structure_not_double_counted()
    test_frames_school_uses_weekly_monthly_only()
    test_trend_fixed_denominator()
    test_momentum_fixed_denominator()
    test_fundamentals_fixed_denominator()
    test_liquidity_fixed_denominator()
    test_missing_never_beats_full_positive()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات #8-A/#8-B نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
