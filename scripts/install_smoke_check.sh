#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-}}"
SAMPLE_DATASET="${SAMPLE_WACA_CORE_DATASET:-waca_path_sample_core}"
LOCATION="${WACA_PATH_LOCATION:-asia-northeast1}"

required_files=(
  "README.md"
  "INSTALL.md"
  "LICENSE"
  ".env.example"
  "backend/main.py"
  "backend/requirements.txt"
  "tenant_config/example.yaml"
  "samples/bigquery/create_anonymous_waca_core_output_sample.sql"
  "scripts/create_sample_dataset.sh"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "ERROR: required file is missing: $file" >&2
    exit 1
  fi
done

for path in docs tests training metrics; do
  if [[ -e "$path" ]]; then
    echo "ERROR: public install repository should not contain: $path" >&2
    exit 1
  fi
done

if find . -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' \) -print -quit | grep -q .; then
  echo "ERROR: cache directories must not be included in the public install repository." >&2
  exit 1
fi

python3 -m py_compile backend/main.py

echo "Static public-install checks passed."

if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "your-gcp-project-id" ]]; then
  cat <<'EOF'

Set PROJECT_ID (or GOOGLE_CLOUD_PROJECT) to your Google Cloud project to also
dry-run the anonymous WACA core output sample SQL:

  PROJECT_ID=your-gcp-project-id DRY_RUN=1 bash scripts/install_smoke_check.sh
EOF
  exit 0
fi

if ! command -v bq >/dev/null 2>&1; then
  echo "bq command not found. Static checks passed, but BigQuery dry-run was skipped." >&2
  exit 0
fi

PROJECT_ID="$PROJECT_ID" \
SAMPLE_WACA_CORE_DATASET="$SAMPLE_DATASET" \
WACA_PATH_AUDIT_LOCATION="$LOCATION" \
DRY_RUN=1 \
scripts/create_sample_dataset.sh

echo "WACA path install smoke check passed."
