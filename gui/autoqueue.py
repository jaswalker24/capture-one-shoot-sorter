"""Auto-queue new shoots (Trips Local AI for Lightroom panel). OFF by default.

Every poll_min minutes look in
Camera Imports/<year>/ for YYYY-MM-DD… folders newer than the last-run cutoff.

A folder is NEW when:
  - its YYYY-MM-DD name is a later date than the cutoff date, or
  - it is a same-day folder (name date == cutoff date) created on the NAS after the cutoff time
    (folder birth/creation, or the oldest image if birth is unavailable). Existing same-day folders
    that were already on disk at the cutoff are left alone even if more files arrive later.

A new folder is queued only when it is stable: the same image count/size on two
polls in a row and nothing written for >= stable_min. There is no Lightroom
catalog check (this edition writes XMP sidecars, which does not need Lightroom open).

Folders already run by auto-queue (`ran`) and folders that already have a job are never re-queued.
Queued jobs use the chosen default profile and its catalog passes (Draft Captions is never included).
MP4/MOV files are ignored as usual. After a successful auto-run the cutoff advances to that job's
queue time (so a folder that arrived while the job was running is not missed).

Settings: gui/autoqueue.json (cutoff is YYYY-MM-DDTHH:MM in Europe/London, the M5 clock);
state: gui/autoqueue-state.json. The toggle stays off until you switch it on.
"""
from __future__ import annotations
import json, re, subprocess, threading, time
from datetime import datetime
import os
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
GUI = HOME / "gui"
CFG = GUI / "autoqueue.json"
STATE = GUI / "autoqueue-state.json"
def _imports_root():
    try:
        import nas_paths as NP
        NP.HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
        return NP.imports_root()
    except Exception:
        return Path("/Volumes/Photos/Camera Imports")

IMPORTS = _imports_root()


def reload_roots():
    """Pick up a new imports_root from config without restarting the panel."""
    global IMPORTS
    IMPORTS = _imports_root()

SHOOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
IMAGE_EXT = {".cr3", ".cr2", ".dng", ".arw", ".nef", ".raf", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".png"}
DEFAULTS = {"enabled": False, "profile_id": "gig-standard", "poll_min": 30, "stable_min": 20,
            "preview_before_apply": False, "cutoff": ""}
CUTOFF_FMT = "%Y-%m-%dT%H:%M"
HISTORY_DIRS = (GUI / "jobs", GUI / "history")
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
        return {"baseline": [], "seen": {}, "queued": {}, "ran": {}, "events": []}


def _save_state(st):
    st["events"] = st.get("events", [])[-50:]
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=1)); tmp.replace(STATE)


def _event(st, msg):
    st.setdefault("events", []).append(time.strftime("%Y-%m-%d %H:%M ") + msg)


# ---------------------------------------------------------------- last-run cutoff
def parse_cutoff(v) -> float | None:
    """'YYYY-MM-DDTHH:MM' (or with a space / seconds) in local (Europe/London) time -> epoch."""
    v = str(v or "").strip().replace(" ", "T")
    for fmt, n in ((CUTOFF_FMT, 16), (CUTOFF_FMT + ":%S", 19)):
        try:
            return datetime.strptime(v[:n], fmt).timestamp()
        except ValueError:
            continue
    return None


def fmt_cutoff(ts: float) -> str:
    return time.strftime(CUTOFF_FMT, time.localtime(ts))


def ensure_cutoff(now=None) -> str:
    """First use: cutoff = now, so everything already on the NAS is left alone."""
    cfg = load_cfg()
    if parse_cutoff(cfg.get("cutoff")) is None:
        cfg["cutoff"] = fmt_cutoff(now or time.time()); save_cfg(cfg)
        st = _load_state(); _event(st, f"last-run cutoff initialised to {cfg['cutoff']}"); _save_state(st)
    return cfg["cutoff"]


def set_cutoff(value: str) -> dict:
    """Set the last-run cutoff (YYYY-MM-DDTHH:MM, Europe/London)."""
    return enable({"cutoff": value})


def _birth(p: Path) -> float:
    s = p.stat()
    return getattr(s, "st_birthtime", None) or s.st_ctime


def _appeared(folder: Path) -> float | None:
    """When this shoot folder showed up on the NAS (folder birth, else oldest image)."""
    try:
        t = _birth(folder)
    except OSError:
        t = None
    oldest = None
    try:
        for p in folder.iterdir():
            if p.name.startswith(".") or p.suffix.lower() not in IMAGE_EXT:
                continue
            try:
                b = _birth(p)
            except OSError:
                continue
            oldest = b if oldest is None else min(oldest, b)
    except OSError:
        pass
    if t and oldest:
        # Prefer folder birth; if it's wildly after the files (copied tree), still use folder birth
        # because that is when the shoot arrived. If folder birth is missing, use oldest file.
        return t
    return t or oldest


def newer_reason(folder: Path, cutoff_ts: float) -> str | None:
    cut_date = time.strftime("%Y-%m-%d", time.localtime(cutoff_ts))
    name_date = folder.name[:10]
    if name_date > cut_date:
        return f"dated {name_date}, after cutoff day"
    if name_date < cut_date:
        return None  # older shoot date, even if copied later
    appeared = _appeared(folder)
    if appeared is not None and appeared > cutoff_ts:
        return f"same-day folder created {time.strftime('%Y-%m-%d %H:%M', time.localtime(appeared))}, after cutoff"
    return None


def is_newer(folder: Path, cutoff_ts: float) -> bool:
    return newer_reason(folder, cutoff_ts) is not None


def _job_record(jid: str) -> dict | None:
    for d in HISTORY_DIRS:
        p = d / f"{jid}.json"
        if p.exists():
            try:
                return json.load(open(p))
            except Exception:
                return None
    return None


def _job_outcome(jid: str, depth=0) -> str:
    """'ok' | 'failed' | 'pending' for an auto-queued job (follows preview -> approved apply job)."""
    j = _job_record(jid)
    if not j or depth > 3:
        return "pending" if not j else "failed"
    stt = j.get("status")
    if stt in ("queued", "running", "paused", "awaiting_approval"):
        return "pending"
    if stt == "done" and j.get("approved_job"):
        return _job_outcome(j["approved_job"], depth + 1)
    if stt == "done" and j.get("preview_discarded"):
        return "failed"
    if stt == "error" or stt == "cancelled":
        return "failed"
    if stt == "done" and not (j.get("verify") or {}).get("rating_or_colour_changed"):
        return "ok"
    return "failed"


def settle_finished(st: dict) -> list:
    """Record finished auto-runs in 'ran'; advance the cutoff past successful ones."""
    ran = st.setdefault("ran", {}); moved = []
    cfg = load_cfg(); cut = parse_cutoff(cfg.get("cutoff")) or 0
    for name, jid in list((st.get("queued") or {}).items()):
        if name in ran or not isinstance(jid, str) or jid == "existing job":
            continue
        out = _job_outcome(jid)
        if out == "pending":
            continue
        j = _job_record(jid) or {}
        ran[name] = {"job": jid, "result": out,
                     "finished": j.get("finished") or time.strftime("%Y-%m-%dT%H:%M:%S")}
        if out == "ok":
            created = parse_cutoff(j.get("created")) or time.time()
            if created > cut:
                cut = created; moved.append(name)
        _event(st, f"auto-run {name} {('finished' if out == 'ok' else 'did not finish cleanly')} ({jid})")
    if moved:
        cfg["cutoff"] = fmt_cutoff(cut); save_cfg(cfg)
        _event(st, f"last-run cutoff advanced to {cfg['cutoff']} after {', '.join(moved)}")
    return moved


def note_job(job=None):
    """Hook from the panel when a job is archived; advances the cutoff for finished auto-runs."""
    with _LOCK:
        st = _load_state()
        settle_finished(st)
        _save_state(st)


def preview(existing_folders=lambda: set(), now=None) -> list:
    """Dry run, filesystem only (no Capture One call): folders the next poll would consider."""
    ensure_cutoff()
    cfg = load_cfg(); st = _load_state()
    cut = parse_cutoff(cfg.get("cutoff"))
    if cut is None or not IMPORTS.is_dir():
        return []
    have = existing_folders(); ran = st.get("ran") or {}; queued = st.get("queued") or {}
    out = []
    for f in shoot_folders(now, since=cut):
        why = newer_reason(f, cut)
        if not why and f.name not in (st.get("seen") or {}):
            continue
        if f.name in ran or f.name in queued or str(f).rstrip("/") in have:
            continue
        sig = signature(f)
        out.append({"folder": f.name, "path": str(f), "why": why or "already being watched",
                    "images": sig["n"],
                    "newest_file": (time.strftime("%Y-%m-%d %H:%M", time.localtime(sig["newest"]))
                                    if sig["newest"] else None),
                    "waiting": ((st.get("seen") or {}).get(f.name) or {}).get("why_waiting")})
    return out


def year_dirs(now=None, since=None):
    y = time.localtime(now).tm_year
    y0 = time.localtime(since).tm_year if since else (y - 1 if time.localtime(now).tm_mon == 1 else y)
    if y0 > y:
        y0 = y
    return [IMPORTS / str(yy) for yy in range(y, y0 - 1, -1)]


def shoot_folders(now=None, since=None):
    out = []
    for yd in year_dirs(now, since):
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


def _first_image(folder: Path) -> str | None:
    return next((p.name for p in sorted(folder.iterdir())
                 if p.suffix.lower() in IMAGE_EXT and not p.name.startswith(".")), None)


def enable(cfg_update: dict) -> dict:
    """Apply settings; switching ON leaves older-than-cutoff folders alone."""
    with _LOCK:
        cfg = load_cfg(); was = cfg["enabled"]; cutoff_changed = False
        for k in DEFAULTS:
            if k == "cutoff":
                continue
            if k in cfg_update:
                cfg[k] = type(DEFAULTS[k])(cfg_update[k])
        cfg["poll_min"] = max(5, min(240, int(cfg["poll_min"])))
        cfg["stable_min"] = max(5, min(240, int(cfg["stable_min"])))
        if cfg_update.get("cutoff_now"):
            cfg["cutoff"] = fmt_cutoff(time.time()); cutoff_changed = True
        elif "cutoff" in cfg_update and cfg_update.get("cutoff") not in (None, ""):
            ts = parse_cutoff(cfg_update["cutoff"])
            if ts is None:
                raise ValueError("cutoff must be YYYY-MM-DDTHH:MM")
            cfg["cutoff"] = fmt_cutoff(ts); cutoff_changed = True
        elif parse_cutoff(cfg.get("cutoff")) is None:
            cfg["cutoff"] = fmt_cutoff(time.time()); cutoff_changed = True
        save_cfg(cfg)
        st = _load_state()
        if cutoff_changed:
            _event(st, f"last-run cutoff set to {cfg['cutoff']} (by hand)")
            cut = parse_cutoff(cfg["cutoff"])
            for name, e in list((st.get("seen") or {}).items()):
                if not is_newer(Path(e.get("folder") or name), cut):
                    st["seen"].pop(name, None)
        if cfg["enabled"] and not was:
            cut = parse_cutoff(cfg["cutoff"]) or time.time()
            older = [p.name for p in shoot_folders() if not is_newer(p, cut)]
            st["baseline"] = sorted(set(st.get("baseline", [])) | set(older))
            st["enabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S"); st["last_poll"] = 0
            _event(st, f"switched on; last-run cutoff {cfg['cutoff']}; {len(older)} older folders left alone")
        elif was and not cfg["enabled"]:
            _event(st, "switched off")
        _save_state(st)
        return status()


def status() -> dict:
    if parse_cutoff(load_cfg().get("cutoff")) is None:
        ensure_cutoff()
    cfg = load_cfg(); st = _load_state()
    nxt = (st.get("last_poll") or 0) + cfg["poll_min"] * 60
    return {**cfg, "nas_mounted": IMPORTS.is_dir(), "last_poll": st.get("last_poll_human"),
            "next_poll": time.strftime("%H:%M", time.localtime(nxt)) if cfg["enabled"] and st.get("last_poll") else None,
            "waiting": {k: v for k, v in (st.get("seen") or {}).items() if k not in (st.get("queued") or {})},
            "queued": st.get("queued") or {}, "ran": st.get("ran") or {},
            "cutoff": cfg.get("cutoff") or "",
            "cutoff_tz": "Europe/London (M5 clock: " + time.strftime("%Z") + ")",
            "events": (st.get("events") or [])[-10:]}


def poll(enqueue, existing_folders, catalog_busy=None, force=False, now=None) -> dict:
    """One poll. enqueue(folder_path, cfg) -> job id; existing_folders() -> set of folder paths with jobs.

    catalog_busy is accepted for compatibility with the panel and ignored: this
    edition does not wait on a catalog.
    """
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
        settle_finished(st)
        cut = parse_cutoff(load_cfg().get("cutoff"))
        if cut is None:
            cut = now; cfg["cutoff"] = fmt_cutoff(now); save_cfg(cfg)
            _event(st, f"last-run cutoff initialised to {cfg['cutoff']}")
        queued = st.setdefault("queued", {}); seen = st.setdefault("seen", {})
        ran = st.setdefault("ran", {})
        base = set(st.get("baseline") or [])
        have_jobs = existing_folders()
        done = []
        for f in shoot_folders(now, since=cut):
            if f.name in queued or f.name in ran:
                continue
            if f.name not in seen and (f.name in base or not is_newer(f, cut)):
                continue
            if str(f).rstrip("/") in have_jobs:
                queued[f.name] = "existing job"; continue
            sig = signature(f)
            prev = seen.get(f.name)
            entry = {"folder": str(f), "sig": sig, "first_seen": (prev or {}).get("first_seen") or
                     time.strftime("%Y-%m-%d %H:%M", time.localtime(now))}
            if not sig["n"]:
                entry["why_waiting"] = "no images yet"; seen[f.name] = entry; continue
            stable = bool(prev) and prev.get("sig") == sig and now - sig["newest"] >= cfg["stable_min"] * 60
            if stable:
                reason = "folder is stable"
                try:
                    jid = enqueue(f, cfg)
                    queued[f.name] = jid; seen.pop(f.name, None)
                    _event(st, f"queued {f.name} ({sig['n']} images, {reason}) as {jid}"); done.append(f.name)
                except Exception as e:
                    entry["why_waiting"] = f"queue failed: {e}"; seen[f.name] = entry
                    _event(st, f"could not queue {f.name}: {e}")
            else:
                entry["why_waiting"] = ("still changing" if prev and prev.get("sig") != sig else
                                        "waiting for a second look")
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
