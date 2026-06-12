"""Tests for the SQLite transcript cache."""

from __future__ import annotations

from pathlib import Path

from app.db import TranscriptCache
from app.providers.base import EpisodeTranscript


def _t(season: int, episode: int, title: str = "T", text: str = "some dialogue"):
    return EpisodeTranscript(season=season, episode=episode, title=title, text=text)


def test_construction_is_lazy(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "cache.db"
    TranscriptCache(db)
    assert not db.exists()


def test_put_get_roundtrip(tmp_path: Path) -> None:
    cache = TranscriptCache(tmp_path / "cache.db")
    cache.put("chakoteya", "tng", _t(3, 15, "Yesterday's Enterprise", "history has changed"))

    got = cache.get("chakoteya", "tng", 3, 15)
    assert got is not None
    assert got.title == "Yesterday's Enterprise"
    assert got.text == "history has changed"

    assert cache.get("chakoteya", "tng", 3, 16) is None
    assert cache.get("opensubtitles", "tng", 3, 15) is None  # provider key separates rows


def test_get_season_sorted_by_episode(tmp_path: Path) -> None:
    cache = TranscriptCache(tmp_path / "cache.db")
    cache.put("chakoteya", "tng", _t(1, 2, "Second"))
    cache.put("chakoteya", "tng", _t(1, 1, "First"))
    cache.put("chakoteya", "tng", _t(2, 1, "Other season"))

    season = cache.get_season("chakoteya", "tng", 1)
    assert [t.episode for t in season] == [1, 2]
    assert [t.title for t in season] == ["First", "Second"]


def test_put_upserts(tmp_path: Path) -> None:
    cache = TranscriptCache(tmp_path / "cache.db")
    cache.put("chakoteya", "tng", _t(1, 1, "Old", "old text"))
    cache.put("chakoteya", "tng", _t(1, 1, "New", "new text"))

    got = cache.get("chakoteya", "tng", 1, 1)
    assert got is not None
    assert got.title == "New"
    assert got.text == "new text"
    assert len(cache.get_season("chakoteya", "tng", 1)) == 1
