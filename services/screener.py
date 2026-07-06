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
from datetime import datetime, timezone, timedelta, date as date_cls

from models import db, StockCache, Signal, PricePoint
from services import fmp_client
from services import scoring
from services import indicators
from services import telegram_client

# عتبات توليد الإشارات (تعليمية، لا توصية)
PIOTROSKI_SIGNAL_MIN = 8   # جودة مالية قوية
CATALYST_SIGNAL_MIN = 80   # نمو قوي

# قائمة أسهم مختارة للماسح (موسّعة، لكن محدودة لحماية حدود الـ API المجانية)
# الحساب: كل سهم = 6 استدعاءات عند التحديث الكامل؛ 32 سهماً = 192 من حد 250/يوم (هامش ~58).
# كل الرموز هنا مُختبرة ومتاحة بباقة FMP المجانية (رموز كثيرة أخرى محجوبة عنها بخطأ 402).
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    "TSLA", "AMD", "NFLX", "JPM", "V", "WMT",
    "UNH", "JNJ", "XOM", "ADBE", "COST", "CVX",
    "KO", "PEP", "DIS", "INTC", "BAC", "PYPL",
    "ABBV", "PFE", "CSCO", "VZ", "T", "WFC",
    "GS", "NKE",
]

# بادئة لتمييز سجلّات الماسح داخل stock_cache
_PREFIX = "screen:"

# عدد جلسات التداول لقياس الزخم/القوة النسبية (~3 أشهر)
MOMENTUM_SESSIONS = 63

# نافذة "الصعود الأخير" (~أسبوعان) — أساس فلتر "لسا ما صعد"
RECENT_SESSIONS = 10
# لسا ما صعد: نستبعد من قائمة "قبل الانطلاق" ما قفز أكثر من هذا خلال النافذة الأخيرة
EARLY_MAX_RECENT_GAIN = 12.0


def _bullish_strategies(record):
    """يُرجع قائمة أسماء الاستراتيجيات/المؤشرات الصاعدة المتحققة للسهم (تأكيد متعدد).

    كلما طال هذا العدد، زاد تضافر الأدلة على أن السهم مرشّح قوي للانطلاق.
    """
    inds = {b.get("label"): b for b in (record.get("indicators") or [])}

    def is_bull(label):
        b = inds.get(label)
        return bool(b and b.get("status") == "bull")

    checks = {
        "الاتجاه صاعد (EMA)": is_bull("EMA"),
        "زخم إيجابي (MACD)": is_bull("MACD"),
        "قوة نسبية صحية (RSI)": is_bull("RSI"),
        "اتجاه قوي (ADX)": is_bull("ADX"),
        "اختراق قمة": is_bull("اختراق"),
        "حجم مرتفع": is_bull("الحجم"),
        "سوبرترند صاعد": is_bull("سوبرترند"),
        "تقاطع صاعد": is_bull("تقاطع"),
        "تراكم حجم (OBV)": is_bull("تراكم"),
        "سيولة داخلة": (record.get("money_flow") or {}).get("status") == "bull",
        "جودة مالية (Piotroski)": record.get("piotroski") is not None and record["piotroski"] >= 7,
        "أقوى من السوق": record.get("rel_strength") is not None and record["rel_strength"] > 0,
    }
    return [name for name, ok in checks.items() if ok]


def measures_met(record):
    """عدد المقاييس الإيجابية المجتمعة للسهم (تضافر الأدلة الصاعدة).

    يعدّ: كل شارة فنية حالتها صاعدة (حتى 12) + سيولة داخلة + أقوى من السوق
    + جودة مالية عالية (Piotroski≥8) + نمو قوي (Catalyst≥80). الأقصى ~16.
    كلما زاد العدد، زاد تضافر المقاييس الإيجابية على السهم.
    """
    count = sum(1 for b in (record.get("indicators") or []) if b.get("status") == "bull")
    if (record.get("money_flow") or {}).get("status") == "bull":
        count += 1
    if record.get("rel_strength") is not None and record["rel_strength"] > 0:
        count += 1
    if record.get("piotroski") is not None and record["piotroski"] >= 8:
        count += 1
    if record.get("catalyst") is not None and record["catalyst"] >= 80:
        count += 1
    return count


def early_launch_candidates(records=None, min_strategies=3):
    """مرشّحو "قبل الانطلاق": أسهم في مرحلة مبكرة ولم تصعد بعد، مرتّبة بقوة التأكيد.

    المرحلة المبكرة = أحدها:
    - قيد الشحن: انضغاط بولينجر قائم (تذبذب ضيّق يسبق الحركة) دون اختراق بعد.
    - بداية الاختراق: أول اختراق قمة 20 يوماً (الحركة للتو بدأت).
    الفلتر "لسا ما صعد": يُستبعد ما قفز أكثر من EARLY_MAX_RECENT_GAIN خلال آخر أسبوعين
    (None = العائد غير محسوب بعد، فلا نستبعد — سيُحسب مع أول تحديث ليلي).
    الترتيب حسب عدد الاستراتيجيات الصاعدة المتحققة (min_strategies حدّ أدنى).
    يُرجع قائمة مرتّبة تنازلياً (بلا استدعاء API — من الكاش).
    """
    if records is None:
        records, _ = load_records()
    out = []
    for r in records:
        badges = r.get("indicators") or []
        squeezed = any(b.get("label") == "انضغاط" and b.get("value") == "نعم" for b in badges)
        breakout = any(b.get("label") == "اختراق" and b.get("status") == "bull" for b in badges)
        if not (squeezed or breakout):
            continue  # ليس في مرحلة مبكرة

        rg = r.get("recent_gain")
        if rg is not None and rg > EARLY_MAX_RECENT_GAIN:
            continue  # صعد كثيراً بالفعل — فات وقت الدخول المبكر

        strategies = _bullish_strategies(r)
        if len(strategies) < min_strategies:
            continue  # تأكيد ضعيف

        # المرحلة: قيد الشحن (انضغاط بلا اختراق) أقدم من بداية الاختراق
        if squeezed and not breakout:
            stage, stage_key = "🔋 قيد الشحن", "coiling"
        elif breakout:
            stage, stage_key = "🚀 بداية الاختراق", "breakout"
        else:
            stage, stage_key = "🔋 قيد الشحن", "coiling"

        out.append({
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "price": r.get("price"),
            "catalyst": r.get("catalyst"),
            "piotroski": r.get("piotroski"),
            "recent_gain": rg,
            "stage": stage,
            "stage_key": stage_key,
            "strategies": strategies,
            "strength": len(strategies),
        })
    # الأقوى تأكيداً أولاً، ثم الأقل صعوداً (الأطزج)، ثم قيد الشحن قبل الاختراق
    out.sort(key=lambda x: (
        -x["strength"],
        x["recent_gain"] if x["recent_gain"] is not None else 0,
        0 if x["stage_key"] == "coiling" else 1,
    ))
    return out

# ننبّه المستخدم لو موعد إعلان أرباح السهم خلال هذه المدة (أعلى أوقات التذبذب خطراً)
EARNINGS_LOOKAHEAD_DAYS = 21


def _shares_float_map():
    """خريطة {رمز: {float_shares, free_float_pct}} لأسهم UNIVERSE — طلب FMP واحد (bulk).

    يُرجع {} عند أي فشل (الميزة كماليّة ولا يجوز أن تُسقط التحديث).
    """
    try:
        data = fmp_client.get_shares_float_all()
    except Exception:  # noqa: BLE001
        return {}
    if not data:
        return {}
    universe = set(UNIVERSE)
    out = {}
    for row in data:
        sym = row.get("symbol")
        if sym not in universe:
            continue
        out[sym] = {
            "float_shares": row.get("floatShares"),
            "free_float_pct": row.get("freeFloat"),
        }
    return out


def _upcoming_earnings():
    """خريطة {رمز: تاريخ أقرب إعلان أرباح قادم} لأسهم UNIVERSE — طلب FMP واحد فقط.

    يُرجع {} عند أي فشل (التنبيه كماليّ ولا يجوز أن يُسقط التحديث).
    """
    today = datetime.now(timezone.utc).date()
    to = today + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)
    try:
        data = fmp_client.get_earnings_calendar(today.isoformat(), to.isoformat())
    except Exception:  # noqa: BLE001
        return {}
    if not data:
        return {}
    universe = set(UNIVERSE)
    out = {}
    for row in data:
        sym = row.get("symbol")
        raw = row.get("date")
        if sym not in universe or not raw:
            continue
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d < today:
            continue  # تجاهل المواعيد الماضية
        if sym not in out or d < out[sym]:
            out[sym] = d  # نُبقي الأقرب قادماً
    return out


def _period_return(closes_old_to_new, sessions):
    """عائد السهم عبر آخر `sessions` جلسة (%). closes مرتّبة من الأقدم للأحدث.

    يُرجع None لو البيانات غير كافية أو السعر القديم صفر (None ≠ 0).
    """
    if not closes_old_to_new or len(closes_old_to_new) < sessions + 1:
        return None
    old = closes_old_to_new[-(sessions + 1)]
    new = closes_old_to_new[-1]
    if not old:
        return None
    return (new - old) / old * 100.0


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
    # 250 يوماً: يكفي ADX الموثوق وقمة قريبة من قمة 52 أسبوعاً (نفس طلب FMP الواحد)
    try:
        candles = fmp_client.get_historical_prices(ticker, limit=250)
        tech = indicators.build_indicators(candles)
        flow = indicators.money_flow(candles)  # تدفق السيولة — من نفس الشموع، بلا استدعاء إضافي
        squeeze_bo = indicators.squeeze_breakout(candles)  # استراتيجية الانفجار الوشيك
        closes = [c["close"] for c in reversed(candles or []) if c.get("close") is not None]
        gc = indicators.golden_cross(closes)  # التقاطع الذهبي SMA50/SMA200
        pullback = indicators.trend_pullback(candles)  # ارتداد الترند (شراء الانخفاض)
        atr_val = indicators.atr(candles)  # تذبذب السهم — لمستويات الدخول/الوقف/الهدف بالتنبيهات
        mom_63d = _period_return(closes, MOMENTUM_SESSIONS)  # زخم ~3 أشهر (للقوة النسبية مقابل السوق)
        recent_gain = _period_return(closes, RECENT_SESSIONS)  # صعود آخر أسبوعين (لفلتر "لسا ما صعد")
        _save_price_history(ticker, candles)  # نفس البيانات المجلوبة أصلاً — بلا استدعاء API إضافي
    except Exception:  # noqa: BLE001
        tech = []
        flow = None
        squeeze_bo = False
        gc = None
        pullback = False
        atr_val = None
        mom_63d = None
        recent_gain = None

    return {
        "ticker": ticker,
        "name": (quote.get("name") if quote else None) or (profile.get("name") if profile else None),
        "sector": profile.get("sector") if profile else None,
        "price": quote.get("price") if quote else None,
        "market_cap": quote.get("market_cap") if quote else None,
        "piotroski": scoring.piotroski_score(financials)["score"],
        "catalyst": catalyst["score"],
        "indicators": tech,
        "money_flow": flow,
        "squeeze_breakout": squeeze_bo,
        "golden_cross": (gc or {}).get("cross"),
        "trend_pullback": pullback,
        "atr": atr_val,
        "mom_63d": mom_63d,
        "recent_gain": recent_gain,
    }


# نافذة منع تكرار الإشارة: لا تُسجَّل إشارة جديدة لنفس (السهم، النوع) خلال هذه المدة.
# الإشارة الأولى تبقى المرجع الذي يُقاس منه العائد — لو تكرّرت يومياً لعلِق العائد على 0%.
SIGNAL_COOLDOWN_DAYS = 30


def is_golden(record):
    """🥇 هل يستوفي السجل شروط الإشارة الذهبية الثلاثة مجتمعة؟

    1. جودة مالية عالية: Piotroski >= 8
    2. سيولة داخلة: تدفق الأموال بحالة "تجميع"
    3. اختراق فني: إغلاق فوق أعلى 20 يوماً (شارة "اختراق" الصاعدة)
    اجتماعها نادر — لذلك تُعد أقوى إشارات المنصة.
    """
    quality_ok = record.get("piotroski") is not None and record["piotroski"] >= PIOTROSKI_SIGNAL_MIN
    flow = record.get("money_flow")
    flow_ok = bool(flow and flow.get("status") == "bull")
    breakout_ok = any(
        b.get("label") == "اختراق" and b.get("status") == "bull"
        for b in (record.get("indicators") or [])
    )
    return quality_ok and flow_ok and breakout_ok


def _record_signal(ticker, signal_type, price, atr=None, earnings_days=None):
    """يسجّل إشارة لأول تأهّل فقط — لا تتجدد إلا بعد غياب SIGNAL_COOLDOWN_DAYS.

    None ≠ 0 : price قد يكون None (سعر غير متوفّر) ويُخزّن كذلك دون تلفيق.
    atr (اختياري): تذبذب السهم — يُمرَّر للتنبيه لحساب مستويات الدخول/الوقف/الهدف.
    earnings_days (اختياري): أيام لموعد الأرباح — لإضافة تحذير تذبذب في التنبيه.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_COOLDOWN_DAYS)
    exists = (
        Signal.query
        .filter(Signal.ticker == ticker, Signal.signal_type == signal_type,
                Signal.triggered_at >= cutoff)
        .first()
    )
    if exists:
        return
    db.session.add(Signal(ticker=ticker, signal_type=signal_type, price_at_signal=price))
    # تنبيه تلغرام (اختياري — خامل بلا إعداد, وفشله لا يؤثر على التحديث)
    telegram_client.notify_signal(ticker, signal_type, price, atr=atr, earnings_days=earnings_days)


def dedupe_signals():
    """تنظيف الإشارات المكررة: يُبقي الأقدم لكل (سهم، نوع) ويحذف الأحدث المكررة.

    آمنة الاستدعاء دائماً (idempotent) — تُشغَّل عند بدء التطبيق لتصحيح المكررات
    التي خلّفتها النسخة القديمة (كانت تسجّل إشارة كل يوم لنفس السهم).
    يُرجع عدد الصفوف المحذوفة.
    """
    sigs = Signal.query.order_by(Signal.triggered_at.asc()).all()
    seen = set()
    removed = 0
    for s in sigs:
        key = (s.ticker, s.signal_type)
        if key in seen:
            db.session.delete(s)
            removed += 1
        else:
            seen.add(key)
    if removed:
        db.session.commit()
        print(f"[screener] حُذفت {removed} إشارة مكررة (أُبقي الأقدم لكل سهم/نوع)")
    return removed


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


# رمز مؤشر السوق للمقارنة المعيارية (متاح بباقة FMP المجانية — مُختبر)
MARKET_BENCHMARK = "SPY"


def _benchmark_return(sessions):
    """عائد مؤشر السوق (SPY) عبر آخر `sessions` جلسة (%) من price_point.

    يُرجع None لو تاريخ المؤشر غير كافٍ (لا نقارن بلا مرجع موثوق).
    """
    rows = (
        PricePoint.query
        .filter_by(ticker=MARKET_BENCHMARK)
        .order_by(PricePoint.date.asc())
        .all()
    )
    prices = [r.price for r in rows if r.price is not None]
    return _period_return(prices, sessions)


def _refresh_spy_history(today):
    """يحفظ أسعار مؤشر السوق (SPY) اليومية في price_point — لمقارنة أداء الإشارات بالسوق.

    طلب FMP واحد يومياً فقط (يتخطى لو تحدّث اليوم). فشله لا يؤثر على تحديث الأسهم.
    """
    try:
        has_today = PricePoint.query.filter_by(ticker=MARKET_BENCHMARK, date=today).first()
        if has_today:
            return
        candles = fmp_client.get_historical_prices(MARKET_BENCHMARK, limit=120)
        if candles:
            _save_price_history(MARKET_BENCHMARK, candles, days=120)
            db.session.commit()
            print(f"[screener] حُدّث تاريخ مؤشر السوق ({MARKET_BENCHMARK})")
    except Exception as e:  # noqa: BLE001
        print(f"[screener] تعذّر تحديث مؤشر السوق: {e}")
        db.session.rollback()


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
    _refresh_spy_history(today)  # مؤشر السوق للمقارنة (طلب واحد يومياً، يتخطى لو محدث)
    spy_mom = _benchmark_return(MOMENTUM_SESSIONS)  # زخم السوق لنفس الفترة (يُحسب مرة واحدة)
    earnings_map = _upcoming_earnings()  # مواعيد الأرباح القادمة (طلب FMP واحد لكل الأسهم)
    float_map = _shares_float_map()  # الأسهم الحرة (طلب FMP واحد لكل الأسهم)
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

            # القوة النسبية = تفوّق زخم السهم على زخم السوق عن نفس الفترة (None ≠ 0)
            if record.get("mom_63d") is not None and spy_mom is not None:
                record["rel_strength"] = record["mom_63d"] - spy_mom

            # موعد الأرباح القادم (لو ضمن نافذة التنبيه) — تحذير من التذبذب المرتفع
            ed = earnings_map.get(ticker)
            if ed:
                record["earnings_date"] = ed.isoformat()
                record["days_to_earnings"] = (ed - today).days

            # الأسهم الحرة (float) — كلما قلّت زاد احتمال الحركة السريعة
            fl = float_map.get(ticker)
            if fl:
                record["float_shares"] = fl.get("float_shares")
                record["free_float_pct"] = fl.get("free_float_pct")

            row = existing
            payload = json.dumps(record, ensure_ascii=False)
            now = datetime.now(timezone.utc)
            if row:
                row.data_json = payload
                row.updated_at = now
            else:
                db.session.add(StockCache(ticker=key, data_json=payload, updated_at=now))

            # توليد إشارات تعليمية عند تجاوز العتبات
            sig_price, sig_atr = record.get("price"), record.get("atr")
            sig_earn = record.get("days_to_earnings")
            if record.get("piotroski") is not None and record["piotroski"] >= PIOTROSKI_SIGNAL_MIN:
                _record_signal(ticker, "piotroski_strong", sig_price, atr=sig_atr, earnings_days=sig_earn)
            if record.get("catalyst") is not None and record["catalyst"] >= CATALYST_SIGNAL_MIN:
                _record_signal(ticker, "catalyst_strong", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🥇 الإشارة الذهبية: 3 عوامل مجتمعة (نادرة) — جودة عالية + سيولة داخلة + اختراق فني
            if is_golden(record):
                _record_signal(ticker, "golden", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 💣 الانفجار الوشيك: انضغاط بولينجر + اختراق + حجم مرتفع
            if record.get("squeeze_breakout"):
                _record_signal(ticker, "squeeze_breakout", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🌟 التقاطع الذهبي: SMA50 قطع SMA200 صعوداً (اتجاه صاعد طويل المدى)
            if record.get("golden_cross") == "golden":
                _record_signal(ticker, "golden_cross", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🎯 ارتداد الترند: ترند صاعد + تراجع لمس EMA20 + بدء ارتداد (شراء الانخفاض)
            if record.get("trend_pullback"):
                _record_signal(ticker, "trend_pullback", sig_price, atr=sig_atr, earnings_days=sig_earn)

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


def signals_performance():
    """سجل الأداء الكامل: كل إشارة تاريخية + عائدها منذ صدورها حتى الآن.

    يُرجع (قائمة الصفوف، إحصائيات إجمالية، إحصائيات لكل نوع إشارة).
    العائد يُحسب فقط لو توفّر السعران (None ≠ 0). لا استدعاءات API (أسعار من الكاش).
    """
    sigs = Signal.query.order_by(Signal.triggered_at.desc()).all()
    records, _ = load_records()
    price_by_ticker = {r["ticker"]: r.get("price") for r in records}
    name_by_ticker = {r["ticker"]: r.get("name") for r in records}

    # أسعار مؤشر السوق (SPY) من الكاش — لمقارنة كل إشارة بأداء السوق عن نفس الفترة
    spy_rows = PricePoint.query.filter_by(ticker=MARKET_BENCHMARK).all()
    spy_by_date = {p.date: p.price for p in spy_rows if p.price is not None}
    spy_last = spy_by_date[max(spy_by_date)] if spy_by_date else None

    def _spy_on(day):
        """سعر المؤشر في يومٍ ما (أو أقرب يوم تداول سابق خلال أسبوع). None لو غير متوفر."""
        for back in range(8):
            p = spy_by_date.get(day - timedelta(days=back))
            if p is not None:
                return p
        return None

    now = datetime.now(timezone.utc)
    rows = []
    all_returns = []
    alphas = []
    by_type = {}  # signal_type -> list of returns
    for s in sigs:
        current = price_by_ticker.get(s.ticker)
        ret = None
        if current is not None and s.price_at_signal:
            ret = (current - s.price_at_signal) / s.price_at_signal * 100.0
            all_returns.append(ret)
            by_type.setdefault(s.signal_type, []).append(ret)
        triggered_at = s.triggered_at
        if triggered_at.tzinfo is None:  # SQLite محلياً بلا tzinfo
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)

        # عائد السوق عن نفس الفترة + الألفا (تفوق الإشارة على السوق)
        spy_ret = alpha = None
        if ret is not None and spy_last is not None:
            spy_start = _spy_on(triggered_at.date())
            if spy_start:
                spy_ret = (spy_last - spy_start) / spy_start * 100.0
                alpha = ret - spy_ret
                alphas.append(alpha)

        rows.append({
            "ticker": s.ticker,
            "name": name_by_ticker.get(s.ticker),
            "signal_type": s.signal_type,
            "date": s.triggered_at,
            "days": (now - triggered_at).days,
            "price_at_signal": s.price_at_signal,
            "current": current,
            "return_pct": ret,
            "spy_return_pct": spy_ret,
            "alpha": alpha,
        })

    def _stats(returns):
        """إحصائيات لمجموعة عوائد — None لو لا عوائد قابلة للحساب."""
        if not returns:
            return None
        wins = sum(1 for r in returns if r > 0)
        return {
            "count": len(returns),
            "avg": sum(returns) / len(returns),
            "win_count": wins,
            "win_rate": wins / len(returns) * 100.0,
            "best": max(returns),
            "worst": min(returns),
        }

    overall = _stats(all_returns)
    if overall and alphas:
        overall["avg_alpha"] = sum(alphas) / len(alphas)
        overall["beat_market"] = sum(1 for a in alphas if a > 0)
        overall["alpha_count"] = len(alphas)
    type_stats = {t: _stats(rs) for t, rs in by_type.items()}
    return rows, overall, type_stats


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


def market_direction():
    """اتجاه السوق الأمريكي من مؤشر S&P 500 (SPY) — من price_point، بلا استدعاء API.

    يقارن سعر المؤشر بمتوسطيه المتحركين 20 و50 يوماً:
    - صاعد: السعر فوق متوسط 20 وهو فوق متوسط 50 (ترند صاعد واضح).
    - هابط: السعر تحت متوسط 20 وهو تحت متوسط 50 (ترند هابط).
    - عرضي: ما عدا ذلك (تذبذب بلا اتجاه حاسم).
    يُرجع dict {label, emoji, cls, change_20} أو None لو تاريخ المؤشر غير كافٍ.
    """
    rows = (
        PricePoint.query
        .filter_by(ticker=MARKET_BENCHMARK)
        .order_by(PricePoint.date.asc())
        .all()
    )
    prices = [r.price for r in rows if r.price is not None]
    if len(prices) < 55:
        return None
    price = prices[-1]
    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    change_20 = (prices[-1] - prices[-21]) / prices[-21] * 100.0 if prices[-21] else None
    if price > sma20 > sma50:
        label, emoji, cls = "صاعد", "🟢", "bull"
    elif price < sma20 < sma50:
        label, emoji, cls = "هابط", "🔴", "bear"
    else:
        label, emoji, cls = "عرضي", "⚪", "neutral"
    return {"label": label, "emoji": emoji, "cls": cls, "change_20": change_20}


def market_mood(records=None):
    """مزاج السوق العام: كم سهم صاعد/هابط/محايد من كامل العيّنة (من الكاش، بلا API).

    لكل سهم نوازن مؤشراته الفنية: عدد الإشارات الصاعدة مقابل الهابطة.
    - صاعد لو غلبت الصاعدة، هابط لو غلبت الهابطة، محايد لو تعادلا.
    ثم نحكم على السوق: إيجابي (≥60% صاعد)، سلبي (≤40%)، أو متوازن بينهما.
    يُرجع dict بالعدّات والنسبة وحكم عام، أو None لو لا بيانات مؤشرات بعد.
    """
    if records is None:
        records, _ = load_records()
    bull = bear = neutral = 0
    for r in records:
        inds = r.get("indicators") or []
        if not inds:
            continue  # None ≠ 0 : سهم بلا مؤشرات لا يُحسب (لا يُعدّ محايداً زوراً)
        up = sum(1 for i in inds if i.get("status") == "bull")
        down = sum(1 for i in inds if i.get("status") == "bear")
        if up > down:
            bull += 1
        elif down > up:
            bear += 1
        else:
            neutral += 1
    total = bull + bear + neutral
    if total == 0:
        return None
    bull_pct = bull / total * 100.0
    if bull_pct >= 60:
        label, emoji, cls = "إيجابي", "🟢", "bull"
    elif bull_pct <= 40:
        label, emoji, cls = "سلبي", "🔴", "bear"
    else:
        label, emoji, cls = "متوازن", "🟡", "neutral"
    return {
        "bull": bull, "bear": bear, "neutral": neutral, "total": total,
        "bull_pct": bull_pct, "label": label, "emoji": emoji, "cls": cls,
    }


def filter_records(records, piotroski_min=None, catalyst_min=None,
                   price_max=None, market_cap_min=None, sector=None,
                   recent_gain_max=None, float_max=None, min_measures=None):
    """يطبّق الفلاتر على السجلّات المخزّنة.

    None ≠ 0 : السجلّ الذي تكون قيمته المطلوبة None لا يجتاز فلتراً يحدّ تلك القيمة
    (لا نعامله كصفر).
    market_cap_min بالدولار (خام)، يُحوّل من المليارات في طبقة الـ route.
    recent_gain_max: "لسا ما صعد" — يستبعد ما قفز أكثر من هذا خلال آخر أسبوعين
    (recent_gain=None يُبقى: العائد غير محسوب بعد، لا نستبعد بلا يقين).
    float_max: أعلى عدد أسهم حرة (خام) — يعرض الأسهم قليلة الحرة (الأسرع حركة)؛
    float_shares=None يُستبعد لأن العتبة صريحة (لا حكم بلا بيانات float).
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
        if recent_gain_max is not None:
            rg = r.get("recent_gain")
            if rg is not None and rg > recent_gain_max:
                continue
        if float_max is not None:
            fs = r.get("float_shares")
            if fs is None or fs > float_max:
                continue
        if min_measures is not None:
            if measures_met(r) < min_measures:
                continue
        out.append(r)
    # ترتيب تنازلي حسب Catalyst ثم Piotroski (None في الأسفل)
    out.sort(key=lambda r: (r.get("catalyst") or -1, r.get("piotroski") or -1), reverse=True)
    return out
