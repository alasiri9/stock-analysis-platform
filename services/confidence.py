"""
confidence.py — درجة الثقة بالبيانات (Data Confidence) — دالة نقية F2 (PHASE 6).

⚠️ الغرض: تقيس **مدى اكتمال وحداثة البيانات** التي بُني عليها تحليل السهم — لا جودة الفرصة.
هذا يختلف جوهرياً عن `algomatix_score` في `screener.py`:
- `algomatix_score` يقرأ **قِيَم** الحقول (اتجاه صاعد/هابط، درجة نمو مرتفعة/منخفضة) ليحكم على الفرصة.
- `data_confidence` يقرأ **توفّر وصلاحية** الحقول (هل حُسبت فعلاً؟ هل حديثة؟) ليحكم على موثوقية البيانات.
فقد يكون هيكل السوق «هابطاً» (يخفض Algomatix) لكنه «متوفّر ومحسوب» (يرفع الثقة) — بُعدان متعامدان.

العوامل السبعة (مجموع الأوزان = 100)، كلٌّ مربوط بحقل مصدر مستقل بعتبة كفاية مستقلة:
- اكتمال النمو (Catalyst)      20 : record["catalyst"] (0–100) / record["catalyst_complete"] (bool)
- قابلية حساب Piotroski        20 : record["piotroski_computable"] (عدد صحيح 0–9 حصراً)
- توفّر المؤشرات الفنية        20 : record["indicators"] (شارات محسوبة صالحة — تستبعد «تراكم»/OBV منعاً للتكرار مع Flow)
- توفّر هيكل السوق             15 : record["structure"] (ناتج market_structure — يثبت الحساب بـtrend+status)
- توفّر الفريمات الإضافية      10 : record["frames"] (الأسبوعي والشهري فقط — اليومي ممثّل بعوامل فنية أخرى)
- توفّر تدفق السيولة            5 : record["money_flow"] (ناتج money_flow — يثبت الحساب بـscore+status)
- الحداثة                      10 : record["analysis_date"] / record["analysis_close"] (مرساة EOD)

عقود المصادر المتحقَّق منها (من الكود الفعلي، بلا تعديله):
- scoring.catalyst_score() ⇒ {"score": float|None في [0,100], "complete": bool, ...}. score=None لو لا مكوّن يُحسب.
- indicators.market_structure() ⇒ dict فيه "trend"∈{up,down,side} و"status"∈{bull,bear,neutral} (أو None).
- indicators.money_flow() ⇒ dict فيه "score" (عدد) و"status"∈{bull,bear,neutral} (أو None).
- indicators.build_indicators() ⇒ شارات {"label","value"(≠None),"status"∈{bull,bear,neutral}}.
لذلك لا يكفي «القاموس غير فارغ»: نتحقّق من **المفاتيح والقيم الدنيا التي تثبت أن الحساب تمّ فعلاً**.

النطاقات: عالية 80–100 · متوسطة 50–79 · منخفضة أقل من 50.

سقوف حاسمة (تمنع ثقة عالية فوق أساس مكسور أو غير قابل للتحقّق أو ناقص ركيزةً جوهرية):
- تاريخ التحليل مفقود/غير صالح/مستقبلي، أو سعر الأساس (analysis_close) غير عدد منتهٍ موجب (يرفض NaN/±∞/≤0) → ≤49 (منخفضة).
- تعذّر التحقّق من الحداثة (لا expected_session_date صالحة)، أو تاريخ أقدم من الجلسة المتوقّعة → ≤79 (متوسطة).
- **بوابة الركائز الجوهرية**: أي عامل جوهري (Catalyst · Piotroski · المؤشرات الفنية) دون 50% من وزنه
  الأقصى (10/20) → ≤79 (متوسطة). لا يُعاد توزيع الأوزان ولا تُرفع درجة أي عامل؛ raw_score يبقى كما هو.
- عند اجتماع سقفين يُطبَّق **الأشدّ** (49 يغلب 79)، وتُجمع أسباب المتوسط في سجل Medium واحد واضح.

الدالة **نقية**: لا قاعدة بيانات، لا استدعاء API، لا Flask، لا live_price، لا تعدّل record،
حتمية (نفس الدخل ⇒ نفس الخرج)، وخرجها قابل للتسلسل JSON، ولا ترفع استثناءً على المدخلات التالفة.
"""

import math
from datetime import date, datetime

SCHEMA_VERSION = 1

# أوزان العوامل (المجموع = 100). كل عامل مربوط بحقل مصدر مستقل (انظر NON-OVERLAP في الوثائق).
FACTOR_WEIGHTS = {
    "catalyst_completeness": 20,
    "piotroski_computability": 20,
    "technical_indicators": 20,
    "structure_availability": 15,
    "frames_availability": 10,
    "flow_availability": 5,
    "freshness": 10,
}

# تسميات عربية للعرض/الوصف (لا تُغيّر المنطق).
FACTOR_LABELS = {
    "catalyst_completeness": "اكتمال درجة النمو (Catalyst)",
    "piotroski_computability": "قابلية حساب Piotroski",
    "technical_indicators": "توفّر المؤشرات الفنية",
    "structure_availability": "توفّر هيكل السوق",
    "frames_availability": "توفّر الفريمات الإضافية (الأسبوعي والشهري)",
    "flow_availability": "توفّر تدفق السيولة",
    "freshness": "حداثة البيانات",
}

# المؤشرات الفنية المسموح احتسابها في Data Confidence (قائمة صريحة ثابتة).
# هذه شارات build_indicators الاثنتا عشرة **ما عدا «تراكم»** (OBV): OBV يُحتسب حصراً في عامل
# flow_availability عبر money_flow، فإدراجه هنا أيضاً يُكرّر المصدر نفسه (تداخل OBV). المقام =
# len(القائمة) لا رقم يدوي، فلا يتقادم لو تغيّرت الشارات. المفاتيح المجهولة/الإضافية لا تُمنح نقاطاً.
# ملاحظة: الاستبعاد داخل حساب الثقة فقط — build_indicators/money_flow والشارة على المنصة بلا تغيير.
CONFIDENCE_TECHNICAL_INDICATOR_KEYS = (
    "EMA", "MACD", "RSI", "اختراق", "الحجم", "ADX",
    "قمة", "انضغاط", "تقاطع", "سوبرترند", "ستوكاستيك",
)  # 11 مفتاحاً — «تراكم»/OBV مُستبعَد (يُحتسب في flow_availability)

# مفتاح الشارة المستبعَد صراحةً (OBV) — لأغراض التوثيق/الاختبار.
EXCLUDED_OBV_INDICATOR_KEY = "تراكم"

# حالات الشارة/الهيكل/السيولة الصالحة (تثبت أن الحساب أنتج تصنيفاً فعلياً).
_VALID_STATUS = ("bull", "bear", "neutral")
# اتجاهات الفريم الصالحة (محسوبة فعلاً؛ "na"/غيرها = غير محسوب).
_VALID_FRAME_TRENDS = ("up", "down", "side")
# اتجاهات هيكل السوق الصالحة.
_VALID_STRUCTURE_TRENDS = ("up", "down", "side")

BAND_HIGH_MIN = 80     # ≥ 80 ⇒ عالية
BAND_MEDIUM_MIN = 50   # 50–79 ⇒ متوسطة ، أقل ⇒ منخفضة
CAP_LOW_MAX = 49       # سقف «منخفضة»
CAP_MEDIUM_MAX = 79    # سقف «متوسطة»

PIOTROSKI_MAX = 9      # عدد مقاييس Piotroski (النطاق الصحيح 0..9 حصراً)
CATALYST_MIN = 0.0     # النطاق الرسمي لـCatalyst من scoring.catalyst_score (0–100)
CATALYST_MAX = 100.0

# بوابة الركائز الجوهرية: عوامل لا يجوز أن تكون النتيجة High إذا سقط أيٌّ منها دون 50% من وزنه.
# (فقدان ركيزة كاملة يجعل تسمية High مضلِّلة حتى لو صحّ المجموع الحسابي.)
CRITICAL_FACTOR_KEYS = ("catalyst_completeness", "piotroski_computability", "technical_indicators")
CRITICAL_FACTOR_MIN_RATIO = 0.5

# أسباب ثابتة الصياغة (قابلة للاختبار).
REASON_DATE_MISSING = "تاريخ التحليل مفقود أو غير صالح"
REASON_DATE_FUTURE = "تاريخ التحليل مستقبلي (أحدث من الجلسة المتوقّعة)"
REASON_BAD_PRICE = "سعر الأساس (analysis_close) مفقود أو غير صالح أو ≤ 0"
REASON_DATE_STALE = "تاريخ التحليل أقدم من الجلسة المتوقّعة (بيانات متأخّرة)"
REASON_EXPECTED_MISSING = "الجلسة المتوقّعة غير مُمرَّرة أو غير صالحة — تعذّر التحقّق من حداثة البيانات"
REASON_CRITICAL_PREFIX = "ركيزة جوهرية غير كافية (دون 50% من وزنها): "


def _finite_number(value):
    """يُرجع value كعدد حقيقي منتهٍ (int/float غير bool وغير NaN/±∞) وإلا None.

    bool مرفوض (True ليس رقماً)، وNaN/Infinity/-Infinity مرفوضة صراحةً بـmath.isfinite.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            return None
    return None


def _parse_date(value):
    """يحوّل قيمة تاريخ إلى datetime.date أو None (بلا استثناء).

    يقبل: datetime.date · datetime.datetime · نص "YYYY-MM-DD" (أو أوّل 10 محارف منه).
    نقي وحتمي — لا يستدعي today()/now().
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        s = value.strip()[:10]
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    return None


def _catalyst_points(record):
    """اكتمال النمو: Score صالح [0,100] + complete is True ⇒ كامل · +complete≠True ⇒ نصف · Score تالف ⇒ صفر.

    يرفض النص/NaN/±∞/خارج [0,100]. complete المفقود أو غير Boolean لا يُعدّ اكتمالاً كاملاً.
    """
    score = _finite_number(record.get("catalyst"))
    if score is None or not (CATALYST_MIN <= score <= CATALYST_MAX):
        return 0.0
    if record.get("catalyst_complete") is True:  # يجب أن يكون Boolean True حصراً
        return float(FACTOR_WEIGHTS["catalyst_completeness"])
    return FACTOR_WEIGHTS["catalyst_completeness"] / 2.0


def _piotroski_points(record):
    """قابلية حساب Piotroski: عدد صحيح 0..9 حصراً ⇒ نسبته · غير ذلك (كسري/سالب/>9/NaN/نص/bool) ⇒ صفر."""
    pc = record.get("piotroski_computable")
    if isinstance(pc, bool) or not isinstance(pc, int):
        return 0.0  # يرفض bool والكسري (float) والنص وNone
    if pc < 0 or pc > PIOTROSKI_MAX:
        return 0.0  # لا clamp يمنح التالف نقاطاً
    return FACTOR_WEIGHTS["piotroski_computability"] * (pc / PIOTROSKI_MAX)


def _badge_is_computed(b):
    """شارة مؤشر محسوبة فعلاً: dict، value ليست None، status ضمن {bull,bear,neutral}."""
    if not isinstance(b, dict):
        return False
    if b.get("value") is None:
        return False
    return b.get("status") in _VALID_STATUS


def _technical_points(record):
    """توفّر المؤشرات: نسبة المفاتيح المسموحة **المحسوبة صالحةً** إلى طول القائمة الثابتة.

    «تراكم»/OBV مُستبعَد. الشارة التي تحمل label فقط (بلا status صالح/بقيمة None) لا تُحتسب.
    """
    inds = record.get("indicators")
    if not isinstance(inds, (list, tuple)):
        return 0.0
    computed_labels = {b.get("label") for b in inds if _badge_is_computed(b)}
    denom = len(CONFIDENCE_TECHNICAL_INDICATOR_KEYS)
    if denom <= 0:
        return 0.0
    matched = sum(1 for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS if k in computed_labels)
    frac = min(1.0, matched / float(denom))
    return FACTOR_WEIGHTS["technical_indicators"] * frac


def _structure_is_computed(s):
    """هيكل السوق محسوب فعلاً: dict فيه trend∈{up,down,side} وstatus∈{bull,bear,neutral} (عقد market_structure).

    القاموس الفارغ أو ذو المفاتيح المجهولة أو قيم None ⇒ غير محسوب. الاتجاه الهابط الصالح محسوب.
    """
    return (isinstance(s, dict)
            and s.get("trend") in _VALID_STRUCTURE_TRENDS
            and s.get("status") in _VALID_STATUS)


def _flow_is_computed(f):
    """تدفق السيولة محسوب فعلاً: dict فيه score عدد منتهٍ وstatus∈{bull,bear,neutral} (عقد money_flow).

    الفارغ/المفاتيح المجهولة/score غير رقمي ⇒ غير محسوب. Flow السلبي (bear) الصالح محسوب.
    """
    return (isinstance(f, dict)
            and _finite_number(f.get("score")) is not None
            and f.get("status") in _VALID_STATUS)


def _band(score):
    """النطاق من الدرجة النهائية."""
    if score >= BAND_HIGH_MIN:
        return "high"
    if score >= BAND_MEDIUM_MIN:
        return "medium"
    return "low"


def data_confidence(record, expected_session_date=None):
    """يحسب درجة الثقة بالبيانات (0–100) لسجلّ سهم — دالة نقية.

    record: قاموس السهم (نفس ما يبنيه screener). لا يُعدَّل.
    expected_session_date: أحدث جلسة تداول يُفترض أن تعكسها البيانات (date أو نص "YYYY-MM-DD").
        عند غيابه أو عدم صلاحيته: حداثة البيانات **غير قابلة للتحقّق** — عامل الحداثة = 0،
        ويُضاف سبب واضح إلى missing وسجل إلى caps_applied، ويُطبَّق سقف 79 (فلا تكون النتيجة High).
        لا يُفترض أن analysis_date حديث لمجرد أنه تاريخ صالح.

    يُرجع قاموساً قابلاً للتسلسل JSON:
    {
      "score": الدرجة النهائية بعد السقوف (int 0–100),
      "band": "high" | "medium" | "low",
      "factors": {اسم: {"points", "max", "label"}} لكل عامل,
      "missing": [أسباب/عوامل غائبة أو غير قابلة للتحقّق],
      "caps_applied": [أوصاف السقوف المطبّقة],
      "schema_version": int,
      "raw_score": الدرجة قبل السقوف (int 0–100),
      "final_score": == score (للتوضيح),
    }
    """
    record = record if isinstance(record, dict) else {}
    factors = {}
    missing = []

    def _set(key, points):
        w = FACTOR_WEIGHTS[key]
        pts = round(points)
        factors[key] = {"points": pts, "max": w, "label": FACTOR_LABELS[key]}
        if pts <= 0:
            missing.append(FACTOR_LABELS[key])

    # 1) اكتمال النمو (Catalyst) — تحقّق صارم من النطاق والاكتمال.
    _set("catalyst_completeness", _catalyst_points(record))

    # 2) قابلية حساب Piotroski — عدد صحيح 0..9 حصراً.
    _set("piotroski_computability", _piotroski_points(record))

    # 3) توفّر المؤشرات الفنية — شارات محسوبة صالحة فقط، «تراكم»/OBV مُستبعَد.
    _set("technical_indicators", _technical_points(record))

    # 4) توفّر هيكل السوق — يثبت الحساب بـtrend+status (لا مجرد dict غير فارغ).
    _set("structure_availability",
         FACTOR_WEIGHTS["structure_availability"] if _structure_is_computed(record.get("structure")) else 0)

    # 5) توفّر الفريمات الإضافية — الأسبوعي والشهري المحسوبان (ليسا na). واحد ⇒ نصف · لا شيء ⇒ صفر.
    frames = record.get("frames")
    if isinstance(frames, dict):
        real = sum(1 for tf in ("weekly", "monthly") if frames.get(tf) in _VALID_FRAME_TRENDS)
        _set("frames_availability", FACTOR_WEIGHTS["frames_availability"] * (real / 2.0))
    else:
        _set("frames_availability", 0)

    # 6) توفّر تدفق السيولة — يثبت الحساب بـscore+status. (يحمل OBV حصراً — لا يُكرَّر في المؤشرات.)
    _set("flow_availability",
         FACTOR_WEIGHTS["flow_availability"] if _flow_is_computed(record.get("money_flow")) else 0)

    # 7) الحداثة — تُمنح فقط إذا كان التاريخ صالحاً **ويطابق** الجلسة المتوقّعة الصالحة.
    #    غياب/عدم صلاحية الجلسة المتوقّعة ⇒ الحداثة غير قابلة للتحقّق ⇒ 0 (لا افتراض حداثة).
    adate = _parse_date(record.get("analysis_date"))
    edate = _parse_date(expected_session_date)
    if adate is not None and edate is not None and adate == edate:
        _set("freshness", FACTOR_WEIGHTS["freshness"])
    else:
        _set("freshness", 0)

    raw_score = round(sum(f["points"] for f in factors.values()))
    final_score = raw_score
    caps_applied = []

    # ── السقوف الحاسمة ──
    close = _finite_number(record.get("analysis_close"))   # يرفض NaN/±∞/bool/نص
    bad_price = close is None or close <= 0
    date_missing_or_invalid = adate is None
    date_future = adate is not None and edate is not None and adate > edate
    date_stale = adate is not None and edate is not None and adate < edate
    expected_missing = edate is None

    # سبب واضح في missing حين تتعذّر معرفة الحداثة (لا مرجع صالح للمقارنة).
    if expected_missing:
        missing.append(REASON_EXPECTED_MISSING)

    low_reasons = []
    if date_missing_or_invalid:
        low_reasons.append(REASON_DATE_MISSING)
    if date_future:
        low_reasons.append(REASON_DATE_FUTURE)
    if bad_price:
        low_reasons.append(REASON_BAD_PRICE)

    # بوابة الركائز الجوهرية: أي عامل جوهري دون 50% من وزنه يمنع High (سقف متوسط 79).
    critical_reasons = [
        REASON_CRITICAL_PREFIX + FACTOR_LABELS[k]
        for k in CRITICAL_FACTOR_KEYS
        if factors[k]["points"] < CRITICAL_FACTOR_MIN_RATIO * FACTOR_WEIGHTS[k]
    ]

    if low_reasons:
        # السقف الأشدّ (49) يغلب أي سقف أخفّ (79) — لا يُسجَّل Medium منفصل حينها.
        if final_score > CAP_LOW_MAX:
            final_score = CAP_LOW_MAX
        caps_applied.append({"cap": "low", "max": CAP_LOW_MAX, "reasons": low_reasons})
    else:
        # تُجمع كل أسباب المتوسط (حداثة + ركائز جوهرية) في سجل Medium واحد واضح.
        medium_reasons = []
        if expected_missing:
            medium_reasons.append(REASON_EXPECTED_MISSING)
        elif date_stale:
            medium_reasons.append(REASON_DATE_STALE)
        medium_reasons.extend(critical_reasons)
        if medium_reasons:
            if final_score > CAP_MEDIUM_MAX:
                final_score = CAP_MEDIUM_MAX
            caps_applied.append({"cap": "medium", "max": CAP_MEDIUM_MAX, "reasons": medium_reasons})

    final_score = int(max(0, min(100, final_score)))

    return {
        "score": final_score,
        "band": _band(final_score),
        "factors": factors,
        "missing": missing,
        "caps_applied": caps_applied,
        "schema_version": SCHEMA_VERSION,
        "raw_score": int(max(0, min(100, raw_score))),
        "final_score": final_score,
    }
