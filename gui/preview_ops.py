"""Keyword preview before apply (Capture One Shoot Sorter panel).

build(): per-frame keywords a run would ADD, from the proposals files of the chosen passes, using the
same confidence rules as the job runner (needs_review / errors never applied, Rank only General/Rank/*,
AF focus Missed never applied). Compared with the keywords earlier runs recorded for each frame
(offline, from out/<shoot>/*-result.json), and optionally checked against Capture One with a dry run
(read-only; refused while another job is writing to the catalog).
approve(): freezes the approved subset into out/<shoot>/preview-approved-<id>.json and queues an
apply-only job. Nothing is written to Capture One until that job runs.
"""
from __future__ import annotations
import json, subprocess, sys, threading, time
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app_home

HOME = app_home.home()
OUT = HOME / "out"
sys.path.insert(0, str(HOME))

_CHECKS: dict = {}   # preview id -> background C1 dry-run state
_LOCK = threading.Lock()


def _jr():
    import jobrunner as JR
    return JR


def sources_for(passes, afpoints=True, apply_rank_keywords=True):
    """pass ids whose proposals produce keywords, in run order (sharpness also carries afpoints)"""
    out = []
    for p in passes:
        if p in ("index", "cull"):
            continue
        if p == "rank" and not apply_rank_keywords:
            continue
        out.append(p)
        if p == "sharpness" and afpoints:
            out.append("afpoints")
    return out


def _confident(src, rows):
    rows = [r for r in rows if r.get("proposed_keywords") and not r.get("needs_review") and not r.get("error")
            and not str(r.get("filename", "")).lower().endswith((".mp4", ".mov"))]
    if src == "rank":
        rows = [dict(r, proposed_keywords=[k for k in r["proposed_keywords"] if k.startswith("General/Rank/")]) for r in rows]
        rows = [r for r in rows if r["proposed_keywords"]]
    if src == "afpoints":   # Missed goes to Review, never straight to the catalog (also for older proposals files)
        rows = [r for r in rows if "General/Focus/Missed" not in r["proposed_keywords"]]
    return rows


def known_keywords(shoot: str) -> dict:
    """filename -> keywords seen on the frame in earlier apply results (before lists + applied)."""
    fo = OUT / shoot
    known = defaultdict(set)
    if not fo.is_dir():
        return known
    for p in fo.glob("*-result.json"):
        try:
            r = json.load(open(p))
        except Exception:
            continue
        for b in r.get("before") or []:
            for k in b.get("keywords") or []:
                known[b.get("filename")].add(k)
        if not r.get("dry"):
            for a in r.get("applied") or []:
                if a.get("keyword") and not a.get("dry"):
                    known[a.get("filename")].add(a["keyword"])
    return known


def build(folder: str, passes, profile="motorsport", afpoints=True, apply_rank_keywords=True, job_id=None):
    JR = _jr()
    folder = Path(folder); shoot = folder.name
    srcs = sources_for(passes, afpoints, apply_rank_keywords)
    known = known_keywords(shoot)
    frames = {}
    by_kw, by_pass, missing = Counter(), Counter(), []
    review_counts = {}
    for src in srcs:
        pf = JR._proposal_file(folder, src, profile)
        if not pf:
            missing.append(src); continue
        data = json.load(open(pf))
        review_counts[src] = sum(1 for r in data.get("images", []) if r.get("needs_review")
                                 or (src == "afpoints" and "General/Focus/Missed" in (r.get("proposed_keywords") or [])))
        for r in _confident(src, data.get("images", [])):
            fr = frames.setdefault(r["filename"], {"filename": r["filename"], "sha256": r.get("sha256"), "add": [], "already": []})
            for k in r["proposed_keywords"]:
                if any(x["keyword"] == k for x in fr["add"] + fr["already"]):
                    continue
                item = {"keyword": k, "pass": src}
                if k in known.get(r["filename"], ()):
                    fr["already"].append(item)
                else:
                    fr["add"].append(item); by_kw[k] += 1; by_pass[src] += 1
    items = sorted(frames.values(), key=lambda x: x["filename"])
    pid = job_id or ("pv-" + time.strftime("%Y%m%d-%H%M%S"))
    res = {"id": pid, "folder": str(folder), "shoot": shoot, "profile": profile, "passes": list(passes), "sources": srcs,
           "afpoints": bool(afpoints), "apply_rank_keywords": bool(apply_rank_keywords),
           "missing_sources": missing, "needs_review_not_applied": review_counts,
           "frames_total": len(items), "frames_with_new": sum(1 for x in items if x["add"]),
           "keywords_new": sum(len(x["add"]) for x in items), "keywords_already": sum(len(x["already"]) for x in items),
           "by_keyword": dict(by_kw.most_common()), "by_pass": dict(by_pass), "items": items,
           "compared_with": "keywords recorded by earlier runs of this shoot (offline); use 'Check against Capture One' for an exact read",
           "built": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (OUT / shoot).mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / shoot / f"preview-{pid}.json", "w"))
    return res


def load(shoot: str, pid: str):
    f = (OUT / shoot / f"preview-{pid}.json").resolve()
    if f.parent != (OUT / shoot).resolve() or not f.exists():
        raise FileNotFoundError("preview not found")
    return json.load(open(f))


def select(pv: dict, exclude_keywords=(), exclude_passes=(), exclude_files=()):
    ek, ep, ef = set(exclude_keywords or ()), set(exclude_passes or ()), set(exclude_files or ())
    plan = defaultdict(list)   # pass -> images
    for fr in pv["items"]:
        if fr["filename"] in ef:
            continue
        per = defaultdict(list)
        for it in fr["add"]:
            if it["keyword"] in ek or it["pass"] in ep:
                continue
            per[it["pass"]].append(it["keyword"])
        for src, kws in per.items():
            plan[src].append({"filename": fr["filename"], "sha256": fr.get("sha256"), "needs_review": False,
                              "proposed_keywords": kws})
    return {src: plan[src] for src in pv["sources"] if plan.get(src)}


def approve(shoot: str, pid: str, exclude_keywords=(), exclude_passes=(), exclude_files=()):
    pv = load(shoot, pid)
    plan = select(pv, exclude_keywords, exclude_passes, exclude_files)
    n_kw = sum(len(i["proposed_keywords"]) for v in plan.values() for i in v)
    if not n_kw:
        raise ValueError("nothing selected to apply")
    f = OUT / shoot / f"preview-approved-{pid}.json"
    json.dump({"folder": pv["folder"], "preview_id": pid, "approved": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "exclude_keywords": list(exclude_keywords or ()), "exclude_passes": list(exclude_passes or ()),
               "exclude_files": list(exclude_files or ()), "plan": plan}, open(f, "w"), indent=1)
    return {"plan_path": str(f), "keywords": n_kw, "frames": len({i["filename"] for v in plan.values() for i in v}),
            "passes": list(plan), "folder": pv["folder"], "profile": pv["profile"]}


# ---------------------------------------------------------------- optional exact check (C1 dry run, read-only)
def start_c1_check(shoot: str, pid: str, busy_fn):
    busy = busy_fn()
    if busy:
        raise RuntimeError(f"Capture One is busy ({busy}); the offline comparison is shown instead")
    pv = load(shoot, pid)
    with _LOCK:
        if _CHECKS.get(pid, {}).get("status") == "running":
            return _CHECKS[pid]
        _CHECKS[pid] = {"status": "running", "started": time.strftime("%H:%M:%S"), "done_chunks": 0}
    threading.Thread(target=_c1_check, args=(pv,), daemon=True).start()
    return _CHECKS[pid]


def _c1_check(pv):
    pid = pv["id"]; shoot = pv["shoot"]
    try:
        from apply_c1 import apply_proposals
        imgs = [{"filename": fr["filename"], "sha256": fr.get("sha256"), "needs_review": False,
                 "proposed_keywords": [x["keyword"] for x in fr["add"] + fr["already"]]} for fr in pv["items"]]
        already = defaultdict(set); new = defaultdict(set)
        for i in range(0, len(imgs), 200):
            cf = OUT / shoot / f"preview-{pid}-check-{i // 200:03d}.json"
            json.dump({"folder": pv["folder"], "images": imgs[i:i + 200]}, open(cf, "w"))
            # dry run: apply_c1.js reads the catalog and reports what it WOULD add; it writes nothing
            r = apply_proposals(str(cf), apply=False, prefix=str(OUT / shoot / f"preview-{pid}-check-c{i // 200:03d}"))
            for a in r.get("applied") or []:
                if a.get("keyword"): new[a["filename"]].add(a["keyword"])
            for s in r.get("skipped") or []:
                if s.get("keyword"): already[s["filename"]].add(s["keyword"])
            with _LOCK:
                _CHECKS[pid]["done_chunks"] = i // 200 + 1
        for fr in pv["items"]:
            allk = fr["add"] + fr["already"]
            fr["add"] = [x for x in allk if x["keyword"] in new.get(fr["filename"], ())]
            fr["already"] = [x for x in allk if x["keyword"] not in new.get(fr["filename"], ())]
        by_kw, by_pass = Counter(), Counter()
        for fr in pv["items"]:
            for x in fr["add"]:
                by_kw[x["keyword"]] += 1; by_pass[x["pass"]] += 1
        pv.update(by_keyword=dict(by_kw.most_common()), by_pass=dict(by_pass),
                  keywords_new=sum(len(x["add"]) for x in pv["items"]),
                  keywords_already=sum(len(x["already"]) for x in pv["items"]),
                  frames_with_new=sum(1 for x in pv["items"] if x["add"]),
                  compared_with="Capture One (dry run at " + time.strftime("%H:%M") + ")")
        json.dump(pv, open(OUT / shoot / f"preview-{pid}.json", "w"))
        with _LOCK:
            _CHECKS[pid].update(status="done", finished=time.strftime("%H:%M:%S"))
    except BaseException as e:
        with _LOCK:
            _CHECKS[pid].update(status="error", error=str(e)[:300])


def check_status(pid: str):
    with _LOCK:
        return dict(_CHECKS.get(pid) or {})
