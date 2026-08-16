"""
state.py — محرّك الحالة المركزي لـ PHASE 5 (State Engine).

مصدر الحقيقة الوحيد لحالة السهم. لا يُعاد بناء منطق الحالة في القوالب إطلاقاً.

طبقتان:
- classify_setup(record): نقية، من السجل الحالي فقط. ست حالات بناء:
    WATCH · FORMING · NEAR_READY · READY · LAUNCHED · EXTENDED
- resolve_lifecycle_state(setup, record, context): تضيف الحالتين الواعيتين بدورة الحياة فقط:
    LOSING_MOMENTUM · INVALIDATED  (لا تظهران بلا lifecycle صاعد حقيقي)
- stock_state(record, context=None): الواجهة المركزية. context=None ⇒ لا تُظهر
  INVALIDATED/LOSING_MOMENTUM أبداً (طبقة نقية فقط).

مبادئ مقفلة (PHASE 5 FINAL DESIGN LOCK):
- صفر threshold رقمي سوقي جديد. يُعيد استخدام عتبات PHASE 1–4 فقط عبر screener:
  catalyst≥80، نطاقات tech_tilt، ADX≥25 (داخل حالة شارة ADX)، تأكيد الحجم 1.5×،
  ext_atr>4.0 (entry_zone)، أحداث structure، reversal.
- READY = عين HIGH #1: catalyst≥80 ∧ tilt موجود ∧ tilt.kind ∉ {neg1,neg2}. (المحايد neu مقبول.)
- المفقود ≠ إيجابي: أي حقل None ⇒ predicate=False.
- يعتمد على حقول التحليل الليلي فقط (indicators/structure/catalyst/tilt) — لا live_price إطلاقاً.
"""

from services import screener

# رموز الحالات + تسمياتها العربية (مصدر واحد للعرض)
STATE_LABELS = {
    "WATCH": "راقب",
    "FORMING": "يتكوّن",
    "NEAR_READY": "قريب من الجاهزية",
    "READY": "جاهز",
    "LAUNCHED": "منطلق",
    "EXTENDED": "ممتد",
    "LOSING_MOMENTUM": "يفقد الزخم",
    "INVALIDATED": "فقد السيناريو",
}

# الحالة التالية المحتملة على مسار الفرصة (للعرض التعليمي فقط)
_NEXT_STATE = {
    "WATCH": "FORMING",
    "FORMING": "NEAR_READY",
    "NEAR_READY": "READY",
    "READY": "LAUNCHED",
    "LAUNCHED": "EXTENDED",
    "EXTENDED": "EXTENDED",
    "LOSING_MOMENTUM": "READY",
    "INVALIDATED": "WATCH",
}

# الحالات الصاعدة «المتقدّمة» (تعافٍ بلا فقدان زخم)
_ADVANCED = ("READY", "LAUNCHED", "EXTENDED")
# الحالات القابلة للتتبّع كأحداث أداء
TRACKED_EVENT_STATES = ("READY", "LAUNCHED", "INVALIDATED")


def _signals(record):
    """يستخرج كل الأعلام الحتمية من حقول موجودة فقط (بلا رقم جديد)."""
    record = record or {}
    tilt = screener.tech_tilt(record)
    tk = tilt.get("kind") if tilt else None

    catalyst = record.get("catalyst")
    catalyst_high = catalyst is not None and catalyst >= 80  # عتبة PHASE موجودة

    brk = record.get("break_status") or {}
    brk_up_confirmed = bool(brk.get("confirmed") and brk.get("dir") == "breakout")
    brk_down_confirmed = bool(brk.get("confirmed") and brk.get("dir") == "breakdown")
    brk_forming = bool(brk.get("dir") == "breakout" and not brk.get("confirmed"))

    sus = record.get("sustained") or {}
    sustained_ok = bool(sus.get("sustained"))
    extended_zone = bool(sustained_ok and sus.get("entry_zone") == "extended")

    st = record.get("structure") or {}
    struct_up = st.get("trend") == "up"
    bos_up = st.get("event") == "BOS" and st.get("event_dir") == "up"
    choch_down = st.get("event") == "CHOCH" and st.get("event_dir") == "down"
    retest_ok = st.get("retest_state") == "confirmed"

    rev = record.get("reversal") or {}
    rev_bear = rev.get("status") == "bear"

    squeeze_on = bool(record.get("squeeze_breakout"))

    tilt_present = tilt is not None
    tilt_nonneg = tilt_present and tk in ("neu", "pos1", "pos2")
    tilt_negative = tk in ("neg1", "neg2")

    return {
        "tilt": tilt, "tilt_kind": tk, "tilt_present": tilt_present,
        "tilt_nonneg": tilt_nonneg, "tilt_negative": tilt_negative,
        "catalyst_high": catalyst_high,
        "brk_up_confirmed": brk_up_confirmed, "brk_down_confirmed": brk_down_confirmed,
        "brk_forming": brk_forming,
        "sustained_ok": sustained_ok, "extended_zone": extended_zone,
        "struct_up": struct_up, "bos_up": bos_up, "choch_down": choch_down,
        "retest_ok": retest_ok, "rev_bear": rev_bear, "squeeze_on": squeeze_on,
    }


def classify_setup(record):
    """التصنيف النقي (من السجل الحالي فقط) — واحدة من ست حالات بناء.

    الأولوية (أول تحقّق يفوز): EXTENDED > LAUNCHED > READY > NEAR_READY > FORMING > WATCH.
    لا تُنتِج INVALIDATED/LOSING_MOMENTUM (هاتان في resolve_lifecycle_state فقط).
    """
    s = _signals(record)

    if s["extended_zone"]:
        return "EXTENDED"
    if (s["brk_up_confirmed"] or s["sustained_ok"]) and not s["extended_zone"]:
        return "LAUNCHED"
    # READY = HIGH #1 حرفياً
    if s["catalyst_high"] and s["tilt_nonneg"]:
        return "READY"
    # NEAR_READY: بوابة واحدة فقط ناقصة من READY (بلا threshold جديد)
    if s["tilt_present"] and s["tilt_kind"] != "neg2":
        near = (
            (s["catalyst_high"] and s["tilt_kind"] == "neg1")
            or (not s["catalyst_high"] and s["tilt_nonneg"]
                and (s["brk_up_confirmed"] or s["retest_ok"] or s["bos_up"]))
        )
        if near:
            return "NEAR_READY"
    # FORMING: بناء صاعد مبكّر مع ميل غير سلبي
    if s["tilt_nonneg"] and (s["struct_up"] or s["bos_up"] or s["squeeze_on"] or s["brk_forming"]):
        return "FORMING"
    return "WATCH"


def resolve_lifecycle_state(setup_code, record, context):
    """الطبقة الواعية بدورة الحياة. context = {"open_bullish": bool}.

    INVALIDATED/LOSING_MOMENTUM لا تظهران إلا مع lifecycle صاعد مفتوح حقيقي (open_bullish).
    الأولوية: INVALIDATED > LOSING_MOMENTUM > (نتيجة الطبقة النقية).
    """
    context = context or {}
    open_bullish = bool(context.get("open_bullish"))
    s = _signals(record)
    weakening = s["tilt_negative"] or s["rev_bear"]

    if (s["brk_down_confirmed"] or s["choch_down"]) and open_bullish:
        return "INVALIDATED"
    if (open_bullish and setup_code not in _ADVANCED and weakening):
        return "LOSING_MOMENTUM"
    return setup_code


def _confidence(record):
    """جودة البيانات (وصف لا عتبة سوق): high/medium/low حسب توفّر الحقول الجوهرية."""
    record = record or {}
    tilt_missing = screener.tech_tilt(record) is None
    struct_missing = not record.get("structure")
    if tilt_missing or struct_missing:
        return "low"
    if record.get("catalyst") is None or not record.get("indicators"):
        return "medium"
    return "high"


def missing_conditions(record, code):
    """الشروط الفعلية التي تنقص السهم للانتقال — من نفس المحرك (لا منطق مستقل).

    لغة neg1/neg2: «يحتاج الميل الفني للعودة إلى محايد أو إيجابي» (لأن المحايد كافٍ في HIGH #1).
    نقص بيانات جوهرية ⇒ «غير معروف» بلا عدد زائف.
    """
    s = _signals(record)
    out = []
    if code in ("READY", "LAUNCHED", "EXTENDED"):
        return out  # لا شروط ناقصة (متحقّقة/متقدّمة)

    if code == "INVALIDATED":
        return out  # حالة نهائية — لا شروط انتقال تُعرض

    if code == "LOSING_MOMENTUM":
        out.append("يحتاج الميل الفني للعودة إلى محايد أو إيجابي")
        out.append("تأكيد عدم كسر الهيكل الصاعد")
        return out

    # WATCH / FORMING / NEAR_READY — نعدّد بوابات READY غير المحقّقة فعلياً
    if not s["tilt_present"]:
        out.append("المؤشرات الفنية غير متوفّرة بعد (غير معروف)")
    elif s["tilt_negative"]:
        out.append("يحتاج الميل الفني للعودة إلى محايد أو إيجابي")
    if not s["catalyst_high"]:
        out.append("درجة النمو (Catalyst) دون العتبة")
    if code == "FORMING" or code == "WATCH":
        if not (s["brk_up_confirmed"] or s["retest_ok"]):
            out.append("تأكيد الاختراق بحجم")
    return out


def stock_state(record, context=None):
    """الواجهة المركزية. تُرجع dict كامل للحالة.

    context=None ⇒ طبقة نقية فقط (لا INVALIDATED/LOSING_MOMENTUM).
    context={"open_bullish": bool} ⇒ الحالة الواعية بدورة الحياة.
    """
    setup = classify_setup(record)
    if context is None:
        code = setup
    else:
        code = resolve_lifecycle_state(setup, record, context)

    miss = missing_conditions(record, code)
    conf = _confidence(record)
    # عدد الشروط: حتمي إن كانت كلها معروفة؛ إن كان أحدها «غير معروف» لا نعطي عدداً زائفاً
    unknown = any("غير معروف" in m for m in miss)
    count = None if unknown else len(miss)

    return {
        "code": code,
        "label": STATE_LABELS.get(code, code),
        "setup": setup,               # التصنيف النقي (للتشخيص)
        "reason": [],                 # (محجوز — يمكن ملؤه لاحقاً بلا كسر توافق)
        "missing_conditions": miss,
        "missing_conditions_count": count,
        "next_state": _NEXT_STATE.get(code),
        "next_state_label": STATE_LABELS.get(_NEXT_STATE.get(code)),
        "confidence": conf,
    }
