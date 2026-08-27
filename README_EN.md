# Hinoter

[中文](README.md) | English

Hinoter reads and generates Huawei Notes `.hinote` files. Bounded notes can be previewed and exported as SVG, PDF, or PNG, and PDFs can be wrapped into importable notes. The infinite canvas has an experimental preview, and its format is still being analyzed.

No note files ever leave your machine: the web viewers process everything in the browser, and the Python scripts are meant for batch export.

## Quick start

Open `index.html` from the repository root and pick a viewer:

- [`web/hinote-viewer.html`](web/hinote-viewer.html) — bounded notes (`PENCILENGINE`)
- [`web/infinite-viewer.html`](web/infinite-viewer.html) — infinite canvas (`PENKITINFENG`, experimental)

Drop a `.hinote` file onto the page. The regular viewer supports page-by-page preview, page selection, and SVG, PDF, and PNG export. When you import a PDF as a page background, pdf.js is loaded from a CDN; everything else is parsed locally.

## Python export

Python 3.10 or newer is required. Basic SVG and PDF export only uses the standard library:

```powershell
python src/hinote_vector_export.py "笔记.hinote"
```

By default the result lands in `out/<笔记名>/`:

```text
out/笔记/
├── svg/             one SVG per page
├── 笔记.pdf         merged PDF
└── report.md        export summary and unsupported items
```

You can process several files at once and choose an output directory:

```powershell
python src/hinote_vector_export.py "笔记一.hinote" "笔记二.hinote" -o export
```

PNG export needs CairoSVG:

```powershell
pip install cairosvg
python src/hinote_vector_export.py "笔记.hinote" --ppi 300 --size a4
```

Optional dependencies:

- `fonttools` — embed Chinese text in PDFs; usable Chinese fonts must also be installed on the system
- `pymupdf` — restore imported PDF pages as background images

```powershell
pip install fonttools pymupdf
```

## Convert PDFs into .hinote

[`pdf_to_hinote.py`](src/pdf_to_hinote.py) wraps an original PDF into a bounded note, with each PDF page becoming one note page. The PDF is not rasterized — vector graphics, text, and page sizes are preserved — so after importing into Huawei Notes you can keep annotating by hand.

Install the dependency:

```powershell
pip install pypdf
```

Convert a single file:

```powershell
python src/pdf_to_hinote.py "资料.pdf"
```

This creates `资料.hinote` next to the PDF. You can also pick an output file and a note title:

```powershell
python src/pdf_to_hinote.py "资料.pdf" -o "导入用.hinote" --title "项目资料"
```

The tool stops when the output file already exists; pass `--force` to overwrite.

Only the original PDF content is kept — the PDF path is never disguised as editable handwriting. The `.hinote` format was reverse-engineered from samples, so different versions of Huawei Notes may behave differently. It's best to import a copy first and check the result before relying on it.

## What works today

The main pipeline for bounded notes is ready, including:

- Pen and highlighter strokes that preserve variable width and opacity
- Text boxes, images, and page backgrounds
- Common templates such as ruled, grid, dot grid, and blank paper
- Imported PDF or image backgrounds
- Landscape and portrait pages
- SVG and PDF output, plus optional PNG

`.hinote` is a ZIP container where page metadata is GZIP-compressed JSON; strokes use Huawei's binary format. Confirmed fields and parsing methods are recorded in [`docs/HINOTE_FORMAT.md`](docs/HINOTE_FORMAT.md).

## Infinite canvas status

The infinite canvas uses a separate stroke format called `PENKITINFENG`. So far we can:

- Recognize infinite-canvas notebooks and their `bsd_X_Y_*` blocks
- Read the 52-byte BSD file header, stroke descriptions, and 28-byte point records in order
- Extract global coordinates, pressure, time, color, pen width, and opacity
- Read the 2500-unit grid in GSD and its mapping to BSD block IDs
- Tile the blocks in [`web/infinite-viewer.html`](web/infinite-viewer.html) for an experimental preview

These issues are still open:

- The meaning of some reserved fields in stroke descriptions and point records
- GED element data and the remaining fields of the GSD header
- Controlled sample validation for colored pens, highlighters, and varying widths
- Hooking the infinite canvas into the SVG and PDF export pipeline

Coordinates inside BSD are already global canvas coordinates; the grid only records where each stroke starts, so strokes crossing cells need no stitching or re-offsetting. Since only black-pen samples exist, the preview is best treated as format verification rather than a full export. When the Python script encounters such data it flags it in `report.md`. Details live in [`docs/无界笔记格式分析.md`](docs/无界笔记格式分析.md), with code in [`src/hinote_infinite.py`](src/hinote_infinite.py).

## Layout

```text
hinoter/
├── index.html                     viewer entry point
├── src/
│   ├── hinote_vector_export.py    bounded-note export
│   ├── hinote_infinite.py         infinite-canvas parsing experiments
│   └── pdf_to_hinote.py           PDF → bounded note
├── tests/
│   └── test_pdf_to_hinote.py      conversion & container checks
├── web/
│   ├── hinote-viewer.html         bounded-note viewer
│   └── infinite-viewer.html       infinite-canvas viewer
└── docs/
    ├── HINOTE_FORMAT.md           bounded-note format record
    └── 无界笔记格式分析.md        infinite-canvas format record
```

## License

Released under the MIT License — see [`LICENSE`](LICENSE).

## Thanks

If this project helps you, please consider giving it a star.
