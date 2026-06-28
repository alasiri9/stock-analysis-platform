"""
finnhub_client.py — عميل Finnhub: سعر لحظي (مصدر تأكيد ثانٍ للسعر).

ملاحظة عن الباقة المجانية:
- endpoint /quote متاح (سعر لحظي) ونستخدمه.
- endpoint /stock/candle (الشموع التاريخية) محجوب في الباقة المجانية (403)،
  لذلك نحسب ATR من بيانات FMP التاريخية بدلاً منه (انظر analysis.py).

Unit Guard مهم:
- الحقل dp (تغيّر %) يأتي كنسبة مئوية جاهزة (3.14 تعني 3.14%) — ليس كسراً.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
BASE_URL = "https://finnhub.io/api/v1"
TIMEOUT = 15


def get_quote(ticker):
    """سعر لحظي من Finnhub. يُرجع dict مبسّط أو None عند الفشل/غياب المفتاح.

    حقول Finnhub: c=السعر الحالي، d=التغيّر($)، dp=التغيّر(%)، pc=الإغلاق السابق.
    None ≠ 0 : لو السعر صفر (رمز غير صالح) نعتبره غياب بيانات ونُرجع None.
    """
    if not FINNHUB_API_KEY:
        print("[Finnhub] لا يوجد FINNHUB_API_KEY في .env")
        return None

    try:
        resp = requests.get(
            f"{BASE_URL}/quote",
            params={"symbol": ticker, "token": FINNHUB_API_KEY},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"[Finnhub] فشل الاتصال: {e}")
        return None

    if resp.status_code != 200:
        print(f"[Finnhub] حالة {resp.status_code}: {resp.text[:150]}")
        return None

    data = resp.json()
    price = data.get("c")
    # Finnhub يُرجع c=0 للرموز غير الصالحة → نعتبره لا بيانات (None ≠ 0)
    if not price:
        return None

    return {
        "price": price,                  # دولار
        "change": data.get("d"),         # دولار
        "change_percent": data.get("dp"),# نسبة مئوية جاهزة (3.14 = 3.14%)
        "prev_close": data.get("pc"),    # دولار
    }


if __name__ == "__main__":
    print("=== اختبار Finnhub: سعر AAPL ===")
    q = get_quote("AAPL")
    if q:
        print(f"السعر: {q['price']} $ | التغيّر: {q['change']} $ ({q['change_percent']}%)")
    else:
        print("لا توجد بيانات.")
