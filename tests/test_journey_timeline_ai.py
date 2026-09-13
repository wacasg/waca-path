"""Tests for the v0.4.0 journey timeline, journey context and optional AI summary.

Everything runs without a network and without any provider SDK installed:

* the timeline / inference-note / context functions take plain dicts;
* the API and UI routes are exercised with ``journey.get_user_journey``
  monkeypatched to return a fixture, and ``ai_summary.complete_text``
  replaced by a fake provider;
* the AI gate is tested by clearing / setting environment variables only.

Run from the repository root:

    python -m pytest -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from i18n import SUPPORTED_LANGS, translate  # noqa: E402
from services import ai_summary, journey, journey_context  # noqa: E402


def _row(ts: str, event: str, loc: str | None, *, sid: int = 1, key: object = 0, **extra):
    row = {
        "event_timestamp": datetime.fromisoformat(ts),
        "event_name": event,
        "user_pseudo_id": "u1",
        "ga_session_id": sid,
        "pseudonymous_session_id": None,
        "page_location": loc,
        "page_title": f"title {loc}" if loc else None,
        "engagement_time_msec": 1000,
        "is_key_event": key,
    }
    row.update(extra)
    return row


# Session 1 (organic, desktop): session_start before the first PV, scroll on
# /pricing, a key event on /contact/ after 3 pages. Session 2, nine days
# later (mobile, cpc): a bounce on /pricing. Session 3 (same day as 2): an
# events-only session with no page_view at all.
ROWS = [
    _row("2026-09-01 10:00:00", "session_start", "https://x.test/", traffic_source="google", traffic_medium="organic", device_category="desktop"),
    _row("2026-09-01 10:00:00", "first_visit", "https://x.test/"),
    _row("2026-09-01 10:00:01", "page_view", "https://x.test/"),
    _row("2026-09-01 10:00:30", "page_view", "https://x.test/pricing?utm=a"),
    _row("2026-09-01 10:00:45", "scroll", "https://x.test/pricing?utm=a"),
    _row("2026-09-01 10:01:00", "scroll", "https://x.test/pricing?utm=a"),
    _row("2026-09-01 10:03:30", "page_view", "https://x.test/contact/"),
    _row("2026-09-01 10:04:00", "form_submit", "https://x.test/contact/"),
    _row("2026-09-01 10:04:10", "generate_lead", "https://x.test/contact/", key=1),
    _row("2026-09-10 09:00:00", "session_start", "https://x.test/pricing", sid=2, traffic_source="google", traffic_medium="cpc", device_category="mobile"),
    _row("2026-09-10 09:00:01", "page_view", "https://x.test/pricing", sid=2),
    _row("2026-09-10 09:00:11", "user_engagement", "https://x.test/pricing", sid=2),
    _row("2026-09-10 12:00:00", "session_start", None, sid=3),
    _row("2026-09-10 12:00:02", "user_engagement", None, sid=3),
]

TENANT = {
    "site": {"base_url": "https://www.x.test", "key_paths": ["/", "/pricing", "/checkout/thank-you", "/contact"]},
}


def _journey(rows=ROWS, langs=("ja", "en")):
    return journey.assemble(rows, "u1", max_rows=500, langs=langs)


# ---------------------------------------------------------------- timeline
class TestTimeline:
    def test_events_attach_to_the_page_view_that_was_open(self):
        sess = journey.group_sessions(ROWS, "u1")[0]
        tl = sess["timeline"]
        assert [i["kind"] for i in tl] == ["pv", "pv", "pv"]
        assert [i["page_path"] for i in tl] == ["/", "/pricing", "/contact/"]
        # session_start / first_visit fire before the first page_view -> first PV.
        assert [e["event_name"] for e in tl[0]["attached_events"]] == ["session_start", "first_visit"]
        assert [e["event_name"] for e in tl[1]["attached_events"]] == ["scroll", "scroll"]
        assert [e["event_name"] for e in tl[2]["attached_events"]] == ["form_submit", "generate_lead"]

    def test_dwell_counts_and_key_flag(self):
        tl = journey.group_sessions(ROWS, "u1")[0]["timeline"]
        assert tl[0]["dwell_sec"] == 29.0  # 10:00:01 -> 10:00:30
        assert tl[1]["dwell_sec"] == 180.0
        assert tl[2]["dwell_sec"] == 40.0  # last PV: until the session's last event
        assert tl[1]["scrolls"] == 2 and tl[0]["scrolls"] == 0
        assert tl[2]["key_event_count"] == 1 and tl[1]["key_event_count"] == 0
        assert [i["page_view_no"] for i in tl] == [1, 2, 3]

    def test_session_without_page_views_yields_orphan_events(self):
        sess = journey.group_sessions(ROWS, "u1")[2]
        assert sess["page_views"] == 0
        assert [i["kind"] for i in sess["timeline"]] == ["event", "event"]
        assert [i["event_name"] for i in sess["timeline"]] == ["session_start", "user_engagement"]

    def test_event_without_timestamp_attaches_to_first_page_view(self):
        steps = [
            {"step_no": 1, "event_name": "page_view", "event_timestamp": datetime(2026, 9, 1, 10), "page_path": "/"},
            {"step_no": 2, "event_name": "page_view", "event_timestamp": datetime(2026, 9, 1, 10, 1), "page_path": "/a"},
            {"step_no": 3, "event_name": "click", "event_timestamp": None},
        ]
        tl = journey.build_timeline(steps)
        assert [e["event_name"] for e in tl[0]["attached_events"]] == ["click"]
        assert tl[1]["attached_events"] == []

    def test_steps_api_shape_is_unchanged(self):
        """Backward compatibility: the v0.3.0 fields are all still there."""
        data = _journey()
        sess = data["sessions"][0]
        for key in ("session_no", "session_start", "duration_sec", "entry_path", "exit_path", "page_path_sequence", "steps"):
            assert key in sess
        assert {"timeline", "inference_notes"} <= set(sess)
        assert set(data) == {"user_pseudo_id", "summary", "sessions", "observations"}


# ---------------------------------------------------------------- inference notes
class TestInferenceNotes:
    def _codes(self, rows):
        return [[n["code"] for n in s["inference_notes"]] for s in journey.group_sessions(rows, "u1")]

    def test_fixture_notes(self):
        s1, s2, s3 = self._codes(ROWS)
        assert s1 == ["organic_search", "key_event_after_pages", "long_dwell"]
        assert s2[:2] == ["paid_entry", "bounce"]
        assert "mobile_quick" in s2
        assert s3 == ["quick_check"]  # no page view at all is not a bounce

    def test_notes_are_capped(self):
        rows = [
            _row("2026-09-01 10:00:00", "page_view", "https://x.test/login", traffic_source="google", traffic_medium="organic", device_category="mobile"),
            _row("2026-09-01 10:00:05", "page_view", "https://a.test/pay"),
            _row("2026-09-01 10:00:08", "purchase", "https://a.test/pay", key=1),
        ]
        (codes,) = self._codes(rows)
        assert len(codes) == journey.MAX_INFERENCE_NOTES
        assert codes == ["organic_search", "task_landing", "funnel_purchase"]

    def test_funnel_order_and_cross_domain(self):
        rows = [
            _row("2026-09-01 10:00:00", "page_view", "https://x.test/p"),
            _row("2026-09-01 10:00:05", "add_to_cart", "https://x.test/p"),
            _row("2026-09-01 10:00:10", "page_view", "https://pay.test/checkout"),
            _row("2026-09-01 10:00:15", "begin_checkout", "https://pay.test/checkout"),
        ]
        (codes,) = self._codes(rows)
        assert "funnel_begin_checkout" in codes and "funnel_add_to_cart" not in codes
        assert "cross_domain" in codes

    def test_deep_session_and_returned_to_entry(self):
        deep = [_row(f"2026-09-01 10:00:{i:02d}", "page_view", f"https://x.test/p{i}") for i in range(journey.DEEP_SESSION_PV)]
        assert "deep_session" in self._codes(deep)[0]
        back = [
            _row("2026-09-01 10:00:00", "page_view", "https://x.test/"),
            _row("2026-09-01 10:00:10", "page_view", "https://x.test/a"),
            _row("2026-09-01 10:00:20", "page_view", "https://x.test/"),
        ]
        assert "returned_to_entry" in self._codes(back)[0]

    def test_quick_check_fallback(self):
        rows = [
            _row("2026-09-01 10:00:00", "page_view", "https://x.test/a"),
            _row("2026-09-01 10:00:05", "page_view", "https://x.test/b"),
        ]
        assert self._codes(rows) == [["quick_check"]]

    def test_every_note_code_is_translated_and_placeholders_fill(self):
        codes = [k.split(".", 2)[2] for k in json.loads((ROOT / "backend/i18n/ja.json").read_text("utf-8")) if k.startswith("journey.note.")]
        assert len(codes) >= 15
        params = {"source": "g", "medium": "cpc", "path": "/x", "pages": 3, "event": "e", "seconds": 9, "count": 2}
        for code in codes:
            for lang in SUPPORTED_LANGS:
                text = translate(f"journey.note.{code}", lang, **params)
                assert text != f"journey.note.{code}", (code, lang)
                assert "{" not in text, (code, lang, text)

    def test_assemble_localizes_notes(self):
        data = _journey(langs=("en",))
        note = data["sessions"][0]["inference_notes"][0]
        assert note["code"] == "organic_search"
        assert set(note["text"]) == {"en"} and "organic search" in note["text"]["en"]


# ---------------------------------------------------------------- journey context
class TestJourneyContext:
    def test_gap_analysis_flags_seven_days_or_more(self):
        ctx = journey_context.build_context(_journey(), TENANT)
        gaps = ctx["session_gaps"]
        assert gaps["threshold_days"] == 7.0
        assert [g["gap_days"] for g in gaps["gaps"]] == [8.96, 0.12]
        assert [g["long"] for g in gaps["gaps"]] == [True, False]
        assert gaps["long_gaps"][0]["from_session_no"] == 1 and gaps["long_gaps"][0]["to_session_no"] == 2
        assert gaps["max_gap_days"] == 8.96

    def test_single_session_has_no_gaps(self):
        ctx = journey_context.build_context(_journey(ROWS[:9]), TENANT)
        assert ctx["session_gaps"]["gaps"] == [] and ctx["session_gaps"]["max_gap_days"] is None

    def test_visited_paths_and_counts(self):
        ctx = journey_context.build_context(_journey(), TENANT)
        visited = {v["path"]: v for v in ctx["visited_paths"]}
        assert list(visited) == ["/", "/pricing", "/contact/"]
        assert visited["/pricing"]["page_views"] == 2 and visited["/pricing"]["sessions"] == 2
        assert visited["/"]["title"] == "title https://x.test/"

    def test_expected_next_pages_from_tenant_key_paths(self):
        ctx = journey_context.build_context(_journey(), TENANT)
        nx = ctx["expected_next_pages"]
        assert nx["key_paths"] == ["/", "/pricing", "/checkout/thank-you", "/contact"]
        # "/contact" (config) == "/contact/" (visited): trailing slash ignored.
        assert nx["reached"] == ["/", "/pricing", "/contact"]
        assert nx["not_reached"] == [{"path": "/checkout/thank-you", "url": "https://www.x.test/checkout/thank-you"}]

    def test_expected_next_pages_without_tenant_site_block(self):
        nx = journey_context.build_context(_journey(), {})["expected_next_pages"]
        assert nx == {"key_paths": [], "reached": [], "not_reached": []}
        assert journey_context.key_paths_of({"site": {"key_paths": "not-a-list"}}) == []

    def test_outcome_precedence(self):
        assert journey_context.build_context(_journey(), TENANT)["journey_outcome"] == "key_event"
        rows = ROWS[:9] + [_row("2026-09-01 10:05:00", "add_to_cart", "https://x.test/p")]
        assert journey_context.build_context(_journey(rows), TENANT)["journey_outcome"] == "cart_abandon"
        rows += [_row("2026-09-01 10:06:00", "purchase", "https://x.test/p")]
        assert journey_context.build_context(_journey(rows), TENANT)["journey_outcome"] == "purchase"
        assert journey_context.build_context(_journey(ROWS[12:]), TENANT)["journey_outcome"] == "view_only"

    def test_device_traffic_and_window(self):
        ctx = journey_context.build_context(_journey(), TENANT)
        d = ctx["device_and_traffic"]
        assert {x["value"]: x["sessions"] for x in d["devices"]} == {"desktop": 1, "mobile": 1}
        assert {x["value"] for x in d["traffic"]} == {"google / organic", "google / cpc"}
        assert d["last_hour_of_day"] == 12 and d["last_weekday"] == "Thursday"
        w = ctx["data_window"]
        assert w["sessions_count"] == 3 and w["span_days"] == 9.08 and w["truncated"] is False
        assert ctx["sessions"][0]["note_codes"][0] == "organic_search"

    def test_degrades_on_reduced_columns(self):
        """The sample dataset has no device / traffic columns: nothing breaks."""
        rows = [{k: v for k, v in r.items() if k not in ("device_category", "traffic_source", "traffic_medium")} for r in ROWS]
        ctx = journey_context.build_context(_journey(rows), TENANT)
        assert ctx["device_and_traffic"]["devices"] == [] and ctx["device_and_traffic"]["traffic"] == []
        assert ctx["visited_paths"]


# ---------------------------------------------------------------- AI gating (no SDK, no network)
@pytest.fixture
def no_ai(monkeypatch):
    for var in (ai_summary.PROVIDER_ENV, ai_summary.MODEL_ENV, ai_summary.RATE_LIMIT_ENV, "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ai_summary, "_limiter", None)


@pytest.fixture
def fake_ai(no_ai, monkeypatch):
    """Provider configured + a fake client; records the prompts it received."""
    monkeypatch.setenv(ai_summary.PROVIDER_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    calls: list[tuple[str, str, str]] = []

    def fake_complete(cfg, system, user):
        calls.append((cfg.model, system, user))
        return (
            "Here you go:\n```json\n"
            + json.dumps(
                {
                    "persona_summary": "A visitor comparing pricing before contacting sales.",
                    "journey_stages": [
                        {"stage": "Discover", "evidence_pages": ["/"], "summary": "landed", "confidence": "high"},
                        {"stage": "Compare", "evidence_pages": ["/pricing"], "summary": "read pricing", "confidence": "medium"},
                        {"stage": "Decide", "evidence_pages": ["/contact/"], "summary": "sent the form", "confidence": "HIGH"},
                    ],
                    "improvement_ideas": ["idea 1", "idea 2", "idea 3", "idea 4"],
                    "unexpected": "dropped",
                }
            )
            + "\n```"
        )

    monkeypatch.setattr(ai_summary, "complete_text", fake_complete)
    return calls


class TestAIConfig:
    def test_disabled_when_env_unset(self, no_ai):
        assert ai_summary.ai_config() is None
        assert ai_summary.is_enabled() is False
        assert ai_summary.public_status() == {"enabled": False, "provider": None, "model": None}

    def test_disabled_when_key_missing_or_provider_unknown(self, no_ai, monkeypatch):
        monkeypatch.setenv(ai_summary.PROVIDER_ENV, "openai")
        assert ai_summary.ai_config() is None
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        assert ai_summary.ai_config().model == "gpt-5-mini"
        monkeypatch.setenv(ai_summary.PROVIDER_ENV, "mystery")
        assert ai_summary.ai_config() is None

    def test_model_override_and_status_never_leaks_key(self, fake_ai, monkeypatch):
        monkeypatch.setenv(ai_summary.MODEL_ENV, "claude-haiku-4-5")
        status = ai_summary.public_status()
        assert status == {"enabled": True, "provider": "anthropic", "model": "claude-haiku-4-5"}
        assert "sk-test" not in json.dumps(status)

    def test_default_models(self):
        assert {p: v[1] for p, v in ai_summary.PROVIDERS.items()} == {
            "anthropic": "claude-sonnet-4-6",
            "openai": "gpt-5-mini",
            "google": "gemini-2.5-flash",
        }

    def test_rate_limiter_sliding_window(self):
        now = [0.0]
        lim = ai_summary.SlidingWindowLimiter(2, clock=lambda: now[0])
        assert lim.try_acquire() and lim.try_acquire() and not lim.try_acquire()
        now[0] = 61.0
        assert lim.try_acquire()

    def test_parse_and_normalize(self):
        obj = ai_summary.parse_json_object('noise {"persona_summary": "p", "journey_stages": [{"stage": "s", "confidence": "bogus", "evidence_pages": "/x"}], "improvement_ideas": [1, "", "b"]}')
        out = ai_summary.normalize_summary(obj)
        assert out["journey_stages"] == [{"stage": "s", "summary": "", "evidence_pages": [], "confidence": "low"}]
        assert out["improvement_ideas"] == ["1", "b"]
        with pytest.raises(ai_summary.AIProviderError):
            ai_summary.parse_json_object("no json here")

    def test_prompt_contains_data_but_never_a_key(self, fake_ai):
        data = _journey()
        ctx = journey_context.build_context(data, TENANT)
        system, user = ai_summary.build_prompt(ctx, data, "en")
        assert "JSON" in system and "/pricing" in user and "/checkout/thank-you" in user
        assert "sk-test" not in user and "sk-test" not in system
        assert "Write the JSON values in English" in user
        assert "Japanese" in ai_summary.build_prompt(ctx, data, "ja")[1]

    def test_missing_sdk_is_reported_not_crashed(self, no_ai, monkeypatch):
        """The real dispatcher maps ImportError -> AISDKMissing and any other
        SDK exception -> AIProviderError (message = class name only, no key)."""
        monkeypatch.setenv(ai_summary.PROVIDER_ENV, "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
        cfg = ai_summary.ai_config()

        def boom(cfg, system, user):
            raise ImportError("No module named 'anthropic'")

        monkeypatch.setitem(ai_summary._CALLS, "anthropic", boom)
        with pytest.raises(ai_summary.AISDKMissing):
            ai_summary.complete_text(cfg, "s", "u")

        def http_fail(cfg, system, user):
            raise RuntimeError("401 unauthorized sk-test-not-a-real-key")

        monkeypatch.setitem(ai_summary._CALLS, "anthropic", http_fail)
        with pytest.raises(ai_summary.AIProviderError) as info:
            ai_summary.complete_text(cfg, "s", "u")
        assert "sk-test" not in str(info.value)


# ---------------------------------------------------------------- API + UI routes
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    async def fake_journey(**kwargs):
        data = journey.assemble(ROWS, kwargs["user_pseudo_id"], max_rows=kwargs.get("max_rows", 500), langs=kwargs.get("langs", SUPPORTED_LANGS))
        data["source"] = "p.d.micro_user_table"
        return data

    monkeypatch.setattr(journey, "get_user_journey", fake_journey)
    return TestClient(main.app)


class TestRoutes:
    def test_context_endpoint(self, client, no_ai):
        r = client.get("/api/users/u1/journey/context?tenant_id=example")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == "example" and body["journey_outcome"] == "key_event"
        assert body["session_gaps"]["long_gaps"][0]["gap_days"] == 8.96
        # example.yaml lists /, /pricing, /checkout/thank-you
        assert [p["path"] for p in body["expected_next_pages"]["not_reached"]] == ["/checkout/thank-you"]
        assert body["ai"] == {"enabled": False, "provider": None, "model": None}

    def test_journey_endpoint_carries_timeline_and_notes(self, client, no_ai):
        body = client.get("/api/users/u1/journey?tenant_id=example").json()
        first = body["sessions"][0]
        assert first["timeline"][1]["attached_events"][0]["event_name"] == "scroll"
        assert first["inference_notes"][0]["text"]["ja"].startswith("検索経由")

    def test_summary_is_409_without_a_provider(self, client, no_ai):
        r = client.post("/api/users/u1/journey/summary", json={"tenant_id": "example", "lang": "en"})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "ai_disabled"
        assert detail["message"] == translate("ai.error.ai_disabled", "en")
        assert set(detail["message_i18n"]) == set(SUPPORTED_LANGS)

    def test_summary_is_409_when_provider_set_but_key_missing(self, client, no_ai, monkeypatch):
        monkeypatch.setenv(ai_summary.PROVIDER_ENV, "google")
        assert client.post("/api/users/u1/journey/summary", json={"tenant_id": "example"}).status_code == 409

    def test_summary_with_fake_provider(self, client, fake_ai):
        r = client.post("/api/users/u1/journey/summary", json={"tenant_id": "example", "lang": "en"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "anthropic" and body["model"] == "claude-sonnet-4-6" and body["lang"] == "en"
        s = body["summary"]
        assert s["persona_summary"].startswith("A visitor")
        assert [st["stage"] for st in s["journey_stages"]] == ["Discover", "Compare", "Decide"]
        assert s["journey_stages"][2]["confidence"] == "high"  # normalised
        assert len(s["improvement_ideas"]) == 3  # capped
        assert "unexpected" not in s
        assert body["disclaimer"] == translate("ai.disclaimer", "en")
        # The fake client saw the journey, and nothing secret.
        model, system, user = fake_ai[0]
        assert model == "claude-sonnet-4-6" and "/contact/" in user and "sk-test" not in user
        assert "sk-test" not in r.text

    def test_summary_rate_limit_and_provider_errors(self, client, fake_ai, monkeypatch):
        monkeypatch.setenv(ai_summary.RATE_LIMIT_ENV, "1")
        monkeypatch.setattr(ai_summary, "_limiter", None)
        assert client.post("/api/users/u1/journey/summary", json={"tenant_id": "example"}).status_code == 200
        r = client.post("/api/users/u1/journey/summary", json={"tenant_id": "example"})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "ai_rate_limited"

        monkeypatch.setattr(ai_summary, "_limiter", None)

        def provider_down(cfg, system, user):
            raise ai_summary.AIProviderError("boom")

        monkeypatch.setattr(ai_summary, "complete_text", provider_down)
        assert client.post("/api/users/u1/journey/summary", json={"tenant_id": "example"}).status_code == 502

        monkeypatch.setattr(ai_summary, "_limiter", None)

        def sdk_missing(cfg, system, user):
            raise ai_summary.AISDKMissing("anthropic")

        monkeypatch.setattr(ai_summary, "complete_text", sdk_missing)
        r = client.post("/api/users/u1/journey/summary", json={"tenant_id": "example"})
        assert r.status_code == 503 and "requirements-ai.txt" in r.json()["detail"]["message"]

    @pytest.mark.parametrize("lang", SUPPORTED_LANGS)
    def test_ui_hides_ai_button_when_disabled(self, client, no_ai, lang):
        r = client.get(f"/ui/users/u1/journey?lang={lang}&tenant=example")
        assert r.status_code == 200, r.text
        html = r.text
        assert "ai-summary-btn" not in html and translate("ai.button", lang) not in html
        # Timeline, notes and context are all there without AI.
        assert translate("journey.timeline.attached", lang) in html
        assert translate("journey.context", lang) in html
        assert translate("journey.note.organic_search", lang) in html
        assert "/checkout/thank-you" in html
        assert translate("journey.timeline.orphan", lang) in html  # session 3
        assert "/journey/context?" in html

    def test_ui_shows_ai_button_when_enabled(self, client, fake_ai):
        html = client.get("/ui/users/u1/journey?lang=en&tenant=example").text
        assert 'id="ai-summary-btn"' in html and "Summarize with AI" in html
        assert "/api/users/u1/journey/summary" in html
        assert "anthropic / claude-sonnet-4-6" in html
        assert "sk-test" not in html


# ---------------------------------------------------------------- i18n parity for the new keys
def test_new_keys_exist_in_every_catalogue():
    ja = json.loads((ROOT / "backend/i18n/ja.json").read_text("utf-8"))
    keys = {k for k in ja if k.startswith(("journey.timeline", "journey.note", "journey.notes", "journey.context", "ai."))}
    assert len(keys) >= 70
    for lang in SUPPORTED_LANGS:
        cat = json.loads((ROOT / f"backend/i18n/{lang}.json").read_text("utf-8"))
        assert keys <= set(cat), f"{lang} lacks new keys"
    for code in ("ai_disabled", "ai_rate_limited", "ai_sdk_missing", "ai_provider_error", "ai_error"):
        assert f"ai.error.{code}" in ja


def test_requirements_ai_is_optional():
    """Provider SDKs must not be hard dependencies of the backend."""
    hard = (ROOT / "backend/requirements.txt").read_text("utf-8").lower()
    for pkg in ("anthropic", "openai", "google-genai", "google-generativeai"):
        assert pkg not in hard, pkg
    optional = (ROOT / "backend/requirements-ai.txt").read_text("utf-8").lower()
    assert "anthropic" in optional and "openai" in optional and "google-genai" in optional
