# حالة العمل — PHASE 6 / F2: Data Confidence UI Wiring

> ملف تسليم لحفظ الحالة بين الجلسات. آخر تحديث: نهاية جلسة STEP 2 (بانتظار مراجعة Codex).

## البوصلة السريعة (اقرأ هذا أولاً)
- **STEP 1 (Backend Wiring): مكتمل ومدموج محليًّا** في commit `c204b60` (اعتمده Codex + أذن المستخدم).
- **STEP 2 (Card Badge + CSS + Cache Key + Tooltip): مكتمل تقنيًّا، بانتظار قرار Codex النهائي.**
  تعديلاته **غير مُلتزمة عمدًا** (إذن Codex/المستخدم = NO COMMIT حتى APPROVED). محفوظة كرقعة تُعاد تطبيقها.
- **STEP 3 (لوحة صفحة السهم stock.html): لم يبدأ.**

## آلية العمل الحاكمة (مهمة جدًّا)
العمل يجري بمراجعة خارجية صارمة من **Codex** + إذن صريح من **المستخدم (أحمد)** لكل خطوة:
1. المستخدم يأذن بتنفيذ خطوة محليًّا (NO COMMIT).
2. أنفّذ + أشغّل الاختبارات + أُنشئ **رقعة مراجعة** (`.patch`) و**bundle** وألصق النتائج، وأرفق الملفات الفعلية.
3. Codex يطبّق الرقعة على الـbundle ويراجع مستقلًّا → `APPROVED FOR LOCAL COMMIT` أو `CHANGES REQUIRED`.
4. عند APPROVED، **إذن مستخدم مستقل** لإنشاء commit محلي واحد.
- **لا push/PR/merge/deploy/amend/migration/delete** إلا بإذن صريح جديد.
- كل دمج في `main` يَنشر تلقائيًّا على الموقع الحيّ خلال دقيقتين ⇒ `python tests/smoke_test.py` إلزامي قبله.
- **الجسر إلى Codex عبر المستخدم:** أرفق الملفات عبر أداة الإرفاق، والمستخدم ينزّلها ويرفعها إلى Codex يدويًّا.

## حالة git الحالية
- الفرع: `claude/american-platform-file-19r593`
- HEAD: `c204b6095516fd3273b3e2f35074698710dd5773` (STEP 1).
- سلسلة التاريخ: `6ff24aa` (merge F2 core في main) → `49a098b` (أول ملف تسليم) → `c204b60` (STEP 1) → (هذا التحديث لملف التسليم).
- **تعديلات STEP 2 غير المُلتزمة** على 4 ملفات: `static/style.css`, `templates/_scard.html`, `templates/base.html`, `tests/test_phase6_ui_wiring.py`.
  محفوظة بالكامل في `phase6_f2_step2_review.patch` (تُطبَّق نظيفة على `c204b60`).

## كيف تستأنف STEP 2 عند العودة
```bash
cd /home/user/stock-analysis-platform
git checkout claude/american-platform-file-19r593      # HEAD يجب أن يكون c204b60 أو أحدث
git apply phase6_f2_step2_review.patch                 # يعيد تعديلات STEP 2 الأربعة إلى شجرة العمل
python tests/test_phase6_ui_wiring.py                  # يجب: 77 نجح · 0 فشل
```
ثم انتظر قرار Codex الأخير على STEP 2 (كان بانتظار الرد على إصلاح فصل hover/اللمس).

---

## STEP 1 — Backend Wiring (مكتمل، commit c204b60)
- `app.py`: مُساعد module-level `_confidence_view_map(tickers)` — dedup، فارغة⇒{} بلا استعلام، استعلام واحد عبر
  `tracking.latest_confidence_map`، **fallback مركزي `present_confidence(None)`** ⇒ خريطة كثيفة (view-model جاهز، بلا None).
- المسارات: `index()` تمرّر اتحاد `results+ready+breakouts` · `gems()/leaders()` تمرّر `results` · `stock_report()` تطلب
  `[report.ticker]` بعد تأكّد report ليست None (report=None ⇒ صفر استعلام).
- اختبار: `tests/test_phase6_ui_wiring.py` (STEP 1 = اختبارات 1–10).

## STEP 2 — Card Badge + CSS + Cache Key + Tooltip (بانتظار Codex)
- `templates/_scard.html`: شارة `conf-badge` داخل `scard-top` **بعد state-pill وقبل scard-ticker**؛ تقرأ view-model من
  `confidence_map` فقط (band_label/band_class/score_text/explanation)؛ لا فكّ JSON/تصنيف في القالب؛ آمنة عند غياب السياق.
  `aria-label` يتضمّن band_label + score_text + explanation. `data-tip` = explanation.
- `static/style.css`: `.conf-badge` + `.conf-high/medium/low/na` بخلفيات صلبة مقفلة (تباين AA مقيس):
  high `#a7c0e8`/`#2b3f79` (border `#7c6fe6`) · medium `#f5c451`/`#314468` · low & na `#cdd5e4`/`#2b4371`.
  الدرجة تُخفى بصريًّا على الجوال (`@media max-width:560px`). أسماء `conf-*` فقط؛ `.confidence` القائم لم يُمَسّ.
- `templates/base.html`: (1) cache key `?v=20260816b → ?v=20260821a`. (2) **إصلاح التلميح المشترك**: استبدال
  `mouseenter/mouseleave` بـ`pointerenter/pointerleave` مقيّدة بـ`e.pointerType === 'mouse'` — يمنع hover الاصطناعي
  من اللمس، فيبقى `click` وحده يبدّل على اللمس (أول لمسة تُظهر وتُبقي، الثانية تُخفي)، مع `preventDefault+stopPropagation`
  لمنع انتقال رابط البطاقة. أُضيف `.conf-badge` لمحدد النظام المشترك `.nav-eye, .help, .help-q, .conf-badge`.
- اختبار: STEP 2 = اختبارات 11–17 (منها test_17 يقفل فصل hover الفأرة عن اللمس).

### آخر قرار Codex على STEP 2
`CHANGES REQUIRED` (جولتان): (1) ربط conf-badge بنظام التلميح + aria يشمل explanation → عولج. (2) أول لمسة حقيقية
يجب أن تُظهر التلميح وتُبقيه (كان ينتهي مخفيًا بسبب mouseenter الاصطناعي) → **عولج بـpointer-events gating**.
النسخة المُرسلة الأخيرة إلى Codex (بانتظار قراره):
- `phase6_f2_step2_review.patch` — 16,384 bytes — SHA-256 `a42773730cae4a5feb7e36b221f0b3b67c90fb6d76e438414cdecbd4e3482107`
- `phase6_f2_step2_desktop.png` — 97,565 bytes — SHA-256 `20665535a929d4f15ab97cf167a19730c69d74ed142b4432be64355c1b5120e2`
- `phase6_f2_step2_mobile.png` — 89,352 bytes — SHA-256 `e949b79351835b7d8e294f3cc30d316940a6ced9af9dadce6b8f046f5336ebe5`

## نتائج الاختبارات (آخر تشغيل STEP 2)
- `python tests/test_phase6_ui_wiring.py` → **77 نجح · 0 فشل**.
- `pytest tests/test_phase6_ui_wiring.py -q` → 17 passed. مجموعة F2 كاملة + الملف → 158 passed. smoke_test → 35 صفحة ✓.
- `py_compile` + jinja parse(_scard, base) → OK. `git diff --check` → نظيف.
- `pytest tests/` كامل → **26 failed, 291 passed**. الـ26 كلها **legacy pre-existing** (5 ملفات: persistence/audit_phase2/
  audit_phase4/phase5_baseline/audit_high_v2) — **كلٌّ ينجح منفردًا**، والتصادم فقط عند تشغيل المجلد كله بعملية pytest واحدة
  (اختبارات قديمة تعدّل os.environ/globals عند الاستيراد). **تعديلاتي تضيف 0 إخفاق** (مؤكَّد بمقارنة baseline نظيف).

## فحص بصري آلي (Playwright — سكربتات في scratchpad، ليست في المستودع)
- `scratchpad/step2_visual_check.py`: overflow + إخفاء/إظهار score (desktop/mobile).
- `scratchpad/step2_tooltip_check.py`: يستخرج سكربت التلميح الفعلي من base.html ويختبر تفاعليًّا:
  desktop hover ⇒ يظهر/mouseleave ⇒ يختفي · **أول mobile tap حقيقي واحد ⇒ يظهر ويبقى + URL ثابت + tap ثانٍ يُخفي**.
- ملاحظة: `playwright` مثبّتة **في البيئة المؤقتة فقط** — ليست في requirements/المستودع. أعد `pip install playwright` عند الحاجة
  (المتصفح في `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`).

## قيود STEP 2 (لا تتجاوزها)
ملفات مسموحة: `_scard.html`, `style.css`, `base.html`, `tests/test_phase6_ui_wiring.py` فقط.
ممنوع لمس: `app.py`, `stock.html`, `changes.html`, `index/gems/leaders.html`, `confidence*.py`, `tracking.py`, `models.py`.

## STEP 3 (لاحقًا، لم يبدأ) — لوحة صفحة السهم
لوحة «ثقة البيانات» منفصلة في `stock.html` بعد `score-cards` (المرساة ~سطر 139): band_label/score_text/as_of/explanation +
`<details>` للعوامل السبعة (points/max/pct + critical_below_half) + missing + caps_applied. عند unavailable: بلا قائمة عوامل،
بلا reason_code. المتغيّر `confidence` يصل stock.html أصلًا من STEP 1 (مُمرَّر في stock_report).

## أسئلة/قرارات سابقة محسومة
- لون high = `#a7c0e8` نصًّا (`#7c6fe6` حدّ فقط) — مقفل من Codex.
- `/changes` مؤجّلة بالكامل خارج هذا العمل.

## أول خطوة عند العودة
اقرأ هذا الملف → `git apply phase6_f2_step2_review.patch` → تحقّق 77/0 → انتظر قرار Codex على STEP 2 → عند APPROVED +
إذن مستخدم صريح، أنشئ commit STEP 2 واحدًا بالرسالة `feat(phase6): add data confidence badge to stock card`.
