#!/usr/bin/env python3
"""Discover Camera Exports, match them to raws, and recalibrate the rank model.

Read-only on the NAS. Does not apply rank keywords or change stars/colours,
and never writes to Capture One.

usage:
  venv/bin/python tools/train_rank_exports.py
  venv/bin/python tools/train_rank_exports.py --write-config   # adopt only if held-out is better
  venv/bin/python tools/train_rank_exports.py --export-root "/path/Camera Exports" --index-missing
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rank_exports as R

STAMP = time.strftime("%Y%m%d-%H%M%S")
OUTDIR = HOME / "out" / "rank-exports"
REPORT = OUTDIR / f"train-{STAMP}.json"


def write_config(candidate, reason, result, adopted: bool, stamp: str | None = None, report_path: Path | None = None):
    """Update config.json rank section. Never touches stars, colours or keywords."""
    stamp = stamp or STAMP
    report_path = report_path or REPORT
    cfg_path = HOME / "config.json"
    bak = HOME / f"config.json.bak-rank-exports-{stamp}"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    prev = cfg.get("rank") or {}
    # Always keep a rollback snapshot of the weights / model that were in force.
    previous_weights = (prev.get("weights") if isinstance(prev.get("weights"), dict) else None) or dict(R.DEFAULT_WEIGHTS)
    previous_model = prev.get("model") or "weighted"
    previous_logistic = prev.get("logistic")
    if cfg_path.exists():
        shutil.copy2(cfg_path, bak)
    rank = dict(prev)
    rank["previous_weights"] = previous_weights
    rank["previous_model"] = previous_model
    if previous_logistic is not None:
        rank["previous_logistic"] = previous_logistic
    rank["best_pct"] = rank.get("best_pct", 0.10)
    rank["good_pct"] = rank.get("good_pct", 0.20)
    rank["min_rated_for_calibration"] = rank.get("min_rated_for_calibration", 10)
    cand_model = (result or {}).get("candidate_model") or {"type": "weighted", "weights": candidate}
    if adopted:
        rank["weights"] = candidate or previous_weights
        if cand_model.get("type") == "logistic":
            rank["model"] = "logistic"
            rank["logistic"] = {k: cand_model[k] for k in ("coef", "intercept", "features") if k in cand_model}
            rank["source"] = "exports+stars-loso-logistic"
        else:
            rank["model"] = "weighted"
            rank.pop("logistic", None)
            rank["source"] = "exports+stars-loso"
        rank["trained"] = stamp
        rank["adopted"] = True
        rank["adopt_reason"] = reason
        rank["report"] = str(report_path)
    else:
        # leave current weights / model in place (defaults if unset)
        rank.setdefault("weights", dict(R.DEFAULT_WEIGHTS))
        rank.setdefault("model", previous_model)
        rank["adopted"] = False
        rank["adopt_reason"] = reason
        rank["last_export_training"] = stamp
        rank["candidate_weights"] = candidate
        rank["candidate_model"] = cand_model
        rank["report"] = str(report_path)
    cfg["rank"] = rank
    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(cfg_path)
    return str(cfg_path), str(bak)


def latest_report_path() -> Path:
    return OUTDIR / "train-latest.json"


def load_latest_report() -> dict | None:
    p = latest_report_path()
    if not p.is_file():
        # fall back to newest train-*.json
        cands = sorted(OUTDIR.glob("train-*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        cands = [c for c in cands if c.name != "train-latest.json"]
        p = cands[0] if cands else None
    if not p or not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    data["_path"] = str(p)
    return data


def load_rank_config() -> dict:
    cfg_path = HOME / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return (json.loads(cfg_path.read_text(encoding="utf-8")) or {}).get("rank") or {}
    except Exception:
        return {}


def adopt_from_report(report: dict | None = None) -> dict:
    """Promote the last report's candidate into production weights. Snapshot current as previous_*."""
    report = report if report is not None else load_latest_report()
    if not report:
        raise ValueError("no training report found")
    train = report.get("train") or {}
    candidate = train.get("candidate_weights")
    if not candidate and not train.get("candidate_model"):
        raise ValueError("report has no candidate weights")
    reason = train.get("adopt_reason") or "adopted from Training tab"
    stamp = report.get("stamp") or time.strftime("%Y%m%d-%H%M%S")
    report_path = Path(report.get("_path") or latest_report_path())
    written = write_config(candidate, reason, train, True, stamp=stamp, report_path=report_path)
    return {"ok": True, "config": written[0], "backup": written[1], "reason": reason}


def rollback_rank() -> dict:
    """Restore rank.previous_weights / previous_model. The abandoned weights become the new previous_*."""
    cfg_path = HOME / "config.json"
    if not cfg_path.is_file():
        raise ValueError("config.json not found")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    rank = dict(cfg.get("rank") or {})
    prev_w = rank.get("previous_weights")
    if not isinstance(prev_w, dict):
        raise ValueError("no previous_weights to roll back to")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = HOME / f"config.json.bak-rank-rollback-{stamp}"
    shutil.copy2(cfg_path, bak)
    current_w = dict(rank.get("weights") or R.DEFAULT_WEIGHTS)
    current_model = rank.get("model") or "weighted"
    current_logistic = rank.get("logistic")
    rank["weights"] = dict(prev_w)
    rank["previous_weights"] = current_w
    rank["model"] = rank.get("previous_model") or "weighted"
    rank["previous_model"] = current_model
    prev_log = rank.get("previous_logistic")
    if current_logistic is not None:
        rank["previous_logistic"] = current_logistic
    if prev_log is not None and rank["model"] == "logistic":
        rank["logistic"] = prev_log
    elif rank["model"] != "logistic":
        rank.pop("logistic", None)
    rank["adopted"] = False
    rank["adopt_reason"] = f"rolled back to previous_weights ({stamp})"
    rank["rolled_back"] = stamp
    cfg["rank"] = rank
    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(cfg_path)
    return {"ok": True, "config": str(cfg_path), "backup": str(bak), "reason": rank["adopt_reason"]}


def _index_folder(path: Path) -> int:
    """Index one import folder into the local catalog. Read-only on the NAS."""
    py = HOME / "venv" / "bin" / "python"
    features = HOME / "features.py"
    if not features.is_file():
        features = Path(__file__).resolve().parents[1] / "features.py"
    if py.is_file():
        cmd = [str(py), str(features), "index", str(path)]
    else:
        cmd = [sys.executable, str(features), "index", str(path)]
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(HOME))
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-config", action="store_true",
                    help="Write config.json if (and only if) the new weights beat the baseline on held-out shoots")
    ap.add_argument("--min-indexed", type=int, default=50)
    ap.add_argument("--export-root", action="append", default=[],
                    help="Export root to scan (repeatable). Default: Camera Exports.")
    ap.add_argument("--index-missing", action=argparse.BooleanOptionalAction, default=False,
                    help="Index import folders that have export matches but are missing or too small in the local index")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    roots = [Path(p) for p in (args.export_root or [])]
    if not roots:
        roots = [R.exports_root()]
    print("listing dated export folders…", flush=True)
    year_imports = R.list_year_imports()
    folders = R.discover_export_folders_many(roots)
    print(f"  {len(folders)} export folders from {len(roots)} root(s); import years: {sorted(year_imports)}", flush=True)

    print("loading local index…", flush=True)
    index_by_folder = R.index_rows_by_folder()
    print(f"  indexed shoots: { {k: len(v) for k,v in index_by_folder.items()} }", flush=True)

    print("matching exports to raws (stem, then EXIF time for leftovers)…", flush=True)
    matches, _ = R.match_exports(folders, year_imports, index_by_folder)
    stats = R.folder_match_stats(matches)
    for s in stats:
        rate = "n/a" if s["match_rate"] is None else f"{100*s['match_rate']:.0f}%"
        dests = s.get("mapped_imports") or {}
        dest = ", ".join(f"{k}:{v}" for k,v in dests.items()) if dests else (s.get("mapped_import") or "(no import folder)")
        kind = s.get("kind") or "dated"
        print(f"  [{kind}] {s['export_folder']}: {s['matched']}/{s['unique_stems']} stems -> {dest} ({rate})", flush=True)

    positives = R.positives_by_shoot(matches)

    indexed_now = []
    if args.index_missing:
        need = R.folders_needing_index(positives, index_by_folder, min_indexed=args.min_indexed,
                                       year_imports=year_imports)
        if not need:
            print("index missing: nothing to do", flush=True)
        for item in need:
            if not item.get("path"):
                print(f"index missing: skip {item['folder']} (import path not found)", flush=True)
                continue
            print(f"index missing: {item['folder']} has {item['indexed']} indexed, "
                  f"{item['exported_matched']} exported matches — indexing {item['path']}", flush=True)
            rc = _index_folder(Path(item["path"]))
            indexed_now.append({**item, "index_rc": rc})
            if rc:
                print(f"index missing: {item['folder']} exited {rc}", flush=True)
        if need:
            print("reloading local index…", flush=True)
            index_by_folder = R.index_rows_by_folder()
            print(f"  indexed shoots: { {k: len(v) for k,v in index_by_folder.items()} }", flush=True)

    labels, labsum = R.build_labels(index_by_folder, positives, min_indexed=args.min_indexed)
    print("labels:", flush=True)
    for s in labsum:
        print(" ", s, flush=True)

    baseline = dict(R.DEFAULT_WEIGHTS)
    # if config already has weights, the 'before' model is those (what's in production)
    cfg = json.loads((HOME / "config.json").read_text()) if (HOME / "config.json").exists() else {}
    if isinstance(cfg.get("rank"), dict) and cfg["rank"].get("weights"):
        baseline = {**R.DEFAULT_WEIGHTS, **cfg["rank"]["weights"]}

    train = {}
    if labels:
        print("evaluating leave-one-shoot-out…", flush=True)
        train = R.leave_one_shoot_out(labels, baseline)
        adopted, reason = R.should_adopt(train)
        train["adopt"] = adopted
        train["adopt_reason"] = reason
        print("BEFORE overall (expanded set, current weights):", train["before"]["overall"], flush=True)
        print("AFTER  LOSO macro (chosen):", train["after_loso_macro"], flush=True)
        print("  grid   :", train.get("after_loso_macro_grid"), flush=True)
        print("  logistic:", train.get("after_loso_macro_logistic"), flush=True)
        print("weighted-sum LOSO (per shoot):", flush=True)
        for row in train["loso"]:
            print(f"  hold out {row['held_out']}: combined {row['before']['auc_combined']} -> {row['after']['auc_combined']}; "
                  f"export {row['before']['auc_export']} -> {row['after']['auc_export']}; "
                  f"star {row['before']['auc_star']} -> {row['after']['auc_star']}", flush=True)
        if train.get("loso_logistic"):
            print("logistic LOSO (per shoot):", flush=True)
            for row in train["loso_logistic"]:
                print(f"  hold out {row['held_out']}: combined {row['before']['auc_combined']} -> {row['after']['auc_combined']}; "
                      f"export {row['before']['auc_export']} -> {row['after']['auc_export']}; "
                      f"star {row['before']['auc_star']} -> {row['after']['auc_star']}", flush=True)
        print("candidate weights:", train["candidate_weights"], flush=True)
        print("candidate model:", train.get("candidate_model"), flush=True)
        print("adopt?", adopted, reason, flush=True)
    else:
        adopted, reason = False, "no labelled indexed shoots with exports"
        print(reason, flush=True)

    written = None
    if args.write_config and labels:
        # only write new weights as default when adopted; always record the attempt
        written = write_config(train.get("candidate_weights") or baseline, reason, train, adopted)
        print("config:", written, flush=True)
    elif args.write_config and not labels:
        print("not writing config: no labels", flush=True)

    report = {
        "stamp": STAMP,
        "export_roots": [str(r) for r in roots],
        "index_missing": bool(args.index_missing),
        "indexed_now": indexed_now,
        "export_folders": [s for s in stats],
        "label_summaries": labsum,
        "positives_by_shoot": {k: sorted(v) for k, v in positives.items()},
        "baseline_weights": baseline,
        "train": {
            "before": train.get("before"),
            "loso": train.get("loso"),
            "loso_logistic": train.get("loso_logistic"),
            "after_loso_macro": train.get("after_loso_macro"),
            "after_loso_macro_grid": train.get("after_loso_macro_grid"),
            "after_loso_macro_logistic": train.get("after_loso_macro_logistic"),
            "candidate_weights": train.get("candidate_weights"),
            "candidate_model": train.get("candidate_model"),
            "candidate_in_sample": train.get("candidate_in_sample"),
            "candidate_in_sample_logistic": train.get("candidate_in_sample_logistic"),
            "adopt": train.get("adopt"),
            "adopt_reason": train.get("adopt_reason") or reason,
        },
        "config_written": written,
        "nas_readonly": True,
        "wrote_capture_one": False,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    (OUTDIR / "train-latest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("report:", REPORT, flush=True)


if __name__ == "__main__":
    main()
