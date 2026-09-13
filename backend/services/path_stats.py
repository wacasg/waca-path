"""Cross-user path aggregation ("よく通る経路"): the routes visitors take most.

Companion to :mod:`services.journey` (one user's sessions). This module looks
at *every* session in a period and counts, per session:

* page transitions ``A -> B`` (consecutive page views within one session),
* entry pages, exit pages,
* whole session sequences (consecutive duplicates collapsed, capped in length).

Reads only WACA core output (``micro_user_table``). Nothing here writes and
nothing here calls an LLM. BigQuery does the row scan (partition-filtered on
``event_date``, ``page_view`` rows only, one row per session via ``ARRAY_AGG``);
Python does the pattern counting so the rules stay readable and unit-testable.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException

from .config import validate_identifier
from .journey import collapse_consecutive, page_path_of
from .user_explorer import _client, _scalar_params, available_columns, has_column, max_event_date

logger = logging.getLogger("waca_path_backend.path_stats")

DEFAULT_PERIOD_DAYS = 30
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
# Sessions fetched per request. One row per session comes back, so this bounds
# both the transfer and the Python work; the response says when it was hit.
DEFAULT_MAX_SESSIONS = 10000
MAX_SESSIONS_LIMIT = 50000
SEQUENCE_MAX_STEPS = 8


# --------------------------------------------------------------------------
# Pure counting helpers (unit-tested with in-memory rows)
# --------------------------------------------------------------------------
def session_sequences(rows: list[dict[str, Any]]) -> list[list[str]]:
    """One collapsed page-path sequence per session row.

    Each row carries ``locations`` - the session's ``page_location`` values in
    time order (as ``ARRAY_AGG`` returns them). Rows without any usable page
    are dropped rather than counted as an empty route.
    """
    out: list[list[str]] = []
    for row in rows:
        paths = [page_path_of(loc) for loc in (row.get("locations") or [])]
        seq = collapse_consecutive([p for p in paths if p])
        if seq:
            out.append(seq)
    return out


def count_transitions(sequences: list[list[str]]) -> Counter[tuple[str, str]]:
    """``A -> B`` hops, counted once per session (a session that bounces between
    two pages five times still counts as one session for that hop)."""
    counts: Counter[tuple[str, str]] = Counter()
    for seq in sequences:
        counts.update(set(zip(seq, seq[1:])))
    return counts


def count_entries(sequences: list[list[str]]) -> Counter[str]:
    return Counter(seq[0] for seq in sequences)


def count_exits(sequences: list[list[str]]) -> Counter[str]:
    return Counter(seq[-1] for seq in sequences)


def count_sequences(sequences: list[list[str]], max_steps: int = SEQUENCE_MAX_STEPS) -> Counter[tuple[str, ...]]:
    """Whole routes, cut to ``max_steps`` so that long, unique tails do not
    fragment the count. A cut route is keyed by its first ``max_steps`` pages."""
    return Counter(tuple(seq[:max_steps]) for seq in sequences)


def top_n(counter: Counter, limit: int, total: int) -> list[dict[str, Any]]:
    """Most common keys with their session count and share of ``total``.

    Ties are broken by key so that the output is stable between runs.
    """
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[: max(0, limit)]
    return [
        {"key": key, "sessions": n, "share": round(n / total, 4) if total else 0.0}
        for key, n in items
    ]


def aggregate(rows: list[dict[str, Any]], *, limit: int = DEFAULT_LIMIT, max_steps: int = SEQUENCE_MAX_STEPS) -> dict[str, Any]:
    """Pure assembly used by the API, the UI and the tests."""
    seqs = session_sequences(rows)
    total = len(seqs)
    transitions = [
        {"from": key[0], "to": key[1], "sessions": n, "share": share}
        for key, n, share in (
            (e["key"], e["sessions"], e["share"]) for e in top_n(count_transitions(seqs), limit, total)
        )
    ]
    sequences = [
        {
            "path": list(e["key"]),
            "steps": len(e["key"]),
            "cut": len(e["key"]) >= max_steps,
            "sessions": e["sessions"],
            "share": e["share"],
        }
        for e in top_n(count_sequences(seqs, max_steps), limit, total)
    ]
    entries = [
        {"path": e["key"], "sessions": e["sessions"], "share": e["share"]}
        for e in top_n(count_entries(seqs), limit, total)
    ]
    exits = [
        {"path": e["key"], "sessions": e["sessions"], "share": e["share"]}
        for e in top_n(count_exits(seqs), limit, total)
    ]
    return {
        "sessions_analyzed": total,
        "sequence_max_steps": max_steps,
        "transitions": transitions,
        "entries": entries,
        "exits": exits,
        "sequences": sequences,
    }


def resolve_period(
    date_from: date | None,
    date_to: date | None,
    *,
    today: date | None = None,
    latest: date | None = None,
    days: int = DEFAULT_PERIOD_DAYS,
) -> dict[str, Any]:
    """Default period is the last ``days`` days, clamped to the data.

    If the caller gave no dates and the table's newest ``event_date`` lies
    before the default window (a stalled upstream batch), the window is moved
    back to end on that newest date - otherwise the page would show an empty
    result and nothing else. Data that is merely a day or two old is left
    alone. Explicit dates are respected as given (swapped if reversed).
    """
    today = today or date.today()
    clamped = False
    if date_to is None and date_from is None:
        end = today
        if latest is not None and latest < today - timedelta(days=days):
            end = latest
            clamped = True
        start = end - timedelta(days=days)
    else:
        end = date_to or today
        start = date_from or (end - timedelta(days=days))
    if start > end:
        start, end = end, start
    return {
        "date_from": start,
        "date_to": end,
        "clamped": clamped,
        "latest_event_date": latest,
    }


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------
def _session_key_sql(cols: set[str]) -> str:
    if has_column(cols, "pseudonymous_session_id"):
        return "COALESCE(pseudonymous_session_id, CONCAT(user_pseudo_id, ':', CAST(ga_session_id AS STRING)))"
    return "CONCAT(user_pseudo_id, ':', CAST(ga_session_id AS STRING))"


def build_sessions_sql(project: str, dataset: str, cols: set[str] | None = None) -> str:
    """One row per session: the session's page_view locations in time order.

    Most recent sessions first, so that the ``LIMIT`` drops the oldest ones
    when the period holds more sessions than ``@max_sessions``.
    """
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    cols = cols if cols is not None else set()
    order = "event_timestamp"
    if has_column(cols, "session_event_no"):
        order = "event_timestamp, session_event_no"
    return f"""
SELECT
  {_session_key_sql(cols)} AS session_key,
  ANY_VALUE(user_pseudo_id) AS user_pseudo_id,
  MIN(event_timestamp) AS session_start,
  ARRAY_AGG(page_location ORDER BY {order}) AS locations
FROM `{project}.{dataset}.micro_user_table`
WHERE event_date BETWEEN @date_from AND @date_to
  AND event_name = 'page_view'
  AND page_location IS NOT NULL
GROUP BY session_key
ORDER BY session_start DESC
LIMIT @max_sessions
"""


def build_totals_sql(project: str, dataset: str, cols: set[str] | None = None) -> str:
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    cols = cols if cols is not None else set()
    return f"""
SELECT
  COUNT(DISTINCT {_session_key_sql(cols)}) AS sessions,
  COUNT(DISTINCT user_pseudo_id)           AS users,
  COUNTIF(event_name = 'page_view')        AS page_views
FROM `{project}.{dataset}.micro_user_table`
WHERE event_date BETWEEN @date_from AND @date_to
"""


def _query(project: str, dataset: str, period: dict[str, Any], max_sessions: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from google.api_core.exceptions import BadRequest, Forbidden, GoogleAPICallError, NotFound
    from google.cloud import bigquery

    cols = available_columns(project, dataset)
    params = {"date_from": period["date_from"], "date_to": period["date_to"], "max_sessions": max_sessions + 1}
    client = _client(project)
    job_config = bigquery.QueryJobConfig(query_parameters=_scalar_params(params))
    try:
        rows = [dict(r) for r in client.query(build_sessions_sql(project, dataset, cols), job_config=job_config).result()]
        totals_rows = list(client.query(build_totals_sql(project, dataset, cols), job_config=job_config).result())
    except NotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"BigQuery table not found: {project}.{dataset}.micro_user_table. "
                "Confirm WACA core has created it and that GOOGLE_CLOUD_PROJECT / "
                "WACA_CORE_DATASET point at the right project and dataset."
            ),
        ) from exc
    except Forbidden as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "Permission denied for BigQuery. The runtime identity needs "
                "roles/bigquery.dataViewer on the dataset and "
                "roles/bigquery.jobUser on the project."
            ),
        ) from exc
    except BadRequest as exc:
        logger.warning("BigQuery rejected the path-stats query: %s", getattr(exc, "message", str(exc)))
        raise HTTPException(
            status_code=400,
            detail=(
                "BigQuery rejected the request. Check that the dataset and "
                "micro_user_table schema match what WACA path expects."
            ),
        ) from exc
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=502, detail=f"BigQuery request failed: {type(exc).__name__}"
        ) from exc
    totals = dict(totals_rows[0]) if totals_rows else {}
    return rows, {
        "sessions": int(totals.get("sessions") or 0),
        "users": int(totals.get("users") or 0),
        "page_views": int(totals.get("page_views") or 0),
    }


async def get_top_journeys(
    *,
    project: str,
    dataset: str,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    max_sessions = max(1, min(int(max_sessions), MAX_SESSIONS_LIMIT))

    latest = None
    if date_from is None and date_to is None:
        latest = await max_event_date(project=project, dataset=dataset)
    period = resolve_period(date_from, date_to, latest=latest)

    rows, totals = await asyncio.to_thread(_query, project, dataset, period, max_sessions)
    truncated = len(rows) > max_sessions
    rows = rows[:max_sessions]

    data = aggregate(rows, limit=limit)
    data.update(
        {
            "source": f"{project}.{dataset}.micro_user_table",
            "period": period,
            "totals": totals,
            "max_sessions": max_sessions,
            "truncated": truncated,
            "limit": limit,
        }
    )
    return data
