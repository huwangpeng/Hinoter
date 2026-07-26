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
├── README.md
└── .gitignore
```

## Python 导出

```powershell
python src/hinote_vector_export.py "Your File.hinote"
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
- 支持笔迹、文本、图片、背景网格的 SVG 预览
- 可下载单页 SVG 和汇总 PDF
- 带进度条和响应式设计
- **横/纵向自适应**：缩略图按页面真实宽高比渲染；当笔记以横向（landscape）为主时自动进入「横向视图」（水平滚动、按视口高度适配宽屏），也可用「横向视图 / 网格视图」按钮手动切换；大图预览同时适配横向与纵向页面。

## 支持的格式特性

- **有界笔记 (PENCILENGINE)**：笔迹、文本框、图片、背景模板
- **导入页背景**：以 PDF 为底（`*_pdf`，抽取内嵌 JPEG）/ 以图像为底（`*_image`），按 `bkgAttachmentId`+`bkgAttachmentIndex` 还原满页背景层，笔迹叠在其上
- **背景模板**：宽横格、窄横格、点阵、小格子、中格子、空白
- **笔迹颜色**：普通笔、荧光笔（含老版本兼容）
- **笔迹渲染**：可变宽度矢量轮廓、圆弧端帽、收细算法
- **横/纵向**：按 `pageOrientation` 决定长短轴，横向页水平宽度恒为 1000
- **输出的PDF**：矢量笔迹路径、贴合的图片、子集化字体文本

## 待完成

- **无边画布 (PENKITINFENG)** 解码 — 分块笔迹的 stride、网格拼装

## 参考

- 文件格式详见 `docs/HINOTE_FORMAT.md`
- `.hinote` = ZIP 容器；`.jhinote` = GZIP+JSON 页面描述；笔迹为华为私有二进制

## 感谢
如果这个项目对你有帮助的话，请点一个star吧
