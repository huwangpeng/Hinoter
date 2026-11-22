"""无边画布（PENKITINFENG）笔迹与元素解析的回归测试。

用内存里合成的最小 bsd / ged 块覆盖两种样式长度（早期 16 字节、infinite_1.0.2 的
20 字节）、极短笔画的元数据变体、颜色字节序和 GED 元素几何。如果设置了
HINOTE_INFINITE_SAMPLE 指向真实无边画布笔记，会额外核对每个块都能完整解出。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import hinote_infinite as hi  # noqa: E402


def make_stroke(points, color=0xFF000000, style_size=20, meta=None):
    n = len(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    descriptor = struct.pack(">IIII", 0x10, 0x49, 0x14, n * 28) + b"\x11" * 16 + bytes(16)
    m0, m1 = meta if meta else (0.0, float(n - 1))
    table = struct.pack(">II", n, 28) + struct.pack(">ff", m0, m1)
    table += struct.pack(">4f", min(xs), min(ys), max(xs), max(ys)) + struct.pack(">II", 7, 0xFFFFFFFF)
    style = struct.pack(">IfIf", 2, 2.5, color, 1.0)
    if style_size == 20:
        style += struct.pack(">f", 1.0)
    pts = b"".join(struct.pack(">ffIffII", x, y, 10 * i, 0.5, 0.2, 0, 0) for i, (x, y) in enumerate(points))
    return descriptor + table + b"\x01" + style + pts


def make_bsd(strokes, padding=4):
    body = b"".join(strokes)
    header = hi.PENKIT_MAGIC + struct.pack(">IIQIIIIII", 1, 0x10000, 42, 16, 8,
                                           len(body) + padding, 0, len(strokes), 0)
    return header + body + bytes(padding)


def make_ged(records):
    body = b""
    for kind, floats, element_id in records:
        rec = struct.pack(">I", kind) + struct.pack(">8f", *floats)
        for text in (element_id, "f" * 32):
            rec += struct.pack(">I", len(text)) + text.encode()
        rec += b"\x00"
        body += struct.pack(">II", 8, len(rec)) + rec
    return (hi.PENKIT_MAGIC + struct.pack(">IIQIII", 4, 0x10000, 42, 12, 4, len(body))
            + struct.pack(">I", len(records)) + body)


class StrokeLayout(unittest.TestCase):
    def test_style_20_bytes(self):
        strokes = hi.decode_penkit_strokes(make_bsd([
            make_stroke([(0, 0), (10, 5), (20, 0)]),
            make_stroke([(100, 100), (110, 120)]),
        ]))
        self.assertEqual(len(strokes), 2)
        self.assertEqual(strokes[0]["points"], [(0, 0), (10, 5), (20, 0)])
        self.assertEqual(strokes[0]["elapsed_ms"], [0, 10, 20])
        self.assertEqual(strokes[1]["style_size"], 20)

    def test_style_16_bytes(self):
        strokes = hi.decode_penkit_strokes(make_bsd([make_stroke([(1, 2), (3, 4)], style_size=16)] * 3, padding=0))
        self.assertEqual([s["points"] for s in strokes], [[(1, 2), (3, 4)]] * 3)
        self.assertEqual(strokes[0]["style_size"], 16)

    def test_short_stroke_metadata(self):
        strokes = hi.decode_penkit_strokes(make_bsd([
            make_stroke([(5, 5), (6, 6)], meta=(16.9, 18.7)),
            make_stroke([(7, 7), (8, 8)]),
        ]))
        self.assertEqual(len(strokes), 2)

    def test_color_is_abgr(self):
        strokes = hi.decode_penkit_strokes(make_bsd([make_stroke([(0, 0), (1, 1)], color=0xFFF7590A)]))
        self.assertEqual(strokes[0]["color"], (0x0A, 0x59, 0xF7))
        self.assertEqual(strokes[0]["opacity"], 1.0)

    def test_broken_block(self):
        data = bytearray(make_bsd([make_stroke([(0, 0), (1, 1)])]))
        data[52 + 48 + 4] = 99
        self.assertEqual(hi.decode_penkit_strokes(bytes(data)), [])


class Elements(unittest.TestCase):
    def test_ged_records(self):
        elements = hi.decode_penkit_elements(make_ged([
            (0x400, (531.4, 812.0, 0.8, 4148.2, -470.0, 1713.5, 2618.5, 0.0), "a" * 32),
            (0x800, (350.9, 149.2, 1.65, 8372.4, -1338.7, 909.1, 386.6, 0.0), "b" * 32),
            (0x400, (606.0, 1284.3, 1.0, 13969.7, 197.8, 519.7, 1101.4, -1.5619), "c" * 32),
        ]))
        self.assertEqual([e.kind for e in elements], [0x400, 0x800, 0x400])
        self.assertEqual(elements[1].element_id, "b" * 32)
        self.assertAlmostEqual(elements[0].x, 4148.2, places=2)
        self.assertAlmostEqual(elements[2].angle, -1.5619, places=4)


@unittest.skipUnless(os.environ.get("HINOTE_INFINITE_SAMPLE"), "set HINOTE_INFINITE_SAMPLE to a real note")
class RealSample(unittest.TestCase):
    def test_every_block_decodes_completely(self):
        with zipfile.ZipFile(os.environ["HINOTE_INFINITE_SAMPLE"]) as zf:
            files = {Path(n).name: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
        blocks = hi.discover_blocks(files)
        self.assertTrue(blocks)
        for block in blocks.values():
            header = hi.parse_penkit_header(block.data)
            self.assertEqual(len(hi.decode_penkit_strokes(block.data)), header.stroke_count, block.file_path)


if __name__ == "__main__":
    unittest.main()
