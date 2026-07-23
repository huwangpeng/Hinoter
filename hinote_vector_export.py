#!/usr/bin/env python3
"""Export verified Huawei PENCILENGINE strokes from .hinote files to SVG and PDF."""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import math
import shutil
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
class Page:
    name: str
    width: float
    height: float
    background: tuple[int, int, int]
    strokes: list[Stroke]
    images: list[ImageElement]


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
            table_prefix == 0
            and 2 <= count <= 16_384
            and stride == POINT_STRIDE
            and reserved == 0
        ):
            continue
        # The 64-byte index segment after each table distinguishes primary
        # handwriting tables from coincidental sequences inside metadata.
        if points_end + 64 > len(data):
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
        color_value = read_be_uint(data, style_offset + 8)
        color, opacity = stroke_style(data, style_offset, color_value)
        strokes.append(Stroke(points, pressures, base_width, color, opacity))
    return strokes


def stroke_color(value: int) -> tuple[int, int, int]:
    # Huawei uses 0xffffffff as the built-in black-ink sentinel. Other values
    # are stored as ARGB and can be copied directly into the vector output.
    if value in {0, 0xFFFFFFFF}:
        return (0, 0, 0)
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def stroke_style(data: bytes, style_offset: int, color_value: int) -> tuple[tuple[int, int, int], float]:
    if color_value not in {0, 0xFFFFFFFF}:
        return stroke_color(color_value), 1.0
    components = [read_be_float(data, style_offset + offset) for offset in (20, 24, 28)]
    if all(math.isfinite(component) and 0 <= component <= 1 for component in components) and any(components):
        # These RGB fields are also used by ordinary colored handwriting. The
        # PENCILENGINE style segment has no reliable highlighter flag, so keep
        # them opaque rather than incorrectly fading colored text.
        return tuple(round(component * 255) for component in components), 1.0
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
    # The record stores normalized pressure and the enclosing pen record base nib.
    return max(0.1, pressure * stroke.base_width)


def color_hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


def stroke_outline(stroke: Stroke) -> tuple[list[tuple[float, float]], tuple[float, float], tuple[float, float]]:
    """Build one smooth variable-width polygon for a complete pen stroke."""
    samples = list(zip(stroke.points, stroke.pressures))
    compact: list[tuple[tuple[float, float], float]] = []
    for point, pressure in samples:
        if compact and point == compact[-1][0]:
            compact[-1] = (point, pressure)
        else:
            compact.append((point, pressure))
    if len(compact) < 2:
        return [], (0, 0), (0, 0)

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for index, ((x, y), pressure) in enumerate(compact):
        previous = compact[max(0, index - 1)][0]
        following = compact[min(len(compact) - 1, index + 1)][0]
        dx = following[0] - previous[0]
        dy = following[1] - previous[1]
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        normal_x = -dy / length
        normal_y = dx / length
        # Preserve a calligraphic taper at the two ends while keeping the
        # body width driven by the recorded pressure.
        taper = 0.2 if index in {0, len(compact) - 1} else 1.0
        radius = stroke_width(stroke, pressure) * taper / 2
        left.append((x + normal_x * radius, y + normal_y * radius))
        right.append((x - normal_x * radius, y - normal_y * radius))
    return left + list(reversed(right)), compact[0][0], compact[-1][0]


def svg_path(points: list[tuple[float, float]], start_tip: tuple[float, float], end_tip: tuple[float, float]) -> str:
    commands = [f"M {fmt(points[0][0])} {fmt(points[0][1])}"]
    for index in range(1, len(points) - 1):
        midpoint = ((points[index][0] + points[index + 1][0]) / 2, (points[index][1] + points[index + 1][1]) / 2)
        commands.append(f"Q {fmt(points[index][0])} {fmt(points[index][1])} {fmt(midpoint[0])} {fmt(midpoint[1])}")
    commands.append(f"Q {fmt(points[-1][0])} {fmt(points[-1][1])} {fmt(start_tip[0])} {fmt(start_tip[1])}")
    commands.append(f"Q {fmt(points[0][0])} {fmt(points[0][1])} {fmt(points[0][0])} {fmt(points[0][1])} Z")
    return " ".join(commands)


def pdf_path(points: list[tuple[float, float]], start_tip: tuple[float, float], page_height: float) -> list[str]:
    commands = [f"{fmt(points[0][0])} {fmt(page_height - points[0][1])} m"]
    current = points[0]
    for index in range(1, len(points) - 1):
        control = points[index]
        end = ((points[index][0] + points[index + 1][0]) / 2, (points[index][1] + points[index + 1][1]) / 2)
        cubic_one = (current[0] + (control[0] - current[0]) * 2 / 3, current[1] + (control[1] - current[1]) * 2 / 3)
        cubic_two = (end[0] + (control[0] - end[0]) * 2 / 3, end[1] + (control[1] - end[1]) * 2 / 3)
        commands.append(
            f"{fmt(cubic_one[0])} {fmt(page_height - cubic_one[1])} {fmt(cubic_two[0])} {fmt(page_height - cubic_two[1])} {fmt(end[0])} {fmt(page_height - end[1])} c"
        )
        current = end
    commands.append(f"{fmt(points[-1][0])} {fmt(page_height - points[-1][1])} {fmt(start_tip[0])} {fmt(page_height - start_tip[1])} {fmt(points[0][0])} {fmt(page_height - points[0][1])} c")
    return commands + ["h f"]


def svg_document(title: str, page: Page) -> str:
    paths = []
    for stroke in page.strokes:
        outline, start_tip, end_tip = stroke_outline(stroke)
        if outline:
            paths.append(
                f'<path d="{svg_path(outline, start_tip, end_tip)}" fill="{color_hex(stroke.color)}" fill-opacity="{fmt(stroke.opacity)}"/>'
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
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'viewBox="0 0 {fmt(page.width)} {fmt(page.height)}">',
            f"<title>{html.escape(title)}</title>",
            f'<rect width="{fmt(page.width)}" height="{fmt(page.height)}" fill="{color}"/>',
            *images,
            *paths,
            "</svg>",
            "",
        ]
    )


def pdf_stream(page: Page) -> bytes:
    red, green, blue = (channel / 255 for channel in page.background)
    commands = [f"{fmt(red)} {fmt(green)} {fmt(blue)} rg", f"0 0 {fmt(page.width)} {fmt(page.height)} re f"]
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
    for stroke in page.strokes:
        red, green, blue = (channel / 255 for channel in stroke.color)
        outline, start_tip, end_tip = stroke_outline(stroke)
        if not outline:
            continue
        commands.append(f"{fmt(red)} {fmt(green)} {fmt(blue)} rg")
        commands.append(f"/GS{fmt(stroke.opacity).replace('.', '_')} gs")
        commands.extend(pdf_path(outline, start_tip, page.height))
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


def write_pdf(destination: Path, pages: list[Page]) -> None:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_ids: list[int] = []
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
        stream = pdf_stream(page)
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream")
        xobjects = " ".join(f"/Im{index} {object_id} 0 R" for index, object_id in image_ids.items())
        states = " ".join(
            f"/GS{fmt(opacity).replace('.', '_')} {object_id} 0 R"
            for opacity, object_id in opacity_ids.items()
        )
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {fmt(page.width)} {fmt(page.height)}] "
                f"/Contents {content_id} 0 R /Resources << /XObject << {xobjects} >> /ExtGState << {states} >> >> >>"
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
    destination.write_bytes(output)


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
    for element in sorted(page_data.get("pageElement", []), key=lambda item: item.get("positionZ", 0)):
        if element.get("elementType") != 1:
            continue
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
        images.append(
            ImageElement(
                data=data,
                mime_type=mime_type,
                x=element.get("positionX", 0) * width,
                y=element.get("positionY", 0) * height,
                width=element_width,
                height=element_height,
                angle=element.get("angle", 0),
            )
        )
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
    )


def export_archive(archive_path: Path, output_root: Path) -> Path:
    destination = output_root / archive_path.stem
    raw = destination / "raw"
    svg_dir = destination / "svg"
    raw.mkdir(parents=True, exist_ok=True)
    if svg_dir.exists():
        shutil.rmtree(svg_dir)
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
            if page.strokes or page.images:
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
