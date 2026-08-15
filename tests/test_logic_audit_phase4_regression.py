"""
test_logic_audit_phase4_regression.py — regression لتدقيق Codex (PHASE 4: LOW #9).

LOW #9: صفحة «هيكل السوق» (/structure) كانت تُرتّب بدرجة Algomatix الموزونة (التي تشمل عوامل
غير هيكلية) فيتصدّرها سهم لأسباب غير هيكلية. الإصلاح: الترتيب صار حسب درجة مدرسة «هيكل السوق»
الموجودة أصلاً في مؤشر Algomatix (0..1، محسوبة بحتاً من BOS/CHOCH/إعادة الاختبار/الاتجاه) —
بلا اختراع معادلة/threshold — مع كاسرات التعادل الموجودة (الدرجة الموزونة ثم خطط التداول).
لم يتغيّر Algomatix Score نفسه ولا حسابات market_structure.

التشغيل:  python tests/test_logic_audit_phase4_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone

os.environ["APP_PASSWORD"] = "phase4-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


_ALL_BULL = [{"label": k, "status": "bull"} for k in
             ("EMA", "تقاطع", "سوبرترند", "ADX", "MACD", "RSI", "الحجم", "اختراق",
              "قمة", "انضغاط", "ستوكاستيك", "تراكم")]


def _rec(ticker, ms_status, retest=None, piotroski=6, catalyst=50, indicators=None,
         frames=None):
    ms = {"status": ms_status, "trend": ("up" if ms_status == "bull" else
                                         "down" if ms_status == "bear" else "side"),
          "retest_state": retest, "event": None, "event_dir": None, "event_label": "",
          "level": 100.0, "swings": []}
    return {
        "ticker": ticker, "name": ticker + " Co", "sector": "Technology",
        "price": 100.0, "analysis_price": 100.0, "change_percent": 1.0,
        "catalyst": catalyst, "catalyst_complete": True,
        "piotroski": piotroski, "piotroski_computable": 9,
        "structure": ms,
        "frames": frames or {"daily": "up", "weekly": "side", "monthly": "side",
                             "up_count": 1, "down_count": 0},
        "indicators": indicators if indicators is not None else [{"label": "EMA", "status": "neutral"}],
        "money_flow": None, "rel_strength": None, "break_status": None,
        "volume_profile": None, "fibonacci": None, "near_resistance": None, "reversal": None,
    }


def _seed(records):
    with app.app_context():
        StockCache.query.filter(StockCache.ticker.like(screener._PREFIX + "%")).delete()
        now = datetime.now(timezone.utc)
        for r in records:
            db.session.add(StockCache(ticker=screener._PREFIX + r["ticker"],
                                      data_json=json.dumps(r), updated_at=now))
        db.session.commit()


def _get_structure():
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True; s["role"] = "admin"
        resp = c.get("/structure")
    return resp.status_code, resp.get_data(as_text=True)


# ==================== المقياس الهيكلي الموجود ====================
def test_structure_subscore_is_pure_structural():
    print("\n[#9] درجة مدرسة «هيكل السوق» مقياس هيكلي بحت موجود مسبقاً:")
    s_bull = screener._algx_subscores({"structure": {"status": "bull", "retest_state": "confirmed"}})["structure"]
    s_bear = screener._algx_subscores({"structure": {"status": "bear"}})["structure"]
    s_side = screener._algx_subscores({"structure": {"status": "side", "trend": "side"}})["structure"]
    check(s_bull == 1.0, "شراء مؤكّد → 1.0")
    check(s_bear == 0.15, "هابط → 0.15")
    check(s_bear < s_side < s_bull, f"ترتيب هيكلي متّسق: هابط({s_bear}) < عرضي({s_side}) < صاعد({s_bull})")


# ==================== الترتيب فعلاً حسب الهيكل ====================
def test_structure_page_ranks_by_structure_not_algomatix():
    print("\n[#9] الصفحة تُرتّب حسب قوة الهيكل لا حسب Algomatix الشامل:")
    # SS: هيكل قوي (شراء مؤكّد) لكن بقية العوامل ضعيفة → Algomatix أقل
    ss = _rec("SSSTRUCT", "bull", retest="confirmed", piotroski=3, catalyst=15,
              indicators=[{"label": "EMA", "status": "neutral"}])
    # WS: هيكل ضعيف (هابط) لكن بقية العوامل قوية → Algomatix أعلى
    ws = _rec("WSSTRUCT", "bear", piotroski=9, catalyst=100, indicators=_ALL_BULL,
              frames={"daily": "down", "weekly": "up", "monthly": "up", "up_count": 2, "down_count": 1})
    _seed([ws, ss])  # نُدخل WS أولاً لنثبت أن الترتيب لا يعتمد على ترتيب الإدخال
    code, html = _get_structure()
    check(code == 200, f"الصفحة تُحمّل (200) — {code}")

    algx_ss = screener.algomatix_score(ss)["score"]
    algx_ws = screener.algomatix_score(ws)["score"]
    check(algx_ws > algx_ss, f"WS أعلى في Algomatix ({algx_ws} > {algx_ss}) — التعادل مضمون")
    # رغم أن WS أعلى Algomatix، الهيكل الأقوى (SS) يظهر أولاً
    check(html.index("SSSTRUCT") < html.index("WSSTRUCT"),
          "السهم ذو الهيكل الأقوى (SS) يسبق ذا الهيكل الأضعف (WS) رغم Algomatix الأعلى للأخير")


def test_structure_page_tiebreak_by_algomatix():
    print("\n[#9] عند تساوي قوة الهيكل: الدرجة الموزونة كاسر تعادل موجود:")
    # كلاهما «شراء مؤكّد» (نفس درجة الهيكل 1.0)، لكن A أقوى في بقية العوامل
    a = _rec("TIEAAA", "bull", retest="confirmed", piotroski=9, catalyst=100, indicators=_ALL_BULL)
    b = _rec("TIEBBB", "bull", retest="confirmed", piotroski=4, catalyst=20,
             indicators=[{"label": "EMA", "status": "neutral"}])
    _seed([b, a])
    code, html = _get_structure()
    check(code == 200, f"الصفحة تُحمّل (200) — {code}")
    check(html.index("TIEAAA") < html.index("TIEBBB"),
          "عند تساوي الهيكل: الأعلى Algomatix (A) أولاً (كاسر تعادل موجود)")


# ==================== لا انحدار: Algomatix + بيانات الهيكل ====================
def test_algomatix_score_unchanged_by_fix():
    print("\n[#9] الإصلاح لم يغيّر Algomatix Score نفسه (مجرّد إعادة ترتيب عرض):")
    r = _rec("CHK", "bull", retest="confirmed", piotroski=8, catalyst=80, indicators=_ALL_BULL)
    # الدرجة = مجموع (sub × weight) على المدارس التسع — نتحقّق أن الترتيب لم يمسّ الحساب
    subs = screener._algx_subscores(r)
    expected = round(sum(subs[k] * w for k, w in screener.ALGOMATIX_WEIGHTS.items()))
    check(screener.algomatix_score(r)["score"] == expected,
          f"algomatix_score = مجموع المدارس الموزون ({expected}) — غير متأثّر بترتيب الصفحة")


def test_structure_data_and_counts_intact():
    print("\n[#9] بيانات الهيكل وعدّاداتها سليمة (لا انحدار):")
    up = _rec("UPX", "bull", retest="confirmed")
    down = _rec("DOWNX", "bear")
    side = _rec("SIDEX", "side")
    _seed([up, down, side])
    code, html = _get_structure()
    check(code == 200, "الصفحة 200")
    for t in ("UPX", "DOWNX", "SIDEX"):
        check(t in html, f"السهم {t} يظهر (بياناته الهيكلية معروضة)")
    # درجات مدرسة الهيكل تعكس الحالات الثلاث (لا تغيير في market_structure)
    check(screener._algx_subscores(up)["structure"] == 1.0
          and screener._algx_subscores(down)["structure"] == 0.15,
          "درجات الهيكل تعكس الحالة (صاعد مؤكّد=1.0، هابط=0.15) — market_structure سليم")


def main():
    print("=" * 62)
    print("regression — تدقيق Codex (PHASE 4: LOW #9 ترتيب صفحة هيكل السوق)")
    print("=" * 62)
    test_structure_subscore_is_pure_structural()
    test_structure_page_ranks_by_structure_not_algomatix()
    test_structure_page_tiebreak_by_algomatix()
    test_algomatix_score_unchanged_by_fix()
    test_structure_data_and_counts_intact()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات PHASE 4 نجحت ✓ ({_passed} تحقّقاً) — LOW #9 مقفولة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
