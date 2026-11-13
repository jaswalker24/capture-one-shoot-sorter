#!/usr/bin/env python3
"""Apply CONFIDENT tagger proposals to Capture One (additive). Dry run unless --apply.
Confident = needs_review is false; only proposed_keywords (never tentative_keywords).
SHA-256 of each original is re-checked before applying."""
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path
HOME = Path(__import__("os").environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
IMAGE_EXT = {".cr3", ".cr2", ".dng", ".arw", ".nef", ".raf", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".png"}  # MP4/MOV ignored

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()

DEFAULT_KEYWORD_LIBRARY = "Trip Keywords"
LEGACY_KEYWORD_LIBRARY = "Grok Keywords"


def keyword_library(name=None):
    """Library name for Capture One. config.json keyword_library, else Trip Keywords.

    An existing catalog can set keyword_library in config.json to the name already in use.
    """
    if name:
        return name
    cfg_path = HOME / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and cfg.get("keyword_library"):
                return str(cfg["keyword_library"])
        except Exception:
            pass
    return DEFAULT_KEYWORD_LIBRARY


def apply_proposals(proposals_json, apply=False, prefix=None, library=None):
    data = json.load(open(proposals_json))
    folder = Path(data["folder"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = prefix or f"/tmp/trip-localai-apply-{stamp}"
    library = keyword_library(library)
    items, skipped = [], []
    for r in data["images"]:
        if r.get("error"):
            skipped.append({"filename": r["filename"], "reason": "tagger error"}); continue
        if r.get("needs_review"):
            skipped.append({"filename": r["filename"], "reason": "needs_review: " + "; ".join(r.get("review_reasons", [])),
                            "keywords": r.get("proposed_keywords", []) + r.get("tentative_keywords", [])}); continue
        # Captions/descriptions are export-only; never write them into Capture One here.
        if not r.get("proposed_keywords"):
            continue
        p = folder / r["filename"]
        if r.get("sha256") and __import__("shacache").sha256_cached(p) != r["sha256"]:
            skipped.append({"filename": r["filename"], "reason": "sha256 mismatch"}); continue
        items.append({"filename": r["filename"], "path": str(p), "sha256": r.get("sha256"),
                      "keywords": r.get("proposed_keywords", []), "description": None})
    plan = {"folder": str(folder), "library": library, "items": items, "skipped_by_policy": skipped}
    # Folder scope (fix 2026-11-09): apply_c1.js selects this folder in Capture One, checks the variants match,
    # refuses to write if they don't, then restores what Capture One was showing.
    plan["scope_folder"] = str(folder)
    try:
        plan["expected_count"] = sum(1 for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))
    except OSError:
        plan["expected_count"] = None
    json.dump(plan, open(prefix + "-plan.json", "w"), indent=1)
    out = subprocess.run(["osascript", "-l", "JavaScript", str(HOME / "apply_c1.js"), prefix + "-plan.json",
                          prefix + "-result.json"] + ([] if apply else ["dry"]), capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("osascript failed: " + out.stderr)
    res = json.load(open(prefix + "-result.json"))
    json.dump(res["before"], open(prefix + "-before.json", "w"), indent=1)
    if res.get("refused"):
        print("REFUSED (nothing written):", res["refused"], "| scope:", json.dumps(res.get("scope"))[:300])
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {len(res['applied'])} keywords/fields on "
          f"{len({a['filename'] for a in res['applied']})} variants; skipped {len(res['skipped'])} "
          f"(+{len(skipped)} images by policy); failures {len(res['failures'])}")
    print("files:", prefix + "-{before,plan,result}.json")
    return res

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("proposals_json"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prefix"); a = ap.parse_args()
    apply_proposals(a.proposals_json, a.apply, a.prefix)
