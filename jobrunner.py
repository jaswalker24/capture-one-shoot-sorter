#!/usr/bin/env python3
"""Pass-by-pass Local AI job runner for the control panel.

Applies keywords after each pass when apply_each=True (default).
Always uses --no-caption for catalog tagging. Never touches stars/colours.
Does not modify fullrun.py; reuses the same CLI entrypoints (tag / apply_c1).
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time, traceback
from pathlib import Path

HOME = Path.home() / "localai"
sys.path.insert(0, str(HOME))
TAG = str(HOME / "tag")
SNAP_JS = HOME / "snap.js"
OUT = HOME / "out"
IMPORTS_ROOT = Path("/Volumes/Photos/Camera Imports")
EXPORTS_ROOT = Path("/Volumes/Photos/Camera Exports")

# Pass catalogue. Rank is available (percentile calibration is in features.py) but
# score keywords stay OFF unless apply_rank_keywords is explicitly True.
PASSES = [
    {"id": "tagger", "label": "Tag with Local AI", "needs_index": False},
    {"id": "index", "label": "Index (search)", "needs_index": False},
    {"id": "sharpness", "label": "Sharpness", "needs_index": True},
    {"id": "bursts", "label": "Bursts", "needs_index": True},
    {"id": "rank", "label": "Rank", "needs_index": True,
     "note": "Ordering CSV always; Score keywords opt-in (off by default)."},
    {"id": "ocr", "label": "Read Text in Frame", "needs_index": False},
    {"id": "schedule", "label": "Tag by Schedule", "needs_index": False},
]
PASS_BY_ID = {p["id"]: p for p in PASSES}
DEFAULT_ORDER = ["tagger", "index", "sharpness", "bursts", "ocr", "schedule"]  # rank omitted by default


def log(state, *a):
    line = time.strftime("%H:%M:%S") + " " + " ".join(str(x) for x in a)
    print(line, flush=True)
    state.setdefault("log", []).append(line)
    state["log"] = state["log"][-400:]
    _save(state)


def _save(state):
    path = Path(state["_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = {k: v for k, v in state.items() if not k.startswith("_")}
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def snap(path: Path):
    subprocess.run(["osascript", "-l", "JavaScript", str(SNAP_JS), str(path)],
                   check=True, capture_output=True)
    return json.load(open(path))


def _run_streaming(state, cmd, env=None, pass_id=None):
    log(state, "$", " ".join(cmd[:8]), ("..." if len(cmd) > 8 else ""))
    env_full = {**os.environ, **(env or {})}
    p = subprocess.Popen(cmd, env=env_full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    state["_proc"] = p
    state["pid"] = p.pid
    _save(state)
    tail = []
    try:
        for line in p.stdout:
            if re.search(r"warn|detach\(\)|logit_scale|FBBA|UserWarning", line, re.I):
                continue
            line = line.rstrip()
            tail = (tail + [line])[-60:]
            m = re.match(r"^\[(\d+)/(\d+)\]", line)
            if m:
                n, total = int(m.group(1)), int(m.group(2))
                state["progress"] = {"pass": pass_id, "n": n, "total": total, "line": line[:160]}
                if n == 1 or n == total or n % 25 == 0:
                    log(state, "  ", line[:160])
                else:
                    _save(state)
            else:
                if line:
                    log(state, "  ", line[:200])
        rc = p.wait()
    finally:
        state["_proc"] = None
        state["pid"] = None
    if state.get("cancel_requested"):
        raise RuntimeError("cancelled")
    if rc:
        raise RuntimeError(f"step failed rc={rc}: {' '.join(cmd[:4])}")
    return tail


def _proposal_file(folder: Path, pass_id: str, profile: str) -> Path | None:
    fo = OUT / folder.name
    mapping = {
        "tagger": fo / f"{profile}-proposals-full.json",
        "sharpness": fo / "sharpness-proposals.json",
        "bursts": fo / "bursts-proposals.json",
        "rank": fo / "rank-proposals.json",
        "ocr": fo / "ocr-proposals.json",
        "schedule": fo / "schedule-proposals.json",
    }
    p = mapping.get(pass_id)
    return p if p and p.exists() else None


def _files_missing_car_number(folder: Path, profile: str) -> list[Path]:
    """Frames with no Motorsport/Car Number/* in tagger proposals (proposed or tentative)."""
    prop = OUT / folder.name / f"{profile}-proposals-full.json"
    if not prop.exists():
        # fall back to any motorsport proposals
        cands = sorted((OUT / folder.name).glob("*-proposals*.json"))
        prop = next((c for c in cands if "motorsport" in c.name and "full" in c.name), None)
    if not prop or not prop.exists():
        return []
    data = json.load(open(prop))
    missing = []
    for r in data.get("images", []):
        kws = (r.get("proposed_keywords") or []) + (r.get("tentative_keywords") or [])
        if any("Car Number/" in k for k in kws):
            continue
        p = folder / r["filename"]
        if p.exists():
            missing.append(p)
    return missing


def _apply_pass(state, folder: Path, pass_id: str, profile: str, apply: bool):
    if pass_id in ("index",):
        return {"skipped": "no keywords"}
    if pass_id == "rank" and not state.get("apply_rank_keywords"):
        log(state, "skip apply for rank (score keywords disabled)")
        return {"skipped": "rank keywords disabled"}
    prop = _proposal_file(folder, pass_id, profile)
    if not prop:
        log(state, "no proposals for", pass_id)
        return {"skipped": "no proposals"}
    from apply_c1 import apply_proposals
    prefix = str(OUT / folder.name / f"gui-apply-{pass_id}-{time.strftime('%H%M%S')}")
    # Only apply confident rows; filter needs_review already in apply_proposals
    # For tagger: prefer non-review. apply_proposals handles that.
    # Chunk large sets
    data = json.load(open(prop))
    images = [r for r in data.get("images", []) if r.get("proposed_keywords") and not r.get("needs_review") and not r.get("error")]
    if pass_id == "rank":
        # even if apply_rank_keywords, only those with Score keywords
        images = [r for r in images if any(k.startswith("General/Score/") for k in r.get("proposed_keywords") or [])]
    if not images:
        log(state, f"apply {pass_id}: nothing confident to apply")
        return {"applied": 0, "variants": 0}
    chunk_n = 200
    total_applied = 0
    variants = set()
    failures = 0
    for i in range(0, len(images), chunk_n):
        chunk = {"folder": str(folder), "images": images[i:i + chunk_n]}
        cf = OUT / folder.name / f"gui-{pass_id}-chunk-{i // chunk_n:03d}.json"
        json.dump(chunk, open(cf, "w"))
        res = apply_proposals(str(cf), apply=apply, prefix=f"{prefix}-c{i // chunk_n:03d}")
        total_applied += len(res.get("applied", []))
        variants.update(a["filename"] for a in res.get("applied", []))
        failures += len(res.get("failures", []))
        if state.get("cancel_requested"):
            raise RuntimeError("cancelled")
    out = {"applied_keywords": total_applied, "variants": len(variants), "failures": failures, "dry_run": not apply}
    log(state, f"apply {pass_id}:", out)
    return out


def folder_already_indexed(folder: Path) -> bool:
    try:
        import sqlite3
        db = HOME / "index" / "catalog.sqlite"
        if not db.exists():
            return False
        c = sqlite3.connect(str(db))
        n = c.execute("SELECT COUNT(*) FROM images WHERE folder=?", (folder.name,)).fetchone()[0]
        c.close()
        return n > 0
    except Exception:
        return False


def ensure_pass_order(passes: list[str], folder: Path | None = None) -> list[str]:
    """If sharpness/bursts/rank selected, ensure index runs first — unless already indexed."""
    needs = any(PASS_BY_ID.get(p, {}).get("needs_index") for p in passes)
    out = list(passes)
    if needs and "index" not in out:
        if folder is not None and folder_already_indexed(folder):
            return out
        if "tagger" in out:
            out.insert(out.index("tagger") + 1, "index")
        else:
            out.insert(0, "index")
    return out


def run_job(state_path: Path):
    state = json.load(open(state_path))
    state["_path"] = str(state_path)
    state["status"] = "running"
    state["started"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["cancel_requested"] = False
    state.setdefault("log", [])
    state.setdefault("pass_results", {})
    _save(state)

    folder = Path(state["folder"])
    profile = state.get("profile") or "motorsport"
    context = state.get("context") or ""
    apply_each = bool(state.get("apply_each", True))
    dry_run = bool(state.get("dry_run", False))
    apply = apply_each and not dry_run
    ocr_missing_only = bool(state.get("ocr_missing_car_number", False)) and profile == "motorsport"
    sample = int(state.get("sample") or 0)
    passes = ensure_pass_order([p for p in state.get("passes") or DEFAULT_ORDER if p in PASS_BY_ID], folder)
    state["passes"] = passes
    state["apply_rank_keywords"] = bool(state.get("apply_rank_keywords", False))
    out = OUT / folder.name
    out.mkdir(parents=True, exist_ok=True)

    try:
        if not folder.is_dir():
            raise RuntimeError(f"folder not found: {folder}")
        log(state, "JOB", folder.name, "profile=", profile, "passes=", passes,
            "apply_each=", apply, "dry_run=", dry_run, "sample=", sample)

        # Full-catalog snapshot is expensive (and can stall while another C1 job runs).
        # Skip for sample jobs; for full jobs, time out after 90s so the queue does not hang.
        before_path = out / f"gui-flags-before-{state['id']}.json"
        if sample or dry_run:
            log(state, "snapshot before skipped (sample/dry-run)")
            state["snapshot_before_skipped"] = "sample" if sample else "dry_run"
        else:
            try:
                log(state, "snapshot before (90s timeout)...")
                proc = subprocess.Popen(["osascript", "-l", "JavaScript", str(SNAP_JS), str(before_path)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    proc.communicate(timeout=90)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise TimeoutError("snapshot before timed out after 90s")
                if proc.returncode:
                    raise RuntimeError(f"snapshot rc={proc.returncode}")
                state["snapshot_before"] = str(before_path)
                log(state, "snapshot before ok,", len(json.load(open(before_path))), "variants")
            except Exception as e:
                log(state, "snapshot before skipped:", e)
                state["snapshot_before_error"] = str(e)

        for i, pid in enumerate(passes):
            if state.get("cancel_requested"):
                raise RuntimeError("cancelled")
            state["current_pass"] = pid
            state["progress"] = {"pass": pid, "n": 0, "total": 0, "line": ""}
            log(state, f"PASS {i+1}/{len(passes)}:", pid)
            t0 = time.time()

            if pid == "tagger":
                cmd = [TAG, str(folder), "--profile", profile, "--tag", "full", "--resume", "--no-caption"]
                if context:
                    cmd += ["--context", context]
                if sample:
                    cmd += ["--sample", str(sample)]
                _run_streaming(state, cmd, env={"LOCALAI_NO_APPLY_STEP": "1"}, pass_id=pid)

            elif pid == "index":
                cmd = [TAG, "index", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid in ("sharpness", "bursts"):
                cmd = [TAG, pid, str(folder)]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "rank":
                cmd = [TAG, "rank", str(folder), "--top", "50"]
                if state.get("apply_rank_keywords"):
                    cmd.append("--apply-score-keywords")
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "ocr":
                cmd = [TAG, "ocr", str(folder), "--resume"]
                if sample:
                    cmd += ["--sample", str(sample)]
                if ocr_missing_only:
                    missing = _files_missing_car_number(folder, profile)
                    log(state, f"ocr missing-car-number filter: {len(missing)} frames")
                    if not missing:
                        log(state, "ocr: nothing missing a car number; skip")
                        state["pass_results"][pid] = {"elapsed": 0, "skipped": "none missing"}
                        _save(state)
                        continue
                    # pass in batches via --files
                    batch = 80
                    for bi in range(0, len(missing), batch):
                        chunk = missing[bi:bi + batch]
                        c2 = [TAG, "ocr", "--files"] + [str(p) for p in chunk]
                        _run_streaming(state, c2, pass_id=pid)
                        if state.get("cancel_requested"):
                            raise RuntimeError("cancelled")
                else:
                    _run_streaming(state, cmd, pass_id=pid)

            elif pid == "schedule":
                cmd = [TAG, "schedule", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                sched = state.get("schedule_path") or str(HOME / "schedules/example.json")
                cmd += ["--schedule", sched]
                _run_streaming(state, cmd, pass_id=pid)

            else:
                raise RuntimeError(f"unknown pass {pid}")

            elapsed = round(time.time() - t0)
            result = {"elapsed": elapsed}
            if apply_each or dry_run:
                try:
                    result["apply"] = _apply_pass(state, folder, pid, profile, apply=apply)
                except Exception as e:
                    result["apply_error"] = str(e)
                    log(state, "apply error:", e)
            state["pass_results"][pid] = result
            state.setdefault("timings", {})[pid] = elapsed
            _save(state)
            log(state, f"PASS done {pid} in {elapsed}s")

        # verify stars/colours unchanged (skip for sample jobs)
        if sample or dry_run:
            log(state, "verify skipped (sample/dry-run)")
            state["verify_skipped"] = "sample" if sample else "dry_run"
        else:
            try:
                after_path = out / f"gui-flags-after-{state['id']}.json"
                log(state, "snapshot after (90s timeout)...")
                proc = subprocess.Popen(["osascript", "-l", "JavaScript", str(SNAP_JS), str(after_path)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    proc.communicate(timeout=90)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise TimeoutError("snapshot after timed out after 90s")
                if proc.returncode:
                    raise RuntimeError(f"snapshot after rc={proc.returncode}")
                after = json.load(open(after_path))
                state["snapshot_after"] = str(after_path)
                if state.get("snapshot_before") and Path(state["snapshot_before"]).exists():
                    before = json.load(open(state["snapshot_before"]))
                    changed = [{"id": k, "before": before[k], "after": after.get(k)}
                               for k in before
                               if after.get(k, {}).get("rating") != before[k].get("rating")
                               or after.get(k, {}).get("color") != before[k].get("color")]
                    state["verify"] = {"variants": len(before), "rating_or_colour_changed": len(changed),
                                       "examples": changed[:20]}
                    log(state, "verify:", state["verify"])
            except Exception as e:
                state["verify_error"] = str(e)
                log(state, "verify skipped:", e)

        state["status"] = "cancelled" if state.get("cancel_requested") else "done"
        state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["current_pass"] = None
        log(state, "FINISHED", state["status"])
    except Exception as e:
        state["status"] = "cancelled" if state.get("cancel_requested") else "error"
        state["error"] = str(e)
        state["traceback"] = traceback.format_exc()[-2000:]
        state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        log(state, "ERROR", e)
        _save(state)
        raise
    finally:
        state["_proc"] = None
        _save(state)


def request_cancel(state_path: Path):
    state = json.load(open(state_path))
    state["cancel_requested"] = True
    state["_path"] = str(state_path)
    _save(state)
    # best-effort: kill child process group if we recorded pid from a live runner — GUI worker tracks it
    return state


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: jobrunner.py <job-state.json>")
        sys.exit(2)
    run_job(Path(sys.argv[1]))
