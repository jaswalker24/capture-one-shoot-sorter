#!/usr/bin/env python3
"""
camera_info: EXIF / Canon MakerNotes + a cheap preview clip check (local, proposals only).

Reads CR2/CR3 files with the bundled exiftool (read-only). Ignores MP4s and JPEG companions.
No caption model. Keywords are additive proposals under General/Camera/*, plus
General/Orientation/* and General/Quality/Clipped Highlights|Crushed Shadows.

White balance, Picture Style and Highlight Tone Priority are stored as metadata
in the proposals file, not as keywords (keeps the keyword count sane).
Sub-second capture time and body serial are also stored, not keyworded, for
burst / schedule use.

Usage:
  venv/bin/python camera_info.py <folder> [--sample N] [--refresh-exif] [--no-clip]
Outputs (in ~/localai/out/<folder>/): camera-exif.json, camera.csv, camera-proposals.json
Never writes to originals or the catalog.
"""
from __future__ import annotations

import argparse, csv, json, math, os, re, subprocess, sys, time
from collections import Counter
from pathlib import Path

HOME = Path.home() / "localai"
OUT = HOME / "out"
EXIFTOOL = str(HOME / "bin" / "exiftool")
RAW_EXT = {".cr3", ".cr2"}


def is_schedule_file(p) -> bool:
    """Event schedule posters (_schedule*) are skipped by every pass."""
    return Path(str(p)).name.lower().startswith("_schedule")
PREFIX = "General/Camera/"

# Tags we have verified on Canon EOS R6 CR3 files (2026-06-23 and Brands Hatch).
# Use decoded values (no -n) so enums come out as text. LensType is recorded but
# unreliable on Sigma zooms — LensModel is the source of truth.
TAGS = [
    "FileName", "Model", "SerialNumber", "LensModel", "LensType",
    "FocalLength", "MaxAperture", "FNumber", "ExposureTime", "ISO",
    "ExposureCompensation", "CanonExposureMode",
    "ContinuousDrive", "ShutterMode",
    "FocusMode", "AFAreaMode", "SubjectToDetect", "EyeDetection",
    "ImageStabilization", "Orientation", "Flash",
    "WhiteBalance", "PictureStyle", "HighlightTonePriority",
    "SubSecTimeOriginal", "DateTimeOriginal", "OffsetTimeOriginal",
    "SubSecDateTimeOriginal", "GPSLatitude",
]

DEFAULTS = {
    "high_iso": 6400,
    "very_high_iso": 12800,
    "wide_open_stops": 1.0 / 3.0,       # within 1/3 stop of the lens max
    "slow_shutter_is_stops": 2.0,       # extra stops allowed when IS is on (handshake rule)
    "slow_shutter_abs": None,           # optional absolute floor in seconds, e.g. 1/60
    "ec_min": 2.0 / 3.0,                # |exposure comp| at or above this is "notable"
    "focal_wide": 35.0,                 # < this: Wide
    "focal_normal": 70.0,               # < this: Normal; < focal_tele: Tele; else Super Tele
    "focal_tele": 300.0,
    "highlight_level": 253,
    "highlight_frac": 0.008,            # all-channel blown fraction (conservative)
    "shadow_level": 2,
    "shadow_frac": 0.35,                # all-channel crushed fraction (high: gig stages are dark)
    "clip_preview_px": 800,
    "workers": 8,
}

# (regex, short name without slashes, max aperture or None if variable / use EXIF)
LENS_MAP = [
    (re.compile(r"(EF|RF)\s*50mm\s*f/?1\.2", re.I), "Canon 50mm f1.2", 1.2),
    (re.compile(r"(EF|RF)\s*85mm\s*f/?1\.4", re.I), "Canon 85mm f1.4", 1.4),
    (re.compile(r"(EF|RF)\s*35mm\s*f/?2(?:\b|IS|USM|L)", re.I), "Canon 35mm f2", 2.0),
    (re.compile(r"(EF|RF)\s*135mm\s*f/?2(?:\b|L|USM)", re.I), "Canon 135mm f2", 2.0),
    (re.compile(r"150\s*-\s*600", re.I), "Sigma 150-600", None),
    (re.compile(r"(EF|RF)\s*70\s*-\s*200mm\s*f/?4", re.I), "Canon 70-200 f4", 4.0),
]


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(HOME / "config.json")).get("features", {}).get("camera", {}))
    except Exception:
        pass
    return c


# ------------------------------------------------------------------ parsers (pure, unit-tested)

def as_float(v):
    """Parse exiftool numbers, '1/800', '50.0 mm', or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    s = re.sub(r"\s*(mm|sec|s|ev)\s*$", "", s, flags=re.I).strip()
    if s.startswith("+"):
        s = s[1:]
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def short_lens(model: str | None):
    """-> (short name without slashes, known max aperture or None)."""
    s = (model or "").strip()
    if not s:
        return None, None
    for rx, name, mx in LENS_MAP:
        if rx.search(s):
            return name, mx
    # Fallback: drop Sigma-style suffixes and any slash that would break the keyword path
    s = re.sub(r"\s*\|\s*.*$", "", s)
    s = re.sub(r"\s+", " ", s).replace("/", "").strip()
    return (s or None), None


def focal_band(focal, C=None):
    C = C or DEFAULTS
    if focal is None:
        return None
    if focal < C["focal_wide"]:
        return "Wide"
    if focal < C["focal_normal"]:
        return "Normal"
    if focal < C["focal_tele"]:
        return "Tele"
    return "Super Tele"


def is_wide_open(fnumber, max_aperture, C=None):
    C = C or DEFAULTS
    if not fnumber or not max_aperture or fnumber <= 0 or max_aperture <= 0:
        return False
    stops = 2.0 * math.log2(fnumber / max_aperture)
    return stops <= float(C["wide_open_stops"]) + 1e-6


def is_slow_shutter(exposure, focal, is_on, C=None):
    """Handshake-style: slower than 1/focal, with IS allowing N extra stops.
    Optional absolute floor (slow_shutter_abs, seconds) also counts.
    Intentionally does not flag typical panning speeds on a long lens with IS on.
    """
    C = C or DEFAULTS
    if exposure is None or exposure <= 0:
        return False
    abs_floor = C.get("slow_shutter_abs")
    if abs_floor and exposure >= float(abs_floor) - 1e-12:
        return True
    if not focal or focal <= 0:
        return False
    extra = float(C.get("slow_shutter_is_stops") or 0) if is_on else 0.0
    # 1 / (focal / 2^extra) = 2^extra / focal
    limit = (2.0 ** extra) / focal
    return exposure >= limit - 1e-12


def iso_flag(iso, C=None):
    C = C or DEFAULTS
    if iso is None:
        return None
    if iso >= C["very_high_iso"]:
        return "Very High ISO"
    if iso >= C["high_iso"]:
        return "High ISO"
    return None


def notable_ec(ec, C=None):
    C = C or DEFAULTS
    if ec is None:
        return None
    if ec >= float(C["ec_min"]) - 1e-9:
        return "Plus"
    if ec <= -float(C["ec_min"]) + 1e-9:
        return "Minus"
    return None


def is_on_off(v):
    s = str(v or "").strip().lower()
    if not s or s in ("n/a", "none", "unknown"):
        return None
    if s.startswith("off") or s.startswith("disable"):
        return False
    if s.startswith("on") or "stabiliz" in s:
        return True
    return None


def flash_fired(v):
    s = str(v or "").strip().lower()
    if not s or s in ("no flash", "off", "none", "n/a"):
        return False
    if "did not fire" in s or "not fired" in s or "no flash" in s:
        return False
    if "fired" in s or "strobe" in s or s.startswith("on"):
        return True
    return False


def electronic_shutter(v):
    """True only for full electronic / silent shutter, not EFCS or mechanical."""
    s = str(v or "").strip().lower()
    if not s:
        return False
    if "first curtain" in s or "first-curtain" in s or "efcs" in s or "1st" in s:
        return False
    if s in ("electronic", "electronic shutter", "silent", "electronic (silent)", "silent electronic"):
        return True
    return "electronic" in s and "curtain" not in s and "mechanical" not in s


def high_speed_drive(v):
    s = str(v or "").strip().lower()
    if not s:
        return False
    if "high+" in s or "high +" in s:
        return True
    if "high speed" in s or "continuous, high" in s or "continuous high" in s:
        return True
    return False


def af_mode(v):
    s = str(v or "").strip().lower()
    if not s or s in ("n/a", "none"):
        return None
    if "manual" in s and "servo" not in s and "one-shot" not in s and "one shot" not in s:
        return "Manual"
    if "servo" in s:
        return "Servo"
    if "one-shot" in s or "one shot" in s or "oneshot" in s:
        return "One-Shot"
    return None


def af_area(v):
    s = re.sub(r"\s+", " ", str(v or "").strip())
    if not s or s.lower() in ("n/a", "none"):
        return None
    key = s.lower()
    if "face" in key and "track" in key:
        return "Face + Tracking"
    if "spot" in key:
        return "Spot"
    if "1-point" in key or "1 point" in key or "single-point" in key or "single point" in key:
        return "1-Point"
    if "expand" in key:
        return "Expand"
    if "flexible zone" in key:
        return "Flexible Zone"
    if "large zone" in key:
        return "Large Zone"
    if "zone" in key:
        return "Zone"
    # last resort: keep a slash-free label so it can still be proposed
    return s.replace("/", " ").replace("|", " ").strip() or None


def subject_detect(v):
    s = str(v or "").strip()
    if not s or s.lower() in ("n/a", "none", "off", "disable", "disabled"):
        return None
    low = s.lower()
    if low in ("people", "humans", "person", "human"):
        return "People"
    if low in ("animals", "animal"):
        return "Animals"
    if low in ("vehicles", "vehicle", "cars"):
        return "Vehicles"
    return None


def eye_on(v):
    s = str(v or "").strip().lower()
    if s in ("auto", "enable", "enabled", "on"):
        return True
    return False


def orientation_flag(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        if n in (6, 8):
            return "Portrait"
        if n in (1, 2, 3, 4):
            return "Landscape"
        return None
    s = str(v).strip().lower()
    if "90" in s or "270" in s or "portrait" in s:
        return "Portrait"
    if "horizontal" in s or "landscape" in s or "180" in s or s in ("1", "3"):
        return "Landscape"
    return None


def keywords_for(meta: dict, C=None) -> list[str]:
    """Build the additive keyword list from a normalised metadata dict."""
    C = C or DEFAULTS
    kws = []
    lens = meta.get("lens_short")
    if lens:
        kws.append(f"{PREFIX}Lens/{lens}")
    band = meta.get("focal_band")
    if band:
        kws.append(f"{PREFIX}Focal/{band}")
    iso_k = iso_flag(meta.get("iso"), C)
    if iso_k:
        kws.append(PREFIX + iso_k)
    if meta.get("wide_open"):
        kws.append(PREFIX + "Wide Open")
    if meta.get("slow_shutter"):
        kws.append(PREFIX + "Slow Shutter")
    ec = notable_ec(meta.get("ec"), C)
    if ec:
        kws.append(f"{PREFIX}Exposure Comp/{ec}")
    if meta.get("high_speed"):
        kws.append(f"{PREFIX}Drive/High Speed")
    if meta.get("electronic"):
        kws.append(f"{PREFIX}Shutter/Electronic")
    af = meta.get("af")
    if af:
        kws.append(f"{PREFIX}AF/{af}")
    area = meta.get("af_area")
    if area:
        kws.append(f"{PREFIX}AF Area/{area}")
    subj = meta.get("subject")
    if subj:
        kws.append(f"{PREFIX}Subject/{subj}")
    if meta.get("eye"):
        kws.append(PREFIX + "Eye Detection")
    iso_state = meta.get("is_on")
    if iso_state is True:
        kws.append(f"{PREFIX}IS/On")
    elif iso_state is False:
        kws.append(f"{PREFIX}IS/Off")
    if meta.get("flash"):
        kws.append(PREFIX + "Flash Fired")
    ori = meta.get("orientation")
    if ori:
        kws.append(f"General/Orientation/{ori}")
    if meta.get("clipped_highlights"):
        kws.append("General/Quality/Clipped Highlights")
    if meta.get("crushed_shadows"):
        kws.append("General/Quality/Crushed Shadows")
    return kws


def normalize(r: dict, C=None) -> dict:
    """Map one exiftool record to a flat, typed dict used for keywords + output."""
    C = C or DEFAULTS
    lens_model = r.get("LensModel") or None
    short, known_max = short_lens(lens_model)
    fnumber = as_float(r.get("FNumber"))
    max_ap = known_max if known_max is not None else as_float(r.get("MaxAperture"))
    focal = as_float(r.get("FocalLength"))
    exposure = as_float(r.get("ExposureTime"))
    iso = as_float(r.get("ISO"))
    is_state = is_on_off(r.get("ImageStabilization"))
    # Sub-second timestamp: prefer the composite, else stitch DateTimeOriginal + SubSec + offset
    cap = r.get("SubSecDateTimeOriginal") or None
    if not cap and r.get("DateTimeOriginal"):
        cap = str(r["DateTimeOriginal"]).replace(":", "-", 2).replace(" ", "T")
        if r.get("SubSecTimeOriginal") is not None:
            cap += f".{str(r['SubSecTimeOriginal']).zfill(2)}"
        if r.get("OffsetTimeOriginal"):
            cap += str(r["OffsetTimeOriginal"])
    elif cap:
        cap = str(cap).replace(":", "-", 2).replace(" ", "T")
    serial = r.get("SerialNumber")
    if serial is not None:
        serial = str(serial)
    meta = dict(
        model=r.get("Model"),
        serial=serial,
        lens_model=lens_model,
        lens_type=r.get("LensType"),
        lens_short=short,
        focal=focal,
        focal_band=focal_band(focal, C),
        fnumber=fnumber,
        max_aperture=max_ap,
        wide_open=is_wide_open(fnumber, max_ap, C),
        exposure=exposure,
        iso=int(round(iso)) if iso is not None else None,
        ec=as_float(r.get("ExposureCompensation")),
        exposure_mode=r.get("CanonExposureMode"),
        drive=r.get("ContinuousDrive"),
        high_speed=high_speed_drive(r.get("ContinuousDrive")),
        shutter_mode=r.get("ShutterMode"),
        electronic=electronic_shutter(r.get("ShutterMode")),
        focus_mode=r.get("FocusMode"),
        af=af_mode(r.get("FocusMode")),
        af_area_raw=r.get("AFAreaMode"),
        af_area=af_area(r.get("AFAreaMode")),
        subject=subject_detect(r.get("SubjectToDetect")),
        eye=eye_on(r.get("EyeDetection")),
        is_raw=r.get("ImageStabilization"),
        is_on=is_state,
        slow_shutter=is_slow_shutter(exposure, focal, is_state is True, C),
        orientation_raw=r.get("Orientation"),
        orientation=orientation_flag(r.get("Orientation")),
        flash_raw=r.get("Flash"),
        flash=flash_fired(r.get("Flash")),
        white_balance=r.get("WhiteBalance"),
        picture_style=r.get("PictureStyle"),
        highlight_tone_priority=r.get("HighlightTonePriority"),
        capture_time=cap,
        gps=bool(r.get("GPSLatitude")),
    )
    return meta


# ------------------------------------------------------------------ IO

def list_raws(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW_EXT and not p.name.startswith(".") and not is_schedule_file(p))


def read_exif(files, cache_path=None, workers=8, refresh=False):
    """exiftool (decoded, read-only) in parallel batches; cached as JSON keyed by filename."""
    cache = {}
    if cache_path and Path(cache_path).exists() and not refresh:
        try:
            cache = {r["FileName"]: r for r in json.load(open(cache_path)) if r.get("FileName")}
        except Exception:
            cache = {}
    todo = [str(f) for f in files if Path(f).name not in cache]
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        batches = [todo[i:i + 50] for i in range(0, len(todo), 50)]

        def run(b):
            p = subprocess.run(
                [EXIFTOOL, "-j", "-fast"] + [f"-{t}" for t in TAGS] + b,
                capture_output=True, text=True,
            )
            return json.loads(p.stdout or "[]")

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, recs in enumerate(ex.map(run, batches)):
                for r in recs:
                    if r.get("FileName"):
                        cache[r["FileName"]] = r
                print(f"[exif {min(len(todo), (i + 1) * 50)}/{len(todo)}]", flush=True)
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            json.dump(list(cache.values()), open(cache_path, "w"))
    return cache


def index_shas(folder_name: str) -> dict:
    """filename -> sha256 from the local index, if present."""
    db = HOME / "index" / "catalog.sqlite"
    if not db.exists():
        return {}
    try:
        import sqlite3
        c = sqlite3.connect(str(db))
        return {r[0]: r[1] for r in c.execute(
            "SELECT filename, sha256 FROM images WHERE folder=?", (folder_name,))}
    except Exception:
        return {}


def load_preview(path: Path, sha: str | None, max_px: int):
    """Oriented-enough RGB preview: tagger cache, else embedded PreviewImage. None on failure."""
    from PIL import Image
    import io
    try:
        if sha:
            c = HOME / "cache" / f"{sha[:24]}.jpg"
            if c.exists():
                im = Image.open(c).convert("RGB")
                return _downscale(im, max_px)
        p = subprocess.run([EXIFTOOL, "-b", "-PreviewImage", str(path)], capture_output=True)
        data = p.stdout or b""
        if not data:
            p = subprocess.run([EXIFTOOL, "-b", "-JpgFromRaw", str(path)], capture_output=True)
            data = p.stdout or b""
        if not data:
            return None
        im = Image.open(io.BytesIO(data)).convert("RGB")
        return _downscale(im, max_px)
    except Exception:
        return None


def _downscale(im, max_px: int):
    w, h = im.size
    m = max(w, h)
    if m > max_px:
        im = im.resize((max(1, int(w * max_px / m)), max(1, int(h * max_px / m))))
    return im


def clip_fractions(im, highlight_level=253, shadow_level=2):
    """-> (highlight_frac, shadow_frac) on an RGB preview.
    Highlights: all three channels at or above highlight_level (blown).
    Shadows: all three channels at or below shadow_level (crushed).
    """
    import numpy as np
    arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
    if arr.size == 0:
        return 0.0, 0.0
    hi = float((arr.min(axis=2) >= highlight_level).mean())
    lo = float((arr.max(axis=2) <= shadow_level).mean())
    return hi, lo


def clip_flags(im, C=None):
    C = C or DEFAULTS
    if im is None:
        return False, False, None, None
    hi, lo = clip_fractions(im, C["highlight_level"], C["shadow_level"])
    return hi >= C["highlight_frac"], lo >= C["shadow_frac"], hi, lo


# ------------------------------------------------------------------ main pass

def run_folder(folder, files=None, sample=0, refresh=False, no_clip=False, quiet=False):
    folder = Path(folder)
    fo = folder.name
    d = OUT / fo
    d.mkdir(parents=True, exist_ok=True)
    C = cfg()
    files = [Path(p) for p in (files or list_raws(folder))]
    files = [p for p in files if p.suffix.lower() in RAW_EXT and not p.name.startswith(".") and not is_schedule_file(p)]
    if sample and len(files) > sample:
        # even spread, same idea as tagger.spread
        if sample == 1:
            files = [files[0]]
        else:
            files = [files[int(round(i * (len(files) - 1) / (sample - 1)))] for i in range(sample)]
    t0 = time.time()
    if not quiet:
        print(f"camera: {len(files)} RAW frames in {fo}", flush=True)
    ex = read_exif(files, d / "camera-exif.json", C["workers"], refresh)
    shas = index_shas(fo)
    rows = []
    clip_ok = 0
    for i, p in enumerate(files, 1):
        rec = ex.get(p.name) or {}
        meta = normalize(rec, C)
        sha = shas.get(p.name)
        hi = lo = None
        if not no_clip:
            im = load_preview(p, sha, C["clip_preview_px"])
            ch, cs, hi, lo = clip_flags(im, C)
            meta["clipped_highlights"] = ch
            meta["crushed_shadows"] = cs
            if im is not None:
                clip_ok += 1
        kws = keywords_for(meta, C)
        rows.append(dict(
            filename=p.name,
            sha256=sha,
            needs_review=False,
            proposed_keywords=kws,
            capture_time=meta.get("capture_time"),
            serial=meta.get("serial"),
            camera=meta,
            clip={"highlight_frac": hi, "shadow_frac": lo} if hi is not None else None,
        ))
        if not quiet and (i == 1 or i == len(files) or i % 50 == 0):
            print(f"[{i}/{len(files)}] {p.name}", flush=True)

    # outputs
    counts = Counter(k for r in rows for k in r["proposed_keywords"])
    proposals = {
        "folder": str(folder),
        "profile": "camera",
        "pass": "camera",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "thresholds": {k: C[k] for k in DEFAULTS},
        "summary": {
            "frames": len(rows),
            "with_keywords": sum(1 for r in rows if r["proposed_keywords"]),
            "clip_previews": clip_ok,
            "elapsed_s": round(time.time() - t0, 1),
            "by_keyword": dict(counts.most_common()),
        },
        "images": rows,
    }
    pf = d / "camera-proposals.json"
    json.dump(proposals, open(pf, "w"))
    cols = [
        "filename", "lens", "focal", "focal_band", "fnumber", "exposure", "iso",
        "wide_open", "slow_shutter", "high_speed", "electronic", "af", "af_area",
        "subject", "is_on", "orientation", "flash", "white_balance", "picture_style",
        "highlight_tone_priority", "serial", "capture_time", "keywords",
        "highlight_frac", "shadow_frac",
    ]
    with open(d / "camera.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in rows:
            m = r["camera"]
            clip = r.get("clip") or {}
            w.writerow({
                "filename": r["filename"],
                "lens": m.get("lens_short") or m.get("lens_model"),
                "focal": m.get("focal"),
                "focal_band": m.get("focal_band"),
                "fnumber": m.get("fnumber"),
                "exposure": m.get("exposure"),
                "iso": m.get("iso"),
                "wide_open": int(bool(m.get("wide_open"))),
                "slow_shutter": int(bool(m.get("slow_shutter"))),
                "high_speed": int(bool(m.get("high_speed"))),
                "electronic": int(bool(m.get("electronic"))),
                "af": m.get("af"),
                "af_area": m.get("af_area"),
                "subject": m.get("subject"),
                "is_on": "" if m.get("is_on") is None else int(bool(m.get("is_on"))),
                "orientation": m.get("orientation"),
                "flash": int(bool(m.get("flash"))),
                "white_balance": m.get("white_balance"),
                "picture_style": m.get("picture_style"),
                "highlight_tone_priority": m.get("highlight_tone_priority"),
                "serial": r.get("serial"),
                "capture_time": r.get("capture_time"),
                "keywords": "|".join(r["proposed_keywords"]),
                "highlight_frac": clip.get("highlight_frac"),
                "shadow_frac": clip.get("shadow_frac"),
            })
    if not quiet:
        print(f"camera: {len(rows)} frames, {clip_ok} previews, {sum(counts.values())} keyword assignments; -> {pf}", flush=True)
        for k, n in counts.most_common():
            print(f"  {n:5d}  {k}", flush=True)
        print(f"elapsed {time.time() - t0:.1f}s", flush=True)
    return pf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--refresh-exif", action="store_true")
    ap.add_argument("--no-clip", action="store_true", help="skip the preview highlight/shadow check")
    a = ap.parse_args()
    run_folder(a.folder, sample=a.sample, refresh=a.refresh_exif, no_clip=a.no_clip)


if __name__ == "__main__":
    main()
