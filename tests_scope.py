#!/usr/bin/env python3
"""Unit tests for catalog-folder scoping and cull-album membership (no Capture One)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scope_folder as S

WANT = "/Volumes/Photos/Camera Imports/2026/2026-10-04"
A = {"name": "2026-10-04", "kind": "catalog folder", "folder": WANT}
B = {"name": "2026-10-04", "kind": "catalog folder",
     "folder": "/Volumes/other/[03] Photos/Camera Imports/2026/2026-10-04"}
ALBUM = {"name": "2026-10-04", "kind": "album", "folder": WANT}
NAMED_ELSEWHERE = {"name": "2026-10-04", "kind": "catalog folder",
                   "folder": "/Volumes/Photos/Other/2026-10-04"}
SHARP, BURST, RANK = S.SHARP, S.BURST_BEST, S.RANK_BEST


def test_normalize_trailing_slash():
    assert S.normalize_folder(WANT + "/") == WANT
    assert S.normalize_folder(WANT) == WANT


def test_exact_path_wins():
    chosen, how = S.pick_catalog_folder([A, B, ALBUM], WANT)
    assert chosen is A
    assert how == "exact-path"


def test_exact_path_ignores_same_name_album():
    chosen, how = S.pick_catalog_folder([ALBUM, A], WANT + "/")
    assert chosen is A and how == "exact-path"


def test_fallback_name_under_camera_imports():
    alt = dict(A, folder="/Volumes/NAS/Camera Imports/2026/2026-10-04")
    chosen, how = S.pick_catalog_folder([alt, NAMED_ELSEWHERE], WANT)
    assert chosen is alt
    assert how == "name-under-camera-imports"


def test_fallback_rejects_two_under_camera_imports():
    a = dict(A, folder="/Volumes/a/Camera Imports/2026/2026-10-04")
    b = dict(A, folder="/Volumes/b/Camera Imports/2026/2026-10-04")
    try:
        S.pick_catalog_folder([a, b], WANT)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "multiple" in str(e) and "Camera Imports" in str(e)


def test_not_found():
    try:
        S.pick_catalog_folder([NAMED_ELSEWHERE, ALBUM], WANT)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not found" in str(e)


def test_ambiguous_exact_path():
    try:
        S.pick_catalog_folder([A, dict(A)], WANT)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "multiple catalog folders at" in str(e)


def test_cull_membership():
    frames = [
        {"id": "s", "keywords": [SHARP, BURST]},
        {"id": "r", "keywords": [RANK]},
        {"id": "b", "keywords": [SHARP, BURST, RANK]},
        {"id": "x", "keywords": [SHARP, BURST, "General/Quality/Motion Blur"]},
        {"id": "y", "keywords": [RANK, "General/Quality/Soft Focus"]},
        {"id": "z", "keywords": ["Gig/Portrait"]},
    ]
    g = S.cull_groups(frames)
    assert g["Cull: Sharp + Burst Best"] == ["s", "b"]
    assert g["Cull: Rank Best"] == ["r", "b"]
    assert g["Cull: Shortlist"] == ["s", "r", "b"]


def test_cull_empty_keywords():
    g = S.cull_groups([{"id": "n", "keywords": []}, {"id": "m"}])
    assert g["Cull: Sharp + Burst Best"] == []
    assert g["Cull: Rank Best"] == []
    assert g["Cull: Shortlist"] == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all tests passed")
