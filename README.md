# WACA path

WACA path is an open-source backend that reads WACA core output and creates a
first draft of site-audit observations for one user journey.

WACA path does not modify production websites. It reads analysis-ready BigQuery
tables in your own Google Cloud project and returns observations that a human
analyst can review.

## Prerequisites: WACA core

WACA path reads the analysis-ready tables that **WACA core** produces — in
particular `micro_user_table`. WACA path does not generate these tables itself.
Install and run WACA core first so that its output dataset exists in your Google
Cloud project:

- WACA core: https://github.com/wacasg/waca-core

For a local rehearsal you can instead load the anonymous sample in
`samples/bigquery/create_anonymous_waca_core_output_sample.sql`, which reproduces
the shape of WACA core output without a full WACA core install.

## What Is Included

This repository is the minimal public install package for WACA path.

| Path | Purpose |
|---|---|
| `backend/main.py` | FastAPI app: `/healthz`, `/api/agent/site-audit`, and the admin UI. |
| `backend/routers/ui.py` | Read-only admin UI: users list, user detail, CSV export. |
| `backend/services/` | BigQuery queries, filtering and paging, CSV export. |
| `backend/i18n/` | UI language catalogues (`ja.json`, `en.json`). |
| `backend/templates/` | Jinja2 templates for the admin UI. |
| `backend/requirements.txt` | Python dependencies. |
| `tenant_config/example.yaml` | Example tenant configuration. |
| `tests/` | Unit tests (`python -m pytest -q`). |
| `samples/bigquery/create_anonymous_waca_core_output_sample.sql` | Anonymous WACA core output sample. |
| `scripts/create_sample_dataset.sh` | Helper script to create the sample dataset. |
| `scripts/install_smoke_check.sh` | Static and optional local backend checks. |
| `.env.example` | Configuration template. |
| `INSTALL.md` | Step-by-step installation guide. |
| `CONTRIBUTING.md` | How to contribute (DCO, Apache License 2.0). |
| `SECURITY.md` | How to report security concerns privately. |
| `LICENSE` | Apache License 2.0. |

## Quick Start

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
bash scripts/install_smoke_check.sh
```

To run the backend locally:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080/healthz
http://127.0.0.1:8080/docs
http://127.0.0.1:8080/ui/users/
```

## Admin UI

`/ui/users/` is a read-only view over `micro_user_table`. It never writes.

* **Users list** - filter by period, free text, presets (key events, identified,
  repeat visitors, has Clarity recording) and sort by any listed column.
  Paging uses a keyset cursor, so rows are never duplicated or skipped.
* **Saved segments** - name the current filter set and recall it later. Segments
  live in the browser's `localStorage`, so this stays a read-only tool; use
  **Export** / **Import** to move them between machines or share them.
* **CSV export** - the current filtered result, up to 10,000 rows, UTF-8 with a
  BOM so Excel opens it correctly. If the limit is reached the UI says so and a
  note is written into the file; the export is never truncated silently.
* **User detail** - profile, totals, and one row per session.

### Language

The UI ships in English and Japanese. The language is chosen in this order:

1. `?lang=en` / `?lang=ja` (the switch in the header; the choice is remembered
   in a cookie)
2. the `waca_path_lang` cookie
3. the browser's `Accept-Language`
4. `ui.default_lang` in the tenant config
5. Japanese

To add a language, copy `backend/i18n/ja.json`, translate the values, and add the
code to `SUPPORTED_LANGS` in `backend/i18n/__init__.py`. `python -m pytest -q`
fails if a catalogue is missing a key.

### Custom columns

WACA path ships no organisation-specific columns. Declare the GA4 custom
dimensions your own `micro_user_table` has, and they appear in the list, the
detail page and the CSV:

```yaml
ui:
  custom_columns:
    - column: membership_type
      label: "Membership"
```

A declared column that does not exist in the table is skipped rather than
breaking the page.

### Clarity recordings

If `micro_user_table` carries a `clarity_play_url`, each session row shows the
recording links for that session plus the Clarity-side user and session IDs.

**Clarity and GA4 do not agree on where a session ends.** GA4 starts a new
session after 30 minutes of inactivity and at midnight; Clarity does not. One
recording therefore routinely maps onto several GA4 sessions. WACA path keeps
every recording rather than picking one, and flags a recording that is shared
with other sessions in the displayed period, so a recording is never silently
presented as belonging to a single row. Copy the IDs to line the two systems up
inside Clarity.

The "has Clarity recording" filter is evaluated **per user**: a matching user can
still have sessions with no recording.

### Not included

This repository is the public install package. LLM-generated personas and
journey maps, external knowledge-base integrations, and commerce dashboards are
not part of it.

## Legal Notice

The software code in this repository is licensed under Apache License 2.0. See
[LICENSE](LICENSE).

`WACA path` is a project name of WACA. Japan trademark application no.
商願2026-57846 was filed in 2026, and registration is pending. Other
jurisdictions may not yet be filed. Do not use WACA names in a way that implies
official certification, endorsement, or compatibility approval unless WACA has
granted permission.

This repository may include technology that is the subject of pending patent
applications or future patent applications. The Apache License 2.0 patent grant
applies as stated in the license.

## 日本語

WACA path は、WACA core の出力を読み、一人の user journey に対する site audit
観察メモの draft を作るための open-source backend です。

WACA path は本番 website を自動変更しません。利用者自身の Google Cloud project
にある BigQuery table を読み、人間の analyst が確認するための観察結果を返します。

### 前提: WACA core

WACA path は **WACA core** が生成する分析用 table（特に `micro_user_table`）を
読み取ります。これらの table を WACA path 自身は作成しません。先に WACA core を
install して実行し、出力 dataset を自分の Google Cloud project に用意してください。

- WACA core: https://github.com/wacasg/waca-core

ローカルでの動作確認だけであれば、
`samples/bigquery/create_anonymous_waca_core_output_sample.sql` の匿名 sample を
読み込むことで、WACA core を完全に install しなくても WACA core 出力と同じ形の
table を作成できます。

### 含まれるファイル

この repository は、Git から install して WACA path を動かすための最小構成です。

| Path | 役割 |
|---|---|
| `backend/main.py` | `/healthz` と `/api/agent/site-audit` を持つ FastAPI backend。 |
| `backend/requirements.txt` | Python dependencies。 |
| `tenant_config/example.yaml` | tenant 設定例。 |
| `samples/bigquery/create_anonymous_waca_core_output_sample.sql` | 匿名 WACA core output sample。 |
| `scripts/create_sample_dataset.sh` | sample dataset 作成 helper。 |
| `scripts/install_smoke_check.sh` | static check と optional local backend check。 |
| `.env.example` | 設定 template。 |
| `INSTALL.md` | install 手順書。 |
| `CONTRIBUTING.md` | 貢献方法（DCO、Apache License 2.0）。 |
| `SECURITY.md` | セキュリティ報告の窓口（非公開）。 |
| `LICENSE` | Apache License 2.0。 |

### まず試す

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
bash scripts/install_smoke_check.sh
```

詳しい手順は [INSTALL.md](INSTALL.md) を参照してください。

### 法的表示

この repository の software code は Apache License 2.0 で提供されます。
ライセンス本文は [LICENSE](LICENSE) にあります。

`WACA path` は WACA の project name です。日本では商願2026-57846として
2026年に商標出願済みで、登録は審査中です。WACA から許可を得ていない場合、
WACA による認定、推奨、互換性保証があるように見える使い方はしないでください。
