"""Needs-review queues for the Trips Local AI panel.

kind="numbers" (default): unsure race-number proposals.
kind="focus": AF focus check frames flagged General/Focus/Missed by afpoints.py (never auto-applied).

Reads out/<shoot>/<profile>-proposals-full.json (needs_review rows), stores review decisions in
out/<shoot>/review-decisions.json and applies ONLY confirmed/fixed numbers through review_apply.py
(additive Motorsport/Car Number/<n> keywords, snapshot before, stars/colours checked after).
Unreviewed or rejected rows are never applied.
"""
from __future__ import annotations
import json, os, re, subprocess, threading, time
from pathlib import Path

HOME = Path.home() / "localai"
OUT = HOME / "out"
CACHE = HOME / "cache"
THUMBS = CACHE / "_review"
EXIFTOOL = HOME / "bin" / "exiftool"
_LOCK = threading.Lock()
_DATA: dict = {}   # shoot -> (mtime, rows)
NUM_RE = re.compile(r"^\d{1,3}$")


def _safe_shoot(shoot: str) -> Path:
    p = (OUT / shoot).resolve()
    if p.parent != OUT.resolve() or not p.is_dir():
        raise ValueError("unknown shoot")
    return p


KINDS = {
    "numbers": {"file": "motorsport-proposals-full.json", "decisions": "review-decisions.json",
                "applied": "review-applied.json", "last": "review-apply-last.json"},
    "focus": {"file": "afpoints-proposals.json", "decisions": "review-focus-decisions.json",
              "applied": "review-focus-applied.json", "last": "review-focus-apply-last.json"},
}
FOCUS_KW = "General/Focus/Missed"


def _kind(kind) -> str:
    kind = kind or "numbers"
    if kind not in KINDS:
        raise ValueError("unknown review kind")
    return kind


def _prop_file(d: Path, kind: str = "numbers") -> Path | None:
    f = d / KINDS[_kind(kind)]["file"]
    return f if f.exists() else None


def shoots(kind: str = "numbers"):
    kind = _kind(kind)
    out = []
    for d in sorted(OUT.iterdir(), key=lambda x: x.name, reverse=True):
        if d.is_dir() and _prop_file(d, kind):
            try:
                rows = _rows(d.name, kind)
                if kind == "focus" and not rows:
                    continue
                dec = _decisions(d, kind)
                done = sum(1 for r in rows if r["filename"] in dec)
                out.append({"shoot": d.name, "needs_review": len(rows), "reviewed": done})
            except Exception as e:
                out.append({"shoot": d.name, "error": str(e)})
    return out


def _candidates(r):
    nums = []
    def add(n, src, conf=None):
        n = str(n or "").strip()
        if NUM_RE.match(n):
            n = str(int(n))
            for c in nums:
                if c["number"] == n:
                    c["sources"].append(src); return
            nums.append({"number": n, "sources": [src], "conf": conf})
    for k in r.get("tentative_keywords") or []:
        if k.startswith("Motorsport/Car Number/"):
            add(k.rsplit("/", 1)[1], "tentative")
    add(r.get("race_number"), "combined")
    add(r.get("vlm_number"), "vlm")
    for o in sorted(r.get("car_numbers") or [], key=lambda x: -(x.get("conf") or 0)):
        add(o.get("number"), "ocr", o.get("conf"))
    return nums


def _rows(shoot: str, kind: str = "numbers"):
    kind = _kind(kind)
    d = _safe_shoot(shoot); pf = _prop_file(d, kind)
    if not pf:
        raise ValueError("no proposals of this kind for shoot")
    m = pf.stat().st_mtime
    key = (shoot, kind)
    with _LOCK:
        if key in _DATA and _DATA[key][0] == m:
            return _DATA[key][1]
    data = json.load(open(pf))
    folder = data.get("folder", "")
    rows = []
    for r in data.get("images", []):
        if not r.get("needs_review") or r.get("error"):
            continue
        fn = r["filename"]
        if fn.lower().endswith((".mp4", ".mov")):
            continue
        if kind == "focus":
            rows.append({"filename": fn, "sha256": r.get("sha256"), "folder": folder,
                         "proposed": [FOCUS_KW], "candidates": [], "reasons": r.get("review_reasons") or [],
                         "af_box": r.get("af_box"), "af_sharp": r.get("af_sharp"),
                         "other_sharp": r.get("other_sharp"), "ratio": r.get("ratio")})
            continue
        cands = _candidates(r)
        rows.append({"filename": fn, "sha256": r.get("sha256"), "folder": folder,
                     "proposed": [c["number"] for c in cands[:1]], "candidates": cands,
                     "number_source": r.get("number_source"), "reasons": r.get("review_reasons") or [],
                     "car_colour": r.get("car_colour") or r.get("vlm_colour"),
                     "is_car_conf": r.get("is_car_conf"),
                     "already_confident": [k for k in r.get("proposed_keywords") or [] if "Car Number/" in k]})
    rows.sort(key=lambda x: x["filename"])
    with _LOCK:
        _DATA[key] = (m, rows)
    return rows


def _decisions(d: Path, kind: str = "numbers") -> dict:
    f = d / KINDS[_kind(kind)]["decisions"]
    return json.load(open(f)).get("items", {}) if f.exists() else {}


def _applied(d: Path, kind: str = "numbers") -> dict:
    f = d / KINDS[_kind(kind)]["applied"]
    return json.load(open(f)).get("items", {}) if f.exists() else {}


def queue(shoot: str, status: str = "pending", only_numbers: bool = False, kind: str = "numbers"):
    kind = _kind(kind)
    d = _safe_shoot(shoot); rows = _rows(shoot, kind); dec = _decisions(d, kind); app = _applied(d, kind)
    items = []
    for r in rows:
        if kind == "numbers" and only_numbers and not r["candidates"]:
            continue
        x = dict(r); x["decision"] = dec.get(r["filename"]); x["applied"] = app.get(r["filename"])
        st = "pending" if not x["decision"] else x["decision"]["action"]
        x["status"] = st
        if status == "all" or status == st or (status == "reviewed" and st != "pending"):
            items.append(x)
    counts = {"total": len(rows), "pending": 0, "confirm": 0, "fix": 0, "reject": 0, "skip": 0}
    for r in rows:
        dd = dec.get(r["filename"]); counts["pending" if not dd else dd["action"]] = counts.get("pending" if not dd else dd["action"], 0) + 1
    if kind == "focus":
        to_apply = sum(1 for fn, dd in dec.items() if dd["action"] == "confirm" and fn not in app)
    else:
        to_apply = sum(1 for fn, dd in dec.items() if dd["action"] in ("confirm", "fix") and dd.get("numbers")
                       and sorted(app.get(fn, {}).get("numbers", [])) != sorted(dd["numbers"]))
    counts["ready_to_apply"] = to_apply
    counts["applied"] = len(app)
    return {"shoot": shoot, "kind": kind, "items": items, "counts": counts, "apply": apply_status(shoot, kind)}


def decide(shoot: str, filename: str, action: str, numbers=None, kind: str = "numbers"):
    kind = _kind(kind)
    d = _safe_shoot(shoot)
    if action not in ("confirm", "fix", "reject", "skip", "undo"):
        raise ValueError("bad action")
    rows = {r["filename"]: r for r in _rows(shoot, kind)}
    if filename not in rows:
        raise ValueError("not in review queue")
    nums = []
    if kind == "focus":
        if action == "fix":
            raise ValueError("focus frames are confirm (add Focus/Missed) or reject only")
    elif action == "confirm":
        nums = rows[filename]["proposed"]
        if not nums:
            raise ValueError("nothing proposed to confirm; type the number to fix")
    elif action == "fix":
        raw = numbers if isinstance(numbers, list) else re.split(r"[ ,;/]+", str(numbers or ""))
        nums = []
        for n in raw:
            n = str(n).strip()
            if not n: continue
            if not NUM_RE.match(n):
                raise ValueError(f"car numbers must be 0-999 (got {n!r})")
            if str(int(n)) not in nums: nums.append(str(int(n)))
        if not nums:
            raise ValueError("type at least one number")
    f = d / KINDS[kind]["decisions"]
    with _LOCK:
        data = json.load(open(f)) if f.exists() else {"items": {}}
        if action == "undo":
            data["items"].pop(filename, None)
        else:
            data["items"][filename] = {"action": action, "numbers": nums, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=1)); tmp.replace(f)
    return data["items"].get(filename)


def thumb(shoot: str, filename: str, kind: str = "numbers") -> Path | None:
    kind = _kind(kind)
    rows = {r["filename"]: r for r in _rows(shoot, kind)}
    r = rows.get(filename)
    if not r or not r.get("sha256"):
        return None
    key = r["sha256"][:24]
    THUMBS.mkdir(parents=True, exist_ok=True)
    if kind == "focus" and r.get("af_box"):
        # preview with the camera's AF box drawn (red), via afpoints.draw
        t = THUMBS / f"{key}-af.jpg"
        if t.exists():
            return t
        import sys
        sys.path.insert(0, str(HOME))
        import afpoints
        f = afpoints.draw({"filename": filename, "sha256": r["sha256"], "box": r["af_box"], "focus": "Missed",
                           "af_sharp": r.get("af_sharp") or 0, "other_sharp": r.get("other_sharp") or 0,
                           "ratio": r.get("ratio"), "af_state": "AF box"}, r["folder"], THUMBS)
        Path(f).replace(t)
        return t
    t = THUMBS / f"{key}.jpg"
    if t.exists():
        return t
    src = CACHE / f"{key}.jpg"
    tmp_prev = None
    if not src.exists():
        orig = Path(r["folder"]) / filename          # read-only: extract embedded preview
        if not orig.exists():
            return None
        tmp_prev = THUMBS / f"{key}.prev.jpg"
        with open(tmp_prev, "wb") as fh:
            subprocess.run([str(EXIFTOOL), "-b", "-JpgFromRaw", str(orig)], stdout=fh, timeout=60)
        if tmp_prev.stat().st_size < 1000:
            with open(tmp_prev, "wb") as fh:
                subprocess.run([str(EXIFTOOL), "-b", "-PreviewImage", str(orig)], stdout=fh, timeout=60)
        src = tmp_prev
    try:
        from PIL import Image
        im = Image.open(src); im.thumbnail((1600, 1600)); im.convert("RGB").save(t, "JPEG", quality=85)
    finally:
        if tmp_prev and tmp_prev.exists():
            tmp_prev.unlink()
    return t


# ---------------------------------------------------------------- apply (background)
_APPLY: dict = {}


def apply_status(shoot: str, kind: str = "numbers"):
    kind = _kind(kind)
    s = _APPLY.get((shoot, kind))
    if not s:
        f = OUT / shoot / KINDS[kind]["last"]
        return json.load(open(f)) if f.exists() else None
    if s.get("proc") and s["proc"].poll() is not None and s.get("status") == "running":
        s["status"] = "done" if s["proc"].returncode == 0 else "error"
        s["rc"] = s["proc"].returncode
    try:
        s["log_tail"] = Path(s["log"]).read_text().splitlines()[-15:]
    except Exception:
        pass
    return {k: v for k, v in s.items() if k != "proc"}


def catalog_busy():
    if subprocess.run(["pgrep", "-f", "fullrun.py"], capture_output=True).returncode == 0:
        return "a full run is applying keywords"
    if subprocess.run(["pgrep", "-f", "jobrunner.py"], capture_output=True).returncode == 0:
        return "a panel job is running"
    if subprocess.run(["pgrep", "-f", "review_apply.py"], capture_output=True).returncode == 0:
        return "a review apply is already running"
    return None


def start_apply(shoot: str, kind: str = "numbers"):
    kind = _kind(kind)
    _safe_shoot(shoot)
    busy = catalog_busy()
    if busy:
        raise RuntimeError(f"Capture One is busy ({busy}); try again when it finishes")
    log = HOME / "logs" / "gui" / f"review-apply-{kind}-{shoot}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(HOME / "venv/bin/python"), str(HOME / "review_apply.py"), shoot, "--apply", "--kind", kind],
                            cwd=str(HOME), stdout=open(log, "w"), stderr=subprocess.STDOUT)
    _APPLY[(shoot, kind)] = {"status": "running", "proc": proc, "pid": proc.pid, "log": str(log),
                             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return apply_status(shoot, kind)
