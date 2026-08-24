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
`document.scrollingElement.scrollWidth <= clientWidth + 1` على الصفحة كاملة، بثماني حالات
pytest **مستقلة** (صفحتان `/` و`/stock/AAPL` × حجمان desktop/mobile × ثيمان dark/light) عبر
`parametrize("path", …)` — فشل الرئيسية لا يمنع فحص صفحة السهم. بلا `skip/xfail` وبلا مقارنة
بـ`innerWidth` وبلا تضييق القياس.
> ⚠️ **محلياً على Chromium 141** يفشل فحص الرئيسية على الجوال فقط (`scrollWidth=404 > clientWidth=390`)؛
> الدليل يرجّح أنه أثر عرض شريط التمرير الرأسي في RTL تحت محاكاة الجوال على المتصفح غير المطابق
> (لا child/pseudo يتجاوز، والصفحة لا تتمرّر أفقياً). **الحسم بمرجع Chromium 151 في CI** (المرحلة ب):
> إن اختفى ⇒ يمرّ بلا تغيير. **وإن استمرّ على 151، فالرأي الفني الموصى به هو إصلاح التخطيط مستقلاً
> قبل إغلاق البند 8** (تخفيف معيار «خلو الصفحة من overflow» ليس خياراً فنياً مساوياً). لا يُصلَح
> CSS/إنتاج بناءً على نتيجة 141.

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

## المصفوفة — 22 اختباراً (محلياً على 141: 20 ✓ / 2 ✗ = الرئيسية-جوال overflow فقط)
`e2e/test_confidence_ui_e2e.py`:
- شارة: hover-يُظهر-ويطابق-data-tip/mouseleave-يُخفي · tap-toggle-يطابق-النص+منع الانتقال(جوال).
- score: مخفي(جوال)/ظاهر(مكتب) عبر `.conf-badge .conf-score` computed.
- لوحة السهم: الموضع+7 عوامل+section/aria+غياب reason_code/schema_version · missing+caps+عامل
  جوهري تحت النصف(MSFT) · unavailable بلا عوامل(NVDA) · تاريخ `.dc-asof time` LTR محسوب.
- overflow×8 (الصفحة كاملة، حالات مستقلة: /+/stock × desktop/mobile × dark/light).
- تباين×2 (dark/light): warning (`.dc-missing-title`,`.dc-cap-head`) + critical
  (`.dc-below-half .dc-factor-label`,`.dc-below-half .dc-crit-tag`) + العادي
  (`.dc-factor:not(.dc-below-half) .dc-crit-tag`) بتركيب alpha ثم WCAG≥AA.
- منع الشبكة صريح · انحدار `is_allowed_url` (قبول محلي + رفض روابط عدائية + malformed) ·
  انحدار `is_loopback_host` · عزل `.env`.

> **المرحلة أ والبند 8 لم يُغلقا** — مرجع الحسم النهائي نجاح CI بالمتصفح المطابق 151.
