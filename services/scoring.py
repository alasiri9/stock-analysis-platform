"""
scoring.py — حساب المؤشرات: Piotroski Score (9 نقاط) ثم Catalyst Score (0–100).

المبادئ المطبّقة:
- None ≠ 0 : أي نقطة لا تتوفّر بياناتها تكون passed = None (غير قابلة للحساب)،
  ولا تُحتسب صفراً. نوضّح للمستخدم كم نقطة أمكن حسابها فعلاً.
- لا أرقام ملفّقة : كل قيمة مشتقّة من القوائم المالية الحقيقية من FMP.
- شفافية : كل نقطة تُرجع تفصيلاً يوضّح كيف حُسبت.

Piotroski يقارن آخر سنة (index 0) بالسنة السابقة (index 1).
"""


def _safe_div(a, b):
    """قسمة آمنة: تُرجع None لو أي طرف None أو المقام صفر (تجنّب أرقام ملفّقة/أخطاء)."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _field(statement_list, index, key):
    """يقرأ حقلاً من قائمة مالية في سنة معيّنة بأمان.

    statement_list: قائمة سنوات (أو None). index: 0=الأحدث، 1=السابقة.
    يُرجع None لو القائمة ناقصة أو السنة غير موجودة أو الحقل غير موجود.
    """
    if not statement_list or len(statement_list) <= index:
        return None
    return statement_list[index].get(key)


def piotroski_score(financials):
    """يحسب Piotroski (0–9) من ناتج fmp_client.get_financials(ticker).

    financials = {"income": [...], "balance": [...], "cashflow": [...]}
    كل قائمة: الأحدث أولاً (index 0)، والسنة السابقة (index 1).

    يُرجع dict:
    {
      "score": عدد النقاط المحقّقة (int) من النقاط القابلة للحساب,
      "computable": كم نقطة أمكن حسابها (من 9),
      "components": قائمة 9 عناصر، كل عنصر {n, name, passed, detail}
    }
    حيث passed: True (محقّقة) / False (غير محقّقة) / None (بيانات ناقصة).
    """
    inc = financials.get("income") if financials else None
    bal = financials.get("balance") if financials else None
    cf = financials.get("cashflow") if financials else None

    # --- القيم الخام للسنتين ---
    net_income0 = _field(inc, 0, "netIncome")
    net_income1 = _field(inc, 1, "netIncome")
    revenue0 = _field(inc, 0, "revenue")
    revenue1 = _field(inc, 1, "revenue")
    gross0 = _field(inc, 0, "grossProfit")
    gross1 = _field(inc, 1, "grossProfit")
    shares0 = _field(inc, 0, "weightedAverageShsOut")
    shares1 = _field(inc, 1, "weightedAverageShsOut")

    assets0 = _field(bal, 0, "totalAssets")
    assets1 = _field(bal, 1, "totalAssets")
    cur_assets0 = _field(bal, 0, "totalCurrentAssets")
    cur_assets1 = _field(bal, 1, "totalCurrentAssets")
    cur_liab0 = _field(bal, 0, "totalCurrentLiabilities")
    cur_liab1 = _field(bal, 1, "totalCurrentLiabilities")
    ltdebt0 = _field(bal, 0, "longTermDebt")
    ltdebt1 = _field(bal, 1, "longTermDebt")

    cfo0 = _field(cf, 0, "operatingCashFlow")

    # --- نسب مشتقّة ---
    roa0 = _safe_div(net_income0, assets0)   # العائد على الأصول (كسر: 0.25 = 25%)
    roa1 = _safe_div(net_income1, assets1)
    gm0 = _safe_div(gross0, revenue0)        # هامش إجمالي (كسر)
    gm1 = _safe_div(gross1, revenue1)
    cr0 = _safe_div(cur_assets0, cur_liab0)  # نسبة السيولة الجارية
    cr1 = _safe_div(cur_assets1, cur_liab1)
    lev0 = _safe_div(ltdebt0, assets0)       # الرافعة = دين طويل/أصول
    lev1 = _safe_div(ltdebt1, assets1)
    at0 = _safe_div(revenue0, assets0)       # دوران الأصول
    at1 = _safe_div(revenue1, assets1)
    cfo_assets0 = _safe_div(cfo0, assets0)

    components = []

    def add(n, name, passed, detail):
        components.append({"n": n, "name": name, "passed": passed, "detail": detail})

    # 1) ROA > 0
    add(1, "ROA > 0",
        (roa0 > 0) if roa0 is not None else None,
        f"ROA = صافي الربح/الأصول = {roa0:.4f}" if roa0 is not None else "بيانات ناقصة")

    # 2) CFO > 0  (التدفق النقدي التشغيلي موجب)
    add(2, "CFO > 0",
        (cfo0 > 0) if cfo0 is not None else None,
        f"CFO = {cfo0:,.0f}" if cfo0 is not None else "بيانات ناقصة")

    # 3) ΔROA > 0  (تحسّن العائد على الأصول)
    delta_roa_ok = (roa0 > roa1) if (roa0 is not None and roa1 is not None) else None
    add(3, "ΔROA > 0",
        delta_roa_ok,
        f"ROA: {roa1:.4f} → {roa0:.4f}" if delta_roa_ok is not None else "بيانات ناقصة")

    # 4) Accruals = CFO/Assets − ROA < 0  (جودة الأرباح: تدفق نقدي يفوق الربح المحاسبي)
    if cfo_assets0 is not None and roa0 is not None:
        accr = cfo_assets0 - roa0
        add(4, "Accruals < 0", accr < 0, f"CFO/Assets − ROA = {accr:.4f}")
    else:
        add(4, "Accruals < 0", None, "بيانات ناقصة")

    # 5) ΔLeverage < 0  (انخفاض الرافعة المالية = دين أقل نسبياً)
    delta_lev_ok = (lev0 < lev1) if (lev0 is not None and lev1 is not None) else None
    add(5, "ΔLeverage < 0",
        delta_lev_ok,
        f"الرافعة: {lev1:.4f} → {lev0:.4f}" if delta_lev_ok is not None else "بيانات ناقصة")

    # 6) ΔLiquidity > 0  (تحسّن نسبة السيولة الجارية)
    delta_liq_ok = (cr0 > cr1) if (cr0 is not None and cr1 is not None) else None
    add(6, "ΔLiquidity > 0",
        delta_liq_ok,
        f"نسبة السيولة: {cr1:.2f} → {cr0:.2f}" if delta_liq_ok is not None else "بيانات ناقصة")

    # 7) لا إصدار أسهم جديدة  (عدد الأسهم لم يزد)
    no_dilution = (shares0 <= shares1) if (shares0 is not None and shares1 is not None) else None
    add(7, "لا إصدار أسهم جديدة",
        no_dilution,
        f"الأسهم: {shares1:,.0f} → {shares0:,.0f}" if no_dilution is not None else "بيانات ناقصة")

    # 8) ΔGross Margin > 0  (تحسّن الهامش الإجمالي)
    delta_gm_ok = (gm0 > gm1) if (gm0 is not None and gm1 is not None) else None
    add(8, "ΔGross Margin > 0",
        delta_gm_ok,
        f"الهامش الإجمالي: {gm1:.4f} → {gm0:.4f}" if delta_gm_ok is not None else "بيانات ناقصة")

    # 9) ΔAsset Turnover > 0  (تحسّن كفاءة استخدام الأصول)
    delta_at_ok = (at0 > at1) if (at0 is not None and at1 is not None) else None
    add(9, "ΔAsset Turnover > 0",
        delta_at_ok,
        f"دوران الأصول: {at1:.4f} → {at0:.4f}" if delta_at_ok is not None else "بيانات ناقصة")

    score = sum(1 for c in components if c["passed"] is True)
    computable = sum(1 for c in components if c["passed"] is not None)

    return {"score": score, "computable": computable, "components": components}


def _scale(value, low, high):
    """يحوّل قيمة إلى درجة 0–100 خطّياً بين حدّين.

    - value <= low  → 0
    - value >= high → 100
    - بينهما        → تدرّج خطّي
    - value is None → None (لا نخترع صفراً)
    """
    if value is None:
        return None
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    return (value - low) / (high - low) * 100.0


def catalyst_score(financials):
    """يحسب Catalyst Score (0–100) من ناتج fmp_client.get_financials(ticker).

    ⚠️ ملاحظة جوهرية: الأوزان والحدود أدناه افتراضية وغير مُختبرة تاريخياً
    (حسب المواصفات) — يجب عرض هذه الملاحظة في الواجهة.

    المكوّنات وأوزانها:
    - نمو الإيرادات YoY   (25%) : 0→0% ، 100→20%
    - نمو صافي الأرباح YoY (25%) : 0→0% ، 100→20%
    - ROE                 (20%) : 0→0% ، 100→25%
    - هامش التشغيل         (15%) : 0→0% ، 100→25%
    - ROA                 (15%) : 0→0% ، 100→15%

    None ≠ 0 : أي مكوّن بياناته ناقصة يُستبعد، ونعيد توزيع وزنه على المتوفّر.
    يُرجع dict: {score, components:[{name, weight, points, detail}], computable_weight}.
    score = None لو لا يمكن حساب أي مكوّن.
    """
    inc = financials.get("income") if financials else None
    bal = financials.get("balance") if financials else None

    net_income0 = _field(inc, 0, "netIncome")
    net_income1 = _field(inc, 1, "netIncome")
    revenue0 = _field(inc, 0, "revenue")
    revenue1 = _field(inc, 1, "revenue")
    op_income0 = _field(inc, 0, "operatingIncome")
    assets0 = _field(bal, 0, "totalAssets")
    equity0 = _field(bal, 0, "totalStockholdersEquity")

    # نمو الإيرادات YoY (كسر: 0.2 = 20%) — يحتاج إيرادات السنة السابقة موجبة
    if revenue0 is not None and revenue1 is not None and revenue1 > 0:
        rev_growth = (revenue0 - revenue1) / revenue1
    else:
        rev_growth = None

    # نمو صافي الأرباح YoY — يحتاج ربح السنة السابقة موجب (وإلا النسبة مضلّلة)
    if net_income0 is not None and net_income1 is not None and net_income1 > 0:
        ni_growth = (net_income0 - net_income1) / net_income1
    else:
        ni_growth = None

    roe = _safe_div(net_income0, equity0)        # كسر
    op_margin = _safe_div(op_income0, revenue0)  # كسر
    roa = _safe_div(net_income0, assets0)        # كسر

    # (المكوّن، الوزن، الدرجة 0–100، تفصيل)
    specs = [
        ("نمو الإيرادات YoY", 0.25, _scale(rev_growth, 0.0, 0.20), rev_growth, "نمو"),
        ("نمو صافي الأرباح YoY", 0.25, _scale(ni_growth, 0.0, 0.20), ni_growth, "نمو"),
        ("ROE", 0.20, _scale(roe, 0.0, 0.25), roe, "نسبة"),
        ("هامش التشغيل", 0.15, _scale(op_margin, 0.0, 0.25), op_margin, "نسبة"),
        ("ROA", 0.15, _scale(roa, 0.0, 0.15), roa, "نسبة"),
    ]

    components = []
    weighted_sum = 0.0
    computable_weight = 0.0
    for name, weight, points, raw, kind in specs:
        if raw is not None:
            detail = f"{raw * 100:.1f}%"
        else:
            detail = "بيانات ناقصة"
        components.append({"name": name, "weight": weight, "points": points, "detail": detail})
        if points is not None:
            weighted_sum += weight * points
            computable_weight += weight

    score = (weighted_sum / computable_weight) if computable_weight > 0 else None

    return {
        "score": score,
        "computable_weight": computable_weight,
        "components": components,
    }


def compute_atr(candles, period=14):
    """يحسب ATR (متوسط المدى الحقيقي) من شموع يومية.

    candles: قائمة أيام (الأحدث أولاً) كل يوم فيه high/low/close (من FMP).
    True Range لليوم = أكبر من:
      (high − low) ، |high − إغلاق أمس| ، |low − إغلاق أمس|
    ATR = متوسط آخر `period` قيمة TR.
    يُرجع float أو None لو البيانات غير كافية (لا نخترع رقماً).
    """
    if not candles or len(candles) < period + 1:
        return None

    # نرتّب من الأقدم للأحدث لحساب الفروق الزمنية الصحيحة
    rows = list(reversed(candles))
    trs = []
    for i in range(1, len(rows)):
        high = rows[i].get("high")
        low = rows[i].get("low")
        prev_close = rows[i - 1].get("close")
        if high is None or low is None or prev_close is None:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    if len(trs) < period:
        return None
    # متوسط بسيط لآخر `period` قيمة
    return sum(trs[-period:]) / period


def atr_trade_plan(current_price, candles, period=14, stop_mult=1.5, target_mult=3.0):
    """يبني خطة تداول تعليمية (دخول/وقف/هدف) من ATR.

    ⚠️ تعليمي فقط، ليس توصية. المضاعفات افتراضية وقابلة للتعديل.
    - الدخول = السعر الحالي
    - الوقف  = الدخول − stop_mult × ATR
    - الهدف  = الدخول + target_mult × ATR
    - نسبة العائد/المخاطرة = (الهدف−الدخول) / (الدخول−الوقف)

    يُرجع dict أو None لو تعذّر حساب ATR أو غاب السعر.
    """
    if current_price is None:
        return None
    atr = compute_atr(candles, period)
    if atr is None:
        return None

    stop = current_price - stop_mult * atr
    target = current_price + target_mult * atr
    risk = current_price - stop
    reward = target - current_price
    rr = (reward / risk) if risk > 0 else None

    return {
        "atr": atr,
        "period": period,
        "entry": current_price,
        "stop": stop,
        "target": target,
        "stop_mult": stop_mult,
        "target_mult": target_mult,
        "risk_reward": rr,
    }


# ----------------------------------------------------------------------------
# اختبار يدوي: py services/scoring.py   (يجلب AAPL ويعرض النقاط التسع)
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    # نضيف مجلد المشروع للمسار حتى نقدر نستورد fmp_client
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services import fmp_client

    ticker = "AAPL"
    print(f"=== Piotroski Score لـ {ticker} ===\n")

    financials = fmp_client.get_financials(ticker)
    result = piotroski_score(financials)

    icon = {True: "✅", False: "❌", None: "—"}
    for c in result["components"]:
        print(f"{icon[c['passed']]}  [{c['n']}] {c['name']}")
        print(f"      {c['detail']}")

    print(f"\nالنتيجة: {result['score']} / 9", end="")
    if result["computable"] < 9:
        print(f"  (أمكن حساب {result['computable']} نقاط فقط — الباقي بيانات ناقصة)")
    else:
        print()

    # --- Catalyst Score ---
    print(f"\n=== Catalyst Score لـ {ticker} (أوزان افتراضية غير مختبرة) ===\n")
    cat = catalyst_score(financials)
    for c in cat["components"]:
        pts = "—" if c["points"] is None else f"{c['points']:.0f}/100"
        print(f"  {c['name']} (وزن {c['weight']*100:.0f}%): {c['detail']}  →  {pts}")
    if cat["score"] is None:
        print("\nالنتيجة: — (لا تتوفّر بيانات كافية)")
    else:
        print(f"\nالنتيجة: {cat['score']:.0f} / 100", end="")
        if cat["computable_weight"] < 1.0:
            print(f"  (محسوبة من {cat['computable_weight']*100:.0f}% من الأوزان فقط)")
        else:
            print()
