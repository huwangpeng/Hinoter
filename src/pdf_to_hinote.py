#!/usr/bin/env python3
"""将 PDF 封装为保留原始矢量内容的华为有界笔记。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError as 错误:  # pragma: no cover - 仅在依赖缺失时触发
    raise SystemExit("缺少 pypdf，请先运行：pip install pypdf") from 错误


class 转换错误(Exception):
    """输入 PDF 或生成的 .hinote 不符合预期。"""


@dataclass(frozen=True)
class PDF页面:
    宽度: float
    高度: float
    方向: int
    比例: float


@dataclass(frozen=True)
class 转换结果:
    输出路径: Path
    页数: int
    标题: str


def 内容哈希(数据: bytes) -> str:
    return hashlib.sha256(数据).hexdigest().upper()


def 文件名哈希(名称: str) -> str:
    return hashlib.sha256(名称.encode("utf-8")).hexdigest().lower()


def 压缩_json(对象: dict[str, Any]) -> bytes:
    原文 = json.dumps(
        对象,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(原文, compresslevel=9, mtime=0)


def 解压_json(数据: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(数据).decode("utf-8"))


def 读取_pdf页面(PDF数据: bytes) -> list[PDF页面]:
    if not PDF数据.startswith(b"%PDF-"):
        raise 转换错误("输入文件不是有效的 PDF")
    try:
        阅读器 = PdfReader(io.BytesIO(PDF数据), strict=False)
    except Exception as 错误:
        raise 转换错误(f"无法读取 PDF：{错误}") from 错误
    if 阅读器.is_encrypted:
        raise 转换错误("暂不支持加密或带密码的 PDF")
    if not 阅读器.pages:
        raise 转换错误("PDF 中没有页面")

    页面列表: list[PDF页面] = []
    for 页码, 页面 in enumerate(阅读器.pages, start=1):
        边界 = 页面.cropbox or 页面.mediabox
        宽度 = float(边界.width)
        高度 = float(边界.height)
        旋转 = int(页面.get("/Rotate", 0) or 0) % 360
        if 旋转 in (90, 270):
            宽度, 高度 = 高度, 宽度
        if 宽度 <= 0 or 高度 <= 0:
            raise 转换错误(f"第 {页码} 页的页面尺寸无效")
        方向 = 1 if 宽度 > 高度 else 0
        比例 = round(min(宽度, 高度) / max(宽度, 高度), 8)
        页面列表.append(PDF页面(宽度, 高度, 方向, 比例))
    return 页面列表


def 资源条目(名称: str, 数据: bytes) -> dict[str, str]:
    return {"name": 名称, "hash": 内容哈希(数据)}


def 详情文件映射(资源: dict[str, bytes]) -> str:
    映射 = {
        名称: [{"name": 名称, "hash": 内容哈希(数据)}]
        for 名称, 数据 in 资源.items()
    }
    return json.dumps(映射, ensure_ascii=False, separators=(",", ":"))


def 构建顶层元数据(
    *,
    笔记编号: str,
    附件编号: str,
    PDF名称: str,
    PDF数据: bytes,
    大纲名称: str,
    大纲数据: bytes,
    页面列表: list[PDF页面],
    标题: str,
    时间戳: int,
) -> dict[str, Any]:
    顶层资源 = {PDF名称: PDF数据, 大纲名称: 大纲数据}
    首页 = 页面列表[0]
    数据一 = {
        "relationTags": "[]",
        "relationPages": "",
        "originDeviceType": "tablet",
        "isContentCover": "0",
        "isInfNote": "0",
        "infStylusPath": "",
        "detailFileMap": 详情文件映射(顶层资源),
        "record_item_mapper_key": "",
        "localData1": "{}",
    }
    附件 = {
        "attachType": 3,
        "cloudSyncState": 1,
        "createTime": 时间戳,
        "filePath": f"/data/user/0/com.huawei.hinote/files/importfiles/{PDF名称}",
        "isDelete": 0,
        "modifiedTime": 时间戳,
        "notesId": 笔记编号,
        "playbackProgress": 0,
        "id": 附件编号,
        "notePageId": None,
        "data1": json.dumps(
            {"synDataTpye": "memonote", "pageElementId": ""},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "data2": "",
        "data3": "",
        "data4": "",
        "data5": "",
        "unStructUuid": "",
        "transText": None,
        "contentText": "",
    }
    内容 = {
        "attachment": [附件],
        "background": "base1",
        "categoryId": "system_category_uuid_unclassified",
        "cloudSyncState": 0,
        "createTime": 时间戳,
        "data1": json.dumps(数据一, ensure_ascii=False, separators=(",", ":")),
        "deleteTag": 0,
        "deleteTime": 0,
        "extendFields": "hinote_1.0.4",
        "guid": "",
        "hasCover": 0,
        "id": 笔记编号,
        "isFavorite": 0,
        "isTop": 0,
        "modifiedTime": 时间戳,
        "noteIcon": "import_pdf",
        "noteTitle": 标题,
        "noteType": 101,
        "pageColor": -1,
        "pageOrientation": 首页.方向,
        "pageRatio": 首页.比例,
        "unStructUuid": "",
        "bookIntroduction": "",
        "userId": "",
        "noteTemplate": "",
        "data2": "",
        "data3": "",
        "data4": "",
        "data5": "",
        "coverId": "",
        "parentId": "system_category_uuid_unclassified",
        "outLineAttachment": [],
        "recordItemEntities": [],
    }
    return {
        "customNoteContent": 内容,
        "fileList": [
            资源条目(PDF名称, PDF数据),
            资源条目(大纲名称, 大纲数据),
        ],
    }


def 构建页面元数据(
    *,
    笔记编号: str,
    页面编号: str,
    附件编号: str,
    页面: PDF页面,
    页面索引: int,
    页面总数: int,
    时间戳: int,
) -> dict[str, Any]:
    内容 = {
        "pageBookMarks": [],
        "attachment": [],
        "background": "base1",
        "bkgAttachmentId": 附件编号,
        "bkgAttachmentIndex": 页面索引,
        "chapterNumber": "",
        "cloudSyncState": 0,
        "createTime": 时间戳,
        "guid": "",
        "id": 页面编号,
        "isDelete": 0,
        "lastPageTag": 1 if 页面索引 == 页面总数 - 1 else 0,
        "modifiedTime": 时间戳,
        "notesId": 笔记编号,
        "pageColor": -1,
        "pageElement": [],
        "pageNumber": 页面索引 + 1,
        "pageOrientation": 页面.方向,
        "pageRatio": 页面.比例,
        "pageType": 1,
        "thumbnail": "",
        "unStructUuid": "",
        "data1": json.dumps(
            {"detailFileMap": "{}", "thumbnail_area": "", "book_mark": ""},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "data2": "",
        "data3": "",
        "data4": "",
        "data5": "",
    }
    return {"customNotePageContent": 内容, "fileList": []}


def 构建校验元数据(资源: dict[str, bytes]) -> dict[str, Any]:
    return {
        "customMdContents": [
            {
                "fileMdStr": 内容哈希(数据),
                "fileNameMdStr": 文件名哈希(名称),
            }
            for 名称, 数据 in 资源.items()
        ]
    }


def 校验_hinote(路径: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(路径) as 压缩包:
            名称集合 = set(压缩包.namelist())
            顶层列表 = sorted(
                名称 for 名称 in 名称集合
                if "/" not in 名称 and 名称.endswith(".jhinote")
                and 名称 != "custom_md.jhinote"
            )
            页面列表 = sorted(
                名称 for 名称 in 名称集合
                if 名称.startswith("pages/") and 名称.endswith(".jhinote")
            )
            PDF列表 = sorted(
                名称 for 名称 in 名称集合
                if 名称.startswith("files/") and 名称.endswith("_pdf")
            )
            if len(顶层列表) != 1 or not 页面列表 or len(PDF列表) != 1:
                raise 转换错误(".hinote 容器缺少顶层元数据、页面或 PDF 附件")

            顶层 = 解压_json(压缩包.read(顶层列表[0]))
            页面对象 = [解压_json(压缩包.read(名称)) for 名称 in 页面列表]
            校验对象 = 解压_json(压缩包.read("custom_md.jhinote"))
            内容 = 顶层["customNoteContent"]
            笔记编号 = 内容["id"]
            附件 = 内容["attachment"]
            if len(附件) != 1 or 附件[0]["attachType"] != 3:
                raise 转换错误("PDF 附件关系无效")
            附件编号 = 附件[0]["id"]

            页面内容 = [对象["customNotePageContent"] for 对象 in 页面对象]
            页面内容.sort(key=lambda 对象: 对象["pageNumber"])
            if [对象["pageNumber"] for 对象 in 页面内容] != list(range(1, len(页面内容) + 1)):
                raise 转换错误("页面编号不连续")
            if sum(对象["lastPageTag"] == 1 for 对象 in 页面内容) != 1:
                raise 转换错误("末页标记无效")
            for 索引, 对象 in enumerate(页面内容):
                if 对象["notesId"] != 笔记编号:
                    raise 转换错误("页面与笔记编号不匹配")
                if 对象["bkgAttachmentId"] != 附件编号:
                    raise 转换错误("页面与 PDF 附件编号不匹配")
                if 对象["bkgAttachmentIndex"] != 索引:
                    raise 转换错误("PDF 页索引不连续")

            资源 = {
                Path(名称).name: 压缩包.read(名称)
                for 名称 in 名称集合 if 名称.startswith("files/")
            }
            期望校验 = {
                (内容哈希(数据), 文件名哈希(名称))
                for 名称, 数据 in 资源.items()
            }
            实际校验 = {
                (条目["fileMdStr"], 条目["fileNameMdStr"])
                for 条目 in 校验对象["customMdContents"]
            }
            if 实际校验 != 期望校验:
                raise 转换错误("custom_md.jhinote 中的资源哈希不一致")
            return {
                "title": 内容["noteTitle"],
                "pageCount": len(页面内容),
                "noteId": 笔记编号,
                "pdfName": Path(PDF列表[0]).name,
            }
    except 转换错误:
        raise
    except (KeyError, OSError, EOFError, ValueError, zipfile.BadZipFile) as 错误:
        raise 转换错误(f"生成结果校验失败：{错误}") from 错误


def 转换_pdf(
    输入路径: Path,
    输出路径: Path | None = None,
    *,
    标题: str | None = None,
    覆盖: bool = False,
) -> 转换结果:
    输入路径 = Path(输入路径)
    if not 输入路径.is_file():
        raise 转换错误(f"找不到输入文件：{输入路径}")
    if 输入路径.suffix.lower() != ".pdf":
        raise 转换错误("输入文件扩展名必须是 .pdf")

    输出路径 = Path(输出路径) if 输出路径 else 输入路径.with_suffix(".hinote")
    if 输出路径.suffix.lower() != ".hinote":
        raise 转换错误("输出文件扩展名必须是 .hinote")
    if 输入路径.resolve() == 输出路径.resolve():
        raise 转换错误("输入和输出不能是同一个文件")
    if 输出路径.exists() and not 覆盖:
        raise 转换错误(f"输出文件已存在：{输出路径}；需要覆盖时请加 --force")

    PDF数据 = 输入路径.read_bytes()
    页面列表 = 读取_pdf页面(PDF数据)
    笔记标题 = 标题.strip() if 标题 and 标题.strip() else 输入路径.stem
    时间戳 = int(time.time() * 1000)
    笔记编号 = uuid.uuid4().hex
    附件编号 = uuid.uuid4().hex
    PDF编号 = uuid.uuid4().hex
    PDF名称 = f"{PDF编号}_pdf"
    大纲名称 = f"{笔记编号}_{时间戳}_outline.json"
    大纲数据 = 笔记编号.encode("ascii")
    资源 = {PDF名称: PDF数据, 大纲名称: 大纲数据}

    顶层元数据 = 构建顶层元数据(
        笔记编号=笔记编号,
        附件编号=附件编号,
        PDF名称=PDF名称,
        PDF数据=PDF数据,
        大纲名称=大纲名称,
        大纲数据=大纲数据,
        页面列表=页面列表,
        标题=笔记标题,
        时间戳=时间戳,
    )
    页面文件: list[tuple[str, bytes]] = []
    for 索引, 页面 in enumerate(页面列表):
        页面编号 = uuid.uuid4().hex
        页面元数据 = 构建页面元数据(
            笔记编号=笔记编号,
            页面编号=页面编号,
            附件编号=附件编号,
            页面=页面,
            页面索引=索引,
            页面总数=len(页面列表),
            时间戳=时间戳,
        )
        页面文件.append((f"pages/{页面编号}.jhinote", 压缩_json(页面元数据)))

    输出路径.parent.mkdir(parents=True, exist_ok=True)
    临时路径 = 输出路径.with_name(f".{输出路径.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            临时路径,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as 压缩包:
            for 名称, 数据 in 页面文件:
                压缩包.writestr(名称, 数据)
            for 名称, 数据 in 资源.items():
                压缩包.writestr(f"files/{名称}", 数据)
            压缩包.writestr(f"{笔记编号}.jhinote", 压缩_json(顶层元数据))
            压缩包.writestr("custom_md.jhinote", 压缩_json(构建校验元数据(资源)))

        校验结果 = 校验_hinote(临时路径)
        if 校验结果["pageCount"] != len(页面列表):
            raise 转换错误("生成结果的页数与 PDF 不一致")
        with zipfile.ZipFile(临时路径) as 压缩包:
            写入PDF = 压缩包.read(f"files/{PDF名称}")
        if 写入PDF != PDF数据:
            raise 转换错误("生成结果中的 PDF 附件与原文件不一致")
        os.replace(临时路径, 输出路径)
    finally:
        if 临时路径.exists():
            临时路径.unlink()

    return 转换结果(输出路径, len(页面列表), 笔记标题)


def main() -> None:
    参数器 = argparse.ArgumentParser(
        description="将 PDF 原样封装为华为有界笔记，保留 PDF 的矢量内容。"
    )
    参数器.add_argument("input", type=Path, help="输入 PDF 文件")
    参数器.add_argument("-o", "--output", type=Path, help="输出 .hinote 文件")
    参数器.add_argument("--title", help="笔记标题，默认使用 PDF 文件名")
    参数器.add_argument("--force", action="store_true", help="允许覆盖已有输出文件")
    参数 = 参数器.parse_args()
    try:
        结果 = 转换_pdf(
            参数.input,
            参数.output,
            标题=参数.title,
            覆盖=参数.force,
        )
    except 转换错误 as 错误:
        参数器.error(str(错误))
    print(f"已生成：{结果.输出路径}")
    print(f"标题：{结果.标题} · {结果.页数} 页")
    print("PDF 作为原始矢量附件写入，导入华为笔记后可继续添加手写批注。")


if __name__ == "__main__":
    main()
