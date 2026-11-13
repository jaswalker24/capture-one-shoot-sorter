#!/usr/bin/env python3
"""Build undo plan from a job's apply result files; dry-run or apply via apply_xmp."""
from __future__ import annotations
import os
import json, time
from pathlib import Path
from collections import defaultdict

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
OUT = HOME / "out"

def collect_added_keywords(job: dict) -> dict:
    """Return {filename: [keywords added by this job]} from gui-apply-*-result.json and apply-chunk results referenced in logs/out."""
    folder_name = job.get("folder_name") or Path(job.get("folder") or "").name
    fo = OUT / folder_name
    added = defaultdict(list)
    sources = []
    # Prefer job-scoped gui-apply results
    jid = job.get("id") or ""
    patterns = [
        f"gui-apply-*-result.json",
        f"gui-*-result.json",
        f"apply-chunk-*-result.json",
    ]
    # Also look for prefixes stored on the job
    for p in sorted(fo.glob("*-result.json")) if fo.exists() else []:
        try:
            r = json.load(open(p))
        except Exception:
            continue
        apps = r.get("applied") or []
        if not apps:
            continue
        # Heuristic: include if job id appears in path, or job started around file mtime, or job lists it
        use = False
        if jid and jid in p.name:
            use = True
        elif any(jid[:8] in str(p) for _ in [0]):
            use = True
        # gui-apply with timestamp overlapping job
        if p.name.startswith("gui-apply-") or p.name.startswith("gui-"):
            use = True
        if job.get("undo_result_files") and str(p) in job["undo_result_files"]:
            use = True
        if not use and job.get("type") == "fullrun_legacy":
            use = p.name.startswith("apply-chunk-")
        if not use:
            # If job recorded pass_results with apply timestamps, still allow gui-apply from same day as job
            if job.get("started") and p.name.startswith("gui-apply-"):
                use = True
        if not use:
            continue
        sources.append(str(p))
        for a in apps:
            if a.get("dry"):
                continue
            fn, kw = a.get("filename"), a.get("keyword")
            if fn and kw and kw not in added[fn]:
                added[fn].append(kw)
    # Prefer explicit list from job if present
    if job.get("added_keywords"):
        added = defaultdict(list)
        for fn, kws in job["added_keywords"].items():
            added[fn] = list(kws)
        sources = job.get("undo_result_files") or sources
    return {"folder": job.get("folder"), "folder_name": folder_name, "items":
            [{"filename": fn, "keywords": kws} for fn, kws in sorted(added.items())],
            "sources": sources,
            "keyword_count": sum(len(v) for v in added.values()),
            "variant_count": len(added)}



def run_undo(job: dict, apply: bool = False, prefix: str | None = None):
    plan_data = collect_added_keywords(job)
    if not plan_data["items"]:
        return {"ok": False, "error": "no applied keywords found for this job", "plan": plan_data}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = prefix or str(HOME / "out" / plan_data["folder_name"] / f"undo-{job.get('id','job')}-{stamp}")
    plan_path = prefix + "-plan.json"
    plan = {"items": plan_data["items"], "job_id": job.get("id"),
            "sources": plan_data["sources"],
            "scope_folder": job.get("folder") or plan_data.get("folder"),
            "folder": job.get("folder") or plan_data.get("folder")}
    Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(plan, open(plan_path, "w"), indent=1)
    import sys as _sys
    _sys.path.insert(0, str(HOME))
    import apply_xmp as AX
    res = AX.undo_plan(plan, apply=apply, prefix=prefix)
    return {"ok": True, "dry_run": not apply, "plan": plan_data, "plan_path": plan_path,
            "result_path": prefix + "-result.json", "removed": len(res.get("removed") or []),
            "skipped": len(res.get("skipped") or []), "failures": res.get("failures") or [],
            "result": res}
