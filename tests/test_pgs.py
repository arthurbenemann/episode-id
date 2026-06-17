"""Unit tests for the PGS (Blu-ray bitmap subtitle) parser.

Pure parsing/rendering — no ffmpeg or Tesseract — so it runs in the fast
unit suite. The `build_*` helpers assemble a minimal but spec-valid `.sup`
byte stream and double as documentation of the segment layout; they're also
imported by `test_extractor` to exercise the OCR pipeline end to end.
"""

from __future__ import annotations

import struct

from app.core.pgs import _decode_rle, parse_sup

# Segment type codes.
_PDS, _ODS, _PCS, _END = 0x14, 0x15, 0x16, 0x80


def _segment(stype: int, pts: int, payload: bytes) -> bytes:
    # 2 magic + 4 PTS + 4 DTS + 1 type + 2 size + payload
    return (
        b"PG"
        + struct.pack(">II", pts, 0)
        + bytes([stype])
        + struct.pack(">H", len(payload))
        + payload
    )


def _pcs(state: int, palette_id: int, objects: list[tuple[int, int, int]]) -> bytes:
    p = struct.pack(">HHB", 1920, 1080, 0x10)  # video width, height, frame rate
    p += struct.pack(">H", 0)  # composition number
    p += bytes([state, 0, palette_id, len(objects)])  # state, palette_update, palette_id, count
    for obj_id, x, y in objects:
        p += struct.pack(">H", obj_id) + bytes([0, 0]) + struct.pack(">HH", x, y)
    return p


def _pds(palette_id: int, entries: dict[int, tuple[int, int, int, int]]) -> bytes:
    p = bytes([palette_id, 0])
    for idx, (y, cr, cb, a) in entries.items():
        p += bytes([idx, y, cr, cb, a])
    return p


def _ods(obj_id: int, width: int, height: int, rle: bytes) -> bytes:
    data_len = 4 + len(rle)  # width(2) + height(2) + rle
    return (
        struct.pack(">H", obj_id)
        + bytes([0, 0xC0])  # version, sequence flag (first + last)
        + struct.pack(">I", data_len)[1:]  # 3-byte object data length
        + struct.pack(">HH", width, height)
        + rle
    )


def build_caption_sup(
    *,
    width: int,
    height: int,
    rle: bytes,
    palette: dict[int, tuple[int, int, int, int]],
    start_ms: int,
    end_ms: int,
) -> bytes:
    """Assemble a one-caption `.sup`: show display set, then a clearing one."""
    start_pts, end_pts = start_ms * 90, end_ms * 90
    show = (
        _segment(_PCS, start_pts, _pcs(0x80, 0, [(0, 0, 0)]))  # epoch start, object at (0,0)
        + _segment(_PDS, start_pts, _pds(0, palette))
        + _segment(_ODS, start_pts, _ods(0, width, height, rle))
        + _segment(_END, start_pts, b"")
    )
    clear = _segment(_PCS, end_pts, _pcs(0x00, 0, [])) + _segment(_END, end_pts, b"")
    return show + clear


# A 4x2 caption: top row opaque white (palette index 1), bottom row transparent.
WHITE_TOP_RLE = bytes([1, 1, 1, 1, 0, 0, 0, 4, 0, 0])
WHITE_PALETTE = {1: (235, 128, 128, 255)}


def test_decode_rle_handles_all_run_codes() -> None:
    # single pixel, short color-0 run, color run, extended color-0 run,
    # extended color run, end-of-line.
    data = bytes(
        [0x07, 0x00, 0x03, 0x00, 0x82, 0x09, 0x00, 0x40, 0x05, 0x00, 0xC0, 0x04, 0x0B, 0x00, 0x00]
    )
    expected = bytes([7, 0, 0, 0, 9, 9, 0, 0, 0, 0, 0, 11, 11, 11, 11])
    assert _decode_rle(data, 15, 1) == expected


def test_decode_rle_pads_short_lines_to_width() -> None:
    # One pixel then end-of-line, but width is 4 -> padded with index 0.
    assert _decode_rle(bytes([0x05, 0x00, 0x00]), 4, 1) == bytes([5, 0, 0, 0])


def test_parse_sup_yields_one_timed_caption() -> None:
    data = build_caption_sup(
        width=4,
        height=2,
        rle=WHITE_TOP_RLE,
        palette=WHITE_PALETTE,
        start_ms=1000,
        end_ms=3000,
    )
    subs = parse_sup(data)

    assert len(subs) == 1
    sub = subs[0]
    assert sub.start_ms == 1000
    assert sub.end_ms == 3000
    assert sub.image.size == (4, 2)


def test_parse_sup_renders_inverted_for_ocr() -> None:
    # Light text on transparent background should come out dark-on-white.
    sub = parse_sup(
        build_caption_sup(
            width=4, height=2, rle=WHITE_TOP_RLE, palette=WHITE_PALETTE, start_ms=0, end_ms=2000
        )
    )[0]
    assert sub.image.getpixel((0, 0)) < 64  # white text -> dark ink
    assert sub.image.getpixel((0, 1)) == 255  # transparent -> white background


def test_parse_sup_falls_back_to_default_duration() -> None:
    # No clearing display set: end time defaults to start + 3s.
    show = (
        _segment(_PCS, 0, _pcs(0x80, 0, [(0, 0, 0)]))
        + _segment(_PDS, 0, _pds(0, WHITE_PALETTE))
        + _segment(_ODS, 0, _ods(0, 4, 2, WHITE_TOP_RLE))
        + _segment(_END, 0, b"")
    )
    subs = parse_sup(show)
    assert len(subs) == 1
    assert subs[0].end_ms == 3000


def test_parse_sup_ignores_trailing_garbage() -> None:
    data = build_caption_sup(
        width=4, height=2, rle=WHITE_TOP_RLE, palette=WHITE_PALETTE, start_ms=500, end_ms=1500
    )
    # A truncated segment header at the end must not raise.
    assert len(parse_sup(data + b"PG\x00\x00")) == 1
