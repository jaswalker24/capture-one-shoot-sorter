#!/usr/bin/env python3
"""Apply reviewed race numbers (confirm / fix decisions only) as additive Motorsport/Car Number/<n> keywords.
usage: venv/bin/python review_apply.py <shoot-folder-name> [--apply]
Snapshot before (whole collection: stars/colours), apply in chunks of 200 via apply_c1, verify after.
Rejected, skipped and unreviewed frames are never touched. Undo: out/<shoot>/review-apply-*-before.json."""
import argparse, json, re, subprocess, sys, time
from pathlib import Path
HOME = Path.home() / "localai"; sys.path.insert(0, str(HOME))
OUT = HOME / "out"

def snap(path):
    subprocess.run(["osascript", "-l", "JavaScript", str(HOME / "snap.js"), str(path)], check=True, capture_output=True, timeout=300)
    return json.load(open(path))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("shoot"); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    d = OUT / a.shoot; stamp = time.strftime("%Y%m%d-%H%M%S")
    props = json.load(open(d / "motorsport-proposals-full.json"))
    folder = props["folder"]; sha = {r["filename"]: r.get("sha256") for r in props["images"] if r.get("needs_review")}
    dec = json.load(open(d / "review-decisions.json")).get("items", {}) if (d / "review-decisions.json").exists() else {}
    af = d / "review-applied.json"
    applied = json.load(open(af)).get("items", {}) if af.exists() else {}
    todo = []
    for fn, x in sorted(dec.items()):
        if x.get("action") not in ("confirm", "fix") or fn not in sha: continue
        nums = [n for n in x.get("numbers", []) if re.fullmatch(r"\d{1,3}", n)]
        new = [n for n in nums if n not in applied.get(fn, {}).get("numbers", [])]
        if new:
            todo.append({"filename": fn, "sha256": sha[fn], "needs_review": False,
                         "proposed_keywords": [f"Motorsport/Car Number/{n}" for n in new], "_nums": new})
    print(time.strftime("%H:%M:%S"), f"{len(todo)} reviewed frames to apply ({'APPLY' if a.apply else 'dry run'})", flush=True)
    if not todo:
        json.dump({"status": "done", "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "applied": 0, "failures": 0}, open(d / "review-apply-last.json", "w"))
        return
    from apply_c1 import apply_proposals
    before = after = None
    if a.apply:
        before = snap(d / f"review-apply-{stamp}-flags-before.json"); print("snapshot before:", len(before), "variants", flush=True)
    tot_kw = tot_fail = 0
    for i in range(0, len(todo), 200):
        chunk = todo[i:i + 200]
        cf = d / f"review-apply-{stamp}-chunk-{i // 200:03d}.json"
        json.dump({"folder": folder, "images": [{k: v for k, v in r.items() if k != "_nums"} for r in chunk]}, open(cf, "w"), indent=1)
        res = apply_proposals(str(cf), apply=a.apply, prefix=str(d / f"review-apply-{stamp}-c{i // 200:03d}"))
        tot_kw += len(res["applied"]); tot_fail += len(res["failures"])
        if a.apply:
            ok = {x["filename"] for x in res["applied"]} | {x["filename"] for x in res["skipped"] if x.get("reason") == "already applied"}
            bad = {x["filename"] for x in res["failures"]}
            for r in chunk:
                if r["filename"] in ok and r["filename"] not in bad:
                    e = applied.setdefault(r["filename"], {"numbers": []})
                    e["numbers"] = sorted(set(e["numbers"]) | set(r["_nums"]), key=int); e["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            tmp = af.with_suffix(".tmp"); tmp.write_text(json.dumps({"items": applied}, indent=1)); tmp.replace(af)
    verify = None
    if a.apply:
        after = snap(d / f"review-apply-{stamp}-flags-after.json")
        changed = [k for k in before if after.get(k, {}).get("rating") != before[k]["rating"] or after.get(k, {}).get("color") != before[k]["color"]]
        verify = {"variants": len(before), "rating_or_colour_changed": len(changed), "examples": changed[:10]}
        print("verify:", verify, flush=True)
    last = {"status": "done", "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "frames": len(todo), "applied_keywords": tot_kw,
            "failures": tot_fail, "verify": verify, "dry_run": not a.apply, "stamp": stamp}
    json.dump(last, open(d / "review-apply-last.json", "w"), indent=1)
    print(time.strftime("%H:%M:%S"), "FINISHED", json.dumps(last), flush=True)

if __name__ == "__main__":
    main()
