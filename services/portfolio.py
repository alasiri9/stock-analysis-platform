"""
portfolio.py — تقييم المحفظة، اللقطات اليومية، وشارت الأداء.

- valuation(): قيمة المحفظة الآن من كاش الماسح (بلا API لأسهم الماسح).
- record_snapshot(): لقطة يومية (يستدعيها المجدول الليلي بعد تحديث الأسعار).
- performance_chart(): بيانات SVG لمنحنى قيمة المحفظة عبر الأيام.

المبادئ: None ≠ 0 — لا لقطة ولا شارت بقيم ناقصة مضللة.
"""

from datetime import datetime, timezone

from models import db, PortfolioHolding, PortfolioSnapshot
from services import screener


def valuation(user_id="admin"):
    """يقيّم محفظة مستخدم من كاش الماسح. يُرجع dict:
    {count, total_cost, total_value (None لو سعر أي مقتنى مفقود), complete}

    الافتراضي محفظة المدير (admin) — هي المرجعية للقطات الليلية والتقرير.
    """
    holdings = PortfolioHolding.query.filter_by(user_id=user_id).all()
    if not holdings:
        return {"count": 0, "total_cost": None, "total_value": None, "complete": False}

    records, _ = screener.load_records()
    price_by = {r["ticker"]: r.get("price") for r in records}

    total_cost = total_value = 0.0
    complete = True
    for h in holdings:
        total_cost += h.shares * h.buy_price
        p = price_by.get(h.ticker)
        if p is None:
            complete = False
        else:
            total_value += h.shares * p
    return {
        "count": len(holdings),
        "total_cost": total_cost,
        "total_value": total_value if complete else None,
        "complete": complete,
    }


def record_snapshot():
    """يسجّل لقطة اليوم لقيمة المحفظة (merge — آمنة التكرار).

    تُتخطى لو المحفظة فارغة أو سعر أي مقتنى غير متوفر بالكاش.
    يُرجع True لو سُجّلت.
    """
    v = valuation()
    if not v["count"] or v["total_value"] is None:
        return False
    today = datetime.now(timezone.utc).date()
    db.session.merge(PortfolioSnapshot(
        date=today, total_cost=v["total_cost"], total_value=v["total_value"],
    ))
    db.session.commit()
    print(f"[portfolio] لقطة {today}: قيمة {v['total_value']:.2f}$")
    return True


def performance_chart(width=900, height=240, pad=10):
    """يبني بيانات شارت لمنحنى قيمة المحفظة من اللقطات اليومية.

    يُرجع dict {points, area_points, width, height, first_date, last_date,
    first_value, last_value, change_pct, up, days} أو None لو أقل من لقطتين.
    """
    snaps = PortfolioSnapshot.query.order_by(PortfolioSnapshot.date.asc()).all()
    if len(snaps) < 2:
        return None

    values = [s.total_value for s in snaps]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    step = (width - 2 * pad) / (n - 1)

    pts = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    points = " ".join(pts)
    area_points = f"{pad:.1f},{height - pad} " + points + f" {pad + (n - 1) * step:.1f},{height - pad}"

    first, last = values[0], values[-1]
    return {
        "points": points,
        "area_points": area_points,
        "width": width,
        "height": height,
        "first_date": snaps[0].date.isoformat(),
        "last_date": snaps[-1].date.isoformat(),
        "first_value": first,
        "last_value": last,
        "change_pct": (last - first) / first * 100.0 if first else None,
        "up": last >= first,
        "days": n,
    }
