"""End-to-end tests for the M2 FastAPI surface.

We mock the two heavy/IO-bound steps — ffmpeg subtitle extraction and the
Chakoteya HTTP fetch — so the tests run offline and don't depend on real
MKV files. Everything else (matching, path construction, dry-run vs apply)
runs against the real code.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pysubs2
import pytest
from fastapi.testclient import TestClient

from app.core.extractor import ExtractedSubtitles, SubtitleStream
from app.main import create_app
from app.providers.base import EpisodeTranscript
from app.services import jobs as jobs_service

# Two fake "files" whose dialogue cleanly aligns with two fake episodes so the
# Hungarian matcher produces a deterministic, high-confidence mapping.
FAKE_DIALOGUE: dict[str, str] = {
    "t1.mkv": "warp factor nine engage make it so number one captain",
    "t2.mkv": "fire photon torpedoes shields up red alert klingons decloaking",
}

FAKE_EPISODES: list[EpisodeTranscript] = [
    EpisodeTranscript(
        season=1,
        episode=1,
        title="Engage",
        text="warp factor nine engage make it so number one captain on the bridge",
    ),
    EpisodeTranscript(
        season=1,
        episode=2,
        title="Battle Stations",
        text="fire photon torpedoes shields up red alert klingons decloaking off the bow",
    ),
]


def _fake_extract(file_path: Path) -> ExtractedSubtitles:
    """Stub for `extractor.extract_subtitles` that returns canned dialogue."""
    text = FAKE_DIALOGUE[file_path.name]
    # 200_000 ms = 200s, which is past the default 180s sample_start_seconds.
    event = pysubs2.SSAEvent(start=200_000, end=210_000, text=text)
    return ExtractedSubtitles(
        source=file_path,
        stream=SubtitleStream(
            index=0,
            codec="subrip",
            language="eng",
            title=None,
            forced=False,
        ),
        events=[event],
    )


def _fake_fetch_season(self: object, series_key: str, season: int) -> list[EpisodeTranscript]:
    return FAKE_EPISODES


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Each test starts with an empty job store."""
    jobs_service.get_store().clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def mkv_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "rips"
    folder.mkdir()
    for name in FAKE_DIALOGUE:
        # Content doesn't matter because we mock extract_subtitles.
        (folder / name).write_bytes(b"")
    return folder


@pytest.fixture
def patched_pipeline():
    """Patch the two external dependencies for the scan pipeline."""
    with (
        patch("app.services.jobs.extractor.extract_subtitles", side_effect=_fake_extract),
        patch(
            "app.services.jobs.ChakoteyaProvider.fetch_season",
            new=_fake_fetch_season,
        ),
    ):
        yield


def _start_scan(client: TestClient, folder: Path, library_root: Path) -> str:
    resp = client.post(
        "/scan",
        json={
            "folder": str(folder),
            "series_key": "tng",
            "season": 1,
            "series_title": "Star Trek The Next Generation",
            "year": 1987,
            "tvdb_id": 71470,
            "library_root": str(library_root),
        },
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def test_healthz_responds_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_scan_runs_full_pipeline(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    job_id = _start_scan(client, mkv_folder, tmp_path / "out")

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded"
    assert status["progress"]["files_total"] == 2
    assert status["progress"]["files_done"] == 2
    assert status["progress"]["stage"] == "done"


def test_results_match_jellyfin_layout(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    job_id = _start_scan(client, mkv_folder, library_root)

    body = client.get(f"/jobs/{job_id}/results").json()
    matches = {Path(m["source"]).name: m for m in body["matches"]}

    assert set(matches) == {"t1.mkv", "t2.mkv"}

    t1 = matches["t1.mkv"]
    assert t1["episode_title"] == "Engage"
    assert t1["season"] == 1
    assert t1["episode"] == 1
    assert "Season 01" in t1["destination"]
    assert "S01E01" in t1["destination"]
    assert "[tvdbid-71470]" in t1["destination"]
    assert t1["confidence"] > 80

    t2 = matches["t2.mkv"]
    assert t2["episode_title"] == "Battle Stations"
    assert "S01E02" in t2["destination"]


def test_scan_validates_folder(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/scan",
        json={
            "folder": str(tmp_path / "does-not-exist"),
            "series_key": "tng",
            "season": 1,
            "series_title": "X",
        },
    )
    assert resp.status_code == 400


def test_status_404_for_unknown_job(client: TestClient) -> None:
    assert client.get("/jobs/deadbeef").status_code == 404
    assert client.get("/jobs/deadbeef/results").status_code == 404


def test_results_409_before_completion(client: TestClient) -> None:
    # Manually insert a job that never ran.
    job = jobs_service.get_store().create(
        jobs_service.ScanRequest(
            folder=Path("/tmp"),
            series_key="tng",
            season=1,
            series_title="X",
        )
    )
    resp = client.get(f"/jobs/{job.id}/results")
    assert resp.status_code == 409


def test_dry_run_apply_does_not_move(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    job_id = _start_scan(client, mkv_folder, library_root)

    body = client.post(f"/jobs/{job_id}/apply", json={"confirm": False}).json()
    assert body["applied"] is False
    assert body["errors"] == []
    assert sorted(p.name for p in mkv_folder.iterdir()) == ["t1.mkv", "t2.mkv"]
    assert not library_root.exists()


def test_apply_with_confirm_moves_files(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    job_id = _start_scan(client, mkv_folder, library_root)

    body = client.post(f"/jobs/{job_id}/apply", json={"confirm": True}).json()
    assert body["applied"] is True
    assert body["errors"] == []

    moved = sorted(p.name for p in library_root.rglob("*.mkv"))
    assert moved == [
        "Star Trek The Next Generation - S01E01 - Engage.mkv",
        "Star Trek The Next Generation - S01E02 - Battle Stations.mkv",
    ]
    # Sources gone.
    assert list(mkv_folder.iterdir()) == []


def test_apply_before_completion_returns_409(client: TestClient) -> None:
    job = jobs_service.get_store().create(
        jobs_service.ScanRequest(
            folder=Path("/tmp"),
            series_key="tng",
            season=1,
            series_title="X",
        )
    )
    resp = client.post(f"/jobs/{job.id}/apply", json={"confirm": True})
    assert resp.status_code == 409


def test_scan_failure_recorded_in_status(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
) -> None:
    """If the worker raises, the job ends in FAILED with an error message."""
    with patch(
        "app.services.jobs.extractor.extract_subtitles",
        side_effect=RuntimeError("ffmpeg blew up"),
    ):
        job_id = _start_scan(client, mkv_folder, tmp_path / "out")

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert "no usable text subtitles" in (status["error"] or "")
