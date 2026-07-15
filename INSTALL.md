# Install WACA path

This guide installs WACA path from Git and runs the minimal public backend.

WACA path reads WACA core output tables in your own BigQuery dataset. Start with
the anonymous sample dataset first, then switch to your own WACA core output.

## Requirements

- Python 3.10–3.13. Python 3.14 is not yet supported: `pydantic-core` fails to
  build against it. On a newer system, install [uv](https://docs.astral.sh/uv/)
  and create the environment with `uv venv --python 3.13`, or use a 3.10–3.13
  interpreter.
- The Google Cloud SDK (`bq`) if you run the sample dataset script.

## 1. Clone and Configure

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
```

Edit `.env`. Note that `.env` is a reference template: the backend reads these
as **environment variables** and does not auto-load `.env`. Export them before
starting the backend (see section 5).

| Variable | Used by | Meaning |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | backend | Your Google Cloud project ID. |
| `WACA_CORE_DATASET` | backend | Dataset the backend queries (WACA core output). |
| `SAMPLE_WACA_CORE_DATASET` | sample script | Dataset name the sample script creates. |
| `DEFAULT_TENANT_ID` | backend | Tenant config to load from `tenant_config/<id>.yaml`. |

`SAMPLE_WACA_CORE_DATASET` (used by the sample script) and `WACA_CORE_DATASET`
(read by the backend) are different variables. For the sample rehearsal, set
`WACA_CORE_DATASET` to the dataset the sample script created.

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

Run `uvicorn main:app` from the `backend` directory (as shown above). Started
from the repository root it fails with `Could not import module "main"`.

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

Add a one-line `Procfile` next to `backend/main.py`:

```text
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Deploy from the `backend` directory and keep it private:

```bash
cd backend
gcloud run deploy waca-path-backend \
  --source=. \
  --region="${WACA_PATH_LOCATION:-asia-northeast1}" \
  --no-allow-unauthenticated \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=your-gcp-project-id,WACA_CORE_DATASET=waca_core_output,DEFAULT_TENANT_ID=example
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
| `clean_page_path` | STRING | |
| `page_title` | STRING | |
| `engagement_time_msec` | INT64 | |
| `device_category` | STRING | |
| `browser` | STRING | |
| `country` | STRING | |
| `region` | STRING | |
| `is_key_event` | BOOL | |

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

### 必要環境

- Python 3.10–3.13。Python 3.14 は未対応です（`pydantic-core` のビルドが失敗します）。
  新しい環境では [uv](https://docs.astral.sh/uv/) を導入し `uv venv --python 3.13`
  で環境を作るか、3.10–3.13 の interpreter を使ってください。
- sample dataset script を使う場合は Google Cloud SDK（`bq`）。

### 1. clone して設定する

```bash
git clone https://github.com/wacasg/waca-path.git
cd waca-path
cp .env.example .env
```

`.env` を編集します。`.env` は控え用の template で、backend はこれらを
**environment variables** として読み、`.env` 自体は自動読み込みしません。起動前に
export してください（§5 参照）。

| 変数 | 使用者 | 意味 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | backend | 自分の Google Cloud project ID。 |
| `WACA_CORE_DATASET` | backend | backend が参照する dataset（WACA core output）。 |
| `SAMPLE_WACA_CORE_DATASET` | sample script | sample script が作成する dataset 名。 |
| `DEFAULT_TENANT_ID` | backend | `tenant_config/<id>.yaml` から読む tenant 設定。 |

`SAMPLE_WACA_CORE_DATASET`（sample script 用）と `WACA_CORE_DATASET`（backend が
参照）は別の変数です。sample で動作確認する場合は、`WACA_CORE_DATASET` に sample
script が作成した dataset を指定してください。

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

`uvicorn main:app` は `backend` ディレクトリから起動してください（上記のとおり）。
リポジトリ直下から起動すると `Could not import module "main"` になります。

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

`backend/main.py` の隣に 1 行の `Procfile` を置きます。

```text
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

`backend` ディレクトリからデプロイし、非公開のままにします。

```bash
cd backend
gcloud run deploy waca-path-backend \
  --source=. \
  --region="${WACA_PATH_LOCATION:-asia-northeast1}" \
  --no-allow-unauthenticated \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=your-gcp-project-id,WACA_CORE_DATASET=waca_core_output,DEFAULT_TENANT_ID=example
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
| `clean_page_path` | STRING | |
| `page_title` | STRING | |
| `engagement_time_msec` | INT64 | |
| `device_category` | STRING | |
| `browser` | STRING | |
| `country` | STRING | |
| `region` | STRING | |
| `is_key_event` | BOOL | |

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
