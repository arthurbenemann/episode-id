"""End-to-end tests for the M2 JSON API.

Shared fixtures live in `tests/conftest.py`. The extractor and Chakoteya
HTTP fetch are patched via the `patched_pipeline` fixture so the suite
runs offline; everything else (matching, path construction, dry-run vs
apply) exercises the real code.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services import jobs as jobs_service


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


def test_scan_recurses_into_subfolders(
    client: TestClient,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    # ARM/MakeMKV layout: each disc in its own subfolder, no mkvs at the top.
    src = tmp_path / "input"
    (src / "disc1").mkdir(parents=True)
    (src / "disc2").mkdir(parents=True)
    (src / "disc1" / "t1.mkv").write_bytes(b"")
    (src / "disc2" / "t2.mkv").write_bytes(b"")

    job_id = _start_scan(client, src, tmp_path / "out")

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded", status
    assert status["progress"]["files_total"] == 2

    body = client.get(f"/jobs/{job_id}/results").json()
    assert {Path(m["source"]).name for m in body["matches"]} == {"t1.mkv", "t2.mkv"}


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


def test_scan_rejects_unknown_provider(
    client: TestClient,
    mkv_folder: Path,
) -> None:
    resp = client.post(
        "/scan",
        json={
            "folder": str(mkv_folder),
            "series_key": "tng",
            "season": 1,
            "series_title": "X",
            "provider": "nonsense",
        },
    )
    assert resp.status_code == 400
    assert "unknown provider" in resp.json()["detail"]


def test_scan_opensubtitles_requires_tvdb_id(
    client: TestClient,
    mkv_folder: Path,
) -> None:
    resp = client.post(
        "/scan",
        json={
            "folder": str(mkv_folder),
            "series_key": "tng",
            "season": 1,
            "series_title": "X",
            "provider": "opensubtitles",
        },
    )
    assert resp.status_code == 400
    assert "tvdb_id" in resp.json()["detail"]


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


def test_scan_409_when_a_job_is_active(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
) -> None:
    # A job parked in PENDING counts as active; a second scan must be refused.
    jobs_service.get_store().create(
        jobs_service.ScanRequest(folder=Path("/tmp"), series_key="tng", season=1, series_title="X")
    )
    resp = client.post(
        "/scan",
        json={
            "folder": str(mkv_folder),
            "series_key": "tng",
            "season": 1,
            "series_title": "Star Trek The Next Generation",
        },
    )
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]


def test_status_404_for_unknown_job(client: TestClient) -> None:
    assert client.get("/jobs/deadbeef").status_code == 404
    assert client.get("/jobs/deadbeef/results").status_code == 404


def test_results_409_before_completion(client: TestClient) -> None:
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
    # Dry-run never touches Jellyfin even when it is configured.
    assert body["jellyfin"]["state"] == "skipped"
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
    # Jellyfin not configured in tests -> rescan skipped.
    assert body["jellyfin"]["state"] == "skipped"

    moved = sorted(p.name for p in library_root.rglob("*.mkv"))
    assert moved == [
        "Star Trek The Next Generation - S01E01 - Engage.mkv",
        "Star Trek The Next Generation - S01E02 - Battle Stations.mkv",
    ]
    assert list(mkv_folder.iterdir()) == []


def test_apply_triggers_jellyfin_rescan_when_configured(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    job_id = _start_scan(client, mkv_folder, library_root)

    with patch(
        "app.services.jobs.jellyfin.trigger_rescan",
        return_value=jobs_service.jellyfin.JellyfinStatus.triggered(),
    ) as triggered:
        body = client.post(f"/jobs/{job_id}/apply", json={"confirm": True}).json()

    triggered.assert_called_once()
    assert body["jellyfin"]["state"] == "triggered"
    assert body["jellyfin"]["detail"] is None


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
    assert "no readable subtitles" in (status["error"] or "")
