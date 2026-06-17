"""Unit tests for input-file discovery and subtitle-stream handling.

`find_mkv_files` and `pick_best_stream` are pure logic. The OCR path is
exercised with ffmpeg and Tesseract mocked, so the whole module runs in the
fast unit suite (the real PGS bytes come from `test_pgs`'s builder).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pysubs2

from app.core.extractor import (
    SubtitleStream,
    extract_image_stream,
    extract_subtitles,
    find_mkv_files,
    pick_best_stream,
)
from tests.test_pgs import WHITE_PALETTE, WHITE_TOP_RLE, build_caption_sup


def _stream(
    index: int, codec: str, *, language: str = "eng", forced: bool = False
) -> SubtitleStream:
    return SubtitleStream(index=index, codec=codec, language=language, title=None, forced=forced)


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


# --- stream selection ---


def test_pick_prefers_text_over_pgs() -> None:
    chosen = pick_best_stream([_stream(0, "hdmv_pgs_subtitle"), _stream(1, "subrip")])
    assert chosen is not None and chosen.index == 1 and chosen.is_text


def test_pick_falls_back_to_pgs_when_no_text() -> None:
    streams = [
        _stream(0, "hdmv_pgs_subtitle", language="fre"),
        _stream(1, "hdmv_pgs_subtitle", language="eng"),
    ]
    chosen = pick_best_stream(streams)
    assert chosen is not None and chosen.index == 1 and chosen.is_image


def test_pick_prefers_english_non_forced_text() -> None:
    streams = [
        _stream(0, "subrip", language="eng", forced=True),
        _stream(1, "subrip", language="fre"),
        _stream(2, "subrip", language="eng"),
    ]
    assert pick_best_stream(streams).index == 2


def test_pick_returns_none_for_unsupported_only() -> None:
    # VobSub (dvd_subtitle) isn't OCR-able yet, so it's not "usable".
    assert pick_best_stream([_stream(0, "dvd_subtitle")]) is None


# --- OCR path ---


def test_extract_subtitles_uses_text_path_when_available() -> None:
    text_event = [pysubs2.SSAEvent(start=1000, end=2000, text="make it so")]
    with (
        patch("app.core.extractor.probe_subtitle_streams", return_value=[_stream(0, "subrip")]),
        patch("app.core.extractor.extract_stream", return_value=text_event) as text,
        patch("app.core.extractor.extract_image_stream") as image,
    ):
        result = extract_subtitles(Path("ep.mkv"))

    text.assert_called_once()
    image.assert_not_called()
    assert result is not None and result.events[0].plaintext == "make it so"


def test_extract_subtitles_ocrs_pgs_when_no_text() -> None:
    pgs_event = [pysubs2.SSAEvent(start=1000, end=2000, text="warp core breach")]
    with (
        patch(
            "app.core.extractor.probe_subtitle_streams",
            return_value=[_stream(2, "hdmv_pgs_subtitle")],
        ),
        patch("app.core.extractor.extract_image_stream", return_value=pgs_event) as image,
    ):
        result = extract_subtitles(Path("ep.mkv"))

    image.assert_called_once()
    assert result is not None
    assert result.stream.codec == "hdmv_pgs_subtitle"
    assert result.events[0].plaintext == "warp core breach"


def test_extract_subtitles_none_when_no_usable_stream() -> None:
    with patch(
        "app.core.extractor.probe_subtitle_streams",
        return_value=[_stream(0, "dvd_subtitle")],
    ):
        assert extract_subtitles(Path("ep.mkv")) is None


def test_extract_image_stream_parses_sup_and_ocrs() -> None:
    sup = build_caption_sup(
        width=4, height=2, rle=WHITE_TOP_RLE, palette=WHITE_PALETTE, start_ms=4000, end_ms=6000
    )

    def fake_ffmpeg(cmd: list[str]) -> str:
        # The .sup path is the last argument; drop our crafted bytes there.
        Path(cmd[-1]).write_bytes(sup)
        return ""

    with (
        patch("app.core.extractor._run", side_effect=fake_ffmpeg),
        patch("app.core.extractor._ocr_image", return_value="engage") as ocr,
    ):
        events = extract_image_stream(Path("video.mkv"), 2)

    ocr.assert_called_once()
    assert len(events) == 1
    assert events[0].start == 4000
    assert events[0].plaintext == "engage"
