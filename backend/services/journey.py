"""Per-user journey ("ユーザー別経路"): sessions, ordered steps and rule-based notes.

Reads only WACA core output (``micro_user_table``). Nothing here writes, and
nothing here calls an LLM: every observation is a plain rule over the rows, so
the feature works on an install that has no AI provider configured at all.

Pipeline:

    rows (BigQuery, ordered by event_timestamp)
      -> group_sessions()        one dict per session, steps in order
      -> summarize()             user-level totals and the most common A -> B hop
      -> build_observations()    short factual notes, keyed for i18n

The grouping, collapsing and summarising functions are pure and take plain
dicts, so they are unit-tested without a network.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from .config import validate_identifier
from .user_explorer import _client, _scalar_params, available_columns, has_column

logger = logging.getLogger("waca_path_backend.journey")

DEFAULT_MAX_ROWS = 500
MAX_ROWS_LIMIT = 5000
PSEUDO_ID_MAX = 256

# Every WACA core output provides these; the query fails without them.
REQUIRED_COLUMNS = (
    "event_timestamp",
    "event_name",
    "user_pseudo_id",
    "ga_session_id",
    "page_location",
    "page_title",
    "engagement_time_msec",
    "is_key_event",
)

# Present in the full WACA core output, absent from the reduced sample. Each
# one is probed via INFORMATION_SCHEMA and selected as NULL when missing, in
# the same way the users list treats its optional columns.
OPTIONAL_COLUMNS: dict[str, str] = {
    "pseudonymous_session_id": "STRING",
    "session_event_no": "INT64",
    "user_event_no": "INT64",
    "seconds_from_prev_event": "FLOAT64",
    "device_category": "STRING",
    "browser": "STRING",
    "traffic_source": "STRING",
    "traffic_medium": "STRING",
    "traffic_campaign": "STRING",
}

# Threshold for the "many pages in one session" note.
MANY_PAGES_THRESHOLD = 5


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def page_path_of(page_location: str | None) -> str | None:
    """Path component of ``page_location`` (``https://x.test/a/b?q=1`` -> ``/a/b``).

    Query strings and fragments are dropped so that ``/pricing?utm=...`` and
    ``/pricing`` count as the same page. An empty path is normalised to ``/``.
    """
    if not page_location:
        return None
    try:
        parsed = urlparse(page_location)
    except ValueError:
        return None
    if not parsed.scheme and not parsed.netloc:
        # Already a bare path such as "/pricing".
        path = page_location.split("?", 1)[0].split("#", 1)[0]
        return path or "/"
    return parsed.path or "/"


def collapse_consecutive(values: list[Any]) -> list[Any]:
    """Drop consecutive duplicates: ``[a, a, b, a] -> [a, b, a]``."""
    out: list[Any] = []
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def is_truthy_key_event(value: Any) -> bool:
    """``is_key_event`` is BOOL in the sample, INT64 (0/1) in WACA core, and
    occasionally STRING. Treat all of them the same way."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


def _first_non_null(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def session_key(row: dict[str, Any], user_pseudo_id: str) -> str:
    """Group key: ``pseudonymous_session_id``, else ``user_pseudo_id:ga_session_id``."""
    psid = row.get("pseudonymous_session_id")
    if psid:
        return str(psid)
    return f"{user_pseudo_id}:{row.get('ga_session_id')}"


def _seconds_between(later: Any, earlier: Any) -> float | None:
    if isinstance(later, datetime) and isinstance(earlier, datetime):
        return round((later - earlier).total_seconds(), 3)
    return None


def _sort_key(index: int, row: dict[str, Any]) -> tuple:
    ts = row.get("event_timestamp")
    # Rows with an unparseable timestamp keep their query order at the end.
    ts_key = (0, ts) if isinstance(ts, datetime) else (1, index)
    no = row.get("session_event_no")
    no = no if isinstance(no, int) else row.get("user_event_no")
    return (ts_key, no if isinstance(no, int) else index, index)


def group_sessions(rows: list[dict[str, Any]], user_pseudo_id: str) -> list[dict[str, Any]]:
    """Turn ordered ``micro_user_table`` rows into one dict per session.

    Sessions are ordered by their first event; steps inside a session by
    ``event_timestamp`` (then ``session_event_no`` when the install has it).
    ``seconds_from_prev`` is recomputed from the timestamps so that it is
    always relative to the previous step *of the same session*, regardless of
    how the upstream column was defined.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(session_key(row, user_pseudo_id), []).append(row)

    sessions: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        ordered = [r for _, r in sorted(enumerate(bucket), key=lambda ir: _sort_key(*ir))]
        steps: list[dict[str, Any]] = []
        prev_ts: Any = None
        for no, row in enumerate(ordered, start=1):
            ts = row.get("event_timestamp")
            steps.append(
                {
                    "step_no": no,
                    "event_timestamp": ts,
                    "event_name": row.get("event_name"),
                    "page_title": row.get("page_title"),
                    "page_path": page_path_of(row.get("page_location")),
                    "page_location": row.get("page_location"),
                    "engagement_time_msec": row.get("engagement_time_msec"),
                    "seconds_from_prev": _seconds_between(ts, prev_ts) if prev_ts else None,
                    "is_key_event": is_truthy_key_event(row.get("is_key_event")),
                }
            )
            prev_ts = ts if isinstance(ts, datetime) else prev_ts

        paths = collapse_consecutive([s["page_path"] for s in steps if s["page_path"]])
        first_ts = steps[0]["event_timestamp"]
        last_ts = steps[-1]["event_timestamp"]
        sessions.append(
            {
                "session_key": key,
                "ga_session_id": _first_non_null(ordered, "ga_session_id"),
                "pseudonymous_session_id": _first_non_null(ordered, "pseudonymous_session_id"),
                "session_start": first_ts,
                "session_end": last_ts,
                "duration_sec": _seconds_between(last_ts, first_ts) or 0.0,
                "events": len(steps),
                "page_views": sum(1 for s in steps if s["event_name"] == "page_view"),
                "key_event_count": sum(1 for s in steps if s["is_key_event"]),
                "engagement_time_msec": sum(
                    int(s["engagement_time_msec"] or 0) for s in steps
                ),
                "device_category": _first_non_null(ordered, "device_category"),
                "browser": _first_non_null(ordered, "browser"),
                "traffic_source": _first_non_null(ordered, "traffic_source"),
                "traffic_medium": _first_non_null(ordered, "traffic_medium"),
                "traffic_campaign": _first_non_null(ordered, "traffic_campaign"),
                "entry_path": paths[0] if paths else None,
                "exit_path": paths[-1] if paths else None,
                "page_path_sequence": paths,
                "steps": steps,
            }
        )

    sessions.sort(key=lambda s: ((0, s["session_start"]) if isinstance(s["session_start"], datetime) else (1, 0)))
    for no, sess in enumerate(sessions, start=1):
        sess["session_no"] = no
    return sessions


def most_common_transition(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The ``A -> B`` page hop seen most often across all sessions.

    Counted over ``page_path_sequence`` (consecutive duplicates already
    collapsed, so ``A -> A`` never appears). Ties go to the hop seen first.
    """
    counts: Counter[tuple[str, str]] = Counter()
    order: list[tuple[str, str]] = []
    for sess in sessions:
        seq = sess.get("page_path_sequence") or []
        for a, b in zip(seq, seq[1:]):
            pair = (a, b)
            if pair not in counts:
                order.append(pair)
            counts[pair] += 1
    if not counts:
        return None
    best = max(order, key=lambda p: counts[p])  # first max wins on ties
    return {"from": best[0], "to": best[1], "count": counts[best]}


def summarize(sessions: list[dict[str, Any]], *, truncated: bool = False) -> dict[str, Any]:
    all_paths: set[str] = set()
    for sess in sessions:
        all_paths.update(sess.get("page_path_sequence") or [])
    starts = [s["session_start"] for s in sessions if isinstance(s.get("session_start"), datetime)]
    ends = [s["session_end"] for s in sessions if isinstance(s.get("session_end"), datetime)]
    return {
        "sessions_count": len(sessions),
        "events_count": sum(s.get("events", 0) for s in sessions),
        "total_pv": sum(s.get("page_views", 0) for s in sessions),
        "key_event_count": sum(s.get("key_event_count", 0) for s in sessions),
        "distinct_paths": len(all_paths),
        "most_common_transition": most_common_transition(sessions),
        "first_touch": min(starts) if starts else None,
        "last_active": max(ends) if ends else None,
        "truncated": truncated,
    }


def build_observations(summary: dict[str, Any], sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Short factual notes. Each is ``{"code", "params"}``; the text lives in
    the i18n catalogues under ``journey.obs.<code>`` so the UI and the API
    render the same rule in the visitor's language."""
    obs: list[dict[str, Any]] = []
    if not sessions:
        obs.append({"code": "no_rows", "params": {}})
        return obs

    n = summary["sessions_count"]
    obs.append(
        {
            "code": "sessions_overview",
            "params": {
                "sessions": n,
                "pv": summary["total_pv"],
                "paths": summary["distinct_paths"],
            },
        }
    )

    if summary["key_event_count"] == 0:
        obs.append({"code": "no_key_event", "params": {}})
    else:
        with_key = sum(1 for s in sessions if s.get("key_event_count", 0) > 0)
        obs.append(
            {
                "code": "key_events",
                "params": {"count": summary["key_event_count"], "sessions": with_key},
            }
        )

    same = [s for s in sessions if len(s.get("page_path_sequence") or []) >= 2 and s["entry_path"] == s["exit_path"]]
    if same:
        obs.append({"code": "same_entry_exit", "params": {"count": len(same), "path": same[0]["entry_path"]}})

    single = [s for s in sessions if len(s.get("page_path_sequence") or []) == 1]
    if single:
        obs.append({"code": "single_page_sessions", "params": {"count": len(single), "total": n}})

    busiest = max(sessions, key=lambda s: len(s.get("page_path_sequence") or []))
    if len(busiest.get("page_path_sequence") or []) >= MANY_PAGES_THRESHOLD:
        obs.append(
            {
                "code": "many_pages_in_session",
                "params": {
                    "count": len(busiest["page_path_sequence"]),
                    "session_no": busiest.get("session_no"),
                },
            }
        )

    hop = summary.get("most_common_transition")
    if hop and hop["count"] >= 2:
        obs.append({"code": "repeat_transition", "params": dict(hop)})

    if summary.get("truncated"):
        obs.append({"code": "truncated", "params": {"rows": summary["events_count"]}})
    return obs


def localize_observations(obs: list[dict[str, Any]], langs: tuple[str, ...]) -> list[dict[str, Any]]:
    """Attach the translated sentence for each language to every observation."""
    from i18n import translate  # backend/ is on sys.path, as in routers/ui.py

    out = []
    for item in obs:
        entry = dict(item)
        entry["text"] = {
            lang: translate(f"journey.obs.{item['code']}", lang, **item["params"]) for lang in langs
        }
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------
def build_journey_sql(project: str, dataset: str, cols: set[str] | None = None) -> str:
    """Rows for one user, oldest first. Optional columns become typed NULLs."""
    project = validate_identifier(project, "project")
    dataset = validate_identifier(dataset, "dataset")
    cols = cols if cols is not None else set()

    selected = [f"  {name}" for name in REQUIRED_COLUMNS]
    for name, typ in OPTIONAL_COLUMNS.items():
        if has_column(cols, name):
            selected.append(f"  {name}")
        else:
            selected.append(f"  CAST(NULL AS {typ}) AS {name}")
    select_list = ",\n".join(selected)
    return f"""
SELECT
{select_list}
FROM `{project}.{dataset}.micro_user_table`
WHERE user_pseudo_id = @user_pseudo_id
ORDER BY event_timestamp
LIMIT @limit
"""


def _query_rows(project: str, dataset: str, user_pseudo_id: str, max_rows: int) -> list[dict[str, Any]]:
    """Run the journey query, mapping BigQuery failures the same way
    ``/api/agent/site-audit`` does (404 / 403 / 400 / 502)."""
    from google.api_core.exceptions import BadRequest, Forbidden, GoogleAPICallError, NotFound
    from google.cloud import bigquery

    cols = available_columns(project, dataset)
    sql = build_journey_sql(project, dataset, cols)
    # One extra row tells us whether the cap was hit without a second query.
    params = {"user_pseudo_id": user_pseudo_id, "limit": max_rows + 1}
    client = _client(project)
    job_config = bigquery.QueryJobConfig(query_parameters=_scalar_params(params))
    try:
        return [dict(row) for row in client.query(sql, job_config=job_config).result()]
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
        logger.warning("BigQuery rejected the journey query: %s", getattr(exc, "message", str(exc)))
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


def assemble(rows: list[dict[str, Any]], user_pseudo_id: str, *, max_rows: int, langs: tuple[str, ...]) -> dict[str, Any]:
    """Pure assembly step shared by the API and the UI (and the tests)."""
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    sessions = group_sessions(rows, user_pseudo_id)
    summary = summarize(sessions, truncated=truncated)
    observations = localize_observations(build_observations(summary, sessions), langs)
    return {
        "user_pseudo_id": user_pseudo_id,
        "summary": summary,
        "sessions": sessions,
        "observations": observations,
    }


async def get_user_journey(
    *,
    project: str,
    dataset: str,
    user_pseudo_id: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    langs: tuple[str, ...] = ("ja", "en"),
) -> dict[str, Any]:
    if not user_pseudo_id or len(user_pseudo_id) > PSEUDO_ID_MAX:
        raise HTTPException(status_code=400, detail="Invalid user_pseudo_id")
    max_rows = max(1, min(int(max_rows), MAX_ROWS_LIMIT))
    rows = await asyncio.to_thread(_query_rows, project, dataset, user_pseudo_id, max_rows)
    data = assemble(rows, user_pseudo_id, max_rows=max_rows, langs=langs)
    data["source"] = f"{project}.{dataset}.micro_user_table"
    return data
