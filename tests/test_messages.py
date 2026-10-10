"""Contacting the publisher of a report: visible account email, "Es mío / Lo encontré" requests
and the conversation between both people."""

import re

import pytest
from sqlalchemy import create_engine, inspect

import app as app_module
from config import Config
from extensions import db
from models import Conversation, Item, Message, User


def _text(response):
    return response.get_data(as_text=True)


def _main(html):
    return html.split("<main", 1)[1].split("</main>", 1)[0]


def _nav(html):
    return html.split("<main", 1)[0]


@pytest.fixture
def carlos(browser_for):
    """Publisher of the report."""
    return browser_for("carlos")


@pytest.fixture
def maria(browser_for):
    """Person who lost or found the object."""
    return browser_for("maria")


@pytest.fixture
def report(app, carlos, make_item):
    owner = User.query.filter_by(username="carlos").one()
    return make_item(owner, name="Celular Samsung", status="found", contact="999 000 111")


def _start(browser, item, body="Es mío, tiene de fondo una foto de mi perro.", kind="owner"):
    return browser.post(f"/messages/start/{item.id}", data={"kind": kind, "body": body})


# --- 1. account email on the report -------------------------------------------


def test_report_shows_the_publisher_account_email(maria, report):
    body = _main(_text(maria.get(f"/item/{report.id}")))

    assert 'href="mailto:carlos@example.com"' in body
    assert "carlos@example.com" in body


# --- 2. contact box on the report ---------------------------------------------


def test_other_users_see_the_contact_form_with_a_kind_that_fits_the_status(maria, report, make_item):
    body = _main(_text(maria.get(f"/item/{report.id}")))
    assert "¿Es tuyo o lo encontraste?" in body
    assert re.search(r'<option[^>]*selected[^>]*value="owner"|<option[^>]*value="owner"[^>]*selected', body)

    lost = make_item(report.user, name="Mochila", status="lost")
    body = _main(_text(maria.get(f"/item/{lost.id}")))
    assert re.search(r'<option[^>]*selected[^>]*value="finder"|<option[^>]*value="finder"[^>]*selected', body)


def test_publisher_sees_received_messages_instead_of_the_form(carlos, report):
    body = _main(_text(carlos.get(f"/item/{report.id}")))

    assert "Mensajes recibidos" in body
    assert "¿Es tuyo o lo encontraste?" not in body
    assert f'action="/messages/start/{report.id}"' not in body


def test_publisher_cannot_write_about_their_own_report(carlos, report):
    response = _start(carlos, report)

    assert response.status_code == 302
    assert Conversation.query.count() == 0


def test_returned_reports_accept_no_new_requests(maria, report):
    report.status = "returned"
    db.session.commit()

    assert "¿Es tuyo o lo encontraste?" not in _text(maria.get(f"/item/{report.id}"))
    _start(maria, report)
    assert Conversation.query.count() == 0


def test_invalid_message_is_rejected_with_the_error_on_the_report(maria, report):
    response = _start(maria, report, body="hola")

    assert response.status_code == 400
    assert "El mensaje debe tener entre 5 y 1000 caracteres." in _text(response)
    assert Message.query.count() == 0


def test_unknown_kind_is_rejected(maria, report):
    response = _start(maria, report, kind="hack")

    assert response.status_code == 400
    assert Conversation.query.count() == 0


# --- 3. conversation ----------------------------------------------------------


def test_starting_creates_a_conversation_and_opens_it(maria, report):
    response = _start(maria, report)

    conversation = Conversation.query.one()
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/messages/{conversation.id}")
    assert conversation.kind == "owner"
    assert conversation.starter.username == "maria"
    assert [m.body for m in conversation.messages] == ["Es mío, tiene de fondo una foto de mi perro."]

    body = _main(_text(maria.get(f"/item/{report.id}")))
    assert "Ver conversación" in body
    assert "¿Es tuyo o lo encontraste?" not in body


def test_writing_again_reuses_the_same_conversation(maria, report):
    _start(maria, report)
    _start(maria, report, body="Otro detalle: la funda es transparente.")

    assert Conversation.query.count() == 1
    assert Message.query.count() == 2


def test_publisher_gets_an_unread_badge_until_opening_the_thread(carlos, maria, report):
    _start(maria, report)
    conversation = Conversation.query.one()

    nav = _nav(_text(carlos.get("/")))
    assert re.search(r'class="nav-badge"[^>]*>1<', nav)
    inbox = _main(_text(carlos.get("/messages/")))
    assert "Celular Samsung" in inbox and "Le escribió maria" in inbox and "is-unread" in inbox

    thread = _text(carlos.get(f"/messages/{conversation.id}"))
    assert "Es mío, tiene de fondo una foto de mi perro." in thread
    assert 'href="mailto:maria@example.com"' in thread
    assert "Ya lo entregué" in thread

    assert Message.query.one().read_at is not None
    assert "nav-badge" not in _nav(_text(carlos.get("/")))


def test_both_people_can_reply(carlos, maria, report):
    _start(maria, report)
    conversation = Conversation.query.one()

    response = carlos.post(f"/messages/{conversation.id}", data={"body": "Hola, ¿cuándo puedes recogerlo?"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/messages/{conversation.id}#ultimo")
    maria.post(f"/messages/{conversation.id}", data={"body": "Mañana a las 10 en la biblioteca."})

    senders = [m.sender.username for m in Conversation.query.one().messages]
    assert senders == ["maria", "carlos", "maria"]
    assert re.search(r'class="nav-badge"[^>]*>1<', _nav(_text(maria.get("/"))))


def test_starter_does_not_see_the_mark_returned_button(maria, report):
    _start(maria, report)
    conversation = Conversation.query.one()

    assert "Ya lo entregué" not in _text(maria.get(f"/messages/{conversation.id}"))


def test_empty_reply_is_rejected(maria, report):
    _start(maria, report)
    conversation = Conversation.query.one()

    response = maria.post(f"/messages/{conversation.id}", data={"body": "   "})
    assert response.status_code == 400
    assert "Escriba un mensaje." in _text(response)
    assert Message.query.count() == 1


def test_other_people_cannot_open_or_answer_a_conversation(browser_for, maria, report):
    _start(maria, report)
    conversation = Conversation.query.one()
    intruder = browser_for("intruso")

    assert intruder.get(f"/messages/{conversation.id}").status_code == 404
    assert intruder.post(f"/messages/{conversation.id}", data={"body": "Hola, soy yo"}).status_code == 404
    assert "Celular Samsung" not in _text(intruder.get("/messages/"))
    assert Message.query.count() == 1


def test_messages_require_login(browser_for, report):
    anonymous = browser_for()
    assert anonymous.get("/messages/").status_code == 302
    assert anonymous.post(f"/messages/start/{report.id}", data={"kind": "owner", "body": "Es mío"}).status_code == 302


def test_empty_inbox_explains_how_to_start(maria):
    body = _main(_text(maria.get("/messages/")))

    assert "Aún no tiene conversaciones." in body


def test_deleting_the_report_deletes_its_conversations(carlos, maria, report):
    _start(maria, report)

    carlos.post(f"/delete/{report.id}")

    assert Item.query.count() == 0
    assert Conversation.query.count() == 0
    assert Message.query.count() == 0


def test_messages_link_is_in_the_sidebar(maria):
    nav = _nav(_text(maria.get("/messages/")))

    assert 'href="/messages/"' in nav
    assert re.search(r'<a[^>]*href="/messages/"[^>]*aria-current="page"', nav)


def test_start_requires_a_csrf_token(csrf_app, make_csrf_user, make_item):
    owner = make_csrf_user("carlos")
    make_csrf_user("maria")
    item = make_item(owner)
    client = csrf_app.test_client()
    client.post("/auth/login", data={"username": "maria", "password": "password123"})

    response = client.post(f"/messages/start/{item.id}", data={"kind": "finder", "body": "Lo encontré ayer"})

    assert response.status_code in (302, 400)
    assert Conversation.query.count() == 0


# --- existing databases get the new tables -------------------------------------


def test_existing_database_gets_the_message_tables(monkeypatch, tmp_path):
    db_path = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, "
            "password_hash VARCHAR(256) NOT NULL, email VARCHAR(120), bio VARCHAR(300), "
            "avatar VARCHAR(120), created_at DATETIME, last_login DATETIME)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE item (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, description TEXT, "
            "category VARCHAR(80), image_filename VARCHAR(200), created_at DATETIME, user_id INTEGER)"
        )
    engine.dispose()
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")

    application = app_module.create_app()
    try:
        with application.app_context():
            tables = set(inspect(db.engine).get_table_names())
    finally:
        with application.app_context():
            db.engine.dispose()

    assert {"conversation", "message", "item", "user"} <= tables
