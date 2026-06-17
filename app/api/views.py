"""HTML views for the htmx UI (Milestone 3).

The same orchestrator from `app.services.jobs` powers both the JSON API
and these HTML endpoints — views are a thin presentation layer.

Note: every `/ui/*` route returns 200 even on error. htmx does not swap
4xx/5xx response bodies by default, and we want the error fragment to
land in the page without extra extensions or client-side configuration.
The JSON API in `app/api/scan.py` keeps its real status codes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.providers.chakoteya import SERIES_PATHS
from app.providers.registry import PROVIDER_NAMES
from app.services import jobs as jobs_service

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


def _form_defaults() -> dict[str, object]:
    return {
        "media_root": str(settings.media_root),
        "library_root": str(settings.library_root),
        "include_provider_id": settings.include_provider_id,
    }


def _parse_optional_int(value: str) -> int | None:
    """Form-encoded numerics arrive as '' when the field is blank."""
    value = value.strip()
    if not value:
        return None
    return int(value)


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_error.html",
        {"message": message},
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series_keys": sorted(SERIES_PATHS),
            "providers": PROVIDER_NAMES,
            "defaults": _form_defaults(),
        },
    )


@router.post("/ui/scan", response_class=HTMLResponse)
def ui_scan(
    request: Request,
    background_tasks: BackgroundTasks,
    folder: str = Form(...),
    series_key: str = Form(...),
    season: int = Form(..., ge=0),
    series_title: str = Form(...),
    year: str = Form(""),
    tvdb_id: str = Form(""),
    library_root: str = Form(""),
    include_provider_id: bool = Form(False),
    provider: str = Form("chakoteya"),
) -> HTMLResponse:
    try:
        year_int = _parse_optional_int(year)
        tvdb_id_int = _parse_optional_int(tvdb_id)
    except ValueError:
        return _error(request, "year and tvdb_id must be integers")

    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        return _error(request, f"folder not found: {folder}")

    if provider not in PROVIDER_NAMES:
        return _error(request, f"unknown provider: {provider}")

    if provider == "chakoteya" and series_key not in SERIES_PATHS:
        return _error(request, f"unknown series key: {series_key}")

    if provider == "opensubtitles" and tvdb_id_int is None:
        return _error(request, "the opensubtitles provider requires a TVDB id")

    req = jobs_service.ScanRequest(
        folder=folder_path,
        series_key=series_key,
        season=season,
        series_title=series_title,
        year=year_int,
        tvdb_id=tvdb_id_int,
        library_root=Path(library_root) if library_root.strip() else None,
        include_provider_id=include_provider_id,
        provider=provider,
    )
    job = jobs_service.get_store().create(req)
    background_tasks.add_task(jobs_service.run_scan, job.id)
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@router.get("/ui/jobs/{job_id}/progress", response_class=HTMLResponse)
def ui_progress(request: Request, job_id: str) -> HTMLResponse:
    job = jobs_service.get_store().get(job_id)
    if job is None:
        return _error(request, f"job {job_id} not found")
    if job.status == jobs_service.JobStatus.FAILED:
        return _error(request, job.error or "job failed")
    if job.status == jobs_service.JobStatus.SUCCEEDED:
        return templates.TemplateResponse(
            request,
            "_results.html",
            {"job": job, "matches": job.results},
        )
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@router.post("/ui/jobs/{job_id}/apply", response_class=HTMLResponse)
def ui_apply(
    request: Request,
    job_id: str,
    confirm: bool = Form(False),
) -> HTMLResponse:
    try:
        result = jobs_service.apply_job(job_id, confirm=confirm)
    except KeyError:
        return _error(request, f"job {job_id} not found")
    except RuntimeError as exc:
        return _error(request, str(exc))

    job = jobs_service.get_store().get(job_id)
    return templates.TemplateResponse(
        request,
        "_applied.html",
        {
            "applied": confirm,
            "errors": result.errors,
            "jellyfin": result.jellyfin,
            "total": len(job.results) if job else 0,
        },
    )
