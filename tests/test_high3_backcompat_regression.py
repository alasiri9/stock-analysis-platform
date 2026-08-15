"""
test_high3_backcompat_regression.py — توافق خلفي لـ HIGH#3: تقرير سهم قديم بلا analysis_price.

سيناريو Codex المؤكَّد:
1. تقرير مخزّن قديم يحتوي price فقط (بلا analysis_price).
2. مسار /stock/<t> يقرأه ويستبدل report["price"] بالسعر اللحظي (مدير/مشترك بمفتاح).
3. القالب كان يصل مباشرةً report.analysis_price → Undefined في Jinja → خطأ عند format → 500.

الإصلاح: app.py يحفظ analysis_price قبل الاستبدال (setdefault)، وstock.html يستعمل الآلية
الموحّدة analysis_price(report) (fallback إلى price) بدل الوصول المباشر. النتيجة: لا 500،
والملاحظة تظهر بسعر التحليل الصحيح (السعر القديم) مقابل السعر اللحظي.

التشغيل:  python tests/test_high3_backcompat_regression.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone

os.environ["APP_PASSWORD"] = "backcompat-pw"
_fd, _dbp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbp}"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from services import screener, fmp_client, finnhub_client, news_client  # noqa: E402
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


# تقرير قديم كما كان يُخزَّن قبل إضافة analysis_price: فيه price ولا يحوي analysis_price.
def _old_report():
    return {
        "ticker": "AAPL", "name": "Apple", "sector": "Technology", "industry": "Consumer",
        "price": 100.0,  # سعر التحليل القديم (بلا حقل analysis_price)
        "change": 1.0, "change_percent": 1.0, "market_cap": 3e12,
        "metrics": {"roe": 10.0, "roa": 5.0, "op_margin": 20.0, "gross_margin": 40.0,
                    "pe": 25.0, "peg": None},
        "piotroski": {"score": 6, "computable": 9, "components": []},
        "catalyst": {"score": 80},
        "price_sources": 1, "insider_trades": [], "finnhub_price": None,
        "atr_plan": {"entry": 100.0, "stop": 95.0, "target": 110.0, "atr": 2.0, "period": 14,
                     "risk_reward": 2.0, "rr_quality": "good", "stop_basis": "atr",
                     "target_basis": "atr", "stop_mult": 1.5, "target_mult": 3.0},
        "break_status": None, "sustained": None, "indicators": [], "reversal": None,
        "near_resistance": None, "fibonacci": None, "volume_profile": None, "chart": None,
    }


def test_old_report_no_analysis_price_no_500():
    print("\n[HIGH#3 توافق خلفي] /stock/AAPL بتقرير قديم بلا analysis_price + سعر لحظي مختلف:")
    # سجل ماسح فارغ (scan/peers لا يكسران)
    screener.load_records = lambda: ([], None)
    with app.app_context():
        db.session.merge(StockCache(ticker="report:AAPL",
                                    data_json=json.dumps(_old_report()),
                                    updated_at=datetime.now(timezone.utc)))
        db.session.commit()

    # سعر لحظي مختلف عن سعر التحليل القديم (يحاكي المدير/مفتاح المنصة) — بلا شبكة
    fmp_client.get_quote = lambda ticker, api_key=None: {
        "price": 125.0, "change": 2.5, "change_percent": 2.0}

    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True
            s["role"] = "admin"   # مدير → _do_live=True → يُفعّل مسار الاستبدال اللحظي
        resp = c.get("/stock/AAPL")
        html = resp.get_data(as_text=True)

    check(resp.status_code == 200, f"لا 500 — الحالة {resp.status_code} (كانت ستنهار على Undefined)")
    check("سعر التحليل" in html, "ملاحظة «سعر التحليل» ظهرت")
    check("100.00 $" in html, "الملاحظة تعرض سعر التحليل الصحيح (100.00 $ = السعر القديم)")
    check("125.00 $" in html, "الترويسة/الملاحظة تعرض السعر اللحظي (125.00 $)")


def test_analysis_price_helper_fallback():
    print("\n[HIGH#3 توافق خلفي] analysis_price() يرجع price للتقارير القديمة:")
    old = _old_report()
    check(screener.analysis_price(old) == 100.0,
          "analysis_price(تقرير قديم بلا الحقل) = price (100)")
    # بعد إضافة الحقل (تقرير جديد): analysis_price = الحقل الثابت (100) لا السعر المعروض (125)
    newr = dict(old, analysis_price=100.0, price=125.0)
    check(screener.analysis_price(newr) == 100.0,
          "analysis_price(تقرير جديد) = الحقل الثابت (100) لا price المعروض (125)")


def test_old_report_no_live_quote_no_500():
    print("\n[HIGH#3 توافق خلفي] تقرير قديم بلا وصول سعر لحظي (لا استبدال) لا ينهار:")
    screener.load_records = lambda: ([], None)
    with app.app_context():
        db.session.merge(StockCache(ticker="report:AAPL",
                                    data_json=json.dumps(_old_report()),
                                    updated_at=datetime.now(timezone.utc)))
        db.session.commit()
    # مدير لكن لا يرجع سعر لحظي (q=None) → لا استبدال → price=analysis القديم، بلا analysis_price:
    # القالب يستعمل analysis_price(report)=price فلا Undefined ولا ملاحظة (السعران متساويان).
    fmp_client.get_quote = lambda ticker, api_key=None: None
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True
            s["role"] = "admin"
        resp = c.get("/stock/AAPL")
        html = resp.get_data(as_text=True)
    check(resp.status_code == 200, f"لا 500 (تقرير قديم بلا سعر لحظي) — الحالة {resp.status_code}")
    check("سعر التحليل" not in html, "لا تظهر ملاحظة الفرق حين لا يوجد سعر لحظي مختلف")


def main():
    print("=" * 62)
    print("regression — توافق خلفي HIGH#3 (تقرير سهم قديم بلا analysis_price)")
    print("=" * 62)
    test_old_report_no_analysis_price_no_500()
    test_analysis_price_helper_fallback()
    test_old_report_no_live_quote_no_500()
    print("\n" + "-" * 62)
    if _failed == 0:
        print(f"كل اختبارات التوافق الخلفي نجحت ✓ ({_passed} تحقّقاً) — لا 500 على التقارير القديمة.")
        return 0
    print(f"✗ فشل {_failed} (نجح {_passed}).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
