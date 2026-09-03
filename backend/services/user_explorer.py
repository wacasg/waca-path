"""Queries for the users list and the user detail page.

Reads only WACA core output (``micro_user_table``). Nothing here writes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from .config import validate_identifier
from .user_filters import (
    UserListFilters,
    build_cursor_condition,
    build_order_by,
    build_where_and_params,
)

logger = logging.getLogger("waca_path_backend.user_explorer")

CLARITY_HOST = "clarity.microsoft.com"


@dataclass
class UserListRow:
    user_pseudo_id: str
    user_id: str | None = None
    last_active_timestamp: datetime | None = None
    first_touch_timestamp: datetime | None = None
    sessions: int = 0
    page_views: int = 0
    events: int = 0
    key_events: int = 0
    device_category: str | None = None
    browser: str | None = None
    country: str | None = None
    region: str | None = None
    has_clarity: bool | None = None
    custom: dict[str, Any] = field(default_factory=dict)

    @property
    def is_identified(self) -> bool:
        return bool(self.user_id)

    @classmethod
    def from_row(cls, row: Any, custom_columns: list[dict[str, str]]) -> "UserListRow":
        # NOTE: ``key in bigquery.Row`` tests *values*, not keys. Always use
        # ``row.get(key)``; a previous version of this code used ``in`` and
        # silently returned None for every column.
        def opt(key: str) -> Any:
            return row.get(key)

        return cls(
            user_pseudo_id=row["user_pseudo_id"],
            user_id=opt("user_id"),
            last_active_timestamp=opt("last_active_timestamp"),
            first_touch_timestamp=opt("first_touch_timestamp"),
            sessions=int(opt("sessions") or 0),
            page_views=int(opt("page_views") or 0),
            events=int(opt("events") or 0),
            key_events=int(opt("key_events") or 0),
            device_category=opt("device_category"),
            browser=opt("browser"),
            country=opt("country"),
            region=opt("region"),
            has_clarity=opt("has_clarity"),
            custom={c["column"]: opt(c["column"]) for c in custom_columns},
        )


# --------------------------------------------------------------------------
# Clarity helpers
# --------------------------------------------------------------------------
def parse_clarity_ids(raw_url: str | None) -> dict[str, str | None] | None:
    """Pull the Clarity identifiers out of a ``clarity_play_url``.

    The canonical form is::

        https://clarity.microsoft.com/player/<project>/<clarity_user>/<clarity_session>

    Surfacing these IDs matters because **Clarity and GA4 do not agree on where a
    session ends**. GA4 starts a new session after 30 minutes of inactivity and at
    midnight; Clarity does not. One Clarity recording therefore routinely maps to
    several ``ga_session_id`` values, which makes a naively chosen "the" recording
    look like it belongs to the wrong row. Showing the IDs lets an analyst line the
    two systems up by hand inside Clarity.

    Returns ``None`` when the URL is absent or is not a Clarity player URL.
    """
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None
    if parsed.netloc != CLARITY_HOST:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) == 4 and parts[0] == "player":
        return {
            "project_id": parts[1],
            "clarity_user_id": parts[2],
            "clarity_session_id": parts[3],
        }
    if len(parts) == 3 and parts[0] == "player":
        # Opaque single-segment form: cannot be split into user/session.
        return {
            "project_id": parts[1],
            "clarity_user_id": None,
            "clarity_session_id": parts[2],
        }
    return None


def clarity_open_url(raw_url: str | None) -> str | None:
    """Where the recording button should point.

    The four-segment player URL is a valid deep link and is used as-is. The
    three-segment form cannot identify a single recording, so it falls back to
    the project's recordings list instead of 404-ing the user.
    """
    if not raw_url:
        return None
    ids = parse_clarity_ids(raw_url)
    if ids and ids["clarity_user_id"] is None and ids["project_id"]:
        return f"https://{CLARITY_HOST}/projects/view/{ids['project_id']}/recordings"
    return raw_url


def build_recordings(
    urls: list[str] | None, shared_counts: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """Turn a session's recording URLs into render-ready entries.

    Every distinct recording is kept. Collapsing them to one - which an earlier
    version did - is what made recordings appear to be attached to the wrong
    session.
    """
    out: list[dict[str, Any]] = []
    for url in urls or []:
        if not url:
            continue
        out.append(
            {
                "url": url,
                "open_url": clarity_open_url(url),
                "ids": parse_clarity_ids(url),
                "shared_sessions": (shared_counts or {}).get(url, 1),
            }
        )
    return out


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------
_columns_cache: dict[tuple[str, str], set[str]] = {}

# Columns the UI can use but that an install is allowed not to have. Referencing
# a missing column fails the whole query, so each one is probed first and simply
# omitted when absent (the UI then hides the corresponding cell).
OPTIONAL_COLUMNS = ("browser", "region", "country", "device_category", "clarity_play_url")


def available_columns(project: str, dataset: str) -> set[str]:
    """Column names present in ``micro_user_table``, cached per dataset.

    WACA core installs differ: older outputs have no ``clarity_play_url``, and
    the public sample dataset ships a reduced column set. Probing once keeps the
    UI working against any of them instead of returning a 400 for everyone.
    """
    key = (project, dataset)
    if key in _columns_cache:
        return _columns_cache[key]

    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    sql = f"""
    SELECT column_name
    FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = 'micro_user_table'
    """
    try:
        client = _client(project)
        cols = {r["column_name"] for r in client.query(sql).result()}
    except Exception:
        logger.warning(
            "could not read INFORMATION_SCHEMA for %s.%s; assuming all optional "
            "columns are present",
            project,
            dataset,
        )
        cols = set()
    _columns_cache[key] = cols
    return cols


def has_column(cols: set[str], name: str) -> bool:
    # An empty set means the probe itself failed; stay optimistic rather than
    # hiding every optional feature.
    return not cols or name in cols


def _client(project: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project)


def _scalar_params(params: dict[str, Any]):
    from google.cloud import bigquery

    out = []
    for key, value in params.items():
        if isinstance(value, bool):
            out.append(bigquery.ScalarQueryParameter(key, "BOOL", value))
        elif isinstance(value, int):
            out.append(bigquery.ScalarQueryParameter(key, "INT64", value))
        elif isinstance(value, date) and not isinstance(value, datetime):
            out.append(bigquery.ScalarQueryParameter(key, "DATE", value))
        else:
            out.append(bigquery.ScalarQueryParameter(key, "STRING", str(value)))
    return out


def _custom_select(custom_columns: list[dict[str, str]]) -> str:
    if not custom_columns:
        return ""
    lines = []
    for c in custom_columns:
        col = validate_identifier(c["column"], "custom column")
        lines.append(f"    ANY_VALUE({col}) AS {col},")
    return "\n".join(lines) + "\n"


def _optional_agg(cols: set[str], name: str, expr: str, alias: str) -> str:
    """Aggregate ``name`` when the install has it, otherwise select NULL."""
    if has_column(cols, name):
        return f"    {expr} AS {alias},"
    return f"    CAST(NULL AS STRING) AS {alias},"


def build_users_sql(
    project: str, dataset: str, f: UserListFilters, cols: set[str] | None = None
) -> tuple[str, dict[str, Any]]:
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    micro = f"`{project}.{dataset}.micro_user_table`"
    cols = cols if cols is not None else set()

    conds, params = build_where_and_params(f)
    params["date_from"] = f.date_from
    params["date_to"] = f.date_to
    params["page_size"] = f.page_size

    cursor_cond, cursor_params = build_cursor_condition(f)
    if cursor_cond:
        conds = [*conds, cursor_cond]
        params.update(cursor_params)

    having = ("WHERE " + " AND ".join(conds)) if conds else ""

    optional = "\n".join(
        [
            _optional_agg(cols, "device_category", "ANY_VALUE(device_category)", "device_category"),
            _optional_agg(cols, "browser", "ANY_VALUE(browser)", "browser"),
            _optional_agg(cols, "country", "ANY_VALUE(country)", "country"),
            _optional_agg(cols, "region", "ANY_VALUE(region)", "region"),
        ]
    )

    # Per-user flag: TRUE when ANY event carried a Clarity URL. Deliberately
    # user-level, and the UI says so - a matching user can still have sessions
    # with no recording at all.
    clarity = (
        "    LOGICAL_OR(clarity_play_url IS NOT NULL) AS has_clarity"
        if has_column(cols, "clarity_play_url")
        else "    CAST(NULL AS BOOL) AS has_clarity"
    )

    sql = f"""
WITH per_user AS (
  SELECT
    user_pseudo_id,
    ANY_VALUE(user_id)               AS user_id,
    MAX(event_timestamp)             AS last_active_timestamp,
    MIN(event_timestamp)             AS first_touch_timestamp,
    COUNT(DISTINCT ga_session_id)    AS sessions,
    COUNTIF(event_name = 'page_view') AS page_views,
    COUNT(*)                         AS events,
    -- is_key_event is BOOL in WACA core output, but some installs materialise it
    -- as STRING/INT64. SAFE_CAST keeps the query working either way.
    COUNTIF(SAFE_CAST(is_key_event AS BOOL))  AS key_events,
{optional}
{_custom_select(f.custom_columns)}{clarity}
  FROM {micro}
  WHERE event_date BETWEEN @date_from AND @date_to
  GROUP BY user_pseudo_id
)
SELECT * FROM per_user
{having}
{build_order_by(f)}
LIMIT @page_size
"""
    return sql, params


def build_max_event_date_sql(project: str, dataset: str) -> str:
    """micro_user_table に入っている最新 event_date を取る SQL。

    [DATA-FRESHNESS] (2026-09-03): 上流バッチが壊れて出力テーブルが古い月ぶんだけに
    なると、UI は「該当なし」とだけ出して原因を一切伝えない。実際 2026-09-02 に
    上流の破壊的リビルドが先頭月だけ実行されて止まり、micro_user_table の
    max(event_date) が 5 か月前で固定されたまま、画面上は単なる空表として
    半日以上放置された。空表の理由が「絞り込み過ぎ」なのか「データが無い」のかを
    利用者が区別できるようにする。
    """
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    return f"SELECT MAX(event_date) AS max_event_date FROM `{project}.{dataset}.micro_user_table`"


async def max_event_date(*, project: str, dataset: str) -> date | None:
    """テーブル内の最新 event_date。取得に失敗したら None (UI は注記を出さない)。"""

    def _run() -> date | None:
        client = _client(project)
        for row in client.query(build_max_event_date_sql(project, dataset)).result():
            return row["max_event_date"]
        return None

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        # 鮮度注記は補助情報にすぎない。ここでの失敗で一覧表示まで壊さない。
        logger.exception("max event_date lookup failed")
        return None


async def list_users(
    *, project: str, dataset: str, filters: UserListFilters
) -> list[UserListRow]:
    def _run() -> list[UserListRow]:
        from google.cloud import bigquery

        cols = available_columns(project, dataset)
        # Drop custom columns the install does not actually have, so one stale
        # tenant_config entry cannot break the whole page.
        usable = [c for c in filters.custom_columns if has_column(cols, c["column"])]
        effective = replace(filters, custom_columns=usable)

        sql, params = build_users_sql(project, dataset, effective, cols)
        client = _client(project)
        job_config = bigquery.QueryJobConfig(query_parameters=_scalar_params(params))
        rows = client.query(sql, job_config=job_config).result()
        return [UserListRow.from_row(r, usable) for r in rows]

    return await asyncio.to_thread(_run)


def build_detail_sql(
    project: str,
    dataset: str,
    custom_columns: list[dict[str, str]],
    cols: set[str] | None = None,
) -> tuple[str, str]:
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    micro = f"`{project}.{dataset}.micro_user_table`"
    cols = cols if cols is not None else set()

    optional = "\n".join(
        [
            _optional_agg(cols, "device_category", "ANY_VALUE(device_category)", "device_category"),
            _optional_agg(cols, "browser", "ANY_VALUE(browser)", "browser"),
            _optional_agg(cols, "country", "ANY_VALUE(country)", "country"),
            _optional_agg(cols, "region", "ANY_VALUE(region)", "region"),
        ]
    )

    # Keep EVERY distinct recording for the session. Collapsing to one made a
    # recording look like it belonged to a different session (see
    # parse_clarity_ids for why one recording spans several GA4 sessions).
    clarity = (
        "  ARRAY_AGG(DISTINCT clarity_play_url IGNORE NULLS) AS clarity_play_urls"
        if has_column(cols, "clarity_play_url")
        else "  CAST([] AS ARRAY<STRING>) AS clarity_play_urls"
    )

    sessions_sql = f"""
SELECT
  ga_session_id,
  MIN(event_timestamp) AS session_start,
  MAX(event_timestamp) AS session_end,
  COUNTIF(event_name = 'page_view') AS page_views,
  COUNT(*)             AS events,
  COUNTIF(SAFE_CAST(is_key_event AS BOOL)) AS key_events,
{optional}
{clarity}
FROM {micro}
WHERE user_pseudo_id = @pid AND event_date BETWEEN @date_from AND @date_to
GROUP BY ga_session_id
ORDER BY session_start DESC
LIMIT 200
"""

    profile_cols = "".join(
        f"  ANY_VALUE({validate_identifier(c['column'], 'custom column')}) AS {c['column']},\n"
        for c in custom_columns
    )
    profile_sql = f"""
SELECT
  ANY_VALUE(user_id)   AS user_id,
  MIN(event_timestamp) AS first_touch_timestamp,
  MAX(event_timestamp) AS last_active_timestamp,
  COUNT(DISTINCT ga_session_id) AS sessions,
  COUNTIF(event_name = 'page_view') AS page_views,
  COUNT(*)             AS events,
  COUNTIF(SAFE_CAST(is_key_event AS BOOL)) AS key_events,
{profile_cols}{_optional_agg(cols, "device_category", "ANY_VALUE(device_category)", "device_category").rstrip(",")}
FROM {micro}
WHERE user_pseudo_id = @pid AND event_date BETWEEN @date_from AND @date_to
"""
    return profile_sql, sessions_sql


async def get_user_detail(
    *,
    project: str,
    dataset: str,
    user_pseudo_id: str,
    date_from: date,
    date_to: date,
    custom_columns: list[dict[str, str]],
) -> dict[str, Any]:
    params = {"pid": user_pseudo_id, "date_from": date_from, "date_to": date_to}

    def _run() -> dict[str, Any]:
        from google.cloud import bigquery

        cols = available_columns(project, dataset)
        usable = [c for c in custom_columns if has_column(cols, c["column"])]
        profile_sql, sessions_sql = build_detail_sql(project, dataset, usable, cols)
        client = _client(project)
        job_config = bigquery.QueryJobConfig(query_parameters=_scalar_params(params))
        profile_rows = list(client.query(profile_sql, job_config=job_config).result())
        session_rows = [
            dict(r) for r in client.query(sessions_sql, job_config=job_config).result()
        ]
        return {
            "profile": dict(profile_rows[0]) if profile_rows else {},
            "sessions": session_rows,
        }

    data = await asyncio.to_thread(_run)

    # How many sessions in the displayed window share each recording. The UI
    # states that this count is scoped to the displayed period, because widening
    # the period can legitimately increase it.
    counts: dict[str, int] = {}
    for sess in data["sessions"]:
        for url in sess.get("clarity_play_urls") or []:
            if url:
                counts[url] = counts.get(url, 0) + 1

    for sess in data["sessions"]:
        sess["clarity_recordings"] = build_recordings(
            sess.get("clarity_play_urls"), counts
        )

    data["user_pseudo_id"] = user_pseudo_id
    return data
