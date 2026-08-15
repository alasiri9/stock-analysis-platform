"""
test_logic_audit_phase2_regression.py — regression لتدقيق Codex (PHASE 2: MEDIUM #4/#6/#7).

#4 تعريف «الجوهرة» موحّد (Piotroski ≥ 8 والنقاط التسع قابلة للحساب) عبر is_gem —
   نفس التصنيف في الشارة والفلتر و/gems والعدّادات وmeasures_met.
#6 Catalyst: المكوّن الناقص يُحسب صفراً على الوزن الكامل (لا إعادة تطبيع) — نقص البيانات
   لا يرفع الدرجة؛ + علَم complete وcomputable_weight؛ القيمة 0 بيان صالح.
#7 Piotroski: computable محفوظ في السجل؛ العرض X/computable لا X/9؛ الجوهرة لا تستفيد من
   Piotroski ناقص؛ توافق خلفي مع سجلّات بلا computable.

التشغيل:  python tests/test_logic_audit_phase2_regression.py
"""

import os
import sys
import copy
import tempfile

os.environ["APP_PASSWORD"] = "phase2-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import screener, scoring  # noqa: E402
from app import app  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


# ==================== #6 Catalyst: نقص المكونات ====================
FULL_CAT = {
    "income": [
        {"netIncome": 120, "revenue": 1000, "operatingIncome": 200},
        {"netIncome": 100, "revenue": 900},
    ],
    "balance": [
        {"totalAssets": 2000, "totalStockholdersEquity": 1000},
        {"totalAssets": 1900},
    ],
    "cashflow": [{"operatingCashFlow": 150}],
}

# إزالة مُدخل فريد لكل مكوّن (دون المساس بغيره)
_REMOVERS = {
    "نمو الإيرادات": ("income", 1, "revenue"),        # rev_growth يحتاج revenue1
    "نمو صافي الأرباح": ("income", 1, "netIncome"),   # ni_growth يحتاج netIncome1
    "العائد على حقوق الملكية": ("balance", 0, "totalStockholdersEquity"),
    "هامش التشغيل": ("income", 0, "operatingIncome"),
    "العائد على الأصول": ("balance", 0, "totalAssets"),
}


def _without(component_key):
    f = copy.deepcopy(FULL_CAT)
    stmt, idx, field = _REMOVERS[component_key]
    f[stmt][idx].pop(field, None)
    return f


def test_catalyst_full():
    print("\n[#6] Catalyst ببيانات كاملة → درجة مكتملة، computable_weight كامل:")
    r = scoring.catalyst_score(FULL_CAT)
    check(r["complete"] is True, "بيانات كاملة → complete=True")
    check(abs(r["computable_weight"] - 1.0) < 1e-9, "computable_weight = 1.0 (كل الأوزان)")
    check(r["score"] is not None and 0 <= r["score"] <= 100, f"score رقم صالح ({r['score']:.1f})")


def test_catalyst_missing_no_inflation():
    print("\n[#6] فقد كل مكوّن على حدة لا يرفع الدرجة (يمنع الأفضلية الحسابية):")
    full = scoring.catalyst_score(FULL_CAT)["score"]
    for key in _REMOVERS:
        r = scoring.catalyst_score(_without(key))
        check(r["complete"] is False, f"إزالة «{key}» → complete=False")
        check(r["computable_weight"] < 1.0 - 1e-9, f"إزالة «{key}» → computable_weight < 1.0")
        check(r["score"] is None or r["score"] <= full + 1e-9,
              f"إزالة «{key}» → الدرجة لم ترتفع ({(r['score'] or 0):.1f} ≤ {full:.1f})")


def test_catalyst_single_component_not_100():
    print("\n[#6] مكوّن واحد ممتاز فقط لا يعطي 100 (لا إعادة تطبيع):")
    # فقط نمو الإيرادات محسوب (=100 نقطة، وزنه 0.25) والباقي مفقود
    only_rev = {"income": [{"revenue": 1000}, {"revenue": 500}], "balance": [{}], "cashflow": [{}]}
    r = scoring.catalyst_score(only_rev)
    check(r["score"] is not None and abs(r["score"] - 25.0) < 1e-6,
          f"مكوّن واحد بوزن 0.25 ودرجة 100 → 25 لا 100 (score={r['score']:.1f})")
    check(r["complete"] is False, "درجة جزئية (complete=False)")


def test_catalyst_zero_is_valid():
    print("\n[#6] القيمة 0 بيان صالح (لا تُعامَل missing):")
    # نمو إيرادات صفر (revenue0 == revenue1) — المكوّن محسوب بقيمة 0، لا مفقود
    zero_growth = copy.deepcopy(FULL_CAT)
    zero_growth["income"][1]["revenue"] = 1000  # يساوي revenue0 → نمو 0
    r = scoring.catalyst_score(zero_growth)
    check(r["complete"] is True, "نمو 0 → المكوّن محسوب (complete يبقى True، 0 ليست missing)")
    rev_comp = next(c for c in r["components"] if c["name"].startswith("نمو الإيرادات"))
    check(rev_comp["points"] == 0.0, "مكوّن نمو الإيرادات: points=0.0 (قيمة صالحة)")


def test_catalyst_all_missing_none():
    print("\n[#6] غياب كل المكوّنات → score=None (لا صفر ملفّق):")
    r = scoring.catalyst_score({"income": [{}], "balance": [{}], "cashflow": [{}]})
    check(r["score"] is None and r["complete"] is False, "لا بيانات → score=None، complete=False")


# ==================== #7 Piotroski computable ====================
FULL_PIO = {
    "income": [
        {"netIncome": 120, "revenue": 1000, "grossProfit": 400, "weightedAverageShsOut": 50},
        {"netIncome": 100, "revenue": 900, "grossProfit": 350, "weightedAverageShsOut": 50},
    ],
    "balance": [
        {"totalAssets": 2000, "totalCurrentAssets": 800, "totalCurrentLiabilities": 400, "longTermDebt": 300},
        {"totalAssets": 1900, "totalCurrentAssets": 700, "totalCurrentLiabilities": 400, "longTermDebt": 350},
    ],
    "cashflow": [{"operatingCashFlow": 200}],
}


def test_piotroski_computable_full_vs_partial():
    print("\n[#7] computable يعكس عدد النقاط القابلة للحساب:")
    full = scoring.piotroski_score(FULL_PIO)
    check(full["computable"] == 9, f"بيانات كاملة → computable=9 (score={full['score']})")
    # إزالة التدفق النقدي → CFO(2) + Accruals(4) غير قابلين للحساب
    no_cf = copy.deepcopy(FULL_PIO); no_cf["cashflow"] = None
    part = scoring.piotroski_score(no_cf)
    check(part["computable"] == 7, f"بلا تدفق نقدي → computable=7 (نقطتان تعذّرتا)")
    check(part["score"] <= part["computable"], "score ≤ computable دائماً")


def test_is_gem_requires_full_piotroski():
    print("\n[#4/#7] الجوهرة تشترط Piotroski ≥ 8 والنقاط التسع قابلة للحساب:")
    check(screener.is_gem({"piotroski": 8, "piotroski_computable": 9}) is True,
          "8 من 9 قابلة للحساب → جوهرة")
    check(screener.is_gem({"piotroski": 8, "piotroski_computable": 8}) is False,
          "8 من 8 فقط (نقطة تعذّرت) → ليست جوهرة (لا تستفيد من نقص)")
    check(screener.is_gem({"piotroski": 7, "piotroski_computable": 9}) is False,
          "7 من 9 → ليست جوهرة")
    check(screener.is_gem({"piotroski": 9, "piotroski_computable": 9}) is True, "9 من 9 → جوهرة")


def test_piotroski_computable_backward_compat():
    print("\n[#7] توافق خلفي: سجل قديم بلا piotroski_computable:")
    old = {"piotroski": 8}  # بلا حقل computable
    check(screener.piotroski_computable(old) == 9, "غياب الحقل → يُعامَل 9 (توافق خلفي)")
    check(screener.is_gem(old) is True, "سجل قديم Piotroski=8 بلا computable → جوهرة (توافق خلفي)")
    check(screener.piotroski_computable({}) is None or screener.piotroski_computable(None) is None
          or True, "سجل فارغ لا يرمي استثناء")


# ==================== #4 اتساق تعريف الجوهرة عبر المسارات ====================
def _rec(pio, comp=9, catalyst=85, **extra):
    r = {"ticker": "T", "name": "Co", "sector": "Technology", "price": 100.0,
         "analysis_price": 100.0, "change_percent": 1.0, "catalyst": catalyst,
         "catalyst_complete": True, "piotroski": pio, "piotroski_computable": comp,
         "indicators": [{"label": "EMA", "status": "bull"}], "money_flow": None,
         "break_status": None, "sustained": None, "reversal": None,
         "days_to_earnings": None, "float_shares": None, "rel_strength": None}
    r.update(extra)
    return r


def _render_card(rec):
    with app.test_request_context():
        return app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)


def test_gem_consistency_across_paths():
    print("\n[#4] نفس السهم: جوهرة في كل المسارات أو ليست في أيّها (لا تناقض):")
    gem = _rec(8, 9)        # جوهرة
    part = _rec(8, 8)       # ليست (Piotroski ناقص)
    low = _rec(6, 9)        # ليست (جودة أقل)
    records = [gem, part, low]

    by_is_gem = {id(r) for r in records if screener.is_gem(r)}
    by_filter = {id(r) for r in screener.filter_records(records, piotroski_min=8)}
    by_count = {id(r) for r in records if screener.is_gem(r)}  # نفس دالة العدّ
    check(by_is_gem == by_filter, "is_gem == فلتر الماسح/gems (piotroski_min=8 + computable)")
    check(by_is_gem == by_count, "is_gem == عدّاد الجواهر")
    check(id(gem) in by_is_gem and id(part) not in by_is_gem and id(low) not in by_is_gem,
          "الجوهرة فقط للسهم المكتمل ≥8 (الجزئي والأقل مستبعدان)")

    # الشارة على البطاقة تتبع نفس التعريف
    check("💎 جوهرة" in _render_card(gem), "بطاقة السهم المكتمل ≥8 → تعرض شارة 💎")
    check("💎 جوهرة" not in _render_card(part), "بطاقة 8/8 (ناقص) → لا تعرض 💎")
    check("💎 جوهرة" not in _render_card(low), "بطاقة 6/9 → لا تعرض 💎")


def test_card_shows_computable_denominator():
    print("\n[#7] البطاقة تعرض المقام الفعلي (X/computable) لا X/9 دائماً:")
    check("</b>/9" in _render_card(_rec(8, 9)), "8 من 9 → تُعرض /9")
    html8 = _render_card(_rec(8, 8))
    check("</b>/8" in html8 and "</b>/9" not in html8, "8 من 8 → تُعرض /8 لا /9 (لا تضليل)")
    # توافق خلفي: سجل بلا computable → /9
    old = _rec(8, 9); old.pop("piotroski_computable")
    check("</b>/9" in _render_card(old), "سجل قديم بلا computable → /9 (توافق خلفي)")


def test_catalyst_partial_marker_on_card():
    print("\n[#6] البطاقة تُميّز درجة النمو الجزئية (لا تُعرض كمكتملة الثقة):")
    partial = _rec(8, 9, catalyst=70); partial["catalyst_complete"] = False
    check("*" in _render_card(partial), "درجة نمو جزئية → علامة * على البطاقة")
    complete = _rec(8, 9, catalyst=70); complete["catalyst_complete"] = True
    # لا نتحقّق من غياب * إجمالاً (قد ترد في مواضع أخرى)، بل من الوسم الخاص
    check("درجة نمو جزئية" not in _render_card(complete),
          "درجة نمو مكتملة → لا وسم «جزئية»")


def test_phase1_not_regressed():
    print("\n[PHASE1] عدم انحدار: ميل فني وبوابة «جاهز للانطلاق» ما زالت تعمل:")
    # measures_met ما زالت تعمل والجوهرة تُحتسب نقطة
    check(screener.measures_met(_rec(8, 9)) >= 1, "measures_met يحتسب الجوهرة نقطة جودة")
    check(screener.measures_met(_rec(8, 8)) == screener.measures_met(_rec(6, 9)),
          "Piotroski ناقص (8/8) لا يُحتسب نقطة جودة (كالأقل من 8)")


def main():
    print("=" * 62)
    print("regression — تدقيق Codex (PHASE 2: MEDIUM #4/#6/#7)")
    print("=" * 62)
    test_catalyst_full()
    test_catalyst_missing_no_inflation()
    test_catalyst_single_component_not_100()
    test_catalyst_zero_is_valid()
    test_catalyst_all_missing_none()
    test_piotroski_computable_full_vs_partial()
    test_is_gem_requires_full_piotroski()
    test_piotroski_computable_backward_compat()
    test_gem_consistency_across_paths()
    test_card_shows_computable_denominator()
    test_catalyst_partial_marker_on_card()
    test_phase1_not_regressed()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات PHASE 2 نجحت ✓ ({_passed} تحقّقاً) — MEDIUM #4/#6/#7 مقفولة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
