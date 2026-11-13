#!/usr/bin/env python3
"""Wait until the Trips Local AI for Lightroom job queue is empty, then reload the GUI LaunchAgent.

Used so code changes (e.g. People path UI) go live without interrupting a running tagging job.
Safe to leave detached: polls jobs/*.json; exits after one successful kickstart.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
JOBS = HOME / "gui" / "jobs"
LOG = HOME / "logs" / "gui" / "reload-when-idle.log"
POLL = float(os.environ.get("LOCALAI_RELOAD_POLL", "20"))
LABELS = ("com.trips.localai-lr.gui",)


def log(msg: str):
    line = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + msg
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def busy_jobs():
    out = []
    if not JOBS.is_dir():
        return out
    for p in JOBS.glob("*.json"):
        try:
            j = json.load(open(p))
        except Exception:
            continue
        if j.get("status") in ("running", "queued"):
            out.append({"id": j.get("id") or p.stem, "status": j.get("status"),
                        "folder": j.get("folder_name") or j.get("folder"),
                        "pass": j.get("current_pass")})
    return out


def main():
    uid = os.getuid()
    def _loaded():
        for label in LABELS:
            r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return label
        return LABELS[0]

    label = _loaded()
    log(f"watcher start; poll={POLL}s; waiting for empty queue before kickstart {label}")
    while True:
        busy = busy_jobs()
        if not busy:
            break
        log("busy: " + ", ".join(f"{b['id']}={b['status']}/{b.get('pass') or '-'} ({b.get('folder')})" for b in busy))
        time.sleep(POLL)
    log("queue empty — kickstarting LaunchAgent")
    cmd = ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        # Try the other known label if the first one is not loaded.
        other = next((x for x in LABELS if x != label), None)
        if other:
            cmd2 = ["launchctl", "kickstart", "-k", f"gui/{uid}/{other}"]
            r2 = subprocess.run(cmd2, capture_output=True, text=True)
            if r2.returncode == 0:
                log(f"kickstart ok: {' '.join(cmd2)}")
                subprocess.run(["pkill", "-f", "LOCALAI_GUI_PORT=8766"], capture_output=True)
                log("done")
                return
        log(f"kickstart FAILED rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        sys.exit(1)
    log(f"kickstart ok: {' '.join(cmd)}")
    # stop any leftover NO_WORKER test server on 8766
    subprocess.run(["pkill", "-f", "LOCALAI_GUI_PORT=8766"], capture_output=True)
    log("done")


if __name__ == "__main__":
    main()
