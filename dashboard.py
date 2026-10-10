"""Monthly reporting dashboard for all authenticated lost-and-found reports."""

import calendar
import json

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

from admin import admin_required
from sqlalchemy import case, func

from domain import CATEGORY_LABELS, SUMMARY_APPROVALS, month_bounds, parse_month
from extensions import db
from models import Item
from utils import MONTH_NAMES, today_local

dashboard = Blueprint("dashboard", __name__)


def _month_text(year, month):
    return f"{MONTH_NAMES[month - 1]} de {year}"


def _month_key(year, month):
    return f"{year:04d}-{month:02d}"


def _adjacent(year, month, offset):
    value = year * 12 + month - 1 + offset
    return value // 12, value % 12 + 1


def _count_label(value):
    return f"{value:,}".replace(",", ".")


def _delta_text(current, previous):
    if previous == 0 and current == 0:
        return "Sin datos del mes anterior"
    difference = current - previous
    if difference == 0:
        return "Sin cambios respecto al mes anterior"
    sign = "+" if difference > 0 else ""
    return f"{sign}{_count_label(difference)} respecto al mes anterior"


def month_summary(year, month):
    """Build a month summary with SQL aggregation and fixed-size result sets."""
    first_day, next_month = month_bounds(year, month)
    previous_year, previous_month = _adjacent(year, month, -1)
    previous_first, previous_next = (None, None)
    if previous_year >= 1:
        previous_first, previous_next = month_bounds(previous_year, previous_month)
    # Published and deactivated reports: deactivating hides a report, it does not erase history.
    visible = Item.approval.in_(SUMMARY_APPROVALS)
    period = Item.event_date >= first_day, Item.event_date < next_month, visible
    previous_period = None
    if previous_first is not None:
        previous_period = Item.event_date >= previous_first, Item.event_date < previous_next, visible

    status_rows = (
        db.session.query(Item.status, func.count(Item.id))
        .filter(*period)
        .group_by(Item.status)
        .all()
    )
    counts = {"lost": 0, "found": 0, "returned": 0}
    total = 0
    for status, amount in status_rows:
        total += amount
        if status in counts:
            counts[status] = amount

    previous_total = 0
    if previous_first is not None:
        previous_total = db.session.query(func.count(Item.id)).filter(*previous_period).scalar() or 0
    days = calendar.monthrange(year, month)[1]
    daily_rows = (
        db.session.query(Item.event_date, func.count(Item.id))
        .filter(*period)
        .group_by(Item.event_date)
        .all()
    )
    daily_counts = [0] * days
    for event_date, amount in daily_rows:
        daily_counts[event_date.day - 1] = amount

    category_key = func.trim(func.coalesce(Item.category, ""))
    category_label = case(
        (category_key == "", "Sin categoría"),
        *[(category_key == key, label) for key, label in CATEGORY_LABELS.items()],
        else_=category_key,
    )
    category_rows = (
        db.session.query(category_label, func.count(Item.id))
        .filter(*period)
        .group_by(category_label)
        .order_by(func.count(Item.id).desc(), category_label)
        .all()
    )

    blank_location = case(
        (func.trim(func.coalesce(Item.location, "")) == "", "Sin especificar"),
        else_=func.trim(Item.location),
    )
    location_rows = (
        db.session.query(blank_location.label("location"), func.count(Item.id))
        .filter(*period)
        .group_by(blank_location)
        .order_by(func.count(Item.id).desc(), blank_location)
        .limit(5)
        .all()
    )
    recent = (
        Item.query.filter(*period)
        .order_by(Item.event_date.desc(), Item.id.desc())
        .limit(10)
        .all()
    )
    return {
        "mes": _month_key(year, month),
        "month_label": _month_text(year, month),
        "prev_mes": _month_key(previous_year, previous_month) if year > 1 or month > 1 else None,
        "next_mes": _month_key(*_adjacent(year, month, 1)) if year < 9998 or month < 12 else None,
        "kpis": {
            "total": total,
            "lost": counts["lost"],
            "found": counts["found"],
            "returned": counts["returned"],
            "return_rate": f"{counts['returned'] * 100 / total:.1f} %".replace(".", ",") if total else "—",
            "delta_vs_previous": _delta_text(total, previous_total),
        },
        "daily": {"labels": [str(day) for day in range(1, days + 1)], "counts": daily_counts},
        "categories": {"labels": [row[0] for row in category_rows], "counts": [row[1] for row in category_rows]},
        "locations": {"labels": [row[0] for row in location_rows], "counts": [row[1] for row in location_rows]},
        "recent": recent,
    }


@dashboard.route("/")
@admin_required
def home():
    default = today_local()
    selected = parse_month(request.args.get("mes", ""), (default.year, default.month))
    summary = month_summary(*selected)
    has_reports = summary["kpis"]["total"] > 0
    chart_data = {key: summary[key] for key in ("daily", "categories", "locations")}
    chart_data["month"] = summary["month_label"]
    chart_json = json.dumps(chart_data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return render_template("dashboard.html", summary=summary, has_reports=has_reports, chart_json=chart_json)


@dashboard.route("")
@admin_required
def legacy_redirect():
    query = request.query_string.decode("utf-8", errors="ignore")
    target = url_for("dashboard.home")
    return redirect(f"{target}?{query}" if query else target)
