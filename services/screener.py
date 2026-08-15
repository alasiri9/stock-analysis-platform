"""
screener.py — فلترة الأسهم (Screener) مع تخزين مؤقت لحماية حدود الـ API.

الفكرة:
- لدينا قائمة أسهم مختارة (UNIVERSE).
- نحسب درجاتها مرة واحدة ونخزّنها في جدول stock_cache (data_json).
- الفلترة تتم على البيانات المخزّنة (فورية، بدون استدعاءات API).
- زر "تحديث" يعيد بناء الكاش يدوياً عند الحاجة.

هذا يقلّل استهلاك الباقة المجانية (الفلترة لا تكلّف استدعاءات).
"""

import json
import time
from datetime import datetime, timezone, timedelta, date as date_cls

from models import db, StockCache, Signal, PricePoint
from services import fmp_client
from services import finnhub_client
from services import scoring
from services import indicators
from services import telegram_client

# عتبات توليد الإشارات (تعليمية، لا توصية)
PIOTROSKI_SIGNAL_MIN = 8   # جودة مالية قوية
CATALYST_SIGNAL_MIN = 80   # نمو قوي

# قائمة أسهم مختارة للماسح (موسّعة، لكن محدودة لحماية حدود الـ API المجانية)
# الحساب: كل سهم = 6 استدعاءات عند التحديث الكامل؛ 32 سهماً = 192 من حد 250/يوم (هامش ~58).
# كل الرموز هنا مُختبرة ومتاحة بباقة FMP المجانية (رموز كثيرة أخرى محجوبة عنها بخطأ 402).
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    "TSLA", "AMD", "NFLX", "JPM", "V", "WMT",
    "UNH", "JNJ", "XOM", "ADBE", "COST", "CVX",
    "KO", "PEP", "DIS", "INTC", "BAC", "PYPL",
    "ABBV", "PFE", "CSCO", "VZ", "T", "WFC",
    "GS", "NKE",
]

# بادئة لتمييز سجلّات الماسح داخل stock_cache
_PREFIX = "screen:"

# عدد جلسات التداول لقياس الزخم/القوة النسبية (~3 أشهر)
MOMENTUM_SESSIONS = 63

# نافذة "الصعود الأخير" (~أسبوعان) — أساس فلتر "لسا ما صعد"
RECENT_SESSIONS = 10
# لسا ما صعد: نستبعد من قائمة "قبل الانطلاق" ما قفز أكثر من هذا خلال النافذة الأخيرة
EARLY_MAX_RECENT_GAIN = 12.0


def _bullish_strategies(record):
    """يُرجع قائمة أسماء الاستراتيجيات/المؤشرات الصاعدة المتحققة للسهم (تأكيد متعدد).

    كلما طال هذا العدد، زاد تضافر الأدلة على أن السهم مرشّح قوي للانطلاق.
    """
    inds = {b.get("label"): b for b in (record.get("indicators") or [])}

    def is_bull(label):
        b = inds.get(label)
        return bool(b and b.get("status") == "bull")

    checks = {
        "الاتجاه صاعد (EMA)": is_bull("EMA"),
        "زخم إيجابي (MACD)": is_bull("MACD"),
        "قوة نسبية صحية (RSI)": is_bull("RSI"),
        "اتجاه قوي (ADX)": is_bull("ADX"),
        "اختراق قمة": is_bull("اختراق"),
        "حجم مرتفع": is_bull("الحجم"),
        "سوبرترند صاعد": is_bull("سوبرترند"),
        "تقاطع صاعد": is_bull("تقاطع"),
        "تراكم حجم (OBV)": is_bull("تراكم"),
        "سيولة داخلة": (record.get("money_flow") or {}).get("status") == "bull",
        "جودة مالية (Piotroski)": record.get("piotroski") is not None and record["piotroski"] >= 7,
        "أقوى من السوق": record.get("rel_strength") is not None and record["rel_strength"] > 0,
    }
    return [name for name, ok in checks.items() if ok]


def bullish_reasons(record):
    """أسباب ترشّح السهم (العوامل الصاعدة المتحققة) — لعرض جملة مبسّطة على البطاقة."""
    return _bullish_strategies(record)


def measures_met(record):
    """عدد المقاييس الإيجابية المجتمعة للسهم (تضافر الأدلة الصاعدة).

    يعدّ: كل شارة فنية حالتها صاعدة (حتى 12) + سيولة داخلة + أقوى من السوق
    + جودة مالية عالية (Piotroski≥8) + نمو قوي (Catalyst≥80). الأقصى ~16.
    كلما زاد العدد، زاد تضافر المقاييس الإيجابية على السهم.
    """
    count = sum(1 for b in (record.get("indicators") or []) if b.get("status") == "bull")
    if (record.get("money_flow") or {}).get("status") == "bull":
        count += 1
    if record.get("rel_strength") is not None and record["rel_strength"] > 0:
        count += 1
    if record.get("piotroski") is not None and record["piotroski"] >= 8:
        count += 1
    if record.get("catalyst") is not None and record["catalyst"] >= 80:
        count += 1
    return count


def tech_tilt(record):
    """ميل الإشارات الفنية للسهم (تعليمي) — لميزان نصف الدائرة على البطاقة.

    يُرجع dict {frac (0..1)، label تعليمي مختصر، kind} أو None لو لا مؤشرات.
    frac = (الإيجابية + نصف المحايدة) ÷ الإجمالي. تعليمي — ليس توصية شراء/بيع.
    """
    inds = record.get("indicators") or []
    if not inds:
        return None
    bull = sum(1 for b in inds if b.get("status") == "bull")
    bear = sum(1 for b in inds if b.get("status") == "bear")
    neutral = len(inds) - bull - bear
    frac = (bull + 0.5 * neutral) / len(inds)
    if frac >= 0.70:
        label, kind = "إيجابي قوي", "pos2"
    elif frac >= 0.56:
        label, kind = "إيجابي", "pos1"
    elif frac > 0.44:
        label, kind = "محايد", "neu"
    elif frac > 0.30:
        label, kind = "سلبي", "neg1"
    else:
        label, kind = "سلبي قوي", "neg2"
    return {"frac": frac, "label": label, "kind": kind}


# ── نموذج خطة التداول الآلية (من دورة سلطان الفيفي) ──────────────────────────
# يحوّل حالة مؤشرات المنصة إلى «درجة انطباق من 10» لكل استراتيجية (كما يقيّمها الشيخ
# يدوياً)، ثم يطبّق قاعدته آلياً: الدخول يحتاج ≥3 استراتيجيات درجتها ≥7.
# ⚠️ تعليمي وصفي فقط — لا توصية. كل درجة تحمل «سبباً» ظاهراً للمتعلّم (شفافية).
# عتبة الانطباق (درجة تُعدّ «منطبقة») — نفس عتبة الشيخ:
PLAN_MET_SCORE = 7

# المجموع الكامل لاستراتيجيات الخطة (المعرّفة في _plan_strategy_scores).
# يُستخدم مقاماً ثابتاً في العرض حتى لا يتذبذب حسب توفّر بيانات كل سهم
# (استراتيجية تنقص بياناتها تُعدّ «غير منطبقة» ضمن المجموع نفسه).
PLAN_TOTAL_STRATEGIES = 13


def _plan_strategy_scores(record):
    """يحسب درجة كل استراتيجية فنية (0–10) + سببها من سجل الماسح.

    يُرجع قائمة عناصر {name, status, score, reason} مرتّبة (الأقوى أولاً).
    """
    inds = {b.get("label"): b for b in (record.get("indicators") or [])}
    brk = record.get("break_status") or {}
    out = []

    def add(name, status, score, reason):
        out.append({"name": name, "status": status, "score": score, "reason": reason})

    # 1) الاتجاه (المتوسطات EMA 20/50) — الشارة صاعدة فقط عند السعر > EMA20 > EMA50
    b = inds.get("EMA")
    if b:
        st = b.get("status")
        if st == "bull":
            add("الاتجاه (متوسطات 20/50)", "bull", 9, "السعر فوق المتوسطين 20 و50 — اتجاه صاعد منظّم")
        elif st == "bear":
            add("الاتجاه (متوسطات 20/50)", "bear", 1, "السعر تحت المتوسطين — اتجاه هابط")
        else:
            add("الاتجاه (متوسطات 20/50)", "neutral", 5, "الاتجاه غير محسوم (بين المتوسطين)")

    # 2) التقاطع الذهبي (SMA 50/200) — استراتيجية الشيخ «التقاطعات»
    b = inds.get("تقاطع")
    if b:
        val, st = str(b.get("value") or ""), b.get("status")
        if "ذهبي" in val:
            add("التقاطع الذهبي (50/200)", "bull", 10, "تقاطع ذهبي حديث — أقوى إشارة اتجاه طويل")
        elif "هابط" in val:
            add("التقاطع الذهبي (50/200)", "bear", 0, "تقاطع الموت — إشارة خروج")
        elif st == "bull":
            add("التقاطع الذهبي (50/200)", "bull", 7, "المتوسط 50 فوق 200 — اتجاه صاعد قائم")
        else:
            add("التقاطع الذهبي (50/200)", "bear", 2, "المتوسط 50 تحت 200 — اتجاه ضعيف")

    # 3) الزخم (MACD)
    b = inds.get("MACD")
    if b:
        if b.get("status") == "bull":
            add("الزخم (MACD)", "bull", 8, "الهيستوجرام موجب — زخم صاعد يدعم الاستمرار")
        else:
            add("الزخم (MACD)", "bear", 1, "زخم سلبي (الهيستوجرام سالب)")

    # 4) القوة النسبية (RSI)
    b = inds.get("RSI")
    if b:
        val, st = str(b.get("value") or ""), b.get("status")
        if st == "bull" and "بيعي" in val:
            add("القوة النسبية (RSI)", "bull", 9, "ارتد من تشبّع بيعي — أفضل مناطق الشراء")
        elif "مؤكّد بحجم" in val:            # تشبّع شرائي مؤكّد بحجم عالٍ = زخم انطلاق
            add("القوة النسبية (RSI)", "bull", 8, "زخم قوي — تشبّع شرائي مؤكّد بحجم عالٍ")
        elif "شرائي" in val:                  # تشبّع شرائي بلا تأكيد حجم = محايد (راقب) لا عقوبة
            add("القوة النسبية (RSI)", "neutral", 5, "تشبّع شرائي بلا تأكيد حجم — راقب")
        elif st == "bull":
            add("القوة النسبية (RSI)", "bull", 7, "قوة نسبية صحّية (فوق 50)")
        else:
            add("القوة النسبية (RSI)", "bear", 3, "قوة نسبية أقل من 50")

    # 5) قوة الاتجاه (ADX)
    b = inds.get("ADX")
    if b:
        if b.get("status") == "bull":
            add("قوة الاتجاه (ADX)", "bull", 8, f"ADX {b.get('value')} — اتجاه قوي واضح")
        else:
            add("قوة الاتجاه (ADX)", "neutral", 4, f"ADX {b.get('value')} — اتجاه ضعيف/عرضي")

    # 6) سوبرترند
    b = inds.get("سوبرترند")
    if b:
        if b.get("status") == "bull":
            add("سوبرترند", "bull", 8, "سوبرترند صاعد — يدعم متابعة الاتجاه")
        else:
            add("سوبرترند", "bear", 1, "سوبرترند هابط")

    # 7) الاختراق (+ تأكيد الحجم من break_status)
    b = inds.get("اختراق")
    if b:
        if b.get("status") == "bull":
            if brk.get("confirmed") and brk.get("dir") == "breakout":
                add("الاختراق", "bull", 10, "اختراق قمة مؤكّد بحجم عالٍ")
            else:
                add("الاختراق", "bull", 8, "اخترق أعلى قمة 20 يوماً")
        else:
            add("الاختراق", "neutral", 4, "لم يخترق قمته بعد")

    # 8) البولينجر (انضغاط)
    b = inds.get("انضغاط")
    if b:
        val = str(b.get("value") or "")
        if b.get("status") == "bull":
            add("البولينجر (انضغاط)", "bull", 8, "انضغاط مؤكّد صعودياً — تمهيد لانفجار سعري")
        elif val == "نعم":
            add("البولينجر (انضغاط)", "neutral", 6, "انضغاط قائم — انفجار سعري محتمل قريباً")
        else:
            add("البولينجر (انضغاط)", "neutral", 4, "لا انضغاط حالياً")

    # 9) الحجم
    b = inds.get("الحجم")
    if b:
        if b.get("status") == "bull":
            add("الحجم", "bull", 8, "حجم اليوم أعلى من المتوسط — اهتمام حقيقي")
        else:
            add("الحجم", "neutral", 4, f"حجم {b.get('value')}")

    # 10) تراكم السيولة (OBV)
    b = inds.get("تراكم")
    if b:
        st = b.get("status")
        if st == "bull":
            add("تراكم السيولة (OBV)", "bull", 8, "تجميع — الحجم يتراكم مع الصعود")
        elif st == "bear":
            add("تراكم السيولة (OBV)", "bear", 1, "تصريف — الحجم يتراكم مع الهبوط")
        else:
            add("تراكم السيولة (OBV)", "neutral", 4, "تراكم محايد")

    # 11) السيولة الداخلة (تدفق الأموال / MFI)
    mf = record.get("money_flow")
    if mf:
        st = mf.get("status")
        if st == "bull":
            _sc = mf.get("score")
            add("السيولة (تدفق الأموال)", "bull", 8,
                "سيولة داخلة (تجميع)" + (f" — درجة {_sc:.0f}" if _sc is not None else ""))
        elif st == "bear":
            add("السيولة (تدفق الأموال)", "bear", 1, "سيولة خارجة (تصريف)")
        else:
            add("السيولة (تدفق الأموال)", "neutral", 4, "تدفق سيولة محايد")

    # 12) أقوى من السوق (القوة النسبية مقابل S&P 500)
    rs = record.get("rel_strength")
    if rs is not None:
        if rs > 5:
            add("أقوى من السوق", "bull", 9, f"يتفوّق على السوق بوضوح (+{rs:.0f}%)")
        elif rs > 0:
            add("أقوى من السوق", "bull", 7, f"أقوى من السوق (+{rs:.0f}%)")
        else:
            add("أقوى من السوق", "bear", 2, f"أضعف من السوق ({rs:.0f}%)")

    # 13) شمعة الانعكاس (الشموع اليابانية) — داعمة لا تُعتمد وحدها (كما نصّ الشيخ)
    # عمداً أقصى درجة صعودية = 6 (تحت عتبة الانطباق 7): الشمعة الواحدة تأكيد داعم
    # لا «ركيزة» مستقلّة، فلا تُكمِل وحدها شرط الدخول (3 استراتيجيات ≥7).
    rv = record.get("reversal")
    if rv:
        st = rv.get("status")
        if st == "bull":
            add("شمعة الانعكاس", "bull", 6, f"{rv.get('pattern')} — انعكاس صعودي محتمل (تأكيد داعم)")
        elif st == "bear":
            add("شمعة الانعكاس", "bear", 1, f"{rv.get('pattern')} — انعكاس هبوطي محتمل")
        else:
            add("شمعة الانعكاس", "neutral", 4, f"{rv.get('pattern')} — تردّد")

    out.sort(key=lambda s: s["score"], reverse=True)
    return out


def trading_plan(record):
    """نموذج خطة التداول الآلي لسهم — جدول استراتيجيات مسجّل + جدول قواعد + خلاصة تعليمية.

    يطبّق قاعدة الشيخ آلياً: «مكتمل الشروط للدراسة» يحتاج ≥3 استراتيجيات درجتها ≥7
    مع صفقة رابحة وبُعد عن المقاومة. ⚠️ تعليمي وصفي فقط — لا توصية بشراء أو بيع.
    يُرجع dict {strategies, total, met_count, verdict, verdict_label, rules} أو None.
    """
    strategies = _plan_strategy_scores(record)
    if not strategies:
        return None

    met_count = sum(1 for s in strategies if s["score"] >= PLAN_MET_SCORE)
    total = sum(s["score"] for s in strategies)

    plan = record.get("atr_plan") or {}
    rr = plan.get("risk_reward")
    # نميّز «صفقة ضعيفة مؤكّدة» (العائد < المخاطرة، بيانات موجودة) عن «بيانات ناقصة»
    # (atr_plan لم يُحسب بعد). البيانات الناقصة لا تُخفِّض الحكم — فقط السبب المعروف يُخفّضه.
    rr_weak = plan.get("rr_quality") == "weak"
    near_res = record.get("near_resistance") is not None

    # قاعدة الشيخ آلياً: ≥3 استراتيجيات ≥7 = عتبة الدخول. تُخفَّض لسبب معروف فقط:
    # صفقة ضعيفة مؤكّدة أو السعر ملاصق لمقاومة (قاعدة الانتظار).
    if met_count >= 3 and not rr_weak and not near_res:
        verdict, verdict_label = "ready", "مكتمل الشروط للدراسة"
    elif met_count == 0:
        verdict, verdict_label = "weak", "ضعيف — لا تتوفّر شروط"
    else:
        verdict, verdict_label = "waiting", "ناقص — انتظار"

    # سبب الانتظار (شفافية للمتعلّم)
    wait_reasons = []
    if met_count < 3:
        wait_reasons.append(f"عدد الاستراتيجيات المنطبقة {met_count} (المطلوب 3)")
    if rr_weak:
        wait_reasons.append("العائد المتوقّع لا يتجاوز المخاطرة بوضوح")
    if near_res:
        wait_reasons.append("السعر ملاصق لمقاومة سابقة — انتظر تأكيد الاختراق")

    # سبب مختصر يظهر في السطر المختصر (شفافية بلمحة)
    wait_short = None
    if verdict == "waiting":
        parts = []
        if met_count < 3:
            parts.append("استراتيجيات ناقصة")
        if rr_weak:
            parts.append(f"صفقة ضعيفة ({rr:.1f})" if rr is not None else "صفقة ضعيفة")
        if near_res:
            parts.append("قرب مقاومة")
        wait_short = " · ".join(parts) if parts else None

    ema = next((s for s in strategies if s["name"].startswith("الاتجاه")), None)
    rules = {
        "entry": plan.get("entry"),
        "stop": plan.get("stop"),
        "target": plan.get("target"),
        "risk_reward": rr,
        "rr_quality": plan.get("rr_quality"),
        "trend": (ema or {}).get("status"),
        "near_resistance": record.get("near_resistance"),
        "rel_strength": record.get("rel_strength"),
        "piotroski": record.get("piotroski"),
        "catalyst": record.get("catalyst"),
    }

    return {
        "strategies": strategies,
        "total": total,
        "met_count": met_count,
        "strategy_total": PLAN_TOTAL_STRATEGIES,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "wait_reasons": wait_reasons,
        "wait_short": wait_short,
        "rules": rules,
    }


# ── 👑 مؤشر Algomatix — درجة فرصة موحّدة (0–100) تجمع أفضل المدارس بأوزان معتمدة ──────
# الأوزان اعتمدها أحمد (2026-08-03، + مدرسة الفريمات 2026-08-08): متوازن. المجموع = 100.
ALGOMATIX_WEIGHTS = {
    "structure": 15,      # 🧭 هيكل السوق (BOS/CHOCH/إعادة اختبار)
    "fundamentals": 18,   # 🏦 الجودة والنمو (Piotroski + Catalyst)
    "trend": 12,          # 📈 الاتجاه (EMA + تقاطع ذهبي + سوبرترند + ADX)
    "momentum": 11,       # ⚡ الزخم (MACD + RSI)
    "liquidity": 12,      # 💧 السيولة (تدفق الأموال + OBV + POC)
    "levels": 10,         # 📐 المستويات (فيبوناتشي + قرب المقاومة)
    "frames": 10,         # ⏱️ الفريمات الثلاثة (يومي/أسبوعي/شهري)
    "rel_strength": 7,    # 💪 القوة النسبية مقابل السوق
    "price_action": 5,    # 🕯️ السلوك السعري (شموع الانعكاس)
}
_ALGX_LABELS = {
    "structure": "🧭 هيكل السوق", "fundamentals": "🏦 الجودة والنمو", "trend": "📈 الاتجاه",
    "momentum": "⚡ الزخم", "liquidity": "💧 السيولة", "levels": "📐 المستويات",
    "frames": "⏱️ الفريمات الثلاثة",
    "rel_strength": "💪 القوة النسبية", "price_action": "🕯️ السلوك السعري",
}


def _status_val(status):
    """يحوّل حالة شارة إلى قيمة 0..1 (صاعد=1 · محايد=0.5 · هابط=0)."""
    return 1.0 if status == "bull" else (0.5 if status == "neutral" else 0.0)


def _algx_subscores(record):
    """درجة كل مدرسة (0..1). القيم الغائبة تُعامل محايدةً (0.5) فلا تُخفّض ظلماً."""
    inds = {b.get("label"): b for b in (record.get("indicators") or [])}

    # 🧭 هيكل السوق
    ms = record.get("structure")
    if not ms:
        s_structure = 0.5
    elif ms.get("status") == "bull":
        rs, ev = ms.get("retest_state"), ms.get("event")
        s_structure = (1.0 if rs == "confirmed" else 0.78 if rs == "testing"
                       else 0.85 if ev == "BOS" else 0.70 if ev == "CHOCH" else 0.58)
    elif ms.get("status") == "bear":
        s_structure = 0.15
    else:
        s_structure = 0.42 if ms.get("trend") == "side" else 0.5

    # 🏦 الجودة والنمو
    fund = []
    if record.get("piotroski") is not None:
        fund.append(min(1.0, record["piotroski"] / 9.0))
    if record.get("catalyst") is not None:
        fund.append(min(1.0, record["catalyst"] / 100.0))
    s_fund = sum(fund) / len(fund) if fund else 0.5

    # 📈 الاتجاه
    tr = [_status_val(inds[k]["status"]) for k in ("EMA", "تقاطع", "سوبرترند", "ADX") if k in inds]
    s_trend = sum(tr) / len(tr) if tr else 0.5

    # ⚡ الزخم
    mo = [_status_val(inds[k]["status"]) for k in ("MACD", "RSI") if k in inds]
    s_mom = sum(mo) / len(mo) if mo else 0.5

    # 💧 السيولة
    liq = []
    if record.get("money_flow"):
        liq.append(_status_val(record["money_flow"].get("status")))
    if "تراكم" in inds:
        liq.append(_status_val(inds["تراكم"]["status"]))
    vp = record.get("volume_profile")
    if vp:
        liq.append({"above": 0.70, "at": 0.55}.get(vp.get("position"), 0.30))
    s_liq = sum(liq) / len(liq) if liq else 0.5

    # 📐 المستويات
    s_lev = 0.5
    fib = record.get("fibonacci")
    if fib:
        s_lev = 0.82 if fib.get("in_golden") else (0.62 if fib.get("at_level") else 0.5)
    if record.get("near_resistance") is not None:
        s_lev = max(0.0, s_lev - 0.25)   # ملاصق لمقاومة = حذر

    # 💪 القوة النسبية
    rsv = record.get("rel_strength")
    # تدرّج ناعم بدل قفزة حادة عند الصفر: مطابق/أضعف قليلاً لا يُعاقَب كأضعف بوضوح
    s_rs = 0.5 if rsv is None else (1.0 if rsv > 5 else 0.7 if rsv > 0 else 0.4 if rsv > -5 else 0.2)

    # 🕯️ السلوك السعري
    rv = record.get("reversal")
    s_pa = 0.5 if not rv else (0.9 if rv.get("status") == "bull" else 0.1 if rv.get("status") == "bear" else 0.5)

    # ⏱️ الفريمات الثلاثة (توافق يومي/أسبوعي/شهري)
    frm = record.get("frames")
    if not frm:
        s_frames = 0.5
    else:
        ups = frm.get("up_count", 0)
        downs = frm.get("down_count", 0)
        s_frames = {3: 1.0, 2: 0.72, 1: 0.5, 0: 0.35}.get(ups, 0.5)
        s_frames = max(0.0, min(1.0, s_frames - 0.08 * downs))

    return {
        "structure": s_structure, "fundamentals": s_fund, "trend": s_trend,
        "momentum": s_mom, "liquidity": s_liq, "levels": s_lev,
        "frames": s_frames, "rel_strength": s_rs, "price_action": s_pa,
    }


def algomatix_score(record):
    """👑 مؤشر Algomatix: درجة فرصة موحّدة (0–100) موزونة عبر 9 مدارس + تفصيل شفّاف.

    ⚠️ تعليمي وصفي فقط — لا توصية بشراء أو بيع. يُرجع {score, verdict, verdict_label, breakdown}.
    """
    subs = _algx_subscores(record)
    breakdown, score = [], 0.0
    for key, w in ALGOMATIX_WEIGHTS.items():
        pts = subs[key] * w
        score += pts
        breakdown.append({"key": key, "label": _ALGX_LABELS[key], "weight": w,
                          "sub": round(subs[key], 2), "points": round(pts, 1)})
    score = round(score)
    if score >= 70:
        verdict, vlabel = "strong", "فرصة قوية للدراسة"
    elif score >= 55:
        verdict, vlabel = "good", "فرصة جيدة"
    elif score >= 40:
        verdict, vlabel = "neutral", "محايد"
    else:
        verdict, vlabel = "weak", "ضعيف"
    breakdown.sort(key=lambda x: x["points"], reverse=True)
    return {"score": score, "verdict": verdict, "verdict_label": vlabel, "breakdown": breakdown}


def early_launch_candidates(records=None, min_strategies=3):
    """مرشّحو "قبل الانطلاق": أسهم في مرحلة مبكرة ولم تصعد بعد، مرتّبة بقوة التأكيد.

    المرحلة المبكرة = أحدها:
    - قيد الشحن: انضغاط بولينجر قائم (تذبذب ضيّق يسبق الحركة) دون اختراق بعد.
    - بداية الاختراق: أول اختراق قمة 20 يوماً (الحركة للتو بدأت).
    الفلتر "لسا ما صعد": يُستبعد ما قفز أكثر من EARLY_MAX_RECENT_GAIN خلال آخر أسبوعين
    (None = العائد غير محسوب بعد، فلا نستبعد — سيُحسب مع أول تحديث ليلي).
    الترتيب حسب عدد الاستراتيجيات الصاعدة المتحققة (min_strategies حدّ أدنى).
    يُرجع قائمة مرتّبة تنازلياً (بلا استدعاء API — من الكاش).
    """
    if records is None:
        records, _ = load_records()
    out = []
    for r in records:
        badges = r.get("indicators") or []
        squeezed = any(b.get("label") == "انضغاط" and b.get("value") in ("نعم", "إيجابي") for b in badges)
        breakout = any(b.get("label") == "اختراق" and b.get("status") == "bull" for b in badges)
        if not (squeezed or breakout):
            continue  # ليس في مرحلة مبكرة

        rg = r.get("recent_gain")
        if rg is not None and rg > EARLY_MAX_RECENT_GAIN:
            continue  # صعد كثيراً بالفعل — فات وقت الدخول المبكر

        strategies = _bullish_strategies(r)
        if len(strategies) < min_strategies:
            continue  # تأكيد ضعيف

        # المرحلة: قيد الشحن (انضغاط بلا اختراق) أقدم من بداية الاختراق
        if squeezed and not breakout:
            stage, stage_key = "🔋 قيد الشحن", "coiling"
        elif breakout:
            stage, stage_key = "🚀 بداية الاختراق", "breakout"
        else:
            stage, stage_key = "🔋 قيد الشحن", "coiling"

        out.append({
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "sector": r.get("sector"),
            "price": r.get("price"),
            "catalyst": r.get("catalyst"),
            "piotroski": r.get("piotroski"),
            "recent_gain": rg,
            "stage": stage,
            "stage_key": stage_key,
            "strategies": strategies,
            "strength": len(strategies),
        })
    # الأقوى تأكيداً أولاً، ثم الأقل صعوداً (الأطزج)، ثم قيد الشحن قبل الاختراق
    out.sort(key=lambda x: (
        -x["strength"],
        x["recent_gain"] if x["recent_gain"] is not None else 0,
        0 if x["stage_key"] == "coiling" else 1,
    ))
    return out

# ننبّه المستخدم لو موعد إعلان أرباح السهم خلال هذه المدة (أعلى أوقات التذبذب خطراً)
EARNINGS_LOOKAHEAD_DAYS = 21


def _shares_float_map():
    """خريطة {رمز: {float_shares, free_float_pct}} لأسهم UNIVERSE — طلب FMP واحد (bulk).

    نُميّز الفشل عن النجاح صراحةً:
    - فشل الجلب → None (المتصل يُبقي قيم الأسهم الحرة السابقة بدل مسحها).
    - نجاح الجلب → خريطة (قد تكون فارغة {} = المصدر رجع بلا بيانات لأسهمنا).
    الميزة كماليّة ولا يجوز أن تُسقط التحديث.
    """
    try:
        data = fmp_client.get_shares_float_all()
    except Exception:  # noqa: BLE001
        return None
    if data is None:  # فشل الجلب (لا «لا-بيانات») — نُميّزه بـ None
        return None
    universe = set(UNIVERSE)
    out = {}
    for row in data:
        sym = row.get("symbol")
        if sym not in universe:
            continue
        out[sym] = {
            "float_shares": row.get("floatShares"),
            "free_float_pct": row.get("freeFloat"),
        }
    return out


def _upcoming_earnings():
    """خريطة {رمز: تاريخ أقرب إعلان أرباح قادم} لأسهم UNIVERSE — طلب FMP واحد فقط.

    نُميّز الفشل عن النجاح صراحةً:
    - فشل الجلب → None (المتصل يُبقي مواعيد الأرباح السابقة بدل مسحها).
    - نجاح الجلب → خريطة (قد تكون فارغة {} = لا أرباح قادمة لأسهمنا في النافذة،
      وهي حالة سليمة تُمسح عندها المواعيد القديمة المنتهية).
    التنبيه كماليّ ولا يجوز أن يُسقط التحديث.
    """
    today = datetime.now(timezone.utc).date()
    to = today + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)
    try:
        data = fmp_client.get_earnings_calendar(today.isoformat(), to.isoformat())
    except Exception:  # noqa: BLE001
        return None
    if data is None:  # فشل الجلب (لا «لا-أرباح») — نُميّزه بـ None
        return None
    universe = set(UNIVERSE)
    out = {}
    for row in data:
        sym = row.get("symbol")
        raw = row.get("date")
        if sym not in universe or not raw:
            continue
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d < today:
            continue  # تجاهل المواعيد الماضية
        if sym not in out or d < out[sym]:
            out[sym] = d  # نُبقي الأقرب قادماً
    return out


def _period_return(closes_old_to_new, sessions):
    """عائد السهم عبر آخر `sessions` جلسة (%). closes مرتّبة من الأقدم للأحدث.

    يُرجع None لو البيانات غير كافية أو السعر القديم صفر (None ≠ 0).
    """
    if not closes_old_to_new or len(closes_old_to_new) < sessions + 1:
        return None
    old = closes_old_to_new[-(sessions + 1)]
    new = closes_old_to_new[-1]
    if not old:
        return None
    return (new - old) / old * 100.0


def _build_record(ticker):
    """يبني سجلّ ماسح لسهم: اسم، قطاع، سعر، قيمة سوقية، Piotroski، Catalyst.

    يُرجع dict أو None لو تعذّر جلب الأساسيات.
    """
    quote = fmp_client.get_quote(ticker)
    profile = fmp_client.get_profile(ticker)
    financials = fmp_client.get_financials(ticker)

    # financials قاموس {income, balance, cashflow}؛ قد تكون كلها None عند انتهاء حد الـ API.
    # لا نخزّن سجلّاً فارغاً (None ≠ 0): نتخطّى السهم لو لا سعر ولا أي قائمة مالية حقيقية.
    has_financials = bool(financials and any(financials.values()))
    if not quote and not has_financials:
        return None

    catalyst = scoring.catalyst_score(financials)

    # مؤشرات فنية للكرت (جلب تاريخي إضافي؛ لا يكسر السجلّ لو فشل)
    # FMP يُرجع كل التاريخ المتاح (~5 سنوات) في طلب واحد؛ نأخذ آخر 250 يوماً لبقية المؤشرات
    # (كما كان) ونمرّر التاريخ الكامل للفريمات الثلاثة (يحتاجه الشهري للقمم والقيعان) — بلا طلب إضافي.
    full_candles = None  # يبقى None لو فشل جلب التاريخ (فشل/قاطع FMP) — إشارة «تحليل ناقص» أدناه
    try:
        full_candles = fmp_client.get_historical_prices(ticker, limit=5000)
        candles = full_candles[:250] if full_candles else None
        tech = indicators.build_indicators(candles)
        flow = indicators.money_flow(candles)  # تدفق السيولة — من نفس الشموع، بلا استدعاء إضافي
        reversal = indicators.reversal_pattern(candles)  # شمعة انعكاس على آخر جلسة (تنبيه معرفي)
        structure = indicators.market_structure(candles)  # قراءة هيكل السوق (BOS/CHOCH/إعادة اختبار)
        frames = indicators.multi_timeframe(full_candles, daily_structure=structure)  # الفريمات الثلاثة بالقمم والقيعان — اليومي مطابق لهيكل السوق
        squeeze_bo = indicators.squeeze_breakout(candles)  # استراتيجية الانفجار الوشيك
        closes = [c["close"] for c in reversed(candles or []) if c.get("close") is not None]
        gc = indicators.golden_cross(closes)  # التقاطع الذهبي SMA50/SMA200
        pullback = indicators.trend_pullback(candles)  # ارتداد الترند (شراء الانخفاض)
        atr_val = indicators.atr(candles)  # تذبذب السهم — لمستويات الدخول/الوقف/الهدف بالتنبيهات
        brk = scoring.break_status(candles)  # اختراق/كسر مؤكّد بالحجم
        sustained = indicators.sustained_breakout(candles)  # اختراق مستمر (يواصل صعوده بثبات)
        atr_plan = scoring.atr_trade_plan(quote.get("price") if quote else None, candles)  # دخول/وقف/هدف + الصفقة الرابحة
        near_res = indicators.resistance_warning(candles)  # قرب المقاومة (قاعدة الانتظار — لخطة التداول)
        fib = indicators.fibonacci_levels(candles)  # مستويات فيبوناتشي (تغذّي المؤشر النهائي)
        vprofile = indicators.volume_profile(candles)  # البروفايل الحجمي و POC (تغذّي المؤشر النهائي)
        mom_63d = _period_return(closes, MOMENTUM_SESSIONS)  # زخم ~3 أشهر (للقوة النسبية مقابل السوق)
        recent_gain = _period_return(closes, RECENT_SESSIONS)  # صعود آخر أسبوعين (لفلتر "لسا ما صعد")
        _save_price_history(ticker, candles)  # نفس البيانات المجلوبة أصلاً — بلا استدعاء API إضافي
    except Exception:  # noqa: BLE001
        tech = []
        flow = None
        reversal = None
        structure = None
        frames = None
        squeeze_bo = False
        gc = None
        pullback = False
        atr_val = None
        brk = None
        sustained = None
        atr_plan = None
        near_res = None
        fib = None
        vprofile = None
        mom_63d = None
        recent_gain = None

    # مقاييس مالية للمقارنة (نفس حساب analysis.build_quick_summary) — تُخزَّن ليقرأها جدول
    # المقارنة من الكاش مباشرة بلا أي طلب FMP إضافي (البيانات المالية مجلوبة أصلاً أعلاه).
    _inc = financials.get("income") if financials else None
    _bal = financials.get("balance") if financials else None
    _ni = _inc[0].get("netIncome") if _inc else None
    _rev = _inc[0].get("revenue") if _inc else None
    _gross = _inc[0].get("grossProfit") if _inc else None
    _op = _inc[0].get("operatingIncome") if _inc else None
    _eps = _inc[0].get("eps") if _inc else None
    _assets = _bal[0].get("totalAssets") if _bal else None
    _equity = _bal[0].get("totalStockholdersEquity") if _bal else None
    _price = quote.get("price") if quote else None
    metrics = {
        # ROE: حقوق ملكية سالبة + خسارة تعطي نسبة موجبة خادعة → لا نعرضها (حرس equity>0)
        "roe": (scoring._safe_div(_ni, _equity) * 100.0
                if ((_equity or 0) > 0 and scoring._safe_div(_ni, _equity) is not None) else None),
        "roa": (None if scoring._safe_div(_ni, _assets) is None else scoring._safe_div(_ni, _assets) * 100.0),
        "op_margin": (None if scoring._safe_div(_op, _rev) is None else scoring._safe_div(_op, _rev) * 100.0),
        "gross_margin": (None if scoring._safe_div(_gross, _rev) is None else scoring._safe_div(_gross, _rev) * 100.0),
        "pe": scoring._safe_div(_price, _eps) if _eps not in (None, 0) else None,
    }

    # اكتمال التحليل: يجب أن تكتمل **كل** بيانات FMP الأساسية — السعر (quote) + الملف
    # التعريفي (profile) + القوائم المالية الثلاث كاملة (financials_complete، لا any) +
    # تاريخ الأسعار (full_candles) ومنه المؤشرات (tech). لو نقص أيّها (فشل profile/إحدى
    # القوائم أو توقّف قاطع FMP وسط التحليل) نعدّه ناقصاً → لا يستبدل سجلاً سليماً ولا
    # يُعلَّم محدثاً لليوم (يُقرأ ويُزال في refresh_cache فلا يُخزَّن ضمن السجل).
    _complete = (
        (quote is not None and quote.get("price") is not None)
        and profile is not None
        and fmp_client.financials_complete(financials)   # القوائم الثلاث كاملة (لا any)
        and bool(full_candles) and bool(tech)
    )

    return {
        "ticker": ticker,
        "_complete": _complete,
        "name": (quote.get("name") if quote else None) or (profile.get("name") if profile else None),
        "sector": profile.get("sector") if profile else None,
        "price": quote.get("price") if quote else None,
        "change_percent": quote.get("change_percent") if quote else None,  # التغيّر اليومي %
        "market_cap": quote.get("market_cap") if quote else None,
        "piotroski": scoring.piotroski_score(financials)["score"],
        "catalyst": catalyst["score"],
        "metrics": metrics,
        "indicators": tech,
        "money_flow": flow,
        "reversal": reversal,
        "structure": structure,
        "frames": frames,
        "squeeze_breakout": squeeze_bo,
        "golden_cross": (gc or {}).get("cross"),
        "trend_pullback": pullback,
        "atr": atr_val,
        "atr_plan": atr_plan,
        "near_resistance": near_res,
        "fibonacci": fib,
        "volume_profile": vprofile,
        "break_status": brk,
        "sustained": sustained,
        "mom_63d": mom_63d,
        "recent_gain": recent_gain,
    }


# نافذة منع تكرار الإشارة: لا تُسجَّل إشارة جديدة لنفس (السهم، النوع) خلال هذه المدة.
# الإشارة الأولى تبقى المرجع الذي يُقاس منه العائد — لو تكرّرت يومياً لعلِق العائد على 0%.
SIGNAL_COOLDOWN_DAYS = 30


def is_golden(record):
    """🥇 هل يستوفي السجل شروط الإشارة الذهبية الثلاثة مجتمعة؟

    1. جودة مالية عالية: Piotroski >= 8
    2. سيولة داخلة: تدفق الأموال بحالة "تجميع"
    3. اختراق فني: إغلاق فوق أعلى 20 يوماً (شارة "اختراق" الصاعدة)
    اجتماعها نادر — لذلك تُعد أقوى إشارات المنصة.
    """
    quality_ok = record.get("piotroski") is not None and record["piotroski"] >= PIOTROSKI_SIGNAL_MIN
    flow = record.get("money_flow")
    flow_ok = bool(flow and flow.get("status") == "bull")
    breakout_ok = any(
        b.get("label") == "اختراق" and b.get("status") == "bull"
        for b in (record.get("indicators") or [])
    )
    return quality_ok and flow_ok and breakout_ok


def _record_signal(ticker, signal_type, price, atr=None, earnings_days=None):
    """يسجّل إشارة لأول تأهّل فقط — لا تتجدد إلا بعد غياب SIGNAL_COOLDOWN_DAYS.

    None ≠ 0 : price قد يكون None (سعر غير متوفّر) ويُخزّن كذلك دون تلفيق.
    atr (اختياري): تذبذب السهم — يُمرَّر للتنبيه لحساب مستويات الدخول/الوقف/الهدف.
    earnings_days (اختياري): أيام لموعد الأرباح — لإضافة تحذير تذبذب في التنبيه.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_COOLDOWN_DAYS)
    exists = (
        Signal.query
        .filter(Signal.ticker == ticker, Signal.signal_type == signal_type,
                Signal.triggered_at >= cutoff)
        .first()
    )
    if exists:
        return
    db.session.add(Signal(ticker=ticker, signal_type=signal_type, price_at_signal=price))
    # تنبيه تلغرام (اختياري — خامل بلا إعداد, وفشله لا يؤثر على التحديث)
    telegram_client.notify_signal(ticker, signal_type, price, atr=atr, earnings_days=earnings_days)


def notify_new_prelaunch(min_strategies=3):
    """يُنبّه (تلغرام) عن الأسهم التي دخلت حديثاً قائمة «الاستعداد للانطلاق».

    يسجّل إشارة "prelaunch_ready" لكل مرشّح جديد (منع تكرار SIGNAL_COOLDOWN_DAYS)
    ويرسل تنبيهاً. يُستدعى بعد التحديث الليلي داخل app_context. يُرجع عدد التنبيهات الجديدة.
    """
    candidates = early_launch_candidates(min_strategies=min_strategies)
    if not candidates:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_COOLDOWN_DAYS)
    fired = 0
    for c in candidates:
        ticker = c["ticker"]
        exists = (
            Signal.query
            .filter(Signal.ticker == ticker,
                    Signal.signal_type == "prelaunch_ready",
                    Signal.triggered_at >= cutoff)
            .first()
        )
        if exists:
            continue  # سبق التنبيه عنه ضمن نافذة منع التكرار
        db.session.add(Signal(ticker=ticker, signal_type="prelaunch_ready",
                              price_at_signal=c.get("price")))
        telegram_client.notify_signal(ticker, "prelaunch_ready", c.get("price"))
        fired += 1
    if fired:
        db.session.commit()
    return fired


def dedupe_signals():
    """تنظيف الإشارات المكررة: يُبقي الأقدم لكل (سهم، نوع) ويحذف الأحدث المكررة.

    آمنة الاستدعاء دائماً (idempotent) — تُشغَّل عند بدء التطبيق لتصحيح المكررات
    التي خلّفتها النسخة القديمة (كانت تسجّل إشارة كل يوم لنفس السهم).
    يُرجع عدد الصفوف المحذوفة.
    """
    sigs = Signal.query.order_by(Signal.triggered_at.asc()).all()
    seen = set()
    removed = 0
    for s in sigs:
        key = (s.ticker, s.signal_type)
        if key in seen:
            db.session.delete(s)
            removed += 1
        else:
            seen.add(key)
    if removed:
        db.session.commit()
        print(f"[screener] حُذفت {removed} إشارة مكررة (أُبقي الأقدم لكل سهم/نوع)")
    return removed


def _save_price_history(ticker, candles, days=60):
    """يحفظ إغلاقات آخر `days` يوماً في price_point — بيانات حقيقية من FMP مجلوبة أصلاً للمؤشرات.

    upsert عبر db.session.merge (المفتاح مركّب ticker+date)؛ يبني مسار سعري حقيقي
    يكبر كل يوم تحديث، ويُستخدم لاحقاً لرسم مصغّر (sparkline) بلا أي استدعاء API إضافي.
    """
    for c in (candles or [])[:days]:
        raw_date = c.get("date")
        close = c.get("close")
        if not raw_date or close is None:
            continue
        try:
            day = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        db.session.merge(PricePoint(ticker=ticker, date=day, price=close))


def check_price_alerts():
    """يفحص التنبيهات السعرية النشطة مقابل آخر الأسعار (من الكاش — بلا استدعاء API).

    عند تحقّق الشرط: يرسل رسالة تلغرام ثم يطفئ التنبيه (مرة واحدة). يُرجع عدد ما أُطلق.
    يُستدعى بعد التحديث الليلي داخل app_context. None ≠ 0: سهم بلا سعر لا يُحكم عليه.
    """
    from models import PriceAlert  # استيراد محلي يتجنب دورة استيراد
    alerts = PriceAlert.query.filter_by(active=True).all()
    if not alerts:
        return 0
    records, _ = load_records()
    price_by = {r["ticker"]: r.get("price") for r in records}
    fired = 0
    now = datetime.now(timezone.utc)
    for a in alerts:
        price = price_by.get(a.ticker)
        if price is None:
            continue  # لا سعر متوفّر ⇒ لا حكم
        hit = ((a.direction == "below" and price <= a.target_price)
               or (a.direction == "above" and price >= a.target_price))
        if not hit:
            continue
        # لا نطفئ التنبيه إلا إذا نجح إرسال تلغرام فعلاً — وإلا يبقى نشطاً ليُعاد المحاولة
        # في الفحص التالي (فلا يفقد المستخدم تنبيهه عند فشل إرسال عابر).
        if not telegram_client.notify_price_alert(a.ticker, a.direction, a.target_price, price):
            continue
        a.active = False
        a.triggered_at = now
        fired += 1
    if fired:
        db.session.commit()
    return fired


# رمز مؤشر السوق للمقارنة المعيارية (متاح بباقة FMP المجانية — مُختبر)
MARKET_BENCHMARK = "SPY"

# إشارات هبوطية (تحذير بيع/تفادٍ) — يُقاس أداؤها معكوساً: نزول السهم بعدها = نجاح.
BEARISH_SIGNALS = {"breakdown_confirmed"}

# الحدّ الأدنى لعمر الإشارة (أيام) حتى تدخل الإحصائيات — الأصغر «لم تنضج» (لم يمرّ وقت كافٍ للحكم).
# 10 أيام: حكم أنضج من 3 (3 أيام ضجيج قصير المدى لا يقيس هل الإشارة اشتغلت فعلاً).
MATURE_MIN_DAYS = 10
# الحدّ الأدنى لعدد إشارات النوع حتى نعرض نسبته (أقل = عيّنة صغيرة مضلّلة، «100% على 2» بلا دلالة).
MIN_TYPE_SAMPLE = 5


def _benchmark_return(sessions):
    """عائد مؤشر السوق (SPY) عبر آخر `sessions` جلسة (%) من price_point.

    يُرجع None لو تاريخ المؤشر غير كافٍ (لا نقارن بلا مرجع موثوق).
    """
    rows = (
        PricePoint.query
        .filter_by(ticker=MARKET_BENCHMARK)
        .order_by(PricePoint.date.asc())
        .all()
    )
    prices = [r.price for r in rows if r.price is not None]
    return _period_return(prices, sessions)


def _refresh_spy_history(today):
    """يحفظ أسعار مؤشر السوق (SPY) اليومية في price_point — لمقارنة أداء الإشارات بالسوق.

    طلب FMP واحد يومياً فقط (يتخطى لو تحدّث اليوم). فشله لا يؤثر على تحديث الأسهم.
    """
    try:
        has_today = PricePoint.query.filter_by(ticker=MARKET_BENCHMARK, date=today).first()
        if has_today:
            return
        candles = fmp_client.get_historical_prices(MARKET_BENCHMARK, limit=120)
        if candles:
            _save_price_history(MARKET_BENCHMARK, candles, days=120)
            db.session.commit()
            print(f"[screener] حُدّث تاريخ مؤشر السوق ({MARKET_BENCHMARK})")
    except Exception as e:  # noqa: BLE001
        print(f"[screener] تعذّر تحديث مؤشر السوق: {e}")
        db.session.rollback()


# تكلفة تحليل سهم واحد بطلبات FMP (quote + profile + 3 قوائم مالية + تاريخ الأسعار).
# نستخدمها لضمان كفاية الميزانية قبل بدء تحليل سهم، فلا يتوقّف قاطع FMP وسط التحليل.
_STOCK_FMP_COST = 6


def refresh_cache(time_budget=20):
    """يحدّث كاش الماسح على دفعات ضمن حدّ زمني آمن، ويولّد إشارات للأسهم القوية.

    - حدّ زمني (time_budget ثانية) يضمن رجوع الطلب قبل مهلة الخادم (فلا خطأ 500).
    - يتخطّى الأسهم المحدّثة اليوم بالفعل → الضغطات المتتالية تُكمل الباقي بلا إعادة.
    - يُحفظ كل سهم على حدة، وفشل سهم لا يُسقط الباقي.
    يُرجع عدد الأسهم المحدّثة في هذه الدفعة.
    """
    start = time.monotonic()
    today = datetime.now(timezone.utc).date()
    updated = 0
    _refresh_spy_history(today)  # مؤشر السوق للمقارنة (طلب واحد يومياً، يتخطى لو محدث)
    spy_mom = _benchmark_return(MOMENTUM_SESSIONS)  # زخم السوق لنفس الفترة (يُحسب مرة واحدة)
    earnings_map = _upcoming_earnings()  # مواعيد الأرباح القادمة (طلب FMP واحد لكل الأسهم)
    float_map = _shares_float_map()  # الأسهم الحرة (طلب FMP واحد لكل الأسهم)
    # None = فشل الجلب الجماعي: عندها لا نُسقط القيم السليمة السابقة من السجل بل نحملها
    # كما هي (تُعاد بالجلب الناجح لاحقاً). أمّا الخريطة الفارغة {} فهي «نجاح بلا نتائج»
    # (لا أرباح قادمة/لا بيانات حرة) وعندها نُحدّث فعلاً (تُمسح المواعيد المنتهية).
    earnings_ok = earnings_map is not None
    float_ok = float_map is not None
    for ticker in UNIVERSE:
        if time.monotonic() - start > time_budget:
            break  # انتهى الحدّ الزمني — نرجع، وضغطة أخرى تُكمل الباقي

        key = _PREFIX + ticker
        existing = db.session.get(StockCache, key)
        # تخطّي ما حُدّث اليوم (إكمال على دفعات دون إعادة استهلاك الـ API)
        if existing and existing.updated_at and existing.updated_at.date() == today:
            continue

        # حجز ذرّي لميزانية العملية كاملة (6 طلبات) قبل بدء التحليل — يمنع بدء تحليلين
        # متزامنين بميزانية تبدو كافية ثم توقّف أحدهما وسط التحليل. لو لم تتّسع الميزانية
        # للعملية كاملة نتوقّف (تبقى بقية الأسهم بسجلّها السليم لتُحدَّث لاحقاً).
        if not fmp_client.reserve_operation(_STOCK_FMP_COST):
            print(f"[screener] توقّف: الميزانية لا تتّسع لتحليل سهم كامل ({_STOCK_FMP_COST} طلبات) — "
                  f"تُركت بقية الأسهم بسجلّها السليم")
            break

        try:
            record = _build_record(ticker)
            if not record:
                continue
            # تحليل ناقص (بيانات FMP جزئية): لا نستبدل السجل السليم ولا نعلّمه محدثاً لليوم
            # (يبقى آخر سجل سليم، ويُعاد بناؤه في دفعة/ليلة لاحقة).
            if not record.pop("_complete", True):
                print(f"[screener] تحليل {ticker} ناقص — أبقينا آخر سجل سليم ولم نعلّمه محدثاً")
                continue

            # القوة النسبية = تفوّق زخم السهم على زخم السوق عن نفس الفترة (None ≠ 0)
            # نضبط المفتاح دائماً (None لو تعذّر الحساب) حتى يبقى مخطّط السجل ثابتاً ولا
            # يكسر أي مستهلك يقرأه مباشرةً (مثل صفحة السهم).
            if record.get("mom_63d") is not None and spy_mom is not None:
                record["rel_strength"] = record["mom_63d"] - spy_mom
            else:
                record["rel_strength"] = None

            # موعد الأرباح القادم (لو ضمن نافذة التنبيه) — تحذير من التذبذب المرتفع.
            # عند نجاح الجلب (earnings_ok) نضبط الحقول من الجديد؛ الغياب هنا = لا موعد قادم.
            ed = earnings_map.get(ticker) if earnings_ok else None
            if ed:
                record["earnings_date"] = ed.isoformat()
                record["days_to_earnings"] = (ed - today).days

            # الأسهم الحرة (float) — كلما قلّت زاد احتمال الحركة السريعة
            fl = float_map.get(ticker) if float_ok else None
            if fl:
                record["float_shares"] = fl.get("float_shares")
                record["free_float_pct"] = fl.get("free_float_pct")

            # عند فشل الجلب الجماعي (خريطة فارغة) نحمل القيم السليمة السابقة بدل مسحها.
            if (not earnings_ok or not float_ok) and existing:
                try:
                    _old = json.loads(existing.data_json)
                except (ValueError, TypeError):
                    _old = {}
                if not earnings_ok and _old.get("earnings_date"):
                    record["earnings_date"] = _old["earnings_date"]
                    try:
                        record["days_to_earnings"] = (date_cls.fromisoformat(_old["earnings_date"]) - today).days
                    except (ValueError, TypeError):
                        record["days_to_earnings"] = _old.get("days_to_earnings")
                if not float_ok:
                    for _k in ("float_shares", "free_float_pct"):
                        if _old.get(_k) is not None:
                            record[_k] = _old[_k]

            row = existing
            payload = json.dumps(record, ensure_ascii=False)
            now = datetime.now(timezone.utc)
            if row:
                row.data_json = payload
                row.updated_at = now
            else:
                db.session.add(StockCache(ticker=key, data_json=payload, updated_at=now))

            # توليد إشارات تعليمية عند تجاوز العتبات
            sig_price, sig_atr = record.get("price"), record.get("atr")
            sig_earn = record.get("days_to_earnings")
            if record.get("piotroski") is not None and record["piotroski"] >= PIOTROSKI_SIGNAL_MIN:
                _record_signal(ticker, "piotroski_strong", sig_price, atr=sig_atr, earnings_days=sig_earn)
            if record.get("catalyst") is not None and record["catalyst"] >= CATALYST_SIGNAL_MIN:
                _record_signal(ticker, "catalyst_strong", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🥇 الإشارة الذهبية: 3 عوامل مجتمعة (نادرة) — جودة عالية + سيولة داخلة + اختراق فني
            if is_golden(record):
                _record_signal(ticker, "golden", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 💣 الانفجار الوشيك: انضغاط بولينجر + اختراق + حجم مرتفع
            if record.get("squeeze_breakout"):
                _record_signal(ticker, "squeeze_breakout", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🌟 التقاطع الذهبي: SMA50 قطع SMA200 صعوداً (اتجاه صاعد طويل المدى)
            if record.get("golden_cross") == "golden":
                _record_signal(ticker, "golden_cross", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🎯 ارتداد الترند: ترند صاعد + تراجع لمس EMA20 + بدء ارتداد (شراء الانخفاض)
            if record.get("trend_pullback"):
                _record_signal(ticker, "trend_pullback", sig_price, atr=sig_atr, earnings_days=sig_earn)
            # 🚀/⚠️ اختراق أو كسر مؤكّد بحجم عالٍ (إغلاق تجاوز نطاق 20 يوماً + حجم مرتفع)
            brk = record.get("break_status")
            if brk and brk.get("confirmed"):
                if brk.get("dir") == "breakout":
                    _record_signal(ticker, "breakout_confirmed", sig_price, atr=sig_atr, earnings_days=sig_earn)
                elif brk.get("dir") == "breakdown":
                    _record_signal(ticker, "breakdown_confirmed", sig_price)

            db.session.commit()  # حفظ هذا السهم مباشرة
            updated += 1
        except Exception as e:  # noqa: BLE001 — سهم واحد لا يجب أن يُسقط كل التحديث
            print(f"[screener] تعذّر تحديث {ticker}: {e}")
            db.session.rollback()
            continue
        finally:
            fmp_client.release_operation()  # يُعيد أي حجز غير مستهلَك (نجاح/تخطٍّ/فشل)

    return updated


def universe_fresh_today():
    """يُرجع مجموعة رموز UNIVERSE التي كاشها محدَّث فعلاً اليوم (بيانات طازجة).

    يعتمد على وجود سجل ماسح بمفتاح screen:TICKER وتاريخ تحديثه = تاريخ اليوم (UTC).
    يستعمله المجدول ليتأكّد من اكتمال التحديث الليلي لكل الأسهم (لا يكتفي بعدّاد الدفعات).
    """
    today = datetime.now(timezone.utc).date()
    fresh = set()
    for ticker in UNIVERSE:
        rec = db.session.get(StockCache, _PREFIX + ticker)
        if rec and rec.updated_at and rec.updated_at.date() == today:
            fresh.add(ticker)
    return fresh


def recent_signals(limit=12):
    """يُرجع أحدث الإشارات (الأحدث أولاً) للعرض في الواجهة."""
    return Signal.query.order_by(Signal.triggered_at.desc()).limit(limit).all()


def signals_performance():
    """سجل الأداء الكامل: كل إشارة تاريخية + عائدها منذ صدورها حتى الآن.

    يُرجع (قائمة الصفوف، إحصائيات إجمالية، إحصائيات لكل نوع إشارة).
    العائد يُحسب فقط لو توفّر السعران (None ≠ 0). لا استدعاءات API (أسعار من الكاش).
    """
    sigs = Signal.query.order_by(Signal.triggered_at.desc()).all()
    records, _ = load_records()
    price_by_ticker = {r["ticker"]: r.get("price") for r in records}
    name_by_ticker = {r["ticker"]: r.get("name") for r in records}

    # أسعار مؤشر السوق (SPY) من الكاش — لمقارنة كل إشارة بأداء السوق عن نفس الفترة
    spy_rows = PricePoint.query.filter_by(ticker=MARKET_BENCHMARK).all()
    spy_by_date = {p.date: p.price for p in spy_rows if p.price is not None}
    spy_last = spy_by_date[max(spy_by_date)] if spy_by_date else None

    # ⏱️ SPY لحظي: لو توفّر سعر SPY اللحظي (يحدّثه شبه لايف كل 5د في AppSetting) وكان
    # تاريخه أحدث من آخر إغلاق مخزّن، نستعمله كنقطة «الآن» ليطابق توقيت الأسعار اللحظية
    # للأسهم — فيزول انزياح الألفا (سهم لحظي مقابل SPY إغلاق أمس). الإغلاق الرسمي يفوز
    # بمجرّد توفّره (تاريخه ≥ تاريخ اللحظي). أي خطأ: نبقى على الإغلاق المخزّن.
    try:
        from models import AppSetting
        srow = db.session.get(AppSetting, "spy_live")
        if srow and srow.value:
            live = json.loads(srow.value)
            if live.get("price") and live.get("at"):
                live_date = datetime.fromisoformat(live["at"]).date()
                last_close = max(spy_by_date) if spy_by_date else None
                if last_close is None or live_date > last_close:
                    spy_last = live["price"]
    except Exception:  # noqa: BLE001
        pass

    def _spy_on(day):
        """سعر المؤشر في يومٍ ما (أو أقرب يوم تداول سابق خلال أسبوع). None لو غير متوفر."""
        for back in range(8):
            p = spy_by_date.get(day - timedelta(days=back))
            if p is not None:
                return p
        return None

    # لتفادي تضخيم الإحصائيات: الإشارة نفسها تتكرّر كل SIGNAL_COOLDOWN_DAYS يوماً،
    # فعدّ كل تكرار كعيّنة مستقلّة يحيّز العيّنة (سهم واحد يُثقل النوع). نحتسب في
    # الإحصائيات **أقدم إشارة فقط لكل (سهم + نوع)** — والباقي يبقى معروضاً بالجدول.
    # sigs مرتّبة الأحدث أولاً، فالأقدم يُكتب أخيراً في القاموس فيبقى هو المعتمد.
    _first_sig_id = {}
    for s in sigs:
        _first_sig_id[(s.ticker, s.signal_type)] = s.id
    counted_ids = set(_first_sig_id.values())

    now = datetime.now(timezone.utc)
    rows = []
    all_returns = []
    alphas = []
    by_type = {}  # signal_type -> list of returns
    for s in sigs:
        triggered_at = s.triggered_at
        if triggered_at.tzinfo is None:  # SQLite محلياً بلا tzinfo
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        days = (now - triggered_at).days
        mature = days >= MATURE_MIN_DAYS  # نضجت؟ (مرّ وقت كافٍ للحكم) — الطازجة لا تدخل الإحصائيات
        counted = s.id in counted_ids     # أقدم إشارة لنوعها على هذا السهم؟ (وحدها تدخل الإحصائيات)

        current = price_by_ticker.get(s.ticker)
        # الإشارات الهابطة (كسر مؤكّد) تُقاس معكوسة: نزول السهم بعدها = نجاح (فائدة تحذير البيع/التفادي).
        # sign = -1 يقلب العائد فيصير النزول عائداً موجباً، والصعود سلبياً — كما يحصل للصاعدة بالعكس.
        sign = -1 if s.signal_type in BEARISH_SIGNALS else 1
        ret = None
        if current is not None and s.price_at_signal:
            ret = sign * (current - s.price_at_signal) / s.price_at_signal * 100.0
            if mature and counted:  # الطازجة أو المكرّرة تُعرض بالجدول لكن لا تدخل الإحصائيات
                all_returns.append(ret)
                by_type.setdefault(s.signal_type, []).append(ret)

        # عائد السوق عن نفس الفترة + الألفا (تفوق الإشارة على السوق)
        spy_ret = alpha = None
        if ret is not None and spy_last is not None:
            spy_start = _spy_on(triggered_at.date())
            if spy_start:
                spy_ret = (spy_last - spy_start) / spy_start * 100.0  # حركة السوق الفعلية (للعرض)
                # الألفا بنفس اتجاه الإشارة: شراء للصاعدة, تفادٍ/بيع للهابطة (نطرح مساهمة السوق بنفس الإشارة)
                alpha = ret - sign * spy_ret
                if mature and counted:
                    alphas.append(alpha)

        rows.append({
            "ticker": s.ticker,
            "name": name_by_ticker.get(s.ticker),
            "signal_type": s.signal_type,
            "date": s.triggered_at,
            "days": days,
            "mature": mature,
            "counted": counted,
            "price_at_signal": s.price_at_signal,
            "current": current,
            "return_pct": ret,
            "spy_return_pct": spy_ret,
            "alpha": alpha,
        })

    def _stats(returns):
        """إحصائيات لمجموعة عوائد — None لو لا عوائد قابلة للحساب."""
        if not returns:
            return None
        wins = sum(1 for r in returns if r > 0)
        return {
            "count": len(returns),
            "avg": sum(returns) / len(returns),
            "win_count": wins,
            "win_rate": wins / len(returns) * 100.0,
            "best": max(returns),
            "worst": min(returns),
        }

    overall = _stats(all_returns)
    if overall and alphas:
        overall["avg_alpha"] = sum(alphas) / len(alphas)
        overall["beat_market"] = sum(1 for a in alphas if a > 0)
        overall["alpha_count"] = len(alphas)
    type_stats = {t: _stats(rs) for t, rs in by_type.items()}

    # ترتيب «اختبار الأداء»: الناضجة أولاً بالأعلى عائداً (الأفضل ← الأسوأ، والمجهول العائد أسفلها)،
    # ثم الطازجة (لم تنضج بعد — لم تُختبر) في الأسفل بترتيبها الزمني (الأحدث أولاً).
    matured = [r for r in rows if r["mature"]]
    fresh = [r for r in rows if not r["mature"]]
    matured.sort(key=lambda r: (r["return_pct"] is not None,
                                r["return_pct"] if r["return_pct"] is not None else 0.0),
                 reverse=True)
    fresh.sort(key=lambda r: r["date"], reverse=True)
    rows = matured + fresh

    return rows, overall, type_stats


def launched_stocks(limit=6):
    """الأسهم التي صدرت لها إشارة + عائدها منذ الإشارة (لوحة "انطلقت بالفعل").

    العائد = (السعر الحالي المخزّن − السعر وقت الإشارة) / السعر وقت الإشارة.
    None ≠ 0 : يُحسب العائد فقط لو توفّر السعران. لا استدعاءات API (نقرأ من الكاش).
    يُرجع (قائمة، إحصائيات الأداء).
    """
    # نجيب دفعة أكبر من الإشارات لأن السهم الواحد قد يُسجَّل له أكثر من إشارة (Piotroski + Catalyst)،
    # ثم نُبقي أحدث إشارة فقط لكل سهم حتى تظهر لوحة "انطلقت" بأسهم متنوّعة لا مكرّرة.
    sigs = Signal.query.order_by(Signal.triggered_at.desc()).limit(limit * 4).all()
    records, _ = load_records()
    price_by_ticker = {r["ticker"]: r.get("price") for r in records}
    name_by_ticker = {r["ticker"]: r.get("name") for r in records}
    sector_by_ticker = {r["ticker"]: r.get("sector") for r in records}

    now = datetime.now(timezone.utc)
    rows = []
    returns = []
    days_list = []
    seen = set()
    for s in sigs:
        # الإشارات الهابطة (تحذير كسر) ليست «انطلاقاً» — تُستبعد من هذه اللوحة (تظهر في اختبار الأداء معكوسةً).
        if s.signal_type in BEARISH_SIGNALS:
            continue
        if s.ticker in seen:
            continue
        seen.add(s.ticker)
        current = price_by_ticker.get(s.ticker)
        ret = None
        if current is not None and s.price_at_signal:
            ret = (current - s.price_at_signal) / s.price_at_signal * 100.0
            returns.append((s.ticker, ret))
        triggered_at = s.triggered_at
        if triggered_at.tzinfo is None:  # SQLite محلياً لا يحفظ tzinfo رغم DateTime(timezone=True)
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        days_elapsed = (now - triggered_at).days
        days_list.append(days_elapsed)
        rows.append({
            "ticker": s.ticker,
            "name": name_by_ticker.get(s.ticker),
            "sector": sector_by_ticker.get(s.ticker),
            "signal_type": s.signal_type,
            "price_at_signal": s.price_at_signal,
            "current": current,
            "return_pct": ret,
            "date": s.triggered_at,
            "days_elapsed": days_elapsed,
        })
        if len(rows) >= limit:
            break

    # إحصائيات الأداء العام (من الإشارات القابلة للحساب فقط)
    stats = {
        "avg": None, "win_rate": None, "best": None, "best_ticker": None,
        "avg_days": None, "count": len(returns), "win_count": 0,
    }
    if returns:
        values = [r for _, r in returns]
        stats["avg"] = sum(values) / len(values)
        stats["win_count"] = sum(1 for r in values if r > 0)
        stats["win_rate"] = stats["win_count"] / len(values) * 100.0
        best_ticker, best_ret = max(returns, key=lambda x: x[1])
        stats["best"] = best_ret
        stats["best_ticker"] = best_ticker
    if days_list:
        stats["avg_days"] = sum(days_list) / len(days_list)
    return rows, stats


def refresh_prices_intraday():
    """تحديث «شبه لايف» خفيف للسعر الحالي فقط — من Finnhub /quote المجاني.

    يحدّث حقلَي price و change_percent (ووسم price_live_at) لكل أسهم UNIVERSE دون
    إعادة التحليل الثقيل (الفريمات/الدرجات/التاريخ تبقى من التحديث الليلي). كل سهم =
    طلب واحد؛ 32 طلباً < حد Finnhub المجاني 60/دقيقة. لا يمسّ updated_at (يبقى وقت
    التحليل الليلي). None ≠ 0: لو Finnhub لم يُرجع سعراً نُبقي القديم.
    يُرجع عدد الأسهم التي حُدّث سعرها.
    """
    updated = 0
    now = datetime.now(timezone.utc)
    for ticker in UNIVERSE:
        try:
            q = finnhub_client.get_quote(ticker)
        except Exception:  # noqa: BLE001 — لا نُسقط الدفعة بخطأ سهم واحد
            q = None
        if not q or not q.get("price"):
            continue
        row = StockCache.query.filter_by(ticker=_PREFIX + ticker).first()
        if not row:
            continue
        try:
            record = json.loads(row.data_json)
        except (ValueError, TypeError):
            continue
        record["price"] = q["price"]
        if q.get("change_percent") is not None:
            record["change_percent"] = q["change_percent"]
        record["price_live_at"] = now.isoformat()   # وسم آخر تحديث سعر «شبه لايف»
        row.data_json = json.dumps(record, ensure_ascii=False)
        updated += 1

    # ⏱️ SPY لحظياً أيضاً (نفس مصدر Finnhub المجاني، طلب واحد إضافي < حد 60/دقيقة):
    # يُخزَّن في AppSetting منفصل ('spy_live') حتى لا يلوّث سلسلة الإغلاقات اليومية في
    # price_point ولا يتعارض مع _refresh_spy_history. يستعمله «اختبار الأداء» كنقطة
    # «الآن» فيطابق توقيت الأسعار اللحظية للأسهم (يزيل انزياح الألفا).
    spy_stored = False
    try:
        sq = finnhub_client.get_quote(MARKET_BENCHMARK)
        if sq and sq.get("price"):
            from models import AppSetting
            payload = json.dumps({"price": sq["price"], "at": now.isoformat()})
            srow = db.session.get(AppSetting, "spy_live")
            if srow:
                srow.value = payload
            else:
                db.session.add(AppSetting(key="spy_live", value=payload))
            spy_stored = True
    except Exception:  # noqa: BLE001 — فشل SPY لا يُسقط تحديث أسعار الأسهم
        pass

    if updated or spy_stored:
        db.session.commit()
    return updated


def load_records():
    """يقرأ سجلّات الماسح المخزّنة. يُرجع (قائمة السجلّات، أحدث وقت تحديث أو None)."""
    rows = StockCache.query.filter(StockCache.ticker.like(_PREFIX + "%")).all()
    records = []
    latest = None
    for row in rows:
        try:
            records.append(json.loads(row.data_json))
        except (ValueError, TypeError):
            continue
        if latest is None or row.updated_at > latest:
            latest = row.updated_at
    return records, latest


def market_direction():
    """اتجاه السوق الأمريكي من مؤشر S&P 500 (SPY) — من price_point، بلا استدعاء API.

    يقارن سعر المؤشر بمتوسطيه المتحركين 20 و50 يوماً:
    - صاعد: السعر فوق متوسط 20 وهو فوق متوسط 50 (ترند صاعد واضح).
    - هابط: السعر تحت متوسط 20 وهو تحت متوسط 50 (ترند هابط).
    - عرضي: ما عدا ذلك (تذبذب بلا اتجاه حاسم).
    يُرجع dict {label, emoji, cls, change_20} أو None لو تاريخ المؤشر غير كافٍ.
    """
    rows = (
        PricePoint.query
        .filter_by(ticker=MARKET_BENCHMARK)
        .order_by(PricePoint.date.asc())
        .all()
    )
    prices = [r.price for r in rows if r.price is not None]
    if len(prices) < 55:
        return None
    price = prices[-1]
    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    change_20 = (prices[-1] - prices[-21]) / prices[-21] * 100.0 if prices[-21] else None
    if price > sma20 > sma50:
        label, emoji, cls = "صاعد", "🟢", "bull"
    elif price < sma20 < sma50:
        label, emoji, cls = "هابط", "🔴", "bear"
    else:
        label, emoji, cls = "عرضي", "⚪", "neutral"
    return {"label": label, "emoji": emoji, "cls": cls, "change_20": change_20}


def market_mood(records=None):
    """مزاج السوق العام: كم سهم صاعد/هابط/محايد من كامل العيّنة (من الكاش، بلا API).

    لكل سهم نوازن مؤشراته الفنية: عدد الإشارات الصاعدة مقابل الهابطة.
    - صاعد لو غلبت الصاعدة، هابط لو غلبت الهابطة، محايد لو تعادلا.
    ثم نحكم على السوق: إيجابي (≥60% صاعد)، سلبي (≤40%)، أو متوازن بينهما.
    يُرجع dict بالعدّات والنسبة وحكم عام، أو None لو لا بيانات مؤشرات بعد.
    """
    if records is None:
        records, _ = load_records()
    bull = bear = neutral = 0
    for r in records:
        inds = r.get("indicators") or []
        if not inds:
            continue  # None ≠ 0 : سهم بلا مؤشرات لا يُحسب (لا يُعدّ محايداً زوراً)
        up = sum(1 for i in inds if i.get("status") == "bull")
        down = sum(1 for i in inds if i.get("status") == "bear")
        if up > down:
            bull += 1
        elif down > up:
            bear += 1
        else:
            neutral += 1
    total = bull + bear + neutral
    if total == 0:
        return None
    bull_pct = bull / total * 100.0
    if bull_pct >= 60:
        label, emoji, cls = "إيجابي", "🟢", "bull"
    elif bull_pct <= 40:
        label, emoji, cls = "سلبي", "🔴", "bear"
    else:
        label, emoji, cls = "متوازن", "🟡", "neutral"
    return {
        "bull": bull, "bear": bear, "neutral": neutral, "total": total,
        "bull_pct": bull_pct, "label": label, "emoji": emoji, "cls": cls,
    }


def filter_records(records, piotroski_min=None, catalyst_min=None,
                   price_max=None, market_cap_min=None, sector=None,
                   recent_gain_max=None, float_max=None, min_measures=None,
                   break_dir=None, money_flow_bull=None, sustained_only=None):
    """يطبّق الفلاتر على السجلّات المخزّنة.

    None ≠ 0 : السجلّ الذي تكون قيمته المطلوبة None لا يجتاز فلتراً يحدّ تلك القيمة
    (لا نعامله كصفر).
    market_cap_min بالدولار (خام)، يُحوّل من المليارات في طبقة الـ route.
    recent_gain_max: "لسا ما صعد" — يستبعد ما قفز أكثر من هذا خلال آخر أسبوعين
    (recent_gain=None يُبقى: العائد غير محسوب بعد، لا نستبعد بلا يقين).
    float_max: أعلى عدد أسهم حرة (خام) — يعرض الأسهم قليلة الحرة (الأسرع حركة)؛
    float_shares=None يُستبعد لأن العتبة صريحة (لا حكم بلا بيانات float).
    break_dir: "breakout" أو "breakdown" — يُبقي فقط الأسهم ذات اختراق/كسر «مؤكّد» بحجم عالٍ.
    money_flow_bull: True — يُبقي فقط الأسهم ذات سيولة داخلة (تجميع).
    sustained_only: True — يُبقي فقط الأسهم ذات «اختراق مستمر» (يواصل صعوده بثبات).
    """
    out = []
    for r in records:
        if piotroski_min is not None:
            if r.get("piotroski") is None or r["piotroski"] < piotroski_min:
                continue
        if catalyst_min is not None:
            if r.get("catalyst") is None or r["catalyst"] < catalyst_min:
                continue
        if price_max is not None:
            if r.get("price") is None or r["price"] > price_max:
                continue
        if market_cap_min is not None:
            if r.get("market_cap") is None or r["market_cap"] < market_cap_min:
                continue
        if sector:
            if r.get("sector") != sector:
                continue
        if recent_gain_max is not None:
            rg = r.get("recent_gain")
            if rg is not None and rg > recent_gain_max:
                continue
        if float_max is not None:
            fs = r.get("float_shares")
            if fs is None or fs > float_max:
                continue
        if min_measures is not None:
            if measures_met(r) < min_measures:
                continue
        if break_dir is not None:
            bk = r.get("break_status") or {}
            if not (bk.get("dir") == break_dir and bk.get("confirmed")):
                continue
        if money_flow_bull:
            if (r.get("money_flow") or {}).get("status") != "bull":
                continue
        if sustained_only:
            if not (r.get("sustained") or {}).get("sustained"):
                continue
        out.append(r)
    # ترتيب تنازلي حسب Catalyst ثم Piotroski (None في الأسفل)
    out.sort(key=lambda r: (r.get("catalyst") or -1, r.get("piotroski") or -1), reverse=True)
    return out
