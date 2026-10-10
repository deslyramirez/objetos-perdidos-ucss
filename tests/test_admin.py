"""Administrator role: may delete any report, nothing else changes."""

import pytest
from sqlalchemy import create_engine, inspect

from admin import ensure_user_columns, set_admin
from extensions import db
from models import Conversation, Item, Message, User


def _text(response):
    return response.get_data(as_text=True)


@pytest.fixture
def report(app, browser_for, make_item):
    browser_for("carlos")
    owner = User.query.filter_by(username="carlos").one()
    return make_item(owner, name="Paraguas negro")


@pytest.fixture
def admin(browser_for):
    browser = browser_for("desly")
    set_admin("desly")
    return browser


def test_new_users_are_not_administrators(app, make_user):
    assert make_user("ana").is_admin is False


def test_set_admin_grants_and_revokes_ignoring_case(app, make_user):
    make_user("Desly30")

    assert set_admin("desly30").is_admin is True
    assert set_admin("DESLY30", value=False).is_admin is False
    assert set_admin("nadie") is None


def test_admin_sees_the_delete_button_on_other_reports(admin, report):
    html = _text(admin.get(f"/item/{report.id}"))

    assert "Administrador" in html
    assert f'action="/delete/{report.id}"' in html
    assert f'href="/edit/{report.id}"' not in html


def test_admin_can_delete_any_report_with_its_messages(admin, browser_for, report):
    maria = browser_for("maria")
    maria.post(f"/messages/start/{report.id}", data={"kind": "owner", "body": "Es mío, tiene mango de madera."})
    assert Conversation.query.count() == 1

    response = admin.post(f"/delete/{report.id}")

    assert response.status_code == 302
    assert Item.query.count() == 0
    assert Conversation.query.count() == 0 and Message.query.count() == 0


def test_regular_users_still_cannot_delete_other_reports(browser_for, report):
    maria = browser_for("maria")

    assert f'action="/delete/{report.id}"' not in _text(maria.get(f"/item/{report.id}"))
    maria.post(f"/delete/{report.id}")
    assert Item.query.count() == 1


def test_admin_still_cannot_edit_other_reports(admin, report):
    admin.post(f"/edit/{report.id}", data={"name": "Cambiado"})

    assert db.session.get(Item, report.id).name == "Paraguas negro"


def test_admin_cannot_read_private_conversations(admin, browser_for, report):
    maria = browser_for("maria")
    maria.post(f"/messages/start/{report.id}", data={"kind": "owner", "body": "Es mío, tiene mango de madera."})
    conversation = Conversation.query.one()

    assert admin.get(f"/messages/{conversation.id}").status_code == 404


def test_old_user_table_gets_the_admin_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(64), password_hash VARCHAR(256))")
        conn.exec_driver_sql("INSERT INTO user (username, password_hash) VALUES ('ana', 'x')")
    ensure_user_columns(engine)
    ensure_user_columns(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("user")}
    with engine.connect() as conn:
        value = conn.exec_driver_sql("SELECT is_admin FROM user").scalar()
    engine.dispose()

    assert "is_admin" in columns and value == 0


def test_create_admin_account_generates_a_password(app):
    from admin import create_admin_account

    user, password = create_admin_account()

    assert (user.username, user.email, user.is_admin) == ("admin", "admin@gmail.com", True)
    assert len(password) >= 10 and user.check_password(password)


def test_create_admin_account_again_resets_the_password_without_duplicates(app):
    from admin import create_admin_account

    create_admin_account("primera123")
    user, password = create_admin_account("segunda123")

    assert User.query.count() == 1
    assert password == "segunda123" and user.check_password("segunda123")


def test_create_admin_account_reuses_an_account_with_that_email(app, make_user):
    from admin import create_admin_account

    make_user("profe", email="admin@gmail.com")
    user, _ = create_admin_account("clave1234")

    assert User.query.count() == 1
    assert user.username == "profe" and user.is_admin


def test_admin_account_can_log_in(app, browser_for):
    from admin import create_admin_account

    create_admin_account("clave1234")
    browser = browser_for()
    browser.post("/auth/login", data={"username": "admin", "password": "clave1234"})

    assert "Administrador" in _text(browser.get("/"))
