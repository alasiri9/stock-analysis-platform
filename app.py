"""
app.py — تطبيق Flask الرئيسي: يربط قاعدة البيانات والمسارات (routes) والقوالب.

المسارات:
- /                  الرئيسية (Screener — حالياً بحث عن سهم)
- /stock/<ticker>    تقرير سهم كامل
- /watchlist         قائمة المتابعة (تُبنى لاحقاً)
- /compare           مقارنة أسهم (تُبنى لاحقاً)
"""

import os

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for

from models import db, Watchlist
from services import analysis
from services import fmp_client
from services import screener

# مستخدم افتراضي وحيد (لا يوجد تسجيل دخول بعد)
GUEST_USER = "guest"

load_dotenv()


def _database_uri():
    """يحدّد رابط قاعدة البيانات.

    - على Railway: DATABASE_URL يُضاف تلقائياً (PostgreSQL).
      SQLAlchemy يحتاج بادئة postgresql:// وليس postgres:// فنصلحها.
    - محلياً: لو DATABASE_URL فارغ، نستخدم SQLite (ملف محلي بسيط).
    """
    url = os.getenv("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    return "sqlite:///local.db"


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()  # ينشئ الجداول لو ما كانت موجودة

    def _to_float(name):
        """يقرأ قيمة رقمية من باراميتر الطلب، أو None لو فارغة/غير صالحة."""
        raw = request.args.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @app.route("/")
    def index():
        # الرئيسية = الماسح: يقرأ السجلّات المخزّنة ويطبّق الفلاتر (بدون استدعاء API).
        records, latest = screener.load_records()
        sectors = sorted({r["sector"] for r in records if r.get("sector")})

        # القيمة السوقية تُدخل بالمليارات في الواجهة وتُحوّل لدولار خام
        market_cap_billions = _to_float("market_cap_min")
        market_cap_min = market_cap_billions * 1e9 if market_cap_billions is not None else None

        filters = {
            "piotroski_min": _to_float("piotroski_min"),
            "catalyst_min": _to_float("catalyst_min"),
            "price_max": _to_float("price_max"),
            "market_cap_min": market_cap_min,
            "sector": request.args.get("sector", "").strip() or None,
        }
        results = screener.filter_records(records, **filters)
        # نمرّر قيمة المليارات للواجهة (لإبقائها في الخانة)
        filters["market_cap_billions"] = market_cap_billions

        # إحصائيات علوية (من كامل العيّنة، لا المُفلتر)
        stats = {
            "total": len(records),
            "gems": sum(1 for r in records if r.get("piotroski") is not None and r["piotroski"] >= 8),
            "strong": sum(1 for r in records if r.get("catalyst") is not None and r["catalyst"] >= 80),
        }
        return render_template(
            "index.html",
            results=results, sectors=sectors, latest=latest,
            filters=filters, total=len(records), stats=stats,
            signals=screener.recent_signals(),
        )

    @app.route("/screener/refresh", methods=["POST"])
    def screener_refresh():
        # إعادة بناء كاش الماسح يدوياً (يستهلك استدعاءات API — لذلك يدوي).
        # نلتقط أي خطأ حتى لا تظهر صفحة 500؛ ما تم حفظه (commit لكل سهم) يبقى.
        try:
            screener.refresh_cache()
        except Exception as e:  # noqa: BLE001
            print(f"[app] خطأ أثناء تحديث الماسح: {e}")
        return redirect(url_for("index"))

    @app.route("/stock")
    def stock_search():
        # يستقبل البحث من نموذج الرئيسية ويحوّل لصفحة السهم
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return redirect(url_for("index"))
        return redirect(url_for("stock_report", ticker=ticker))

    @app.route("/stock/<ticker>")
    def stock_report(ticker):
        report = analysis.build_stock_report(ticker)
        if report is None:
            # لا نخترع بيانات: نوضّح أن السهم غير متاح
            return render_template("stock.html", report=None, ticker=ticker.upper())
        return render_template("stock.html", report=report, ticker=report["ticker"])

    # ===================== المقارنة =====================

    @app.route("/compare")
    def compare():
        # نستقبل الرموز كنص مفصول بفواصل: ?tickers=AAPL,MSFT,NVDA
        raw = request.args.get("tickers", "").strip()
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()][:4]  # حد أقصى 4
        summaries = []
        for t in tickers:
            s = analysis.build_quick_summary(t)
            if s:
                summaries.append(s)
        return render_template("compare.html", summaries=summaries, raw=raw)

    # ===================== قائمة المتابعة =====================

    @app.route("/watchlist")
    def watchlist():
        items = Watchlist.query.filter_by(user_id=GUEST_USER).order_by(Watchlist.added_at.desc()).all()
        rows = []
        for item in items:
            quote = fmp_client.get_quote(item.ticker)
            current = quote.get("price") if quote else None
            # العائد منذ الإضافة — None ≠ 0 : يُحسب فقط لو توفّر السعران
            if current is not None and item.added_price:
                ret_pct = (current - item.added_price) / item.added_price * 100.0
            else:
                ret_pct = None
            rows.append({
                "id": item.id,
                "ticker": item.ticker,
                "added_price": item.added_price,
                "added_at": item.added_at,
                "current": current,
                "return_pct": ret_pct,
            })
        return render_template("watchlist.html", rows=rows)

    @app.route("/watchlist/add", methods=["POST"])
    def watchlist_add():
        ticker = request.form.get("ticker", "").strip().upper()
        if ticker:
            exists = Watchlist.query.filter_by(user_id=GUEST_USER, ticker=ticker).first()
            if not exists:
                # نسجّل سعر الإضافة من السعر اللحظي (قد يكون None لو لم يتوفّر)
                quote = fmp_client.get_quote(ticker)
                added_price = quote.get("price") if quote else None
                db.session.add(Watchlist(ticker=ticker, user_id=GUEST_USER, added_price=added_price))
                db.session.commit()
        # نرجع للصفحة التي جاء منها الطلب (المتابعة أو صفحة السهم)
        return redirect(request.referrer or url_for("watchlist"))

    @app.route("/watchlist/remove", methods=["POST"])
    def watchlist_remove():
        item_id = request.form.get("id")
        item = Watchlist.query.filter_by(id=item_id, user_id=GUEST_USER).first()
        if item:
            db.session.delete(item)
            db.session.commit()
        return redirect(url_for("watchlist"))

    return app


# كائن التطبيق — يستخدمه gunicorn على Railway (app:app) ويستخدمه التشغيل المحلي
app = create_app()


if __name__ == "__main__":
    # تشغيل محلي للتطوير فقط
    app.run(debug=True, port=5000)
