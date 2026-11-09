#!/usr/bin/env python3
"""Schedule importer helpers for Trips Local AI control panel.
Reuses ~/localai/schedule_tag.py for mapping; confirmed events only by default.
"""
from __future__ import annotations
import csv, io, json, re, time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home() / "localai"
SCHED_DIR = HOME / "schedules"
IMPORTS = Path("/Volumes/Photos/Camera Imports")

CAMERA_TZ = {
    "Europe/London": "+00:00",   # GMT in Nov; BST would be +01:00 in summer — user can force
    "Asia/Tokyo": "+09:00",
    "UTC": "+00:00",
}

def list_schedule_files():
    SCHED_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(SCHED_DIR.glob("*.json")):
        try:
            d = json.load(open(p))
            ev = d.get("events") or []
            out.append({
                "name": p.name, "path": str(p),
                "title": d.get("name") or p.stem,
                "timezone": d.get("timezone"),
                "events": len(ev),
                "confirmed": sum(1 for e in ev if e.get("status") == "confirmed"),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            })
        except Exception as e:
            out.append({"name": p.name, "path": str(p), "error": str(e)})
    return out


def _acts_of(sl):
    if "act" in sl: return [sl["act"]]
    return list(sl.get("acts") or [])


def validate_schedule(data: dict, confirmed_only: bool = True) -> dict:
    """Return preview rows + warnings (overlaps, TBA, missing times)."""
    warnings = []
    rows = []
    events = data.get("events") or []
    tz_name = data.get("timezone") or "UTC"
    try:
        ZoneInfo(tz_name)
    except Exception:
        warnings.append(f"Unknown timezone {tz_name!r}")

    for e in events:
        status = e.get("status") or "unknown"
        if confirmed_only and status != "confirmed":
            rows.append({
                "date": e.get("date"), "event": e.get("event"), "venue": e.get("venue"),
                "status": status, "slot_type": "—", "act": "—", "start": e.get("start"),
                "end": e.get("window_end"), "note": "skipped (not confirmed)",
            })
            continue
        tba = e.get("acts_tba") or []
        if tba:
            warnings.append(f"{e.get('event')}: {len(tba)} TBA act(s) listed — shown but not time-tagged")
            for a in tba:
                rows.append({
                    "date": e.get("date"), "event": e.get("event"), "venue": e.get("venue"),
                    "status": status, "slot_type": "tba", "act": a, "start": "", "end": "",
                    "note": "TBA — no tag until times published",
                })
        slots = e.get("slots") or []
        if not slots and not tba:
            warnings.append(f"{e.get('event')}: no timed slots")
            rows.append({
                "date": e.get("date"), "event": e.get("event"), "venue": e.get("venue"),
                "status": status, "slot_type": "—", "act": "—",
                "start": e.get("start"), "end": e.get("window_end"),
                "note": "event/venue only until slots added",
            })
        # overlap check within event (set/show only)
        timed = []
        for sl in slots:
            acts = ", ".join(_acts_of(sl)) or "—"
            rows.append({
                "date": e.get("date"), "event": e.get("event"), "venue": e.get("venue"),
                "status": status, "slot_type": sl.get("type") or "set",
                "act": acts, "start": sl.get("start"), "end": sl.get("end"),
                "note": "",
            })
            if sl.get("type") in ("set", "show") and sl.get("start") and sl.get("end"):
                timed.append(sl)
        for i, a in enumerate(timed):
            for b in timed[i + 1:]:
                # string HH:MM compare works for same-day zero-padded times
                if a["start"] < b["end"] and b["start"] < a["end"]:
                    # touching boundaries (end==start) is changeover, not overlap
                    if a["end"] == b["start"] or b["end"] == a["start"]:
                        continue
                    warnings.append(
                        f"{e.get('event')}: overlapping sets "
                        f"{'/'.join(_acts_of(a))} {a['start']}-{a['end']} vs "
                        f"{'/'.join(_acts_of(b))} {b['start']}-{b['end']}"
                    )
        # cross-event same-day venue overlaps (doors–window)
        pass

    # cross-event window overlaps (confirmed only)
    conf = [e for e in events if (not confirmed_only) or e.get("status") == "confirmed"]
    for i, a in enumerate(conf):
        for b in conf[i + 1:]:
            if a.get("date") != b.get("date"):
                continue
            as_, ae = a.get("doors") or a.get("start"), a.get("window_end")
            bs, be = b.get("doors") or b.get("start"), b.get("window_end")
            if as_ and ae and bs and be and as_ < be and bs < ae:
                warnings.append(
                    f"Same-day window overlap: {a.get('event')} ({as_}-{ae}) vs {b.get('event')} ({bs}-{be})"
                )

    return {
        "name": data.get("name"),
        "timezone": tz_name,
        "changeover_buffer_min": data.get("changeover_buffer_min", 3),
        "events_total": len(events),
        "events_confirmed": sum(1 for e in events if e.get("status") == "confirmed"),
        "rows": rows,
        "warnings": sorted(set(warnings)),
        "ok": True,
    }


def csv_to_schedule(text: str, name: str = "Imported schedule", timezone: str = "Asia/Tokyo",
                    buffer_min: int = 3) -> dict:
    """CSV columns: event,venue,date,act,start,end [,type] [,status] [,doors] [,window_end]"""
    f = io.StringIO(text.strip())
    # sniff delimiter
    sample = text[:1024]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(f, dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    fields = {h.strip().lower(): h for h in reader.fieldnames if h}
    need = ["event", "venue", "date", "act", "start", "end"]
    missing = [c for c in need if c not in fields]
    if missing:
        raise ValueError(f"CSV missing columns: {', '.join(missing)} (have: {list(fields)})")

    groups = {}
    for row in reader:
        def g(k, default=""):
            src = fields.get(k)
            return (row.get(src) or default).strip() if src else default
        key = (g("date"), g("event"), g("venue"))
        if not all(key):
            continue
        ev = groups.setdefault(key, {
            "id": re.sub(r"[^a-z0-9]+", "-", key[1].lower()).strip("-")[:40] or "event",
            "status": g("status") or "confirmed",
            "date": key[0], "event": key[1], "venue": key[2],
            "doors": g("doors") or None, "start": None, "window_end": g("window_end") or None,
            "slots": [], "acts_tba": [], "no_photo_acts": [],
        })
        st, en, act = g("start"), g("end"), g("act")
        typ = g("type") or "set"
        if not st or not en:
            if act:
                ev["acts_tba"].append(act)
            continue
        ev["slots"].append({"act": act, "type": typ, "start": st, "end": en})
        if ev["start"] is None or st < ev["start"]:
            ev["start"] = st
        if not ev.get("doors"):
            ev["doors"] = st
        end_candidate = en
        if not ev.get("window_end") or end_candidate > ev["window_end"]:
            ev["window_end"] = end_candidate

    events = list(groups.values())
    if not events:
        raise ValueError("CSV produced no events")
    return {
        "name": name,
        "source": "csv-import",
        "timezone": timezone,
        "changeover_buffer_min": buffer_min,
        "notes": "Imported via Trips Local AI Schedule importer",
        "events": events,
    }


def save_schedule(data: dict, filename: str | None = None) -> Path:
    SCHED_DIR.mkdir(parents=True, exist_ok=True)
    if not filename:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = re.sub(r"[^a-z0-9]+", "-", (data.get("name") or "schedule").lower()).strip("-")[:40]
        filename = f"{base or 'schedule'}-{stamp}.json"
    if not filename.endswith(".json"):
        filename += ".json"
    # safety: no path separators
    filename = Path(filename).name
    path = SCHED_DIR / filename
    # strip private underscore keys if any
    clean = json.loads(json.dumps(data, default=str))
    for e in clean.get("events") or []:
        for k in list(e.keys()):
            if k.startswith("_"):
                del e[k]
        for sl in e.get("slots") or []:
            for k in list(sl.keys()):
                if k.startswith("_"):
                    del sl[k]
    json.dump(clean, open(path, "w"), ensure_ascii=False, indent=2)
    return path


def with_buffer(data: dict, buffer_min: int) -> dict:
    d = deepcopy(data)
    d["changeover_buffer_min"] = int(buffer_min)
    return d


def camera_offset_for(tz_name: str, force: bool = True) -> str:
    off = CAMERA_TZ.get(tz_name)
    if not off:
        # try compute from zoneinfo for "now"
        try:
            z = datetime.now(ZoneInfo(tz_name)).strftime("%z")  # +0000
            off = z[:3] + ":" + z[3:]
        except Exception as e:
            raise ValueError(f"unsupported camera timezone {tz_name!r}: {e}")
    return ("force:" if force else "") + off


def dry_run(folder: Path, schedule_path: Path, camera_tz: str = "Europe/London",
            buffer_min: int = 3, confirmed_only: bool = True, sample: int = 0) -> dict:
    import sys
    sys.path.insert(0, str(HOME))
    import schedule_tag as ST
    import tagger as T

    data = json.load(open(schedule_path))
    if buffer_min != data.get("changeover_buffer_min", 3) or (confirmed_only and any(e.get("status") != "confirmed" for e in data.get("events") or [])):
        d2 = with_buffer(data, buffer_min)
        if confirmed_only:
            d2["events"] = [e for e in d2["events"] if e.get("status") == "confirmed"]
        tmp = SCHED_DIR / f"_dryrun-{time.strftime('%H%M%S')}.json"
        json.dump(d2, open(tmp, "w"), ensure_ascii=False, indent=2)
        schedule_path = tmp
    else:
        tmp = None

    files = T.list_images(folder)
    if sample:
        files = T.spread(files, sample)
    off = camera_offset_for(camera_tz, force=True)
    props = ST.schedule_proposals(files, str(schedule_path), folder, off)
    images = props["images"]

    by_event = Counter()
    by_act = Counter()
    changeover = 0
    cheki = 0
    meet = 0
    outside = 0
    review = 0
    for r in images:
        if r.get("needs_review") and not r.get("event"):
            # no event match usually means outside, unless no EXIF
            if any("no EXIF" in x for x in (r.get("review_reasons") or [])):
                review += 1
            else:
                outside += 1
            continue
        if not r.get("event"):
            outside += 1
            continue
        by_event[r["event"]] += 1
        kws = r.get("proposed_keywords") or []
        if "Gig/Changeover" in kws:
            changeover += 1
        if "Gig/Cheki" in kws:
            cheki += 1
        if "Gig/Meet & Greet" in kws:
            meet += 1
        for k in kws:
            if k.startswith("Gig/Act/"):
                by_act[k.split("/", 2)[-1]] += 1
        if r.get("needs_review"):
            review += 1

    if tmp and tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass

    return {
        "folder": str(folder),
        "folder_name": folder.name,
        "schedule": str(schedule_path if not tmp else schedule_path),
        "camera_tz": camera_tz,
        "camera_offset": off,
        "buffer_min": buffer_min,
        "frames": len(images),
        "outside_slots": outside,
        "changeover": changeover,
        "cheki": cheki,
        "meet": meet,
        "needs_review": review,
        "by_event": dict(by_event),
        "by_act": dict(by_act),
        "sample_rows": [
            {"filename": r["filename"], "local_time": r.get("local_time"), "event": r.get("event"),
             "keywords": r.get("proposed_keywords"), "review": r.get("review_reasons")}
            for r in images[:25]
        ],
    }


def prepare_apply_schedule(data: dict | None, schedule_path: str | None, buffer_min: int,
                           confirmed_only: bool = True) -> Path:
    """Write a working schedule JSON with buffer / confirmed filter for the job runner."""
    if data is None:
        data = json.load(open(schedule_path))
    d2 = with_buffer(data, buffer_min)
    if confirmed_only:
        d2["events"] = [e for e in d2.get("events") or [] if e.get("status") == "confirmed"]
    path = save_schedule(d2, filename=f"apply-{time.strftime('%Y%m%d-%H%M%S')}.json")
    return path
