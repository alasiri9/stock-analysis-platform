"""
test_phase5_catalyst_unknown_regression.py — PHASE 5 (Catalyst UNKNOWN ≠ BELOW).

بعد إصلاح ISSUE #2 صارت شروط النقص مرتبطة بمسار الانتقال الفعلي (next_state):
- في مسار NEAR_READY → READY يكون Catalyst هو البوابة، فيظهر تمييزه:
    * catalyst None  ⇒ «بيانات النمو (Catalyst) غير متوفّرة» + count=None (لا «دون العتبة»).
    * catalyst < 80  ⇒ «درجة النمو (Catalyst) دون العتبة» ويُحتسب.
    * catalyst >= 80 مع ميل سلبي ⇒ الناقص الميل فقط.
- في مسار FORMING → NEAR_READY لا يكون Catalyst بوابة (دون العتبة/مجهول جزء من تعريف NEAR_READY)،
  فلا يظهر أي نص عن Catalyst.

التشغيل:  python tests/test_phase5_catalyst_unknown_regression.py
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


_BOS = {"event": "BOS", "event_dir": "up", "trend": "up"}  # يحقّق تأكيد NEAR_READY


def test_near_ready_catalyst_none():
    print("\n[NEAR_READY→READY] catalyst=None ⇒ UNKNOWN لا BELOW:")
    rec = {"ticker": "NRN", "catalyst": None, "indicators": _tilt("pos1"), "structure": _BOS}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY")
    st = stock_state(rec)
    check(_BELOW not in st["missing_conditions"], "لا «دون العتبة»")
    check(_UNAVAIL in st["missing_conditions"], "يظهر «بيانات Catalyst غير متوفّرة»")
    check(st["missing_conditions_count"] is None, "count=None (بوابة Catalyst مجهولة)")


def test_near_ready_catalyst_79_below():
    print("\n[NEAR_READY→READY] catalyst=79 ⇒ KNOWN BELOW (يُحتسب):")
    rec = {"ticker": "NR79", "catalyst": 79, "indicators": _tilt("pos1"), "structure": _BOS}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY")
    st = stock_state(rec)
    check(_BELOW in st["missing_conditions"], "يظهر «دون العتبة»")
    check(_UNAVAIL not in st["missing_conditions"], "لا «غير متوفّرة»")
    check(st["missing_conditions_count"] == 1, "count=1 (Catalyst البوابة الوحيدة)")


def test_near_ready_catalyst_80_neg1():
    print("\n[NEAR_READY→READY] catalyst≥80 + neg1 ⇒ الناقص الميل فقط:")
    rec = {"ticker": "NR80", "catalyst": 80, "indicators": _tilt("neg1")}
    check(classify_setup(rec) == "NEAR_READY", "التصنيف NEAR_READY")
    st = stock_state(rec)
    check(_BELOW not in st["missing_conditions"] and _UNAVAIL not in st["missing_conditions"],
          "لا شرط Catalyst (متحقّق ≥80)")
    check(st["missing_conditions_count"] == 1, "count=1 (الميل فقط)")


def test_forming_catalyst_not_a_barrier():
    print("\n[FORMING→NEAR_READY] Catalyst ليس بوابة (لا يظهر مهما كان None/<80):")
    for cat in (None, 79):
        rec = {"ticker": "FRM", "catalyst": cat, "indicators": _tilt("pos1"),
               "structure": {"trend": "up"}}
        check(classify_setup(rec) == "FORMING", f"التصنيف FORMING (catalyst={cat})")
        st = stock_state(rec)
        check(_BELOW not in st["missing_conditions"] and _UNAVAIL not in st["missing_conditions"],
              f"لا نص عن Catalyst في مسار FORMING→NEAR_READY (catalyst={cat})")
        check(st["missing_conditions_count"] == 1, f"count=1 (تأكيد فني فقط) (catalyst={cat})")


def test_ui_count_none_no_number():
    print("\n[UI] count=None ⇒ لا رقم بل «غير قابلة للتقييم»:")
    rec = {"ticker": "UI1", "name": "UI", "catalyst": None, "piotroski": None,
           "indicators": _tilt("pos1"), "structure": _BOS}
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه" not in html, "لا «ينقصه X شرط» عند count=None")
    check("غير قابلة للتقييم" in html, "يظهر «بعض الشروط غير قابلة للتقييم»")


def test_ui_count_known_shows_number():
    print("\n[UI] count معروف ⇒ يظهر الرقم:")
    rec = {"ticker": "UI2", "name": "UI", "catalyst": 79, "piotroski": 6,
           "indicators": _tilt("pos1"), "structure": _BOS}
    rec["state"] = stock_state(rec)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)
    check("ينقصه" in html, "يظهر «ينقصه X شرط» عند عدد معروف")


def test_backcompat_no_catalyst_key():
    print("\n[توافق خلفي] NEAR_READY بلا مفتاح catalyst ⇒ UNKNOWN، count=None:")
    rec = {"ticker": "NOKEY", "indicators": _tilt("pos1"), "structure": _BOS}
    st = stock_state(rec)  # لا يرفع استثناء
    check(_BELOW not in st["missing_conditions"], "لا «دون العتبة» بلا بيانات نمو")
    check(_UNAVAIL in st["missing_conditions"], "يوصف النقص بصدق (غير متوفّرة)")
    check(st["missing_conditions_count"] is None, "count=None (لا رقم زائف)")


def main():
    print("=" * 60)
    print("PHASE 5 — Catalyst UNKNOWN ≠ BELOW (مسار الانتقال)")
    print("=" * 60)
    test_near_ready_catalyst_none()
    test_near_ready_catalyst_79_below()
    test_near_ready_catalyst_80_neg1()
    test_forming_catalyst_not_a_barrier()
    test_ui_count_none_no_number()
    test_ui_count_known_shows_number()
    test_backcompat_no_catalyst_key()
    print("\n" + "-" * 60)
    if _failed == 0:
        print(f"كل اختبارات Catalyst UNKNOWN نجحت ✓ ({_passed} تحقّقاً).")
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
