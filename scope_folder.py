#!/usr/bin/env python3
"""Cull-keyword membership (and leftover catalog-folder helpers kept for tests).

Shared by tests and cull_album.py. This Lightroom edition does not talk to a catalog;
cull membership is computed from sidecar keywords and written back as Cull|* keywords.
"""
from pathlib import Path

BLUR = ("General/Quality/Motion Blur", "General/Quality/Soft Focus")
SHARP = "General/Quality/Sharp"
BURST_BEST = "General/Burst Best"
RANK_BEST = "General/Rank/Best"


def normalize_folder(path):
    return str(path).replace("\\", "/").rstrip("/")


def pick_catalog_folder(cands, want_path):
    """Choose a catalog folder collection from {name, kind, folder} dicts.

    Returns (chosen_dict, how) where how is 'exact-path' or
    'name-under-camera-imports'. Raises ValueError if none or ambiguous.
    """
    want = normalize_folder(want_path)
    shoot = Path(want).name
    folders = [c for c in cands if c.get("kind") == "catalog folder" and c.get("name") == shoot]
    exact = [c for c in folders if normalize_folder(c.get("folder") or "") == want]
    if len(exact) == 1:
        return exact[0], "exact-path"
    if len(exact) > 1:
        raise ValueError(f"multiple catalog folders at {want}")
    under = []
    for c in folders:
        p = normalize_folder(c.get("folder") or "")
        if "/Camera Imports/" in p or p.endswith("/Camera Imports"):
            under.append(c)
    if len(under) == 1:
        return under[0], "name-under-camera-imports"
    if len(under) > 1:
        raise ValueError(f"multiple catalog folders named {shoot} under Camera Imports")
    raise ValueError(f"catalog folder not found for {want}")


def _kwset(kws):
    return set(kws or [])


def not_blurred(kws):
    s = _kwset(kws)
    return BLUR[0] not in s and BLUR[1] not in s


def in_sharp_burst(kws):
    s = _kwset(kws)
    return SHARP in s and BURST_BEST in s and not_blurred(kws)


def in_rank_best(kws):
    s = _kwset(kws)
    return RANK_BEST in s and not_blurred(kws)


def in_shortlist(kws):
    return in_sharp_burst(kws) or in_rank_best(kws)


def cull_groups(frames):
    """frames: iterable of {id, keywords}. Returns dict of album-suffix -> [id]."""
    sharp, best, short = [], [], []
    for f in frames:
        i, kws = f["id"], f.get("keywords") or []
        if in_sharp_burst(kws):
            sharp.append(i)
        if in_rank_best(kws):
            best.append(i)
        if in_shortlist(kws):
            short.append(i)
    return {
        "Cull: Sharp + Burst Best": sharp,
        "Cull: Rank Best": best,
        "Cull: Shortlist": short,
    }
