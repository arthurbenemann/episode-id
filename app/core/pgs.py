"""Minimal PGS (Presentation Graphic Stream) parser for subtitle OCR.

Blu-ray subtitles are bitmaps, not text. To fingerprint an episode we have to
rasterise each caption and hand it to OCR. ffmpeg copies the PGS stream out of
the MKV into a `.sup` byte stream (`-c:s copy`); this module turns that stream
into per-caption images with timing, which `extractor` then OCRs.

Only the subset of the format needed for subtitle captions is implemented:
Presentation Composition (PCS), Palette Definition (PDS), Object Definition
(ODS) and End (END) segments. Window (WDS) segments are skipped — we render the
object bitmaps directly rather than compositing into screen windows.

For OCR we only need luminance contrast, so the YCrCb palette is collapsed to
its luma channel and composited over black; the final image is inverted so the
(usually light) text comes out dark-on-white, which Tesseract prefers.

Format reference:
https://blog.thescorpius.com/index.php/2017/07/15/presentation-graphic-stream-sup-files-bluray-subtitle-format/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageOps

log = logging.getLogger(__name__)

# Segment type codes.
_PDS = 0x14  # Palette Definition
_ODS = 0x15  # Object Definition (the RLE bitmap)
_PCS = 0x16  # Presentation Composition (defines a display set)
_WDS = 0x17  # Window Definition (ignored)
_END = 0x80  # End of display set

_HEADER_LEN = 13  # 2 magic + 4 PTS + 4 DTS + 1 type + 2 size
_PTS_HZ = 90  # PGS timestamps are a 90 kHz clock, so ms = pts // 90

# When a caption never gets an explicit "clear" composition, assume it stays up
# this long. Only used as a fallback; matching keys off start times anyway.
_DEFAULT_DURATION_MS = 3000


@dataclass(frozen=True)
class PgsSubtitle:
    """One rendered caption: an OCR-ready image plus its on-screen timing."""

    start_ms: int
    end_ms: int
    image: Image.Image


@dataclass
class _Composition:
    epoch_start: bool
    palette_id: int
    objects: list[tuple[int, int, int]] = field(default_factory=list)  # (obj_id, x, y)


def parse_sup(data: bytes) -> list[PgsSubtitle]:
    """Parse a PGS `.sup` byte stream into rendered, timed captions."""
    palettes: dict[int, dict[int, tuple[int, int]]] = {}  # palette_id -> {idx: (luma, alpha)}
    pending: dict[int, list] = {}  # obj_id -> [width, height, bytearray] mid-assembly
    objects: dict[int, tuple[int, int, bytes]] = {}  # obj_id -> (width, height, indices)

    comp: _Composition | None = None
    comp_ms = 0
    subs: list[PgsSubtitle] = []
    open_sub: int | None = None  # index into `subs` awaiting an end time

    for pts, stype, payload in _iter_segments(data):
        ms = pts // _PTS_HZ
        if stype == _PCS:
            comp = _parse_pcs(payload)
            comp_ms = ms
            if comp is not None and comp.epoch_start:
                palettes.clear()
                pending.clear()
                objects.clear()
        elif stype == _PDS:
            pid, entries = _parse_pds(payload)
            palettes.setdefault(pid, {}).update(entries)
        elif stype == _ODS:
            _accumulate_ods(payload, pending, objects)
        elif stype == _END:
            if comp is None:
                continue
            if comp.objects:
                image = _compose(comp, objects, palettes.get(comp.palette_id, {}))
                if image is not None:
                    subs.append(PgsSubtitle(start_ms=comp_ms, end_ms=comp_ms, image=image))
                    open_sub = len(subs) - 1
            elif open_sub is not None:
                # Empty composition = the previous caption is cleared now.
                subs[open_sub] = _with_end(subs[open_sub], comp_ms)
                open_sub = None
            comp = None

    return [_finalised(s) for s in subs]


def _iter_segments(data: bytes):
    """Yield (pts, segment_type, payload) for each well-formed segment."""
    pos, n = 0, len(data)
    while pos + _HEADER_LEN <= n:
        if data[pos : pos + 2] != b"PG":
            log.debug("PGS resync lost at offset %d; stopping", pos)
            break
        pts = int.from_bytes(data[pos + 2 : pos + 6], "big")
        stype = data[pos + 10]
        size = int.from_bytes(data[pos + 11 : pos + 13], "big")
        start = pos + _HEADER_LEN
        end = start + size
        if end > n:
            log.debug("PGS segment truncated at offset %d; stopping", pos)
            break
        yield pts, stype, data[start:end]
        pos = end


def _parse_pcs(payload: bytes) -> _Composition | None:
    if len(payload) < 11:
        return None
    state = payload[7]
    palette_id = payload[9]
    count = payload[10]
    comp = _Composition(epoch_start=(state == 0x80), palette_id=palette_id)
    i = 11
    for _ in range(count):
        if i + 8 > len(payload):
            break
        obj_id = int.from_bytes(payload[i : i + 2], "big")
        cropped = payload[i + 3]
        x = int.from_bytes(payload[i + 4 : i + 6], "big")
        y = int.from_bytes(payload[i + 6 : i + 8], "big")
        comp.objects.append((obj_id, x, y))
        i += 16 if cropped & 0x40 else 8  # skip the crop rectangle if present
    return comp


def _parse_pds(payload: bytes) -> tuple[int, dict[int, tuple[int, int]]]:
    palette_id = payload[0]
    entries: dict[int, tuple[int, int]] = {}
    i = 2  # skip palette_id + version
    while i + 5 <= len(payload):
        idx = payload[i]
        luma = payload[i + 1]  # Y; Cr/Cb (i+2, i+3) are unused for OCR
        alpha = payload[i + 4]
        entries[idx] = (luma, alpha)
        i += 5
    return palette_id, entries


def _accumulate_ods(
    payload: bytes,
    pending: dict[int, list],
    objects: dict[int, tuple[int, int, bytes]],
) -> None:
    """Collect an Object Definition, which may be split across fragments."""
    if len(payload) < 4:
        return
    obj_id = int.from_bytes(payload[0:2], "big")
    seq = payload[3]
    first, last = bool(seq & 0x80), bool(seq & 0x40)
    if first:
        # payload[4:7] is a 3-byte data length we don't need; width/height follow.
        width = int.from_bytes(payload[7:9], "big")
        height = int.from_bytes(payload[9:11], "big")
        pending[obj_id] = [width, height, bytearray(payload[11:])]
    elif obj_id in pending:
        pending[obj_id][2].extend(payload[4:])
    if last and obj_id in pending:
        width, height, rle = pending.pop(obj_id)
        objects[obj_id] = (width, height, _decode_rle(rle, width, height))


def _decode_rle(data: bytes, width: int, height: int) -> bytes:
    """Decode PGS run-length encoding into a flat row-major index buffer."""
    pixels = bytearray()
    line = bytearray()
    i, n = 0, len(data)

    def flush_line() -> None:
        if len(line) < width:
            line.extend(b"\x00" * (width - len(line)))
        pixels.extend(line[:width])
        line.clear()

    while i < n:
        b = data[i]
        i += 1
        if b != 0:
            line.append(b)
            continue
        if i >= n:
            break
        code = data[i]
        i += 1
        if code == 0:
            flush_line()
            continue
        flag = code & 0xC0
        low = code & 0x3F
        if flag == 0x00:
            length, color = low, 0
        elif flag == 0x40:
            length, color, i = (low << 8) | data[i], 0, i + 1
        elif flag == 0x80:
            length, color, i = low, data[i], i + 1
        else:  # 0xC0
            length = (low << 8) | data[i]
            color = data[i + 1]
            i += 2
        line.extend(bytes([color]) * length)

    if line:
        flush_line()

    expected = width * height
    if len(pixels) < expected:
        pixels.extend(b"\x00" * (expected - len(pixels)))
    return bytes(pixels[:expected])


def _render_object(
    width: int,
    height: int,
    indices: bytes,
    palette: dict[int, tuple[int, int]],
) -> Image.Image:
    """Render one object's index buffer to a luma image (light text on black)."""
    buf = bytearray(width * height)
    for i, idx in enumerate(indices):
        luma, alpha = palette.get(idx, (0, 0))
        buf[i] = (luma * alpha) // 255  # composite over black using luma only
    return Image.frombytes("L", (width, height), bytes(buf))


def _compose(
    comp: _Composition,
    objects: dict[int, tuple[int, int, bytes]],
    palette: dict[int, tuple[int, int]],
) -> Image.Image | None:
    """Paste a display set's objects onto one canvas and invert for OCR."""
    placed: list[tuple[int, int, Image.Image]] = []
    for obj_id, x, y in comp.objects:
        obj = objects.get(obj_id)
        if obj is None:
            continue
        width, height, indices = obj
        if width == 0 or height == 0:
            continue
        placed.append((x, y, _render_object(width, height, indices, palette)))

    if not placed:
        return None

    min_x = min(x for x, _, _ in placed)
    min_y = min(y for _, y, _ in placed)
    max_x = max(x + img.width for x, _, img in placed)
    max_y = max(y + img.height for _, y, img in placed)

    canvas = Image.new("L", (max_x - min_x, max_y - min_y), 0)
    for x, y, img in placed:
        region = (x - min_x, y - min_y, x - min_x + img.width, y - min_y + img.height)
        # `lighter` keeps text pixels when objects share the bounding box.
        canvas.paste(ImageChops.lighter(canvas.crop(region), img), region)
    return ImageOps.invert(canvas)


def _with_end(sub: PgsSubtitle, end_ms: int) -> PgsSubtitle:
    return PgsSubtitle(start_ms=sub.start_ms, end_ms=end_ms, image=sub.image)


def _finalised(sub: PgsSubtitle) -> PgsSubtitle:
    if sub.end_ms > sub.start_ms:
        return sub
    return _with_end(sub, sub.start_ms + _DEFAULT_DURATION_MS)
