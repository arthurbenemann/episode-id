"""FastAPI entry point for episode-id (Milestone 2).

Run locally:

    uvicorn app.main:app --reload --port 8080

Endpoints:

    POST /scan                  -> {job_id}
    GET  /jobs/{id}             -> status + progress
    GET  /jobs/{id}/results     -> proposed Jellyfin mapping
    POST /jobs/{id}/apply       -> dry-run or move files
    GET  /healthz               -> liveness probe
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.scan import router as scan_router


def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title="episode-id",
        version="0.1.0",
        description=(
            "Identify TV episode MKVs by subtitle fuzzy-matching and propose "
            "Jellyfin-compatible renames."
        ),
    )
    fastapi_app.include_router(scan_router)

    @fastapi_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return fastapi_app


app = create_app()
