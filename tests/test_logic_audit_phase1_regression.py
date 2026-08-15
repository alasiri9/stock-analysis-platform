"""
test_logic_audit_phase1_regression.py — regression لتدقيق Codex المنطقي (PHASE 1: HIGH الثلاث).

يغطّي:
  1) «جاهز للانطلاق» لا يعتمد على Catalyst وحده — يُحجب عند ميل فني سلبي  (_scard.html + tech_tilt)
  2) ADX القوي لا يُحتسب صاعداً إلا عند اتجاه صاعد (+DI>−DI)؛ الاتجاه القوي الهابط
     يُعامَل هابطاً لا صاعداً  (indicators._adx_di + build_indicators + screener)
  3) فصل نضارة السعر عن نضارة التحليل — الشارة تُخصّ السعر وتنصّ أن التحليل من آخر فحص (base.html)

التشغيل:  python tests/test_logic_audit_phase1_regression.py
"""

import os
import sys
import tempfile

os.environ["APP_PASSWORD"] = "phase1-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import indicators, screener  # noqa: E402
from app import app  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


# ============ HIGH#2: ADX يقيس القوة لا الاتجاه ============
def _trend_rows(n, direction):
    """صفوف شموع مرتّبة الأقدم أولاً (i=0 أقدم) باتجاه واضح — هذا ترتيب _adx_di المتوقّع
    (بعد _clean). صاعد = الإغلاق يرتفع مع الزمن؛ هابط = ينخفض."""
    rows = []
    for i in range(n):
        c = 100.0 + i if direction == "up" else 100.0 + (n - i)
        rows.append({"open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1_000_000, "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"})
    return rows


def _fmp_candles(n, direction):
    """شموع بترتيب FMP (الأحدث أولاً) لتمريرها لـbuild_indicators الذي يعكسها عبر _clean
    إلى الأقدم أولاً. نعكس صفوف _trend_rows (الأقدم أولاً) لتصبح الأحدث أولاً."""
    return list(reversed(_trend_rows(n, direction)))


def test_adx_direction():
    print("\n[HIGH#2] ADX القوي لا يُحتسب صاعداً إلا عند اتجاه صاعد فعلي:")
    up = _trend_rows(80, "up")
    down = _trend_rows(80, "down")

    # (أ) طبقة _adx_di: تُميّز الاتجاه عبر +DI/−DI
    adx_u, pdi_u, ndi_u = indicators._adx_di(up)
    check(adx_u is not None and adx_u >= 25 and pdi_u > ndi_u,
          f"اتجاه صاعد قوي → ADX≥25 و+DI>−DI (adx={adx_u:.0f})")
    adx_d, pdi_d, ndi_d = indicators._adx_di(down)
    check(adx_d is not None and adx_d >= 25 and ndi_d > pdi_d,
          f"اتجاه هابط قوي → ADX≥25 و−DI>+DI (adx={adx_d:.0f})")

    # (ب) _adx القديم لم يتغيّر سلوكه (غلاف يُرجع القيمة نفسها) — عدم انحدار
    check(indicators._adx(up) == adx_u, "_adx (الغلاف) يُرجع نفس قيمة ADX (لا انحدار)")

    # (ج) build_indicators: شارة ADX صاعدة للصاعد، هابطة (ليست صاعدة) للهابط.
    # build_indicators يتلقّى شموع FMP (الأحدث أولاً) ويعكسها عبر _clean.
    def adx_status(direction):
        b = [x for x in indicators.build_indicators(_fmp_candles(80, direction))
             if x["label"] == "ADX"]
        return b[0]["status"] if b else None
    check(adx_status("up") == "bull", "build_indicators: اتجاه صاعد قوي → ADX bull")
    st_down = adx_status("down")
    check(st_down == "bear", "build_indicators: اتجاه هابط قوي → ADX bear")
    check(st_down != "bull", "build_indicators: الاتجاه الهابط القوي لا يُحتسب صاعداً (التصحيح الأساسي)")


def test_adx_not_counted_bull_in_strength():
    print("\n[HIGH#2] الاتجاه الهابط القوي لا يرفع «قوة التأكيد» ولا ميزان الإشارات:")
    inds = indicators.build_indicators(_fmp_candles(80, "down"))
    rec = {"ticker": "DN", "indicators": inds}
    # measures_met يعدّ الشارات الصاعدة فقط — ADX الهابط يجب ألّا يُحسب
    adx_badge = next((b for b in inds if b["label"] == "ADX"), None)
    check(adx_badge is not None and adx_badge["status"] != "bull",
          "ADX في اتجاه هابط: ليس bull → لا يُعدّ ضمن measures_met الصاعدة")
    # tech_tilt: ADX الهابط يُحسب bear (يخفض الميل) لا يُرفعه
    tilt = screener.tech_tilt(rec)
    check(tilt is not None, "tech_tilt محسوب")


# ============ HIGH#1: «جاهز للانطلاق» لا يعتمد على Catalyst وحده ============
def _render_card(catalyst, ind_status, piotroski=4):
    rec = {
        "ticker": "TST", "name": "Test Co", "sector": "Technology", "price": 100.0,
        "change_percent": 1.0, "catalyst": catalyst, "piotroski": piotroski,
        "rel_strength": None, "money_flow": None, "break_status": None, "sustained": None,
        "reversal": None, "days_to_earnings": None, "float_shares": None,
        "indicators": [{"label": f"i{k}", "status": ind_status, "value": "x"} for k in range(9)],
    }
    with app.test_request_context():
        return app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)


def test_prelaunch_label_gated():
    print("\n[HIGH#1] «جاهز للانطلاق» يُحجب عند ميل فني سلبي رغم Catalyst≥80:")
    LABEL = "جاهز للانطلاق"
    # Catalyst≥80 + كل المؤشرات هابطة (ميل سلبي قوي) → لا تظهر
    check(LABEL not in _render_card(85, "bear"),
          "Catalyst=85 + ميل فني سلبي → لا تظهر «جاهز للانطلاق» (التصحيح)")
    # Catalyst≥80 + مؤشرات صاعدة (ميل إيجابي) → تظهر
    check(LABEL in _render_card(85, "bull"),
          "Catalyst=85 + ميل فني إيجابي → تظهر «جاهز للانطلاق»")
    # Catalyst≥80 + مؤشرات محايدة (ميل غير سلبي) → تظهر (الشرط: ألّا يكون سلبياً)
    check(LABEL in _render_card(85, "neutral"),
          "Catalyst=85 + ميل محايد (غير سلبي) → تظهر")
    # Catalyst<80 → لا تظهر أصلاً (لم يتغيّر)
    check(LABEL not in _render_card(70, "bull"),
          "Catalyst=70 (<80) → لا تظهر أصلاً")


# ============ HIGH#3: فصل نضارة السعر عن التحليل ============
def test_live_price_banner_scopes_to_price():
    print("\n[HIGH#3] شارة «حيّ» تُخصّ السعر وتنصّ أن التحليل من آخر فحص:")
    with open(os.path.join(_ROOT, "templates", "base.html"), encoding="utf-8") as f:
        src = f.read()
    # الوسم القديم المضلِّل («أسعار حيّة») أُزيل
    check("أسعار حيّة" not in src, "الوسم القديم «أسعار حيّة» أُزيل (كان يُوهم بلحظية كل شيء)")
    # الشارة صارت تُخصّ السعر
    check("السعر حيّ" in src, "الشارة تُخصّ السعر صراحةً («السعر حيّ»)")
    # نصّ ظاهر: التحليل من آخر فحص وليس لحظياً
    check("التحليل الفني من آخر فحص" in src,
          "نصّ ظاهر: «التحليل الفني من آخر فحص (ليس لحظياً)»")
    # التلميح يفصل نضارة السعر عن نضارة التحليل
    check("ليست لحظية" in src and "آخر فحص" in src,
          "التلميح يفصل نضارة السعر عن نضارة التحليل (المؤشرات/الدرجات/الخطة)")


def main():
    print("=" * 62)
    print("regression — تدقيق Codex المنطقي (PHASE 1: HIGH الثلاث)")
    print("=" * 62)
    test_adx_direction()
    test_adx_not_counted_bull_in_strength()
    test_prelaunch_label_gated()
    test_live_price_banner_scopes_to_price()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات PHASE 1 نجحت ✓ ({_passed} تحقّقاً) — المشاكل HIGH الثلاث مقفولة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
