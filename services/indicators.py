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

# عدد المؤشرات الفنية الثابت في المنصة (EMA · MACD · RSI · اختراق · الحجم · ADX ·
# قمة · انضغاط · تقاطع · سوبرترند · ستوكاستيك · تراكم). يُستخدم مقاماً ثابتاً للعرض
# حتى لا يتذبذب حسب توفّر بيانات كل سهم (بعض المؤشرات يحتاج تاريخاً أطول).
TOTAL_INDICATORS = 12


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
            "date": r.get("date"),
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

    # --- نسبة حجم اليوم لمتوسط 20 يوماً (تُستخدم في تصنيف RSI وفي شارة الحجم) ---
    vol_ratio = None
    if len(rows) >= 21 and rows[-1]["volume"]:
        _pv = [r["volume"] for r in rows[-21:-1] if r["volume"]]
        if _pv:
            _av = sum(_pv) / len(_pv)
            if _av > 0:
                vol_ratio = rows[-1]["volume"] / _av

    # --- RSI ---
    rsi = _rsi(closes)
    if rsi is not None:
        if rsi >= 70:
            # تشبّع شرائي = قوة لا ضعف حين يزامنه حجم عالٍ (تأكيد انطلاق حقيقية)؛
            # وبلا تأكيد حجم يبقى محايداً (راقب) لا عقوبة — كثير من الأسهم بعد 70 تعطي
            # عطاءً ممتازاً بشرط تزامنها مع سيولة عالية (ملاحظة أحمد، مؤكّدة فنياً).
            if vol_ratio is not None and vol_ratio >= 1.2:
                status, note = "bull", "زخم قوي (مؤكّد بحجم)"
            else:
                status, note = "neutral", "تشبّع شرائي — راقب"
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

    # --- الحجم: حجم اليوم مقابل متوسط 20 يوماً (نفس vol_ratio المحسوب أعلاه) ---
    if vol_ratio is not None:
        if vol_ratio >= 1.2:
            status, val = "bull", "مرتفع"
        elif vol_ratio <= 0.8:
            status, val = "neutral", "منخفض"
        else:
            status, val = "neutral", "عادي"
        badges.append({"label": "الحجم", "value": val, "status": status})

    # --- ADX: قوة الاتجاه (يقيس القوة لا الاتجاه) — نُميّز اتجاهه من +DI/−DI ---
    # اتجاه قوي صاعد (ADX≥25 و+DI>−DI) = صاعد؛ اتجاه قوي هابط (ADX≥25 و−DI>+DI) = هابط
    # (لا يُحتسب صاعداً — تصحيح: القوة وحدها لا تعني صعوداً)؛ ADX ضعيف (<25) = عرضي/محايد.
    adx, pdi, ndi = _adx_di(rows)
    if adx is not None:
        if adx >= 25 and pdi is not None and ndi is not None and pdi > ndi:
            status = "bull"
        elif adx >= 25 and pdi is not None and ndi is not None and ndi > pdi:
            status = "bear"
        else:
            status = "neutral"
        badges.append({"label": "ADX", "value": f"{adx:.0f}", "status": status})

    # --- قرب القمة: السعر ضمن 5% من أعلى قمة بالفترة المتاحة ---
    highs = [r["high"] for r in rows if r["high"] is not None]
    if highs:
        peak = max(highs)
        if peak > 0:
            status = "bull" if price >= peak * 0.95 else "neutral"
            badges.append({"label": "قمة", "value": f"{price / peak * 100:.0f}%", "status": status})

    # --- انضغاط بولينجر: تضيّق شديد للنطاق يسبق الانفجارات السعرية غالباً ---
    # الانضغاط محايد الاتجاه بذاته — يُعدّ إيجابياً (bull) فقط إذا رافقه تأكيد صعودي
    # مزدوج: اتجاه صاعد (EMA) + السعر قرب قمته (قمة)، أي انضغاط فوق اتجاه صاعد وقرب
    # القمة = تمهيد صعودي كلاسيكي (علم/راية). اخترنا «قرب القمة» بدل MACD لأن التجميع
    # يُضعف زخم MACD بطبيعته (فيندر التأكيد)، بينما «قرب القمة» يصمد أثناء الانضغاط.
    # بدون هذا التأكيد يبقى محايداً فلا يُضخّم عدّاد الإشارات الإيجابية / قوة التأكيد.
    sq = _bollinger_squeeze(closes)
    if sq is not None:
        squeezed = sq["squeezed"]
        ema_bull = any(b.get("label") == "EMA" and b.get("status") == "bull" for b in badges)
        near_high = any(b.get("label") == "قمة" and b.get("status") == "bull" for b in badges)
        confirmed = squeezed and ema_bull and near_high
        # القيمة تعكس المعنى: «إيجابي» = انضغاط مؤكّد صعودياً · «نعم» = انضغاط محايد · «لا» = بلا انضغاط
        value = "إيجابي" if confirmed else ("نعم" if squeezed else "لا")
        badges.append({
            "label": "انضغاط",
            "value": value,
            "status": "bull" if confirmed else "neutral",
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

    # --- SuperTrend: حكم اتجاه صريح + مستوى وقف متحرك (ATR-based) ---
    st = supertrend(rows)
    if st is not None:
        badges.append({
            "label": "سوبرترند",
            "value": f"صاعد ({st['level']:.0f}$)" if st["trend"] == "up" else f"هابط ({st['level']:.0f}$)",
            "status": "bull" if st["trend"] == "up" else "bear",
        })

    # --- Stochastic (14,3): زخم قصير المدى وتشبّع ---
    stoch = _stochastic(rows)
    if stoch is not None:
        k, d = stoch
        if k >= 80:
            val, status = f"{k:.0f} تشبّع شرائي", "neutral"
        elif k <= 20:
            val, status = f"{k:.0f} تشبّع بيعي", "neutral"
        elif k > d:
            val, status = f"{k:.0f} صاعد", "bull"
        else:
            val, status = f"{k:.0f} هابط", "bear"
        badges.append({"label": "ستوكاستيك", "value": val, "status": status})

    # --- OBV (تراكم الحجم): تجميع مقابل تصريف عبر آخر 20 جلسة ---
    obv = _obv_trend(rows)
    if obv is not None:
        badges.append({"label": "تراكم", "value": obv["value"], "status": obv["status"]})

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


def _adx_di(rows, period=14):
    """ADX + آخر +DI/−DI بتمهيد Wilder. يُرجع (adx, plus_di, minus_di) أو (None, None, None).

    ADX يقيس «قوة» الاتجاه فقط ولا يحدّد اتجاهه؛ الاتجاه يُقرأ من +DI مقابل −DI الأخيرين:
    +DI > −DI = زخم اتجاه صاعد، −DI > +DI = زخم اتجاه هابط. (يُستعمَل لتمييز الاتجاه القوي
    الهابط عن الصاعد فلا يُحتسب الهابط إشارة صاعدة.)
    """
    rows = [r for r in rows if r["high"] is not None and r["low"] is not None]
    if len(rows) < period * 3:
        return None, None, None
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
    pdi = ndi = None  # آخر قيمتين تعكسان الاتجاه الحالي
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
        return None, None, None
    adx = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx, pdi, ndi


def _adx(rows, period=14):
    """ADX (قوة الاتجاه) بتمهيد Wilder — غلاف رفيع حول _adx_di. يُرجع القيمة أو None."""
    return _adx_di(rows, period)[0]


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
    _prior_highs = [r["high"] for r in rows[-21:-1] if r["high"] is not None]
    if not _prior_highs:
        return False
    prior_high = max(_prior_highs)
    if closes[-1] <= prior_high:
        return False

    # 3) حجم اليوم أعلى من متوسط 20 يوماً بوضوح
    vols = [r["volume"] for r in rows[-21:-1] if r["volume"]]
    if not vols or not rows[-1]["volume"]:
        return False
    return rows[-1]["volume"] >= (sum(vols) / len(vols)) * 1.2


def _anchored_vwap(window):
    """VWAP المرتكز: متوسط السعر (النموذجي) مرجّحاً بالحجم عبر نافذة أيام معطاة.

    السعر النموذجي لليوم = (قمة + قاع + إغلاق) / 3. المرساة = أول يوم بالنافذة
    (يوم الاختراق مثلاً). يُرجع القيمة أو None لو لا حجم صالح.
    """
    num = den = 0.0
    for r in window:
        h, l, c, v = r.get("high"), r.get("low"), r.get("close"), r.get("volume")
        if h is None or l is None or c is None or not v:
            continue
        num += ((h + l + c) / 3.0) * v
        den += v
    return (num / den) if den else None


def _resample(candles, period="W"):
    """يجمّع الشموع اليومية إلى أسبوعية/شهرية (معيار OHLC — نفس ما يبني به أي شارت).

    candles: خام من FMP (الأحدث أولاً) فيه date/high/low/close.
    period: 'W' أسبوعي (حسب أسبوع ISO) · 'M' شهري.
    قمة الفترة = أعلى قمم أيامها، قاعها = أدنى قيعانها، إغلاقها = إغلاق آخر يوم فيها.
    يُرجع قائمة شموع مجمّعة مرتّبة الأقدم أولاً: [{high, low, close}].
    """
    from datetime import date
    buckets = {}
    for c in (candles or []):
        d = c.get("date")
        h, l, cl = c.get("high"), c.get("low"), c.get("close")
        if not d or h is None or l is None:
            continue
        ds = str(d)[:10]  # "YYYY-MM-DD"
        try:
            y, m, day = int(ds[:4]), int(ds[5:7]), int(ds[8:10])
            key = (y, m) if period == "M" else date(y, m, day).isocalendar()[:2]
        except (ValueError, TypeError):
            continue
        b = buckets.get(key)
        if b is None:
            # الأحدث أولاً ⇒ أول ظهور للفترة = آخر يوم فيها ⇒ نثبّت الإغلاق منه
            buckets[key] = {"high": h, "low": l, "close": cl}
        else:
            b["high"] = max(b["high"], h)
            b["low"] = min(b["low"], l)
    return [buckets[k] for k in sorted(buckets)]


def _fractal_highs(bars, wing=2):
    """قمم فراكتالية (Bill Williams): شمعة قمّتها أعلى من `wing` شموع قبلها وبعدها.

    bars: شموع مرتّبة الأقدم أولاً. آخر `wing` شمعة لا تُحسب (لم تتأكد بعد).
    يُرجع قائمة قيم القمم المؤكّدة.
    """
    highs = [b["high"] for b in bars]
    n = len(highs)
    out = []
    for i in range(wing, n - wing):
        h = highs[i]
        if all(h > highs[i - k] for k in range(1, wing + 1)) and \
           all(h > highs[i + k] for k in range(1, wing + 1)):
            out.append(h)
    return out


def _overhead_target(candles, price, cap=0.6):
    """أقرب هدف فوق السعر = أقرب قمة فراكتالية أسبوعية/شهرية سابقة (الحساب الرسمي).

    عند اختراق مقاومة، أقرب هدف منطقي هو المقاومة التالية فوقها (والمخترَقة تصير دعماً).
    نجمّع الشموع أسبوعياً وشهرياً، نستخرج قممها الفراكتالية، ونأخذ أقربها فوق السعر
    (ضمن سقف cap فتُهمَل القمم البعيدة جداً).
    يُرجع (المستوى، البُعد النسبي، الإطار "أسبوعية"/"شهرية") أو (None, None, None) لو الأفق صافٍ.
    """
    ceiling = price * (1 + cap)
    candidates = []
    for tf, label in (("W", "أسبوعية"), ("M", "شهرية")):
        for lv in _fractal_highs(_resample(candles, tf)):
            if price < lv <= ceiling:
                candidates.append((lv, label))
    if not candidates:
        return None, None, None
    lv, label = min(candidates, key=lambda x: x[0])
    return lv, (lv - price) / price, label


def _leg_start(rows, lookback=20, max_age=30):
    """بداية الساق الصاعدة الحالية = يوم اختراق قاعدة (قمة الـlookback) لا يزال الإغلاق
    فوق مستواه حتى اليوم — مع **السماح بتصحيح بسيط** (المهم ألا يعود تحت المستوى).

    نبحث ضمن آخر max_age جلسة عن **أبكر** يوم اخترق فيه قمة الـlookback السابقة وبقي
    الإغلاق فوق ذلك المستوى في كل الجلسات التالية (تصحيحٌ نحو المستوى مسموح، الكسر تحته لا).
    هذا يلتقط استمرار الصعود الواقعي بدل اشتراط تسجيل قمة جديدة كل يوم.

    يُرجع (index يوم الاختراق، مستوى القاعدة المخترَق) أو (None, None) لو لا اختراق قائم.
    """
    n = len(rows)
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    start = max(lookback, n - max_age)
    for i in range(start, n):  # الأقدم أولاً ضمن النافذة ⇒ أبكر اختراق ما زال صامداً
        prior = [h for h in highs[i - lookback:i] if h is not None]
        if not prior or closes[i] is None:
            continue
        level = max(prior)
        if closes[i] > level and all(
            (closes[j] is None) or (closes[j] > level) for j in range(i, n)
        ):
            return i, level
    return None, None
    return None, None


def sustained_breakout(candles, hold_min=2, adx_min=15, clear_air_min=0.03, vol_mult=1.5,
                       ext_atr_max=4.0):
    """«اختراق مستمر»: سهم اخترق قاعدته بحجم مؤكّد ولا يزال يواصل صعوده بثبات.

    الهدف: تمييز الاختراق «الصحّي المستمر» عن الاختراق الكذّاب. يشترط اجتماع:
      1) اختراق صاعد لقاعدة (قمة 20 يوماً) **مؤكّد بالحجم** يوم بدايته، لا يزال قائماً
         منذ ≥ hold_min جلسة (السعر لم يعُد تحت مستوى الاختراق).
      2) السعر فوق **VWAP المرتكز** على يوم الاختراق — كل من اشترى بعد الاختراق رابح.
      3) اتجاه صاعد: EMA20 صاعد والسعر فوقه.
      4) قوة اتجاه: ADX ≥ adx_min.
      5) **مساحة حرة فوقه**: لا مقاومة سابقة (قمة أسبوعية/شهرية) خلال clear_air_min فوق السعر.

    كما يقيس **موضع الدخول** (entry_zone): بُعد السعر عن EMA20 بوحدات ATR — «قريب»
    (لا يزال في منطقة دخول جيدة) أو «ممتد» (صعد بعيداً، الدخول الآن متأخر ومخاطرته أعلى)
    — احترازاً من مطاردة سهم امتدّ كثيراً عن نقطة انطلاقه.

    «المساحة الحرة» = أقرب هدف فوق السعر (أقرب قمة أسبوعية/شهرية فراكتالية سابقة):
    قريبة ⇒ أفق ضيّق · بعيدة/غائبة ⇒ أفق مفتوح. وهي نفسها أقرب هدف سعري بعد الاختراق.

    يُرجع dict وصفي {sustained, above_avwap, avwap, ema_rising, adx_ok, clear_air,
    next_resistance, resistance_pct, resistance_tf, days_held, level, entry_zone,
    ext_atr, ext_pct} أو None لو لا اختراق/بيانات ناقصة.
    """
    rows = _clean(candles)  # الأقدم أولاً
    closes = [r["close"] for r in rows]
    if len(closes) < 45:
        return None
    price = closes[-1]

    anchor, level = _leg_start(rows)
    if anchor is None:
        return None
    days_held = (len(rows) - 1) - anchor  # جلسات منذ يوم الاختراق

    # (1) ثبات فوق مستوى الاختراق منذ عدة جلسات + حجم مؤكّد حول يوم الاختراق
    held = days_held >= hold_min and price > level
    base_vols = [r["volume"] for r in rows[max(0, anchor - 21):max(0, anchor - 1)] if r["volume"]]
    avg_vol = (sum(base_vols) / len(base_vols)) if base_vols else 0
    # طفرة الحجم تتجمّع حول الاختراق — نتسامح بيوم قبله/بعده
    brk_vols = [rows[j]["volume"] for j in range(max(0, anchor - 1), min(len(rows), anchor + 2))
                if rows[j]["volume"]]
    confirmed = bool(avg_vol and brk_vols and max(brk_vols) >= avg_vol * vol_mult)

    # (2) VWAP المرتكز على يوم الاختراق (من الاختراق حتى اليوم)
    avwap = _anchored_vwap(rows[anchor:])
    above_avwap = avwap is not None and price >= avwap

    # (3) EMA20 صاعد والسعر فوقه
    ema_s = _ema_series(closes, 20)
    ema_rising = len(ema_s) >= 6 and ema_s[-1] > ema_s[-6] and price > ema_s[-1]

    # (4) قوة الاتجاه
    adx = _adx(rows)
    adx_ok = adx is not None and adx >= adx_min

    # (5) مساحة حرة فوق السهم: أقرب هدف = أقرب قمة أسبوعية/شهرية سابقة (الحساب الرسمي)
    next_res, res_pct, res_tf = _overhead_target(candles, price)
    clear_air = (next_res is None) or (res_pct is not None and res_pct >= clear_air_min)

    # موضع الدخول: كم ابتعد السعر عن متوسطه المتحرك (EMA20) بوحدات ATR (احترازاً من مطاردة سهم ممتد).
    # قريب من متوسطه = منطقة دخول أفضل · بعيد جداً = ممتد، الدخول متأخر ومخاطرته أعلى.
    atr_val = atr(candles)
    ema20 = ema_s[-1] if ema_s else None
    ext_atr = ((price - ema20) / atr_val) if (ema20 is not None and atr_val) else None
    ext_pct = ((price - ema20) / ema20 * 100.0) if ema20 else None
    entry_zone = None if ext_atr is None else ("extended" if ext_atr > ext_atr_max else "near")

    sustained = bool(confirmed and held and above_avwap and ema_rising and adx_ok and clear_air)
    return {
        "rising": True,             # يوجد ساق صاعدة قائمة (السعر فوق مستوى الاختراق حتى اليوم)
        "sustained": sustained,     # اكتملت كل شروط التأكيد ⇒ «اختراق مستمر»
        "confirmed": confirmed,     # حجم مؤكّد يوم الاختراق
        "held": held,               # ثابت فوق مستوى الاختراق منذ عدة جلسات
        "start_date": rows[anchor].get("date"),  # تاريخ بداية موجة الصعود (يوم)
        "above_avwap": above_avwap,
        "avwap": avwap,
        "ema_rising": ema_rising,
        "adx_ok": adx_ok,
        "clear_air": clear_air,
        "next_resistance": next_res,   # أقرب هدف فوق السعر (قمة أسبوعية/شهرية) أو None
        "resistance_pct": res_pct,     # بُعد الهدف نسبةً
        "resistance_tf": res_tf,       # إطار الهدف: "أسبوعية" / "شهرية" / None
        "days_held": days_held,
        "level": level,
        "entry_zone": entry_zone,   # "near" (دخول جيد) · "extended" (ممتد، متأخر) · None
        "ext_atr": ext_atr,         # بُعد السعر عن EMA20 بوحدات ATR
        "ext_pct": ext_pct,         # البُعد نفسه كنسبة مئوية (للعرض)
    }


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


def _stochastic(rows, period=14, smooth=3):
    """Stochastic (%K المُنعَّم، %D) — يُرجع (k, d) أو None لو البيانات غير كافية."""
    valid = [r for r in rows if r["high"] is not None and r["low"] is not None]
    if len(valid) < period + smooth * 2:
        return None
    raw_ks = []
    for i in range(period - 1, len(valid)):
        win = valid[i - period + 1:i + 1]
        hh = max(r["high"] for r in win)
        ll = min(r["low"] for r in win)
        c = valid[i]["close"]
        raw_ks.append(50.0 if hh == ll else (c - ll) / (hh - ll) * 100.0)
    if len(raw_ks) < smooth * 2:
        return None
    # %K المنعم = SMA3 للخام، و%D = SMA3 للمنعم
    ks = [sum(raw_ks[i - smooth + 1:i + 1]) / smooth for i in range(smooth - 1, len(raw_ks))]
    if len(ks) < smooth:
        return None
    d = sum(ks[-smooth:]) / smooth
    return ks[-1], d


def _obv_trend(rows, lookback=20):
    """اتجاه OBV (On-Balance Volume — تراكم الحجم): تجميع/تصريف/محايد.

    OBV تراكمي: يُضاف حجم اليوم لو أغلق أعلى من أمس، ويُطرح لو أغلق أدنى.
    صعود OBV = سيولة تتجمّع (تجميع، إشارة قوة)، هبوطه = تصريف.
    نقارن OBV الآن بقيمته قبل `lookback` جلسة. يُرجع dict {value, status} أو None.
    """
    valid = [r for r in rows if r.get("volume")]
    if len(valid) < lookback + 5:
        return None
    obv = 0.0
    series = [0.0]
    for i in range(1, len(valid)):
        if valid[i]["close"] > valid[i - 1]["close"]:
            obv += valid[i]["volume"]
        elif valid[i]["close"] < valid[i - 1]["close"]:
            obv -= valid[i]["volume"]
        series.append(obv)
    change = series[-1] - series[-lookback - 1]
    # عتبة بسيطة لتفادي الضجيج: نعتبره محايداً لو التغيّر ضئيل مقابل متوسط الحجم
    total_vol = sum(r["volume"] for r in valid[-lookback:])
    avg_vol = total_vol / lookback
    # نسبة قوة التجميع = صافي التغيّر ÷ إجمالي الحجم بالفترة (0–100%)
    pct = round(abs(change) / total_vol * 100) if total_vol else 0
    if avg_vol <= 0 or abs(change) < avg_vol:
        return {"value": "محايد", "status": "neutral"}
    if change > 0:
        return {"value": f"تجميع {pct}%", "status": "bull"}
    return {"value": f"تصريف {pct}%", "status": "bear"}


def supertrend(rows, period=10, mult=3.0):
    """SuperTrend — اتجاه صريح (up/down) مع مستوى الوقف المتحرك الحالي.

    يُرجع {"trend": "up"|"down", "level": float} أو None لو البيانات غير كافية.
    """
    valid = [r for r in rows if r["high"] is not None and r["low"] is not None]
    if len(valid) < period * 2:
        return None

    # ATR بتمهيد Wilder
    trs = []
    for i in range(1, len(valid)):
        h, l, pc = valid[i]["high"], valid[i]["low"], valid[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    atrs = [atr]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        atrs.append(atr)

    # النطاقات النهائية + الاتجاه (المنطق القياسي للتتبع)
    closes_v = [r["close"] for r in valid]
    start = period  # أول شمعة لها ATR
    fub = flb = None  # final upper/lower band
    trend = "up"
    for i in range(start, len(valid)):
        mid = (valid[i]["high"] + valid[i]["low"]) / 2
        a = atrs[i - start]
        bub = mid + mult * a
        blb = mid - mult * a
        prev_close = closes_v[i - 1]
        fub = bub if (fub is None or bub < fub or prev_close > fub) else fub
        flb = blb if (flb is None or blb > flb or prev_close < flb) else flb
        if closes_v[i] > fub:
            trend = "up"
        elif closes_v[i] < flb:
            trend = "down"
    return {"trend": trend, "level": flb if trend == "up" else fub}


def trend_pullback(candles):
    """استراتيجية "ارتداد الترند": ترند صاعد + تراجع لمس EMA20 + بدء ارتداد.

    الشروط: EMA20 > EMA50 (ترند صاعد) + قاع إحدى آخر 3 جلسات لامس EMA20
    (ضمن 1%) + RSI بين 35 و60 (تنفّس لا انهيار) + إغلاق اليوم أعلى من الأمس.
    يُرجع True/False (False أيضاً عند نقص البيانات — لا إشارة بلا يقين).
    """
    rows = _clean(candles)
    closes = [r["close"] for r in rows]
    if len(closes) < 55:
        return False

    ema20_s = _ema_series(closes, 20)
    ema50_s = _ema_series(closes, 50)
    if not ema20_s or not ema50_s:
        return False
    if ema20_s[-1] <= ema50_s[-1]:
        return False  # لا ترند صاعد

    # لمس EMA20 بإحدى آخر 3 جلسات (قاع الجلسة نزل لحدود المتوسط)
    touched = False
    for back in range(1, 4):
        row = rows[-back]
        low = row["low"] if row["low"] is not None else row["close"]
        ema_then = ema20_s[-back]
        if low <= ema_then * 1.01:
            touched = True
            break
    if not touched:
        return False

    rsi = _rsi(closes)
    if rsi is None or not (35 <= rsi <= 60):
        return False

    return closes[-1] > closes[-2]  # بدأ يرتد فعلاً


def reversal_pattern(candles):
    """كشف «شمعة انعكاس» على آخر جلسة (Price Action) — من نفس شموع FMP بلا استدعاء إضافي.

    تعليمي فقط: ينبّه المتعلّم أن آخر شمعة تحمل شكلاً انعكاسياً معروفاً عند المحللين
    (ابتلاع · مطرقة · شهاب · دوجي) — ليس توصية شراء/بيع. الشمعة الانعكاسية أقوى دلالة
    حين تظهر عكس اتجاه قصير سابق (مطرقة بعد هبوط، شهاب بعد صعود).

    مقصود ألا يدخل هذا في عدّاد المؤشرات الاثني عشر ولا في «قوة التأكيد»: شمعة واحدة
    دليل ضعيف بذاته، فلا نُدخلها لتُعيد ترتيب جودة الأسهم — نعرضها كتنبيه معرفي فقط.

    يُرجع dict {"pattern", "status", "note"} أو None لو لا نمط واضح.
      status ∈ {bull, bear, neutral} — للون الشارة فقط.
    """
    rows = []
    for r in reversed(candles or []):  # FMP الأحدث أولاً ⇒ نعكسها للأقدم أولاً
        o, h, l, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
        if None in (o, h, l, c):
            continue
        rows.append({"open": o, "high": h, "low": l, "close": c})
    if len(rows) < 5:
        return None  # نحتاج اتجاهاً قصيراً سابقاً حتى يكون للانعكاس معنى

    cur, prev = rows[-1], rows[-2]
    o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper = h - max(o, c)   # الظل العلوي
    lower = min(o, c) - l   # الظل السفلي
    cur_green = c > o
    cur_red = c < o
    prev_green = prev["close"] > prev["open"]
    prev_red = prev["close"] < prev["open"]
    prev_mid = (prev["open"] + prev["close"]) / 2.0
    prev_body = abs(prev["close"] - prev["open"])

    # الشمعة قبل السابقة (للنماذج الثلاثية: نجمة الصباح/المساء)
    prev2 = rows[-3]
    prev2_green = prev2["close"] > prev2["open"]
    prev2_red = prev2["close"] < prev2["open"]
    prev2_body = abs(prev2["close"] - prev2["open"])
    prev2_mid = (prev2["open"] + prev2["close"]) / 2.0

    # اتجاه قصير سابق (إغلاق ما قبل الشمعة الحالية مقابل إغلاق ~٣ جلسات قبله)
    trend_down = prev["close"] < rows[-5]["close"]
    trend_up = prev["close"] > rows[-5]["close"]

    # 0أ) نجمة الصباح: ثلاث شموع (هبوط كبير ⇒ نجمة صغيرة ⇒ صعود قوي يُغلق فوق منتصف الأولى)
    if (trend_down and prev2_red and prev2_body > 0
            and prev_body <= 0.5 * prev2_body
            and cur_green and body >= 0.5 * rng and c > prev2_mid):
        return {"pattern": "نجمة الصباح", "status": "bull",
                "note": "ثلاث شموع: هبوط ⇒ تردّد ⇒ صعود قوي — انعكاس صعودي محتمل"}

    # 0ب) نجمة المساء: ثلاث شموع (صعود كبير ⇒ نجمة صغيرة ⇒ هبوط قوي يُغلق تحت منتصف الأولى)
    if (trend_up and prev2_green and prev2_body > 0
            and prev_body <= 0.5 * prev2_body
            and cur_red and body >= 0.5 * rng and c < prev2_mid):
        return {"pattern": "نجمة المساء", "status": "bear",
                "note": "ثلاث شموع: صعود ⇒ تردّد ⇒ هبوط قوي — انعكاس هبوطي محتمل"}

    # 1) الابتلاع الصاعد: شمعة خضراء تبتلع جسم شمعة حمراء قبلها (انعكاس صعودي كلاسيكي)
    if cur_green and prev_red and o <= prev["close"] and c >= prev["open"] and body >= 0.5 * rng:
        return {"pattern": "ابتلاع صاعد", "status": "bull",
                "note": "شمعة صعود ابتلعت هبوط أمس — إشارة انعكاس صعودي محتملة"}

    # 2) الابتلاع الهابط: شمعة حمراء تبتلع جسم شمعة خضراء قبلها (انعكاس هبوطي)
    if cur_red and prev_green and o >= prev["close"] and c <= prev["open"] and body >= 0.5 * rng:
        return {"pattern": "ابتلاع هابط", "status": "bear",
                "note": "شمعة هبوط ابتلعت صعود أمس — إشارة انعكاس هبوطي محتملة"}

    # 2أ) خط المخترق: بعد هبوط، شمعة خضراء تفتح تحت قاع أمس وتُغلق فوق منتصف جسمه (دون ابتلاعه)
    if (trend_down and prev_red and cur_green and o < prev["low"]
            and prev_mid < c < prev["open"] and body >= 0.5 * rng):
        return {"pattern": "خط المخترق", "status": "bull",
                "note": "فتحت تحت أمس وأغلقت فوق منتصفه — انعكاس صعودي محتمل"}

    # 2ب) الغيمة القاتمة: بعد صعود، شمعة حمراء تفتح فوق قمة أمس وتُغلق تحت منتصف جسمه
    if (trend_up and prev_green and cur_red and o > prev["high"]
            and prev["open"] < c < prev_mid and body >= 0.5 * rng):
        return {"pattern": "الغيمة القاتمة", "status": "bear",
                "note": "فتحت فوق أمس وأغلقت تحت منتصفه — انعكاس هبوطي محتمل"}

    # 3) المطرقة: جسم صغير أعلى الشمعة وظلّ سفلي طويل بعد هبوط قصير (رفض للنزول)
    if body > 0 and lower >= 2 * body and upper <= 0.15 * rng and body <= 0.35 * rng and trend_down:
        return {"pattern": "مطرقة", "status": "bull",
                "note": "ذيل سفلي طويل بعد هبوط — رفض للنزول وانعكاس صعودي محتمل"}

    # 4) الشهاب: جسم صغير أسفل الشمعة وظلّ علوي طويل بعد صعود قصير (رفض للصعود)
    if body > 0 and upper >= 2 * body and lower <= 0.15 * rng and body <= 0.35 * rng and trend_up:
        return {"pattern": "شهاب", "status": "bear",
                "note": "ذيل علوي طويل بعد صعود — رفض للارتفاع وانعكاس هبوطي محتمل"}

    # 5) الدوجي: جسم شبه معدوم (فتح ≈ إغلاق) = تردّد وتوازن قد يسبق انعكاساً
    if body <= 0.1 * rng:
        return {"pattern": "دوجي", "status": "neutral",
                "note": "فتح ≈ إغلاق — تردّد بين البائعين والمشترين قد يسبق انعكاساً"}

    return None


def resistance_warning(candles, near_pct=0.02):
    """قاعدة الانتظار (من الدورة): تنبيه حين يكون السعر ملاصقاً لمقاومة سابقة لم تُخترق بعد.

    الشراء على مستوى قمة/مقاومة سابقة يكثر عنده الارتداد — فالأفضل انتظار تأكيد الاختراق.
    نستخدم أقرب مقاومة فوق السعر (أقرب قمة فراكتالية أسبوعية/شهرية سابقة) — نفس حساب الهدف.

    يُرجع {"level", "pct", "tf"} إذا كانت المقاومة على بُعد ≤ near_pct فوق السعر، وإلا None.
    تنبيه معرفي وصفي فقط، لا توصية.
    """
    rows = _clean(candles)
    closes = [r["close"] for r in rows if r["close"] is not None]
    if len(closes) < 45:
        return None
    price = closes[-1]
    level, pct, tf = _overhead_target(candles, price)
    if level is None or pct is None:
        return None
    if pct <= near_pct:
        return {"level": level, "pct": pct, "tf": tf}
    return None


def atr(candles, period=14):
    """ATR (متوسط المدى الحقيقي، تمهيد Wilder) — مقياس تذبذب السهم بالدولار.

    يُرجع float أو None لو البيانات غير كافية.
    يُستخدم لحساب مستويات وقف الخسارة والهدف في تنبيهات تلغرام.
    """
    rows = [r for r in _clean(candles) if r["high"] is not None and r["low"] is not None]
    if len(rows) < period + 1:
        return None
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


# ── قراءة هيكل السوق (Market Structure / Smart Money Concepts) ────────────────
# نقرأ تتابع القمم والقيعان (HH/HL/LH/LL) لتحديد الاتجاه، ونكشف:
#   • BOS (اختراق الهيكل) = كسر مع الاتجاه ⇒ استمرار.
#   • CHOCH (تغيّر الهيكل) = أول كسر عكس الاتجاه ⇒ إنذار انعكاس.
#   • إعادة الاختبار (Retest) = عودة السعر لاختبار المستوى المكسور ثم صموده فوقه/تحته.
# التأكيد بإغلاق السعر خلف المستوى (لا بالفتيل) — أدقّ وأقلّ إشارات كاذبة.
# ⚠️ تعليمي وصفي فقط — لا توصية. (بحث متحقَّق 2026-08: FXOpen · DailyPriceAction.)

def volume_profile(candles, lookback=90, bins=24, value_area=0.70):
    """البروفايل الحجمي و POC (Point of Control) — بحث متحقَّق 2026-08 (TradingView · GoCharting).

    من الشموع اليومية (بلا داتا لحظية): نقسّم مدى السعر خلال `lookback` إلى شرائح،
    ونوزّع حجم كل شمعة على الشرائح التي يغطّيها مداها (high–low). ثم:
    - **POC** = الشريحة ذات أكبر حجم متراكم (أقوى دعم/مقاومة — تركّز اهتمام حقيقي).
    - **منطقة القيمة** (Value Area 70%): نوسّع من POC حتى 70% من الحجم → VAH (أعلى) وVAL (أدنى).
    يُرجع dict {poc, vah, val, price, position, dist_pct, in_value_area} أو None.
    position ∈ {above (POC دعم), below (POC مقاومة), at (عند POC)}. ⚠️ تعليمي لا توصية.
    """
    rows = [r for r in _clean(candles)
            if r["high"] is not None and r["low"] is not None and r["volume"]]
    if len(rows) < 20:
        return None
    seg = rows[-lookback:]
    lo = min(r["low"] for r in seg)
    hi = max(r["high"] for r in seg)
    if hi <= lo:
        return None
    bin_size = (hi - lo) / bins
    vol = [0.0] * bins
    for r in seg:
        b0 = max(0, min(bins - 1, int((r["low"] - lo) / bin_size)))
        b1 = max(0, min(bins - 1, int((r["high"] - lo) / bin_size)))
        share = r["volume"] / (b1 - b0 + 1)
        for b in range(b0, b1 + 1):
            vol[b] += share
    total = sum(vol)
    if total <= 0:
        return None

    poc_bin = max(range(bins), key=lambda b: vol[b])
    poc_price = lo + (poc_bin + 0.5) * bin_size

    # منطقة القيمة: نوسّع من POC للجار الأعلى حجماً حتى نبلغ 70% من الحجم
    acc = vol[poc_bin]
    low_b = high_b = poc_bin
    target = total * value_area
    while acc < target and (low_b > 0 or high_b < bins - 1):
        below = vol[low_b - 1] if low_b > 0 else -1.0
        above = vol[high_b + 1] if high_b < bins - 1 else -1.0
        if above >= below:
            high_b += 1
            acc += vol[high_b]
        else:
            low_b -= 1
            acc += vol[low_b]
    vah = lo + (high_b + 1) * bin_size
    val = lo + low_b * bin_size

    price = rows[-1]["close"]
    atr_v = atr(candles) or bin_size
    if abs(price - poc_price) <= max(0.5 * atr_v, bin_size):
        position = "at"
    elif price > poc_price:
        position = "above"
    else:
        position = "below"

    return {
        "poc": poc_price, "vah": vah, "val": val,
        "price": price, "position": position,
        "dist_pct": ((price - poc_price) / poc_price * 100.0) if poc_price else None,
        "in_value_area": val <= price <= vah,
    }


FIB_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]


def fibonacci_levels(candles, lookback=90):
    """مستويات فيبوناتشي للموجة الحالية (Fibonacci Retracement) — بحث متحقَّق 2026-08.

    نحدّد الموجة من أعلى قمة وأدنى قاع خلال آخر `lookback` جلسة، واتجاهها من أيّهما أحدث:
    - موجة صاعدة (قاع ثم قمة): المستوى = القمة − (القمة−القاع)×نسبة → **دعوم** محتملة عند التصحيح.
    - موجة هابطة (قمة ثم قاع): المستوى = القاع + (القمة−القاع)×نسبة → **مقاومات** محتملة.
    المستوى الذهبي 61.8% هو الأهمّ (نقطة قرار). المنطقة الذهبية = بين 50% و61.8%.
    يُرجع dict {direction, high, low, levels, nearest, at_level, in_golden, price} أو None.
    ⚠️ تعليمي وصفي فقط — لا توصية.
    """
    rows = [r for r in _clean(candles)
            if r["high"] is not None and r["low"] is not None and r["close"] is not None]
    if len(rows) < 20:
        return None
    seg = rows[-lookback:]
    highs = [r["high"] for r in seg]
    lows = [r["low"] for r in seg]
    ih = max(range(len(seg)), key=lambda i: highs[i])
    il = min(range(len(seg)), key=lambda i: lows[i])
    H, L = highs[ih], lows[il]
    span = H - L
    if span <= 0:
        return None
    up = ih > il                       # القمة أحدث ⇒ موجة صاعدة (ارتداد لأسفل نحو الدعوم)
    price = rows[-1]["close"]

    levels = []
    for r in FIB_RATIOS:
        lvl = (H - span * r) if up else (L + span * r)
        levels.append({"ratio": r, "pct": round(r * 100, 1), "price": lvl})

    nearest = min(levels, key=lambda x: abs(x["price"] - price))
    atr_v = atr(candles) or (span * 0.02)
    at_level = abs(nearest["price"] - price) <= max(0.5 * atr_v, price * 0.005)

    g50 = (H - span * 0.5) if up else (L + span * 0.5)
    g618 = (H - span * 0.618) if up else (L + span * 0.618)
    in_golden = min(g50, g618) <= price <= max(g50, g618)

    return {
        "direction": "up" if up else "down",
        "high": H, "low": L,
        "levels": levels,
        "nearest": nearest,
        "at_level": at_level,
        "in_golden": in_golden,
        "price": price,
    }


def _swing_points(rows, wing=2):
    """نقاط الارتكاز (قمم/قيعان فراكتالية) مرتّبة زمنياً بالتناوب.

    rows: شموع الأقدم أولاً فيها high/low. قمة فراكتالية = high أعلى من `wing` شموع
    قبلها وبعدها، وقاع = low أدنى منها. عند تتابع نفس النوع نُبقي الأكثر تطرّفاً
    (أعلى قمة / أدنى قاع) لنحافظ على تناوب قمة↔قاع.
    يُرجع [{type:'H'|'L', price, idx}] زمنياً.
    """
    n = len(rows)
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    raw = []
    for i in range(wing, n - wing):
        h = highs[i]
        if h is not None and all(highs[i - k] is not None and h > highs[i - k] for k in range(1, wing + 1)) \
           and all(highs[i + k] is not None and h >= highs[i + k] for k in range(1, wing + 1)):
            raw.append((i, "H", h))
        l = lows[i]
        if l is not None and all(lows[i - k] is not None and l < lows[i - k] for k in range(1, wing + 1)) \
           and all(lows[i + k] is not None and l <= lows[i + k] for k in range(1, wing + 1)):
            raw.append((i, "L", l))
    raw.sort(key=lambda x: x[0])
    pts = []
    for idx, typ, price in raw:
        if pts and pts[-1]["type"] == typ:
            if (typ == "H" and price >= pts[-1]["price"]) or (typ == "L" and price <= pts[-1]["price"]):
                pts[-1] = {"type": typ, "price": price, "idx": idx}
        else:
            pts.append({"type": typ, "price": price, "idx": idx})
    return pts


def _retest_state(rows, level, direction, atr_v, window=25, touch_bars=8):
    """كشف «إعادة الاختبار» المتقدّم (Break & Retest): اختراق ⇒ امتداد ⇒ عودة لاختبار
    المستوى المكسور ⇒ صمود/ارتداد. (بحث متحقَّق 2026-08: FXOpen · Capital.com · ACY.)

    القاعدة الاحترافية: لا يكفي أن السعر «قرب المستوى» — لا بد أن يكون قد **اخترق
    وامتدّ** فعلاً، ثم **عاد** لملامسة المستوى (صار دعماً/مقاومة)، وما زال **صامداً**،
    ويُفضّل أن يكون **ارتد** عنه (تأكيد).
    يُرجع 'confirmed' (ارتداد مؤكّد — أفضل دخول) · 'testing' (يختبر الآن) · None.
    """
    if not level or not atr_v or direction not in ("up", "down"):
        return None
    seg = rows[-window:] if len(rows) > window else rows
    if len(seg) < 6:
        return None
    highs = [r["high"] for r in seg]
    lows = [r["low"] for r in seg]
    closes = [r["close"] for r in seg]
    price = closes[-1]
    tol = 0.6 * atr_v
    near_band = 2.5 * atr_v   # يجب أن يكون السعر ما زال قريباً من المستوى (إعادة اختبار حالية لا منتهية)
    if direction == "up":
        if price < level or (price - level) > near_band:    # كسر تحته، أو ابتعد كثيراً ⇒ لا اختبار حالٍ
            return None
        pull_low = min(lows[-touch_bars:])
        if pull_low > level + tol:                          # لم يعُد للمس منطقة المستوى مؤخراً
            return None
        bounced = (price - pull_low) >= 0.5 * atr_v and closes[-1] >= closes[-2]
        return "confirmed" if bounced else "testing"
    else:
        if price > level or (level - price) > near_band:
            return None
        pull_high = max(highs[-touch_bars:])
        if pull_high < level - tol:
            return None
        bounced = (pull_high - price) >= 0.5 * atr_v and closes[-1] <= closes[-2]
        return "confirmed" if bounced else "testing"


def market_structure(candles, wing=2):
    """قراءة هيكل السوق من القمم/القيعان المتعاقبة (SMC).

    يُرجع dict {trend, event, event_dir, level, retest, status, label, event_label,
    last_high, last_low, swings} أو None لو البيانات غير كافية.
      trend ∈ {up, down, side} · event ∈ {BOS, CHOCH, None} · status ∈ {bull, bear, neutral}.
    """
    rows = [r for r in _clean(candles)
            if r["high"] is not None and r["low"] is not None and r["close"] is not None]
    if len(rows) < wing * 2 + 15:
        return None
    swings = _swing_points(rows, wing)
    if len(swings) < 4:
        return None

    # تصنيف كل ارتكاز مقارنةً بسابقه من نفس النوع
    prev_h = prev_l = None
    for s in swings:
        if s["type"] == "H":
            s["label"] = "HH" if (prev_h is not None and s["price"] > prev_h) else ("LH" if prev_h is not None else "H")
            prev_h = s["price"]
        else:
            s["label"] = "HL" if (prev_l is not None and s["price"] > prev_l) else ("LL" if prev_l is not None else "L")
            prev_l = s["price"]

    # الاتجاه من آخر 4 ارتكازات
    recent = [s["label"] for s in swings[-4:]]
    up = sum(1 for x in recent if x in ("HH", "HL"))
    down = sum(1 for x in recent if x in ("LH", "LL"))
    trend = "up" if up > down else ("down" if down > up else "side")

    price = rows[-1]["close"]
    last_high = next((s["price"] for s in reversed(swings) if s["type"] == "H"), None)
    last_low = next((s["price"] for s in reversed(swings) if s["type"] == "L"), None)

    # BOS/CHOCH بإغلاق السعر خلف آخر ارتكاز
    event = event_dir = level = None
    if trend == "up":
        if last_high is not None and price > last_high:
            event, event_dir, level = "BOS", "up", last_high        # استمرار صعود
        elif last_low is not None and price < last_low:
            event, event_dir, level = "CHOCH", "down", last_low     # انعكاس هبوطي
    elif trend == "down":
        if last_low is not None and price < last_low:
            event, event_dir, level = "BOS", "down", last_low        # استمرار هبوط
        elif last_high is not None and price > last_high:
            event, event_dir, level = "CHOCH", "up", last_high       # انعكاس صعودي
    else:  # عرضي: أول كسر واضح
        if last_high is not None and price > last_high:
            event, event_dir, level = "BOS", "up", last_high
        elif last_low is not None and price < last_low:
            event, event_dir, level = "BOS", "down", last_low

    # إعادة الاختبار الهيكلي (Break & Retest): في الاتجاه الصاعد = ارتداد من آخر قاع صاعد
    # (HL) كدعم؛ في الهابط = ارتداد من آخر قمة هابطة (LH) كمقاومة. الأصحّ هيكلياً من اختبار
    # القمة المخترَقة، لأنه يلتقط «اشترِ الانخفاض ضمن الاتجاه» بعد الامتداد والعودة.
    atr_v = atr(candles)
    if trend == "up" and last_low is not None:
        retest_state = _retest_state(rows, last_low, "up", atr_v)
    elif trend == "down" and last_high is not None:
        retest_state = _retest_state(rows, last_high, "down", atr_v)
    else:
        retest_state = None
    retest = retest_state is not None

    # الحالة اللونية + التسمية التعليمية
    status = "bull" if event_dir == "up" else ("bear" if event_dir == "down" else "neutral")
    trend_lbl = {"up": "صاعد", "down": "هابط", "side": "عرضي"}[trend]
    if event == "BOS":
        event_label = "استمرار صاعد (BOS)" if event_dir == "up" else "استمرار هابط (BOS)"
    elif event == "CHOCH":
        event_label = "إنذار انعكاس صاعد (CHOCH)" if event_dir == "up" else "إنذار انعكاس هبوطي (CHOCH)"
    else:
        event_label = "داخل الهيكل — لا اختراق ولا كسر بعد"

    return {
        "trend": trend,
        "trend_label": trend_lbl,
        "event": event,
        "event_dir": event_dir,
        "event_label": event_label,
        "level": level,
        "retest": retest,
        "retest_state": retest_state,   # 'confirmed' (ارتداد مؤكّد) · 'testing' (يختبر الآن) · None
        "status": status,
        "last_high": last_high,
        "last_low": last_low,
        "swings": [{"label": s["label"], "price": s["price"]} for s in swings[-6:]],
    }


# ==== تعدّد الفريمات (يومي/أسبوعي/شهري) — بالقمم والقيعان (هيكل السوق SMC) ====
# الاتجاه في كل فريم يُقرأ بتتابع القمم/القيعان (HH/HL/LH/LL) — الطريقة الصحيحة
# (داو/سمارت موني)، لا بالمتوسطات المتأخرة. الأسبوعي/الشهري يُبنيان بتجميع الشموع
# اليومية الكاملة (~5 سنوات، من نفس طلب FMP الواحد) فتكفي شموعهما للقمم والقيعان.
def _resample_full(candles, period):
    """يجمّع الشموع اليومية (صيغة FMP: الأحدث أولاً، date/open/high/low/close) إلى
    شموع أسبوعية ('W') أو شهرية ('M') بمعيار OHLC. يُرجع قائمة بنفس صيغة FMP (الأحدث
    أولاً) صالحة لتمريرها إلى market_structure/atr."""
    from datetime import datetime
    rows = []
    for c in candles or []:
        d = c.get("date")
        o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
        if not d or None in (h, l, cl):
            continue
        rows.append((str(d)[:10], o, h, l, cl))
    if not rows:
        return []
    rows.sort(key=lambda r: r[0])  # الأقدم أولاً
    buckets, order = {}, []
    for d, o, h, l, cl in rows:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        key = (dt.isocalendar()[0], dt.isocalendar()[1]) if period == "W" else (dt.year, dt.month)
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"date": d, "open": o, "high": h, "low": l, "close": cl}
            order.append(key)
        else:
            b["high"] = max(b["high"], h)
            b["low"] = min(b["low"], l)
            b["close"] = cl   # آخر إغلاق في الفترة (الصفوف مرتّبة الأقدم أولاً)
            b["date"] = d
    agg = [buckets[k] for k in order]  # الأقدم أولاً
    agg.reverse()                       # الأحدث أولاً (صيغة FMP)
    return agg


def multi_timeframe(candles, daily_structure=None):
    """حالة السهم على 3 فريمات (يومي/أسبوعي/شهري) + قوة الفريمات — بالقمم والقيعان.

    candles: التاريخ اليومي الكامل من FMP (الأحدث أولاً، ~5 سنوات) — يلزم لبناء الشهري.
    daily_structure: نتيجة market_structure لليومي إن حُسبت مسبقاً (لتطابق صفحة الهيكل).
    يُرجع dict {daily, weekly, monthly, up_count, down_count, strength} أو None.
    كل اتجاه ∈ {up, down, side, na}. (na = شموع الفريم غير كافية للقمم والقيعان.)
    """
    if not candles:
        return None

    def _trend(ms):
        # اتجاه الفريم يحترم الواقع الحالي: القمم/القيعان المؤكّدة تتأخّر لأن قمّة
        # الاختراق الجديدة لا تتأكّد إلا بعد شمعتين — فحين ينقلب الهيكل (CHOCH) نعرض
        # اتجاهه الجديد بدل الاتجاه القديم المتأخّر، ليطابق ما يراه المتداول على الشارت.
        if not ms:
            return "na"
        if ms.get("event") == "CHOCH" and ms.get("event_dir") in ("up", "down"):
            return ms["event_dir"]
        return ms["trend"]

    daily_ms = daily_structure if daily_structure is not None else market_structure(candles[:250])
    daily_t = _trend(daily_ms)
    weekly_t = _trend(market_structure(_resample_full(candles, "W")))
    monthly_t = _trend(market_structure(_resample_full(candles, "M")))

    trends = [daily_t, weekly_t, monthly_t]
    ups = sum(1 for t in trends if t == "up")
    downs = sum(1 for t in trends if t == "down")
    # قوة متناظرة: توافق صاعد ↑ · مختلط · توافق هابط ↓ (البيانات نفسها up/down_count)
    if ups >= 3:
        strength = "triple_up"
    elif downs >= 3:
        strength = "triple_down"
    elif ups == 2:
        strength = "double_up"
    elif downs == 2:
        strength = "double_down"
    else:
        strength = "mixed"
    return {"daily": daily_t, "weekly": weekly_t, "monthly": monthly_t,
            "up_count": ups, "down_count": downs, "strength": strength}
