"""Auto-queue new shoots (Trips Local AI panel). OFF by default.

Every ~30 minutes (in step with Capture One's referencing of new imports) look in
[03] Photos/Camera Imports/<year>/ for new YYYY-MM-DD... folders. A new folder is queued once
  - Capture One already has it (checked read-only, only while no job is using the catalog), or
  - it is stable: same image count/size on two polls in a row and nothing written for >= stable_min.
Folders that existed when auto-queue was switched on (the baseline) and folders that already have a
job are never queued. Queued jobs use the chosen default profile; MP4/MOV files are ignored as usual.
Settings: gui/autoqueue.json; state: gui/autoqueue-state.json.
"""
from __future__ import annotations
import json, re, subprocess, threading, time
from pathlib import Path

HOME = Path.home() / "localai"
GUI = HOME / "gui"
CFG = GUI / "autoqueue.json"
STATE = GUI / "autoqueue-state.json"
IMPORTS = Path("/Volumes/Photos/Camera Imports")
SHOOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
IMAGE_EXT = {".cr3", ".cr2", ".dng", ".arw", ".nef", ".raf", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".png"}
DEFAULTS = {"enabled": False, "profile_id": "gig-standard", "poll_min": 30, "stable_min": 20,
            "preview_before_apply": False}
_LOCK = threading.Lock()


def load_cfg() -> dict:
    try:
        return {**DEFAULTS, **json.load(open(CFG))}
    except Exception:
        return dict(DEFAULTS)


def save_cfg(cfg: dict):
    json.dump({k: cfg.get(k, v) for k, v in DEFAULTS.items()}, open(CFG, "w"), indent=2)


def _load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {"baseline": [], "seen": {}, "queued": {}, "events": []}


def _save_state(st):
    st["events"] = st.get("events", [])[-50:]
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=1)); tmp.replace(STATE)


def _event(st, msg):
    st.setdefault("events", []).append(time.strftime("%Y-%m-%d %H:%M ") + msg)


def year_dirs(now=None):
    y = time.localtime(now).tm_year
    return [IMPORTS / str(y)] + ([IMPORTS / str(y - 1)] if time.localtime(now).tm_mon == 1 else [])


def shoot_folders(now=None):
    out = []
    for yd in year_dirs(now):
        if yd.is_dir():
            out += [p for p in sorted(yd.iterdir()) if p.is_dir() and SHOOT_RE.match(p.name)]
    return out


def signature(folder: Path):
    n = size = 0; newest = 0.0
    for p in folder.iterdir():
        if p.name.startswith(".") or p.suffix.lower() not in IMAGE_EXT:
            continue
        try:
            s = p.stat()
        except OSError:
            continue
        n += 1; size += s.st_size; newest = max(newest, s.st_mtime)
    return {"n": n, "size": size, "newest": newest}


def c1_has_folder(folder: Path, first_image: str | None) -> bool | None:
    """Read-only: does the Capture One catalog hold this folder's first image? None = could not check."""
    if not first_image:
        return None
    js = ("function run(a){const d=Application('Capture One').currentDocument();"
          "return d.images.whose({path:a[0]})().length;}")
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js, str(folder / first_image)],
                           capture_output=True, text=True, timeout=120)
        return int(r.stdout.strip() or 0) > 0 if r.returncode == 0 else None
    except Exception:
        return None


def enable(cfg_update: dict) -> dict:
    """Apply settings; switching ON records today's folders as the baseline (never queued)."""
    with _LOCK:
        cfg = load_cfg(); was = cfg["enabled"]
        for k in DEFAULTS:
            if k in cfg_update:
                cfg[k] = type(DEFAULTS[k])(cfg_update[k])
        cfg["poll_min"] = max(5, min(240, int(cfg["poll_min"])))
        cfg["stable_min"] = max(5, min(240, int(cfg["stable_min"])))
        save_cfg(cfg)
        st = _load_state()
        if cfg["enabled"] and not was:
            st["baseline"] = sorted(set(st.get("baseline", [])) | {p.name for p in shoot_folders()})
            st["enabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S"); st["last_poll"] = 0
            _event(st, f"switched on; {len(st['baseline'])} existing folders left alone")
        elif was and not cfg["enabled"]:
            _event(st, "switched off")
        _save_state(st)
        return status()


def status() -> dict:
    cfg = load_cfg(); st = _load_state()
    nxt = (st.get("last_poll") or 0) + cfg["poll_min"] * 60
    return {**cfg, "nas_mounted": IMPORTS.is_dir(), "last_poll": st.get("last_poll_human"),
            "next_poll": time.strftime("%H:%M", time.localtime(nxt)) if cfg["enabled"] and st.get("last_poll") else None,
            "waiting": {k: v for k, v in (st.get("seen") or {}).items() if k not in (st.get("queued") or {})},
            "queued": st.get("queued") or {}, "events": (st.get("events") or [])[-10:]}


def poll(enqueue, existing_folders, catalog_busy, force=False, now=None) -> dict:
    """One poll. enqueue(folder_path, cfg) -> job id; existing_folders() -> set of folder paths with jobs."""
    with _LOCK:
        cfg = load_cfg()
        if not cfg["enabled"]:
            return {"skipped": "off"}
        now = now or time.time()
        st = _load_state()
        if not force and now - (st.get("last_poll") or 0) < cfg["poll_min"] * 60 - 5:
            return {"skipped": "not due"}
        st["last_poll"] = now; st["last_poll_human"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        if not IMPORTS.is_dir():
            _event(st, "NAS not mounted; nothing checked"); _save_state(st); return {"skipped": "nas not mounted"}
        base = set(st.get("baseline") or []); queued = st.setdefault("queued", {}); seen = st.setdefault("seen", {})
        have_jobs = existing_folders()
        done = []
        for f in shoot_folders(now):
            if f.name in base or f.name in queued:
                continue
            if str(f) in have_jobs:
                queued[f.name] = "existing job"; continue
            sig = signature(f)
            prev = seen.get(f.name)
            entry = {"folder": str(f), "sig": sig, "first_seen": (prev or {}).get("first_seen") or
                     time.strftime("%Y-%m-%d %H:%M", time.localtime(now))}
            if not sig["n"]:
                entry["why_waiting"] = "no images yet"; seen[f.name] = entry; continue
            stable = bool(prev) and prev.get("sig") == sig and now - sig["newest"] >= cfg["stable_min"] * 60
            in_c1 = None
            if not stable and not catalog_busy():
                first = next((p.name for p in sorted(f.iterdir()) if p.suffix.lower() in IMAGE_EXT
                              and not p.name.startswith(".")), None)
                in_c1 = c1_has_folder(f, first)
            if stable or in_c1:
                reason = "in Capture One" if in_c1 else "folder stable"
                try:
                    jid = enqueue(f, cfg)
                    queued[f.name] = jid; seen.pop(f.name, None)
                    _event(st, f"queued {f.name} ({sig['n']} images, {reason}) as {jid}"); done.append(f.name)
                except Exception as e:
                    entry["why_waiting"] = f"queue failed: {e}"; seen[f.name] = entry
                    _event(st, f"could not queue {f.name}: {e}")
            else:
                entry["why_waiting"] = ("still changing" if prev and prev.get("sig") != sig else
                                        "waiting for a second look / Capture One referencing")
                seen[f.name] = entry
        _save_state(st)
        return {"queued": done}


def start_thread(enqueue, existing_folders, catalog_busy, tick=60):
    def loop():
        while True:
            try:
                poll(enqueue, existing_folders, catalog_busy)
            except Exception as e:
                try:
                    st = _load_state(); _event(st, f"poll error: {e}"); _save_state(st)
                except Exception:
                    pass
            time.sleep(tick)
    t = threading.Thread(target=loop, name="localai-autoqueue", daemon=True); t.start(); return t
