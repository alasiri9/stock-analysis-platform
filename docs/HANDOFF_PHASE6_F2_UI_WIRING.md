# حالة العمل — PHASE 6 / F2: Data Confidence UI Wiring

> ملف تسليم لحفظ الحالة بين الجلسات. **لا كود منفّذ بعد — الخطة فقط جاهزة ومعتمدة داخليًّا وتنتظر مراجعة Codex + إذن مستقل.**

## أين نحن الآن
- **F2 (نواة Data Confidence) مدموجة في `main`** عبر PR #2 (`claude/phase6-f2-confidence-core`).
- HEAD الحالي لـ`main` والفرع المخصّص: `6ff24aa` (Merge PR #2).
- الفرع المخصّص للعمل القادم: `claude/american-platform-file-19r593` (أُعيد ضبطه فوق أحدث `main`).
- **المرحلة القادمة (لم تبدأ):** ربط الثقة بالواجهة (UI Wiring). خطة كاملة جاهزة، تنتظر:
  1. مراجعة Codex واعتمادها.
  2. الإجابة عن سؤالين مفتوحين (أدناه).
  3. إذن مستقل جديد لبدء التنفيذ (STEP 3A).

## القاعدة الحاكمة
- **PLAN ONLY حتى الآن:** لا commit كود/لا push/لا PR/لا merge/لا deploy/لا migration/لا حذف — إلا بإذن مستقل جديد لكل خطوة.
- كل دمج في `main` يَنشر تلقائيًّا على الموقع الحيّ خلال ~دقيقتين → **`python tests/smoke_test.py` إلزامي قبل أي دمج**.

## ما تعرضه المرحلة القادمة
عرض `data_confidence` **المخزّنة فقط** (لا إعادة حساب/لا حفظ/لا DB/لا API/لا live price) عبر الـview-model الموجود:
`present_confidence` · `present_confidence_from_extra_json` · `latest_confidence_map` (في `services/tracking.py`, `services/confidence_view.py`).

## المسارات والقوالب الفعلية (مؤكّدة بالكود)
| المسار | دالة app.py | القالب | قائمة الأسهم |
|---|---|---|---|
| `/` | `index()` 820–878 | `index.html` | results/ready/breakouts (`.ticker`) |
| `/gems` | `gems()` 880–885 | `gems.html` | results |
| `/leaders` | `leaders()` 887–893 | `leaders.html` | results |
| `/changes` | `changes()` 1591–1597 | `changes.html` | items[].ticker |
| `/stock/<t>` | `stock_report()` 1640–1748 | `stock.html` | report.ticker |

- البطاقة المشتركة `_scard.html` مُضمّنة في index (162،180،276) وgems (23) وleaders (25) وتستخدم `r.ticker`.
- شارات حالية للاسترشاد: `badge-gem` (_scard:13)، `state-pill` (_scard:14) داخل `scard-top`.
- نمط tooltip قابل لإعادة الاستخدام: `data-tip="..."` + CSS `.help-q`/`.nav-eye` (style.css:817).
- كسر كاش CSS: `base.html:11` حاليًّا `?v=20260816b` (يُرفع مرّة واحدة عند تعديل CSS).
- **لا كلاسات `conf-*` موجودة** (تجنّب `.confidence` في style.css:656 — نصّ مختلف).

## عقد الربط (Backend → Template)
- الصفحات ذات القوائم: `tracking.latest_confidence_map()` **بلا وسيط** → كل UNIVERSE (≤32) باستعلام **واحد** يغطّي كل القوائم.
- صفحة السهم: `tracking.latest_confidence_map([report["ticker"]]).get(report["ticker"])`.
- **تمرير صريح في المسارات فقط — لا `context_processor`** (يُشغّل الاستعلام على كل صفحة).
- `_scard` يقرأ `confidence_map.get(r.ticker)` من dict جاهز → **لا N+1**. القيمة view-model جاهزة للعرض → **لا helper إضافي**، فقط حارس Jinja.
- غياب ticker من الخريطة → `None` → «غير متوفرة» بلا خطأ.

## نصوص الواجهة (من الـview-model)
- high = «ثقة عالية» / لون **أزرق-نيلي (ليس أخضر)** · medium = «ثقة متوسطة» / كهرماني · low = «ثقة منخفضة» / رمادي.
- missing + corrupt → موحّدان «درجة الثقة غير متوفرة» (رمادي محايد). `reason_code`/`schema_version` **داخلي فقط، لا يُعرض للمستخدم**. لا JSON/تفاصيل مخيفة.
- جملة `explanation` ملازمة. فصل بصري تام عن جودة السهم (Piotroski) وقوة الفرصة (Algomatix/جاهز).
- البطاقة: حبة `conf-badge` آخر `scard-top` (بعد state-pill/gem)، تُخفى `score_text` على الجوال.
- صفحة السهم: لوحة قرب `score-cards` (stock.html:139): band + score + as_of + عوامل (factors) + missing ودّيًّا.

## الملفات المتوقّع تعديلها (عند الإذن)
`app.py` (Prod) · `templates/_scard.html` · `templates/stock.html` · `static/style.css` · `templates/base.html` (رفع ?v=) · `templates/changes.html` (اختياري/مؤجّل) · جديد `tests/test_phase6_ui_wiring.py`.
**بلا تعديل:** index/gems/leaders.html (include فقط) · confidence*.py/tracking.py (مجمّدة) · models.py.

## المراحل والـcommits المقترحة
- **STEP 3A** — ربط backend: `app.py` + `tests/test_phase6_ui_wiring.py` → `feat(phase6): wire confidence map into scanner routes`
- **STEP 3B** — شارة البطاقة + CSS + ?v=: `_scard.html`+`style.css`+`base.html` → `feat(phase6): add data confidence badge to stock card`
- **STEP 3C** — لوحة صفحة السهم: `stock.html` → `feat(phase6): add data confidence panel to stock page`
- **STEP 3D** — تلميع/legend/(changes)/اختبارات → `feat(phase6): polish confidence UI (legend, responsive, tests)`

## مصفوفة الاختبارات (قبل أي دمج)
```
python -m pytest tests/test_phase6_ui_wiring.py -rA -q        # جديد: استعلام واحد/صفحة، 5 حالات، لا N+1/write/API
python -m pytest tests/test_phase6_confidence.py tests/test_phase6_confidence_read.py \
                tests/test_phase6_confidence_view.py tests/test_phase6_persistence.py -q
python tests/test_logic_audit_phase*.py                       # PHASE 1–5
python tests/smoke_test.py                                    # إلزامي قبل الدمج (لا 500)
git diff --check                                              # مسافات
```

## حواف مضمونة (بلا HTTP 500 — مثبتة بـF2)
NULL/فارغ→missing · تالف/بنية/score خارج→corrupt · snap_date غير صالح→as_of None · ticker غائب→na · قائمة فارغة→{} · تكرار/حالة أحرف→`_clean_tickers`.

## أسئلة مفتوحة (تحتاج قرار أحمد قبل STEP 3A)
1. **`/changes`:** ضمن المرحلة الأولى (STEP 3D) أم مرحلة لاحقة مستقلة؟
2. **لون `high`:** نيلي `#7c6fe6` أم أزرق فاتح `#a7c0e8`؟

## أول خطوة عند العودة
اقرأ هذا الملف → انتظر اعتماد Codex + جواب السؤالين + إذن مستقل → ابدأ STEP 3A (backend wiring + ملف الاختبار) فقط.
