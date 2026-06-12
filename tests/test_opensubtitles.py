"""Tests for the OpenSubtitles provider.

All HTTP goes through `httpx.MockTransport` so the suite runs offline.
The fake API serves a two-episode season; episode 1 also has an
AI-translated sub with a huge download count that must be skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import TranscriptCache
from app.providers.opensubtitles import OpenSubtitlesProvider

SRT_BY_FILE_ID = {
    101: "1\n00:03:20,000 --> 00:03:22,000\nMake it so.\n\n2\n00:03:25,000 --> 00:03:27,000\nEngage.\n",
    102: "1\n00:03:20,000 --> 00:03:22,000\nShields up.\n\n2\n00:03:25,000 --> 00:03:27,000\nRed alert.\n",
    103: "1\n00:03:20,000 --> 00:03:22,000\nThird episode dialogue.\n",
    666: "1\n00:03:20,000 --> 00:03:22,000\nMACHINE TRANSLATED GARBAGE.\n",
}


def _sub(
    episode: int,
    file_id: int,
    *,
    title: str | None = None,
    downloads: int = 10,
    trusted: bool = False,
    ai: bool = False,
) -> dict[str, Any]:
    return {
        "id": str(file_id),
        "type": "subtitle",
        "attributes": {
            "language": "en",
            "download_count": downloads,
            "ai_translated": ai,
            "machine_translated": False,
            "from_trusted": trusted,
            "foreign_parts_only": False,
            "files": [{"file_id": file_id, "file_name": f"ep{episode}.srt"}],
            "feature_details": {
                "season_number": 1,
                "episode_number": episode,
                "title": title or f"Title {episode}",
            },
        },
    }


def _make_handler(
    pages: dict[int, list[dict[str, Any]]],
    download_log: list[int],
):
    total_pages = len(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/subtitles"):
            assert request.headers["Api-Key"] == "test-key"
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(
                200,
                json={
                    "page": page,
                    "total_pages": total_pages,
                    "data": pages.get(page, []),
                },
            )
        if path.endswith("/download"):
            import json

            file_id = json.loads(request.content)["file_id"]
            download_log.append(file_id)
            return httpx.Response(200, json={"link": f"https://dl.test/{file_id}.srt"})
        if request.url.host == "dl.test":
            file_id = int(path.strip("/").removesuffix(".srt"))
            return httpx.Response(200, content=SRT_BY_FILE_ID[file_id].encode())
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


def _provider(
    pages: dict[int, list[dict[str, Any]]],
    download_log: list[int],
    *,
    cache: TranscriptCache | None = None,
    api_key: str = "test-key",
) -> OpenSubtitlesProvider:
    client = httpx.Client(transport=httpx.MockTransport(_make_handler(pages, download_log)))
    return OpenSubtitlesProvider(api_key=api_key, client=client, cache=cache)


def test_fetch_season_returns_transcripts() -> None:
    pages = {1: [_sub(1, 101), _sub(2, 102, title="Battle Stations")]}
    provider = _provider(pages, [])

    transcripts = provider.fetch_season("71470", 1)

    assert [(t.season, t.episode) for t in transcripts] == [(1, 1), (1, 2)]
    assert "Make it so" in transcripts[0].text
    assert "Red alert" in transcripts[1].text
    assert transcripts[1].title == "Battle Stations"


def test_skips_ai_translated_subs() -> None:
    # The AI sub has far more downloads but must lose to the human one.
    pages = {1: [_sub(1, 666, downloads=99999, ai=True), _sub(1, 101, downloads=3)]}
    provider = _provider(pages, [])

    transcripts = provider.fetch_season("71470", 1)

    assert len(transcripts) == 1
    assert "Make it so" in transcripts[0].text
    assert "GARBAGE" not in transcripts[0].text


def test_prefers_trusted_then_downloads() -> None:
    pages = {1: [_sub(1, 102, downloads=500), _sub(1, 101, downloads=5, trusted=True)]}
    provider = _provider(pages, [])

    transcripts = provider.fetch_season("71470", 1)

    assert "Make it so" in transcripts[0].text  # trusted file 101 wins


def test_paginates_search_results() -> None:
    pages = {
        1: [_sub(1, 101), _sub(2, 102)],
        2: [_sub(3, 103)],
    }
    provider = _provider(pages, [])

    transcripts = provider.fetch_season("71470", 1)

    assert [t.episode for t in transcripts] == [1, 2, 3]


def test_cache_hits_skip_downloads(tmp_path: Path) -> None:
    pages = {1: [_sub(1, 101), _sub(2, 102)]}
    cache = TranscriptCache(tmp_path / "cache.db")
    download_log: list[int] = []

    _provider(pages, download_log, cache=cache).fetch_season("71470", 1)
    assert len(download_log) == 2

    transcripts = _provider(pages, download_log, cache=cache).fetch_season("71470", 1)
    assert len(download_log) == 2  # no new downloads
    assert len(transcripts) == 2
    assert "Make it so" in transcripts[0].text


def test_download_failure_skips_episode() -> None:
    pages = {1: [_sub(1, 101), _sub(2, 102)]}
    download_log: list[int] = []
    base = _make_handler(pages, download_log)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/download"):
            import json

            if json.loads(request.content)["file_id"] == 101:
                return httpx.Response(406, json={"message": "quota exceeded"})
        return base(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenSubtitlesProvider(api_key="test-key", client=client)

    transcripts = provider.fetch_season("71470", 1)

    assert [t.episode for t in transcripts] == [2]


def test_missing_api_key_raises() -> None:
    provider = _provider({1: []}, [], api_key="")
    with pytest.raises(ValueError, match="OPENSUBTITLES_API_KEY"):
        provider.fetch_season("71470", 1)


def test_non_numeric_series_key_raises() -> None:
    provider = _provider({1: []}, [])
    with pytest.raises(ValueError, match="TVDB id"):
        provider.fetch_season("tng", 1)


def test_no_results_raises() -> None:
    provider = _provider({1: []}, [])
    with pytest.raises(ValueError, match="no English subtitles"):
        provider.fetch_season("71470", 1)
