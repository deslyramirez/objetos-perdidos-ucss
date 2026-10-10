"""Administrator role: schema upgrade for existing databases and granting/revoking the role."""

from extensions import db
from models import User


def ensure_user_columns(engine):
    """Add `is_admin` to a `user` table created before the role existed (SQLite, idempotent)."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user)")}
        if columns and "is_admin" not in columns:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")


def set_admin(username, value=True):
    """Grant (or revoke) the administrator role. Return the user, or None if it does not exist."""
    user = User.query.filter(db.func.lower(User.username) == username.strip().lower()).first()
    if user is None:
        return None
    user.is_admin = value
    db.session.commit()
    return user


def admin_usernames():
    return [u.username for u in User.query.filter_by(is_admin=True).order_by(User.username)]
