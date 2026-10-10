"""Lost-and-found routes: list with filters, detail, create, edit, delete request, mark returned,
Excel export and Excel/CSV import."""

import csv
import os
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from io import StringIO

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from PIL import Image
from sqlalchemy import event, func, or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from domain import (
    CATEGORY_LABELS,
    CONVERSATION_KINDS,
    APPROVAL_LABELS,
    LEGACY_CATEGORY_MAP,
    STATUS_LABELS,
    SUMMARY_APPROVALS,
    default_conversation_kind,
    month_bounds,
    parse_month,
)
from extensions import db
from forms import (
    ITEM_CONTACT_MAX,
    ITEM_DESCRIPTION_MAX,
    ITEM_IMAGE_EXTENSIONS,
    ITEM_LOCATION_MAX,
    ITEM_NAME_MAX,
    AddItemForm,
    ContactForm,
    EditItemForm,
)
from admin import admin_required, is_admin
from excel import XLSX_MIMETYPE, build_export, build_import_template, read_xlsx_records
from models import Item, visible_items_filter
from utils import MONTH_NAMES, _display_zone, formato_numero, today_local

bp = Blueprint("main", __name__, template_folder="templates")

NOT_OWNER = "Solo quien creó el reporte puede modificarlo."
INACTIVE_LOCKED = "El administrador desactivó este reporte; ya no se puede modificar."
DELETION_REASON_MAX = 300
INVALID_FORMAT = "Formato no permitido. Use PNG, JPG, GIF o WEBP."
INVALID_IMAGE = "El archivo no es una imagen válida."

# Pillow format names that match ITEM_IMAGE_EXTENSIONS.
IMAGE_FORMATS = {"PNG", "JPEG", "GIF", "WEBP"}

CSV_HEADER = [
    "id", "name", "description", "category", "location", "event_date", "status", "contact",
    "image_filename", "reported_by", "created_at",
]
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# Every field is required, as in the «Nuevo reporte» form.
CSV_REQUIRED_COLUMNS = ("name", "location", "event_date", "description", "category", "status", "contact")
# Column titles accepted on import (compared without accents, case or a trailing '*'): the English
# keys of the CSV export and the Spanish titles of the Excel export and template.
IMPORT_COLUMN_ALIASES = {
    "name": ("name", "objeto", "nombre", "nombre del objeto"),
    "location": ("location", "lugar"),
    "event_date": ("event_date", "fecha del suceso", "fecha"),
    "description": ("description", "descripcion"),
    "category": ("category", "categoria"),
    "status": ("status", "estado"),
    "contact": ("contact", "contacto"),
}
HEADER_SEARCH_ROWS = 10
XLSX_SIGNATURE = b"PK\x03\x04"
MAX_IMPORT_ROWS = 2000
MAX_REPORTED_SKIPS = 5


# -----------------------------
# SEARCH SUPPORT (SQLite)
# -----------------------------
def _casefold(value):
    return value.casefold() if isinstance(value, str) else value


@event.listens_for(Engine, "connect")
def _register_sqlite_functions(dbapi_connection, _connection_record):
    # SQLite lower()/LIKE only fold ASCII letters, so "ÁRBOL" would not match "árbol".
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function("py_casefold", 1, _casefold, deterministic=True)


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# -----------------------------
# IMAGE HANDLING
# -----------------------------
def _has_upload(file_storage):
    return bool(file_storage and file_storage.filename)


def _check_image(file_storage):
    """Validate an uploaded image. Return (extension, None) or (None, error message)."""
    filename = file_storage.filename or ""
    extension = os.path.splitext(filename)[1].lower().lstrip(".")
    if not secure_filename(filename) or extension not in ITEM_IMAGE_EXTENSIONS:
        return None, INVALID_FORMAT

    stream = file_storage.stream
    try:
        stream.seek(0)
        image = Image.open(stream)
        image.verify()
        is_image = image.format in IMAGE_FORMATS
    except Exception:  # Pillow raises many different errors on corrupt or foreign input
        is_image = False
    finally:
        stream.seek(0)

    if not is_image:
        return None, INVALID_IMAGE
    return extension, None


def _save_image(file_storage, extension):
    """Store a validated upload as '<user_id>_<UTC timestamp>.<ext>' and return its name."""
    folder = current_app.config["ITEM_UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    while True:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        filename = f"{current_user.id}_{timestamp}.{extension}"
        path = os.path.join(folder, filename)
        if not os.path.exists(path):
            break
    file_storage.stream.seek(0)
    file_storage.save(path)
    return filename


def _remove_image_file(filename):
    """Delete a stored image. Only the base name is used, always inside the item folder."""
    if not filename:
        return
    name = os.path.basename(filename.replace("\\", "/"))
    if name in ("", ".", ".."):
        return
    path = os.path.join(current_app.config["ITEM_UPLOAD_FOLDER"], name)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.warning("Could not delete item image %s", path, exc_info=True)


def _add_field_error(field, message):
    field.errors = list(field.errors) + [message]


# -----------------------------
# ACCESS RULES
# -----------------------------
# Every authenticated user can read every report; only the creator can change it.
def _owned_item(item_id, fallback_endpoint):
    """Return (item, None) for the creator, or (item, redirect response) after a flash.

    A missing id aborts with 404. Reports without an owner (legacy rows) belong to nobody.
    """
    item = db.get_or_404(Item, item_id)
    if item.user_id is None or item.user_id != current_user.id:
        flash(NOT_OWNER, "danger")
        if fallback_endpoint == "main.view":
            return item, redirect(url_for("main.view", item_id=item.id))
        return item, redirect(url_for(fallback_endpoint))
    if item.is_inactive and not is_admin(current_user):
        flash(INACTIVE_LOCKED, "warning")
        return item, redirect(url_for("main.view", item_id=item.id))
    return item, None


# -----------------------------
# LIST FILTERS
# -----------------------------
def _legacy_categories():
    """Stored categories that are not current keys (old data or free-text CSV imports)."""
    rows = (
        db.session.query(Item.category)
        .filter(Item.category.isnot(None), Item.category != "")
        .filter(Item.category.notin_(list(CATEGORY_LABELS)))
        .distinct()
        .order_by(Item.category)
        .all()
    )
    return [row[0] for row in rows]


def _read_filters(args):
    """Return the validated filters of a request: q, estado, categoria, mes (strings) and bounds."""
    q = args.get("q", "").strip()

    estado = args.get("estado", "").strip()
    if estado not in STATUS_LABELS:
        estado = ""

    categoria = args.get("categoria", "").strip()
    if categoria and categoria not in CATEGORY_LABELS:
        stored = db.session.query(Item.id).filter(Item.category == categoria).first()
        if stored is None:
            categoria = ""

    mes = ""
    bounds = None
    parsed = parse_month(args.get("mes", ""), None)
    if parsed is not None:
        mes = f"{parsed[0]:04d}-{parsed[1]:02d}"
        bounds = month_bounds(*parsed)

    return {"q": q, "estado": estado, "categoria": categoria, "mes": mes, "bounds": bounds}


def _apply_filters(query, filters, with_status=True, visibility=None):
    # Only published reports, plus the user's own ones while they wait for review.
    if visibility is None:
        visibility = visible_items_filter(current_user)
    query = query.filter(visibility)
    if filters["q"]:
        pattern = f"%{_escape_like(filters['q'].casefold())}%"
        query = query.filter(
            or_(
                *(
                    func.py_casefold(column, type_=db.String).like(pattern, escape="\\")
                    for column in (Item.name, Item.description, Item.location)
                )
            )
        )
    if filters["categoria"]:
        query = query.filter(Item.category == filters["categoria"])
    if filters["bounds"]:
        first_day, next_month = filters["bounds"]
        query = query.filter(Item.event_date >= first_day, Item.event_date < next_month)
    if with_status and filters["estado"]:
        query = query.filter(Item.status == filters["estado"])
    return query


def _public_params(filters):
    """Filters worth keeping in URLs (empty ones are dropped)."""
    return {key: filters[key] for key in ("q", "estado", "categoria", "mes") if filters[key]}


def _status_counts(filters):
    """Reports per status for q/categoria/mes (the active status is ignored on purpose)."""
    rows = _apply_filters(
        db.session.query(Item.status, func.count(Item.id)), filters, with_status=False
    ).group_by(Item.status)
    counts = {key: 0 for key in STATUS_LABELS}
    for status, amount in rows:
        counts[status] = amount
    counts["all"] = sum(counts.values())
    return counts


# -----------------------------
# LIST (THE ONE LISTING AND SEARCH PAGE)
# -----------------------------
@bp.route("/")
@login_required
def index():
    filters = _read_filters(request.args)
    page = request.args.get("page", 1, type=int)

    query = _apply_filters(Item.query.options(joinedload(Item.user)), filters).order_by(
        Item.event_date.is_(None), Item.event_date.desc(), Item.id.desc()
    )
    items = query.paginate(page=page, per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    if items.pages and page > items.pages:
        return redirect(url_for("main.index", page=items.pages, **_public_params(filters)))

    counts = _status_counts(filters)
    legacy = _legacy_categories()
    return render_template(
        "index.html",
        items=items,
        filters=filters,
        filter_params=_public_params(filters),
        filters_active=bool(_public_params(filters)),
        status_counts=counts,
        total=counts["all"],
        category_options=list(CATEGORY_LABELS.items()) + [(value, value) for value in legacy],
    )


# -----------------------------
# SEARCH (LEGACY URL)
# -----------------------------
@bp.route("/search")
@login_required
def search():
    """Old search URL: the list page now owns search. 302 (not 301) so browsers never cache it."""
    url = url_for("main.index")
    query_string = request.query_string.decode("utf-8", errors="ignore")
    if query_string:
        url = f"{url}?{query_string}"
    return redirect(url, code=302)


# -----------------------------
# VIEW REPORT (ANY AUTHENTICATED USER)
# -----------------------------
@bp.route("/item/<int:item_id>")
@login_required
def view(item_id):
    item = db.get_or_404(Item, item_id)
    return render_item_view(item)


def render_item_view(item, contact_form=None):
    """Render the report page; `contact_form` carries errors back from messages.start."""
    from messages import conversation_for, received_conversations, unread_by_conversation

    is_owner = item.user_id is not None and item.user_id == current_user.id
    admin_view = is_admin(current_user)
    context = {
        "item": item,
        "is_owner": is_owner,
        "admin_view": admin_view,
        "approval_labels": APPROVAL_LABELS,
    }
    if not admin_view and (item.is_inactive or (not item.is_approved and not is_owner)):
        return render_template("view_hidden.html", **context), 404
    if is_owner:
        received = received_conversations(item)
        context["received"] = received
        context["received_unread"] = unread_by_conversation(current_user.id, [c.id for c in received])
    elif item.user_id is not None and item.is_approved:
        context["my_conversation"] = conversation_for(item.id, current_user.id)
        if contact_form is None:
            contact_form = ContactForm(kind=default_conversation_kind(item.status))
        context["contact_form"] = contact_form
    context["kinds"] = CONVERSATION_KINDS
    return render_template("view.html", **context)


# -----------------------------
# ADD REPORT
# -----------------------------
@bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    form = AddItemForm()

    if form.validate_on_submit():
        upload = form.image.data
        extension = None
        if _has_upload(upload):
            extension, error = _check_image(upload)
            if error:
                _add_field_error(form.image, error)
                return render_template("add.html", form=form)

        image_filename = _save_image(upload, extension) if extension else None
        item = Item(
            name=form.name.data,
            description=form.description.data or None,
            category=form.category.data or None,
            location=form.location.data,
            event_date=form.event_date.data,
            status=form.status.data,
            contact=form.contact.data or None,
            image_filename=image_filename,
            user_id=current_user.id,
            # The administrator publishes directly; everyone else waits for approval.
            approval="approved" if is_admin(current_user) else "pending",
        )
        db.session.add(item)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not save a new report")
            _remove_image_file(image_filename)
            form.form_errors.append("No se pudo guardar el reporte. Intente nuevamente.")
            return render_template("add.html", form=form)

        if item.is_approved:
            flash("Reporte registrado y publicado.", "success")
        else:
            flash("Reporte registrado. Quedó «En revisión» hasta que el administrador lo apruebe.", "success")
        return redirect(url_for("main.index"))

    return render_template("add.html", form=form)


# -----------------------------
# EDIT REPORT (ONLY CREATOR)
# -----------------------------
@bp.route("/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item, denied = _owned_item(item_id, "main.view")
    if denied is not None:
        return denied

    form = EditItemForm(obj=item)
    if item.category and item.category not in CATEGORY_LABELS:
        # Keep legacy or imported categories selectable so saving does not erase them.
        form.category.choices = list(form.category.choices) + [(item.category, item.category)]

    if form.validate_on_submit():
        # The form and the new image are fully validated before anything is written or removed.
        upload = form.image.data
        extension = None
        if _has_upload(upload):
            extension, error = _check_image(upload)
            if error:
                _add_field_error(form.image, error)
                return render_template("edit.html", form=form, item=item)

        previous_image = item.image_filename
        new_image = _save_image(upload, extension) if extension else None

        item.name = form.name.data
        item.description = form.description.data or None
        item.category = form.category.data or None
        item.location = form.location.data
        item.event_date = form.event_date.data
        item.status = form.status.data
        item.contact = form.contact.data or None
        if new_image:
            item.image_filename = new_image
        # Any change by a regular user goes back to review, so approved content cannot be swapped.
        if not is_admin(current_user):
            item.approval = "pending"
            item.rejection_reason = None
            # Editing means the author wants to keep it: a pending deletion request is dropped.
            item.deletion_requested_at = None
            item.deletion_reason = None

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not update report %s", item_id)
            _remove_image_file(new_image)
            form.form_errors.append("No se pudieron guardar los cambios. Intente nuevamente.")
            return render_template("edit.html", form=form, item=item)

        if new_image:
            _remove_image_file(previous_image)

        if item.is_approved:
            flash("Reporte actualizado correctamente.", "success")
        else:
            flash("Reporte actualizado. Vuelve a estar «En revisión» hasta que el administrador lo apruebe.", "success")
        return redirect(url_for("main.view", item_id=item.id))

    return render_template("edit.html", form=form, item=item)


# -----------------------------
# DELETE REPORT (ONLY CREATOR, POST ONLY)
# -----------------------------
# A report that was never published (in review or disapproved) is deleted by its author at
# once, and so are the administrator's own reports. Once published, a regular user only sends a
# request: the administrator then deactivates the report (kept in the Excel summary) or deletes it.
@bp.route("/delete/<int:item_id>", methods=["POST"])
@login_required
def delete(item_id):
    item, denied = _owned_item(item_id, "main.index")
    if denied is not None:
        return denied

    if item.is_approved and not is_admin(current_user):
        return _request_deletion(item)

    image_filename = item.image_filename
    db.session.delete(item)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not delete report %s", item_id)
        flash("No se pudo eliminar el reporte. Intente nuevamente.", "danger")
        return redirect(url_for("main.index"))

    _remove_image_file(image_filename)
    flash("Reporte eliminado correctamente.", "info")
    return redirect(url_for("main.index"))


def _request_deletion(item):
    if item.deletion_requested:
        flash("Ya había solicitado eliminar este reporte. El administrador la revisará.", "info")
        return redirect(url_for("main.view", item_id=item.id))

    reason = (request.form.get("reason") or "").strip()[:DELETION_REASON_MAX]
    item.deletion_requested_at = datetime.utcnow()
    item.deletion_reason = reason or None
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not request deletion of report %s", item.id)
        flash("No se pudo enviar la solicitud. Intente nuevamente.", "danger")
        return redirect(url_for("main.view", item_id=item.id))

    flash("Solicitud enviada. El administrador decidirá si desactiva o elimina el reporte.", "success")
    return redirect(url_for("main.view", item_id=item.id))


@bp.route("/item/<int:item_id>/cancelar-eliminacion", methods=["POST"])
@login_required
def cancel_deletion(item_id):
    item, denied = _owned_item(item_id, "main.view")
    if denied is not None:
        return denied

    if item.deletion_requested:
        item.deletion_requested_at = None
        item.deletion_reason = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not cancel deletion of report %s", item_id)
            flash("No se pudo cancelar la solicitud. Intente nuevamente.", "danger")
            return redirect(url_for("main.view", item_id=item.id))
        flash("Se canceló la solicitud de eliminación.", "info")
    return redirect(url_for("main.view", item_id=item.id))


# -----------------------------
# MARK AS RETURNED (ONLY CREATOR, POST ONLY)
# -----------------------------
@bp.route("/item/<int:item_id>/returned", methods=["POST"])
@login_required
def mark_returned(item_id):
    item, denied = _owned_item(item_id, "main.view")
    if denied is not None:
        return denied

    if item.status == "returned":
        flash("El objeto ya estaba marcado como entregado.", "info")
        return redirect(url_for("main.view", item_id=item.id))

    item.status = "returned"
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not mark report %s as returned", item_id)
        flash("No se pudo actualizar el reporte. Intente nuevamente.", "danger")
        return redirect(url_for("main.view", item_id=item_id))

    flash("El objeto se marcó como entregado.", "success")
    return redirect(url_for("main.view", item_id=item.id))


# -----------------------------
# EXPORT CSV (EVERY REPORT, OPTIONAL FILTERS)
# -----------------------------
def _csv_text(value):
    """Return a text cell, prefixed with a quote when a spreadsheet could run it as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


@bp.route("/export/csv")
@admin_required
def export_csv():
    filters = _read_filters(request.args)
    items = (
        _apply_filters(Item.query.options(joinedload(Item.user)), filters)
        .order_by(Item.id)
        .all()
    )

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for item in items:
        writer.writerow([
            item.id,
            _csv_text(item.name),
            _csv_text(item.description),
            _csv_text(item.category),
            _csv_text(item.location),
            item.event_date.isoformat() if item.event_date else "",
            item.status,
            _csv_text(item.contact),
            _csv_text(item.image_filename),
            _csv_text(item.user.username if item.user else ""),
            item.created_at.isoformat() if item.created_at else "",
        ])

    filename = f"objetos_{filters['mes']}.csv" if filters["mes"] else "objetos.csv"
    # utf-8-sig adds the BOM that lets Excel detect UTF-8 (accents and ñ).
    response = Response(buffer.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    return response


# -----------------------------
# EXPORT EXCEL (FORMATTED, WITH A SUMMARY SHEET)
# -----------------------------
def _export_subtitle(filters):
    parts = []
    if filters["mes"]:
        year, month = (int(part) for part in filters["mes"].split("-"))
        parts.append(f"Periodo: {MONTH_NAMES[month - 1]} de {year}")
    else:
        parts.append("Periodo: todos los reportes")
    if filters["estado"]:
        parts.append(f"Estado: {STATUS_LABELS[filters['estado']]}")
    if filters["categoria"]:
        parts.append(f"Categoría: {CATEGORY_LABELS.get(filters['categoria'], filters['categoria'])}")
    if filters["q"]:
        parts.append(f"Búsqueda: «{filters['q']}»")
    now = datetime.now(timezone.utc).astimezone(_display_zone())
    parts.append(f"Generado el {now:%d/%m/%Y %H:%M}")
    return " · ".join(parts)


def _local_naive(value):
    """Stored UTC datetime -> local time without tzinfo (Excel has no time zones)."""
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(_display_zone()).replace(tzinfo=None)


@bp.route("/export/excel")
@admin_required
def export_excel():
    """Published and deactivated reports: a deleted report is the only one that disappears."""
    filters = _read_filters(request.args)
    items = (
        _apply_filters(
            Item.query.options(joinedload(Item.user)),
            filters,
            visibility=Item.approval.in_(SUMMARY_APPROVALS),
        )
        .order_by(Item.event_date.is_(None), Item.event_date, Item.id)
        .all()
    )
    content = build_export(items, _export_subtitle(filters), _local_naive)
    filename = f"objetos_{filters['mes']}.xlsx" if filters["mes"] else "objetos.xlsx"
    response = Response(content, content_type=XLSX_MIMETYPE)
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    return response


@bp.route("/import/plantilla")
@admin_required
def import_template():
    response = Response(build_import_template(today_local()), content_type=XLSX_MIMETYPE)
    response.headers.set("Content-Disposition", "attachment", filename="plantilla_importar_reportes.xlsx")
    return response


# -----------------------------
# IMPORT CSV (REPORTS BELONG TO THE CURRENT USER)
# -----------------------------
def _fold(text):
    """Lowercase and strip accents so 'Electrónica', 'electronica' and 'ELECTRONICA' match."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


CATEGORY_LOOKUP = {}
for _key, _label in CATEGORY_LABELS.items():
    CATEGORY_LOOKUP[_fold(_key)] = _key
    CATEGORY_LOOKUP[_fold(_label)] = _key
for _legacy, _key in LEGACY_CATEGORY_MAP.items():
    CATEGORY_LOOKUP.setdefault(_fold(_legacy), _key)

ALIAS_LOOKUP = {}
for _key, _aliases in IMPORT_COLUMN_ALIASES.items():
    for _alias in _aliases:
        ALIAS_LOOKUP[_fold(_alias)] = _key

STATUS_LOOKUP = {}
for _key, _label in STATUS_LABELS.items():
    STATUS_LOOKUP[_fold(_key)] = _key
    STATUS_LOOKUP[_fold(_label)] = _key

ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
DAY_FIRST_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})", re.ASCII)


def _parse_event_date(raw):
    """Parse 'YYYY-MM-DD' or 'DD/MM/YYYY'; return None when the text is not a real date."""
    text = raw.strip()
    try:
        if match := ISO_DATE.fullmatch(text):
            year, month, day = (int(part) for part in match.groups())
        elif match := DAY_FIRST_DATE.fullmatch(text):
            day, month, year = (int(part) for part in match.groups())
        else:
            return None
        return date(year, month, day)
    except ValueError:
        return None


def _decode_csv(raw):
    """Decode as UTF-8 (BOM allowed), then Latin-1. Return None for binary or UTF-16 data."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        if text.startswith("\u00ef\u00bb\u00bf"):  # UTF-8 BOM read as Latin-1
            text = text[3:]
    return None if "\x00" in text else text


def _detect_delimiter(text):
    for line in text.splitlines():
        if line.strip():
            return ";" if line.count(";") > line.count(",") else ","
    return ","


def _is_blank(record):
    return not any(cell.strip() for cell in record)


def _header_columns(record):
    """Map each known column (name, location...) to its position in a header row."""
    columns = {}
    for position, title in enumerate(record):
        key = ALIAS_LOOKUP.get(_fold(title.replace("*", "")))
        if key is not None:
            columns.setdefault(key, position)
    return columns


def _find_header(records):
    """Return (index, columns) of the header: the first row, among the first non-blank ones,
    that names every required column. A title above the table (Excel export, template) is fine."""
    first = None
    seen = 0
    for index, record in enumerate(records):
        if _is_blank(record):
            continue
        columns = _header_columns(record)
        if first is None:
            first = (index, columns)
        if all(column in columns for column in CSV_REQUIRED_COLUMNS):
            return index, columns
        seen += 1
        if seen >= HEADER_SEARCH_ROWS:
            break
    return first if first is not None else (None, {})


def _unescape_formula_guard(text):
    """Undo the leading quote that the export adds to cells starting with =, +, -, @."""
    if len(text) > 1 and text[0] == "'" and text[1] in CSV_FORMULA_PREFIXES:
        return text[1:]
    return text


def _parse_import_row(record, columns, today):
    """Return (field values, None) for a valid row or (None, reason) for a row to skip."""

    def cell(column):
        position = columns.get(column)
        if position is None or position >= len(record):
            return ""
        return _unescape_formula_guard(record[position]).strip()

    name = cell("name")
    if not name:
        return None, "nombre requerido"
    if len(name) > ITEM_NAME_MAX:
        return None, "nombre demasiado largo"

    location = cell("location")
    if not location:
        return None, "lugar requerido"
    if len(location) > ITEM_LOCATION_MAX:
        return None, "lugar demasiado largo"

    event_date = _parse_event_date(cell("event_date"))
    if event_date is None:
        return None, "fecha inválida"
    if event_date > today:
        return None, "fecha futura"

    description = cell("description")
    if not description:
        return None, "descripción requerida"
    if len(description) > ITEM_DESCRIPTION_MAX:
        return None, "descripción demasiado larga"

    raw_category = cell("category")
    if not raw_category:
        return None, "categoría requerida"
    category = CATEGORY_LOOKUP.get(_fold(raw_category))
    if category is None:
        return None, "categoría desconocida"

    raw_status = cell("status")
    if not raw_status:
        return None, "estado requerido"
    status = STATUS_LOOKUP.get(_fold(raw_status))
    if status is None:
        return None, "estado desconocido"

    contact = cell("contact")
    if not contact:
        return None, "contacto requerido"
    if len(contact) > ITEM_CONTACT_MAX:
        return None, "contacto demasiado largo"

    return {
        "name": name,
        "description": description,
        "category": category,
        "location": location,
        "event_date": event_date,
        "status": status,
        "contact": contact,
    }, None


def _skipped_message(skipped):
    shown = ", ".join(skipped[:MAX_REPORTED_SKIPS])
    remaining = len(skipped) - MAX_REPORTED_SKIPS
    verb, noun = ("Se omitió", "fila") if len(skipped) == 1 else ("Se omitieron", "filas")
    tail = f" y {formato_numero(remaining)} más." if remaining > 0 else "."
    return f"{verb} {formato_numero(len(skipped))} {noun}: {shown}{tail}"


def _import_error(message):
    flash(message, "danger")
    return redirect(url_for("main.import_csv"))


@bp.route("/import/csv", methods=["GET", "POST"])
@admin_required
def import_csv():
    if request.method == "GET":
        return render_template(
            "import_csv.html",
            categories=CATEGORY_LABELS,
            statuses=STATUS_LABELS,
            max_rows=MAX_IMPORT_ROWS,
        )

    upload = request.files.get("file")
    if not _has_upload(upload):
        return _import_error("Seleccione un archivo Excel (.xlsx) o CSV.")

    raw = upload.stream.read()
    if raw.startswith(XLSX_SIGNATURE) or upload.filename.lower().endswith(".xlsx"):
        records = read_xlsx_records(raw)
        if records is None:
            return _import_error("No se pudo leer el archivo. Verifique que sea un Excel (.xlsx) válido.")
    else:
        text = _decode_csv(raw)
        if text is None:
            return _import_error("No se pudo leer el archivo. Verifique que sea un CSV válido.")
        try:
            records = list(csv.reader(StringIO(text, newline=""), delimiter=_detect_delimiter(text)))
        except csv.Error:
            return _import_error("No se pudo leer el archivo. Verifique que sea un CSV válido.")

    header_index, columns = _find_header(records)
    if any(column not in columns for column in CSV_REQUIRED_COLUMNS):
        return _import_error(
            "El archivo debe incluir todas las columnas: «Objeto», «Lugar», «Fecha del suceso», "
            "«Descripción», «Categoría», «Estado» y «Contacto». Use la plantilla para no equivocarse."
        )

    # Row numbers follow the spreadsheet: the header is row 1, blank lines still count.
    data_rows = [
        (index + 1, record)
        for index, record in enumerate(records)
        if index > header_index and not _is_blank(record)
    ]
    if len(data_rows) > MAX_IMPORT_ROWS:
        return _import_error(f"El archivo supera el máximo de {MAX_IMPORT_ROWS} filas.")
    if not data_rows:
        flash("El archivo no contiene filas para importar.", "warning")
        return redirect(url_for("main.import_csv"))

    today = today_local()
    new_items = []
    skipped = []
    for row_number, record in data_rows:
        values, reason = _parse_import_row(record, columns, today)
        if reason:
            skipped.append(f"fila {row_number} ({reason})")
        else:
            new_items.append(Item(user_id=current_user.id, approval="approved", **values))

    if new_items:
        db.session.add_all(new_items)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("CSV import failed")
            return _import_error("No se pudo completar la importación. No se guardó ningún reporte.")

    if not new_items:
        flash(f"No se importó ningún reporte. {_skipped_message(skipped)}", "warning")
        return redirect(url_for("main.import_csv"))

    if len(new_items) == 1:
        flash("Se importó 1 reporte.", "success")
    else:
        flash(f"Se importaron {formato_numero(len(new_items))} reportes.", "success")
    if skipped:
        flash(_skipped_message(skipped), "warning")
    return redirect(url_for("main.index"))
