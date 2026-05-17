"""Tests for the fuzzy matcher."""

from __future__ import annotations

from pathlib import Path

from app.core.matcher import (
    EpisodeReference,
    FileSample,
    build_cost_matrix,
    match,
    score_pair,
)


def _ep(season: int, episode: int, title: str, text: str) -> EpisodeReference:
    return EpisodeReference(season=season, episode=episode, title=title, transcript=text)


def _fs(name: str, dialogue: str) -> FileSample:
    return FileSample(path=Path(f"/fake/{name}"), dialogue=dialogue)


def test_score_pair_handles_empty_input() -> None:
    assert score_pair("", "anything") == 0.0
    assert score_pair("anything", "") == 0.0
    assert score_pair("", "") == 0.0


def test_score_pair_high_for_overlapping_text() -> None:
    a = "Make it so number one engage warp factor seven"
    b = "Captain Picard said make it so engage warp factor seven number one"
    assert score_pair(a, b) >= 90


def test_score_pair_low_for_unrelated_text() -> None:
    a = "klingon battle cruiser decloaking off the starboard bow"
    b = "tea earl grey hot please computer"
    # token_set_ratio is generous with single-token overlap so the bar is low.
    assert score_pair(a, b) < 60


def test_cost_matrix_shape_and_values() -> None:
    files = [_fs("a.mkv", "alpha bravo charlie"), _fs("b.mkv", "delta echo foxtrot")]
    episodes = [
        _ep(1, 1, "Alphas", "alpha bravo charlie delta"),
        _ep(1, 2, "Echoes", "delta echo foxtrot golf hotel"),
        _ep(1, 3, "Foxes", "completely unrelated content here"),
    ]
    matrix = build_cost_matrix(files, episodes)
    assert matrix.shape == (2, 3)
    # File 0 should be cheapest against episode 0 (Alphas).
    assert matrix[0, 0] < matrix[0, 1]
    assert matrix[0, 0] < matrix[0, 2]
    # File 1 should be cheapest against episode 1 (Echoes).
    assert matrix[1, 1] < matrix[1, 0]
    assert matrix[1, 1] < matrix[1, 2]


def test_match_assigns_each_file_to_best_episode() -> None:
    files = [
        _fs("rand1.mkv", "tea earl grey hot computer arch end program"),
        _fs("rand2.mkv", "fire photon torpedoes shields up red alert"),
        _fs("rand3.mkv", "warp factor nine engage make it so"),
    ]
    episodes = [
        _ep(1, 1, "Engage", "warp factor nine engage make it so number one"),
        _ep(1, 2, "Battle", "fire photon torpedoes shields up red alert klingons"),
        _ep(1, 3, "Tea", "tea earl grey hot computer arch end program ready room"),
    ]
    matches = match(files, episodes)
    assert len(matches) == 3
    by_file = {m.file.path.name: m for m in matches}
    assert by_file["rand1.mkv"].best.episode.title == "Tea"
    assert by_file["rand2.mkv"].best.episode.title == "Battle"
    assert by_file["rand3.mkv"].best.episode.title == "Engage"
    # All matches should have high confidence given the synthetic data.
    for m in matches:
        assert m.confidence > 80


def test_match_hungarian_prevents_double_assignment() -> None:
    """If two files look similar to the same episode, the Hungarian
    assignment should still produce a one-to-one mapping rather than
    sending both files to the same target.
    """
    files = [
        _fs("a.mkv", "warp engine plasma conduit"),
        _fs("b.mkv", "warp engine plasma conduit"),
    ]
    episodes = [
        _ep(1, 1, "Warp", "warp engine plasma conduit dilithium chamber"),
        _ep(1, 2, "Other", "completely different topic about diplomacy"),
    ]
    matches = match(files, episodes)
    assigned_titles = {m.best.episode.title for m in matches}
    assert assigned_titles == {"Warp", "Other"}


def test_match_flags_low_confidence_for_review() -> None:
    files = [_fs("mystery.mkv", "qwerty asdf zxcv unrelated tokens")]
    episodes = [_ep(1, 1, "Something", "completely unrelated reference text")]
    matches = match(files, episodes)
    assert matches[0].needs_review is True


def test_match_empty_files_returns_empty() -> None:
    episodes = [_ep(1, 1, "X", "some text")]
    assert match([], episodes) == []
