"""
test_phase5_catalyst_unknown_regression.py — PHASE 5 (إصلاح MEDIUM: Catalyst UNKNOWN ≠ BELOW).

يقفل التمييز الصريح بين ثلاث حالات في missing_conditions:
  - catalyst is None  ⇒ UNKNOWN: «بيانات النمو (Catalyst) غير متوفّرة» + count=None (لا «دون العتبة»).
  - catalyst < 80     ⇒ KNOWN-BELOW: «درجة النمو (Catalyst) دون العتبة» ويُحتسب.
  - catalyst >= 80    ⇒ متحقّق، لا يُضاف.
وواجهة صادقة: عند count=None لا تظهر «ينقصه X شروط» بل «بعض الشروط غير قابلة للتقييم».

التشغيل:  python tests/test_phase5_catalyst_unknown_regression.py
"""

import os
import sys
import tempfile

os.environ.pop("APP_PASSWORD", None)  # وضع مفتوح لعرض البطاقات
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import fmp_client, news_client  # noqa: E402
fmp_client.get_quote = lambda *a, **k: None
fmp_client.get_historical_prices = lambda *a, **k: None
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from services.state import stock_state, classify_setup  # noqa: E402

_passed = 0
_failed = 0
_BELOW = "درجة النمو (Catalyst) دون العتبة"
_UNAVAIL = "بيانات النمو (Catalyst) غير متوفّرة"


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


def test_forming_catalyst_none():
    print("\n[A] FORMING + catalyst=None ⇒ UNKNOWN لا BELOW:")
    rec = {"ticker": "FRM", "catalyst": None, "indicators": _tilt("pos1"),
           "structure": {"trend": "up"}}
    check(classify_setup(rec) == "FORMING", "التصنيف FORMING")
    st = stock_state(rec)
    check(_BELOW not in st["missing_conditions"], "لا يظهر «Catalyst دون العتبة»")
    check(_UNAVAIL in st["missing_conditions"], "يظهر «بيانات Catalyst غير متوفّرة»")
    check(st["missing_conditions_count"] is None, "missing_conditions_count = None")


def test_near_ready_catalyst_none():
    print("\n[B] NEAR_READY + catalyst=None ⇒ UNKNOWN لا BELOW:")
    rec = {"ticker": "NR", "catalyst": None, "indicators": _tilt("pos1"),
           "structure": {"event": "BOS", "event_dir": "up", "trend": "up"}}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY (بلا اختراع Catalyst منخفض)")
    st = stock_state(rec)
    check(_BELOW not in st["missing_conditions"], "لا يظهر «Catalyst دون العتبة»")
    check(_UNAVAIL in st["missing_conditions"], "يظهر «بيانات Catalyst غير متوفّرة»")
    check(st["missing_conditions_count"] is None, "missing_conditions_count = None (غير موثوق)")


def test_catalyst_79_known_below():
    print("\n[C] catalyst=79 ⇒ KNOWN BELOW (يظهر ويُحتسب):")
    rec = {"ticker": "C79", "catalyst": 79, "indicators": _tilt("pos1"),
           "structure": {"trend": "up"}}
    st = stock_state(rec)
    check(_BELOW in st["missing_conditions"], "يظهر «Catalyst دون العتبة» (معروف)")
    check(_UNAVAIL not in st["missing_conditions"], "لا يظهر وصف «غير متوفّرة»")
    check(isinstance(st["missing_conditions_count"], int) and st["missing_conditions_count"] >= 1,
          f"العدد رقم صحيح ويُحتسب ({st['missing_conditions_count']})")


def test_catalyst_80_met():
    print("\n[D] catalyst=80 ⇒ متحقّق (لا يُضاف شرط Catalyst):")
    rec = {"ticker": "C80", "catalyst": 80, "indicators": _tilt("neg1")}  # tilt سلبي ⇒ NEAR_READY
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY (catalyst≥80 + neg1)")
    st = stock_state(rec)
    check(_BELOW not in st["missing_conditions"] and _UNAVAIL not in st["missing_conditions"],
          "لا شرط Catalyst (متحقّق ≥80)")
    check(st["missing_conditions_count"] == 1, "العدّ يشمل الميل فقط (1)")


def test_ui_count_none_no_number():
    print("\n[E] الواجهة: count=None ⇒ لا «ينقصه X شروط» بل وصف غياب البيانات:")
    rec = {"ticker": "UI1", "name": "UI", "catalyst": None, "piotroski": None,
           "indicators": _tilt("pos1"), "structure": {"trend": "up"}}
    rec["state"] = stock_state(rec)  # كما يُخزَّن ليلياً
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه" not in html, "لا يظهر «ينقصه X شرط» عند count=None")
    check("غير قابلة للتقييم" in html, "يظهر «بعض الشروط غير قابلة للتقييم»")


def test_ui_count_known_shows_number():
    print("\n[E] الواجهة: count معروف ⇒ يظهر الرقم:")
    rec = {"ticker": "UI2", "name": "UI", "catalyst": 79, "piotroski": 6,
           "indicators": _tilt("pos1"), "structure": {"trend": "up"}}
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه" in html, "يظهر «ينقصه X شرط» عند عدد معروف")


def test_backcompat_no_catalyst_key():
    print("\n[F] توافق خلفي: record بلا مفتاح catalyst ⇒ بلا انهيار وبلا تفسير زائف:")
    rec = {"ticker": "NOKEY", "indicators": _tilt("pos1"), "structure": {"trend": "up"}}
    st = stock_state(rec)  # لا يرفع استثناء
    check(_BELOW not in st["missing_conditions"], "لا «دون العتبة» بلا بيانات نمو")
    check(_UNAVAIL in st["missing_conditions"], "يوصف النقص بصدق (غير متوفّرة)")
    check(st["missing_conditions_count"] is None, "count=None (لا رقم زائف)")


def main():
    print("=" * 60)
    print("PHASE 5 — Catalyst UNKNOWN ≠ BELOW (MEDIUM fix)")
    print("=" * 60)
    test_forming_catalyst_none()
    test_near_ready_catalyst_none()
    test_catalyst_79_known_below()
    test_catalyst_80_met()
    test_ui_count_none_no_number()
    test_ui_count_known_shows_number()
    test_backcompat_no_catalyst_key()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات إصلاح Catalyst UNKNOWN نجحت ✓ ({_passed} تحقّقاً).")
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
