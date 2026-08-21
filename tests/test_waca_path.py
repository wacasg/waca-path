"""Tests for the WACA path backend (v0.2.0).

Run from the repository root:

    python -m pytest -q
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from i18n import (  # noqa: E402
    DEFAULT_LANG,
    SUPPORTED_LANGS,
    make_translator,
    missing_keys,
    negotiate_lang,
    normalize_lang,
    translate,
)
from services import csv_export  # noqa: E402
from services.config import custom_columns  # noqa: E402
from services.user_explorer import (  # noqa: E402
    UserListRow,
    build_recordings,
    build_users_sql,
    clarity_open_url,
    has_column,
    parse_clarity_ids,
)
from services.user_filters import (  # noqa: E402
    UserListFilters,
    build_cursor_condition,
    build_where_and_params,
    decode_cursor,
    encode_cursor,
    make_next_cursor,
    parse_filters_from_query,
    CursorState,
)


# ---------------------------------------------------------------- i18n
class TestI18n:
    def test_every_source_key_is_translated(self):
        """An untranslated string must never reach a release."""
        for lang in SUPPORTED_LANGS:
            if lang == DEFAULT_LANG:
                continue
            assert missing_keys(lang) == [], f"{lang} is missing keys"

    def test_catalogues_have_identical_key_sets(self):
        base = set(json.loads((ROOT / "backend/i18n/ja.json").read_text("utf-8")))
        for lang in SUPPORTED_LANGS:
            keys = set(json.loads((ROOT / f"backend/i18n/{lang}.json").read_text("utf-8")))
            assert keys == base, f"{lang} key set differs"

    def test_falls_back_to_source_language(self, monkeypatch):
        import i18n

        monkeypatch.setitem(i18n._catalogs, "en", {})
        assert translate("users.title", "en") == translate("users.title", "ja")

    def test_unknown_key_returns_the_key(self):
        assert translate("no.such.key", "ja") == "no.such.key"

    def test_placeholders_are_filled(self):
        out = translate("clarity.shared", "en", count=3)
        assert "3" in out and "{count}" not in out

    def test_bad_placeholder_does_not_raise(self):
        # Missing params must degrade to the raw string, never crash a page.
        assert "{" in translate("clarity.shared", "en")

    @pytest.mark.parametrize(
        "value,expected",
        [("en", "en"), ("EN", "en"), ("ja-JP", "ja"), ("en-US", "en"), ("fr", None), ("", None)],
    )
    def test_normalize_lang(self, value, expected):
        assert normalize_lang(value) == expected

    def test_negotiation_priority(self):
        # query wins over everything
        assert negotiate_lang(query="en", cookie="ja", accept_language="ja") == "en"
        # then cookie
        assert negotiate_lang(cookie="en", accept_language="ja") == "en"
        # then Accept-Language
        assert negotiate_lang(accept_language="en-US,en;q=0.9") == "en"
        # then the tenant default
        assert negotiate_lang(tenant_default="en") == "en"
        # then ja
        assert negotiate_lang() == "ja"

    def test_unsupported_values_are_ignored(self):
        assert negotiate_lang(query="fr", accept_language="de") == "ja"

    def test_translator_binds_language(self):
        t = make_translator("en")
        assert t("users.title") == translate("users.title", "en")


# ------------------------------------------------------------- Clarity
class TestClarity:
    def test_four_segment_url_is_split(self):
        ids = parse_clarity_ids("https://clarity.microsoft.com/player/proj1/user9/sess8")
        assert ids == {
            "project_id": "proj1",
            "clarity_user_id": "user9",
            "clarity_session_id": "sess8",
        }

    def test_four_segment_url_is_used_as_is(self):
        url = "https://clarity.microsoft.com/player/proj1/user9/sess8"
        assert clarity_open_url(url) == url

    def test_three_segment_url_falls_back_to_the_recordings_list(self):
        url = "https://clarity.microsoft.com/player/proj1/opaque"
        assert clarity_open_url(url).endswith("/projects/view/proj1/recordings")

    def test_non_clarity_url_is_left_alone(self):
        assert parse_clarity_ids("https://example.test/x") is None
        assert clarity_open_url("https://example.test/x") == "https://example.test/x"

    def test_none_is_handled(self):
        assert parse_clarity_ids(None) is None
        assert clarity_open_url(None) is None

    def test_every_recording_is_kept(self):
        """Collapsing several recordings into one is what made them look mismatched."""
        urls = [
            "https://clarity.microsoft.com/player/p/u/rec1",
            "https://clarity.microsoft.com/player/p/u/rec2",
        ]
        recs = build_recordings(urls, {urls[0]: 2, urls[1]: 1})
        assert len(recs) == 2
        assert recs[0]["shared_sessions"] == 2
        assert recs[1]["shared_sessions"] == 1

    def test_empty_input(self):
        assert build_recordings(None) == []
        assert build_recordings([]) == []


# ------------------------------------------------------------- filters
def _filters(**kw) -> UserListFilters:
    base = dict(date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))
    base.update(kw)
    return UserListFilters(**base)


class TestFilters:
    def test_unknown_query_keys_are_dropped(self):
        f = parse_filters_from_query({"fc_evil": "x", "fmin_nope": "3", "sort": "bogus"})
        assert f.text_contains == {}
        assert f.numeric_min == {}
        assert f.sort_col == "last_active"

    def test_known_keys_are_kept(self):
        f = parse_filters_from_query({"fc_user_id": "abc", "fmin_sessions": "5"})
        assert f.text_contains == {"user_id": "abc"}
        assert f.numeric_min == {"sessions": 5}

    def test_custom_columns_become_filterable(self):
        cols = [{"column": "membership_type", "label": "Membership"}]
        f = parse_filters_from_query({"fe_membership_type": "member"}, custom_columns=cols)
        assert f.text_equals == {"membership_type": "member"}

    def test_reversed_dates_are_swapped(self):
        f = parse_filters_from_query({"from": "2026-05-31", "to": "2026-05-01"})
        assert f.date_from < f.date_to

    def test_invalid_preset_is_dropped(self):
        f = parse_filters_from_query({"preset": "clarity,drop_table"})
        assert f.presets == ["clarity"]

    def test_page_size_is_capped(self):
        f = parse_filters_from_query({}, page_size=100000)
        assert f.page_size <= 500

    def test_values_are_parameterised_not_inlined(self):
        f = _filters(text_contains={"user_id": "o'brien"})
        conds, params = build_where_and_params(f)
        assert "o'brien" not in " ".join(conds)
        assert "o'brien" in params.values()

    def test_cursor_roundtrip(self):
        c = CursorState("sessions", "desc", "5", "u1")
        assert decode_cursor(encode_cursor(c)) == c

    def test_corrupt_cursor_is_ignored(self):
        assert decode_cursor("!!!not-base64!!!") is None
        assert decode_cursor("") is None

    def test_cursor_breaks_ties_on_pseudo_id(self):
        """Without the tie-breaker, users sharing a sort value would repeat or vanish."""
        f = _filters(sort_col="sessions", cursor=CursorState("sessions", "desc", "5", "u1"))
        cond, params = build_cursor_condition(f)
        assert "user_pseudo_id" in cond
        assert params["cursor_pseudo_id"] == "u1"

    def test_next_cursor_uses_the_sort_column(self):
        rows = [UserListRow(user_pseudo_id="u9", sessions=7)]
        token = make_next_cursor(rows, _filters(sort_col="sessions"))
        state = decode_cursor(token)
        assert state.pseudo_id == "u9" and state.sort_val == "7"


# ----------------------------------------------------------------- SQL
class TestSql:
    def test_missing_optional_columns_are_replaced_with_null(self):
        """An install without clarity_play_url must still get a working query."""
        sql, _ = build_users_sql("p", "d", _filters(), {"user_pseudo_id", "event_date"})
        assert "clarity_play_url" not in sql
        assert "CAST(NULL AS BOOL) AS has_clarity" in sql

    def test_present_optional_columns_are_used(self):
        sql, _ = build_users_sql("p", "d", _filters(), {"clarity_play_url", "browser"})
        assert "LOGICAL_OR(clarity_play_url IS NOT NULL)" in sql
        assert "ANY_VALUE(browser)" in sql

    def test_key_event_cast_is_safe(self):
        """is_key_event is BOOL in WACA core but STRING/INT in some installs."""
        sql, _ = build_users_sql("p", "d", _filters(), set())
        assert "SAFE_CAST(is_key_event AS BOOL)" in sql

    def test_identifiers_are_validated(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            build_users_sql("p; DROP TABLE x", "d", _filters(), set())

    def test_empty_column_probe_stays_optimistic(self):
        assert has_column(set(), "clarity_play_url") is True
        assert has_column({"a"}, "clarity_play_url") is False


# ----------------------------------------------------------------- CSV
class TestCsv:
    @pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
    def test_formula_injection_is_neutralised(self, payload):
        """CWE-1236: a cell must never be executed by Excel / Sheets."""
        assert csv_export.csv_safe(payload).startswith("'")

    def test_ordinary_values_are_untouched(self):
        assert csv_export.csv_safe("member") == "member"
        assert csv_export.csv_safe("") == ""

    def test_header_follows_the_language(self):
        rows = [UserListRow(user_pseudo_id="u1")]
        ja = csv_export.build_csv(rows, lang="ja", custom_columns=[], truncated=False)
        en = csv_export.build_csv(rows, lang="en", custom_columns=[], truncated=False)
        assert "最終活動" in ja.splitlines()[0]
        assert "Last active" in en.splitlines()[0]

    def test_custom_columns_are_appended(self):
        cols = [{"column": "membership_type", "label": "Membership"}]
        row = UserListRow(user_pseudo_id="u1", custom={"membership_type": "member"})
        out = csv_export.build_csv([row], lang="en", custom_columns=cols, truncated=False)
        assert "Membership" in out.splitlines()[0]
        assert "member" in out.splitlines()[1]

    def test_bom_is_present_for_excel(self):
        out = csv_export.build_csv([], lang="ja", custom_columns=[], truncated=False)
        assert out.startswith("﻿")

    def test_truncation_is_announced(self):
        """Silent truncation would read as a complete export."""
        out = csv_export.build_csv([], lang="en", custom_columns=[], truncated=True)
        assert str(csv_export.MAX_ROWS) in out

    def test_datetime_formatting(self):
        assert csv_export.format_cell(datetime(2026, 5, 1, 10, 0), lang="ja") == "2026-05-01 10:00:00"

    def test_column_attributes_exist_on_the_row_type(self):
        """Guards against a typo silently emptying a whole column."""
        row = UserListRow(user_pseudo_id="u1")
        for _, attr in csv_export.BASE_COLUMNS:
            assert hasattr(row, attr), attr


# -------------------------------------------------------------- config
class TestCustomColumns:
    def test_unsafe_column_names_are_dropped(self):
        cfg = {"ui": {"custom_columns": [
            {"column": "ok_col", "label": "OK"},
            {"column": "bad col; DROP", "label": "nope"},
            {"column": "", "label": "empty"},
            "not-a-mapping",
        ]}}
        cols = custom_columns(cfg)
        assert [c["column"] for c in cols] == ["ok_col"]

    def test_label_defaults_to_the_column_name(self):
        cols = custom_columns({"ui": {"custom_columns": [{"column": "plan"}]}})
        assert cols[0]["label"] == "plan"

    def test_absent_config_is_fine(self):
        assert custom_columns({}) == []
