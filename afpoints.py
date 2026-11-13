#!/usr/bin/env python3
"""
afpoints: Canon AF-point focus check (local, read-only on originals, proposals only).

Reads the AF area the camera actually used from the CR3 maker notes (exiftool, Canon AFInfo2:
AFAreaMode, ValidAFPoints, AFImageWidth/Height, AFAreaX/YPositions, AFAreaWidths/Heights,
AFPointsInFocus/Selected), maps it onto the oriented preview cached by the tagger, and compares
sharpness inside the AF box with the rest of the frame.

Proposed (additive) keywords:
  General/Focus/On Target  - AF box is the sharpest part of the frame and sharp in absolute terms
  General/Focus/Missed     - AF box soft while other parts of the frame are clearly sharp (front/back focus,
                             or AF locked on the wrong thing). Written as needs_review: only applied after a
                             human confirms it in the panel's review screen.
Frames with no AF data, or where everything is soft (motion blur / low light), get no focus keyword:
the existing General/Quality/* flags cover those.

Usage:
  venv/bin/python afpoints.py <folder> [--annotate N] [--sample N] [--refresh-exif]
Outputs (in ~/localai/out/<folder>/): afpoints-exif.json (cache), afpoints.csv, afpoints-proposals.json
Never writes to originals or the catalog.
"""
import argparse, os, csv, json, math, os, subprocess, sys, time
from collections import Counter
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
OUT = HOME / "out"
EXIFTOOL = str(HOME / "bin" / "exiftool")
TAGS = ["FileName", "Orientation", "Model", "FocusMode", "AFAreaMode", "NumAFPoints", "ValidAFPoints",
        "AFImageWidth", "AFImageHeight", "AFAreaWidths", "AFAreaHeights", "AFAreaXPositions", "AFAreaYPositions",
        "AFPointsInFocus", "AFPointsSelected", "SubjectToDetect", "EyeDetection",
        "FocusDistanceUpper", "FocusDistanceLower", "AspectRatio", "CroppedImageWidth", "CroppedImageHeight",
        "CroppedImageLeft", "CroppedImageTop", "FocalLength", "ExposureTime"]
DEFAULTS = {
    # calibrated on 2026-11-07 Brands Hatch (2,332 R6 frames): keeps 32/34 of the 3-5 star frames from that calibration set
    "on_target_ratio": 1.0,      # AF-box sharpness / sharpest region elsewhere in the frame
    "on_target_pct": 0.0,        # optional folder-percentile floor (off: absolute floor is more consistent)
    "on_target_abs_min": 300.0,  # Laplacian p90 floor on the 2400px preview (same as sharpness Sharp floor)
    "missed_ratio": 0.45,        # AF box at most this fraction of the sharpest other region...
    "missed_other_abs_min": 400.0,  # ...and that other region must be genuinely sharp
    "suppress_on_target_if": ["Motion Blur", "Soft Focus"],  # don't contradict the sharpness pass
    "workers": 6,
}
RAW_EXT = {".cr3", ".cr2"}

def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(HOME / "config.json")).get("features", {}).get("afpoints", {}))
    except Exception:
        pass
    return c

# ------------------------------------------------------------------ metadata
def _ints(v):
    if v is None: return []
    if isinstance(v, (int, float)): return [int(v)]
    return [int(x) for x in str(v).replace(",", " ").split() if x.lstrip("-").isdigit()]

def _bits(v):
    """exiftool -n gives AFPointsInFocus/Selected as raw 16-bit words; bit k of word j = AF point 16*j+k"""
    out = set()
    for j, w in enumerate(_ints(v)):
        w &= 0xFFFF
        for k in range(16):
            if w >> k & 1: out.add(16 * j + k)
    return out

def read_exif(files, cache_path=None, workers=6, refresh=False):
    """exiftool -n on originals (read-only), parallel batches; cached as JSON keyed by filename."""
    cache = {}
    if cache_path and Path(cache_path).exists() and not refresh:
        cache = {r["FileName"]: r for r in json.load(open(cache_path))}
    todo = [str(f) for f in files if Path(f).name not in cache]
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        batches = [todo[i:i + 40] for i in range(0, len(todo), 40)]
        def run(b):
            p = subprocess.run([EXIFTOOL, "-j", "-n", "-fast"] + [f"-{t}" for t in TAGS] + b,
                               capture_output=True, text=True)
            return json.loads(p.stdout or "[]")
        with ThreadPoolExecutor(workers) as ex:
            for i, recs in enumerate(ex.map(run, batches)):
                for r in recs: cache[r["FileName"]] = r
                print(f"[exif {min(len(todo), (i + 1) * 40)}/{len(todo)}]", flush=True)
        if cache_path:
            json.dump(list(cache.values()), open(cache_path, "w"))
    return cache

def af_boxes(r):
    """-> list of dict(box=(x0,y0,x1,y1) normalised, top-left origin, DISPLAY orientation, in_focus, selected).
    Canon AFInfo2 positions are relative to the AF image centre, +Y up, in AFImageWidth x AFImageHeight units
    (sensor/landscape orientation)."""
    W, H = r.get("AFImageWidth"), r.get("AFImageHeight")
    n = r.get("ValidAFPoints") or 0
    if not W or not H or not n: return []
    xs, ys = _ints(r.get("AFAreaXPositions")), _ints(r.get("AFAreaYPositions"))
    ws, hs = _ints(r.get("AFAreaWidths")), _ints(r.get("AFAreaHeights"))
    inf, sel = _bits(r.get("AFPointsInFocus")), _bits(r.get("AFPointsSelected"))
    o = int(r.get("Orientation") or 1)
    out = []
    for i in range(min(n, len(xs), len(ys), len(ws), len(hs))):
        if ws[i] <= 0 or hs[i] <= 0: continue
        cx, cy = W / 2 + xs[i], H / 2 - ys[i]
        x0, x1 = (cx - ws[i] / 2) / W, (cx + ws[i] / 2) / W
        y0, y1 = (cy - hs[i] / 2) / H, (cy + hs[i] / 2) / H
        if o == 6:   x0, y0, x1, y1 = 1 - y1, x0, 1 - y0, x1      # displayed rotated 90 CW
        elif o == 8: x0, y0, x1, y1 = y0, 1 - x1, y1, 1 - x0      # displayed rotated 90 CCW
        elif o == 3: x0, y0, x1, y1 = 1 - x1, 1 - y1, 1 - x0, 1 - y0
        clamp = lambda v: max(0.0, min(1.0, v))
        out.append(dict(box=(clamp(x0), clamp(y0), clamp(x1), clamp(y1)), in_focus=i in inf, selected=i in sel))
    return out

def primary_box(boxes):
    """union of in-focus points, else selected, else all valid points"""
    use = [b for b in boxes if b["in_focus"]]
    if not use and len(boxes) == 1: use = boxes          # single tracking/zone box
    if not use: return None                              # multi-point grid with nothing in focus = no lock
    x0, y0 = min(b["box"][0] for b in use), min(b["box"][1] for b in use)
    x1, y1 = max(b["box"][2] for b in use), max(b["box"][3] for b in use)
    m = 0.05                                             # pad tiny single points so there is texture to measure
    if x1 - x0 < m: c = (x0 + x1) / 2; x0, x1 = max(0, c - m / 2), min(1, c + m / 2)
    if y1 - y0 < m: c = (y0 + y1) / 2; y0, y1 = max(0, c - m / 2), min(1, c + m / 2)
    return (x0, y0, x1, y1)

def af_state(r, boxes):
    if not boxes: return "no AF data"
    if len(boxes) == 1: return "tracking box" if r.get("AFAreaMode") == 6 else "single area"
    return f"grid {sum(b['in_focus'] for b in boxes)}/{len(boxes)} in focus" if any(b["in_focus"] for b in boxes) else "grid, no lock"

# ------------------------------------------------------------------ sharpness
def tile_lap(g, x0, y0, x1, y1, tiles=6):
    """90th percentile of Laplacian variance over a tile grid (same idea as features.sharpness_metrics)"""
    import cv2, numpy as np
    crop = g[max(0, y0):max(y0 + 8, y1), max(0, x0):max(x0 + 8, x1)]
    th, tw = max(8, crop.shape[0] // tiles), max(8, crop.shape[1] // tiles)
    v = [cv2.Laplacian(crop[y:y + th, x:x + tw], cv2.CV_32F).var()
         for y in range(0, crop.shape[0] - th + 1, th) for x in range(0, crop.shape[1] - tw + 1, tw)]
    return float(np.percentile(v, 90)) if v else float(cv2.Laplacian(crop, cv2.CV_32F).var())

def measure(im, box):
    """-> af_sharp (inside AF box), other_sharp (sharpest region outside the box, same tile size)"""
    import cv2, numpy as np
    g = np.asarray(im.convert("L"), dtype=np.float32)
    H, W = g.shape
    x0, y0, x1, y1 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
    af = tile_lap(g, x0, y0, x1, y1)
    # tiles the size of one AF-box tile across the whole frame, excluding ones overlapping the box
    th, tw = max(16, (y1 - y0) // 6), max(16, (x1 - x0) // 6)
    th, tw = max(th, H // 24), max(tw, W // 24)
    vals = []
    for y in range(0, H - th + 1, th):
        for x in range(0, W - tw + 1, tw):
            if x < x1 and x + tw > x0 and y < y1 and y + th > y0: continue
            vals.append(cv2.Laplacian(g[y:y + th, x:x + tw], cv2.CV_32F).var())
    other = float(np.percentile(vals, 98)) if vals else 0.0
    return af, other

def preview_for(path, sha):
    """oriented preview: tagger cache (2400px) if present, else embedded preview via tagger.load_preview"""
    from PIL import Image
    c = HOME / "cache" / f"{sha[:24]}.jpg" if sha else None
    if c and c.exists(): return Image.open(c).convert("RGB")
    sys.path.insert(0, str(HOME)); import tagger as T
    return T.load_preview(Path(path), sha or T.sha256_file(path), 2400)[0]

# ------------------------------------------------------------------ main pass
def index_rows(folder_name):
    import sqlite3
    c = sqlite3.connect(HOME / "index" / "catalog.sqlite")
    return {r[1]: dict(path=r[0], sha256=r[2]) for r in
            c.execute("SELECT path, filename, sha256 FROM images WHERE folder=?", (folder_name,))}

def classify(rows, C, sharp_flags=None):
    import numpy as np
    vals = [r["af_sharp"] for r in rows if r.get("af_sharp") is not None]
    order = np.argsort(np.argsort(np.log1p(vals))) / max(1, len(vals) - 1) if vals else []
    it = iter(order)
    for r in rows:
        r["focus"] = None
        if r.get("af_sharp") is None: continue
        r["af_pct"] = round(float(next(it)), 3)
        ratio = r["af_sharp"] / max(1.0, r["other_sharp"]); r["ratio"] = round(ratio, 3)
        if ratio >= C["on_target_ratio"] and r["af_pct"] >= C["on_target_pct"] and r["af_sharp"] >= C["on_target_abs_min"]:
            if (sharp_flags or {}).get(r["filename"]) not in C["suppress_on_target_if"]: r["focus"] = "On Target"
        elif ratio <= C["missed_ratio"] and r["other_sharp"] >= C["missed_other_abs_min"]:
            r["focus"] = "Missed"
    return rows

def run_folder(folder, annotate=0, sample=0, refresh=False, quiet=False):
    if not annotate:
        try: annotate = int(os.environ.get("LOCALAI_AFPOINTS_ANNOTATE") or 0)
        except ValueError: annotate = 0
    folder = Path(folder); fo = folder.name; d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
    C = cfg(); idx = index_rows(fo)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW_EXT and not p.name.startswith(".")
                   and not p.name.lower().startswith("_schedule"))
    if sample: files = files[::max(1, len(files) // sample)][:sample]
    ex = read_exif(files, d / "afpoints-exif.json", C["workers"], refresh)
    rows = []
    for i, p in enumerate(files):
        r = ex.get(p.name, {}); ir = idx.get(p.name, {})
        boxes = af_boxes(r); box = primary_box(boxes)
        row = dict(filename=p.name, sha256=ir.get("sha256"), af_mode=r.get("AFAreaMode"), valid_points=r.get("ValidAFPoints"),
                   orientation=r.get("Orientation"), n_boxes=len(boxes), af_state=af_state(r, boxes),
                   box=[round(v, 4) for v in box] if box else None, af_sharp=None, other_sharp=None)
        if box:
            try:
                im = preview_for(p, ir.get("sha256"))
                a, o = measure(im, box); row["af_sharp"], row["other_sharp"] = round(a, 1), round(o, 1)
            except Exception as e:
                row["error"] = str(e)[:120]
        rows.append(row)
        if not quiet and (i + 1) % 100 == 0: print(f"[{i + 1}/{len(files)}] {p.name}", flush=True)
    sf = {}
    if (d / "sharpness.csv").exists():
        sf = {x["filename"]: x["flag"] for x in csv.DictReader(open(d / "sharpness.csv"))}
    classify(rows, C, sf)
    with open(d / "afpoints.csv", "w", newline="") as fh:
        keys = ["filename", "focus", "af_sharp", "other_sharp", "ratio", "af_pct", "af_mode", "af_state", "valid_points", "n_boxes", "orientation", "box", "sha256"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    # On Target is applied like any other confident proposal. Missed is never auto-applied: it is marked
    # needs_review so it lands in the panel's review screen (Review -> Missed focus) for a human decision.
    recs = []
    for r in rows:
        rec = dict(filename=r["filename"], sha256=r["sha256"], needs_review=False, proposed_keywords=[])
        if r["focus"] == "On Target":
            rec["proposed_keywords"] = ["General/Focus/On Target"]
        elif r["focus"] == "Missed":
            rec.update(needs_review=True, proposed_keywords=["General/Focus/Missed"], af_box=r["box"],
                       af_sharp=r["af_sharp"], other_sharp=r["other_sharp"], ratio=r.get("ratio"),
                       review_reasons=[f"AF box soft ({r['af_sharp']:.0f}) while elsewhere is sharp ({r['other_sharp']:.0f}), "
                                       f"ratio {r.get('ratio')}: front/back focus or AF on the wrong thing?"])
        recs.append(rec)
    pf = d / "afpoints-proposals.json"
    json.dump({"folder": str(folder), "profile": "afpoints", "images": recs}, open(pf, "w"), indent=1)
    dist = dict(Counter(r["focus"] or ("no usable AF box" if not r["box"] else "no tag") for r in rows))
    print(fo, dist, "->", d / "afpoints.csv", pf, flush=True)
    if annotate: annotate_samples(rows, folder, annotate)
    return rows, dist

def annotate_samples(rows, folder, n, outdir=OUT / "afcheck"):
    """draw AF box on previews: green=On Target, red=Missed, yellow=no tag"""
    from PIL import ImageDraw
    outdir.mkdir(parents=True, exist_ok=True)
    pick = []
    for tag, k in (("On Target", n // 3), ("Missed", n // 3), (None, n - 2 * (n // 3))):
        g = [r for r in rows if r["box"] and r["focus"] == tag and r.get("af_sharp") is not None]
        pick += g[::max(1, len(g) // max(1, k))][:k]
    for r in pick: draw(r, folder, outdir)
    print("annotated:", len(pick), "->", outdir, flush=True)

def draw(r, folder, outdir=OUT / "afcheck"):
    """save a preview with the AF box drawn: green=On Target, red=Missed, yellow=no focus tag"""
    from PIL import ImageDraw
    Path(outdir).mkdir(parents=True, exist_ok=True)
    im = preview_for(Path(folder) / r["filename"], r["sha256"]); W, H = im.size
    x0, y0, x1, y1 = r["box"]; col = {"On Target": (0, 255, 0), "Missed": (255, 40, 40)}.get(r["focus"], (255, 220, 0))
    dr = ImageDraw.Draw(im)
    for t in range(6): dr.rectangle([x0 * W - t, y0 * H - t, x1 * W + t, y1 * H + t], outline=col)
    label = (f"{r['filename']}  {r['focus'] or 'no focus tag'}  AF box={float(r['af_sharp']):.0f}  "
             f"elsewhere={float(r['other_sharp']):.0f}  ratio={r['ratio']}  [{r.get('af_state', '')}]")
    dr.rectangle([0, 0, W, 46], fill=(0, 0, 0))
    if _pil_fs(): dr.text((10, 8), label, fill=col, font_size=30)
    else: dr.text((10, 12), label, fill=col)
    im.thumbnail((1600, 1600))
    f = Path(outdir) / f"{Path(r['filename']).stem}_{(r['focus'] or 'none').replace(' ', '')}.jpg"
    im.save(f, quality=85); return f

def _pil_fs():
    import PIL; return tuple(int(x) for x in PIL.__version__.split(".")[:2]) >= (10, 1)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder"); ap.add_argument("--annotate", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0); ap.add_argument("--refresh-exif", action="store_true")
    a = ap.parse_args()
    run_folder(a.folder, a.annotate, a.sample, a.refresh_exif)

if __name__ == "__main__":
    main()
