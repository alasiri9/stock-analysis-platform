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
from datetime import datetime, timezone, date as date_cls

from models import db, StockCache, Signal, PricePoint
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
        _save_price_history(ticker, candles)  # نفس البيانات المجلوبة أصلاً — بلا استدعاء API إضافي
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


def _save_price_history(ticker, candles, days=60):
    """يحفظ إغلاقات آخر `days` يوماً في price_point — بيانات حقيقية من FMP مجلوبة أصلاً للمؤشرات.

    upsert عبر db.session.merge (المفتاح مركّب ticker+date)؛ يبني مسار سعري حقيقي
    يكبر كل يوم تحديث، ويُستخدم لاحقاً لرسم مصغّر (sparkline) بلا أي استدعاء API إضافي.
    """
    for c in (candles or [])[:days]:
        raw_date = c.get("date")
        close = c.get("close")
        if not raw_date or close is None:
            continue
        try:
            day = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        db.session.merge(PricePoint(ticker=ticker, date=day, price=close))


def get_price_series(ticker, limit=60):
    """يُرجع أسعار إغلاق السهم مرتّبة من الأقدم للأحدث (للرسم المصغّر)."""
    rows = (
        PricePoint.query
        .filter_by(ticker=ticker)
        .order_by(PricePoint.date.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [r.price for r in rows if r.price is not None]


def _sparkline_points(prices, width=100, height=32, pad=3):
    """يبني نقاط SVG polyline لمسار سعري (رسم مصغّر). يُرجع '' لو البيانات غير كافية."""
    if not prices or len(prices) < 2:
        return ""
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1.0
    n = len(prices)
    step = (width - 2 * pad) / (n - 1)
    points = []
    for i, p in enumerate(prices):
        x = pad + i * step
        y = pad + (height - 2 * pad) * (1 - (p - lo) / span)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def backfill_price_history(time_budget=20):
    """يملأ price_point للأسهم المخزّنة مسبقاً بدون إعادة بناء سجلّها كاملاً.

    استدعاء واحد فقط لكل سهم (historical-price-eod) بدل 5-6 استدعاءات (quote/profile/financials)
    التي يحتاجها refresh_cache — يوفّر حصة الـ API عندما يكون الهدف فقط تعبئة الرسم البياني
    لأسهم بياناتها الأساسية محدّثة أصلاً لكن رسمها لسا فاضٍ (مثلاً بعد إضافة الميزة حديثاً).
    يتخطّى أي سهم له نقطة سعر مسجّلة اليوم بالفعل. يُرجع عدد الأسهم المحدّثة.
    """
    start = time.monotonic()
    today = date_cls.today()
    records, _ = load_records()
    updated = 0
    for r in records:
        if time.monotonic() - start > time_budget:
            break

        ticker = r["ticker"]
        has_today = PricePoint.query.filter_by(ticker=ticker, date=today).first()
        if has_today:
            continue

        try:
            candles = fmp_client.get_historical_prices(ticker, limit=120)
            if not candles:
                continue
            _save_price_history(ticker, candles)
            db.session.commit()
            updated += 1
        except Exception as e:  # noqa: BLE001
            print(f"[screener] تعذّر تحديث تاريخ الأسعار لـ {ticker}: {e}")
            db.session.rollback()
            continue

    return updated


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
    name_by_ticker = {r["ticker"]: r.get("name") for r in records}

    now = datetime.now(timezone.utc)
    rows = []
    returns = []
    days_list = []
    seen = set()
    for s in sigs:
        if s.ticker in seen:
            continue
        seen.add(s.ticker)
        current = price_by_ticker.get(s.ticker)
        ret = None
        if current is not None and s.price_at_signal:
            ret = (current - s.price_at_signal) / s.price_at_signal * 100.0
            returns.append((s.ticker, ret))
        triggered_at = s.triggered_at
        if triggered_at.tzinfo is None:  # SQLite محلياً لا يحفظ tzinfo رغم DateTime(timezone=True)
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        days_elapsed = (now - triggered_at).days
        days_list.append(days_elapsed)
        prices = get_price_series(s.ticker)
        rows.append({
            "ticker": s.ticker,
            "name": name_by_ticker.get(s.ticker),
            "signal_type": s.signal_type,
            "price_at_signal": s.price_at_signal,
            "current": current,
            "return_pct": ret,
            "date": s.triggered_at,
            "days_elapsed": days_elapsed,
            "spark_points": _sparkline_points(prices),
            "spark_up": (prices[-1] >= prices[0]) if len(prices) >= 2 else None,
            "spark_days": len(prices),
        })
        if len(rows) >= limit:
            break

    # إحصائيات الأداء العام (من الإشارات القابلة للحساب فقط)
    stats = {
        "avg": None, "win_rate": None, "best": None, "best_ticker": None,
        "avg_days": None, "count": len(returns), "win_count": 0,
    }
    if returns:
        values = [r for _, r in returns]
        stats["avg"] = sum(values) / len(values)
        stats["win_count"] = sum(1 for r in values if r > 0)
        stats["win_rate"] = stats["win_count"] / len(values) * 100.0
        best_ticker, best_ret = max(returns, key=lambda x: x[1])
        stats["best"] = best_ret
        stats["best_ticker"] = best_ticker
    if days_list:
        stats["avg_days"] = sum(days_list) / len(days_list)
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
