"""
screener.py — فلترة الأسهم (Screener) مع تخزين مؤقت لحماية حدود الـ API.

الفكرة:
- لدينا قائمة أسهم مختارة (UNIVERSE).
- نحسب درجاتها مرة واحدة ونخزّنها في جدول stock_cache (data_json).
- الفلترة تتم على البيانات المخزّنة (فورية، بدون استدعاءات API).
- زر "تحديث" يعيد بناء الكاش يدوياً عند الحاجة.

هذا يقلّل استهلاك الباقة المجانية (الفلترة لا تكلّف استدعاءات).
"""

import json
from datetime import datetime, timezone

from models import db, StockCache
from services import fmp_client
from services import scoring

# قائمة أسهم مختارة للماسح (نبقيها صغيرة لحماية حدود الـ API)
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    "TSLA", "AMD", "NFLX", "JPM", "V", "WMT",
]

# بادئة لتمييز سجلّات الماسح داخل stock_cache
_PREFIX = "screen:"


def _build_record(ticker):
    """يبني سجلّ ماسح لسهم: اسم، قطاع، سعر، قيمة سوقية، Piotroski، Catalyst.

    يُرجع dict أو None لو تعذّر جلب الأساسيات.
    """
    quote = fmp_client.get_quote(ticker)
    profile = fmp_client.get_profile(ticker)
    financials = fmp_client.get_financials(ticker)
    if not quote and not financials:
        return None

    catalyst = scoring.catalyst_score(financials)
    return {
        "ticker": ticker,
        "name": (quote.get("name") if quote else None) or (profile.get("name") if profile else None),
        "sector": profile.get("sector") if profile else None,
        "price": quote.get("price") if quote else None,
        "market_cap": quote.get("market_cap") if quote else None,
        "piotroski": scoring.piotroski_score(financials)["score"],
        "catalyst": catalyst["score"],
    }


def refresh_cache():
    """يعيد بناء كاش الماسح لكل أسهم UNIVERSE. يُرجع عدد الأسهم المحدّثة."""
    updated = 0
    for ticker in UNIVERSE:
        record = _build_record(ticker)
        if not record:
            continue
        key = _PREFIX + ticker
        row = db.session.get(StockCache, key)
        payload = json.dumps(record, ensure_ascii=False)
        now = datetime.now(timezone.utc)
        if row:
            row.data_json = payload
            row.updated_at = now
        else:
            db.session.add(StockCache(ticker=key, data_json=payload, updated_at=now))
        updated += 1
    db.session.commit()
    return updated


def load_records():
    """يقرأ سجلّات الماسح المخزّنة. يُرجع (قائمة السجلّات، أحدث وقت تحديث أو None)."""
    rows = StockCache.query.filter(StockCache.ticker.like(_PREFIX + "%")).all()
    records = []
    latest = None
    for row in rows:
        try:
            records.append(json.loads(row.data_json))
        except (ValueError, TypeError):
            continue
        if latest is None or row.updated_at > latest:
            latest = row.updated_at
    return records, latest


def filter_records(records, piotroski_min=None, catalyst_min=None,
                   price_max=None, sector=None):
    """يطبّق الفلاتر على السجلّات المخزّنة.

    None ≠ 0 : السجلّ الذي تكون قيمته المطلوبة None لا يجتاز فلتراً يحدّ تلك القيمة
    (لا نعامله كصفر).
    """
    out = []
    for r in records:
        if piotroski_min is not None:
            if r.get("piotroski") is None or r["piotroski"] < piotroski_min:
                continue
        if catalyst_min is not None:
            if r.get("catalyst") is None or r["catalyst"] < catalyst_min:
                continue
        if price_max is not None:
            if r.get("price") is None or r["price"] > price_max:
                continue
        if sector:
            if r.get("sector") != sector:
                continue
        out.append(r)
    # ترتيب تنازلي حسب Catalyst ثم Piotroski (None في الأسفل)
    out.sort(key=lambda r: (r.get("catalyst") or -1, r.get("piotroski") or -1), reverse=True)
    return out
