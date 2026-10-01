# Huawei Hinote (.hinote) 文件格式分析与矢量导出

## 概述

Hinote 是华为笔记 App 的私有笔记格式，扩展名为 `.hinote`。本文档记录了对该格式的逆向分析结果以及矢量导出工具 `hinote_vector_export.py` 的渲染方案。

---

## 容器结构

`.hinote` 文件是一个标准 ZIP 压缩包，内部包含：

### 根目录
- `{noteId}.jhinote` — 笔记元数据（GZIP + JSON），包含标题、创建时间、背景等。`customNoteContent.extendFields` 记录数据版本（如 `hinote_1.0.4`、`hinote_1.0.5`、`hinote_1.0.6`），格式升级时由此字段区分。1.0.6 起 `data1` 里还带有图层定义，详见[多图层（hinote_1.0.6）](#多图层hinote_106)
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

> `hinote_1.0.6` 起该记录改为 **52 字节**，并在其前多出 16 字节的图层 UUID，字段整体前移；两套布局的对照表见[多图层（hinote_1.0.6）](#多图层hinote_106)。

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

### 追加式累积存储（hinote_1.0.5 实测）

通过对比同一笔记升级前（`hinote_1.0.4`）与升级后（`hinote_1.0.5`）的样本确认：

- 笔迹 bin 采用**追加式存储**：页面被编辑时，原有数据逐字节保留（含旧 Style Record 和点表），新的笔画作为独立段落追加到文件末尾；文件头偏移 96 处的数据区长度与点表总数随之累加。一个 bin 中可以包含多个编辑会话的段落。
- 升级本身不重排已有数据：对比样本中 24 个未编辑页的 bin 完全一致；被编辑页的 bin 前缀与旧文件逐字节相同，仅追加了新内容。
- `prefix` 点表在旧样本中全部为 0，新样本中出现 `prefix=2` 的荧光笔小段（每段 count=2），且新旧可混存于同一文件。
- 文件级索引头部（紧跟 Style Record 的区域）记录了数据区长度与点表总数：`0x0F06B8`（984760）/ 703 表 → `0x1E50BC`（1986748）/ 1522 表。
- 页面元数据引用可能随升级改写：被编辑页的 `pages/*.jhinote` 中 `attachment.filePath` 指向新生成的 bin（旧 bin 从容器中删除）。新增页的路径风格为 `/data/user/0/...`（旧页为 `/data/data/...`），其 `data1` 可无 `book_mark` 字段、`data5` 缩略图字段可为空——解析时均不应依赖这些差异。
- 结论：现有的「扫描 + 验证」策略天然兼容追加式存储，无需按会话分段处理；重复绘制不存在（各段落互不重叠）。

### 解析策略

采用**逐字节扫描 + 验证**策略，而非链式跟随：

1. 每 4 字节对齐扫描 `[prefix, count, 36, 0]` 模式
2. 接受 `prefix ∈ {0, 2}`
3. 校验 style record 的 `pen_type ∈ {1,2,3,5}` 和 `base_width ∈ (0,100]`
4. 校验至少一个采样点坐标有效
5. 跳过 `16 + count × 36` 字节继续扫描

---

## 多图层（hinote_1.0.6）

华为笔记在 `hinote_1.0.6`（`customNoteContent.extendFields`）引入了页面图层。图层定义是**二次编码的 JSON 字符串**（外层字段本身是字符串，需要再 `json.loads` 一次）；笔画归属则记录在每个笔画自己的样式记录里。

### 笔记级图层定义（`{noteId}.jhinote`）

`customNoteContent.data1` 解析后：

| 字段 | 含义 |
|------|------|
| `canvasLayerList` | JSON 字符串 → 图层数组 |
| `layerOrderedInfo` | JSON 字符串 → `layerId` 字符串数组，绘制顺序 |
| `activeLayerId` | 当前选中的图层 |

单个图层对象：

```json
{"id":"96a0201cf65f4f958c731249db4729ee","noteId":"d444f48f726b4e909956db4483945986",
 "layerId":"297ea0829a4a4f10801e2fd0bc3f8abd","name":"图层 1","visibleStatus":1,"lockStatus":0,
 "createdTime":1790298908177,"modifiedTime":1790298908177,"isDelete":0,"deleteTime":0,
 "cloudSync":1,"data1":"","data2":"","data3":"","data4":"","data5":""}
```

- `layerId` 为 32 位小写十六进制（无连字符）。
- 内置「默认图层」的 `layerId` 是字符串 `"0"`，在笔迹文件里对应 16 字节全零。
- `visibleStatus`：1 可见、0 隐藏；`lockStatus`：0 未锁定；`isDelete` 非 0 需跳过。

### 页面级绑定（`pages/{uuid}.jhinote`）

`customNotePageContent.data1` 解析后：

| 字段 | 含义 |
|------|------|
| `bindLayerInfos` | JSON 字符串 → 本页绑定的图层数组（字段同上） |
| `bindLayerIds` | JSON 字符串 → `layerId` 数组 |

`bindLayerIds` 是本页的权威顺序；缺失时回落到笔记级定义。此外 1.0.6 的页面 `data1` 新增了 `localData1`、`note_time`，这两个字段与图层无关。

### 笔画归属：样式记录前的 16 字节图层 UUID

1.0.6 的每个笔画在样式记录**之前**多出 16 字节，内容即该笔所属图层的 `layerId` 原始字节：

```
[图层 UUID 16B][样式记录 52B][prefix, count, 36, 0 (16B)][count × 36B 采样点][索引/填充 108B]
```

- UUID 为 16 字节全零 ⇒ 默认图层 `"0"`；否则按十六进制小写展开即 `layerId`。
- 样式记录同时从 60 字节缩短为 52 字节，字段整体前移：

| 偏移 | 1.0.4 / 1.0.5（60B） | 1.0.6（52B） |
|------|----------------------|--------------|
| +0 | float pointCount（镜像） | uint32 **pen_type**（1/2/3/5） |
| +4 | uint32 标志（颜色通道顺序） | uint32 颜色哨兵（0 = 用浮点分量） |
| +8 | uint32 颜色哨兵 | float **B** |
| +12 | uint32 **pen_type** | float **G** |
| +16 | 保留 | float **R** |
| +20 | float **R** | float **softness** |
| +24 | float **G** | float（与 softness 相同） |
| +28 | float **B** | float **base_width** |
| +32 | float **softness** | 保留 16B |
| +36 | float（与 softness 相同） | — |
| +40 | float **base_width** | — |
| +44 | 保留 12B | — |
| +48 | — | float（观测恒为 1.0，含义未知） |

### 布局判别

两套布局共用同一个 `[prefix, count, 36, 0]` 点表头，只有样式记录长度不同，因此**不能只靠扫描点表**区分。文件头偏移 **112** 处的 uint32 记录了该文件精确的点表数量，可用作判据：

1. 扫描全部点表候选（表头形状 + 采样点全部有限且不塌缩到一点）；
2. 分别按 60 字节、52 字节布局校验 `pen_type ∈ {1,2,3,5}` 且 `0 < base_width ≤ 100`；
3. 取合法笔数与该头部计数相等的布局；两者都相等或计数不可信时取合法笔数更多的一方，平局沿用旧布局。

实测判别无歧义：`sample/` 下 56 个笔迹文件里，60 字节布局能把扫描到的点表**全部**解成合法样式（合法笔数 = 扫描到的点表数），而 52 字节布局最多只命中 1 条；多图层样本恰好相反——60 字节布局 0 条、52 字节布局 16 条，等于扫描到的点表数，也与头部计数 16 一致。

> 注：头部计数偶尔会略多于实际可解码的笔数（56 个旧文件中有 46 个存在这种偏差，例如头部 721、实际 719）。这些点表本身退化了，新旧解析器都会跳过，所以「合法笔数 = 头部计数」并不总能命中；命中不了时回退到「取合法笔数更多的一方」，旧文件仍然稳定落到 60 字节布局。

### 绘制顺序与渲染

- `layerOrderedInfo` / `bindLayerIds` 的下标 0 **就是**最先绘制（最底层）的图层，下标越大越靠上，详见[图层绘制顺序（已确认）](#图层绘制顺序已确认)。
- 同一图层内仍遵循「荧光笔在下、不透明笔迹在上」。
- 图片与文本框（`pageElement`）在有图层信息的样本中同样为空、在旧样本中没有图层字段，因此仍作为不分层的元素画在所有笔迹图层之下。
- 命令行：`--split-layers` 按图层额外导出 `svg/layers/`；`--hide-layer` / `--only-layer` 可按图层名或 id 取舍；`--all-layers` 连隐藏图层一并导出。网页查看器有对应的图层勾选面板。

### 浮点颜色的通道顺序（已确认）

1.0.4/1.0.5 靠样式记录偏移 +4 的标志区分 RGB / BGR；1.0.6 该位置已被颜色哨兵占用，判别标志消失。用带彩色手写的样本 `多图层测试_202610012341_5867.hinote` 可以确定：**1.0.6 仍然按 BGR 存放**，与旧版一致。

该样本在点阵页上用三种颜色写了「红 / 绿 / 蓝」三个字（笔记自带缩略图即为准），三组笔画的浮点分量与位置如下：

| 存储的三个 float | 按 BGR 读 | 按 RGB 读 | 笔画数 | 页内 y 区间 | 缩略图对应字 |
|------------------|-----------|-----------|--------|-------------|--------------|
| (0.149, 0.251, 0.910) | (232, 64, 38) 红 | (38, 64, 232) 蓝 ✗ | 4 | 176–234 | 红（最上） |
| (0.808, 0.992, 0.894) | (228, 253, 206) 浅绿 | (206, 253, 228) 浅青 | 7 | 261–312 | 绿（居中） |
| (0.890, 0.694, 0.275) | (70, 177, 227) 天蓝 | (227, 177, 70) 橙 ✗ | 13 | 332–420 | 蓝（最下） |

只有按 BGR 解读时，最上面的字才是红色、最下面的才是蓝色，与缩略图吻合；按 RGB 解读会得到蓝 / 浅绿 / 橙，直接矛盾。荧光笔（type 5）在所有版本里也都是 BGR，因此 1.0.6 可以统一按 BGR 处理。

### 图层绘制顺序（已确认）

`layerOrderedInfo` / `bindLayerIds` 的**下标 0 是最底层**（最先绘制），下标越大越靠上。

依据是 `多图层测试_202610012345_08610.hinote`：用户选中「图层 1」在「蓝」字上画了一笔红色涂鸦，`activeLayerId` 随之变为 `297ea0829a4a4f10801e2fd0bc3f8abd`。数据侧完全对得上——

- 「蓝」字在默认图层，`layerOrderedInfo` 下标 **0**（x 514–615、y 332–420）；
- 红色涂鸦在「图层 1」，下标 **1**，笔迹文件里确实记在图层 1 名下（单笔、笔宽 4.0、x 491–611、y 333–436），两者大面积重叠。

放大笔记自带的缩略图可见，两笔相交处**全部是红色压住蓝色**，蓝色在重叠区被完全遮挡：

```
红（图层 1，下标 1）  ←  画在上层
蓝（默认图层，下标 0） ←  画在下层
```

若下标 0 是最顶层，图层 1 就会被压在下面，与缩略图矛盾。因此绘制顺序就是 `layerOrderedInfo` 的正序，本项目 Python 与网页两端的实现都与之一致。

### 已验证与未决

三份多图层样本的导出结果都已与华为笔记自己生成的缩略图逐像素核对一致：三行手写、点阵背景、「layer 2」右侧那处小标记、「红 / 绿 / 蓝」三色手写，以及第三份里跨图层的红色涂鸦压住蓝字——位置、颜色、遮挡关系都对得上。

仍需在拿到更多样本后复核的点：

- 1.0.6 样式记录尾部 +48 的 float（观测恒为 1.0）以及 +32..47 的 16 字节保留区含义。
- 文本框 / 图片是否也可归属图层（现有样本无法判定）。
- 图层的 `lockStatus` 目前只读出来备用，没有参与导出决策；`visibleStatus` 已生效。

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

### 导入页背景（PDF / 图像为底）
华为笔记支持把外部 PDF 或图像作为整页背景（`bkgAttachmentId` + `bkgAttachmentIndex`），手写笔迹叠在其上。

- **关联映射**在顶层 `{noteId}.jhinote` 的 `customNoteContent.attachment[]`：每项 `{ id, filePath }`，`id` 即页面 `bkgAttachmentId`，`filePath` 取 basename 指向 `files/*_pdf` 或 `files/*_image`。
- **PDF 底（`*_pdf`）**：文件是标准 PDF 文档。需要用 PDF 渲染器（Python 端用 PyMuPDF/fitz，Web 端用 pdf.js）将 `bkgAttachmentIndex` 指定的页面渲染为 JPEG 图像作为背景。**不能直接从 PDF 中抽取 JPEG XObject**——那些通常是缩略图或嵌入的小图，并非完整页面渲染结果。
- **图像底（`*_image`）**：文件本身即 JPEG（`FFD8FF`）或 PNG，直接作背景。
- **渲染**：背景作为满页 `ImageElement(x=0,y=0,w=pageW,h=pageH)` 插到 images 首位（先绘制=最底层），页面底色 → 背景图 → 网格(无) → 图片/文本 → 笔迹。
- 实测：`pdf 示例.hinote`（2 页，每页 1 张 2398×3172/3514 JPEG）、`图像示例.hinote`（1 页背景 + 41 笔迹）。

---

## 渲染方案

### 笔画渲染
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