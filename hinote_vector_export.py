#!/usr/bin/env python3
"""Export verified Huawei PENCILENGINE strokes from .hinote files to SVG and PDF."""

from __future__ import annotations

import argparse
import html
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
    width: float


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
        widths: list[float] = []
        for index in range(count):
            point_offset = points_start + index * stride
            x = read_be_float(data, point_offset + 4)
            y = read_be_float(data, point_offset + 8)
            width = read_be_float(data, point_offset + 28)
            if not finite_coordinate(x) or not finite_coordinate(y):
                points = []
                break
            points.append((x, y))
            if math.isfinite(width) and 0 < width < 100:
                widths.append(width)
        if len(points) < 2:
            continue

        # Reject tables that happen to resemble a header inside metadata.
        x_span = max(x for x, _ in points) - min(x for x, _ in points)
        y_span = max(y for _, y in points) - min(y for _, y in points)
        if x_span == 0 and y_span == 0:
            continue
        strokes.append(Stroke(points, max(0.2, sum(widths) / len(widths)) if widths else 1.0))
    return strokes


def bounds(strokes: list[Stroke]) -> tuple[float, float, float, float]:
    xs = [x for stroke in strokes for x, _ in stroke.points]
    ys = [y for stroke in strokes for _, y in stroke.points]
    return min(xs), min(ys), max(xs), max(ys)


def page_geometry(strokes: list[Stroke]) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds(strokes)
    padding = max(24.0, max(stroke.width for stroke in strokes) * 4)
    return left - padding, top - padding, max(1.0, right - left + padding * 2), max(1.0, bottom - top + padding * 2)


def fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def svg_document(title: str, strokes: list[Stroke]) -> str:
    x, y, width, height = page_geometry(strokes)
    paths = []
    for stroke in strokes:
        commands = [f"M {fmt(stroke.points[0][0])} {fmt(stroke.points[0][1])}"]
        commands.extend(f"L {fmt(px)} {fmt(py)}" for px, py in stroke.points[1:])
        paths.append(
            f'<path d="{" ".join(commands)}" fill="none" stroke="#111" '
            f'stroke-width="{fmt(stroke.width)}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'viewBox="{fmt(x)} {fmt(y)} {fmt(width)} {fmt(height)}">',
            f"<title>{html.escape(title)}</title>",
            '<rect x="-100000" y="-100000" width="200000" height="200000" fill="white"/>',
            *paths,
            "</svg>",
            "",
        ]
    )


def pdf_stream(strokes: list[Stroke]) -> tuple[float, float, bytes]:
    x, y, width, height = page_geometry(strokes)
    commands = ["1 1 1 rg", f"0 0 {fmt(width)} {fmt(height)} re f", "0 0 0 RG"]
    for stroke in strokes:
        commands.append(f"{fmt(stroke.width)} w 1 J 1 j")
        first_x, first_y = stroke.points[0]
        commands.append(f"{fmt(first_x - x)} {fmt(height - (first_y - y))} m")
        for point_x, point_y in stroke.points[1:]:
            commands.append(f"{fmt(point_x - x)} {fmt(height - (point_y - y))} l")
        commands.append("S")
    return width, height, ("\n".join(commands) + "\n").encode("ascii")


def write_pdf(destination: Path, pages: list[tuple[str, list[Stroke]]]) -> None:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_ids: list[int] = []
    for _, strokes in pages:
        width, height, stream = pdf_stream(strokes)
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {fmt(width)} {fmt(height)}] "
                f"/Contents {content_id} 0 R /Resources << >> >>"
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


def export_archive(archive_path: Path, output_root: Path) -> Path:
    destination = output_root / archive_path.stem
    raw = destination / "raw"
    svg_dir = destination / "svg"
    raw.mkdir(parents=True, exist_ok=True)
    svg_dir.mkdir(parents=True, exist_ok=True)
    pages: list[tuple[str, list[Stroke]]] = []
    unsupported: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            data = archive.read(info)
            name = Path(info.filename).name
            if data.startswith(PENCIL_ENGINE):
                strokes = parse_pencilengine(data)
                if strokes:
                    title = f"{archive_path.name}: {name}"
                    (svg_dir / f"{name}.svg").write_text(svg_document(title, strokes), encoding="utf-8")
                    pages.append((name, strokes))
                else:
                    unsupported.append(f"{name}: PENCILENGINE point tables were not recognized")
            elif data.startswith(PENKIT_INFINITE):
                unsupported.append(f"{name}: PENKITINFENG infinite-canvas block needs a dedicated decoder")

    if pages:
        write_pdf(destination / f"{archive_path.stem}.pdf", pages)
    lines = [f"# {archive_path.name} 矢量导出", "", f"- 已导出 SVG 页：{len(pages)}"]
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
