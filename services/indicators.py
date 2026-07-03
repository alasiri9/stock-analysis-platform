"""
indicators.py — مؤشرات فنية محسوبة من أسعار FMP التاريخية اليومية (OHLC).

نحسبها يدوياً (لا مكتبات خارجية) من بيانات حقيقية:
- EMA (المتوسط المتحرك الأسّي) → اتجاه السعر
- MACD (12/26/9)               → زخم/تقاطع
- RSI (14)                     → قوة نسبية (تشبّع شرائي/بيعي)
- اختراق (Breakout)            → إغلاق فوق أعلى 20 يوماً
- الحجم (Volume)               → مقارنة حجم اليوم بمتوسط 20 يوماً

المبادئ:
- None ≠ 0 : لو البيانات غير كافية لمؤشّر، قيمته None ولا يُعرض كصفر/إشارة.
- لا توصية : المؤشرات وصفية تعليمية فقط.

كل badge: {"label", "value", "status"} حيث status ∈ {bull, bear, neutral}.
"""


def _clean(candles):
    """يرتّب الشموع من الأقدم للأحدث ويُسقط ما ينقصه الإغلاق."""
    rows = []
    for r in reversed(candles or []):  # FMP يُرجع الأحدث أولاً
        if r.get("close") is None:
            continue
        rows.append({
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "volume": r.get("volume"),
        })
    return rows


def _ema_series(values, period):
    """سلسلة EMA كاملة (تبدأ بمتوسط بسيط seed). تُرجع [] لو البيانات أقل من period."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    series = [sum(values[:period]) / period]  # seed = SMA لأول period
    for v in values[period:]:
        series.append(v * k + series[-1] * (1 - k))
    return series


def _macd(closes):
    """MACD(12,26,9). يُرجع dict {macd, signal, hist} أو None."""
    if len(closes) < 26 + 9:
        return None
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    # محاذاة: ema12 أطول بـ (26-12) قيمة من البداية
    ema12_aligned = ema12[26 - 12:]
    macd_line = [a - b for a, b in zip(ema12_aligned, ema26)]
    signal = _ema_series(macd_line, 9)
    if not signal:
        return None
    return {"macd": macd_line[-1], "signal": signal[-1], "hist": macd_line[-1] - signal[-1]}


def _rsi(closes, period=14):
    """RSI(14) بطريقة Wilder. يُرجع 0–100 أو None."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def build_indicators(candles):
    """يحسب كل المؤشرات ويُرجع قائمة badges جاهزة للعرض (قد تكون فارغة)."""
    rows = _clean(candles)
    closes = [r["close"] for r in rows]
    if len(closes) < 20:
        return []  # بيانات غير كافية لأي مؤشّر موثوق

    badges = []
    price = closes[-1]

    # --- EMA: اتجاه السعر مقابل EMA20 و EMA50 ---
    ema20_s = _ema_series(closes, 20)
    ema50_s = _ema_series(closes, 50)
    ema20 = ema20_s[-1] if ema20_s else None
    ema50 = ema50_s[-1] if ema50_s else None
    if ema20 is not None:
        if ema50 is not None and price > ema20 > ema50:
            status = "bull"
        elif ema50 is not None and price < ema20 < ema50:
            status = "bear"
        else:
            status = "neutral"
        badges.append({"label": "EMA", "value": "صاعد" if status == "bull" else ("هابط" if status == "bear" else "محايد"), "status": status})

    # --- MACD ---
    macd = _macd(closes)
    if macd is not None:
        status = "bull" if macd["hist"] > 0 else "bear"
        badges.append({"label": "MACD", "value": "إيجابي" if status == "bull" else "سلبي", "status": status})

    # --- RSI ---
    rsi = _rsi(closes)
    if rsi is not None:
        if rsi >= 70:
            status, note = "bear", "تشبّع شرائي"
        elif rsi <= 30:
            status, note = "bull", "تشبّع بيعي"
        else:
            status = "bull" if rsi >= 50 else "bear"
            note = ""
        label = f"RSI {rsi:.0f}" + (f" ({note})" if note else "")
        badges.append({"label": "RSI", "value": f"{rsi:.0f}" + (f" · {note}" if note else ""), "status": status})

    # --- اختراق: إغلاق اليوم فوق أعلى قمة في آخر 20 يوماً (باستثناء اليوم) ---
    if len(rows) >= 21:
        prior_highs = [r["high"] for r in rows[-21:-1] if r["high"] is not None]
        if prior_highs:
            is_breakout = price >= max(prior_highs)
            badges.append({
                "label": "اختراق",
                "value": "نعم" if is_breakout else "لا",
                "status": "bull" if is_breakout else "neutral",
            })

    # --- الحجم: حجم اليوم مقابل متوسط 20 يوماً ---
    if len(rows) >= 21 and rows[-1]["volume"]:
        prior_vols = [r["volume"] for r in rows[-21:-1] if r["volume"]]
        if prior_vols:
            avg_vol = sum(prior_vols) / len(prior_vols)
            if avg_vol > 0:
                ratio = rows[-1]["volume"] / avg_vol
                if ratio >= 1.2:
                    status, val = "bull", "مرتفع"
                elif ratio <= 0.8:
                    status, val = "neutral", "منخفض"
                else:
                    status, val = "neutral", "عادي"
                badges.append({"label": "الحجم", "value": val, "status": status})

    return badges


def money_flow(candles):
    """درجة تدفق السيولة الذكية (0-100) من OBV + MFI + نسبة الحجم.

    - OBV: هل الحجم يتراكم مع الصعود (تجميع) أم مع الهبوط (تصريف)؟
    - MFI(14): مؤشر تدفق الأموال — RSI مرجّح بالحجم.
    - نسبة الحجم: حجم آخر يوم إلى متوسط 20 يوماً.
    يُرجع dict: {score, status, label, mfi, obv_trend, vol_ratio} أو None لو البيانات غير كافية.
    None ≠ 0 : غياب الحجم أو قصر التاريخ ⇒ None وليس درجة صفرية ملفّقة.
    """
    rows = _clean(candles)
    # نحتاج حجماً وأسعار قمة/قاع صالحة لـ21 يوماً على الأقل
    rows = [r for r in rows if r["volume"] and r["high"] is not None and r["low"] is not None]
    if len(rows) < 21:
        return None

    closes = [r["close"] for r in rows]
    volumes = [r["volume"] for r in rows]

    # --- OBV: سلسلة تراكمية، ونقارن آخر قيمة بقيمتها قبل 10 جلسات ---
    obv = [0.0]
    for i in range(1, len(rows)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    recent_span = abs(obv[-1] - obv[-11])
    avg_vol20 = sum(volumes[-20:]) / 20
    if obv[-1] > obv[-11] and recent_span > avg_vol20:
        obv_trend, obv_pts = "up", 40
    elif obv[-1] < obv[-11] and recent_span > avg_vol20:
        obv_trend, obv_pts = "down", 0
    else:
        obv_trend, obv_pts = "flat", 20

    # --- MFI(14) ---
    period = 14
    pos_flow = neg_flow = 0.0
    for i in range(len(rows) - period, len(rows)):
        tp = (rows[i]["high"] + rows[i]["low"] + rows[i]["close"]) / 3
        tp_prev = (rows[i - 1]["high"] + rows[i - 1]["low"] + rows[i - 1]["close"]) / 3
        raw = tp * volumes[i]
        if tp > tp_prev:
            pos_flow += raw
        elif tp < tp_prev:
            neg_flow += raw
    if pos_flow + neg_flow == 0:
        mfi = 50.0
    elif neg_flow == 0:
        mfi = 100.0
    else:
        mfi = 100 - 100 / (1 + pos_flow / neg_flow)

    # --- نسبة الحجم ---
    vol_ratio = volumes[-1] / avg_vol20 if avg_vol20 else None
    if vol_ratio is None:
        vol_pts = 0
    elif vol_ratio >= 1.5:
        vol_pts = 20
    elif vol_ratio >= 1.0:
        vol_pts = 10
    else:
        vol_pts = 0

    score = obv_pts + (mfi / 100) * 40 + vol_pts
    if score >= 65:
        status, label = "bull", "تجميع"
    elif score <= 35:
        status, label = "bear", "تصريف"
    else:
        status, label = "neutral", "محايد"

    return {
        "score": round(score, 1),
        "status": status,
        "label": label,
        "mfi": round(mfi, 1),
        "obv_trend": obv_trend,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
    }


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services import fmp_client

    candles = fmp_client.get_historical_prices("AAPL", limit=120)
    print("مؤشرات AAPL:")
    for b in build_indicators(candles):
        print(f"  {b['label']}: {b['value']} [{b['status']}]")
