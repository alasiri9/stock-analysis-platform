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

from flask import Flask, render_template, request, redirect, url_for, session, Response

from werkzeug.security import generate_password_hash, check_password_hash

from models import (db, Watchlist, PortfolioHolding, PriceAlert, StockNote,
                    Subscriber, StockCache, Signal, PricePoint, MarketMoodSnapshot,
                    AppSetting)


def _upsert_setting(key, value):
    """يحفظ/يحدّث إعداداً في جدول AppSetting (لا يعمل commit — المتصل يلتزم)."""
    row = db.session.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.session.add(AppSetting(key=key, value=value))


def _get_setting(key):
    """يُرجع قيمة إعداد أو None."""
    row = db.session.get(AppSetting, key)
    return row.value if row else None


# حدّ محاولات الدخول (حماية من التخمين) — في الذاكرة (Procfile يشغّل عاملاً واحداً)
_LOGIN_MAX_FAILS = 3
_LOGIN_LOCK_MINUTES = 5
_login_state = {}  # ip -> {"fails": int, "lock_until": datetime|None}


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")


def _login_locked_minutes():
    """دقائق القفل المتبقية للآيبي الحالي إن كان مقفولاً، وإلا None."""
    from datetime import datetime, timezone
    st = _login_state.get(_client_ip())
    if st and st.get("lock_until"):
        rem = (st["lock_until"] - datetime.now(timezone.utc)).total_seconds()
        if rem > 0:
            return int(rem // 60) + 1
        st["lock_until"] = None
    return None


def _record_login_fail():
    """يسجّل محاولة فاشلة؛ بعد _LOGIN_MAX_FAILS يقفل الآيبي مؤقتاً وينبّه تلغرام."""
    from datetime import datetime, timezone, timedelta
    ip = _client_ip()
    st = _login_state.setdefault(ip, {"fails": 0, "lock_until": None})
    st["fails"] += 1
    if st["fails"] >= _LOGIN_MAX_FAILS:
        st["lock_until"] = datetime.now(timezone.utc) + timedelta(minutes=_LOGIN_LOCK_MINUTES)
        st["fails"] = 0
        # تنبيه محاولة اختراق (خامل بلا تلغرام، وفشله لا يؤثر)
        try:
            telegram_client.send_message(
                f"⚠️ <b>محاولة دخول مشبوهة على Algomatix</b>\n"
                f"{_LOGIN_MAX_FAILS} محاولات فاشلة من IP: <code>{ip}</code>\n"
                f"تم القفل {_LOGIN_LOCK_MINUTES} دقائق تلقائياً.")
        except Exception:  # noqa: BLE001
            pass


def _clear_login_fails():
    _login_state.pop(_client_ip(), None)
from services import analysis
from services import fmp_client
from services import radar
from services import news_client
from services import screener
from services import telegram_client

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

    def is_admin():
        """المدير = صاحب المنصة (كلمة المرور الرئيسية)، أو الوضع المحلي بلا كلمة مرور."""
        return (not app_password) or session.get("role") == "admin"

    @app.before_request
    def _require_login():
        if not app_password:
            return None  # الحماية غير مفعّلة
        # مسموح بلا جلسة: الدخول + الملفات الثابتة + مسارات استعادة كلمة المرور (المستخدم مقفول برّه)
        if request.endpoint in ("login", "static", "password_forgot", "password_reset"):
            return None
        if session.get("authed"):
            # المشترك: نتحقق أن اشتراكه لم ينتهِ في كل طلب (يُمنع فور الانتهاء)
            if session.get("role") == "sub":
                sub = db.session.get(Subscriber, session.get("sub_id"))
                if not sub or not sub.is_active():
                    session.clear()
                    return redirect(url_for("login", expired=1))
            return None
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            locked = _login_locked_minutes()
            if locked:
                return render_template("login.html",
                                       error=f"محاولات كثيرة. حاول بعد {locked} دقيقة.")
            entered = request.form.get("password", "")
            # 1) المدير: الكلمة المخزّنة بالمنصة (مشفّرة) أو كلمة Railway (مفتاح طوارئ دائم)
            stored = db.session.get(AppSetting, "admin_password_hash")
            admin_ok = bool(stored and stored.value and check_password_hash(stored.value, entered))
            if not admin_ok and app_password and secrets.compare_digest(entered, app_password):
                admin_ok = True
            if admin_ok:
                _clear_login_fails()
                session["authed"] = True
                session["role"] = "admin"
                session.permanent = True
                return redirect(url_for("index"))
            # 2) رمز مشترك ساري المفعول
            if entered.strip():
                sub = Subscriber.query.filter_by(access_code=entered.strip()).first()
                if sub and sub.is_active():
                    _clear_login_fails()
                    session["authed"] = True
                    session["role"] = "sub"
                    session["sub_id"] = sub.id
                    session.permanent = True
                    from datetime import datetime as _dt, timezone as _tz
                    sub.last_login = _dt.now(_tz.utc)  # تتبّع آخر دخول
                    db.session.commit()
                    return redirect(url_for("index"))
                if sub and not sub.is_active():
                    error = "انتهت مدة اشتراكك. تواصل مع صاحب المنصة للتجديد."
            if not error:
                error = "كلمة المرور أو رمز الاشتراك غير صحيح"
                _record_login_fail()
                locked = _login_locked_minutes()
                if locked:
                    error = f"محاولات كثيرة. حاول بعد {locked} دقيقة."
        expired = request.args.get("expired")
        return render_template("login.html", error=error, expired=expired)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login") if app_password else url_for("index"))

    @app.context_processor
    def inject_sub_status():
        # حالة اشتراك المشترك الحالي (لعرض تنبيه قرب الانتهاء) — None للمدير أو الوضع المفتوح
        info = None
        if app_password and session.get("role") == "sub":
            sub = db.session.get(Subscriber, session.get("sub_id"))
            if sub and sub.is_active():
                info = {"name": sub.name, "days_left": sub.days_left(),
                        "end_date": sub.end_date.strftime("%Y-%m-%d")}
        # إعلان المدير (يظهر لكل المستخدمين إن وُجد)
        ann = db.session.get(AppSetting, "announcement")
        announcement = ann.value if (ann and ann.value.strip()) else None
        # هل الاستعادة عبر تلغرام مفعّلة؟ (لإظهار زر «نسيت كلمة المرور»)
        recovery_on = telegram_client.is_configured() and _get_setting("recovery_off") != "1"
        return {"sub_status": info, "is_admin": is_admin(),
                "announcement": announcement, "recovery_on": recovery_on}

    @app.route("/announcement/save", methods=["POST"])
    def announcement_save():
        if not is_admin():
            return redirect(url_for("settings"))
        text = request.form.get("announcement", "").strip()
        row = db.session.get(AppSetting, "announcement")
        if text:
            if row:
                row.value = text
            else:
                db.session.add(AppSetting(key="announcement", value=text))
        elif row:
            db.session.delete(row)  # مسح النص يلغي الإعلان
        db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/recovery/toggle", methods=["POST"])
    def recovery_toggle():
        # تفعيل/إيقاف الاستعادة عبر تلغرام — للمدير فقط
        if not is_admin():
            return redirect(url_for("settings"))
        cur_off = _get_setting("recovery_off") == "1"
        _upsert_setting("recovery_off", "0" if cur_off else "1")
        db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/password/change", methods=["POST"])
    def password_change():
        # تغيير كلمة مرور المدير من المنصة (تُخزّن مشفّرة) — للمدير فقط
        if not is_admin():
            return redirect(url_for("settings"))
        new = request.form.get("new_password", "").strip()
        if len(new) >= 6:
            _upsert_setting("admin_password_hash", generate_password_hash(new))
            db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/password/forgot", methods=["POST"])
    def password_forgot():
        # استعادة عبر تلغرام: يرسل رمزاً مؤقتاً لمحادثة المدير
        if not app_password:
            return redirect(url_for("login"))
        if _get_setting("recovery_off") == "1":
            return render_template("login.html",
                                   error="الاستعادة عبر تلغرام معطّلة حالياً. تواصل مع صاحب المنصة.")
        if not telegram_client.is_configured():
            return render_template("login.html",
                                   error="الاستعادة عبر تلغرام غير متاحة (تلغرام غير مضبوط).")
        import random
        from datetime import datetime, timezone, timedelta
        code = f"{random.randint(0, 999999):06d}"
        _upsert_setting("reset_code_hash", generate_password_hash(code))
        _upsert_setting("reset_code_expiry",
                        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
        db.session.commit()
        telegram_client.send_message(
            f"🔑 <b>رمز استعادة كلمة مرور Algomatix</b>: <code>{code}</code>\n"
            f"صالح 10 دقائق. إن لم تطلبه، تجاهله.")
        return render_template("login.html", reset_stage=True,
                               info="أرسلنا رمزاً إلى تلغرامك. أدخله مع كلمة مرور جديدة.")

    @app.route("/password/reset", methods=["POST"])
    def password_reset():
        # التحقق من رمز تلغرام وتعيين كلمة مرور جديدة
        if not app_password:
            return redirect(url_for("login"))
        locked = _login_locked_minutes()
        if locked:
            return render_template("login.html", error=f"محاولات كثيرة. حاول بعد {locked} دقيقة.")
        from datetime import datetime, timezone
        code = request.form.get("code", "").strip()
        new = request.form.get("new_password", "").strip()
        ch = db.session.get(AppSetting, "reset_code_hash")
        exp = db.session.get(AppSetting, "reset_code_expiry")
        valid = False
        if ch and exp and code and check_password_hash(ch.value, code):
            try:
                valid = datetime.fromisoformat(exp.value) >= datetime.now(timezone.utc)
            except (ValueError, TypeError):
                valid = False
        if valid and len(new) >= 6:
            _clear_login_fails()
            _upsert_setting("admin_password_hash", generate_password_hash(new))
            for k in ("reset_code_hash", "reset_code_expiry"):
                r = db.session.get(AppSetting, k)
                if r:
                    db.session.delete(r)
            db.session.commit()
            return render_template("login.html", info="✅ تم تغيير كلمة المرور. سجّل الدخول بها.")
        _record_login_fail()  # تخمين رمز خاطئ يُحتسب ضمن حدّ المحاولات
        return render_template("login.html", reset_stage=True,
                               error="الرمز غير صحيح أو منتهٍ، أو كلمة المرور قصيرة (6 أحرف على الأقل).")

    db.init_app(app)

    @app.teardown_request
    def _cleanup_db_session(exc):
        # حماية من الجلسات الفاسدة: أي طلب انتهى بخطأ يُرجَع تراجعه فوراً،
        # فلا تعلق حالة PendingRollback وتكسر الطلبات اللاحقة (حدثت فعلياً 2026-07-04).
        if exc is not None:
            db.session.rollback()
    with app.app_context():
        db.create_all()  # ينشئ الجداول لو ما كانت موجودة
        # هجرة خفيفة: أعمدة أُضيفت لجداول موجودة مسبقاً (create_all لا يعدّل الجداول القائمة)
        from sqlalchemy import text as _sql
        for stmt in ("ALTER TABLE subscriber ADD COLUMN last_login TIMESTAMP",):
            try:
                db.session.execute(_sql(stmt))
                db.session.commit()
            except Exception:  # noqa: BLE001 — العمود موجود أصلاً: تجاهل
                db.session.rollback()
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
    app.jinja_env.globals["measures_met"] = screener.measures_met
    app.jinja_env.globals["bullish_reasons"] = screener.bullish_reasons
    app.jinja_env.globals["UNIVERSE"] = screener.UNIVERSE  # لاقتراح الرموز في البحث

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

    @app.template_filter("growth_icon")
    def growth_icon(score):
        """أيقونة مستوى قوة النمو (Catalyst): أخضر/أصفر/أحمر حسب الرقم (0–100)."""
        if score is None:
            return ""
        if score >= 80:
            return "🟢"   # نمو قوي
        if score >= 40:
            return "🟡"   # نمو متوسط أو جيد
        return "🔴"       # نمو ضعيف

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
        min_measures = _to_float("min_measures")  # عدد المقاييس الإيجابية المجتمعة (الأدنى)
        filters = {
            "piotroski_min": _to_float("piotroski_min"),
            "catalyst_min": _to_float("catalyst_min"),
            "price_max": _to_float("price_max"),
            "sector": request.args.get("sector", "").strip() or None,
            "recent_gain_max": screener.EARLY_MAX_RECENT_GAIN if not_risen else None,
            "float_max": float_max,
            "min_measures": int(min_measures) if min_measures is not None else None,
        }
        results = screener.filter_records(records, **filters)

        # ترتيب النتائج حسب اختيار المستخدم (افتراضياً: قوة التأكيد)
        sort = request.args.get("sort", "confidence")
        if sort == "growth":
            results.sort(key=lambda r: (r.get("catalyst") is not None, r.get("catalyst") or 0), reverse=True)
        elif sort == "price":
            results.sort(key=lambda r: (r.get("price") is None, r.get("price") or 0))
        else:  # confidence — عدد المقاييس المجتمعة
            sort = "confidence"
            results.sort(key=lambda r: screener.measures_met(r), reverse=True)

        screener.attach_sparklines(results)  # رسم مصغّر لكل بطاقة (من جدول الأسعار — بلا API)
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
            sort=sort,
        )

    @app.route("/gems")
    def gems():
        # الجواهر المخفية = نفس فلتر Piotroski>=8 من الماسح، بصفحة مستقلة
        records, latest = screener.load_records()
        results = screener.filter_records(records, piotroski_min=8)
        screener.attach_sparklines(results)
        return render_template("gems.html", results=results, latest=latest, total=len(records))

    @app.route("/leaders")
    def leaders():
        # القادة المستقبليون = أعلى 10 أسهم حسب Catalyst (بيانات الماسح نفسها، ترتيب مختلف)
        records, latest = screener.load_records()
        results = screener.filter_records(records)[:10]
        screener.attach_sparklines(results)
        return render_template("leaders.html", results=results, latest=latest, total=len(records))

    @app.route("/prelaunch")
    def prelaunch():
        # قبل الانطلاق: أسهم مبكرة (قيد الشحن/بداية اختراق) لم تصعد بعد، مرتّبة بقوة التأكيد
        candidates = screener.early_launch_candidates()
        return render_template("prelaunch.html", candidates=candidates)

    @app.route("/signals")
    def signals_page():
        # كل الإشارات الأخيرة + نسبة نجاح كل نوع تاريخياً (من الكاش، بلا API)
        _, _, type_stats = screener.signals_performance()
        return render_template("signals.html",
                               signals=screener.recent_signals(limit=50), type_stats=type_stats)

    @app.route("/learn")
    def learn():
        # قسم تعليمي: قاموس مصطلحات مبسّط (محتوى ثابت — بلا استدعاءات API)
        glossary = [
            {"icon": "🎯", "title": "قوة التأكيد (الرقم الكبير في البطاقة)", "terms": [
                {"name": "قوة التأكيد / عدد المقاييس المجتمعة", "id": "confidence",
                 "desc": "الرقم الأخضر الكبير في بطاقة السهم. يعدّ كم عاملاً إيجابياً اجتمع على السهم معاً: كل مؤشر فني صاعد (مثل EMA و MACD و RSI...) + سيولة داخلة + تفوّق على السوق + جودة مالية عالية (Piotroski) + نمو قوي (Catalyst). كلما زاد الرقم، زاد تضافر الأدلة وكان التأكيد أقوى (الأقصى ~16). تعليمي فقط — ليس ضماناً ولا توصية.",
                 "example": "سهم رقمه 12 اجتمعت عليه 12 إشارة إيجابية — تأكيد أقوى من سهم رقمه 5."},
            ]},
            {"icon": "🏦", "title": "الجودة المالية للشركة", "terms": [
                {"name": "Piotroski (بيوتروسكي)", "id": "piotroski",
                 "desc": "درجة من 0 إلى 9 تقيس صحة الشركة المالية (ربحيتها، ديونها، كفاءتها). كلما زادت الدرجة كانت الشركة أمتن مالياً. 8 أو 9 تعتبر قوية.",
                 "example": "شركة درجتها 8 تعني أنها اجتازت 8 من 9 اختبارات صحّة مالية."},
                {"name": "ROE (العائد على حقوق الملكية)",
                 "desc": "كم ربح تحقّقه الشركة مقابل أموال المساهمين. أعلى = استغلال أفضل لأموال الملّاك.",
                 "example": "ROE = 15% يعني كل 100 ريال من المساهمين تولّد 15 ريال ربح."},
                {"name": "ROA (العائد على الأصول)",
                 "desc": "كم ربح تحقّقه الشركة مقابل كل أصولها (مصانع، نقد، معدّات). يقيس كفاءة استخدام الموارد.",
                 "example": None},
                {"name": "P/E (مكرّر الربحية)",
                 "desc": "كم يدفع المستثمر مقابل كل ريال ربح. مرتفع = السوق يتوقّع نمواً كبيراً (أو السهم غالٍ).",
                 "example": "P/E = 20 يعني السعر يعادل 20 ضعف ربح السهم السنوي."},
                {"name": "PEG",
                 "desc": "يعدّل مكرّر الربحية حسب نمو الشركة. أقل من 1 يُعتبر تقييماً معقولاً مقابل النمو.",
                 "example": None},
            ]},
            {"icon": "🚀", "title": "النمو (Catalyst)", "terms": [
                {"name": "Catalyst / قوة النمو", "id": "catalyst",
                 "desc": "درجة من 0 إلى 100 تقيس سرعة نمو الشركة (مبيعات وأرباح). كلما زادت كانت أسرع نمواً. 🟢 قوي (80+) 🟡 متوسط (40-79) 🔴 ضعيف (أقل من 40).",
                 "example": "درجة 85 تعني الشركة تنمو بسرعة واضحة."},
            ]},
            {"icon": "📈", "title": "الاتجاه والزخم", "terms": [
                {"name": "EMA (المتوسط المتحرّك)",
                 "desc": "خط يوضّح اتجاه السعر العام. السعر فوق الخط = اتجاه صاعد، وتحته = هابط.",
                 "example": None},
                {"name": "MACD (زخم السعر)",
                 "desc": "يقيس قوة الحركة وتسارعها. إيجابي = زخم صاعد يدعم استمرار الصعود.",
                 "example": None},
                {"name": "RSI (القوة النسبية)",
                 "desc": "مقياس من 0 إلى 100 يوضّح هل السهم اشتُري بكثرة (فوق 70) أو بيع بكثرة (تحت 30).",
                 "example": None},
                {"name": "ADX (قوة الاتجاه)",
                 "desc": "يقيس قوة الاتجاه (وليس اتجاهه). قيمة عالية = اتجاه قوي واضح، منخفضة = حركة عرضية بلا اتجاه.",
                 "example": None},
                {"name": "سوبرترند (Supertrend)",
                 "desc": "مؤشر يلوّن الاتجاه: أخضر صاعد وأحمر هابط، ويساعد على متابعة الاتجاه الحالي.",
                 "example": None},
            ]},
            {"icon": "💥", "title": "الاختراق والحجم", "terms": [
                {"name": "اختراق القمة (Breakout)",
                 "desc": "تجاوز السعر أعلى نقطة خلال فترة (مثلاً 20 يوماً). كثيراً ما يسبق بداية حركة صاعدة.",
                 "example": None},
                {"name": "انضغاط بولينجر (Squeeze)",
                 "desc": "تضيّق التذبذب بشدّة — مثل نابض مشدود. غالباً يسبق حركة قوية (صعوداً أو هبوطاً).",
                 "example": None},
                {"name": "الحجم (Volume)",
                 "desc": "عدد الأسهم المتداولة. حجم مرتفع مع الحركة = اهتمام حقيقي يدعم الحركة.",
                 "example": None},
                {"name": "تراكم الحجم (OBV)",
                 "desc": "يتتبّع تدفّق الأسهم داخلاً وخارجاً. صاعد = تراكم (شراء تدريجي) قد يسبق الصعود.",
                 "example": None},
            ]},
            {"icon": "💧", "title": "السيولة والقوة", "terms": [
                {"name": "السيولة الداخلة (Money Flow)",
                 "desc": "تقدير لتدفّق الأموال إلى السهم. داخلة = ضغط شراء، خارجة = ضغط بيع.",
                 "example": None},
                {"name": "أقوى من السوق (القوة النسبية)",
                 "desc": "مقارنة أداء السهم بأداء السوق العام. أقوى من السوق = السهم يتفوّق على المؤشر.",
                 "example": None},
            ]},
            {"icon": "🎯", "title": "خطة التداول (ATR)", "terms": [
                {"name": "ATR (متوسط المدى الحقيقي)",
                 "desc": "يقيس مقدار تحرّك السهم يومياً (تقلّبه). يُستخدم لحساب مسافات منطقية للوقف والهدف.",
                 "example": None},
                {"name": "الدخول / الوقف / الهدف",
                 "desc": "خطة تعليمية: الدخول سعر بداية الصفقة، الوقف حدّ الخسارة لحماية رأس المال، الهدف مستوى جني الربح — محسوبة من ATR كمثال تعليمي لا توصية.",
                 "example": None},
            ]},
            {"icon": "🕵️", "title": "المطّلعون (Insiders)", "terms": [
                {"name": "معاملات المطّلعين (EDGAR)",
                 "desc": "بيع وشراء المدراء وكبار المسؤولين لأسهم شركتهم، من هيئة الأوراق المالية الأمريكية (SEC). شراؤهم قد يعكس ثقة بالشركة.",
                 "example": None},
            ]},
        ]
        return render_template("learn.html", glossary=glossary)

    @app.route("/how")
    def how():
        # صفحة تعليمية: كيف تعمل المنصة (محتوى ثابت — بلا استدعاءات API)
        return render_template("how.html")

    @app.route("/health")
    def health():
        # لوحة صحة المنصة — للمدير فقط (من قاعدة البيانات، بلا استدعاء API)
        if not is_admin():
            return redirect(url_for("settings"))
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        def _aware(dt):
            return dt.replace(tzinfo=timezone.utc) if (dt and dt.tzinfo is None) else dt

        screen_rows = StockCache.query.filter(StockCache.ticker.like("screen:%")).all()
        updates = [_aware(r.updated_at) for r in screen_rows if r.updated_at]
        last_update = max(updates) if updates else None
        fresh = sum(1 for u in updates if (now - u) <= timedelta(hours=30))
        hours_since = round((now - last_update).total_seconds() / 3600, 1) if last_update else None

        subs = Subscriber.query.all()
        health = {
            "hours_since": hours_since,
            "last_update": last_update.strftime("%Y-%m-%d %H:%M") + " UTC" if last_update else None,
            "stocks_total": len(screen_rows),
            "stocks_fresh": fresh,
            "telegram": telegram_client.is_configured(),
            "subs_total": len(subs),
            "subs_active": sum(1 for s in subs if s.is_active()),
            "subs_soon": sum(1 for s in subs if s.is_active() and s.days_left() <= 7),
            "signals": Signal.query.count(),
            "alerts": PriceAlert.query.filter_by(active=True).count(),
            "price_points": PricePoint.query.count(),
            "notes": StockNote.query.count(),
        }
        return render_template("health.html", health=health)

    @app.route("/settings")
    def settings():
        # إعدادات المنصة (المظهر/الترتيب/مبلغ المحاكاة تُحفظ في المتصفح — localStorage)
        # إدارة المشتركين تظهر للمدير فقط
        subs = []
        security = None
        if is_admin():
            subs = Subscriber.query.order_by(Subscriber.end_date.desc()).all()
            tg = telegram_client.is_configured()
            security = {
                "pw_changed": _get_setting("admin_password_hash") is not None,
                "telegram": tg,
                "recovery_on": tg and _get_setting("recovery_off") != "1",
                "recovery_off": _get_setting("recovery_off") == "1",
            }
        return render_template("settings.html", is_admin=is_admin(), subscribers=subs,
                               security=security)

    @app.route("/subscribers/add", methods=["POST"])
    def subscribers_add():
        if not is_admin():
            return redirect(url_for("settings"))
        name = request.form.get("name", "").strip()
        try:
            days = int(request.form.get("days", "30"))
        except (TypeError, ValueError):
            days = 30
        days = min(max(days, 1), 3650)  # بين يوم و10 سنوات
        if name:
            from datetime import date, timedelta
            # رمز فريد للمشترك
            code = secrets.token_hex(4).upper()
            while Subscriber.query.filter_by(access_code=code).first():
                code = secrets.token_hex(4).upper()
            today = date.today()
            db.session.add(Subscriber(
                name=name, access_code=code, start_date=today,
                end_date=today + timedelta(days=days)))
            db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/subscribers/extend", methods=["POST"])
    def subscribers_extend():
        if not is_admin():
            return redirect(url_for("settings"))
        sub = db.session.get(Subscriber, request.form.get("id"))
        try:
            days = int(request.form.get("days", "30"))
        except (TypeError, ValueError):
            days = 30
        if sub:
            from datetime import date, timedelta
            # نمدّد من تاريخ الانتهاء إن كان مستقبلياً، وإلا من اليوم
            base = sub.end_date if sub.end_date >= date.today() else date.today()
            sub.end_date = base + timedelta(days=min(max(days, 1), 3650))
            db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/subscribers/remove", methods=["POST"])
    def subscribers_remove():
        if not is_admin():
            return redirect(url_for("settings"))
        sub = db.session.get(Subscriber, request.form.get("id"))
        if sub:
            db.session.delete(sub)
            db.session.commit()
        return redirect(url_for("settings"))

    @app.route("/notes")
    def notes():
        # كل ملاحظات المستخدم على الأسهم (مرتّبة بالأحدث تعديلاً)
        items = StockNote.query.filter_by(user_id=GUEST_USER).order_by(
            StockNote.updated_at.desc()).all()
        return render_template("notes.html", notes=items)

    @app.route("/export/scanner.csv")
    def export_scanner():
        # تصدير نتائج الماسح كملف CSV (يفتح بـExcel) — من الكاش، بلا API
        import csv
        import io
        records, _ = screener.load_records()
        records.sort(key=lambda r: screener.measures_met(r), reverse=True)
        buf = io.StringIO()
        buf.write("﻿")  # BOM ليعرض Excel العربية صح
        w = csv.writer(buf)
        w.writerow(["الرمز", "الشركة", "القطاع", "السعر", "التغير اليومي %",
                    "Piotroski", "النمو", "قوة التأكيد"])
        for r in records:
            w.writerow([
                r.get("ticker"), r.get("name"), r.get("sector"),
                r.get("price"), r.get("change_percent"),
                r.get("piotroski"), r.get("catalyst"), screener.measures_met(r),
            ])
        return Response(
            buf.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=algomatix_scanner.csv"},
        )

    @app.route("/pulse")
    def pulse():
        # نبض السوق التاريخي: نسبة الأسهم الصاعدة عبر الأيام (من لقطات المزاج المخزّنة)
        snaps = MarketMoodSnapshot.query.order_by(MarketMoodSnapshot.date.asc()).all()
        chart = None
        if len(snaps) >= 2:
            W, H, pad = 900, 260, 14
            vals = [s.bull_pct for s in snaps]
            n = len(vals)
            step = (W - 2 * pad) / (n - 1)
            pts = []
            for i, v in enumerate(vals):
                x = pad + i * step
                y = pad + (H - 2 * pad) * (1 - v / 100.0)
                pts.append(f"{x:.1f},{y:.1f}")
            chart = {
                "points": " ".join(pts),
                "area_points": f"{pad:.1f},{H - pad} " + " ".join(pts) + f" {pad + (n - 1) * step:.1f},{H - pad}",
                "width": W, "height": H, "days": n,
                "first_date": snaps[0].date.strftime("%Y-%m-%d"),
                "last_date": snaps[-1].date.strftime("%Y-%m-%d"),
                "first_pct": vals[0], "last_pct": vals[-1],
                "up": vals[-1] >= vals[0],
            }
        return render_template("pulse.html", snaps=snaps, chart=chart)

    @app.route("/movers")
    def movers():
        # الرابحون والخاسرون اليوم: أعلى/أدنى تغيّر يومي من الـ32 (من الكاش، بلا API)
        records, latest = screener.load_records()
        measured = [r for r in records if r.get("change_percent") is not None]
        gainers = sorted(measured, key=lambda r: r["change_percent"], reverse=True)[:5]
        losers = sorted(measured, key=lambda r: r["change_percent"])[:5]
        return render_template("movers.html", gainers=gainers, losers=losers, latest=latest)

    @app.route("/earnings")
    def earnings():
        # رزنامة الأرباح: الأسهم ذات موعد أرباح قادم، مرتّبة بالأقرب (من الكاش — بلا استدعاء API)
        records, latest = screener.load_records()
        upcoming = [r for r in records if r.get("days_to_earnings") is not None]
        upcoming.sort(key=lambda r: r["days_to_earnings"])
        return render_template("earnings.html", upcoming=upcoming, latest=latest)

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
        # محاكاة "لو تابعت الإشارات": استثمار مبلغ عند كل إشارة (قابل للتخصيص من الإعدادات)
        try:
            PER_TRADE = float(request.args.get("amount", 1000))
        except (TypeError, ValueError):
            PER_TRADE = 1000.0
        PER_TRADE = min(max(PER_TRADE, 1.0), 1_000_000.0)  # حدود منطقية
        measured = [r for r in rows if r.get("return_pct") is not None]
        sim = None
        if measured:
            invested = PER_TRADE * len(measured)
            value = sum(PER_TRADE * (1 + r["return_pct"] / 100.0) for r in measured)
            sim = {
                "per_trade": PER_TRADE, "count": len(measured), "invested": invested,
                "value": value, "pnl": value - invested,
                "pnl_pct": (value - invested) / invested * 100.0 if invested else None,
            }
        return render_template(
            "performance.html", rows=rows, overall=overall, type_stats=type_stats, sim=sim,
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
        # سجل الماسح لنفس السهم (لعرض قوة التأكيد والملخّص الذكي) — None لو خارج قائمة المنصة
        records, _ = screener.load_records()
        scan = next((r for r in records if r["ticker"] == report["ticker"]), None)
        summary = analysis.smart_summary(report, scan)  # ملخّص ذكي مُولّد آلياً (بلا API)
        # أسهم من نفس القطاع للمقارنة (من الكاش، بلا API) — الأعلى قوة تأكيد أولاً
        peers = []
        if report.get("sector"):
            peers = [r for r in records
                     if r.get("sector") == report["sector"] and r["ticker"] != report["ticker"]]
            peers.sort(key=lambda r: screener.measures_met(r), reverse=True)
            peers = peers[:6]
        # ملاحظة المستخدم الشخصية على هذا السهم (إن وُجدت)
        note = StockNote.query.filter_by(user_id=GUEST_USER, ticker=report["ticker"]).first()
        return render_template("stock.html", report=report, ticker=report["ticker"],
                               scan=scan, summary=summary, peers=peers,
                               note=(note.body if note else ""))

    @app.route("/stock/<ticker>/note", methods=["POST"])
    def stock_note_save(ticker):
        ticker = ticker.upper().strip()
        body = request.form.get("note", "").strip()
        note = StockNote.query.filter_by(user_id=GUEST_USER, ticker=ticker).first()
        if body:
            if note:
                note.body = body
            else:
                db.session.add(StockNote(ticker=ticker, user_id=GUEST_USER, body=body))
        elif note:
            db.session.delete(note)  # مسح الملاحظة إذا فُرّغت
        db.session.commit()
        return redirect(url_for("stock_report", ticker=ticker))

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
                # نقاط قوة/تنبيهات خفيفة من البيانات المتاحة (بلا استدعاء API إضافي)
                s["summary"] = analysis.smart_summary(s)
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

    # ===================== التنبيهات السعرية =====================

    @app.route("/alerts")
    def alerts():
        items = PriceAlert.query.filter_by(user_id=GUEST_USER).order_by(
            PriceAlert.active.desc(), PriceAlert.created_at.desc()).all()
        # السعر الحالي من كاش الماسح (فوري وبلا استهلاك حصة)
        records, _ = screener.load_records()
        cache_prices = {r["ticker"]: r.get("price") for r in records}
        rows = [{
            "id": a.id, "ticker": a.ticker, "direction": a.direction,
            "target_price": a.target_price, "active": a.active,
            "triggered_at": a.triggered_at,
            "current": cache_prices.get(a.ticker),
        } for a in items]
        telegram_on = telegram_client.is_configured()
        return render_template("alerts.html", rows=rows, telegram_on=telegram_on)

    @app.route("/alerts/add", methods=["POST"])
    def alerts_add():
        ticker = request.form.get("ticker", "").strip().upper()
        direction = request.form.get("direction", "").strip()
        target_raw = request.form.get("target_price", "").strip()
        try:
            target = float(target_raw)
        except (TypeError, ValueError):
            target = None
        # نقبل فقط مدخلات صحيحة (سهم + اتجاه معروف + سعر موجب)
        if ticker and direction in ("below", "above") and target is not None and target > 0:
            db.session.add(PriceAlert(
                ticker=ticker, direction=direction, target_price=target, user_id=GUEST_USER))
            db.session.commit()
        return redirect(url_for("alerts"))

    @app.route("/alerts/remove", methods=["POST"])
    def alerts_remove():
        item_id = request.form.get("id")
        item = PriceAlert.query.filter_by(id=item_id, user_id=GUEST_USER).first()
        if item:
            db.session.delete(item)
            db.session.commit()
        return redirect(url_for("alerts"))

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
