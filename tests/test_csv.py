"""CSV export and import of lost-and-found reports: data contract, safety rules and errors."""

import csv
import re
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO

import pytest
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Item, User
from utils import today_local

HEADER = [
    "id", "name", "description", "category", "location", "event_date", "status", "contact",
    "image_filename", "reported_by", "created_at",
]
READ_ERROR = "No se pudo leer el archivo. Verifique que sea un CSV válido."
MISSING_COLUMNS = "El archivo debe incluir todas las columnas: «Objeto», «Lugar», «Fecha del suceso»"
BASE = "name,location,event_date,description,category,status,contact"


def _text(response):
    return response.get_data(as_text=True)


def _main(html):
    return html.split("<main", 1)[1].split("</main>", 1)[0]


def _location_path(response):
    return response.headers["Location"].split("?", 1)[0]


@pytest.fixture
def maria(browser_for):
    """The administrator: the panel, import and export belong to that role."""
    browser = browser_for("maria")
    user = User.query.filter_by(username="maria").one()
    user.is_admin = True
    db.session.commit()
    return browser


@pytest.fixture
def maria_user(maria):
    return User.query.filter_by(username="maria").first()


@pytest.fixture
def carlos_user(maria, make_user):
    return make_user("carlos", "password123")


def _rows(response):
    """Parse an export response into a list of dict rows."""
    text = response.get_data().decode("utf-8-sig")
    return list(csv.DictReader(StringIO(text)))


def _upload(browser, content, filename="objetos.csv", follow=True):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return browser.post(
        "/import/csv",
        data={"file": (BytesIO(content), filename)},
        content_type="multipart/form-data",
        follow_redirects=follow,
    )


def _imported(owner=None):
    query = Item.query.order_by(Item.id)
    if owner is not None:
        query = query.filter_by(user_id=owner.id)
    return query.all()


def _csv(*lines):
    return "\n".join(lines) + "\n"


# --- export ------------------------------------------------------------------


def test_export_requires_login(browser_for):
    response = browser_for().get("/export/csv")

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"


def test_export_header_row_is_the_exact_english_contract(maria):
    first_line = maria.get("/export/csv").get_data().decode("utf-8-sig").splitlines()[0]

    assert first_line == ",".join(HEADER)
    assert first_line == "id,name,description,category,location,event_date,status,contact,image_filename,reported_by,created_at"


def test_export_has_no_quantity_or_price_columns(maria):
    first_line = maria.get("/export/csv").get_data().decode("utf-8-sig").splitlines()[0]

    assert "quantity" not in first_line and "price" not in first_line


def test_export_response_headers_and_bom(maria):
    response = maria.get("/export/csv")

    assert response.status_code == 200
    assert response.content_type == "text/csv; charset=utf-8"
    assert response.headers["Content-Disposition"] == "attachment; filename=objetos.csv"
    assert response.get_data().startswith(b"\xef\xbb\xbf")
    assert response.get_data().count(b"\xef\xbb\xbf") == 1


def test_export_contains_the_reports_of_every_user_ordered_by_id(maria, maria_user, carlos_user, make_item):
    first = make_item(maria_user, name="Primero")
    other = make_item(carlos_user, name="De carlos")
    second = make_item(maria_user, name="Segundo")

    rows = _rows(maria.get("/export/csv"))

    assert [row["name"] for row in rows] == ["Primero", "De carlos", "Segundo"]
    assert [int(row["id"]) for row in rows] == [first.id, other.id, second.id]
    assert [row["reported_by"] for row in rows] == ["maria", "carlos", "maria"]


def test_export_formats_dates_and_keeps_accents(maria, maria_user, make_item):
    make_item(
        maria_user,
        name="Teclado ñandú",
        description="Descripción con, coma y \"comillas\"\ny salto",
        category="electronics",
        location="Biblioteca, piso 2",
        event_date=date(2026, 1, 15),
        status="found",
        contact="ana@universidad.edu",
        image_filename="1_foto.png",
        created_at=datetime(2026, 1, 15, 10, 30, 5),
    )

    raw = maria.get("/export/csv").get_data()
    row = _rows(maria.get("/export/csv"))[0]

    assert "Teclado ñandú".encode("utf-8") in raw
    assert row["name"] == "Teclado ñandú"
    assert row["description"] == "Descripción con, coma y \"comillas\"\ny salto"
    assert row["category"] == "electronics"
    assert row["location"] == "Biblioteca, piso 2"
    assert row["event_date"] == "2026-01-15"
    assert row["status"] == "found"
    assert row["contact"] == "ana@universidad.edu"
    assert row["image_filename"] == "1_foto.png"
    assert row["reported_by"] == "maria"
    assert row["created_at"] == "2026-01-15T10:30:05"


def test_export_leaves_missing_values_empty(maria):
    item = Item(name="Minimo", approval="approved")
    db.session.add(item)
    db.session.commit()

    row = _rows(maria.get("/export/csv"))[0]

    assert (row["description"], row["category"], row["location"], row["event_date"]) == ("", "", "", "")
    assert (row["contact"], row["image_filename"], row["reported_by"]) == ("", "", "")
    assert row["status"] == "lost"


def test_export_of_an_empty_database_is_just_the_header(maria):
    assert _rows(maria.get("/export/csv")) == []


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_export_neutralizes_formula_injection_in_text_columns(maria, carlos_user, make_item, prefix):
    make_item(
        carlos_user,
        name=f"{prefix}CMD()",
        description=f"{prefix}1+1",
        category=f"{prefix}cat",
        location=f"{prefix}Aula",
        contact=f"{prefix}555",
        image_filename=f"{prefix}x.png",
    )
    carlos_user.username = f"{prefix}carlos"
    db.session.commit()

    row = _rows(maria.get("/export/csv"))[0]

    for column in ("name", "description", "category", "location", "contact", "image_filename", "reported_by"):
        assert row[column].startswith("'" + prefix), column


def test_export_does_not_prefix_safe_text_or_structured_columns(maria, maria_user, make_item):
    make_item(maria_user, name="Normal 'con comilla'", description="Texto - con guion")

    row = _rows(maria.get("/export/csv"))[0]

    assert row["name"] == "Normal 'con comilla'"
    assert row["description"] == "Texto - con guion"
    assert row["event_date"] == "2026-03-10"
    assert row["status"] == "lost"


@pytest.fixture
def seeded(maria_user, carlos_user, make_item):
    make_item(maria_user, name="Llaves marzo", category="keys", status="lost", event_date=date(2026, 3, 2))
    make_item(carlos_user, name="Llaves abril", category="keys", status="found", event_date=date(2026, 4, 2))
    make_item(maria_user, name="Cartera marzo", category="bags", status="returned", event_date=date(2026, 3, 30))
    make_item(carlos_user, name="Gorra marzo", category="clothing", status="found", event_date=date(2026, 3, 31))


def test_export_filters_by_month_and_names_the_file_after_it(maria, seeded):
    response = maria.get("/export/csv?mes=2026-03")

    assert response.headers["Content-Disposition"] == "attachment; filename=objetos_2026-03.csv"
    assert [row["name"] for row in _rows(response)] == ["Llaves marzo", "Cartera marzo", "Gorra marzo"]


def test_export_month_boundaries_are_inclusive_and_exclusive(maria, maria_user, make_item):
    make_item(maria_user, name="Ultimo de febrero", event_date=date(2026, 2, 28))
    make_item(maria_user, name="Primero de marzo", event_date=date(2026, 3, 1))
    make_item(maria_user, name="Primero de abril", event_date=date(2026, 4, 1))

    assert [row["name"] for row in _rows(maria.get("/export/csv?mes=2026-03"))] == ["Primero de marzo"]


@pytest.mark.parametrize("mes", ["2026-13", "marzo", "", "2026-3"])
def test_export_ignores_an_invalid_month_and_uses_the_plain_filename(maria, seeded, mes):
    response = maria.get("/export/csv", query_string={"mes": mes})

    assert response.headers["Content-Disposition"] == "attachment; filename=objetos.csv"
    assert len(_rows(response)) == 4


def test_export_filters_by_status_category_and_text(maria, seeded):
    assert [r["name"] for r in _rows(maria.get("/export/csv?estado=found"))] == ["Llaves abril", "Gorra marzo"]
    assert [r["name"] for r in _rows(maria.get("/export/csv?categoria=keys"))] == ["Llaves marzo", "Llaves abril"]
    assert [r["name"] for r in _rows(maria.get("/export/csv?q=cartera"))] == ["Cartera marzo"]
    assert [r["name"] for r in _rows(maria.get("/export/csv?mes=2026-03&estado=found&categoria=clothing"))] == ["Gorra marzo"]


def test_export_ignores_an_unknown_status(maria, seeded):
    assert len(_rows(maria.get("/export/csv?estado=archived"))) == 4


def test_export_with_filters_but_no_matches_is_just_the_header(maria, seeded):
    assert _rows(maria.get("/export/csv?mes=2020-01")) == []


# --- import page -------------------------------------------------------------


def test_import_page_requires_login(browser_for):
    for method in ("get", "post"):
        response = getattr(browser_for(), method)("/import/csv")
        assert response.status_code == 302
        assert _location_path(response) == "/auth/login"


def test_import_page_documents_the_format_in_spanish(maria):
    html = _text(maria.get("/import/csv"))
    body = _main(html)

    assert "Importar reportes" in html.split("</title>")[0]
    assert re.search(r'<form[^>]*enctype="multipart/form-data"', body)
    assert re.search(r'<form[^>]*method="post"', body, re.IGNORECASE)
    assert 'name="csrf_token"' in body
    assert re.search(r'<input[^>]*type="file"[^>]*name="file"|<input[^>]*name="file"[^>]*type="file"', body)
    assert re.search(r'<label[^>]*for="file"', body)
    for label in ("Documentos", "Electrónica", "Llaves", "Ropa y accesorios", "Mochilas y útiles", "Otros"):
        assert label in body
    for label in ("Perdido", "Encontrado", "Entregado"):
        assert label in body
    assert "DD/MM/AAAA" in body
    assert "UTF-8" in body
    assert "punto y coma" in body
    assert "2.000" in body
    for title in ("Objeto", "Lugar", "Fecha del suceso", "Descripción", "Categoría", "Estado", "Contacto"):
        assert f'<th scope="row">{title}</th>' in body, title
    assert "Todos los campos son obligatorios." in body
    assert 'href="/import/plantilla"' in body
    assert 'accept=".xlsx,.csv' in body


def test_import_page_actions(maria):
    body = _main(_text(maria.get("/import/csv")))

    # Two primary actions: download the template (step 1) and upload the file.
    assert body.count("btn-primary") == 2
    assert "Importar archivo" in body
    assert "Descargar plantilla" in body
    assert re.search(r'<a[^>]*href="/export/excel"[^>]*btn-secondary|<a[^>]*btn-secondary[^>]*href="/export/excel"', body)
    assert "Descargar reportes" in body
    assert "Inventario" not in body and "artículo" not in body


# --- import: happy paths -----------------------------------------------------


def test_import_comma_separated_file(maria, maria_user):
    content = _csv(
        "name,location,event_date,description,category,status,contact",
        "Llaves,Biblioteca,2026-03-02,Con llavero,keys,found,ana@u.edu",
        "Cartera,Cafetería,2026-03-05,Negra,bags,lost,999",
    )

    response = _upload(maria, content)

    assert response.request.path == "/"
    assert "Se importaron 2 reportes." in _text(response)
    items = _imported(maria_user)
    assert [(i.name, i.location, i.event_date, i.description, i.category, i.status, i.contact) for i in items] == [
        ("Llaves", "Biblioteca", date(2026, 3, 2), "Con llavero", "keys", "found", "ana@u.edu"),
        ("Cartera", "Cafetería", date(2026, 3, 5), "Negra", "bags", "lost", "999"),
    ]


def test_import_semicolon_separated_file(maria, maria_user):
    content = _csv(
        "name;location;event_date;description;category;status;contact",
        "Llaves;Aula 3, ala norte;02/03/2026;Acero, inox;keys;lost;999",
    )

    response = _upload(maria, content)

    assert "Se importó 1 reporte." in _text(response)
    item = _imported(maria_user)[0]
    assert (item.location, item.event_date, item.description) == ("Aula 3, ala norte", date(2026, 3, 2), "Acero, inox")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-03-02", date(2026, 3, 2)),
        ("02/03/2026", date(2026, 3, 2)),
        ("2/3/2026", date(2026, 3, 2)),
        ("  2026-03-02  ", date(2026, 3, 2)),
        ("29/02/2024", date(2024, 2, 29)),
    ],
)
def test_import_accepts_both_date_formats(maria, maria_user, value, expected):
    _upload(maria, _csv(BASE, f"Llaves,Aula,{value},Desc,keys,lost,999"))

    assert _imported(maria_user)[0].event_date == expected


def test_import_accepts_today(maria, maria_user):
    _upload(maria, _csv(BASE, f"Llaves,Aula,{today_local().isoformat()},Desc,keys,lost,999"))

    assert _imported(maria_user)[0].event_date == today_local()


def test_import_accepts_utf8_bom_and_accents(maria, maria_user):
    content = f"\ufeff{BASE}\nÁrbol ñandú,Cafetería,2026-03-02,Descripción,keys,lost,999\n".encode("utf-8")

    _upload(maria, content)

    item = _imported(maria_user)[0]
    assert (item.name, item.location, item.description) == ("Árbol ñandú", "Cafetería", "Descripción")


def test_import_header_matching_is_case_and_space_insensitive_and_extra_columns_are_ignored(maria, maria_user):
    content = _csv(
        "ID, Name ,LOCATION,Color,Event_Date, Description,CATEGORY,Status,contact",
        "99,Cable,Aula,rojo,2026-03-02,Negro,electronics,lost,999",
    )

    _upload(maria, content)

    item = _imported(maria_user)[0]
    assert (item.name, item.location, item.event_date) == ("Cable", "Aula", date(2026, 3, 2))
    assert item.id != 99


def test_import_without_the_optional_looking_columns_is_rejected(maria):
    response = _upload(maria, _csv("name,location,event_date", "Solo lo básico,Aula,2026-03-02"))

    assert MISSING_COLUMNS in _text(response)
    assert Item.query.count() == 0


@pytest.mark.parametrize(
    "value, expected",
    [
        ("documents", "documents"),
        ("Electronics", "electronics"),
        ("Electrónica", "electronics"),
        ("electronica", "electronics"),
        ("LLAVES", "keys"),
        ("Ropa y accesorios", "clothing"),
        ("Mochilas y útiles", "bags"),
        ("otros", "other"),
        ("  Other  ", "other"),
        ("general", "other"),
        ("office", "other"),
        ("consumable", "other"),
    ],
)
def test_import_normalizes_categories_from_keys_labels_and_legacy_keys(maria, maria_user, value, expected):
    _upload(maria, _csv(BASE, f"Objeto,Aula,2026-03-02,Desc,{value},lost,999"))

    assert _imported(maria_user)[0].category == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("lost", "lost"),
        ("found", "found"),
        ("returned", "returned"),
        ("Perdido", "lost"),
        ("ENCONTRADO", "found"),
        ("Entregado", "returned"),
        ("  entregado ", "returned"),
    ],
)
def test_import_accepts_status_keys_and_spanish_labels(maria, maria_user, value, expected):
    _upload(maria, _csv(BASE, f"Objeto,Aula,2026-03-02,Desc,keys,{value},999"))

    assert _imported(maria_user)[0].status == expected


def test_import_handles_quoted_fields_with_delimiters_and_newlines(maria, maria_user):
    content = f'{BASE}\n"Cable, largo","Aula, 2",2026-03-02,"Linea 1\nLinea 2",keys,lost,999\n'

    _upload(maria, content)

    item = _imported(maria_user)[0]
    assert (item.name, item.location, item.description) == ("Cable, largo", "Aula, 2", "Linea 1\nLinea 2")


def test_import_skips_blank_lines_without_reporting_them(maria):
    response = _upload(maria, f"{BASE}\nUno,Aula,2026-03-02,Desc,keys,lost,999\n\n\nDos,Aula,2026-03-02,Desc,keys,lost,999\n")

    html = _text(response)
    assert "Se importaron 2 reportes." in html
    assert "omitieron" not in html and "omitió" not in html


def test_import_falls_back_to_latin1_when_the_file_is_not_utf8(maria, maria_user):
    content = f"{BASE}\nTornillo ñandú,Cafetería,2026-03-02,Descripción,keys,lost,999\n".encode("latin-1")

    response = _upload(maria, content)

    assert "Se importó 1 reporte." in _text(response)
    item = _imported(maria_user)[0]
    assert (item.name, item.location, item.description) == ("Tornillo ñandú", "Cafetería", "Descripción")


def test_import_assigns_reports_to_the_current_user_only(maria, maria_user, carlos_user, make_item):
    make_item(carlos_user, name="De carlos")

    _upload(maria, _csv(BASE, "De maria,Aula,2026-03-02,Desc,keys,lost,999"))

    assert [i.name for i in _imported(maria_user)] == ["De maria"]
    assert [i.name for i in _imported(carlos_user)] == ["De carlos"]


def test_import_never_reads_image_filename_or_ownership_columns(maria, maria_user, carlos_user):
    content = _csv(
        f"{BASE},image_filename,user_id,created_at",
        f"Con imagen,Aula,2026-03-02,Desc,keys,lost,999,../../secret.png,{carlos_user.id},2001-01-01",
    )

    _upload(maria, content)

    item = _imported()[0]
    assert item.image_filename is None
    assert item.user_id == maria_user.id
    assert item.created_at.year != 2001


def test_import_unescapes_the_formula_guard_added_by_the_export(maria, maria_user, make_item):
    make_item(
        maria_user, name="=SUMA(A1)", description="-nota", location="@Aula", contact="+555",
        category="electronics",
    )
    exported = maria.get("/export/csv").get_data()
    Item.query.delete()
    db.session.commit()

    _upload(maria, exported)

    item = _imported(maria_user)[0]
    assert (item.name, item.description, item.location, item.contact, item.category) == (
        "=SUMA(A1)", "-nota", "@Aula", "+555", "electronics",
    )


def test_export_then_import_round_trip_preserves_data(maria, maria_user, make_item):
    make_item(
        maria_user, name="Teclado", description="Con, coma", category="electronics",
        location="Biblioteca", event_date=date(2026, 2, 3), status="found", contact="ana@u.edu",
    )
    make_item(maria_user, name="Ratón ñ", description="Inalámbrico", category="other", event_date=date(2026, 2, 4), contact="999")
    exported = maria.get("/export/csv").get_data()

    _upload(maria, exported)

    clones = _imported(maria_user)[2:]
    assert [(i.name, i.description, i.category, i.location, i.event_date, i.status, i.contact) for i in clones] == [
        ("Teclado", "Con, coma", "electronics", "Biblioteca", date(2026, 2, 3), "found", "ana@u.edu"),
        ("Ratón ñ", "Inalámbrico", "other", "Biblioteca central", date(2026, 2, 4), "lost", "999"),
    ]


# --- import: row validation --------------------------------------------------


def _tomorrow():
    return (today_local() + timedelta(days=1)).isoformat()


@pytest.mark.parametrize(
    "row, reason",
    [
        (",Aula,2026-03-02,D,keys,lost,9", "nombre requerido"),
        ("   ,Aula,2026-03-02,D,keys,lost,9", "nombre requerido"),
        ("x" * 121 + ",Aula,2026-03-02,D,keys,lost,9", "nombre demasiado largo"),
        ("Obj,,2026-03-02,D,keys,lost,9", "lugar requerido"),
        ("Obj,   ,2026-03-02,D,keys,lost,9", "lugar requerido"),
        ("Obj," + "l" * 121 + ",2026-03-02,D,keys,lost,9", "lugar demasiado largo"),
        ("Obj,Aula,,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,hoy,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,2026-02-30,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,31/04/2026,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,2026/03/02,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,03-02-2026,D,keys,lost,9", "fecha inválida"),
        ("Obj,Aula,9999-12-31,D,keys,lost,9", "fecha futura"),
        ("Obj,Aula,2026-03-02,,keys,lost,9", "descripción requerida"),
        ("Obj,Aula,2026-03-02," + "d" * 501 + ",keys,lost,9", "descripción demasiado larga"),
        ("Obj,Aula,2026-03-02,D,,lost,9", "categoría requerida"),
        ("Obj,Aula,2026-03-02,D,Herramientas,lost,9", "categoría desconocida"),
        ("Obj,Aula,2026-03-02,D,keys,,9", "estado requerido"),
        ("Obj,Aula,2026-03-02,D,keys,archivado,9", "estado desconocido"),
        ("Obj,Aula,2026-03-02,D,keys,lost,", "contacto requerido"),
        ("Obj,Aula,2026-03-02,D,keys,lost," + "c" * 121, "contacto demasiado largo"),
    ],
)
def test_invalid_rows_are_skipped_with_a_reason_and_valid_rows_still_import(maria, maria_user, row, reason):
    content = _csv(
        "name,location,event_date,description,category,status,contact",
        "Bueno,Aula,2026-03-02,ok,keys,lost,999",
        row,
    )

    response = _upload(maria, content)
    html = _text(response)

    assert "Se importó 1 reporte." in html
    assert f"Se omitió 1 fila: fila 3 ({reason})." in html
    assert "alert-warning" in html
    assert [i.name for i in _imported(maria_user)] == ["Bueno"]


def test_future_date_is_rejected_relative_to_today(maria, maria_user):
    response = _upload(maria, _csv(BASE, f"Futuro,Aula,{_tomorrow()},Desc,keys,lost,999", f"Hoy,Aula,{today_local().isoformat()},Desc,keys,lost,999"))

    assert "fila 2 (fecha futura)" in _text(response)
    assert [i.name for i in _imported(maria_user)] == ["Hoy"]


def test_future_date_in_day_first_format_is_rejected(maria):
    tomorrow = today_local() + timedelta(days=1)

    response = _upload(maria, _csv(BASE, f"Futuro,Aula,{tomorrow.strftime('%d/%m/%Y')},Desc,keys,lost,999"))

    assert "fila 2 (fecha futura)" in _text(response)
    assert Item.query.count() == 0


def test_boundary_lengths_are_accepted(maria, maria_user):
    content = _csv(BASE, f"{'n' * 120},{'l' * 120},2026-03-02,{'d' * 500},keys,lost,{'c' * 120}")

    response = _upload(maria, content)

    assert "Se importó 1 reporte." in _text(response)
    assert len(_imported(maria_user)) == 1


def test_skipped_rows_report_spreadsheet_row_numbers_with_blank_lines_counted(maria):
    response = _upload(maria, f"{BASE}\nBueno,Aula,2026-03-02,Desc,keys,lost,999\n\nMalo,Aula,hoy,Desc,keys,lost,999\n,Aula,2026-03-02,Desc,keys,lost,999\n")

    html = _text(response)
    assert "fila 4 (fecha inválida)" in html
    assert "fila 5 (nombre requerido)" in html
    assert "Se omitieron 2 filas:" in html


def test_skipped_report_lists_at_most_five_reasons_then_the_remainder(maria, maria_user):
    lines = [BASE, "Bueno,Aula,2026-03-02,Desc,keys,lost,999"] + [f"Malo {n},Aula,hoy,Desc,keys,lost,999" for n in range(7)]

    response = _upload(maria, _csv(*lines))
    html = _text(response)

    assert "Se omitieron 7 filas:" in html
    assert html.count("(fecha inválida)") == 5
    assert "fila 3 (fecha inválida)" in html
    assert "fila 7 (fecha inválida)" in html
    assert "fila 8" not in html
    assert "y 2 más" in html
    assert [i.name for i in _imported(maria_user)] == ["Bueno"]


def test_import_with_no_valid_rows_warns_and_stays_on_the_import_page(maria, maria_user):
    response = _upload(maria, _csv(BASE, "Malo,Aula,hoy,Desc,keys,lost,999"))

    html = _text(response)
    assert response.request.path == "/import/csv"
    assert "No se importó ningún reporte." in html
    assert "fila 2 (fecha inválida)" in html
    assert _imported(maria_user) == []


def test_import_of_a_header_only_file_warns(maria):
    response = _upload(maria, f"{BASE}\n")

    assert "El archivo no contiene filas para importar." in _text(response)
    assert Item.query.count() == 0


# --- import: file level errors -----------------------------------------------


@pytest.mark.parametrize(
    "header",
    ["nombres,location,event_date", "name,event_date", "name,location", "name", "location,event_date", "objeto,lugar"],
)
def test_import_rejects_files_missing_a_required_column(maria, header):
    response = _upload(maria, _csv(header, "a,b,2026-03-02"))

    assert MISSING_COLUMNS in _text(response)
    assert response.request.path == "/import/csv"
    assert Item.query.count() == 0


def test_import_rejects_an_empty_file(maria):
    response = _upload(maria, "")

    assert MISSING_COLUMNS in _text(response)
    assert Item.query.count() == 0


def test_import_rejects_more_than_2000_rows_without_importing_any(maria):
    lines = [BASE] + [f"Objeto {n},Aula,2026-03-02,Desc,keys,lost,999" for n in range(2001)]

    response = _upload(maria, _csv(*lines))

    assert "El archivo supera el máximo de 2000 filas." in _text(response)
    assert Item.query.count() == 0


def test_import_accepts_exactly_2000_rows(maria, maria_user):
    lines = [BASE] + [f"Objeto {n},Aula,2026-03-02,Desc,keys,lost,999" for n in range(2000)]

    response = _upload(maria, _csv(*lines))

    assert "Se importaron 2.000 reportes." in _text(response)
    assert Item.query.filter_by(user_id=maria_user.id).count() == 2000


def test_row_cap_ignores_blank_lines(maria):
    lines = [BASE] + ["Objeto,Aula,2026-03-02,Desc,keys,lost,999"] * 5 + [""] * 3000

    response = _upload(maria, _csv(*lines))

    assert "Se importaron 5 reportes." in _text(response)


@pytest.mark.parametrize("payload", [b"name\n\x00\x01\x02", f"{BASE}\nTornillo,Aula,2026-03-02,Desc,keys,lost,999\n".encode("utf-16")])
def test_import_rejects_binary_or_utf16_files(maria, payload):
    response = _upload(maria, payload)

    assert READ_ERROR in _text(response)
    assert Item.query.count() == 0


def test_import_rejects_oversized_csv_fields(maria):
    response = _upload(maria, f"{BASE}\n" + "x" * 200000 + ",Aula,2026-03-02,Desc,keys,lost,999\n")

    assert READ_ERROR in _text(response)
    assert Item.query.count() == 0


def test_import_without_a_file_asks_to_select_one(maria):
    response = maria.post("/import/csv", data={}, follow_redirects=True)

    assert "Seleccione un archivo Excel (.xlsx) o CSV." in _text(response)
    assert response.request.path == "/import/csv"


def test_import_with_an_empty_file_input_asks_to_select_one(maria):
    response = maria.post(
        "/import/csv",
        data={"file": (BytesIO(b""), "")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "Seleccione un archivo Excel (.xlsx) o CSV." in _text(response)


def test_database_failure_rolls_back_every_row_and_reports_it(maria, monkeypatch):
    def boom():
        raise SQLAlchemyError("forced failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", boom)
        response = _upload(maria, _csv(BASE, "Uno,Aula,2026-03-02,Desc,keys,lost,999", "Dos,Aula,2026-03-02,Desc,keys,lost,999", "Tres,Aula,2026-03-02,Desc,keys,lost,999"))
    db.session.rollback()

    assert "No se pudo completar la importación. No se guardó ningún reporte." in _text(response)
    assert Item.query.count() == 0


def test_valid_rows_are_inserted_in_a_single_commit(maria, monkeypatch):
    commits = []
    real_commit = db.session.commit

    def counting_commit():
        commits.append(1)
        return real_commit()

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", counting_commit)
        _upload(maria, _csv(BASE, "Uno,Aula,2026-03-02,Desc,keys,lost,999", "Dos,Aula,2026-03-02,Desc,keys,lost,999", "Tres,Aula,2026-03-02,Desc,keys,lost,999"))

    assert len(commits) == 1
    assert Item.query.count() == 3


def test_import_requires_a_csrf_token(csrf_app, make_csrf_user, csrf_token):
    make_csrf_user("maria", "password123").is_admin = True
    db.session.commit()
    client = csrf_app.test_client()
    token = csrf_token(client, "/auth/login")
    client.post("/auth/login", data={"username": "maria", "password": "password123", "csrf_token": token})
    content = f"{BASE}\nUno,Aula,2026-03-02,Desc,keys,lost,999\n".encode()

    rejected = client.post(
        "/import/csv", data={"file": (BytesIO(content), "x.csv")}, content_type="multipart/form-data"
    )
    assert rejected.status_code == 302
    assert Item.query.count() == 0

    token = csrf_token(client, "/import/csv")
    client.post(
        "/import/csv",
        data={"file": (BytesIO(content), "x.csv"), "csrf_token": token},
        content_type="multipart/form-data",
    )
    assert Item.query.count() == 1


def test_anonymous_import_cannot_create_reports(browser_for):
    response = _upload(browser_for(), _csv(BASE, "Uno,Aula,2026-03-02,Desc,keys,lost,999"), follow=False)

    assert response.status_code == 302
    assert _location_path(response) == "/auth/login"
    assert Item.query.count() == 0
