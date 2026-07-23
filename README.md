# 华为笔记 `.hinote` 解析与矢量导出工具

解析华为笔记 `.hinote` 文件格式，将手写笔迹、文本、图片和背景模板还原为矢量 SVG / PDF。

## 项目结构

```
hinoter/
├── src/
│   └── hinote_vector_export.py   # 核心导出脚本
├── web/
│   └── hinote-viewer.html        # 纯本地网页查看器
├── docs/                         # 格式文档
│   ├── HINOTE_FORMAT.md          # 格式逆向文档
│   └── 解析结果.md               # 初步解析记录
├── tools/                        # 辅助工具 & 测试
│   ├── check_final.py            # 渲染正确性验证
│   ├── _grid_detect.py           # 背景网格间距检测
│   └── _test_web.mjs             # 网页核心逻辑测试
├── out/                          # 导出产物（SVG/PDF）— 不提交
├── README.md
└── .gitignore
```

## Python 导出

```powershell
python src/hinote_vector_export.py "samples/有界.hinote"
```

结果写入 `out/<文件名>/`：
- `svg/page_*.svg` — 每页独立矢量图
- `<文件名>.pdf` — 汇总 PDF（含字体子集化）
- `report.md` — 导出摘要

### 依赖

- `fonttools` — CJK 字体子集化（PDF 文本必需）
- `pymupdf`（可选）— 渲染 PDF 预览验证

## 网页查看器

直接打开 `web/hinote-viewer.html` 即可使用：

- 拖入 `.hinote` 文件或点击选择
- **全部计算在浏览器本地完成**，不上传任何数据
- 支持笔迹、文本、图片、背景网格的 SVG 预览
- 可下载单页 SVG 和汇总 PDF
- 带进度条和响应式设计

## 已支持的格式特性

- **有界笔记 (PENCILENGINE)**：笔迹、文本框、图片、背景模板
- **背景模板**：宽横格、窄横格、点阵、小格子、中格子、空白
- **笔迹颜色**：普通笔、荧光笔（含老版本兼容）
- **笔迹渲染**：可变宽度矢量轮廓、圆弧端帽、收细算法
- **输出的PDF**：矢量笔迹路径、贴合的图片、子集化字体文本

## 待完成

- **无边画布 (PENKITINFENG)** 解码 — 分块笔迹的 stride、网格拼装

## 参考

- 文件格式详见 `docs/HINOTE_FORMAT.md`
- `.hinote` = ZIP 容器；`.jhinote` = GZIP+JSON 页面描述；笔迹为华为私有二进制
