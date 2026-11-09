#!/usr/bin/env python3
"""Apply CONFIDENT tagger proposals to Capture One (additive). Dry run unless --apply.
Confident = needs_review is false; only proposed_keywords (never tentative_keywords).
SHA-256 of each original is re-checked before applying."""
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path
HOME = Path.home() / "localai"

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()

def apply_proposals(proposals_json, apply=False, prefix=None, library="Trip Keywords"):
    data = json.load(open(proposals_json))
    folder = Path(data["folder"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = prefix or f"/tmp/trip-localai-apply-{stamp}"
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
    json.dump(plan, open(prefix + "-plan.json", "w"), indent=1)
    out = subprocess.run(["osascript", "-l", "JavaScript", str(HOME / "apply_c1.js"), prefix + "-plan.json",
                          prefix + "-result.json"] + ([] if apply else ["dry"]), capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("osascript failed: " + out.stderr)
    res = json.load(open(prefix + "-result.json"))
    json.dump(res["before"], open(prefix + "-before.json", "w"), indent=1)
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
