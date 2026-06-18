"""HTTP routes for scan jobs."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.schemas import (
    ApplyRequest,
    ApplyResponse,
    CandidateModel,
    JobProgressModel,
    JobResultsResponse,
    JobStatusResponse,
    MatchModel,
    ScanRequestModel,
    ScanResponse,
)
from app.providers.registry import PROVIDER_NAMES
from app.services import jobs as jobs_service

router = APIRouter()


@router.post("/scan", response_model=ScanResponse, status_code=202)
def scan(body: ScanRequestModel, background_tasks: BackgroundTasks) -> ScanResponse:
    store = jobs_service.get_store()
    active = store.active()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"a scan is already in progress (job {active.id})",
        )
    if not body.folder.exists() or not body.folder.is_dir():
        raise HTTPException(status_code=400, detail=f"folder not found: {body.folder}")
    if body.provider not in PROVIDER_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider: {body.provider}. Known: {', '.join(PROVIDER_NAMES)}",
        )
    if body.provider == "opensubtitles" and body.tvdb_id is None:
        raise HTTPException(
            status_code=400,
            detail="the opensubtitles provider requires tvdb_id",
        )

    req = jobs_service.ScanRequest(
        folder=body.folder,
        series_key=body.series_key,
        season=body.season,
        series_title=body.series_title,
        year=body.year,
        tvdb_id=body.tvdb_id,
        library_root=body.library_root,
        include_provider_id=body.include_provider_id,
        provider=body.provider,
    )
    job = store.create(req)
    background_tasks.add_task(jobs_service.run_scan, job.id)
    return ScanResponse(job_id=job.id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_status(job_id: str) -> JobStatusResponse:
    job = jobs_service.get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        progress=JobProgressModel(
            files_total=job.progress.files_total,
            files_done=job.progress.files_done,
            stage=job.progress.stage,
        ),
        error=job.error,
    )


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
def get_results(job_id: str) -> JobResultsResponse:
    job = jobs_service.get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if job.status != jobs_service.JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"job not ready (status: {job.status.value})",
        )
    matches = [
        MatchModel(
            source=m.source,
            destination=m.destination,
            confidence=m.confidence,
            needs_review=m.needs_review,
            season=m.season,
            episode=m.episode,
            episode_title=m.episode_title,
            alternates=[
                CandidateModel(
                    season=c.season,
                    episode=c.episode,
                    title=c.title,
                    confidence=c.confidence,
                )
                for c in m.alternates
            ],
        )
        for m in job.results
    ]
    return JobResultsResponse(job_id=job.id, status=job.status.value, matches=matches)


@router.post("/jobs/{job_id}/apply", response_model=ApplyResponse)
def apply(job_id: str, body: ApplyRequest) -> ApplyResponse:
    try:
        result = jobs_service.apply_job(job_id, confirm=body.confirm)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"job {exc.args[0]} not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApplyResponse(
        job_id=job_id,
        applied=body.confirm,
        errors=result.errors,
        jellyfin={"state": result.jellyfin.state, "detail": result.jellyfin.detail},
    )
