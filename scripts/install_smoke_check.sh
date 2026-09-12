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
  "CONTRIBUTING.md"
  "SECURITY.md"
  "LICENSE"
  ".env.example"
  "backend/main.py"
  "backend/requirements.txt"
  "backend/routers/ui.py"
  "backend/services/config.py"
  "backend/services/user_explorer.py"
  "backend/services/user_filters.py"
  "backend/services/csv_export.py"
  "backend/i18n/__init__.py"
  "backend/i18n/ja.json"
  "backend/i18n/en.json"
  "backend/templates/base.html"
  "backend/templates/users/index.html"
  "backend/templates/users/detail.html"
  "CHANGELOG.md"
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

# `tests/` is deliberately allowed from v0.2.0: the suite enforces guarantees a
# reader cannot verify by inspection - that every UI string is translated, that
# CSV cells cannot be executed as spreadsheet formulas, and that cursor paging
# does not drop rows. Internal-only material (docs, training, metrics) stays out.
for path in docs training metrics; do
  if [[ -e "$path" ]]; then
    echo "ERROR: public install repository should not contain: $path" >&2
    exit 1
  fi
done

# Cache directories must not be *committed*. Running the backend (INSTALL.md
# section 4) legitimately writes backend/__pycache__ next to a local .venv, so
# check the Git index when available and otherwise skip virtualenvs.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git ls-files | grep -Eq '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache)/'; then
    echo "ERROR: cache directories must not be committed to the public install repository." >&2
    exit 1
  fi
elif find . -type d -name '.venv' -prune -o -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' \) -print -quit | grep -q .; then
  echo "ERROR: cache directories must not be included in the public install repository." >&2
  exit 1
fi

# Syntax-check the backend without writing __pycache__. py_compile ignores
# PYTHONDONTWRITEBYTECODE and always writes bytecode, which would trip the
# cache-directory guard above on the next run, so use ast.parse instead to
# keep this check idempotent.
python3 -c "import ast; ast.parse(open('backend/main.py').read())"

# --- Admin UI static checks (v0.2.0) -----------------------------------------
# Catch the two failure modes that only show up at runtime otherwise: an
# untranslated UI string, and a template that no longer parses.
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYCHECK'
import json
import sys
from pathlib import Path

root = Path(".")
catalogs = {p.stem: json.loads(p.read_text("utf-8")) for p in root.glob("backend/i18n/*.json")}
if "ja" not in catalogs:
    print("ERROR: backend/i18n/ja.json is missing", file=sys.stderr)
    sys.exit(1)

base = set(catalogs["ja"])
failed = False
for lang, cat in catalogs.items():
    missing = base - set(cat)
    if missing:
        failed = True
        print(f"ERROR: {lang}.json is missing {len(missing)} key(s): "
              f"{sorted(missing)[:5]}", file=sys.stderr)
if failed:
    sys.exit(1)
print(f"i18n catalogues OK ({len(base)} keys x {len(catalogs)} languages)")
PYCHECK
else
  echo "python3 not found; skipped the i18n catalogue check." >&2
fi

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
