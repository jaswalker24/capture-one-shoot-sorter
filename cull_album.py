#!/usr/bin/env python3
"""Write Cull|* keywords into XMP sidecars so Lightroom smart collections can match them.

usage: venv/bin/python cull_album.py <shoot folder path> [--dry]

For each image in the shoot folder (MP4s and _schedule* files skipped), reads
keywords already in the sidecar and additively writes:

  Cull/Sharp + Burst Best  — General/Quality/Sharp AND General/Burst Best,
                             and not Motion Blur or Soft Focus
  Cull/Rank Best           — General/Rank/Best, and not blur
  Cull/Shortlist           — either of the above

Ratings, colour labels and develop settings are never changed. Re-running is
idempotent. Lightroom cannot receive smart collections from outside, so you
build them once on these keywords (see INSTALL-LR.md).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(HOME))
import scope_folder as S  # noqa: E402
import apply_xmp as AX  # noqa: E402

CULL_KEYWORD = {
    "Cull: Sharp + Burst Best": "Cull/Sharp + Burst Best",
    "Cull: Rank Best": "Cull/Rank Best",
    "Cull: Shortlist": "Cull/Shortlist",
}


def frames_from_folder(folder: Path) -> list[dict]:
    frames = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return frames
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in AX.IMAGE_EXT:
            continue
        if p.name.startswith(".") or AX.is_schedule_file(p.name):
            continue
        kws = []
        sc = AX.sidecar_path(p)
        if sc.is_file():
            try:
                info = AX.read_sidecar(sc.read_text(encoding="utf-8"))
                kws = info.get("keywords") or []
            except Exception:
                kws = []
        frames.append({"id": p.name, "filename": p.name, "path": str(p), "keywords": kws})
    return frames


def proposals_for(folder: Path, frames: list[dict]) -> dict:
    groups = S.cull_groups(frames)
    by_file = {}
    for suffix, names in groups.items():
        kw = CULL_KEYWORD[suffix]
        for name in names:
            by_file.setdefault(name, []).append(kw)
    images = [{"filename": fn, "needs_review": False, "proposed_keywords": kws}
              for fn, kws in sorted(by_file.items())]
    return {"folder": str(folder), "images": images, "groups": {s: list(n) for s, n in groups.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shoot")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    p = Path(a.shoot)
    folder = Path(str(p.resolve()) if p.exists() else p)
    folder = Path(str(folder).rstrip("/"))
    shoot = folder.name
    outdir = HOME / "out" / shoot
    outdir.mkdir(parents=True, exist_ok=True)
    frames = frames_from_folder(folder)
    plan = proposals_for(folder, frames)
    plan_path = outdir / "cull-keywords-plan.json"
    result_path = outdir / "cull-keywords-result.json"
    json.dump(plan, open(plan_path, "w"), indent=1)
    if a.dry or not plan["images"]:
        counts = {s: len(n) for s, n in plan["groups"].items()}
        if a.dry:
            print("dry: would write Cull keywords into XMP sidecars in", folder)
        for suffix, n in counts.items():
            print(f"{suffix}\t{n}")
        print("frames_in_folder=", len(frames), "to_update=", len(plan["images"]))
        json.dump({"dry": True, "counts": counts, "frames_in_folder": len(frames)},
                  open(result_path, "w"), indent=1)
        return
    prefix = str(outdir / f"cull-keywords-{time.strftime('%Y%m%d-%H%M%S')}")
    res = AX.apply_proposals(str(plan_path), apply=True, prefix=prefix)
    json.dump(res, open(result_path, "w"), indent=1, ensure_ascii=False)
    counts = {s: len(n) for s, n in plan["groups"].items()}
    for suffix, n in counts.items():
        print(f"{suffix}\t{n}")
    print("frames_in_folder=", len(frames),
          "applied=", len(res.get("applied") or []),
          "failures=", len(res.get("failures") or []))


if __name__ == "__main__":
    main()
