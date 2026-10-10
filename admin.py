"""Administrator: approves or disapproves reports and is the only one with the monthly panel,
CSV import and CSV export.

The account (usuario «admin», admin@gmail.com) is created on start when ADMIN_PASSWORD is set
in the server environment (the WSGI file), so no password ever lives in the repository.
"""

import secrets
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from domain import APPROVAL_LABELS
from extensions import db
from models import Item, User

admin = Blueprint("admin", __name__)

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@gmail.com"
ADMIN_ONLY = "Solo el administrador puede acceder a esa sección."
REJECTION_REASON_MAX = 300


# -----------------------------
# ROLE
# -----------------------------
def is_admin(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_admin", False))


def admin_required(view):
    """Logged-in administrators only; everyone else goes back to the report center."""

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not is_admin(current_user):
            flash(ADMIN_ONLY, "warning")
            return redirect(url_for("main.index"))
        return view(*args, **kwargs)

    return wrapper


def home_endpoint(user):
    """First screen after logging in: the panel for the administrator, reports for everyone else."""
    return "dashboard.home" if is_admin(user) else "main.index"


# -----------------------------
# SCHEMA UPGRADE AND ACCOUNT
# -----------------------------
def ensure_moderation_columns(engine):
    """Add the role and moderation columns to databases created before them (SQLite, idempotent).

    Reports that already existed stay published ('approved'); only new ones wait for review.
    """
    with engine.begin() as conn:
        user_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user)")}
        if user_columns and "is_admin" not in user_columns:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
        item_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(item)")}
        if item_columns and "approval" not in item_columns:
            conn.exec_driver_sql(
                "ALTER TABLE item ADD COLUMN approval VARCHAR(20) NOT NULL DEFAULT 'approved'"
            )
        if item_columns and "rejection_reason" not in item_columns:
            conn.exec_driver_sql("ALTER TABLE item ADD COLUMN rejection_reason VARCHAR(300)")


def create_admin_account(password=None, username=ADMIN_USERNAME, email=ADMIN_EMAIL):
    """Create (or reset) the administrator account and return (user, password).

    If the email already belongs to an account, that account becomes the administrator.
    """
    password = password or secrets.token_urlsafe(9)
    user = User.query.filter(func.lower(User.email) == email.lower()).first()
    if user is None:
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
    if user is None:
        user = User(username=username, email=email)
        db.session.add(user)
    user.is_admin = True
    user.set_password(password)
    db.session.commit()
    return user, password


def ensure_admin_from_env(environ):
    """On start: with ADMIN_PASSWORD set, make sure the administrator account exists.

    The password is written only when the account is created (or was not admin yet), so a later
    change in «Cambiar contraseña» survives every reload.
    """
    password = (environ.get("ADMIN_PASSWORD") or "").strip()
    if len(password) < 6:
        return None
    user = User.query.filter(func.lower(User.email) == ADMIN_EMAIL).first()
    if user is None:
        user = User.query.filter(func.lower(User.username) == ADMIN_USERNAME).first()
    if user is not None and user.is_admin:
        return user
    user, _ = create_admin_account(password)
    return user


# -----------------------------
# COUNTS
# -----------------------------
def pending_count():
    return db.session.query(func.count(Item.id)).filter(Item.approval == "pending").scalar()


@admin.app_context_processor
def inject_admin():
    if is_admin(current_user):
        return {"is_admin": True, "moderation_count": pending_count()}
    return {"is_admin": False, "moderation_count": 0}


# -----------------------------
# MODERATION SCREEN
# -----------------------------
TABS = {
    "pendientes": "Pendientes",
    "desaprobadas": "Desaprobadas",
}


@admin.route("/moderacion")
@admin_required
def moderation():
    tab = request.args.get("tab", "pendientes")
    if tab not in TABS:
        tab = "pendientes"

    base = Item.query.options(joinedload(Item.user))
    if tab == "pendientes":
        items = base.filter(Item.approval == "pending").order_by(Item.created_at, Item.id).all()
    else:
        items = base.filter(Item.approval == "rejected").order_by(Item.id.desc()).all()

    tab_counts = {
        "pendientes": pending_count(),
        "desaprobadas": db.session.query(func.count(Item.id))
        .filter(Item.approval == "rejected")
        .scalar(),
    }
    return render_template(
        "admin/moderation.html",
        tab=tab,
        tabs=TABS,
        tab_counts=tab_counts,
        items=items,
        approval_labels=APPROVAL_LABELS,
        reason_max=REJECTION_REASON_MAX,
    )


def _back(default_tab):
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(url_for("admin.moderation", tab=default_tab))


def _save(message, category="success"):
    try:
        db.session.commit()
        flash(message, category)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Moderation change failed")
        flash("No se pudo guardar el cambio. Intente nuevamente.", "danger")


@admin.route("/item/<int:item_id>/aprobar", methods=["POST"])
@admin_required
def approve(item_id):
    item = db.get_or_404(Item, item_id)
    item.approval = "approved"
    item.rejection_reason = None
    _save(f"«{item.name}» quedó publicado.")
    return _back("pendientes")


@admin.route("/item/<int:item_id>/desaprobar", methods=["POST"])
@admin_required
def reject(item_id):
    item = db.get_or_404(Item, item_id)
    reason = (request.form.get("reason") or "").strip()[:REJECTION_REASON_MAX]
    item.approval = "rejected"
    item.rejection_reason = reason or None
    _save(f"«{item.name}» fue desaprobado y ya no es visible para la comunidad.", "info")
    return _back("pendientes")
