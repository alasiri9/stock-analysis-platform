"""
crypto.py — تشفير متماثل خفيف للبيانات الحسّاسة (مفاتيح FMP الخاصة بالمشتركين).

طبقة أمان إضافية: نخزّن مفتاح المشترك مشفّراً في قاعدة البيانات، ونفكّه فقط لحظة
استخدامه لجلب سعره اللحظي. لو تسرّبت القاعدة، لا تظهر المفاتيح كنص واضح.

- نستخدم Fernet (تشفير متماثل موثّق AES-128-CBC + HMAC) من مكتبة cryptography.
- مفتاح التشفير مشتق من APP_PASSWORD (ثابت عبر إعادة التشغيل، بلا متغير بيئة جديد).
  لو APP_PASSWORD غير مضبوط (تطوير محلي) نشتقّه من ثابت — التشفير هنا للإنتاج.
- decrypt متسامح: لو وصلته قيمة قديمة غير مشفّرة (نص خام)، يُرجعها كما هي
  (توافق خلفي — لا حاجة لهجرة بيانات).
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet():
    """كائن Fernet بمفتاح مشتق من APP_PASSWORD (32 بايت → urlsafe base64)."""
    secret = os.getenv("APP_PASSWORD") or "algomatix-local-dev-secret"
    digest = hashlib.sha256(f"fmpkey-{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text):
    """يُشفّر نصاً ويُرجع الناتج (نص). قيمة فارغة/None تُرجع كما هي."""
    if not text:
        return text
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    """يفكّ نصاً مشفّراً. لو لم يكن مشفّراً (قيمة قديمة) أو تعذّر الفك، يُرجعه كما هو."""
    if not token:
        return token
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return token  # قيمة قديمة غير مشفّرة أو تالفة — توافق خلفي
