"""Profile pages: view, avatar upload, edit profile and change password."""

import os
import re
import struct
import zlib
from io import BytesIO
from urllib.parse import urlparse

import pytest
from flask import url_for

from config import BASE_DIR
from extensions import db
from models import Item, User
from utils import fecha, fecha_hora

AVATAR_NAME = re.compile(r"^(\d+)_(\d{20})\.(\w+)$")


def _png_bytes():
    """Return a valid 1x1 PNG image."""

    def chunk(tag, data):
        body = tag + data
        checksum = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + checksum

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


PNG = _png_bytes()


def _text(response):
    return response.get_data(as_text=True)


def _location_path(response):
    return urlparse(response.headers["Location"]).path


@pytest.fixture
def user(make_user):
    return make_user("maria", "password123", email="maria@example.com")


@pytest.fixture
def logged_in(client, user, login):
    login("maria", "password123")
    return client


@pytest.fixture
def avatar_dir(app):
    folder = app.config["AVATAR_UPLOAD_FOLDER"]
    real_folder = os.path.join(BASE_DIR, "static", "avatars")
    assert os.path.abspath(folder) != os.path.abspath(real_folder)
    return folder


def _upload(client, filename="foto.png", content=PNG, **kwargs):
    data = {} if filename is None else {"avatar": (BytesIO(content), filename)}
    return client.post(
        "/profile/avatar", data=data, content_type="multipart/form-data", **kwargs
    )


def _saved_files(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


# --- routes -----------------------------------------------------------------


def test_profile_route_table(app):
    with app.test_request_context("/"):
        assert url_for("profile.view_profile") == "/profile/"
        assert url_for("profile.update_avatar") == "/profile/avatar"
        assert url_for("profile.edit_profile") == "/profile/edit"
        assert url_for("profile.change_password") == "/profile/change-password"


def test_old_duplicated_profile_urls_are_gone(logged_in):
    for url in ("/profile/profile", "/profile/profile/edit", "/profile/profile/change-password"):
        assert logged_in.get(url).status_code == 404


def test_profile_module_has_no_import_time_upload_folder():
    import profile as profile_module

    assert not hasattr(profile_module, "UPLOAD_FOLDER")


def test_creating_the_app_does_not_create_the_avatar_folder(app, avatar_dir):
    assert not os.path.exists(avatar_dir)


@pytest.mark.parametrize(
    "method, url",
    [
        ("get", "/profile/"),
        ("get", "/profile/edit"),
        ("get", "/profile/change-password"),
        ("post", "/profile/avatar"),
        ("post", "/profile/edit"),
        ("post", "/profile/change-password"),
    ],
)
def test_anonymous_access_redirects_to_login_with_flash(client, method, url):
    response = getattr(client, method)(url)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    assert "next=" in response.headers["Location"]
    assert "Inicie sesión para acceder a esta página." in _text(
        client.get("/auth/login")
    )


# --- view -------------------------------------------------------------------


def test_profile_view_shows_identity_and_stats(logged_in, user):
    for index in range(3):
        db.session.add(Item(name=f"Objeto {index}", location="Biblioteca", user_id=user.id))
    db.session.commit()

    response = logged_in.get("/profile/")
    html = _text(response)

    assert response.status_code == 200
    assert "Perfil · Objetos perdidos" in html
    assert "maria" in html
    assert "maria@example.com" in html
    assert "Reportes registrados" in html
    assert re.search(r"Reportes registrados</dt>\s*<dd[^>]*>\s*3\s*</dd>", html)
    assert "Miembro desde" in html
    assert fecha(user.created_at) in html
    assert "Último acceso" in html
    assert fecha_hora(user.last_login) in html
    assert "Zona horaria: UTC" in html
    assert "Editar perfil" in html
    assert "Cambiar contraseña" in html
    assert 'href="/profile/edit"' in html
    assert 'href="/profile/change-password"' in html


def test_profile_view_shows_dash_when_user_never_logged_in(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True

    html = _text(client.get("/profile/"))

    assert "Último acceso" in html
    assert "—" in html
    assert user.last_login is None


def test_profile_view_uses_default_avatar_when_file_is_missing(logged_in, user):
    user.avatar = "gone.png"
    db.session.commit()

    html = _text(logged_in.get("/profile/"))

    assert "img/avatar-default.svg" in html
    assert "avatars/gone.png" not in html
    assert re.search(r'<img[^>]*alt="Foto de perfil de maria"', html)
    assert "object-cover" in html


def test_profile_view_shows_bio_or_placeholder(logged_in, user):
    assert "Aún no se ha agregado una biografía." in _text(logged_in.get("/profile/"))

    user.bio = "Responsable del almacén central."
    db.session.commit()
    html = _text(logged_in.get("/profile/"))

    assert "Responsable del almacén central." in html
    assert "Aún no se ha agregado una biografía." not in html


def test_profile_view_has_avatar_upload_form(logged_in):
    html = _text(logged_in.get("/profile/"))

    form = re.search(
        r'<form[^>]*action="/profile/avatar"[^>]*>(.*?)</form>', html, re.DOTALL
    )
    assert form is not None
    assert re.search(r'method="post"', form.group(0), re.IGNORECASE)
    assert 'enctype="multipart/form-data"' in form.group(0)
    assert 'name="avatar"' in form.group(1)
    assert "Actualizar foto" in form.group(1)
    assert "Show" not in html and "Ver más" not in html


def test_profile_pages_have_no_english_ui_text(logged_in):
    for url in ("/profile/", "/profile/edit", "/profile/change-password"):
        html = _text(logged_in.get(url))
        for english in ("Profile", "Password", "Save", "Username", "Email", "Upload"):
            assert english not in html, (url, english)


# --- avatar upload ----------------------------------------------------------


def test_valid_avatar_is_saved_with_user_id_and_timestamp(logged_in, user, avatar_dir):
    response = _upload(logged_in)

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"

    files = _saved_files(avatar_dir)
    assert len(files) == 1
    match = AVATAR_NAME.match(files[0])
    assert match is not None
    assert match.group(1) == str(user.id)
    assert match.group(3) == "png"
    with open(os.path.join(avatar_dir, files[0]), "rb") as handle:
        assert handle.read() == PNG

    db.session.refresh(user)
    assert user.avatar == files[0]

    html = _text(logged_in.get("/profile/"))
    assert "Foto de perfil actualizada correctamente." in html
    assert f"avatars/{files[0]}" in html


def test_avatar_folder_is_created_at_upload_time(logged_in, avatar_dir):
    assert not os.path.exists(avatar_dir)

    _upload(logged_in)

    assert os.path.isdir(avatar_dir)


def test_avatar_extension_is_normalised_to_lowercase(logged_in, user, avatar_dir):
    _upload(logged_in, filename="FOTO.PNG")

    db.session.refresh(user)
    assert user.avatar.endswith(".png")


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "gif", "webp"])
def test_all_allowed_extensions_are_accepted(logged_in, user, avatar_dir, extension):
    _upload(logged_in, filename=f"foto.{extension}")

    db.session.refresh(user)
    assert user.avatar.endswith(f".{extension}")
    assert os.path.isfile(os.path.join(avatar_dir, user.avatar))


def test_unsafe_original_filename_never_reaches_the_disk_name(logged_in, user, avatar_dir):
    _upload(logged_in, filename="../../etc/pass wd.png")

    files = _saved_files(avatar_dir)
    assert len(files) == 1
    assert AVATAR_NAME.match(files[0])


def test_new_upload_removes_previous_own_avatar(logged_in, user, avatar_dir):
    os.makedirs(avatar_dir)
    old_name = f"{user.id}_20200101000000000000.png"
    with open(os.path.join(avatar_dir, old_name), "wb") as handle:
        handle.write(b"old")
    user.avatar = old_name
    db.session.commit()

    _upload(logged_in)

    files = _saved_files(avatar_dir)
    assert old_name not in files
    assert len(files) == 1
    db.session.refresh(user)
    assert user.avatar == files[0]


def test_consecutive_uploads_keep_a_single_unique_file(logged_in, user, avatar_dir):
    _upload(logged_in)
    first = _saved_files(avatar_dir)
    _upload(logged_in)
    second = _saved_files(avatar_dir)

    assert len(first) == len(second) == 1
    assert first != second


@pytest.mark.parametrize(
    "legacy_name",
    ["legacy-photo.png", "default.png", "99_20200101000000000000.png", "11_20200101000000000000.png"],
)
def test_upload_never_deletes_legacy_default_or_foreign_files(
    logged_in, user, avatar_dir, legacy_name
):
    assert user.id == 1
    os.makedirs(avatar_dir)
    with open(os.path.join(avatar_dir, legacy_name), "wb") as handle:
        handle.write(b"keep me")
    user.avatar = legacy_name
    db.session.commit()

    _upload(logged_in)

    files = _saved_files(avatar_dir)
    assert legacy_name in files
    assert len(files) == 2
    db.session.refresh(user)
    assert user.avatar != legacy_name


def test_upload_does_not_touch_other_users_avatar(logged_in, user, make_user, avatar_dir):
    other = make_user("jose", "password123")
    os.makedirs(avatar_dir)
    other_name = f"{other.id}_20200101000000000000.png"
    with open(os.path.join(avatar_dir, other_name), "wb") as handle:
        handle.write(b"other")
    other.avatar = other_name
    db.session.commit()

    _upload(logged_in)

    assert other_name in _saved_files(avatar_dir)


@pytest.mark.parametrize(
    "filename",
    ["page.html", "image.svg", "virus.exe", "script.php", "photo.png.exe", "noextension"],
)
def test_invalid_extension_is_rejected(logged_in, user, avatar_dir, filename):
    user.avatar = "before.png"
    db.session.commit()

    response = _upload(logged_in, filename=filename)

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    html = _text(logged_in.get("/profile/"))
    assert "Formato no permitido. Use PNG, JPG, GIF o WEBP." in html
    assert _saved_files(avatar_dir) == []
    db.session.refresh(user)
    assert user.avatar == "before.png"


def test_invalid_upload_keeps_the_existing_avatar_file(logged_in, user, avatar_dir):
    os.makedirs(avatar_dir)
    current = f"{user.id}_20200101000000000000.png"
    with open(os.path.join(avatar_dir, current), "wb") as handle:
        handle.write(b"current")
    user.avatar = current
    db.session.commit()

    _upload(logged_in, filename="page.html")

    assert _saved_files(avatar_dir) == [current]


def test_missing_file_is_rejected(logged_in, user, avatar_dir):
    response = _upload(logged_in, filename=None)

    assert response.status_code == 302
    assert "Seleccione una imagen." in _text(logged_in.get("/profile/"))
    assert _saved_files(avatar_dir) == []


def test_empty_filename_is_rejected(logged_in, avatar_dir):
    response = _upload(logged_in, filename="", content=b"")

    assert response.status_code == 302
    assert "Seleccione una imagen." in _text(logged_in.get("/profile/"))
    assert _saved_files(avatar_dir) == []


def test_filename_that_sanitises_to_nothing_is_rejected(logged_in, avatar_dir):
    # secure_filename("....png") returns "png": no extension is left to validate.
    response = _upload(logged_in, filename="....png")

    assert response.status_code == 302
    html = _text(logged_in.get("/profile/"))
    assert "Formato no permitido. Use PNG, JPG, GIF o WEBP." in html
    assert "Foto de perfil actualizada correctamente." not in html
    assert _saved_files(avatar_dir) == []


def test_avatar_upload_does_not_accept_get(logged_in):
    assert logged_in.get("/profile/avatar").status_code == 405


# --- edit profile -----------------------------------------------------------


def test_edit_profile_form_is_prefilled_and_spanish(logged_in, user):
    user.bio = "Hola equipo"
    db.session.commit()

    response = logged_in.get("/profile/edit")
    html = _text(response)

    assert response.status_code == 200
    assert "Editar perfil · Objetos perdidos" in html
    assert 'value="maria"' in html
    assert 'value="maria@example.com"' in html
    assert "Hola equipo" in html
    assert "Usuario" in html
    assert "Correo electrónico" in html
    assert "Biografía" in html
    assert "Guardar cambios" in html
    assert "Cancelar" in html
    assert 'href="/profile/"' in html
    assert "/ 300 caracteres" in html
    assert 'name="phone"' not in html
    assert 'name="location"' not in html


def test_edit_profile_success_updates_user_and_redirects(logged_in, user):
    response = logged_in.post(
        "/profile/edit",
        data={"username": "  maria2  ", "email": " maria2@example.com ", "bio": "Nueva biografía"},
    )

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    assert "Perfil actualizado correctamente." in _text(logged_in.get("/profile/"))
    db.session.refresh(user)
    assert user.username == "maria2"
    assert user.email == "maria2@example.com"
    assert user.bio == "Nueva biografía"


def test_edit_profile_allows_keeping_own_username_and_email(logged_in, user):
    response = logged_in.post(
        "/profile/edit",
        data={"username": "maria", "email": "maria@example.com", "bio": ""},
    )

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"


def test_edit_profile_accepts_empty_bio_and_exactly_300_characters(logged_in, user):
    base = {"username": "maria", "email": "maria@example.com"}

    assert logged_in.post("/profile/edit", data={**base, "bio": "x" * 300}).status_code == 302
    db.session.refresh(user)
    assert user.bio == "x" * 300

    assert logged_in.post("/profile/edit", data={**base, "bio": ""}).status_code == 302
    db.session.refresh(user)
    assert not user.bio


def _edit(client, **overrides):
    data = {"username": "maria", "email": "maria@example.com", "bio": ""}
    data.update(overrides)
    return client.post("/profile/edit", data=data)


def test_edit_profile_rejects_username_taken_by_another_user(logged_in, user, make_user):
    make_user("jose", "password123")

    response = _edit(logged_in, username="jose")

    assert response.status_code == 200
    assert "El nombre de usuario ya está en uso." in _text(response)
    db.session.refresh(user)
    assert user.username == "maria"


def test_edit_profile_rejects_email_taken_by_another_user(logged_in, user, make_user):
    make_user("jose", "password123", email="jose@example.com")

    response = _edit(logged_in, email="jose@example.com")

    assert response.status_code == 200
    assert "El correo electrónico ya está en uso." in _text(response)
    db.session.refresh(user)
    assert user.email == "maria@example.com"


@pytest.mark.parametrize("email", ["", "   "])
def test_edit_profile_rejects_empty_email(logged_in, user, email):
    response = _edit(logged_in, email=email)

    assert response.status_code == 200
    assert "Ingrese su correo electrónico." in _text(response)
    db.session.refresh(user)
    assert user.email == "maria@example.com"


def test_edit_profile_rejects_invalid_email(logged_in, user):
    response = _edit(logged_in, email="no-es-un-correo")

    assert response.status_code == 200
    assert "Ingrese un correo electrónico válido." in _text(response)


def test_edit_profile_rejects_bio_over_300_characters(logged_in, user):
    response = _edit(logged_in, bio="x" * 301)

    assert response.status_code == 200
    assert "La biografía no puede superar los 300 caracteres." in _text(response)
    db.session.refresh(user)
    assert not user.bio


@pytest.mark.parametrize("username", ["ab", "x" * 65, ""])
def test_edit_profile_rejects_invalid_username_length(logged_in, user, username):
    response = _edit(logged_in, username=username)

    assert response.status_code == 200
    html = _text(response)
    assert (
        "El nombre de usuario debe tener entre 3 y 64 caracteres." in html
        or "Ingrese un nombre de usuario." in html
    )
    db.session.refresh(user)
    assert user.username == "maria"


def test_failed_edit_keeps_navbar_name_and_entered_values(logged_in, user):
    response = _edit(logged_in, email="roto", bio="Texto conservado")

    html = _text(response)
    assert "Texto conservado" in html
    assert 'value="roto"' in html
    assert "maria" in html


# --- change password --------------------------------------------------------


def _change(client, current="password123", new="nueva-clave", confirm=None):
    return client.post(
        "/profile/change-password",
        data={
            "current_password": current,
            "new_password": new,
            "confirm_password": new if confirm is None else confirm,
        },
    )


def test_change_password_form_is_spanish(logged_in):
    response = logged_in.get("/profile/change-password")
    html = _text(response)

    assert response.status_code == 200
    assert "Cambiar contraseña · Objetos perdidos" in html
    for name in ("current_password", "new_password", "confirm_password"):
        assert f'name="{name}"' in html
    assert "Contraseña actual" in html
    assert "Nueva contraseña" in html
    assert "Confirmar nueva contraseña" in html
    assert "Cancelar" in html
    assert 'href="/profile/"' in html
    assert 'type="password"' in html


def test_change_password_success(client, logged_in, user):
    old_hash = user.password_hash

    response = _change(logged_in)

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    assert "Contraseña actualizada correctamente." in _text(logged_in.get("/profile/"))
    db.session.refresh(user)
    assert user.password_hash != old_hash
    assert user.password_hash != "nueva-clave"
    assert user.check_password("nueva-clave")
    assert not user.check_password("password123")

    client.post("/auth/logout")
    old = client.post("/auth/login", data={"username": "maria", "password": "password123"})
    assert _location_path(old) == "/auth/login"
    new = client.post("/auth/login", data={"username": "maria", "password": "nueva-clave"})
    assert _location_path(new) == "/"


def test_change_password_rejects_wrong_current_password(logged_in, user):
    response = _change(logged_in, current="incorrecta")

    assert response.status_code == 200
    assert "La contraseña actual es incorrecta." in _text(response)
    db.session.refresh(user)
    assert user.check_password("password123")


def test_change_password_rejects_mismatch(logged_in, user):
    response = _change(logged_in, confirm="otra-distinta")

    assert response.status_code == 200
    assert "Las contraseñas no coinciden." in _text(response)
    db.session.refresh(user)
    assert user.check_password("password123")


def test_change_password_rejects_short_password(logged_in, user):
    response = _change(logged_in, new="corta")

    assert response.status_code == 200
    assert "La nueva contraseña debe tener al menos 6 caracteres." in _text(response)
    db.session.refresh(user)
    assert user.check_password("password123")


def test_change_password_requires_every_field(logged_in, user):
    response = logged_in.post("/profile/change-password", data={})

    html = _text(response)
    assert response.status_code == 200
    assert "Ingrese su contraseña actual." in html
    assert "Ingrese la nueva contraseña." in html
    assert "Confirme la nueva contraseña." in html


# --- CSRF -------------------------------------------------------------------


@pytest.fixture
def csrf_session(csrf_app, make_csrf_user, csrf_token):
    """Return a logged-in client for the CSRF-enforcing app."""
    user = make_csrf_user("maria", "password123", email="maria@example.com")
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")
    response = client.post(
        "/auth/login",
        data={"username": "maria", "password": "password123", "csrf_token": token},
    )
    assert _location_path(response) == "/"
    return client, user


def test_change_password_without_csrf_token_is_redirected_with_flash(csrf_session):
    client, user = csrf_session

    response = client.post(
        "/profile/change-password",
        data={
            "current_password": "password123",
            "new_password": "nueva-clave",
            "confirm_password": "nueva-clave",
        },
        headers={"Referer": "http://localhost/profile/change-password"},
    )

    assert response.status_code == 302
    assert _location_path(response) == "/profile/change-password"
    html = _text(client.get("/profile/change-password"))
    assert "La sesión del formulario expiró o no es válida. Intente nuevamente." in html
    db.session.refresh(user)
    assert user.check_password("password123")


def test_csrf_failure_for_logged_in_user_without_referrer_goes_home(csrf_session):
    client, _ = csrf_session

    response = client.post("/profile/edit", data={"username": "x", "email": "x@example.com"})

    assert response.status_code == 302
    assert _location_path(response) == "/"


def test_change_password_with_csrf_token_works(csrf_session, csrf_token):
    client, user = csrf_session
    token = csrf_token(client, "/profile/change-password")

    response = client.post(
        "/profile/change-password",
        data={
            "current_password": "password123",
            "new_password": "nueva-clave",
            "confirm_password": "nueva-clave",
            "csrf_token": token,
        },
    )

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    db.session.refresh(user)
    assert user.check_password("nueva-clave")


def test_profile_forms_render_a_csrf_token(csrf_session):
    client, _ = csrf_session

    for url, action in (
        ("/profile/", "/profile/avatar"),
        ("/profile/edit", None),
        ("/profile/change-password", None),
    ):
        html = _text(client.get(url))
        form = re.search(r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % action, html, re.DOTALL) if action else None
        scope = form.group(1) if form else html
        assert 'name="csrf_token"' in scope, url


def test_avatar_upload_without_csrf_token_is_rejected(csrf_session, csrf_app):
    client, user = csrf_session

    response = _upload(client, headers={"Referer": "http://localhost/profile/"})

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    assert "La sesión del formulario expiró o no es válida." in _text(client.get("/profile/"))
    assert not os.path.exists(csrf_app.config["AVATAR_UPLOAD_FOLDER"])


def test_avatar_upload_with_csrf_token_works(csrf_session, csrf_app, csrf_token):
    client, user = csrf_session
    token = csrf_token(client, "/profile/")

    response = client.post(
        "/profile/avatar",
        data={"avatar": (BytesIO(PNG), "foto.png"), "csrf_token": token},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert _location_path(response) == "/profile/"
    files = _saved_files(csrf_app.config["AVATAR_UPLOAD_FOLDER"])
    assert len(files) == 1 and AVATAR_NAME.match(files[0])
