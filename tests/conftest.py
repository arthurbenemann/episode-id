"""Shared fixtures for API + UI tests.

Both `tests/test_api.py` and `tests/test_ui.py` mock the same two heavy
steps — ffmpeg subtitle extraction and the Chakoteya HTTP fetch — so the
fast/unit-style tests run offline. The orchestrator, matcher, renamer,
and now the htmx views all run against real code.
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
    # 200_000 ms = 200s, past the default 180s sample_start_seconds.
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
    """Each test starts with an empty in-memory job store."""
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
            "app.providers.chakoteya.ChakoteyaProvider.fetch_season",
            new=_fake_fetch_season,
        ),
    ):
        yield
