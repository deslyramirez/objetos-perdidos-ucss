from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from domain import DEFAULT_STATUS, STATUS_LABELS
from extensions import db, login_manager
import pytz

# -----------------------------
# USER MODEL
# -----------------------------
class User(UserMixin, db.Model):
    created_at = db.Column(db.DateTime, default=lambda: datetime.utcnow())
    last_login = db.Column(db.DateTime, default=None)
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    email = db.Column(db.String(120), unique=True)
    bio = db.Column(db.String(300))

    # The administrator approves reports and owns the panel, import and export (see admin.py).
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default="0")

    # avatar filename (default avatar included in static folder)
    avatar = db.Column(db.String(120), default="default.png")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Constructor helpers
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# -----------------------------
# ITEM MODEL
# -----------------------------
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    # NEW: category and image filename
    category = db.Column(db.String(80), nullable=True)         # e.g. "electronics"
    image_filename = db.Column(db.String(200), nullable=True)  # store filename under static/uploads/

    # Lost-and-found fields. location/event_date/contact are nullable at database level so rows
    # from the legacy schema survive; the forms are what makes location and event_date required.
    location = db.Column(db.String(120), nullable=True)
    event_date = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default=DEFAULT_STATUS,
        server_default=DEFAULT_STATUS,
    )
    contact = db.Column(db.String(120), nullable=True)

    # Moderation. New reports start 'pending'; rows that existed before moderation keep the
    # server default 'approved' so nothing already published disappears.
    approval = db.Column(db.String(20), nullable=False, default="pending", server_default="approved")
    rejection_reason = db.Column(db.String(300), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_approved(self):
        return self.approval == "approved"

    @property
    def is_returned(self):
        return self.status == "returned"

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)

    # optional: link item → user who added it
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    user = db.relationship('User', backref='items')


# -----------------------------
# CONVERSATIONS ABOUT A REPORT
# -----------------------------
class Conversation(db.Model):
    """One thread per report and per person who writes to its publisher."""

    __table_args__ = (db.UniqueConstraint("item_id", "starter_id", name="uq_conversation_item_starter"),)

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False, index=True)
    starter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="question")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    item = db.relationship(
        "Item",
        backref=db.backref("conversations", cascade="all, delete-orphan", passive_deletes=False),
    )
    starter = db.relationship("User", foreign_keys=[starter_id])
    messages = db.relationship(
        "Message",
        backref="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at, Message.id",
    )

    def other_user(self, user_id):
        """The other participant: the publisher for the starter and the starter for the publisher."""
        return self.item.user if user_id == self.starter_id else self.starter

    def has_participant(self, user_id):
        return user_id is not None and user_id in (self.starter_id, self.item.user_id)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversation.id"), nullable=False, index=True
    )
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    sender = db.relationship("User")


def visible_items_filter(user=None):
    """Published reports, plus the user's own reports in any moderation state."""
    published = Item.approval == "approved"
    if user is not None and getattr(user, "is_authenticated", False):
        return db.or_(published, Item.user_id == user.id)
    return published
