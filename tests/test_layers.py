"""多图层（hinote_1.0.6）解析与图层选择的回归测试。

样本文件（`sample/`）不入库，所以这里用内存里合成的最小笔迹文件覆盖解析逻辑；
如果本机存在真实多图层样本，末尾的用例会额外做一次端到端核对。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import gzip
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import hinote_vector_export as hv  # noqa: E402


# --------------------------------------------------------------------------
# 合成笔迹文件
# --------------------------------------------------------------------------

LAYER_A = "11111111111111111111111111111111"
LAYER_B = "22222222222222222222222222222222"
DEFAULT_UUID = b"\x00" * 16


def uuid_bytes(layer_id: str) -> bytes:
    return bytes.fromhex(layer_id)


def style_v106(pen_type: int = 1, rgb=(0.1, 0.2, 0.3), softness: float = 1.0,
               base_width: float = 5.0) -> bytes:
    buf = bytearray(hv.STYLE_V106_SIZE)
    struct.pack_into(">I", buf, 0, pen_type)
    struct.pack_into(">I", buf, 4, 0)                      # 哨兵 0 → 用浮点分量
    for index, channel in enumerate(rgb):
        struct.pack_into(">f", buf, 8 + index * 4, channel)
    struct.pack_into(">f", buf, 20, softness)
    struct.pack_into(">f", buf, 24, softness)
    struct.pack_into(">f", buf, 28, base_width)
    struct.pack_into(">f", buf, 48, 1.0)
    return bytes(buf)


def style_v105(pen_type: int = 1, rgb=(0.1, 0.2, 0.3), softness: float = 1.0,
               base_width: float = 4.0) -> bytes:
    buf = bytearray(hv.STYLE_V105_SIZE)
    struct.pack_into(">I", buf, 4, 0x01000000)             # 新版标志 → RGB 顺序
    struct.pack_into(">I", buf, 8, 0xFFFFFFFF)             # 黑色哨兵
    struct.pack_into(">I", buf, 12, pen_type)
    for index, channel in enumerate(rgb):
        struct.pack_into(">f", buf, 20 + index * 4, channel)
    struct.pack_into(">f", buf, 32, softness)
    struct.pack_into(">f", buf, 36, softness)
    struct.pack_into(">f", buf, 40, base_width)
    return bytes(buf)


def point_table(points, pressure: float = 0.5) -> bytes:
    out = bytearray()
    for x, y in points:
        record = bytearray(hv.POINT_STRIDE)
        struct.pack_into(">f", record, 4, x)
        struct.pack_into(">f", record, 8, y)
        struct.pack_into(">f", record, 16, pressure)
        out += record
    return struct.pack(">IIII", 0, len(points), hv.POINT_STRIDE, 0) + bytes(out)


def build_bin(entries, version: int = 106) -> bytes:
    """``entries``：``(layer_uuid_bytes, points)`` 列表。"""
    body = bytearray()
    for layer_uuid, points in entries:
        if version == 106:
            assert len(layer_uuid) == hv.LAYER_UUID_SIZE
            body += layer_uuid
            body += style_v106()
        else:
            body += style_v105()
        body += point_table(points)
    header = bytearray(124)
    header[0:12] = hv.PENCIL_ENGINE
    struct.pack_into(">I", header, 12, 76)
    struct.pack_into(">I", header, 16, 0x00010001)
    struct.pack_into(">I", header, 24, 1)
    struct.pack_into(">I", header, 88, len(body))
    struct.pack_into(">I", header, hv.HEADER_TABLE_COUNT_OFFSET, len(entries))
    return bytes(header) + bytes(body)


def stroke_points(offset: float = 0.0):
    return [(100.0 + offset, 200.0), (110.0 + offset, 210.0), (120.0 + offset, 205.0)]


NOTE_LAYERS_JSON = json.dumps([
    {"id": "e228628a82ab47cebb4f5df59ef5ae28", "layerId": "0", "name": "默认图层",
     "visibleStatus": 1, "lockStatus": 0, "isDelete": 0},
    {"id": "96a0201cf65f4f958c731249db4729ee", "layerId": LAYER_A, "name": "图层 1",
     "visibleStatus": 1, "lockStatus": 0, "isDelete": 0},
    {"id": "55a727d830b94c819d730cb8d527e722", "layerId": LAYER_B, "name": "图层 2",
     "visibleStatus": 0, "lockStatus": 0, "isDelete": 0},
], ensure_ascii=False)


def note_content(layer_order=None) -> dict:
    order = layer_order or ["0", LAYER_A, LAYER_B]
    return {
        "data1": json.dumps({
            "canvasLayerList": NOTE_LAYERS_JSON,
            "layerOrderedInfo": json.dumps(order),
            "activeLayerId": LAYER_B,
        }, ensure_ascii=False),
    }


class BinLayoutTests(unittest.TestCase):
    def test_v106_assigns_layer_and_uuid(self):
        data = build_bin([
            (DEFAULT_UUID, stroke_points(0)),
            (uuid_bytes(LAYER_A), stroke_points(10)),
            (uuid_bytes(LAYER_A), stroke_points(20)),
            (uuid_bytes(LAYER_B), stroke_points(30)),
        ])
        strokes = hv.parse_pencilengine(data)
        self.assertEqual(4, len(strokes))
        self.assertEqual(
            [hv.DEFAULT_LAYER_ID, LAYER_A, LAYER_A, LAYER_B],
            [stroke.layer for stroke in strokes],
        )
        self.assertEqual(1, strokes[0].pen_type)
        self.assertAlmostEqual(5.0, strokes[0].base_width)
        # 1.0.6 的浮点分量按 BGR 存放：合成的 (0.1, 0.2, 0.3) 读出来是 (77, 51, 26)
        self.assertEqual((77, 51, 26), strokes[0].color)

    def test_v105_layout_is_detected_and_layered_as_default(self):
        data = build_bin(
            [(DEFAULT_UUID, stroke_points(0)), (DEFAULT_UUID, stroke_points(10))],
            version=105,
        )
        strokes = hv.parse_pencilengine(data)
        self.assertEqual(2, len(strokes))
        self.assertEqual([hv.DEFAULT_LAYER_ID] * 2, [s.layer for s in strokes])
        self.assertAlmostEqual(4.0, strokes[0].base_width)

    def test_layout_detection_uses_header_count(self):
        v106 = build_bin([(uuid_bytes(LAYER_A), stroke_points(0))], version=106)
        v105 = build_bin([(DEFAULT_UUID, stroke_points(0))], version=105)
        self.assertEqual(hv.STYLE_V106_SIZE, hv.detect_style_layout(v106, hv.scan_point_tables(v106)))
        self.assertEqual(hv.STYLE_V105_SIZE, hv.detect_style_layout(v105, hv.scan_point_tables(v105)))

    def test_highlighter_pen_type_and_opacity(self):
        entries = [(DEFAULT_UUID, stroke_points(0))]
        body = bytearray()
        body += DEFAULT_UUID
        body += style_v106(pen_type=5, rgb=(0.2, 0.2, 0.9), softness=0.8, base_width=20.0)
        body += point_table(stroke_points(0))
        header = bytearray(124)
        header[0:12] = hv.PENCIL_ENGINE
        struct.pack_into(">I", header, 88, len(body))
        struct.pack_into(">I", header, hv.HEADER_TABLE_COUNT_OFFSET, len(entries))
        strokes = hv.parse_pencilengine(bytes(header) + bytes(body))
        self.assertEqual(1, len(strokes))
        self.assertEqual(5, strokes[0].pen_type)
        self.assertAlmostEqual(0.8, strokes[0].opacity)
        self.assertTrue(hv.is_highlighter(strokes[0]))
        # 荧光笔在所有版本里都按 BGR 存放，红蓝需要互换
        self.assertEqual((229, 51, 51), strokes[0].color)


class LayerMetadataTests(unittest.TestCase):
    def test_note_layers_order_and_visibility(self):
        layers, active = hv.parse_note_layers(note_content())
        self.assertEqual([hv.DEFAULT_LAYER_ID, LAYER_A, LAYER_B], [layer.id for layer in layers])
        self.assertEqual(["默认图层", "图层 1", "图层 2"], [layer.name for layer in layers])
        self.assertEqual([True, True, False], [layer.visible for layer in layers])
        self.assertEqual(LAYER_B, active)

    def test_deleted_layers_are_dropped(self):
        content = {
            "data1": json.dumps({
                "canvasLayerList": json.dumps([
                    {"layerId": "0", "name": "默认图层", "isDelete": 0},
                    {"layerId": LAYER_A, "name": "已删除", "isDelete": 1},
                ]),
                "layerOrderedInfo": json.dumps(["0", LAYER_A]),
            }),
        }
        layers, _ = hv.parse_note_layers(content)
        self.assertEqual([hv.DEFAULT_LAYER_ID], [layer.id for layer in layers])

    def test_page_binding_overrides_note_order(self):
        layers, _ = hv.parse_note_layers(note_content())
        page = {"data1": json.dumps({
            "bindLayerIds": json.dumps([LAYER_A, "0"]),
            "bindLayerInfos": json.dumps([
                {"layerId": LAYER_A, "name": "图层 1", "visibleStatus": 1},
                {"layerId": "0", "name": "默认图层", "visibleStatus": 1},
            ]),
        })}
        resolved = hv.resolve_page_layers(page, layers)
        self.assertEqual([LAYER_A, hv.DEFAULT_LAYER_ID], [layer.id for layer in resolved])
        self.assertEqual(["图层 1", "默认图层"], [layer.name for layer in resolved])

    def test_missing_page_metadata_falls_back_to_note_layers(self):
        layers, _ = hv.parse_note_layers(note_content())
        resolved = hv.resolve_page_layers({"data1": ""}, layers)
        self.assertEqual([layer.id for layer in layers], [layer.id for layer in resolved])
        self.assertEqual([layer.id for layer in layers],
                         [layer.id for layer in hv.resolve_page_layers({}, layers)])

    def test_legacy_note_yields_single_default_layer(self):
        layers, active = hv.parse_note_layers({"data1": json.dumps({"book_mark": ""})})
        self.assertEqual([], layers)
        self.assertEqual(hv.DEFAULT_LAYER_ID, active)
        resolved = hv.resolve_page_layers({"data1": ""}, layers)
        self.assertEqual([], resolved)


class AttachLayerTests(unittest.TestCase):
    def make_strokes(self, layer_ids):
        strokes = []
        for index, layer_id in enumerate(layer_ids):
            strokes.append(hv.Stroke(
                points=[(0.0, 0.0), (1.0, 1.0)], pressures=[0.5, 0.5],
                base_width=4.0, color=(0, 0, 0), opacity=1.0, pen_type=1, layer=layer_id,
            ))
        return strokes

    def test_index_and_visibility_are_recorded(self):
        layers = [hv.Layer("0", "默认图层"), hv.Layer(LAYER_A, "图层 1")]
        strokes = self.make_strokes(["0", LAYER_A])
        hv.attach_layers(strokes, layers)
        self.assertEqual([0, 1], [s.layer_index for s in strokes])
        self.assertTrue(all(s.layer_visible for s in strokes))

    def test_unknown_layer_is_appended_not_dropped(self):
        layers = [hv.Layer("0", "默认图层")]
        strokes = self.make_strokes([LAYER_B])
        hv.attach_layers(strokes, layers)
        self.assertEqual(2, len(layers))
        self.assertEqual(LAYER_B, layers[1].id)
        self.assertEqual(1, strokes[0].layer_index)
        self.assertTrue(strokes[0].layer_visible)

    def test_empty_layer_list_gets_default_layer(self):
        layers = []
        strokes = self.make_strokes(["0"])
        hv.attach_layers(strokes, layers)
        self.assertEqual([hv.Layer("0", "默认图层")], layers)
        self.assertEqual(0, strokes[0].layer_index)

    def test_legacy_strokes_are_never_filtered_by_default(self):
        layers = [hv.Layer("0", "默认图层")]
        strokes = self.make_strokes(["0", "0"])
        self.assertIs(layers, hv.attach_layers(strokes, layers))
        self.assertTrue(all(s.layer_visible for s in strokes))


class LayerSelectorTests(unittest.TestCase):
    LAYERS = [
        hv.Layer("0", "默认图层", visible=True),
        hv.Layer(LAYER_A, "图层 1", visible=True),
        hv.Layer(LAYER_B, "图层 2", visible=False),
    ]

    def test_default_hides_layers_marked_hidden(self):
        selector = hv.make_layer_selector()
        self.assertEqual([True, True, False], [selector(layer) for layer in self.LAYERS])

    def test_all_layers_includes_hidden(self):
        selector = hv.make_layer_selector(include_hidden=True)
        self.assertEqual([True, True, True], [selector(layer) for layer in self.LAYERS])

    def test_hide_by_name_and_id(self):
        by_name = hv.make_layer_selector(hide_layers=("图层 1",), include_hidden=True)
        self.assertEqual([True, False, True], [by_name(layer) for layer in self.LAYERS])
        by_id = hv.make_layer_selector(hide_layers=(LAYER_A.upper(),), include_hidden=True)
        self.assertEqual([True, False, True], [by_id(layer) for layer in self.LAYERS])

    def test_only_layer(self):
        selector = hv.make_layer_selector(only_layers=("图层 2",), include_hidden=True)
        self.assertEqual([False, False, True], [selector(layer) for layer in self.LAYERS])

    def test_selector_marks_layers_not_exported(self):
        layers = [hv.Layer(layer.id, layer.name, layer.visible) for layer in self.LAYERS]
        strokes = [hv.Stroke([(0.0, 0.0)], [0.5], 4.0, (0, 0, 0), 1.0, 1, layer.id)
                   for layer in layers]
        hv.attach_layers(strokes, layers, hv.make_layer_selector(only_layers=("图层 1",)))
        self.assertEqual([False, True, False], [layer.exported for layer in layers])
        self.assertEqual([True, True, False], [layer.visible for layer in layers],
                         "笔记自身的可见性不应被选择器改写")
        self.assertEqual([False, True, False], [s.layer_visible for s in strokes])
        self.assertEqual(3, len(strokes), "排除图层只改渲染标记，不应丢弃笔画")

    def test_all_layers_overrides_note_visibility(self):
        layers = [hv.Layer(layer.id, layer.name, layer.visible) for layer in self.LAYERS]
        strokes = [hv.Stroke([(0.0, 0.0)], [0.5], 4.0, (0, 0, 0), 1.0, 1, layer.id)
                   for layer in layers]
        hv.attach_layers(strokes, layers, hv.make_layer_selector(include_hidden=True))
        self.assertEqual([True, True, True], [layer.exported for layer in layers])
        self.assertEqual([True, True, False], [layer.visible for layer in layers])
        self.assertEqual([True, True, True], [s.layer_visible for s in strokes])


class EndToEndTests(unittest.TestCase):
    def make_page(self, layer_selector=None):
        strokes = [
            (DEFAULT_UUID, stroke_points(0)),
            (uuid_bytes(LAYER_A), stroke_points(50)),
            (uuid_bytes(LAYER_B), stroke_points(100)),
        ]
        data = build_bin(strokes)
        page_data = {
            "pageNumber": 1, "pageRatio": 0.706, "pageOrientation": 0,
            "pageColor": -1, "background": "base1",
            "attachment": [{"filePath": "/data/user/0/com.huawei.hinote/files/x.bin"}],
            "data1": json.dumps({"bindLayerIds": json.dumps(["0", LAYER_A, LAYER_B])}),
        }
        note_layers, active = hv.parse_note_layers(note_content())
        page = hv.build_page(page_data, {"x.bin": data}, {}, note_layers, active, layer_selector)
        return page

    def test_page_carries_layers_and_stroke_indices(self):
        page = self.make_page()
        self.assertEqual([hv.DEFAULT_LAYER_ID, LAYER_A, LAYER_B], [l.id for l in page.layers])
        self.assertEqual([0, 1, 2], [s.layer_index for s in page.strokes])
        self.assertEqual(["默认图层", "图层 1", "图层 2"], [l.name for l in page.layers])

    def test_hidden_layer_is_not_rendered(self):
        page = self.make_page()
        # 图层 2 在笔记元数据里是 visibleStatus 0
        self.assertEqual([True, True, False], [s.layer_visible for s in page.strokes])
        svg = hv.svg_document("t", page)
        self.assertEqual(2, svg.count("<path "))

    def test_all_layers_flag_renders_hidden_layer(self):
        page = self.make_page(hv.make_layer_selector(include_hidden=True))
        self.assertEqual(3, hv.svg_document("t", page).count("<path "))

    def test_strokes_are_painted_in_layer_order(self):
        page = self.make_page(hv.make_layer_selector(include_hidden=True))
        # 打乱输入顺序后，SVG 中的 path 仍按图层自下而上排列
        page.strokes.reverse()
        svg = hv.svg_document("t", page)
        first_x = float(svg.split("<path ")[1].split('d="M ')[1].split(" ")[0])
        self.assertLess(first_x, 200.0, "最底层（默认图层）应最先绘制")

    def test_pdf_stream_skips_hidden_layers(self):
        page = self.make_page()
        stream = hv.pdf_stream(page, None).decode("ascii")
        self.assertEqual(2, stream.count("\nf\n"), "隐藏图层不应写入 PDF 内容流")


class ArchiveExportTests(unittest.TestCase):
    """端到端跑 export_archive，覆盖笔记级图层读取、分层导出与报告。"""

    NOTE_ID = "d444f48f726b4e909956db4483945986"
    PAGE_ID = "22d05ee0b951487faeebc7e31fcd1b03"

    def build_archive(self, root: Path, all_visible: bool = False) -> Path:
        layers = json.loads(NOTE_LAYERS_JSON)
        if all_visible:
            for layer in layers:
                layer["visibleStatus"] = 1
        note = {"customNoteContent": {
            "attachment": [],
            "data1": json.dumps({
                "canvasLayerList": json.dumps(layers, ensure_ascii=False),
                "layerOrderedInfo": json.dumps(["0", LAYER_A, LAYER_B]),
                "activeLayerId": LAYER_B,
            }, ensure_ascii=False),
        }}
        page = {"customNotePageContent": {
            "id": self.PAGE_ID, "pageNumber": 1, "pageRatio": 0.706,
            "pageOrientation": 0, "pageColor": -1, "background": "base1",
            "pageElement": [],
            "attachment": [{"filePath": "/data/user/0/com.huawei.hinote/files/strokes.bin"}],
            "data1": json.dumps({"bindLayerIds": json.dumps(["0", LAYER_A, LAYER_B])}),
        }}
        strokes = build_bin([
            (DEFAULT_UUID, stroke_points(0)),
            (uuid_bytes(LAYER_A), stroke_points(50)),
            (uuid_bytes(LAYER_B), stroke_points(100)),
        ])
        path = root / "图层测试.hinote"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{self.NOTE_ID}.jhinote",
                gzip.compress(json.dumps(note, ensure_ascii=False).encode("utf-8")),
            )
            archive.writestr(
                f"pages/{self.PAGE_ID}.jhinote",
                gzip.compress(json.dumps(page, ensure_ascii=False).encode("utf-8")),
            )
            archive.writestr("custom_md.jhinote", gzip.compress(b"{}"))
            archive.writestr("files/strokes.bin", strokes)
        return path

    def test_hidden_layer_is_reported_but_not_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = hv.export_archive(
                self.build_archive(root), root / "out", split_layers=True,
            )
            report = (destination / "report.md").read_text(encoding="utf-8")
            self.assertIn("默认图层（`0`）：1 笔 · 可见", report)
            self.assertIn("图层 1", report)
            self.assertIn("图层 2", report)
            self.assertIn("未导出", report)

            svg = (destination / "svg" / "page-1.svg").read_text(encoding="utf-8")
            self.assertEqual(2, svg.count("<path "))
            layer_svgs = sorted(p.name for p in (destination / "svg" / "layers").glob("*.svg"))
            self.assertEqual(2, len(layer_svgs), layer_svgs)

    def test_all_layers_exports_the_hidden_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = hv.export_archive(
                self.build_archive(root, all_visible=True), root / "out", split_layers=True,
            )
            svg = (destination / "svg" / "page-1.svg").read_text(encoding="utf-8")
            self.assertEqual(3, svg.count("<path "))
            layer_svgs = sorted(p.name for p in (destination / "svg" / "layers").glob("*.svg"))
            self.assertEqual(3, len(layer_svgs), layer_svgs)


REAL_SAMPLE_CANDIDATES = [
    os.environ.get("HINOTE_MULTILAYER_SAMPLE", ""),
    str(ROOT / "sample" / "多图层测试.hinote"),
    r"E:\WechatFiles\xwechat_files\wxid_l3qnphal9h0a51_e844\msg\file\2026-10"
    r"\多图层测试_202610012312_51668.hinote",
]

# 带红/绿/蓝手写的多图层样本，用来锁定浮点颜色的 BGR 顺序
COLOR_SAMPLE_CANDIDATES = [
    os.environ.get("HINOTE_COLOR_SAMPLE", ""),
    str(ROOT / "sample" / "多图层彩色测试.hinote"),
    r"E:\WechatFiles\xwechat_files\wxid_l3qnphal9h0a51_e844\msg\file\2026-10"
    r"\多图层测试_202610012341_5867.hinote",
]

# 上层图层在蓝字上涂鸦的样本，用来锁定图层绘制顺序
ZORDER_SAMPLE_CANDIDATES = [
    os.environ.get("HINOTE_ZORDER_SAMPLE", ""),
    str(ROOT / "sample" / "多图层叠放测试.hinote"),
    r"E:\WechatFiles\xwechat_files\wxid_l3qnphal9h0a51_e844\msg\file\2026-10"
    r"\多图层测试_202610012345_08610.hinote",
]


class RealSampleTests(unittest.TestCase):
    """真实样本存在时做一次端到端核对；缺失则跳过。"""

    @classmethod
    def setUpClass(cls):
        cls.path = next((Path(p) for p in REAL_SAMPLE_CANDIDATES if p and Path(p).exists()), None)
        if cls.path is None:
            raise unittest.SkipTest("未找到真实多图层样本，设置 HINOTE_MULTILAYER_SAMPLE 可启用")

    def test_sample_is_recovered_as_three_layers(self):
        with zipfile.ZipFile(self.path) as archive:
            names = archive.namelist()
            meta_name = next(
                name for name in names
                if name.endswith(".jhinote") and "/" not in name and name != "custom_md.jhinote"
            )
            meta = json.loads(gzip.decompress(archive.read(meta_name)).decode("utf-8"))
            layer_meta, active = hv.parse_note_layers(meta["customNoteContent"])
            self.assertEqual(3, len(layer_meta))
            self.assertEqual(["默认图层", "图层 1", "图层 2"], [l.name for l in layer_meta])
            self.assertEqual(active, layer_meta[2].id)

            page_name = next(name for name in names if name.startswith("pages/"))
            page_data = json.loads(
                gzip.decompress(archive.read(page_name)).decode("utf-8")
            )["customNotePageContent"]
            files = {Path(name).name: archive.read(name) for name in names}
            page = hv.build_page(page_data, files, {}, layer_meta, active)

        self.assertEqual(3, len(page.layers))
        self.assertEqual(16, len(page.strokes))
        counts = {}
        for stroke in page.strokes:
            counts[stroke.layer] = counts.get(stroke.layer, 0) + 1
        self.assertEqual(5, counts.get(hv.DEFAULT_LAYER_ID))
        self.assertEqual(5, counts.get(layer_meta[1].id))
        self.assertEqual(6, counts.get(layer_meta[2].id))
        self.assertTrue(all(stroke.pen_type == 1 for stroke in page.strokes))

        # 三层在版面上自上而下排开，与官方缩略图的三行手写一致
        bands = []
        for index in range(3):
            ys = [y for s in page.strokes if s.layer_index == index for _, y in s.points]
            bands.append((min(ys), max(ys)))
        self.assertLess(bands[0][1], bands[1][0])
        self.assertLess(bands[1][1], bands[2][0])


class ColorSampleTests(unittest.TestCase):
    """带红/绿/蓝手写的样本：锁定 1.0.6 的浮点颜色分量是 BGR 顺序。

    笔记自带的缩略图自上而下依次是「红」「绿」「蓝」。按 BGR 解读时三组
    颜色分别是 (232,64,38) / (228,253,206) / (70,177,227)，按 RGB 解读则
    会变成蓝 / 浅绿 / 橙，与缩略图直接矛盾。
    """

    @classmethod
    def setUpClass(cls):
        cls.path = next((Path(p) for p in COLOR_SAMPLE_CANDIDATES if p and Path(p).exists()), None)
        if cls.path is None:
            raise unittest.SkipTest("未找到彩色多图层样本，设置 HINOTE_COLOR_SAMPLE 可启用")

    def test_float_components_are_stored_bgr(self):
        with zipfile.ZipFile(self.path) as archive:
            names = archive.namelist()
            page_name = next(name for name in names if name.startswith("pages/"))
            page_data = json.loads(
                gzip.decompress(archive.read(page_name)).decode("utf-8")
            )["customNotePageContent"]
            files = {Path(name).name: archive.read(name) for name in names}
            page = hv.build_page(page_data, files, {})

        self.assertEqual(40, len(page.strokes))

        # 每种手写颜色取第一次出现的 y，据此还原自上而下的顺序
        top_of: dict[tuple[int, int, int], float] = {}
        for stroke in page.strokes:
            if max(stroke.color) - min(stroke.color) < 40:
                continue                      # 近黑的黑色手写
            top_of.setdefault(stroke.color, min(y for _, y in stroke.points))
        self.assertEqual(3, len(top_of), sorted(top_of))

        (red, _), (green, _), (blue, _) = sorted(top_of.items(), key=lambda item: item[1])
        self.assertGreater(red[0], red[2], f"最上面的应是红色，实得 {red}")
        self.assertGreaterEqual(green[1], max(green[0], green[2]), f"中间应是绿色，实得 {green}")
        self.assertGreater(blue[2], blue[0], f"最下面的应是蓝色，实得 {blue}")


class ZOrderSampleTests(unittest.TestCase):
    """上层图层在蓝字上涂鸦的样本：锁定「layerOrderedInfo 下标 0 = 最底层」。

    笔记自带的缩略图里，图层 1（下标 1）的红色涂鸦完全盖住了默认图层
    （下标 0）的蓝色「蓝」字；若下标 0 是最顶层，遮挡关系就会反过来。
    """

    @classmethod
    def setUpClass(cls):
        cls.path = next((Path(p) for p in ZORDER_SAMPLE_CANDIDATES if p and Path(p).exists()), None)
        if cls.path is None:
            raise unittest.SkipTest("未找到图层叠放样本，设置 HINOTE_ZORDER_SAMPLE 可启用")

    def test_upper_layer_paints_over_lower_layer(self):
        with zipfile.ZipFile(self.path) as archive:
            names = archive.namelist()
            meta_name = next(
                name for name in names
                if name.endswith(".jhinote") and "/" not in name and name != "custom_md.jhinote"
            )
            meta = json.loads(gzip.decompress(archive.read(meta_name)).decode("utf-8"))
            layer_meta, active = hv.parse_note_layers(meta["customNoteContent"])
            page_name = next(name for name in names if name.startswith("pages/"))
            page_data = json.loads(
                gzip.decompress(archive.read(page_name)).decode("utf-8")
            )["customNotePageContent"]
            files = {Path(name).name: archive.read(name) for name in names}
            page = hv.build_page(page_data, files, {}, layer_meta, active)

        # layerOrderedInfo 的下标 0 是最底层：默认图层在最下，图层 1 在其上
        self.assertEqual(
            [hv.DEFAULT_LAYER_ID, layer_meta[1].id, layer_meta[2].id],
            [layer.id for layer in page.layers],
        )
        self.assertEqual(layer_meta[1].id, active, "当前图层应是图层 1")

        blue = [s for s in page.strokes if s.color == (70, 177, 227)]
        scribble = [
            s for s in page.strokes
            if s.layer_index == 1 and s.color == (232, 64, 38) and s.base_width == 4.0
        ]
        self.assertTrue(blue, "应能找到蓝色「蓝」字")
        self.assertEqual(1, len(scribble), "图层 1 上应只有一笔红色涂鸦")
        self.assertTrue(all(s.layer_index == 0 for s in blue), "蓝字应在默认图层")

        # 两层确实在版面上重叠，否则这条断言没有意义
        xs = [x for x, _ in scribble[0].points]
        ys = [y for _, y in scribble[0].points]
        self.assertTrue(any(510 < x < 615 for x in xs) and any(335 < y < 420 for y in ys))

        # 绘制顺序里涂鸦必须排在所有蓝字之后，否则会被蓝字盖住
        order = sorted(
            page.strokes, key=lambda s: (s.layer_index, 0 if hv.is_highlighter(s) else 1)
        )
        blue_ids = {id(s) for s in blue}
        last_blue = max(i for i, s in enumerate(order) if id(s) in blue_ids)
        self.assertGreater(order.index(scribble[0]), last_blue,
                           "图层 1 的涂鸦应画在默认图层的蓝字之上")

        svg = hv.svg_document("t", page)
        self.assertGreater(svg.rindex("#e84026"), svg.rindex("#46b1e3"))


if __name__ == "__main__":
    unittest.main()
