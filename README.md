# 华为笔记 `.hinote` 解析实验

两份样本均为 ZIP 容器。容器内的 `.jhinote` 文件是 GZIP 压缩的 UTF-8 JSON，图片保持 PNG/JPEG 原格式，手写笔迹则使用华为私有二进制格式。

## 使用方法

```powershell
python hinote_extract.py "无边.hinote" "有界.hinote"
```

结果默认写入 `parsed/<文件名>/`：

- `raw/`：ZIP 中的原始条目
- `decoded/`：解压后的 `.jhinote.json`
- `metadata.json`：汇总元数据、页面、文件签名和哈希
- `report.md`：便于阅读的结构报告

本机安装 Tesseract 并准备中文语言数据后，可以同时 OCR 图片资源：

```powershell
python hinote_extract.py "无边.hinote" "有界.hinote" `
  --ocr-language chi_sim `
  --tessdata-dir "C:\path\to\tessdata"
```

## 已识别格式

- `PENCILENGINE`：有界笔记的笔迹数据，样本采用大端数值记录。
- `PENKITINFENG`：无界画布的分块笔迹数据，文件名中的坐标表示画布分块。
- `gsd_*.bin` / `ged_*.bin`：无界笔记全局状态数据。
- `*_navigation.json`：无界画布浏览位置和当前视图索引。

私有笔迹二进制中不存在可直接提取的正文字符串。当前工具保留这些文件并报告格式与大小；可读内容优先来自原始图片、缩略图和 OCR。

## 矢量导出

```powershell
python hinote_vector_export.py "有界.hinote" "无边.hinote"
```

`PENCILENGINE` 笔迹会导出为 SVG `<path>`，并汇总为一个 PDF；两种输出都保持笔迹为矢量路径，缩放不会失真。导出会读取页面背景色、图片元素的位置和尺寸，并从笔画记录中的实际压感字段恢复逐点粗细。输出在 `vector-export/<文件名>/`。

无界笔记的 `PENKITINFENG` 分块笔迹与有界格式不同。工具会识别并报告这些块，但不会用缩略图替代为伪矢量内容。
