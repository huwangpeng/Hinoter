import zipfile, re
from pathlib import Path
import fitz
from hinote_vector_export import decode_jhinote

z = zipfile.ZipFile("页面样例.hinote")
files = {Path(i.filename).name: z.read(i) for i in z.infolist() if not i.is_dir()}
SCALE = 763 / 1000.0

pages = []
for info in z.infolist():
    if info.filename.startswith("pages/") and info.filename.endswith(".jhinote"):
        c = decode_jhinote(files[Path(info.filename).name])["customNotePageContent"]
        d1 = c.get("data1", "")
        pngs = re.findall(r"([0-9a-f]+\.png)", d1)
        pages.append((c["pageNumber"], c.get("background", ""), pngs[0] if pngs else None))


def to_gray(png_bytes):
    pix = fitz.Pixmap(png_bytes)
    w, h, n = pix.width, pix.height, pix.n
    s = pix.samples
    gray = bytearray(w * h)
    for i in range(w * h):
        gray[i] = (s[i * n] + s[i * n + 1] + s[i * n + 2]) // 3
    return w, h, gray


def profile_lines(gray, w, h, axis, sample):
    """axis=0: scan column x=sample for H-lines; axis=1: scan row y=sample for V-lines."""
    bg_val = 255
    vals = []
    if axis == 0:
        vals = [gray[y * w + sample] for y in range(h)]
        N = h
    else:
        vals = [gray[sample * w + x] for x in range(w)]
        N = w
    dips = []
    for i in range(2, N - 2):
        if vals[i] < bg_val - 8 and vals[i] <= vals[i - 1] and vals[i] <= vals[i + 1]:
            dips.append(i)
    clusters = []
    for p in dips:
        if clusters and p - clusters[-1][-1] <= 4:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [sum(c) / len(c) for c in clusters]


import statistics

for pn, bg, png_name in sorted(pages):
    if not png_name:
        continue
    w, h, gray = to_gray(files[png_name])
    hlines = profile_lines(gray, w, h, 0, 400)  # H-lines via col x=400
    vlines = profile_lines(gray, w, h, 1, 500)  # V-lines via row y=500
    hy = [round(y / SCALE, 1) for y in hlines]
    vx = [round(x / SCALE, 1) for x in vlines]
    hsp = [round(hlines[i + 1] - hlines[i], 1) for i in range(len(hlines) - 1)]
    vsp = [round(vlines[i + 1] - vlines[i], 1) for i in range(len(vlines) - 1)]
    hmed = round(statistics.median(hsp) / SCALE, 2) if hsp else None
    vmed = round(statistics.median(vsp) / SCALE, 2) if vsp else None
    print(f"page {pn} bg={bg}:")
    print(f"  H: first={hy[0] if hy else None} median_spacing={hmed} count={len(hy)}")
    print(f"  V: first={vx[0] if vx else None} median_spacing={vmed} count={len(vx)}")
    if bg == "base6":
        # detect dots: scan for small dark clusters
        dots = []
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if gray[y * w + x] < 180:
                    dots.append((x / SCALE, y / SCALE))
        # filter: only dots not in top-left marks (x>40 or y>40)
        clean = [(round(x, 1), round(y, 1)) for x, y in dots if x > 40 or y > 40]
        clean.sort()
        print(f"  dots(marked area excluded) first10={clean[:10]}")
