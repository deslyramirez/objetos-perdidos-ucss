"""Administrator moderation and the administrator account."""

import pytest
from sqlalchemy import create_engine, inspect

from admin import create_admin_account, ensure_admin_from_env, ensure_moderation_columns
from extensions import db
from models import Conversation, Item, User


def _text(response):
    return response.get_data(as_text=True)


def _nav(html):
    return html.split("<main", 1)[0]


@pytest.fixture
def admin(browser_for):
    browser = browser_for("jefa")
    User.query.filter_by(username="jefa").one().is_admin = True
    db.session.commit()
    return browser


@pytest.fixture
def carlos(browser_for):
    return browser_for("carlos")


@pytest.fixture
def maria(browser_for):
    return browser_for("maria")


def _carlos():
    return User.query.filter_by(username="carlos").one()


def _add(browser, png, name="Mochila roja"):
    from io import BytesIO

    data = {
        "name": name, "description": "Con llavero", "category": "bags", "location": "Cafetería",
        "event_date": "2026-03-10", "status": "lost", "contact": "999 111 222",
        "image": (BytesIO(png), "foto.png"),
    }
    return browser.post("/add", data=data, content_type="multipart/form-data", follow_redirects=True)


# --- publishing waits for approval ----------------------------------------------


def test_a_new_report_waits_for_review_and_only_its_author_sees_it(carlos, maria, png_bytes):
    from io import BytesIO

    carlos.post("/add", data={
        "name": "Mochila roja", "description": "Con llavero", "category": "bags", "location": "Cafetería",
        "event_date": "2026-03-10", "status": "lost", "contact": "999 111 222",
        "image": (BytesIO(png_bytes), "foto.png"),
    }, content_type="multipart/form-data")
    item = Item.query.one()

    assert item.approval == "pending"
    assert "Mochila roja" in _text(carlos.get("/")) and "En revisión" in _text(carlos.get("/"))
    assert "Mochila roja" not in _text(maria.get("/"))
    response = maria.get(f"/item/{item.id}")
    assert response.status_code == 404 and "Publicación no disponible" in _text(response)


def test_admin_approves_and_the_report_becomes_public(admin, carlos, maria, make_item):
    item = make_item(_carlos(), name="Paraguas azul", approval="pending")

    page = _text(admin.get("/admin/moderacion"))
    assert "Paraguas azul" in page
    admin.post(f"/admin/item/{item.id}/aprobar")

    assert db.session.get(Item, item.id).approval == "approved"
    assert "Paraguas azul" in _text(maria.get("/"))


def test_admin_disapproves_with_a_reason_the_author_can_read(admin, carlos, maria, make_item):
    item = make_item(_carlos(), name="Celular", approval="pending")

    admin.post(f"/admin/item/{item.id}/desaprobar", data={"reason": "Falta una descripción clara"})

    item = db.session.get(Item, item.id)
    assert item.approval == "rejected" and item.rejection_reason == "Falta una descripción clara"
    assert "Celular" not in _text(maria.get("/"))
    page = _text(carlos.get(f"/item/{item.id}"))
    assert "Desaprobado por el administrador" in page and "Falta una descripción clara" in page


def test_editing_a_report_sends_it_back_to_review(carlos, make_item):
    item = make_item(_carlos(), name="Llaves", approval="rejected", rejection_reason="Foto borrosa")

    carlos.post(f"/edit/{item.id}", data={
        "name": "Llaves", "description": "Tres llaves", "category": "keys", "location": "Patio",
        "event_date": "2026-03-10", "status": "lost", "contact": "999",
    }, content_type="multipart/form-data")

    item = db.session.get(Item, item.id)
    assert item.approval == "pending" and item.rejection_reason is None


def test_admin_reports_are_published_directly(admin, png_bytes):
    _add(admin, png_bytes, "Calculadora")

    assert Item.query.one().approval == "approved"


def test_reports_that_existed_before_moderation_stay_published(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(64), password_hash VARCHAR(256))")
        conn.exec_driver_sql("CREATE TABLE item (id INTEGER PRIMARY KEY, name VARCHAR(120))")
        conn.exec_driver_sql("INSERT INTO item (name) VALUES ('Viejo')")
    ensure_moderation_columns(engine)
    ensure_moderation_columns(engine)
    with engine.connect() as conn:
        approval = conn.exec_driver_sql("SELECT approval FROM item").scalar()
    columns = {c["name"] for c in inspect(engine).get_columns("user")}
    engine.dispose()

    assert approval == "approved" and "is_admin" in columns


def test_nobody_can_message_about_an_unpublished_report(maria, make_item, carlos):
    item = make_item(_carlos(), approval="pending")

    response = maria.post(f"/messages/start/{item.id}", data={"kind": "owner", "body": "Es mío de verdad"})

    assert response.status_code == 404
    assert Conversation.query.count() == 0


# --- the administrator's sections -------------------------------------------------


def test_regular_users_cannot_moderate(maria, make_item, carlos):
    item = make_item(_carlos(), approval="pending")

    assert maria.get("/admin/moderacion").status_code == 302
    maria.post(f"/admin/item/{item.id}/aprobar")
    assert db.session.get(Item, item.id).approval == "pending"


def test_admin_sidebar_has_the_panel_moderation_import_and_export(admin, make_item, carlos):
    make_item(_carlos(), approval="pending")
    nav = _nav(_text(admin.get("/")))

    for label in ("Panel mensual", "Moderación", "Importar CSV", "Exportar CSV", "Administrador"):
        assert label in nav, label
    assert 'class="nav-badge"' in nav


def test_admin_lands_on_the_panel_and_users_on_the_report_center(browser_for, app):
    create_admin_account("Clave2026")
    browser = browser_for()
    response = browser.post("/auth/login", data={"username": "admin", "password": "Clave2026"})
    assert response.headers["Location"].endswith("/dashboard/")

    browser_for("ana")
    other = browser_for()
    response = other.post("/auth/login", data={"username": "ana", "password": "password123"})
    assert response.headers["Location"].split("?")[0].endswith("/")
    assert not response.headers["Location"].endswith("/dashboard/")


def test_admin_detail_page_has_approve_and_disapprove(admin, make_item, carlos):
    item = make_item(_carlos(), approval="pending")
    page = _text(admin.get(f"/item/{item.id}"))

    assert f'action="/admin/item/{item.id}/aprobar"' in page
    assert f'action="/admin/item/{item.id}/desaprobar"' in page


# --- no community flags ---------------------------------------------------------------


def test_there_is_no_report_publication_button(maria, make_item, carlos):
    item = make_item(_carlos())

    assert "Reportar publicación" not in _text(maria.get(f"/item/{item.id}"))
    assert maria.post(f"/item/{item.id}/flag", data={"reason": "fake"}).status_code in (404, 405)
    assert "Reportadas" not in _text(carlos.get("/"))


# --- administrator account ------------------------------------------------------------


def test_admin_account_is_created_from_the_environment_once(app):
    assert ensure_admin_from_env({}) is None
    assert ensure_admin_from_env({"ADMIN_PASSWORD": "123"}) is None

    user = ensure_admin_from_env({"ADMIN_PASSWORD": "Inicial2026"})
    assert (user.username, user.email, user.is_admin) == ("admin", "admin@gmail.com", True)
    assert user.check_password("Inicial2026")

    user.set_password("CambiadaPorElla")
    db.session.commit()
    ensure_admin_from_env({"ADMIN_PASSWORD": "Inicial2026"})  # next reload
    assert User.query.count() == 1
    assert User.query.one().check_password("CambiadaPorElla")


def test_environment_admin_takes_over_an_account_registered_with_that_email(app, make_user):
    make_user("admin", email="admin@gmail.com")

    user = ensure_admin_from_env({"ADMIN_PASSWORD": "Inicial2026"})

    assert User.query.count() == 1
    assert user.is_admin and user.check_password("Inicial2026")
