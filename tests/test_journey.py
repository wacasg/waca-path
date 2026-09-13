"""Tests for the per-user journey and the cross-user route aggregation (v0.3.0).

Both features are rule-based and LLM-free, and everything below runs without a
network: the grouping / counting functions take plain dicts, the SQL builders
return strings, and the templates are rendered with an in-memory context.

Run from the repository root:

    python -m pytest -q
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from i18n import SUPPORTED_LANGS, make_translator, translate  # noqa: E402
from services import journey, path_stats  # noqa: E402


def _row(ts: str, event: str, loc: str | None, *, sid: int = 1, psid: str | None = None, key: object = 0, **extra):
    row = {
        "event_timestamp": datetime.fromisoformat(ts),
        "event_name": event,
        "user_pseudo_id": "u1",
        "ga_session_id": sid,
        "pseudonymous_session_id": psid,
        "page_location": loc,
        "page_title": f"title {loc}" if loc else None,
        "engagement_time_msec": 1000,
        "is_key_event": key,
    }
    row.update(extra)
    return row


# One user, two sessions. Session A: / -> /pricing -> /pricing (dup) -> /contact
# with a key event; session B: / -> /pricing -> / (entry == exit).
ROWS = [
    _row("2026-09-12 10:00:00", "session_start", "https://x.test/", sid=1),
    _row("2026-09-12 10:00:01", "page_view", "https://x.test/", sid=1),
    _row("2026-09-12 10:00:30", "page_view", "https://x.test/pricing?utm=a", sid=1),
    _row("2026-09-12 10:00:45", "scroll", "https://x.test/pricing?utm=a", sid=1),
    _row("2026-09-12 10:01:30", "page_view", "https://x.test/contact/", sid=1),
    _row("2026-09-12 10:02:00", "generate_lead", "https://x.test/contact/", sid=1, key=1),
    _row("2026-09-13 09:00:00", "page_view", "https://x.test/", sid=2),
    _row("2026-09-13 09:00:20", "page_view", "https://x.test/pricing", sid=2),
    _row("2026-09-13 09:01:00", "page_view", "https://x.test/", sid=2),
]


# ---------------------------------------------------------------- helpers
class TestHelpers:
    @pytest.mark.parametrize(
        "loc, expected",
        [
            ("https://x.test/a/b?q=1#f", "/a/b"),
            ("https://x.test", "/"),
            ("/pricing?x=1", "/pricing"),
            ("", None),
            (None, None),
        ],
    )
    def test_page_path_of(self, loc, expected):
        assert journey.page_path_of(loc) == expected

    def test_collapse_consecutive(self):
        assert journey.collapse_consecutive(["a", "a", "b", "a", "a"]) == ["a", "b", "a"]
        assert journey.collapse_consecutive([]) == []

    @pytest.mark.parametrize("value, expected", [(1, True), (0, False), (True, True), ("1", True), ("false", False), (None, False)])
    def test_key_event_is_normalised_across_types(self, value, expected):
        # INT64 in WACA core, BOOL in the sample, STRING on some installs.
        assert journey.is_truthy_key_event(value) is expected


# ---------------------------------------------------------------- grouping
class TestGroupSessions:
    def test_sessions_are_grouped_and_ordered(self):
        sessions = journey.group_sessions(ROWS, "u1")
        assert [s["session_no"] for s in sessions] == [1, 2]
        assert [s["events"] for s in sessions] == [6, 3]
        assert sessions[0]["session_start"] == datetime(2026, 9, 12, 10, 0, 0)
        assert sessions[0]["duration_sec"] == 120.0

    def test_pseudonymous_session_id_wins_over_ga_session_id(self):
        rows = [
            _row("2026-09-12 10:00:00", "page_view", "https://x.test/", sid=1, psid="p1"),
            _row("2026-09-12 10:00:05", "page_view", "https://x.test/a", sid=2, psid="p1"),
            _row("2026-09-12 11:00:00", "page_view", "https://x.test/b", sid=3, psid=None),
        ]
        sessions = journey.group_sessions(rows, "u1")
        assert [s["session_key"] for s in sessions] == ["p1", "u1:3"]
        assert sessions[0]["events"] == 2

    def test_steps_carry_path_and_elapsed_seconds(self):
        steps = journey.group_sessions(ROWS, "u1")[0]["steps"]
        assert [s["step_no"] for s in steps] == [1, 2, 3, 4, 5, 6]
        assert steps[0]["seconds_from_prev"] is None
        assert steps[1]["seconds_from_prev"] == 1.0
        assert steps[2]["page_path"] == "/pricing"  # query string dropped
        assert steps[5]["is_key_event"] is True

    def test_path_sequence_collapses_duplicates(self):
        sessions = journey.group_sessions(ROWS, "u1")
        assert sessions[0]["page_path_sequence"] == ["/", "/pricing", "/contact/"]
        assert sessions[0]["entry_path"] == "/" and sessions[0]["exit_path"] == "/contact/"
        assert sessions[1]["page_path_sequence"] == ["/", "/pricing", "/"]

    def test_counts_per_session(self):
        sessions = journey.group_sessions(ROWS, "u1")
        assert sessions[0]["page_views"] == 3
        assert sessions[0]["key_event_count"] == 1
        assert sessions[1]["key_event_count"] == 0

    def test_empty_input(self):
        assert journey.group_sessions([], "u1") == []


# ---------------------------------------------------------------- summary
class TestSummary:
    def test_most_common_transition(self):
        sessions = journey.group_sessions(ROWS, "u1")
        hop = journey.most_common_transition(sessions)
        assert hop == {"from": "/", "to": "/pricing", "count": 2}

    def test_most_common_transition_ties_go_to_the_first_seen(self):
        sessions = [{"page_path_sequence": ["/a", "/b", "/c"]}]
        assert journey.most_common_transition(sessions) == {"from": "/a", "to": "/b", "count": 1}
        assert journey.most_common_transition([]) is None

    def test_summary_totals(self):
        sessions = journey.group_sessions(ROWS, "u1")
        s = journey.summarize(sessions)
        assert s["sessions_count"] == 2
        assert s["total_pv"] == 6
        assert s["events_count"] == 9
        assert s["key_event_count"] == 1
        assert s["distinct_paths"] == 3
        assert s["first_touch"] == datetime(2026, 9, 12, 10, 0, 0)
        assert s["last_active"] == datetime(2026, 9, 13, 9, 1, 0)
        assert s["truncated"] is False


# ---------------------------------------------------------------- observations
class TestObservations:
    def _codes(self, rows):
        sessions = journey.group_sessions(rows, "u1")
        return [o["code"] for o in journey.build_observations(journey.summarize(sessions), sessions)]

    def test_rules_fire_on_the_fixture(self):
        codes = self._codes(ROWS)
        assert codes[0] == "sessions_overview"
        assert "key_events" in codes and "no_key_event" not in codes
        assert "same_entry_exit" in codes  # session B: / -> /pricing -> /
        assert "repeat_transition" in codes  # / -> /pricing twice
        assert "many_pages_in_session" not in codes

    def test_no_rows(self):
        assert self._codes([]) == ["no_rows"]

    def test_no_key_event_and_single_page(self):
        rows = [_row("2026-09-12 10:00:00", "page_view", "https://x.test/", sid=1)]
        codes = self._codes(rows)
        assert "no_key_event" in codes and "single_page_sessions" in codes

    def test_many_pages_threshold(self):
        rows = [
            _row(f"2026-09-12 10:00:{i:02d}", "page_view", f"https://x.test/p{i}", sid=1)
            for i in range(journey.MANY_PAGES_THRESHOLD)
        ]
        assert "many_pages_in_session" in self._codes(rows)

    def test_every_observation_code_is_translated(self):
        """Each rule has a sentence in every language, and the placeholders fill."""
        sessions = journey.group_sessions(ROWS, "u1")
        obs = journey.build_observations(journey.summarize(sessions, truncated=True), sessions)
        obs += journey.build_observations(journey.summarize([]), [])
        out = journey.localize_observations(obs, SUPPORTED_LANGS)
        for item in out:
            for lang in SUPPORTED_LANGS:
                text = item["text"][lang]
                assert text != f"journey.obs.{item['code']}", (item["code"], lang)
                assert "{" not in text, (item["code"], lang, text)

    def test_assemble_marks_truncation(self):
        data = journey.assemble(ROWS, "u1", max_rows=4, langs=("en",))
        assert data["summary"]["truncated"] is True
        assert data["summary"]["events_count"] == 4
        assert data["observations"][-1]["code"] == "truncated"


# ---------------------------------------------------------------- SQL
class TestJourneySql:
    def test_optional_columns_become_typed_nulls(self):
        sql = journey.build_journey_sql("proj", "ds", {"event_timestamp", "page_location"})
        assert "CAST(NULL AS STRING) AS pseudonymous_session_id" in sql
        assert "CAST(NULL AS INT64) AS session_event_no" in sql
        assert "`proj.ds.micro_user_table`" in sql
        assert "@user_pseudo_id" in sql and "@limit" in sql

    def test_present_optional_columns_are_selected(self):
        sql = journey.build_journey_sql("proj", "ds", set(journey.OPTIONAL_COLUMNS) | {"event_name"})
        assert "CAST(NULL" not in sql
        assert "  pseudonymous_session_id" in sql

    def test_identifiers_are_validated(self):
        with pytest.raises(Exception):
            journey.build_journey_sql("proj", "ds`; DROP TABLE x --")

    def test_api_route_is_registered_and_version_bumped(self):
        import main

        paths = {r.path for r in main.app.routes}
        assert "/api/users/{user_pseudo_id}/journey" in paths
        assert "/ui/users/{user_pseudo_id}/journey" in paths
        assert "/api/journeys/top" in paths
        assert "/ui/journeys/" in paths
        assert "/api/users/{user_pseudo_id}/journey/context" in paths
        assert "/api/users/{user_pseudo_id}/journey/summary" in paths
        assert main.app.version == "0.4.0"


# ---------------------------------------------------------------- cross-user aggregation
SESSION_ROWS = [
    {"session_key": "s1", "locations": ["https://x.test/", "https://x.test/pricing", "https://x.test/pricing", "https://x.test/contact/"]},
    {"session_key": "s2", "locations": ["https://x.test/", "https://x.test/pricing"]},
    {"session_key": "s3", "locations": ["https://x.test/blog/a", "https://x.test/", "https://x.test/pricing", "https://x.test/"]},
    {"session_key": "s4", "locations": ["https://x.test/pricing"]},
    {"session_key": "s5", "locations": []},  # no page: dropped, not an empty route
]


class TestPathStats:
    def test_session_sequences_collapse_and_drop_empty(self):
        seqs = path_stats.session_sequences(SESSION_ROWS)
        assert len(seqs) == 4
        assert seqs[0] == ["/", "/pricing", "/contact/"]

    def test_transitions_counted_once_per_session(self):
        seqs = [["/a", "/b", "/a", "/b"], ["/a", "/b"]]
        counts = path_stats.count_transitions(seqs)
        assert counts[("/a", "/b")] == 2  # not 3: the first session bounces twice
        assert counts[("/b", "/a")] == 1

    def test_entries_and_exits(self):
        seqs = path_stats.session_sequences(SESSION_ROWS)
        assert path_stats.count_entries(seqs) == Counter({"/": 2, "/blog/a": 1, "/pricing": 1})
        assert path_stats.count_exits(seqs) == Counter({"/contact/": 1, "/pricing": 2, "/": 1})

    def test_sequences_are_cut_to_max_steps(self):
        seqs = [["/1", "/2", "/3", "/4"], ["/1", "/2", "/3", "/9"]]
        counts = path_stats.count_sequences(seqs, max_steps=3)
        assert counts == Counter({("/1", "/2", "/3"): 2})

    def test_top_n_is_stable_and_has_shares(self):
        top = path_stats.top_n(Counter({"b": 2, "a": 2, "c": 1}), limit=2, total=4)
        assert [t["key"] for t in top] == ["a", "b"]  # tie broken by key
        assert top[0]["share"] == 0.5

    def test_aggregate_shape(self):
        data = path_stats.aggregate(SESSION_ROWS, limit=3)
        assert data["sessions_analyzed"] == 4
        assert data["transitions"][0] == {"from": "/", "to": "/pricing", "sessions": 3, "share": 0.75}
        assert data["entries"][0]["path"] == "/"
        assert data["exits"][0]["path"] == "/pricing"
        assert data["sequences"][0]["path"] == ["/", "/pricing"] or data["sequences"][0]["sessions"] == 1
        assert all(len(r["path"]) <= path_stats.SEQUENCE_MAX_STEPS for r in data["sequences"])

    def test_period_defaults_to_last_30_days_clamped_to_data(self):
        today = date(2026, 9, 13)
        p = path_stats.resolve_period(None, None, today=today, latest=date(2026, 6, 1))
        assert p["date_to"] == date(2026, 6, 1) and p["clamped"] is True
        assert p["date_from"] == date(2026, 5, 2)
        # Data that is only a few days old is inside the window: no clamp.
        p = path_stats.resolve_period(None, None, today=today, latest=date(2026, 9, 12))
        assert p["date_to"] == today and p["clamped"] is False
        p = path_stats.resolve_period(None, None, today=today, latest=None)
        assert (p["date_from"], p["date_to"]) == (date(2026, 8, 14), today)

    def test_explicit_period_is_respected_and_swapped(self):
        p = path_stats.resolve_period(date(2026, 9, 10), date(2026, 9, 1), latest=date(2026, 1, 1))
        assert (p["date_from"], p["date_to"]) == (date(2026, 9, 1), date(2026, 9, 10))
        assert p["clamped"] is False

    def test_sql_uses_partition_filter_and_optional_session_id(self):
        sql = path_stats.build_sessions_sql("proj", "ds", {"event_date"})
        assert "event_date BETWEEN @date_from AND @date_to" in sql
        assert "event_name = 'page_view'" in sql
        assert "pseudonymous_session_id" not in sql
        assert "LIMIT @max_sessions" in sql
        sql = path_stats.build_sessions_sql("proj", "ds", {"pseudonymous_session_id", "session_event_no"})
        assert "COALESCE(pseudonymous_session_id" in sql
        assert "ORDER BY event_timestamp, session_event_no" in sql
        totals = path_stats.build_totals_sql("proj", "ds")
        assert "COUNT(DISTINCT user_pseudo_id)" in totals

    def test_sql_identifiers_are_validated(self):
        with pytest.raises(Exception):
            path_stats.build_sessions_sql("proj", "bad dataset")


# ---------------------------------------------------------------- i18n + templates
class TestJourneyI18nAndTemplates:
    def test_journey_keys_exist_in_every_catalogue(self):
        ja = json.loads((ROOT / "backend/i18n/ja.json").read_text("utf-8"))
        keys = {k for k in ja if k.startswith(("journey.", "journeys.", "detail.journey", "app.nav.journeys"))}
        assert len(keys) > 40
        for lang in SUPPORTED_LANGS:
            cat = json.loads((ROOT / f"backend/i18n/{lang}.json").read_text("utf-8"))
            assert keys <= set(cat), f"{lang} lacks journey keys"

    @pytest.mark.parametrize("lang", SUPPORTED_LANGS)
    def test_templates_render_offline(self, lang):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader(str(ROOT / "backend/templates")), autoescape=True)
        base = {
            "t": make_translator(lang),
            "lang": lang,
            "supported_langs": SUPPORTED_LANGS,
            "custom_columns": [],
            "data_source": {"project": "p", "dataset": "d"},
            "lang_links": {code: f"?lang={code}" for code in SUPPORTED_LANGS},
            "error": None,
        }
        data = journey.assemble(ROWS, "u1", max_rows=8, langs=(lang,))  # 9 rows: truncated
        html = env.get_template("users/journey.html").render(
            base | {"journey": data, "max_rows": 8, "api_url": "/api/users/u1/journey"}
        )
        assert translate("journey.title", lang) in html
        assert "/contact/" in html and "+1 s" in html
        assert translate("journey.truncated", lang, rows=8) in html

        stats = path_stats.aggregate(SESSION_ROWS, limit=5)
        stats.update({"period": {"clamped": True, "latest_event_date": "2026-09-12"}, "totals": {"sessions": 4, "users": 2, "page_views": 11}, "truncated": False, "max_sessions": 10})
        html = env.get_template("journeys/index.html").render(
            base | {"stats": stats, "limit": 10, "date_from": "2026-08-13", "date_to": "2026-09-12", "api_url": "/api/journeys/top"}
        )
        assert translate("journeys.transitions", lang) in html
        assert "/pricing" in html and "75.0%" in html
