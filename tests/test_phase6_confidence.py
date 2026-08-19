"""
test_phase6_confidence.py — PHASE 6 F2 (Data Confidence Core) — مع معالجة خصومية.

يقفل سلوك الدالة النقية `services.confidence.data_confidence(record, expected_session_date=None)`:
- سبعة عوامل (المجموع 100) كلٌّ من حقل مصدر مستقل — لا تداخل، لا تكرار.
- تدرّج العوامل (كامل/جزئي/غائب) حسب **توفّر وصلاحية** ناتج الحساب لا قيمته.
- المؤشرات الفنية: قائمة مسموحة ثابتة تستبعد «تراكم»/OBV (يُحتسب في Flow)، ولا تُحتسب شارة بلا حساب صالح.
- هيكل السوق/تدفق السيولة: القاموس الفارغ/المجهول ⇒ صفر؛ يثبت الحساب بالمفاتيح الدنيا (trend+status / score+status).
- تحقّق رقمي صارم: يرفض NaN/±∞/bool/النص/الكسري/خارج النطاق.
- السقوف: تاريخ مفقود/غير صالح/مستقبلي أو سعر أساس مكسور ⇒ ≤49 · قديم/تعذّر التحقّق من الحداثة ⇒ ≤79 · الأشدّ يغلب.
- النقاء: لا تعديل للسجل · حتمية · JSON-serializable · لا live_price · لا استثناء على المدخل التالف.
- بنية الاختبار: check() ترفع AssertionError فعلياً ⇒ الفشل يظهر في التشغيل المباشر وفي pytest.

التشغيل:  python tests/test_phase6_confidence.py
          python -m pytest tests/test_phase6_confidence.py -q
"""

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.confidence import (  # noqa: E402
    BAND_HIGH_MIN, BAND_MEDIUM_MIN, CAP_LOW_MAX, CAP_MEDIUM_MAX,
    CONFIDENCE_TECHNICAL_INDICATOR_KEYS, CRITICAL_FACTOR_KEYS, CRITICAL_FACTOR_MIN_RATIO,
    EXCLUDED_OBV_INDICATOR_KEY, FACTOR_LABELS, FACTOR_WEIGHTS,
    REASON_CRITICAL_PREFIX, REASON_EXPECTED_MISSING, SCHEMA_VERSION,
    data_confidence,
)

_passed = 0
_failed = 0
TODAY = date(2026, 8, 17)          # جلسة متوقّعة ثابتة (نقاء: لا today() فعلي)
YESTERDAY = date(2026, 8, 14)      # جلسة أقدم (الجمعة السابقة)
TOMORROW = date(2026, 8, 18)

NAN = float("nan")
POS_INF = float("inf")
NEG_INF = float("-inf")
_FRAMES_LABEL = FACTOR_LABELS["frames_availability"]


def check(cond, label):
    """يطبع النتيجة **ويرفع AssertionError عند الفشل** — فيظهر الفشل مباشرةً وفي pytest معاً."""
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")
        raise AssertionError(label)


def _badge(label, value="x", status="bull"):
    return {"label": label, "value": value, "status": status}


def _tech_all():
    """كل المؤشرات الفنية المسموح احتسابها في الثقة (تستبعد «تراكم»/OBV)، شارات محسوبة صالحة."""
    return [_badge(k) for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]


def _tech_first(n):
    """أول n مفتاح من القائمة المسموحة (شارات صالحة)."""
    return [_badge(k) for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS[:n]]


OBV_BADGE = {"label": EXCLUDED_OBV_INDICATOR_KEY, "value": "تجميع 40%", "status": "bull"}


def _full_record(**over):
    """سجلّ كامل البيانات وحديث ⇒ ثقة عالية (100) قبل أي تعديل (يُمرَّر معه TODAY).

    الهيكل/السيولة يحملان المفاتيح الدنيا التي تثبت الحساب (trend+status / score+status).
    """
    r = {
        "ticker": "FULL",
        "catalyst": 72,
        "catalyst_complete": True,
        "piotroski_computable": 9,
        "indicators": _tech_all(),                                  # كل المؤشرات المسموحة ⇒ 20/20
        "structure": {"trend": "up", "status": "bull", "event": "BOS"},
        "frames": {"daily": "up", "weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull", "label": "تجميع"},
        "analysis_date": "2026-08-17",
        "analysis_close": 123.45,
    }
    r.update(over)
    return r


# ═══════════════ 1) السجل الكامل الحديث ⇒ 100 عالية ═══════════════
def test_1_full_record_perfect():
    print("\n[1] سجل كامل حديث ⇒ 100 (عالية)، لا نواقص، لا سقوف:")
    out = data_confidence(_full_record(), TODAY)
    check(out["raw_score"] == 100, f"raw_score=100 (كان {out['raw_score']})")
    check(out["score"] == 100, "score=100")
    check(out["band"] == "high", "النطاق عالية")
    check(out["missing"] == [], "لا عوامل غائبة")
    check(out["caps_applied"] == [], "لا سقوف مطبّقة")


# ═══════════════ 2) مجموع الأوزان = 100 (لم يتغيّر) ═══════════════
def test_2_weights_sum_100():
    print("\n[2] مجموع أوزان العوامل = 100 والعوامل السبعة كلها ممثّلة (الأوزان لم تتغيّر):")
    check(sum(FACTOR_WEIGHTS.values()) == 100, "المجموع = 100")
    check(FACTOR_WEIGHTS == {
        "catalyst_completeness": 20, "piotroski_computability": 20,
        "technical_indicators": 20, "structure_availability": 15,
        "frames_availability": 10, "flow_availability": 5, "freshness": 10,
    }, "الأوزان المعتمدة ثابتة")
    out = data_confidence(_full_record(), TODAY)
    check(len(out["factors"]) == 7, "سبعة عوامل بالضبط")
    check(all(out["factors"][k]["max"] == FACTOR_WEIGHTS[k] for k in FACTOR_WEIGHTS),
          "max لكل عامل = وزنه المعرّف")


# ═══════════════ 3) اكتمال Catalyst: كامل/جزئي/غائب ═══════════════
def test_3_catalyst_complete_full():
    print("\n[3] Catalyst مكتمل ⇒ 20 نقطة كاملة:")
    out = data_confidence(_full_record(catalyst=50, catalyst_complete=True), TODAY)
    check(out["factors"]["catalyst_completeness"]["points"] == 20, "20/20")


def test_4_catalyst_partial_half():
    print("\n[4] Catalyst متوفّر لكن غير مكتمل ⇒ نصف (10):")
    out = data_confidence(_full_record(catalyst=40, catalyst_complete=False), TODAY)
    check(out["factors"]["catalyst_completeness"]["points"] == 10, "10/20 (جزئي)")
    check("اكتمال درجة النمو (Catalyst)" not in out["missing"], "الجزئي ليس «غائباً»")


def test_5_catalyst_absent_zero():
    print("\n[5] Catalyst = None ⇒ 0 ويُدرَج في النواقص:")
    out = data_confidence(_full_record(catalyst=None, catalyst_complete=None), TODAY)
    check(out["factors"]["catalyst_completeness"]["points"] == 0, "0/20")
    check("اكتمال درجة النمو (Catalyst)" in out["missing"], "مدرَج في missing")


# ═══════════════ 4) Piotroski computability نسبي + None≠9 ═══════════════
def test_6_piotroski_proportional():
    print("\n[6] Piotroski computable=6 ⇒ (6/9)*20 ≈ 13:")
    out = data_confidence(_full_record(piotroski_computable=6), TODAY)
    check(out["factors"]["piotroski_computability"]["points"] == round(20 * 6 / 9),
          f"points = {out['factors']['piotroski_computability']['points']} (≈13)")


def test_7_piotroski_none_is_zero_not_nine():
    print("\n[7] Piotroski computable=None ⇒ 0 (لا يُفترض 9 كالتوافق الخلفي):")
    out = data_confidence(_full_record(piotroski_computable=None), TODAY)
    check(out["factors"]["piotroski_computability"]["points"] == 0, "0/20 (None ⇒ 0)")
    check("قابلية حساب Piotroski" in out["missing"], "مدرَج في missing")


def test_8_piotroski_full_nine():
    print("\n[8] Piotroski computable=9 ⇒ 20 كاملة:")
    out = data_confidence(_full_record(piotroski_computable=9), TODAY)
    check(out["factors"]["piotroski_computability"]["points"] == 20, "20/20")


# ═══════════════ 5) المؤشرات الفنية: قائمة مسموحة ثابتة، مقام من طولها ═══════════════
def test_9_indicators_proportional():
    print("\n[9] نصف المؤشرات المسموحة ⇒ نصف نقاط عامل المؤشرات (المقام من القائمة):")
    denom = len(CONFIDENCE_TECHNICAL_INDICATOR_KEYS)
    half = denom // 2
    out = data_confidence(_full_record(indicators=_tech_first(half)), TODAY)
    expected = round(20 * half / denom)
    check(out["factors"]["technical_indicators"]["points"] == expected,
          f"points = {out['factors']['technical_indicators']['points']} (متوقّع {expected}، مقام={denom})")


def test_10_indicators_empty_zero():
    print("\n[10] لا مؤشرات (قائمة فارغة) ⇒ 0 ومدرَج في النواقص:")
    out = data_confidence(_full_record(indicators=[]), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 0, "0/20")
    check("توفّر المؤشرات الفنية" in out["missing"], "مدرَج في missing")


def test_11_indicators_over_cap_not_exceed():
    print("\n[11] كل المسموحة + «تراكم» + مجاهيل ⇒ لا يتجاوز 20 (سقف نسبي 1.0):")
    over = _tech_all() + [OBV_BADGE, _badge("مجهول1"), _badge("مجهول2")]
    out = data_confidence(_full_record(indicators=over), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 20, "20/20 (لا يتعدّى)")


# ═══════════════ 6) هيكل السوق: توفّر ثنائي ═══════════════
def test_12_structure_present_vs_absent():
    print("\n[12] هيكل محسوب هابط ⇒ 15 · None ⇒ 0 (توفّر لا قيمة):")
    present = data_confidence(_full_record(structure={"trend": "down", "status": "bear"}), TODAY)
    absent = data_confidence(_full_record(structure=None), TODAY)
    check(present["factors"]["structure_availability"]["points"] == 15,
          "هيكل هابط لكنه محسوب صالح ⇒ 15 (الثقة تقيس التوفّر لا الاتجاه)")
    check(absent["factors"]["structure_availability"]["points"] == 0, "None ⇒ 0")
    check("توفّر هيكل السوق" in absent["missing"], "الغائب مدرَج في missing")


# ═══════════════ 7) الفريمات: كامل/نصف/غائب ═══════════════
def test_13_frames_full():
    print("\n[13] أسبوعي+شهري محسوبان ⇒ 10 كاملة:")
    out = data_confidence(_full_record(frames={"daily": "up", "weekly": "down", "monthly": "side"}), TODAY)
    check(out["factors"]["frames_availability"]["points"] == 10, "10/10")


def test_14_frames_half_na():
    print("\n[14] أحد الفريمين na ⇒ نصف (5):")
    out = data_confidence(_full_record(frames={"daily": "up", "weekly": "up", "monthly": "na"}), TODAY)
    check(out["factors"]["frames_availability"]["points"] == 5, "5/10 (فريم واحد na)")


def test_15_frames_none_zero():
    print("\n[15] frames=None ⇒ 0 ومدرَج في النواقص (بالتسمية الجديدة «الإضافية»):")
    out = data_confidence(_full_record(frames=None), TODAY)
    check(out["factors"]["frames_availability"]["points"] == 0, "0/10")
    check(_FRAMES_LABEL in out["missing"], "مدرَج في missing")
    check("الأسبوعي والشهري" in _FRAMES_LABEL and "الثلاثة" not in _FRAMES_LABEL,
          "التسمية دقيقة (أسبوعي+شهري، بلا «الثلاثة»)")


# ═══════════════ 8) تدفق السيولة: توفّر ثنائي ═══════════════
def test_16_flow_present_vs_absent():
    print("\n[16] money_flow محسوب ⇒ 5 · None ⇒ 0:")
    present = data_confidence(_full_record(money_flow={"score": 12.0, "status": "bear"}), TODAY)
    absent = data_confidence(_full_record(money_flow=None), TODAY)
    check(present["factors"]["flow_availability"]["points"] == 5, "متوفّر ⇒ 5")
    check(absent["factors"]["flow_availability"]["points"] == 0, "None ⇒ 0")


# ═══════════════ 9) الحداثة + السقف الزمني ═══════════════
def test_17_fresh_full():
    print("\n[17] تاريخ التحليل = الجلسة المتوقّعة ⇒ حداثة 10، لا سقف:")
    out = data_confidence(_full_record(analysis_date="2026-08-17"), TODAY)
    check(out["factors"]["freshness"]["points"] == 10, "10/10")
    check(out["caps_applied"] == [], "لا سقف")


def test_18_stale_medium_cap():
    print("\n[18] تاريخ أقدم من المتوقّع ⇒ حداثة 0 + سقف متوسط (≤79):")
    out = data_confidence(_full_record(analysis_date="2026-08-14"), TODAY)  # أقدم
    check(out["factors"]["freshness"]["points"] == 0, "حداثة 0")
    check(out["score"] <= CAP_MEDIUM_MAX, f"score ≤ {CAP_MEDIUM_MAX} (كان {out['score']})")
    check(any(c["cap"] == "medium" for c in out["caps_applied"]), "سقف medium مطبّق")
    check(out["band"] == "medium", "النطاق متوسطة")


# ═══════════════ 10) السقوف الحاسمة (منخفضة ≤49) ═══════════════
def test_19_missing_date_low_cap():
    print("\n[19] تاريخ تحليل مفقود ⇒ سقف منخفض (≤49) مهما كانت البيانات كاملة:")
    out = data_confidence(_full_record(analysis_date=None), TODAY)
    check(out["score"] <= CAP_LOW_MAX, f"score ≤ {CAP_LOW_MAX} (كان {out['score']})")
    check(out["band"] == "low", "النطاق منخفضة")
    check(any(c["cap"] == "low" for c in out["caps_applied"]), "سقف low مطبّق")


def test_20_bad_baseline_price_low_cap():
    print("\n[20] سعر الأساس ≤0 أو مفقود ⇒ سقف منخفض (≤49):")
    zero = data_confidence(_full_record(analysis_close=0.0), TODAY)
    none = data_confidence(_full_record(analysis_close=None), TODAY)
    check(zero["score"] <= CAP_LOW_MAX and zero["band"] == "low", "close=0 ⇒ منخفضة")
    check(none["score"] <= CAP_LOW_MAX and none["band"] == "low", "close=None ⇒ منخفضة")
    check(any("analysis_close" in r for c in none["caps_applied"] for r in c["reasons"]),
          "السبب يذكر سعر الأساس")


def test_21_future_date_low_cap():
    print("\n[21] تاريخ تحليل مستقبلي (أحدث من المتوقّع) ⇒ سقف منخفض (≤49):")
    out = data_confidence(_full_record(analysis_date="2026-08-18"), TODAY)  # غداً
    check(out["factors"]["freshness"]["points"] == 0, "حداثة 0 (مستقبلي)")
    check(out["score"] <= CAP_LOW_MAX and out["band"] == "low", "منخفضة")
    check(any(c["cap"] == "low" for c in out["caps_applied"]), "سقف low مطبّق")


# ═══════════════ 11) النقاء ═══════════════
def test_22_purity_no_mutation_deterministic_json():
    print("\n[22] نقاء: لا تعديل للسجل · نفس الخرج مرتين · قابل للتسلسل JSON:")
    rec = _full_record()
    snapshot = json.dumps(rec, sort_keys=True, default=str)
    out1 = data_confidence(rec, TODAY)
    out2 = data_confidence(rec, TODAY)
    check(json.dumps(rec, sort_keys=True, default=str) == snapshot, "السجل لم يُعدَّل")
    check(out1 == out2, "نفس الدخل ⇒ نفس الخرج (حتمية)")
    try:
        json.dumps(out1)
        ok = True
    except (TypeError, ValueError):
        ok = False
    check(ok, "الخرج قابل للتسلسل JSON")
    check(out1["schema_version"] == SCHEMA_VERSION, "schema_version موجود")


def test_23_live_price_and_date_forms_and_empty():
    print("\n[23] live_price لا يؤثّر · يقبل date/datetime/نص للجلسة · سجل فارغ آمن:")
    base = _full_record()
    withlive = dict(base); withlive["live_price"] = 999.0
    check(data_confidence(base, TODAY) == data_confidence(withlive, TODAY),
          "live_price لا يغيّر الثقة")
    as_str = data_confidence(base, "2026-08-17")
    as_date = data_confidence(base, date(2026, 8, 17))
    as_dt = data_confidence(base, datetime(2026, 8, 17, 9, 30))
    check(as_str == as_date == as_dt, "نص/date/datetime للجلسة ⇒ نفس النتيجة")
    empty = data_confidence({}, TODAY)
    check(empty["score"] <= CAP_LOW_MAX and empty["band"] == "low",
          "سجل فارغ ⇒ منخفضة (بلا استثناء)")
    check(len(empty["missing"]) >= 6, "معظم العوامل غائبة في السجل الفارغ")


# ═══════════════ 12) غياب/عدم صلاحية expected_session_date ═══════════════
def test_24_no_expected_session_capped_79():
    print("\n[24] سجل كامل بلا expected_session_date ⇒ ≤79 · ليست High · Freshness=0 · سبب+سقف:")
    out = data_confidence(_full_record(), None)
    check(out["score"] <= CAP_MEDIUM_MAX, f"score ≤ 79 (كان {out['score']})")
    check(out["band"] != "high", f"ليست High (band={out['band']})")
    check(out["factors"]["freshness"]["points"] == 0, "Freshness = 0")
    check(REASON_EXPECTED_MISSING in out["missing"], "السبب موجود في missing")
    check(any(c["cap"] == "medium" and REASON_EXPECTED_MISSING in c["reasons"]
              for c in out["caps_applied"]), "السقف (medium) موجود في caps_applied مع السبب")


def test_25_invalid_expected_session_capped_79():
    print("\n[25] expected_session_date نص غير صالح ⇒ نفس معالجة الغياب (≤79، ليست High):")
    out = data_confidence(_full_record(), "not-a-date")
    check(out["score"] <= CAP_MEDIUM_MAX and out["band"] != "high", "≤79 وليست High")
    check(out["factors"]["freshness"]["points"] == 0, "Freshness = 0")
    check(REASON_EXPECTED_MISSING in out["missing"], "السبب في missing")


def test_26_no_expected_does_not_assume_fresh():
    print("\n[26] بلا expected: تاريخ صالح لا يُمنح نقاط حداثة (لا افتراض حداثة):")
    out = data_confidence(_full_record(analysis_date="2026-08-17"), None)
    check(out["factors"]["freshness"]["points"] == 0, "تاريخ صالح لكنه بلا مرجع ⇒ 0")
    check(out["score"] == 79, f"90 مقصوصة إلى 79 (كان {out['score']})")


def test_27_no_expected_plus_bad_price_yields_49():
    print("\n[27] اجتماع سقف 79 (بلا expected) مع سقف 49 (سعر أساس مكسور) ⇒ الأشدّ 49:")
    out = data_confidence(_full_record(analysis_close=0.0), None)
    check(out["score"] <= CAP_LOW_MAX, f"score ≤ 49 (كان {out['score']})")
    check(out["band"] == "low", "النطاق منخفضة (49 يغلب 79)")
    check(any(c["cap"] == "low" for c in out["caps_applied"]), "السقف المطبّق = low")
    check(all(c["cap"] != "medium" for c in out["caps_applied"]),
          "لا يُدرَج سقف medium حين يغلبه الأشدّ")


# ═══════════════ 13) إزالة تداخل OBV ═══════════════
def test_28_obv_alone_no_technical_points():
    print("\n[28] «تراكم» (OBV) وحده لا يمنح نقاط Technical Indicators:")
    out = data_confidence(_full_record(indicators=[OBV_BADGE]), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 0, "0/20 («تراكم» مُستبعَد)")
    check(EXCLUDED_OBV_INDICATOR_KEY not in CONFIDENCE_TECHNICAL_INDICATOR_KEYS,
          "«تراكم» ليس ضمن القائمة المسموحة")


def test_29_flow_scores_by_its_own_rule():
    print("\n[29] money_flow محسوب ⇒ نقاط Flow (5) وفق قاعدته المستقلّة:")
    out = data_confidence(_full_record(indicators=[], money_flow={"score": 60.0, "status": "bull"}), TODAY)
    check(out["factors"]["flow_availability"]["points"] == 5, "Flow = 5")
    check(out["factors"]["technical_indicators"]["points"] == 0, "المؤشرات = 0 (لا يستعير من Flow)")


def test_30_obv_and_flow_not_double_counted():
    print("\n[30] «تراكم» + money_flow معاً ⇒ OBV يُحتسب مرة واحدة (في Flow فقط):")
    mf = {"score": 60.0, "status": "bull"}
    with_obv = data_confidence(_full_record(indicators=[OBV_BADGE], money_flow=mf), TODAY)
    without_obv = data_confidence(_full_record(indicators=[], money_flow=mf), TODAY)
    check(with_obv["factors"]["technical_indicators"]["points"] == 0, "«تراكم» لا يرفع نقاط المؤشرات")
    check(with_obv["factors"]["flow_availability"]["points"] == 5, "Flow = 5")
    check(with_obv["score"] == without_obv["score"],
          "وجود «تراكم» لا يغيّر الدرجة (لا احتساب مزدوج لـOBV)")


def test_31_all_allowed_indicators_full_20():
    print("\n[31] كل المؤشرات المسموح بها ⇒ 20 كاملة:")
    out = data_confidence(_full_record(indicators=_tech_all()), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 20, "20/20")


def test_32_unknown_keys_no_points():
    print("\n[32] مفاتيح مجهولة لا ترفع الدرجة:")
    out = data_confidence(
        _full_record(indicators=[_badge("مجهول1"), _badge("XYZ"), _badge("")]), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 0, "0/20 (كلها مجهولة)")


def test_33_denominator_from_fixed_list():
    print("\n[33] المقام مشتقّ من طول القائمة الثابتة لا من رقم يدوي:")
    denom = len(CONFIDENCE_TECHNICAL_INDICATOR_KEYS)
    one = data_confidence(_full_record(indicators=_tech_first(1)), TODAY)
    check(one["factors"]["technical_indicators"]["points"] == round(20 * 1 / denom),
          f"مفتاح واحد ⇒ round(20/{denom}) = {round(20 / denom)}")
    check(denom == 11, f"طول القائمة = 11 (12 شارة − «تراكم») (كان {denom})")


def test_34_score_never_exceeds_100():
    print("\n[34] النتيجة لا تتجاوز 100 مهما زادت المؤشرات/التكرارات:")
    flood = _tech_all() * 3 + [OBV_BADGE] * 5 + [_badge("z%d" % i) for i in range(20)]
    out = data_confidence(_full_record(indicators=flood), TODAY)
    check(out["score"] <= 100 and out["raw_score"] <= 100, "score و raw_score ≤ 100")
    check(out["factors"]["technical_indicators"]["points"] == 20, "المؤشرات ≤ 20")


def test_35_other_factors_weights_unchanged():
    print("\n[35] بقية العوامل والأوزان لم تتغيّر بإصلاح OBV:")
    out = data_confidence(_full_record(), TODAY)
    check(out["factors"]["catalyst_completeness"]["max"] == 20, "Catalyst وزن 20")
    check(out["factors"]["piotroski_computability"]["max"] == 20, "Piotroski وزن 20")
    check(out["factors"]["structure_availability"]["max"] == 15, "Structure وزن 15")
    check(out["factors"]["frames_availability"]["max"] == 10, "Frames وزن 10")
    check(out["factors"]["flow_availability"]["max"] == 5, "Flow وزن 5 (بلا تغيير)")
    check(out["factors"]["freshness"]["max"] == 10, "Freshness وزن 10")
    check(out["factors"]["technical_indicators"]["max"] == 20, "Technical وزن 20 (بلا تغيير)")


# ═══════════════ 14) خصومية: التحقق الرقمي (NaN/±∞) والنطاقات ═══════════════
def test_36_analysis_close_nan_inf_low_cap():
    print("\n[36] analysis_close = NaN/∞/−∞ ⇒ سقف 49 (Low) وسبب سعر الأساس:")
    for bad in (NAN, POS_INF, NEG_INF):
        out = data_confidence(_full_record(analysis_close=bad), TODAY)
        check(out["score"] <= CAP_LOW_MAX and out["band"] == "low", f"close={bad!r} ⇒ منخفضة")
        check(any("analysis_close" in r for c in out["caps_applied"] for r in c["reasons"]),
              f"close={bad!r} ⇒ سبب سعر الأساس")


def test_37_piotroski_adversarial_zero():
    print("\n[37] Piotroski بقيم تالفة (NaN/∞/−∞/−1/10/4.5/True/نص) ⇒ صفر:")
    for bad in (NAN, POS_INF, NEG_INF, -1, 10, 4.5, True, "9", "corrupt", 3.0):
        out = data_confidence(_full_record(piotroski_computable=bad), TODAY)
        check(out["factors"]["piotroski_computability"]["points"] == 0,
              f"piotroski_computable={bad!r} ⇒ 0")


def test_38_piotroski_valid_ints_only():
    print("\n[38] Piotroski = أعداد صحيحة 0..9 فقط تُمنح نقاطاً نسبيّة:")
    for pc in range(0, 10):
        out = data_confidence(_full_record(piotroski_computable=pc), TODAY)
        check(out["factors"]["piotroski_computability"]["points"] == round(20 * pc / 9),
              f"pc={pc} ⇒ {round(20 * pc / 9)}")


def test_39_catalyst_adversarial_zero():
    print("\n[39] Catalyst بنص/NaN/∞/خارج [0,100] ⇒ صفر (لا 20 لنص «corrupt»):")
    for bad in ("corrupt", NAN, POS_INF, NEG_INF, -5, 150, True):
        out = data_confidence(_full_record(catalyst=bad, catalyst_complete=True), TODAY)
        check(out["factors"]["catalyst_completeness"]["points"] == 0,
              f"catalyst={bad!r} (مع complete=True) ⇒ 0")


def test_40_catalyst_complete_flag_must_be_true_bool():
    print("\n[40] catalyst_complete المفقود/غير Boolean لا يمنح اكتمالاً كاملاً ⇒ 10 (Score صالح):")
    for flag in (None, "yes", 1, 0, "True"):
        out = data_confidence(_full_record(catalyst=50, catalyst_complete=flag), TODAY)
        check(out["factors"]["catalyst_completeness"]["points"] == 10,
              f"complete={flag!r} ⇒ 10 (ليس 20)")


# ═══════════════ 15) خصومية: القواميس الفارغة/المجهولة والشارات الوهمية ═══════════════
def test_41_structure_empty_and_unknown_zero():
    print("\n[41] structure فارغ/بمفاتيح مجهولة/قيم None ⇒ صفر:")
    for bad in ({}, {"foo": 1, "bar": 2}, {"trend": None, "status": None},
                {"trend": "up"}, {"status": "bull"}, [], "up"):
        out = data_confidence(_full_record(structure=bad), TODAY)
        check(out["factors"]["structure_availability"]["points"] == 0,
              f"structure={bad!r} ⇒ 0")


def test_42_structure_down_valid_full():
    print("\n[42] structure هابط لكنه محسوب صالح ⇒ 15 (توفّر لا إيجابية):")
    out = data_confidence(_full_record(structure={"trend": "down", "status": "bear"}), TODAY)
    check(out["factors"]["structure_availability"]["points"] == 15, "15/15")


def test_43_money_flow_empty_and_unknown_zero():
    print("\n[43] money_flow فارغ/بمفاتيح مجهولة/score غير رقمي ⇒ صفر:")
    for bad in ({}, {"foo": 1}, {"score": None, "status": "bull"},
                {"score": NAN, "status": "bull"}, {"status": "bull"}, {"score": 10.0}, []):
        out = data_confidence(_full_record(money_flow=bad), TODAY)
        check(out["factors"]["flow_availability"]["points"] == 0,
              f"money_flow={bad!r} ⇒ 0")


def test_44_money_flow_negative_complete_full():
    print("\n[44] money_flow سلبي (bear) لكنه مكتمل صالح ⇒ 5 (توفّر لا إيجابية):")
    out = data_confidence(_full_record(money_flow={"score": 8.0, "status": "bear", "label": "تصريف"}), TODAY)
    check(out["factors"]["flow_availability"]["points"] == 5, "5/5")


def test_45_indicator_label_only_no_points():
    print("\n[45] شارة تحمل label فقط (بلا status/قيمة) ⇒ لا نقاط:")
    label_only = [{"label": k} for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]
    out = data_confidence(_full_record(indicators=label_only), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 0, "0/20 (بلا حساب صالح)")


def test_46_indicator_none_value_or_status_no_points():
    print("\n[46] شارة بقيمة None أو status None/غير صالح ⇒ لا نقاط:")
    bad_badges = [
        {"label": "RSI", "value": None, "status": "bull"},   # value None
        {"label": "MACD", "value": "x", "status": None},     # status None
        {"label": "EMA", "value": "x", "status": "weird"},   # status غير صالح
    ]
    out = data_confidence(_full_record(indicators=bad_badges), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == 0, "0/20 (كلها غير محسوبة)")


def test_47_indicator_valid_bear_counts():
    print("\n[47] شارة فنية صحيحة هابطة (status=bear) ⇒ تُحتسب كتوافر:")
    denom = len(CONFIDENCE_TECHNICAL_INDICATOR_KEYS)
    out = data_confidence(_full_record(indicators=[_badge("RSI", "25", "bear")]), TODAY)
    check(out["factors"]["technical_indicators"]["points"] == round(20 * 1 / denom),
          f"شارة هابطة واحدة صالحة ⇒ round(20/{denom})")
    check(out["factors"]["technical_indicators"]["points"] > 0, "> 0 (محسوبة رغم الهبوط)")


# ═══════════════ 16) لا استثناء على المدخل التالف كلياً ═══════════════
def test_48_corrupt_inputs_no_exception():
    print("\n[48] مدخلات تالفة بالكامل ⇒ لا استثناء، خرج dict صالح منخفض:")
    corrupt = {
        "catalyst": object(), "catalyst_complete": 3,
        "piotroski_computable": [1, 2], "indicators": "not-a-list",
        "structure": 12345, "frames": "weekly", "money_flow": 3.14,
        "analysis_date": 999, "analysis_close": object(),
    }
    raised = False
    try:
        out = data_confidence(corrupt, TODAY)
    except Exception:  # noqa: BLE001
        raised = True
        out = None
    check(not raised, "لم يُرفع أي استثناء")
    check(isinstance(out, dict) and isinstance(out["score"], int), "خرج dict وscore عدد صحيح")
    check(out["band"] == "low" and out["score"] <= CAP_LOW_MAX, "منخفضة (بيانات تالفة)")
    check(data_confidence(None, TODAY)["band"] == "low", "record=None آمن ⇒ منخفضة")


# ═══════════════ 18) بوابة الركائز الجوهرية (Critical Factor Gate) ═══════════════
def _critical_reason(key):
    return REASON_CRITICAL_PREFIX + FACTOR_LABELS[key]


def test_50_catalyst_zero_caps_medium_raw_unchanged():
    print("\n[50] Catalyst=0 (بقية العوامل كاملة) ⇒ raw=80 لكن final=79 وMedium:")
    out = data_confidence(_full_record(catalyst=None, catalyst_complete=None), TODAY)
    check(out["raw_score"] == 80, f"raw_score يبقى 80 (كان {out['raw_score']})")
    check(out["score"] == 79 and out["band"] == "medium", "final=79 وMedium")
    check(any(c["cap"] == "medium" and _critical_reason("catalyst_completeness") in c["reasons"]
              for c in out["caps_applied"]), "سبب الركيزة (Catalyst) في caps_applied")


def test_51_piotroski_zero_caps_medium():
    print("\n[51] Piotroski=0 ⇒ final=79 وMedium (raw=80):")
    out = data_confidence(_full_record(piotroski_computable=None), TODAY)
    check(out["raw_score"] == 80, "raw=80")
    check(out["score"] == 79 and out["band"] == "medium", "final=79 وMedium")
    check(any(_critical_reason("piotroski_computability") in c["reasons"]
              for c in out["caps_applied"]), "سبب الركيزة (Piotroski) مذكور")


def test_52_technical_zero_caps_medium():
    print("\n[52] Technical Indicators=0 ⇒ final=79 وMedium (raw=80):")
    out = data_confidence(_full_record(indicators=[]), TODAY)
    check(out["raw_score"] == 80, "raw=80")
    check(out["score"] == 79 and out["band"] == "medium", "final=79 وMedium")
    check(any(_critical_reason("technical_indicators") in c["reasons"]
              for c in out["caps_applied"]), "سبب الركيزة (المؤشرات) مذكور")


def test_53_catalyst_half_allows_high():
    print("\n[53] Catalyst=10/20 (المجموع ≥80) ⇒ يُسمح بـHigh:")
    out = data_confidence(_full_record(catalyst=50, catalyst_complete=False), TODAY)  # ⇒ 10
    check(out["factors"]["catalyst_completeness"]["points"] == 10, "Catalyst=10 (نصف)")
    check(out["score"] >= BAND_HIGH_MIN and out["band"] == "high", f"High (score={out['score']})")
    check(all(c["cap"] != "medium" for c in out["caps_applied"]), "لا سقف ركائز (10 ≥ 50%)")


def test_54_piotroski_below_ten_blocks_high():
    print("\n[54] Piotroski < 10 (pc=4 ⇒ 9) يمنع High:")
    out = data_confidence(_full_record(piotroski_computable=4), TODAY)  # 20*4/9≈9
    check(out["factors"]["piotroski_computability"]["points"] == 9, "Piotroski=9 (<10)")
    check(out["band"] != "high" and out["score"] <= CAP_MEDIUM_MAX, "ليست High (سقف 79)")
    check(any(_critical_reason("piotroski_computability") in c["reasons"]
              for c in out["caps_applied"]), "سبب الركيزة مذكور")


def test_55_piotroski_ten_or_more_allows_high():
    print("\n[55] Piotroski ≥ 10 (pc=5 ⇒ 11) يسمح بـHigh حسابيًا:")
    out = data_confidence(_full_record(piotroski_computable=5), TODAY)  # 20*5/9≈11
    check(out["factors"]["piotroski_computability"]["points"] == 11, "Piotroski=11 (≥10)")
    check(out["band"] == "high", "High")


def test_56_technical_below_ten_blocks_high():
    print("\n[56] Technical < 10 (5 مؤشرات ⇒ 9) يمنع High:")
    out = data_confidence(_full_record(indicators=_tech_first(5)), TODAY)  # 20*5/11≈9
    check(out["factors"]["technical_indicators"]["points"] == 9, "Technical=9 (<10)")
    check(out["band"] != "high" and out["score"] <= CAP_MEDIUM_MAX, "ليست High (سقف 79)")
    check(any(_critical_reason("technical_indicators") in c["reasons"]
              for c in out["caps_applied"]), "سبب الركيزة مذكور")


def test_57_technical_ten_or_more_allows_high():
    print("\n[57] Technical ≥ 10 (6 مؤشرات ⇒ 11) يسمح بـHigh حسابيًا:")
    out = data_confidence(_full_record(indicators=_tech_first(6)), TODAY)  # 20*6/11≈11
    check(out["factors"]["technical_indicators"]["points"] == 11, "Technical=11 (≥10)")
    check(out["band"] == "high", "High")


def test_58_multiple_insufficient_all_listed():
    print("\n[58] أكثر من ركيزة جوهرية ناقصة ⇒ كلها في أسباب السقف:")
    out = data_confidence(_full_record(catalyst=None, catalyst_complete=None,
                                       piotroski_computable=None, indicators=[]), TODAY)
    reasons = [r for c in out["caps_applied"] if c["cap"] == "medium" for r in c["reasons"]]
    check(_critical_reason("catalyst_completeness") in reasons, "Catalyst مذكور")
    check(_critical_reason("piotroski_computability") in reasons, "Piotroski مذكور")
    check(_critical_reason("technical_indicators") in reasons, "المؤشرات مذكورة")
    # الثلاثة صفر ⇒ raw=40 فالنتيجة low أصلاً (أقلّ من سقف 79)، والمهم أن الأسباب كلها مسجّلة وليست High.
    check(out["band"] != "high" and out["score"] <= CAP_MEDIUM_MAX, "ليست High (≤79)")


def test_59_low_cap_wins_over_critical_gate():
    print("\n[59] سعر مكسور (49) + ركيزة ناقصة ⇒ الأشدّ 49 (لا سجل Medium منفصل):")
    out = data_confidence(_full_record(catalyst=None, catalyst_complete=None,
                                       analysis_close=0.0), TODAY)
    check(out["score"] <= CAP_LOW_MAX and out["band"] == "low", "منخفضة (49)")
    check(any(c["cap"] == "low" for c in out["caps_applied"]), "سقف low مطبّق")
    check(all(c["cap"] != "medium" for c in out["caps_applied"]), "لا سقف Medium منفصل")


def test_60_full_record_still_100_high():
    print("\n[60] السجل الكامل يظل 100 وHigh (البوابة لا تمسّه):")
    out = data_confidence(_full_record(), TODAY)
    check(out["score"] == 100 and out["band"] == "high", "100 High")
    check(out["caps_applied"] == [], "لا سقوف")


def test_61_gate_does_not_change_raw_or_weights():
    print("\n[61] البوابة لا تغيّر raw_score ولا الأوزان ولا ترفع أي عامل:")
    out = data_confidence(_full_record(indicators=[]), TODAY)  # Technical=0، البوابة فعّالة
    check(out["raw_score"] == 80, "raw_score = 80 (غير متأثّر بالبوابة)")
    check(out["factors"]["technical_indicators"]["points"] == 0, "المؤشرات لم تُرفع (تبقى 0)")
    check(out["factors"]["catalyst_completeness"]["points"] == 20
          and out["factors"]["piotroski_computability"]["points"] == 20,
          "بقية الركائز بلا تغيير (لا إعادة توزيع)")
    check(sum(v for v in FACTOR_WEIGHTS.values()) == 100, "الأوزان ثابتة (المجموع 100)")


def test_62_gate_constants_and_json_serializable():
    print("\n[62] ثوابت البوابة صحيحة وmissing/caps_applied قابلان للتسلسل JSON:")
    check(CRITICAL_FACTOR_KEYS == ("catalyst_completeness", "piotroski_computability", "technical_indicators"),
          "CRITICAL_FACTOR_KEYS الثلاثة")
    check(CRITICAL_FACTOR_MIN_RATIO == 0.5, "CRITICAL_FACTOR_MIN_RATIO = 0.5")
    out = data_confidence(_full_record(catalyst=None, catalyst_complete=None, indicators=[]), TODAY)
    try:
        json.dumps(out["missing"]); json.dumps(out["caps_applied"])
        ok = True
    except (TypeError, ValueError):
        ok = False
    check(ok, "missing و caps_applied قابلان للتسلسل JSON")


def test_63_bands_thresholds_unchanged():
    print("\n[63] النطاقات لم تتغيّر (High≥80 · Medium≥50 · سقوف 79/49):")
    check(BAND_HIGH_MIN == 80 and BAND_MEDIUM_MIN == 50, "عتبات النطاقات 80/50")
    check(CAP_MEDIUM_MAX == 79 and CAP_LOW_MAX == 49, "سقوف 79/49")


# ═══════════════ 19) بنية الاختبار: check() ترفع فعلاً ═══════════════
def test_49_check_raises_on_failure():
    print("\n[49] check() ترفع AssertionError عند الفشل (تُلتقط في pytest والتشغيل المباشر):")
    import contextlib
    import io
    global _passed, _failed
    saved_p, saved_f = _passed, _failed
    raised = False
    # نكتم طباعة الفحص الداخلي حتى لا تظهر «✗» مضلّلة، ونلغي أثره على العدّادات.
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            check(False, "internal-probe")
        except AssertionError:
            raised = True
    _passed, _failed = saved_p, saved_f
    check(raised, "check(False) رفعت AssertionError")


def main():
    print("=" * 60)
    print("PHASE 6 F2 — Data Confidence Core (adversarial)")
    print("=" * 60)
    tests = [
        test_1_full_record_perfect, test_2_weights_sum_100,
        test_3_catalyst_complete_full, test_4_catalyst_partial_half, test_5_catalyst_absent_zero,
        test_6_piotroski_proportional, test_7_piotroski_none_is_zero_not_nine, test_8_piotroski_full_nine,
        test_9_indicators_proportional, test_10_indicators_empty_zero, test_11_indicators_over_cap_not_exceed,
        test_12_structure_present_vs_absent,
        test_13_frames_full, test_14_frames_half_na, test_15_frames_none_zero,
        test_16_flow_present_vs_absent,
        test_17_fresh_full, test_18_stale_medium_cap,
        test_19_missing_date_low_cap, test_20_bad_baseline_price_low_cap, test_21_future_date_low_cap,
        test_22_purity_no_mutation_deterministic_json, test_23_live_price_and_date_forms_and_empty,
        test_24_no_expected_session_capped_79, test_25_invalid_expected_session_capped_79,
        test_26_no_expected_does_not_assume_fresh, test_27_no_expected_plus_bad_price_yields_49,
        test_28_obv_alone_no_technical_points, test_29_flow_scores_by_its_own_rule,
        test_30_obv_and_flow_not_double_counted, test_31_all_allowed_indicators_full_20,
        test_32_unknown_keys_no_points, test_33_denominator_from_fixed_list,
        test_34_score_never_exceeds_100, test_35_other_factors_weights_unchanged,
        test_36_analysis_close_nan_inf_low_cap, test_37_piotroski_adversarial_zero,
        test_38_piotroski_valid_ints_only, test_39_catalyst_adversarial_zero,
        test_40_catalyst_complete_flag_must_be_true_bool,
        test_41_structure_empty_and_unknown_zero, test_42_structure_down_valid_full,
        test_43_money_flow_empty_and_unknown_zero, test_44_money_flow_negative_complete_full,
        test_45_indicator_label_only_no_points, test_46_indicator_none_value_or_status_no_points,
        test_47_indicator_valid_bear_counts,
        test_48_corrupt_inputs_no_exception,
        # بوابة الركائز الجوهرية
        test_50_catalyst_zero_caps_medium_raw_unchanged, test_51_piotroski_zero_caps_medium,
        test_52_technical_zero_caps_medium, test_53_catalyst_half_allows_high,
        test_54_piotroski_below_ten_blocks_high, test_55_piotroski_ten_or_more_allows_high,
        test_56_technical_below_ten_blocks_high, test_57_technical_ten_or_more_allows_high,
        test_58_multiple_insufficient_all_listed, test_59_low_cap_wins_over_critical_gate,
        test_60_full_record_still_100_high, test_61_gate_does_not_change_raw_or_weights,
        test_62_gate_constants_and_json_serializable, test_63_bands_thresholds_unchanged,
        # بنية الاختبار
        test_49_check_raises_on_failure,
    ]
    for fn in tests:
        fn()
    print("\n" + "-" * 60)
    print(f"كل اختبارات F2 نجحت ✓ ({_passed} تحقّقاً).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
