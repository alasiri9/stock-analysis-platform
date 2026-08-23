"""اختبار انحدار مباشر لعزل وحدات الخدمات (fixture في tests/conftest.py).

يثبت أن الـfixture التلقائية `_restore_shared_modules` تعيد حالة وحدة خدمة مشتركة إلى
ما كانت عليه بين اختبارين متتاليين، بعد ثلاثة أنواع من التلويث داخل الاختبار الأول:
  (1) استبدال دالة موجودة بأخرى (sentinel).
  (2) إضافة اسم مؤقت جديد لم يكن موجوداً.
  (3) حذف اسم موجود.

الاختبار الثاني (يلي الأول في نفس الملف) يتحقّق أن الثلاثة عادت إلى أصلها — وهو ما
لا يتحقّق إلا إذا كانت الـfixture فعّالة. لو أُزيلت الـfixture أو عُطّلت، يبقى تلوّث
الاختبار الأول نافذاً فيفشل الاختبار الثاني. لا skip/xfail، ولا تنظيف يدوي يخفي عمل
الـfixture (لا نُعيد الحالة بأنفسنا داخل الاختبار الأول).

الأصول تُلتقط مرة واحدة عند استيراد الملف (قبل أي تلويث)، فتكون مرجعاً موثوقاً.
"""

import services.scoring as scoring

# أصول موثوقة تُلتقط عند الاستيراد (قبل أي تلويث داخل الاختبارات).
_ORIG_PIOTROSKI = scoring.piotroski_score
_ORIG_CATALYST = scoring.catalyst_score

_ADDED_NAME = "_iso_regression_temp_marker"
_SENTINEL = object()


def _sentinel_piotroski(fin):  # بديل مميّز يسهل كشفه
    return _SENTINEL


def test_a_pollutes_shared_module():
    # حالة البداية نظيفة (الـfixture عزلت أي تلوّث سابق):
    assert scoring.piotroski_score is _ORIG_PIOTROSKI
    assert scoring.catalyst_score is _ORIG_CATALYST
    assert not hasattr(scoring, _ADDED_NAME)

    # (1) استبدال دالة موجودة.
    scoring.piotroski_score = _sentinel_piotroski
    # (2) إضافة اسم مؤقت جديد.
    setattr(scoring, _ADDED_NAME, "polluted")
    # (3) حذف اسم موجود.
    del scoring.catalyst_score

    # تأكيد أن التلويث نافذ فعلاً داخل هذا الاختبار (لا نستعيده يدوياً).
    assert scoring.piotroski_score is _sentinel_piotroski
    assert getattr(scoring, _ADDED_NAME) == "polluted"
    assert not hasattr(scoring, "catalyst_score")


def test_b_module_restored_by_fixture():
    # يجب أن تكون الـfixture قد استعادت كل شيء بعد الاختبار الأول:
    # (1) الدالة المستبدَلة عادت لأصلها.
    assert scoring.piotroski_score is _ORIG_PIOTROSKI
    # (2) الاسم المُضاف حُذف.
    assert not hasattr(scoring, _ADDED_NAME)
    # (3) الاسم المحذوف عاد لأصله.
    assert hasattr(scoring, "catalyst_score")
    assert scoring.catalyst_score is _ORIG_CATALYST
