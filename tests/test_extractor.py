"""Unit tests for input-file discovery.

`find_mkv_files` is pure filesystem walking (no ffmpeg required), so it
belongs in the fast unit suite.
"""

from __future__ import annotations

from pathlib import Path

from app.core.extractor import find_mkv_files


def test_finds_top_level_mkvs(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").touch()
    (tmp_path / "b.mkv").touch()
    (tmp_path / "notes.txt").touch()
    assert find_mkv_files(tmp_path) == [tmp_path / "a.mkv", tmp_path / "b.mkv"]


def test_recurses_into_subdirectories(tmp_path: Path) -> None:
    # ARM/MakeMKV drop each disc into its own subfolder, with nothing at the top.
    disc = tmp_path / "DISC_LABEL"
    disc.mkdir()
    (disc / "title_t00.mkv").touch()
    nested = tmp_path / "season1" / "disc2"
    nested.mkdir(parents=True)
    (nested / "title_t01.mkv").touch()

    expected = sorted([disc / "title_t00.mkv", nested / "title_t01.mkv"])
    assert find_mkv_files(tmp_path) == expected


def test_suffix_match_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "rip.MKV").touch()
    assert find_mkv_files(tmp_path) == [tmp_path / "rip.MKV"]


def test_skips_directories_named_like_mkv(tmp_path: Path) -> None:
    (tmp_path / "weird.mkv").mkdir()
    (tmp_path / "real.mkv").touch()
    assert find_mkv_files(tmp_path) == [tmp_path / "real.mkv"]


def test_empty_folder_returns_empty_list(tmp_path: Path) -> None:
    assert find_mkv_files(tmp_path) == []
