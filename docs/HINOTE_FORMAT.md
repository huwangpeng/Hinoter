# Huawei Hinote (.hinote) 文件格式分析与矢量导出

## 概述

Hinote 是华为笔记 App 的私有笔记格式，扩展名为 `.hinote`。本文档记录了对该格式的逆向分析结果以及矢量导出工具 `hinote_vector_export.py` 的渲染方案。

---

## 容器结构

`.hinote` 文件是一个标准 ZIP 压缩包，内部包含：

### 根目录
- `{noteId}.jhinote` — 笔记元数据（GZIP + JSON），包含标题、创建时间、背景等
- `custom_md.jhinote` — 文件哈希校验信息

### files/ 目录
- `*.bin` — PENCILENGINE 格式笔迹数据
- `*.jpg` / `*.png` — 嵌入的图片文件
- `*_outline.json` — 大纲/目录数据

### pages/ 目录
- `{uuid}.jhinote` — 每一页的数据（GZIP + JSON），包含该页的所有元素和附件引用

---

## `pageElement` 元素类型

每页的 `pageElement` 数组按 `positionZ` 排序，支持以下类型：

| elementType | 描述 | 数据来源 |
|-------------|------|---------|
| 0 | 文本框 | `html` 字段（自定义 XML） |
| 1 | 图片 | `filePath` → `files/*.jpg` / `*.png` |
| 其他 | 未知（暂不支持） | — |

### 文本框（elementType=0）

`html` 字段结构：
```xml
<note><element type="TEXT">
  <font color="#ARGB"><hw_font size="1.15">文本内容</hw_font></font><br>
  <font color="#ARGB"><hw_font size="1.15">更多文本</hw_font></font>
</element></note>
```

- `color` = ARGB 十六进制字符串（如 `#ff000000` = 不透明黑）
- `hw_font size` = 字号（与 `scale` × `sourceDpi` 换算为页面坐标 px）
- 文本以 HTML 实体编码（`&#x...;`）
- `data1` 中可获取 `sourceDpi`（如 400）
- `positionX/Y` 为页面比例坐标（0~1），乘以页面宽高得绝对坐标
- `width/height` 同样为比例值
- `scale` 为设备 DPI 缩放因子

---

## 笔迹格式（PENCILENGINE）

### 文件头（76 字节）

```
偏移  0: "PENCILENGINE" (12 字节)
偏移 12: 0x0000004C = 76（头部大小）
偏移 16: 0x00010001（版本/标志）
偏移 24: 0x00000001
偏移 76: 第一个 Style Record
```

### 笔画数据链

每个笔画包含：

#### 1. Style Record（60 字节）

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 4B float | pointCount | 镜像，与 point table 的 count 一致 |
| 4 | 4B | uint[1] | 标志位，通常 0x01000000 |
| 8 | 4B uint | color_sentinel | 0 或 0xFFFFFFFF=黑色标记，否则为直接 ARGB |
| 12 | 4B uint | **pen_type** | 笔类型：1=普通笔，2=铅笔，3=毛笔，5=荧光笔 |
| 16 | 4B | 保留字段 | |
| 20 | 4B float | **R** | RGB 浮点分量（type 5 为 BGR 顺序） |
| 24 | 4B float | **G** | |
| 28 | 4B float | **B** | |
| 32 | 4B float | **softness** | 透明度/软度（荧光笔=0.8，毛笔=0.2） |
| 36 | 4B float | [未知] | 通常与 softness 相同 |
| 40 | 4B float | **base_width** | 基准笔宽（普通笔 2~6，荧光笔 20~24） |
| 44-56 | 12B | 保留 | 全零 |

**颜色规则**：
- `color_sentinel` 不为 0 或 0xFFFFFFFF → 直接作为 ARGB 提取
- `color_sentinel` 为 0 或 0xFFFFFFFF → 使用 RGB 浮点分量
- type 5 高亮笔的浮点分量存储顺序为 BGR（非 RGB）

**笔类型规则**：
- type 1,2 → 不透明（opacity = 1.0）
- type 3 → 毛笔，透明度取自 softness
- type 5 → 荧光笔，透明度取自 softness

**颜色通道顺序（版本差异）**
- 新版 style record 在偏移 4 处标志为 `0x01000000` 或 `0x01010000`，此时普通笔/铅笔/毛笔的浮点颜色按 **RGB** 存储。
- 旧版（如 `1.hinote` page 9-12）偏移 4 为随机值，浮点颜色按 **BGR** 存储，直接读取会导致红色与蓝色互换。解析时需根据偏移 4 的标志判断顺序。
- 荧光笔（type 5）在所有观测版本里均按 **BGR** 存储，始终需要反转红蓝通道。

#### 2. Point Table Header（16 字节）

| 偏移 | 大小 | 字段 |
|------|------|------|
| 0 | 4B | **prefix**：0=普通笔画，2=特殊笔画（荧光笔等） |
| 4 | 4B | **count**：采样点数量（2~16384） |
| 8 | 4B | **stride**：固定为 36 |
| 12 | 4B | reserved：固定为 0 |

#### 3. Point Data（count × 36 字节）

| 偏移 | 字段 |
|------|------|
| 0 | reserved |
| 4 | **X 坐标**（big-endian float） |
| 8 | **Y 坐标**（big-endian float） |
| 12 | reserved |
| 16 | **pressure**（压力值，0~1） |
| 20 | 未知（高亮笔=2.3） |
| 24 | 未知（高亮笔=10.0） |
| 28 | 未知（恒为 0.2，非宽度） |
| 32 | 未知 |

#### 4. Index/Padding（108 字节）

每个 Point Table 之后有约 108 字节的索引/填充数据。

### 解析策略

采用**逐字节扫描 + 验证**策略，而非链式跟随：

1. 每 4 字节对齐扫描 `[prefix, count, 36, 0]` 模式
2. 接受 `prefix ∈ {0, 2}`
3. 校验 style record 的 `pen_type ∈ {1,2,3,5}` 和 `base_width ∈ (0,100]`
4. 校验至少一个采样点坐标有效
5. 跳过 `16 + count × 36` 字节继续扫描

---

## 页面布局

### 页面尺寸
```
pageRatio（如 0.70695555）   恒为「短边/长边」比例，与朝向无关
pageOrientation: 0=纵向(portrait), 1=横向(landscape)

笔迹坐标系的水平宽度恒为 1000；pageOrientation 仅决定垂直高度：
  纵向 (orientation=0)：width  = 1000，height = 1000 / pageRatio（如 1414.516）
  横向 (orientation=1)：width  = 1000，height = 1000 × pageRatio（如 706.956）
```
> 注：笔迹 x 坐标在所有页里都落在 [0, 1000] 区间，因此水平宽度固定为 1000。
> 横向页若误把 width 取 1000/pageRatio，笔迹 x[0..1000] 会只占页面宽度的
> 1000/1414 ≈ 70%（「刚好占满一整页的字迹只剩 2/3」）。
> 实测 `sample/2.hinote`（4 页 `pageOrientation=1`）缩略图 1080×763（1.4155），
> 与 width=1000/height=1000*0.70695555=706.956（1.4145）一致，笔迹填满全宽。

### 背景色
```
pageColor（如 -1 = 白色, -1302 = 暖白 #FFFAEA）
计算：((value & 0xFFFFFFFF) >> 16 & 0xFF), (>> 8), (& 0xFF)
```

### 背景模板（`background` 字段）

页元数据的 `background` 字段指向华为 Notes 内置模板，取值与含义（由 `页面样例.hinote` 实测）：

| background | 类型 | 间距(页单位) | 起点 |
|-----------|------|-----------|------|
| `base1` | 空白 | — | — |
| `base2` | 中格子 | 101 | (102, 101) |
| `base3` | 小格子 | 58.3 | (62, 59) |
| `base4` | 宽横格 | 75（仅水平线） | y=71 |
| `base5` | 窄横格 | 47（仅水平线） | y=47 |
| `base6` | 点阵 | 33 | (18, 17) |

- 网格线/点颜色为浅蓝 `#E4E4FF`（从缩略图采样 RGB(228,228,255)），线宽 0.8，点边长 2.6。
- 间距和起点通过缩略图亮度剖面（局部最小值）+ 用户在网格交点/线条上做的标记笔画交叉验证得出。
- 渲染：SVG 用 `<line>`/`<circle>`；PDF 用 `m`/`l`/`S` 描边线条、`re f` 填充小方块代替点（PDF `arc` 算子不被 MuPDF 等阅读器支持）。网格在背景色之后、图片/文本/笔迹之前绘制。

### 图片嵌入
图片元素使用比例坐标（`positionX/Y × width/height`），支持旋转（`angle`）。PDF 中 PNG 带透明通道时分离为 RGB 数据 + `/SMask`。

---

## 渲染方案

### 笔画渲染（当前方案：整笔轮廓填充）

替代了以下已废弃的方案：
1. ❌ `stroke-linecap="round"` — 无笔锋变化，视觉扁平
2. ❌ 逐段填充多边形 — 抗锯齿接缝产生"空心圆点"
3. ❌ Catmull-Rom 样条 — 锐角处飞线

**当前方案（整笔轮廓）**：

```
对每个笔画：
1. 计算每个采样点的切线（前后点差分）
2. 计算法线 → left/right 偏移轮廓
3. 组装闭合路径：
   left[0] → left[1] → ... → left[n-1]
   → 16步半圆弧（端帽，从 left[n-1] 到 right[n-1]，向前凸出）
   → right[n-1] → right[n-2] → ... → right[0]
   → 16步半圆弧（起帽，从 right[0] 到 left[0]，向后凸出）
4. n > 4 时端点收细（r × 0.12），形成笔锋
```

**关键参数**：
- 端帽步数：16（精度/大小平衡）
- 笔画宽度：`pressure × base_width × 0.8`
- 端帽方向：`-`（减角度，朝外凸出）
- 收细阈值：仅 n > 4 且为首尾点

### PDF 透明支持
使用 `/ExtGState` 的 `/CA`（描边透明度）和 `/ca`（填充透明度），配合 `/GS{opacity} gs` 命令。

### PDF 文本框（嵌入 CJK 字体子集）

SVG 可直接渲染 Unicode 文本，但 from-scratch 的 PDF 写入器没有内置 CJK 字体。实现方案：

1. **字体选择**：扫描系统目录（`C:/Windows/Fonts/simhei.ttf`、`msyh.ttc`、`/System/Library/Fonts/PingFang.ttc`、`/usr/share/fonts/.../NotoSansCJK-Regular.ttc` 等）。依赖 `fonttools` 做子集化（按需安装）。
2. **子集化**：用 `fontTools.subset.Subsetter` 仅保留文本中出现的字符，写入 `Identity-H` 编码的 Type0/CIDFontType2 字体。每个字形取 `head.xMin/yMin/xMax/yMax` 作为 `FontBBox`，按 1000 单位缩放 `Ascent/Descent/CapHeight`，并把 `hmtx` 提供的 advance 换算为 `W` 宽度数组（`DW 1000`）。
3. **upem 归一化**：子集化后必须调用 `fontTools.ttLib.scaleUpem.scale_upem(font, 1000)`，把 SimHei (upem=256) 等缩放到 1000。否则部分阅读器按 1000 处理 `unitsPerEm` 会把字形放大 ~3.9 倍且产生锯齿。
4. **ToUnicode CMap**：为了 PDF 阅读器可以复制/搜索文本，再额外写入一个 `/ToUnicode` 流（CMapType 2，`beginbfchar` 将 GID 映射回 Unicode 码点，每块 ≤100 项）。
5. **行布局**：第 i 行基线 `y = te.y + fs × (1 + 1.2×i)`，PDF Y 取 `page.height − y`。PDF 内容流：`/F1 fs Tf  1 0 0 1 x pdf_y Tm  <gid_hex> Tj`。
6. **文本与笔迹层级**：在图片之后、笔迹之前输出 `BT…ET` 块，与 SVG 一致。

---

## 关键发现与决策记录

### 笔类型字段位置
`uint[3]`（Style Record 偏移 12）是区分荧光笔、毛笔、普通笔的关键字段。最初的亮度启发式（`max(RGB) >= 60`）错误标记了有色圆珠笔。

### prefix=2 表
荧光笔等特殊笔画使用 `prefix=2` 的表头而非标准的 `prefix=0`。最初的 `prefix == 0` 过滤漏掉了所有 prefix=2 的表，直到全面扫描才发现。

### 端帽方向
半圆弧端帽的数学方向：
- `+`（加角度）= 从 left 向 right 经过反向（180°），造成凹陷
- `-`（减角度）= 从 left 向 right 经过正向（0°），朝外凸出

### 2 点笔画（荧光笔）
荧光笔多为仅含 2 个采样点的短笔画（覆盖一行文字）。端点收细（`r × 0.12`）会让整笔都变细，因此短笔画（n ≤ 4）跳过收细。

**宽度与版本兼容**：荧光笔是标记笔，渲染宽度应固定为 `base_width × 0.8`。早期 hinote 版本在 type 5 的压感字段里写入极小的噪声值（如 0.01），若直接套用 `pressure × base_width` 会让高亮条几乎不可见。因此解析时忽略荧光笔的逐点压感，统一按固定宽度渲染；新版文件压感本身为 1.0，结果一致。

---

## 已废弃的格式探索

### PENKITINFENG
PENCILENGINE 的另一种变体，用于无限画布笔记。字节前缀为 `PENKITINFENG`。其图块式画布结构尚未解码。

### 边距/网格
`bkgAttachmentId` 和 `background` 字段（如 `"base5"`）指向华为 Notes 内置背景模板，非嵌入式图片，暂不支持。

---

## 文件说明

- `hinote_vector_export.py` — 主程序：解析 + SVG/PDF 导出
- `hinote_extract.py` — 已废弃的提取工具
- `1.hinote` — 测试用 25 页有界笔记（含荧光笔、毛笔、图片）
- `有界.hinote` — 小规模有界笔记（仅 1 页）
- `无边.hinote` — 无限画布笔记（暂不支持）
