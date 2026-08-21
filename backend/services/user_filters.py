"""Filter / sort / cursor handling for the users list.

Design notes for anyone porting this further:

* Every column that can reach SQL is whitelisted here. Values are always passed
  as BigQuery query parameters; only *column names* are interpolated, and only
  after they have been matched against a whitelist. Unknown filter keys are
  dropped silently so that a hand-edited URL cannot inject anything.
* Paging uses a keyset cursor, not OFFSET. The cursor carries the sort value
  plus ``user_pseudo_id`` as a tie-breaker, so rows are never duplicated or
  skipped when many users share the same sort value.
* Unlike the private WACA build, this file ships **no** organisation-specific
  columns. Tenants declare their own GA4 custom dimensions under
  ``ui.custom_columns`` in their tenant config.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, Mapping

SortDir = Literal["asc", "desc"]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

# sort key -> SQL expression (already aggregated in the outer query)
SORTABLE_COLS: dict[str, str] = {
    "last_active": "last_active_timestamp",
    "first_touch": "first_touch_timestamp",
    "user_id": "user_id",
    "pseudo_id": "user_pseudo_id",
    "sessions": "sessions",
    "page_views": "page_views",
    "events": "events",
    "key_events": "key_events",
    "device_category": "device_category",
    "browser": "browser",
    "country": "country",
    "region": "region",
}

# free-text partial match (LOWER(col) LIKE %value%)
TEXT_SEARCH_COLS: dict[str, str] = {
    "user_id": "user_id",
    "pseudo_id": "user_pseudo_id",
}

# exact match
TEXT_EQUALS_COLS: dict[str, str] = {
    "device_category": "device_category",
    "browser": "browser",
    "country": "country",
    "region": "region",
}

NUMERIC_COLS: dict[str, str] = {
    "sessions": "sessions",
    "page_views": "page_views",
    "events": "events",
    "key_events": "key_events",
}

VALID_PRESETS: set[str] = {
    "cv_positive",
    "identified",
    "recent7d",
    "clarity",
    "multi_session",
}

# The sort key used for a row is read back off the dataclass through this map.
_SORT_ATTR: dict[str, str] = {
    "last_active": "last_active_timestamp",
    "first_touch": "first_touch_timestamp",
    "user_id": "user_id",
    "pseudo_id": "user_pseudo_id",
    "sessions": "sessions",
    "page_views": "page_views",
    "events": "events",
    "key_events": "key_events",
    "device_category": "device_category",
    "browser": "browser",
    "country": "country",
    "region": "region",
}


@dataclass
class CursorState:
    sort_col: str
    sort_dir: SortDir
    sort_val: str | None
    pseudo_id: str


@dataclass
class UserListFilters:
    date_from: date
    date_to: date
    sort_col: str = "last_active"
    sort_dir: SortDir = "desc"
    text_contains: dict[str, str] = field(default_factory=dict)
    text_equals: dict[str, str] = field(default_factory=dict)
    numeric_min: dict[str, int] = field(default_factory=dict)
    numeric_max: dict[str, int] = field(default_factory=dict)
    presets: list[str] = field(default_factory=list)
    q_global: str | None = None
    cursor: CursorState | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    custom_columns: list[dict[str, str]] = field(default_factory=list)


def encode_cursor(c: CursorState) -> str:
    raw = json.dumps(
        {
            "sort_col": c.sort_col,
            "sort_dir": c.sort_dir,
            "sort_val": c.sort_val,
            "pseudo_id": c.pseudo_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(token: str) -> CursorState | None:
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    sort_col = data.get("sort_col")
    pseudo_id = data.get("pseudo_id")
    if sort_col not in SORTABLE_COLS or not isinstance(pseudo_id, str) or not pseudo_id:
        return None
    sort_val = data.get("sort_val")
    return CursorState(
        sort_col=sort_col,
        sort_dir="asc" if data.get("sort_dir") == "asc" else "desc",
        sort_val=None if sort_val is None else str(sort_val),
        pseudo_id=pseudo_id,
    )


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return fallback


def parse_filters_from_query(
    params: Mapping[str, str],
    *,
    today: date | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    custom_columns: list[dict[str, str]] | None = None,
) -> UserListFilters:
    """Build filters from a query mapping. Unknown keys are dropped silently."""
    today = today or date.today()
    date_to = _parse_date(params.get("to"), today)
    date_from = _parse_date(params.get("from"), date_to - timedelta(days=30))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    sort_col = params.get("sort") or "last_active"
    if sort_col not in SORTABLE_COLS:
        sort_col = "last_active"
    sort_dir: SortDir = "asc" if (params.get("dir") or "desc").lower() == "asc" else "desc"

    text_contains: dict[str, str] = {}
    text_equals: dict[str, str] = {}
    numeric_min: dict[str, int] = {}
    numeric_max: dict[str, int] = {}

    custom = custom_columns or []
    custom_keys = {c["column"] for c in custom}

    for key, raw in params.items():
        value = (raw or "").strip()
        if not value:
            continue
        if key.startswith("fc_"):
            col = key[3:]
            if col in TEXT_SEARCH_COLS or col in custom_keys:
                text_contains[col] = value[:100]
        elif key.startswith("fe_"):
            col = key[3:]
            if col in TEXT_EQUALS_COLS or col in custom_keys:
                text_equals[col] = value[:100]
        elif key.startswith("fmin_"):
            col = key[5:]
            if col in NUMERIC_COLS:
                try:
                    numeric_min[col] = int(value)
                except ValueError:
                    pass
        elif key.startswith("fmax_"):
            col = key[5:]
            if col in NUMERIC_COLS:
                try:
                    numeric_max[col] = int(value)
                except ValueError:
                    pass

    presets = [p for p in (params.get("preset") or "").split(",") if p in VALID_PRESETS]

    q_global = (params.get("q_global") or "").strip()[:100] or None

    return UserListFilters(
        date_from=date_from,
        date_to=date_to,
        sort_col=sort_col,
        sort_dir=sort_dir,
        text_contains=text_contains,
        text_equals=text_equals,
        numeric_min=numeric_min,
        numeric_max=numeric_max,
        presets=presets,
        q_global=q_global,
        cursor=decode_cursor(params.get("cursor") or ""),
        page_size=max(1, min(page_size, MAX_PAGE_SIZE)),
        custom_columns=custom,
    )


def _column_sql(col: str, f: UserListFilters) -> str | None:
    """Resolve a filter key to a SQL expression, or None if it is not allowed."""
    if col in TEXT_SEARCH_COLS:
        return TEXT_SEARCH_COLS[col]
    if col in TEXT_EQUALS_COLS:
        return TEXT_EQUALS_COLS[col]
    for c in f.custom_columns:
        if c["column"] == col:
            # already validated against _IDENT_RE when the tenant config was read
            return c["column"]
    return None


def build_where_and_params(f: UserListFilters) -> tuple[list[str], dict[str, object]]:
    """Return (conditions, named query parameters) for the outer HAVING clause."""
    conds: list[str] = []
    params: dict[str, object] = {}

    for i, (col, value) in enumerate(f.text_contains.items()):
        sql_col = _column_sql(col, f)
        if not sql_col:
            continue
        key = f"fc{i}"
        conds.append(f"LOWER({sql_col}) LIKE CONCAT('%', LOWER(@{key}), '%')")
        params[key] = value

    for i, (col, value) in enumerate(f.text_equals.items()):
        sql_col = _column_sql(col, f)
        if not sql_col:
            continue
        key = f"fe{i}"
        conds.append(f"{sql_col} = @{key}")
        params[key] = value

    for i, (col, value) in enumerate(f.numeric_min.items()):
        if col not in NUMERIC_COLS:
            continue
        key = f"fmin{i}"
        conds.append(f"{NUMERIC_COLS[col]} >= @{key}")
        params[key] = value

    for i, (col, value) in enumerate(f.numeric_max.items()):
        if col not in NUMERIC_COLS:
            continue
        key = f"fmax{i}"
        conds.append(f"{NUMERIC_COLS[col]} <= @{key}")
        params[key] = value

    if "cv_positive" in f.presets:
        conds.append("key_events > 0")
    if "identified" in f.presets:
        conds.append("user_id IS NOT NULL")
    if "clarity" in f.presets:
        conds.append("has_clarity = TRUE")
    if "multi_session" in f.presets:
        conds.append("sessions >= 2")
    if "recent7d" in f.presets:
        conds.append("DATE(last_active_timestamp) >= DATE_SUB(@date_to, INTERVAL 7 DAY)")

    if f.q_global:
        conds.append(
            "(LOWER(COALESCE(user_id, '')) LIKE CONCAT('%', LOWER(@q_global), '%')"
            " OR LOWER(user_pseudo_id) LIKE CONCAT('%', LOWER(@q_global), '%'))"
        )
        params["q_global"] = f.q_global

    return conds, params


def build_cursor_condition(f: UserListFilters) -> tuple[str | None, dict[str, object]]:
    """Keyset pagination predicate.

    ``user_pseudo_id`` breaks ties so that users sharing a sort value (very
    common for small integers such as ``sessions``) are neither repeated nor
    skipped across page boundaries.
    """
    c = f.cursor
    if not c:
        return None, {}
    expr = SORTABLE_COLS.get(c.sort_col)
    if not expr:
        return None, {}
    cmp_op = "<" if f.sort_dir == "desc" else ">"
    params: dict[str, object] = {"cursor_pseudo_id": c.pseudo_id}
    if c.sort_val is None:
        # NULLs sort last in DESC / first in ASC; once past them only the
        # tie-breaker matters.
        return f"({expr} IS NULL AND user_pseudo_id {cmp_op} @cursor_pseudo_id)", params
    params["cursor_val"] = c.sort_val
    null_tail = f" OR {expr} IS NULL" if f.sort_dir == "desc" else ""
    return (
        f"({expr} {cmp_op} CAST(@cursor_val AS STRING)"
        f" OR ({expr} = CAST(@cursor_val AS STRING)"
        f" AND user_pseudo_id {cmp_op} @cursor_pseudo_id){null_tail})",
        params,
    )


def build_order_by(f: UserListFilters) -> str:
    expr = SORTABLE_COLS.get(f.sort_col, "last_active_timestamp")
    direction = "DESC" if f.sort_dir == "desc" else "ASC"
    return f"ORDER BY {expr} {direction}, user_pseudo_id {direction}"


def make_next_cursor(rows: list, f: UserListFilters) -> str | None:
    if not rows:
        return None
    last = rows[-1]
    pseudo_id = getattr(last, "user_pseudo_id", None)
    if not pseudo_id:
        return None
    attr = _SORT_ATTR.get(f.sort_col, "last_active_timestamp")
    raw = getattr(last, attr, None)
    return encode_cursor(
        CursorState(
            sort_col=f.sort_col,
            sort_dir=f.sort_dir,
            sort_val=None if raw is None else str(raw),
            pseudo_id=pseudo_id,
        )
    )
