import os
from urllib.parse import urlparse

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from extensions import csrf, db, login_manager
from schema import upgrade_legacy_schema
from utils import is_safe_redirect_target, register_template_helpers


def debug_enabled():
    """Debug mode is opt-in through FLASK_DEBUG=1."""
    return os.environ.get("FLASK_DEBUG") == "1"


def _same_site_referrer_path():
    """Return the referrer as a relative path when it points to this site, else None."""
    referrer = request.referrer
    if not referrer:
        return None
    parsed = urlparse(referrer)
    if parsed.scheme not in ("http", "https") or parsed.netloc != request.host:
        return None
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return target if is_safe_redirect_target(target) else None


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Load config first so tests can override it before any extension or DB access
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    register_template_helpers(app)

    # Register blueprints
    from main import bp as main_bp
    from auth import bp as auth_bp
    from dashboard import dashboard as dashboard_bp
    from profile import profile as profile_bp
    from messages import messages as messages_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(messages_bp, url_prefix="/messages")

    # Error pages (status codes preserved)
    @app.errorhandler(404)
    def not_found(error):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("500.html"), 500

    def _redirect_back():
        target = _same_site_referrer_path()
        if target is None:
            endpoint = "main.index" if current_user.is_authenticated else "auth.login"
            target = url_for(endpoint)
        return redirect(target)

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        flash("La sesión del formulario expiró o no es válida. Intente nuevamente.", "warning")
        return _redirect_back()

    @app.errorhandler(RequestEntityTooLarge)
    def payload_too_large(error):
        flash("El archivo supera el tamaño máximo permitido (5 MB).", "danger")
        return _redirect_back()

    # Create the database on first run and upgrade an existing legacy one (never for test configs)
    if not test_config:
        with app.app_context():
            # create_all only adds missing tables (e.g. messages on an existing database).
            import models  # noqa: F401  registers every table before create_all

            db.create_all()
            from messages import ensure_message_columns

            ensure_message_columns(db.engine)
            result = upgrade_legacy_schema(db.engine)
            app.logger.info(result.describe())

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=debug_enabled())
