"""Match Camera Exports back to Camera Imports and (re)calibrate the rank model.

Read-only on the NAS: never modify, move or delete anything there. Dated
export folders and event-named folders under "<Year> Exports" are listed;
Archive, C1 High Res and Untitled Export are skipped unless they clearly
map. MP4/MOV are ignored.

Exports are positives; unexported frames in the same indexed shoot are
negatives. Star ratings of 3+ are also treated as positives when a snapshot
exists. Training uses only local index features (no images leave the Mac).
"""
from __future__ import annotations

import csv, itertools, json, math, os, re, sqlite3, subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
IDX = HOME / "index" / "catalog.sqlite"
OUT = HOME / "out"
PHOTOS = Path("/Volumes/Photos")
EXPORTS_ROOT = PHOTOS / "Camera Exports"
IMPORTS_ROOT = PHOTOS / "Camera Imports"
EXIFTOOL = HOME / "bin" / "exiftool"
SKIP_VIDEO = {".mp4", ".mov", ".m4v", ".avi", ".mpg", ".mpeg", ".mts", ".m2ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic", ".jxl",
             ".dng", ".cr3", ".cr2", ".nef", ".arw", ".raf", ".rw2", ".orf"}
# leftover undated containers inside a dated folder are not walked
SKIP_SUBDIR = {"untitled export", "archive", "archiveimages", "@eadir",
               ".appledouble", "tmp", "temp"}

CAMERA_STEM = re.compile(
    r"^_?[A-Za-z]{1,8}\d[A-Za-z0-9_]{0,8}\d{2,}$"
    r"|^_?[A-Za-z]{2,8}_?\d{3,6}$"
    r"|^IMG[L_]?\d+$"
    r"|^\d{2,4}[A-Za-z]\d{3,5}$",  # e.g. 029A4324
    re.I,
)
SUFFIX = re.compile(
    r"([\s._-](?:\d+|edit\d*|enhanced-?nr|copy\d*|output|export|edited|crop|web|full)"
    r"|\s*\(\d+(?:\s+of\s+\d+)?\))$",
    re.I,
)
# 2026-11-07BritCar..., 2026-6-23C1, 2026-06-231, 2026-7-12 CSF26, 20190706
DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
DATE_LOOSE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$")
YEAR_BUCKET = re.compile(r"^(\d{4})\s*exports$", re.I)

# Brands Hatch in-catalog weights (2026-11-09, AUC 0.80 against 3★+). Rollback default.
DEFAULT_WEIGHTS = {"sharp": 0.5, "aes": 0.15, "burst_best": 0.3, "burst_other": 0.1, "blur": 0.3}

GRID = {
    "sharp": [0.15, 0.25, 0.4, 0.5, 0.75, 1.0],
    "aes": [0.05, 0.15, 0.3, 0.45],
    "burst_best": [0.0, 0.1, 0.2, 0.3, 0.5],
    "burst_other": [0.0, 0.1, 0.2, 0.3],
    "blur": [0.0, 0.1, 0.2, 0.3, 0.4, 0.6],
}


def canonical_stem(filename: str) -> str:
    """Map an export filename to the likely raw stem, peeling _1 / -2 / ' 1' / -Edit / -Enhanced-NR."""
    s = Path(filename).stem
    while True:
        m = SUFFIX.search(s)
        if not m:
            break
        base = s[: m.start()]
        if CAMERA_STEM.match(base):
            s = base
        else:
            break
    return s


def parse_export_folder_date(name: str):
    """Return (YYYY-MM-DD, suffix) if `name` is a dated export folder, else None."""
    name = name.strip()
    m = DATE_COMPACT.match(name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", ""
    m = DATE_LOOSE.match(name)
    if not m:
        return None
    y, mo, d, rest = m.group(1), int(m.group(2)), m.group(3), (m.group(4) or "")
    if not 1 <= mo <= 12:
        return None
    # 2026-06-231 → day 23, suffix 1
    if len(d) == 3 and d[2].isdigit() and 1 <= int(d[:2]) <= 31:
        rest = d[2:] + rest
        d = d[:2]
    if not d.isdigit():
        return None
    day = int(d)
    if not 1 <= day <= 31:
        return None
    return f"{y}-{mo:02d}-{day:02d}", rest.lstrip(" _-")


def normalize_time(s: str | None, keep_subsec: bool = True) -> str | None:
    """Canonical 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS.ss' (hundredths)."""
    if not s:
        return None
    s2 = str(s).strip()
    s2 = s2.replace("T", " ").replace("Z", "")
    s2 = re.sub(r"[+-]\d{2}:\d{2}$", "", s2)
    m = re.search(
        r"(\d{4})[-:](\d{2})[-:](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.:](\d+))?",
        s2,
    )
    if not m:
        return None
    base = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
    frac = m.group(7)
    if keep_subsec and frac:
        # hundredths, already-rounded strings like .28 or .280 or .2
        frac = (frac + "00")[:2]
        return f"{base}.{frac}"
    return base


def time_second(t: str | None) -> str | None:
    if not t:
        return None
    return t.split(".")[0]


def _auc(scores, labels) -> float:
    """Mann–Whitney AUC, including 0.5 for ties. O(n log n), fully vectorised."""
    import numpy as np
    s = np.asarray(scores, float).ravel()
    lab = np.asarray(labels, bool).ravel()
    n_pos = int(lab.sum()); n_neg = int((~lab).sum())
    if not n_pos or not n_neg:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    s_sorted = s[order]
    y_sorted = lab[order]
    # mid-ranks of tied scores: start + (count-1)/2, 1-based
    starts = np.empty(s_sorted.size, dtype=bool)
    starts[0] = True
    starts[1:] = s_sorted[1:] != s_sorted[:-1]
    start_idx = np.flatnonzero(starts)
    counts = np.diff(np.append(start_idx, s_sorted.size))
    mid = start_idx + (counts - 1) / 2.0 + 1.0
    ranks = np.repeat(mid, counts)
    u = ranks[y_sorted].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def rank_score(r, w):
    return (w["sharp"] * r["sharp_pct"] + w["aes"] * r["aes_pct"]
            + w["burst_best"] * r["burst_best"]
            - w["burst_other"] * r["burst_other"] - w["blur"] * r["blur"])


def list_year_imports() -> dict[str, list[str]]:
    """Map year -> import folder names (directory names only)."""
    out = {}
    if not IMPORTS_ROOT.is_dir():
        return out
    for ydir in IMPORTS_ROOT.iterdir():
        if not ydir.is_dir() or not re.fullmatch(r"\d{4}", ydir.name):
            continue
        out[ydir.name] = sorted(p.name for p in ydir.iterdir() if p.is_dir() and not p.name.startswith("."))
    return out


def map_date_to_import(date: str, suffix: str, year_imports: dict[str, list[str]]) -> str | None:
    year = date[:4]
    names = year_imports.get(year, [])
    exact = [n for n in names if n == date]
    if len(exact) == 1:
        return exact[0]
    prefixed = [n for n in names if n == date or n.startswith(date + "_") or n.startswith(date + "-")]
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        key = re.sub(r"[^a-z0-9]", "", (suffix or "").lower())
        if "britcar" in key or "intothenight" in key or "brandshatch" in key:
            hits = [n for n in prefixed if "brandshatch" in n.lower() or "britcar" in n.lower() or "intothenight" in n.lower()]
            if len(hits) == 1:
                return hits[0]
        if key:
            hits = [n for n in prefixed if key and key in re.sub(r"[^a-z0-9]", "", n.lower())]
            if len(hits) == 1:
                return hits[0]
    return exact[0] if len(exact) == 1 else None




def _cfg() -> dict:
    p = HOME / "config.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _np():
    import nas_paths as NP
    NP.HOME = HOME
    return NP


def photos_root() -> Path:
    return _np().photos_root(HOME)


def exports_root() -> Path:
    return _np().exports_root(HOME)


def imports_root() -> Path:
    return _np().imports_root(HOME)


def other_exports_root() -> Path:
    return _np().other_exports_root(HOME)


def exports_2025_root() -> Path:
    """Backward-compatible alias for other_exports_root()."""
    return other_exports_root()


def _other_exports_overridden() -> bool:
    cfg = _cfg()
    return bool(
        os.environ.get("LOCALAI_OTHER_EXPORTS")
        or os.environ.get("LOCALAI_EXPORTS_2025")
        or cfg.get("other_exports_root")
        or cfg.get("exports_2025_root")
    )


def list_export_roots(photos: Path | None = None) -> list[dict]:
    """Known export roots the Training tab can offer: Camera Exports and Other Exports."""
    photos = Path(photos) if photos is not None else photos_root()
    cam = exports_root()
    if not cam.is_dir():
        # still show the conventional path under photos
        cam = photos / "Camera Exports"
    other = other_exports_root() if photos is None or photos == photos_root() else (Path(photos) / "2025 Exports")
    # When the caller passed an explicit photos root, prefer that tree unless config
    # has an explicit other_exports_root (or legacy exports_2025_root / env).
    if photos != photos_root() and not _other_exports_overridden():
        other = Path(photos) / "2025 Exports"
        if not other.is_dir() and (cam / "2025 Exports").is_dir():
            other = cam / "2025 Exports"
    candidates = [
        ("camera-exports", "Camera Exports", cam),
        ("other-exports", "Other Exports", other),
    ]
    roots, seen = [], set()
    for rid, label, path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append({"id": rid, "label": label, "path": key, "available": path.is_dir()})
    return roots


def discover_export_folders_many(roots) -> list[dict]:
    """Union of dated/event export folders under one or more roots, unique by path."""
    found, seen = [], set()
    for root in roots or []:
        for f in discover_export_folders(Path(root)):
            if f["path"] in seen:
                continue
            seen.add(f["path"])
            found.append(f)
    return found


def import_folder_path(name: str, year_imports: dict | None = None) -> Path | None:
    """Resolve an import shoot folder name to a path under Camera Imports."""
    year_imports = year_imports if year_imports is not None else list_year_imports()
    for year, names in year_imports.items():
        if name in names:
            p = IMPORTS_ROOT / year / name
            if p.is_dir():
                return p
    if IMPORTS_ROOT.is_dir():
        for ydir in IMPORTS_ROOT.iterdir():
            if ydir.is_dir() and re.fullmatch(r"\d{4}", ydir.name):
                p = ydir / name
                if p.is_dir():
                    return p
    return None


def folders_needing_index(positives, index_by_folder, min_indexed=50, year_imports=None):
    """Import folders with export matches that are missing or too small in the local index."""
    out = []
    for fo, stems in (positives or {}).items():
        n = len(index_by_folder.get(fo) or [])
        if n >= min_indexed:
            continue
        path = import_folder_path(fo, year_imports)
        out.append({
            "folder": fo,
            "path": str(path) if path else None,
            "indexed": n,
            "exported_matched": len(stems),
            "missing_path": path is None,
        })
    return out


def discover_export_folders(root: Path = EXPORTS_ROOT) -> list[dict]:
    """Dated export folders only (top-level, under '<Year> Exports', and dated subfolders)."""
    found = []
    if not root.is_dir():
        return found

    def consider(path: Path, source: str):
        parsed = parse_export_folder_date(path.name)
        if not parsed:
            return
        date, suffix = parsed
        found.append({"path": str(path), "name": path.name, "date": date, "suffix": suffix, "source": source, "kind": "dated"})

    def add_bucket_children(bucket: Path, source: str):
        loose = False
        for c in sorted(bucket.iterdir(), key=lambda x: x.name):
            if c.name.startswith(".") or c.name.startswith("._"):
                continue
            if c.is_dir() and c.name.lower() not in SKIP_SUBDIR:
                parsed = parse_export_folder_date(c.name)
                if parsed:
                    consider(c, source)
                else:
                    found.append({"path": str(c), "name": c.name, "date": None,
                                  "suffix": "", "source": source, "kind": "event"})
            elif c.is_file():
                ext = c.suffix.lower()
                if ext in IMAGE_EXT and ext not in SKIP_VIDEO:
                    loose = True
        if loose:
            found.append({"path": str(bucket), "name": bucket.name + " (root files)", "date": None,
                          "suffix": "", "source": source, "kind": "event-root"})

    # When the selected root itself is '<Year> Exports', walk its event/dated children.
    if YEAR_BUCKET.match(root.name):
        add_bucket_children(root, root.name)
        uniq, seen = [], set()
        for f in found:
            if f["path"] in seen:
                continue
            seen.add(f["path"])
            uniq.append(f)
        return uniq

    for p in sorted(root.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name.startswith(".") or p.name.lower() in SKIP_SUBDIR:
            continue
        if YEAR_BUCKET.match(p.name):
            add_bucket_children(p, p.name)
            continue
        if p.name.lower() in ("archive", "archiveimages", "botrequested", "c1 high res", "untitled export"):
            continue
        consider(p, "top")
        # dated subfolders of a dated folder (e.g. 2026-6-23C1/2026-09-11)
        parsed = parse_export_folder_date(p.name)
        if parsed:
            try:
                kids = list(p.iterdir())
            except OSError:
                kids = []
            for c in kids:
                if c.is_dir() and not c.name.startswith(".") and c.name.lower() not in SKIP_SUBDIR:
                    consider(c, p.name)
    # unique by path
    uniq, seen = [], set()
    for f in found:
        if f["path"] in seen:
            continue
        seen.add(f["path"])
        uniq.append(f)
    return uniq


def iter_export_images(folder: Path):
    """Image files directly in a dated folder; do not walk undated nested containers."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for ent in entries:
        if not ent.is_file():
            continue
        name = ent.name
        if name.startswith(".") or name.startswith("._"):
            continue
        ext = Path(name).suffix.lower()
        if ext in SKIP_VIDEO:
            continue
        if ext in IMAGE_EXT:
            yield Path(ent.path)


def list_import_stems(import_dir: Path) -> dict[str, str]:
    """filename stem -> filename, ignoring videos. Used only for a specific shoot folder."""
    out = {}
    if not import_dir.is_dir():
        return out
    try:
        entries = os.scandir(import_dir)
    except OSError:
        return out
    with entries:
        for ent in entries:
            if not ent.is_file():
                continue
            name = ent.name
            if name.startswith(".") or name.startswith("._"):
                continue
            ext = Path(name).suffix.lower()
            if ext in SKIP_VIDEO:
                continue
            if ext not in IMAGE_EXT and ext not in {".xmp"}:
                continue
            if ext == ".xmp":
                continue
            out[Path(name).stem] = name
    return out


def index_rows_by_folder(db_path: Path = IDX) -> dict[str, list[dict]]:
    if not Path(db_path).exists():
        return {}
    c = sqlite3.connect(str(db_path))
    rows = c.execute(
        "SELECT sha256, filename, folder, capture_time, camera, sharp_subject, coherence, aesthetic, path "
        "FROM images WHERE sharp_subject IS NOT NULL"
    ).fetchall()
    c.close()
    by = defaultdict(list)
    for sha, fn, fo, ct, cam, lap, coh, aes, path in rows:
        by[fo].append({
            "sha256": sha, "filename": fn, "folder": fo, "capture_time": ct,
            "camera": cam, "sharp_subject": lap, "coherence": coh, "aesthetic": aes,
            "path": path, "stem": Path(fn).stem,
            "time": normalize_time(ct),
        })
    return dict(by)


def quality_features(folder_rows, bursts_by_name):
    """Same sharp_pct / aes_pct / blur / burst flags as features.rank_features."""
    if not folder_rows:
        return []
    def pct_rank(vals):
        n = len(vals)
        order = sorted(range(n), key=lambda i: vals[i])
        out = [0.0] * n
        for rank, i in enumerate(order):
            out[i] = rank / (n - 1) if n > 1 else 1.0
        return out
    sharp = [math.log1p(r["sharp_subject"]) for r in folder_rows]
    aes = [r["aesthetic"] for r in folder_rows]
    sp, ap = pct_rank(sharp), pct_rank(aes)
    out = []
    for r, p, a in zip(folder_rows, sp, ap):
        flag = None
        # match features.py FCFG defaults
        if p >= 0.60 and r["sharp_subject"] >= 300.0:
            flag = "Sharp"
        elif p <= 0.25 or r["sharp_subject"] <= 120.0:
            flag = "Motion Blur" if (r.get("coherence") or 0) >= 0.30 else "Soft Focus"
        rec = dict(r)
        rec["sharp_pct"] = round(p, 3)
        rec["aes_pct"] = round(a, 3)
        rec["flag"] = flag
        rec["burst_best"] = 1.0 if bursts_by_name.get(r["filename"]) is True else 0.0
        rec["burst_other"] = 1.0 if bursts_by_name.get(r["filename"]) is False else 0.0
        rec["blur"] = 1.0 if flag in ("Motion Blur", "Soft Focus") else 0.0
        out.append(rec)
    return out


def load_bursts(folder: str) -> dict:
    p = OUT / folder / "bursts.csv"
    inb = {}
    if p.exists():
        for r in csv.DictReader(open(p)):
            inb[r["filename"]] = (r.get("best") == "True")
    return inb


def load_ratings(folder: str) -> dict[str, int]:
    d = OUT / folder
    snaps = sorted(d.glob("*flags-*.json"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        return {}, None
    s = json.load(open(snaps[-1]))
    ratings = {v["name"]: int(v.get("rating") or 0) for v in s.values() if isinstance(v, dict) and "name" in v}
    return ratings, snaps[-1].name


def exif_for(paths: list[Path]) -> dict[str, dict]:
    """Read-only exiftool for unmatched exports (DateTimeOriginal + SerialNumber)."""
    out = {}
    if not paths:
        return out
    tool = str(EXIFTOOL if EXIFTOOL.exists() else "exiftool")
    # chunk to keep the command line reasonable
    for i in range(0, len(paths), 80):
        chunk = paths[i:i + 80]
        r = subprocess.run(
            [tool, "-json", "-n", "-DateTimeOriginal", "-CreateDate",
             "-SubSecTimeOriginal", "-SubSecDateTimeOriginal", "-SerialNumber",
             "-CameraSerialNumber", "-FileName"] + [str(p) for p in chunk],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode and not r.stdout:
            continue
        try:
            rows = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        for rec in rows:
            src = rec.get("SourceFile") or rec.get("FileName")
            raw_t = rec.get("SubSecDateTimeOriginal") or rec.get("DateTimeOriginal") or rec.get("CreateDate")
            sub = rec.get("SubSecTimeOriginal")
            if sub is not None and raw_t and "." not in str(raw_t):
                raw_t = f"{raw_t}.{sub}"
            out[src] = {
                "time": normalize_time(raw_t),
                "serial": rec.get("SerialNumber") or rec.get("CameraSerialNumber"),
                "filename": rec.get("FileName"),
            }
    return out



RAW_EXTS = [".CR3", ".CR2", ".NEF", ".ARW", ".DNG", ".RAF", ".RW2", ".ORF", ".RAW"]


def find_raw_by_stem(stem: str, import_dir: Path, exts=None):
    """Cheap existence check for a stem in one import folder (no directory listing)."""
    if not import_dir or not stem:
        return None
    for ext in (exts or RAW_EXTS):
        p = import_dir / f"{stem}{ext}"
        try:
            if p.is_file():
                return p.name
        except OSError:
            continue
        # case variants only if the canonical missed
        p2 = import_dir / f"{stem}{ext.lower()}"
        if p2 != p:
            try:
                if p2.is_file():
                    return p2.name
            except OSError:
                continue
    return None


def _folder_date(name: str):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_stem_across_year(stem: str, year: str, year_imports: dict[str, list[str]],
                             prefer: str | None = None, around: str | None = None):
    """Look in the mapped folder, then nearby dates, then the rest of that year, via exists()."""
    names = list(year_imports.get(year, []))
    around_d = _folder_date(around or prefer or "")
    def nearby(n):
        d = _folder_date(n)
        return d is not None and around_d is not None and abs((d - around_d).days) <= 2
    ordered = []
    if prefer and prefer in names:
        ordered.append(prefer)
    ordered += [n for n in names if n not in ordered and nearby(n)]
    rest = [n for n in names if n not in ordered]

    def search(pool, exts=None):
        hits = []
        for name in pool:
            raw = find_raw_by_stem(stem, IMPORTS_ROOT / year / name, exts=exts)
            if raw:
                hits.append((name, raw))
                if prefer and name == prefer:
                    return hits
        return hits

    hits = search(ordered)
    if prefer and any(h[0] == prefer for h in hits):
        return next(h for h in hits if h[0] == prefer)
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None  # ambiguous among nearby
    hits = search(rest, exts=[".CR3", ".CR2"])
    if len(hits) == 1:
        return hits[0]
    return None


def lookup_exif(info_by_src: dict, path: Path) -> dict:
    info = info_by_src.get(str(path)) or info_by_src.get(path.name) or {}
    if info:
        return info
    for k, v in info_by_src.items():
        if k.endswith(str(path)) or Path(k) == path:
            return v
    return {}


def capture_date_of(exif_time: str | None) -> str | None:
    if not exif_time:
        return None
    return exif_time.split(" ")[0] if " " in exif_time else exif_time[:10]


def serial_key(v) -> str | None:
    if v is None or v == "":
        return None
    return str(v).strip()


def list_import_raws(import_dir: Path) -> list[Path]:
    """Raws in one import folder (that folder only)."""
    out = []
    if not import_dir.is_dir():
        return out
    try:
        entries = os.scandir(import_dir)
    except OSError:
        return out
    with entries:
        for ent in entries:
            if not ent.is_file():
                continue
            name = ent.name
            if name.startswith(".") or name.startswith("._"):
                continue
            ext = Path(name).suffix.lower()
            if ext in SKIP_VIDEO or ext not in {e.lower() for e in RAW_EXTS}:
                continue
            out.append(Path(ent.path))
    return out


def import_exif_index(import_dir: Path, year: str | None = None) -> list[dict]:
    """Read-only EXIF (time + serial) for raws in one import folder, cached locally."""
    cache_dir = HOME / "cache" / "import-exif"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{year or import_dir.parent.name}-{import_dir.name}.json"
    cache = cache_dir / key
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass
    rows = []
    files = list_import_raws(import_dir)
    exif = exif_for(files)
    for p in files:
        info = lookup_exif(exif, p)
        rows.append({
            "filename": p.name,
            "stem": Path(p.name).stem,
            "time": info.get("time"),
            "serial": serial_key(info.get("serial")),
            "folder": import_dir.name,
            "path": str(p),
        })
    cache.write_text(json.dumps(rows))
    return rows

def match_exports(export_folders, year_imports, index_by_folder, import_stem_cache=None):
    """Match each export image to a raw.

    Prefer EXIF capture date (plus sub-second and body serial) to pick the import
    folder, then filename stem. Falls back to the export folder's date name, the
    local index, and exists() in the same year. For event-named folders the EXIF
    date is the only folder mapping.
    """
    import_stem_cache = import_stem_cache if import_stem_cache is not None else {}
    stem_index = defaultdict(list)
    time_index = defaultdict(list)
    time_second_index = defaultdict(list)
    serial_time_index = defaultdict(list)
    for fo, rows in index_by_folder.items():
        for r in rows:
            stem_index[r["stem"]].append((fo, r))
            if r["time"]:
                time_index[(fo, r["time"])].append(r)
                time_second_index[(fo, time_second(r["time"]))].append(r)
            if r["time"] and r.get("serial"):
                serial_time_index[(fo, r["time"], serial_key(r["serial"]))].append(r)

    # EXIF every export image we are about to consider (needed for event-folder mapping).
    all_images = []
    for ef in export_folders:
        imgs = list(iter_export_images(Path(ef["path"])))
        all_images.append((ef, imgs))
    flat = [img for _, imgs in all_images for img in imgs]
    print(f"  reading EXIF for {len(flat)} export files (read-only)…", flush=True)
    exif_all = exif_for(flat)

    results = []
    for ef, images in all_images:
        date, suffix = ef.get("date"), ef.get("suffix") or ""
        mapped_by_name = map_date_to_import(date, suffix, year_imports) if date else None
        recs = []
        for img in images:
            stem = canonical_stem(img.name)
            info = lookup_exif(exif_all, img)
            et, es = info.get("time"), serial_key(info.get("serial"))
            edate = capture_date_of(et)
            mapped = map_date_to_import(edate, "", year_imports) if edate else None
            if not mapped:
                mapped = mapped_by_name
            year = (edate or date or "")[:4] or None
            if not year and ef.get("source") and YEAR_BUCKET.match(str(ef.get("source"))):
                year = YEAR_BUCKET.match(str(ef["source"])).group(1)
            rec = {
                "export_path": str(img), "export_name": img.name, "stem": stem,
                "how": None, "import_folder": None, "raw_name": None,
                "mapped_import": mapped, "exif_time": et, "exif_serial": es,
                "exif_date": edate, "year": year,
            }
            # 1. stem in the EXIF/date-mapped folder (index or exists)
            if CAMERA_STEM.match(stem) and mapped and year:
                if mapped in index_by_folder:
                    hits = [r for r in index_by_folder[mapped] if r["stem"] == stem]
                    if len(hits) == 1:
                        rec.update(how="stem", import_folder=mapped, raw_name=hits[0]["filename"])
                if not rec["how"]:
                    raw = find_raw_by_stem(stem, IMPORTS_ROOT / year / mapped)
                    if raw:
                        rec.update(how="stem", import_folder=mapped, raw_name=raw)
            recs.append(rec)
        results.append({"export": ef, "mapped_import": mapped_by_name, "images": recs, "n_images": len(images)})

    # 2. EXIF time (+ serial) against the local index
    for block in results:
        for rec in block["images"]:
            if rec["how"] or not rec.get("exif_time"):
                continue
            t, ser, mapped = rec["exif_time"], rec.get("exif_serial"), rec.get("mapped_import")
            candidates = []
            how = "exif-time"
            if ser and mapped:
                candidates = list(serial_time_index.get((mapped, t, ser), []))
            if not candidates and mapped:
                candidates = list(time_index.get((mapped, t), []))
            if not candidates:
                hits = []
                index = serial_time_index if ser else time_index
                key_fn = (lambda fo: (fo, t, ser)) if ser else (lambda fo: (fo, t))
                for fo in index_by_folder:
                    hits.extend((fo, r) for r in index.get(key_fn(fo), []))
                if len(hits) == 1:
                    candidates, rec["mapped_import"] = [hits[0][1]], hits[0][0]
            if not candidates:
                sec = time_second(t)
                pool = list(time_second_index.get((mapped, sec), [])) if mapped else []
                if not pool:
                    hits = []
                    for fo in index_by_folder:
                        hits.extend((fo, r) for r in time_second_index.get((fo, sec), []))
                    if hits and len({h[0] for h in hits}) == 1:
                        pool = [h[1] for h in hits]
                        rec["mapped_import"] = hits[0][0]
                if len(pool) == 1:
                    candidates = pool
                elif len(pool) > 1 and "." in (t or ""):
                    def subsec(row):
                        tt = row.get("time") or ""
                        return int((tt.split(".")[1] + "00")[:2]) if "." in tt else 0
                    want = int((t.split(".")[1] + "00")[:2])
                    ranked = sorted(pool, key=lambda r: abs(subsec(r) - want))
                    # prefer matching serial if present
                    if ser:
                        same = [r for r in ranked if serial_key(r.get("serial")) == ser]
                        if len(same) == 1:
                            candidates, how = same, "exif-time-serial"
                    if not candidates and abs(subsec(ranked[0]) - want) < abs(subsec(ranked[1]) - want):
                        candidates, how = [ranked[0]], "exif-time-nearest"
            if len(candidates) == 1:
                r = candidates[0]
                rec["how"] = how
                rec["import_folder"] = r["folder"]
                rec["raw_name"] = r["filename"]

    # 3. Stem exists() in the same year, preferring the EXIF-dated import folder
    for block in results:
        for rec in block["images"]:
            if rec["how"] or not CAMERA_STEM.match(rec["stem"]):
                continue
            year = rec.get("year")
            if not year:
                continue
            found = resolve_stem_across_year(
                rec["stem"], year, year_imports,
                prefer=rec.get("mapped_import"),
                around=rec.get("exif_date") or block["export"].get("date"),
            )
            if found:
                rec["how"] = "stem-exists"
                rec["import_folder"], rec["raw_name"] = found

    # 4. For leftovers, read EXIF of the candidate import folder(s) only and match time+serial
    need_folders = set()
    for block in results:
        for rec in block["images"]:
            if rec["how"] or not rec.get("exif_time"):
                continue
            year = rec.get("year")
            mapped = rec.get("mapped_import")
            if year and mapped:
                need_folders.add((year, mapped))
            elif year and rec.get("exif_date"):
                # exact date folder if it exists but wasn't set as mapped
                if rec["exif_date"] in year_imports.get(year, []):
                    need_folders.add((year, rec["exif_date"]))
    raw_time = defaultdict(list)       # (folder, time)
    raw_serial_time = defaultdict(list)
    if need_folders:
        print(f"  reading EXIF for {len(need_folders)} import folder(s) with unmatched exports…", flush=True)
    for year, name in sorted(need_folders):
        rows = import_exif_index(IMPORTS_ROOT / year / name, year=year)
        for r in rows:
            if r.get("time"):
                raw_time[(name, r["time"])].append(r)
                raw_time[(name, time_second(r["time"]))].append(r)
            if r.get("time") and r.get("serial"):
                raw_serial_time[(name, r["time"], r["serial"])].append(r)
    for block in results:
        for rec in block["images"]:
            if rec["how"] or not rec.get("exif_time"):
                continue
            t, ser, mapped = rec["exif_time"], rec.get("exif_serial"), rec.get("mapped_import")
            candidates = []
            how = "exif-time-import"
            if ser and mapped:
                candidates = list(raw_serial_time.get((mapped, t, ser), []))
            if not candidates and mapped:
                candidates = [r for r in raw_time.get((mapped, t), []) if "." in (t or "") or True]
                # exact time first
                exact = [r for r in raw_time.get((mapped, t), [])]
                candidates = exact
            if len(candidates) > 1 and ser:
                same = [r for r in candidates if r.get("serial") == ser]
                if len(same) == 1:
                    candidates, how = same, "exif-time-serial"
            if not candidates and mapped and "." in (t or ""):
                sec = time_second(t)
                pool = [r for r in raw_time.get((mapped, sec), [])]
                if len(pool) == 1:
                    candidates = pool
                elif len(pool) > 1:
                    def subsec(row):
                        tt = row.get("time") or ""
                        return int((tt.split(".")[1] + "00")[:2]) if "." in tt else 0
                    want = int((t.split(".")[1] + "00")[:2])
                    if ser:
                        same = [r for r in pool if r.get("serial") == ser]
                        if same:
                            pool = same
                    ranked = sorted(pool, key=lambda r: abs(subsec(r) - want))
                    if len(ranked) == 1 or (len(ranked) > 1 and abs(subsec(ranked[0]) - want) < abs(subsec(ranked[1]) - want)):
                        candidates, how = [ranked[0]], "exif-time-nearest"
            if len(candidates) == 1:
                r = candidates[0]
                rec["how"] = how
                rec["import_folder"] = r["folder"]
                rec["raw_name"] = r["filename"]
    return results, import_stem_cache



def folder_match_stats(match_results) -> list[dict]:
    """Per export folder and per import shoot match rates (unique stems)."""
    per_export = []
    for block in match_results:
        ef = block["export"]
        # unique stems; a stem is matched if any file of that stem matched
        by_stem = defaultdict(list)
        for rec in block["images"]:
            by_stem[rec["stem"]].append(rec)
        n = len(by_stem)
        matched = sum(1 for recs in by_stem.values() if any(r["how"] for r in recs))
        hows = defaultdict(int)
        for recs in by_stem.values():
            h = next((r["how"] for r in recs if r["how"]), None)
            if h:
                hows[h] += 1
        mapped_hits = defaultdict(int)
        exif_dates = defaultdict(int)
        for recs in by_stem.values():
            rec = next((r for r in recs if r["how"]), recs[0])
            if rec.get("import_folder"):
                mapped_hits[rec["import_folder"]] += 1
            if rec.get("exif_date"):
                exif_dates[rec["exif_date"]] += 1
        per_export.append({
            "export_folder": ef["name"],
            "export_path": ef["path"],
            "date": ef.get("date"),
            "kind": ef.get("kind") or "dated",
            "mapped_import": block.get("mapped_import"),
            "mapped_imports": dict(mapped_hits),
            "exif_dates": dict(exif_dates),
            "unique_stems": n,
            "matched": matched,
            "unmatched": n - matched,
            "match_rate": round(matched / n, 4) if n else None,
            "how": dict(hows),
            "unmatched_names": sorted({r["export_name"] for recs in by_stem.values() if not any(x["how"] for x in recs) for r in recs}),
        })
    return per_export


def positives_by_shoot(match_results) -> dict[str, set[str]]:
    """import folder -> set of raw stems that were exported (matched)."""
    pos = defaultdict(set)
    for block in match_results:
        for rec in block["images"]:
            if rec["how"] and rec["import_folder"] and rec["raw_name"]:
                pos[rec["import_folder"]].add(Path(rec["raw_name"]).stem)
    return dict(pos)



FEATURE_KEYS = ("sharp_pct", "aes_pct", "burst_best", "burst_other", "blur")


def _folder_pack(recs):
    """Numeric feature matrix plus combined / export / star labels for one shoot."""
    import numpy as np
    X = np.asarray([[float(r[k]) for k in FEATURE_KEYS] for r in recs], dtype=float)
    y = np.asarray([bool(r["y"]) for r in recs])
    ye = np.asarray([bool(r["y_export"]) for r in recs])
    ys = np.asarray([bool(r["y_star"]) for r in recs])
    return X, y, ye, ys


def _weighted_scores(X, w):
    import numpy as np
    wv = np.asarray([w["sharp"], w["aes"], w["burst_best"], -w["burst_other"], -w["blur"]], dtype=float)
    return X @ wv


def _logistic_scores(X, model):
    import numpy as np
    coef = np.asarray(model["coef"], dtype=float)
    feats = list(model.get("features") or FEATURE_KEYS)
    if feats != list(FEATURE_KEYS):
        # reorder columns if a stored model used a different feature order
        idx = [FEATURE_KEYS.index(k) for k in feats]
        X = X[:, idx]
    return X @ coef + float(model.get("intercept") or 0.0)


def model_scores(X, model):
    """Scores for a packed feature matrix. model is weighted weights or a logistic dict."""
    if isinstance(model, dict) and model.get("type") == "logistic":
        return _logistic_scores(X, model)
    w = model["weights"] if isinstance(model, dict) and "weights" in model else model
    return _weighted_scores(X, w)


def fit_logistic(labels_by_folder, folders, packs=None):
    """Logistic regression on the existing rank features, combined labels, class-balanced."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    packs = packs or {fo: _folder_pack(labels_by_folder[fo]) for fo in folders}
    Xs, ys = [], []
    for fo in folders:
        X, y, _, _ = packs[fo]
        Xs.append(X); ys.append(y)
    if not Xs:
        return None
    X = np.concatenate(Xs, axis=0)
    y = np.concatenate(ys, axis=0)
    if int(y.sum()) == 0 or int((~y).sum()) == 0:
        return None
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", solver="lbfgs")
    clf.fit(X, y.astype(int))
    return {
        "type": "logistic",
        "coef": [round(float(x), 6) for x in clf.coef_[0]],
        "intercept": round(float(clf.intercept_[0]), 6),
        "features": list(FEATURE_KEYS),
    }


def build_labels(index_by_folder, positives, min_indexed=50, star_threshold=3, use_star_labels=True):
    """For each indexed shoot that has exports: exported (and optionally 3★+) = positive."""
    labels = {}
    summaries = []
    for fo, rows in index_by_folder.items():
        if fo not in positives:
            continue
        if len(rows) < min_indexed:
            summaries.append({"folder": fo, "skipped": f"index too small ({len(rows)} < {min_indexed})"})
            continue
        bursts = load_bursts(fo)
        feats = quality_features(rows, bursts)
        ratings, snap = load_ratings(fo)
        pos_stems = positives[fo]
        recs = []
        n_exp = n_star = n_both = n_neg = 0
        for r in feats:
            stem = Path(r["filename"]).stem
            exported = stem in pos_stems
            star = int(ratings.get(stem, 0) or 0)
            starred = bool(use_star_labels) and star >= star_threshold
            y = bool(exported or starred)
            if exported and starred:
                n_both += 1
            elif exported:
                n_exp += 1
            elif starred:
                n_star += 1
            else:
                n_neg += 1
            recs.append({**r, "exported": exported, "rating": star, "y": y, "y_export": exported, "y_star": starred})
        labels[fo] = recs
        summaries.append({
            "folder": fo, "indexed": len(feats), "exported_matched": len(pos_stems),
            "positives_export_only": n_exp, "positives_star_only": n_star,
            "positives_both": n_both, "positives_combined": n_exp + n_star + n_both,
            "negatives": n_neg, "ratings_snapshot": snap,
            "stars_3plus": sum(1 for r in recs if r["rating"] >= 3),
        })
    return labels, summaries


def eval_weights(labels_by_folder, w, folders=None, packs=None):
    """Per-shoot and overall AUCs for combined / export / star labels (weighted sum)."""
    model = {"type": "weighted", "weights": w}
    return eval_model(labels_by_folder, model, folders=folders, packs=packs)


def eval_model(labels_by_folder, model, folders=None, packs=None):
    """Per-shoot and overall AUCs for a weighted-sum or logistic model."""
    folders = folders or list(labels_by_folder)
    packs = packs or {fo: _folder_pack(labels_by_folder[fo]) for fo in folders}
    per = {}
    all_s, all_y, all_ye, all_ys = [], [], [], []
    import numpy as np
    for fo in folders:
        X, y, ye, ys = packs[fo]
        s = model_scores(X, model)
        per[fo] = {
            "n": int(len(s)),
            "auc_combined": _round_auc(_auc(s, y)),
            "auc_export": _round_auc(_auc(s, ye)),
            "auc_star": _round_auc(_auc(s, ys)),
            "positives_combined": int(y.sum()),
            "positives_export": int(ye.sum()),
            "positives_star": int(ys.sum()),
        }
        all_s.append(s); all_y.append(y); all_ye.append(ye); all_ys.append(ys)
    if all_s:
        all_s = np.concatenate(all_s); all_y = np.concatenate(all_y)
        all_ye = np.concatenate(all_ye); all_ys = np.concatenate(all_ys)
    else:
        all_s = all_y = all_ye = all_ys = np.asarray([])
    overall = {
        "auc_combined_pooled": _round_auc(_auc(all_s, all_y)) if len(all_s) else None,
        "auc_export_pooled": _round_auc(_auc(all_s, all_ye)) if len(all_s) else None,
        "auc_star_pooled": _round_auc(_auc(all_s, all_ys)) if len(all_s) else None,
        "auc_combined_macro": _macro([per[fo]["auc_combined"] for fo in folders]),
        "auc_export_macro": _macro([per[fo]["auc_export"] for fo in folders]),
        "auc_star_macro": _macro([per[fo]["auc_star"] for fo in folders]),
        "n_frames": int(len(all_s)),
        "n_shoots": len(folders),
    }
    out = {"per_shoot": per, "overall": overall}
    if isinstance(model, dict) and model.get("type") == "logistic":
        out["model"] = model
    else:
        w = model["weights"] if isinstance(model, dict) and "weights" in model else model
        out["weights"] = w
        out["model"] = {"type": "weighted", "weights": w}
    return out


def _round_auc(x):
    return None if x != x else round(float(x), 3)  # NaN check


def _macro(xs):
    vals = [x for x in xs if x is not None]
    return None if not vals else round(sum(vals) / len(vals), 3)


def fit_grid(labels_by_folder, folders, metric="auc_combined", packs=None):
    """Grid-search weights maximising the mean per-shoot combined AUC on `folders`.

    Only combined AUC is scored during the search (export/star are evaluated
    for the winning weights afterwards).
    """
    import numpy as np
    packs = packs or {fo: _folder_pack(labels_by_folder[fo]) for fo in folders}
    keys = ("sharp", "aes", "burst_best", "burst_other", "blur")
    Xs, ys = [], []
    for fo in folders:
        X, y, _, _ = packs[fo]
        Xs.append(X); ys.append(y)
    best = None
    for vals in itertools.product(*[GRID[k] for k in keys]):
        w = dict(zip(keys, vals))
        wv = np.asarray([w["sharp"], w["aes"], w["burst_best"], -w["burst_other"], -w["blur"]], dtype=float)
        aucs, all_s, all_y = [], [], []
        for X, y in zip(Xs, ys):
            s = X @ wv
            a = _auc(s, y)
            if a == a:
                aucs.append(a)
            all_s.append(s); all_y.append(y)
        if not aucs:
            continue
        m = sum(aucs) / len(aucs)
        m3 = _auc(np.concatenate(all_s), np.concatenate(all_y))
        m3 = 0.0 if m3 != m3 else m3
        score = (m, m3)
        if best is None or score > best[0]:
            best = (score, w, None)
    if best is None:
        return None
    w = best[1]
    ev = eval_weights(labels_by_folder, w, folders, packs=packs)
    return (best[0], w, ev)


def _macro_tuple(overall):
    """Sort key: combined macro, then export, then star. Missing values sort last."""
    def v(x):
        return -1 if x is None else x
    return (v(overall.get("auc_combined_macro")),
            v(overall.get("auc_export_macro")),
            v(overall.get("auc_star_macro")))


def _loso_for_fit(labels_by_folder, folders, packs, baseline_w, fit_fn, kind):
    """Leave-one-shoot-out. fit_fn(train_folders) -> model dict or None."""
    loso, after_per = [], {}
    for i, held in enumerate(folders, 1):
        print(f"    LOSO {kind} {i}/{len(folders)} hold out {held}…", flush=True)
        train = [f for f in folders if f != held]
        if not train:
            continue
        model = fit_fn(train)
        if not model:
            continue
        ev_held = eval_model(labels_by_folder, model, [held], packs=packs)
        ev_base = eval_weights(labels_by_folder, baseline_w, [held], packs=packs)
        rec = {
            "held_out": held,
            "kind": kind,
            "after": ev_held["per_shoot"][held],
            "before": ev_base["per_shoot"][held],
        }
        if kind == "logistic":
            rec["model"] = model
        else:
            rec["weights"] = model["weights"]
        loso.append(rec)
        after_per[held] = ev_held["per_shoot"][held]
    after_macro = {
        "auc_combined_macro": _macro([after_per[f]["auc_combined"] for f in after_per]),
        "auc_export_macro": _macro([after_per[f]["auc_export"] for f in after_per]),
        "auc_star_macro": _macro([after_per[f]["auc_star"] for f in after_per]),
        "n_shoots": len(after_per),
        "kind": kind,
    }
    return loso, after_macro


def leave_one_shoot_out(labels_by_folder, baseline_w):
    folders = sorted(labels_by_folder)
    packs = {fo: _folder_pack(labels_by_folder[fo]) for fo in folders}
    before = eval_weights(labels_by_folder, baseline_w, folders, packs=packs)

    def fit_w(train):
        fit = fit_grid(labels_by_folder, train, packs=packs)
        if not fit:
            return None
        return {"type": "weighted", "weights": fit[1]}

    def fit_l(train):
        return fit_logistic(labels_by_folder, train, packs=packs)

    loso, after_macro = _loso_for_fit(labels_by_folder, folders, packs, baseline_w, fit_w, "weighted")
    loso_log, after_log = _loso_for_fit(labels_by_folder, folders, packs, baseline_w, fit_l, "logistic")

    # Refit on all labelled shoots as the candidate production models
    full = fit_grid(labels_by_folder, folders, packs=packs)
    candidate_w = full[1] if full else baseline_w
    candidate_weighted = {"type": "weighted", "weights": candidate_w}
    candidate_log = fit_logistic(labels_by_folder, folders, packs=packs)
    after_in_sample = eval_weights(labels_by_folder, candidate_w, folders, packs=packs)
    after_in_sample_log = (eval_model(labels_by_folder, candidate_log, folders, packs=packs)
                           if candidate_log else None)

    # Prefer logistic only if it beats the weighted-sum LOSO on held-out data
    use_logistic = bool(candidate_log) and _macro_tuple(after_log) > _macro_tuple(after_macro)
    if use_logistic:
        candidate_model = candidate_log
        after_loso_macro = after_log
    else:
        candidate_model = candidate_weighted
        after_loso_macro = after_macro

    return {
        "before": before,
        "loso": loso,
        "loso_logistic": loso_log,
        "after_loso_macro": after_loso_macro,
        "after_loso_macro_grid": after_macro,
        "after_loso_macro_logistic": after_log,
        "candidate_weights": candidate_w,
        "candidate_model": candidate_model,
        "candidate_in_sample": after_in_sample,
        "candidate_in_sample_logistic": after_in_sample_log,
    }


def should_adopt(result) -> tuple[bool, str]:
    """Adopt only if held-out (LOSO) combined macro AUC improves; require export macro not worse."""
    before = result["before"]["overall"]
    after = result["after_loso_macro"]
    b = before.get("auc_combined_macro")
    a = after.get("auc_combined_macro")
    if a is None or b is None:
        return False, "missing combined macro AUC"
    kind = after.get("kind") or (result.get("candidate_model") or {}).get("type") or "weighted"
    if a > b + 1e-9:
        be, ae = before.get("auc_export_macro"), after.get("auc_export_macro")
        if be is not None and ae is not None and ae + 0.02 < be:
            return False, f"{kind} combined {b:.3f}->{a:.3f} but export macro dropped {be:.3f}->{ae:.3f}"
        return True, f"held-out combined macro {b:.3f} -> {a:.3f} ({kind})"
    return False, f"held-out combined macro {b:.3f} -> {a:.3f} ({kind}, not better)"
