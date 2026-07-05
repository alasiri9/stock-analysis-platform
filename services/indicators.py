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

    # --- ADX: قوة الاتجاه (كانت شارة شكلية — الآن محسوبة فعلياً) ---
    adx = _adx(rows)
    if adx is not None:
        status = "bull" if adx >= 25 else "neutral"
        badges.append({"label": "ADX", "value": f"{adx:.0f}", "status": status})

    # --- قرب القمة: السعر ضمن 5% من أعلى قمة بالفترة المتاحة ---
    highs = [r["high"] for r in rows if r["high"] is not None]
    if highs:
        peak = max(highs)
        if peak > 0:
            status = "bull" if price >= peak * 0.95 else "neutral"
            badges.append({"label": "قمة", "value": f"{price / peak * 100:.0f}%", "status": status})

    # --- انضغاط بولينجر: تضيّق شديد للنطاق يسبق الانفجارات السعرية غالباً ---
    sq = _bollinger_squeeze(closes)
    if sq is not None:
        badges.append({
            "label": "انضغاط",
            "value": "نعم" if sq["squeezed"] else "لا",
            "status": "bull" if sq["squeezed"] else "neutral",
        })

    # --- التقاطع الذهبي/الموت: SMA50 مقابل SMA200 (اتجاه طويل المدى) ---
    gc = golden_cross(closes)
    if gc is not None:
        if gc["cross"] == "golden":
            badges.append({"label": "تقاطع", "value": "ذهبي 🌟", "status": "bull"})
        elif gc["cross"] == "death":
            badges.append({"label": "تقاطع", "value": "هابط", "status": "bear"})
        else:
            badges.append({
                "label": "تقاطع",
                "value": "فوق" if gc["above"] else "تحت",
                "status": "bull" if gc["above"] else "bear",
            })

    return badges


def _sma_series(values, period):
    """سلسلة متوسط بسيط. تُرجع [] لو البيانات أقل من period."""
    if len(values) < period:
        return []
    out = []
    total = sum(values[:period])
    out.append(total / period)
    for i in range(period, len(values)):
        total += values[i] - values[i - period]
        out.append(total / period)
    return out


def golden_cross(closes, fast=50, slow=200, recent=5):
    """يكشف التقاطع الذهبي/الهابط بين SMA50 وSMA200.

    يُرجع dict {cross: 'golden'|'death'|None, above: bool} — cross تعني تقاطعاً
    حدث خلال آخر `recent` جلسات، وabove حالة SMA50 مقابل SMA200 الآن.
    None لو البيانات أقل من slow + recent (لا حكم بلا تاريخ كافٍ).
    """
    if len(closes) < slow + recent:
        return None
    fast_s = _sma_series(closes, fast)
    slow_s = _sma_series(closes, slow)
    # نحاذي السلسلتين من النهاية (لكل نقطة نفس يوم الإغلاق)
    n = min(len(fast_s), len(slow_s))
    fast_s, slow_s = fast_s[-n:], slow_s[-n:]
    if n < recent + 1:
        return None

    above_now = fast_s[-1] > slow_s[-1]
    cross = None
    for i in range(n - recent, n):
        prev_above = fast_s[i - 1] > slow_s[i - 1]
        now_above = fast_s[i] > slow_s[i]
        if not prev_above and now_above:
            cross = "golden"
        elif prev_above and not now_above:
            cross = "death"
    return {"cross": cross, "above": above_now}


def _adx(rows, period=14):
    """ADX (متوسط مؤشر الاتجاه) بتمهيد Wilder. يُرجع القيمة أو None لو البيانات غير كافية."""
    rows = [r for r in rows if r["high"] is not None and r["low"] is not None]
    if len(rows) < period * 3:
        return None
    trs, pdms, ndms = [], [], []
    for i in range(1, len(rows)):
        h, l, prev = rows[i]["high"], rows[i]["low"], rows[i - 1]
        tr = max(h - l, abs(h - prev["close"]), abs(l - prev["close"]))
        up_move = h - prev["high"]
        down_move = prev["low"] - l
        pdms.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        ndms.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        trs.append(tr)
    atr = sum(trs[:period])
    pdm_s = sum(pdms[:period])
    ndm_s = sum(ndms[:period])
    dxs = []
    for i in range(period, len(trs)):
        atr = atr - atr / period + trs[i]
        pdm_s = pdm_s - pdm_s / period + pdms[i]
        ndm_s = ndm_s - ndm_s / period + ndms[i]
        if atr == 0:
            continue
        pdi = 100 * pdm_s / atr
        ndi = 100 * ndm_s / atr
        if pdi + ndi == 0:
            continue
        dxs.append(100 * abs(pdi - ndi) / (pdi + ndi))
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx


def _bollinger_squeeze(closes, period=20, lookback=90):
    """انضغاط بولينجر: هل عرض النطاق الحالي ضمن أدنى 20% من قيمه بفترة المراجعة؟

    عرض النطاق = (الحد الأعلى − الأدنى) / الوسط = 4×الانحراف المعياري / المتوسط.
    يُرجع {squeezed, width, threshold} أو None لو البيانات غير كافية.
    """
    if len(closes) < period + 10:
        return None
    widths = []
    for i in range(period - 1, len(closes)):
        win = closes[i - period + 1:i + 1]
        mean = sum(win) / period
        if not mean:
            continue
        sd = (sum((x - mean) ** 2 for x in win) / period) ** 0.5
        widths.append(4 * sd / mean)
    if len(widths) < 10:
        return None
    recent = widths[-lookback:]
    ordered = sorted(recent)
    threshold = ordered[max(0, int(len(ordered) * 0.2) - 1)]
    current = widths[-1]
    return {"squeezed": current <= threshold, "width": current, "threshold": threshold}


def squeeze_breakout(candles):
    """استراتيجية "الانفجار الوشيك": انضغاط بولينجر حديث + اختراق قمة 20 يوماً + حجم مرتفع.

    يُرجع True فقط عند اجتماع الشروط الثلاثة (None ≠ 0: بيانات ناقصة ⇒ False).
    """
    rows = _clean(candles)
    closes = [r["close"] for r in rows]
    if len(closes) < 40:
        return False

    # 1) انضغاط قائم أو انفكّ للتو (خلال آخر 10 جلسات) — الانفجار يلي الانضغاط
    sq_now = _bollinger_squeeze(closes)
    sq_before = _bollinger_squeeze(closes[:-5]) if len(closes) > 45 else None
    squeezed_recently = bool((sq_now and sq_now["squeezed"]) or (sq_before and sq_before["squeezed"]))
    if not squeezed_recently:
        return False

    # 2) اختراق: إغلاق اليوم فوق أعلى قمة الـ20 يوماً السابقة
    prior_high = max(r["high"] for r in rows[-21:-1] if r["high"] is not None)
    if closes[-1] <= prior_high:
        return False

    # 3) حجم اليوم أعلى من متوسط 20 يوماً بوضوح
    vols = [r["volume"] for r in rows[-21:-1] if r["volume"]]
    if not vols or not rows[-1]["volume"]:
        return False
    return rows[-1]["volume"] >= (sum(vols) / len(vols)) * 1.2


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
