"""
analysis.py — طبقة التجميع: تبني تقرير سهم كامل من المصادر + المؤشرات.

تجمع في مكان واحد:
- السعر والتغيّر (من FMP quote)
- الملف التعريفي (القطاع، الصناعة)
- المقاييس المالية (ROE, ROA, هامش تشغيل, هامش إجمالي, P/E, PEG)
- Piotroski و Catalyst (من scoring)
- درجة ثقة البيانات (كم مصدر أكّد السعر)

المبادئ:
- None ≠ 0 : أي مقياس لا تتوفّر مدخلاته يكون None، والواجهة تعرضه "—".
- لا أرقام ملفّقة : كل قيمة مشتقّة من بيانات حقيقية.
- Unit Guard : النسب تُخزّن ككسر (0.31) ونحوّلها لنسبة مئوية عند العرض فقط.

ملاحظات على ما لم يُبنَ بعد (يحتاج مصادر إضافية):
- خطة ATR التداولية   → تحتاج شموع الأسعار من Finnhub (لاحقاً).
- معاملات المطلعين     → تحتاج SEC EDGAR (لاحقاً).
"""

import re

from services import fmp_client
from services import finnhub_client
from services import scoring
from services import edgar_client
from services import indicators


def _pct(fraction):
    """يحوّل كسراً (0.312) إلى نسبة مئوية (31.2) للعرض. None تبقى None."""
    return None if fraction is None else fraction * 100.0


def price_chart(candles, days=90, width=900, height=280, pad=10):
    """يبني بيانات شارت كبير لمسار السعر (SVG polyline + تسميات) من شموع FMP.

    يُرجع dict: {points, area_points, width, height, high, low, first_date, last_date,
    first_price, last_price, change_pct, up, days} أو None لو البيانات غير كافية.
    None ≠ 0 : أقل من 5 أيام صالحة ⇒ لا شارت (بدل خط مضلّل).
    """
    rows = []
    for r in reversed(candles or []):  # FMP يُرجع الأحدث أولاً
        if r.get("close") is not None and r.get("date"):
            rows.append({"close": r["close"], "date": r["date"][:10]})
    rows = rows[-days:]
    if len(rows) < 5:
        return None

    closes = [r["close"] for r in rows]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    n = len(closes)
    step = (width - 2 * pad) / (n - 1)

    pts = []
    for i, p in enumerate(closes):
        x = pad + i * step
        y = pad + (height - 2 * pad) * (1 - (p - lo) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    points = " ".join(pts)
    # مضلّع مغلق لتعبئة المساحة تحت الخط (تدرّج خفيف)
    area_points = f"{pad:.1f},{height - pad} " + points + f" {pad + (n - 1) * step:.1f},{height - pad}"

    change_pct = (closes[-1] - closes[0]) / closes[0] * 100.0 if closes[0] else None
    return {
        "points": points,
        "area_points": area_points,
        "width": width,
        "height": height,
        "high": hi,
        "low": lo,
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "first_price": closes[0],
        "last_price": closes[-1],
        "change_pct": change_pct,
        "up": closes[-1] >= closes[0],
        "days": n,
    }


def build_quick_summary(ticker):
    """ملخّص خفيف وسريع للمقارنة: سعر + مقاييس + Piotroski + Catalyst.

    يتجاهل EDGAR و ATR و Finnhub (غير لازمة للمقارنة) ليكون أسرع وأخفّ على الـ API.
    يُرجع dict أو None لو لم يُعثر على السهم.
    """
    ticker = ticker.upper().strip()
    quote = fmp_client.get_quote(ticker)
    financials = fmp_client.get_financials(ticker)
    if not quote and not financials:
        return None

    inc = financials.get("income") if financials else None
    bal = financials.get("balance") if financials else None
    net_income = inc[0].get("netIncome") if inc else None
    revenue = inc[0].get("revenue") if inc else None
    gross = inc[0].get("grossProfit") if inc else None
    op_income = inc[0].get("operatingIncome") if inc else None
    eps = inc[0].get("eps") if inc else None
    assets = bal[0].get("totalAssets") if bal else None
    equity = bal[0].get("totalStockholdersEquity") if bal else None
    price = quote.get("price") if quote else None

    return {
        "ticker": ticker,
        "name": quote.get("name") if quote else None,
        "price": price,
        "change_percent": quote.get("change_percent") if quote else None,
        "metrics": {
            "roe": _pct(scoring._safe_div(net_income, equity)),
            "roa": _pct(scoring._safe_div(net_income, assets)),
            "op_margin": _pct(scoring._safe_div(op_income, revenue)),
            "gross_margin": _pct(scoring._safe_div(gross, revenue)),
            "pe": scoring._safe_div(price, eps) if (eps not in (None, 0)) else None,
        },
        "piotroski": scoring.piotroski_score(financials),
        "catalyst": scoring.catalyst_score(financials),
    }


def smart_summary(report, scan=None):
    """يبني ملخّصاً ذكياً بالعربي البسيط من بيانات التقرير: نقاط قوة + تنبيهات.

    وصفي تعليمي فقط (لا توصية). يجمّع المؤشرات المتفرّقة في جُمل يفهمها المبتدئ.
    report: ناتج build_stock_report. scan: سجل الماسح لنفس السهم (أو None).
    يُرجع {"strengths": [...], "cautions": [...]} — قد تكون القائمتان فارغتين.
    """
    strengths, cautions = [], []

    # الجودة المالية (Piotroski)
    p = (report.get("piotroski") or {}).get("score")
    if p is not None:
        if p >= 8:
            strengths.append(f"جودة مالية ممتازة (Piotroski {p}/9)")
        elif p >= 6:
            strengths.append(f"جودة مالية جيدة (Piotroski {p}/9)")
        elif p <= 3:
            cautions.append(f"جودة مالية ضعيفة (Piotroski {p}/9)")

    # النمو (Catalyst)
    c = (report.get("catalyst") or {}).get("score")
    if c is not None:
        if c >= 80:
            strengths.append(f"نمو قوي (درجة النمو {c:.0f} من 100)")
        elif c >= 40:
            strengths.append(f"نمو متوسط (درجة النمو {c:.0f} من 100)")
        else:
            cautions.append(f"نمو ضعيف (درجة النمو {c:.0f} من 100)")

    # الاتجاه والزخم والاختراق (من المؤشرات الفنية)
    inds = {b.get("label"): b for b in (report.get("indicators") or [])}

    def is_bull(lbl):
        b = inds.get(lbl)
        return bool(b and b.get("status") == "bull")

    if is_bull("EMA") and is_bull("MACD"):
        strengths.append("اتجاه صاعد بزخم إيجابي (EMA + MACD)")
    elif is_bull("EMA"):
        strengths.append("اتجاه عام صاعد (EMA)")
    if is_bull("اختراق"):
        strengths.append("اخترق قمة سعرية حديثة")

    # RSI — شراء/بيع زائد
    rsi = inds.get("RSI")
    if rsi and rsi.get("value"):
        m = re.search(r"\d+", str(rsi.get("value")))
        if m:
            rv = int(m.group())
            if rv >= 70:
                cautions.append(f"RSI في منطقة الشراء الزائد ({rv}) — احتمال تصحيح")
            elif rv <= 30:
                strengths.append(f"RSI في منطقة البيع الزائد ({rv}) — احتمال ارتداد")

    # من سجل الماسح (متوفّر لأسهم قائمة المنصة فقط)
    if scan:
        rs = scan.get("rel_strength")
        if rs is not None and rs > 0:
            strengths.append(f"أقوى من السوق (+{rs:.0f}% مقابل المؤشر)")
        elif rs is not None and rs < 0:
            cautions.append(f"أضعف من السوق ({rs:.0f}% مقابل المؤشر)")
        mf = (scan.get("money_flow") or {}).get("status")
        if mf == "bull":
            strengths.append("سيولة داخلة (تجميع)")
        elif mf == "bear":
            cautions.append("سيولة خارجة (تصريف)")
        dte = scan.get("days_to_earnings")
        if dte is not None and dte <= 7:
            when = "اليوم" if dte == 0 else ("غداً" if dte == 1 else f"بعد {dte} أيام")
            cautions.append(f"موعد الأرباح قريب ({when}) — توقّع تذبذباً مرتفعاً")

    # التقييم (P/E مرتفع)
    pe = (report.get("metrics") or {}).get("pe")
    if pe is not None and pe > 40:
        cautions.append(f"تقييم مرتفع نسبياً (P/E {pe:.0f})")

    return {"strengths": strengths, "cautions": cautions}


def build_stock_report(ticker):
    """يبني تقرير سهم كامل. يُرجع dict جاهز للقالب، أو None لو فشل جلب الأساسيات."""
    ticker = ticker.upper().strip()

    quote = fmp_client.get_quote(ticker)
    profile = fmp_client.get_profile(ticker)
    financials = fmp_client.get_financials(ticker)

    # لو ما توفّر لا سعر ولا ملف تعريفي، نعتبر السهم غير موجود/غير متاح
    if not quote and not profile:
        return None

    inc = financials.get("income") if financials else None
    bal = financials.get("balance") if financials else None

    # --- القيم الخام لآخر سنة ---
    net_income = inc[0].get("netIncome") if inc else None
    revenue = inc[0].get("revenue") if inc else None
    gross = inc[0].get("grossProfit") if inc else None
    op_income = inc[0].get("operatingIncome") if inc else None
    eps = inc[0].get("eps") if inc else None
    assets = bal[0].get("totalAssets") if bal else None
    equity = bal[0].get("totalStockholdersEquity") if bal else None

    # نمو الأرباح (لأجل PEG) — يحتاج سنتين وربحاً سابقاً موجباً
    ni_prev = inc[1].get("netIncome") if (inc and len(inc) > 1) else None
    if net_income is not None and ni_prev is not None and ni_prev > 0:
        earnings_growth_pct = (net_income - ni_prev) / ni_prev * 100.0
    else:
        earnings_growth_pct = None

    # --- المقاييس (تُخزّن ككسر، نحوّلها % عند العرض) ---
    roe = scoring._safe_div(net_income, equity)
    roa = scoring._safe_div(net_income, assets)
    op_margin = scoring._safe_div(op_income, revenue)
    gross_margin = scoring._safe_div(gross, revenue)

    # P/E = السعر / ربحية السهم
    price = quote.get("price") if quote else (profile.get("price") if profile else None)
    pe = scoring._safe_div(price, eps) if (eps is not None and eps != 0) else None

    # PEG = P/E مقسوم على نسبة نمو الأرباح (%) — تعليمي، يُحسب فقط لو النمو موجب
    if pe is not None and earnings_growth_pct is not None and earnings_growth_pct > 0:
        peg = pe / earnings_growth_pct
    else:
        peg = None

    # --- درجة ثقة البيانات: كم مصدر أكّد السعر (حالياً FMP فقط؛ يزيد مع Finnhub) ---
    price_sources = 0
    if quote and quote.get("price") is not None:
        price_sources += 1

    # --- سعر تأكيد ثانٍ من Finnhub (يرفع ثقة البيانات) ---
    finnhub_quote = finnhub_client.get_quote(ticker)
    finnhub_price = finnhub_quote.get("price") if finnhub_quote else None
    if finnhub_price is not None:
        price_sources += 1

    # --- خطة ATR + المؤشرات الفنية + الشارت من أسعار FMP التاريخية (جلب واحد) ---
    # 250 يوماً (نفس الفحص) ليكفي لحساب التقاطع الذهبي SMA50/SMA200 — بلا طلب إضافي.
    try:
        candles = fmp_client.get_historical_prices(ticker, limit=250)
        atr_plan = scoring.atr_trade_plan(price, candles)
        tech_indicators = indicators.build_indicators(candles)
        chart = price_chart(candles)  # نفس الشموع المجلوبة — بلا استدعاء إضافي
    except Exception as e:  # noqa: BLE001
        print(f"[analysis] تعذّر حساب ATR/المؤشرات لـ {ticker}: {e}")
        atr_plan = None
        tech_indicators = []
        chart = None

    # --- معاملات المطلعين من SEC EDGAR (لا تكسر الصفحة لو فشلت) ---
    try:
        insider_trades = edgar_client.get_insider_transactions(ticker)
    except Exception as e:  # noqa: BLE001 — أي خطأ هنا لا يجب أن يُسقط التقرير
        print(f"[analysis] تعذّر جلب معاملات المطلعين لـ {ticker}: {e}")
        insider_trades = []

    metrics = {
        "roe": _pct(roe),                  # %
        "roa": _pct(roa),                  # %
        "op_margin": _pct(op_margin),      # %
        "gross_margin": _pct(gross_margin),# %
        "pe": pe,                          # مضاعف (رقم عادي)
        "peg": peg,                        # نسبة (رقم عادي)
    }

    return {
        "ticker": ticker,
        "name": (profile.get("name") if profile else None) or (quote.get("name") if quote else None),
        "sector": profile.get("sector") if profile else None,
        "industry": profile.get("industry") if profile else None,
        "price": price,                                       # دولار
        "change": quote.get("change") if quote else None,    # دولار
        "change_percent": quote.get("change_percent") if quote else None,  # % جاهزة
        "market_cap": (quote.get("market_cap") if quote else None) or (profile.get("market_cap") if profile else None),
        "metrics": metrics,
        "piotroski": scoring.piotroski_score(financials),
        "catalyst": scoring.catalyst_score(financials),
        "price_sources": price_sources,   # درجة ثقة مبدئية (عدد المصادر المؤكِّدة للسعر)
        "insider_trades": insider_trades,  # من SEC EDGAR (قد تكون قائمة فارغة)
        "finnhub_price": finnhub_price,    # سعر تأكيد ثانٍ (أو None)
        "atr_plan": atr_plan,              # خطة ATR التعليمية (أو None)
        "indicators": tech_indicators,     # مؤشرات فنية (قد تكون قائمة فارغة)
        "chart": chart,                    # بيانات شارت مسار السعر (أو None)
    }
