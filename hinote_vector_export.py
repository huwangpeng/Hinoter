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
    """Recover point lists from the stable count/stride point-table layout.

    Huawei's bounded-note format stores each pen stroke as a point table with a
    36-byte point stride. X and Y are the second and third big-endian floats.
    The surrounding pen metadata is version-dependent, so the parser validates
    each table itself rather than relying on a fixed record header length.
    """
    if not data.startswith(PENCIL_ENGINE):
        return []

    strokes: list[Stroke] = []
    for offset in range(0, len(data) - 12, 4):
        count = read_be_uint(data, offset)
        table_type = read_be_uint(data, offset + 4)
        stride = read_be_uint(data, offset + 8)
        reserved = read_be_uint(data, offset + 12)
        points_start = offset + 16
        points_end = points_start + count * stride
        if not (
            2 <= count <= 16_384
            and 1 <= table_type <= 8
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
        base_width = read_be_float(data, offset - 20) if offset >= 20 else 1.0
        if not math.isfinite(base_width) or not 0 < base_width <= 100:
            base_width = 1.0
        strokes.append(Stroke(points, pressures, base_width))
    return strokes


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
    return max(0.25, pressure * stroke.base_width * 10)


def svg_document(title: str, page: Page) -> str:
    paths = []
    for stroke in page.strokes:
        for index, ((x1, y1), (x2, y2)) in enumerate(zip(stroke.points, stroke.points[1:])):
            width = stroke_width(stroke, (stroke.pressures[index] + stroke.pressures[index + 1]) / 2)
            paths.append(
                f'<path d="M {fmt(x1)} {fmt(y1)} L {fmt(x2)} {fmt(y2)}" fill="none" stroke="#111" '
                f'stroke-width="{fmt(width)}" stroke-linecap="round" stroke-linejoin="round"/>'
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
        if image.mime_type != "image/jpeg":
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
                f"{fmt(image.width)} 0 0 {fmt(image.height)} -0.5 -0.5 cm",
                f"/Im{index} Do",
                "Q",
            ]
        )
    commands.append("0 0 0 RG")
    for stroke in page.strokes:
        for index, ((x1, y1), (x2, y2)) in enumerate(zip(stroke.points, stroke.points[1:])):
            width = stroke_width(stroke, (stroke.pressures[index] + stroke.pressures[index + 1]) / 2)
            commands.extend(
                [
                    f"{fmt(width)} w 1 J 1 j",
                    f"{fmt(x1)} {fmt(page.height - y1)} m",
                    f"{fmt(x2)} {fmt(page.height - y2)} l S",
                ]
            )
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


def write_pdf(destination: Path, pages: list[Page]) -> None:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_ids: list[int] = []
    for page in pages:
        image_ids: dict[int, int] = {}
        for index, image in enumerate(page.images):
            if image.mime_type != "image/jpeg":
                continue
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
        stream = pdf_stream(page)
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream")
        xobjects = " ".join(f"/Im{index} {object_id} 0 R" for index, object_id in image_ids.items())
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {fmt(page.width)} {fmt(page.height)}] "
                f"/Contents {content_id} 0 R /Resources << /XObject << {xobjects} >> >> >>"
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
    height = 1000.0
    width = height * ratio
    images: list[ImageElement] = []
    for element in page_data.get("pageElement", []):
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
        images.append(
            ImageElement(
                data=data,
                mime_type=mime_type,
                x=element.get("positionX", 0) * width,
                y=element.get("positionY", 0) * height,
                width=element.get("width", 1) * width,
                height=element.get("height", 1) * height,
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
