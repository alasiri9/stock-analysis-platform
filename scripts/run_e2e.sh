#!/usr/bin/env bash
# أمر تشغيل E2E الموحّد (تستدعيه CI نفسه كي تتطابق البوابة المحلية والرسمية).
# المتصفح:
#   - المسار الرسمي (CI/بيئة غير محجوبة): لا تضبط E2E_CHROMIUM_EXECUTABLE،
#     ونفّذ أولاً: python -m playwright install --with-deps chromium
#   - بيئة محجوبة عن cdn.playwright.dev: صدّر E2E_CHROMIUM_EXECUTABLE=<مسار chrome محلي>
#     (متصفح غير مطابق رسمياً — للاكتشاف المحلي فقط، ليس مرجع الإغلاق).
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pytest e2e -q \
  --tracing=retain-on-failure \
  --screenshot=only-on-failure \
  --video=off \
  --output=e2e/_artifacts
