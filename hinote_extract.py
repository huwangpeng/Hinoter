#!/usr/bin/env python3
"""Extract Huawei .hinote archives using only the Python standard library."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import struct
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def safe_destination(root: Path, member_name: str) -> Path:
    member = PurePosixPath(member_name)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"unsafe archive member: {member_name}")
    return root.joinpath(*member.parts)


def decode_jhinote(data: bytes) -> dict:
    return json.loads(gzip.decompress(data).decode("utf-8"))


def iso_time(milliseconds: int | None) -> str:
    if not milliseconds:
        return ""
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def identify_binary(data: bytes) -> str:
    signatures = {
        b"PENCILENGINE": "bounded-stroke/PENCILENGINE",
        b"PENKITINFENG": "infinite-canvas-stroke/PENKITINFENG",
        b"\x89PNG\r\n\x1a\n": "PNG image",
        b"\xff\xd8\xff": "JPEG image",
    }
    for signature, description in signatures.items():
        if data.startswith(signature):
            return description
    return "unknown binary"


def run_ocr(image: Path, language: str, tessdata_dir: Path | None) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return "[OCR skipped: tesseract was not found]"
    command = [executable, str(image), "stdout"]
    if tessdata_dir:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    command.extend(["-l", language, "--psm", "11"])
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        return f"[OCR failed: {error}]"
    return result.stdout.decode("utf-8", errors="replace").strip()


def extract_archive(
    source: Path,
    output_root: Path,
    ocr_language: str | None,
    tessdata_dir: Path | None,
) -> Path:
    note_root = output_root / source.stem
    raw_root = note_root / "raw"
    decoded_root = note_root / "decoded"
    raw_root.mkdir(parents=True, exist_ok=True)
    decoded_root.mkdir(parents=True, exist_ok=True)

    members: list[dict] = []
    note_metadata: dict = {}
    pages: list[dict] = []
    ocr_results: dict[str, str] = {}

    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            destination = safe_destination(raw_root, info.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            destination.write_bytes(data)

            item = {
                "name": info.filename,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "type": identify_binary(data),
            }
            if info.filename.endswith(".jhinote"):
                decoded = decode_jhinote(data)
                decoded_path = safe_destination(decoded_root, info.filename + ".json")
                decoded_path.parent.mkdir(parents=True, exist_ok=True)
                decoded_path.write_text(
                    json.dumps(decoded, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                item["type"] = "gzip-compressed JSON"
                if "customNoteContent" in decoded:
                    note_metadata = decoded["customNoteContent"]
                if "customNotePageContent" in decoded:
                    pages.append(decoded["customNotePageContent"])
            elif data.startswith(b"PENKITINFENG") and len(data) >= 40:
                item["payload_size"] = struct.unpack_from(">I", data, 36)[0]
            members.append(item)

            if ocr_language and destination.suffix.lower() in IMAGE_SUFFIXES:
                ocr_results[info.filename] = run_ocr(
                    destination, ocr_language, tessdata_dir
                )

    pages.sort(key=lambda page: page.get("pageNumber", 0))
    summary = {
        "source": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "note": note_metadata,
        "pages": pages,
        "members": members,
        "ocr": ocr_results,
    }
    (note_root / "metadata.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (note_root / "report.md").write_text(
        build_report(summary), encoding="utf-8"
    )
    return note_root


def build_report(summary: dict) -> str:
    note = summary["note"]
    mode = "无界笔记" if '"isInfNote":"1"' in note.get("data1", "") else "有界笔记"
    lines = [
        f"# {summary['source']} 解析报告",
        "",
        f"- 类型：{mode}",
        f"- 标题：{note.get('noteTitle', '')}",
        f"- 笔记 ID：`{note.get('id', '')}`",
        f"- 创建时间（UTC）：{iso_time(note.get('createTime'))}",
        f"- 修改时间（UTC）：{iso_time(note.get('modifiedTime'))}",
        f"- 页面数：{len(summary['pages'])}",
        f"- SHA-256：`{summary['source_sha256']}`",
        "",
        "## 页面",
        "",
    ]
    for page in summary["pages"]:
        attachments = page.get("attachment", [])
        elements = page.get("pageElement", [])
        lines.extend(
            [
                f"### 第 {page.get('pageNumber', '?')} 页",
                "",
                f"- 页面 ID：`{page.get('id', '')}`",
                f"- 附件数：{len(attachments)}",
                f"- 页面元素数：{len(elements)}",
            ]
        )
        for element in elements:
            lines.append(
                f"- 元素：类型 {element.get('elementType')}，文件 "
                f"`{PurePosixPath(element.get('filePath', '')).name}`"
            )
        for attachment in attachments:
            lines.append(
                f"- 附件：类型 {attachment.get('attachType')}，文件 "
                f"`{PurePosixPath(attachment.get('filePath', '')).name}`"
            )
        lines.append("")

    lines.extend(["## 文件结构", ""])
    for member in summary["members"]:
        lines.append(
            f"- `{member['name']}`：{member['type']}，{member['size']} 字节"
        )

    if summary["ocr"]:
        lines.extend(["", "## OCR 原始结果", ""])
        for name, text in summary["ocr"].items():
            lines.extend([f"### `{name}`", "", "```text", text, "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("parsed"))
    parser.add_argument("--ocr-language")
    parser.add_argument("--tessdata-dir", type=Path)
    args = parser.parse_args()
    for archive in args.archives:
        result = extract_archive(
            archive, args.output, args.ocr_language, args.tessdata_dir
        )
        print(result)


if __name__ == "__main__":
    main()
