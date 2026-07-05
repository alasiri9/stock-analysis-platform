"""
telegram_client.py — إرسال تنبيهات تلغرام عند إشارات الماسح القوية.

الإعداد (اختياري — بدون المتغيرين تبقى الميزة خاملة بلا أي أثر):
- TELEGRAM_BOT_TOKEN: توكن البوت من @BotFather.
- TELEGRAM_CHAT_ID: معرّف المحادثة (رقم) الذي تُرسل إليه التنبيهات.

المبادئ:
- الفشل صامت مع سطر سجل فقط — التنبيه كماليّ ولا يجوز أن يُسقط تحديث البيانات.
- مهلة قصيرة (10 ثوانٍ) حتى لا يعلّق التحديث لو تعثّر تلغرام.
"""

import os

import requests

TIMEOUT = 10


def is_configured():
    """هل متغيرا التلغرام مضبوطان؟"""
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_message(text):
    """يرسل رسالة تلغرام. يُرجع True عند النجاح، False عند الفشل أو غياب الإعداد."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False  # الميزة غير مفعّلة — تجاهل صامت

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[telegram] فشل الإرسال ({resp.status_code}): {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"[telegram] تعذّر الإرسال: {e}")
        return False


def notify_signal(ticker, signal_type, price):
    """يبني نص تنبيه إشارة ويرسله (لو الميزة مفعّلة)."""
    kind = {
        "piotroski_strong": "💎 جودة مالية قوية (Piotroski)",
        "catalyst_strong": "⚡ زخم قوي (Catalyst)",
        "golden": "🥇 إشارة ذهبية — 3 عوامل مجتمعة (جودة + سيولة + اختراق)",
        "squeeze_breakout": "💣 انفجار وشيك — انضغاط بولينجر + اختراق بحجم مرتفع",
        "golden_cross": "🌟 تقاطع ذهبي — SMA50 قطع SMA200 صعوداً (اتجاه طويل المدى)",
    }.get(signal_type, signal_type)
    price_txt = f"{price:.2f}$" if price is not None else "غير متوفر"
    text = (
        f"🚨 <b>إشارة جديدة من Algomatix</b>\n\n"
        f"السهم: <b>{ticker}</b>\n"
        f"النوع: {kind}\n"
        f"السعر وقت الإشارة: {price_txt}\n\n"
        f"https://algomatix-production.up.railway.app/stock/{ticker}"
    )
    return send_message(text)
