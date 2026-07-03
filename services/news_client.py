"""
news_client.py — أخبار السوق الأمريكي من Finnhub (الباقة المجانية).

endpoint: /news?category=general — يُرجع ~100 خبر عام (إنجليزية).
حصة Finnhub المجانية سخية (60 طلباً/دقيقة) لكن لا داعي للإسراف:
كاش بالذاكرة لمدة 10 دقائق — فتح الصفحة المتكرر لا يستهلك طلبات جديدة.

المبادئ: None ≠ 0، لا نخترع بيانات — لو فشل الجلب نُرجع آخر كاش صالح أو [].
"""

import os
import time

import requests

TIMEOUT = 10
CACHE_TTL = 600  # ثوانٍ (10 دقائق)

# كاش بالذاكرة: (وقت الجلب، القائمة)
_cache = {"ts": 0.0, "items": []}


def _base_url():
    return "https://finnhub.io/api/v1"


def _api_key():
    return os.getenv("FINNHUB_API_KEY")


def get_market_news(limit=40):
    """يُرجع أحدث أخبار السوق العامة (من الكاش لو عمره أقل من 10 دقائق).

    كل خبر dict فيه: headline, summary, source, url, image, datetime (unix seconds).
    يُرجع [] لو الجلب فشل ولا يوجد كاش سابق.
    """
    now = time.time()
    if _cache["items"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["items"][:limit]

    key = _api_key()
    if not key:
        print("[news] FINNHUB_API_KEY غير مضبوط")
        return _cache["items"][:limit]

    try:
        resp = requests.get(
            f"{_base_url()}/news",
            params={"category": "general", "token": key},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[news] فشل الجلب ({resp.status_code}): {resp.text[:150]}")
            return _cache["items"][:limit]
        items = resp.json()
        if not isinstance(items, list):
            return _cache["items"][:limit]
    except (requests.RequestException, ValueError) as e:
        print(f"[news] تعذّر الجلب: {e}")
        return _cache["items"][:limit]

    # ننظّف: نستبعد ما بلا عنوان أو رابط (لا نعرض عناصر مكسورة)
    cleaned = [
        {
            "headline": it.get("headline"),
            "summary": it.get("summary") or "",
            "source": it.get("source") or "",
            "url": it.get("url"),
            "image": it.get("image") or None,
            "datetime": it.get("datetime"),
        }
        for it in items
        if it.get("headline") and it.get("url")
    ]
    _cache["ts"] = now
    _cache["items"] = cleaned
    return cleaned[:limit]
