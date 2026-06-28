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

from services import fmp_client
from services import finnhub_client
from services import scoring
from services import edgar_client


def _pct(fraction):
    """يحوّل كسراً (0.312) إلى نسبة مئوية (31.2) للعرض. None تبقى None."""
    return None if fraction is None else fraction * 100.0


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

    # --- خطة ATR التداولية (تعليمية) من أسعار FMP التاريخية ---
    try:
        candles = fmp_client.get_historical_prices(ticker, limit=60)
        atr_plan = scoring.atr_trade_plan(price, candles)
    except Exception as e:  # noqa: BLE001
        print(f"[analysis] تعذّر حساب خطة ATR لـ {ticker}: {e}")
        atr_plan = None

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
    }
