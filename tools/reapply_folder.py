#!/usr/bin/env python3
"""Re-apply a finished job's saved proposals to its own folder (folder-scoped apply, 2026-11-09 fix).

Per pass, additive keywords only (confident rows; needs_review rows are never applied), no captions,
MP4/MOV ignored, before/after rating+colour snapshots of that folder, undo data (*-before/-result.json).
Stops at the first refusal (Capture One scope mismatch). Optionally records the result files on the job
so the panel's Undo for that job also covers this re-apply.

usage: reapply_folder.py <folder> --profile gig --passes tagger,sharpness,afpoints,bursts [--job <job.json>]
       [--check NAME:RATING:COLOR ...] [--apply]
"""
import argparse, json, subprocess, sys, time
from pathlib import Path
HOME = Path.home() / "localai"; OUT = HOME / "out"
sys.path.insert(0, str(HOME))
from jobrunner import _proposal_file  # noqa: E402
from apply_c1 import apply_proposals  # noqa: E402

def snap(path, folder):
    subprocess.run(["osascript", "-l", "JavaScript", str(HOME / "snap.js"), str(path), "--folder", str(folder)],
                   check=True, capture_output=True, timeout=600)
    return json.load(open(path))

def checks(s, specs):
    bad = []
    for spec in specs:
        name, r, c = spec.split(":")
        hits = [v for v in s.values() if v["name"] == name]
        if len(hits) != 1 or hits[0]["rating"] != int(r) or hits[0]["color"] != int(c):
            bad.append({"name": name, "want": [int(r), int(c)], "got": hits})
    return bad

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--profile", default="gig")
    ap.add_argument("--passes", default="tagger,sharpness,afpoints,bursts")
    ap.add_argument("--job"); ap.add_argument("--check", action="append", default=[])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    folder = Path(a.folder.rstrip("/")); od = OUT / folder.name; stamp = time.strftime("%Y%m%d-%H%M%S")
    summ = {"folder": str(folder), "stamp": stamp, "apply": a.apply, "passes": {}, "result_files": []}
    def save():
        json.dump(summ, open(od / f"reapply-{stamp}-summary.json", "w"), indent=1)
    before = snap(od / f"reapply-{stamp}-flags-before.json", folder)
    summ["variants_in_folder"] = len(before)
    summ["checks_before"] = checks(before, a.check)
    print("before:", len(before), "variants; check failures:", summ["checks_before"], flush=True)
    if summ["checks_before"]:
        summ["stopped"] = "pre-check failed"; save(); sys.exit(2)
    for pid in a.passes.split(","):
        prop = _proposal_file(folder, pid, a.profile)
        if not prop:
            summ["passes"][pid] = {"skipped": "no proposals"}; print(pid, "no proposals"); continue
        imgs = [r for r in json.load(open(prop)).get("images", [])
                if r.get("proposed_keywords") and not r.get("needs_review") and not r.get("error")
                and not str(r.get("filename", "")).lower().endswith((".mp4", ".mov"))
                and not (pid == "afpoints" and "General/Focus/Missed" in r["proposed_keywords"])]
        tot = fails = 0; var = set(); refused = None; skipped = 0
        for i in range(0, len(imgs), 200):
            cf = od / f"gui-reapply-{pid}-{stamp}-chunk-{i // 200:03d}.json"
            json.dump({"folder": str(folder), "images": imgs[i:i + 200]}, open(cf, "w"))
            prefix = str(od / f"gui-apply-reapply-{pid}-{stamp}-c{i // 200:03d}")
            res = apply_proposals(str(cf), apply=a.apply, prefix=prefix)
            summ["result_files"].append(prefix + "-result.json")
            tot += len(res.get("applied", [])); fails += len(res.get("failures", [])); skipped += len(res.get("skipped", []))
            var.update(x["filename"] for x in res.get("applied", []))
            if res.get("refused"):
                refused = res["refused"]; break
        summ["passes"][pid] = {"proposals": prop.name, "confident_frames": len(imgs), "applied_keywords": tot,
                               "variants": len(var), "already_or_skipped": skipped, "failures": fails, "refused": refused}
        print(pid, summ["passes"][pid], flush=True); save()
        if refused:
            summ["stopped"] = f"refused in {pid}: {refused}"; break
    after = snap(od / f"reapply-{stamp}-flags-after.json", folder)
    changed = [{"id": k, "name": v["name"], "before": [v["rating"], v["color"]],
                "after": [after[k]["rating"], after[k]["color"]] if k in after else None}
               for k, v in before.items() if k not in after or (after[k]["rating"], after[k]["color"]) != (v["rating"], v["color"])]
    summ["flags_changed"] = changed; summ["checks_after"] = checks(after, a.check)
    print("after:", len(after), "variants; flags changed:", len(changed), "; check failures:", summ["checks_after"])
    if a.apply and a.job:
        jp = Path(a.job); j = json.load(open(jp))
        if j.get("status") not in ("running", "queued"):
            j.setdefault("undo_result_files", [])
            j["undo_result_files"] += [f for f in summ["result_files"] if f not in j["undo_result_files"]]
            ak = j.setdefault("added_keywords", {})
            for f in summ["result_files"]:
                for x in json.load(open(f)).get("applied") or []:
                    if x.get("dry") or not x.get("keyword"): continue
                    l = ak.setdefault(x["filename"], [])
                    if x["keyword"] not in l: l.append(x["keyword"])
            j["reapply"] = {"stamp": stamp, "summary": str(od / f"reapply-{stamp}-summary.json"),
                            "passes": summ["passes"], "flags_changed": len(changed)}
            tmp = jp.with_suffix(".tmp"); tmp.write_text(json.dumps(j, indent=1, ensure_ascii=False)); tmp.replace(jp)
            summ["job_updated"] = str(jp)
    save(); print("summary:", od / f"reapply-{stamp}-summary.json")

if __name__ == "__main__":
    main()
