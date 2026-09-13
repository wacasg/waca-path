"""Journey context ("タイムライン情報"): a compact, LLM-independent digest of one
user's journey.

Ported from the non-AI half of snowprism's ``agent/data_collector.py``
(``collect_user_context`` and ``_get_expected_next_pages``). snowprism built
this dict to feed a persona generator; here it is a first-class, rule-based
view of its own (``GET /api/users/{id}/journey/context`` and the
"Journey context" card on the journey page) and, when an AI provider happens
to be configured, the *input* to the optional summary. Nothing in this module
imports an SDK or reads an API key.

Everything is derived from the journey dict produced by
:func:`services.journey.assemble` plus the tenant config, so it is unit-tested
without a network.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from .journey import FUNNEL_EVENTS, page_path_of

# A pause between two sessions at least this long is reported as a "long gap"
# (the user came back after a week or more). Same threshold as snowprism's
# persona prompt, which treats 7+ days as a distinct return visit.
LONG_GAP_DAYS = 7.0
MAX_VISITED_PATHS = 50
MAX_EXPECTED_NEXT = 10


def _iso(value: Any) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) else None


def _days_between(later: Any, earlier: Any) -> float | None:
    if isinstance(later, datetime) and isinstance(earlier, datetime):
        return round((later - earlier).total_seconds() / 86400.0, 2)
    return None


# --------------------------------------------------------------------------
# Pure pieces
# --------------------------------------------------------------------------
def data_window(journey: dict[str, Any]) -> dict[str, Any]:
    summary = journey.get("summary") or {}
    first = summary.get("first_touch")
    last = summary.get("last_active")
    return {
        "first_touch": _iso(first),
        "last_active": _iso(last),
        "span_days": _days_between(last, first),
        "sessions_count": summary.get("sessions_count") or 0,
        "events_count": summary.get("events_count") or 0,
        "page_views": summary.get("total_pv") or 0,
        "key_event_count": summary.get("key_event_count") or 0,
        "truncated": bool(summary.get("truncated")),
    }


def session_gaps(sessions: list[dict[str, Any]], *, long_gap_days: float = LONG_GAP_DAYS) -> dict[str, Any]:
    """Pauses between consecutive sessions (end of one -> start of the next).

    ``long_gaps`` lists every pause of at least ``long_gap_days``; a returning
    visitor after a week or more usually marks a new intent, which is why
    the persona sketch and the analyst both want to see it.
    """
    gaps: list[dict[str, Any]] = []
    for prev, nxt in zip(sessions, sessions[1:]):
        days = _days_between(nxt.get("session_start"), prev.get("session_end"))
        if days is None:
            continue
        days = max(days, 0.0)
        gaps.append(
            {
                "from_session_no": prev.get("session_no"),
                "to_session_no": nxt.get("session_no"),
                "gap_days": days,
                "long": days >= long_gap_days,
            }
        )
    values = [g["gap_days"] for g in gaps]
    return {
        "threshold_days": long_gap_days,
        "gaps": gaps,
        "long_gaps": [g for g in gaps if g["long"]],
        "max_gap_days": max(values) if values else None,
        "median_gap_days": sorted(values)[len(values) // 2] if values else None,
    }


def visited_paths(sessions: list[dict[str, Any]], *, limit: int = MAX_VISITED_PATHS) -> list[dict[str, Any]]:
    """Distinct page paths in first-seen order with page-view and session counts.

    Every event that carries a ``page_location`` counts as "visited" - the
    same rule as ``page_path_sequence`` - because GA4 puts the landing page
    on ``session_start`` / ``first_visit`` as well, and a page the user was
    demonstrably on must not be reported as "not reached". ``page_views``
    counts only ``page_view`` rows; ``events`` counts everything.
    """
    order: list[str] = []
    pv_counts: Counter[str] = Counter()
    ev_counts: Counter[str] = Counter()
    sess_counts: Counter[str] = Counter()
    titles: dict[str, str] = {}
    for sess in sessions:
        seen_here: set[str] = set()
        for step in sess.get("steps") or []:
            path = step.get("page_path")
            if not path:
                continue
            if path not in ev_counts:
                order.append(path)
            ev_counts[path] += 1
            if step.get("event_name") == "page_view":
                pv_counts[path] += 1
            seen_here.add(path)
            if step.get("page_title") and path not in titles:
                titles[path] = step["page_title"]
        for path in seen_here:
            sess_counts[path] += 1
    out = [
        {"path": p, "title": titles.get(p), "page_views": pv_counts[p], "events": ev_counts[p], "sessions": sess_counts[p]}
        for p in order
    ]
    return out[:limit]


def journey_outcome(sessions: list[dict[str, Any]]) -> str:
    """``purchase`` > ``checkout_abandon`` > ``cart_abandon`` > ``key_event`` > ``view_only``.

    The three e-commerce outcomes are snowprism's; ``key_event`` is added
    because WACA core marks conversions with ``is_key_event`` regardless of
    the event name (a lead form on a B2B site, for example).
    """
    names: set[str] = set()
    any_key = False
    for sess in sessions:
        for step in sess.get("steps") or []:
            names.add(step.get("event_name"))
            any_key = any_key or bool(step.get("is_key_event"))
    if "purchase" in names:
        return "purchase"
    if "begin_checkout" in names:
        return "checkout_abandon"
    if "add_to_cart" in names:
        return "cart_abandon"
    if any_key:
        return "key_event"
    return "view_only"


def key_paths_of(tenant_config: dict[str, Any]) -> list[str]:
    site = tenant_config.get("site") or {}
    raw = site.get("key_paths") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        path = page_path_of(item.strip())
        if path and path not in out:
            out.append(path)
    return out


def expected_next_pages(
    visited: list[dict[str, Any]], tenant_config: dict[str, Any], *, limit: int = MAX_EXPECTED_NEXT
) -> dict[str, Any]:
    """Key pages of the site that this user has not reached yet.

    snowprism derived "the page the user should have reached next" from the
    e-commerce URL patterns in its tenant config. WACA path keeps only the
    tenant's ``site.key_paths`` list (already in ``tenant_config/example.yaml``),
    so the rule is simply: key paths, in the tenant's order, minus the ones
    already visited. Trailing-slash variants are treated as the same page.
    """
    key_paths = key_paths_of(tenant_config)
    base_url = str((tenant_config.get("site") or {}).get("base_url") or "").strip()
    seen = {v["path"].rstrip("/") or "/" for v in visited}

    def _norm(p: str) -> str:
        return p.rstrip("/") or "/"

    reached = [p for p in key_paths if _norm(p) in seen]
    missing = [p for p in key_paths if _norm(p) not in seen]
    return {
        "key_paths": key_paths,
        "reached": reached,
        "not_reached": [
            {"path": p, "url": urljoin(base_url.rstrip("/") + "/", p.lstrip("/")) if base_url else None}
            for p in missing[:limit]
        ],
    }


def device_and_traffic(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    devices: Counter[str] = Counter()
    browsers: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    hostnames: Counter[str] = Counter()
    for sess in sessions:
        if sess.get("device_category"):
            devices[str(sess["device_category"])] += 1
        if sess.get("browser"):
            browsers[str(sess["browser"])] += 1
        src, med = sess.get("traffic_source"), sess.get("traffic_medium")
        if src or med:
            sources[f"{src or '(direct)'} / {med or '(none)'}"] += 1
        for host in sess.get("hostnames") or []:
            hostnames[host] += 1

    def _top(counter: Counter[str]) -> list[dict[str, Any]]:
        return [{"value": k, "sessions": v} for k, v in counter.most_common(5)]

    last = sessions[-1] if sessions else None
    last_ts = last.get("session_end") if last else None
    return {
        "devices": _top(devices),
        "browsers": _top(browsers),
        "traffic": _top(sources),
        "hostnames": _top(hostnames),
        "last_hour_of_day": last_ts.hour if isinstance(last_ts, datetime) else None,
        "last_weekday": last_ts.strftime("%A") if isinstance(last_ts, datetime) else None,
    }


def session_digest(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One compact line per session - what the AI summary (and a human) needs."""
    out = []
    for sess in sessions:
        out.append(
            {
                "session_no": sess.get("session_no"),
                "start": _iso(sess.get("session_start")),
                "duration_sec": int(sess.get("duration_sec") or 0),
                "page_views": sess.get("page_views") or 0,
                "key_event_count": sess.get("key_event_count") or 0,
                "device_category": sess.get("device_category"),
                "traffic": (
                    f"{sess.get('traffic_source') or '(direct)'} / {sess.get('traffic_medium') or '(none)'}"
                    if (sess.get("traffic_source") or sess.get("traffic_medium"))
                    else None
                ),
                "path_sequence": sess.get("page_path_sequence") or [],
                "note_codes": [n["code"] for n in sess.get("inference_notes") or []],
            }
        )
    return out


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def build_context(journey: dict[str, Any], tenant_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the journey context from an already computed journey dict."""
    tenant_config = tenant_config or {}
    sessions = journey.get("sessions") or []
    visited = visited_paths(sessions)
    return {
        "user_pseudo_id": journey.get("user_pseudo_id"),
        "data_window": data_window(journey),
        "session_gaps": session_gaps(sessions),
        "visited_paths": visited,
        "journey_outcome": journey_outcome(sessions),
        "expected_next_pages": expected_next_pages(visited, tenant_config),
        "device_and_traffic": device_and_traffic(sessions),
        "sessions": session_digest(sessions),
        "funnel_events_checked": list(FUNNEL_EVENTS),
    }
