"""WACA path public backend.

v0.1.0 exposed a single self-contained module with ``/healthz`` and
``/api/agent/site-audit``. v0.2.0 keeps both of those unchanged and adds a
read-only admin UI under ``/ui/``. Shared helpers moved to
``services/config.py`` so that both halves validate identifiers the same way.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from routers import ui as ui_router
from services.config import (
    default_tenant_id,
    load_tenant_config,
    project_and_dataset,
    validate_identifier,
)

logger = logging.getLogger("waca_path_backend")


app = FastAPI(
    title="WACA path backend",
    description="Public site-audit backend and read-only admin UI for WACA core output.",
    version="0.2.0",
)

app.include_router(ui_router.router)


class SiteAuditRequest(BaseModel):
    user_pseudo_id: str = Field(..., min_length=1, max_length=256)
    tenant_id: str = Field(default_factory=default_tenant_id)
    max_rows: int = Field(default=50, ge=1, le=500)


# Backwards-compatible aliases. The implementations now live in
# services/config.py; behaviour is identical to v0.1.0.
_validate_identifier = validate_identifier
_load_tenant_config = load_tenant_config
_project_and_dataset = project_and_dataset


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [
        {
            "event_name": row.get("event_name"),
            "page_title": row.get("page_title"),
            "page_location": row.get("page_location"),
            "event_timestamp": str(row.get("event_timestamp")),
            "engagement_time_msec": row.get("engagement_time_msec"),
            "is_key_event": row.get("is_key_event"),
        }
        for row in rows
    ]
    key_events = [p for p in pages if p.get("is_key_event")]
    visited_paths = [p.get("page_location") for p in pages if p.get("page_location")]
    observations: list[str] = []
    if not rows:
        observations.append("No rows were found for this user in micro_user_table.")
    else:
        observations.append(f"Found {len(rows)} rows for the selected user.")
        if key_events:
            observations.append(f"Found {len(key_events)} key event row(s).")
        if visited_paths:
            observations.append(f"First observed page: {visited_paths[0]}")
            observations.append(f"Last observed page: {visited_paths[-1]}")
    return {
        "rows_found": len(rows),
        "pages": pages,
        "observations": observations,
        "draft_improvements": [
            "Review the first and last observed pages for friction.",
            "Compare high-engagement pages with key-event pages.",
            "Use this draft as an analyst starting point, not an automatic production change.",
        ],
    }


async def _query_micro_user_table(
    project: str,
    dataset: str,
    user_pseudo_id: str,
    max_rows: int,
) -> list[dict[str, Any]]:
    import asyncio
    from google.cloud import bigquery
    from google.api_core.exceptions import (
        BadRequest,
        Forbidden,
        GoogleAPICallError,
        NotFound,
    )

    # Select only columns that every WACA core output provides. ``clean_page_path``
    # is not part of the public WACA core micro_user_table, and the summary uses
    # ``page_location`` anyway, so requesting it turned every real-data call into
    # a 400 (``Unrecognized name: clean_page_path``).
    sql = f"""
    SELECT
      event_date,
      event_timestamp,
      event_name,
      user_pseudo_id,
      user_id,
      ga_session_id,
      page_location,
      page_title,
      engagement_time_msec,
      device_category,
      browser,
      country,
      region,
      is_key_event
    FROM `{project}.{dataset}.micro_user_table`
    WHERE user_pseudo_id = @user_pseudo_id
    ORDER BY event_timestamp
    LIMIT @max_rows
    """

    def _run() -> list[dict[str, Any]]:
        client = bigquery.Client(project=project)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_pseudo_id", "STRING", user_pseudo_id),
                bigquery.ScalarQueryParameter("max_rows", "INT64", max_rows),
            ]
        )
        try:
            return [dict(row) for row in client.query(sql, job_config=job_config).result()]
        except NotFound as exc:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"BigQuery table not found: {project}.{dataset}.micro_user_table. "
                    "Confirm WACA core has created it and that GOOGLE_CLOUD_PROJECT / "
                    "WACA_CORE_DATASET point at the right project and dataset."
                ),
            ) from exc
        except Forbidden as exc:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Permission denied for BigQuery. The runtime identity needs "
                    "roles/bigquery.dataViewer on the dataset and "
                    "roles/bigquery.jobUser on the project."
                ),
            ) from exc
        except BadRequest as exc:
            logger.warning(
                "BigQuery rejected the query: %s", getattr(exc, "message", str(exc))
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "BigQuery rejected the request. Check that the dataset and "
                    "micro_user_table schema match what WACA path expects."
                ),
            ) from exc
        except GoogleAPICallError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"BigQuery request failed: {type(exc).__name__}",
            ) from exc

    return await asyncio.to_thread(_run)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "waca-path-backend",
        "env": "cloud_run" if os.environ.get("K_SERVICE") else "local",
    }


@app.post("/api/agent/site-audit", tags=["agent"])
async def site_audit(req: SiteAuditRequest) -> dict[str, Any]:
    config = _load_tenant_config(req.tenant_id)
    project, dataset = _project_and_dataset(config)
    rows = await _query_micro_user_table(
        project=project,
        dataset=dataset,
        user_pseudo_id=req.user_pseudo_id,
        max_rows=req.max_rows,
    )
    summary = _summarize_rows(rows)
    return {
        "tenant_id": req.tenant_id,
        "user_pseudo_id": req.user_pseudo_id,
        "source": f"{project}.{dataset}.micro_user_table",
        **summary,
    }
