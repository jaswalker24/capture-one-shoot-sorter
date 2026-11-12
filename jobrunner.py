#!/usr/bin/env python3
"""Pass-by-pass Local AI job runner for the control panel.

Applies keywords after each pass when apply_each=True (default).
Always uses --no-caption for catalog tagging. Never touches stars/colours.
Does not modify fullrun.py; reuses the same CLI entrypoints (tag / apply_c1).
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time, traceback
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
sys.path.insert(0, str(HOME))
TAG = str(HOME / "tag")
SNAP_JS = HOME / "snap.js"
OUT = HOME / "out"
def _nas_roots():
    try:
        import nas_paths as NP
        NP.HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
        return NP.imports_root(), NP.exports_root()
    except Exception:
        photos = Path("/Volumes/Photos")
        return photos / "Camera Imports", photos / "Camera Exports"

IMPORTS_ROOT, EXPORTS_ROOT = _nas_roots()

# Pass catalogue. Rank is calibrated (features.py cmd_rank) and its General/Rank/{Best,Good}
# keywords are applied by default (apply_rank_keywords=False turns them off).
PASSES = [
    {"id": "tagger", "label": "Tag with Local AI", "needs_index": False},
    {"id": "index", "label": "Index (search)", "needs_index": False},
    {"id": "sharpness", "label": "Sharpness", "needs_index": True,
     "note": "Includes the AF focus check (Canon AF points) unless turned off below: On Target applies, Missed goes to Review."},
    {"id": "bursts", "label": "Bursts", "needs_index": True},
    {"id": "rank", "label": "Rank (calibrated)", "needs_index": True,
     "note": "Top 10% General/Rank/Best, next 20% General/Rank/Good (calibrated on catalog star ratings)."},
    {"id": "ocr", "label": "Read Text in Frame", "needs_index": False},
    {"id": "people", "label": "People (faces)", "needs_index": False,
     "note": "Local face clusters + People/<Name> keywords. Off by default; name clusters in the People tab first."},
    {"id": "camera", "label": "Camera info", "needs_index": False,
     "note": "EXIF / Canon MakerNotes + a cheap preview clip check: lens, focal band, High ISO, Wide Open, Slow Shutter, drive, electronic shutter, AF, IS, orientation, Flash Fired, clipped highlights. Fast."},
    {"id": "scene", "label": "Scene (general keywords)", "needs_index": False,
     "note": "General/Scene/* multi-label tags (architecture, street, concert, motorsport, temple, food, …). Local SigLIP, conservative thresholds."},
    {"id": "schedule", "label": "Tag by Schedule", "needs_index": False},
    {"id": "cull", "label": "Cull smart albums", "needs_index": False,
     "note": "Smart albums in group 'Trips Local AI': Sharp + Burst Best, Rank Best, Shortlist (no blur). Collections only."},
]
PASS_BY_ID = {p["id"]: p for p in PASSES}
DEFAULT_ORDER = ["tagger", "camera", "scene", "index", "sharpness", "bursts", "rank", "ocr", "schedule", "cull"]


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
        "people": fo / "people-proposals.json",
        "scene": fo / "scene-proposals.json",
        "camera": fo / "camera-proposals.json",
        "schedule": fo / "schedule-proposals.json",
        "afpoints": fo / "afpoints-proposals.json",
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
    """Apply one pass's proposals. Sharpness also carries the AF focus check (afpoints) when enabled."""
    res = _apply_source(state, folder, pass_id, profile, apply)
    if pass_id == "sharpness" and state.get("afpoints", True) and not state.get("sample"):
        prop = _proposal_file(folder, "afpoints", profile)
        if prop and prop.stat().st_mtime >= (state.get("_pass_t0") or 0) - 5:
            n_missed = sum(1 for r in json.load(open(prop)).get("images", [])
                           if r.get("needs_review") or "General/Focus/Missed" in (r.get("proposed_keywords") or []))
            af = _apply_source(state, folder, "afpoints", profile, apply, label="sharpness-afpoints")
            af["missed_to_review"] = n_missed
            log(state, f"AF focus check: {n_missed} Missed frames sent to Review (not applied)")
            res = {"sharpness": res, "afpoints": af}
        else:
            log(state, "AF focus check: no fresh afpoints proposals (no AF data or hook skipped)")
    return res


def _apply_source(state, folder: Path, pass_id: str, profile: str, apply: bool, label: str | None = None):
    if pass_id in ("index", "cull"):
        return {"skipped": "no keywords"}
    if pass_id == "rank" and not state.get("apply_rank_keywords"):
        log(state, "skip apply for rank (rank keywords disabled)")
        return {"skipped": "rank keywords disabled"}
    prop = _proposal_file(folder, pass_id, profile)
    if not prop:
        log(state, "no proposals for", pass_id)
        return {"skipped": "no proposals"}
    from apply_c1 import apply_proposals
    prefix = str(OUT / folder.name / f"gui-apply-{label or pass_id}-{time.strftime('%H%M%S')}")
    # Only apply confident rows; filter needs_review already in apply_proposals
    # For tagger: prefer non-review. apply_proposals handles that.
    # Chunk large sets
    data = json.load(open(prop))
    images = [r for r in data.get("images", []) if r.get("proposed_keywords") and not r.get("needs_review") and not r.get("error")]
    if pass_id == "afpoints":
        # AF focus Missed goes to the Review screen, never straight to the catalog (also for older proposals files)
        images = [r for r in images if "General/Focus/Missed" not in (r.get("proposed_keywords") or [])]
    if pass_id == "rank":
        # calibrated tiers only (legacy General/Score/* buckets are retired)
        images = [r for r in images if any(k.startswith("General/Rank/") for k in r.get("proposed_keywords") or [])]
    if not images:
        log(state, f"apply {pass_id}: nothing confident to apply")
        return {"applied": 0, "variants": 0}
    chunk_n = 200
    total_applied = 0
    variants = set()
    failures = 0
    for i in range(0, len(images), chunk_n):
        chunk = {"folder": str(folder), "images": images[i:i + chunk_n]}
        cf = OUT / folder.name / f"gui-{label or pass_id}-chunk-{i // chunk_n:03d}.json"
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


def _apply_approved(state, folder: Path, apply: bool):
    """apply_only job: apply exactly the keyword plan approved in the Keyword preview (nothing else)."""
    from apply_c1 import apply_proposals
    plan = json.load(open(state["approved_plan"]))["plan"]
    stamp = time.strftime("%H%M%S")
    state.setdefault("undo_result_files", []); state.setdefault("added_keywords", {})
    for src, images in plan.items():
        if state.get("cancel_requested"):
            raise RuntimeError("cancelled")
        state["current_pass"] = src
        log(state, f"APPLY approved {src}: {len(images)} frames")
        tot = fails = 0; variants = set()
        for i in range(0, len(images), 200):
            cf = OUT / folder.name / f"gui-approved-{src}-{state['id']}-chunk-{i // 200:03d}.json"
            json.dump({"folder": str(folder), "images": images[i:i + 200]}, open(cf, "w"))
            prefix = str(OUT / folder.name / f"gui-apply-{src}-approved-{state['id']}-{stamp}-c{i // 200:03d}")
            res = apply_proposals(str(cf), apply=apply, prefix=prefix)
            tot += len(res.get("applied", [])); fails += len(res.get("failures", []))
            variants.update(a["filename"] for a in res.get("applied", []))
            if apply:
                state["undo_result_files"].append(prefix + "-result.json")
                for a in res.get("applied") or []:
                    fn, kw = a.get("filename"), a.get("keyword")
                    if fn and kw and not a.get("dry"):
                        lst = state["added_keywords"].setdefault(fn, [])
                        if kw not in lst: lst.append(kw)
            state["progress"] = {"pass": src, "n": min(i + 200, len(images)), "total": len(images), "line": ""}
            _save(state)
        out = {"applied_keywords": tot, "variants": len(variants), "failures": fails, "dry_run": not apply}
        state["pass_results"][src] = {"apply": out}
        log(state, f"apply approved {src}:", out)


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



def run_rank_train(state):
    """Queued rank training: match exports, optionally index missing, train/eval.

    Read-only on the NAS. Never talks to Capture One and never writes stars,
    colour tags or rank keywords.
    """
    log(state, "RANK TRAIN — NAS read-only; no Capture One, stars, colours or rank keyword writes")
    state["current_pass"] = "train"
    state["progress"] = {"pass": "train", "n": 0, "total": 0, "line": "starting"}
    state["apply_each"] = False
    state["apply_rank_keywords"] = False
    state["dry_run"] = True
    py = HOME / "venv" / "bin" / "python"
    script = HOME / "tools" / "train_rank_exports.py"
    if not script.is_file():
        alt = Path(__file__).resolve().parent / "tools" / "train_rank_exports.py"
        if alt.is_file():
            script = alt
    if not script.is_file():
        raise RuntimeError(f"train_rank_exports.py not found at {script}")
    exe = str(py) if py.is_file() else sys.executable
    cmd = [exe, str(script)]
    for root in state.get("export_roots") or []:
        cmd += ["--export-root", str(root)]
    if state.get("index_missing"):
        cmd.append("--index-missing")
    else:
        cmd.append("--no-index-missing")
    if state.get("auto_adopt"):
        cmd.append("--write-config")
    if "use_star_labels" in state:
        cmd.append("--use-star-labels" if state.get("use_star_labels") else "--no-use-star-labels")
    log(state, "roots:", ", ".join(str(r) for r in (state.get("export_roots") or [])) or "(default)")
    log(state, "index_missing=", bool(state.get("index_missing")),
        "auto_adopt=", bool(state.get("auto_adopt")),
        "use_star_labels=", state.get("use_star_labels"))
    _run_streaming(state, cmd, pass_id="train")
    latest = HOME / "out" / "rank-exports" / "train-latest.json"
    if latest.is_file():
        state["report_path"] = str(latest)
        try:
            rep = json.loads(latest.read_text(encoding="utf-8"))
            train = rep.get("train") or {}
            state["train_summary"] = {
                "stamp": rep.get("stamp"),
                "adopt": train.get("adopt"),
                "adopt_reason": train.get("adopt_reason"),
                "after_loso_macro": train.get("after_loso_macro"),
                "before": (train.get("before") or {}).get("overall"),
                "export_roots": rep.get("export_roots"),
                "sources_used": rep.get("sources_used"),
                "use_star_labels": rep.get("use_star_labels"),
            }
        except Exception as e:
            log(state, "could not read report:", e)
    state["status"] = "done"
    state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["current_pass"] = None
    log(state, "RANK TRAIN finished")
    _save(state)


def run_job(state_path: Path):
    state = json.load(open(state_path))
    state["_path"] = str(state_path)
    state["status"] = "running"
    state["started"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["cancel_requested"] = False
    state.setdefault("log", [])
    state.setdefault("pass_results", {})
    _save(state)

    if state.get("kind") == "rank_train":
        try:
            run_rank_train(state)
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
        return

    folder = Path(state["folder"])
    profile = state.get("profile") or "motorsport"
    context = state.get("context") or ""
    apply_each = bool(state.get("apply_each", True))
    dry_run = bool(state.get("dry_run", False))
    apply = apply_each and not dry_run
    # Keyword preview: run the passes without writing, then wait for approval in the panel
    preview_first = bool(state.get("preview_before_apply")) and not dry_run and not state.get("apply_only")
    apply_only = bool(state.get("apply_only"))
    if preview_first:
        apply_each = False; apply = False
    if apply_only:
        apply = not dry_run
    ocr_missing_only = bool(state.get("ocr_missing_car_number", False)) and profile == "motorsport"
    sample = int(state.get("sample") or 0)
    passes = ensure_pass_order([p for p in state.get("passes") or DEFAULT_ORDER if p in PASS_BY_ID], folder)
    state["passes"] = passes
    state["apply_rank_keywords"] = bool(state.get("apply_rank_keywords", True))
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
        if sample or dry_run or preview_first:
            log(state, "snapshot before skipped (sample/dry-run/preview)")
            state["snapshot_before_skipped"] = "sample" if sample else ("dry_run" if dry_run else "preview")
        else:
            try:
                log(state, "snapshot before, this folder only (180s timeout)...")
                proc = subprocess.Popen(["osascript", "-l", "JavaScript", str(SNAP_JS), str(before_path), "--folder", str(folder)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    proc.communicate(timeout=180)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise TimeoutError("snapshot before timed out after 180s")
                if proc.returncode:
                    raise RuntimeError(f"snapshot rc={proc.returncode}")
                state["snapshot_before"] = str(before_path)
                log(state, "snapshot before ok,", len(json.load(open(before_path))), "variants")
            except Exception as e:
                log(state, "snapshot before skipped:", e)
                state["snapshot_before_error"] = str(e)

        if apply_only:
            _apply_approved(state, folder, apply)
        for i, pid in enumerate([] if apply_only else passes):
            if state.get("cancel_requested"):
                raise RuntimeError("cancelled")
            state["current_pass"] = pid
            state["progress"] = {"pass": pid, "n": 0, "total": 0, "line": ""}
            log(state, f"PASS {i+1}/{len(passes)}:", pid)
            t0 = time.time()
            state["_pass_t0"] = t0

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
                env = None
                if pid == "sharpness":
                    # AF-point focus check runs inside the sharpness step (features.py hook -> afpoints.py)
                    env = {"LOCALAI_AFPOINTS": "1" if state.get("afpoints", True) else "0",
                           "LOCALAI_AFPOINTS_ANNOTATE": str(int(state.get("afpoints_annotate") or 0))}
                    log(state, "AF focus check:", "on" if state.get("afpoints", True) else "off",
                        f"(annotate {env['LOCALAI_AFPOINTS_ANNOTATE']})" if env["LOCALAI_AFPOINTS_ANNOTATE"] != "0" else "")
                _run_streaming(state, cmd, env=env, pass_id=pid)

            elif pid == "rank":
                cmd = [TAG, "rank", str(folder), "--top", "50"]
                if not state.get("apply_rank_keywords"):
                    cmd.append("--no-keywords")
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

            elif pid == "people":
                cmd = [TAG, "faces", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "camera":
                cmd = [TAG, "camera", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "scene":
                cmd = [TAG, "scene", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "schedule":
                cmd = [TAG, "schedule", str(folder)]
                if sample:
                    cmd += ["--sample", str(sample)]
                sched = state.get("schedule_path") or str(HOME / "schedules/example.json")
                cmd += ["--schedule", sched]
                if state.get("camera_offset"):
                    cmd += ["--camera-offset", state["camera_offset"]]
                _run_streaming(state, cmd, pass_id=pid)

            elif pid == "cull":
                if sample or dry_run:
                    log(state, "cull albums skipped (sample/dry-run)")
                else:
                    _run_streaming(state, [str(HOME / "venv/bin/python"), str(HOME / "cull_album.py"), str(folder)], pass_id=pid)

            else:
                raise RuntimeError(f"unknown pass {pid}")

            elapsed = round(time.time() - t0)
            result = {"elapsed": elapsed}
            if apply_each or dry_run:
                try:
                    result["apply"] = _apply_pass(state, folder, pid, profile, apply=apply)
                    # Track keywords this job added (for undo)
                    if apply and isinstance(result.get("apply"), dict):
                        state.setdefault("undo_result_files", [])
                        state.setdefault("added_keywords", {})
                        fo = OUT / folder.name
                        for rp in sorted(fo.glob(f"gui-apply-{pid}-*-result.json")):
                            # only results from this job id prefix if present
                            if state["id"][:8] not in rp.name and state["id"] not in rp.name:
                                # gui-apply-{pid}-{HHMMSS}-c000-result.json — accept recent ones from this pass call
                                pass
                            try:
                                rr = json.load(open(rp))
                            except Exception:
                                continue
                            if str(rp) not in state["undo_result_files"]:
                                state["undo_result_files"].append(str(rp))
                            for a in rr.get("applied") or []:
                                if a.get("dry"): continue
                                fn, kw = a.get("filename"), a.get("keyword")
                                if not fn or not kw: continue
                                lst = state["added_keywords"].setdefault(fn, [])
                                if kw not in lst: lst.append(kw)
                except Exception as e:
                    result["apply_error"] = str(e)
                    log(state, "apply error:", e)
            state["pass_results"][pid] = result
            state.setdefault("timings", {})[pid] = elapsed
            _save(state)
            log(state, f"PASS done {pid} in {elapsed}s")

        # verify stars/colours unchanged (skip for sample jobs)
        if sample or dry_run or preview_first:
            log(state, "verify skipped (sample/dry-run/preview)")
            state["verify_skipped"] = "sample" if sample else ("dry_run" if dry_run else "preview")
        else:
            try:
                after_path = out / f"gui-flags-after-{state['id']}.json"
                log(state, "snapshot after, this folder only (180s timeout)...")
                proc = subprocess.Popen(["osascript", "-l", "JavaScript", str(SNAP_JS), str(after_path), "--folder", str(folder)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    proc.communicate(timeout=180)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise TimeoutError("snapshot after timed out after 180s")
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
        if preview_first and state["status"] == "done":
            try:
                sys.path.insert(0, str(HOME / "gui"))
                import preview_ops
                pv = preview_ops.build(str(folder), passes, profile, afpoints=state.get("afpoints", True),
                                       apply_rank_keywords=state.get("apply_rank_keywords", True), job_id=state["id"])
                state["preview_id"] = pv["id"]
                state["preview_summary"] = {k: pv[k] for k in ("frames_with_new", "keywords_new", "keywords_already", "by_pass")}
                state["status"] = "awaiting_approval"
                log(state, f"PREVIEW ready: {pv['keywords_new']} new keywords on {pv['frames_with_new']} frames; "
                           "nothing written until approved in the panel")
            except Exception as e:
                state["preview_error"] = str(e); log(state, "preview build failed:", e)
        state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["current_pass"] = None
        log(state, "FINISHED", state["status"])
        try:
            import sys as _sys
            _sys.path.insert(0, str(HOME / "gui"))
            from notify import notify_job
            nres = notify_job({k: state.get(k) for k in state if not str(k).startswith("_")})
            state["notify"] = nres; log(state, "notify:", nres)
        except Exception as _ne:
            state["notify_error"] = str(_ne); log(state, "notify error:", _ne)
    except Exception as e:
        state["status"] = "cancelled" if state.get("cancel_requested") else "error"
        state["error"] = str(e)
        state["traceback"] = traceback.format_exc()[-2000:]
        state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        log(state, "ERROR", e)
        try:
            import sys as _sys
            _sys.path.insert(0, str(HOME / "gui"))
            from notify import notify_job
            state["notify"] = notify_job({k: state.get(k) for k in state if not str(k).startswith("_")})
        except Exception:
            pass
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
