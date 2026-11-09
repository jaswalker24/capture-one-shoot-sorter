#!/usr/bin/env python3
"""Profiles for Trips Local AI — supersedes flat Presets.
Stored as JSON files in ~/localai/profiles/. Catalog profiles never include captions.
"""
from __future__ import annotations
import json, re, time, uuid
from copy import deepcopy
from pathlib import Path

HOME = Path.home() / "localai"
PROFILES = HOME / "profiles"
ORDER_FILE = PROFILES / "_order.json"

SHOOT_TYPES = [
    "Gig", "Idol gig", "Concert/arena", "Motorsport", "Wildlife",
    "Aviation", "Temple/travel", "Street", "Exports only",
]

PASS_BUNDLES = {
    "Quick": ["tagger", "sharpness", "bursts"],
    "Standard": ["tagger", "index", "sharpness", "bursts", "schedule"],
    "Full": ["tagger", "index", "sharpness", "bursts", "ocr", "schedule"],
    "Schedule tags only": ["schedule"],
    "Exports captions": ["captions"],
    "Custom": [],
}

CAMERA_TZ = ["Europe/London", "Asia/Tokyo", "UTC", "Other"]
BUFFERS = [0, 1, 3, 5, 10]
STRICTNESS = ["Lenient", "Normal", "Strict"]
NOTIFY = ["Off", "Finish", "Finish+Fail"]

# Keyword sets keyed by shoot type (additive defaults — Gig/* style library paths)
KEYWORD_SETS = {
    "Gig": ["Gig/Genre/Live", "Gig/Shot/Stage"],
    "Idol gig": ["Gig/Genre/Idol", "Gig/Shot/Stage", "Gig/Cheki"],
    "Concert/arena": ["Gig/Genre/Arena", "Gig/Shot/Stage"],
    "Motorsport": ["Motorsport/Night", "Motorsport/Session/Race"],
    "Wildlife": ["Wildlife/Wild"],
    "Aviation": ["Aviation/Airshow"],
    "Temple/travel": ["Japan/Temple", "Kyoto/Landscape"],
    "Street": ["General/Street"],
    "Exports only": [],
}

SHOOT_DEFAULTS = {
    "Gig": {
        "tagger_profile": "gig", "pass_bundle": "Standard", "camera_tz": "Asia/Tokyo",
        "buffer_min": 3, "strictness": "Normal", "notify": "Finish+Fail",
        "keyword_set": "Gig",
    },
    "Idol gig": {
        "tagger_profile": "gig", "pass_bundle": "Standard", "camera_tz": "Asia/Tokyo",
        "buffer_min": 3, "strictness": "Normal", "notify": "Finish+Fail",
        "keyword_set": "Idol gig",
    },
    "Concert/arena": {
        "tagger_profile": "gig", "pass_bundle": "Quick", "camera_tz": "Europe/London",
        "buffer_min": 5, "strictness": "Normal", "notify": "Finish",
        "keyword_set": "Concert/arena",
    },
    "Motorsport": {
        "tagger_profile": "motorsport", "pass_bundle": "Full", "camera_tz": "Europe/London",
        "buffer_min": 0, "strictness": "Strict", "notify": "Finish+Fail",
        "keyword_set": "Motorsport", "ocr_missing_car_number": True,
    },
    "Wildlife": {
        "tagger_profile": "gig", "pass_bundle": "Quick", "camera_tz": "Europe/London",
        "buffer_min": 0, "strictness": "Strict", "notify": "Finish",
        "keyword_set": "Wildlife",
    },
    "Aviation": {
        "tagger_profile": "gig", "pass_bundle": "Quick", "camera_tz": "Europe/London",
        "buffer_min": 0, "strictness": "Normal", "notify": "Finish",
        "keyword_set": "Aviation",
    },
    "Temple/travel": {
        "tagger_profile": "kyoto", "pass_bundle": "Standard", "camera_tz": "Asia/Tokyo",
        "buffer_min": 0, "strictness": "Lenient", "notify": "Finish",
        "keyword_set": "Temple/travel",
    },
    "Street": {
        "tagger_profile": "gig", "pass_bundle": "Quick", "camera_tz": "Europe/London",
        "buffer_min": 0, "strictness": "Lenient", "notify": "Off",
        "keyword_set": "Street",
    },
    "Exports only": {
        "tagger_profile": "gig", "pass_bundle": "Exports captions", "camera_tz": "Europe/London",
        "buffer_min": 0, "strictness": "Normal", "notify": "Finish",
        "keyword_set": "Exports only", "kind": "exports",
    },
}

SEED = [
    {"id": "gig-standard", "name": "Gig standard", "shoot_type": "Gig"},
    {"id": "idol-gig", "name": "Idol gig", "shoot_type": "Idol gig"},
    {"id": "motorsport-full", "name": "Motorsport full", "shoot_type": "Motorsport"},
    {"id": "temple-travel", "name": "Temple / travel", "shoot_type": "Temple/travel"},
    {"id": "schedule-only", "name": "Schedule tags only", "shoot_type": "Gig",
     "pass_bundle": "Schedule tags only", "passes": ["schedule"]},
    {"id": "exports-captions", "name": "Exports only (Draft Captions)", "shoot_type": "Exports only"},
]


def catalogs():
    return {
        "shoot_types": SHOOT_TYPES,
        "pass_bundles": {k: v for k, v in PASS_BUNDLES.items()},
        "camera_timezones": CAMERA_TZ,
        "buffers": BUFFERS,
        "strictness": STRICTNESS,
        "notify": NOTIFY,
        "keyword_sets": {k: v for k, v in KEYWORD_SETS.items()},
        "shoot_defaults": SHOOT_DEFAULTS,
        "tagger_profiles": ["motorsport", "gig", "kyoto"],
    }


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "profile").lower()).strip("-")[:48] or "profile"


def _path(pid: str) -> Path:
    return PROFILES / f"{pid}.json"


def _load_order() -> list[str]:
    if ORDER_FILE.exists():
        return json.load(open(ORDER_FILE)).get("order") or []
    return []


def _save_order(order: list[str]):
    PROFILES.mkdir(parents=True, exist_ok=True)
    json.dump({"order": order}, open(ORDER_FILE, "w"), indent=2)


def build_from_shoot_type(shoot_type: str, overrides: dict | None = None) -> dict:
    d = deepcopy(SHOOT_DEFAULTS.get(shoot_type) or SHOOT_DEFAULTS["Gig"])
    bundle = d.get("pass_bundle") or "Standard"
    passes = list(PASS_BUNDLES.get(bundle) or [])
    kind = d.get("kind") or ("exports" if shoot_type == "Exports only" else "catalog")
    ks = d.get("keyword_set") or shoot_type
    profile = {
        "id": "",
        "name": "",
        "kind": kind,
        "shoot_type": shoot_type,
        "tagger_profile": d.get("tagger_profile") or "gig",
        "pass_bundle": bundle,
        "passes": passes,
        "pass_settings": {pid: {} for pid in passes},
        "camera_tz": d.get("camera_tz") or "Europe/London",
        "camera_tz_other": "",
        "buffer_min": int(d.get("buffer_min") or 3),
        "default_keywords": list(KEYWORD_SETS.get(ks) or []),
        "keyword_set": ks,
        "schedule_path": "",
        "ocr_missing_car_number": bool(d.get("ocr_missing_car_number")),
        "apply_rank_keywords": False,
        "apply_each": True,
        "strictness": d.get("strictness") or "Normal",
        "notify": d.get("notify") or "Off",
        "advanced": {},
    }
    if overrides:
        profile.update({k: v for k, v in overrides.items() if v is not None})
        if "pass_bundle" in overrides and overrides["pass_bundle"] in PASS_BUNDLES and overrides["pass_bundle"] != "Custom":
            profile["passes"] = list(PASS_BUNDLES[overrides["pass_bundle"]])
        if "keyword_set" in overrides and overrides["keyword_set"] in KEYWORD_SETS:
            profile["default_keywords"] = list(KEYWORD_SETS[overrides["keyword_set"]])
    return validate(profile)


def validate(profile: dict) -> dict:
    kind = profile.get("kind") or "catalog"
    passes = list(profile.get("passes") or [])
    if kind != "exports" and "captions" in passes:
        raise ValueError("catalog profiles cannot include Draft Captions")
    if kind == "exports":
        if passes != ["captions"] and "captions" not in passes:
            passes = ["captions"]
        profile["passes"] = passes
        profile["shoot_type"] = "Exports only"
    profile["kind"] = kind
    profile["buffer_min"] = int(profile.get("buffer_min") or 0)
    profile.setdefault("pass_settings", {})
    profile.setdefault("default_keywords", [])
    profile.setdefault("notify", "Off")
    profile.setdefault("strictness", "Normal")
    profile.setdefault("camera_tz", "Europe/London")
    profile.setdefault("apply_each", True)
    return profile


def list_profiles() -> list[dict]:
    PROFILES.mkdir(parents=True, exist_ok=True)
    order = _load_order()
    found = {p.stem: p for p in PROFILES.glob("*.json") if p.name != "_order.json"}
    out = []
    for pid in order:
        if pid in found:
            out.append(json.load(open(found.pop(pid))))
    for pid, path in sorted(found.items()):
        out.append(json.load(open(path)))
    return out


def get_profile(pid: str) -> dict | None:
    path = _path(pid)
    return json.load(open(path)) if path.exists() else None


def save_profile(profile: dict, overwrite: bool = True) -> dict:
    profile = validate(deepcopy(profile))
    if not profile.get("id"):
        profile["id"] = _slug(profile.get("name") or "profile")
    profile["id"] = _slug(profile["id"])
    path = _path(profile["id"])
    if path.exists() and not overwrite:
        raise ValueError(f"profile {profile['id']} already exists")
    profile["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    PROFILES.mkdir(parents=True, exist_ok=True)
    json.dump(profile, open(path, "w"), ensure_ascii=False, indent=2)
    order = _load_order()
    if profile["id"] not in order:
        order.append(profile["id"])
        _save_order(order)
    return profile


def delete_profile(pid: str):
    path = _path(pid)
    if not path.exists():
        raise ValueError("not found")
    path.unlink()
    _save_order([x for x in _load_order() if x != pid])


def duplicate_profile(pid: str, new_name: str | None = None) -> dict:
    src = get_profile(pid)
    if not src:
        raise ValueError("not found")
    dup = deepcopy(src)
    dup["name"] = new_name or (src.get("name") + " copy")
    dup["id"] = _slug(dup["name"]) + "-" + uuid.uuid4().hex[:4]
    return save_profile(dup, overwrite=False)


def reorder(order: list[str]):
    existing = {p["id"] for p in list_profiles()}
    clean = [x for x in order if x in existing]
    for x in existing:
        if x not in clean:
            clean.append(x)
    _save_order(clean)
    return clean


def seed_if_empty():
    PROFILES.mkdir(parents=True, exist_ok=True)
    if any(PROFILES.glob("*.json")) and (PROFILES / "gig-standard.json").exists():
        return list_profiles()
    order = []
    for s in SEED:
        st = s["shoot_type"]
        overrides = {k: v for k, v in s.items() if k not in ("id", "name", "shoot_type")}
        prof = build_from_shoot_type(st, overrides)
        prof["id"] = s["id"]
        prof["name"] = s["name"]
        if "passes" in s:
            prof["passes"] = s["passes"]
            prof["pass_bundle"] = s.get("pass_bundle") or "Custom"
        save_profile(prof, overwrite=True)
        order.append(prof["id"])
    _save_order(order)
    return list_profiles()


def job_fields_from_profile(profile: dict) -> dict:
    """Map a profile onto queue/add + schedule apply fields."""
    cam = profile.get("camera_tz") or "Europe/London"
    if cam == "Other":
        cam = profile.get("camera_tz_other") or "Europe/London"
    return {
        "profile": profile.get("tagger_profile") or "gig",
        "passes": list(profile.get("passes") or []),
        "apply_each": bool(profile.get("apply_each", True)),
        "ocr_missing_car_number": bool(profile.get("ocr_missing_car_number")),
        "apply_rank_keywords": bool(profile.get("apply_rank_keywords")),
        "schedule_path": profile.get("schedule_path") or "",
        "camera_tz": cam,
        "buffer_min": int(profile.get("buffer_min") or 3),
        "default_keywords": list(profile.get("default_keywords") or []),
        "strictness": profile.get("strictness") or "Normal",
        "notify_mode": profile.get("notify") or "Off",
        "kind": profile.get("kind") or "catalog",
        "shoot_type": profile.get("shoot_type"),
        "profile_id": profile.get("id"),
    }
