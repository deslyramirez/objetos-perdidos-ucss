"""Authentication flows: login, register, logout, safe redirects and CSRF."""

import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pytest

from extensions import db
from models import User


def _text(response):
    return response.get_data(as_text=True)


def _location_path(response):
    return urlparse(response.headers["Location"]).path


def _post_login(client, username="testuser", password="password123", next_url=None):
    url = "/auth/login" if next_url is None else f"/auth/login?next={next_url}"
    return client.post(url, data={"username": username, "password": password})


# --- pages ------------------------------------------------------------------


def test_login_page_renders_spanish_form(client):
    response = client.get("/auth/login")
    html = _text(response)

    assert response.status_code == 200
    assert "Iniciar sesión · Objetos perdidos" in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert "Usuario" in html
    assert "Contraseña" in html
    assert "¿No tiene una cuenta?" in html
    assert "Regístrese" in html
    assert 'href="/auth/register"' in html
    assert "btn-primary" in html
    for english in ("Username", "Password", "Login", "Register"):
        assert english not in html


def test_register_page_renders_spanish_form(client):
    response = client.get("/auth/register")
    html = _text(response)

    assert response.status_code == 200
    assert "Crear cuenta · Objetos perdidos" in html
    for name in ("username", "email", "password", "confirm_password"):
        assert f'name="{name}"' in html
    assert "Correo electrónico" in html
    assert "Confirmar contraseña" in html
    assert "¿Ya tiene una cuenta?" in html
    assert "Inicie sesión" in html
    assert 'href="/auth/login"' in html
    for english in ("Username", "Email", "Confirm Password", "Already have"):
        assert english not in html


# --- split layout (anonymous screens) ---------------------------------------

AUTH_URLS = ["/auth/login", "/auth/register"]
EMOJI_PATTERN = re.compile("[\u2600-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff\ufe0f]")


@pytest.mark.parametrize("url", AUTH_URLS)
def test_auth_pages_render_split_layout_with_ucss_logo_and_dark_card(client, url):
    html = _text(client.get(url))

    for css_class in ("auth-split", "auth-brand", "auth-card", "auth-spheres"):
        assert re.search(rf'class="[^"]*\b{css_class}\b', html), css_class
    assert re.search(r'class="[^"]*\bauth-spheres\b[^"]*"[^>]*aria-hidden="true"', html)
    brand = html.split('auth-brand', 1)[1].split('auth-card', 1)[0]
    assert re.search(r'<img[^>]*src="/static/img/ucss-logo\.png\?v=2"', brand)
    assert 'alt="Universidad Católica Sedes Sapientiae (UCSS)"' in brand
    assert "Sistema de gestión de objetos perdidos y encontrados" in brand
    card = html.split('auth-card', 1)[1]
    assert "Objetos perdidos" in card
    assert 'data-auth-date' in card and "bi-calendar3" in card


@pytest.mark.parametrize("url", AUTH_URLS)
def test_auth_pages_hide_global_navigation_but_keep_accessibility_landmarks(client, url):
    html = _text(client.get(url))

    assert "<header" not in html
    assert "<footer" not in html
    assert 'id="nav-toggle"' not in html
    assert 'aria-label="Navegación principal"' not in html
    assert "Reportar objeto" not in html
    assert 'href="#main-content"' in html and "Saltar al contenido" in html
    assert '<main id="main-content"' in html
    assert html.count("<h1") == 1


@pytest.mark.parametrize("url", AUTH_URLS)
def test_auth_pages_have_no_forgot_password_link_and_no_emojis(client, url):
    html = _text(client.get(url))

    for phrase in ("olvid", "recuperar contraseña", "restablecer"):
        assert phrase not in html.lower(), phrase
    assert not EMOJI_PATTERN.search(html)


def test_login_page_shows_welcome_copy_and_keeps_field_contract(csrf_app):
    html = _text(csrf_app.test_client().get("/auth/login"))

    assert "Bienvenido de nuevo" in html
    assert "Ingresa para gestionar tus reportes" in html
    assert 'id="username"' in html and 'name="username"' in html
    assert 'id="password"' in html and 'name="password"' in html
    assert re.search(r'<input[^>]*name="csrf_token"[^>]*type="hidden"|<input[^>]*type="hidden"[^>]*name="csrf_token"', html)
    assert 'action="/auth/login"' in html
    assert re.search(r'<button[^>]*type="submit"[^>]*>\s*(?:<span>)?Iniciar sesión', html)
    assert html.count("data-password-toggle") == 1


def test_register_page_keeps_field_contract_and_toggles_both_passwords(csrf_app):
    html = _text(csrf_app.test_client().get("/auth/register"))

    for name in ("username", "email", "password", "confirm_password"):
        assert f'id="{name}"' in html and f'name="{name}"' in html
    assert re.search(r'<input[^>]*name="csrf_token"[^>]*type="hidden"|<input[^>]*type="hidden"[^>]*name="csrf_token"', html)
    assert 'action="/auth/register"' in html
    assert re.search(r'<button[^>]*type="submit"[^>]*>\s*Crear cuenta', html)
    assert html.count("data-password-toggle") == 2


@pytest.mark.parametrize("url", AUTH_URLS)
def test_password_toggle_is_an_accessible_icon_button(client, url):
    html = _text(client.get(url))

    buttons = re.findall(r"<button[^>]*data-password-toggle[^>]*>", html)
    assert buttons
    for button in buttons:
        assert 'type="button"' in button
        assert 'aria-label="Mostrar contraseña"' in button
        assert 'aria-pressed="false"' in button
        assert re.search(r'aria-controls="(password|confirm_password)"', button)
    assert "Ocultar contraseña" in html  # label used by the script when the password is visible
    assert "if (!input) return;" in html  # null-safe lookups
    assert "if (badge)" in html


def test_login_errors_keep_aria_wiring_inside_the_dark_card(client):
    html = _text(client.post("/auth/login", data={"username": "", "password": ""}))

    assert html.count('aria-invalid="true"') == 2
    assert 'aria-describedby="username-error"' in html
    assert 'id="password-error"' in html
    card = html.split('auth-card', 1)[1]
    assert "Ingrese su usuario." in card


def test_login_form_renders_csrf_token_when_protection_is_enabled(csrf_app, csrf_token):
    token = csrf_token(csrf_app.test_client(), "/auth/login")

    assert token


def test_flash_messages_render_inside_the_dark_card(client):
    with client.session_transaction() as session:
        session["_flashes"] = [("danger", "Usuario o contraseña incorrectos.")]

    html = _text(client.get("/auth/login"))
    card = html.split('auth-card', 1)[1]

    assert "Usuario o contraseña incorrectos." in card
    assert 'role="alert"' in card
    assert html.count("Usuario o contraseña incorrectos.") == 1


# --- login ------------------------------------------------------------------


def test_login_success_redirects_home_with_spanish_flash(client, make_user):
    make_user("testuser", "password123")

    response = _post_login(client)

    assert response.status_code == 302
    assert _location_path(response) == "/dashboard/"

    page = client.get("/")
    html = _text(page)
    assert page.status_code == 200
    assert "Sesión iniciada correctamente." in html
    assert "testuser" in html
    assert "Cerrar sesión" in html
    assert "Iniciar sesión" not in html


def test_login_updates_last_login(client, make_user):
    user = make_user("testuser", "password123")
    assert user.last_login is None

    _post_login(client)

    db.session.refresh(user)
    assert user.last_login is not None
    assert abs(datetime.utcnow() - user.last_login) < timedelta(seconds=30)


def test_login_with_wrong_password_is_rejected(client, make_user):
    make_user("testuser", "password123")

    response = _post_login(client, password="wrong-password")

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    html = _text(client.get("/auth/login"))
    assert "Usuario o contraseña incorrectos." in html
    assert client.get("/").status_code == 302


def test_login_with_unknown_user_uses_the_same_message(client):
    response = _post_login(client, username="nobody")

    assert response.status_code == 302
    assert "Usuario o contraseña incorrectos." in _text(client.get("/auth/login"))


def test_wrong_password_does_not_update_last_login(client, make_user):
    user = make_user("testuser", "password123")

    _post_login(client, password="wrong-password")

    db.session.refresh(user)
    assert user.last_login is None


def test_login_with_empty_fields_shows_inline_errors(client):
    response = client.post("/auth/login", data={"username": "", "password": ""})
    html = _text(response)

    assert response.status_code == 200
    assert "Ingrese su usuario." in html
    assert "Ingrese su contraseña." in html


def test_login_has_no_minimum_password_length(client, make_user):
    make_user("testuser", "abc")

    response = _post_login(client, password="abc")

    assert response.status_code == 302
    assert _location_path(response) == "/dashboard/"


def test_login_honors_safe_next_target(client, make_user):
    make_user("testuser", "password123")

    response = _post_login(client, next_url="/search")

    assert response.status_code == 302
    assert _location_path(response) == "/search"


@pytest.mark.parametrize(
    "next_url",
    [
        "//evil.com",
        "https://evil.com",
        "/\\evil.com",
        "javascript:alert(1)",
        "evil.com",
    ],
)
def test_login_ignores_unsafe_next_target(client, make_user, next_url):
    make_user("testuser", "password123")

    response = _post_login(client, next_url=next_url)

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).netloc == ""
    assert _location_path(response) == "/dashboard/"


def test_login_form_keeps_safe_next_in_action(client):
    html = _text(client.get("/auth/login?next=/search"))

    assert 'action="/auth/login?next=%2Fsearch"' in html


def test_login_form_drops_unsafe_next_from_action(client):
    html = _text(client.get("/auth/login?next=//evil.com"))

    assert "evil.com" not in html
    assert 'action="/auth/login"' in html


def test_wrong_password_keeps_safe_next(client, make_user):
    make_user("testuser", "password123")

    response = _post_login(client, password="wrong-password", next_url="/search")

    assert response.headers["Location"].endswith("/auth/login?next=%2Fsearch")


def test_protected_page_redirects_to_login_with_next_and_flash(client):
    response = client.get("/search", follow_redirects=True)
    html = _text(response)

    assert response.request.path == "/auth/login"
    assert response.request.args["next"] == "/search"
    assert "Inicie sesión para acceder a esta página." in html


@pytest.mark.parametrize("url", ["/auth/login", "/auth/register"])
def test_authenticated_user_is_redirected_away_from_auth_pages(
    client, make_user, login, url
):
    make_user("testuser", "password123")
    login("testuser", "password123")

    response = client.get(url)

    assert response.status_code == 302
    assert _location_path(response) == "/dashboard/"


@pytest.mark.parametrize("url", ["/auth/login", "/auth/register"])
def test_authenticated_user_cannot_post_to_auth_pages(client, make_user, login, url):
    make_user("testuser", "password123")
    login("testuser", "password123")

    response = client.post(url, data={"username": "other", "password": "password123"})

    assert response.status_code == 302
    assert _location_path(response) == "/dashboard/"


# --- register ---------------------------------------------------------------

VALID_REGISTRATION = {
    "username": "nuevo",
    "email": "nuevo@example.com",
    "password": "secreto1",
    "confirm_password": "secreto1",
}


def _register(client, **overrides):
    return client.post("/auth/register", data={**VALID_REGISTRATION, **overrides})


def test_register_creates_user_with_hashed_password(client):
    response = _register(client)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    assert "Cuenta creada correctamente. Inicie sesión." in _text(
        client.get("/auth/login")
    )

    user = User.query.filter_by(username="nuevo").one()
    assert user.email == "nuevo@example.com"
    assert user.password_hash != "secreto1"
    assert user.check_password("secreto1")
    assert user.created_at is not None


def test_registered_user_can_log_in(client):
    _register(client)

    response = _post_login(client, username="nuevo", password="secreto1")

    assert _location_path(response) == "/dashboard/"


def test_register_trims_username_and_email(client):
    _register(client, username="  nuevo  ", email="  nuevo@example.com  ")

    user = User.query.one()
    assert user.username == "nuevo"
    assert user.email == "nuevo@example.com"


def test_register_rejects_duplicate_username(client, make_user):
    make_user("nuevo", "password123", email="otro@example.com")

    response = _register(client)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/register"
    assert "El nombre de usuario ya está en uso." in _text(
        client.get("/auth/register")
    )
    assert User.query.count() == 1


def test_register_rejects_duplicate_email(client, make_user):
    make_user("existente", "password123", email="nuevo@example.com")

    response = _register(client)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/register"
    assert "El correo electrónico ya está en uso." in _text(
        client.get("/auth/register")
    )
    assert User.query.count() == 1


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"confirm_password": "distinta"}, "Las contraseñas no coinciden."),
        (
            {"password": "corta", "confirm_password": "corta"},
            "La contraseña debe tener al menos 6 caracteres.",
        ),
        ({"email": "no-es-un-correo"}, "Ingrese un correo electrónico válido."),
        ({"email": ""}, "Ingrese su correo electrónico."),
        (
            {"username": "ab"},
            "El nombre de usuario debe tener entre 3 y 64 caracteres.",
        ),
        ({"username": ""}, "Ingrese un nombre de usuario."),
        ({"password": "", "confirm_password": ""}, "Ingrese una contraseña."),
    ],
)
def test_register_validation_errors_are_shown_and_create_nothing(
    client, overrides, message
):
    response = _register(client, **overrides)

    assert response.status_code == 200
    assert message in _text(response)
    assert User.query.count() == 0


def test_register_keeps_entered_values_on_validation_error(client):
    response = _register(client, confirm_password="distinta")

    html = _text(response)
    assert 'value="nuevo"' in html
    assert 'value="nuevo@example.com"' in html
    assert "secreto1" not in html


# --- logout -----------------------------------------------------------------


def test_logout_get_is_not_allowed(client, make_user, login):
    make_user("testuser", "password123")
    login("testuser", "password123")

    assert client.get("/auth/logout").status_code == 405
    assert client.get("/").status_code == 200


def test_logout_post_clears_session_and_redirects_to_login(client, make_user, login):
    make_user("testuser", "password123")
    login("testuser", "password123")

    response = client.post("/auth/logout")

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    assert "Sesión cerrada correctamente." in _text(client.get("/auth/login"))

    protected = client.get("/")
    assert protected.status_code == 302
    assert _location_path(protected) == "/auth/login"


def test_logout_requires_login(client):
    response = client.post("/auth/logout")

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"


# --- CSRF -------------------------------------------------------------------


def test_login_post_without_csrf_token_is_redirected_with_flash(
    csrf_app, make_csrf_user
):
    make_csrf_user("testuser", "password123")
    client = csrf_app.test_client()

    response = client.post(
        "/auth/login",
        data={"username": "testuser", "password": "password123"},
        headers={"Referer": "http://localhost/auth/login"},
    )

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    html = _text(client.get("/auth/login"))
    assert "La sesión del formulario expiró o no es válida. Intente nuevamente." in html
    # The credentials were valid, so the user must NOT be signed in.
    assert client.get("/", follow_redirects=False).status_code == 302


def test_csrf_failure_without_referrer_goes_to_login_for_anonymous(csrf_app):
    client = csrf_app.test_client()

    response = client.post("/auth/register", data=VALID_REGISTRATION)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    assert User.query.count() == 0


@pytest.mark.parametrize(
    "referrer",
    ["http://evil.com/phish", "https://evil.com/\\evil", "javascript:alert(1)"],
)
def test_csrf_failure_ignores_foreign_referrer(csrf_app, referrer):
    client = csrf_app.test_client()

    response = client.post(
        "/auth/login",
        data={"username": "x", "password": "y"},
        headers={"Referer": referrer},
    )

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).netloc == ""
    assert _location_path(response) == "/auth/login"


def test_csrf_failure_returns_to_same_site_referrer(csrf_app):
    client = csrf_app.test_client()

    response = client.post(
        "/auth/register",
        data=VALID_REGISTRATION,
        headers={"Referer": "http://localhost/auth/register?x=1"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/register?x=1")


def test_login_with_valid_csrf_token_works(csrf_app, make_csrf_user, csrf_token):
    make_csrf_user("testuser", "password123")
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")

    response = client.post(
        "/auth/login",
        data={"username": "testuser", "password": "password123", "csrf_token": token},
    )

    assert response.status_code == 302
    assert _location_path(response) == "/dashboard/"
    assert client.get("/").status_code == 200


def test_logout_form_without_csrf_token_is_rejected(
    csrf_app, make_csrf_user, csrf_token
):
    make_csrf_user("testuser", "password123")
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")
    client.post(
        "/auth/login",
        data={"username": "testuser", "password": "password123", "csrf_token": token},
    )

    rejected = client.post("/auth/logout")
    assert rejected.status_code == 302
    assert client.get("/").status_code == 200  # still signed in

    token = csrf_token(client, "/")
    accepted = client.post("/auth/logout", data={"csrf_token": token})
    assert accepted.status_code == 302
    assert _location_path(accepted) == "/auth/login"
    assert client.get("/").status_code == 302  # signed out
