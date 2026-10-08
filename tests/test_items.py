"""Lost-and-found report screens: list, add, edit, view, delete, mark returned and images."""

import json
import os
import re
from datetime import date, timedelta
from io import BytesIO

import pytest
from flask import url_for
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Item, User
from utils import today_local

NOT_OWNER = "Solo quien creó el reporte puede modificarlo."
INVALID_IMAGE = "El archivo no es una imagen válida."
INVALID_FORMAT = "Formato no permitido. Use PNG, JPG, GIF o WEBP."
STORED_IMAGE_NAME = re.compile(r"^\d+_\d{20}\.(png|jpg|jpeg|gif|webp)$")
EMOJI_PATTERN = re.compile("[\u2600-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff\ufe0f]")
ENGLISH_WORDS = (
    "Inventory", "Description", "Quantity", "Price", "Actions", "Delete", "Edit", "Save",
    "Cancel", "Search", "Lost", "Found", "Returned", "Location", "Status", "Contact",
    "Category", "Stock",
)


def _text(response):
    return response.get_data(as_text=True)


def _main(html):
    """Return only the page body (what sits between <main> and </main>)."""
    return html.split("<main", 1)[1].split("</main>", 1)[0]


def _visible(html):
    """Return the text a user reads: no scripts, styles or tags."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", html)


def _confirm_message(form_html):
    """Decode the JSON string literal that delete_form puts in the onsubmit confirm()."""
    literal = re.search(r"onsubmit='return confirm\((.*)\);'", form_html)
    assert literal is not None
    return json.loads(literal.group(1))


def _location_path(response):
    return response.headers["Location"].split("?", 1)[0]


def _item_data(**overrides):
    data = {
        "name": "Mochila azul",
        "description": "Con un llavero rojo",
        "category": "bags",
        "location": "Biblioteca central",
        "event_date": "2026-03-10",
        "status": "lost",
        "contact": "ana@universidad.edu",
    }
    data.update(overrides)
    return data


def _with_image(data, content, filename):
    return {**data, "image": (BytesIO(content), filename)}


def _png_bytes():
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (3, 3), (10, 120, 10)).save(buffer, "PNG")
    return buffer.getvalue()


def _post_form(browser, url, data, **kwargs):
    # A photo is required to create a report; attach a valid one unless the test sets its own.
    if url == "/add" and "image" not in data:
        data = _with_image(data, _png_bytes(), "foto.png")
    return browser.post(url, data=data, content_type="multipart/form-data", **kwargs)


def _stored_files(app):
    folder = app.config["ITEM_UPLOAD_FOLDER"]
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _put_file(app, name, content):
    folder = app.config["ITEM_UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(content)


@pytest.fixture
def maria(browser_for):
    return browser_for("maria")


@pytest.fixture
def maria_user(maria):
    return User.query.filter_by(username="maria").first()


@pytest.fixture
def carlos(maria, browser_for):
    return browser_for("carlos")


@pytest.fixture
def carlos_user(carlos):
    return User.query.filter_by(username="carlos").first()


@pytest.fixture
def owned_item(maria_user, make_item):
    return make_item(
        maria_user,
        name="Mochila azul",
        description="Con un llavero rojo",
        category="bags",
        location="Biblioteca central",
        event_date=date(2026, 1, 15),
        contact="ana@universidad.edu",
    )


@pytest.fixture
def other_user_item(carlos_user, make_item):
    return make_item(carlos_user, name="Paraguas negro", location="Cafetería")


# --- route table -------------------------------------------------------------


def test_item_route_table(app):
    with app.test_request_context("/"):
        assert url_for("main.index") == "/"
        assert url_for("main.view", item_id=4) == "/item/4"
        assert url_for("main.add") == "/add"
        assert url_for("main.edit", item_id=4) == "/edit/4"
        assert url_for("main.delete", item_id=4) == "/delete/4"
        assert url_for("main.mark_returned", item_id=4) == "/item/4/returned"
        assert url_for("main.search") == "/search"
        assert url_for("main.import_csv") == "/import/csv"
        assert url_for("main.export_csv") == "/export/csv"


def test_item_routes_allowed_methods(app):
    methods = {}
    for rule in app.url_map.iter_rules():
        methods[rule.endpoint] = rule.methods - {"HEAD", "OPTIONS"}

    assert methods["main.index"] == {"GET"}
    assert methods["main.view"] == {"GET"}
    assert methods["main.add"] == {"GET", "POST"}
    assert methods["main.edit"] == {"GET", "POST"}
    assert methods["main.delete"] == {"POST"}
    assert methods["main.mark_returned"] == {"POST"}
    assert methods["main.search"] == {"GET"}
    assert methods["main.import_csv"] == {"GET", "POST"}
    assert methods["main.export_csv"] == {"GET"}


@pytest.mark.parametrize(
    "method, url",
    [
        ("get", "/"),
        ("get", "/add"),
        ("post", "/add"),
        ("get", "/item/1"),
        ("get", "/edit/1"),
        ("post", "/edit/1"),
        ("post", "/delete/1"),
        ("post", "/item/1/returned"),
        ("get", "/search"),
        ("get", "/export/csv"),
    ],
)
def test_anonymous_users_are_sent_to_login(browser_for, method, url):
    anonymous = browser_for()

    response = getattr(anonymous, method)(url)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"


# --- list --------------------------------------------------------------------


def test_list_shows_empty_state_without_duplicated_actions(maria):
    html = _text(maria.get("/"))
    body = _main(html)

    assert "Aún no hay reportes registrados." in body
    assert 'href="/add"' not in body
    assert 'href="/add"' in html
    assert "<table" not in html
    assert 'class="gallery' not in body
    assert body.count("btn-primary") == 0


def test_list_renders_reports_in_spanish(maria, owned_item):
    html = _text(maria.get("/"))
    body = _main(html)

    assert '<html lang="es"' in html
    assert "Centro de reportes" in body
    assert "1 reporte registrado" in body
    assert "Mochila azul" in body
    assert f'href="/item/{owned_item.id}"' in body
    assert "Perdido" in body
    assert "badge-warning" in body
    assert "Biblioteca central" in body
    assert "15 de enero de 2026" in body
    assert "Mochilas y útiles" in body
    assert "Con un llavero rojo" in body
    assert "maria" in body
    assert "Aún no hay reportes" not in body


def test_list_shows_the_reports_of_every_user(maria, owned_item, other_user_item):
    body = _main(_text(maria.get("/")))

    assert "Mochila azul" in body
    assert "Paraguas negro" in body
    assert "carlos" in body
    assert "2 reportes registrados" in body


def test_list_is_a_gallery_of_cards_not_a_table(maria, owned_item):
    body = _main(_text(maria.get("/")))

    assert re.search(r'class="gallery"', body)
    assert "object-card" in body
    assert "<table" not in body


def test_list_cards_show_status_badges_per_state(maria, maria_user, make_item):
    make_item(maria_user, name="Uno", status="lost")
    make_item(maria_user, name="Dos", status="found")
    make_item(maria_user, name="Tres", status="returned")

    cards = re.findall(r'<article class="object-card.*?</article>', _main(_text(maria.get("/"))), re.DOTALL)

    assert len(cards) == 3
    by_name = {name: card for card in cards for name in ("Uno", "Dos", "Tres") if f">{name}<" in card}
    assert "badge-warning" in by_name["Uno"] and "Perdido" in by_name["Uno"]
    assert "badge-info" in by_name["Dos"] and "Encontrado" in by_name["Dos"]
    assert "badge-success" in by_name["Tres"] and "Entregado" in by_name["Tres"]


def test_list_uses_placeholder_when_report_has_no_photo(maria, owned_item):
    body = _main(_text(maria.get("/")))

    assert "bi-image" in body
    assert "<img" not in body


def test_list_photo_alt_is_the_object_name(maria, maria_user, make_item):
    make_item(maria_user, name="Cartera marrón", image_filename="1_20260101000000000000.png")

    body = _main(_text(maria.get("/")))

    assert 'alt="Cartera marrón"' in body
    assert "/static/uploads/1_20260101000000000000.png" in body
    assert 'loading="lazy"' in body


def test_list_truncates_long_descriptions(maria, maria_user, make_item):
    make_item(maria_user, name="Largo", description=("palabra " * 60).strip())

    body = _main(_text(maria.get("/")))

    assert "…" in body
    assert ("palabra " * 60).strip() not in body


def test_list_escapes_report_names(maria, maria_user, make_item):
    make_item(maria_user, name="<script>alert(1)</script>")

    body = _main(_text(maria.get("/")))

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_list_cards_only_link_to_the_detail_page(maria, owned_item):
    body = _main(_text(maria.get("/")))

    assert 'action="/delete/' not in body
    assert f'href="/edit/{owned_item.id}"' not in body
    assert not re.search(r'<a[^>]*href="[^"]*/delete/', body)


def test_list_orders_by_event_date_desc_then_id_desc_with_missing_dates_last(
    maria, maria_user, make_item
):
    make_item(maria_user, name="Sin fecha", event_date=None)
    make_item(maria_user, name="Antiguo", event_date=date(2026, 1, 1))
    make_item(maria_user, name="Reciente A", event_date=date(2026, 3, 1))
    make_item(maria_user, name="Reciente B", event_date=date(2026, 3, 1))

    body = _main(_text(maria.get("/")))
    positions = [body.index(name) for name in ("Reciente B", "Reciente A", "Antiguo", "Sin fecha")]

    assert positions == sorted(positions)


def test_list_paginates_twelve_per_page(maria, maria_user, make_item):
    for number in range(1, 14):
        make_item(maria_user, name=f"Objeto {number:02d}", event_date=date(2026, 1, number))

    first = _main(_text(maria.get("/")))
    second = _main(_text(maria.get("/?page=2")))

    assert first.count('<article class="object-card"') == 12
    assert "Objeto 13" in first and "Objeto 01" not in first
    assert "Página 1 de 2" in first
    assert "13 resultados" in first
    assert second.count('<article class="object-card"') == 1
    assert "Objeto 01" in second
    assert "Página 2 de 2" in second


def test_list_page_beyond_the_last_redirects_to_it(maria, maria_user, make_item):
    for number in range(14):
        make_item(maria_user, name=f"Objeto {number}")

    response = maria.get("/?page=9")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?page=2")
    assert "Página 2 de 2" in _text(maria.get("/?page=2"))


def test_list_ignores_invalid_page_numbers(maria, owned_item):
    for page in ("abc", "0", "-3"):
        response = maria.get(f"/?page={page}")
        assert response.status_code == 200
        assert "Mochila azul" in _text(response)


def test_list_has_a_filter_bar_and_status_chips(maria, owned_item):
    body = _main(_text(maria.get("/")))

    assert re.search(r'<form[^>]*method="get"[^>]*action="/"|<form[^>]*action="/"[^>]*method="get"', body, re.IGNORECASE)
    assert re.search(r'<label[^>]*for="q"[^>]*>\s*Buscar', body)
    assert 'placeholder="Objeto, descripción o lugar"' in body
    assert re.search(r'<select[^>]*name="categoria"', body)
    assert re.search(r'<input[^>]*type="month"[^>]*name="mes"|<input[^>]*name="mes"[^>]*type="month"', body)
    assert "Aplicar" in body
    assert "Limpiar filtros" in body
    for chip in ("Todos", "Perdidos", "Encontrados", "Entregados"):
        assert chip in body, chip
    assert len(re.findall(r'aria-current="true"', body)) == 1
    assert body.count("btn-primary") == 1


def test_list_pages_have_no_english_labels_or_emojis(maria, owned_item):
    html = _text(maria.get("/"))
    visible = _visible(_main(html))

    for word in ENGLISH_WORDS:
        assert not re.search(rf"\b{word}\b", visible), word
    assert not EMOJI_PATTERN.search(html)


def test_list_has_no_leftover_inventory_terms(maria, owned_item):
    visible = _visible(_text(maria.get("/")))

    for term in ("Inventario", "artículo", "Artículo", "Precio", "Cantidad", "stock", "$"):
        assert term not in visible, term


# --- add ---------------------------------------------------------------------


def test_add_form_is_spanish_sectioned_and_multipart(maria):
    html = _text(maria.get("/add"))
    body = _main(html)

    assert "Nuevo reporte" in html.split("</title>")[0]
    assert re.search(r'<form[^>]*enctype="multipart/form-data"', body)
    assert 'name="csrf_token"' in body
    for label in ("Objeto", "Descripción", "Categoría", "Lugar", "Fecha del suceso", "Estado", "Contacto", "Fotografía"):
        assert label in body, label
    for option in ("Seleccione una categoría", "Documentos", "Electrónica", "Llaves", "Ropa y accesorios", "Mochilas y útiles", "Otros", "Perdido", "Encontrado", "Entregado"):
        assert option in body, option
    assert "Indique dónde se perdió o se encontró el objeto." in body
    assert "Correo o teléfono para coordinar la entrega." in body
    assert body.count("form-section") >= 3
    assert "Guardar reporte" in body
    assert body.count("btn-primary") == 1
    assert "Cancelar" in body


def test_add_form_has_no_quantity_or_price(maria):
    body = _main(_text(maria.get("/add")))

    for name in ("quantity", "price", "Cantidad", "Precio"):
        assert name not in body, name


def test_add_form_date_input_defaults_to_today_and_blocks_the_future(maria):
    body = _main(_text(maria.get("/add")))
    today = today_local().isoformat()

    date_input = re.search(r'<input[^>]*name="event_date"[^>]*>', body).group(0)
    assert 'type="date"' in date_input
    assert f'max="{today}"' in date_input
    assert f'value="{today}"' in date_input
    assert re.search(r'<option[^>]*value="lost"[^>]*selected|<option[^>]*selected[^>]*value="lost"', body)


def test_add_creates_a_report_for_the_current_user(maria, maria_user):
    response = _post_form(maria, "/add", _item_data(), follow_redirects=True)
    html = _text(response)

    assert response.request.path == "/"
    assert "Reporte registrado correctamente." in html
    item = Item.query.one()
    assert item.user_id == maria_user.id
    assert (item.name, item.description, item.category) == ("Mochila azul", "Con un llavero rojo", "bags")
    assert (item.location, item.event_date, item.status) == ("Biblioteca central", date(2026, 3, 10), "lost")
    assert item.contact == "ana@universidad.edu"
    assert item.image_filename.endswith(".png")
    assert "Mochila azul" in _main(html)


def test_add_accepts_found_and_returned_status_and_today(maria):
    _post_form(maria, "/add", _item_data(name="Hallado", status="found", event_date=today_local().isoformat()))
    _post_form(maria, "/add", _item_data(name="Devuelto", status="returned"))

    assert Item.query.filter_by(name="Hallado").one().status == "found"
    assert Item.query.filter_by(name="Hallado").one().event_date == today_local()
    assert Item.query.filter_by(name="Devuelto").one().status == "returned"


def test_add_without_optional_fields_stores_none(maria):
    data = _item_data(category="", description="  ")
    _post_form(maria, "/add", data)

    item = Item.query.one()
    assert (item.category, item.description) == (None, None)


def test_add_requires_contact(maria):
    response = _post_form(maria, "/add", _item_data(contact="  "))

    assert Item.query.count() == 0
    assert "Ingrese un correo o teléfono de contacto." in _main(_text(response))


def test_add_requires_a_photo(maria):
    response = maria.post("/add", data=_item_data(), content_type="multipart/form-data")

    assert Item.query.count() == 0
    assert "Suba una fotografía del objeto." in _main(_text(response))


def test_add_strips_whitespace(maria):
    _post_form(maria, "/add", _item_data(name="  Llaves  ", location="  Aula 3  ", contact="  555-0100 "))

    item = Item.query.one()
    assert (item.name, item.location, item.contact) == ("Llaves", "Aula 3", "555-0100")


def test_add_without_status_defaults_to_lost(maria):
    data = _item_data()
    del data["status"]
    _post_form(maria, "/add", data)

    assert Item.query.one().status == "lost"


TOMORROW = lambda: (today_local() + timedelta(days=1)).isoformat()  # noqa: E731

INVALID_FIELDS = [
    ("name", "", "Ingrese el nombre del objeto."),
    ("name", "   ", "Ingrese el nombre del objeto."),
    ("name", "x" * 121, "120 caracteres"),
    ("description", "d" * 501, "500 caracteres"),
    ("location", "", "Ingrese el lugar."),
    ("location", "l" * 121, "120 caracteres"),
    ("event_date", "", "Ingrese la fecha del suceso."),
    ("event_date", "abc", "Ingrese una fecha válida."),
    ("event_date", "2026-02-30", "Ingrese una fecha válida."),
    ("event_date", "2026-13-01", "Ingrese una fecha válida."),
    ("event_date", "__tomorrow__", "La fecha no puede ser posterior a hoy."),
    ("event_date", "9999-12-31", "La fecha no puede ser posterior a hoy."),
    ("status", "archived", "Seleccione un estado válido."),
    ("contact", "c" * 121, "120 caracteres"),
    ("category", "hacking", "Seleccione una categoría válida."),
]


@pytest.mark.parametrize("field, value, message", INVALID_FIELDS)
def test_add_rejects_invalid_values_with_inline_spanish_errors(maria, field, value, message):
    if value == "__tomorrow__":
        value = TOMORROW()

    response = _post_form(maria, "/add", _item_data(**{field: value}))
    body = _main(_text(response))

    assert response.status_code == 200
    assert message in body
    assert "form-error" in body
    assert "form-input-error" in body
    assert Item.query.count() == 0
    assert 'value="Biblioteca central"' in body or field == "location"


def test_add_invalid_form_does_not_leak_english_validation_messages(maria):
    response = _post_form(maria, "/add", _item_data(name="", event_date="x", status="zzz", category="zzz"))
    body = _main(_text(response))

    for english in ("This field is required", "Not a valid", "Invalid"):
        assert english not in body


def test_add_ignores_legacy_quantity_and_price_fields(maria):
    response = _post_form(maria, "/add", _item_data(quantity="abc", price="xyz"))

    assert response.status_code == 302
    assert Item.query.count() == 1


def test_add_with_a_real_image_saves_a_unique_file_in_the_upload_folder(app, maria, png_bytes):
    assert not os.path.exists(app.config["ITEM_UPLOAD_FOLDER"])

    response = _post_form(
        maria, "/add", _with_image(_item_data(), png_bytes, "Mi Foto.PNG"), follow_redirects=True
    )

    assert "Reporte registrado correctamente." in _text(response)
    item = Item.query.one()
    assert STORED_IMAGE_NAME.match(item.image_filename)
    assert item.image_filename.startswith(f"{item.user_id}_")
    assert _stored_files(app) == [item.image_filename]
    with open(os.path.join(app.config["ITEM_UPLOAD_FOLDER"], item.image_filename), "rb") as handle:
        assert handle.read() == png_bytes
    assert item.image_filename in _main(_text(maria.get("/")))


def test_two_uploads_never_collide(app, maria, png_bytes):
    for name in ("Uno", "Dos"):
        _post_form(maria, "/add", _with_image(_item_data(name=name), png_bytes, "foto.png"))

    assert len(set(_stored_files(app))) == 2


@pytest.mark.parametrize("extension, image_format", [("jpg", "JPEG"), ("jpeg", "JPEG"), ("gif", "GIF"), ("webp", "WEBP")])
def test_add_accepts_every_allowed_image_format(app, maria, extension, image_format):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (3, 3), (10, 120, 10)).save(buffer, image_format)

    _post_form(maria, "/add", _with_image(_item_data(), buffer.getvalue(), f"foto.{extension}"))

    item = Item.query.one()
    assert item.image_filename.endswith(f".{extension}")
    assert _stored_files(app) == [item.image_filename]


def test_add_rejects_non_image_content_with_an_image_extension(app, maria):
    response = _post_form(
        maria, "/add", _with_image(_item_data(), b"<html><script>1</script></html>", "foto.png")
    )

    assert response.status_code == 200
    assert INVALID_IMAGE in _main(_text(response))
    assert Item.query.count() == 0
    assert _stored_files(app) == []


def test_add_rejects_truncated_image(app, maria, png_bytes):
    _post_form(maria, "/add", _with_image(_item_data(), png_bytes[:20], "roto.png"))

    assert Item.query.count() == 0
    assert _stored_files(app) == []


@pytest.mark.parametrize("filename", ["pagina.html", "dibujo.svg", "script.exe", "foto.png.html", "sinextension", "foto.bmp"])
def test_add_rejects_disallowed_extensions(app, maria, png_bytes, filename):
    response = _post_form(maria, "/add", _with_image(_item_data(), png_bytes, filename))

    assert response.status_code == 200
    assert INVALID_FORMAT in _main(_text(response))
    assert Item.query.count() == 0
    assert _stored_files(app) == []


def test_add_double_extension_with_html_payload_is_rejected(app, maria):
    _post_form(maria, "/add", _with_image(_item_data(), b"<svg onload=alert(1)>", "evil.html.png"))

    assert Item.query.count() == 0
    assert _stored_files(app) == []


def test_add_ignores_path_traversal_in_the_uploaded_filename(app, maria, png_bytes):
    _post_form(maria, "/add", _with_image(_item_data(), png_bytes, "../../evil.png"))

    item = Item.query.one()
    assert STORED_IMAGE_NAME.match(item.image_filename)
    assert _stored_files(app) == [item.image_filename]


def test_add_accepts_an_image_whose_name_has_only_non_ascii_characters(app, maria, png_bytes):
    _post_form(maria, "/add", _with_image(_item_data(), png_bytes, "图片.png"))

    assert STORED_IMAGE_NAME.match(Item.query.one().image_filename)


def test_add_invalid_form_with_valid_image_writes_no_file(app, maria, png_bytes):
    _post_form(maria, "/add", _with_image(_item_data(name=""), png_bytes, "foto.png"))

    assert Item.query.count() == 0
    assert _stored_files(app) == []


def test_add_database_error_removes_the_saved_image(app, maria, png_bytes, monkeypatch):
    def boom():
        raise SQLAlchemyError("forced failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", boom)
        response = _post_form(maria, "/add", _with_image(_item_data(), png_bytes, "foto.png"))
    db.session.rollback()

    assert response.status_code == 200
    assert "No se pudo guardar el reporte. Intente nuevamente." in _text(response)
    assert Item.query.count() == 0
    assert _stored_files(app) == []


# --- view --------------------------------------------------------------------


def test_view_shows_the_report_detail_in_spanish(maria, owned_item):
    html = _text(maria.get(f"/item/{owned_item.id}"))
    body = _main(html)

    assert "Mochila azul" in html.split("</title>")[0]
    assert "Objetos perdidos" in html.split("</title>")[0]
    for label in ("Estado", "Categoría", "Lugar", "Fecha del suceso", "Contacto", "Reportado por", "Registrado el"):
        assert re.search(rf"<dt[^>]*>\s*{label}\s*</dt>", body), label
    assert "Perdido" in body
    assert "Mochilas y útiles" in body
    assert "Biblioteca central" in body
    assert "15 de enero de 2026" in body
    assert "ana@universidad.edu" in body
    assert "maria" in body
    assert "Con un llavero rojo" in body
    assert "Volver a los objetos" in body
    assert "Cantidad" not in body and "Precio" not in body


def test_view_without_description_contact_or_photo(maria, maria_user, make_item):
    item = make_item(maria_user, name="Simple", description=None, contact=None)

    body = _main(_text(maria.get(f"/item/{item.id}")))

    assert "Sin descripción." in body
    assert "No indicado" in body
    assert "<img" not in body
    assert "bi-image" in body


def test_view_with_photo_has_alt_text(maria, maria_user, make_item):
    item = make_item(maria_user, name="Con foto", image_filename="1_20260101000000000000.png")

    body = _main(_text(maria.get(f"/item/{item.id}")))

    assert 'alt="Con foto"' in body
    assert "/static/uploads/1_20260101000000000000.png" in body


def test_view_owner_actions(maria, owned_item):
    body = _main(_text(maria.get(f"/item/{owned_item.id}")))

    assert f'href="/edit/{owned_item.id}"' in body
    assert "Editar" in body
    returned = re.search(rf'<form[^>]*action="/item/{owned_item.id}/returned"[^>]*>(.*?)</form>', body, re.DOTALL)
    assert returned is not None
    assert re.search(r'method="post"', returned.group(0), re.IGNORECASE)
    assert 'name="csrf_token"' in returned.group(1)
    assert "Marcar como entregado" in returned.group(1)
    assert "btn-primary" in returned.group(1)
    assert body.count("btn-primary") == 1
    assert body.count("btn-danger") == 1
    assert NOT_OWNER not in body


def test_view_delete_is_a_post_form_naming_the_object_and_there_is_no_get_delete_link(maria, owned_item):
    body = _main(_text(maria.get(f"/item/{owned_item.id}")))

    form = re.search(rf'<form[^>]*action="/delete/{owned_item.id}"[^>]*>(.*?)</form>', body, re.DOTALL)
    assert form is not None
    assert re.search(r'method="post"', form.group(0), re.IGNORECASE)
    assert 'name="csrf_token"' in form.group(1)
    assert "Mochila azul" in _confirm_message(form.group(0))
    assert not re.search(r'<a[^>]*href="[^"]*/delete/', body)
    assert maria.get(f"/delete/{owned_item.id}").status_code == 405


def test_view_hides_mark_returned_when_already_returned(maria, maria_user, make_item):
    item = make_item(maria_user, name="Entregado ya", status="returned")

    body = _main(_text(maria.get(f"/item/{item.id}")))

    assert "Marcar como entregado" not in body
    assert "Entregado" in body
    assert "badge-success" in body
    assert f'action="/item/{item.id}/returned"' not in body


def test_view_of_another_users_report_is_open_but_read_only(maria, other_user_item):
    response = maria.get(f"/item/{other_user_item.id}")
    body = _main(_text(response))

    assert response.status_code == 200
    assert "Paraguas negro" in body and "Cafetería" in body and "carlos" in body
    assert NOT_OWNER in body
    assert "Volver a los objetos" in body
    assert f'href="/edit/{other_user_item.id}"' not in body
    assert f'action="/delete/{other_user_item.id}"' not in body
    assert f'action="/item/{other_user_item.id}/returned"' not in body
    assert "btn-primary" not in body and "btn-danger" not in body


def test_view_of_a_missing_report_is_a_spanish_404(maria):
    response = maria.get("/item/9999")

    assert response.status_code == 404
    assert "Página no encontrada" in _text(response)


def test_view_has_no_english_labels_or_emojis(maria, owned_item):
    html = _text(maria.get(f"/item/{owned_item.id}"))
    visible = _visible(_main(html))

    for word in ENGLISH_WORDS:
        assert not re.search(rf"\b{word}\b", visible), word
    assert not EMOJI_PATTERN.search(html)


# --- edit --------------------------------------------------------------------


def test_edit_form_is_prefilled(maria, owned_item):
    html = _text(maria.get(f"/edit/{owned_item.id}"))
    body = _main(html)

    assert "Editar reporte" in html.split("</title>")[0]
    assert 'value="Mochila azul"' in body
    assert 'value="Biblioteca central"' in body
    assert 'value="2026-01-15"' in body
    assert 'value="ana@universidad.edu"' in body
    assert "Con un llavero rojo" in body
    assert re.search(r'<option[^>]*selected[^>]*value="bags"|<option[^>]*value="bags"[^>]*selected', body)
    assert "Guardar cambios" in body
    assert "Cambiar fotografía (opcional)" in body
    assert body.count("btn-primary") == 1
    assert re.search(r'<form[^>]*enctype="multipart/form-data"', body)
    assert "quantity" not in body and "price" not in body


def test_edit_form_shows_the_current_photo(maria, maria_user, make_item):
    item = make_item(maria_user, name="Con foto", image_filename="1_20260101000000000000.png")

    body = _main(_text(maria.get(f"/edit/{item.id}")))

    assert "Fotografía actual" in body
    assert 'alt="Fotografía actual de Con foto"' in body


def test_edit_updates_fields_and_redirects_to_the_detail(maria, owned_item):
    response = _post_form(
        maria,
        f"/edit/{owned_item.id}",
        _item_data(name="Mochila negra", location="Aula 12", event_date="2026-02-02", status="found", category="clothing", contact="555-0100"),
        follow_redirects=True,
    )
    html = _text(response)

    assert response.request.path == f"/item/{owned_item.id}"
    assert "Reporte actualizado correctamente." in html
    db.session.expire_all()
    item = db.session.get(Item, owned_item.id)
    assert (item.name, item.location, item.event_date) == ("Mochila negra", "Aula 12", date(2026, 2, 2))
    assert (item.status, item.category, item.contact) == ("found", "clothing", "555-0100")
    assert "Encontrado" in html


def test_edit_clearing_the_category_stores_none(maria, owned_item):
    _post_form(maria, f"/edit/{owned_item.id}", _item_data(category=""))

    db.session.expire_all()
    assert db.session.get(Item, owned_item.id).category is None


def test_edit_rejects_a_future_date_and_changes_nothing(maria, owned_item):
    response = _post_form(maria, f"/edit/{owned_item.id}", _item_data(event_date=TOMORROW(), name="Cambiado"))

    assert response.status_code == 200
    assert "La fecha no puede ser posterior a hoy." in _main(_text(response))
    db.session.expire_all()
    item = db.session.get(Item, owned_item.id)
    assert (item.name, item.event_date) == ("Mochila azul", date(2026, 1, 15))


def test_edit_invalid_values_show_errors_and_change_nothing(maria, owned_item):
    response = _post_form(maria, f"/edit/{owned_item.id}", _item_data(location=""))

    assert response.status_code == 200
    assert "Ingrese el lugar." in _main(_text(response))
    db.session.expire_all()
    assert db.session.get(Item, owned_item.id).location == "Biblioteca central"


def test_edit_preserves_a_legacy_category_that_is_not_in_the_list(maria, maria_user, make_item):
    item = make_item(maria_user, name="Antiguo", category="Herramientas")

    form = _main(_text(maria.get(f"/edit/{item.id}")))
    assert 'value="Herramientas"' in form

    _post_form(maria, f"/edit/{item.id}", _item_data(name="Antiguo", category="Herramientas"))
    db.session.expire_all()
    assert db.session.get(Item, item.id).category == "Herramientas"


@pytest.mark.parametrize("legacy", ["general", "office", "consumable"])
def test_edit_keeps_pre_pivot_category_keys_as_an_extra_option(maria, maria_user, make_item, legacy):
    item = make_item(maria_user, name="Antiguo", category=legacy)

    form = _main(_text(maria.get(f"/edit/{item.id}")))
    assert f'value="{legacy}"' in form

    _post_form(maria, f"/edit/{item.id}", _item_data(name="Antiguo", category=legacy))
    db.session.expire_all()
    assert db.session.get(Item, item.id).category == legacy


def test_edit_keeps_the_old_image_when_the_new_one_is_invalid(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    response = _post_form(maria, f"/edit/{item.id}", _with_image(_item_data(), b"not an image", "nueva.png"))

    assert response.status_code == 200
    assert INVALID_IMAGE in _main(_text(response))
    db.session.expire_all()
    assert db.session.get(Item, item.id).image_filename == "1_old.png"
    assert _stored_files(app) == ["1_old.png"]


def test_edit_keeps_the_old_image_when_the_new_extension_is_not_allowed(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    _post_form(maria, f"/edit/{item.id}", _with_image(_item_data(), png_bytes, "nueva.svg"))

    db.session.expire_all()
    assert db.session.get(Item, item.id).image_filename == "1_old.png"
    assert _stored_files(app) == ["1_old.png"]


def test_edit_with_invalid_fields_keeps_old_image_and_writes_no_new_file(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    _post_form(maria, f"/edit/{item.id}", _with_image(_item_data(location=""), png_bytes, "nueva.png"))

    db.session.expire_all()
    assert db.session.get(Item, item.id).image_filename == "1_old.png"
    assert _stored_files(app) == ["1_old.png"]


def test_edit_replaces_the_image_and_removes_the_old_file_after_success(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    response = _post_form(
        maria, f"/edit/{item.id}", _with_image(_item_data(), png_bytes, "nueva.png"), follow_redirects=True
    )

    assert "Reporte actualizado correctamente." in _text(response)
    db.session.expire_all()
    new_name = db.session.get(Item, item.id).image_filename
    assert new_name != "1_old.png" and STORED_IMAGE_NAME.match(new_name)
    assert _stored_files(app) == [new_name]


def test_edit_without_a_new_image_keeps_the_current_one(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    _post_form(maria, f"/edit/{item.id}", _item_data(name="Renombrado"))

    db.session.expire_all()
    assert db.session.get(Item, item.id).image_filename == "1_old.png"
    assert _stored_files(app) == ["1_old.png"]


def test_edit_database_error_keeps_old_image_and_discards_the_new_file(app, maria, maria_user, make_item, png_bytes, monkeypatch):
    _put_file(app, "1_old.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_old.png")

    def boom():
        raise SQLAlchemyError("forced failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", boom)
        response = _post_form(maria, f"/edit/{item.id}", _with_image(_item_data(), png_bytes, "nueva.png"))
    db.session.rollback()

    assert response.status_code == 200
    assert "No se pudieron guardar los cambios. Intente nuevamente." in _text(response)
    db.session.expire_all()
    assert db.session.get(Item, item.id).image_filename == "1_old.png"
    assert _stored_files(app) == ["1_old.png"]


def test_edit_of_another_users_report_is_denied_for_get_and_post(maria, other_user_item):
    get_response = maria.get(f"/edit/{other_user_item.id}")
    post_response = _post_form(maria, f"/edit/{other_user_item.id}", _item_data(name="Hackeado"))

    for response in (get_response, post_response):
        assert response.status_code == 302
        assert _location_path(response) == f"/item/{other_user_item.id}"
    followed = _text(maria.get(f"/edit/{other_user_item.id}", follow_redirects=True))
    assert NOT_OWNER in followed
    assert "alert-danger" in followed
    db.session.expire_all()
    assert db.session.get(Item, other_user_item.id).name == "Paraguas negro"


def test_edit_of_a_missing_report_is_404(maria):
    assert maria.get("/edit/9999").status_code == 404
    assert _post_form(maria, "/edit/9999", _item_data()).status_code == 404


# --- delete ------------------------------------------------------------------


def test_delete_removes_the_report_and_its_photo(app, maria, maria_user, make_item, png_bytes):
    _put_file(app, "1_foto.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_foto.png")

    response = maria.post(f"/delete/{item.id}", follow_redirects=True)
    html = _text(response)

    assert response.request.path == "/"
    assert "Reporte eliminado correctamente." in html
    assert "alert-info" in html
    assert Item.query.count() == 0
    assert _stored_files(app) == []


def test_delete_with_a_missing_photo_file_still_succeeds(maria, maria_user, make_item):
    item = make_item(maria_user, name="Sin archivo", image_filename="1_fantasma.png")

    response = maria.post(f"/delete/{item.id}", follow_redirects=True)

    assert "Reporte eliminado correctamente." in _text(response)
    assert Item.query.count() == 0


def test_delete_never_removes_files_outside_the_upload_folder(app, tmp_path, maria, maria_user, make_item):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    os.makedirs(app.config["ITEM_UPLOAD_FOLDER"])
    item = make_item(maria_user, name="Malicioso", image_filename="../outside.txt")

    maria.post(f"/delete/{item.id}")

    assert Item.query.count() == 0
    assert outside.read_text() == "keep me"


def test_delete_is_post_only(maria, owned_item):
    assert maria.get(f"/delete/{owned_item.id}").status_code == 405
    assert maria.put(f"/delete/{owned_item.id}").status_code == 405
    assert maria.delete(f"/delete/{owned_item.id}").status_code == 405
    assert Item.query.count() == 1


def test_delete_database_error_keeps_the_photo_file(app, maria, maria_user, make_item, png_bytes, monkeypatch):
    _put_file(app, "1_foto.png", png_bytes)
    item = make_item(maria_user, name="Con foto", image_filename="1_foto.png")

    def boom():
        raise SQLAlchemyError("forced failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", boom)
        response = maria.post(f"/delete/{item.id}", follow_redirects=True)
    db.session.rollback()

    assert "No se pudo eliminar el reporte. Intente nuevamente." in _text(response)
    assert Item.query.count() == 1
    assert _stored_files(app) == ["1_foto.png"]


def test_delete_of_another_users_report_is_denied(app, maria, carlos_user, make_item, png_bytes):
    _put_file(app, "2_foto.png", png_bytes)
    item = make_item(carlos_user, name="Ajeno", image_filename="2_foto.png")

    response = maria.post(f"/delete/{item.id}")

    assert response.status_code == 302
    assert _location_path(response) == "/"
    assert NOT_OWNER in _text(maria.get("/"))
    assert Item.query.count() == 1
    assert _stored_files(app) == ["2_foto.png"]


def test_delete_of_a_missing_report_is_404(maria):
    assert maria.post("/delete/9999").status_code == 404


def test_delete_requires_a_csrf_token(csrf_app, make_csrf_user, make_item, csrf_token):
    user = make_csrf_user("maria", "password123")
    item = make_item(user, name="Protegido")
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")
    client.post("/auth/login", data={"username": "maria", "password": "password123", "csrf_token": token})

    rejected = client.post(f"/delete/{item.id}")
    assert rejected.status_code == 302
    assert Item.query.count() == 1

    token = csrf_token(client, "/")
    # The list has no forms, so read the token from the detail page that does.
    token = csrf_token(client, f"/item/{item.id}")
    accepted = client.post(f"/delete/{item.id}", data={"csrf_token": token})
    assert accepted.status_code == 302
    assert Item.query.count() == 0


# --- mark returned -----------------------------------------------------------


def test_mark_returned_changes_the_status_and_flashes_success(maria, owned_item):
    response = maria.post(f"/item/{owned_item.id}/returned", follow_redirects=True)
    html = _text(response)

    assert response.request.path == f"/item/{owned_item.id}"
    assert "El objeto se marcó como entregado." in html
    assert "alert-success" in html
    db.session.expire_all()
    assert db.session.get(Item, owned_item.id).status == "returned"
    assert "Marcar como entregado" not in _main(html)


def test_mark_returned_is_idempotent(maria, maria_user, make_item):
    item = make_item(maria_user, name="Ya entregado", status="returned")

    response = maria.post(f"/item/{item.id}/returned", follow_redirects=True)

    assert "El objeto ya estaba marcado como entregado." in _text(response)
    assert "alert-info" in _text(response)
    db.session.expire_all()
    assert db.session.get(Item, item.id).status == "returned"


def test_mark_returned_from_found_status(maria, maria_user, make_item):
    item = make_item(maria_user, name="Hallado", status="found")

    maria.post(f"/item/{item.id}/returned")

    db.session.expire_all()
    assert db.session.get(Item, item.id).status == "returned"


def test_mark_returned_by_a_non_owner_is_blocked(maria, other_user_item):
    response = maria.post(f"/item/{other_user_item.id}/returned")

    assert response.status_code == 302
    assert _location_path(response) == f"/item/{other_user_item.id}"
    assert NOT_OWNER in _text(maria.get(f"/item/{other_user_item.id}"))
    db.session.expire_all()
    assert db.session.get(Item, other_user_item.id).status == "lost"


def test_mark_returned_is_post_only(maria, owned_item):
    assert maria.get(f"/item/{owned_item.id}/returned").status_code == 405
    assert maria.put(f"/item/{owned_item.id}/returned").status_code == 405
    db.session.expire_all()
    assert db.session.get(Item, owned_item.id).status == "lost"


def test_mark_returned_of_a_missing_report_is_404(maria):
    assert maria.post("/item/9999/returned").status_code == 404


def test_mark_returned_database_error_keeps_the_status(maria, owned_item, monkeypatch):
    def boom():
        raise SQLAlchemyError("forced failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", boom)
        response = maria.post(f"/item/{owned_item.id}/returned", follow_redirects=True)
    db.session.rollback()

    assert "No se pudo actualizar el reporte. Intente nuevamente." in _text(response)
    db.session.expire_all()
    assert db.session.get(Item, owned_item.id).status == "lost"


def test_mark_returned_requires_a_csrf_token(csrf_app, make_csrf_user, make_item, csrf_token):
    user = make_csrf_user("maria", "password123")
    item = make_item(user, name="Protegido")
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")
    client.post("/auth/login", data={"username": "maria", "password": "password123", "csrf_token": token})

    assert client.post(f"/item/{item.id}/returned").status_code == 302
    db.session.expire_all()
    assert db.session.get(Item, item.id).status == "lost"

    token = csrf_token(client, f"/item/{item.id}")
    client.post(f"/item/{item.id}/returned", data={"csrf_token": token})
    db.session.expire_all()
    assert db.session.get(Item, item.id).status == "returned"


# --- upload size limit ---------------------------------------------------------


def test_oversized_upload_shows_a_spanish_message_and_returns_to_the_form(app, maria, png_bytes):
    app.config["MAX_CONTENT_LENGTH"] = 2048

    response = _post_form(
        maria,
        "/add",
        _with_image(_item_data(), png_bytes + b"0" * 5000, "grande.png"),
        headers={"Referer": "http://localhost/add"},
        follow_redirects=True,
    )

    assert response.request.path == "/add"
    assert "El archivo supera el tamaño máximo permitido (5 MB)." in _text(response)
    assert "alert-danger" in _text(response)
    assert Item.query.count() == 0


def test_oversized_upload_without_referrer_goes_to_the_list(app, maria):
    app.config["MAX_CONTENT_LENGTH"] = 2048

    response = _post_form(maria, "/add", _with_image(_item_data(), b"0" * 5000, "grande.png"), follow_redirects=True)

    assert response.request.path == "/"
    assert "El archivo supera el tamaño máximo permitido (5 MB)." in _text(response)


def test_oversized_upload_for_anonymous_users_goes_to_login(app, browser_for):
    app.config["MAX_CONTENT_LENGTH"] = 512

    response = browser_for().post(
        "/auth/login", data={"username": "x", "password": "y" * 4000}, follow_redirects=True
    )

    assert response.request.path == "/auth/login"
    assert "El archivo supera el tamaño máximo permitido (5 MB)." in _text(response)


# --- render smoke ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/?estado=lost&categoria=keys&mes=2026-10&q=ll",
        "/?estado=found",
        "/?mes=2026-03",
        "/add",
        "/item/{id}",
        "/edit/{id}",
        "/import/csv",
        "/dashboard/",
        "/profile/",
    ],
)
def test_authenticated_pages_render(maria, maria_user, make_item, url):
    make_item(maria_user, name="Llaves del laboratorio", category="keys", status="lost", event_date=date(2026, 10, 1))
    make_item(maria_user, name="Lentes", category="other", status="found", event_date=date(2026, 3, 5))
    item = make_item(maria_user, name="Cuaderno", status="returned", event_date=None, location=None)

    response = maria.get(url.format(id=item.id))
    html = _text(response)

    assert response.status_code == 200, url
    assert '<html lang="es"' in html
    assert "Cerrar sesión" in html


def test_anonymous_login_page_and_unknown_url_render(browser_for):
    anonymous = browser_for()

    assert anonymous.get("/auth/login").status_code == 200
    assert anonymous.get("/no-such-page").status_code == 404


def test_navbar_brand_and_links_on_item_pages(maria, owned_item):
    html = _text(maria.get(f"/item/{owned_item.id}"))

    assert "Objetos perdidos" in html
    assert "Nuevo reporte" in html
    assert "Agregar artículo" not in html
    assert ">Buscar<" not in html.split("<main", 1)[0]


def test_legacy_dashboard_template_still_renders(maria, owned_item):
    response = maria.get("/dashboard/")

    assert response.status_code == 200
