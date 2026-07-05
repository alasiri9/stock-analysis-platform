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


def notify_signal(ticker, signal_type, price, atr=None, earnings_days=None):
    """يبني نص تنبيه إشارة ويرسله (لو الميزة مفعّلة).

    atr (اختياري): تذبذب السهم — عند توفره مع السعر تُضاف مستويات تعليمية:
    دخول = السعر الحالي، وقف = السعر − 1.5×ATR، هدف = السعر + 3×ATR (عائد/مخاطرة 1:2).
    earnings_days (اختياري): أيام لموعد الأرباح — يُضاف تحذير لو ≤7 أيام (تذبذب مرتفع).
    """
    kind = {
        "piotroski_strong": "💎 جودة مالية قوية (Piotroski)",
        "catalyst_strong": "⚡ زخم قوي (Catalyst)",
        "golden": "🥇 إشارة ذهبية — 3 عوامل مجتمعة (جودة + سيولة + اختراق)",
        "squeeze_breakout": "💣 انفجار وشيك — انضغاط بولينجر + اختراق بحجم مرتفع",
        "golden_cross": "🌟 تقاطع ذهبي — SMA50 قطع SMA200 صعوداً (اتجاه طويل المدى)",
        "trend_pullback": "🎯 ارتداد الترند — تراجع مؤقت بترند صاعد بدأ يرتد (شراء الانخفاض)",
    }.get(signal_type, signal_type)
    price_txt = f"{price:.2f}$" if price is not None else "غير متوفر"

    levels = ""
    if price is not None and atr:
        stop = price - 1.5 * atr
        target = price + 3.0 * atr
        risk_pct = (price - stop) / price * 100
        gain_pct = (target - price) / price * 100
        levels = (
            f"\n📐 <b>مستويات تعليمية</b> (محسوبة من تذبذب السهم ATR — ليست توصية):\n"
            f"▫️ دخول مقترح: {price:.2f}$\n"
            f"🎯 الهدف: {target:.2f}$ (+{gain_pct:.1f}%)\n"
            f"🛑 وقف الخسارة: {stop:.2f}$ (-{risk_pct:.1f}%)\n"
            f"⚖️ العائد مقابل المخاطرة: 2 : 1\n"
        )

    earn = ""
    if earnings_days is not None and earnings_days <= 7:
        when = "اليوم" if earnings_days == 0 else ("غداً" if earnings_days == 1 else f"بعد {earnings_days} أيام")
        earn = f"\n⚠️ <b>تنبيه:</b> إعلان الأرباح {when} — تذبذب مرتفع متوقّع، توخَّ الحذر.\n"

    text = (
        f"🚨 <b>إشارة جديدة من Algomatix</b>\n\n"
        f"السهم: <b>{ticker}</b>\n"
        f"النوع: {kind}\n"
        f"السعر وقت الإشارة: {price_txt}\n"
        f"{levels}"
        f"{earn}\n"
        f"https://algomatix-production.up.railway.app/stock/{ticker}"
    )
    return send_message(text)
