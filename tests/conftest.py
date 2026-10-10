import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
from io import BytesIO

import pytest
from flask import g
from PIL import Image
from sqlalchemy.pool import StaticPool

from app import create_app
from config import Config
from extensions import db
from models import Item, User

TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "SQLALCHEMY_ENGINE_OPTIONS": {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    },
    "WTF_CSRF_ENABLED": False,
    "SECRET_KEY": "test",
}


def _build_app(overrides, tmp_path):
    config = dict(TEST_CONFIG)
    # Uploads must never reach the repository's real static/avatars or static/uploads folders.
    config["AVATAR_UPLOAD_FOLDER"] = str(tmp_path / "avatars")
    config["ITEM_UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    config.update(overrides)
    app = create_app(config)

    # Safety net: tests must never reach the real database file or upload folders.
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    for key in ("AVATAR_UPLOAD_FOLDER", "ITEM_UPLOAD_FOLDER"):
        assert os.path.abspath(app.config[key]) != os.path.abspath(getattr(Config, key))
    return app


@pytest.fixture
def app(tmp_path):
    app = _build_app({}, tmp_path)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def csrf_app(tmp_path):
    """Same isolated in-memory app, but with CSRF protection enforced."""
    app = _build_app({"WTF_CSRF_ENABLED": True}, tmp_path)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_user(username="testuser", password="password123", email=None):
    user = User(username=username, email=email or f"{username}@example.com")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def make_user(app):
    """Return a factory that creates and persists a user."""
    return _create_user


@pytest.fixture
def make_csrf_user(csrf_app):
    """Same factory, bound to the CSRF-enforcing app."""
    return _create_user


@pytest.fixture
def login(client):
    """Return a helper that logs a user in through the real login route."""

    def _login(username="testuser", password="password123"):
        return client.post(
            "/auth/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    return _login


def _create_item(owner, name="Mochila azul", **fields):
    values = {
        "description": "",
        "category": None,
        "location": "Biblioteca central",
        "event_date": date(2026, 3, 10),
        "status": "lost",
        "contact": None,
        # Test reports are published unless a test asks for another moderation state.
        "approval": "approved",
    }
    values.update(fields)
    item = Item(name=name, user_id=owner.id, **values)
    db.session.add(item)
    db.session.commit()
    return item


@pytest.fixture
def make_item():
    """Return a factory that creates and persists a lost-and-found report owned by the given user.

    Request an app fixture (`app` or `csrf_app`) in the same test so the database exists.
    """
    return _create_item


@pytest.fixture
def png_bytes():
    """Return the bytes of a real, tiny PNG image."""
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


class Browser:
    """Test client that drops Flask-Login's cached user before every request.

    The `app` fixture keeps a single app context open, so `g` is shared by all requests and
    would leak the logged-in user between clients. Popping it makes every request resolve the
    user from its own session cookie, which is what a real server does.
    """

    def __init__(self, app):
        self.client = app.test_client()

    def _call(self, method, *args, **kwargs):
        g.pop("_login_user", None)
        return getattr(self.client, method)(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self._call("get", *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._call("post", *args, **kwargs)

    def put(self, *args, **kwargs):
        return self._call("put", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._call("delete", *args, **kwargs)


@pytest.fixture
def browser_for(app):
    """Return a factory: browser_for("maria") creates the user if needed and logs in."""

    def _browser_for(username=None, password="password123"):
        browser = Browser(app)
        if username is not None:
            if User.query.filter_by(username=username).first() is None:
                _create_user(username, password)
            browser.post("/auth/login", data={"username": username, "password": password})
        return browser

    return _browser_for


CSRF_INPUT = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@pytest.fixture
def csrf_token():
    """Return a helper that reads the CSRF token rendered by a GET page."""

    def _csrf_token(client, url):
        html = client.get(url).get_data(as_text=True)
        match = CSRF_INPUT.search(html)
        assert match is not None, f"no csrf_token input found in {url}"
        return match.group(1)

    return _csrf_token
