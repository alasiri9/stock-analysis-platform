"""
scheduler.py — التحديث التلقائي اليومي لبيانات الماسح.

الفكرة:
- مجدول خلفي (APScheduler) يعمل داخل التطبيق نفسه (Procfile يشغّل worker واحداً،
  فلا خطر تشغيل مزدوج على Railway).
- كل يوم الساعة 01:00 UTC (بعد تصفّر حصة FMP المجانية منتصف الليل بساعة):
  يكرّر refresh_cache على دفعات حتى تكتمل كل الأسهم أو تتوقف عن التقدّم
  (انتهاء الحصة مثلاً) — فلا حاجة للضغط اليدوي على "تحديث البيانات".
- refresh_cache نفسها تحفظ الأسعار التاريخية (الشارت) لكل سهم، فلا حاجة
  لاستدعاء backfill منفصل.
"""

from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from services import radar
from services import screener
from services import telegram_client

# وقت التشغيل اليومي (UTC) — بعد تصفّر حد FMP بساعة أماناً
DAILY_HOUR_UTC = 1

_scheduler = None  # مرجع وحيد يمنع إنشاء مجدولين بنفس العملية


def _auto_refresh(app):
    """يشغّل التحديث على دفعات متتالية حتى الاكتمال أو توقف التقدّم."""
    with app.app_context():
        total_updated = 0
        for round_no in range(1, 7):  # حد أقصى 6 دفعات (يغطي 24 سهماً بسهولة)
            try:
                updated = screener.refresh_cache(time_budget=60)
            except Exception as e:  # noqa: BLE001 — لا نُسقط المجدول بخطأ عابر
                print(f"[scheduler] خطأ في الدفعة {round_no}: {e}")
                break
            total_updated += updated
            print(f"[scheduler] دفعة {round_no}: تحدّث {updated} سهماً")
            if updated == 0:
                break  # لا جديد: إمّا اكتمل الكل أو توقّف التقدّم (حصة/أخطاء)
        print(f"[scheduler] انتهى التحديث التلقائي — إجمالي المحدَّث: {total_updated} "
              f"({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")

        # بعد بيانات السوق: رادار المحفزات (EDGAR بلا حصص لكنه بطيء — دفعات أيضاً)
        radar_total = 0
        for round_no in range(1, 7):
            try:
                updated = radar.refresh_radar(time_budget=90)
            except Exception as e:  # noqa: BLE001
                print(f"[scheduler] خطأ في دفعة الرادار {round_no}: {e}")
                break
            radar_total += updated
            print(f"[scheduler] رادار — دفعة {round_no}: تحدّث {updated} سهماً")
            if updated == 0:
                break
        print(f"[scheduler] انتهى تحديث الرادار — إجمالي: {radar_total}")

        # لقطة يومية لقيمة المحفظة (بعد تحديث الأسعار — لمنحنى الأداء)
        try:
            from services import portfolio
            portfolio.record_snapshot()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر تسجيل لقطة المحفظة: {e}")

        # ختاماً: التقرير الصباحي المجمّع بتلغرام (خامل بلا إعداد، وفشله لا يؤثر)
        try:
            _send_daily_report()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر إرسال التقرير الصباحي: {e}")


def _send_daily_report():
    """يبني تقريراً صباحياً مجمّعاً ويرسله بتلغرام (لو الميزة مفعّلة).

    يُستدعى داخل app_context (من _auto_refresh بعد اكتمال التحديث الليلي).
    كل البيانات من الكاش — لا استدعاءات API خارجية.
    """
    if not telegram_client.is_configured():
        return  # الميزة غير مفعّلة — لا داعي لبناء التقرير أصلاً

    from models import PortfolioHolding  # استيراد محلي يتجنب دورة استيراد

    records, latest = screener.load_records()
    lines = ["📊 <b>تقرير Algomatix الصباحي</b>", ""]

    # حالة البيانات
    lines.append(f"✅ تحدّثت بيانات {len(records)} سهماً الليلة")

    # أقوى 3 أسهم زخماً (Catalyst)
    ranked = sorted(
        (r for r in records if r.get("catalyst") is not None),
        key=lambda r: r["catalyst"], reverse=True,
    )[:3]
    if ranked:
        lines.append("")
        lines.append("🏆 <b>أقوى الأسهم اليوم:</b>")
        for i, r in enumerate(ranked, 1):
            lines.append(f"{i}. {r['ticker']} — زخم {r['catalyst']:.0f}")

    # أقوى تجميع سيولة
    flows = sorted(
        (r for r in records if r.get("money_flow")),
        key=lambda r: r["money_flow"]["score"], reverse=True,
    )[:3]
    strong_flows = [r for r in flows if r["money_flow"]["status"] == "bull"]
    if strong_flows:
        lines.append("")
        lines.append("🟢 <b>أقوى تجميع سيولة:</b>")
        for r in strong_flows:
            lines.append(f"• {r['ticker']} — درجة {r['money_flow']['score']:.0f}")

    # الإشارات الجديدة خلال آخر 24 ساعة
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    new_sigs = [s for s in screener.recent_signals(limit=50)
                if (s.triggered_at.replace(tzinfo=timezone.utc)
                    if s.triggered_at.tzinfo is None else s.triggered_at) >= cutoff]
    if new_sigs:
        lines.append("")
        lines.append(f"⚡ <b>إشارات جديدة ({len(new_sigs)}):</b>")
        for s in new_sigs[:5]:
            kind = {"piotroski_strong": "💎 جودة", "catalyst_strong": "⚡ زخم",
                    "golden": "🥇 ذهبية"}.get(s.signal_type, s.signal_type)
            lines.append(f"• {s.ticker} ({kind})")

    # ملخص المحفظة (لو فيها مقتنيات)
    holdings = PortfolioHolding.query.all()
    if holdings:
        price_by = {r["ticker"]: r.get("price") for r in records}
        cost = value = 0.0
        complete = True
        for h in holdings:
            cost += h.shares * h.buy_price
            p = price_by.get(h.ticker)
            if p is None:
                complete = False
            else:
                value += h.shares * p
        if complete and cost:
            pnl = value - cost
            emoji = "📈" if pnl >= 0 else "📉"
            lines.append("")
            lines.append(f"💼 <b>محفظتك:</b> {value:,.2f}$ ({emoji} {pnl:+,.2f}$)")

    lines.append("")
    lines.append("https://algomatix-production.up.railway.app")
    sent = telegram_client.send_message("\n".join(lines))
    print(f"[scheduler] التقرير الصباحي: {'أُرسل ✅' if sent else 'فشل الإرسال'}")


def init_scheduler(app):
    """يهيّئ المجدول اليومي مرة واحدة لكل عملية."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _auto_refresh,
        CronTrigger(hour=DAILY_HOUR_UTC, minute=0, timezone="UTC"),
        args=[app],
        id="daily_refresh",
        replace_existing=True,
        misfire_grace_time=3600,  # لو فات الموعد (إعادة نشر مثلاً) يعوّضه خلال ساعة
    )
    _scheduler.start()
    print(f"[scheduler] التحديث التلقائي مفعّل — يومياً {DAILY_HOUR_UTC:02d}:00 UTC")
    return _scheduler
