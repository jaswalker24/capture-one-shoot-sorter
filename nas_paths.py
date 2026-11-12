#!/usr/bin/env python3
"""NAS photo-folder roots used by the control panel, jobrunner and training.

Reads config.json (and optional env overrides). Saving from the Settings
Locations section writes only these keys, after a timestamped backup.
Never walks a whole library: previews count immediate children only.
"""
from __future__ import annotations

import json, os, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))

DEFAULT_PHOTOS = Path("/Volumes/Photos")
DEFAULT_IMPORTS = DEFAULT_PHOTOS / "Camera Imports"
DEFAULT_EXPORTS = DEFAULT_PHOTOS / "Camera Exports"

# Keys the Locations UI can show and save.
LOCATION_FIELDS = (
    {
        "key": "photos_root",
        "label": "Photos root",
        "prompt": "Choose the Photos root folder (parent of Camera Imports and Camera Exports)",
        "hint": "Parent folder that contains Camera Imports and Camera Exports, for example …/[03] Photos. Optional if Imports and Exports are set.",
        "optional": True,
        "training_source": False,
    },
    {
        "key": "imports_root",
        "label": "Camera Imports",
        "prompt": "Choose the Camera Imports folder",
        "hint": "Year folders (2025, 2026, …) live here. When marked as training data, 3★+ ratings from these imports are used as star labels.",
        "optional": False,
        "training_source": True,
        "training_role": "star labels (3★+ ratings)",
    },
    {
        "key": "exports_root",
        "label": "Camera Exports",
        "prompt": "Choose the Camera Exports folder",
        "hint": "Dated export folders live here. When marked as training data, exported frames are positives for the ranking model.",
        "optional": False,
        "training_source": True,
        "training_role": "exported frames",
    },
    {
        "key": "other_exports_root",
        "label": "Other Exports",
        "prompt": "Choose the Other Exports folder",
        "hint": "Optional. Leave blank to use the default folder (Photos root/2025 Exports, or a 2025 Exports bucket inside Camera Exports). The NAS folder name is unchanged. When marked as training data, exported frames are positives for the ranking model.",
        "optional": True,
        "training_source": True,
        "training_role": "exported frames",
    },
)

ALLOWED_KEYS = tuple(f["key"] for f in LOCATION_FIELDS)
ENV_FOR_KEY = {
    "photos_root": "LOCALAI_PHOTOS",
    "imports_root": "LOCALAI_IMPORTS",
    "exports_root": "LOCALAI_EXPORTS",
    "other_exports_root": "LOCALAI_OTHER_EXPORTS",
}


def _cfg(home: Path | None = None) -> dict:
    p = (home or HOME) / "config.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def normalize_path(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    p = Path(text).expanduser()
    out = str(p)
    if out != "/" and out.endswith("/"):
        out = out.rstrip("/")
    return out


def photos_root(home: Path | None = None) -> Path:
    env = os.environ.get("LOCALAI_PHOTOS")
    if env:
        return Path(normalize_path(env))
    cfg = _cfg(home)
    if cfg.get("photos_root"):
        return Path(normalize_path(str(cfg["photos_root"])))
    exp = os.environ.get("LOCALAI_EXPORTS") or cfg.get("exports_root")
    if exp:
        return Path(normalize_path(str(exp))).parent
    return DEFAULT_PHOTOS


def imports_root(home: Path | None = None) -> Path:
    env = os.environ.get("LOCALAI_IMPORTS")
    if env:
        return Path(normalize_path(env))
    cfg = _cfg(home)
    if cfg.get("imports_root"):
        return Path(normalize_path(str(cfg["imports_root"])))
    return DEFAULT_IMPORTS


def exports_root(home: Path | None = None) -> Path:
    env = os.environ.get("LOCALAI_EXPORTS")
    if env:
        return Path(normalize_path(env))
    cfg = _cfg(home)
    if cfg.get("exports_root"):
        return Path(normalize_path(str(cfg["exports_root"])))
    return DEFAULT_EXPORTS


# Default on-disk folder name for Other Exports. Do not rename the NAS folder.
DEFAULT_OTHER_EXPORTS_NAME = "2025 Exports"


def other_exports_root(home: Path | None = None) -> Path:
    env = os.environ.get("LOCALAI_OTHER_EXPORTS") or os.environ.get("LOCALAI_EXPORTS_2025")
    if env:
        return Path(normalize_path(env))
    cfg = _cfg(home)
    if cfg.get("other_exports_root"):
        return Path(normalize_path(str(cfg["other_exports_root"])))
    if cfg.get("exports_2025_root"):
        return Path(normalize_path(str(cfg["exports_2025_root"])))
    sibling = photos_root(home) / DEFAULT_OTHER_EXPORTS_NAME
    if sibling.is_dir():
        return sibling
    inner = exports_root(home) / DEFAULT_OTHER_EXPORTS_NAME
    if inner.is_dir():
        return inner
    return sibling


# Backward-compatible alias
def exports_2025_root(home: Path | None = None) -> Path:
    return other_exports_root(home)


RESOLVERS = {
    "photos_root": photos_root,
    "imports_root": imports_root,
    "exports_root": exports_root,
    "other_exports_root": other_exports_root,
}

TRAINING_SOURCE_KEYS = ("exports_root", "other_exports_root", "imports_root")
DEFAULT_TRAINING_SOURCES = {
    "exports_root": True,
    "other_exports_root": True,
    "imports_root": True,  # keep 3★+ star labels in training unless turned off
}


def training_sources(home: Path | None = None) -> dict:
    """Which location keys are marked as training data. Export roots default on."""
    raw = ((_cfg(home).get("training") or {}) if isinstance(_cfg(home).get("training"), dict) else {}).get("sources") or {}
    if not isinstance(raw, dict):
        raw = {}
    out = dict(DEFAULT_TRAINING_SOURCES)
    if "exports_root" in raw:
        out["exports_root"] = bool(raw["exports_root"])
    if "other_exports_root" in raw:
        out["other_exports_root"] = bool(raw["other_exports_root"])
    elif "exports_2025_root" in raw:
        out["other_exports_root"] = bool(raw["exports_2025_root"])
    if "imports_root" in raw:
        out["imports_root"] = bool(raw["imports_root"])
    return out


def set_training_sources(sources: dict, cfg: dict) -> tuple[dict, bool]:
    """Merge training.sources into cfg. Returns (cfg, changed)."""
    if not isinstance(sources, dict):
        raise ValueError("training_sources must be an object")
    unknown = [k for k in sources if k not in TRAINING_SOURCE_KEYS and k != "exports_2025_root"]
    if unknown:
        raise ValueError("Unknown training source: " + ", ".join(unknown))
    current = dict(DEFAULT_TRAINING_SOURCES)
    existing = ((cfg.get("training") or {}) if isinstance(cfg.get("training"), dict) else {}).get("sources") or {}
    if isinstance(existing, dict):
        if "exports_root" in existing:
            current["exports_root"] = bool(existing["exports_root"])
        if "other_exports_root" in existing:
            current["other_exports_root"] = bool(existing["other_exports_root"])
        elif "exports_2025_root" in existing:
            current["other_exports_root"] = bool(existing["exports_2025_root"])
        if "imports_root" in existing:
            current["imports_root"] = bool(existing["imports_root"])
    for k, v in sources.items():
        key = "other_exports_root" if k == "exports_2025_root" else k
        if key in TRAINING_SOURCE_KEYS:
            current[key] = bool(v)
    training = dict(cfg.get("training") or {}) if isinstance(cfg.get("training"), dict) else {}
    prev = training.get("sources")
    if prev == current:
        return cfg, False
    training["sources"] = current
    # drop legacy key from stored sources if we now write the new name
    cfg["training"] = training
    return cfg, True


def preview_path(path: str) -> dict:
    """Shallow check: exists, readable, immediate child counts. No recursion."""
    raw = normalize_path(path)
    out = {
        "path": raw,
        "exists": False,
        "is_dir": False,
        "readable": False,
        "dirs": 0,
        "files": 0,
        "sample_dirs": [],
        "sample_files": [],
        "summary": "",
        "error": None,
        "scan": "immediate children only",
    }
    if not raw:
        out["error"] = "No path entered"
        return out
    p = Path(raw)
    try:
        if not p.exists():
            out["error"] = "Path does not exist (is the NAS mounted?)"
            return out
        out["exists"] = True
        if not p.is_dir():
            out["error"] = "Not a folder"
            return out
        out["is_dir"] = True
        if not os.access(p, os.R_OK):
            out["error"] = "Folder exists but is not readable"
            return out
        out["readable"] = True
        dirs, files = [], []
        with os.scandir(p) as it:
            for ent in it:
                name = ent.name
                if name.startswith(".") or name.startswith("._") or name.lower() in {"@eadir"}:
                    continue
                try:
                    if ent.is_dir(follow_symlinks=False):
                        dirs.append(name)
                    elif ent.is_file(follow_symlinks=False):
                        files.append(name)
                except OSError:
                    continue
        dirs.sort()
        files.sort()
        out["dirs"] = len(dirs)
        out["files"] = len(files)
        out["sample_dirs"] = dirs[:12]
        out["sample_files"] = files[:8]
        years = [d for d in dirs if d.isdigit() and len(d) == 4]
        bits = [f"{len(dirs)} folder" + ("s" if len(dirs) != 1 else ""),
                f"{len(files)} file" + ("s" if len(files) != 1 else "") + " at this level"]
        if years:
            bits.append("year folders: " + ", ".join(years[:8]))
        out["summary"] = ", ".join(bits) + " (not a recursive library scan)"
    except OSError as e:
        out["error"] = str(e)
    return out


def current_locations(home: Path | None = None) -> dict:
    """Resolved paths, config values, env overrides and a shallow preview each."""
    home = home or HOME
    cfg = _cfg(home)
    sources = training_sources(home)
    fields = []
    for spec in LOCATION_FIELDS:
        key = spec["key"]
        env_name = ENV_FOR_KEY[key]
        env_val = os.environ.get(env_name) or ""
        configured = normalize_path(str(cfg[key])) if cfg.get(key) else ""
        # Legacy Other Exports key / env still populate the field
        if key == "other_exports_root":
            if not configured and cfg.get("exports_2025_root"):
                configured = normalize_path(str(cfg["exports_2025_root"]))
            if not env_val and os.environ.get("LOCALAI_EXPORTS_2025"):
                env_val = os.environ.get("LOCALAI_EXPORTS_2025") or ""
                env_name = "LOCALAI_EXPORTS_2025"
        resolved = str(RESOLVERS[key](home))
        preview = preview_path(resolved) if resolved else preview_path("")
        fields.append({
            **spec,
            "configured": configured,
            "resolved": resolved,
            "env_name": env_name,
            "env_value": normalize_path(env_val) if env_val else "",
            "env_override": bool(env_val),
            "preview": preview,
            "training_enabled": bool(spec.get("training_source")) and bool(sources.get(key)),
        })
    return {"fields": fields, "training_sources": sources}


def _as_literal(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


def choose_folder(prompt: str, timeout: int = 360) -> dict:
    """Native macOS folder picker via osascript. Runs on the GUI Mac."""
    prompt = (prompt or "Choose a folder").replace("\n", " ").strip()[:200]
    if sys.platform != "darwin":
        return {"ok": False, "cancelled": False,
                "error": "Folder picker only works on the Mac running the control panel. Paste the path instead."}
    script = f'''
tell application "SystemUIServer"
    activate
    try
        set theFolder to choose folder with prompt {_as_literal(prompt)}
        return POSIX path of theFolder
    on error errMsg number errNum
        if errNum is -128 then
            return "__CANCELLED__"
        else
            return "__ERROR__" & errMsg
        end if
    end try
end tell
'''
    try:
        r = subprocess.run(
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "cancelled": False, "error": "Folder picker timed out. Paste the path instead."}
    except FileNotFoundError:
        return {"ok": False, "cancelled": False, "error": "osascript is not available. Paste the path instead."}
    text = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if text == "__CANCELLED__" or (r.returncode != 0 and "User canceled" in (err + text)):
        return {"ok": False, "cancelled": True, "error": "Cancelled"}
    if text.startswith("__ERROR__") or r.returncode != 0 or not text:
        msg = text[len("__ERROR__"):] if text.startswith("__ERROR__") else (err or text or f"osascript exited {r.returncode}")
        return {"ok": False, "cancelled": False, "error": msg}
    path = normalize_path(text)
    preview = preview_path(path)
    return {"ok": True, "cancelled": False, "path": path, "preview": preview}


def save_locations(updates: dict, home: Path | None = None, require_readable: bool = True,
                   training_sources_update: dict | None = None) -> dict:
    """Write allowed path keys (and optional training.sources) after a timestamped backup."""
    home = Path(home or HOME)
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object of path keys")
    unknown = [k for k in updates if k not in ALLOWED_KEYS]
    if unknown:
        raise ValueError("Unknown location key: " + ", ".join(unknown))

    cleaned = {}
    previews = {}
    for key, value in updates.items():
        if value is None:
            cleaned[key] = ""
            continue
        path = normalize_path(str(value))
        cleaned[key] = path
        optional = next(f["optional"] for f in LOCATION_FIELDS if f["key"] == key)
        if not path:
            if optional:
                continue
            raise ValueError(f"{key} cannot be empty")
        prev = preview_path(path)
        previews[key] = prev
        if require_readable and not prev.get("readable"):
            raise ValueError(f"{key}: {prev.get('error') or 'not a readable folder'}")

    cfg_path = home / "config.json"
    cfg = _cfg(home)
    changed = False
    for key, path in cleaned.items():
        optional = next(f["optional"] for f in LOCATION_FIELDS if f["key"] == key)
        if not path and optional:
            if key in cfg:
                cfg.pop(key, None)
                changed = True
            if key == "other_exports_root" and "exports_2025_root" in cfg:
                cfg.pop("exports_2025_root", None)
                changed = True
            continue
        if cfg.get(key) != path:
            cfg[key] = path
            changed = True

    if training_sources_update is not None:
        cfg, src_changed = set_training_sources(training_sources_update, cfg)
        changed = changed or src_changed

    backup = None
    if changed:
        if cfg_path.is_file():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = home / f"config.json.bak-locations-{stamp}"
            shutil.copy2(cfg_path, backup)
        tmp = cfg_path.with_suffix(".json.tmp-locations")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, cfg_path)

    return {
        "ok": True,
        "changed": changed,
        "backup": str(backup) if backup else None,
        "previews": previews,
        "locations": current_locations(home),
    }
