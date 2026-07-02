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

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from services import radar
from services import screener

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
