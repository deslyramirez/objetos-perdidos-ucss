"""Messages between the publisher of a report and the people who write about it.

A conversation starts from the report page ("Es mío", "Lo encontré" or "Consulta") and belongs
to exactly two people: whoever started it and whoever published the report. Anyone else gets
a 404, so the existence of other people's conversations is never revealed.
"""

from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from domain import CONVERSATION_KINDS
from extensions import db
from forms import MESSAGE_MAX, ContactForm, ConversationKindForm, EditMessageForm, ReplyForm
from models import Conversation, Item, Message

messages = Blueprint("messages", __name__)

OWN_REPORT = "No puede enviarse mensajes sobre su propio reporte."
NO_PUBLISHER = "Este reporte no tiene una persona responsable a quien escribir."
RETURNED = "El objeto ya fue entregado; no se aceptan nuevas solicitudes."
SEND_FAILED = "No se pudo enviar el mensaje. Intente nuevamente."
SAVE_FAILED = "No se pudieron guardar los cambios. Intente nuevamente."


def ensure_message_columns(engine):
    """Add columns introduced after the first release of the message table (SQLite, idempotent)."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(message)")}
        if columns and "edited_at" not in columns:
            conn.exec_driver_sql("ALTER TABLE message ADD COLUMN edited_at DATETIME")


# -----------------------------
# QUERIES
# -----------------------------
def _participant_filter(user_id):
    return or_(Conversation.starter_id == user_id, Item.user_id == user_id)


def unread_count(user_id):
    """Messages written to `user_id` that they have not opened yet."""
    return (
        db.session.query(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(Item, Conversation.item_id == Item.id)
        .filter(_participant_filter(user_id))
        .filter(Message.sender_id != user_id, Message.read_at.is_(None))
        .scalar()
    )


def unread_by_conversation(user_id, conversation_ids):
    if not conversation_ids:
        return {}
    rows = (
        db.session.query(Message.conversation_id, func.count(Message.id))
        .filter(Message.conversation_id.in_(conversation_ids))
        .filter(Message.sender_id != user_id, Message.read_at.is_(None))
        .group_by(Message.conversation_id)
        .all()
    )
    return dict(rows)


def conversation_for(item_id, user_id):
    return Conversation.query.filter_by(item_id=item_id, starter_id=user_id).first()


def received_conversations(item):
    """Conversations started by other people about a report, newest activity first."""
    return (
        Conversation.query.filter_by(item_id=item.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .all()
    )


def _participant_conversation(conversation_id):
    conversation = db.get_or_404(Conversation, conversation_id)
    if not conversation.has_participant(current_user.id):
        abort(404)
    return conversation


@messages.app_context_processor
def inject_unread_messages():
    if current_user.is_authenticated:
        return {"unread_messages": unread_count(current_user.id)}
    return {"unread_messages": 0}


def _commit(error=SEND_FAILED):
    try:
        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not save a message")
        flash(error, "danger")
        return False


def _own_message(message_id):
    """A message the current user wrote; anything else is a 404."""
    message = db.get_or_404(Message, message_id)
    if message.sender_id != current_user.id or not message.conversation.has_participant(current_user.id):
        abort(404)
    return message


# -----------------------------
# INBOX
# -----------------------------
@messages.route("/")
@login_required
def inbox():
    conversations = (
        Conversation.query.join(Item, Conversation.item_id == Item.id)
        .filter(_participant_filter(current_user.id))
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .all()
    )
    unread = unread_by_conversation(current_user.id, [c.id for c in conversations])
    return render_template(
        "messages/inbox.html",
        conversations=conversations,
        unread=unread,
        kinds=CONVERSATION_KINDS,
    )


# -----------------------------
# START FROM A REPORT
# -----------------------------
@messages.route("/start/<int:item_id>", methods=["POST"])
@login_required
def start(item_id):
    item = db.get_or_404(Item, item_id)
    back = redirect(url_for("main.view", item_id=item.id))
    if item.user_id is None:
        flash(NO_PUBLISHER, "warning")
        return back
    if item.user_id == current_user.id:
        flash(OWN_REPORT, "warning")
        return back

    existing = conversation_for(item.id, current_user.id)
    if existing is None and item.is_returned:
        flash(RETURNED, "warning")
        return back

    form = ContactForm()
    if not form.validate_on_submit():
        from main import render_item_view  # main imports nothing from here; avoids a cycle

        return render_item_view(item, contact_form=form), 400

    now = datetime.utcnow()
    conversation = existing
    if conversation is None:
        conversation = Conversation(
            item_id=item.id, starter_id=current_user.id, kind=form.kind.data, created_at=now
        )
        db.session.add(conversation)
    conversation.updated_at = now
    conversation.messages.append(
        Message(sender_id=current_user.id, body=form.body.data, created_at=now)
    )
    if not _commit():
        return back

    flash("Mensaje enviado. La persona que publicó el reporte lo verá en «Mensajes».", "success")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


# -----------------------------
# THREAD AND REPLIES
# -----------------------------
@messages.route("/<int:conversation_id>", methods=["GET", "POST"])
@login_required
def thread(conversation_id):
    conversation = _participant_conversation(conversation_id)
    form = ReplyForm()

    if form.validate_on_submit():
        now = datetime.utcnow()
        conversation.updated_at = now
        conversation.messages.append(
            Message(sender_id=current_user.id, body=form.body.data, created_at=now)
        )
        if _commit():
            return redirect(
                url_for("messages.thread", conversation_id=conversation.id, _anchor="ultimo")
            )

    # Opening the thread marks what the other person wrote as read.
    pending = [m for m in conversation.messages if m.sender_id != current_user.id and m.read_at is None]
    if pending:
        now = datetime.utcnow()
        for message in pending:
            message.read_at = now
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not mark messages as read")

    return render_template(
        "messages/thread.html",
        conversation=conversation,
        item=conversation.item,
        other=conversation.other_user(current_user.id),
        is_publisher=conversation.item.user_id == current_user.id,
        form=form,
        kind_form=ConversationKindForm(kind=conversation.kind),
        kinds=CONVERSATION_KINDS,
        message_max=MESSAGE_MAX,
    ), (400 if form.errors else 200)


# -----------------------------
# EDIT AND DELETE
# -----------------------------
@messages.route("/<int:conversation_id>/kind", methods=["POST"])
@login_required
def change_kind(conversation_id):
    """Only whoever started the conversation chooses its reason."""
    conversation = _participant_conversation(conversation_id)
    if conversation.starter_id != current_user.id:
        abort(404)
    form = ConversationKindForm()
    if form.validate_on_submit():
        conversation.kind = form.kind.data
        if _commit(SAVE_FAILED):
            flash("Motivo actualizado.", "success")
    else:
        flash("Seleccione un motivo válido.", "danger")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


@messages.route("/message/<int:message_id>/edit", methods=["POST"])
@login_required
def edit_message(message_id):
    message = _own_message(message_id)
    conversation_id = message.conversation_id
    form = EditMessageForm()
    if form.validate_on_submit():
        if form.body.data != message.body:
            message.body = form.body.data
            message.edited_at = datetime.utcnow()
            if _commit(SAVE_FAILED):
                flash("Mensaje editado.", "success")
    else:
        for error in form.body.errors:
            flash(error, "danger")
    return redirect(url_for("messages.thread", conversation_id=conversation_id, _anchor=f"m{message.id}"))


@messages.route("/message/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    message = _own_message(message_id)
    conversation_id = message.conversation_id
    db.session.delete(message)
    if _commit(SAVE_FAILED):
        flash("Mensaje eliminado.", "info")
    return redirect(url_for("messages.thread", conversation_id=conversation_id))
