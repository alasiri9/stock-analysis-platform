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

        # فحص التنبيهات السعرية مقابل الأسعار المحدَّثة (يرسل تلغرام عند التحقّق)
        try:
            fired = screener.check_price_alerts()
            if fired:
                print(f"[scheduler] أُطلقت {fired} تنبيهات سعرية")
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر فحص التنبيهات السعرية: {e}")

        # تنبيه الأسهم الجديدة الداخلة قائمة "الاستعداد للانطلاق"
        try:
            n = screener.notify_new_prelaunch()
            if n:
                print(f"[scheduler] {n} سهم جديد جاهز للانطلاق (أُرسلت تنبيهات)")
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر تنبيه الاستعداد للانطلاق: {e}")

        # تذكير المدير بالمشتركين الذين تنتهي اشتراكاتهم قريباً (للتجديد)
        try:
            n = _notify_expiring_subs()
            if n:
                print(f"[scheduler] تذكير تجديد: {n} مشترك قرب الانتهاء")
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر إرسال تذكير التجديد: {e}")

        # لقطة يومية لمزاج السوق (لرسم نبض السوق التاريخي)
        try:
            from models import db, MarketMoodSnapshot
            from datetime import date as _date
            recs, _ = screener.load_records()
            mood = screener.market_mood(recs)
            if mood:
                db.session.merge(MarketMoodSnapshot(
                    date=_date.today(), bull=mood["bull"], neutral=mood["neutral"],
                    bear=mood["bear"], bull_pct=mood["bull_pct"]))
                db.session.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر تسجيل نبض السوق: {e}")

        # ختاماً: التقرير الصباحي المجمّع بتلغرام (خامل بلا إعداد، وفشله لا يؤثر)
        try:
            _send_daily_report()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر إرسال التقرير الصباحي: {e}")

        # تقرير أسبوعي (السبت فقط) — ملخّص أداء الإشارات
        try:
            if datetime.now(timezone.utc).weekday() == 5:  # 5 = السبت
                _send_weekly_report()
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] تعذّر إرسال التقرير الأسبوعي: {e}")


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

    # أقوى 3 أسهم نمواً (Catalyst)
    ranked = sorted(
        (r for r in records if r.get("catalyst") is not None),
        key=lambda r: r["catalyst"], reverse=True,
    )[:3]
    if ranked:
        lines.append("")
        lines.append("🏆 <b>أقوى الأسهم اليوم:</b>")
        for i, r in enumerate(ranked, 1):
            lines.append(f"{i}. {r['ticker']} — نمو {r['catalyst']:.0f}")

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
            kind = {"piotroski_strong": "💎 جودة", "catalyst_strong": "⚡ نمو",
                    "golden": "🥇 ذهبية"}.get(s.signal_type, s.signal_type)
            lines.append(f"• {s.ticker} ({kind})")

    # تنبيه أرباح وشيكة (خلال يومين) — تذبذب مرتفع متوقّع
    soon_earn = sorted(
        (r for r in records
         if r.get("days_to_earnings") is not None and r["days_to_earnings"] <= 2),
        key=lambda r: r["days_to_earnings"],
    )
    if soon_earn:
        lines.append("")
        lines.append("⚠️ <b>أرباح وشيكة (خلال يومين):</b>")
        for r in soon_earn:
            dte = r["days_to_earnings"]
            when = "اليوم" if dte == 0 else ("غداً" if dte == 1 else f"بعد {dte} أيام")
            lines.append(f"• {r['ticker']} — {when}")

    # ملخص المحفظة (محفظة المدير — المرجعية؛ التقرير يصل تلغرام المدير)
    holdings = PortfolioHolding.query.filter_by(user_id="admin").all()
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


def _send_weekly_report():
    """يبني تقريراً أسبوعياً لأداء الإشارات ويرسله بتلغرام (لو الميزة مفعّلة).

    يُستدعى داخل app_context أيام السبت. كل البيانات من الكاش — لا استدعاءات API.
    """
    if not telegram_client.is_configured():
        return

    rows, overall, _ = screener.signals_performance()
    lines = ["🗓️ <b>تقرير Algomatix الأسبوعي</b>", "", "📊 <b>أداء إشاراتنا حتى الآن:</b>"]

    if overall and overall.get("count"):
        lines.append(f"• عدد الإشارات المقيسة: {overall['count']}")
        if overall.get("win_rate") is not None:
            lines.append(f"• نسبة الإشارات الرابحة: {overall['win_rate']:.0f}%")
        if overall.get("avg") is not None:
            lines.append(f"• متوسط العائد منذ الإشارة: {overall['avg']:+.1f}%")
        if overall.get("best_ticker") and overall.get("best") is not None:
            lines.append(f"• أفضل إشارة: {overall['best_ticker']} ({overall['best']:+.1f}%)")
    else:
        lines.append("• لا توجد إشارات مقيسة بعد.")

    # أفضل 3 أسهم منذ إشارتها
    ranked = sorted(
        (r for r in (rows or []) if r.get("return_pct") is not None),
        key=lambda r: r["return_pct"], reverse=True,
    )[:3]
    if ranked:
        lines.append("")
        lines.append("🏆 <b>الأفضل منذ الإشارة:</b>")
        for r in ranked:
            lines.append(f"• {r['ticker']} ({r['return_pct']:+.1f}%)")

    lines.append("")
    lines.append("📌 تعليمي — الأداء السابق لا يضمن المستقبل.")
    lines.append("https://algomatix-production.up.railway.app")
    sent = telegram_client.send_message("\n".join(lines))
    print(f"[scheduler] التقرير الأسبوعي: {'أُرسل ✅' if sent else 'فشل الإرسال'}")


def _notify_expiring_subs():
    """ينبّه المدير بتلغرام بالمشتركين الذين تنتهي اشتراكاتهم خلال يومين (للتجديد).

    يُستدعى داخل app_context من التحديث الليلي. يُرجع عدد المشتركين المنبَّه عنهم.
    """
    if not telegram_client.is_configured():
        return 0
    from models import Subscriber
    subs = Subscriber.query.all()
    soon = sorted((s for s in subs if s.is_active() and 0 <= s.days_left() <= 2),
                  key=lambda s: s.days_left())
    if not soon:
        return 0
    lines = ["⏳ <b>تذكير تجديد اشتراكات</b>", "",
             "مشتركون تنتهي اشتراكاتهم قريباً — تواصل معهم للتجديد:"]
    for s in soon:
        d = s.days_left()
        when = "اليوم" if d == 0 else ("غداً" if d == 1 else f"بعد {d} أيام")
        lines.append(f"• {s.name} — {when} ({s.end_date:%Y-%m-%d})")
    lines.append("")
    lines.append("https://algomatix-production.up.railway.app")
    telegram_client.send_message("\n".join(lines))
    return len(soon)


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
