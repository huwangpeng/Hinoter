#!/usr/bin/env python3
"""PENKITINFENG — Infinite-canvas (无边画布) stroke decoder for Huawei Hinote.

This module handles the second binary stroke format found in ``.hinote`` files.
Unlike the bounded ``PENCILENGINE`` format where each page has its own stroke
data, the infinite canvas uses a tiled grid: the stroke data is split across
multiple ``files/bsd_{grid_x}_{grid_y}_{uuid}.bin`` blocks.  Each block's
filename encodes its position on an abstract grid, and the visible viewport
is defined by the page's ``thumbnail_area`` metadata.

The point-table framing and point coordinates are decoded.  Stroke styles,
the 65-byte per-stroke payload, and the ``gsd``/``ged`` sidecar files are not
fully understood yet, so the result is suitable for inspection rather than
pixel-accurate export.

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

PENKIT_HEADER_SIZE = 52
"""Size of the confirmed BSD file header."""

PENKIT_POINT_STRIDE = 28
"""Confirmed point-record size (bytes) for PENKITINFENG, discovered via
binary analysis of ``sample/无边.hinote``."""

FILE_RE = re.compile(r"^bsd_(-?\d+)_(-?\d+)_")
"""Regex to extract grid coordinates (grid_x, grid_y) from file basename."""


# --- Data classes -------------------------------------------------------

@dataclass
class PenkitHeader:
    """The confirmed fields in the 100-byte block header.

    The remaining words are deliberately kept raw.  Earlier revisions named
    several of them ``color`` and ``base_width`` without enough evidence; the
    sample files show that those values are identifiers and framing data.
    """
    block_type: int = 0
    flags: int = 0
    block_id_high: int = 0
    block_id_low: int = 0
    data_length: int = 0
    stroke_count: int = 0
    raw_words: tuple[int, ...] = field(default_factory=tuple)


@dataclass
class PenkitGridEntry:
    """One GSD grid-to-BSD mapping record."""
    grid_x: int
    grid_y: int
    block_id_high: int
    block_id_low: int


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
    min_x: float = 0.0
    """Left edge of the decoded stroke bounds."""
    min_y: float = 0.0
    """Top edge of the decoded stroke bounds."""


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
    """Parse the 52-byte header of a raw BSD block.

    Returns ``None`` if the data is too short or doesn't start with the
    expected magic.
    """
    if len(data) < PENKIT_HEADER_SIZE or not is_penkit_block(data):
        return None

    # Offsets are absolute from start of block (magic is at 0-11)
    u32 = lambda o: struct.unpack_from(">I", data, o)[0]
    return PenkitHeader(
        block_type=u32(12),
        flags=u32(16),
        block_id_high=u32(20),
        block_id_low=u32(24),
        data_length=u32(36),
        stroke_count=u32(44),
        raw_words=tuple(u32(offset) for offset in range(28, 52, 4)),
    )


def parse_penkit_grid_index(data: bytes) -> tuple[float, list[PenkitGridEntry]]:
    """Parse the observed GSD grid index.

    Returns ``(grid_size, entries)``.  Unknown or malformed data returns an
    empty entry list instead of guessing.
    """
    if len(data) < 64 or not is_penkit_block(data):
        return (0.0, [])
    block_type = struct.unpack_from(">I", data, 12)[0]
    payload_size = struct.unpack_from(">I", data, 56)[0]
    count = struct.unpack_from(">I", data, 60)[0]
    if block_type != 3 or payload_size != count * 16 or 64 + payload_size > len(data):
        return (0.0, [])

    grid_size = struct.unpack_from(">f", data, 44)[0]
    entries: list[PenkitGridEntry] = []
    for index in range(count):
        offset = 64 + index * 16
        grid_x, grid_y, block_hi, block_lo = struct.unpack_from(">iiII", data, offset)
        entries.append(PenkitGridEntry(grid_x, grid_y, block_hi, block_lo))
    return (grid_size, entries)


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

    Huawei stores ``thumbnail_area`` as a JSON string nested inside ``data1``.
    Confirmed fields are ``centerX``, ``centerY`` and ``scalingFactor``.
    """
    result: dict[str, float] = {}
    try:
        import json
        parsed = json.loads(data1)
        area = parsed.get("thumbnail_area")
        if isinstance(area, str) and area:
            area = json.loads(area)
        if isinstance(area, dict):
            for key in ("centerX", "centerY", "scalingFactor"):
                if key in area:
                    result[key] = float(area[key])
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        pass
    return result


# --- Stroke decoding ----------------------------------------------------

PENKIT_DESCRIPTOR_SIZE = 48
PENKIT_STYLE_SIZES = (20, 16)
"""Style record sizes: 20 bytes from ``infinite_1.0.2`` on (an extra float,
observed as 1.0), 16 bytes in the earliest samples.  Detected per block."""


def _walk_penkit_strokes(data: bytes, count: int, style_size: int) -> int | None:
    """Return the end offset if ``count`` strokes parse cleanly, else ``None``."""
    offset = PENKIT_HEADER_SIZE
    for _ in range(count):
        table_at = offset + PENKIT_DESCRIPTOR_SIZE
        style_at = table_at + 8 + 32 + 1
        if style_at + style_size > len(data):
            return None
        n, stride = struct.unpack_from(">II", data, table_at)
        end = style_at + style_size + n * PENKIT_POINT_STRIDE
        if (stride != PENKIT_POINT_STRIDE
                or struct.unpack_from(">I", data, offset + 12)[0] != n * stride
                or struct.unpack_from(">I", data, table_at + 36)[0] != 0xFFFFFFFF
                or data[style_at - 1] != 1
                or end > len(data)):
            return None
        offset = end
    return offset


def penkit_color(value: int) -> tuple[tuple[int, int, int], float]:
    """Stroke colours are stored as ``0xAABBGGRR``.

    Confirmed against the app-rendered thumbnail: ``0xFF0000FF`` is drawn red
    and ``0xFFF7590A`` is the HarmonyOS blue ``#0A59F7``.
    """
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF), ((value >> 24) & 0xFF) / 255.0


def decode_penkit_strokes(data: bytes) -> list[dict]:
    """Extract strokes from a raw PENKITINFENG block.

    Every stroke is laid out sequentially (big-endian)::

        48 bytes   descriptor (+12: point_count * 28)
         8 bytes   point_count, 28
        32 bytes   2 x f32, bbox (4 x f32), sequence, 0xFFFFFFFF
         1 byte    0x01
        16/20      style: pen_type, base_width (f32), color (ABGR), opacity (f32) [, f32]
        n x 28     points: x, y (f32), elapsed_ms (u32), pressure (f32), f32, 0, 0

    The two leading metadata floats are usually ``0`` and ``count - 1`` but
    hold other values on very short strokes (dots), so they are not used for
    validation.  The block may end with zero padding.
    """
    if len(data) < PENKIT_HEADER_SIZE or not is_penkit_block(data):
        return []

    header = parse_penkit_header(data)
    if header is None or header.block_type != 1:
        return []

    for style_size in PENKIT_STYLE_SIZES:
        end = _walk_penkit_strokes(data, header.stroke_count, style_size)
        if end is not None and not any(data[end:]):
            break
    else:
        return []

    strokes: list[dict] = []
    offset = PENKIT_HEADER_SIZE
    for stroke_index in range(header.stroke_count):
        descriptor_at = offset
        table_at = descriptor_at + PENKIT_DESCRIPTOR_SIZE
        count = struct.unpack_from(">I", data, table_at)[0]
        bbox = struct.unpack_from(">ffff", data, table_at + 16)
        style_at = table_at + 8 + 32 + 1
        pen_type, base_width, color_value, style_opacity = struct.unpack_from(">IfIf", data, style_at)
        color, alpha = penkit_color(color_value)
        point_data = style_at + style_size

        points: list[tuple[float, float]] = []
        pressures: list[float] = []
        elapsed_ms: list[int] = []
        for index in range(count):
            x, y, elapsed, pressure = struct.unpack_from(
                ">ffIf", data, point_data + index * PENKIT_POINT_STRIDE)
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if not (math.isfinite(pressure) and 0.0 < pressure <= 1.5):
                pressure = 0.5
            points.append((x, y))
            pressures.append(pressure)
            elapsed_ms.append(elapsed)

        if points:
            strokes.append({
                "points": points,
                "pressures": pressures,
                "elapsed_ms": elapsed_ms,
                "bbox": bbox,
                "color": color,
                "argb": color_value,
                "base_width": base_width,
                "opacity": alpha * style_opacity,
                "pen_type": pen_type,
                "stroke_index": stroke_index,
                "descriptor_offset": descriptor_at,
                "table_offset": table_at,
                "style_size": style_size,
            })

        offset = point_data + count * PENKIT_POINT_STRIDE

    return strokes


@dataclass
class PenkitElement:
    """Canvas geometry of one image or text box, from ``ged_*.bin``.

    ``(x, y)`` is the top-left of the unrotated box in canvas coordinates and
    ``angle`` is in radians around the box centre (clockwise on screen).
    Images are displayed at ``width x height``; text boxes at
    ``width x height x scale``.  ``element_id`` matches ``pageElement.id`` in
    the page JSON, whose normalised ``positionX/Y`` are viewport-relative and
    not usable for placement.
    """
    kind: int                  # 0x400 image, 0x800 text box
    width: float
    height: float
    scale: float
    x: float
    y: float
    angle: float
    element_id: str


def decode_penkit_elements(data: bytes) -> list[PenkitElement]:
    """Decode the element records of a GED block (block_type 4)."""
    if len(data) < 44 or not is_penkit_block(data) or struct.unpack_from(">I", data, 12)[0] != 4:
        return []
    count = struct.unpack_from(">I", data, 40)[0]
    offset = 44
    elements: list[PenkitElement] = []
    for _ in range(count):
        if offset + 12 + 32 + 4 > len(data):
            break
        length, kind = struct.unpack_from(">II", data, offset + 4)
        w, h, scale, x, y, _w0, _h0, angle = struct.unpack_from(">8f", data, offset + 12)
        id_len = struct.unpack_from(">I", data, offset + 44)[0]
        element_id = data[offset + 48:offset + 48 + id_len].decode("ascii", "replace")
        elements.append(PenkitElement(kind, w, h, scale, x, y, angle, element_id))
        offset += 8 + length
    return elements


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
            # bsd point records already use global canvas coordinates.  The
            # grid position is metadata and must not be added a second time.
            s["grid_x"] = blk.grid_x
            s["grid_y"] = blk.grid_y
            all_strokes.append(s)

    canvas.strokes = all_strokes

    # Coordinates are global; derive the extent from stroke bounds instead of
    # assuming a page-sized cell or shifting negative grid coordinates.
    if all_strokes:
        canvas.min_x = min(stroke["bbox"][0] for stroke in all_strokes)
        canvas.min_y = min(stroke["bbox"][1] for stroke in all_strokes)
        max_x = max(stroke["bbox"][2] for stroke in all_strokes)
        max_y = max(stroke["bbox"][3] for stroke in all_strokes)
        canvas.width = max_x - canvas.min_x
        canvas.height = max_y - canvas.min_y

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
            strokes = decode_penkit_strokes(blk.data)
            points = sum(len(stroke["points"]) for stroke in strokes)
            print(f"  Grid ({gk[0]}, {gk[1]})  "
                  f"strokes={len(strokes)}/{hdr.stroke_count}  "
                  f"points={points}  bytes={len(blk.data)}")
        else:
            print(f"  Grid ({gk[0]}, {gk[1]})  <header parse failed>")


if __name__ == "__main__":
    main()
