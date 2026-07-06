"""
app.py — تطبيق Flask الرئيسي: يربط قاعدة البيانات والمسارات (routes) والقوالب.

المسارات:
- /                  الرئيسية (Screener — حالياً بحث عن سهم)
- /stock/<ticker>    تقرير سهم كامل
- /watchlist         قائمة المتابعة (تُبنى لاحقاً)
- /compare           مقارنة أسهم (تُبنى لاحقاً)
"""

import os
import sys

# طباعة النصوص العربية بأمان بأي بيئة (كونسول ويندوز الافتراضي cp1252 ينهار بدونها)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import hashlib
import secrets

from flask import Flask, render_template, request, redirect, url_for, session

from models import db, Watchlist, PortfolioHolding
from services import analysis
from services import fmp_client
from services import radar
from services import news_client
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

    # ===================== الحماية بكلمة مرور =====================
    # APP_PASSWORD متغير بيئة واحد يفعّل كل شيء:
    # - مضبوط  ⇒ كل الصفحات تتطلب تسجيل دخول، وزر "خروج" يعمل فعلياً.
    # - غير مضبوط ⇒ المنصة مفتوحة كما كانت (آمن للنشر التدريجي وللتطوير المحلي).
    app_password = os.getenv("APP_PASSWORD")
    if app_password:
        # مفتاح توقيع الجلسات مشتق من كلمة المرور (ثابت عبر إعادة التشغيل) — متغير واحد يكفي أحمد
        app.secret_key = hashlib.sha256(f"algomatix-session-{app_password}".encode()).hexdigest()
    else:
        app.secret_key = secrets.token_hex(32)
        print("[app] تنبيه: APP_PASSWORD غير مضبوط — المنصة مفتوحة بلا تسجيل دخول")

    @app.before_request
    def _require_login():
        if not app_password:
            return None  # الحماية غير مفعّلة
        # مسموح بلا جلسة: صفحة الدخول نفسها + الملفات الثابتة
        if request.endpoint in ("login", "static"):
            return None
        if session.get("authed"):
            return None
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if app_password and secrets.compare_digest(request.form.get("password", ""), app_password):
                session["authed"] = True
                session.permanent = True
                return redirect(url_for("index"))
            error = "كلمة المرور غير صحيحة"
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login") if app_password else url_for("index"))

    db.init_app(app)

    @app.teardown_request
    def _cleanup_db_session(exc):
        # حماية من الجلسات الفاسدة: أي طلب انتهى بخطأ يُرجَع تراجعه فوراً،
        # فلا تعلق حالة PendingRollback وتكسر الطلبات اللاحقة (حدثت فعلياً 2026-07-04).
        if exc is not None:
            db.session.rollback()
    with app.app_context():
        db.create_all()  # ينشئ الجداول لو ما كانت موجودة
        # تنظيف الإشارات المكررة (آمن ورخيص — يصحح ما خلّفته نسخة قديمة كانت تكرر يومياً)
        try:
            screener.dedupe_signals()
        except Exception as e:  # noqa: BLE001
            print(f"[app] تعذّر تنظيف الإشارات المكررة: {e}")

    # التحديث التلقائي اليومي (01:00 UTC) — انظر services/scheduler.py
    from services.scheduler import init_scheduler
    init_scheduler(app)

    # دالة الإشارة الذهبية متاحة للقوالب (وسم 🥇 على كروت الماسح)
    app.jinja_env.globals["is_golden"] = screener.is_golden

    @app.template_filter("ts_ago")
    def ts_ago(unix_ts):
        """يحوّل طابع unix الزمني لصيغة نسبية عربية (قبل ساعتين...). None ≠ 0."""
        if not unix_ts:
            return ""
        from datetime import datetime, timezone as tz
        delta = datetime.now(tz.utc) - datetime.fromtimestamp(unix_ts, tz.utc)
        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:
            return "الآن"
        if minutes < 60:
            return f"قبل {minutes} دقيقة"
        hours = minutes // 60
        if hours < 24:
            return f"قبل {hours} ساعة"
        days = hours // 24
        return f"قبل {days} يوم"

    @app.template_filter("signal_name")
    def signal_name(signal_type):
        """اسم عربي موحّد لنوع الإشارة (بدل تكراره بكل قالب)."""
        return {
            "piotroski_strong": "💎 جودة مالية قوية",
            "catalyst_strong": "⚡ نمو قوي",
            "golden": "🥇 إشارة ذهبية (3 عوامل)",
            "squeeze_breakout": "💣 انفجار وشيك (انضغاط + اختراق)",
            "golden_cross": "🌟 تقاطع ذهبي (SMA50/200)",
            "trend_pullback": "🎯 ارتداد الترند (شراء الانخفاض)",
        }.get(signal_type, signal_type)

    @app.template_filter("quality_icon")
    def quality_icon(score):
        """أيقونة مستوى الجودة المالية (Piotroski): جوهرة/أصفر/أحمر حسب الرقم."""
        if score is None:
            return ""
        if score >= 8:
            return "💎"   # قوية جداً (جوهرة)
        if score >= 5:
            return "🟡"   # متوسطة أو جيدة
        return "🔴"       # ضعيفة — احذر

    @app.template_filter("sector_ar")
    def sector_ar(sector):
        """اسم القطاع بالعربية للعرض (القيمة المخزّنة تبقى إنجليزية للفلترة)."""
        return {
            "Technology": "التقنية",
            "Healthcare": "الرعاية الصحية",
            "Financial Services": "الخدمات المالية",
            "Financial": "الخدمات المالية",
            "Consumer Cyclical": "السلع الاستهلاكية الكمالية",
            "Consumer Defensive": "السلع الاستهلاكية الأساسية",
            "Energy": "الطاقة",
            "Communication Services": "خدمات الاتصالات",
            "Industrials": "الصناعات",
            "Basic Materials": "المواد الأساسية",
            "Real Estate": "العقارات",
            "Utilities": "المرافق",
        }.get(sector, sector or "")

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

        # الأسهم الحرة تُدخل بالملايين في الواجهة وتُحوّل لعدد خام (لعرض قليلة الحرة = الأسرع)
        float_max_millions = _to_float("float_max")
        float_max = float_max_millions * 1e6 if float_max_millions is not None else None

        # "لسا ما صعد": يستبعد ما قفز أكثر من الحدّ خلال آخر أسبوعين (اصطياد مبكر)
        not_risen = request.args.get("not_risen") in ("1", "on", "true")
        filters = {
            "piotroski_min": _to_float("piotroski_min"),
            "catalyst_min": _to_float("catalyst_min"),
            "price_max": _to_float("price_max"),
            "sector": request.args.get("sector", "").strip() or None,
            "recent_gain_max": screener.EARLY_MAX_RECENT_GAIN if not_risen else None,
            "float_max": float_max,
        }
        results = screener.filter_records(records, **filters)
        # نمرّر قيمة الملايين وحالة الشيك بوكس للواجهة (لإبقائها بالخانة)
        filters["float_max_millions"] = float_max_millions
        filters["not_risen"] = not_risen

        # إحصائيات علوية (من كامل العيّنة، لا المُفلتر)
        stats = {
            "total": len(records),
            "gems": sum(1 for r in records if r.get("piotroski") is not None and r["piotroski"] >= 8),
            "strong": sum(1 for r in records if r.get("catalyst") is not None and r["catalyst"] >= 80),
        }
        launched, perf = screener.launched_stocks()
        mood = screener.market_mood(records)  # مزاج أسهم المنصة (نفس السجلّات — بلا قراءة مكررة)
        market_dir = screener.market_direction()  # اتجاه السوق الأمريكي (S&P 500)
        return render_template(
            "index.html",
            results=results, sectors=sectors, latest=latest,
            filters=filters, total=len(records), stats=stats,
            signals=screener.recent_signals(),
            launched=launched, perf=perf, mood=mood, market_dir=market_dir,
        )

    @app.route("/gems")
    def gems():
        # الجواهر المخفية = نفس فلتر Piotroski>=8 من الماسح، بصفحة مستقلة
        records, latest = screener.load_records()
        results = screener.filter_records(records, piotroski_min=8)
        return render_template("gems.html", results=results, latest=latest, total=len(records))

    @app.route("/leaders")
    def leaders():
        # القادة المستقبليون = أعلى 10 أسهم حسب Catalyst (بيانات الماسح نفسها، ترتيب مختلف)
        records, latest = screener.load_records()
        results = screener.filter_records(records)[:10]
        return render_template("leaders.html", results=results, latest=latest, total=len(records))

    @app.route("/prelaunch")
    def prelaunch():
        # قبل الانطلاق: أسهم مبكرة (قيد الشحن/بداية اختراق) لم تصعد بعد، مرتّبة بقوة التأكيد
        candidates = screener.early_launch_candidates()
        return render_template("prelaunch.html", candidates=candidates)

    @app.route("/signals")
    def signals_page():
        # كل الإشارات الأخيرة (بدل آخر 6 فقط في الرئيسية)
        return render_template("signals.html", signals=screener.recent_signals(limit=50))

    @app.route("/daily-report")
    def daily_report():
        records, latest = screener.load_records()
        stats = {
            "total": len(records),
            "gems": sum(1 for r in records if r.get("piotroski") is not None and r["piotroski"] >= 8),
            "strong": sum(1 for r in records if r.get("catalyst") is not None and r["catalyst"] >= 80),
        }
        return render_template(
            "daily_report.html", stats=stats, latest=latest,
            signals=screener.recent_signals(limit=15),
        )

    @app.route("/radar")
    def radar_page():
        # رادار المحفزات: معاملات المطلعين من كاش EDGAR (بلا استدعاءات عند العرض)
        transactions, open_buys, latest = radar.load_radar()
        return render_template(
            "radar.html",
            transactions=transactions, open_buys=open_buys, latest=latest,
        )

    @app.route("/radar/refresh", methods=["POST"])
    def radar_refresh():
        # تحديث كاش الرادار على دفعات (EDGAR بطيء — قد يحتاج أكثر من ضغطة)
        try:
            radar.refresh_radar()
        except Exception as e:  # noqa: BLE001
            print(f"[app] خطأ أثناء تحديث الرادار: {e}")
        return redirect(url_for("radar_page"))

    @app.route("/news")
    def news_page():
        # أخبار السوق العامة من Finnhub (كاش بالذاكرة 10 دقائق داخل news_client)
        items = news_client.get_market_news(limit=40)
        return render_template("news.html", items=items)

    @app.route("/flow")
    def flow_page():
        # التدفق الذكي: ترتيب الأسهم حسب درجة تدفق السيولة (من كاش الماسح، بلا استدعاءات)
        records, latest = screener.load_records()
        with_flow = [r for r in records if r.get("money_flow")]
        without_flow = [r for r in records if not r.get("money_flow")]
        with_flow.sort(key=lambda r: r["money_flow"]["score"], reverse=True)
        return render_template(
            "flow.html",
            rows=with_flow, pending=without_flow, latest=latest, total=len(records),
        )

    @app.route("/performance")
    def performance():
        # اختيار الأداء: سجل كل الإشارات التاريخية وأداؤها منذ صدورها (من الكاش، بلا API)
        rows, overall, type_stats = screener.signals_performance()
        return render_template(
            "performance.html", rows=rows, overall=overall, type_stats=type_stats,
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

    @app.route("/screener/backfill-history", methods=["POST"])
    def screener_backfill_history():
        # تعبئة الرسم البياني (price_point) للأسهم المخزّنة أصلاً — استدعاء واحد لكل سهم فقط.
        try:
            screener.backfill_price_history()
        except Exception as e:  # noqa: BLE001
            print(f"[app] خطأ أثناء تعبئة تاريخ الأسعار: {e}")
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

    # ===================== حاسبة حجم الصفقة =====================

    @app.route("/calculator")
    def calculator():
        # أداة تعليمية فورية (حساب في المتصفح) — بلا استدعاء API ولا بيانات خادم
        return render_template("calculator.html")

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
        # السعر الحالي من كاش الماسح أولاً (فوري وبلا استهلاك حصة) — نفس نهج المحفظة
        records, _ = screener.load_records()
        cache_prices = {r["ticker"]: r.get("price") for r in records}
        rows = []
        for item in items:
            current = _current_price(item.ticker, cache_prices)
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

    # ===================== المحفظة الذكية =====================

    def _current_price(ticker, cache_prices):
        """السعر الحالي: من كاش الماسح أولاً (بلا API)، وإلا quote حي من FMP."""
        if ticker in cache_prices and cache_prices[ticker] is not None:
            return cache_prices[ticker]
        quote = fmp_client.get_quote(ticker)
        return quote.get("price") if quote else None

    @app.route("/portfolio")
    def portfolio():
        items = (
            PortfolioHolding.query.filter_by(user_id=GUEST_USER)
            .order_by(PortfolioHolding.added_at.desc()).all()
        )
        records, _ = screener.load_records()
        cache_prices = {r["ticker"]: r.get("price") for r in records}

        rows = []
        total_cost = total_value = 0.0
        priced_all = True
        for item in items:
            current = _current_price(item.ticker, cache_prices)
            cost = item.shares * item.buy_price
            value = item.shares * current if current is not None else None
            pnl = value - cost if value is not None else None
            pnl_pct = (pnl / cost * 100.0) if pnl is not None and cost else None
            total_cost += cost
            if value is not None:
                total_value += value
            else:
                priced_all = False
            rows.append({
                "id": item.id, "ticker": item.ticker, "shares": item.shares,
                "buy_price": item.buy_price, "current": current,
                "cost": cost, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
            })

        # الملخص: None ≠ 0 — لو سهم بلا سعر حالي لا نعرض إجمالياً مضلّلاً
        summary = {
            "count": len(rows),
            "total_cost": total_cost if rows else None,
            "total_value": total_value if rows and priced_all else None,
        }
        if summary["total_value"] is not None and total_cost:
            summary["total_pnl"] = summary["total_value"] - total_cost
            summary["total_pnl_pct"] = summary["total_pnl"] / total_cost * 100.0
        else:
            summary["total_pnl"] = None
            summary["total_pnl_pct"] = None
        from services import portfolio as portfolio_svc
        chart = portfolio_svc.performance_chart()
        return render_template("portfolio.html", rows=rows, summary=summary, chart=chart)

    @app.route("/portfolio/add", methods=["POST"])
    def portfolio_add():
        ticker = request.form.get("ticker", "").strip().upper()
        try:
            shares = float(request.form.get("shares", ""))
            buy_price = float(request.form.get("buy_price", ""))
        except ValueError:
            shares = buy_price = 0
        if ticker and shares > 0 and buy_price > 0:
            db.session.add(PortfolioHolding(
                ticker=ticker, shares=shares, buy_price=buy_price, user_id=GUEST_USER,
            ))
            db.session.commit()
        return redirect(url_for("portfolio"))

    @app.route("/portfolio/remove", methods=["POST"])
    def portfolio_remove():
        item_id = request.form.get("id")
        item = PortfolioHolding.query.filter_by(id=item_id, user_id=GUEST_USER).first()
        if item:
            db.session.delete(item)
            db.session.commit()
        return redirect(url_for("portfolio"))

    return app


# كائن التطبيق — يستخدمه gunicorn على Railway (app:app) ويستخدمه التشغيل المحلي
app = create_app()


if __name__ == "__main__":
    # تشغيل محلي للتطوير فقط
    app.run(debug=True, port=5000)
