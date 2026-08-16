"""
tracking.py — تنظيم PHASE 5 المعتمد على قاعدة البيانات (DB orchestration).

يحوي:
- record_nightly_tracking(recs): معالجة كل سهم كمعاملة ذرّية واحدة (قراءة اللقطة السابقة →
  حساب الحالة الواعية بدورة الحياة → كشف/إنشاء الأحداث بحماية SAVEPOINT+UNIQUE →
  كتابة الحالة داخل StockCache → UPSERT لقطة اليوم → commit واحد).
- fill_outcomes(): ملء نتائج الأداء بآفاق جلسات تداول (1/5/10/20) من PricePoint، Close-to-Close،
  مع محاذاة SPY الزمنية. pending تبقى None (ليست Fail). لا live_price إطلاقاً.
- performance_summary(): تجميع الأداء للوحة «أداء الإشارات».
- build_change_summary(current, previous) + latest_changes(): «ماذا تغيّر اليوم؟».

مبادئ مقفلة: الأساس الزمني = baseline_date (candles[0]["date"] المخزّن في record["analysis_date"]).
أساس الأداء = performance_baseline_price (= record["analysis_close"] = إغلاق EOD). تغيّر السعر وحده
لا يُصنَّف «تحسّناً». lifecycle_id = 1 + عدد أحداث INVALIDATED السابقة. UNIQUE(ticker,lifecycle_id,state_code).
"""

import json
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from services import screener
from services.state import stock_state, TRACKED_EVENT_STATES
from models import (db, StockCache, StockSnapshot, StockStateEvent, StockStateOutcome,
                    PricePoint)

HORIZONS = (1, 5, 10, 20)  # جلسات تداول
_SPY = screener.MARKET_BENCHMARK


# ───────────────────────── أدوات دورة الحياة ─────────────────────────

def lifecycle_id(ticker):
    """معرّف دورة الحياة الحالية = 1 + عدد أحداث INVALIDATED السابقة للسهم (حتمي، من صفوف مُثبتة)."""
    n = StockStateEvent.query.filter_by(ticker=ticker, state_code="INVALIDATED").count()
    return 1 + n


def open_bullish(ticker, lid):
    """هل يوجد في الدورة الحالية حدث READY أو LAUNCHED مفتوح (لم يُغلق بـINVALIDATED)؟"""
    row = (db.session.query(StockStateEvent.id)
           .filter(StockStateEvent.ticker == ticker,
                   StockStateEvent.lifecycle_id == lid,
                   StockStateEvent.state_code.in_(("READY", "LAUNCHED")))
           .first())
    return row is not None


def _parse_date(s):
    """يحوّل 'YYYY-MM-DD...' إلى date، أو None عند التعذّر (لا يختلق تاريخاً)."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ───────────────────────── بناء صفوف اللقطة/الحدث ─────────────────────────

def _is_ready(record, tilt):
    tk = tilt.get("kind") if tilt else None
    c = record.get("catalyst")
    return bool(c is not None and c >= 80 and tk in ("neu", "pos1", "pos2"))


def _snapshot_row(record, ticker, today, code):
    tilt = screener.tech_tilt(record)
    algx = screener.algomatix_score(record)
    return StockSnapshot(
        ticker=ticker, snap_date=today,
        analysis_price=screener.analysis_price(record),
        catalyst=record.get("catalyst"),
        catalyst_complete=record.get("catalyst_complete"),
        piotroski=record.get("piotroski"),
        piotroski_computable=screener.piotroski_computable(record),
        measures_met=screener.measures_met(record),
        tech_tilt_kind=(tilt.get("kind") if tilt else None),
        algomatix_score=(algx.get("score") if algx else None),
        structure_status=(record.get("structure") or {}).get("status"),
        state_code=code,
        is_gem=screener.is_gem(record),
        is_ready=_is_ready(record, tilt),
        extra_json=None,
    )


def _event_row(record, ticker, code, lid, prev_code, today, baseline_date):
    tilt = screener.tech_tilt(record)
    algx = screener.algomatix_score(record)
    plan = record.get("atr_plan") or {}
    return StockStateEvent(
        ticker=ticker, lifecycle_id=lid, state_code=code, prev_state=prev_code,
        event_date=today, baseline_date=baseline_date,
        analysis_price=screener.analysis_price(record),          # تدقيق فقط
        performance_baseline_price=record.get("analysis_close"),  # أساس الأداء (EOD)
        algomatix_score=(algx.get("score") if algx else None),
        catalyst=record.get("catalyst"), piotroski=record.get("piotroski"),
        measures_met=screener.measures_met(record),
        tech_tilt_kind=(tilt.get("kind") if tilt else None),
        structure_status=(record.get("structure") or {}).get("status"),
        stop=plan.get("stop"), target=plan.get("target"),
    )


def _pending_event(record, ticker, code, lid, prev_code, today):
    """يُرجع صف الحدث المطلوب إنشاؤه (بلا إضافته للجلسة)، أو None لو لا حدث.

    حماية التكرار على مستوى المنطق = pre-check؛ وعلى مستوى القاعدة = UNIQUE (backstop في record_nightly).
    OLD-RECORD SAFETY: لا حدث بلا baseline موثوق (analysis_date + analysis_close) — لا تخمين، لا nearest-date.
    """
    if code not in TRACKED_EVENT_STATES:
        return None
    if record.get("analysis_close") is None:
        return None
    baseline_date = _parse_date(record.get("analysis_date"))
    if baseline_date is None:
        return None
    exists = (db.session.query(StockStateEvent.id)
              .filter_by(ticker=ticker, lifecycle_id=lid, state_code=code).first())
    if exists:
        return None
    return _event_row(record, ticker, code, lid, prev_code, today, baseline_date)


def _persist_state_into_cache(ticker, state):
    """يكتب الحالة المُحلّة داخل StockCache record (بلا لمس updated_at) — القوالب تقرأها بلا إعادة حساب."""
    key = screener._PREFIX + ticker
    row = db.session.get(StockCache, key)
    if not row:
        return
    try:
        data = json.loads(row.data_json)
    except (ValueError, TypeError):
        return
    data["state"] = state
    row.data_json = json.dumps(data, ensure_ascii=False)
    # عمداً: لا نغيّر row.updated_at (نضارة «اليوم» مربوطة بالتحليل الليلي — HIGH #3)


def record_nightly_tracking(recs, today=None):
    """المعالجة الليلية: لكل سهم معاملة ذرّية واحدة تُختم بـcommit واحد.

    ترتيب مقفل: قراءة اللقطة السابقة (snap_date < today) → حساب الحالة → كشف/إنشاء الأحداث →
    كتابة الحالة في StockCache → UPSERT لقطة اليوم → commit. فشل غير متوقع ⇒ rollback كامل لهذا السهم.
    today: يُحقن في الاختبارات لمحاكاة أيام متعددة (الإنتاج يستخدم date.today()).
    """
    today = today or date.today()
    res = {"snapshots": 0, "events": 0, "errors": 0}
    for rec in (recs or []):
        ticker = rec.get("ticker")
        if not ticker:
            continue
        try:
            prev = (StockSnapshot.query
                    .filter(StockSnapshot.ticker == ticker, StockSnapshot.snap_date < today)
                    .order_by(StockSnapshot.snap_date.desc()).first())
            prev_code = prev.state_code if prev else None
            lid = lifecycle_id(ticker)
            open_b = open_bullish(ticker, lid)
            state = stock_state(rec, context={"open_bullish": open_b})
            code = state["code"]
            ev = _pending_event(rec, ticker, code, lid, prev_code, today)
            # معاملة السهم الذرّية: حدث (إن لزم) + حالة StockCache + لقطة اليوم، بـcommit واحد.
            try:
                if ev is not None:
                    db.session.add(ev)
                _persist_state_into_cache(ticker, state)
                db.session.merge(_snapshot_row(rec, ticker, today, code))
                db.session.commit()
                if ev is not None:
                    res["events"] += 1
                res["snapshots"] += 1
            except IntegrityError:
                # سباق نادر: أُنشئ الحدث بين pre-check والcommit (retry/restart) — UNIQUE منعه.
                # نتراجع كاملاً ثم نعيد كتابة اللقطة+الحالة فقط (بلا حدث) فلا نخسر لقطة صحيحة.
                db.session.rollback()
                _persist_state_into_cache(ticker, state)
                db.session.merge(_snapshot_row(rec, ticker, today, code))
                db.session.commit()
                res["snapshots"] += 1
        except Exception as e:  # noqa: BLE001 — سهم واحد فقط يُتخطّى، لا يُجهض البقية
            db.session.rollback()
            res["errors"] += 1
            print(f"[tracking] تعذّر تتبّع {ticker}: {e}")
    return res


# ───────────────────────── نتائج الأداء (Close-to-Close) ─────────────────────────

def fill_outcomes():
    """يملأ نتائج الآفاق الناضجة من PricePoint (بلا استدعاء API، بلا live_price).

    exits = PricePoint(ticker) WHERE date > baseline_date ORDER BY date ASC.
    return_Nd = (exits[N-1] − performance_baseline_price)/performance_baseline_price ×100.
    SPY: نفس baseline_date ونفس exit_date_N. أفق غير ناضج أو SPY مفقود ⇒ pending/None (بلا استبدال).
    """
    filled = 0
    spy_rows = PricePoint.query.filter_by(ticker=_SPY).all()
    spy_map = {p.date: p.price for p in spy_rows if p.price is not None}
    events = StockStateEvent.query.all()
    for ev in events:
        base = ev.performance_baseline_price
        if not ev.baseline_date or base in (None, 0):
            continue
        exits = (PricePoint.query
                 .filter(PricePoint.ticker == ev.ticker,
                         PricePoint.date > ev.baseline_date,
                         PricePoint.price.isnot(None))
                 .order_by(PricePoint.date.asc()).all())
        spy_base = spy_map.get(ev.baseline_date)
        for h in HORIZONS:
            existing = db.session.get(StockStateOutcome, (ev.id, h))
            if existing and existing.return_pct is not None:
                continue  # نُضجت من قبل — لا نعيد الحساب
            if len(exits) < h:
                continue  # pending — لا نلوّث بجلسة مختلفة
            ep = exits[h - 1]
            close = ep.price
            ret = (close - base) / base * 100.0
            spy_exit = spy_map.get(ep.date)
            if spy_base not in (None, 0) and spy_exit is not None:
                spy_ret = (spy_exit - spy_base) / spy_base * 100.0
                alpha = ret - spy_ret
            else:
                spy_ret = None
                alpha = None
            db.session.merge(StockStateOutcome(
                event_id=ev.id, horizon_days=h, exit_date=ep.date,
                close_price=close, return_pct=ret,
                spy_return_pct=spy_ret, alpha_pct=alpha))
            filled += 1
    if filled:
        db.session.commit()
    return filled


def _stats(returns, alphas):
    if not returns:
        return None
    wins = sum(1 for r in returns if r > 0)
    return {
        "n": len(returns),
        "win_rate": wins / len(returns) * 100.0,
        "avg": sum(returns) / len(returns),
        "best": max(returns),
        "worst": min(returns),
        "avg_alpha": (sum(alphas) / len(alphas) if alphas else None),
    }


def performance_summary():
    """تجميع أداء الإشارات للوحة «أداء الإشارات». Primary Success = return_Nd>0 (alpha منفصل)."""
    by_h = {h: {"ret": [], "alpha": []} for h in HORIZONS}
    for o in StockStateOutcome.query.filter(StockStateOutcome.return_pct.isnot(None)).all():
        if o.horizon_days in by_h:
            by_h[o.horizon_days]["ret"].append(o.return_pct)
            if o.alpha_pct is not None:
                by_h[o.horizon_days]["alpha"].append(o.alpha_pct)
    horizons = {h: _stats(v["ret"], v["alpha"]) for h, v in by_h.items()}
    has_mature = any(horizons.get(h) for h in HORIZONS)
    return {
        "horizons": horizons,
        "has_mature": has_mature,
        "total_events": StockStateEvent.query.count(),
        "ready_count": StockStateEvent.query.filter_by(state_code="READY").count(),
        "launched_count": StockStateEvent.query.filter_by(state_code="LAUNCHED").count(),
        "invalidated_count": StockStateEvent.query.filter_by(state_code="INVALIDATED").count(),
    }


# ───────────────────────── «ماذا تغيّر اليوم؟» ─────────────────────────

_TILT_RANK = {"neg2": 0, "neg1": 1, "neu": 2, "pos1": 3, "pos2": 4}
_TILT_LABEL = {"neg2": "سلبي قوي", "neg1": "سلبي", "neu": "محايد", "pos1": "إيجابي", "pos2": "إيجابي قوي"}
_STRUCT_RANK = {"bear": 0, "neutral": 1, "bull": 2}
_STRUCT_LABEL = {"bear": "هابط", "neutral": "عرضي", "bull": "صاعد"}
_STATE_RANK = {"INVALIDATED": -2, "LOSING_MOMENTUM": -1, "WATCH": 0, "FORMING": 1,
               "NEAR_READY": 2, "READY": 3, "LAUNCHED": 4, "EXTENDED": 5}


def _snap_dict(row):
    if row is None:
        return None
    return {
        "snap_date": row.snap_date, "analysis_price": row.analysis_price,
        "catalyst": row.catalyst, "piotroski": row.piotroski,
        "measures_met": row.measures_met, "tech_tilt_kind": row.tech_tilt_kind,
        "algomatix_score": row.algomatix_score, "structure_status": row.structure_status,
        "state_code": row.state_code, "is_gem": row.is_gem, "is_ready": row.is_ready,
    }


def build_change_summary(current, previous):
    """يقارن لقطتين ويصنّف: تحسّن/تراجع/دون تغيّر جوهري. تغيّر السعر وحده لا يُحتسب تحسّناً.

    يقبل dict لكل لقطة (أو None). لا لقطة سابقة ⇒ رسالة صريحة بلا اختلاق مقارنة.
    """
    if not current:
        return {"has_previous": False, "classification": None, "changes": [],
                "note": "لا توجد لقطة حالية"}
    if not previous:
        return {"has_previous": False, "classification": None, "changes": [],
                "note": "لا يوجد تاريخ سابق كافٍ للمقارنة"}

    changes = []
    net = 0

    def num_change(key, label, fmt="{:.0f}"):
        nonlocal net
        a, b = previous.get(key), current.get(key)
        if a is None or b is None or a == b:
            return
        d = b - a
        changes.append({"key": key, "label": label,
                        "from": fmt.format(a), "to": fmt.format(b),
                        "delta": d, "dir": "up" if d > 0 else "down"})
        net += (1 if d > 0 else -1)

    num_change("catalyst", "النمو (Catalyst)")
    num_change("algomatix_score", "مؤشر Algomatix")
    num_change("measures_met", "قوة التأكيد")
    num_change("piotroski", "الجودة (Piotroski)")

    # الميل الفني (تحوّل نوعي)
    ta, tb = previous.get("tech_tilt_kind"), current.get("tech_tilt_kind")
    if ta != tb and ta in _TILT_RANK and tb in _TILT_RANK:
        d = _TILT_RANK[tb] - _TILT_RANK[ta]
        changes.append({"key": "tech_tilt", "label": "الميل الفني",
                        "from": _TILT_LABEL[ta], "to": _TILT_LABEL[tb],
                        "dir": "up" if d > 0 else "down"})
        net += (1 if d > 0 else -1)

    # هيكل السوق
    sa, sb = previous.get("structure_status"), current.get("structure_status")
    if sa != sb and sa in _STRUCT_RANK and sb in _STRUCT_RANK:
        d = _STRUCT_RANK[sb] - _STRUCT_RANK[sa]
        changes.append({"key": "structure", "label": "هيكل السوق",
                        "from": _STRUCT_LABEL[sa], "to": _STRUCT_LABEL[sb],
                        "dir": "up" if d > 0 else "down"})
        net += (1 if d > 0 else -1)

    # الجوهرة
    ga, gb = bool(previous.get("is_gem")), bool(current.get("is_gem"))
    if ga != gb:
        changes.append({"key": "gem", "label": "الجوهرة", "from": "لا" if not ga else "نعم",
                        "to": "نعم" if gb else "لا", "dir": "up" if gb else "down"})
        net += (1 if gb else -1)

    # الجاهزية
    ra, rb = bool(previous.get("is_ready")), bool(current.get("is_ready"))
    if ra != rb:
        changes.append({"key": "ready", "label": "الجاهزية", "from": "لا" if not ra else "نعم",
                        "to": "نعم" if rb else "لا", "dir": "up" if rb else "down"})
        net += (1 if rb else -1)

    # الحالة (state) — تحوّل على مسار الفرصة
    ca, cb = previous.get("state_code"), current.get("state_code")
    if ca != cb and ca in _STATE_RANK and cb in _STATE_RANK:
        d = _STATE_RANK[cb] - _STATE_RANK[ca]
        from services.state import STATE_LABELS
        changes.append({"key": "state", "label": "الحالة",
                        "from": STATE_LABELS.get(ca, ca), "to": STATE_LABELS.get(cb, cb),
                        "dir": "up" if d > 0 else "down"})
        net += (1 if d > 0 else -1 if d < 0 else 0)

    # السعر: معلومة فقط — لا يدخل التصنيف (تغيّر السعر وحده ليس تحسّن جودة)
    pa, pb = previous.get("analysis_price"), current.get("analysis_price")
    price_info = None
    if pa is not None and pb is not None and pa != pb:
        price_info = {"key": "price", "label": "سعر التحليل",
                      "from": f"{pa:.2f}", "to": f"{pb:.2f}",
                      "dir": "up" if pb > pa else "down", "quality": False}

    classification = "improved" if net > 0 else ("declined" if net < 0 else "unchanged")
    return {"has_previous": True, "classification": classification, "net": net,
            "changes": changes, "price_info": price_info}


def latest_changes():
    """يبني قائمة تغيّرات كل سهم (آخر لقطتين) + عدّادات أعلى الصفحة."""
    from collections import defaultdict
    rows = (StockSnapshot.query
            .order_by(StockSnapshot.ticker.asc(), StockSnapshot.snap_date.desc()).all())
    by_t = defaultdict(list)
    for r in rows:
        by_t[r.ticker].append(r)
    items = []
    counters = {"improved": 0, "declined": 0, "became_ready": 0,
                "lost_ready": 0, "became_gem": 0}
    for t, snaps in by_t.items():
        cur = snaps[0]
        prev = snaps[1] if len(snaps) > 1 else None
        summary = build_change_summary(_snap_dict(cur), _snap_dict(prev))
        items.append({"ticker": t, "summary": summary, "current": _snap_dict(cur)})
        if summary["classification"] == "improved":
            counters["improved"] += 1
        elif summary["classification"] == "declined":
            counters["declined"] += 1
        if prev is not None:
            if cur.is_ready and not prev.is_ready:
                counters["became_ready"] += 1
            if prev.is_ready and not cur.is_ready:
                counters["lost_ready"] += 1
            if cur.is_gem and not prev.is_gem:
                counters["became_gem"] += 1
    # الأكثر تحسّناً أولاً
    _order = {"improved": 0, "unchanged": 1, None: 2, "declined": 3}
    items.sort(key=lambda it: (_order.get(it["summary"]["classification"], 2), it["ticker"]))
    return items, counters
