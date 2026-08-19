"""
test_phase6_confidence_view.py — PHASE 6 F2 (Data Confidence UI View Model).

يقفل مقدّم العرض النقي services.confidence_view.present_confidence:
- high/medium/low ⇒ تسمية/فئة صحيحة + score_text + explanation.
- None/غير dict/unavailable/schema غير مدعوم/score تالف/band غير صالح/factors مفقودة ⇒ «غير متوفرة» بلا استثناء.
- تحقّق رقمي صارم (bool/NaN/±∞/خارج 0–100)، ترتيب العوامل السبعة ثابت، pct صحيح، critical_below_half صحيح.
- لا يعدّل المدخل، حتمي، JSON-serializable، وبلا DB/Flask/API/live_price/إعادة حساب.

التشغيل:  python tests/test_phase6_confidence_view.py
          python -m pytest tests/test_phase6_confidence_view.py -q
"""

import copy
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.confidence import data_confidence, CONFIDENCE_TECHNICAL_INDICATOR_KEYS  # noqa: E402
from services import confidence_view as cv  # noqa: E402

_passed = 0
_failed = 0
TODAY = date(2026, 8, 17)


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")
        raise AssertionError(label)


def _tech():
    return [{"label": k, "value": "x", "status": "bull"} for k in CONFIDENCE_TECHNICAL_INDICATOR_KEYS]


def _record(**over):
    r = {
        "catalyst": 72, "catalyst_complete": True,
        "piotroski_computable": 9, "indicators": _tech(),
        "structure": {"trend": "up", "status": "bull"},
        "frames": {"weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull"},
        "analysis_date": "2026-08-17", "analysis_close": 100.0,
    }
    r.update(over)
    return r


def _dc_high():
    return data_confidence(_record(), TODAY)                      # 100 high


def _dc_medium():
    return data_confidence(_record(analysis_date="2026-08-14"), TODAY)  # stale ⇒ ≤79 medium


def _dc_low():
    return data_confidence(_record(analysis_close=0.0), TODAY)    # bad price ⇒ ≤49 low


# ═══════════════ 1) high / medium / low ═══════════════
def test_1_high():
    print("\n[1] high ⇒ «ثقة عالية» / conf-high / score_text:")
    v = cv.present_confidence(_dc_high(), TODAY)
    check(v["available"] is True, "متوفرة")
    check(v["band"] == "high" and v["band_label"] == "ثقة عالية" and v["band_class"] == "conf-high", "تصنيف high")
    check(v["score"] == 100 and v["score_text"] == "100/100", "score_text = 100/100")
    check(v["explanation"] == cv.EXPLANATION_TEXT, "نص التوضيح موجود")
    check(v["as_of"] == "2026-08-17", "as_of من snap_date")
    check(v["reason_code"] is None, "لا سبب عند التوفّر")


def test_2_medium():
    print("\n[2] medium ⇒ «ثقة متوسطة» / conf-medium:")
    v = cv.present_confidence(_dc_medium(), TODAY)
    check(v["available"] and v["band"] == "medium", "band medium")
    check(v["band_label"] == "ثقة متوسطة" and v["band_class"] == "conf-medium", "تسمية/فئة medium")
    check(v["score"] <= 79, "score ≤ 79")


def test_3_low():
    print("\n[3] low ⇒ «ثقة منخفضة» / conf-low:")
    v = cv.present_confidence(_dc_low(), TODAY)
    check(v["available"] and v["band"] == "low", "band low")
    check(v["band_label"] == "ثقة منخفضة" and v["band_class"] == "conf-low", "تسمية/فئة low")
    check(v["score"] <= 49, "score ≤ 49")


# ═══════════════ 2) unavailable / None / غير dict ═══════════════
def test_4_none_and_non_dict():
    print("\n[4] None / غير dict ⇒ غير متوفرة (missing) بلا استثناء:")
    for bad in (None, 42, "x", [1, 2], object()):
        v = cv.present_confidence(bad, TODAY)
        check(v["available"] is False, f"{type(bad).__name__} ⇒ غير متوفرة")
        check(v["band_label"] == "درجة الثقة غير متوفرة" and v["band_class"] == "conf-na", "تسمية/فئة na")
        check(v["reason_code"] == cv.REASON_MISSING, "reason_code = missing")
        check(v["score"] is None and v["factors"] == [], "لا score ولا عوامل")


def test_5_unavailable_flag_preserves_reason():
    print("\n[5] unavailable=True (schema=1) ⇒ غير متوفرة مع reason_code المخزّن:")
    stored = {"schema_version": 1, "unavailable": True, "reason_code": "confidence_computation_failed"}
    v = cv.present_confidence(stored, TODAY)
    check(v["available"] is False, "غير متوفرة")
    check(v["reason_code"] == "confidence_computation_failed", "احتفظ بالسبب المخزّن")
    check(v["schema_version"] == 1, "schema_version = 1")


def test_6_unavailable_flag_without_valid_reason():
    print("\n[6] unavailable=True بلا reason نصي صالح ⇒ «unavailable» الثابت:")
    for rc in (None, "", "   ", 5, ["x"]):
        v = cv.present_confidence({"schema_version": 1, "unavailable": True, "reason_code": rc}, TODAY)
        check(v["reason_code"] == cv.REASON_UNAVAILABLE, f"reason={rc!r} ⇒ unavailable")


# ═══════════════ 3) فحص schema أوّلاً (قبل unavailable) ═══════════════
def test_7_unsupported_schema():
    print("\n[7] schema_version مفقود/bool/غير مدعوم ⇒ unsupported_schema (فحص أوّلاً):")
    base = _dc_high()
    for sv in (2, 99, 0, None, "1", True, 1.0):
        bad = dict(base); bad["schema_version"] = sv
        v = cv.present_confidence(bad, TODAY)
        check(v["available"] is False and v["reason_code"] == cv.REASON_UNSUPPORTED_SCHEMA,
              f"schema={sv!r} ⇒ unsupported_schema")


def test_7b_unavailable_with_bad_schema_is_unsupported():
    print("\n[7b] unavailable=True مع schema=99 ⇒ unsupported_schema (schema قبل unavailable):")
    v = cv.present_confidence(
        {"schema_version": 99, "unavailable": True, "reason_code": "confidence_computation_failed"}, TODAY)
    check(v["available"] is False and v["reason_code"] == cv.REASON_UNSUPPORTED_SCHEMA,
          "schema يُفحص قبل unavailable")
    check(v["schema_version"] == 99, "echo 99")


def test_7c_schema_true_rejected():
    print("\n[7c] schema=True مرفوض (True==1 في set) ⇒ unsupported_schema:")
    bad = dict(_dc_high()); bad["schema_version"] = True
    v = cv.present_confidence(bad, TODAY)
    check(v["available"] is False and v["reason_code"] == cv.REASON_UNSUPPORTED_SCHEMA, "True مرفوض")


# ═══════════════ 4) score/raw/final أعداد صحيحة حصراً ومتّسقة ═══════════════
def test_8_corrupt_score():
    print("\n[8] score تالف/bool/float(3.0,79.9)/NaN/∞/خارج 0–100 ⇒ corrupt:")
    base = _dc_high()
    for sc in (None, True, False, 3.0, 79.9, float("nan"), float("inf"), float("-inf"), -1, 101, "90", [90]):
        bad = copy.deepcopy(base); bad["score"] = sc; bad["final_score"] = sc
        v = cv.present_confidence(bad, TODAY)
        check(v["available"] is False and v["reason_code"] == cv.REASON_CORRUPT, f"score={sc!r} ⇒ corrupt")


def test_8b_raw_and_final_consistency():
    print("\n[8b] raw≠Σالعوامل · final≠score · score>raw ⇒ corrupt:")
    # raw ≠ مجموع العوامل (نُنقص عاملاً بلا تعديل raw)
    b1 = copy.deepcopy(_dc_high()); b1["factors"]["freshness"]["points"] = 5   # الآن Σ=95 ≠ raw=100
    check(cv.present_confidence(b1, TODAY)["reason_code"] == cv.REASON_CORRUPT, "raw≠Σ ⇒ corrupt")
    # final ≠ score
    b2 = copy.deepcopy(_dc_high()); b2["final_score"] = 99
    check(cv.present_confidence(b2, TODAY)["reason_code"] == cv.REASON_CORRUPT, "final≠score ⇒ corrupt")
    # score > raw (نجعل Σ=90=raw، score=100)
    b3 = copy.deepcopy(_dc_high())
    b3["factors"]["freshness"]["points"] = 0; b3["raw_score"] = 90            # Σ=90=raw، score=100>90
    check(cv.present_confidence(b3, TODAY)["reason_code"] == cv.REASON_CORRUPT, "score>raw ⇒ corrupt")
    # raw خارج 0–100
    b4 = copy.deepcopy(_dc_high()); b4["raw_score"] = 101
    check(cv.present_confidence(b4, TODAY)["reason_code"] == cv.REASON_CORRUPT, "raw>100 ⇒ corrupt")
    # raw/final أعداد صحيحة حصراً
    for key in ("raw_score", "final_score"):
        b = copy.deepcopy(_dc_high()); b[key] = 3.0
        check(cv.present_confidence(b, TODAY)["reason_code"] == cv.REASON_CORRUPT, f"{key}=3.0 ⇒ corrupt")


# ═══════════════ 5) band يجب أن يطابق حدود score ═══════════════
def test_9_band_must_match_score():
    print("\n[9] band غير صالح أو غير مطابق لحدود score ⇒ corrupt:")
    base = _dc_high()  # score=100 ⇒ band يجب high
    # يشمل قيماً غير قابلة للـhash (list/dict) — يجب ألّا ترفع TypeError على membership.
    for b in ("HIGH", "great", None, 1, "", "low", "medium", [], {}, ["high"], {"x": 1}):
        bad = dict(base); bad["band"] = b
        try:
            v = cv.present_confidence(bad, TODAY)
            raised = False
        except Exception:  # noqa: BLE001
            raised = True; v = None
        check(not raised, f"band={b!r} لم يرفع استثناء")
        check(v["reason_code"] == cv.REASON_CORRUPT, f"band={b!r} ⇒ corrupt")
    # score=90 مع band=low ⇒ تعارض
    conflict = copy.deepcopy(base); conflict["band"] = "low"
    check(cv.present_confidence(conflict, TODAY)["reason_code"] == cv.REASON_CORRUPT, "score=100+band=low ⇒ corrupt")
    # التطابق الصحيح لا يُرفض (medium من سجل stale الحقيقي)
    check(cv.present_confidence(_dc_medium(), TODAY)["available"] is True, "medium متّسق ⇒ متوفرة")


# ═══════════════ 6) نقاط العوامل أعداد صحيحة حصراً ═══════════════
def test_10_factors_missing_or_corrupt():
    print("\n[10] factors مفقودة/ليست dict/عامل ناقص/نقاط كسرية/bool/نص ⇒ corrupt:")
    base = _dc_high()
    for f in (None, [], "x", 5):
        bad = dict(base); bad["factors"] = f
        check(cv.present_confidence(bad, TODAY)["reason_code"] == cv.REASON_CORRUPT, f"factors={f!r}")
    miss = dict(base); miss["factors"] = dict(base["factors"]); miss["factors"].pop("freshness")
    check(cv.present_confidence(miss, TODAY)["reason_code"] == cv.REASON_CORRUPT, "عامل مفقود")
    # نقاط كسرية/bool/نص/خارج الحد ⇒ corrupt (لا تقريب)
    for badpts in (4.5, 3.0, True, "5", None, float("nan"), 999, -1):
        b2 = copy.deepcopy(base)
        b2["factors"]["catalyst_completeness"]["points"] = badpts
        check(cv.present_confidence(b2, TODAY)["reason_code"] == cv.REASON_CORRUPT, f"points={badpts!r}")


# ═══════════════ 7) ترتيب العوامل السبعة ثابت ═══════════════
def test_11_factor_order_frozen():
    print("\n[11] ترتيب العوامل السبعة ثابت مهما كان ترتيب المخزّن:")
    base = _dc_high()
    # نعيد بناء factors بترتيب معكوس
    scrambled = dict(base)
    items = list(base["factors"].items())
    scrambled["factors"] = dict(reversed(items))
    v = cv.present_confidence(scrambled, TODAY)
    keys = [f["key"] for f in v["factors"]]
    expected = ["catalyst_completeness", "piotroski_computability", "technical_indicators",
                "structure_availability", "frames_availability", "flow_availability", "freshness"]
    check(keys == expected, "الترتيب المجمّد لـschema 1")
    check(len(v["factors"]) == 7, "سبعة عوامل بالضبط")


# ═══════════════ 8) pct صحيح + critical_below_half ═══════════════
def test_12_pct_and_critical_flags():
    print("\n[12] pct صحيح + critical/critical_below_half صحيح:")
    v = cv.present_confidence(_dc_high(), TODAY)
    fmap = {f["key"]: f for f in v["factors"]}
    check(fmap["catalyst_completeness"]["pct"] == 100 and fmap["catalyst_completeness"]["max"] == 20, "pct=100 لعامل كامل")
    check(fmap["flow_availability"]["max"] == 5, "حد flow = 5")
    # الركائز الثلاث critical
    for k in ("catalyst_completeness", "piotroski_computability", "technical_indicators"):
        check(fmap[k]["critical"] is True, f"{k} critical")
    for k in ("structure_availability", "frames_availability", "flow_availability", "freshness"):
        check(fmap[k]["critical"] is False, f"{k} ليست critical")
    # سجل كامل ⇒ لا شيء تحت النصف
    check(all(not f["critical_below_half"] for f in v["factors"]), "لا critical_below_half في السجل الكامل")


def test_13_critical_below_half_true():
    print("\n[13] ركيزة جوهرية < 50% ⇒ critical_below_half=True (والنسبة صحيحة):")
    # technical_indicators=0 ⇒ ركيزة تحت النصف (والنتيجة medium بالبوابة)
    dc = data_confidence(_record(indicators=[]), TODAY)
    v = cv.present_confidence(dc, TODAY)
    fmap = {f["key"]: f for f in v["factors"]}
    ti = fmap["technical_indicators"]
    check(ti["points"] == 0 and ti["pct"] == 0, "المؤشرات 0/20 ⇒ pct=0")
    check(ti["critical_below_half"] is True, "critical_below_half=True")
    # عامل جزئي: catalyst 10/20 = 50% ⇒ ليست تحت النصف (الحد صارم <)
    dc2 = data_confidence(_record(catalyst=50, catalyst_complete=False), TODAY)
    v2 = cv.present_confidence(dc2, TODAY)
    cat = {f["key"]: f for f in v2["factors"]}["catalyst_completeness"]
    check(cat["points"] == 10 and cat["pct"] == 50, "catalyst 10/20 ⇒ pct=50")
    check(cat["critical_below_half"] is False, "50% ليست < النصف")


# ═══════════════ 9) missing / caps_applied آمنة ═══════════════
def test_14_missing_and_caps_safe():
    print("\n[14] missing/caps_applied آمنة (تمرّر الصالح، تتجاهل التالف):")
    v = cv.present_confidence(_dc_medium(), TODAY)
    check(isinstance(v["missing"], list) and all(isinstance(x, str) for x in v["missing"]), "missing قائمة نصوص")
    check(isinstance(v["caps_applied"], list), "caps_applied قائمة")
    check(any(c.get("cap") == "medium" for c in v["caps_applied"]), "سقف medium مذكور")
    # مدخل بـmissing/caps تالفة ⇒ لا استثناء، تُطهَّر
    base = _dc_high()
    b = dict(base)
    b["missing"] = "notalist"
    b["caps_applied"] = [
        None, "x",                                        # سجلّات غير dict ⇒ تُتجاهل
        {"cap": "low", "reasons": "bad"},                 # max مفقود ⇒ يُتجاهل
        {"cap": "medium", "max": 79.0, "reasons": []},    # max كسري ⇒ يُتجاهل
        {"cap": "weird", "max": 60, "reasons": []},       # cap مجهول ⇒ يُتجاهل
        {"cap": "medium", "max": 79, "reasons": ["سبب صالح", "", 5]},  # صالح: يُطهَّر الأسباب
    ]
    v2 = cv.present_confidence(b, TODAY)
    check(v2["missing"] == [], "missing غير القائمة ⇒ []")
    check(v2["caps_applied"] == [{"cap": "medium", "max": 79, "reasons": ["سبب صالح"]}],
          "caps: يبقى الصالح فقط بأسباب مطهّرة (max كسري/cap مجهول/بلا max مُتجاهَلة)")


def test_14b_caps_official_max_only():
    print("\n[14b] caps: تُقبل القيم الرسمية فقط (low=49، medium=79)؛ أي غيرها يُتجاهل:")
    base = _dc_high()
    def caps_of(cap_list):
        bb = dict(base); bb["caps_applied"] = cap_list
        return cv.present_confidence(bb, TODAY)["caps_applied"]
    # القيم الرسمية ⇒ تبقى
    check(caps_of([{"cap": "low", "max": 49}]) == [{"cap": "low", "max": 49, "reasons": []}], "low=49 يبقى")
    check(caps_of([{"cap": "medium", "max": 79}]) == [{"cap": "medium", "max": 79, "reasons": []}], "medium=79 يبقى")
    # max غير رسمي ⇒ يُتجاهل (لا تصحيح)
    for bad in ([{"cap": "low", "max": 79}], [{"cap": "medium", "max": 49}],
                [{"cap": "low", "max": 48}], [{"cap": "medium", "max": 80}],
                [{"cap": "low", "max": 49.0}], [{"cap": "medium", "max": True}],
                [{"cap": "low", "max": "49"}], [{"cap": "high", "max": 49}]):
        check(caps_of(bad) == [], f"{bad[0]!r} ⇒ يُتجاهل")
    # cap غير قابل للـhash (list/dict) أو رقم/None ⇒ يُتجاهل بأمان بلا TypeError، والعرض يبقى available
    unhashable = [{"cap": [], "max": 49}, {"cap": {}, "max": 79},
                  {"cap": 1, "max": 49}, {"cap": None, "max": 79}]
    bb = dict(base); bb["caps_applied"] = unhashable + [{"cap": "low", "max": 49, "reasons": ["ok"]}]
    try:
        v = cv.present_confidence(bb, TODAY); raised = False
    except Exception:  # noqa: BLE001
        raised = True; v = None
    check(not raised, "cap غير قابل للـhash لم يرفع استثناء")
    check(v["available"] is True, "بقية البيانات صحيحة ⇒ available")
    check(v["caps_applied"] == [{"cap": "low", "max": 49, "reasons": ["ok"]}],
          "التالفة (cap=[]/{}/1/None) مُتجاهَلة، والصالح يبقى")


# ═══════════════ 10) as_of: نص YYYY-MM-DD بطول 10 بالضبط، أو date/datetime حقيقي ═══════════════
def test_15_as_of_forms():
    print("\n[15] as_of: نص 10 محارف صالح فقط · datetime حقيقي مقبول · أي لاحقة/تالف ⇒ None:")
    dc = _dc_high()
    check(cv.present_confidence(dc, date(2026, 8, 17))["as_of"] == "2026-08-17", "date")
    check(cv.present_confidence(dc, datetime(2026, 8, 17, 9, 30))["as_of"] == "2026-08-17", "datetime حقيقي")
    check(cv.present_confidence(dc, "2026-08-17")["as_of"] == "2026-08-17", "نص 10 محارف صالح")
    check(cv.present_confidence(dc, None)["as_of"] is None, "None")
    check(cv.present_confidence(dc, 12345)["as_of"] is None, "نوع غير صالح ⇒ None")
    # نصوص مرفوضة (طول ≠ 10 أو تاريخ غير حقيقي) — بلا [:10] قبل التحقّق
    for bad in ("2026-08-17garbage", "2026-08-17T12:00", "2026-08-17 ", " 2026-08-17",
                "2026-99-99", "2026-13-01", "garbage", "20260817", ""):
        check(cv.present_confidence(dc, bad)["as_of"] is None, f"نص مرفوض {bad!r} ⇒ None")


# ═══════════════ 11) لا تعديل للمدخل · حتمية · JSON ═══════════════
def test_16_no_mutation_deterministic_json():
    print("\n[16] لا تعديل للمدخل · نفس الخرج مرتين · JSON-serializable:")
    dc = _dc_high()
    snap = copy.deepcopy(dc)
    v1 = cv.present_confidence(dc, TODAY)
    v2 = cv.present_confidence(dc, TODAY)
    check(dc == snap, "المدخل لم يُعدَّل")
    check(v1 == v2, "حتمية (نفس الخرج)")
    try:
        json.dumps(v1); ok = True
    except (TypeError, ValueError):
        ok = False
    check(ok, "الخرج قابل للتسلسل JSON")
    # حتى المدخل التالف: لا تعديل + JSON
    corrupt = {"schema_version": 1, "score": float("nan"), "band": "x"}
    cs = copy.deepcopy(corrupt)
    vc = cv.present_confidence(corrupt, TODAY)
    check(corrupt == cs, "مدخل تالف لم يُعدَّل")
    json.dumps(vc)  # لا استثناء
    check(True, "خرج التالف JSON-serializable")


# ═══════════════ 12) نقاء المصدر: لا DB/Flask/API/live_price/إعادة حساب ═══════════════
def test_17_module_purity():
    print("\n[17] الوحدة نقية: لا استيراد DB/Flask/API + live_price لا يؤثّر:")
    import types
    # الوحدات المستوردة فعلياً في مجال الملف ⊆ stdlib نقية (لا flask/models/requests/fmp/db...).
    imported = {n for n, val in vars(cv).items() if isinstance(val, types.ModuleType)}
    _ALLOWED = {"math", "datetime", "json"}   # json نقية (stdlib) — تُستخدم لفكّ extra_json داخل المقدّم
    _BANNED = {"flask", "models", "requests", "fmp_client", "finnhub_client", "screener", "app", "db"}
    check(imported <= _ALLOWED, f"الوحدات المستوردة ⊆ stdlib نقية (كان {sorted(imported)})")
    check(imported.isdisjoint(_BANNED), "لا استيراد DB/Flask/API/screener")
    check("data_confidence" not in vars(cv), "لا استيراد لدالة data_confidence (لا إعادة حساب)")
    # live_price لا يؤثّر (المقدّم لا يقرأه أصلاً — نمرّر مخزّناً فيه المفتاح فلا يتغيّر الخرج)
    dc = _dc_high()
    dc_with = dict(dc); dc_with["live_price"] = 999.0
    check(cv.present_confidence(dc, TODAY) == cv.present_confidence(dc_with, TODAY),
          "مفتاح live_price في المدخل لا يغيّر الخرج")


# ═══════════════ 13) present_confidence_from_extra_json: تصنيف missing/corrupt نقي (المنقول من tracking) ═══════════════
def test_18_present_confidence_from_extra_json():
    print("\n[18] present_confidence_from_extra_json: missing/corrupt/valid + RecursionError، دالة نقية:")
    pcx = cv.present_confidence_from_extra_json
    # ── missing: None / نص فارغ / dict بلا المفتاح ──
    for raw in (None, "", "   ", "\n\t"):
        v = pcx(raw, TODAY)
        check(v["available"] is False and v["reason_code"] == cv.REASON_MISSING, f"{raw!r} ⇒ missing")
        check(v["band_class"] == "conf-na", f"{raw!r} ⇒ conf-na")
    check(pcx(json.dumps({"foo": "bar"}), TODAY)["reason_code"] == cv.REASON_MISSING,
          "dict صالح بلا data_confidence ⇒ missing")
    # ── corrupt: JSON تالف / أعلى مستواه ليس dict / data_confidence ليس dict ──
    for raw in ("{bad json ::", "[1,2,3]", '"a string"', "42", "true", "null"):
        v = pcx(raw, TODAY)
        check(v["available"] is False and v["reason_code"] == cv.REASON_CORRUPT, f"{raw!r} ⇒ corrupt")
    for badval in (42, [1, 2], "x", None, True):
        check(pcx(json.dumps({"data_confidence": badval}), TODAY)["reason_code"] == cv.REASON_CORRUPT,
              f"data_confidence={badval!r} (ليس dict) ⇒ corrupt")
    # ── data_confidence هو dict لكنه تالف بنيوياً ⇒ corrupt (عبر present_confidence) ──
    v = pcx(json.dumps({"data_confidence": {"schema_version": 1, "score": 999, "band": "high"}}), TODAY)
    check(v["reason_code"] == cv.REASON_CORRUPT, "data_confidence dict تالف بنيوياً ⇒ corrupt")
    # ── نوع غير نصّي (دفاعي لعمود Text) ⇒ corrupt ──
    check(pcx(123, TODAY)["reason_code"] == cv.REASON_CORRUPT, "نوع غير نصّي ⇒ corrupt (دفاعي)")
    # ── valid ⇒ present_confidence (high) + as_of من snap_date ──
    good = json.dumps({"data_confidence": _dc_high()})
    v = pcx(good, date(2026, 8, 17))
    check(v["available"] is True and v["band"] == "high", "صالح ⇒ high")
    check(v["as_of"] == "2026-08-17", "as_of من snap_date")
    # ── RecursionError عند فكّ JSON ⇒ corrupt بلا استثناء (لا MemoryError) ──
    orig = cv.json.loads
    def boom(*a, **k):
        raise RecursionError("maximum recursion depth exceeded")
    cv.json.loads = boom
    try:
        v = pcx('{"data_confidence": {}}', TODAY)
    finally:
        cv.json.loads = orig
    check(v["available"] is False and v["reason_code"] == cv.REASON_CORRUPT, "RecursionError ⇒ corrupt")
    # ── نقاء: لا تعديل مدخل، حتمية، JSON-serializable ──
    v1 = pcx(good, TODAY); v2 = pcx(good, TODAY)
    check(v1 == v2, "حتمية")
    json.dumps(pcx(None, TODAY)); json.dumps(v1)   # لا استثناء
    check(True, "الخرج JSON-serializable")


def main():
    print("=" * 60)
    print("PHASE 6 F2 — Data Confidence UI View Model")
    print("=" * 60)
    tests = [
        test_1_high, test_2_medium, test_3_low,
        test_4_none_and_non_dict, test_5_unavailable_flag_preserves_reason,
        test_6_unavailable_flag_without_valid_reason,
        test_7_unsupported_schema, test_7b_unavailable_with_bad_schema_is_unsupported,
        test_7c_schema_true_rejected,
        test_8_corrupt_score, test_8b_raw_and_final_consistency,
        test_9_band_must_match_score, test_10_factors_missing_or_corrupt,
        test_11_factor_order_frozen, test_12_pct_and_critical_flags, test_13_critical_below_half_true,
        test_14_missing_and_caps_safe, test_14b_caps_official_max_only, test_15_as_of_forms,
        test_16_no_mutation_deterministic_json, test_17_module_purity,
        test_18_present_confidence_from_extra_json,
    ]
    for fn in tests:
        fn()
    print("\n" + "-" * 60)
    print(f"كل اختبارات F2 View نجحت ✓ ({_passed} تحقّقاً).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
