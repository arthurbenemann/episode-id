"""Tests for the M3 htmx web UI.

Reuses the same mocked extractor + Chakoteya provider as `test_api.py`
via shared fixtures in `conftest.py`. TestClient runs `BackgroundTasks`
synchronously, so the first poll after a scan is already terminal.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.services import jobs as jobs_service


def _scan_form(folder: Path, library_root: Path) -> dict[str, str]:
    return {
        "folder": str(folder),
        "series_key": "tng",
        "season": "1",
        "series_title": "Star Trek The Next Generation",
        "year": "1987",
        "tvdb_id": "71470",
        "library_root": str(library_root),
        "include_provider_id": "on",
    }


def test_index_renders_form(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")

    form = soup.find("form")
    assert form is not None
    assert form["hx-post"] == "/ui/scan"

    names = {i.get("name") for i in form.find_all(["input", "select"])}
    assert {
        "folder",
        "series_key",
        "season",
        "series_title",
        "year",
        "tvdb_id",
        "library_root",
        "include_provider_id",
    }.issubset(names)

    # htmx script is loaded.
    assert "htmx.org" in resp.text


def test_index_dropdown_lists_all_trek_series(client: TestClient) -> None:
    soup = BeautifulSoup(client.get("/").text, "lxml")
    options = {o["value"] for o in soup.select("select[name=series_key] option")}
    assert options == {"tos", "tas", "tng", "ds9", "voy", "ent", "dis"}


def test_ui_scan_returns_results_after_pipeline(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, library_root))
    assert resp.status_code == 200

    soup = BeautifulSoup(resp.text, "lxml")
    progress = soup.find("div", class_="progress")
    assert progress is not None
    job_id = progress["hx-get"].split("/")[3]
    assert jobs_service.get_store().get(job_id) is not None

    # BackgroundTasks ran inline under TestClient, so the next poll is terminal.
    final = client.get(f"/ui/jobs/{job_id}/progress")
    assert final.status_code == 200
    soup = BeautifulSoup(final.text, "lxml")

    # Terminal fragments must not carry the polling attributes.
    assert "hx-trigger" not in final.text
    assert soup.find("div", class_="results") is not None

    # Confidence badge for the known >80 score on t1.mkv.
    badges = soup.find_all("span", class_="badge")
    assert any("badge-green" in (b.get("class") or []) for b in badges)

    # Destination paths follow the Jellyfin layout.
    cells = [td.get_text(strip=True) for td in soup.find_all("td", class_="path")]
    assert any("Season 01" in c and "S01E01" in c for c in cells)


def test_ui_scan_missing_folder_returns_200_error_fragment(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """htmx doesn't swap 4xx bodies by default — error fragments are 200."""
    form = _scan_form(tmp_path / "missing", tmp_path / "out")
    resp = client.post("/ui/scan", data=form)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")
    assert soup.find("div", class_="error") is not None
    assert "folder not found" in resp.text


def test_ui_scan_accepts_blank_optional_numerics(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    form = _scan_form(mkv_folder, tmp_path / "out")
    form["year"] = ""
    form["tvdb_id"] = ""
    resp = client.post("/ui/scan", data=form)
    assert resp.status_code == 200
    assert BeautifulSoup(resp.text, "lxml").find("div", class_="progress") is not None


def test_ui_scan_rejects_unknown_series_key(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
) -> None:
    form = _scan_form(mkv_folder, tmp_path / "out")
    form["series_key"] = "doesnotexist"
    resp = client.post("/ui/scan", data=form)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")
    assert soup.find("div", class_="error") is not None


def test_index_has_provider_select(client: TestClient) -> None:
    soup = BeautifulSoup(client.get("/").text, "lxml")
    options = {o["value"] for o in soup.select("select[name=provider] option")}
    assert options == {"chakoteya", "opensubtitles"}


def test_ui_scan_opensubtitles_requires_tvdb_id(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
) -> None:
    form = _scan_form(mkv_folder, tmp_path / "out")
    form["provider"] = "opensubtitles"
    form["tvdb_id"] = ""
    resp = client.post("/ui/scan", data=form)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")
    assert soup.find("div", class_="error") is not None
    assert "TVDB id" in resp.text


def test_progress_still_polls_for_pending_job(client: TestClient) -> None:
    """A job that hasn't started yet should keep polling."""
    job = jobs_service.get_store().create(
        jobs_service.ScanRequest(
            folder=Path("/tmp"),
            series_key="tng",
            season=1,
            series_title="X",
        )
    )
    resp = client.get(f"/ui/jobs/{job.id}/progress")
    assert resp.status_code == 200
    assert 'hx-trigger="every 1s"' in resp.text
    assert 'hx-get="/ui/jobs/' in resp.text


def test_progress_for_unknown_job_returns_error_fragment(client: TestClient) -> None:
    resp = client.get("/ui/jobs/deadbeef/progress")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")
    assert soup.find("div", class_="error") is not None
    # No polling attributes — the page should stop hammering us for an unknown job.
    assert "hx-trigger" not in resp.text


def test_ui_apply_dry_run_does_not_move(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, library_root))
    job_id = BeautifulSoup(resp.text, "lxml").find("div", class_="progress")["hx-get"].split("/")[3]
    # Drive the job through to results.
    client.get(f"/ui/jobs/{job_id}/progress")

    # No `confirm` field → False → dry-run.
    applied = client.post(f"/ui/jobs/{job_id}/apply", data={})
    assert applied.status_code == 200
    assert "Dry-run" in applied.text
    assert sorted(p.name for p in mkv_folder.iterdir()) == ["t1.mkv", "t2.mkv"]
    assert not library_root.exists()


def test_ui_apply_with_confirm_on_moves_files(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, library_root))
    job_id = BeautifulSoup(resp.text, "lxml").find("div", class_="progress")["hx-get"].split("/")[3]
    client.get(f"/ui/jobs/{job_id}/progress")

    applied = client.post(f"/ui/jobs/{job_id}/apply", data={"confirm": "on"})
    assert applied.status_code == 200
    assert "Applied" in applied.text
    # Jellyfin unconfigured -> the fallback "trigger a library scan" hint shows.
    assert "Trigger a library scan in Jellyfin" in applied.text

    moved = sorted(p.name for p in library_root.rglob("*.mkv"))
    assert moved == [
        "Star Trek The Next Generation - S01E01 - Engage.mkv",
        "Star Trek The Next Generation - S01E02 - Battle Stations.mkv",
    ]
    assert list(mkv_folder.iterdir()) == []


def test_ui_apply_shows_jellyfin_trigger_message(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, library_root))
    job_id = BeautifulSoup(resp.text, "lxml").find("div", class_="progress")["hx-get"].split("/")[3]
    client.get(f"/ui/jobs/{job_id}/progress")

    with patch(
        "app.services.jobs.jellyfin.trigger_rescan",
        return_value=jobs_service.jellyfin.JellyfinStatus.triggered(),
    ):
        applied = client.post(f"/ui/jobs/{job_id}/apply", data={"confirm": "on"})

    assert applied.status_code == 200
    assert "Jellyfin library scan triggered" in applied.text


def test_ui_apply_shows_jellyfin_failure_message(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    library_root = tmp_path / "out"
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, library_root))
    job_id = BeautifulSoup(resp.text, "lxml").find("div", class_="progress")["hx-get"].split("/")[3]
    client.get(f"/ui/jobs/{job_id}/progress")

    with patch(
        "app.services.jobs.jellyfin.trigger_rescan",
        return_value=jobs_service.jellyfin.JellyfinStatus.failed("ConnectError: refused"),
    ):
        applied = client.post(f"/ui/jobs/{job_id}/apply", data={"confirm": "on"})

    assert applied.status_code == 200
    assert "Jellyfin rescan failed" in applied.text
    assert "ConnectError: refused" in applied.text


def test_ui_apply_unknown_job_returns_error_fragment(client: TestClient) -> None:
    resp = client.post("/ui/jobs/deadbeef/apply", data={"confirm": "on"})
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "lxml")
    assert soup.find("div", class_="error") is not None


def test_static_css_is_served(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "badge-green" in resp.text


def _pending_job() -> jobs_service.Job:
    """A job parked in PENDING — its background task never runs under TestClient."""
    return jobs_service.get_store().create(
        jobs_service.ScanRequest(folder=Path("/tmp"), series_key="tng", season=1, series_title="X")
    )


def test_index_job_panel_reconnects_on_load(client: TestClient) -> None:
    panel = BeautifulSoup(client.get("/").text, "lxml").find(id="job")
    assert panel is not None
    assert panel.get("hx-get") == "/ui/job"
    assert panel.get("hx-trigger") == "load"


def test_ui_job_empty_when_no_active_job(client: TestClient) -> None:
    resp = client.get("/ui/job")
    assert resp.status_code == 200
    assert resp.text.strip() == ""


def test_ui_job_reconnects_to_running_job(client: TestClient) -> None:
    job = _pending_job()
    resp = client.get("/ui/job")
    assert resp.status_code == 200
    assert f'hx-get="/ui/jobs/{job.id}/progress"' in resp.text
    assert 'hx-trigger="every 1s"' in resp.text


def test_ui_job_reconnects_to_results(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
    patched_pipeline: None,
) -> None:
    client.post("/ui/scan", data=_scan_form(mkv_folder, tmp_path / "out"))
    # A fresh tab hitting the index panel sees the finished results, not a blank form.
    panel = client.get("/ui/job")
    assert panel.status_code == 200
    assert BeautifulSoup(panel.text, "lxml").find("div", class_="results") is not None


def test_second_scan_while_active_reconnects_instead_of_duplicating(
    client: TestClient,
    mkv_folder: Path,
    tmp_path: Path,
) -> None:
    existing = _pending_job()
    resp = client.post("/ui/scan", data=_scan_form(mkv_folder, tmp_path / "out"))
    assert resp.status_code == 200
    # Reconnected to the in-flight job; no new job replaced it.
    assert f'hx-get="/ui/jobs/{existing.id}/progress"' in resp.text
    assert jobs_service.get_store().current().id == existing.id
