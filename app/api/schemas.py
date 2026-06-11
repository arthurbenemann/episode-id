"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ScanRequestModel(BaseModel):
    """Body for `POST /scan`."""

    folder: Path = Field(..., description="Absolute path to folder of MKVs.")
    series_key: str = Field(
        ...,
        description="Provider series key, e.g. 'tng' for the Chakoteya provider.",
    )
    season: int = Field(..., ge=0, description="Season number (0 for specials).")
    series_title: str = Field(..., description="Display title used in output filenames.")
    year: int | None = None
    tvdb_id: int | None = None
    library_root: Path | None = Field(
        default=None,
        description="Output root. Defaults to LIBRARY_ROOT env / settings.library_root.",
    )
    include_provider_id: bool = True


class ScanResponse(BaseModel):
    job_id: str


class JobProgressModel(BaseModel):
    files_total: int
    files_done: int
    stage: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: JobProgressModel
    error: str | None = None


class CandidateModel(BaseModel):
    season: int
    episode: int
    title: str
    confidence: float


class MatchModel(BaseModel):
    source: str
    destination: str
    confidence: float
    needs_review: bool
    season: int
    episode: int
    episode_title: str
    alternates: list[CandidateModel]


class JobResultsResponse(BaseModel):
    job_id: str
    status: str
    matches: list[MatchModel]


class ApplyRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="True applies the moves; False is a dry-run.",
    )


class ApplyResponse(BaseModel):
    job_id: str
    applied: bool
    errors: list[str]
