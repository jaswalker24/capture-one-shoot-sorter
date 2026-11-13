#!/usr/bin/env python3
"""Schedule tagging: EXIF capture time -> event/venue/act/cheki/meet/changeover keyword proposals.
Proposals are apply_c1-compatible; flagged frames (needs_review) are never applied."""
import json, subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_home
HOME = app_home.home()

def _off(s):
    s = s.strip(); sign = -1 if s.startswith("-") else 1; h, m = s.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))

def capture_times(paths, camera_utc_offset=None):
    """-> {path: aware datetime}. Offset: EXIF OffsetTimeOriginal, else camera_utc_offset (config), else +00:00."""
    out = {}
    for i in range(0, len(paths), 200):
        r = subprocess.run([str(HOME / "bin/exiftool"), "-j", "-q", "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal"]
                           + [str(p) for p in paths[i:i + 200]], capture_output=True, text=True)
        for d in json.loads(r.stdout or "[]"):
            t = d.get("DateTimeOriginal")
            if not t: continue
            dt = datetime.strptime(str(t), "%Y:%m:%d %H:%M:%S")
            if d.get("SubSecTimeOriginal") is not None:
                dt += timedelta(seconds=float("0." + str(d["SubSecTimeOriginal"])))
            off = d.get("OffsetTimeOriginal") or camera_utc_offset or "+00:00"
            if camera_utc_offset and camera_utc_offset.startswith("force:"): off = camera_utc_offset[6:]
            out[str(Path(d["SourceFile"]))] = dt.replace(tzinfo=_off(off))
    return out

def load_schedule(path):
    s = json.load(open(path)); tz = ZoneInfo(s.get("timezone", "UTC"))
    def at(date, hm, after=None):
        dt = datetime.fromisoformat(f"{date}T{hm}").replace(tzinfo=tz)
        if after is not None and dt <= after:
            dt = dt + timedelta(days=1)
        return dt
    for e in s["events"]:
        doors_hm = e.get("doors") or e["start"]
        start_hm = e.get("start") or doors_hm
        e["_doors"] = at(e["date"], doors_hm)
        start_dt = at(e["date"], start_hm)
        if start_hm and doors_hm and start_hm < doors_hm:
            start_dt = at(e["date"], start_hm, after=e["_doors"])
        end_hm = e.get("attend_until") or e["window_end"]
        e["_end"] = at(e["date"], end_hm, after=start_dt)
        e["_buf"] = timedelta(minutes=e.get("changeover_buffer_min", s.get("changeover_buffer_min", 3)))
        for sl in e["slots"]:
            sl["_s"] = at(e["date"], sl["start"])
            if doors_hm and sl.get("start") and sl["start"] < doors_hm:
                sl["_s"] = at(e["date"], sl["start"], after=e["_doors"])
            sl["_e"] = at(e["date"], sl["end"], after=sl["_s"])
            sl["_acts"] = [sl["act"]] if "act" in sl else sl["acts"]
    return s, tz

def match(t, sched, tz):
    """t: aware datetime -> dict(keywords, tentative, review, local_time, event)"""
    s, _ = sched
    lt = t.astimezone(tz)
    res = {"local_time": lt.isoformat(), "keywords": [], "tentative": [], "review": [], "event": None}
    cands = []
    for e in s["events"]:
        if e.get("status") != "confirmed": continue
        buf = e["_buf"]
        in_slot = [sl for sl in e["slots"] if sl["_s"] - buf <= lt < sl["_e"] + buf and lt <= e["_end"] + buf]
        if e.get("only_inside_slots"):
            if in_slot: cands.append((e, True))
        elif e["_doors"] - buf <= lt <= e["_end"] + buf:
            cands.append((e, bool(in_slot)))
    if not cands: return res
    if len(cands) > 1:
        strong = [c for c in cands if c[1]]
        if len(strong) == 1: cands = strong
        else:
            res["review"].append("overlaps events: " + ", ".join(c[0]["event"] for c in cands))
            for e, _ in cands: res["tentative"] += [f"Gig/Event/{e['event']}", f"Gig/Venue/{e['venue']}"]
            return res
    e = cands[0][0]; buf = e["_buf"]; res["event"] = e["event"]
    res["keywords"] += [f"Gig/Event/{e['event']}", f"Gig/Venue/{e['venue']}"]
    sets = [sl for sl in e["slots"] if sl["type"] in ("set", "show")]
    windows = [sl for sl in e["slots"] if sl["type"] in ("cheki", "meet")]
    in_win = [w for w in windows if w["_s"] <= lt < w["_e"]]
    for w in in_win:
        res["keywords"].append("Gig/Cheki" if w["type"] == "cheki" else "Gig/Meet & Greet")
        res["keywords"] += [f"Gig/Act/{a}" for a in w["_acts"]]
    # changeover: within buffer of a boundary shared by two sets
    starts = [sl["_s"] for sl in sets]; ends = [sl["_e"] for sl in sets]
    shared = [b for b in ends if any(abs((b - s0).total_seconds()) <= 600 for s0 in starts)]
    near = [b for b in shared if abs((lt - b).total_seconds()) <= buf.total_seconds()]
    on_stage = [sl for sl in sets if sl["_s"] <= lt < sl["_e"]]
    if near:
        res["keywords"].append("Gig/Changeover")
        for sl in sets:
            if sl["_s"] - buf <= lt < sl["_e"] + buf:
                res["tentative"] += [f"Gig/Act/{a}" for a in sl["_acts"] if f"Gig/Act/{a}" not in res["keywords"]]
    else:
        for sl in on_stage:
            tgt = res["tentative"] if in_win else res["keywords"]
            tgt += [f"Gig/Act/{a}" for a in sl["_acts"] if f"Gig/Act/{a}" not in res["keywords"]]
        if in_win and on_stage: res["review"].append("on-stage set overlaps cheki/meet window")
    for sl in on_stage + ([sl for sl in sets if sl["_s"] - buf <= lt < sl["_e"] + buf] if near else []):
        for a in sl["_acts"]:
            if a in e.get("no_photo_acts", []):
                res["review"].append(f"inside no-photo act slot: {a}")
    res["review"] = sorted(set(res["review"]))
    res["keywords"] = list(dict.fromkeys(res["keywords"])); res["tentative"] = list(dict.fromkeys(res["tentative"]))
    return res

def schedule_proposals(files, schedule_path, folder, camera_utc_offset=None, sha_fn=None):
    sched = load_schedule(schedule_path); tz = sched[1]
    times = capture_times(files, camera_utc_offset)
    recs = []
    for p in files:
        t = times.get(str(p))
        if t is None:
            recs.append(dict(filename=p.name, needs_review=True, review_reasons=["no EXIF capture time"], proposed_keywords=[])); continue
        m = match(t, sched, tz)
        recs.append(dict(filename=p.name, sha256=sha_fn(p) if sha_fn else None, capture_time_camera=t.isoformat(),
                         local_time=m["local_time"], event=m["event"], proposed_keywords=m["keywords"],
                         tentative_keywords=m["tentative"], needs_review=bool(m["review"]), review_reasons=m["review"]))
    return {"folder": str(folder), "profile": "schedule", "schedule": str(schedule_path), "images": recs}
