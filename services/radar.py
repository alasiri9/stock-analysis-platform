"""
radar.py — رادار المحفزات: معاملات المطلعين لأسهم الماسح (من SEC EDGAR).

الفكرة (نفس نمط screener):
- نجلب معاملات المطلعين لكل سهم في UNIVERSE ونخزّنها في stock_cache
  بمفتاح "radar:TICKER" (EDGAR مجاني بلا حصص لكنه بطيء — لذلك كاش + دفعات).
- الصفحة تقرأ الكاش فقط (فورية)، والتحديث يدوي بزر أو تلقائي ليلاً.
- أهم إشارة: شراء المطلعين من السوق المفتوح (code=P) — تُبرز في قسم مستقل.
"""

import json
import time
from datetime import datetime, timezone

from models import db, StockCache
from services import edgar_client
from services.screener import UNIVERSE

_PREFIX = "radar:"


def refresh_radar(time_budget=60):
    """يحدّث كاش الرادار على دفعات (يتخطى المحدَّث اليوم). يُرجع عدد الأسهم المحدّثة."""
    start = time.monotonic()
    today = datetime.now(timezone.utc).date()
    updated = 0
    for ticker in UNIVERSE:
        if time.monotonic() - start > time_budget:
            break

        key = _PREFIX + ticker
        existing = db.session.get(StockCache, key)
        if existing and existing.updated_at and existing.updated_at.date() == today:
            continue

        try:
            rows = edgar_client.get_insider_transactions(ticker, max_filings=6, max_rows=10)
            payload = json.dumps(rows, ensure_ascii=False)
            now = datetime.now(timezone.utc)
            if existing:
                existing.data_json = payload
                existing.updated_at = now
            else:
                db.session.add(StockCache(ticker=key, data_json=payload, updated_at=now))
            db.session.commit()
            updated += 1
        except Exception as e:  # noqa: BLE001 — سهم واحد لا يُسقط التحديث
            print(f"[radar] تعذّر تحديث {ticker}: {e}")
            db.session.rollback()
            continue

    return updated


def load_radar():
    """يقرأ كاش الرادار. يُرجع (قائمة معاملات موحّدة مرتبة بالأحدث، مشتريات السوق المفتوح، آخر تحديث)."""
    rows_db = StockCache.query.filter(StockCache.ticker.like(_PREFIX + "%")).all()
    all_tx = []
    latest = None
    for row in rows_db:
        try:
            txs = json.loads(row.data_json)
        except (ValueError, TypeError):
            continue
        ticker = row.ticker[len(_PREFIX):]
        for t in txs:
            t["ticker"] = ticker
            all_tx.append(t)
        if latest is None or row.updated_at > latest:
            latest = row.updated_at

    # الأحدث أولاً (التواريخ نصية YYYY-MM-DD فالترتيب النصي يكفي؛ None في الأسفل)
    all_tx.sort(key=lambda t: t.get("date") or "", reverse=True)

    # المحفّز الأهم: شراء فعلي من السوق المفتوح (code=P)
    open_buys = [t for t in all_tx if t.get("code") == "P"]

    return all_tx, open_buys, latest
