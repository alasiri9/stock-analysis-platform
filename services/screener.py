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
import time
from datetime import datetime, timezone

from models import db, StockCache, Signal
from services import fmp_client
from services import scoring
from services import indicators

# عتبات توليد الإشارات (تعليمية، لا توصية)
PIOTROSKI_SIGNAL_MIN = 8   # جودة مالية قوية
CATALYST_SIGNAL_MIN = 80   # زخم نمو قوي

# قائمة أسهم مختارة للماسح (موسّعة، لكن محدودة لحماية حدود الـ API المجانية)
# ملاحظة: كل سهم يستهلك عدة استدعاءات عند التحديث؛ زيادة العدد كثيراً قد تتجاوز الباقة.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    "TSLA", "AMD", "NFLX", "JPM", "V", "WMT",
    "AVGO", "ORCL", "CRM", "ADBE", "COST", "HD",
    "KO", "PEP", "DIS", "INTC", "QCOM", "PYPL",
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

    # financials قاموس {income, balance, cashflow}؛ قد تكون كلها None عند انتهاء حد الـ API.
    # لا نخزّن سجلّاً فارغاً (None ≠ 0): نتخطّى السهم لو لا سعر ولا أي قائمة مالية حقيقية.
    has_financials = bool(financials and any(financials.values()))
    if not quote and not has_financials:
        return None

    catalyst = scoring.catalyst_score(financials)

    # مؤشرات فنية للكرت (جلب تاريخي إضافي؛ لا يكسر السجلّ لو فشل)
    try:
        candles = fmp_client.get_historical_prices(ticker, limit=120)
        tech = indicators.build_indicators(candles)
    except Exception:  # noqa: BLE001
        tech = []

    return {
        "ticker": ticker,
        "name": (quote.get("name") if quote else None) or (profile.get("name") if profile else None),
        "sector": profile.get("sector") if profile else None,
        "price": quote.get("price") if quote else None,
        "market_cap": quote.get("market_cap") if quote else None,
        "piotroski": scoring.piotroski_score(financials)["score"],
        "catalyst": catalyst["score"],
        "indicators": tech,
    }


def _record_signal(ticker, signal_type, price):
    """يسجّل إشارة في جدول signals مرة واحدة كل يوم لكل (سهم، نوع).

    None ≠ 0 : price قد يكون None (سعر غير متوفّر) ويُخزّن كذلك دون تلفيق.
    نستخدم مقارنة بداية اليوم (UTC) بدل دوال SQL خاصة بقاعدة بيانات معيّنة.
    """
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    exists = (
        Signal.query
        .filter(Signal.ticker == ticker, Signal.signal_type == signal_type,
                Signal.triggered_at >= start_of_day)
        .first()
    )
    if exists:
        return
    db.session.add(Signal(ticker=ticker, signal_type=signal_type, price_at_signal=price))


def refresh_cache(time_budget=20):
    """يحدّث كاش الماسح على دفعات ضمن حدّ زمني آمن، ويولّد إشارات للأسهم القوية.

    - حدّ زمني (time_budget ثانية) يضمن رجوع الطلب قبل مهلة الخادم (فلا خطأ 500).
    - يتخطّى الأسهم المحدّثة اليوم بالفعل → الضغطات المتتالية تُكمل الباقي بلا إعادة.
    - يُحفظ كل سهم على حدة، وفشل سهم لا يُسقط الباقي.
    يُرجع عدد الأسهم المحدّثة في هذه الدفعة.
    """
    start = time.monotonic()
    today = datetime.now(timezone.utc).date()
    updated = 0
    for ticker in UNIVERSE:
        if time.monotonic() - start > time_budget:
            break  # انتهى الحدّ الزمني — نرجع، وضغطة أخرى تُكمل الباقي

        key = _PREFIX + ticker
        existing = db.session.get(StockCache, key)
        # تخطّي ما حُدّث اليوم (إكمال على دفعات دون إعادة استهلاك الـ API)
        if existing and existing.updated_at and existing.updated_at.date() == today:
            continue

        try:
            record = _build_record(ticker)
            if not record:
                continue

            row = existing
            payload = json.dumps(record, ensure_ascii=False)
            now = datetime.now(timezone.utc)
            if row:
                row.data_json = payload
                row.updated_at = now
            else:
                db.session.add(StockCache(ticker=key, data_json=payload, updated_at=now))

            # توليد إشارات تعليمية عند تجاوز العتبات
            if record.get("piotroski") is not None and record["piotroski"] >= PIOTROSKI_SIGNAL_MIN:
                _record_signal(ticker, "piotroski_strong", record.get("price"))
            if record.get("catalyst") is not None and record["catalyst"] >= CATALYST_SIGNAL_MIN:
                _record_signal(ticker, "catalyst_strong", record.get("price"))

            db.session.commit()  # حفظ هذا السهم مباشرة
            updated += 1
        except Exception as e:  # noqa: BLE001 — سهم واحد لا يجب أن يُسقط كل التحديث
            print(f"[screener] تعذّر تحديث {ticker}: {e}")
            db.session.rollback()
            continue

    return updated


def recent_signals(limit=12):
    """يُرجع أحدث الإشارات (الأحدث أولاً) للعرض في الواجهة."""
    return Signal.query.order_by(Signal.triggered_at.desc()).limit(limit).all()


def launched_stocks(limit=6):
    """الأسهم التي صدرت لها إشارة + عائدها منذ الإشارة (لوحة "انطلقت بالفعل").

    العائد = (السعر الحالي المخزّن − السعر وقت الإشارة) / السعر وقت الإشارة.
    None ≠ 0 : يُحسب العائد فقط لو توفّر السعران. لا استدعاءات API (نقرأ من الكاش).
    يُرجع (قائمة، إحصائيات الأداء).
    """
    # نجيب دفعة أكبر من الإشارات لأن السهم الواحد قد يُسجَّل له أكثر من إشارة (Piotroski + Catalyst)،
    # ثم نُبقي أحدث إشارة فقط لكل سهم حتى تظهر لوحة "انطلقت" بأسهم متنوّعة لا مكرّرة.
    sigs = Signal.query.order_by(Signal.triggered_at.desc()).limit(limit * 4).all()
    records, _ = load_records()
    price_by_ticker = {r["ticker"]: r.get("price") for r in records}

    rows = []
    returns = []
    seen = set()
    for s in sigs:
        if s.ticker in seen:
            continue
        seen.add(s.ticker)
        current = price_by_ticker.get(s.ticker)
        ret = None
        if current is not None and s.price_at_signal:
            ret = (current - s.price_at_signal) / s.price_at_signal * 100.0
            returns.append(ret)
        rows.append({
            "ticker": s.ticker,
            "signal_type": s.signal_type,
            "price_at_signal": s.price_at_signal,
            "current": current,
            "return_pct": ret,
            "date": s.triggered_at,
        })
        if len(rows) >= limit:
            break

    # إحصائيات الأداء العام (من الإشارات القابلة للحساب فقط)
    stats = {"avg": None, "win_rate": None, "best": None, "count": len(returns)}
    if returns:
        stats["avg"] = sum(returns) / len(returns)
        stats["win_rate"] = sum(1 for r in returns if r > 0) / len(returns) * 100.0
        stats["best"] = max(returns)
    return rows, stats


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
                   price_max=None, market_cap_min=None, sector=None):
    """يطبّق الفلاتر على السجلّات المخزّنة.

    None ≠ 0 : السجلّ الذي تكون قيمته المطلوبة None لا يجتاز فلتراً يحدّ تلك القيمة
    (لا نعامله كصفر).
    market_cap_min بالدولار (خام)، يُحوّل من المليارات في طبقة الـ route.
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
        if market_cap_min is not None:
            if r.get("market_cap") is None or r["market_cap"] < market_cap_min:
                continue
        if sector:
            if r.get("sector") != sector:
                continue
        out.append(r)
    # ترتيب تنازلي حسب Catalyst ثم Piotroski (None في الأسفل)
    out.sort(key=lambda r: (r.get("catalyst") or -1, r.get("piotroski") or -1), reverse=True)
    return out
