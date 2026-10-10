"""Lost-and-found domain constants and pure helpers (no Flask imports)."""

import re
from datetime import date

# Stored keys stay in English (data contract); labels are what the user sees.
STATUS_LABELS = {
    "lost": "Perdido",
    "found": "Encontrado",
    "returned": "Entregado",
}
STATUS_CHOICES = list(STATUS_LABELS.items())
DEFAULT_STATUS = "lost"

# Status -> suffix of the `.badge-*` CSS class.
STATUS_BADGE = {
    "lost": "warning",
    "found": "info",
    "returned": "success",
}

CATEGORY_LABELS = {
    "documents": "Documentos",
    "electronics": "Electrónica",
    "keys": "Llaves",
    "clothing": "Ropa y accesorios",
    "bags": "Mochilas y útiles",
    "other": "Otros",
}

# Categories used before the lost-and-found pivot: only electronics has an equivalent.
LEGACY_CATEGORY_MAP = {
    "electronics": "electronics",
    "general": "other",
    "office": "other",
    "consumable": "other",
    "other": "other",
}

_MONTH_PATTERN = re.compile(r"(\d{4})-(\d{2})", re.ASCII)


def normalize_legacy_category(value):
    """Map a stored category to a current key; empty values stay empty.

    Keys that already belong to CATEGORY_LABELS are kept; anything else (legacy or free
    text coming from old CSV imports) becomes "other" unless LEGACY_CATEGORY_MAP says otherwise.
    """
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    if key in CATEGORY_LABELS:
        return key
    return LEGACY_CATEGORY_MAP.get(key, "other")


def month_bounds(year, month):
    """Return (first day of the month, first day of the next month) as dates."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be between 1 and 12, got {month}")
    first_day = date(year, month, 1)
    if month == 12:
        return first_day, date(year + 1, 1, 1)
    return first_day, date(year, month + 1, 1)


def parse_month(value, default):
    """Parse 'YYYY-MM' into (year, month); return `default` untouched when invalid.

    Surrounding whitespace is ignored. The year must be 0001-9998 so that
    `month_bounds` can always compute the following month.
    """
    if not isinstance(value, str):
        return default
    match = _MONTH_PATTERN.fullmatch(value.strip())
    if match is None:
        return default
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= year <= 9998 or not 1 <= month <= 12:
        return default
    return year, month


# Why someone contacts the person who published a report.
CONVERSATION_KINDS = {
    "owner": "Es mío",
    "finder": "Lo encontré",
    "question": "Consulta",
}
CONVERSATION_KIND_ICONS = {
    "owner": "bi-hand-index-thumb",
    "finder": "bi-search-heart",
    "question": "bi-chat-left-text",
}


def default_conversation_kind(status):
    """A lost object is answered with "Lo encontré", a found one with "Es mío"."""
    return {"lost": "finder", "found": "owner"}.get(status, "question")


# Moderation: new reports wait until the administrator approves them.
APPROVAL_LABELS = {
    "pending": "En revisión",
    "approved": "Publicado",
    "rejected": "Desaprobado",
}
