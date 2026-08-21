"""CSV export for the users list."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Callable

from i18n import translate
from .user_explorer import UserListRow

PAGE_SIZE = 500
MAX_PAGES = 20
MAX_ROWS = PAGE_SIZE * MAX_PAGES  # 10,000

# Cells starting with these are executed as formulas by Excel / Google Sheets
# (CSV injection, CWE-1236). Free-text values that originate from end users -
# page titles, custom dimensions such as an organisation name - can reach this
# export, so every cell is neutralised by prefixing an apostrophe. The original
# text stays readable in the spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# (i18n key, attribute) - labels are resolved per request so the CSV header
# follows the UI language.
BASE_COLUMNS: list[tuple[str, str]] = [
    ("users.col.last_active", "last_active_timestamp"),
    ("users.col.first_touch", "first_touch_timestamp"),
    ("users.col.user_id", "user_id"),
    ("users.col.pseudo_id", "user_pseudo_id"),
    ("users.col.identified", "is_identified"),
    ("users.col.sessions", "sessions"),
    ("users.col.page_views", "page_views"),
    ("users.col.events", "events"),
    ("users.col.key_events", "key_events"),
    ("users.col.device", "device_category"),
    ("users.col.browser", "browser"),
    ("users.col.country", "country"),
    ("users.col.region", "region"),
    ("users.col.clarity", "has_clarity"),
]


def csv_safe(text: str) -> str:
    """Stop a cell from being interpreted as a spreadsheet formula."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def format_cell(value: Any, *, lang: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return translate("value.yes" if value else "value.no", lang)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return csv_safe(str(value))


def build_csv(
    rows: list[UserListRow],
    *,
    lang: str,
    custom_columns: list[dict[str, str]],
    truncated: bool,
    translator: Callable[..., str] | None = None,
) -> str:
    t = translator or (lambda key, **kw: translate(key, lang, **kw))

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")  # Excel-friendly

    header = [t(key) for key, _ in BASE_COLUMNS]
    header += [c["label"] for c in custom_columns]
    writer.writerow(header)

    for row in rows:
        line = [format_cell(getattr(row, attr, None), lang=lang) for _, attr in BASE_COLUMNS]
        line += [format_cell(row.custom.get(c["column"]), lang=lang) for c in custom_columns]
        writer.writerow(line)

    if truncated:
        # Never truncate silently: the operator must be able to tell that the
        # file is partial without counting rows.
        writer.writerow([])
        writer.writerow([t("csv.truncated_note", max=MAX_ROWS)])

    # UTF-8 BOM so Excel opens non-ASCII correctly. Harmless for English.
    return "﻿" + buf.getvalue()


def filename(now: datetime | None = None) -> str:
    # Language-independent on purpose: a stable name is easier to sort and to
    # reference in a report.
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"waca_path_users_{stamp}.csv"
