from datetime import datetime, timezone
from urllib.parse import quote

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from forms import LoginForm, RegisterForm
from models import User
from utils import is_safe_redirect_target

bp = Blueprint("auth", __name__, template_folder="templates")


def _utc_now():
    """Naive UTC timestamp, the format the database columns store."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================
# LOGIN
# ===========================
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    form = LoginForm()

    # Only same-site relative paths survive; anything else is dropped silently.
    requested_next = request.args.get("next")
    safe_next = requested_next if is_safe_redirect_target(requested_next) else None
    login_action = url_for("auth.login")
    if safe_next:
        login_action += "?next=" + quote(safe_next, safe="")

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user is None or not user.check_password(form.password.data):
            flash("Usuario o contraseña incorrectos.", "danger")
            return redirect(login_action)

        user.last_login = _utc_now()
        db.session.commit()

        login_user(user)
        flash("Sesión iniciada correctamente.", "success")
        return redirect(safe_next or url_for("dashboard.home"))

    return render_template("login.html", form=form, login_action=login_action)


# ===========================
# REGISTER
# ===========================
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    form = RegisterForm()

    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("El nombre de usuario ya está en uso.", "warning")
            return redirect(url_for("auth.register"))

        if User.query.filter_by(email=form.email.data).first():
            flash("El correo electrónico ya está en uso.", "danger")
            return redirect(url_for("auth.register"))

        new_user = User(
            username=form.username.data,
            email=form.email.data,
            created_at=_utc_now(),
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)

        try:
            db.session.commit()
        except IntegrityError:
            # Another request registered the same username/email between check and insert.
            db.session.rollback()
            flash("El nombre de usuario o el correo electrónico ya está en uso.", "danger")
            return redirect(url_for("auth.register"))

        flash("Cuenta creada correctamente. Inicie sesión.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html", form=form)


# ===========================
# LOGOUT
# ===========================
@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("auth.login"))
