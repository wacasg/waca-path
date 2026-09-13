# Install WACA path

This guide installs WACA path from Git and runs the minimal public backend.

WACA path reads WACA core output tables in your own BigQuery dataset. Start with
the anonymous sample dataset first, then switch to your own WACA core output.

## 1. Clone and Configure

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
```

Edit `.env`:

| Variable | Meaning |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID. |
| `WACA_CORE_DATASET` | Dataset containing WACA core output tables. |
| `SAMPLE_WACA_CORE_DATASET` | Anonymous sample dataset name for rehearsal. |
| `DEFAULT_TENANT_ID` | Tenant config to load from `tenant_config/<id>.yaml`. |

## 2. Static Smoke Check

```bash
bash scripts/install_smoke_check.sh
```

This checks the minimal public install file set. It does not connect to
BigQuery unless `PROJECT_ID` or `GOOGLE_CLOUD_PROJECT` is set.

## 3. Create Anonymous WACA core Output Sample

```bash
PROJECT_ID=your-gcp-project-id bash scripts/create_sample_dataset.sh
```

By default this creates:

```text
your-gcp-project-id.waca_path_sample_core.micro_user_table
your-gcp-project-id.waca_path_sample_core.micro_items_table
```

## 4. Run the Backend Locally

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8080
```

Check the health endpoint:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"status":"ok","service":"waca-path-backend","env":"local"}
```

Open the API reference:

```text
http://127.0.0.1:8080/docs
```

## 5. Run Site Audit on the Sample Dataset

Set environment variables before starting the backend:

```bash
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
export WACA_CORE_DATASET=waca_path_sample_core
export DEFAULT_TENANT_ID=example
```

Then call:

```bash
curl -X POST http://127.0.0.1:8080/api/agent/site-audit \
  -H 'Content-Type: application/json' \
  -d '{"user_pseudo_id":"anon_user_001","tenant_id":"example"}'
```

The response contains observed pages, simple observations, and draft improvement
ideas. It is a starting point for human review, not an automatic website change.

## 6. Switch to Your WACA core Output

After the sample works, change:

```bash
export WACA_CORE_DATASET=waca_core_output
```

Use a `user_pseudo_id` that exists in your `micro_user_table`.

## 7. Deploy to Cloud Run (optional)

The backend is a FastAPI app. To run it on Cloud Run it must listen on the port
Cloud Run injects as `$PORT`, and you must put access control in front of it —
the API has no built-in authentication.

The backend resolves `tenant_config/` and `backend/templates/` relative to the
repository root, so deploy from the repository root, not from `backend/`. The
repository ships the three files Cloud Run source deploy needs at the root:
`Procfile` (starts uvicorn inside `backend/`), `requirements.txt` (includes
`backend/requirements.txt`), `.python-version` (pins the buildpack to Python
3.13; the pinned `pydantic-core` has no wheel for the buildpack default, 3.14,
and the from-source build fails. The Cloud Run builder offers 3.13 and 3.14 only), and `.gcloudignore` (keeps `.venv`, tests and
caches out of the upload).

Deploy from the repository root with a dedicated runtime service account and
keep the service private:

```bash
SA="waca-path-backend@your-gcp-project-id.iam.gserviceaccount.com"
gcloud run deploy waca-path-backend \
  --source=. \
  --region="${WACA_PATH_LOCATION:-asia-northeast1}" \
  --no-allow-unauthenticated \
  --service-account="${SA}" \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=your-gcp-project-id,WACA_CORE_DATASET=waca_core_output,DEFAULT_TENANT_ID=example
```

Call it with an identity token. Use `/openapi.json` (or `/docs`) for the
check: on Cloud Run a request to exactly `/healthz` is answered with a 404 by
Google's front end before it reaches the container, while every other path,
including `/healthz/`, is forwarded normally.

```bash
URL="$(gcloud run services describe waca-path-backend --region="${WACA_PATH_LOCATION:-asia-northeast1}" --format='value(status.url)')"
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${URL}/openapi.json"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" -X POST "${URL}/api/agent/site-audit" \
  -H 'Content-Type: application/json' -d '{"user_pseudo_id":"anon_user_001"}'
```

`--no-allow-unauthenticated` blocks anonymous callers. Grant invokers the Cloud
Run Invoker role explicitly, or front the service with IAP or internal-only
ingress. The runtime service account needs `roles/bigquery.dataViewer` on the
dataset and `roles/bigquery.jobUser` on the project.

## Reference

### Required columns in `micro_user_table`

When you switch to your own WACA core output, the table must provide these
columns (the backend selects them):

| Column | Type | Note |
|---|---|---|
| `event_date` | DATE | |
| `event_timestamp` | DATETIME | |
| `event_name` | STRING | |
| `user_pseudo_id` | STRING | filter key |
| `user_id` | STRING | nullable |
| `ga_session_id` | INT64 | |
| `page_location` | STRING | |
| `page_title` | STRING | |
| `engagement_time_msec` | INT64 | |
| `device_category` | STRING | |
| `browser` | STRING | |
| `country` | STRING | |
| `region` | STRING | |
| `is_key_event` | INT64 or BOOL | WACA core writes `INT64` (0/1); the sample uses `BOOL`. Truthy values count as key events. |

### BigQuery authentication and required IAM

```bash
gcloud auth application-default login
```

The runtime identity needs at minimum:

- `roles/bigquery.dataViewer` on the dataset that holds `micro_user_table`
- `roles/bigquery.jobUser` on the project

### Example response (`POST /api/agent/site-audit`)

```json
{
  "tenant_id": "example",
  "user_pseudo_id": "anon_user_001",
  "source": "your-gcp-project-id.waca_path_sample_core.micro_user_table",
  "rows_found": 3,
  "pages": [
    {"event_name": "page_view", "page_title": "Home", "page_location": "https://example.test/", "event_timestamp": "2026-01-01 09:00:00", "engagement_time_msec": 1200, "is_key_event": false},
    {"event_name": "page_view", "page_title": "Pricing", "page_location": "https://example.test/pricing", "event_timestamp": "2026-01-01 09:01:30", "engagement_time_msec": 4500, "is_key_event": false},
    {"event_name": "purchase", "page_title": "Thank you", "page_location": "https://example.test/checkout/thank-you", "event_timestamp": "2026-01-01 09:05:00", "engagement_time_msec": 800, "is_key_event": true}
  ],
  "observations": [
    "Found 3 rows for the selected user.",
    "Found 1 key event row(s).",
    "First observed page: https://example.test/",
    "Last observed page: https://example.test/checkout/thank-you"
  ],
  "draft_improvements": [
    "Review the first and last observed pages for friction.",
    "Compare high-engagement pages with key-event pages.",
    "Use this draft as an analyst starting point, not an automatic production change."
  ]
}
```

Each `pages` entry is an object (`event_name`, `page_title`, `page_location`,
`event_timestamp`, `engagement_time_msec`, `is_key_event`), not a bare path
string.

### Configuration precedence

Environment variables override `tenant_config/<id>.yaml`. If both set the
project or dataset, the environment variable wins.

### Constraints

- The API has no built-in authentication and is intended for local use. Before
  deploying it (for example to Cloud Run), put access control in front
  (IAP, ID token, or internal-only ingress). It returns page paths, device, and
  geography for a given `user_pseudo_id`.
- Site audit processes one `user_pseudo_id` per request.

## 日本語

この手順は、Git から WACA path を取得し、最小 public backend を起動するためのものです。

WACA path は、利用者自身の BigQuery dataset にある WACA core output table を読みます。
最初は匿名 sample dataset で動作確認し、その後に自分の WACA core output に切り替えてください。

### 1. clone して設定する

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
```

`.env` を編集します。

| 変数 | 意味 |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | 自分の Google Cloud project ID。 |
| `WACA_CORE_DATASET` | WACA core output table がある dataset。 |
| `SAMPLE_WACA_CORE_DATASET` | rehearsal 用の匿名 sample dataset 名。 |
| `DEFAULT_TENANT_ID` | `tenant_config/<id>.yaml` から読む tenant 設定。 |

### 2. static smoke check

```bash
bash scripts/install_smoke_check.sh
```

これは公開 install 用の最小ファイル構成を確認します。`PROJECT_ID` または
`GOOGLE_CLOUD_PROJECT` を指定しない限り、BigQuery には接続しません。

### 3. 匿名 WACA core output sample を作成する

```bash
PROJECT_ID=your-gcp-project-id bash scripts/create_sample_dataset.sh
```

標準では次の table が作成されます。

```text
your-gcp-project-id.waca_path_sample_core.micro_user_table
your-gcp-project-id.waca_path_sample_core.micro_items_table
```

### 4. backend を local 起動する

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8080
```

health endpoint を確認します。

```bash
curl http://127.0.0.1:8080/healthz
```

期待される response:

```json
{"status":"ok","service":"waca-path-backend","env":"local"}
```

API reference は次で開けます。

```text
http://127.0.0.1:8080/docs
```

### 5. sample dataset に対して site audit を実行する

backend 起動前に environment variables を設定します。

```bash
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
export WACA_CORE_DATASET=waca_path_sample_core
export DEFAULT_TENANT_ID=example
```

次を実行します。

```bash
curl -X POST http://127.0.0.1:8080/api/agent/site-audit \
  -H 'Content-Type: application/json' \
  -d '{"user_pseudo_id":"anon_user_001","tenant_id":"example"}'
```

response には、観察された page、簡単な observation、改善案 draft が含まれます。
これは人間が確認するための starting point であり、website を自動変更するものではありません。

### 6. 自分の WACA core output に切り替える

sample で動作確認できたら、次を自分の output dataset に変更します。

```bash
export WACA_CORE_DATASET=waca_core_output
```

`micro_user_table` に存在する `user_pseudo_id` を指定してください。

### 7. Cloud Run にデプロイする（任意）

backend は FastAPI app です。Cloud Run で動かす場合、Cloud Run が注入する `$PORT`
で listen する必要があり、かつ前段にアクセス制御を置く必要があります（API は認証
機構を持ちません）。

backend は `tenant_config/` と `backend/templates/` を repository root からの相対
path で解決するため、`backend/` ではなく repository root からデプロイします。Cloud Run の
source deploy に必要な 4 file（`Procfile`、`requirements.txt`、`.python-version`、
`.gcloudignore`）は repository root に同梱しています。`.python-version` は buildpack の
Python を 3.13 に固定します（既定の 3.14 では pinned `pydantic-core` の wheel が無く
source build に失敗します。Cloud Run builder が提供するのは 3.13 と 3.14 のみです）。

専用の runtime service account を指定し、非公開のままデプロイします。

```bash
SA="waca-path-backend@your-gcp-project-id.iam.gserviceaccount.com"
gcloud run deploy waca-path-backend \
  --source=. \
  --region="${WACA_PATH_LOCATION:-asia-northeast1}" \
  --no-allow-unauthenticated \
  --service-account="${SA}" \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=your-gcp-project-id,WACA_CORE_DATASET=waca_core_output,DEFAULT_TENANT_ID=example
```

identity token を付けて呼び出します。疎通確認には `/openapi.json`（または `/docs`）を
使ってください。Cloud Run では `/healthz` ちょうどへの request だけが Google の front end で
404 になり container に届きません（`/healthz/` を含む他の path は通常どおり転送されます）。

```bash
URL="$(gcloud run services describe waca-path-backend --region="${WACA_PATH_LOCATION:-asia-northeast1}" --format='value(status.url)')"
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${URL}/openapi.json"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" -X POST "${URL}/api/agent/site-audit" \
  -H 'Content-Type: application/json' -d '{"user_pseudo_id":"anon_user_001"}'
```

`--no-allow-unauthenticated` で匿名アクセスを遮断します。呼び出し元には Cloud Run
Invoker 権限を明示的に付与するか、IAP・内部限定 ingress を前段に置いてください。
runtime service account には、dataset への `roles/bigquery.dataViewer` と project への
`roles/bigquery.jobUser` が必要です。

### 補足（Reference）

#### `micro_user_table` の必須カラム

自分の WACA core output に切り替える場合、table は次のカラムを備えている必要が
あります（backend がこれらを SELECT します）。

| カラム | 型 | 備考 |
|---|---|---|
| `event_date` | DATE | |
| `event_timestamp` | DATETIME | |
| `event_name` | STRING | |
| `user_pseudo_id` | STRING | 絞り込みキー |
| `user_id` | STRING | NULL 可 |
| `ga_session_id` | INT64 | |
| `page_location` | STRING | |
| `page_title` | STRING | |
| `engagement_time_msec` | INT64 | |
| `device_category` | STRING | |
| `browser` | STRING | |
| `country` | STRING | |
| `region` | STRING | |
| `is_key_event` | INT64 or BOOL | WACA core writes `INT64` (0/1); the sample uses `BOOL`. Truthy values count as key events. |

#### BigQuery 認証と必要な IAM

```bash
gcloud auth application-default login
```

実行 identity には最低限、次の権限が必要です。

- `micro_user_table` を含む dataset に対する `roles/bigquery.dataViewer`
- project に対する `roles/bigquery.jobUser`

#### response 例（`POST /api/agent/site-audit`）

```json
{
  "tenant_id": "example",
  "user_pseudo_id": "anon_user_001",
  "source": "your-gcp-project-id.waca_path_sample_core.micro_user_table",
  "rows_found": 3,
  "pages": [
    {"event_name": "page_view", "page_title": "Home", "page_location": "https://example.test/", "event_timestamp": "2026-01-01 09:00:00", "engagement_time_msec": 1200, "is_key_event": false},
    {"event_name": "page_view", "page_title": "Pricing", "page_location": "https://example.test/pricing", "event_timestamp": "2026-01-01 09:01:30", "engagement_time_msec": 4500, "is_key_event": false},
    {"event_name": "purchase", "page_title": "Thank you", "page_location": "https://example.test/checkout/thank-you", "event_timestamp": "2026-01-01 09:05:00", "engagement_time_msec": 800, "is_key_event": true}
  ],
  "observations": [
    "Found 3 rows for the selected user.",
    "Found 1 key event row(s).",
    "First observed page: https://example.test/",
    "Last observed page: https://example.test/checkout/thank-you"
  ],
  "draft_improvements": [
    "Review the first and last observed pages for friction.",
    "Compare high-engagement pages with key-event pages.",
    "Use this draft as an analyst starting point, not an automatic production change."
  ]
}
```

`pages` の各要素は path 文字列ではなく object（`event_name` / `page_title` /
`page_location` / `event_timestamp` / `engagement_time_msec` / `is_key_event`）です。

#### 設定の優先順位

environment variables は `tenant_config/<id>.yaml` を上書きします。project や dataset
を両方で設定した場合は environment variable が優先されます。

#### 制約

- この API は認証機構を持たず、local 利用を想定しています。Cloud Run などにデプロイ
  する前に、必ず前段でアクセス制御（IAP、ID token、内部限定 ingress など）を設けて
  ください。指定された `user_pseudo_id` の page path・device・地域を返します。
- site audit は 1 リクエストにつき 1 つの `user_pseudo_id` を処理します。

## 8. Open the admin UI (v0.2.0)

With the backend running, open:

```text
http://127.0.0.1:8080/ui/users/
```

If you loaded the sample dataset, set the period to cover 2026-05-01 to
2026-05-04 - the sample journeys sit in that range - and you should see three
users, one of which has Clarity recordings attached.

The UI is read-only. It issues `SELECT` queries against `micro_user_table` in
the dataset named by `WACA_CORE_DATASET` and writes nothing.

### Switch language

Use the `日本語` / `English` links in the header, or add `?lang=en` /
`?lang=ja`. The choice is stored in the `waca_path_lang` cookie. Without either,
the browser's `Accept-Language` is used, then `ui.default_lang` from the tenant
config, then Japanese.

### Export a CSV

Filter the list, then press **Export CSV**. The file covers the current filter
set, up to 10,000 rows, and is UTF-8 with a BOM so Excel opens Japanese text
correctly. If the row limit is reached, the UI says so and a note is written at
the end of the file.

### Add your own columns

Declare the GA4 custom dimensions your `micro_user_table` actually has:

```yaml
# tenant_config/<your-tenant>.yaml
ui:
  default_lang: "en"
  custom_columns:
    - column: membership_type
      label: "Membership"
```

They then appear in the users list, the user detail page and the CSV export. A
column that is not present in the table is skipped, so a stale entry cannot
break the page.

### Per-user journey (no LLM)

Every user detail page links to **View journey** (`経路を見る`), at
`/ui/users/<user_pseudo_id>/journey`. It lists the user's sessions in time
order, each as a step list (time, seconds since the previous step, event, page
title and path, engagement, key-event badge), the collapsed page path with the
entry and exit page, a summary (sessions, page views, distinct pages, the most
common `A -> B` transition) and short rule-based observations such as
"no key events" or "entered and exited on the same page". No AI provider is
involved, so it works without any API key.

The same data is available as JSON:

```bash
curl "http://127.0.0.1:8080/api/users/anon_user_001/journey?tenant_id=example&max_rows=500"
```

```json
{
  "tenant_id": "example",
  "user_pseudo_id": "anon_user_001",
  "source": "your-gcp-project-id.waca_path_sample_core.micro_user_table",
  "summary": {
    "sessions_count": 3, "events_count": 7, "total_pv": 4, "key_event_count": 1,
    "distinct_paths": 6,
    "most_common_transition": {"from": "/", "to": "/pricing", "count": 1},
    "first_touch": "2026-05-01T10:00:00", "last_active": "2026-05-02T00:50:00",
    "truncated": false
  },
  "sessions": [
    {
      "session_no": 1, "session_start": "2026-05-01T10:00:00", "duration_sec": 240.0,
      "entry_path": "/", "exit_path": "/contact",
      "page_path_sequence": ["/", "/pricing", "/contact"],
      "steps": [
        {"step_no": 1, "event_name": "session_start", "page_path": "/", "seconds_from_prev": null, "is_key_event": false},
        {"step_no": 2, "event_name": "page_view", "page_path": "/pricing", "engagement_time_msec": 4500, "seconds_from_prev": 60.0, "is_key_event": false}
      ]
    }
  ],
  "observations": [
    {"code": "key_events", "params": {"count": 1, "sessions": 1},
     "text": {"ja": "キーイベントが 1 件あります（1 セッション）。", "en": "1 key event(s) across 1 session(s)."}}
  ]
}
```

`max_rows` (default 500, at most 5,000) caps the events read for the user;
`summary.truncated` is `true` when the cap was hit.

### Common journeys across all users (no LLM)

**Journeys** (`経路`) in the header opens `/ui/journeys/`: for a period
(default the last 30 days) it shows the most common page transitions, entry
pages, exit pages and whole session routes, each with the number of sessions
and its share. Only `page_view` rows are read, the scan is filtered on
`event_date`, and at most 10,000 sessions (newest first) are counted; the page
says so when that cap is reached. If the table has no data in the last 30
days, the period moves back to end on the newest `event_date` and the page says
so, rather than showing an empty result.

```bash
curl "http://127.0.0.1:8080/api/journeys/top?tenant_id=example&date_from=2026-05-01&date_to=2026-05-04&limit=10"
```

The response carries `period`, `totals` (`sessions`, `users`, `page_views`),
`sessions_analyzed`, `truncated`, and the four lists `transitions`
(`from`, `to`, `sessions`, `share`), `entries`, `exits` (`path`, `sessions`,
`share`) and `sequences` (`path[]`, `steps`, `cut`, `sessions`, `share`).

### Run the tests

```bash
python -m pytest -q
```

The suite checks, among other things, that every UI string is translated in
every language, that CSV cells cannot be executed as spreadsheet formulas, and
that cursor paging does not drop or repeat rows.

### 8b. ユーザー別経路と「よく通る経路」（LLM 不使用）

ユーザー詳細画面の **経路を見る** から `/ui/users/<user_pseudo_id>/journey` を
開けます。そのユーザーのセッションを時系列に並べ、各セッションをステップ一覧
（時刻、前ステップからの秒数、イベント、ページタイトルとパス、エンゲージメント、
キーイベント）で表示します。同じページの連続をまとめた経路と入口／出口ページ、
サマリー（セッション数、PV、ユニークページ数、最頻出の `A -> B` 遷移）、
「キーイベント無し」「入口と出口が同じページ」などのルールベースの所見も
併せて表示します。AI プロバイダは使わないため、API キーは不要です。

同じ内容は JSON でも取得できます。

```bash
curl "http://127.0.0.1:8080/api/users/anon_user_001/journey?tenant_id=example&max_rows=500"
```

response には `summary`（`sessions_count`, `total_pv`, `distinct_paths`,
`most_common_transition`, `first_touch`, `last_active`, `truncated`）、
`sessions[]`（`session_start`, `duration_sec`, `entry_path`, `exit_path`,
`page_path_sequence`, `steps[]`）、`observations[]`（`code`, `params`,
`text.ja` / `text.en`）が含まれます。`max_rows`（既定 500、最大 5,000）を
超えた場合は `summary.truncated` が `true` になります。

ヘッダーの **経路** から `/ui/journeys/` を開くと、期間内（既定は直近 30 日）の
全セッションを集計した「よく通る遷移」「入口ページ」「出口ページ」
「よく通る経路パターン」を、セッション数と割合つきで表示します。読むのは
`page_view` 行だけで、`event_date` で絞り込み、新しい順に最大 10,000 セッション
までを対象にします（上限に達した場合は画面に表示します）。直近 30 日に
データが無い場合は、データの最終日までの 30 日間に自動でずらし、その旨を
表示します。

```bash
curl "http://127.0.0.1:8080/api/journeys/top?tenant_id=example&date_from=2026-05-01&date_to=2026-05-04&limit=10"
```
