#!/usr/bin/env python3
"""Export verified Huawei PENCILENGINE strokes from .hinote files to SVG and PDF."""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import math
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path


PENCIL_ENGINE = b"PENCILENGINE"
PENKIT_INFINITE = b"PENKITINFENG"
POINT_STRIDE = 36


@dataclass
class Stroke:
    points: list[tuple[float, float]]
    pressures: list[float]
    base_width: float
    color: tuple[int, int, int]
    opacity: float
    pen_type: int = 0


def is_highlighter(stroke: Stroke) -> bool:
    """Highlighters (pen_type 5) are semi-transparent overlays that must sit
    below opaque ink so handwritten text shows on top of them."""
    return stroke.pen_type == 5 or stroke.opacity < 1.0


@dataclass
class ImageElement:
    data: bytes
    mime_type: str
    x: float
    y: float
    width: float
    height: float
    angle: float


@dataclass
class TextElement:
    lines: list[tuple[str, int, int, int, float]]  # (text, r, g, b, font_size)
    x: float
    y: float
    width: float
    height: float


@dataclass
class Page:
    name: str
    width: float
    height: float
    background: tuple[int, int, int]
    strokes: list[Stroke]
    images: list[ImageElement]
    texts: list[TextElement]
    background_template: str = ""


def finite_coordinate(value: float) -> bool:
    return math.isfinite(value) and -100_000 < value < 100_000


def read_be_uint(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def read_be_float(data: bytes, offset: int) -> float:
    return struct.unpack_from(">f", data, offset)[0]


def parse_pencilengine(data: bytes) -> list[Stroke]:
    """Recover the primary PENCILENGINE stroke chain.

    A bounded-note stroke has a 60-byte style record immediately before a
    ``[0, point_count, 36, 0]`` point table. The table is followed by 64 bytes
    of index data, then the next style record. Parsing that chain is essential:
    scanning every number-shaped table also finds auxiliary geometry and bends
    handwriting into unrelated paths.
    """
    if not data.startswith(PENCIL_ENGINE):
        return []

    strokes: list[Stroke] = []
    for offset in range(60, len(data) - 16, 4):
        table_prefix = read_be_uint(data, offset)
        count = read_be_uint(data, offset + 4)
        stride = read_be_uint(data, offset + 8)
        reserved = read_be_uint(data, offset + 12)
        points_start = offset + 16
        points_end = points_start + count * stride
        if not (
            table_prefix in (0, 2)
            and 2 <= count <= 16_384
            and stride == POINT_STRIDE
            and reserved == 0
        ):
            continue
        if points_end > len(data):
            continue

        points: list[tuple[float, float]] = []
        pressures: list[float] = []
        for index in range(count):
            point_offset = points_start + index * stride
            x = read_be_float(data, point_offset + 4)
            y = read_be_float(data, point_offset + 8)
            pressure = read_be_float(data, point_offset + 16)
            if not finite_coordinate(x) or not finite_coordinate(y):
                points = []
                break
            points.append((x, y))
            pressures.append(pressure if math.isfinite(pressure) and pressure > 0 else 0.0)
        if len(points) < 2:
            continue

        # Reject tables that happen to resemble a header inside metadata.
        x_span = max(x for x, _ in points) - min(x for x, _ in points)
        y_span = max(y for _, y in points) - min(y for _, y in points)
        if x_span == 0 and y_span == 0:
            continue
        if any(pressures):
            first_pressure = next(pressure for pressure in pressures if pressure > 0)
            for index, pressure in enumerate(pressures):
                if pressure == 0:
                    pressures[index] = first_pressure
                else:
                    first_pressure = pressure
        else:
            pressures = [0.2] * len(points)
        style_offset = offset - 60
        base_width = read_be_float(data, style_offset + 40)
        if not math.isfinite(base_width) or not 0 < base_width <= 100:
            base_width = 4.0
        pen_type = read_be_uint(data, style_offset + 12)
        if pen_type not in (1, 2, 3, 5):
            continue
        softness = read_be_float(data, style_offset + 32)
        color_value = read_be_uint(data, style_offset + 8)
        color, opacity = stroke_style(data, style_offset, color_value, pen_type, softness)
        strokes.append(Stroke(points, pressures, base_width, color, opacity, pen_type))
    return strokes


def stroke_color(value: int) -> tuple[int, int, int]:
    # Huawei uses 0xffffffff as the built-in black-ink sentinel. Other values
    # are stored as ARGB and can be copied directly into the vector output.
    if value in {0, 0xFFFFFFFF}:
        return (0, 0, 0)
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def stroke_style(
    data: bytes, style_offset: int, color_value: int, pen_type: int, softness: float,
) -> tuple[tuple[int, int, int], float]:
    if color_value not in {0, 0xFFFFFFFF}:
        return stroke_color(color_value), 1.0
    components = [read_be_float(data, style_offset + offset) for offset in (20, 24, 28)]
    if all(math.isfinite(c) and 0 <= c <= 1 for c in components) and any(components):
        # Color float triple storage order depends on the format version.
        # The flag at style_offset+4 marks newer files (0x01000000/0x01010000)
        # where normal pens/pencils/brushes store RGB.  Older files stored BGR,
        # which makes red and blue channels swap.  Highlighters (pen_type 5) are
        # always stored as BGR in every observed version.
        flag = read_be_uint(data, style_offset + 4)
        is_new = flag in {0x01000000, 0x01010000}
        if pen_type == 5 or not is_new:
            components = list(reversed(components))
        rgb = tuple(round(c * 255) for c in components)
        if pen_type == 5:
            return rgb, softness if math.isfinite(softness) and 0 < softness <= 1 else 0.35
        if pen_type == 3 and math.isfinite(softness) and 0 < softness <= 1:
            return rgb, softness
        return rgb, 1.0
    return (0, 0, 0), 1.0


def bounds(strokes: list[Stroke]) -> tuple[float, float, float, float]:
    xs = [x for stroke in strokes for x, _ in stroke.points]
    ys = [y for stroke in strokes for _, y in stroke.points]
    return min(xs), min(ys), max(xs), max(ys)


def page_geometry(strokes: list[Stroke]) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds(strokes)
    padding = 24.0
    return left - padding, top - padding, max(1.0, right - left + padding * 2), max(1.0, bottom - top + padding * 2)


def fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def stroke_width(stroke: Stroke, pressure: float) -> float:
    # Highlighters (pen_type 5) are marker-like: the app records a fixed
    # base_width and, in early hinote versions, stores tiny/noise pressure
    # values.  Ignore per-point pressure for them so the highlight bar has
    # the intended constant width on both old and new files.
    if stroke.pen_type == 5:
        return max(0.1, stroke.base_width * 0.8)
    return max(0.1, pressure * stroke.base_width * 0.8)


def color_hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


def stroke_outline(stroke: Stroke) -> list[tuple[float, float]]:
    samples = list(zip(stroke.points, stroke.pressures))
    n = len(samples)
    if n < 2:
        return []
    tangents: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0:
            dx = samples[1][0][0] - samples[0][0][0]
            dy = samples[1][0][1] - samples[0][0][1]
        elif i == n - 1:
            dx = samples[n - 1][0][0] - samples[n - 2][0][0]
            dy = samples[n - 1][0][1] - samples[n - 2][0][1]
        else:
            dx = samples[i + 1][0][0] - samples[i - 1][0][0]
            dy = samples[i + 1][0][1] - samples[i - 1][0][1]
        L = math.hypot(dx, dy)
        tangents.append((dx / L, dy / L) if L else (1.0, 0.0))

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    cx: list[float] = []
    cy: list[float] = []
    rad: list[float] = []
    for i in range(n):
        tx, ty = tangents[i]
        nx, ny = -ty, tx
        r = stroke_width(stroke, samples[i][1]) / 2
        x, y = samples[i][0]
        left.append((x + nx * r, y + ny * r))
        right.append((x - nx * r, y - ny * r))
        cx.append(x)
        cy.append(y)
        rad.append(r)

    # Round caps: the endpoint pressure often tapers near zero (pen lift),
    # which shrinks the cap radius to a point and reads as a sharp angle. Floor
    # the endpoint radii to a fraction of the stroke's peak width so the cap
    # arc stays visibly round instead of collapsing to a tip.
    cap_floor = max(rad) * 0.45
    for idx in (0, n - 1):
        if rad[idx] < cap_floor:
            rad[idx] = cap_floor
            nx, ny = -tangents[idx][1], tangents[idx][0]
            x, y = samples[idx][0]
            left[idx] = (x + nx * cap_floor, y + ny * cap_floor)
            right[idx] = (x - nx * cap_floor, y - ny * cap_floor)

    outline = list(left)
    steps = 24
    a0 = math.atan2(left[n - 1][1] - cy[n - 1], left[n - 1][0] - cx[n - 1])
    for step in range(1, steps + 1):
        a = a0 - math.pi * step / steps
        outline.append((cx[n - 1] + rad[n - 1] * math.cos(a), cy[n - 1] + rad[n - 1] * math.sin(a)))
    outline.extend(reversed(right))
    a0 = math.atan2(right[0][1] - cy[0], right[0][0] - cx[0])
    for step in range(1, steps + 1):
        a = a0 - math.pi * step / steps
        outline.append((cx[0] + rad[0] * math.cos(a), cy[0] + rad[0] * math.sin(a)))
    # Chaikin corner-cutting: one pass rounds the polygonal facets between
    # offset samples so the filled edge reads smooth instead of serrated.
    return chaikin_smooth(outline)


def chaikin_smooth(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One pass of Chaikin corner-cutting on a closed polygon."""
    m = len(points)
    if m < 3:
        return points
    out: list[tuple[float, float]] = []
    for i in range(m):
        p0 = points[i]
        p1 = points[(i + 1) % m]
        out.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
        out.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
    return out


# --- Background templates (ruled / grid / dot pages) ---------------------

# Grid specs were measured from 页面样例.hinote thumbnails in page units
# (width = 1000).  Colors sampled from the app-rendered grid: a faint blue.
GRID_COLOR = (221, 221, 221)
GRID_LINE_WIDTH = 0.8
DOT_RADIUS = 1.3

BACKGROUND_GRIDS: dict[str, dict] = {
    "base2": {"type": "grid", "spacing": 101.0, "x0": 102.0, "y0": 101.0},   # medium grid
    "base3": {"type": "grid", "spacing": 58.3, "x0": 62.0, "y0": 59.0},      # small grid
    "base4": {"type": "hlines", "spacing": 75.0, "x0": 0.0, "y0": 71.0},     # wide ruled
    "base5": {"type": "hlines", "spacing": 47.0, "x0": 0.0, "y0": 47.0},    # narrow ruled
    "base6": {"type": "dots", "spacing": 33.0, "x0": 18.0, "y0": 17.0},      # dot grid
}


def grid_spec(background_template: str) -> dict | None:
    return BACKGROUND_GRIDS.get(background_template)


def grid_svg(spec: dict, width: float, height: float) -> list[str]:
    color = color_hex(GRID_COLOR)
    elements: list[str] = []
    kind = spec["type"]
    spacing = spec["spacing"]
    if kind in ("hlines", "grid"):
        y = spec["y0"]
        while y < height:
            elements.append(f'<line x1="0" y1="{fmt(y)}" x2="{fmt(width)}" y2="{fmt(y)}" stroke="{color}" stroke-width="{fmt(GRID_LINE_WIDTH)}"/>')
            y += spacing
    if kind == "grid":
        x = spec["x0"]
        while x < width:
            elements.append(f'<line x1="{fmt(x)}" y1="0" x2="{fmt(x)}" y2="{fmt(height)}" stroke="{color}" stroke-width="{fmt(GRID_LINE_WIDTH)}"/>')
            x += spacing
    elif kind == "dots":
        y = spec["y0"]
        while y < height:
            x = spec["x0"]
            while x < width:
                elements.append(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(DOT_RADIUS)}" fill="{color}"/>')
                x += spacing
            y += spacing
    return elements


def grid_pdf_commands(spec: dict, width: float, height: float) -> list[str]:
    kind = spec["type"]
    spacing = spec["spacing"]
    r, g, b = (c / 255 for c in GRID_COLOR)
    commands: list[str] = []
    if kind in ("hlines", "grid"):
        commands.append(f"{fmt(r)} {fmt(g)} {fmt(b)} RG")
        commands.append(f"{fmt(GRID_LINE_WIDTH)} w")
        y = spec["y0"]
        while y < height:
            py = height - y
            commands.append(f"0 {fmt(py)} m {fmt(width)} {fmt(py)} l S")
            y += spacing
        if kind == "grid":
            x = spec["x0"]
            while x < width:
                commands.append(f"{fmt(x)} 0 m {fmt(x)} {fmt(height)} l S")
                x += spacing
    elif kind == "dots":
        commands.append(f"{fmt(r)} {fmt(g)} {fmt(b)} rg")
        side = DOT_RADIUS * 2
        y = spec["y0"]
        while y < height:
            x = spec["x0"]
            py = height - y
            while x < width:
                # filled square centered at (x, py); 'arc' is not universally
                # supported by PDF readers, so a small rect is more portable.
                commands.append(f"{fmt(x - DOT_RADIUS)} {fmt(py - DOT_RADIUS)} {fmt(side)} {fmt(side)} re f")
                x += spacing
            y += spacing
    return commands


def svg_document(title: str, page: Page) -> str:
    paths = []
    # Highlighters paint first (bottom layer) so opaque ink sits on top of them.
    ordered = sorted(page.strokes, key=lambda s: 0 if is_highlighter(s) else 1)
    for stroke in ordered:
        outline = stroke_outline(stroke)
        if not outline:
            continue
        d = f"M {fmt(outline[0][0])} {fmt(outline[0][1])}"
        for pt in outline[1:]:
            d += f" L {fmt(pt[0])} {fmt(pt[1])}"
        d += " Z"
        paths.append(
            f'<path d="{d}" fill="{color_hex(stroke.color)}" fill-opacity="{fmt(stroke.opacity)}"/>'
        )
    images = []
    for image in page.images:
        encoded = base64.b64encode(image.data).decode("ascii")
        transform = ""
        if image.angle % 360:
            center_x = image.x + image.width / 2
            center_y = image.y + image.height / 2
            transform = f' transform="rotate({fmt(image.angle)} {fmt(center_x)} {fmt(center_y)})"'
        images.append(
            f'<image href="data:{image.mime_type};base64,{encoded}" x="{fmt(image.x)}" y="{fmt(image.y)}" '
            f'width="{fmt(image.width)}" height="{fmt(image.height)}"{transform}/>'
        )
    color = "#" + "".join(f"{channel:02x}" for channel in page.background)
    spec = grid_spec(page.background_template)
    grid_elements = grid_svg(spec, page.width, page.height) if spec else []
    text_els = []
    for te in page.texts:
        tspans = []
        for i, (txt, r, g, b, fs) in enumerate(te.lines):
            style = f"fill:rgb({r},{g},{b});font-size:{fmt(fs)}px"
            # Baseline of line i: top of box + font_size, each following line
            # advances by 1.2x font_size (matches the PDF text matrix math).
            baseline_y = te.y + fs * (1 + 1.2 * i)
            x_str = fmt(te.x) if i == 0 else fmt(te.x + te.width * 0.02)
            tspans.append(f'<tspan x="{x_str}" y="{fmt(baseline_y)}" style="{style}">{html.escape(txt)}</tspan>')
        text_els.append(f'<text xml:space="preserve">{chr(10).join(tspans)}</text>')
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'viewBox="0 0 {fmt(page.width)} {fmt(page.height)}">',
            f"<title>{html.escape(title)}</title>",
            f'<rect width="{fmt(page.width)}" height="{fmt(page.height)}" fill="{color}"/>',
            *grid_elements,
            *images,
            *text_els,
            *paths,
            "</svg>",
            "",
        ]
    )


def pdf_stream(page: Page, pdf_font: dict | None) -> bytes:
    red, green, blue = (channel / 255 for channel in page.background)
    commands = [f"{fmt(red)} {fmt(green)} {fmt(blue)} rg", f"0 0 {fmt(page.width)} {fmt(page.height)} re f"]
    spec = grid_spec(page.background_template)
    if spec:
        commands.extend(grid_pdf_commands(spec, page.width, page.height))
    for index, image in enumerate(page.images):
        if image.mime_type not in {"image/jpeg", "image/png"}:
            continue
        angle = math.radians(-image.angle)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        center_x = image.x + image.width / 2
        center_y = page.height - image.y - image.height / 2
        commands.extend(
            [
                "q",
                f"1 0 0 1 {fmt(center_x)} {fmt(center_y)} cm",
                f"{fmt(cosine)} {fmt(sine)} {fmt(-sine)} {fmt(cosine)} 0 0 cm",
                f"{fmt(image.width)} 0 0 {fmt(image.height)} {fmt(-image.width / 2)} {fmt(-image.height / 2)} cm",
                f"/Im{index} Do",
                "Q",
            ]
        )
    if pdf_font:
        cp_to_gid = pdf_font["cp_to_gid"]
        for text_element in page.texts:
            for index, (txt, red, green, blue, font_size) in enumerate(text_element.lines):
                # Baseline of line i sits font_size below the box top, then
                # each subsequent line advances by 1.2× the font size.
                baseline_y = text_element.y + font_size * (1 + 1.2 * index)
                pdf_y = page.height - baseline_y
                hex_glyphs = "".join(f"{cp_to_gid.get(ord(ch), 0):04x}" for ch in txt)
                if not hex_glyphs:
                    continue
                commands.append(f"{fmt(red / 255)} {fmt(green / 255)} {fmt(blue / 255)} rg")
                commands.append("BT")
                commands.append(f"/F1 {fmt(font_size)} Tf")
                commands.append(f"1 0 0 1 {fmt(text_element.x)} {fmt(pdf_y)} Tm")
                commands.append(f"<{hex_glyphs}> Tj")
                commands.append("ET")
    for stroke in sorted(page.strokes, key=lambda s: 0 if is_highlighter(s) else 1):
        outline = stroke_outline(stroke)
        if not outline:
            continue
        r, g, b = (c / 255 for c in stroke.color)
        commands.append(f"{fmt(r)} {fmt(g)} {fmt(b)} rg")
        commands.append(f"/GS{fmt(stroke.opacity).replace('.', '_')} gs")
        commands.append(f"{fmt(outline[0][0])} {fmt(page.height - outline[0][1])} m")
        for pt in outline[1:]:
            commands.append(f"{fmt(pt[0])} {fmt(page.height - pt[1])} l")
        commands.append("h f")
    return ("\n".join(commands) + "\n").encode("ascii")


def jpeg_size(data: bytes) -> tuple[int, int]:
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack_from(">H", data, offset)[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack_from(">HH", data, offset + 3)
            return width, height
        offset += length
    raise ValueError("JPEG dimensions were not found")


def png_to_pdf_image(data: bytes) -> tuple[int, int, bytes, bytes | None]:
    """Decode a non-interlaced RGB/RGBA PNG into PDF image sample data."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    offset = 8
    chunks: list[bytes] = []
    width = height = bit_depth = color_type = interlace = None
    while offset + 8 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        name = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if name == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif name == b"IDAT":
            chunks.append(payload)
        elif name == b"IEND":
            break
    if bit_depth != 8 or color_type not in {2, 6} or interlace != 0:
        raise ValueError("unsupported PNG layout")
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(b"".join(chunks))
    stride = width * channels
    rows: list[bytearray] = []
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        previous = rows[-1] if rows else bytearray(stride)
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                prediction = left + up - up_left
                distances = (abs(prediction - left), abs(prediction - up), abs(prediction - up_left))
                row[index] = (row[index] + (left, up, up_left)[distances.index(min(distances))]) & 0xFF
            elif filter_type != 0:
                raise ValueError("unsupported PNG filter")
        rows.append(row)
    if channels == 3:
        return width, height, b"".join(rows), None
    rgb = bytearray()
    alpha = bytearray()
    for row in rows:
        for index in range(0, stride, 4):
            rgb.extend(row[index:index + 3])
            alpha.append(row[index + 3])
    return width, height, bytes(rgb), bytes(alpha)


# --- CJK font subsetting for PDF text rendering ---------------------------

CJK_FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Songti.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def find_cjk_font() -> str | None:
    for candidate in CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def subset_cjk_font(characters: set[str]) -> dict | None:
    """Subset the system CJK font to only the glyphs needed for ``characters``.

    Returns a dict with the subsetted TrueType bytes, a codepoint→GID map,
    per-GID advance widths (scaled to 1000 units) and font metrics. Returns
    ``None`` when fontTools is unavailable or no CJK font is installed.
    """
    try:
        from fontTools import subset as ftsubset
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    font_path = find_cjk_font()
    if not font_path:
        return None
    text = "".join(sorted(characters))
    if not text:
        return None
    try:
        font = TTFont(font_path)
        options = ftsubset.Options()
        options.glyph_names = False
        options.name_IDs = []
        options.name_legacy = False
        options.name_languages = []
        options.notdef_outline = True
        options.recalc_bounds = True
        options.recalc_timestamp = False
        options.drop_tables = ["BASE", "JSTF", "DSIG", "EBDL", "EBDT", "EBSC", "PCLT", "LTSH", "MERG", "meta"]
        subsetter = ftsubset.Subsetter(options)
        subsetter.populate(text=text)
        subsetter.subset(font)
        # Normalize to 1000 unitsPerEm. SimHei ships with upem=256; some PDF
        # viewers assume a 1000-unit em for CIDFontType2 and would otherwise
        # rasterise the glyphs ~3.9x too large and jagged.
        from fontTools.ttLib.scaleUpem import scale_upem
        scale_upem(font, 1000)
    except Exception:
        return None
    import io
    buffer = io.BytesIO()
    font.save(buffer)
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    head = font["head"]
    hhea = font["hhea"]
    upem = head.unitsPerEm
    cp_to_gid: dict[int, int] = {}
    gid_to_width: dict[int, int] = {}
    for codepoint, glyph_name in cmap.items():
        try:
            gid = font.getGlyphID(glyph_name)
        except KeyError:
            continue
        cp_to_gid[codepoint] = gid
        try:
            advance = hmtx[glyph_name][0]
        except (KeyError, TypeError):
            advance = upem
        gid_to_width[gid] = round(advance * 1000 / upem)
    return {
        "data": buffer.getvalue(),
        "cp_to_gid": cp_to_gid,
        "gid_to_width": gid_to_width,
        "units_per_em": upem,
        "ascent": hhea.ascent,
        "descent": hhea.descent,
        "bbox": (head.xMin, head.yMin, head.xMax, head.yMax),
    }


def write_pdf(destination: Path, pages: list[Page]) -> None:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_ids: list[int] = []

    # Build one shared subsetted CJK font for every text element.
    all_characters: set[str] = set()
    for page in pages:
        for text_element in page.texts:
            for txt, *_ in text_element.lines:
                all_characters.update(txt)
    pdf_font = subset_cjk_font(all_characters) if all_characters else None
    if all_characters and not pdf_font:
        print(
            "warning: 检测到文本框但无法嵌入 CJK 字体（缺 fonttools 或系统无中文字体），"
            "PDF 文本将被跳过。请运行 `pip install fonttools` 后重试。"
        )
    font_ref = 0
    if pdf_font:
        font_file_id = len(objects) + 1
        objects.append(
            f"<< /Length {len(pdf_font['data'])} /Length1 {len(pdf_font['data'])} >>\nstream\n".encode("ascii")
            + pdf_font["data"]
            + b"\nendstream"
        )
        upem = pdf_font["units_per_em"]
        asc = pdf_font["ascent"] * 1000 / upem
        desc = pdf_font["descent"] * 1000 / upem
        x_min, y_min, x_max, y_max = pdf_font["bbox"]
        font_descriptor_id = len(objects) + 1
        objects.append(
            (
                f"<< /Type /FontDescriptor /FontName /HinoteCJK /Flags 4 "
                f"/FontBBox [{fmt(x_min * 1000 / upem)} {fmt(y_min * 1000 / upem)} "
                f"{fmt(x_max * 1000 / upem)} {fmt(y_max * 1000 / upem)}] "
                f"/ItalicAngle 0 /Ascent {fmt(asc)} /Descent {fmt(desc)} "
                f"/CapHeight {fmt(asc)} /StemV 80 /FontFile2 {font_file_id} 0 R >>"
            ).encode("ascii")
        )
        widths = " ".join(
            f"{gid} [{fmt(width)}]" for gid, width in sorted(pdf_font["gid_to_width"].items())
        )
        cid_font_id = len(objects) + 1
        objects.append(
            (
                "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /HinoteCJK "
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                f"/FontDescriptor {font_descriptor_id} 0 R /DW 1000 /W [{widths}] /CIDToGIDMap /Identity >>"
            ).encode("ascii")
        )
        to_unicode_id = len(objects) + 1
        gid_to_cp = {gid: cp for cp, gid in pdf_font["cp_to_gid"].items()}
        to_unicode_body = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        )
        pair_lines = [f"<{gid:04x}> <{cp:04x}>" for gid, cp in sorted(gid_to_cp.items())]
        for start in range(0, len(pair_lines), 100):
            block = pair_lines[start:start + 100]
            to_unicode_body += f"{len(block)} beginbfchar\n" + "\n".join(block) + "\nendbfchar\n"
        to_unicode_body += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
        objects.append(
            f"<< /Length {len(to_unicode_body)} >>\nstream\n".encode("ascii") + to_unicode_body.encode("ascii") + b"\nendstream"
        )
        font_ref = len(objects) + 1
        objects.append(
            "<< /Type /Font /Subtype /Type0 /BaseFont /HinoteCJK /Encoding /Identity-H "
            f"/DescendantFonts [{cid_font_id} 0 R] /ToUnicode {to_unicode_id} 0 R >>".encode("ascii")
        )
    for page in pages:
        image_ids: dict[int, int] = {}
        opacity_ids: dict[float, int] = {}
        for index, image in enumerate(page.images):
            if image.mime_type == "image/jpeg":
                image_width, image_height = jpeg_size(image.data)
                image_ids[index] = len(objects) + 1
                objects.append(
                    (
                        f"<< /Type /XObject /Subtype /Image /Width {image_width} /Height {image_height} "
                        "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                        f"/Length {len(image.data)} >>\nstream\n"
                    ).encode("ascii")
                    + image.data
                    + b"\nendstream"
                )
            elif image.mime_type == "image/png":
                image_width, image_height, rgb, alpha = png_to_pdf_image(image.data)
                alpha_id = None
                if alpha is not None:
                    alpha_id = len(objects) + 1
                    compressed_alpha = zlib.compress(alpha)
                    objects.append(
                        (
                            f"<< /Type /XObject /Subtype /Image /Width {image_width} /Height {image_height} "
                            "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                            f"/Length {len(compressed_alpha)} >>\nstream\n"
                        ).encode("ascii")
                        + compressed_alpha
                        + b"\nendstream"
                    )
                image_ids[index] = len(objects) + 1
                compressed_rgb = zlib.compress(rgb)
                mask = f" /SMask {alpha_id} 0 R" if alpha_id else ""
                objects.append(
                    (
                        f"<< /Type /XObject /Subtype /Image /Width {image_width} /Height {image_height} "
                        "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                        f"/Length {len(compressed_rgb)}{mask} >>\nstream\n"
                    ).encode("ascii")
                    + compressed_rgb
                    + b"\nendstream"
                )
        for opacity in sorted({stroke.opacity for stroke in page.strokes}):
            opacity_ids[opacity] = len(objects) + 1
            objects.append(f"<< /Type /ExtGState /CA {fmt(opacity)} /ca {fmt(opacity)} >>".encode("ascii"))
        stream = pdf_stream(page, pdf_font)
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream")
        xobjects = " ".join(f"/Im{index} {object_id} 0 R" for index, object_id in image_ids.items())
        states = " ".join(
            f"/GS{fmt(opacity).replace('.', '_')} {object_id} 0 R"
            for opacity, object_id in opacity_ids.items()
        )
        font_resource = f" /Font << /F1 {font_ref} 0 R >>" if font_ref else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {fmt(page.width)} {fmt(page.height)}] "
                f"/Contents {content_id} 0 R /Resources << /XObject << {xobjects} >> "
                f"/ExtGState << {states} >>{font_resource} >> >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    # Write through a temp file then atomically replace, so a PDF viewer
    # holding the destination open doesn't abort the export with EPERM.
    import os
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_bytes(output)
    try:
        os.replace(tmp, destination)
    except PermissionError:
        # Windows: target still locked. Keep the .tmp alongside so the user
        # gets the new output, and report the stale file clearly.
        print(f"warning: {destination} 被占用无法覆盖，新输出保存在 {tmp}（请关闭占用该 PDF 的程序后重试）")


def decode_jhinote(data: bytes) -> dict:
    return json.loads(gzip.decompress(data).decode("utf-8"))


def page_color(value: int) -> tuple[int, int, int]:
    color = value & 0xFFFFFFFF
    return (color >> 16 & 0xFF, color >> 8 & 0xFF, color & 0xFF)


def source_name(path: str) -> str:
    return Path(path.replace("\\", "/")).name


def build_page(page_data: dict, files: dict[str, bytes]) -> Page:
    ratio = page_data.get("pageRatio") or 0.706
    width = 1000.0
    height = width / ratio
    images: list[ImageElement] = []
    texts: list[TextElement] = []
    for element in sorted(page_data.get("pageElement", []), key=lambda item: item.get("positionZ", 0)):
        if element.get("elementType") == 1:
            data = files.get(source_name(element.get("filePath", "")))
            if not data:
                continue
            if data.startswith(b"\xff\xd8\xff"):
                mime_type = "image/jpeg"
            elif data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime_type = "image/png"
            else:
                continue
            element_width = element.get("width", 1) * width
            element_height = element.get("height", 1) * height
            images.append(ImageElement(
                data=data, mime_type=mime_type,
                x=element.get("positionX", 0) * width,
                y=element.get("positionY", 0) * height,
                width=element_width, height=element_height,
                angle=element.get("angle", 0),
            ))
        elif element.get("elementType") == 0:
            html = element.get("html", "")
            scale = element.get("scale", 1.0)
            src_dpi = int(json.loads(element.get("data1", "{}")).get("sourceDpi", 400))
            lines = []
            import html as html_mod
            import re
            for line_html in html.split("<br>"):
                m = re.search(r'<font color\s*=\s*"#([0-9a-fA-F]+)">.*?<hw_font size\s*=\s*"([^"]+)">(.*?)</hw_font>', line_html, re.DOTALL)
                if not m:
                    continue
                color_hex_val = m.group(1)
                font_size = float(m.group(2))
                text = html_mod.unescape(re.sub(r'<[^>]+>', '', m.group(3)))
                a_val = int(color_hex_val[:2], 16) if len(color_hex_val) >= 8 else 255
                r_val = int(color_hex_val[2:4], 16) if len(color_hex_val) >= 6 else 0
                g_val = int(color_hex_val[4:6], 16) if len(color_hex_val) >= 6 else 0
                b_val = int(color_hex_val[6:8], 16) if len(color_hex_val) >= 6 else 0
                if a_val < 128:
                    continue
                font_px = font_size * scale * src_dpi / 24
                lines.append((text, r_val, g_val, b_val, font_px))
            if lines:
                ex = max(0, element.get("positionX", 0) * width)
                ey = max(0, element.get("positionY", 0) * height)
                texts.append(TextElement(
                    lines=lines,
                    x=ex,
                    y=ey,
                    width=element.get("width", 1) * width,
                    height=element.get("height", 1) * height,
                ))
    strokes: list[Stroke] = []
    for attachment in page_data.get("attachment", []):
        data = files.get(source_name(attachment.get("filePath", "")))
        if data and data.startswith(PENCIL_ENGINE):
            strokes.extend(parse_pencilengine(data))
    return Page(
        name=f"page-{page_data.get('pageNumber', 0)}",
        width=width,
        height=height,
        background=page_color(page_data.get("pageColor", -1)),
        strokes=strokes,
        images=images,
        texts=texts,
        background_template=page_data.get("background", ""),
    )


def export_archive(archive_path: Path, output_root: Path) -> Path:
    destination = output_root / archive_path.stem
    raw = destination / "raw"
    svg_dir = destination / "svg"
    raw.mkdir(parents=True, exist_ok=True)
    svg_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Page] = []
    unsupported: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        files = {Path(info.filename).name: archive.read(info) for info in archive.infolist() if not info.is_dir()}
        page_data: list[dict] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            data = files[Path(info.filename).name]
            name = Path(info.filename).name
            if info.filename.startswith("pages/") and info.filename.endswith(".jhinote"):
                page_data.append(decode_jhinote(data)["customNotePageContent"])
            elif data.startswith(PENKIT_INFINITE):
                unsupported.append(f"{name}: PENKITINFENG infinite-canvas block needs a dedicated decoder")

        for data in sorted(page_data, key=lambda item: item.get("pageNumber", 0)):
            page = build_page(data, files)
            if page.strokes or page.images or grid_spec(page.background_template):
                title = f"{archive_path.name}: {page.name}"
                (svg_dir / f"{page.name}.svg").write_text(svg_document(title, page), encoding="utf-8")
                pages.append(page)

    if pages:
        write_pdf(destination / f"{archive_path.stem}.pdf", pages)
    lines = [f"# {archive_path.name} 矢量导出", "", f"- 已导出完整页面：{len(pages)}"]
    if pages:
        lines.append(f"- PDF：`{archive_path.stem}.pdf`，{len(pages)} 页")
    if unsupported:
        lines.extend(["", "## 未导出笔迹", "", *[f"- {item}" for item in unsupported]])
    (destination / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("vector-export"))
    args = parser.parse_args()
    for archive in args.archives:
        print(export_archive(archive, args.output))


if __name__ == "__main__":
    main()
