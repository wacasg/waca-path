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
