"""Match Camera Exports back to Camera Imports and (re)calibrate the rank model.

Read-only on the NAS: never modify, move or delete anything there. Only dated
export folders that can be mapped to a shoot are listed; Archive and other
undated buckets are not walked. MP4/MOV are ignored.

Exports are positives; unexported frames in the same indexed shoot are
negatives. Star ratings of 3+ are also treated as positives when a snapshot
exists. Training uses only local index features (no images leave the Mac).
"""
from __future__ import annotations

import csv, itertools, json, math, os, re, sqlite3, subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HOME = Path.home() / "localai"
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

CAMERA_STEM = re.compile(r"^_?[A-Za-z]{1,8}\d[A-Za-z0-9_]{0,8}\d{2,}$|^_?[A-Za-z]{2,8}_?\d{3,6}$|^IMG[L_]?\d+$", re.I)
SUFFIX = re.compile(r"([\s._-](?:\d+|edit\d*|enhanced-?nr|copy\d*|output|export|edited|crop|web|full))$", re.I)
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
    import numpy as np
    s = np.asarray(scores, float)
    lab = np.asarray(labels, bool)
    pos, neg = s[lab], s[~lab]
    if not len(pos) or not len(neg):
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


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
        found.append({"path": str(path), "name": path.name, "date": date, "suffix": suffix, "source": source})

    for p in sorted(root.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name.startswith(".") or p.name.lower() in SKIP_SUBDIR:
            continue
        if YEAR_BUCKET.match(p.name):
            for c in sorted(p.iterdir(), key=lambda x: x.name):
                if c.is_dir() and not c.name.startswith("."):
                    consider(c, p.name)
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

def match_exports(export_folders, year_imports, index_by_folder, import_stem_cache=None):
    """Match each export image to a raw. Prefer the mapped import shoot, then the local index."""
    import_stem_cache = import_stem_cache if import_stem_cache is not None else {}
    # invert index: stem -> list of (folder, row); time+folder -> rows
    stem_index = defaultdict(list)
    time_index = defaultdict(list)          # (folder, exact time incl. subsec)
    time_second_index = defaultdict(list)   # (folder, YYYY-MM-DD HH:MM:SS)
    for fo, rows in index_by_folder.items():
        for r in rows:
            stem_index[r["stem"]].append((fo, r))
            if r["time"]:
                time_index[(fo, r["time"])].append(r)
                time_second_index[(fo, time_second(r["time"]))].append(r)

    results = []  # per export folder
    unmatched_for_exif = []

    for ef in export_folders:
        date, suffix = ef["date"], ef["suffix"]
        mapped = map_date_to_import(date, suffix, year_imports)
        year = date[:4]
        import_dir = (IMPORTS_ROOT / year / mapped) if mapped else None
        if mapped and mapped not in import_stem_cache:
            # Prefer the local index (no NAS listing) when this shoot is indexed.
            if mapped in index_by_folder:
                import_stem_cache[mapped] = {r["stem"]: r["filename"] for r in index_by_folder[mapped]}
            elif import_dir and import_dir.is_dir():
                import_stem_cache[mapped] = list_import_stems(import_dir)
            else:
                import_stem_cache[mapped] = {}
        stems = import_stem_cache.get(mapped or "", {})
        images = list(iter_export_images(Path(ef["path"])))
        recs = []
        seen_pos = set()
        for img in images:
            stem = canonical_stem(img.name)
            how = None
            target_folder = None
            raw_name = None
            if CAMERA_STEM.match(stem) and mapped and stem in stems:
                how, target_folder, raw_name = "stem", mapped, stems[stem]
            elif CAMERA_STEM.match(stem) and stem in stem_index:
                hits = stem_index[stem]
                # prefer the mapped folder if present
                pref = [h for h in hits if h[0] == mapped] or hits
                if len({h[0] for h in pref}) == 1:
                    how, target_folder, raw_name = "stem-index", pref[0][0], pref[0][1]["filename"]
                elif mapped and any(h[0] == mapped for h in hits):
                    h = next(h for h in hits if h[0] == mapped)
                    how, target_folder, raw_name = "stem", h[0], h[1]["filename"]
            rec = {
                "export_path": str(img), "export_name": img.name, "stem": stem,
                "how": how, "import_folder": target_folder, "raw_name": raw_name,
                "mapped_import": mapped,
            }
            recs.append(rec)
            if not how:
                unmatched_for_exif.append((ef, rec, img))
        results.append({"export": ef, "mapped_import": mapped, "images": recs, "n_images": len(images)})

    # EXIF fallback for still-unmatched (renamed exports, etc.)
    need = [img for _, rec, img in unmatched_for_exif if rec["how"] is None]
    exif = exif_for(need) if need else {}
    by_src = exif
    for ef, rec, img in unmatched_for_exif:
        if rec["how"]:
            continue
        info = by_src.get(str(img)) or by_src.get(img.name) or {}
        # exiftool json SourceFile is the full path
        if not info:
            # try any key ending with the path
            for k, v in by_src.items():
                if k.endswith(str(img)) or Path(k) == img:
                    info = v
                    break
        rec["exif_time"] = info.get("time")
        rec["exif_serial"] = info.get("serial")
        t = info.get("time")
        if not t:
            continue
        mapped = rec.get("mapped_import")

        def hits_for(folder, key, index):
            return list(index.get((folder, key), []))

        def unique_in(index, key):
            hits = []
            for fo in list(index_by_folder):
                hits.extend((fo, r) for r in index.get((fo, key), []))
            return hits

        candidates = []
        how = "exif-time"
        if mapped:
            candidates = hits_for(mapped, t, time_index)
        if not candidates:
            hits = unique_in(time_index, t)
            if len(hits) == 1:
                candidates, mapped = [hits[0][1]], hits[0][0]
        # same whole second, unique or uniquely closest in subseconds
        if not candidates:
            sec = time_second(t)
            pool = hits_for(mapped, sec, time_second_index) if mapped else []
            if not pool:
                hits = unique_in(time_second_index, sec)
                if hits and len({h[0] for h in hits}) == 1:
                    pool = [h[1] for h in hits]
                    mapped = hits[0][0]
            if len(pool) == 1:
                candidates = pool
            elif len(pool) > 1 and "." in t:
                def subsec(row):
                    tt = row.get("time") or ""
                    return int((tt.split(".")[1] + "00")[:2]) if "." in tt else 0
                want = int((t.split(".")[1] + "00")[:2])
                ranked = sorted(pool, key=lambda r: abs(subsec(r) - want))
                if abs(subsec(ranked[0]) - want) < abs(subsec(ranked[1]) - want):
                    candidates = [ranked[0]]
                    how = "exif-time-nearest"
        if len(candidates) == 1:
            r = candidates[0]
            rec["how"] = how
            rec["import_folder"] = r["folder"]
            rec["raw_name"] = r["filename"]
        elif len(candidates) > 1:
            rec["how"] = None
            rec["ambiguous_time"] = [c["filename"] for c in candidates]

    # Stem exists() fallback for leftovers (misfiled export folder, off-by-one dates).
    # Only probes import folders in the same year, one exists() per stem — no full listings.
    still = []
    for block in results:
        year = block["export"]["date"][:4]
        prefer = block["mapped_import"]
        # nearby date aliases (2026-07-12 -> 2026-07-11, 2026-08-20 -> 2026-08-29)
        for rec in block["images"]:
            if rec["how"]:
                continue
            if not CAMERA_STEM.match(rec["stem"]):
                continue
            found = resolve_stem_across_year(rec["stem"], year, year_imports, prefer=prefer,
                                                around=block["export"]["date"])
            if found:
                rec["how"] = "stem-exists"
                rec["import_folder"], rec["raw_name"] = found
            else:
                still.append(rec)
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
        per_export.append({
            "export_folder": ef["name"],
            "export_path": ef["path"],
            "date": ef["date"],
            "mapped_import": block["mapped_import"],
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


def build_labels(index_by_folder, positives, min_indexed=50, star_threshold=3):
    """For each indexed shoot that has exports: exported or 3★+ = positive, else negative."""
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
            starred = star >= star_threshold
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


def eval_weights(labels_by_folder, w, folders=None):
    """Per-shoot and overall AUCs for combined / export / star labels."""
    folders = folders or list(labels_by_folder)
    per, all_s, all_y, all_ye, all_ys = {}, [], [], [], []
    for fo in folders:
        recs = labels_by_folder[fo]
        s = [rank_score(r, w) for r in recs]
        y = [r["y"] for r in recs]
        ye = [r["y_export"] for r in recs]
        ys = [r["y_star"] for r in recs]
        per[fo] = {
            "n": len(recs),
            "auc_combined": _round_auc(_auc(s, y)),
            "auc_export": _round_auc(_auc(s, ye)),
            "auc_star": _round_auc(_auc(s, ys)),
            "positives_combined": int(sum(y)),
            "positives_export": int(sum(ye)),
            "positives_star": int(sum(ys)),
        }
        all_s += s; all_y += y; all_ye += ye; all_ys += ys
    overall = {
        "auc_combined_pooled": _round_auc(_auc(all_s, all_y)),
        "auc_export_pooled": _round_auc(_auc(all_s, all_ye)),
        "auc_star_pooled": _round_auc(_auc(all_s, all_ys)),
        "auc_combined_macro": _macro([per[fo]["auc_combined"] for fo in folders]),
        "auc_export_macro": _macro([per[fo]["auc_export"] for fo in folders]),
        "auc_star_macro": _macro([per[fo]["auc_star"] for fo in folders]),
        "n_frames": len(all_s),
        "n_shoots": len(folders),
    }
    return {"per_shoot": per, "overall": overall, "weights": w}


def _round_auc(x):
    return None if x != x else round(float(x), 3)  # NaN check


def _macro(xs):
    vals = [x for x in xs if x is not None]
    return None if not vals else round(sum(vals) / len(vals), 3)


def fit_grid(labels_by_folder, folders, metric="auc_combined"):
    """Grid-search weights maximising the mean per-shoot AUC on `folders`."""
    best = None
    keys = ("sharp", "aes", "burst_best", "burst_other", "blur")
    for vals in itertools.product(*[GRID[k] for k in keys]):
        w = dict(zip(keys, vals))
        ev = eval_weights(labels_by_folder, w, folders)
        # primary: macro combined AUC; tie-break export macro then pooled
        m = ev["overall"]["auc_combined_macro"]
        m2 = ev["overall"]["auc_export_macro"] or 0
        m3 = ev["overall"]["auc_combined_pooled"] or 0
        if m is None:
            continue
        score = (m, m2, m3)
        if best is None or score > best[0]:
            best = (score, w, ev)
    return best


def leave_one_shoot_out(labels_by_folder, baseline_w):
    folders = sorted(labels_by_folder)
    before = eval_weights(labels_by_folder, baseline_w, folders)
    loso = []
    held_scores = []  # new model scores on held-out shoots (for after overall)
    # For a fair 'after' overall, collect held-out predictions
    after_per = {}
    chosen_from_full = None
    for held in folders:
        train = [f for f in folders if f != held]
        if not train:
            continue
        fit = fit_grid(labels_by_folder, train)
        if not fit:
            continue
        _, w, _ = fit
        ev_held = eval_weights(labels_by_folder, w, [held])
        ev_base = eval_weights(labels_by_folder, baseline_w, [held])
        rec = {
            "held_out": held,
            "weights": w,
            "after": ev_held["per_shoot"][held],
            "before": ev_base["per_shoot"][held],
        }
        loso.append(rec)
        after_per[held] = ev_held["per_shoot"][held]
    after_macro = {
        "auc_combined_macro": _macro([after_per[f]["auc_combined"] for f in after_per]),
        "auc_export_macro": _macro([after_per[f]["auc_export"] for f in after_per]),
        "auc_star_macro": _macro([after_per[f]["auc_star"] for f in after_per]),
        "n_shoots": len(after_per),
    }
    # Refit on all labelled shoots as the candidate production weights
    full = fit_grid(labels_by_folder, folders)
    candidate_w = full[1] if full else baseline_w
    after_in_sample = eval_weights(labels_by_folder, candidate_w, folders)
    return {
        "before": before,
        "loso": loso,
        "after_loso_macro": after_macro,
        "candidate_weights": candidate_w,
        "candidate_in_sample": after_in_sample,
    }


def should_adopt(result) -> tuple[bool, str]:
    """Adopt only if held-out (LOSO) combined macro AUC improves; require export macro not worse."""
    before = result["before"]["overall"]
    after = result["after_loso_macro"]
    b = before.get("auc_combined_macro")
    a = after.get("auc_combined_macro")
    if a is None or b is None:
        return False, "missing combined macro AUC"
    if a > b + 1e-9:
        # don't adopt a win on combined that clearly hurts export prediction
        be, ae = before.get("auc_export_macro"), after.get("auc_export_macro")
        if be is not None and ae is not None and ae + 0.02 < be:
            return False, f"combined {b:.3f}->{a:.3f} but export macro dropped {be:.3f}->{ae:.3f}"
        return True, f"held-out combined macro {b:.3f} -> {a:.3f}"
    return False, f"held-out combined macro {b:.3f} -> {a:.3f} (not better)"
