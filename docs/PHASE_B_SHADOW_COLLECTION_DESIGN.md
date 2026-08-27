# تصميم المرحلة ب — الجمع الظلّي لتدقيق «درجة الثقة بالبيانات»

> **الحالة:** `APPROVED FOR DESIGN FREEZE`
> **الأساس:** `main @ de271d710a6b5531333b6e10d7b723beefaa1b9c`
> **المانع الوحيد المتبقّي:** `ب-0أ` — إثبات FMP الكتابي.
> **حدود الاعتماد:** هذا اعتماد **تصميمي فقط** — لا يجيز أي تنفيذ أو Pilot أو جمع/تخزين
> حمولة خام، ولا إنشاء فرع/كود/`create_all`/migration.
>
> هذا الملف هو **المرجع التصميمي المُجمَّد الوحيد** للمرحلة ب. **لا يُعد مرجعاً قانونياً أو
> بديلاً عن شروط FMP أو الخطة أو Order Form أو الموافقة الكتابية؛ وهذه الوثائق الخارجية هي
> السلطة التعاقدية.** دُمجت فيه تصحيحات الإغلاق السبعة المعتمدة داخل مواضعها الأصلية (لا ملحق
> منفصل، ولا نصوص قديمة متعارضة).
>
> **التسلسل النهائي الملزم:** `ب-0أ → ب-0ب → ب-0ج → ب-1 → ب-2 → ب-3 → ب-4`.
> قبل حسم `ب-0أ`: مواصفة + خطة اختبارات + بدائل مخطط فقط — لا `create_all` ولا Pilot.

---

## 1) خريطة العوامل السبعة (من الكود الفعلي)

المزوّد الوحيد لكل العوامل = **FMP**، ويُنسَّق الجلب الحالي من `screener._build_record` عبر
**نحو 6 طلبات/سهم**. أما الجمع الظلّي المقترح فالتقاطه **من مستويين كما في §2**: نقل داخل
`_get` وlineage داخل المستهلكين. كل الحمولات الخام تُحوَّل ثم تُرمى حالياً — بلا توقيت
قبول/بثّ، بلا بصمة، بلا نسخة كود محفوظة.

| العامل (الوزن) | الحقول الخام | المزوّد/endpoint | الاستدعاء → التحويل | يُحفَظ / يُفقَد |
|---|---|---|---|---|
| catalyst_completeness (20) | netIncome, revenue, grossProfit, operatingIncome (سنتان) + totalAssets, totalStockholdersEquity | FMP income + balance | `screener.py:709` → `scoring.catalyst_score:717` | يُحفظ `catalyst`/`catalyst_complete` · يُفقَد كل الخام (`date`/`fillingDate`/`acceptedDate`/`period`) |
| piotroski_computability (20) | 9 بنود من القوائم الثلاث | FMP income + balance + cashflow | `screener.py:709` → `scoring.piotroski_score:718` | يُحفظ `piotroski`/`piotroski_computable` · يُفقَد الخام |
| technical_indicators (20) | OHLCV (~250) | FMP `historical-price-eod/full` | `screener.py:730` `get_historical_prices(limit=5000)` → `indicators.build_indicators:736` | يُحفظ `indicators` · يُفقَد مصفوفة الأسعار (يبقى إغلاق 60 يوماً في `PricePoint`) |
| structure_availability (15) | نفس الأسعار | FMP (نفس الطلب) | `indicators.market_structure:739` | يُحفظ `structure` · يُفقَد الخام |
| frames_availability (10) | كل التاريخ | FMP `full_candles` | `indicators.multi_timeframe:740` | يُحفظ `frames` · يُفقَد التاريخ الكامل |
| flow_availability (5) | OHLCV (الحجم) | FMP (نفس الطلب) | `indicators.money_flow:737` | يُحفظ `money_flow` · يُفقَد الحجم |
| freshness (10) | `analysis_date`=candles[0].date + مرجع الجلسة | FMP (السهم + SPY) | `screener.py:730` + `_refresh_spy_history` → `tracking.resolve_expected_session` (مرجع SPY لا NYSE) | يُحفظ `analysis_date`/`analysis_close` |

> ⚠️ مرجع الحداثة مشتقّ من **FMP-SPY لا NYSE** — يبقى الإنتاج عليه مجمَّداً، ويُضاف NYSE
> مرجعَ تدقيق مستقل في L3 فقط.

---

## 2) نقطة الجمع الظلّي — التقاط من مستويين

- **المستوى 1 (نقل):** hook في `fmp_client._get` يلتقط request/response envelope + جسم
  الاستجابة الأصلي؛ يعرف `endpoint`+`params` فقط، لا `factor`/`input_field`.
- **المستوى 2 (lineage):** طبقة بعد التحليل في المستهلكين (`scoring`/`indicators`/
  `_build_record`) تربط كل حقل خام مُستخدَم بـ(`factor`, `input_field`, `json_path`,
  القيمة الملتقطة, `payload_sha256`).
- ⇒ سجل `snapshot×factor×input_field` يتحقق **بالمستويين معاً**، لا بـ`_get` وحده.

**تعريف الحمولة:** المحفوظ = `resp.content` (جسم الجسم بعد فك Content-Encoding، ليس
wire-level ولا `resp.text`). يُحفظ: `resp.content` + `sha256(resp.content)` + الطول
(bytes) + Content-Type + charset المعلَن + Content-Encoding. لو لزمت بايتات wire-level ⇒
`stream=True` + `resp.raw.read()` (تغيير نقل صريح يُقرَّر مستقلاً).

**fail-open غير صامت:** كل صف مجدول ⇒ capture أو **durable failure record**. تعذّر قاعدة
الظل ⇒ failure event دائم في **sink خارجي مستقل** + تعليق أهلية الجلسة. يُمنع إعادة الجلب
للتعويض.

**حصر النطاق ومنع الأسرار:** التفعيل حصراً في **context صريح لليلي** + مفتاح المنصة
(`api_key is None`) + إثبات أن المصدر platform-managed. يُمنع BYOK وأي طلب تفاعلي. `apikey`
(في `params["apikey"]` — `fmp_client.py:207`، يظهر في `resp.url`) يُنقَّح من
URL/params/logs/fingerprint/metadata. cleanup مضمون في `finally`.
المسار المُثبَت: APScheduler `BackgroundScheduler` → worker thread واحد؛ `_auto_refresh`
→ `refresh_cache` → `_build_record` → `_get` تتابعي متزامن في نفس الthread ⇒ رمز السياق
يصل `_get`.

> **[تصحيح مدموج]** أي عبور مستقبلي لحدود thread/task يحتاج **propagation مصمَّماً
> ومختبَراً**: context object صريح، أو `ContextVar` مع `copy_context`/آلية نقل مناسبة بحسب
> نموذج التنفيذ — **لا يُفترض أن `contextvars` تنتقل تلقائياً إلى thread جديد** بلا
> propagation صريح.

---

## 3) تصميم البيانات (بدائل مخطط — لا تُنفَّذ قبل ب-0ج)

آلية إنشاء المخطط الفعلية في المستودع = **`db.create_all()`** عند الإقلاع (`app.py:561`)
تُنشئ الجداول الغائبة فقط، بلا `ALTER` بلا Alembic. جداول الظل جديدة كلياً ⇒ `create_all`
وحده يكفيها. **تُسمّى هنا ولا تُنفَّذ.** («migration» و«create_all» ليسا شيئاً واحداً.)

الكيانات المقترحة:

- **`ShadowPayloadBlob`**(`payload_sha256` PK, `content_bytes`[gzip], `byte_length`,
  `content_type`, `content_encoding`, `charset`) — البايتات مرة واحدة content-addressed.
- **`ShadowRequest`**(`collection_run_id`, `request_id`, `attempt`, `method`, `endpoint`,
  `params` منقّحة, `request_sequence`, `status`, `response_headers` المسموح بها,
  `payload_sha256`→blob, `null_reason`, `captured_at_utc`) — حدث طلب append-only مستقل.
- **`ShadowLineageField`** — نوعان:
  - **scalar:** النوع + القيمة + `json_path`.
  - **ordered series/slice:** مرجع blob + selection descriptor ثابت + ترتيب العناصر +
    حدود الشريحة + `sha256` على canonical extracted input (technical=`[:250]`،
    frames=full).
  - `null_reason` على **مستوى الحقل** أيضاً (status 200 لا يعني اكتمال كل الحقول).
- **`ShadowCaptureManifest`**(`collection_run_id`, `ticker`, planned snapshot identity =
  (`ticker`, `snap_date`), `expected_session_date`/source, `code_git_sha`,
  `audit_schema_version`, `universe_version`+`sha256`) + planned denominator
  (الأسهم × الطلبات × المدخلات المطلوبة).
- **الطوابع الأربعة:** `source_effective_at` · `source_published_at` (datetime واعٍ من
  `fillingDate`/`acceptedDate` كـ«ادعاء مزوّد» — **لا مساواة تلقائية بقبول EDGAR الرسمي**)
  · `provider_observed_at_utc` (nullable، لا يُخترع) · `captured_at_utc`.

**نوعا idempotency:** dedup الـblob بـ`payload_sha256` · dedup صفوف lineage بمفتاح ثابت
داخل الmanifest · كل `attempt` حدث append-only مستقل؛ إعادة المحاولة no-op **تبقى لها
attempt/event يسجّل رؤية البصمة** (قيد uniqueness وحده لا يحقق «no-op مسجّل»). بصمة مختلفة
لنفس السهم/الجلسة ⇒ `snapshot_version` جديد أو conflict صريح، **لا overwrite**.

**المعاملتان والمرجع:** Session ظلّ مستقلة من `db.engine` (سابقة `fmp_client._reserve_atomic:62`)
بـ commit/rollback/close خاصة — **لا FK إلى `StockSnapshot` غير المعتمد**. الدليل يُحفَظ عبر
الmanifest المستقل؛ بعد نجاح commit الإنتاج تُضاف علاقة الربط في معاملة ثالثة؛ عند rollback
الإنتاج **يبقى الدليل ويُعلَّم `orphan/ineligible`** بسبب واضح. `StockSnapshot` يُربَط
بالmanifest الجامع (اللقطة تستهلك عدة طلبات) لا بـpayload مبهم.

**التخزين والحجم (لا أرقام غير مقيسة):** Pilot حجمي قصير (بعد `ب-0أ`، غير محتسب في الدراسة)
يقيس bytes خام/مضغوطة + الفهارس + WAL + النسخ الاحتياطية. تُقارَن: Postgres · Object Storage
مشفّر · هجين. **لا خيار نهائي قبل الترخيص + القياس.**

> **[تصحيح مدموج — التصنيف الأمني]** قد لا تحتوي الحمولات المعتادة على PII أو مفتاح API بعد
> التنقيح، **لكنها بيانات مزوّد مرخّصة ومملوكة تعاقدياً** ⇒ تُعامَل كبيانات **مقيَّدة**:
> تشفير + إدارة مفاتيح + تحكّم وصول + تدقيق استخدام وفق العقد. `apikey` لا يُخزَّن إطلاقاً.

**الاستعادة:** البصمة **للتحقق لا نسخة احتياطية**. volume backup + PITR + logical/offsite
dump + **restore drill فعلي** + RPO/RTO رقميان (مقترح: RPO ≤ 24h، RTO ≤ 4h).

> **[تصحيح مدموج — الرجوع]** الرجوع (rollback) التشغيلي = **تعطيل hook النقل والربط الليلي
> مع إبقاء جداول الأدلة والبيانات محفوظة كاملة**. **لا إسقاط للجداول ولا حذف للبيانات ضمن
> rollback.** الإسقاط/الحذف إجراء مستقل يحتاج إذناً صريحاً، بعد التحقق من متطلبات FMP
> والاحتفاظ والنسخ الاحتياطية.

---

## 4) الترخيص والاستقلال

**بوابة FMP [مانع أول]:** حمولة FMP بيانات مزوّد وشروطه تقيّد الاستخدام/التخزين/الحذف/إعادة
التوزيع. **لا نقول «تمنع قطعاً» بل «لا تُثبت أن هذا الاستخدام مسموح»** ⇒ نحتاج موافقة
تعاقدية صريحة. نطاق الخطاب/Order Form المطلوب كتابياً:

- حفظ أجسام استجابات API الخام، بما فيها حمولة الأسعار التاريخية الكاملة.
- استخدام داخلي للتحليل/التدقيق/المعايرة، بلا إعادة توزيع/عرض للخام.
- 32 سهماً × (90 ثم 252) جلسة + مدة الاحتفاظ بالتقويم (لا عبارة تقريبية).
- موقع التخزين (Postgres/Object Storage) + مواقع المعالجة + مزوّدو التخزين.
- النسخ الاحتياطية + PITR + المنطقية/offsite + نسخ restore drill.
- حقوق الاحتفاظ أثناء الاشتراك، والحذف عند انتهائه، وحذف نسخ الاستعادة.
- تأكيد أن الخطة الحالية/Order Form يغطّي ذلك كله.

> **[تصحيح مدموج — المصادر المستقلة]** **FMP يبقى مصدر بيانات الإنتاج، لا مصدر تدقيق مستقل
> (لا يدقّق نفسه).** مصادر التدقيق المستقلة، كلٌّ **ضمن نطاقه فقط** (لا يدقّق أيٌّ منها
> العوامل السبعة كلها):
> - **EDGAR** (مجاني، مُدمَج `edgar_client.py`/`radar.py`): حقول القوائم المالية وتوقيتات
>   الإيداع/القبول — يغطّي مدخلات `catalyst_completeness` و`piotroski_computability`
>   وتوقيتهما (منع look-ahead) فقط.
> - **NYSE** (عام مجاني): الجلسات والتقويم — يغطّي `freshness` فقط.
> - **مزوّد تجاري مستقل محتمل** (Sharadar لأساسيات PIT، Norgate لسلسلة أسعار ثانية): خطوة
>   وعرض سعر منفصلان.
> - العوامل الفنية (`technical`/`structure`/`frames`/`flow`) تبقى **بلا مصدر تدقيق مستقل**
>   حتى التعاقد التجاري — لا يغطّيها EDGAR ولا NYSE.

> ⚠️ **قيد بيئي:** نطاقات المزوّدين التجارية محجوبة من الجلسة (`EGRESS_BLOCKED`) — لا
> يُثبَّت سعر/حق جديد بلا مصدر رسمي؛ قرار الميزانية يُطلَب خارج البيئة.

---

## 5) أثر التنفيذ + إثبات التجميد

الملفات المرشّحة: `models.py` (إضافة كيانات) · `services/shadow.py` (جديد) ·
`services/tracking.py` (استدعاء الجمع، معاملة مستقلة) · `fmp_client.py` (hook النقل) +
موضع lineage في `screener`/`scoring`/`indicators` (**ليس «سطراً واحداً»**).

**الرجوع:** كما في تصحيح الرجوع أعلاه (تعطيل تشغيلي مع حفظ الأدلة، **لا إسقاط**).

**التجميد المُثبَت:** `services/confidence*.py` (الأوزان/العتبات/السقوف/البوابة) **لا
يُلمس**؛ الظل يقرأ مخرَج `data_confidence` ويخزّنه بلا تعديل معادلته/معناه. عقود العرض/API
لا تُلمس. لا `.env`، لا عرض/اختبار أسرار.

---

## 6) الاختبارات والبوابات الرقمية (تُجمَّد قبل التفعيل)

- **صفر طلبات إضافية:** مقارنة **التوقيع القانوني الكامل** `method + endpoint +
  canonical sanitized params/body + request sequence/attempt class`، الأسرار محذوفة قبل
  البصمة. يتطابق العدد والتوقيعات مع/بدون الظل — شامل SPY وإعادات المحاولة ومسارات الفشل.
- **تغيّر `score`/`band`/`reasons`/`output` = 0 حرفياً** — على fixture + وقت + حالة DB
  مثبَّتة نفسها.
- **العزل:** commit/rollback في معاملة الظل لا يمسّ معاملة السهم (اختبار ترتيبَي النجاح
  والفشل).
- **سياق الالتقاط:** الاستثناء · nested context · عدم تسرّب الحالة لمهمة/worker لاحق.
- **منع الأسرار:** لا وجود لسلسلة `apikey` في أي صف/عمود/سجل (assert صريح).
- **التغطية:** 100% من عناصر الplanned manifest ⇒ captured أو durable failure؛
  unresolved capture failures = 0 قبل احتساب الجلسة.
- **الأداء:** حاجز p95 بتعريف baseline + حجم عيّنة + نافذة قياس (مقترح ≤ +10% أو ≤ +30ث،
  أيّهما أصغر) + حدّ تخزين/تنبيه رقمي (يُثبَّت بعد Pilot `ب-0ب`).
- **restore drill ناجح قبل تفعيل hook الإنتاجي.**

> **[تصحيح مدموج — بوابة القبول]** لا اشتراط full-green مطلق بلا إثبات baseline:
> - اختبارات المرحلة ب الجديدة والمحدَّدة = **خضراء بالكامل**.
> - smoke و Playwright المعتمدان = **خضراوان**.
> - full pytest = **لا إخفاقات جديدة** مقارنةً بخط أساس موثَّق على `main`.
> - إذا ثبت لاحقاً أن خط أساس `main` أخضر بالكامل ⇒ تتحوّل البوابة إلى **full green**.

---

## 7) تسلسل التنفيذ (كلٌّ بإذن مستقل)

| الخطوة | المحتوى |
|---|---|
| **ب-0أ** *(مانع)* | إثبات كتابي من FMP يجيز الاستخدام المحدَّد (نطاقه في §4). |
| **ب-0ب** | Pilot حجمي — **بعد ب-0أ فقط** (كونه غير محتسب لا يعفيه من الترخيص). |
| **ب-0ج** | تجميد التخزين/الاحتفاظ/حدود الحجم والتنبيه/RPO-RTO بناءً على قياس ب-0ب، **+ اختيار `durable external failure sink`** مع تحديد: نوعه ومكانه (مستقل عن قاعدة الظل والإنتاج) · مدة احتفاظه · معرّف الربط بـ`collection_run_id` · كيف يُحسب ضمن تغطية 100% (كل عنصر متوقّع ⇒ captured أو failure event في الsink) · سلوك تعطُّله المتزامن مع قاعدة الظل (الجلسة **غير مؤهَّلة حتمياً** حتى استعادة الدليل ومطابقة الmanifest). |
| **ب-1** | المخطط + آليته (`db.create_all`) + الاختبارات — **بعد تجميد ب-0ج**. |
| **ب-2** | blob store content-addressed + طبقة lineage + عزل المعاملة. |
| **ب-3** | hook إنتاجي scoped بعد إذن مستقل — `fmp_client.py` + موضع lineage، بعد restore drill. |
| **ب-4** | ربط ليلي + اختبارات البوابات الرقمية. |

**لاحقاً ومستقل:** تدقيق EDGAR/NYSE ثم المزوّد التجاري — بالتزام سياسات الوصول الرسمية.

**قبل `ب-0أ`:** مواصفة + خطة الاختبارات + بدائل المخطط فقط. لا يبدأ `ب-0ب`/`ب-0ج`/`ب-1`،
ولا يُطبَّق `create_all` على أي قاعدة.

---

## 8) جدول قرارات أحمد + التوصيات (بلا اعتماد جمع خام قبل ب-0أ)

| # | القرار | التوصية |
|---|---|---|
| 1 | **ترخيص FMP للتخزين الداخلي** *(مانع)* | احصل على الإثبات الكتابي أولاً. **حتى حسم `ب-0أ` يقتصر العمل على المواصفة وخطة الاختبارات وبدائل المخطط فقط. لا يبدأ `ب-0ب` أو `ب-0ج` أو `ب-1`، ولا يُطبَّق `create_all` على أي قاعدة.** |
| 2 | معمارية التخزين (Postgres · Object Storage مشفّر · هجين) | لا تحسم قبل Pilot القياس؛ الميل المبدئي للهجين، مشروط بالقياس والترخيص. |
| 3 | مدة الاحتفاظ | نافذة البرنامج + هامش holdout، وحذف بإذن مستقل — بعد تأكيد أن الترخيص يسمح بالمدة. |
| 4 | أهداف الاستعادة | RPO ≤ 24h · RTO ≤ 4h + restore drill إلزامي قبل hook الإنتاجي. |
| 5 | نقطة الالتقاط | `_get` (نقل) + lineage في المستهلكين — المستويان معاً، scoped على الليلي. |
| 6 | durable external failure sink | يُحسم في `ب-0ج` (نوع/مكان/احتفاظ/ربط/تغطية/تعطُّل متزامن). |
| 7 | المصادر الخارجية | EDGAR/NYSE (نطاق محدود) ثم المدفوع — خطوات مستقلة لاحقة، المدفوع بعرض سعر. |

---

## الحكم

**مُعتمَد للتجميد التصميمي (`APPROVED FOR DESIGN FREEZE`) دون تحفظ تصميمي.** المانع الوحيد
المتبقّي تعاقدي خارجي: **`ب-0أ` (إثبات FMP الكتابي)**. لا يبدأ `ب-0ب`/`ب-0ج`/`ب-1` قبل حسمه
وبإذن مستقل من أحمد. **هذا ليس اعتماداً لإنشاء فرع أو ملفات تنفيذية إضافية أو كود أو
`create_all` أو Pilot أو جمع خام.**

---

## المراجع الرسمية

> تاريخ الاطلاع: **2026-08-27**. هذه الروابط مراجع رسمية عامة للتصميم؛ **لا تثبت تلقائياً حق
> تخزين حمولة FMP الخام**. الإثبات المطلوب لـ`ب-0أ` يظل **موافقة FMP المكتوبة المرتبطة
> بالخطة/Order Form** (نطاقها في §4).

- شروط FMP: https://site.financialmodelingprep.com/terms-of-service
- التواصل التجاري مع FMP: https://site.financialmodelingprep.com/enterprise-contact
- واجهات SEC EDGAR: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- سياسة الوصول إلى EDGAR: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- تقويم وساعات NYSE: https://www.nyse.com/markets/hours-calendars
