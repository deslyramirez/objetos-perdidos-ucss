"""Excel files: the formatted export, the import template and reading uploaded .xlsx files.

The export and the template share the same column titles, so an exported file can be filled in
or corrected and imported again.
"""

from collections import Counter
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from domain import APPROVAL_LABELS, CATEGORY_LABELS, STATUS_LABELS

XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SYSTEM_NAME = "Sistema de Objetos Perdidos · UCSS"

# Palette taken from the app (teal header, soft zebra rows).
TEAL = "0D7180"
TEAL_DARK = "0A4F5C"
TEAL_SOFT = "E6F4F6"
ZEBRA = "F6FAFB"
GRID = "C9DDE2"
MUTED = "5B6B75"

STATUS_COLORS = {  # (fill, font) per stored status
    "lost": ("FFF1D6", "8A5300"),
    "found": ("E1EEFB", "1D4F86"),
    "returned": ("DFF3E7", "1E6B3D"),
}
APPROVAL_COLORS = {
    "approved": ("DFF3E7", "1E6B3D"),
    "inactive": ("ECEFF2", "4A5866"),
}

THIN = Side(style="thin", color=GRID)
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=TEAL)
TITLE_FONT = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
TITLE_FILL = PatternFill("solid", fgColor=TEAL_DARK)
SUBTITLE_FONT = Font(name="Calibri", size=10, italic=True, color=MUTED)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
DATE_FORMAT = "DD/MM/YYYY"
DATETIME_FORMAT = "DD/MM/YYYY HH:MM"

# (title, width). The first seven are the import columns; the template uses only those.
IMPORT_COLUMNS = [
    ("Objeto", 28),
    ("Lugar", 24),
    ("Fecha del suceso", 16),
    ("Descripción", 42),
    ("Categoría", 20),
    ("Estado", 14),
    ("Contacto", 26),
]
EXPORT_COLUMNS = [("N°", 6), ("Código", 9)] + IMPORT_COLUMNS + [
    ("Publicación", 14),
    ("Reportado por", 18),
    ("Correo", 28),
    ("Registrado el", 18),
]
EXPORT_HEADER_ROW = 4
TEMPLATE_HEADER_ROW = 4
TEMPLATE_ROWS = 300


# -----------------------------
# SHARED HELPERS
# -----------------------------
def _text_cell(cell, value):
    """Write text that Excel must never evaluate (a name starting with '=' stays text)."""
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"


def _banner(ws, title, subtitle, last_column):
    """Title in row 1 and a subtitle in row 2, both merged across the table width."""
    end = get_column_letter(last_column)
    ws.merge_cells(f"A1:{end}1")
    ws.merge_cells(f"A2:{end}2")
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    ws["A1"].fill = TITLE_FILL
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 32
    ws["A2"] = subtitle
    ws["A2"].font = SUBTITLE_FONT
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1, wrap_text=True)
    ws.row_dimensions[2].height = 30


def _header(ws, row, columns):
    for position, (title, width) in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=position, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(position)].width = width
    ws.row_dimensions[row].height = 30


def _paint(cell, colors):
    fill, font = colors
    cell.fill = PatternFill("solid", fgColor=fill)
    cell.font = Font(name="Calibri", bold=True, color=font)
    cell.alignment = CENTER


def _print_setup(ws, header_row):
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.oddFooter.center.text = "Página &P de &N"


def _save(wb):
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# -----------------------------
# EXPORT
# -----------------------------
def build_export(items, subtitle, local_datetime):
    """Workbook with a formatted «Reportes» sheet and a «Resumen» sheet; returns bytes.

    `local_datetime` turns a stored UTC datetime into the naive local time shown in Excel.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Reportes"
    ws.sheet_view.showGridLines = False
    last = len(EXPORT_COLUMNS)

    count = f"{len(items)} reporte{'s' if len(items) != 1 else ''}"
    _banner(ws, "Reporte de objetos perdidos y encontrados", f"{SYSTEM_NAME} · {subtitle} · {count}", last)
    _header(ws, EXPORT_HEADER_ROW, EXPORT_COLUMNS)

    row = EXPORT_HEADER_ROW
    for number, item in enumerate(items, start=1):
        row += 1
        values = [
            number,
            item.id,
            item.name,
            item.location or "",
            item.event_date,
            item.description or "",
            CATEGORY_LABELS.get(item.category, item.category or "Sin categoría"),
            STATUS_LABELS.get(item.status, item.status),
            item.contact or "",
            APPROVAL_LABELS.get(item.approval, item.approval),
            item.user.username if item.user else "",
            (item.user.email or "") if item.user else "",
            local_datetime(item.created_at) if item.created_at else None,
        ]
        zebra = PatternFill("solid", fgColor=ZEBRA) if number % 2 == 0 else None
        for column, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=column)
            _text_cell(cell, value)
            cell.border = BORDER
            cell.alignment = LEFT
            if zebra is not None:
                cell.fill = zebra
        ws.row_dimensions[row].height = 20
        ws.cell(row=row, column=1).alignment = CENTER
        ws.cell(row=row, column=2).alignment = CENTER
        ws.cell(row=row, column=5).number_format = DATE_FORMAT
        ws.cell(row=row, column=5).alignment = CENTER
        ws.cell(row=row, column=13).number_format = DATETIME_FORMAT
        ws.cell(row=row, column=13).alignment = CENTER
        if item.status in STATUS_COLORS:
            _paint(ws.cell(row=row, column=8), STATUS_COLORS[item.status])
        if item.approval in APPROVAL_COLORS:
            _paint(ws.cell(row=row, column=10), APPROVAL_COLORS[item.approval])

    if items:
        ws.auto_filter.ref = f"A{EXPORT_HEADER_ROW}:{get_column_letter(last)}{row}"
    else:
        row += 1
        ws.merge_cells(f"A{row}:{get_column_letter(last)}{row}")
        ws.cell(row=row, column=1, value="No hay reportes para los filtros elegidos.").font = SUBTITLE_FONT

    ws.freeze_panes = f"D{EXPORT_HEADER_ROW + 1}"
    _print_setup(ws, EXPORT_HEADER_ROW)

    _summary_sheet(wb.create_sheet("Resumen"), items, subtitle)
    return _save(wb)


def _summary_table(ws, top, title, rows, total):
    """A two-column table (label, count, %) starting at `top`; returns the next free row."""
    ws.merge_cells(start_row=top, start_column=2, end_row=top, end_column=4)
    cell = ws.cell(row=top, column=2, value=title)
    cell.font = Font(bold=True, size=12, color=TEAL_DARK)
    header = top + 1
    for column, text in enumerate(("Detalle", "Cantidad", "%"), start=2):
        head = ws.cell(row=header, column=column, value=text)
        head.font = HEADER_FONT
        head.fill = HEADER_FILL
        head.alignment = CENTER
        head.border = BORDER
    row = header
    for label, amount, colors in rows:
        row += 1
        ws.cell(row=row, column=2, value=label)
        ws.cell(row=row, column=3, value=amount)
        ws.cell(row=row, column=4, value=(amount / total) if total else 0)
        ws.cell(row=row, column=4).number_format = "0.0%"
        for column in (2, 3, 4):
            ws.cell(row=row, column=column).border = BORDER
        ws.cell(row=row, column=3).alignment = CENTER
        ws.cell(row=row, column=4).alignment = CENTER
        if colors:
            _paint(ws.cell(row=row, column=2), colors)
            ws.cell(row=row, column=2).alignment = LEFT
    row += 1
    ws.cell(row=row, column=2, value="Total")
    ws.cell(row=row, column=3, value=total)
    ws.cell(row=row, column=4, value=1 if total else 0)
    ws.cell(row=row, column=4).number_format = "0.0%"
    for column in (2, 3, 4):
        cell = ws.cell(row=row, column=column)
        cell.font = Font(bold=True, color=TEAL_DARK)
        cell.fill = PatternFill("solid", fgColor=TEAL_SOFT)
        cell.border = BORDER
        if column > 2:
            cell.alignment = CENTER
    return row + 2


def _summary_sheet(ws, items, subtitle):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 10
    _banner(ws, "Resumen", subtitle, 4)

    total = len(items)
    statuses = Counter(item.status for item in items)
    categories = Counter(
        CATEGORY_LABELS.get(item.category, item.category or "Sin categoría") for item in items
    )
    approvals = Counter(item.approval for item in items)

    row = 4
    row = _summary_table(
        ws, row, "Por estado del objeto",
        [(label, statuses.get(key, 0), STATUS_COLORS.get(key)) for key, label in STATUS_LABELS.items()],
        total,
    )
    row = _summary_table(
        ws, row, "Por categoría",
        [(label, amount, None) for label, amount in sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))],
        total,
    )
    _summary_table(
        ws, row, "Por publicación",
        [
            (APPROVAL_LABELS[key], approvals.get(key, 0), APPROVAL_COLORS.get(key))
            for key in ("approved", "inactive")
        ],
        total,
    )
    ws.cell(row=3, column=2, value="«Desactivado»: oculto para la comunidad, pero conservado en el historial.").font = SUBTITLE_FONT


# -----------------------------
# IMPORT TEMPLATE
# -----------------------------
def build_import_template(today):
    """Template with a ready-to-fill «Reportes» sheet (dropdowns and date checks) and instructions."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Reportes"
    ws.sheet_view.showGridLines = False
    last = len(IMPORT_COLUMNS)

    _banner(
        ws,
        "Plantilla para importar reportes",
        "Escriba un objeto por fila desde la fila 5. Las columnas con fondo oscuro marcado con * son "
        "obligatorias. No cambie los títulos de la fila 4. Vea la hoja «Instrucciones».",
        last,
    )
    _header(ws, TEMPLATE_HEADER_ROW, IMPORT_COLUMNS)
    for position in (1, 2, 3):  # required columns
        cell = ws.cell(row=TEMPLATE_HEADER_ROW, column=position)
        cell.value = f"{cell.value} *"
        cell.fill = TITLE_FILL

    first = TEMPLATE_HEADER_ROW + 1
    last_row = TEMPLATE_HEADER_ROW + TEMPLATE_ROWS
    for row in range(first, last_row + 1):
        for column in range(1, last + 1):
            cell = ws.cell(row=row, column=column)
            cell.border = BORDER
            cell.alignment = LEFT
            if row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=ZEBRA)
        ws.cell(row=row, column=3).number_format = DATE_FORMAT
        ws.cell(row=row, column=3).alignment = CENTER

    def dropdown(values, column, title, prompt):
        validation = DataValidation(
            type="list", formula1='"' + ",".join(values) + '"', allow_blank=True,
            showErrorMessage=True, errorTitle=title, error=f"Elija un valor de la lista: {', '.join(values)}.",
            showInputMessage=True, promptTitle=title, prompt=prompt,
        )
        ws.add_data_validation(validation)
        letter = get_column_letter(column)
        validation.add(f"{letter}{first}:{letter}{last_row}")

    dropdown(list(CATEGORY_LABELS.values()), 5, "Categoría", "Elija la categoría (opcional).")
    dropdown(list(STATUS_LABELS.values()), 6, "Estado", "Vacío = Perdido.")

    dates = DataValidation(
        type="date", operator="between", formula1="DATE(2000,1,1)",
        formula2=f"DATE({today.year},{today.month},{today.day})",
        showErrorMessage=True, errorTitle="Fecha del suceso",
        error="Escriba una fecha válida (DD/MM/AAAA) que no sea posterior a hoy.",
        showInputMessage=True, promptTitle="Fecha del suceso", prompt="Formato DD/MM/AAAA. Obligatoria.",
    )
    ws.add_data_validation(dates)
    dates.add(f"C{first}:C{last_row}")

    for column, limit in ((1, 120), (2, 120), (4, 500), (7, 120)):
        letter = get_column_letter(column)
        length = DataValidation(
            type="textLength", operator="lessThanOrEqual", formula1=str(limit), allow_blank=True,
            showErrorMessage=True, errorTitle="Texto demasiado largo", error=f"Máximo {limit} caracteres.",
        )
        ws.add_data_validation(length)
        length.add(f"{letter}{first}:{letter}{last_row}")

    ws.freeze_panes = f"A{first}"
    ws.print_area = f"A1:{get_column_letter(last)}{TEMPLATE_HEADER_ROW + 40}"
    _print_setup(ws, TEMPLATE_HEADER_ROW)
    _instructions_sheet(wb.create_sheet("Instrucciones"))
    wb.active = 0
    return _save(wb)


def _instructions_sheet(ws):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    widths = {"B": 22, "C": 14, "D": 70}
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width
    _banner(ws, "Cómo llenar la plantilla", "Guarde el archivo como .xlsx y súbalo en «Importar».", 4)

    rows = [
        ("Objeto", "Sí", "Nombre del objeto. Hasta 120 caracteres."),
        ("Lugar", "Sí", "Dónde se perdió o se encontró. Hasta 120 caracteres."),
        ("Fecha del suceso", "Sí", "DD/MM/AAAA (15/03/2026). No puede ser posterior a hoy."),
        ("Descripción", "No", "Detalles que ayuden a reconocerlo. Hasta 500 caracteres."),
        ("Categoría", "No", "Elija de la lista: " + ", ".join(CATEGORY_LABELS.values()) + ". Vacía = sin categoría."),
        ("Estado", "No", "Elija de la lista: " + ", ".join(STATUS_LABELS.values()) + ". Vacío = Perdido."),
        ("Contacto", "No", "Correo o teléfono. Hasta 120 caracteres."),
    ]
    header = 4
    for column, text in enumerate(("Columna", "Obligatoria", "Qué escribir"), start=2):
        cell = ws.cell(row=header, column=column, value=text)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    for offset, values in enumerate(rows, start=1):
        for column, value in enumerate(values, start=2):
            cell = ws.cell(row=header + offset, column=column, value=value)
            cell.border = BORDER
            cell.alignment = LEFT if column != 3 else CENTER
            if offset % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=ZEBRA)
        if values[1] == "Sí":
            ws.cell(row=header + offset, column=3).font = Font(bold=True, color=TEAL_DARK)

    notes_row = header + len(rows) + 2
    ws.cell(row=notes_row, column=2, value="Tenga en cuenta").font = Font(bold=True, size=12, color=TEAL_DARK)
    notes = [
        "Las filas vacías se ignoran; las filas con errores se omiten y se indican al terminar.",
        "Los reportes importados quedan publicados a nombre del administrador.",
        "Las fotografías no se importan: puede agregarlas después con «Editar».",
        "También puede importar un Excel exportado por el sistema o un CSV con los mismos títulos.",
    ]
    for offset, note in enumerate(notes, start=1):
        cell = ws.cell(row=notes_row + offset, column=2, value=f"• {note}")
        ws.merge_cells(start_row=notes_row + offset, start_column=2, end_row=notes_row + offset, end_column=4)
        cell.alignment = LEFT


# -----------------------------
# READ AN UPLOADED .XLSX
# -----------------------------
def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_xlsx_records(raw):
    """Rows of the report sheet as lists of strings (row 1 first), or None if unreadable."""
    try:
        wb = load_workbook(BytesIO(raw), data_only=True)
    except Exception:  # zipfile, XML and openpyxl errors all mean "not a valid workbook"
        return None
    try:
        ws = wb["Reportes"] if "Reportes" in wb.sheetnames else wb.worksheets[0]
        return [[_as_text(value) for value in row] for row in ws.iter_rows(min_row=1, values_only=True)]
    except Exception:
        return None
