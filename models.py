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
