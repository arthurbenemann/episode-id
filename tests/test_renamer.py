"""Tests for the Jellyfin-format renamer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.renamer import (
    RenamePlan,
    SeriesInfo,
    apply_plan,
    build_episode_filename,
    build_path,
    build_season_folder,
    build_series_folder,
    sanitize_component,
)


class TestSanitize:
    def test_strips_reserved_characters(self) -> None:
        # All Jellyfin-reserved characters replaced with spaces.
        result = sanitize_component('Path/With\\Bad:Chars*And"Quotes?Here|<too>')
        for bad in '<>:"/\\|?*':
            assert bad not in result

    def test_collapses_whitespace(self) -> None:
        assert sanitize_component("a   b\t\tc\nd") == "a b c d"

    def test_strips_trailing_dots(self) -> None:
        assert sanitize_component("Title...") == "Title"

    def test_mash_pattern(self) -> None:
        # Per Jellyfin docs: M*A*S*H should become MASH not "M A S H".
        assert sanitize_component("M*A*S*H") == "MASH"

    def test_preserves_normal_titles(self) -> None:
        assert (
            sanitize_component("Star Trek The Next Generation") == "Star Trek The Next Generation"
        )


class TestSeriesFolder:
    def test_full_form(self) -> None:
        series = SeriesInfo(
            title="Star Trek: The Next Generation",
            year=1987,
            tvdb_id=71470,
        )
        # Colon is reserved on Windows so it must be stripped.
        result = build_series_folder(series)
        assert ":" not in result
        assert "(1987)" in result
        assert "[tvdbid-71470]" in result
        assert result.startswith("Star Trek The Next Generation")

    def test_no_provider_id_when_disabled(self) -> None:
        series = SeriesInfo(title="Show", year=2020, tvdb_id=123)
        result = build_series_folder(series, include_provider_id=False)
        assert "tvdbid" not in result
        assert result == "Show (2020)"

    def test_only_title_when_minimal(self) -> None:
        assert build_series_folder(SeriesInfo(title="Solo")) == "Solo"


class TestSeasonFolder:
    def test_zero_padded(self) -> None:
        assert build_season_folder(1) == "Season 01"
        assert build_season_folder(10) == "Season 10"

    def test_specials_use_zero(self) -> None:
        assert build_season_folder(0) == "Season 00"


class TestEpisodeFilename:
    def test_standard_episode(self) -> None:
        series = SeriesInfo(title="The Show")
        result = build_episode_filename(series, 1, 2, "The Title", ".mkv")
        assert result == "The Show - S01E02 - The Title.mkv"

    def test_multi_episode_range(self) -> None:
        series = SeriesInfo(title="The Show")
        result = build_episode_filename(series, 1, 1, "Pilot", ".mkv", end_episode=2)
        assert "S01E01-E02" in result

    def test_extension_normalised(self) -> None:
        series = SeriesInfo(title="X")
        # Extension can be passed with or without leading dot.
        assert build_episode_filename(series, 1, 1, "T", "mkv").endswith(".mkv")
        assert build_episode_filename(series, 1, 1, "T", ".mkv").endswith(".mkv")

    def test_no_title_drops_separator(self) -> None:
        series = SeriesInfo(title="The Show")
        result = build_episode_filename(series, 1, 2, "", ".mkv")
        assert result == "The Show - S01E02.mkv"


class TestBuildPath:
    def test_jellyfin_layout(self) -> None:
        series = SeriesInfo(
            title="Star Trek The Next Generation",
            year=1987,
            tvdb_id=71470,
        )
        path = build_path(
            library_root=Path("/media/tv"),
            series=series,
            season=3,
            episode=15,
            episode_title="Yesterday's Enterprise",
            extension=".mkv",
        )
        # Verify the full expected layout in one go.
        expected = Path(
            "/media/tv/"
            "Star Trek The Next Generation (1987) [tvdbid-71470]/"
            "Season 03/"
            "Star Trek The Next Generation - S03E15 - Yesterday's Enterprise.mkv"
        )
        assert path == expected

    def test_specials_go_in_season_00(self) -> None:
        series = SeriesInfo(title="Show", year=2020)
        path = build_path(
            library_root=Path("/tv"),
            series=series,
            season=0,
            episode=1,
            episode_title="Bonus",
            extension=".mkv",
        )
        assert "Season 00" in str(path)


class TestApplyPlan:
    def test_dry_run_does_not_move(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        src.write_bytes(b"fake data")
        dest = tmp_path / "out" / "dest.mkv"
        plan = RenamePlan(source=src, destination=dest, confidence=99, needs_review=False)
        apply_plan(plan, dry_run=True)
        # Source still there, destination not created.
        assert src.exists()
        assert not dest.exists()

    def test_apply_moves_file_and_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        src.write_bytes(b"fake data")
        dest = tmp_path / "deeply" / "nested" / "dest.mkv"
        plan = RenamePlan(source=src, destination=dest, confidence=99, needs_review=False)
        apply_plan(plan, dry_run=False)
        assert not src.exists()
        assert dest.exists()
        assert dest.read_bytes() == b"fake data"

    def test_refuses_to_overwrite(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        src.write_bytes(b"source")
        dest = tmp_path / "dest.mkv"
        dest.write_bytes(b"existing")
        plan = RenamePlan(source=src, destination=dest, confidence=99, needs_review=False)
        with pytest.raises(FileExistsError):
            apply_plan(plan, dry_run=False)
        # Both files should still be intact.
        assert src.read_bytes() == b"source"
        assert dest.read_bytes() == b"existing"
