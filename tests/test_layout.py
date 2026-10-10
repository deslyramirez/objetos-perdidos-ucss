"""Layout, error pages and shared macros of the design system."""

import json
import re
from types import SimpleNamespace

import pytest

from extensions import db
from flask import g, render_template_string
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DecimalField,
    FileField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange

EMOJI_PATTERN = re.compile(
    "[\u2600-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff\ufe0f]"
)

NAV_LABELS = (
    "Centro de reportes",
    "Nuevo reporte",
    "Mensajes",
)
ADMIN_NAV_LABELS = ("Panel mensual", "Moderación", "Importar CSV", "Exportar CSV")

REMOVED_NAV_LABELS = ("Buscar", "Agregar artículo", "Inventario")


@pytest.fixture
def admin_maria(logged_in):
    from models import User

    User.query.filter_by(username="maria").one().is_admin = True
    db.session.commit()


@pytest.fixture
def logged_in(client, make_user, login):
    make_user("maria", "password123")
    login("maria", "password123")
    return client


def _text(response):
    return response.get_data(as_text=True)


# --- anonymous layout -------------------------------------------------------


def test_anonymous_page_uses_spanish_language_and_auth_links(client):
    # The login/register screens hide the global header, so the anonymous header is checked on
    # an ordinary base-layout page (the 404 page).
    html = _text(client.get("/no-such-page"))

    assert '<html lang="es"' in html
    assert "Iniciar sesión" in html
    assert "Registrarse" in html
    assert "Cerrar sesión" not in html
    assert "Nuevo reporte" not in html
    assert '<html lang="es"' in _text(client.get("/auth/login"))


def test_anonymous_page_has_pinned_assets_and_linked_stylesheet(client):
    html = _text(client.get("/auth/login"))

    assert "https://cdn.tailwindcss.com/3.4.17" in html
    assert "bootstrap-icons@1.11.3" in html
    assert re.search(r"/static/css/app\.css\?v=\d+", html)
    assert "chart.js" not in html.lower()


def test_page_title_has_app_suffix(client):
    html = _text(client.get("/auth/login"))

    assert re.search(r"<title>\s*[^<]*· Objetos perdidos\s*</title>", html)


def test_title_falls_back_to_app_name_when_block_is_empty(app):
    with app.test_request_context("/"):
        html = render_template_string('{% extends "base.html" %}')

    assert re.search(r"<title>\s*Objetos perdidos\s*</title>", html)


def test_old_dead_stylesheet_is_gone():
    import os

    from config import BASE_DIR

    assert not os.path.exists(os.path.join(BASE_DIR, "static", "css", "style.css"))
    assert os.path.exists(os.path.join(BASE_DIR, "static", "css", "app.css"))


# --- authenticated layout ---------------------------------------------------


def test_authenticated_navbar_shows_links_and_user(logged_in):
    html = _text(logged_in.get("/"))

    for label in NAV_LABELS:
        assert label in html, label
    nav = html.split("<main", 1)[0]
    for label in REMOVED_NAV_LABELS + ADMIN_NAV_LABELS:
        assert label not in nav, label
    assert "Objetos perdidos" in nav
    assert "maria" in html
    assert "Perfil" in html
    assert "Cambiar contraseña" in html
    assert "Iniciar sesión" not in html
    assert "Registrarse" not in html


def test_logout_is_a_post_form_with_csrf_token(logged_in):
    html = _text(logged_in.get("/"))

    form = re.search(
        r'<form[^>]*action="/auth/logout"[^>]*>(.*?)</form>', html, re.DOTALL
    )
    assert form is not None
    assert re.search(r'method="post"', form.group(0), re.IGNORECASE)
    assert 'name="csrf_token"' in form.group(1)
    assert "Cerrar sesión" in form.group(1)


def test_navbar_profile_links_use_the_clean_urls(logged_in):
    html = _text(logged_in.get("/"))

    assert 'href="/profile/"' in html
    assert 'href="/profile/change-password"' in html


def test_navbar_logout_form_signs_the_user_out(logged_in):
    form_action = re.search(
        r'<form[^>]*action="([^"]+)"[^>]*>\s*<input[^>]*csrf_token',
        _text(logged_in.get("/")),
    ).group(1)

    response = logged_in.post(form_action)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    assert logged_in.get("/").status_code == 302


def test_navbar_marks_active_link(logged_in, admin_maria):
    html = _text(logged_in.get("/dashboard/"))

    active = re.findall(r'<a[^>]*aria-current="page"[^>]*>', html)
    assert len(active) == 1
    assert 'href="/dashboard/"' in active[0]

    html = _text(logged_in.get("/import/csv"))
    active = re.findall(r'<a[^>]*aria-current="page"[^>]*>', html)
    assert len(active) == 1
    assert "/import/csv" in active[0]


def test_navbar_avatar_has_alt_text(logged_in):
    html = _text(logged_in.get("/"))

    assert re.search(r'<img[^>]*avatar-default\.svg[^>]*alt="[^"]+"', html) or re.search(
        r'<img[^>]*alt="[^"]+"[^>]*avatar-default\.svg', html
    )


def test_footer_has_current_year(logged_in):
    from datetime import datetime, timezone

    html = _text(logged_in.get("/"))

    assert f"© {datetime.now(timezone.utc).year} Objetos perdidos" in html


def _chrome(html):
    """Return the base layout markup around <main> (navbar, footer, scripts)."""
    head, rest = html.split("<main", 1)
    return head + rest.split("</main>", 1)[1]


def test_base_layout_has_no_emojis(app, logged_in):
    assert not EMOJI_PATTERN.search(_chrome(_text(logged_in.get("/"))))
    # The fixture keeps one app context open, so Flask-Login's cached user would leak into
    # the anonymous client below; drop it to simulate a fresh visitor.
    g.pop("_login_user", None)
    assert not EMOJI_PATTERN.search(_chrome(_text(app.test_client().get("/auth/login"))))
    assert EMOJI_PATTERN.search("\U0001f4ca")  # the pattern really detects emojis


def test_navbar_script_is_null_safe_for_anonymous_users(client):
    html = _text(client.get("/auth/login"))

    # No user menu in the DOM, so the script must guard every lookup.
    assert 'id="user-menu"' not in html
    assert "if (userButton && userMenu)" in html


AUTHENTICATED_PAGES = (
    "/",
    "/add",
    "/profile/",
    "/profile/edit",
    "/profile/change-password",
    "/messages/",
)
ADMIN_PAGES = ("/dashboard/", "/import/csv", "/admin/moderacion")


@pytest.mark.parametrize("url", AUTHENTICATED_PAGES)
def test_every_authenticated_page_renders_inside_the_base_layout(logged_in, url):
    response = logged_in.get(url)
    html = _text(response)

    assert response.status_code == 200
    assert '<html lang="es"' in html
    assert "Cerrar sesión" in html
    assert "© " in html


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_render_inside_the_base_layout(logged_in, admin_maria, url):
    response = logged_in.get(url)
    html = _text(response)

    assert response.status_code == 200
    assert "Cerrar sesión" in html
    for label in ADMIN_NAV_LABELS:
        assert label in html.split("<main", 1)[0], label


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_send_regular_users_to_the_report_center(logged_in, url):
    response = logged_in.get(url)

    assert response.status_code == 302
    assert response.headers["Location"].split("?", 1)[0] == "/"


@pytest.mark.parametrize("url", ("/auth/login", "/auth/register"))
def test_anonymous_pages_render_inside_the_base_layout(client, url):
    response = client.get(url)
    html = _text(response)

    assert response.status_code == 200
    assert '<main id="main-content"' in html
    assert "Cerrar sesión" not in html


def test_authenticated_pages_keep_the_exact_default_chrome(logged_in):
    html = _text(logged_in.get("/"))

    assert (
        '<body class="app-shell has-sidebar flex min-h-screen flex-col font-sans text-slate-900 antialiased"'
        in html
    )
    assert '<aside id="nav-menu" class="app-sidebar" aria-label="Navegación principal">' in html
    assert '<header class="app-header">' in html
    assert '<footer class="app-footer">' in html
    assert (
        '<main id="main-content" class="page-shell mx-auto w-full max-w-6xl flex-1 px-4 py-8">'
        in html
    )
    assert "auth-split" not in html


# --- flash messages ---------------------------------------------------------


@pytest.mark.parametrize(
    "category, expected_classes",
    [
        ("success", ("alert-success", "bi-check-circle-fill")),
        ("danger", ("alert-danger", "bi-exclamation-octagon-fill")),
        ("warning", ("alert-warning", "bi-exclamation-triangle-fill")),
        ("info", ("alert-info", "bi-info-circle-fill")),
        ("unknown-category", ("alert-info", "bi-info-circle-fill")),
    ],
)
def test_flash_messages_use_explicit_colors_and_alert_role(
    client, category, expected_classes
):
    with client.session_transaction() as session:
        session["_flashes"] = [(category, "Mensaje de prueba")]

    html = _text(client.get("/auth/login"))

    assert "Mensaje de prueba" in html
    assert 'role="alert"' in html
    for css_class in expected_classes:
        assert css_class in html, css_class
    # Only the mapped style may be used (no per-category leakage between alerts).
    other_alerts = {"alert-success", "alert-danger", "alert-warning", "alert-info"} - {
        expected_classes[0]
    }
    for css_class in other_alerts:
        assert f'class="alert {css_class}"' not in html
    assert "Cerrar mensaje" in html


def test_flash_message_is_consumed_after_rendering(client):
    with client.session_transaction() as session:
        session["_flashes"] = [("success", "Solo una vez")]

    assert "Solo una vez" in _text(client.get("/auth/login"))
    assert "Solo una vez" not in _text(client.get("/auth/login"))


def test_flash_message_text_is_escaped(client):
    with client.session_transaction() as session:
        session["_flashes"] = [("info", "<script>alert(1)</script>")]

    html = _text(client.get("/auth/login"))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- error pages ------------------------------------------------------------


def test_404_page_is_spanish_for_anonymous_users(client):
    response = client.get("/no-such-page")
    html = _text(response)

    assert response.status_code == 404
    assert "Página no encontrada" in html
    assert "Volver al inicio" in html
    assert '<html lang="es"' in html


def test_404_page_for_logged_in_users_keeps_navbar(logged_in):
    response = logged_in.get("/no-such-page")
    html = _text(response)

    assert response.status_code == 404
    assert "Página no encontrada" in html
    assert "Cerrar sesión" in html


def test_500_page_renders_template_and_status(app):
    @app.route("/boom-layout")
    def boom_layout():
        raise RuntimeError("boom")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = app.test_client().get("/boom-layout")
    html = _text(response)

    assert response.status_code == 500
    assert "Error del servidor" in html
    assert "Volver al inicio" in html
    assert '<html lang="es"' in html


# --- macros -----------------------------------------------------------------


class SampleForm(FlaskForm):
    name = StringField("Nombre", validators=[DataRequired(message="El nombre es obligatorio."), Length(max=5)])
    notes = TextAreaField("Notas")
    category = SelectField("Categoría", choices=[("general", "General"), ("other", "Otro")])
    picture = FileField("Imagen")
    secret = PasswordField("Clave")
    quantity = IntegerField("Cantidad", validators=[NumberRange(min=0)])
    price = DecimalField("Precio")
    active = BooleanField("Activo")


def _render(app, source, **context):
    with app.test_request_context("/"):
        return render_template_string(source, **context)


def _bound_form(app, data=None):
    with app.test_request_context("/", method="POST", data=data or {}):
        form = SampleForm()
        form.validate()
        return form


def test_field_macro_renders_label_input_and_help(app):
    form = _bound_form(app, {"name": "Ok"})
    html = _render(
        app,
        '{% from "_macros.html" import field %}{{ field(form.name, help="Hasta 5 caracteres", placeholder="Nombre") }}',
        form=form,
    )

    assert 'class="form-label"' in html
    assert "Nombre" in html
    assert "form-input" in html
    assert "form-input-error" not in html
    assert 'placeholder="Nombre"' in html
    assert "Hasta 5 caracteres" in html
    assert "form-help" in html


def test_field_macro_marks_required_fields(app):
    form = _bound_form(app, {"name": "Ok"})
    html = _render(
        app, '{% from "_macros.html" import field %}{{ field(form.name) }}{{ field(form.notes) }}', form=form
    )

    assert html.count("obligatorio") == 1


def test_field_macro_shows_all_errors_and_error_class(app):
    form = _bound_form(app, {"name": "much too long"})
    assert form.name.errors

    html = _render(
        app, '{% from "_macros.html" import field %}{{ field(form.name) }}', form=form
    )

    assert "form-input-error" in html
    assert 'class="form-error"' in html or 'form-error' in html
    for message in form.name.errors:
        assert message in html
    assert 'aria-invalid="true"' in html


def test_field_macro_shows_required_error_message(app):
    form = _bound_form(app, {"name": ""})

    html = _render(
        app, '{% from "_macros.html" import field %}{{ field(form.name) }}', form=form
    )

    assert "El nombre es obligatorio." in html


@pytest.mark.parametrize(
    "field_name, tag",
    [
        ("notes", "<textarea"),
        ("category", "<select"),
        ("picture", 'type="file"'),
        ("secret", 'type="password"'),
        ("quantity", "<input"),
        ("price", "<input"),
    ],
)
def test_field_macro_supports_widget_types(app, field_name, tag):
    form = _bound_form(app, {})
    html = _render(
        app,
        '{% from "_macros.html" import field %}{{ field(form[field_name]) }}',
        form=form,
        field_name=field_name,
    )

    assert tag in html
    assert "form-input" in html


def test_field_macro_merges_extra_css_class(app):
    form = _bound_form(app, {})
    html = _render(
        app,
        '{% from "_macros.html" import field %}{{ field(form.notes, class_="font-mono", rows=6) }}',
        form=form,
    )

    assert "form-input" in html
    assert "font-mono" in html
    assert 'rows="6"' in html


def test_field_macro_renders_boolean_as_checkbox(app):
    form = _bound_form(app, {})
    html = _render(
        app, '{% from "_macros.html" import field %}{{ field(form.active) }}', form=form
    )

    assert 'type="checkbox"' in html
    assert "Activo" in html
    assert "form-input" not in html


def test_form_errors_macro_shows_csrf_failure_only_when_present(app):
    form = _bound_form(app, {})
    empty = _render(
        app, '{% from "_macros.html" import form_errors %}{{ form_errors(form) }}', form=form
    )
    assert empty.strip() == ""

    form.csrf_token = SimpleNamespace(errors=["The CSRF token is missing."])
    form.form_errors = []
    html = _render(
        app, '{% from "_macros.html" import form_errors %}{{ form_errors(form) }}', form=form
    )
    assert 'role="alert"' in html
    assert "sesión del formulario" in html


def test_csrf_field_macro(app):
    html = _render(app, '{% from "_macros.html" import csrf_field %}{{ csrf_field() }}')

    assert 'type="hidden"' in html
    assert 'name="csrf_token"' in html


def test_page_header_macro_with_and_without_actions(app):
    plain = _render(
        app,
        '{% from "_macros.html" import page_header %}{{ page_header("Objetos", "Todos los reportes") }}',
    )
    assert "page-title" in plain
    assert "Objetos" in plain
    assert "page-subtitle" in plain
    assert "Todos los reportes" in plain

    with_actions = _render(
        app,
        '{% from "_macros.html" import page_header %}'
        '{% call page_header("Objetos") %}<a href="/x">Accion</a>{% endcall %}',
    )
    assert "page-subtitle" not in with_actions
    assert '<a href="/x">Accion</a>' in with_actions


def _pagination(page, pages, total=None):
    return SimpleNamespace(
        page=page,
        pages=pages,
        total=total,
        has_prev=page > 1,
        has_next=page < pages,
        prev_num=page - 1 if page > 1 else None,
        next_num=page + 1 if page < pages else None,
    )


PAGINATION_SOURCE = (
    '{% from "_macros.html" import pagination %}'
    '{{ pagination(pag, "main.index", q=q) }}'
)


def test_pagination_is_hidden_for_a_single_page(app):
    html = _render(app, PAGINATION_SOURCE, pag=_pagination(1, 1, 3), q="tornillo")

    assert "Página" not in html
    assert "Siguiente" not in html


def test_pagination_shows_page_and_keeps_query(app):
    html = _render(app, PAGINATION_SOURCE, pag=_pagination(2, 3, 14), q="tornillo")

    assert "Página 2 de 3" in html
    assert "Anterior" in html
    assert "Siguiente" in html
    assert "page=1" in html
    assert "page=3" in html
    assert html.count("q=tornillo") == 2
    assert "14 resultados" in html


def test_pagination_disables_edges_and_skips_empty_params(app):
    first = _render(app, PAGINATION_SOURCE, pag=_pagination(1, 3, 1), q="")
    assert "page=2" in first
    assert "page=0" not in first
    assert "q=" not in first
    assert 'aria-disabled="true"' in first
    assert "1 resultado" in first and "1 resultados" not in first

    last = _render(app, PAGINATION_SOURCE, pag=_pagination(3, 3), q=None)
    assert "page=2" in last
    assert "page=4" not in last
    assert "resultado" not in last


def test_empty_state_macro_with_and_without_action(app):
    with_action = _render(
        app,
        '{% from "_macros.html" import empty_state %}'
        '{{ empty_state("Sin reportes", "Registre el primero.", url_for("main.add"), "Reportar objeto") }}',
    )
    assert "empty-state" in with_action
    assert "Sin reportes" in with_action
    assert "Registre el primero." in with_action
    assert 'href="/add"' in with_action
    assert "Reportar objeto" in with_action
    assert "bi-inbox" in with_action

    without_action = _render(
        app,
        '{% from "_macros.html" import empty_state %}{{ empty_state("Vacío", "Nada aquí.", icon="bi-search") }}',
    )
    assert "bi-search" in without_action
    assert "<a " not in without_action


def test_delete_form_has_csrf_confirm_and_danger_button(app):
    html = _render(
        app,
        '{% from "_macros.html" import delete_form %}{{ delete_form("/delete/1") }}',
    )

    assert 'action="/delete/1"' in html
    assert re.search(r'method="post"', html, re.IGNORECASE)
    assert 'name="csrf_token"' in html
    assert "btn-danger" in html
    assert "btn-sm" in html
    assert "Eliminar" in html
    # The message is emitted as a JSON string literal; decoding it must give the Spanish text.
    literal = re.search(r"onsubmit='return confirm\((.*)\);'", html)
    assert literal is not None
    assert json.loads(literal.group(1)) == (
        "¿Desea eliminar este elemento? Esta acción no se puede deshacer."
    )


def test_delete_form_escapes_confirmation_message(app):
    message = "Borrar \"X\" y <b>Y</b> o l'a"
    html = _render(
        app,
        '{% from "_macros.html" import delete_form %}'
        '{{ delete_form("/delete/1", confirm=message) }}',
        message=message,
    )

    assert "<b>" not in html
    onsubmit = re.search(r"onsubmit='([^']*)'", html)
    assert onsubmit is not None
    assert "\\u0027" in onsubmit.group(1)
    assert "\\u003cb\\u003e" in onsubmit.group(1)
    assert '\\"X\\"' in onsubmit.group(1)


def test_delete_form_custom_label_and_size(app):
    html = _render(
        app,
        '{% from "_macros.html" import delete_form %}'
        '{{ delete_form("/delete/1", label="Eliminar reporte", size="md") }}',
    )

    assert "Eliminar reporte" in html
    assert "btn-sm" not in html


# --- lost-and-found components -----------------------------------------------


@pytest.mark.parametrize(
    "status, css, label",
    [("lost", "badge-warning", "Perdido"), ("found", "badge-info", "Encontrado"), ("returned", "badge-success", "Entregado")],
)
def test_status_badge_macro_uses_semantic_classes_and_spanish_labels(app, status, css, label):
    html = _render(
        app,
        '{% from "_macros.html" import status_badge %}{{ status_badge(status) }}',
        status=status,
    )

    assert "badge" in html and css in html and label in html


def test_status_badge_macro_falls_back_to_neutral_for_unknown_values(app):
    html = _render(
        app,
        '{% from "_macros.html" import status_badge %}{{ status_badge("archived") }}',
    )

    assert "badge-neutral" in html and "archived" in html


def test_filter_chip_macro_marks_the_active_chip(app):
    source = (
        '{% from "_macros.html" import filter_chip %}'
        '{{ filter_chip("Perdidos", "/?estado=lost", 3, active) }}'
    )

    active = _render(app, source, active=True)
    inactive = _render(app, source, active=False)

    assert 'class="chip is-active"' in active and 'aria-current="true"' in active
    assert 'class="chip"' in inactive and "aria-current" not in inactive
    assert 'href="/?estado=lost"' in active
    assert "Perdidos" in active and ">3<" in active.replace(" ", "").replace("\n", "")


def test_items_grid_macro_renders_cards_with_core_details(app, make_user, make_item):
    from datetime import date

    owner = make_user("maria", "password123")
    item = make_item(owner, name="Mochila azul", location="Aula 4", event_date=date(2026, 3, 10), category="bags")

    html = _render(
        app,
        '{% from "_items_grid.html" import items_grid %}{{ items_grid(items) }}',
        items=[item],
    )

    assert 'class="gallery"' in html and "object-card" in html
    assert f'href="/item/{item.id}"' in html
    for text in ("Mochila azul", "Aula 4", "10 de marzo de 2026", "Mochilas y útiles", "Perdido", "maria"):
        assert text in html, text
    assert "bi-image" in html


def test_items_grid_tints_cards_by_status_and_category(app, make_user, make_item):
    owner = make_user("maria", "password123")
    known = make_item(owner, name="Llaves", category="keys", status="found")
    legacy = make_item(owner, name="Grapadora", category="office")

    html = _render(
        app,
        '{% from "_items_grid.html" import items_grid %}{{ items_grid(items) }}',
        items=[known, legacy],
    )

    assert 'class="status--found cat--keys"' in html and "bi-key" in html
    assert 'class="status--lost cat--other"' in html
    assert "Sin fotografía" in html


def test_detail_page_shows_status_track(app, client, make_user, make_item, login):
    owner = make_user("maria", "password123")
    item = make_item(owner, name="Llaves", category="keys", status="found")
    login("maria", "password123")

    html = client.get(f"/item/{item.id}").get_data(as_text=True)

    track = re.search(r'<ol class="status-track"[^>]*>(.*?)</ol>', html, re.DOTALL).group(1)
    assert "status--lost is-done" in track
    assert re.search(r'status--found is-current"\s*aria-current="step"', track)
    assert "is-done" not in track.split("status--returned", 1)[1]


def test_stylesheet_defines_the_lost_and_found_components():
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")

    for selector in (
        ".gallery", ".object-card", ".object-card__media", ".object-card__body", ".object-card__meta",
        ".filter-bar", ".chip", ".chip.is-active", ".detail-grid", ".form-section",
    ):
        assert selector in css, selector
    assert "aspect-ratio" in css


def test_stock_badge_macro_is_gone(app):
    from pathlib import Path

    macros = (Path(app.root_path) / "templates" / "_macros.html").read_text(encoding="utf-8")

    assert "stock_badge" not in macros
