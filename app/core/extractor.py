"""Subtitle extraction from MKV files using ffprobe + ffmpeg.

For M1 we only handle text-based subtitles (SRT, ASS, mov_text). PGS/VobSub
support requires OCR and is deferred to M5.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pysubs2

log = logging.getLogger(__name__)


# Text-based subtitle codecs ffmpeg knows about. Image-based (hdmv_pgs_subtitle,
# dvd_subtitle) are excluded because they require OCR.
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}


@dataclass(frozen=True)
class SubtitleStream:
    """Metadata for one subtitle stream inside an MKV."""

    index: int
    codec: str
    language: str | None
    title: str | None
    forced: bool

    @property
    def is_text(self) -> bool:
        return self.codec in TEXT_SUBTITLE_CODECS


@dataclass(frozen=True)
class ExtractedSubtitles:
    """The result of extracting one subtitle track from one file."""

    source: Path
    stream: SubtitleStream
    events: list[pysubs2.SSAEvent]

    def dialogue_after(self, start_ms: int, line_count: int) -> str:
        """Return up to `line_count` dialogue lines starting from `start_ms`.

        We collapse each line to plain text and join with newlines so it can be
        fed straight into a fuzzy-matcher.
        """
        lines: list[str] = []
        for event in self.events:
            if event.start < start_ms:
                continue
            text = event.plaintext.strip()
            if not text:
                continue
            lines.append(text)
            if len(lines) >= line_count:
                break
        return "\n".join(lines)


def _run(cmd: list[str]) -> str:
    """Run a subprocess and return stdout. Raises on non-zero exit."""
    log.debug("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}"
        )
    return result.stdout


def probe_subtitle_streams(mkv: Path) -> list[SubtitleStream]:
    """List all subtitle streams in `mkv` via ffprobe."""
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_name:stream_tags=language,title:disposition=forced",
            "-of",
            "json",
            str(mkv),
        ]
    )
    data = json.loads(out)
    streams: list[SubtitleStream] = []
    for s in data.get("streams", []):
        tags = s.get("tags", {}) or {}
        disposition = s.get("disposition", {}) or {}
        streams.append(
            SubtitleStream(
                index=s["index"],
                codec=s.get("codec_name", "unknown"),
                language=tags.get("language"),
                title=tags.get("title"),
                forced=bool(disposition.get("forced", 0)),
            )
        )
    return streams


def pick_best_stream(
    streams: list[SubtitleStream],
    preferred_language: str = "eng",
) -> SubtitleStream | None:
    """Choose the best subtitle stream for matching.

    Preference order:
        1. text-based, preferred language, not forced
        2. text-based, preferred language, forced (fallback)
        3. text-based, any language, not forced
        4. text-based, any language, forced
    Returns None if no text-based stream exists.
    """
    text_streams = [s for s in streams if s.is_text]
    if not text_streams:
        return None

    def score(s: SubtitleStream) -> tuple[int, int, int]:
        # Lower is better.
        lang_match = 0 if s.language == preferred_language else 1
        forced_penalty = 1 if s.forced else 0
        return (lang_match, forced_penalty, s.index)

    return min(text_streams, key=score)


def extract_stream(mkv: Path, stream_index: int) -> list[pysubs2.SSAEvent]:
    """Extract one subtitle stream from `mkv` and parse it into SSAEvents."""
    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(mkv),
                "-map",
                f"0:{stream_index}",
                "-c:s",
                "ass",
                str(out_path),
            ]
        )
        subs = pysubs2.load(str(out_path))
        return list(subs.events)
    finally:
        out_path.unlink(missing_ok=True)


def extract_subtitles(mkv: Path) -> ExtractedSubtitles | None:
    """High-level: probe an MKV, pick the best track, extract its events.

    Returns None if the file has no text-based subtitle stream.
    """
    streams = probe_subtitle_streams(mkv)
    chosen = pick_best_stream(streams)
    if chosen is None:
        log.warning("no text-based subtitle stream in %s", mkv.name)
        return None
    events = extract_stream(mkv, chosen.index)
    return ExtractedSubtitles(source=mkv, stream=chosen, events=events)


def find_mkv_files(folder: Path) -> list[Path]:
    """Recursively collect `.mkv` files under `folder`, sorted for a stable order.

    Disc rippers like ARM and MakeMKV write each disc into its own
    subdirectory, so we walk the whole tree rather than just the top level.
    The suffix match is case-insensitive so `.MKV` rips aren't missed, and
    directories that happen to be named `*.mkv` are skipped.
    """
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".mkv")
