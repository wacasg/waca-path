"""Shared configuration helpers for the WACA path backend.

These were originally private helpers inside ``main.py`` (v0.1.0). They are
extracted here so that both the site-audit endpoint and the admin UI can use the
same tenant/dataset resolution and the same identifier validation.

Behaviour is unchanged from v0.1.0 so that existing installs keep working.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

# BigQuery identifiers cannot be passed as query parameters, so every identifier
# that is interpolated into SQL MUST pass through _validate_identifier first.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-:]{1,128}$")

BASE_DIR = Path(__file__).resolve().parents[2]
TENANT_DIR = BASE_DIR / "tenant_config"
TEMPLATE_DIR = BASE_DIR / "backend" / "templates"


def validate_identifier(value: str, name: str) -> str:
    """Reject anything that is not a plain BigQuery identifier."""
    if not _IDENT_RE.match(value or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: {value!r}")
    return value


def load_tenant_config(tenant_id: str) -> dict[str, Any]:
    safe_id = validate_identifier(tenant_id, "tenant_id")
    path = (TENANT_DIR / f"{safe_id}.yaml").resolve()
    if TENANT_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=400, detail="Invalid tenant config path")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Tenant config not found: {safe_id}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=f"Tenant config must be a mapping: {safe_id}")
    return data


def project_and_dataset(config: dict[str, Any]) -> tuple[str, str]:
    gcp = config.get("gcp") or {}
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or gcp.get("project_id")
    dataset = os.environ.get("WACA_CORE_DATASET") or gcp.get("bq_dataset")
    if not project or not dataset:
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_CLOUD_PROJECT and WACA_CORE_DATASET are required",
        )
    return validate_identifier(project, "project"), validate_identifier(dataset, "dataset")


def default_tenant_id() -> str:
    return os.environ.get("DEFAULT_TENANT_ID", "example")


def custom_columns(config: dict[str, Any]) -> list[dict[str, str]]:
    """Return the tenant's optional custom-dimension columns.

    WACA path deliberately ships with **no** organisation-specific columns. Each
    install declares its own GA4 custom dimensions in ``tenant_config/<id>.yaml``:

        ui:
          custom_columns:
            - column: membership_type   # column in micro_user_table
              label: Membership         # heading shown in the UI and CSV

    Unknown or unsafe column names are dropped rather than interpolated into SQL.
    """
    ui = config.get("ui") or {}
    raw = ui.get("custom_columns") or []
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        col = str(item.get("column") or "").strip()
        if not col or not _IDENT_RE.match(col):
            continue
        out.append({"column": col, "label": str(item.get("label") or col).strip()})
    return out
