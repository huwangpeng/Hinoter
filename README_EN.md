# Hinoter

[中文](README.md) | English

Tools for reading and writing Huawei Notes `.hinote` files. The browser viewer previews a note and exports it as SVG, PDF, or PNG; conversely, an ordinary PDF can be wrapped into a `.hinote` that Huawei Notes imports directly, with handwritten annotation still available on top. All parsing runs locally — notes are never uploaded. `.hinote` is a private format reconstructed by reverse-engineering samples; the findings are documented in [docs/](docs/). The infinite-canvas format (`PENKITINFENG`) is still being analyzed and its features remain incomplete.

## Web viewer

Open [index.html](index.html) from the repository root and pick a viewer for the note type:

- [web/hinote-viewer.html](web/hinote-viewer.html) — regular notes (`PENCILENGINE`)
- [web/infinite-viewer.html](web/infinite-viewer.html) — infinite canvas (`PENKITINFENG`), experimental

Drop a `.hinote` onto the page. The regular viewer supports page-by-page preview, page selection, and SVG / PDF / PNG export. One exception: pdf.js is loaded from a CDN for PDF backgrounds — everything else is parsed in the browser.

## Command-line export

Requires Python 3.10+. Basic SVG and PDF export has no third-party dependencies:

```powershell
python src/hinote_vector_export.py "笔记.hinote"
```

Results are written to `out/笔记/`: one SVG per page plus a merged PDF, with unsupported content reported in `report.md`. Multiple files can be processed at once:

```powershell
python src/hinote_vector_export.py "笔记一.hinote" "笔记二.hinote" -o export
```

PNG export requires CairoSVG:

```powershell
pip install cairosvg
python src/hinote_vector_export.py "笔记.hinote" --ppi 300 --size a4
```

Two optional dependencies: `fonttools` enables embedding Chinese text in PDFs (usable CJK fonts must also be installed on the system), and `pymupdf` enables restoring imported PDF background pages.

```powershell
pip install fonttools pymupdf
```

## Layers

Huawei Notes supports page layers from `hinote_1.0.6` onward. The tools detect them automatically and record the layer list in `report.md`:

```powershell
# merged export (the default: every visible layer, bottom-up)
python src/hinote_vector_export.py "笔记.hinote"

# also write one SVG per layer into svg/layers/
python src/hinote_vector_export.py "笔记.hinote" --split-layers

# pick layers by name or id
python src/hinote_vector_export.py "笔记.hinote" --only-layer "图层 1"
python src/hinote_vector_export.py "笔记.hinote" --hide-layer 0

# include layers the note itself marked hidden
python src/hinote_vector_export.py "笔记.hinote" --all-layers
```

`--hide-layer` and `--only-layer` both match the layer name (case-insensitive) or its `layerId`, and may be repeated. Layers the note marked hidden are skipped unless `--all-layers` is given. Older notes carry no layer information and behave exactly as before.

Stroke ownership is determined by the 16-byte layer UUID that precedes each stroke's style record; see [HINOTE_FORMAT.md](docs/HINOTE_FORMAT.md) for the byte-level details.

## Tests

```powershell
python -m unittest discover -s tests -v
```

`tests/test_layers.py` builds minimal stroke files in memory to cover layer parsing and filtering; when real samples are present it additionally runs end-to-end checks against them (paths can also be given via `HINOTE_MULTILAYER_SAMPLE` / `HINOTE_COLOR_SAMPLE` / `HINOTE_ZORDER_SAMPLE`). The three checks cover layer assignment, the BGR order of float colour components, and an upper layer painting over a lower one.

## Converting PDFs to .hinote

[src/pdf_to_hinote.py](src/pdf_to_hinote.py) wraps each PDF page into a `.hinote` container unchanged. The PDF is not rasterized: vector graphics, text, and page dimensions are preserved, so pages stay crisp after import and remain annotatable.

```powershell
pip install pypdf
python src/pdf_to_hinote.py "资料.pdf"
```

By default the output is written next to the PDF as `资料.hinote`; both the output path and the note title can be specified:

```powershell
python src/pdf_to_hinote.py "资料.pdf" -o "导入用.hinote" --title "项目资料"
```

The tool stops if the output file already exists; use `--force` to overwrite.

Two caveats: the original PDF content is preserved rather than disguised as editable strokes, so pages cannot be edited as text in Huawei Notes. And since the format was reverse-engineered from samples, compatibility may vary between app versions — import a copy first when trying it out.

## What's covered

The main pipeline for regular notes works end to end: pen and highlighter strokes (including variable width and opacity), text boxes, images, ruled / grid / dot-grid templates, imported PDF or image backgrounds, portrait and landscape pages. Multi-layer notes (`hinote_1.0.6`) are reconstructed correctly too: strokes are grouped by the layer they belong to, and layer names, order, and visibility are read from the note, so you can filter by layer or export layers separately. Plain black-ink notes export essentially losslessly.

The infinite canvas is far less complete. Its strokes use a separate format, `PENKITINFENG`: canvas notebooks can already be identified, the 52-byte BSD file header and 28-byte point records decoded, and coordinates, pressure, timestamps, color, and pen width recovered, with an experimental preview in [src/hinote_infinite.py](src/hinote_infinite.py) and [web/infinite-viewer.html](web/infinite-viewer.html). However, many reserved fields in stroke descriptions remain unexplained, and available samples only cover the black pen — treat the preview as format verification rather than a working exporter. When the main script encounters such data it flags it in `report.md`; details in [docs/无界笔记格式分析.md](docs/无界笔记格式分析.md).

## Layout

```text
hinoter/
├── index.html                     viewer entry point
├── src/
│   ├── hinote_vector_export.py    bounded-note export
│   ├── hinote_infinite.py         infinite-canvas parsing experiments
│   └── pdf_to_hinote.py           PDF → bounded note
├── tests/
│   └── test_layers.py             multi-layer parsing & filtering tests
├── web/
│   ├── hinote-viewer.html         bounded-note viewer
│   └── infinite-viewer.html       infinite-canvas viewer
└── docs/
    ├── HINOTE_FORMAT.md           bounded-note format record
    └── 无界笔记格式分析.md        infinite-canvas format record
```

## License

MIT License — see [LICENSE](LICENSE).

## Thanks

If this project is useful to you, a star would be appreciated.
