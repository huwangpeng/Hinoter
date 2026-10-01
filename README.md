# Hinoter

中文 | [English](README_EN.md)

一套读写华为笔记 `.hinote` 文件的工具。浏览器中的查看器可以预览笔记并导出为 SVG、PDF 或 PNG；反过来，也可以把普通 PDF 封装成华为笔记可导入的 `.hinote`，导入后仍可继续手写批注。所有解析均在本地完成，笔记不会上传。`.hinote` 为私有格式，本项目基于样本逆向分析实现，过程与结论记录在 [docs/](docs/) 下；无边画布（`PENKITINFENG`）格式仍在解析中，功能尚不完整。

## 网页查看器

打开根目录的 [index.html](index.html)，按笔记类型选择查看器：

- [web/hinote-viewer.html](web/hinote-viewer.html)：常规笔记（`PENCILENGINE`）
- [web/infinite-viewer.html](web/infinite-viewer.html)：无边画布（`PENKITINFENG`），实验性

将 `.hinote` 文件拖入页面即可。常规查看器支持逐页预览、页面选择以及 SVG / PDF / PNG 导出。唯一的例外是 PDF 背景：pdf.js 从 CDN 加载，其余解析均在本地的浏览器内完成。

## 命令行导出

需要 Python 3.10+。基础的 SVG 与 PDF 导出不依赖第三方库：

```powershell
python src/hinote_vector_export.py "笔记.hinote"
```

结果默认写入 `out/笔记/`：每页一张 SVG 外加合并后的 PDF，未支持的内容会记录在同目录的 `report.md` 中。也支持一次处理多个文件：

```powershell
python src/hinote_vector_export.py "笔记一.hinote" "笔记二.hinote" -o export
```

PNG 导出依赖 CairoSVG：

```powershell
pip install cairosvg
python src/hinote_vector_export.py "笔记.hinote" --ppi 300 --size a4
```

两个可选依赖：安装 `fonttools` 后可在 PDF 中嵌入中文文本（系统同时需要可用的中文字体）；安装 `pymupdf` 后可还原导入的 PDF 背景页。

```powershell
pip install fonttools pymupdf
```

## 多图层

华为笔记 `hinote_1.0.6` 起支持页面图层。本工具会自动识别，并把图层信息写进 `report.md`：

```powershell
# 合并导出（默认行为，所有可见图层按自下而上合成）
python src/hinote_vector_export.py "多图层笔记.hinote"

# 额外按图层各导出一张 SVG 到 svg/layers/
python src/hinote_vector_export.py "多图层笔记.hinote" --split-layers

# 按图层名或 id 筛选
python src/hinote_vector_export.py "多图层笔记.hinote" --only-layer "图层 1"
python src/hinote_vector_export.py "多图层笔记.hinote" --hide-layer 0

# 连笔记里被隐藏的图层一起导出
python src/hinote_vector_export.py "多图层笔记.hinote" --all-layers
```

`--hide-layer` / `--only-layer` 都匹配图层名（不分大小写）或 `layerId`，可重复使用。默认会跳过笔记自身标记为隐藏的图层。旧版本笔记没有图层信息，行为与以往完全一致。

笔迹归属靠每个笔画样式记录前的 16 字节图层 UUID 判定，格式细节与判别方法见 [HINOTE_FORMAT.md](docs/HINOTE_FORMAT.md#多图层hinote_106)。

## 测试

```powershell
python -m unittest discover -s tests -v
```

`tests/test_layers.py` 用内存里合成的最小笔迹文件覆盖图层解析与筛选逻辑；如果本机存在真实样本，会额外做端到端核对，路径也可用 `HINOTE_MULTILAYER_SAMPLE` / `HINOTE_COLOR_SAMPLE` / `HINOTE_ZORDER_SAMPLE` 指定。三项核对分别验证图层划分、浮点颜色的 BGR 顺序，以及上层图层压住下层的绘制顺序。

## 将 PDF 转换为 .hinote

[src/pdf_to_hinote.py](src/pdf_to_hinote.py) 将 PDF 的每一页原样封装进 `.hinote` 容器。PDF 不经过栅格化，矢量图形、文字与页面尺寸完整保留，导入华为笔记后页面依然清晰，并可直接手写批注。

```powershell
pip install pypdf
python src/pdf_to_hinote.py "资料.pdf"
```

默认在 PDF 同目录生成 `资料.hinote`，可用参数指定输出文件与笔记标题：

```powershell
python src/pdf_to_hinote.py "资料.pdf" -o "导入用.hinote" --title "项目资料"
```

输出文件已存在时工具会默认停止；确认覆盖时使用 `--force`。

两点说明：这里保留的是 PDF 本身的内容，并非将其伪装为可编辑笔迹，因此在华为笔记中不能当作文字直接修改；此外 `.hinote` 格式来自样本逆向，不同版本的华为笔记 App 可能存在兼容差异，首次使用时建议先导入副本验证效果。

## 支持范围

常规笔记的主干流程已可用：钢笔与荧光笔笔迹（含可变宽度和透明度）、文本框、图片、横格 / 方格 / 点阵等模板背景、导入的 PDF 或图片页面，横向纵向页面均可处理。多图层笔记（`hinote_1.0.6`）也能正确还原：笔画按所属图层分组，图层名称、顺序与可见性都从笔记里读出，可按图层筛选或分层导出。常规黑白笔记基本无损导出。

无边画布目前能力有限。其笔迹采用另一套格式 `PENKITINFENG`：现已能识别画布类型笔记、解析 52 字节的 BSD 文件头与 28 字节的点记录，提取全局坐标、压力、时间戳、颜色和笔宽，并在 [src/hinote_infinite.py](src/hinote_infinite.py) 与 [web/infinite-viewer.html](web/infinite-viewer.html) 中实现实验性预览。但笔画描述中仍有较多保留字段含义未明，现有样本仅覆盖黑色钢笔，因此预览结果适合用于格式核对，尚不能作为完整的导出流程使用。遇到此类数据时主脚本会在 `report.md` 中标记，详细分析见[无界笔记格式分析.md](docs/无界笔记格式分析.md)。

## 项目结构

```text
hinoter/
├── index.html                     查看器入口
├── src/
│   ├── hinote_vector_export.py    有界笔记导出
│   ├── hinote_infinite.py         无边画布解析实验
│   └── pdf_to_hinote.py           PDF 转有界笔记
├── tests/
│   └── test_layers.py             多图层解析与筛选测试
├── web/
│   ├── hinote-viewer.html         有界笔记查看器
│   └── infinite-viewer.html       无边画布查看器
└── docs/
    ├── HINOTE_FORMAT.md           有界笔记格式记录
    └── 无界笔记格式分析.md        无边画布格式记录
```

## 许可

MIT License，详见 [LICENSE](LICENSE)。

## 感谢

如果这个项目对你有用，欢迎点个 star。
