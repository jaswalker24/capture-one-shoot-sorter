#!/usr/bin/env python3
"""Full-folder pipeline (resumable): tagger -> index -> sharpness/bursts/rank (calibrated, General/Rank/Best|Good) -> OCR
-> additive XMP apply -> Cull keywords -> verification.
Draft Captions are NOT part of this pipeline. Usage: venv/bin/python fullrun.py <folder> [--profile motorsport] [--context ...]"""
import argparse, os, json, os, re, subprocess, sys, time
from pathlib import Path
HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr")); sys.path.insert(0, str(HOME))
TAG = str(HOME / "tag")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def run(cmd, env=None):
    log("$", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    p = subprocess.Popen(cmd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in p.stdout:
        if re.search(r"warn|detach\\(\\)|logit_scale|FBBA", line, re.I): continue
        tail = (tail + [line.rstrip()])[-40:]
        if re.match(r"^\\[(\\d+)/(\\d+)\\]", line):
            n = int(re.match(r"^\\[(\\d+)/", line).group(1))
            if n % 100 == 0: log("   ", line.rstrip()[:160])
        else:
            log("   ", line.rstrip()[:200])
    if p.wait(): raise RuntimeError(f"step failed: {' '.join(cmd[:3])}")
    return tail

def snap(path, folder=None):
    from apply_xmp import write_snap
    if not folder:
        raise ValueError("snap requires a folder (XMP sidecars are read from disk, not a catalog)")
    return write_snap(path, folder)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("folder"); ap.add_argument("--profile", default="motorsport")
    ap.add_argument("--context", default=""); ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--no-cull-albums", action="store_true", help="skip the Cull smart albums step"); a = ap.parse_args()
    folder = Path(a.folder); fo = folder.name; out = HOME / "out" / fo; out.mkdir(parents=True, exist_ok=True)
    st_f = out / "fullrun-state.json"
    st = json.load(open(st_f)) if st_f.exists() else {"done": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "timings": {}}
    def step(name, fn):
        if name in st["done"]: log("skip (done):", name); return
        t = time.time(); log("STEP", name); fn()
        st["done"].append(name); st["timings"][name] = round(time.time() - t)
        json.dump(st, open(st_f, "w"), indent=1)
    step("snapshot_before", lambda: snap(out / "fullrun-flags-before.json", folder))
    step("tagger", lambda: run([TAG, str(folder), "--profile", a.profile, "--tag", "full", "--resume", "--no-caption"] + (["--context", a.context] if a.context else []),
                               env={"LOCALAI_NO_APPLY_STEP": "1"}))
    step("camera", lambda: run([TAG, "camera", str(folder)]))
    step("scene", lambda: run([TAG, "scene", str(folder), "--resume"]))
    step("index", lambda: run([TAG, "index", str(folder)]))
    for f in ("sharpness", "bursts", "rank"):
        step(f, lambda f=f: run([TAG, f, str(folder)] + (["--top", "50"] if f == "rank" else [])))
    step("ocr", lambda: run([TAG, "ocr", str(folder), "--resume"]))
    if not a.no_apply:
        from apply_xmp import apply_proposals
        srcs = {"tagger": f"{a.profile}-proposals-full", "camera": "camera-proposals", "scene": "scene-proposals", "sharpness": "sharpness-proposals", "bursts": "bursts-proposals",
                "rank": "rank-proposals", "ocr": "ocr-proposals"}
        srcs = {k: v for k, v in srcs.items() if (out / f"{v}.json").exists()}
        # AF-point focus check (afpoints.py, written by the sharpness step). On Target applies; Missed is
        # needs_review and goes to the panel's review screen. Skip with LOCALAI_AFPOINTS=0.
        if os.environ.get("LOCALAI_AFPOINTS", "1") != "0" and (out / "afpoints-proposals.json").exists():
            srcs["afpoints"] = "afpoints-proposals"
        def merge():
            merged, review = {}, {}
            for src, f in srcs.items():
                d = json.load(open(out / f"{f}.json"))
                for r in d["images"]:
                    m = merged.setdefault(r["filename"], {"filename": r["filename"], "sha256": r.get("sha256"), "proposed_keywords": [], "sources": {}})
                    if r.get("error"): review.setdefault(src, []).append([r["filename"], "error: " + str(r["error"])[:100]]); continue
                    if r.get("needs_review"):
                        review.setdefault(src, []).append([r["filename"], "; ".join(r.get("review_reasons", []))[:200]]); continue
                    if r.get("sha256") and m["sha256"] and r["sha256"] != m["sha256"]:
                        review.setdefault(src, []).append([r["filename"], "sha mismatch between steps"]); continue
                    for k in r.get("proposed_keywords", []):
                        if k not in m["proposed_keywords"]: m["proposed_keywords"].append(k); m["sources"][k] = src
                    if r.get("description"): m["description"] = r["description"]
            items = sorted(merged.values(), key=lambda x: x["filename"])
            json.dump({"folder": str(folder), "images": items, "skipped_review": review}, open(out / "fullrun-merged-proposals.json", "w"), indent=1)
            n = 200
            for i in range(0, len(items), n):
                json.dump({"folder": str(folder), "images": items[i:i + n]}, open(out / f"fullrun-chunk-{i // n:03d}.json", "w"))
            st["n_chunks"] = (len(items) + n - 1) // n
            st["review_skipped"] = {k: len(v) for k, v in review.items()}
        step("merge", merge)
        for ci in range(st["n_chunks"]):
            def go(ci=ci):
                res = apply_proposals(str(out / f"fullrun-chunk-{ci:03d}.json"), apply=True, prefix=str(out / f"apply-chunk-{ci:03d}"))
                st.setdefault("chunks", {})[str(ci)] = {"applied": len(res["applied"]), "skipped": len(res["skipped"]), "failures": len(res["failures"])}
            step(f"apply:chunk{ci:03d}", go)
        if not a.no_cull_albums:
            # keyword-based smart albums (Sharp+Burst Best / Rank Best / Shortlist) for this shoot; collections only
            step("cull_albums", lambda: run([str(HOME / "venv/bin/python"), str(HOME / "cull_album.py"), str(folder)]))
    def verify():
        before = json.load(open(out / "fullrun-flags-before.json")); after = snap(out / "fullrun-flags-after.json", folder)
        changed = [{"id": k, "before": before[k], "after": after.get(k)} for k in before if after.get(k, {}).get("rating") != before[k]["rating"] or after.get(k, {}).get("color") != before[k]["color"]]
        st["verify"] = {"variants": len(before), "rating_or_colour_changed": len(changed), "examples": changed[:20]}
    step("verify", verify)
    st["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S"); json.dump(st, open(st_f, "w"), indent=1)
    log("FINISHED", json.dumps({k: st.get(k) for k in ("timings", "apply", "verify")})[:2000])

if __name__ == "__main__":
    main()
