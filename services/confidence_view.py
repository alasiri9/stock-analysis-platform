"""
confidence_view.py — مقدّم عرض نقي لدرجة الثقة (Data Confidence UI View Model) — PHASE 6 F2.

الغرض: يحوّل ناتج data_confidence المخزّن (في StockSnapshot.extra_json) إلى **view-model جاهز للعرض**
— بلا أي منطق تصنيف/استخراج في قوالب Jinja. مقدّم واحد مشترك (present_confidence) للبطاقة وصفحة السهم.

مبادئ صارمة (كلها مقفلة):
- **نقي تماماً**: لا قاعدة بيانات، لا Flask، لا API، لا live_price، لا كتابة، ولا إعادة حساب الثقة.
- لا يعدّل المدخل، وخرجه حتمي وقابل للتسلسل JSON.
- المدخلات التالفة/None/unavailable/غير الصالحة ⇒ «غير متوفرة» بلا استثناء (فلا 500 في مسار العرض).
- تحقّق رقمي صارم: score/raw_score/final_score ونقاط العوامل **أعداد صحيحة حصراً** (يرفض bool وfloat
  حتى 3.0 وNaN و±∞ والنصوص)، بلا int()/round() لإصلاح تالف.
- ترتيب فحص schema أوّلاً (قبل unavailable). التوافق: score↔band، final==score، raw==Σالعوامل، score≤raw.

تجميد schema 1 (قرار مهم): ترتيب العوامل السبعة وتسمياتها وحدودها **مجمّدة هنا** لـschema_version=1،
فلا تتغيّر قراءة اللقطات التاريخية إذا تغيّرت نواة confidence.py مستقبلاً. أي schema غير مدعوم ⇒ unavailable.
"""

from datetime import date, datetime

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA = frozenset({1})

# ترتيب/تسميات/حدود عوامل schema 1 — **مجمّدة** (لا تُشتقّ ديناميكياً من النواة).
# (key, label, max_weight). المجموع = 100. الترتيب ثابت للعرض ولتاريخ اللقطات.
_SCHEMA1_FACTORS = (
    ("catalyst_completeness",   "اكتمال درجة النمو (Catalyst)",              20),
    ("piotroski_computability", "قابلية حساب Piotroski",                     20),
    ("technical_indicators",    "توفّر المؤشرات الفنية",                      20),
    ("structure_availability",  "توفّر هيكل السوق",                           15),
    ("frames_availability",     "توفّر الفريمات الإضافية (الأسبوعي والشهري)", 10),
    ("flow_availability",       "توفّر تدفق السيولة",                          5),
    ("freshness",               "حداثة البيانات",                            10),
)

# الركائز الجوهرية الثلاثة (schema 1) — تُعلَّم critical، وعلامة critical_below_half عند < 50% من الحد.
CRITICAL_FACTOR_KEYS = frozenset({
    "catalyst_completeness", "piotroski_computability", "technical_indicators",
})
CRITICAL_MIN_RATIO = 0.5

# حدود النطاقات (schema 1) — للتحقّق من توافق band مع score.
BAND_HIGH_MIN = 80
BAND_MEDIUM_MIN = 50

# تصنيفات النطاق: (band_label, band_class). اللون النهائي في CSS لاحقاً (high = أزرق معلوماتي لا أخضر).
_BAND_META = {
    "high":   ("ثقة عالية",  "conf-high"),
    "medium": ("ثقة متوسطة", "conf-medium"),
    "low":    ("ثقة منخفضة", "conf-low"),
}
_UNAVAILABLE_LABEL = "درجة الثقة غير متوفرة"
_UNAVAILABLE_CLASS = "conf-na"

# السقوف الرسمية في schema 1: cap → max بالضبط (لا قيمة أخرى مقبولة).
_OFFICIAL_CAP_MAX = {"low": 49, "medium": 79}

# نص واجهة ثابت يوضّح المعنى (الفصل عن جودة الفرصة/الجودة المالية).
EXPLANATION_TEXT = "تقيس اكتمال وحداثة البيانات، ولا تقيس جودة الفرصة."

# رموز أسباب عدم التوفّر (ثابتة قابلة للاختبار).
REASON_MISSING = "missing"                        # None / ليس dict
REASON_UNAVAILABLE = "unavailable"                # النواة أعلنت unavailable بلا سبب صالح
REASON_UNSUPPORTED_SCHEMA = "unsupported_schema"  # schema_version مفقود/bool/غير مدعوم
REASON_CORRUPT = "corrupt"                        # score/band/factors/raw/final غير متّسقة


def _strict_int(value):
    """يُرجع value إن كان **عدداً صحيحاً حقيقياً** (int وليس bool) وإلا None.

    يرفض bool وfloat (حتى 3.0) وNaN/±∞ والنصوص — بلا int()/round() لإصلاح تالف.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _band_for_score(score):
    """النطاق المتوقّع من درجة صحيحة (للتحقّق من توافق band)."""
    if score >= BAND_HIGH_MIN:
        return "high"
    if score >= BAND_MEDIUM_MIN:
        return "medium"
    return "low"


def _as_of_str(snap_date):
    """يحوّل snap_date إلى 'YYYY-MM-DD' صالح فعلياً، أو None (بلا استثناء).

    يقبل date/datetime، أو نصاً يمثّل تاريخاً حقيقياً (يُتحقّق بـstrptime). '2026-99-99'/'garbage' ⇒ None.
    """
    if snap_date is None:
        return None
    if isinstance(snap_date, datetime):
        return snap_date.date().isoformat()
    if isinstance(snap_date, date):  # datetime مُستبعَد أعلاه
        return snap_date.isoformat()
    if isinstance(snap_date, str):
        # نصّاً: نقبل صيغة YYYY-MM-DD بطول 10 محارف **بالضبط** (بلا [:10] قبل التحقّق).
        # لاحقات مثل "…garbage" أو "…T12:00" تُرفض؛ datetime الحقيقي يُقبل أعلاه.
        if len(snap_date) != 10:
            return None
        try:
            return datetime.strptime(snap_date, "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            return None
    return None


def _clean_str_list(value):
    """قائمة نصوص غير فارغة آمنة من list، أو [] لغير ذلك (لا تُعدّل المصدر)."""
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, str) and x.strip()]


def _clean_caps(value):
    """caps_applied آمنة (schema 1): تُقبل القيم **الرسمية فقط** — low⇒max=49، medium⇒max=79 بالضبط.

    أي max آخر أو كسر أو bool أو cap مجهول أو سجلّ ليس dict ⇒ يُتجاهل بأمان بلا استثناء ولا int()/round().
    reasons تُطهَّر إلى نصوص غير فارغة فقط.
    """
    if not isinstance(value, list):
        return []
    out = []
    for c in value:
        if not isinstance(c, dict):
            continue
        cap = c.get("cap")
        if not isinstance(cap, str):   # cap غير نصّي (list/dict/رقم/None) — .get عليه قد يرفع TypeError
            continue
        official_max = _OFFICIAL_CAP_MAX.get(cap)   # None لأي cap مجهول
        if official_max is None:
            continue
        cmax = _strict_int(c.get("max"))            # عدد صحيح حصراً (لا كسر/bool/نص)
        if cmax != official_max:                    # يجب أن يطابق القيمة الرسمية تماماً
            continue
        out.append({"cap": cap, "max": cmax, "reasons": _clean_str_list(c.get("reasons"))})
    return out


def _unavailable(reason_code, schema_version=None, as_of=None):
    """يبني view-model «غير متوفرة» موحّداً (بلا استثناء)."""
    return {
        "available": False,
        "score": None,
        "score_text": None,
        "band": None,
        "band_label": _UNAVAILABLE_LABEL,
        "band_class": _UNAVAILABLE_CLASS,
        "explanation": EXPLANATION_TEXT,
        "as_of": as_of,
        "factors": [],
        "missing": [],
        "caps_applied": [],
        "schema_version": schema_version,
        "reason_code": reason_code,
    }


def present_confidence(stored, snap_date=None):
    """يحوّل data_confidence المخزّن إلى view-model عرض موحّد — دالة نقية.

    stored: قاموس data_confidence كما حُفظ داخل extra_json (أو None/تالف).
    snap_date: تاريخ اللقطة المصدر (date أو نص) — للعرض «حتى تاريخ».

    يُرجع قاموساً قابلاً للتسلسل JSON بالعقد:
    {available, score, score_text, band, band_label, band_class, explanation, as_of,
     factors:[{key,label,points,max,pct,critical,critical_below_half}],
     missing, caps_applied, schema_version, reason_code}
    """
    as_of = _as_of_str(snap_date)

    # 1) مدخل غائب/غير dict ⇒ غير متوفرة.
    if not isinstance(stored, dict):
        return _unavailable(REASON_MISSING, as_of=as_of)

    # 2) فحص schema **أوّلاً** (قبل unavailable): 1 فقط مدعوم، وbool مرفوض (True==1 في set).
    raw_schema = stored.get("schema_version")
    schema_ok = (isinstance(raw_schema, int) and not isinstance(raw_schema, bool)
                 and raw_schema in SUPPORTED_SCHEMA)
    schema_echo = raw_schema if (isinstance(raw_schema, int) and not isinstance(raw_schema, bool)) else None
    if not schema_ok:
        return _unavailable(REASON_UNSUPPORTED_SCHEMA, schema_version=schema_echo, as_of=as_of)
    # schema == 1 مؤكّد الآن.

    # 3) النواة أعلنت unavailable ⇒ غير متوفرة (reason نصي غير فارغ وإلا "unavailable").
    if stored.get("unavailable") is True:
        rc = stored.get("reason_code")
        rc = rc if (isinstance(rc, str) and rc.strip()) else REASON_UNAVAILABLE
        return _unavailable(rc, schema_version=SCHEMA_VERSION, as_of=as_of)

    # 4) تحقّق صارم: score/raw_score/final_score أعداد صحيحة، ضمن 0–100، final==score، score≤raw.
    score = _strict_int(stored.get("score"))
    raw = _strict_int(stored.get("raw_score"))
    final = _strict_int(stored.get("final_score"))
    if score is None or raw is None or final is None:
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)
    if not (0 <= score <= 100) or not (0 <= raw <= 100):
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)
    if final != score or score > raw:
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)

    # 5) تحقّق: band نصّ موجود ويطابق حدود score (high 80–100 · medium 50–79 · low 0–49).
    #    نتحقّق من str أوّلاً — membership على قيمة غير قابلة للـhash (list/dict) يرفع TypeError.
    band = stored.get("band")
    if not isinstance(band, str) or band not in _BAND_META or band != _band_for_score(score):
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)

    # 6) تحقّق: factors — dict فيها العوامل السبعة، نقاط أعداد صحيحة 0..max، ومجموعها == raw.
    stored_factors = stored.get("factors")
    if not isinstance(stored_factors, dict):
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)

    factors = []
    total = 0
    for key, label, fmax in _SCHEMA1_FACTORS:  # الترتيب المجمّد
        cell = stored_factors.get(key)
        if not isinstance(cell, dict):
            return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)
        points = _strict_int(cell.get("points"))   # عدد صحيح حصراً (لا تقريب تالف)
        if points is None or not (0 <= points <= fmax):
            return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)
        total += points
        pct = int(round(points / fmax * 100)) if fmax else 0   # نسبة من نقاط صحيحة فقط
        critical = key in CRITICAL_FACTOR_KEYS
        factors.append({
            "key": key,
            "label": label,
            "points": points,
            "max": fmax,
            "pct": pct,
            "critical": critical,
            "critical_below_half": bool(critical and points < CRITICAL_MIN_RATIO * fmax),
        })

    if total != raw:   # raw_score يجب أن يساوي مجموع نقاط العوامل السبعة
        return _unavailable(REASON_CORRUPT, schema_version=SCHEMA_VERSION, as_of=as_of)

    band_label, band_class = _BAND_META[band]
    return {
        "available": True,
        "score": score,
        "score_text": f"{score}/100",
        "band": band,
        "band_label": band_label,
        "band_class": band_class,
        "explanation": EXPLANATION_TEXT,
        "as_of": as_of,
        "factors": factors,
        "missing": _clean_str_list(stored.get("missing")),
        "caps_applied": _clean_caps(stored.get("caps_applied")),
        "schema_version": SCHEMA_VERSION,
        "reason_code": None,
    }
