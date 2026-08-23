# حالة العمل — PHASE 6 / F2: Data Confidence UI Wiring

> **مكتملة عبر STEP 1–3**، ومعتمدة، **ومدموجة في `main` ومنشورة على الموقع الحيّ** (PR #3 · merge commit `f5fa8cb` · نشر Railway `success` · تحقّق حيّ ناجح).

## الخلاصة
ربط **درجة ثقة البيانات (Data Confidence)** بالواجهة: شارة على بطاقات المسح، ولوحة تفصيلية في
صفحة السهم — بعرض **الـview-model المخزّن فقط** (لا إعادة حساب، لا حفظ، لا API، لا كتابة).

## الفروع والالتزامات
- **الخط الأساسي لـ`main`:** `6ff24aa819dfd91b1a700e8aa467ef883dbc8523` (نواة F2 المدموجة عبر PR #2).
- **الفرع النظيف للعمل:** `claude/american-platform-file-19r593-clean`
- **الـcommits المكتملة (بالترتيب):**
  - `c204b6095516fd3273b3e2f35074698710dd5773` — STEP 1: Backend Wiring (ربط الخريطة بالمسارات).
  - `f53bd584f6254f7cc51ae91231228f6055c8fb83` — STEP 2: Card Badges + CSS + Tooltip.
  - `ff26a2234d9745fbfc8700278fedc09e47616ea1` — STEP 3: Stock Detail Confidence Panel.

## العقود النهائية (Backend → Template)
- الرئيسية `/` تمرّر **اتحاد رموز `results` + `ready` + `breakouts`** (بطاقات `_scard` الظاهرة فعلاً).
- `/gems` و`/leaders` تمرّران **رموز `results` فقط**.
- صفحة السهم `/stock/<t>` تطلب **`[report["ticker"]]`** بعد التأكّد أن `report` ليست None
  (مسار `report=None` يعود قبلها ⇒ صفر استعلام ثقة).
- **ممنوع** استدعاء `latest_confidence_map()` بلا وسيط، و**ممنوع** جلب كامل UNIVERSE.
- المُساعد `app._confidence_view_map(tickers)`: خريطة كثيفة — أي رمز بلا لقطة يُملأ مركزياً
  بـ**`present_confidence(None)`** (fallback موحّد)، فيصل القالب view-model جاهزاً دائماً.
- **استعلام واحد كحد أقصى** عند وجود رموز، و**صفر** للقائمة الفارغة. **لا N+1**، ولا كتابة،
  ولا إعادة حساب، ولا API/live-price في مسار العرض.
- **القوالب تقرأ view-model فقط** — لا فكّ JSON ولا تصنيف missing/corrupt ولا استدعاء
  `present_confidence` داخل القالب.
- **`/changes` و`templates/changes.html` مؤجّلان بالكامل ولم يُلمسا.**

## الواجهة النهائية
- **شارة الثقة** في بطاقات المسح (`_scard.html`): داخل `scard-top` بعد `state-pill`، تعرض
  band_label + score_text (تُخفى الدرجة على الجوال، وتبقى في `aria-label`).
- **لوحة ثقة البيانات** في `stock.html`: `<section aria-labelledby="dc-title">` بعد `score-cards`
  وقبل `tmeter-wrap`، تعرض band + score + explanation + **as_of** (`<time dir="ltr">` يبقى YYYY-MM-DD)
  + **العوامل السبعة** (points/max/pct + progress + تمييز `critical_below_half`) + **missing** + **caps_applied**.
- **الألوان (مقفلة، تباين AA مقيس):** high أزرق `#a7c0e8`/`#2b3f79` (border `#7c6fe6`) — ليس أخضر ·
  medium `#f5c451`/`#314468` · low & unavailable `#cdd5e4`/`#2b4371`.
  متغيّرات اللوحة: critical داكن `#ff8a8a` / فاتح `#b91c1c` · warning داكن `#f5c451` / فاتح `#92400e`.
- **tooltip يعمل للفأرة واللمس** عبر النظام المشترك (`pointerenter/pointerleave` مقيّدة بـ`pointerType==='mouse'`
  + `click` toggle مع `preventDefault`/`stopPropagation` لمنع انتقال رابط البطاقة).
- **cache key النهائي:** `?v=20260822a` في `base.html`.
- **لا يُعرض `reason_code` ولا `schema_version`** للمستخدم في أي حالة.

## النتائج النهائية (آخر تشغيل)
- `python tests/test_phase6_ui_wiring.py` → **139 نجح / 0 فشل**.
- `pytest tests/test_phase6_ui_wiring.py -q` → **28 passed**.
- مجموعة F2 (confidence + persistence + confidence_view + confidence_read + ui_wiring) → **169 passed**.
- `python tests/smoke_test.py` → **35 صفحة سليمة ✓**.
- `pytest -q` (المجموعة الكاملة) → **26 failed / 302 passed** — والـ26 هي **baseline legacy مسجّلة**
  (ملفات قديمة: persistence/audit_phase2/audit_phase4/phase5_baseline/audit_high_v2 تتصادم فقط عند
  تشغيل المجلد كله في عملية واحدة؛ كلٌّ ينجح منفرداً؛ **0 إخفاق مُضاف** من هذا العمل).
- Jinja parse + `py_compile` + `git diff --check` → سليمة.

## الملفات المعدّلة في STEP 1–3
`app.py` · `templates/_scard.html` · `templates/stock.html` · `static/style.css` · `templates/base.html`
· `tests/test_phase6_ui_wiring.py` (جديد). **مجمّدة/لم تُمَسّ:** `services/confidence*.py` · `services/tracking.py`
· `models.py` · `templates/changes.html` · `index/gems/leaders.html`.

## الحالة والخطوة القادمة
- **STEP 1–3 مكتملة ومعتمدة ومدموجة ومنشورة.** دُمجت PR #3 في `main` عبر merge commit عادي
  خاص بـF2: `f5fa8cbd76f4531f3801fcccc6feb3684dca3ce4` (أبواه: `6ff24aa` main القديم + `6a1a986`
  رأس الفرع النظيف) — كان HEAD لـ`main` مباشرة بعد دمج F2.
- **النشر التلقائي على Railway نجح** (`blissful-imagination – web = success`). البوابة الإلزامية
  `smoke_test` كانت خضراء قبل الدمج (35 صفحة ✓).
- **تحقّق الموقع الحيّ ناجح:** أثبت عمل الصفحة الرئيسية، وظهور شارة الثقة، وظهور لوحة «ثقة البيانات»
  بعناصرها الأساسية بلا كسر أو overflow ظاهر. أمّا العوامل السبعة وحالات missing/caps والتفاعلات
  التفصيلية فثبتت باختبارات F2 وفحص Playwright قبل الدمج.
- **الإجراء التالي (وفق التسلسل المعتمد، قبل `/changes` أو أي ميزة جديدة):**
  1. إصلاح عزل الإخفاقات الـ26 legacy في **فرع مستقل**.
  2. ثم تحويل فحوص Playwright إلى **اختبارات رسمية قابلة للتكرار** (عمل مستقل).
