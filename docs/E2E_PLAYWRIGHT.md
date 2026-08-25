# اختبارات E2E الرسمية لواجهة ثقة البيانات (Playwright)

اختبارات متصفح حقيقي تكمّل `tests/test_phase6_ui_wiring.py` (التي تغطّي منطق DOM/CSS/JS
على مستوى النص). تعمل هنا فقط ما **يحتاج متصفحاً**: تفاعل التلميح (hover/tap + منع الانتقال +
مطابقة نص `data-tip`)، إظهار/إخفاء `score` بالـcomputed style، الثيمين المحسوبين، اتجاه LTR
المحسوب، عدم overflow أفقي للصفحة كاملة، والتباين المحسوب للأزواج المقفلة (warning + critical).

## التشغيل

### 1) المسار الرسمي (مرجع الإغلاق النهائي) — متصفح Playwright المطابق
بيئة غير محجوبة (CI أو محلي يصل `cdn.playwright.dev`):
```bash
python -m pip install -r requirements.txt -r requirements-e2e.txt
python -m playwright install --with-deps chromium   # Chromium المطابق لـPlaywright 1.62.0
bash scripts/run_e2e.sh
```

### 2) مسار البيئة المحجوبة (اكتشاف محلي فقط — ليس مرجع الإغلاق)
عند حجب `cdn.playwright.dev`، يجوز محلياً استخدام Chromium مثبَّت مسبقاً عبر متغير اختياري:
```bash
export E2E_CHROMIUM_EXECUTABLE=<مسار Chromium المحلي>
bash scripts/run_e2e.sh
```
> ⚠️ **تحذير:** المتصفح المحلي (مثل Chromium 141) **غير مطابق رسمياً** لـPlaywright 1.62.0
> (الذي يشحن Chromium 151)، وهو **fallback للاكتشاف المحلي فقط وليس مرجعاً للإغلاق**. عند غياب
> المتغير يُستخدم المتصفح الافتراضي المطابق، ولا يوجد fallback صامت.

## الثيم
لا يوجد في المشروع **زر ولا تهيئة runtime** للثيم الفاتح ولا مفتاح localStorage؛ `html.light`
مجرد **متغير CSS كامن** في `static/style.css`. لذا يُفرَض `html.light` داخل الاختبار (عبر
`add_init_script` + تطبيق بعد التحميل) للتحقق من الوضع الفاتح — ليس إثباتاً لوجود آلية تبديل
للمستخدم.

## overflow — المقياس الرسمي (ثماني حالات مستقلة)
الإشارة الموثوقة = **أقصى تجاوز لأي عنصر مرئي خارج «إطار العرض المرئي» المستقر**
`[visualViewport.offsetLeft, +visualViewport.width]` على الصفحة كاملة، بثماني حالات pytest
**مستقلة** (صفحتان `/` و`/stock/AAPL` × حجمان desktop/mobile × ثيمان dark/light) عبر
`parametrize("path", …)` — فشل الرئيسية لا يمنع فحص صفحة السهم. المعيار: `maxOverflow <= 1`
(الكاشف الأساسي) مع `scrollRange <= 1` (إشارة مؤكِّدة ثانوية). بلا `skip/xfail` وبلا مقارنة
بـ`innerWidth` وبلا `overflow-x:hidden`.

بالإضافة إلى حواف أبناء `body`، يُقاس **اتساع `html`/`body` نفسيهما بالعرض فقط**
(`rootWidth - viewWidth`, `bodyWidth - viewWidth`) دون مقارنة حوافهما `left/right` المُزاحة
بالـgutter في RTL — فيُكتشف اتساع صريح للجذر/الجسم حتى لو لم يتجاوز أيّ عنصر ابن الإطار.
وقياس `scrollRange` محميّ بـ`try/finally` يستعيد `scrollLeft` الأصلي (على RTL = `-13`) حتى مع
استثناء، ويُعاد `initialScrollLeft` ضمن القيم التشخيصية.

> ⚠️ **لماذا تغيّر المقياس (توثيق خطأ في المقياس القديم):** كان المقياس السابق
> `scrollWidth <= clientWidth + 1`. فشل في CI بالمتصفح المطابق **Chromium 151.0.7922.34**
> (الجولة **32768932821**) على الرئيسية-الجوال فقط: `scrollWidth=404 > clientWidth=390`،
> `innerWidth=404`، **مدى التمرير الأفقي = 0**، و**لا عنصر مرئي يتجاوز إطار العرض فعلاً**.
> التشخيص المُثبَت تجريبياً (على 141 المطابق سلوكياً لـ151 في هذه النقطة): تحت محاكاة الجوال في
> RTL يُزيح شريط التمرير الرأسي مستطيل `documentElement` بـ~13px، ويبقى `scrollWidth=innerWidth=404`
> **مجمَّداً لا يتغيّر حتى عند حقن تجاوز حقيقي**، ويبقى `scrollRange=0`. ⇒ إشارات
> `scrollWidth`/`effectiveOverflow`/`scrollRange` **عاجزة تحت المحاكاة**: لا تميّز الأساس النظيف عن
> تجاوز حقيقي. لذا اعتُمد قياس حواف العناصر مقابل إطار `visualViewport` (لا مستطيل `documentElement`
> المُزاح)، فيعطي **الأساس = 0 في كل الحالات الثماني** ويكشف التجاوز المحقون. **هذا تصحيح للمقياس
> فقط داخل ملف الاختبار — بلا أي تعديل CSS/قالب/إنتاج**، ومعيار «خلو الصفحة من overflow» لم يُخفَّف.

### انحدار الكشف (حقن تجاوز حقيقي)
- **حواف العناصر** — `test_overflow_metric_detects_injected` (`extra=8`, `extra=50`): يحقن عنصراً
  بعرض `innerWidth+extra`، يؤكّد `maxOverflow >= extra-2`، يُزيله في `finally` مع استعادة
  `scrollLeft` الأصلي، ثم يتحقّق من عودة `maxOverflow<=1` و`scrollLeft` للأصل ضمن 1px.
- **اتساع الجذر/الجسم** — `test_overflow_metric_detects_wide_root` (`tgt∈{html,body}` × `extra∈{8,50}`):
  يضبط عرضاً صريحاً على `html`/`body` أكبر من الإطار بـextra، يؤكّد كشفه عبر مسار
  `rootWidth/bodyWidth`، ثم يستعيد العرض و`scrollLeft` ويتحقّق من النظافة والعودة.

يثبت هذا أن المقياس يكشف تجاوزاً حقيقياً **أصغر من الـgutter (8px)** و**أكبر منه (50px)** —
سواء عبر عنصر ابن أو عبر اتساع الجذر/الجسم — فلا يُخفيه، ولا يترك أثراً على موضع التمرير.

## العزل والأمان (fail-closed)
- خادم اختبار في **عملية مستقلة** ببيئة **allowlist** (لا نسخة كاملة من `os.environ`).
- `APP_PASSWORD=""` + قاعدة **SQLite** داخل مجلد مؤقت **تملكه العملية الأم** (`TemporaryDirectory`)،
  يُمرَّر مسارها عبر `E2E_DB_PATH`، ويُنظَّف تلقائياً (لا مخلفات).
- تعطيل `dotenv.load_dotenv` قبل استيراد `app` (المتغير `PYTHON_DOTENV_DISABLED` غير فعّال في
  dotenv 1.0.1، أُثبت تجريبياً؛ واختبار `test_env_isolation_no_dotenv_leak` يثبت عدم التسريب).
- تعطيل المجدول الخلفي.
- عزل العملاء الخارجيين **بالاسم الحقيقي**: `fmp/news/finnhub/edgar` + `telegram_client.send_message`
  (دالة إرسال تلغرام الفعلية). ومفاتيح الخدمات غير مُمرَّرة للبيئة أصلاً.
- **حارس socket** في الخادم يمنع أي اتصال/DNS غير loopback (`connect`/`connect_ex`/
  `create_connection`/`getaddrinfo`)، **يُركّب قبل استيراد وحدات الخدمات و`app`**، وتحقّقه عبر
  `is_loopback_host` (تحليل حقيقي بـ`ipaddress.ip_address(...).is_loopback`، لا prefix نصي):
  يقبل `127.0.0.0/8`/`::1`/`localhost` ويرفض `0.0.0.0` و`127.evil.com` والمضيف الخارجي والقيمة
  غير الصالحة (بلا استثناء).
- **حارس شبكة المتصفح** (autouse على context الافتراضي للـplugin في كل اختبار) بعقد صارم
  عبر `is_allowed_url` (تحليل `urlsplit`: `http` + `hostname==127.0.0.1` + المنفذ الفعلي، **fail-closed**
  على المنفذ/URL malformed ⇒ `False` بلا رمي)؛ يرفض المضيف الخارجي/المشابه نصياً/حيلة userinfo/
  المنفذ المختلف/`localhost`/بروتوكولاً مختلفاً — ثم `abort` ويُجمَع في `violations` ويُفحَص في
  finalizer (لا اعتماد على استثناء داخل الـcallback). كل الاختبارات تستخدم context الافتراضي
  للـplugin (لا `browser.new_context()` خام).

## artifacts
- لا لقطات/فيديو/traces ثنائية ملتزمة (`e2e/_artifacts/` متجاهَل بـgit).
- عبر أعلام Playwright: `--tracing=retain-on-failure --screenshot=only-on-failure --video=off
  --output=e2e/_artifacts` (عند الفشل فقط).
- **خطة المرحلة ب:** رفع artifacts في CI عند الفشل فقط باحتفاظ 7 أيام (غير مُفعَّل حالياً).

## معيار الإغلاق
1. استمرار `330 passed` (`pytest -q`، محصور في `tests/` عبر `pytest.ini`). 2. `smoke_test` = 35 صفحة.
3. مصفوفة E2E كاملة. 4. البوابة الكاملة الموحّدة. 5. **نجاح CI بالمتصفح المطابق 151** (مرجع
الحسم النهائي — لا يُعلَن البند 8 مغلقاً قبله). 6. صفر شبكة خارجية/أسرار. 7. بلا تغيير
إنتاج/عتبات/migrations؛ عدم لمس `/changes`. 8. بلا تبعيات في `requirements.txt` ولا artifacts ملتزمة.

## فجوة تباين `.dc-crit-tag` العادية — **مُعالَجة**
الوسم العادي `.dc-crit-tag` (خارج `dc-below-half`، 10px) كان تباينه الداكن ≈ 4.43:1 < 4.5.
أُصلح بتغيير لونه على `.dc-crit-tag` فقط (لا `--muted` العام، ولا الحجم/الوزن/الحواف):
`color: #9aa6bd` للوضع الداكن، و`html.light .dc-crit-tag { color: var(--muted) }` للفاتح.
يبقى `.dc-factor.dc-below-half .dc-crit-tag` (critical مقفل، أعلى تخصصاً) نافذاً للعامل الجوهري
المنخفض. النسب بعد التنفيذ: **الداكن 5.38:1 · الفاتح 4.82:1** (كلاهما ≥AA). واختبار E2E يفرضها في
الوضعين. (تطلّب هذا تحديث cache key في `base.html` إلى `?v=20260824a` واختباري cache key.)

## المصفوفة — 28 اختباراً (محلياً على 141: 28 ✓)
`e2e/test_confidence_ui_e2e.py`:
- شارة: hover-يُظهر-ويطابق-data-tip/mouseleave-يُخفي · tap-toggle-يطابق-النص+منع الانتقال(جوال).
- score: مخفي(جوال)/ظاهر(مكتب) عبر `.conf-badge .conf-score` computed.
- لوحة السهم: الموضع+7 عوامل+section/aria+غياب reason_code/schema_version · missing+caps+عامل
  جوهري تحت النصف(MSFT) · unavailable بلا عوامل(NVDA) · تاريخ `.dc-asof time` LTR محسوب.
- overflow×8 (الصفحة كاملة، حالات مستقلة: /+/stock × desktop/mobile × dark/light) +
  انحدار الكشف×6 (حواف العناصر: 8px،50px · اتساع الجذر/الجسم: html/body × 8px،50px).
- تباين×2 (dark/light): warning (`.dc-missing-title`,`.dc-cap-head`) + critical
  (`.dc-below-half .dc-factor-label`,`.dc-below-half .dc-crit-tag`) + العادي
  (`.dc-factor:not(.dc-below-half) .dc-crit-tag`) بتركيب alpha ثم WCAG≥AA.
- منع الشبكة صريح · انحدار `is_allowed_url` (قبول محلي + رفض روابط عدائية + malformed) ·
  انحدار `is_loopback_host` · عزل `.env`.

> **المرحلة أ والبند 8 لم يُغلقا** — مرجع الحسم النهائي نجاح CI بالمتصفح المطابق 151.
