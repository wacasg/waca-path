#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-}}"
DATASET_ID="${SAMPLE_WACA_CORE_DATASET:-waca_path_sample_core}"
LOCATION="${WACA_PATH_LOCATION:-${WACA_PATH_AUDIT_LOCATION:-asia-northeast1}}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID or GOOGLE_CLOUD_PROJECT is required." >&2
  echo "Example: PROJECT_ID=your-gcp-project-id scripts/create_sample_dataset.sh" >&2
  exit 1
fi

if ! command -v bq >/dev/null 2>&1; then
  echo "bq command not found. Install and authenticate the Google Cloud SDK first." >&2
  exit 1
fi

args=(
  query
  "--location=${LOCATION}"
  --use_legacy_sql=false
  "--parameter=project_id:STRING:${PROJECT_ID}"
  "--parameter=dataset_id:STRING:${DATASET_ID}"
  "--parameter=location:STRING:${LOCATION}"
)

if [[ "$DRY_RUN" == "1" ]]; then
  args+=(--dry_run)
fi

bq "${args[@]}" < samples/bigquery/create_anonymous_waca_core_output_sample.sql

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Sample dataset SQL dry-run passed for ${PROJECT_ID}.${DATASET_ID}."
  exit 0
fi

bq query --location="${LOCATION}" --use_legacy_sql=false \
  "SELECT 'micro_user_table' AS table_name, COUNT(*) AS row_count FROM \`${PROJECT_ID}.${DATASET_ID}.micro_user_table\`
   UNION ALL
   SELECT 'micro_items_table' AS table_name, COUNT(*) AS row_count FROM \`${PROJECT_ID}.${DATASET_ID}.micro_items_table\`"

echo "Anonymous WACA core output sample dataset ready: ${PROJECT_ID}.${DATASET_ID}"
