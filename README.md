# Hinoter

中文 | [English](README_EN.md)

Hinoter 用来读取和生成华为笔记的 `.hinote` 文件。目前，有界笔记可以预览并导出为 SVG、PDF 或 PNG，也可以把 PDF 封装成可导入的笔记；无边画布已有实验性预览，格式仍在继续分析。

项目不需要上传笔记文件。网页查看器在浏览器内处理文件，Python 脚本则适合批量导出。

## 先试一下

直接打开根目录的 `index.html`，再选择对应的查看器：

- `web/hinote-viewer.html`：有界笔记（`PENCILENGINE`）
- `web/infinite-viewer.html`：无边画布（`PENKITINFENG`，实验性）

把 `.hinote` 文件拖进页面即可。常规查看器支持逐页预览、页面选择，以及 SVG、PDF、PNG 导出。导入 PDF 作为页面背景时，查看器会从 CDN 加载 pdf.js；其余解析在本地完成。

## Python 导出

需要 Python 3.10 或更新版本。基本的 SVG 和 PDF 导出只使用 Python 标准库：

```powershell
python src/hinote_vector_export.py "笔记.hinote"
```

默认结果位于 `out/笔记/`：

```text
out/笔记/
├── svg/             每页一个 SVG
├── 笔记.pdf         合并后的 PDF
└── report.md        本次导出的摘要和未支持内容
```

可以一次处理多个文件，也可以指定输出目录：

```powershell
python src/hinote_vector_export.py "笔记一.hinote" "笔记二.hinote" -o export
```

导出 PNG 时需要 CairoSVG：

```powershell
pip install cairosvg
python src/hinote_vector_export.py "笔记.hinote" --ppi 300 --size a4
```

其他可选依赖：

- `fonttools`：在 PDF 中嵌入中文文本；系统中也需要可用的中文字体
- `pymupdf`：把导入的 PDF 页面还原为背景图

```powershell
pip install fonttools pymupdf
```

## 把 PDF 转成 .hinote

`pdf_to_hinote.py` 会把原始 PDF 封装成有界笔记，每个 PDF 页面对应一页笔记。PDF 文件不会被栅格化，原有的矢量图形、文字和页面尺寸会完整保留；导入华为笔记后，可以继续在页面上添加手写批注。

安装依赖：

```powershell
pip install pypdf
```

转换单个文件：

```powershell
python src/pdf_to_hinote.py "资料.pdf"
```

默认会在 PDF 旁边生成 `资料.hinote`。也可以指定输出文件和笔记标题：

```powershell
python src/pdf_to_hinote.py "资料.pdf" -o "导入用.hinote" --title "项目资料"
```

已有同名文件时，工具会停止而不是直接覆盖。确认要覆盖时加上 `--force`。

这里保留的是 PDF 原始内容，不会把 PDF 路径伪装成可编辑的手写笔迹。`.hinote` 格式来自样本逆向，不同版本的华为笔记可能存在兼容差异；首次使用时建议先导入一个副本确认效果。

## 目前支持什么

有界笔记的主流程已经可用，包括：

- 普通笔和荧光笔笔迹，保留可变宽度和透明度
- 文本框、图片和页面背景
- 横格、方格、点阵、空白等常见模板
- 导入的 PDF 或图片背景
- 横向与纵向页面
- SVG、PDF，以及可选的 PNG 输出

`.hinote` 是 ZIP 容器，页面元数据是 GZIP 压缩的 JSON；笔迹部分则是华为使用的二进制格式。已确认的字段和解析方法记录在 `docs/HINOTE_FORMAT.md`。

## 无边画布进度

无边画布使用另一套笔迹格式 `PENKITINFENG`。现在已经能够：

- 识别无边画布笔记和 `bsd_X_Y_*` 分块
- 顺序读取 52 字节 BSD 文件头、笔画描述和 28 字节点记录
- 提取全局坐标、压力、时间、颜色、笔宽和透明度
- 读取 GSD 中的 2500 单位网格及其与 BSD 块 ID 的对应关系
- 在 `web/infinite-viewer.html` 中平铺分块并做实验性预览

下面这些问题还没有完全解决：

- 笔画描述和点记录中部分保留字段的含义
- GED 元素数据以及 GSD 头部中的其余字段
- 彩色笔、荧光笔和不同笔宽的受控样本验证
- 无边画布接入 SVG、PDF 导出流程

BSD 中的坐标已经是画布全局坐标，网格只记录笔画从哪里落笔，跨格笔画不需要拼接或二次平移。由于目前只有黑色普通笔样本，无边画布预览仍适合格式核对，不应当视为完整导出。Python 主导出脚本遇到这类数据时会在 `report.md` 中标记。分析细节见 `docs/无界笔记格式分析.md`，相关代码在 `src/hinote_infinite.py`。

## 目录

```text
hinoter/
├── index.html                     查看器入口
├── src/
│   ├── hinote_vector_export.py    有界笔记导出
│   ├── hinote_infinite.py         无边画布解析实验
│   └── pdf_to_hinote.py           PDF 转有界笔记
├── tests/
│   └── test_pdf_to_hinote.py      PDF 转换与容器校验
├── web/
│   ├── hinote-viewer.html         有界笔记查看器
│   └── infinite-viewer.html       无边画布查看器
└── docs/
    ├── HINOTE_FORMAT.md           有界笔记格式记录
    └── 无界笔记格式分析.md        无边画布格式记录
```

## 许可

本项目使用 MIT License，详见 `LICENSE`。

## 感谢

如果这个项目对你有用，点颗star吧