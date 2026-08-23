"""عزل حالة الاختبارات بين كل اختبار وآخر (اختبارات فقط — بلا مساس بمنطق الإنتاج).

## السبب الجذري (مثبَت بالدليل)
السبب هو **الإسناد المباشر إلى أسماء داخل وحدات Python المشتركة أثناء الاختبار دون
استعادتها بعده**. وحدات الخدمات (`services.*`) كائنات مفردة في `sys.modules` تُشترَك
عبر العملية الواحدة (آلية استيراد Python المعتادة)، فأي إسناد على أسمائها يبقى نافذاً
لبقية العملية. كثير من ملفات الاختبار تستبدل دوالّ على هذه الوحدات بإسناد مباشر
(monkeypatch يدوي داخل الاختبارات) دون استعادة، مثل:
    scoring.piotroski_score = lambda f: {...}
    screener._period_return = lambda *a, **k: 0.0
    fmp_client.get_financials = lambda t: {...}
لأنها لا تستخدم pytest monkeypatch fixture، تبقى نافذة بعد انتهاء الاختبار وتتسرّب إلى
اختبارات ملفٍ لاحق يعتمد على السلوك الحقيقي (persistence/regression) ⇒ نتائج خاطئة
(TypeError/AttributeError/عدد لقطات ≠ 1). لذلك: 26 إخفاق مجتمعةً، و0 إخفاق منفرداً
(العملية المنفردة نظيفة). الدليل المباشر: تشغيل ملفٍ ملوِّث ثم persistence في عملية
واحدة يُعيد إخفاقات persistence، وتختفي عند تفعيل هذه الـfixture.

ملاحظة دقيقة: تنفّذ pytest المجموعة داخل عملية Python واحدة، لذلك تبقى وحدات
`services.*` المحفوظة في `sys.modules` مشتركة بين الاختبارات. وجود `app = create_app()`
على مستوى الوحدة يعني فقط إنشاء كائن التطبيق مرة واحدة عند استيراد `app` داخل تلك
العملية؛ لا يُبقي العملية أو سياق التطبيق قيد التشغيل، ولا يُنشئ وحدات الخدمات كسنجلتون،
وليس سبب التلوث.

## الحل
قبل كل اختبار نلتقط لقطة **سطحية** من `__dict__` لوحدات الخدمات المشتركة التي ثبت
تلوّثها، وبعده نعيد كل وحدة إلى لقطتها: نُرجع ما تغيّر، ونحذف ما أُضيف. النسخ السطحي
**مقصود** لأن التلوّث هو استبدال/إضافة/حذف *أسماء* على مستوى الوحدة (وهو السبب المثبت)،
فاستعادة خريطة الأسماء تكفي. لا نستخدم deepcopy لقواميس الوحدات لأنها قد تحوي جلسات
واتصالات ودوالّ لا يصحّ نسخها بعمق. ولا نوسّع النطاق ديناميكياً إلى كل `sys.modules`؛
نقتصر على وحدات الخدمات المذكورة أدناه.

هذا إصلاح على مستوى بنية الاختبارات فقط: لا يغيّر منطق الإنتاج ولا العتبات، ولا يحذف
أو يعطّل أو يخفّف أي اختبار، ولا يستخدم skip/xfail. لا نستورد `app` من `conftest.py`
كي تبقى fixture غير مؤثرة في ترتيب استيراد التطبيق أو تهيئة بيئة الاختبارات الحالية.
"""

import sys

import pytest

# وحدات الخدمات التي ثبت تلوّثها في الاختبارات (نُعيدها لحالتها بعد كل اختبار).
_SERVICE_MODULES = (
    "services.screener",
    "services.news_client",
    "services.fmp_client",
    "services.finnhub_client",
    "services.edgar_client",
    "services.analysis",
    "services.indicators",
    "services.scoring",
    "services.radar",
    "services.tracking",
    "services.confidence",
)


@pytest.fixture(autouse=True)
def _restore_shared_modules():
    mods = [m for m in (sys.modules.get(n) for n in _SERVICE_MODULES) if m is not None]
    snapshots = {m: dict(m.__dict__) for m in mods}
    try:
        yield
    finally:
        for m, snap in snapshots.items():
            d = m.__dict__
            for k, v in snap.items():
                d[k] = v
            for k in [k for k in d if k not in snap]:
                del d[k]
