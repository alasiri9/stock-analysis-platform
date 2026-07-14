"""
fmp_client.py — عميل جلب البيانات من FMP (Financial Modeling Prep).

المبادئ المطبّقة هنا:
- None ≠ 0 : لو الحقل غير موجود في رد FMP، نُرجع None (وليس 0). الواجهة تعرض None كـ "—".
- Unit Guards : FMP يُرجع النسب كنسبة مئوية جاهزة أحياناً وككسر أحياناً.
  كل دالة تُوثّق وحدة كل حقل في تعليق واضح فوقها.
- لا نخترع بيانات : لو فشل الاتصال أو ما رجّع شيء، نُرجع None ونوضّح السبب.

ملاحظة عن الـ endpoints: نستخدم واجهة /stable/ حسب توثيق FMP.
"""

import os

import requests
from dotenv import load_dotenv

# نقرأ متغيّرات البيئة من ملف .env (يحتوي FMP_API_KEY)
load_dotenv()

FMP_API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/stable"

# مهلة الاتصال بالثواني — حتى لا يعلّق البرنامج لو الخادم بطيء
TIMEOUT = 8

# حدّ باقة FMP المجانية اليومي (عدد الطلبات) — لعرضه في لوحة «صحة المنصة»
DAILY_LIMIT = 250


def _usage_key(day=None):
    """مفتاح عدّاد طلبات اليوم في جدول AppSetting (يوم UTC — نفس توقيت تصفير حصّة FMP)."""
    from datetime import datetime, timezone
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return "fmp_calls:" + day


def _record_call():
    """يزيد عدّاد طلبات FMP لليوم الحالي بواحد — تشخيصي فقط للوحة صحة المنصة.

    - يستخدم جلسة قاعدة بيانات منفصلة حتى لا يتداخل مع معاملة المتصل (لا يلتزم بياناته).
    - أي فشل (لا سياق تطبيق، لا قاعدة بيانات...) يُتجاهل بصمت: العدّاد مساعد
      ويجب ألّا يؤثر إطلاقاً على جلب البيانات الحقيقي.
    """
    try:
        from sqlalchemy.orm import Session
        from models import db, AppSetting
        with Session(db.engine) as s:
            row = s.get(AppSetting, _usage_key())
            if row is None:
                s.add(AppSetting(key=_usage_key(), value="1"))
            else:
                try:
                    row.value = str(int(row.value) + 1)
                except (TypeError, ValueError):
                    row.value = "1"
            s.commit()
    except Exception:
        pass


def get_today_usage():
    """عدد طلبات FMP المُنفَّذة اليوم (UTC)، أو None لو تعذّرت القراءة."""
    try:
        from sqlalchemy.orm import Session
        from models import db, AppSetting
        with Session(db.engine) as s:
            row = s.get(AppSetting, _usage_key())
            return int(row.value) if row and row.value else 0
    except Exception:
        return None


def _get(endpoint, params=None, api_key=None):
    """دالة مساعدة: تنفّذ طلب GET لنقطة نهاية FMP وتُرجع JSON أو None عند الفشل.

    - api_key: مفتاح مخصّص (مثل مفتاح مشترك). None = مفتاح المنصة (FMP_API_KEY).
    - ترجع None (لا تخترع بيانات) عند أي خطأ، وتطبع سبب الخطأ في الترمنال.
    """
    key = api_key or FMP_API_KEY
    if not key:
        print("[FMP] خطأ: لا مفتاح FMP متاح (لا مخصّص ولا FMP_API_KEY)")
        return None

    params = dict(params or {})
    params["apikey"] = key
    url = f"{BASE_URL}/{endpoint}"

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[FMP] فشل الاتصال بـ {endpoint}: {e}")
        return None

    # نحسب الطلب على عدّاد استهلاك المنصة فقط لو استُخدم مفتاح المنصة.
    # طلبات المشتركين بمفاتيحهم الخاصة تُحسب على حصصهم هم، لا على أحمد.
    if not api_key:
        _record_call()

    if resp.status_code != 200:
        print(f"[FMP] {endpoint} رجّع حالة {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        return resp.json()
    except ValueError:
        print(f"[FMP] {endpoint} رجّع رداً ليس JSON: {resp.text[:200]}")
        return None


def get_quote(ticker, api_key=None):
    """السعر اللحظي والتغيّر اليومي للسهم.

    endpoint: /stable/quote?symbol=AAPL  → يُرجع قائمة فيها عنصر واحد (dict).
    api_key: مفتاح مخصّص (مثل مفتاح مشترك). None = مفتاح المنصة.

    وحدات الحقول المُرجعة (مهم — Unit Guard):
    - price           : السعر بالدولار (رقم عادي، مثال 195.12)
    - change          : تغيّر السعر بالدولار منذ الإغلاق السابق
    - changePercentage: التغيّر كـ نسبة مئوية جاهزة (مثال 1.25 تعني 1.25%) — ليست كسراً

    يُرجع dict مبسّط، وأي حقل غير موجود يكون None (وليس 0).
    """
    data = _get("quote", {"symbol": ticker}, api_key=api_key)
    if not data:  # None أو قائمة فارغة = لا بيانات
        return None

    # FMP يُرجع قائمة؛ نأخذ أول عنصر
    row = data[0] if isinstance(data, list) and data else None
    if not row:
        return None

    return {
        "ticker": row.get("symbol"),
        "name": row.get("name"),
        "price": row.get("price"),                      # دولار
        "change": row.get("change"),                    # دولار
        "change_percent": row.get("changePercentage"),  # نسبة مئوية جاهزة (1.25 = 1.25%)
        "market_cap": row.get("marketCap"),             # دولار
    }


def get_profile(ticker):
    """الملف التعريفي للشركة (الاسم، القطاع، القيمة السوقية...).

    endpoint: /stable/profile?symbol=AAPL → قائمة فيها عنصر واحد (dict).

    وحدات الحقول:
    - price     : السعر بالدولار
    - mktCap    : القيمة السوقية بالدولار
    - sector    : اسم القطاع (نص)
    - industry  : الصناعة (نص)
    """
    data = _get("profile", {"symbol": ticker})
    if not data:
        return None

    row = data[0] if isinstance(data, list) and data else None
    if not row:
        return None

    return {
        "ticker": row.get("symbol"),
        "name": row.get("companyName"),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "price": row.get("price"),       # دولار
        "market_cap": row.get("marketCap"),  # دولار
    }


def get_statement(ticker, statement, years=2):
    """يجلب قائمة مالية سنوية (annual) لآخر `years` سنوات.

    statement أحد القيم:
    - "income-statement"        : قائمة الدخل
    - "balance-sheet-statement" : الميزانية العمومية
    - "cash-flow-statement"     : التدفق النقدي

    يُرجع قائمة (list) من القواميس، الأحدث أولاً (index 0 = آخر سنة).
    أي حقل غير موجود لاحقاً نقرأه بـ .get() فيرجع None (تطبيقاً لـ None ≠ 0).
    يُرجع None لو فشل الجلب كلياً.
    """
    data = _get(statement, {"symbol": ticker, "period": "annual", "limit": years})
    if not isinstance(data, list) or not data:
        return None
    return data


def get_historical_prices(ticker, limit=60):
    """يجلب أسعار يومية تاريخية (OHLC) لحساب ATR.

    endpoint: /stable/historical-price-eod/full?symbol=AAPL
    يُرجع قائمة الأيام (الأحدث أولاً) كل عنصر فيه open/high/low/close، أو None.
    نقتصر على آخر `limit` يوم (يكفي ATR الذي يحتاج ~14 يوماً).
    """
    data = _get("historical-price-eod/full", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        return None
    return data[:limit]


def get_earnings_calendar(from_date, to_date):
    """تقويم الأرباح القادمة لكل الأسهم ضمن نطاق تواريخ — طلب FMP واحد فقط.

    endpoint: /stable/earnings-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD
    يُرجع قائمة عناصر فيها symbol و date (نص YYYY-MM-DD)، أو None عند الفشل.
    نستعمله لتنبيه المستخدم قبل موعد إعلان الأرباح (أعلى أوقات التذبذب خطراً).
    """
    data = _get("earnings-calendar", {"from": from_date, "to": to_date})
    if not isinstance(data, list) or not data:
        return None
    return data


def get_shares_float_all():
    """الأسهم الحرة (Free Float) لكل الأسهم — طلب FMP واحد فقط (bulk).

    endpoint: /stable/shares-float-all
    كل عنصر فيه: symbol, floatShares (عدد الأسهم الحرة), freeFloat (نسبة مئوية),
    outstandingShares (إجمالي الأسهم). يُرجع قائمة أو None عند الفشل.
    الأسهم الحرة = المتاحة فعلاً للتداول (تُستبعد حصص المؤسسين/الإدارة المحجوزة).
    """
    data = _get("shares-float-all")
    if not isinstance(data, list) or not data:
        return None
    return data


def get_financials(ticker, years=2):
    """يجمع القوائم الثلاث في قاموس واحد جاهز لحساب Piotroski.

    يُرجع dict فيه: income / balance / cashflow (كل واحدة قائمة سنوات أو None).
    لا نخترع بيانات: لو قائمة ما رجعت، قيمتها None.
    """
    return {
        "income": get_statement(ticker, "income-statement", years),
        "balance": get_statement(ticker, "balance-sheet-statement", years),
        "cashflow": get_statement(ticker, "cash-flow-statement", years),
    }


# ----------------------------------------------------------------------------
# اختبار يدوي في الترمنال: py services/fmp_client.py
# الهدف: نتأكد أن المفتاح يعمل وأن البيانات حقيقية قبل بناء أي شيء فوقها.
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    def show(value):
        """يعرض None كـ '—' تطبيقاً لمبدأ None ≠ 0."""
        return "—" if value is None else value

    test_ticker = "AAPL"
    print(f"=== اختبار جلب بيانات {test_ticker} من FMP ===\n")

    print("[1] السعر اللحظي (get_quote):")
    quote = get_quote(test_ticker)
    if quote:
        print(f"    الشركة      : {show(quote['name'])}")
        print(f"    السعر       : {show(quote['price'])} دولار")
        print(f"    التغيّر      : {show(quote['change'])} دولار")
        print(f"    التغيّر %    : {show(quote['change_percent'])}%  (نسبة جاهزة)")
        print(f"    القيمة السوقية: {show(quote['market_cap'])} دولار")
    else:
        print("    لا توجد بيانات (تحقّق من المفتاح أو الاتصال).")

    print("\n[2] الملف التعريفي (get_profile):")
    profile = get_profile(test_ticker)
    if profile:
        print(f"    الاسم   : {show(profile['name'])}")
        print(f"    القطاع  : {show(profile['sector'])}")
        print(f"    الصناعة : {show(profile['industry'])}")
        print(f"    السعر   : {show(profile['price'])} دولار")
    else:
        print("    لا توجد بيانات.")
