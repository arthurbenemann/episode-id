"""FastAPI entry point for episode-id.

Run locally:

    uvicorn app.main:app --reload --port 8080

JSON API (M2):

    POST /scan                  -> {job_id}
    GET  /jobs/{id}             -> status + progress
    GET  /jobs/{id}/results     -> proposed Jellyfin mapping
    POST /jobs/{id}/apply       -> dry-run or move files
    GET  /healthz               -> liveness probe

htmx UI (M3):

    GET  /                      -> scan form
    POST /ui/scan               -> kick off a job, return progress fragment
    GET  /ui/jobs/{id}/progress -> progress | results | error fragment
    POST /ui/jobs/{id}/apply    -> applied or error fragment
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.scan import router as scan_router
from app.api.views import router as views_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title="episode-id",
        version="0.1.0",
        description=(
            "Identify TV episode MKVs by subtitle fuzzy-matching and propose "
            "Jellyfin-compatible renames."
        ),
    )

    fastapi_app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    fastapi_app.include_router(scan_router)
    fastapi_app.include_router(views_router)

    @fastapi_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return fastapi_app


app = create_app()
