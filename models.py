"""
models.py — تعريف جداول قاعدة البيانات باستخدام SQLAlchemy.

ملاحظة على المبادئ الجوهرية:
- None ≠ 0 : نخزّن البيانات كما هي. القيمة الغائبة تبقى NULL (None) وليست صفر.
- stock_cache يخزّن البيانات كنص JSON (data_json) عشان نقدر نخزّن أي شكل بيانات
  بدون ما نضطر نعرّف عمود لكل مقياس.
"""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

# كائن قاعدة البيانات — يُربط بتطبيق Flask لاحقاً في app.py عبر db.init_app(app)
db = SQLAlchemy()


def _utcnow():
    """الوقت الحالي بتوقيت UTC (موحّد، بدون اعتماد على توقيت السيرفر المحلي)."""
    return datetime.now(timezone.utc)


class PortfolioSnapshot(db.Model):
    """لقطة يومية لقيمة المحفظة — يسجّلها المجدول الليلي لرسم منحنى الأداء.

    الأعمدة: (date [مفتاح أساسي — لقطة واحدة لكل يوم], total_cost, total_value)
    تُسجَّل فقط عندما تتوفر أسعار حالية لكل المقتنيات (None ≠ 0: لا لقطة ناقصة مضللة).
    """

    __tablename__ = "portfolio_snapshot"

    date = db.Column(db.Date, primary_key=True)
    total_cost = db.Column(db.Float, nullable=False)
    total_value = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<PortfolioSnapshot {self.date} value={self.total_value}>"


class PortfolioHolding(db.Model):
    """مقتنى في المحفظة الافتراضية — كل صف = عملية شراء سجّلها المستخدم.

    الأعمدة: (id, ticker, shares, buy_price, user_id, added_at)
    - shares: عدد الأسهم المشتراة (يقبل كسوراً مثل 0.5 سهم).
    - buy_price: سعر الشراء الذي أدخله المستخدم (إلزامي — أساس حساب الربح/الخسارة).
    """

    __tablename__ = "portfolio_holding"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    shares = db.Column(db.Float, nullable=False)
    buy_price = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.String(64), nullable=False, index=True)
    added_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<PortfolioHolding {self.ticker} x{self.shares}>"


class Watchlist(db.Model):
    """قائمة المتابعة — كل صف = سهم أضافه المستخدم.

    الأعمدة حسب المواصفات: (id, ticker, user_id, added_at, added_price)
    - added_price: سعر السهم لحظة الإضافة (نتتبّع منه العائد). قد يكون None
      لو ما توفّر السعر وقت الإضافة (لا نضع صفر ملفّق).
    """

    __tablename__ = "watchlist"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    user_id = db.Column(db.String(64), nullable=False, index=True)
    added_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    added_price = db.Column(db.Float, nullable=True)  # None = السعر لم يتوفّر وقت الإضافة

    def __repr__(self):
        return f"<Watchlist {self.ticker} user={self.user_id}>"


class PriceAlert(db.Model):
    """تنبيه سعري — ينبّه المستخدم بتلغرام عند وصول سهم لسعر مستهدف.

    - direction: 'below' (تحت السعر) أو 'above' (فوق السعر).
    - target_price: السعر المستهدف الذي يُطلق التنبيه عند تجاوزه.
    - active: True طالما لم يتحقق بعد؛ يُطفأ (False) بعد إطلاقه مرة واحدة.
    - triggered_at: وقت تحقّق التنبيه (None = لم يتحقق بعد).
    يُفحص مرة يومياً بعد تحديث الأسعار الليلي (الباقة المجانية لا تسمح بفحص لحظي).
    """

    __tablename__ = "price_alert"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    direction = db.Column(db.String(8), nullable=False)  # 'below' | 'above'
    target_price = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.String(64), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    triggered_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PriceAlert {self.ticker} {self.direction} {self.target_price} user={self.user_id}>"


class Subscriber(db.Model):
    """مشترك له صلاحية دخول للمنصة عبر رمز خاص، بمدة اشتراك محدّدة.

    - access_code: رمز الدخول الفريد الذي يستخدمه المشترك في صفحة الدخول.
    - start_date / end_date: مدة الاشتراك. ينتهي الدخول تلقائياً بعد end_date.
    """

    __tablename__ = "subscriber"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    access_code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)  # آخر دخول للمشترك
    # مفتاح FMP الخاص بالمشترك (اختياري) — لو أدخله يرى الأسعار اللحظية على حصّته هو
    # (لا على حصّة المنصة). None = بلا مفتاح → يرى سعر آخر تحديث ليلي.
    # يُخزّن مشفّراً (Fernet) — القيمة المشفّرة أطول بكثير من المفتاح الخام (~140 حرفاً)، لذا 256.
    fmp_api_key = db.Column(db.String(256), nullable=True)
    # وقت موافقة المشترك على تنبيه إخلاء المسؤولية (يُسجَّل مرة واحدة كإثبات).
    # None = لم يوافق بعد ⇒ تُعرض له صفحة التنبيه ولا تُفتح المنصة حتى يوافق.
    disclaimer_accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def is_active(self, today=None):
        """هل الاشتراك ساري (لم ينتهِ بعد)؟"""
        from datetime import date as _date
        today = today or _date.today()
        return self.start_date <= today <= self.end_date

    def days_left(self, today=None):
        from datetime import date as _date
        today = today or _date.today()
        return (self.end_date - today).days

    def __repr__(self):
        return f"<Subscriber {self.name} ends={self.end_date}>"


class MarketMoodSnapshot(db.Model):
    """لقطة يومية لمزاج أسهم المنصة (كم صاعد/محايد/هابط) — لرسم نبض السوق عبر الأيام."""

    __tablename__ = "market_mood_snapshot"

    date = db.Column(db.Date, primary_key=True)
    bull = db.Column(db.Integer, nullable=False)
    neutral = db.Column(db.Integer, nullable=False)
    bear = db.Column(db.Integer, nullable=False)
    bull_pct = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<MarketMoodSnapshot {self.date} bull%={self.bull_pct:.0f}>"


class AppSetting(db.Model):
    """إعدادات عامة للمنصة (مفتاح/قيمة) — مثل إعدادات الاستعادة وكلمة المرور."""

    __tablename__ = "app_setting"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")

    def __repr__(self):
        return f"<AppSetting {self.key}>"


class Message(db.Model):
    """رسالة من المدير لكل المستخدمين — تُحفظ في صندوق الرسائل (Inbox)."""

    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    # وجهة الرسالة: None = للجميع (كل المستخدمين)، أو رقم مشترك محدّد = لهذا المشترك فقط.
    subscriber_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<Message {self.id}>"


class MessageTrash(db.Model):
    """سلة مهملات الرسائل — خاصة بكل مستخدم (إخفاء لا يؤثّر على غيره).

    نقل الرسالة للمهملات = صف هنا (user_id, message_id). تبقى مخفية عن صندوق المستخدم للأبد،
    وقابلة للاستعادة خلال 30 يوماً؛ بعدها (أو بتصفية يدوية cleared=True) تختفي من السلة نهائياً.
    """

    __tablename__ = "message_trash"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64), nullable=False)   # admin أو sub:<رقم>
    message_id = db.Column(db.Integer, nullable=False)
    trashed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    cleared = db.Column(db.Boolean, nullable=False, default=False)  # صُفّيت يدوياً؟ (غير قابلة للاستعادة)
    cleared_at = db.Column(db.DateTime(timezone=True), nullable=True)  # وقت التصفية اليدوية (للحذف التلقائي بعده)

    __table_args__ = (db.UniqueConstraint("user_id", "message_id", name="uq_msgtrash_user_msg"),)

    def __repr__(self):
        return f"<MessageTrash u={self.user_id} m={self.message_id}>"


class StockCache(db.Model):
    """تخزين مؤقت لبيانات سهم — نقلّل عدد استدعاءات الـ API (الباقات المجانية محدودة).

    الأعمدة حسب المواصفات: (ticker, data_json, updated_at)
    - ticker هو المفتاح الأساسي (سهم واحد = صف واحد، نحدّثه عند كل جلب جديد).
    - data_json: نص JSON يحتوي البيانات المجمّعة للسهم.
    """

    __tablename__ = "stock_cache"

    ticker = db.Column(db.String(16), primary_key=True)
    data_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<StockCache {self.ticker} updated={self.updated_at}>"


class PricePoint(db.Model):
    """سعر إغلاق يومي لسهم — يُبنى تدريجياً لعرض مسار سعري حقيقي (رسم مصغّر) بلا استدعاء API إضافي.

    الأعمدة حسب المواصفات: (ticker, date, price)
    - المفتاح الأساسي مركّب (ticker, date): صف واحد لكل سهم في كل يوم تداول.
    - يُملأ من نفس بيانات الأسعار التاريخية التي تُجلب أصلاً لحساب المؤشرات الفنية.
    """

    __tablename__ = "price_point"

    ticker = db.Column(db.String(16), primary_key=True)
    date = db.Column(db.Date, primary_key=True)
    price = db.Column(db.Float, nullable=True)  # None = السعر لم يتوفّر لذلك اليوم

    def __repr__(self):
        return f"<PricePoint {self.ticker} {self.date}>"


class Signal(db.Model):
    """إشارة محسوبة لسهم (مثلاً تجاوز Piotroski حدّ معيّن) — لأغراض تعليمية لا توصية.

    الأعمدة حسب المواصفات: (ticker, signal_type, triggered_at, price_at_signal)
    - price_at_signal: السعر لحظة الإشارة. None لو ما توفّر (لا صفر ملفّق).
    """

    __tablename__ = "signals"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    signal_type = db.Column(db.String(64), nullable=False)
    triggered_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    price_at_signal = db.Column(db.Float, nullable=True)  # None = السعر لم يتوفّر

    def __repr__(self):
        return f"<Signal {self.ticker} {self.signal_type}>"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — تتبّع تطوّر الفرصة عبر الزمن (State / Change / Events / Performance)
# جداول جديدة فقط (تُنشأ تلقائياً عبر create_all — بلا ALTER لجداول قائمة، بلا Alembic).
# ═══════════════════════════════════════════════════════════════════════════


class StockSnapshot(db.Model):
    """لقطة تاريخية مختصرة لسهم بعد كل تحديث ليلي مكتمل — أساس «ماذا تغيّر اليوم؟».

    مفتاح أساسي مركّب (ticker, snap_date): لقطة واحدة لكل سهم في كل يوم (UPSERT عبر merge).
    نخزّن قيماً مفردة مختصرة فقط (لا blob كامل من StockCache). القيم الغائبة تبقى NULL (None ≠ 0).
    """

    __tablename__ = "stock_snapshot"

    ticker = db.Column(db.String(16), primary_key=True)
    snap_date = db.Column(db.Date, primary_key=True)
    analysis_price = db.Column(db.Float, nullable=True)
    catalyst = db.Column(db.Float, nullable=True)
    catalyst_complete = db.Column(db.Boolean, nullable=True)
    piotroski = db.Column(db.Integer, nullable=True)
    piotroski_computable = db.Column(db.Integer, nullable=True)
    measures_met = db.Column(db.Integer, nullable=True)
    tech_tilt_kind = db.Column(db.String(8), nullable=True)   # pos2/pos1/neu/neg1/neg2
    algomatix_score = db.Column(db.Float, nullable=True)
    structure_status = db.Column(db.String(8), nullable=True)  # bull/bear/neutral
    state_code = db.Column(db.String(20), nullable=True)       # الحالة المُحلّة (واعية بدورة الحياة)
    is_gem = db.Column(db.Boolean, nullable=True)
    is_ready = db.Column(db.Boolean, nullable=True)
    extra_json = db.Column(db.Text, nullable=True)             # محجوز للتوافق المستقبلي (بلا ALTER لاحقاً)

    def __repr__(self):
        return f"<StockSnapshot {self.ticker} {self.snap_date} {self.state_code}>"


class StockStateEvent(db.Model):
    """حدث انتقال حالة قابل للتتبّع (READY / LAUNCHED / INVALIDATED) — قائم على الانتقال.

    قاعدة عدم التكرار على مستوى القاعدة: UNIQUE(ticker, lifecycle_id, state_code) — حدث واحد
    لكل حالة داخل كل دورة حياة. lifecycle_id = 1 + عدد أحداث INVALIDATED السابقة للسهم.
    baseline_date/performance_baseline_price هما أساس حساب الأداء (Close-to-Close، مجمّدان).
    analysis_price للتدقيق فقط (السعر الذي رآه النظام لحظة الإشارة). live_price لا يمسّها أبداً.
    """

    __tablename__ = "stock_state_event"

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    lifecycle_id = db.Column(db.Integer, nullable=False)
    state_code = db.Column(db.String(20), nullable=False)
    prev_state = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)  # لحظة الاكتشاف
    event_date = db.Column(db.Date, nullable=True)          # تاريخ الإنشاء (توثيق فقط)
    baseline_date = db.Column(db.Date, nullable=True)       # جلسة EOD للتحليل (مرساة الآفاق)
    analysis_price = db.Column(db.Float, nullable=True)     # السعر الذي رآه النظام (تدقيق فقط)
    performance_baseline_price = db.Column(db.Float, nullable=True)  # إغلاق EOD = أساس الأداء الوحيد
    algomatix_score = db.Column(db.Float, nullable=True)
    catalyst = db.Column(db.Float, nullable=True)
    piotroski = db.Column(db.Integer, nullable=True)
    measures_met = db.Column(db.Integer, nullable=True)
    tech_tilt_kind = db.Column(db.String(8), nullable=True)
    structure_status = db.Column(db.String(8), nullable=True)
    stop = db.Column(db.Float, nullable=True)
    target = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("ticker", "lifecycle_id", "state_code", name="uq_event_ticker_lid_state"),
    )

    def __repr__(self):
        return f"<StockStateEvent {self.ticker} lc{self.lifecycle_id} {self.state_code}>"


class StockStateOutcome(db.Model):
    """نتيجة أداء حدث عند أفق زمني (1/5/10/20 جلسة تداول) — Close-to-Close من PricePoint.

    مفتاح أساسي مركّب (event_id, horizon_days): نتيجة واحدة لكل أفق لكل حدث (UPSERT عبر merge).
    القيم تبقى None (pending) حتى تنضج الجلسة المطلوبة — pending ليست Fail. لا live_price إطلاقاً.
    """

    __tablename__ = "stock_state_outcome"

    # مفتاح خارجي حقيقي على حدث موجود، مع حذف تعاقبي: حذف الحدث يحذف نتائجه (لا نتائج يتيمة).
    # (جدول جديد لم يُنشر بعد ⇒ يُعرَّف ضمن create_all بلا ALTER/migration على الإنتاج.)
    event_id = db.Column(db.Integer,
                         db.ForeignKey("stock_state_event.id", ondelete="CASCADE"),
                         primary_key=True)
    horizon_days = db.Column(db.Integer, primary_key=True)   # 1 / 5 / 10 / 20 (جلسات تداول)
    exit_date = db.Column(db.Date, nullable=True)
    close_price = db.Column(db.Float, nullable=True)
    return_pct = db.Column(db.Float, nullable=True)          # (exit - performance_baseline)/baseline ×100
    spy_return_pct = db.Column(db.Float, nullable=True)      # نفس الأفق، Close-to-Close
    alpha_pct = db.Column(db.Float, nullable=True)           # return_pct - spy_return_pct (مستقل عن النجاح)

    def __repr__(self):
        return f"<StockStateOutcome ev{self.event_id} {self.horizon_days}d>"
