"""Admin UI routes: users list, user detail, CSV export.

Read-only. Nothing in this module writes to BigQuery.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from i18n import LANG_COOKIE, SUPPORTED_LANGS, make_translator, negotiate_lang, normalize_lang
from services import csv_export
from services.config import (
    TEMPLATE_DIR,
    custom_columns,
    default_tenant_id,
    load_tenant_config,
    project_and_dataset,
)
from services.user_explorer import get_user_detail, list_users
from services.user_filters import (
    NUMERIC_COLS,
    SORTABLE_COLS,
    TEXT_EQUALS_COLS,
    TEXT_SEARCH_COLS,
    VALID_PRESETS,
    decode_cursor,
    make_next_cursor,
    parse_filters_from_query,
)

logger = logging.getLogger("waca_path_backend.ui")
router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

_PSEUDO_ID_MAX = 256


def _lang_for(request: Request, tenant_config: dict) -> tuple[str, bool]:
    """Resolve the UI language. Second value is True when it came from ?lang=."""
    query = normalize_lang(request.query_params.get("lang"))
    lang = negotiate_lang(
        query=request.query_params.get("lang"),
        cookie=request.cookies.get(LANG_COOKIE),
        accept_language=request.headers.get("accept-language"),
        tenant_default=(tenant_config.get("ui") or {}).get("default_lang"),
    )
    return lang, query is not None


def _tenant(request: Request) -> tuple[dict, str, str]:
    tenant_id = request.query_params.get("tenant") or default_tenant_id()
    config = load_tenant_config(tenant_id)
    project, dataset = project_and_dataset(config)
    return config, project, dataset


def _query_string(params, *, drop: set[str] | None = None) -> str:
    drop = (drop or set()) | {"cursor"}
    items = params.multi_items() if hasattr(params, "multi_items") else params.items()
    return urlencode([(k, v) for k, v in items if k not in drop and v not in (None, "")])


def _base_context(request: Request, lang: str, config: dict, project: str, dataset: str) -> dict:
    return {
        "t": make_translator(lang),
        "lang": lang,
        "supported_langs": SUPPORTED_LANGS,
        "custom_columns": custom_columns(config),
        "data_source": {"project": project, "dataset": dataset},
        # Language links must preserve the current filters: the whole filter
        # state lives in the query string, so rebuild it rather than linking to
        # a bare "?lang=".
        "lang_links": {
            code: "?" + urlencode(
                [
                    *[(k, v) for k, v in request.query_params.multi_items() if k != "lang"],
                    ("lang", code),
                ]
            )
            for code in SUPPORTED_LANGS
        },
    }


def _with_lang_cookie(response: Response, lang: str, explicit: bool) -> Response:
    # Only a deliberate ?lang= choice is persisted; an Accept-Language guess
    # must not overwrite what the user picked earlier.
    if explicit:
        response.set_cookie(
            LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, httponly=False, samesite="lax"
        )
    return response


@router.get("/ui/", include_in_schema=False)
async def ui_root() -> RedirectResponse:
    return RedirectResponse(url="/ui/users/", status_code=307)


@router.get("/ui/users/", response_class=HTMLResponse, include_in_schema=False)
async def users_index(request: Request) -> Response:
    config, project, dataset = _tenant(request)
    lang, explicit = _lang_for(request, config)
    cols = custom_columns(config)

    filters = parse_filters_from_query(
        dict(request.query_params), page_size=50, custom_columns=cols
    )

    rows: list = []
    error: str | None = None
    try:
        rows = await list_users(project=project, dataset=dataset, filters=filters)
    except Exception:
        # Never surface the raw BigQuery error: it leaks project / dataset / SQL.
        logger.exception("users list query failed")
        error = "users.error.bq"

    context = _base_context(request, lang, config, project, dataset)
    context.update(
        {
            "rows": rows,
            "filters": filters,
            "date_from": filters.date_from.isoformat(),
            "date_to": filters.date_to.isoformat(),
            "next_cursor": make_next_cursor(rows, filters) if rows else None,
            "error": error,
            "query_base": _query_string(request.query_params),
            "sortable_cols": list(SORTABLE_COLS),
            "text_search_cols": list(TEXT_SEARCH_COLS),
            "text_equals_cols": list(TEXT_EQUALS_COLS),
            "numeric_cols": list(NUMERIC_COLS),
            "valid_presets": sorted(VALID_PRESETS),
            "csv_max_rows": csv_export.MAX_ROWS,
        }
    )
    response = templates.TemplateResponse(request, "users/index.html", context)
    return _with_lang_cookie(response, lang, explicit)


@router.get("/ui/users/export.csv", include_in_schema=False)
async def users_export_csv(request: Request) -> Response:
    config, project, dataset = _tenant(request)
    lang, _ = _lang_for(request, config)
    cols = custom_columns(config)

    filters = parse_filters_from_query(
        dict(request.query_params), page_size=csv_export.PAGE_SIZE, custom_columns=cols
    )
    filters.cursor = None  # an export always starts from the first row

    rows: list = []
    truncated = False
    try:
        for page_no in range(csv_export.MAX_PAGES):
            page = await list_users(project=project, dataset=dataset, filters=filters)
            if not page:
                break
            rows.extend(page)
            if len(page) < filters.page_size:
                break  # last page
            token = make_next_cursor(page, filters)
            if not token:
                break
            filters.cursor = decode_cursor(token)
            if page_no == csv_export.MAX_PAGES - 1:
                # Truncated only if a further page is actually still pending -
                # finishing exactly on the limit is not a truncation.
                truncated = True
    except Exception:
        logger.exception("csv export query failed")
        raise HTTPException(status_code=500, detail="internal error - see logs") from None

    # Audit trail: a CSV export takes pseudonymous IDs out of the tool in bulk.
    # Filter *keys* only - values can contain search terms and custom-dimension
    # values, which may be personal data.
    logger.info(
        "csv export rows=%d truncated=%s lang=%s filter_keys=%s",
        len(rows),
        truncated,
        lang,
        sorted(dict(request.query_params).keys()),
    )

    body = csv_export.build_csv(
        rows, lang=lang, custom_columns=cols, truncated=truncated
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{csv_export.filename()}"',
            "X-Waca-Path-Rows": str(len(rows)),
            "X-Waca-Path-Truncated": "1" if truncated else "0",
        },
    )


@router.get("/ui/users/{user_pseudo_id}", response_class=HTMLResponse, include_in_schema=False)
async def user_detail(user_pseudo_id: str, request: Request) -> Response:
    if not user_pseudo_id or len(user_pseudo_id) > _PSEUDO_ID_MAX:
        raise HTTPException(status_code=400, detail="Invalid user_pseudo_id")

    config, project, dataset = _tenant(request)
    lang, explicit = _lang_for(request, config)
    cols = custom_columns(config)

    today = date.today()
    try:
        date_to = date.fromisoformat(request.query_params.get("to") or today.isoformat())
    except ValueError:
        date_to = today
    try:
        date_from = date.fromisoformat(
            request.query_params.get("from") or (date_to - timedelta(days=30)).isoformat()
        )
    except ValueError:
        date_from = date_to - timedelta(days=30)

    detail: dict = {"profile": {}, "sessions": [], "user_pseudo_id": user_pseudo_id}
    error: str | None = None
    try:
        detail = await get_user_detail(
            project=project,
            dataset=dataset,
            user_pseudo_id=user_pseudo_id,
            date_from=date_from,
            date_to=date_to,
            custom_columns=cols,
        )
    except Exception:
        logger.exception("user detail query failed")
        error = "users.error.bq"

    context = _base_context(request, lang, config, project, dataset)
    context.update(
        {
            "detail": detail,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "error": error,
            "has_clarity_recordings": any(
                s.get("clarity_recordings") for s in detail.get("sessions", [])
            ),
        }
    )
    response = templates.TemplateResponse(request, "users/detail.html", context)
    return _with_lang_cookie(response, lang, explicit)


@router.get("/ui/healthz", include_in_schema=False)
async def ui_healthz() -> dict[str, str]:
    return {"status": "ok", "ui": "enabled", "env": "cloud_run" if os.environ.get("K_SERVICE") else "local"}
