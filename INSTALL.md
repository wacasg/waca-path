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

Call it with an identity token:

```bash
URL="$(gcloud run services describe waca-path-backend --region="${WACA_PATH_LOCATION:-asia-northeast1}" --format='value(status.url)')"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${URL}/healthz"
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

identity token を付けて呼び出します。

```bash
URL="$(gcloud run services describe waca-path-backend --region="${WACA_PATH_LOCATION:-asia-northeast1}" --format='value(status.url)')"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${URL}/healthz"
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

### Run the tests

```bash
python -m pytest -q
```

The suite checks, among other things, that every UI string is translated in
every language, that CSV cells cannot be executed as spreadsheet formulas, and
that cursor paging does not drop or repeat rows.
