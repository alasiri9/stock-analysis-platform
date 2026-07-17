"""
smoke_test.py — اختبار «كل الصفحات ترجع 200» (اختبار دخان بسيط بلا مكتبات إضافية).

الهدف (لأحمد):
شبكة أمان تُشغَّل قبل الدمج في main، تفتح كل صفحات المنصة وتتأكد أنها لا تنكسر
(لا خطأ 500). لو صار خطأ في قالب أو مسار بعد أي تعديل، يمسكه هذا الاختبار
قبل ما يوصل الموقع الحي.

كيف تشغّله:
    python tests/smoke_test.py
لو طبع «كل الصفحات سليمة ✓» فالدمج آمن. لو طبع ✗ عند صفحة، لا تدمج قبل الإصلاح.

ملاحظات تقنية:
- يشغّل نسخة اختبار من التطبيق على قاعدة SQLite مؤقتة (لا يمسّ قاعدة الإنتاج).
- يفتح المنصة بوضع مفتوح (APP_PASSWORD غير مضبوط) فلا يحتاج تسجيل دخول،
  ويرى صفحات المدير أيضاً.
- يعزل استدعاءات الشبكة (FMP/Finnhub) فالاختبار سريع وثابت ولا يستهلك أي باقة.
"""

import os
import sys
import tempfile

# --- تجهيز البيئة قبل استيراد التطبيق ---
os.environ.pop("APP_PASSWORD", None)          # وضع مفتوح: بلا تسجيل دخول + صلاحيات مدير
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

# جذر المشروع في مسار الاستيراد (حتى يعمل من أي مجلد)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- عزل الشبكة: نستبدل دوال جلب البيانات الخارجية بردود فارغة آمنة ---
from services import fmp_client, news_client   # noqa: E402

fmp_client.get_quote = lambda *a, **k: None
fmp_client.get_profile = lambda *a, **k: None
fmp_client.get_financials = lambda *a, **k: {"income": None, "balance": None, "cashflow": None}
fmp_client.get_historical_prices = lambda *a, **k: None
fmp_client.get_earnings_calendar = lambda *a, **k: None
fmp_client.get_shares_float_all = lambda *a, **k: None
news_client.get_market_news = lambda *a, **k: []

from app import app   # noqa: E402  (استيراده ينشئ الجداول على القاعدة المؤقتة)

# الصفحات التي نفتحها (GET، بلا معطيات إلزامية). المقبول: 200 أو تحويلة 30x.
PAGES = [
    "/", "/gems", "/leaders", "/prelaunch", "/signals", "/learn", "/how",
    "/health", "/dashboard", "/business", "/settings", "/pulse", "/movers",
    "/earnings", "/daily-report", "/radar", "/news", "/flow", "/performance",
    "/calculator", "/compare", "/watchlist", "/alerts", "/portfolio",
    "/stock", "/stock/AAPL", "/messages", "/export/scanner.xlsx", "/login",
]

OK_CODES = (200, 301, 302, 303, 304, 308)


def run():
    client = app.test_client()
    failures = []
    for path in PAGES:
        try:
            resp = client.get(path)
            code = resp.status_code
        except Exception as e:  # خطأ يمنع الرد أصلاً = كسر مؤكّد
            code = None
            print(f"  ✗ {path:24s} استثناء: {type(e).__name__}: {e}")
            failures.append(path)
            continue
        if code in OK_CODES:
            print(f"  ✓ {path:24s} {code}")
        else:
            print(f"  ✗ {path:24s} {code}")
            failures.append(path)

    print("-" * 44)
    if failures:
        print(f"✗ {len(failures)} صفحة/صفحات فيها مشكلة: {', '.join(failures)}")
        print("لا تدمج قبل إصلاحها.")
        return 1
    print(f"كل الصفحات سليمة ✓ ({len(PAGES)} صفحة) — الدمج آمن.")
    return 0


if __name__ == "__main__":
    try:
        code = run()
    finally:
        try:
            os.unlink(_db_path)
        except OSError:
            pass
    os._exit(code)   # خروج فوري (لا ننتظر خيط المجدول الخلفي)
