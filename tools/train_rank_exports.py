#!/usr/bin/env python3
"""Discover Camera Exports, match them to raws, and recalibrate the rank model.

Read-only on the NAS. Does not apply rank keywords or change stars/colours.

usage:
  venv/bin/python tools/train_rank_exports.py
  venv/bin/python tools/train_rank_exports.py --write-config   # only if held-out is better
"""
from __future__ import annotations
import argparse, json, shutil, sys, time
from pathlib import Path

HOME = Path.home() / "localai"
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rank_exports as R

STAMP = time.strftime("%Y%m%d-%H%M%S")
OUTDIR = HOME / "out" / "rank-exports"
REPORT = OUTDIR / f"train-{STAMP}.json"


def write_config(candidate, reason, result, adopted: bool):
    cfg_path = HOME / "config.json"
    bak = HOME / f"config.json.bak-rank-exports-{STAMP}"
    cfg = json.loads(cfg_path.read_text())
    prev = cfg.get("rank") or {}
    # Always keep a rollback snapshot of the weights / model that were in force.
    previous_weights = (prev.get("weights") if isinstance(prev.get("weights"), dict) else None) or dict(R.DEFAULT_WEIGHTS)
    previous_model = prev.get("model") or "weighted"
    previous_logistic = prev.get("logistic")
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
            rank["source"] = "exports+stars-loso"
        rank["trained"] = STAMP
        rank["adopted"] = True
        rank["adopt_reason"] = reason
        rank["report"] = str(REPORT)
    else:
        # leave current weights / model in place (defaults if unset)
        rank.setdefault("weights", dict(R.DEFAULT_WEIGHTS))
        rank.setdefault("model", previous_model)
        rank["adopted"] = False
        rank["adopt_reason"] = reason
        rank["last_export_training"] = STAMP
        rank["candidate_weights"] = candidate
        rank["candidate_model"] = cand_model
        rank["report"] = str(REPORT)
    cfg["rank"] = rank
    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(cfg_path)
    return str(cfg_path), str(bak)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-config", action="store_true",
                    help="Write config.json if (and only if) the new weights beat the baseline on held-out shoots")
    ap.add_argument("--min-indexed", type=int, default=50)
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    print("listing dated export folders…", flush=True)
    year_imports = R.list_year_imports()
    folders = R.discover_export_folders()
    print(f"  {len(folders)} dated export folders; import years: {sorted(year_imports)}", flush=True)

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
    }
    # don't store huge stem lists of every positive in the printed report twice — keep them
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    # compact copy under a stable name
    (OUTDIR / "train-latest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("report:", REPORT, flush=True)


if __name__ == "__main__":
    main()
