#!/usr/bin/env python3
"""PENKITINFENG — Infinite-canvas (无边画布) stroke decoder for Huawei Hinote.

This module handles the second binary stroke format found in ``.hinote`` files.
Unlike the bounded ``PENCILENGINE`` format where each page has its own stroke
data, the infinite canvas uses a tiled grid: the stroke data is split across
multiple ``files/bsd_{grid_x}_{grid_y}_{uuid}.bin`` blocks.  Each block's
filename encodes its position on an abstract grid, and the visible viewport
is defined by the page's ``thumbnail_area`` metadata.

The format is **not yet fully decoded**.  This module provides the scaffolding
(file discovery, binary header parsing, known-field extraction) so that
downstream code can discover and classify blocks without importing the main
export pipeline.  Implement the decoder functions (``decode_block`` and
``assemble_canvas``) once the stride, style-record layout, and coordinate
mapping are reverse-engineered.

Usage::

    from hinote_infinite import (
        is_penkit_block,
        parse_penkit_header,
        discover_blocks,
        PENKIT_MAGIC,
    )

    blocks = discover_blocks(files)
    for bid, data in sorted(blocks.items()):
        hdr = parse_penkit_header(data)
        # … your custom decoder here …
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path


# --- Magic & constants -------------------------------------------------

PENKIT_MAGIC = b"PENKITINFENG"
"""Magic bytes that identify a PENKITINFENG binary block (12 bytes)."""

PENKIT_HEADER_SIZE = 100
"""Total header size for PENKITINFENG blocks (confirmed via sample analysis).

Includes: 12-byte magic + 88 bytes of style/state data.  The first point
table [count, stride=28, reserved=0] starts immediately after this header.
"""

PENKIT_POINT_STRIDE = 28
"""Confirmed point-record size (bytes) for PENKITINFENG, discovered via
binary analysis of ``sample/无边.hinote``."""

FILE_RE = re.compile(r"^bsd_(-?\d+)_(-?\d+)_")
"""Regex to extract grid coordinates (grid_x, grid_y) from file basename."""


# --- Data classes -------------------------------------------------------

@dataclass
class PenkitHeader:
    """Parsed fields from a 100-byte PENKITINFENG header (confirmed via
    ``sample/无边.hinote`` analysis).

    Fields are big-endian unless noted.  Offsets are relative to the
    start of the block (after the 12-byte magic).
    """
    block_type: int = 0            # [12:16] — always 0x00000001
    flags: int = 0                 # [16:20] — typically 0x00010000
    sequence_id: int = 0           # [20:24] — increments across edits (e.g. 0x0006567e)
    color: tuple[int, int, int] = (0, 0, 0)   # [24:28] — ARGB value
    base_width: int = 0            # [28:32] — BE uint (NOT float)
    unknown_32: int = 0            # [32:36] — always 8 in observed samples
    data_length: int = 0           # [36:40] — bytes after this header
    grid_total: int = 0            # [40:44] — total points across all blocks?
    stylus_type: int = 0           # [44:48] — pen type / tool ID
    extra_flag: int = 0            # [48:52] — per-block flag
    split_flag: int = 0            # [52:56] — splitting flag
    field_56: int = 0              # [56:60]
    field_60: int = 0              # [60:64]
    field_64: int = 0              # [64:68]
    field_68: int = 0              # [68:72]
    raw_72_100: bytes = field(default_factory=bytes)  # [72:100] — remaining 28 bytes


@dataclass
class PenkitBlock:
    """One infinite-canvas stroke block discovered in the archive."""
    grid_x: int
    """X coordinate of the grid cell (from filename ``bsd_X_Y_*``)."""
    grid_y: int
    """Y coordinate of the grid cell (from filename ``bsd_X_Y_*``)."""
    data: bytes
    """Raw binary content of the block (starts with ``PENKIT_MAGIC``)."""
    file_path: str = ""
    """Original path inside the ZIP archive (for debugging)."""


@dataclass
class InfiniteCanvas:
    """Represents a fully assembled infinite-canvas page.

    This is the output of ``assemble_canvas()`` once the decoder is
    implemented.  For now it serves as a placeholder.
    """
    blocks: list[PenkitBlock] = field(default_factory=list)
    """All discovered blocks belonging to this canvas."""

    thumbnail_area: str = ""
    """Value of ``thumbnail_area`` from the page's ``data1`` field."""

    strokes: list = field(default_factory=list)
    """Decoded stroke data (placeholder — type TBD)."""

    width: float = 1000.0
    """Canvas width in page units."""
    height: float = 1000.0
    """Canvas height in page units."""


# --- Utility helpers ----------------------------------------------------

def maybe_int(value: object, default: int = 0) -> int:
    """Coerce *value* to ``int``, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def source_name(path: str) -> str:
    """Return the basename of *path*, normalising backslashes."""
    return Path(path.replace("\\", "/")).name


# --- Block discovery ----------------------------------------------------

def is_penkit_block(data: bytes) -> bool:
    """Return ``True`` if *data* begins with the PENKITINFENG magic."""
    return data.startswith(PENKIT_MAGIC)


def parse_penkit_header(data: bytes) -> PenkitHeader | None:
    """Parse the 100-byte header of a raw PENKITINFENG block.

    Returns ``None`` if the data is too short or doesn't start with the
    expected magic.
    """
    if len(data) < PENKIT_HEADER_SIZE or not is_penkit_block(data):
        return None

    # Offsets are absolute from start of block (magic is at 0-11)
    u32 = lambda o: struct.unpack_from(">I", data, o)[0]
    f32 = lambda o: struct.unpack_from(">f", data, o)[0]

    hdr = PenkitHeader(
        block_type=u32(12),
        flags=u32(16),
        sequence_id=u32(20),
        base_width=u32(28),
        unknown_32=u32(32),
        data_length=u32(36),
        grid_total=u32(40),
        stylus_type=u32(44),
        extra_flag=u32(48),
        split_flag=u32(52),
        field_56=u32(56),
        field_60=u32(60),
        field_64=u32(64),
        field_68=u32(68),
        raw_72_100=data[72:PENKIT_HEADER_SIZE],
    )

    # Color: ARGB at offset 24
    color_raw = u32(24)
    if color_raw not in (0, 0xFFFFFFFF):
        hdr.color = ((color_raw >> 16) & 0xFF,
                     (color_raw >> 8) & 0xFF,
                     color_raw & 0xFF)

    return hdr


def grid_key(name: str) -> tuple[int, int] | None:
    """Extract ``(grid_x, grid_y)`` from a ``bsd_X_Y_*`` filename.

    Returns ``None`` if the name doesn't match the pattern.
    """
    m = FILE_RE.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def discover_blocks(files: dict[str, bytes]) -> dict[tuple[int, int], PenkitBlock]:
    """Scan *files* for PENKITINFENG blocks and return them keyed by grid
    position.

    *files* should be a mapping of ``{basename: raw_bytes}``, typically
    obtained from the ZIP archive.
    """
    blocks: dict[tuple[int, int], PenkitBlock] = {}
    for name, raw in files.items():
        if is_penkit_block(raw):
            gk = grid_key(name)
            if gk is not None:
                blocks[gk] = PenkitBlock(
                    grid_x=gk[0], grid_y=gk[1],
                    data=raw, file_path=name,
                )
    return blocks


def parse_thumbnail_area(data1: str) -> dict[str, float]:
    """Parse ``thumbnail_area`` from a page's ``data1`` JSON field.

    The value is typically a comma-separated string like
    ``"thumbnail_area": "-0.2,-0.5,1.2,1.5"`` representing the visible
    viewport in normalised coordinates ``(x_min, y_min, x_max, y_max)``
    relative to the grid.
    """
    result: dict[str, float] = {
        "x0": 0.0, "y0": 0.0,
        "x1": 1.0, "y1": 1.0,
    }
    # Try to extract from the nested JSON inside data1
    try:
        import json
        parsed = json.loads(data1)
        area = parsed.get("thumbnail_area", "")
        if area and isinstance(area, str):
            parts = [float(v) for v in area.split(",")]
            if len(parts) == 4:
                result["x0"], result["y0"], result["x1"], result["y1"] = parts
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return result


# --- Stroke decoding ----------------------------------------------------

def decode_penkit_strokes(data: bytes) -> list[dict]:
    """Extract strokes from a raw PENKITINFENG block.

    Supports both Big-Endian (BE) and Little-Endian (LE) table encodings,
    extracting 2D point coordinates and real-time stylus pressure data.
    """
    HEADER = PENKIT_HEADER_SIZE
    u32_be = lambda d, o: struct.unpack_from(">I", d, o)[0]
    u32_le = lambda d, o: struct.unpack_from("<I", d, o)[0]
    f32_be = lambda d, o: struct.unpack_from(">f", d, o)[0]

    color = (0, 0, 0)
    if len(data) >= 28:
        c_raw = u32_be(data, 24)
        if c_raw not in (0, 0xFFFFFFFF):
            r = (c_raw >> 16) & 0xFF
            g = (c_raw >> 8) & 0xFF
            b = c_raw & 0xFF
            color = (r, g, b)

    strokes: list[dict] = []

    for off in range(HEADER, len(data) - 12, 4):
        # 1. Try Big-Endian table pattern [count, 28, 0]
        cnt_be, st_be, z_be = struct.unpack_from(">III", data, off)
        if 2 <= cnt_be <= 16384 and st_be == PENKIT_POINT_STRIDE and z_be == 0:
            pts_start = off + 12
            pts_end = pts_start + cnt_be * st_be
            if pts_end <= len(data):
                r0 = pts_start
                min_x = f32_be(data, r0 + 4)
                min_y = f32_be(data, r0 + 8)
                max_x = f32_be(data, r0 + 12)
                max_y = f32_be(data, r0 + 16)

                points: list[tuple[float, float]] = []
                pressures: list[float] = []
                for ri in range(1, cnt_be):
                    ro = r0 + ri * st_be
                    x = f32_be(data, ro + 17)
                    y = f32_be(data, ro + 21)
                    p_raw = f32_be(data, ro + 1)
                    p = p_raw if (math.isfinite(p_raw) and 0.01 <= p_raw <= 2.0) else 0.5
                    if math.isfinite(x) and math.isfinite(y):
                        points.append((x, y))
                        pressures.append(p)

                if len(points) >= 2:
                    strokes.append({
                        "points": points,
                        "pressures": pressures,
                        "bbox": (min_x, min_y, max_x, max_y),
                        "color": color,
                        "base_width": 4.0,
                        "opacity": 1.0,
                        "is_le": False
                    })

        # 2. Try Little-Endian table pattern [count, 28, z_val != 0]
        cnt_le, st_le, z_le = struct.unpack_from("<III", data, off)
        if 2 <= cnt_le <= 16384 and st_le == PENKIT_POINT_STRIDE and z_le != 0:
            pts_start = off + 12
            pts_end = pts_start + cnt_le * st_le
            if pts_end <= len(data):
                r0 = pts_start
                points: list[tuple[float, float]] = []
                pressures: list[float] = []
                for ri in range(1, cnt_le):
                    ro = r0 + ri * st_le
                    x = f32_be(data, ro + 14)
                    y = f32_be(data, ro + 18)
                    p_raw = f32_be(data, ro + 16)
                    p = p_raw if (math.isfinite(p_raw) and 0.01 <= p_raw <= 2.0) else 0.5
                    if math.isfinite(x) and math.isfinite(y) and abs(x) < 100000 and abs(y) < 100000:
                        points.append((x, y))
                        pressures.append(p)

                if len(points) >= 2:
                    xs = [p[0] for p in points]
                    ys = [p[1] for p in points]
                    strokes.append({
                        "points": points,
                        "pressures": pressures,
                        "bbox": (min(xs), min(ys), max(xs), max(ys)),
                        "color": color,
                        "base_width": 4.0,
                        "opacity": 1.0,
                        "is_le": True
                    })

    return strokes


# --- Canvas assembly -----------------------------------------------------

def assemble_canvas(blocks: list[PenkitBlock],
                    thumbnail_area: str = "") -> InfiniteCanvas:
    """Assemble an ``InfiniteCanvas`` from a list of discovered blocks.

    Decodes strokes using ``decode_penkit_strokes()`` and places them
    at their grid-aligned positions within the canvas.
    """
    canvas = InfiniteCanvas()
    canvas.thumbnail_area = thumbnail_area
    all_strokes: list = []

    for blk in blocks:
        strokes = decode_penkit_strokes(blk.data)
        for s in strokes:
            # Translate points by grid offset
            s["grid_x"] = blk.grid_x
            s["grid_y"] = blk.grid_y
            all_strokes.append(s)

    canvas.strokes = all_strokes

    # Compute canvas extent from block positions
    if blocks:
        xs = [b.grid_x for b in blocks]
        ys = [b.grid_y for b in blocks]
        span_x = max(xs) - min(xs) + 1
        span_y = max(ys) - min(ys) + 1
        canvas.width = span_x * 1000.0
        canvas.height = span_y * 1000.0

    return canvas


# --- Command-line introspection ------------------------------------------

def main() -> None:
    """Quick introspection: read a ``.hinote`` file and list PENKIT blocks."""
    import argparse
    import zipfile

    parser = argparse.ArgumentParser(
        description="List PENKITINFENG blocks in a .hinote file"
    )
    parser.add_argument("archive", type=Path, help="Path to .hinote file")
    args = parser.parse_args()

    with zipfile.ZipFile(args.archive) as zf:
        raw_files = {Path(inf.filename).name: zf.read(inf)
                     for inf in zf.infolist() if not inf.is_dir()}

    blocks = discover_blocks(raw_files)
    if not blocks:
        print("No PENKITINFENG blocks found in", args.archive.name)
        return

    print(f"Found {len(blocks)} PENKITINFENG block(s):")
    for gk in sorted(blocks.keys()):
        blk = blocks[gk]
        hdr = parse_penkit_header(blk.data)
        if hdr:
            print(f"  Grid ({gk[0]}, {gk[1]})  stride={hdr.stride}  "
                  f"pen={hdr.pen_type}  width={hdr.base_width:.1f}  "
                  f"points_est={hdr.point_count}")
        else:
            print(f"  Grid ({gk[0]}, {gk[1]})  <header parse failed>")


if __name__ == "__main__":
    main()
