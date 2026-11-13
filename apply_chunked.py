#!/usr/bin/env python3
"""Additively apply one proposals file to Capture One in chunks, with a whole-collection star/colour snapshot
before and a verify after. usage: venv/bin/python apply_chunked.py <proposals.json> --tag NAME [--apply] [--prefix-keyword General/Rank/]
Undo data: out/<shoot>/<tag>-<time>-cNNN-before.json (keywords per variant before the write)."""
import argparse, json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_home
HOME = app_home.home(); sys.path.insert(0, str(HOME))

def snap(path, folder=None):
    # folder-scoped (snap.js --folder): never snapshot whatever collection Capture One happens to show
    subprocess.run(["osascript", "-l", "JavaScript", str(HOME / "snap.js"), str(path)] + (["--folder", str(folder)] if folder else []),
                   check=True, capture_output=True, timeout=300)
    return json.load(open(path))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("proposals"); ap.add_argument("--tag", required=True)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--prefix-keyword", default="")
    ap.add_argument("--chunk", type=int, default=200); a = ap.parse_args()
    from apply_c1 import apply_proposals
    p = Path(a.proposals); d = p.parent; data = json.load(open(p)); stamp = time.strftime("%Y%m%d-%H%M%S")
    imgs = []
    for r in data["images"]:
        if r.get("needs_review") or r.get("error"): continue
        kws = [k for k in r.get("proposed_keywords", []) if k.startswith(a.prefix_keyword)]
        if kws: imgs.append(dict(r, proposed_keywords=kws))
    print(time.strftime("%H:%M:%S"), f"{a.tag}: {len(imgs)} frames to {'APPLY' if a.apply else 'dry-run'}", flush=True)
    before = snap(d / f"{a.tag}-{stamp}-flags-before.json", data["folder"]) if a.apply else None
    tot = {"applied": 0, "skipped": 0, "failures": 0, "variants": 0}
    for i in range(0, len(imgs), a.chunk):
        cf = d / f"{a.tag}-{stamp}-chunk-{i // a.chunk:03d}.json"
        json.dump({"folder": data["folder"], "images": imgs[i:i + a.chunk]}, open(cf, "w"))
        res = apply_proposals(str(cf), apply=a.apply, prefix=str(d / f"{a.tag}-{stamp}-c{i // a.chunk:03d}"))
        tot["applied"] += len(res["applied"]); tot["skipped"] += len(res["skipped"]); tot["failures"] += len(res["failures"])
        tot["variants"] += len({x["filename"] for x in res["applied"]})
        print(time.strftime("%H:%M:%S"), f"chunk {i // a.chunk}: {json.dumps(tot)}", flush=True)
    if a.apply:
        after = snap(d / f"{a.tag}-{stamp}-flags-after.json", data["folder"])
        changed = [k for k in before if after.get(k, {}).get("rating") != before[k]["rating"] or after.get(k, {}).get("color") != before[k]["color"]]
        tot["verify"] = {"variants": len(before), "rating_or_colour_changed": len(changed), "examples": changed[:10]}
    tot["stamp"] = stamp
    json.dump(tot, open(d / f"{a.tag}-{stamp}-summary.json", "w"), indent=1)
    print(time.strftime("%H:%M:%S"), "FINISHED", json.dumps(tot), flush=True)

if __name__ == "__main__":
    main()
