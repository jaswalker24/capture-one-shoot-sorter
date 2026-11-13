#!/usr/bin/env python3
"""Backend for the Capture One Shoot Sorter Scripts menu. Runs at low priority, logs to <runtime>/logs/scripts/,
posts a macOS notification when done. Capture One writes only via apply_c1 (additive keywords; Description only if empty)."""
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_home
HOME = app_home.home()
sys.path.insert(0, str(HOME))
LOG = HOME / "logs" / "scripts"; LOG.mkdir(parents=True, exist_ok=True)
CFG = json.load(open(HOME / "config.json"))
TAG = str(HOME / "tag")

def notify(title, msg):
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg[:200])} with title {json.dumps(title)}'])

def run(cmd, env=None):
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, env={**os.environ, **(env or {})}, text=True, capture_output=True)
    out = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not re.search(r"warn|detach\(\)|logit_scale", l, re.I))
    print(out, flush=True)
    if r.returncode: raise RuntimeError(f"{cmd[1] if len(cmd)>1 else cmd[0]} failed ({r.returncode})")
    return out

def profile_for(folder: str):
    for pat, prof in CFG.get("profile_by_folder", {"BrandsHatch|Britcar|Silverstone|Donington|Snetterton|Oulton|Thruxton|Goodwood|Fuji Speedway|Suzuka|Motegi": "motorsport"}).items():
        if re.search(pat, folder, re.I): return prof
    return CFG.get("script_profile_default", "gig")

def apply(proposals):
    from apply_c1 import apply_proposals
    res = apply_proposals(str(proposals), apply=True)
    return res

def main():
    action, listfile = sys.argv[1], sys.argv[2]
    files = [l.strip() for l in open(listfile, encoding="utf-8") if l.strip()]
    files = [f for f in files if Path(f).suffix.lower() not in (".mp4", ".mov", ".m4v")]
    title = {"tag": "Tag with Local AI", "bursts": "Find Bursts in Selection", "rank": "Rank Selection",
             "sharpness": "Check Sharpness", "ocr": "Read Text in Frame", "schedule": "Tag by Schedule",
             "captions": "Draft Captions (Exports)"}[action]
    print(time.strftime("%Y-%m-%d %H:%M:%S"), title, len(files), "files", flush=True)
    if not files: notify(title, "Nothing selected"); return
    folder = str(Path(files[0]).parent); fo = Path(folder).name; out = HOME / "out" / fo
    try:
        if action == "tag":
            prof = profile_for(folder)
            ctx = CFG.get("script_context", {}).get(fo, "")
            stem_tag = "selection-" + time.strftime("%Y%m%d-%H%M%S")
            run([TAG, "--profile", prof, "--tag", stem_tag, "--files", *files] + (["--context", ctx] if ctx else []),
                env={"LOCALAI_NO_APPLY_STEP": "1"})
            res = apply(out / f"{prof}-proposals-{stem_tag}.json"); summary = f"{len(res['applied'])} keywords added ({prof})"
        elif action in ("bursts", "rank", "sharpness"):
            run([TAG, "index", "--files", *files]); run([TAG, action, "--files", *files])
            res = apply(out / f"{action}-proposals.json"); summary = f"{len(res['applied'])} keywords added"
        elif action == "ocr":
            run([TAG, "ocr", "--translate", "--files", *files])
            res = apply(out / "ocr-proposals.json")
            summary = f"{len(res['applied'])} descriptions written; {len([s for s in res['skipped'] if s.get('field')])} kept (not empty, sidecar only)"
        elif action == "schedule":
            run([TAG, "schedule", "--files", *files])
            res = apply(out / "schedule-proposals.json"); summary = f"{len(res['applied'])} keywords added"
        elif action == "captions":
            o = run([TAG, "captions", "--files", *files]); summary = "drafts saved: " + (re.findall(r"sidecar: (.*)", o) or ["?"])[-1]
        if action != "captions" and res["failures"]:
            summary += f"; {len(res['failures'])} problems (see log)"
        print("DONE", summary, flush=True); notify(title, summary)
    except Exception as e:
        print("ERROR", e, flush=True); notify(title, f"Failed: {e}")

if __name__ == "__main__":
    main()
