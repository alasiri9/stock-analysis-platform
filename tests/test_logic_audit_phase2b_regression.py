"""
test_logic_audit_phase2b_regression.py — regression لثغرتَي PHASE 2 (#4/#7) اللتين كشفهما Codex:

1) quality_icon كان يعطي 💎 لسهم Piotroski جزئي (8/8) اعتماداً على score وحده — الآن 💎
   لا تظهر إلا لجوهرة فعلية (score≥8 و computable≥9)، متطابقة مع is_gem (لا إشارتان متعارضتان).
2) stock.html وsmart_summary كانا يعرضان /9 للنتيجة الجزئية — الآن المقام = computable الفعلي
   (8/8 لا 8/9)، مع توافق خلفي (بلا computable → 9).

التشغيل:  python tests/test_logic_audit_phase2b_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone

os.environ["APP_PASSWORD"] = "phase2b-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import screener, analysis, fmp_client, news_client  # noqa: E402
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


_QI = app.jinja_env.filters["quality_icon"]


# ==================== #4: quality_icon و💎 ====================
def test_quality_icon_gem_completeness():
    print("\n[#4] quality_icon: 💎 فقط لجوهرة مكتملة (score≥8 و computable≥9):")
    check(_QI(8, 9) == "💎", "8/9 → 💎")
    check(_QI(9, 9) == "💎", "9/9 → 💎")
    check(_QI(8, 8) != "💎", "8/8 (جزئية) → ليست 💎")
    check(_QI(8, 8) == "🟡", "8/8 → 🟡 (عالية لكن غير مكتملة)")
    check(_QI(6, 9) == "🟡", "6/9 → 🟡 (سلوك بقية الأيقونات محفوظ)")
    check(_QI(2, 9) == "🔴", "2/9 → 🔴 (محفوظ)")
    check(_QI(8) == "💎", "توافق خلفي: بلا computable → 9 → 💎")
    check(_QI(8, None) == "💎", "computable=None → يُعامَل 9 → 💎")
    check(_QI(None) == "", "None → '' (محفوظ)")


def _rec(pio, comp=9, catalyst=85):
    return {"ticker": "T", "name": "Co", "sector": "Technology", "price": 100.0,
            "analysis_price": 100.0, "change_percent": 1.0, "catalyst": catalyst,
            "catalyst_complete": True, "piotroski": pio, "piotroski_computable": comp,
            "indicators": [{"label": "EMA", "status": "bull"}], "money_flow": None,
            "break_status": None, "sustained": None, "reversal": None,
            "days_to_earnings": None, "float_shares": None, "rel_strength": None}


def _render_card(rec):
    with app.test_request_context():
        return app.jinja_env.get_template("_scard.html").render(r=rec, rank=1)


def test_card_no_contradictory_gem_signal():
    print("\n[#4] البطاقة لا تعطي إشارتين متعارضتين للسهم نفسه:")
    html88 = _render_card(_rec(8, 8))
    check("💎 جوهرة" not in html88, "8/8: لا شارة «💎 جوهرة»")
    check("💎" not in html88, "8/8: لا أيقونة 💎 إطلاقاً (توافق الشارة والأيقونة)")
    check("</b>/8" in html88, "8/8: يُعرض المقام /8")
    html89 = _render_card(_rec(8, 9))
    check("💎 جوهرة" in html89, "8/9: شارة «💎 جوهرة» تظهر")
    check("💎" in html89, "8/9: أيقونة 💎 تظهر (متسقة مع الشارة)")


# ==================== #7: smart_summary المقام ====================
def _report_min(pio_dict):
    return {"piotroski": pio_dict, "catalyst": {"score": 50, "complete": True},
            "metrics": {}, "near_resistance": None}


def test_smart_summary_denominator():
    print("\n[#7] smart_summary يستخدم computable كمقام:")
    s88 = analysis.smart_summary(_report_min({"score": 8, "computable": 8}))
    check(any("Piotroski 8/8" in x for x in s88["strengths"]),
          "8/8 → smart_summary يكتب «Piotroski 8/8»")
    check(not any("8/9" in x for x in s88["strengths"]), "8/8 → لا يظهر 8/9")
    s89 = analysis.smart_summary(_report_min({"score": 8, "computable": 9}))
    check(any("Piotroski 8/9" in x for x in s89["strengths"]), "8/9 → يكتب «Piotroski 8/9»")
    old = analysis.smart_summary(_report_min({"score": 8}))  # بلا computable
    check(any("Piotroski 8/9" in x for x in old["strengths"]),
          "سجل قديم بلا computable → /9 (توافق خلفي، بلا انهيار)")


# ==================== #7: صفحة السهم /stock (المقام المرئي) ====================
def _report_full(computable):
    return {
        "ticker": "AAPL", "name": "Apple", "sector": "Technology", "industry": "Consumer",
        "price": 100.0, "analysis_price": 100.0, "change": 1.0, "change_percent": 1.0,
        "market_cap": 3e12,
        "metrics": {"roe": 10.0, "roa": 5.0, "op_margin": 20.0, "gross_margin": 40.0,
                    "pe": 25.0, "peg": None},
        "piotroski": {"score": 8, "computable": computable, "components": []},
        "catalyst": {"score": 85, "complete": True, "components": []},
        "price_sources": 1, "insider_trades": [], "finnhub_price": None,
        "atr_plan": None, "break_status": None, "sustained": None, "indicators": [],
        "reversal": None, "near_resistance": None, "fibonacci": None,
        "volume_profile": None, "chart": None,
    }


def _get_stock_html(report):
    screener.load_records = lambda: ([], None)
    with app.app_context():
        db.session.merge(StockCache(ticker="report:AAPL",
                                    data_json=json.dumps(report),
                                    updated_at=datetime.now(timezone.utc)))
        db.session.commit()
    fmp_client.get_quote = lambda ticker, api_key=None: None  # لا استبدال لحظي
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True; s["role"] = "admin"
        resp = c.get("/stock/AAPL")
    return resp.status_code, resp.get_data(as_text=True)


def test_stock_page_partial_denominator():
    print("\n[#7] صفحة السهم: نتيجة 8/8 لا تُعرض 8/9 في أي موضع Piotroski:")
    code, html = _get_stock_html(_report_full(8))
    check(code == 200, f"الصفحة تُحمّل (200) — {code}")
    check("Piotroski 8/9" not in html, "لا يظهر «Piotroski 8/9» (لا تضليل)")
    check("Piotroski 8/8" in html, "smart_summary يكتب «Piotroski 8/8»")
    check('gauge-max">/8<' in html, "عدّاد الجودة يُظهر المقام /8")
    check("💎 جوهرة" not in html, "8/8 → لا شارة جوهرة")
    check("أمكن حساب 8 نقاط فقط" in html, "توضيح «أمكن حساب 8 نقاط فقط»")


def test_stock_page_full_denominator():
    print("\n[#7] صفحة السهم: نتيجة 8/9 مكتملة تُعرض 8/9 وشارة الجوهرة:")
    code, html = _get_stock_html(_report_full(9))
    check(code == 200, f"الصفحة تُحمّل (200) — {code}")
    check("Piotroski 8/9" in html, "smart_summary يكتب «Piotroski 8/9»")
    check('gauge-max">/9<' in html, "عدّاد الجودة يُظهر المقام /9")
    check("💎 جوهرة" in html, "8/9 مكتملة → شارة الجوهرة تظهر")


def test_stock_page_old_report_no_computable():
    print("\n[#7] صفحة السهم: تقرير قديم بلا computable → /9 ولا ينهار:")
    rep = _report_full(9)
    rep["piotroski"].pop("computable")  # سجل قديم
    code, html = _get_stock_html(rep)
    check(code == 200, f"لا انهيار (200) — {code}")
    check('gauge-max">/9<' in html, "غياب computable → المقام /9 (توافق خلفي)")


def main():
    print("=" * 62)
    print("regression — PHASE 2 ثغرتا #4/#7 (quality_icon + مقام Piotroski)")
    print("=" * 62)
    test_quality_icon_gem_completeness()
    test_card_no_contradictory_gem_signal()
    test_smart_summary_denominator()
    test_stock_page_partial_denominator()
    test_stock_page_full_denominator()
    test_stock_page_old_report_no_computable()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات PHASE 2b نجحت ✓ ({_passed} تحقّقاً) — ثغرتا #4/#7 مقفولتان.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
