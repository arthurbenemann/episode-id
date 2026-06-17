"""Scan job orchestration.

A "scan job" wraps one end-to-end run of the M1 pipeline (extract subtitles,
fetch reference transcripts, fuzzy-match, build Jellyfin paths) so the API
layer can hand it off to a background task and poll for progress.

The store is in-memory for M2; SQLite-backed persistence is a later milestone.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from app.config import settings
from app.core import extractor, matcher, renamer
from app.db import TranscriptCache
from app.providers.registry import create_provider
from app.services import jellyfin

log = logging.getLogger(__name__)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Stages mirror the pipeline phases so the UI can show what's happening.
STAGE_QUEUED = "queued"
STAGE_EXTRACTING = "extracting"
STAGE_FETCHING = "fetching"
STAGE_MATCHING = "matching"
STAGE_DONE = "done"


@dataclass
class JobProgress:
    files_total: int = 0
    files_done: int = 0
    stage: str = STAGE_QUEUED


@dataclass(frozen=True)
class CandidateOut:
    season: int
    episode: int
    title: str
    confidence: float


@dataclass(frozen=True)
class MatchOut:
    source: str
    destination: str
    confidence: float
    needs_review: bool
    season: int
    episode: int
    episode_title: str
    alternates: tuple[CandidateOut, ...]


@dataclass(frozen=True)
class ScanRequest:
    folder: Path
    series_key: str
    season: int
    series_title: str
    year: int | None = None
    tvdb_id: int | None = None
    library_root: Path | None = None
    include_provider_id: bool = True
    provider: str = "chakoteya"


@dataclass
class Job:
    id: str
    request: ScanRequest
    status: JobStatus = JobStatus.PENDING
    progress: JobProgress = field(default_factory=JobProgress)
    error: str | None = None
    results: list[MatchOut] = field(default_factory=list)


class JobStore:
    """Thread-safe in-memory job store.

    BackgroundTasks runs sync handlers in a thread pool, so concurrent reads
    from the API and writes from the worker have to be serialised.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, request: ScanRequest) -> Job:
        job = Job(id=uuid.uuid4().hex, request=request)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


_store = JobStore()


def get_store() -> JobStore:
    return _store


def run_scan(job_id: str, store: JobStore | None = None) -> None:
    """Worker entry point — runs the full pipeline for one job.

    Designed to be called from `BackgroundTasks.add_task`. All exceptions are
    captured into the job record rather than re-raised so the worker thread
    can't crash the server.
    """
    store = store or _store
    job = store.get(job_id)
    if job is None:
        log.error("run_scan called for unknown job %s", job_id)
        return

    with store._lock:
        job.status = JobStatus.RUNNING

    try:
        _execute(job, store)
        with store._lock:
            job.status = JobStatus.SUCCEEDED
            job.progress.stage = STAGE_DONE
    except Exception as exc:
        log.exception("scan job %s failed", job_id)
        with store._lock:
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"


def _execute(job: Job, store: JobStore) -> None:
    req = job.request

    files = sorted(p for p in req.folder.iterdir() if p.suffix.lower() == ".mkv")
    if not files:
        raise ValueError(f"no .mkv files in {req.folder}")

    with store._lock:
        job.progress.files_total = len(files)
        job.progress.stage = STAGE_EXTRACTING

    samples: list[matcher.FileSample] = []
    for f in files:
        try:
            result = extractor.extract_subtitles(f)
        except Exception as exc:
            log.warning("extract failed for %s: %s", f.name, exc)
            result = None
        if result is not None:
            dialogue = result.dialogue_after(
                start_ms=settings.sample_start_seconds * 1000,
                line_count=settings.sample_line_count,
            )
            samples.append(matcher.FileSample(path=f, dialogue=dialogue))
        with store._lock:
            job.progress.files_done += 1

    if not samples:
        raise ValueError(
            "no usable text subtitles found in any file (PGS/VobSub OCR is a future milestone)"
        )

    with store._lock:
        job.progress.stage = STAGE_FETCHING

    provider = create_provider(req.provider, cache=TranscriptCache(settings.cache_db_path))
    if req.provider == "opensubtitles":
        # OpenSubtitles keys series by TVDB id (see providers/base.py).
        if req.tvdb_id is None:
            raise ValueError("the opensubtitles provider requires a TVDB id")
        series_key = str(req.tvdb_id)
    else:
        series_key = req.series_key
    transcripts = provider.fetch_season(series_key, req.season)
    episodes = [
        matcher.EpisodeReference(
            season=t.season,
            episode=t.episode,
            title=t.title,
            transcript=t.text,
        )
        for t in transcripts
    ]

    with store._lock:
        job.progress.stage = STAGE_MATCHING

    matches = matcher.match(samples, episodes)

    library_root = req.library_root or settings.library_root
    series = renamer.SeriesInfo(title=req.series_title, year=req.year, tvdb_id=req.tvdb_id)

    out: list[MatchOut] = []
    for m in matches:
        ep = m.best.episode
        destination = renamer.build_path(
            library_root=library_root,
            series=series,
            season=ep.season,
            episode=ep.episode,
            episode_title=ep.title,
            extension=m.file.path.suffix,
            include_provider_id=req.include_provider_id,
        )
        alternates = tuple(
            CandidateOut(
                season=c.episode.season,
                episode=c.episode.episode,
                title=c.episode.title,
                confidence=c.score,
            )
            for c in m.alternates
        )
        out.append(
            MatchOut(
                source=str(m.file.path),
                destination=str(destination),
                confidence=m.confidence,
                needs_review=m.needs_review,
                season=ep.season,
                episode=ep.episode,
                episode_title=ep.title,
                alternates=alternates,
            )
        )

    with store._lock:
        job.results = out


@dataclass(frozen=True)
class ApplyResult:
    """What `apply_job` returns: per-file errors plus the Jellyfin outcome."""

    errors: list[str]
    jellyfin: jellyfin.JellyfinStatus


def apply_job(
    job_id: str,
    *,
    confirm: bool,
    store: JobStore | None = None,
) -> ApplyResult:
    """Apply the proposed renames for `job_id`.

    `confirm=False` is a dry-run — the renamer logs the moves but doesn't
    touch the filesystem. On a fully successful confirmed apply, this also
    pokes Jellyfin's `/Library/Refresh` if it's configured so the new files
    show up without a manual scan.
    """
    store = store or _store
    job = store.get(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise RuntimeError(
            f"job {job_id} is in status {job.status.value}; results not ready to apply"
        )

    plans = [
        renamer.RenamePlan(
            source=Path(m.source),
            destination=Path(m.destination),
            confidence=m.confidence,
            needs_review=m.needs_review,
        )
        for m in job.results
    ]
    errors = renamer.apply_plans(plans, dry_run=not confirm)
    error_messages = [f"{type(e).__name__}: {e}" for e in errors]

    # Only kick Jellyfin when files actually moved without failures —
    # partial applies surface errors first; dry-runs don't change anything.
    if confirm and not errors and plans:
        jf = jellyfin.trigger_rescan()
    else:
        jf = jellyfin.JellyfinStatus.skipped()

    return ApplyResult(errors=error_messages, jellyfin=jf)
