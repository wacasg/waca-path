# Changelog

All notable changes to WACA path are recorded here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Session timeline on the per-user journey page (no AI).** Each session
  is now shown the way snowprism's user explorer shows it: one row per
  `page_view` with the events that fired while that page was open
  (`scroll`, `click`, `form_submit`, key events, ...) nested underneath,
  the dwell time until the next page, and orphan events for a session that
  has no page view at all. The JSON API keeps every v0.3.0 field and adds
  `sessions[].timeline` and `sessions[].inference_notes`. The v0.3.0 step
  table is still there, collapsed under "Show all events as a table".
- **Inference notes per session (no AI).** Up to three heuristic reading
  aids per session, ported from snowprism's `_build_session_inference_notes`
  and extended: organic-search / paid entry, task-style landing page,
  purchase / checkout / cart reached, key event after N pages, bounce, deep
  session, long dwell on one page, returned to the entry page, scrolled,
  cross-domain hop, quick mobile visit. Every note is a fixed rule with an
  `i18n` code (`journey.note.<code>`), hedged wording, and degrades
  gracefully when `traffic_*` / `device_category` are absent (the sample).
- **Journey context (`タイムライン情報`), no AI.** `GET
  /api/users/{user_pseudo_id}/journey/context` and a card on the journey
  page: data window, pauses between sessions with 7+ day gaps flagged,
  visited pages with counts, journey outcome (`purchase` / `checkout_abandon`
  / `cart_abandon` / `key_event` / `view_only`), the tenant's key pages not
  yet reached (`site.key_paths` in the tenant config - previously
  documented as reserved, now read), device / traffic summary and a
  one-line digest per session. Ported from the non-AI half of snowprism's
  `collect_user_context`.
- **Optional AI summary.** `POST /api/users/{user_pseudo_id}/journey/summary`
  and a **Summarize with AI** button on the journey page, which exists only
  when `WACA_PATH_AI_PROVIDER` (`anthropic` / `openai` / `google`) *and* the
  matching key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`)
  are set. Without them the endpoint answers **409** with a translated
  message and the page shows nothing AI-related. The model receives only
  the journey context and timeline digest (paths, timestamps, event names)
  and returns a one-paragraph persona-style summary, a 3-5 stage customer
  journey sketch (stage, evidence pages, confidence) and up to three draft
  ideas, as JSON with `provider` / `model`. Provider SDKs are **not** in
  `backend/requirements.txt`; they live in the optional
  `backend/requirements-ai.txt` and are imported lazily. Defaults:
  `claude-sonnet-4-6`, `gpt-5-mini`, `gemini-2.5-flash`
  (`WACA_PATH_AI_MODEL` overrides), 1,200 output tokens, and a per-process
  limit of 10 calls per minute (`WACA_PATH_AI_RATE_LIMIT_PER_MIN`). Keys
  are never logged.
- `tests/test_journey_timeline_ai.py`: attached-event grouping, inference
  notes, gap analysis, expected next pages, the AI gate (env unset -> 409
  and no button), a fake provider for the summary endpoint, i18n parity.
- App version is now `0.4.0`.

## [0.3.0] - 2026-09-13

### Added

- **Per-user journey (`ユーザー別経路`), rule-based and LLM-free.**
  `GET /api/users/{user_pseudo_id}/journey` returns the user's
  `micro_user_table` events grouped into sessions (by
  `pseudonymous_session_id`, falling back to `user_pseudo_id` +
  `ga_session_id`), each session as an ordered step list (event, page title,
  page path, engagement, seconds since the previous step, key-event flag),
  the collapsed page-path sequence with entry / exit page, a user-level
  summary (sessions, page views, distinct pages, most common `A -> B`
  transition, first touch / last active) and short factual observations in
  every UI language. The same view is rendered at
  `/ui/users/{user_pseudo_id}/journey`, linked from the user detail page.
  No AI provider or API key is involved: every observation is a fixed rule.
- **Common journeys (`よく通る経路`) across all users.** `GET /api/journeys/top`
  and `/ui/journeys/` (linked from the header) aggregate every session's
  `page_view` rows in a period into the most common page transitions, entry
  pages, exit pages and whole session routes (consecutive duplicates
  collapsed, first 8 steps), each counted by sessions with its share. The
  default period is the last 30 days; if the table has no data that recent
  the window moves back to end on the newest `event_date` and the page says
  so. The BigQuery scan is partition-filtered on `event_date`, returns one
  row per session and is capped (10,000 sessions by default, announced when
  hit); the pattern counting happens in Python and is unit-tested.
- Optional columns (`pseudonymous_session_id`, `session_event_no`,
  `seconds_from_prev_event`, `traffic_*`, `device_category`, `browser`) are
  probed at runtime, so both features work on the reduced sample dataset as
  well as on a full WACA core output.
- App version is now `0.3.0`.

### Fixed

- **`POST /api/agent/site-audit` returned 400 against every real WACA core
  output.** The query selected `clean_page_path`, which the public WACA core
  `micro_user_table` does not have (BigQuery: `Unrecognized name:
  clean_page_path`). The column was never used by the response, which is built
  from `page_location`, so it is no longer selected. Found on the first
  real-data call after installing WACA core and WACA path on a fresh Google
  Cloud project (cvrlabo.com, 2026-09-12). INSTALL.md's required-column table
  no longer lists it, and notes that `is_key_event` may be `INT64` (WACA core)
  or `BOOL` (sample).
- **INSTALL.md section 7 deployed a Cloud Run service that could not start.**
  It deployed `--source=.` from `backend/`, but the app resolves
  `tenant_config/` and `backend/templates/` relative to the repository root, so
  every tenant lookup and UI render failed inside the container. The repository
  now ships a root `Procfile`, `requirements.txt` (which includes
  `backend/requirements.txt`), `.python-version` (3.13: the buildpack default,
  3.14, has no `pydantic-core` wheel and the Rust build fails; the builder
  offers 3.13 and 3.14 only) and
  `.gcloudignore`, and the guide deploys from
  the repository root with a dedicated runtime service account and shows how to
  call the private service with an identity token. The check uses
  `/openapi.json`: on Cloud Run a request to exactly `/healthz` is answered
  404 by Google's front end and never reaches the container.
- **`scripts/install_smoke_check.sh` failed after following INSTALL.md
  section 4.** Starting the backend writes `backend/__pycache__`, which the
  cache-directory guard then reported as an error. The guard now checks the Git
  index (committed files) when run inside a repository, and prunes `.venv`
  otherwise.

## [0.2.0] - 2026-08-21

### Added

- **Read-only admin UI** under `/ui/`, backed by the same `micro_user_table`
  that the site-audit endpoint reads.
  - `/ui/users/` - users list with period, free-text and preset filters, sorting
    on any listed column, and keyset (cursor) paging.
  - `/ui/users/{user_pseudo_id}` - profile, totals and per-session detail.
  - `/ui/users/export.csv` - CSV export of the current filtered result
    (up to 10,000 rows, UTF-8 with BOM for Excel).
- **Saved segments.** Name the current filter set, recall it later, and export or
  import the list as JSON. Stored in the browser's `localStorage`, so the tool
  stays read-only and needs no extra storage backend.
- **Japanese / English UI** with a language switch in the header. Resolution
  order: `?lang=` > cookie > `Accept-Language` > `ui.default_lang` > Japanese.
  Language links preserve the current filters. Catalogues are plain JSON under
  `backend/i18n/`; the test suite fails if a key is missing from a translation.
- **Tenant-declared custom columns** via `ui.custom_columns` in the tenant
  config. They appear in the list, the detail page and the CSV. WACA path itself
  ships no organisation-specific columns.
- **Clarity recording support.** When `micro_user_table` carries
  `clarity_play_url`, each session row shows every recording attached to it,
  together with the Clarity-side user and session IDs (click to copy).
- `tests/` with unit coverage for i18n, filters, cursor paging, SQL generation,
  CSV export and Clarity parsing.
- `CHANGELOG.md` (this file).

### Changed

- `backend/main.py` now composes routers instead of being a single module.
  `/healthz` and `/api/agent/site-audit` keep the same URLs, request bodies and
  responses as v0.1.0.
- Tenant loading, dataset resolution and identifier validation moved to
  `backend/services/config.py` and are shared by both halves of the app.
- The sample dataset now contains three users across five sessions, including
  Clarity recordings and an example custom dimension, so the UI is usable with
  the sample alone.

### Notes on correctness

Three behaviours here exist because the naive version of each was wrong:

- **Recordings are never collapsed to one per session.** Clarity does not split
  sessions the way GA4 does, so one recording legitimately maps onto several
  GA4 sessions. Picking one made recordings look like they belonged to the wrong
  session. Every recording is shown, and one shared with other sessions in the
  displayed period is flagged.
- **CSV cells beginning `=`, `+`, `-`, `@`, tab or CR are prefixed with an
  apostrophe** so a spreadsheet cannot execute them as formulas (CWE-1236).
  Free-text values, including custom dimensions, reach this export.
- **Truncation is always announced**, in the UI and inside the file. A silently
  shortened export reads as a complete one.

### Compatibility

- Existing v0.1.0 installs can upgrade in place. The only new runtime dependency
  is `jinja2`; run `pip install -r backend/requirements.txt` again.
- Optional columns (`clarity_play_url`, `browser`, `region`, `country`,
  `device_category`) are probed at runtime and omitted when the install does not
  have them, so a reduced `micro_user_table` still renders.

## [0.1.0] - 2026-06-28

### Added

- Minimal public backend with `/healthz` and `POST /api/agent/site-audit`.
- Anonymous WACA core output sample and install smoke check.
- Apache License 2.0, contribution guide and security policy.
