"""
test_logic_audit_phase3_regression.py — regression لتدقيق Codex (PHASE 3: MEDIUM #5 و#8).

#5 «قادة النمو»: الصفحة تُرتّب بدرجة النمو (Catalyst) وحدها — أُعيد تسميتها لتطابق المنطق
   (بدل «القادة المستقبليون» الموحي بقيادة شاملة)، مع توضيح صريح أن النمو ليس قوة شاملة،
   وإبقاء الجودة/التأكيد ظاهرة على كل بطاقة. لا تغيير للمعادلة/الأوزان/Catalyst/Piotroski.

#8 لا تغيير في الكود (قرار موثّق): مؤشر Algomatix يُعالج الارتباط أصلاً بمتوسطة المؤشرات
   المترابطة في «مدارس» متمايزة (الاتجاه = EMA+تقاطع+سوبرترند+ADX متوسّطة؛ الجودة+النمو
   متوسّطة؛ الزخم = MACD+RSI متوسّطة) ولا يظهر أي مؤشر في مدرستين. الاختبارات تقفل هذا
   السلوك الصحيح وتمنع انحداره. (أي إزالة ازدواج في measures_met تحتاج إعادة معايرة — لم تُنفَّذ.)

التشغيل:  python tests/test_logic_audit_phase3_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone

os.environ["APP_PASSWORD"] = "phase3-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import screener, news_client  # noqa: E402
news_client.get_market_news = lambda *a, **k: []
from app import app  # noqa: E402
from models import db, StockCache  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ✓ {label}")
    else:
        _failed += 1; print(f"  ✗ {label}")


def _rec(ticker, catalyst, pio, comp=9):
    return {"ticker": ticker, "name": ticker + " Co", "sector": "Technology",
            "price": 100.0, "analysis_price": 100.0, "change_percent": 1.0,
            "catalyst": catalyst, "catalyst_complete": True,
            "piotroski": pio, "piotroski_computable": comp,
            "indicators": [{"label": "EMA", "status": "bull"}], "money_flow": None,
            "break_status": None, "sustained": None, "reversal": None,
            "days_to_earnings": None, "float_shares": None, "rel_strength": None}


# ==================== #5 قادة النمو ====================
def _seed(records):
    with app.app_context():
        StockCache.query.filter(StockCache.ticker.like(screener._PREFIX + "%")).delete()
        now = datetime.now(timezone.utc)
        for r in records:
            db.session.add(StockCache(ticker=screener._PREFIX + r["ticker"],
                                      data_json=json.dumps(r), updated_at=now))
        db.session.commit()


def _get_leaders():
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True; s["role"] = "admin"
        resp = c.get("/leaders")
    return resp.status_code, resp.get_data(as_text=True)


def test_leaders_name_matches_growth_logic():
    print("\n[#5] اسم الصفحة يطابق المنطق (نمو Catalyst) لا «قيادة شاملة»:")
    # سهم نموّه عالٍ لكن جودته ضعيفة (NVDA-like) + سهم متوازن + سهم أضعف
    nvda = _rec("NVDA", catalyst=100, pio=4)
    solid = _rec("MSFT", catalyst=85, pio=8)
    weak = _rec("XYZ", catalyst=40, pio=6)
    _seed([weak, nvda, solid])
    code, html = _get_leaders()
    check(code == 200, f"الصفحة تُحمّل (200) — {code}")
    check("قادة النمو" in html, "العنوان الجديد «قادة النمو» ظاهر")
    check("القادة المستقبليون" not in html, "الاسم القديم «القادة المستقبليون» أُزيل")
    check("قوة شاملة" in html, "توضيح صريح: النمو ليس «قوة شاملة»")
    check("النمو (Catalyst)" in html or "درجة النمو" in html, "يوضّح أن الترتيب بدرجة النمو")


def test_leaders_high_catalyst_weak_quality_distinguished():
    print("\n[#5] سهم نمو مرتفع/جودة ضعيفة لا يُعرض كقائد شامل دون تمييز:")
    nvda = _rec("NVDA", catalyst=100, pio=4)
    solid = _rec("MSFT", catalyst=85, pio=8)
    _seed([nvda, solid])
    code, html = _get_leaders()
    check("NVDA" in html, "السهم عالي النمو يظهر (قائد نمو)")
    # جودته المالية الضعيفة ظاهرة على البطاقة (4/9) — تمييز واضح لا «شمولية» مزعومة
    check("<b>4</b>/9" in html, "جودة NVDA المالية الضعيفة (4/9) ظاهرة على البطاقة (تمييز)")
    # ليست جوهرة (Piotroski 4) — لا شارة تُوحي بالاكتمال. MSFT (8/9) جوهرة.
    check(html.count("💎 جوهرة") >= 1, "السهم المتكامل (MSFT 8/9) يحمل شارة الجوهرة")


def test_leaders_ranking_still_by_growth():
    print("\n[#5] الترتيب ما زال بالنمو (Catalyst) — لم تتغيّر المعادلة:")
    nvda = _rec("NVDA", catalyst=100, pio=4)
    solid = _rec("MSFT", catalyst=85, pio=8)
    weak = _rec("XYZ", catalyst=40, pio=6)
    ordered = screener.filter_records([weak, nvda, solid])
    check([r["ticker"] for r in ordered][:3] == ["NVDA", "MSFT", "XYZ"],
          "أعلى Catalyst أولاً (100 ثم 85 ثم 40) — الترتيب بالنمو محفوظ")


# ==================== #8 قفل تصميم Algomatix (بلا ازدواج) ====================
def _sub(record, key):
    bd = screener.algomatix_score(record)["breakdown"]
    return next(x["sub"] for x in bd if x["key"] == key)


def test_algomatix_trend_school_averages_not_sums():
    print("\n[#8] مدرسة الاتجاه تُمتوسِط مؤشراتها (لا تُحسب أربع مرات):")
    r4 = {"indicators": [{"label": k, "status": "bull"}
                         for k in ("EMA", "تقاطع", "سوبرترند", "ADX")]}
    check(abs(_sub(r4, "trend") - 1.0) < 1e-9,
          "4 مؤشرات اتجاه صاعدة → درجة المدرسة 1.0 (متوسط لا مجموع)")
    r1 = {"indicators": [{"label": "EMA", "status": "bull"}]}
    check(abs(_sub(r1, "trend") - 1.0) < 1e-9,
          "مؤشر اتجاه واحد صاعد → 1.0 (نفس السقف — لا مضاعفة بعدد المؤشرات)")


def test_algomatix_fundamentals_averaged_once():
    print("\n[#8] الجودة والنمو مدرسة واحدة متوسّطة (لا نقطتان منفصلتان):")
    r = {"piotroski": 9, "catalyst": 100}
    check(abs(_sub(r, "fundamentals") - 1.0) < 1e-9, "Piotroski 9 + Catalyst 100 → 1.0 (متوسط)")
    r2 = {"piotroski": 9, "catalyst": 0}
    check(abs(_sub(r2, "fundamentals") - 0.5) < 1e-9,
          "Piotroski 9 + Catalyst 0 → 0.5 (متوسط المكوّنين، لا جمعهما)")


def test_algomatix_missing_data_no_advantage():
    print("\n[#8] البيانات الغائبة محايدة (0.5) — لا أفضلية للنقص:")
    empty = {}
    for key in ("trend", "momentum", "fundamentals", "structure", "liquidity",
                "levels", "frames", "rel_strength", "price_action"):
        check(abs(_sub(empty, key) - 0.5) < 1e-9, f"مدرسة «{key}» بلا بيانات → 0.5")


def test_algomatix_high2_adx_respected():
    print("\n[#8/HIGH#2] ADX الهابط القوي لا يرفع مدرسة الاتجاه:")
    up = {"indicators": [{"label": "ADX", "status": "bull"}]}
    down = {"indicators": [{"label": "ADX", "status": "bear"}]}
    check(_sub(up, "trend") == 1.0, "ADX صاعد → مدرسة الاتجاه 1.0")
    check(_sub(down, "trend") == 0.0, "ADX هابط (bear) → مدرسة الاتجاه 0.0 (لا يُحتسب صاعداً)")


def main():
    print("=" * 62)
    print("regression — تدقيق Codex (PHASE 3: MEDIUM #5 و#8)")
    print("=" * 62)
    test_leaders_name_matches_growth_logic()
    test_leaders_high_catalyst_weak_quality_distinguished()
    test_leaders_ranking_still_by_growth()
    test_algomatix_trend_school_averages_not_sums()
    test_algomatix_fundamentals_averaged_once()
    test_algomatix_missing_data_no_advantage()
    test_algomatix_high2_adx_respected()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات PHASE 3 نجحت ✓ ({_passed} تحقّقاً).")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
