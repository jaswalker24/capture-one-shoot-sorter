#!/usr/bin/env python3
"""Schedules tree: spreadsheet editor, history, pre-load manifest, auto-queue lookup.

All NAS writes go through confirm_* helpers. Tests must point schedules_root
at a temp directory (or set LOCALAI_SCHEDULES). Never create the live NAS tree.
"""
from __future__ import annotations

import json, os, re, time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
DRAFTS_DIR = HOME / "gui" / "schedule-drafts"
MERGED_DIR = HOME / "schedules"
JOBS_DIR = HOME / "gui" / "jobs"
HISTORY_DIR = HOME / "gui" / "history"

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
ILLEGAL_FOLDER = re.compile(r'[\\/:*?"<>|]')
JAPAN_TRIP_START = date(2026, 11, 19)
JAPAN_TRIP_END = date(2026, 11, 28)
APPLY_UNTIL_DAYS = 14
CLASH_TOLERANCE_MIN = 5
GAP_WARN_MIN = 60
VALID_SOURCES = ("manual", "poster", "notion", "rollback")
VALID_STATES = ("draft", "pending", "waiting", "applied", "held", "skipped")
EVENT_TYPES = ("gig", "wedding", "corporate")

COLUMN_HINTS = {
    "gig": {
        "act": "Act name",
        "start": "Set start",
        "end": "Set end",
        "doors": "Doors / 開場",
        "notes": "Notes",
    },
    "wedding": {
        "act": "Moment or group",
        "start": "Start",
        "end": "End",
        "doors": "Arrival",
        "notes": "Notes",
    },
    "corporate": {
        "act": "Segment",
        "start": "Start",
        "end": "End",
        "doors": "Doors",
        "notes": "Notes",
    },
}


def default_timezone(d: date | str) -> str:
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except Exception:
            return "Europe/London"
    if JAPAN_TRIP_START <= d <= JAPAN_TRIP_END:
        return "Asia/Tokyo"
    return "Europe/London"


def sanitize_event_name(name: str) -> str:
    s = ILLEGAL_FOLDER.sub("-", (name or "").strip())
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip(" .-")
    if not s or s in {".", ".."}:
        s = "Untitled event"
    raw = s.encode("utf-8")
    while len(raw) > 240:
        s = s[:-1].rstrip(" .-")
        raw = s.encode("utf-8")
    return s or "Untitled event"


def parse_time(value) -> str | None:
    """Accept 19:30, 1930, 19.30, 9:30, 930. Return HH:MM or None."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    s = s.replace(".", ":")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        digits = re.fullmatch(r"(\d{3,4})", s)
        if not digits:
            return None
        n = digits.group(1)
        if len(n) == 3:
            hh, mm = int(n[0]), int(n[1:])
        else:
            hh, mm = int(n[:2]), int(n[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _add_days_iso(date_str: str, days: int) -> str:
    return (date.fromisoformat(date_str) + timedelta(days=days)).isoformat()


def event_dir(root: Path, date_str: str, event_name: str) -> Path:
    yyyy = date_str[:4]
    return Path(root) / yyyy / date_str / sanitize_event_name(event_name)


def volume_for(path: Path) -> Path | None:
    parts = Path(path).resolve().parts if Path(path).is_absolute() else Path(path).parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return Path("/Volumes") / parts[2]
    return None


def nas_status(root: Path) -> dict:
    """Whether the Schedules root can be used. Missing tree is OK; unmounted volume is not."""
    root = Path(root)
    vol = volume_for(root)
    if vol is not None and not vol.exists():
        return {"ok": False, "mounted": False, "exists": False,
                "error": "NAS is not mounted. Your draft is still saved on this Mac."}
    return {"ok": True, "mounted": True, "exists": root.is_dir(), "error": None}


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def slug_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return s or "event"


def column_hints(event_type: str) -> dict:
    return dict(COLUMN_HINTS.get(event_type) or COLUMN_HINTS["gig"])


# ------------------------------------------------------------------ editor

def rows_from_schedule(data: dict) -> list[dict]:
    rows = []
    for ev in data.get("events") or []:
        doors = ev.get("doors") or ""
        for sl in ev.get("slots") or []:
            acts = sl.get("act")
            if acts is None:
                acts = ", ".join(sl.get("acts") or [])
            rows.append({
                "act": acts or "",
                "start": sl.get("start") or "",
                "end": sl.get("end") or "",
                "doors": doors if not rows else "",
                "notes": sl.get("note") or sl.get("notes") or "",
                "type": sl.get("type") or "set",
            })
            doors = ""
        for a in ev.get("acts_tba") or []:
            rows.append({"act": a, "start": "", "end": "", "doors": "", "notes": "TBA", "type": "tba"})
    return rows


def build_schedule(meta: dict, rows: Iterable[dict]) -> dict:
    date_str = (meta.get("date") or "").strip()
    event_name = (meta.get("event") or meta.get("name") or "").strip()
    venue = (meta.get("venue") or "").strip()
    tz = meta.get("timezone") or default_timezone(date_str)
    buffer_min = int(meta.get("changeover_buffer_min") or meta.get("buffer_min") or 3)
    event_type = meta.get("event_type") or "gig"
    source = meta.get("source") or "manual"
    if source not in VALID_SOURCES:
        source = "manual"

    slots, tba = [], []
    doors_times, start_vals, end_vals = [], [], []
    for raw in rows:
        act = (raw.get("act") or "").strip()
        st, en = parse_time(raw.get("start")), parse_time(raw.get("end"))
        doors = parse_time(raw.get("doors"))
        if doors:
            doors_times.append(doors)
        typ = raw.get("type") or "set"
        notes = (raw.get("notes") or raw.get("note") or "").strip()
        if typ == "tba" or (act and not st and not en):
            if act:
                tba.append(act)
            continue
        slot = {"act": act, "type": typ if typ in ("set", "show", "cheki", "meet") else "set"}
        if st:
            slot["start"] = st
            start_vals.append((st, _minutes(st)))
        if en:
            slot["end"] = en
            # rolled minutes for window end
            em = _minutes(en)
            if st and em <= _minutes(st):
                em += 24 * 60
            end_vals.append((en, em))
        if notes:
            slot["note"] = notes
        slots.append(slot)

    first_start = min(start_vals, key=lambda x: x[1])[0] if start_vals else None
    last_end = max(end_vals, key=lambda x: x[1])[0] if end_vals else None
    event_doors = None
    if doors_times:
        event_doors = min(doors_times, key=_minutes)
    elif first_start:
        event_doors = first_start

    ev = {
        "id": meta.get("id") or slug_id(event_name),
        "status": meta.get("status") or "confirmed",
        "date": date_str,
        "event": event_name,
        "venue": venue,
        "doors": event_doors,
        "start": first_start,
        "window_end": last_end,
        "slots": slots,
        "acts_tba": tba,
        "no_photo_acts": list(meta.get("no_photo_acts") or []),
    }
    return {
        "name": event_name,
        "source": source,
        "source_label": meta.get("source_label") or {
            "manual": "typed in the Schedules tab",
            "poster": "read from a poster",
            "notion": "exported from Notion",
            "rollback": "rolled back from history",
        }.get(source, source),
        "timezone": tz,
        "changeover_buffer_min": buffer_min,
        "event_type": event_type if event_type in EVENT_TYPES else "gig",
        "events": [ev],
    }


def _slot_span(date_str: str, start: str, end: str):
    """Return (start_dt, end_dt) with midnight roll, naive local."""
    s = datetime.fromisoformat(f"{date_str}T{start}")
    e = datetime.fromisoformat(f"{date_str}T{end}")
    if e <= s:
        e = e + timedelta(days=1)
    return s, e


def validate_event(meta: dict, rows: list[dict], confirmed: dict | None = None) -> dict:
    errors, warnings = [], []
    date_str = (meta.get("date") or "").strip()
    event_name = (meta.get("event") or "").strip()
    if not date_str or not DATE_RE.match(date_str):
        errors.append({"code": "bad_date", "level": "red", "message": "Pick a date in YYYY-MM-DD form."})
    if not event_name:
        errors.append({"code": "blank_event", "level": "red", "message": "Enter an event name."})
    tz_name = meta.get("timezone") or (default_timezone(date_str) if date_str else "Europe/London")
    try:
        ZoneInfo(tz_name)
    except Exception:
        errors.append({"code": "bad_tz", "level": "red", "message": f"Unknown timezone {tz_name!r}."})

    parsed = []
    seen = set()
    first_start = None
    doors_event = None
    for i, raw in enumerate(rows):
        act = (raw.get("act") or "").strip()
        st, en = parse_time(raw.get("start")), parse_time(raw.get("end"))
        doors = parse_time(raw.get("doors"))
        row_errs, row_warns = [], []
        if not act:
            row_errs.append("blank_act")
            errors.append({"code": "blank_act", "level": "red", "row": i,
                           "message": f"Row {i + 1} has no act name."})
        if (st and not en) or (en and not st):
            row_errs.append("incomplete_times")
            errors.append({"code": "incomplete_times", "level": "red", "row": i,
                           "message": f"Row {i + 1} needs both a start and an end, or neither."})
        midnight = False
        if st and en:
            if st == en:
                row_errs.append("end_not_after")
                errors.append({"code": "end_not_after", "level": "red", "row": i,
                               "message": f"Row {i + 1}: end must be after start."})
            elif _minutes(en) <= _minutes(st):
                midnight = True
                row_warns.append("midnight")
                warnings.append({"code": "midnight", "level": "amber", "row": i,
                                 "message": f"Row {i + 1} crosses midnight; the end will be the next day, and the folder date stays {date_str or 'the show date'}."})
            if date_str and st != en:
                parsed.append({"i": i, "act": act, "start": st, "end": en,
                               "span": _slot_span(date_str, st, en)})
            if first_start is None or (st and _minutes(st) < _minutes(first_start) and not midnight):
                # first start is the earliest clock start on the show date; midnight starts are later that night
                if first_start is None:
                    first_start = st
                elif not midnight and _minutes(st) < _minutes(first_start):
                    first_start = st
            elif first_start is None:
                first_start = st
        if doors:
            if doors_event is None or _minutes(doors) < _minutes(doors_event):
                doors_event = doors
        key = (act.lower(), st or "", en or "")
        if act and key in seen:
            warnings.append({"code": "duplicate", "level": "amber", "row": i,
                             "message": f"Row {i + 1} is a duplicate of an earlier row."})
        elif act:
            seen.add(key)
    timed = [p for p in parsed if isinstance(p, dict) and p.get("span")]
    if timed:
        first_start = min(timed, key=lambda p: p["span"][0])["start"]

    if doors_event and first_start and _minutes(doors_event) > _minutes(first_start):
        # doors after first start, unless first start has rolled (00:xx) and doors are evening
        first_span_start = min(p["span"][0] for p in timed) if timed else None
        doors_dt = datetime.fromisoformat(f"{date_str or '2000-01-01'}T{doors_event}") if date_str else None
        if doors_dt and first_span_start and doors_dt > first_span_start:
            warnings.append({"code": "doors_after_start", "level": "amber",
                             "message": f"Doors ({doors_event}) are after the first start ({first_start})."})
        elif not date_str:
            warnings.append({"code": "doors_after_start", "level": "amber",
                             "message": f"Doors ({doors_event}) are after the first start ({first_start})."})

    # overlaps and gaps
    sets = sorted(timed, key=lambda p: p["span"][0])
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            as_, ae = a["span"]; bs, be = b["span"]
            if as_ < be and bs < ae:
                if ae == bs or be == as_:
                    continue
                warnings.append({
                    "code": "overlap", "level": "amber", "row": a["i"],
                    "message": (f"{a['act'] or 'Row ' + str(a['i'] + 1)} {a['start']}–{a['end']} "
                                f"overlaps {b['act'] or 'row ' + str(b['i'] + 1)} {b['start']}–{b['end']}.")
                })
        if i + 1 < len(sets):
            b = sets[i + 1]
            gap = (b["span"][0] - a["span"][1]).total_seconds() / 60
            if gap > GAP_WARN_MIN:
                warnings.append({
                    "code": "gap", "level": "amber", "row": a["i"],
                    "message": f"{int(gap)}-minute gap between {a['act'] or a['start']} and {b['act'] or b['start']}."
                })

    if confirmed:
        warnings.extend(_clash_warnings(build_schedule(meta, rows), confirmed))

    # dedupe messages
    def _dedupe(items):
        out, seen_m = [], set()
        for it in items:
            k = (it.get("code"), it.get("row"), it.get("message"))
            if k in seen_m:
                continue
            seen_m.add(k)
            out.append(it)
        return out

    errors, warnings = _dedupe(errors), _dedupe(warnings)
    can_save = not errors
    return {
        "ok": can_save and not warnings,
        "can_save": can_save,
        "needs_ack": can_save and bool(warnings),
        "errors": errors,
        "warnings": warnings,
        "timezone": tz_name,
        "hints": column_hints(meta.get("event_type") or "gig"),
        "folder": str(event_dir(Path(meta.get("schedules_root") or "Schedules"), date_str or "0000-00-00", event_name or "Untitled event")) if date_str or event_name else "",
    }


def _acts_of(sl) -> list[str]:
    if "act" in sl and sl["act"]:
        return [sl["act"]]
    return list(sl.get("acts") or [])


def _clash_warnings(built: dict, confirmed: dict) -> list[dict]:
    out = []
    be = (built.get("events") or [None])[0]
    conf_events = confirmed.get("events") or ([confirmed] if confirmed.get("event") else [])
    if not be or not conf_events:
        return out
    # match by event name, else the only confirmed event
    ce = next((e for e in conf_events if (e.get("event") or "").strip() == (be.get("event") or "").strip()), None)
    if ce is None and len(conf_events) == 1:
        ce = conf_events[0]
    if ce is None:
        out.append({"code": "clash", "level": "amber",
                    "message": "This event name is not in confirmed.json."})
        return out

    def slots_of(ev):
        items = []
        for sl in ev.get("slots") or []:
            st, en = parse_time(sl.get("start")), parse_time(sl.get("end"))
            if not st or not en:
                continue
            for a in _acts_of(sl):
                items.append((a, st, en, sl.get("type") or "set"))
        return items

    bslots, cslots = slots_of(be), slots_of(ce)
    # time/name match within 5 minutes
    used = set()
    for a, st, en, typ in bslots:
        found = None
        for j, (ca, cst, cen, ctyp) in enumerate(cslots):
            if j in used:
                continue
            if a.strip().lower() != ca.strip().lower():
                continue
            ds = abs(_minutes(st) - _minutes(cst))
            de = abs(_minutes(en) - _minutes(cen))
            # account for midnight wrap (e.g. 00:10 vs 24:10 not stored)
            ds = min(ds, 24 * 60 - ds)
            de = min(de, 24 * 60 - de)
            if ds <= CLASH_TOLERANCE_MIN and de <= CLASH_TOLERANCE_MIN:
                found = j
                break
        if found is None:
            out.append({"code": "clash", "level": "amber",
                        "message": f"Act {a} {st}–{en} is not in the confirmed schedule (or the time differs by more than {CLASH_TOLERANCE_MIN} minutes)."})
        else:
            used.add(found)
    for j, (ca, cst, cen, _t) in enumerate(cslots):
        if j not in used:
            out.append({"code": "clash", "level": "amber",
                        "message": f"Confirmed act {ca} {cst}–{cen} is missing from this schedule."})
    # order
    bnames = [a for a, *_ in bslots]
    cnames = [a for a, *_ in cslots]
    if [n for n in bnames if n in cnames] != [n for n in cnames if n in bnames]:
        out.append({"code": "clash", "level": "amber",
                    "message": "Act order differs from confirmed.json."})
    return out


# ------------------------------------------------------------------ tree listing

def list_events(root: Path) -> list[dict]:
    root = Path(root)
    st = nas_status(root)
    if not st["ok"]:
        return []
    if not root.is_dir():
        return []
    out = []
    for year in sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit()) if root.is_dir() else []:
        for day in sorted(p for p in year.iterdir() if p.is_dir() and DATE_RE.match(p.name)):
            for evdir in sorted(p for p in day.iterdir() if p.is_dir() and not p.name.startswith(".")):
                out.append(_event_info(evdir, root))
    return out


def _event_info(evdir: Path, root: Path) -> dict:
    rel = str(evdir.relative_to(root)) if evdir.is_relative_to(root) else str(evdir)
    sched_p = evdir / "schedule.json"
    conf_p = evdir / "confirmed.json"
    poster_p = evdir / "poster.json"
    data = _read_json(sched_p) if sched_p.is_file() else None
    ev = ((data or {}).get("events") or [{}])[0] if data else {}
    date_str = ev.get("date") or evdir.parent.name
    name = ev.get("event") or evdir.name
    posters = [p.name for p in evdir.iterdir() if p.is_file() and p.name.lower().startswith("_schedule")] if evdir.is_dir() else []
    hist = evdir / "history.json"
    versions = 0
    if hist.is_file():
        try:
            versions = len((_read_json(hist) or {}).get("versions") or [])
        except Exception:
            versions = 0
    mtime = None
    if sched_p.is_file():
        mtime = sched_p.stat().st_mtime
    return {
        "date": date_str,
        "event": name,
        "venue": ev.get("venue") or "",
        "timezone": (data or {}).get("timezone") or "",
        "path": str(evdir),
        "rel": rel.replace("\\", "/"),
        "folder_name": evdir.name,
        "has_schedule": sched_p.is_file(),
        "has_confirmed": conf_p.is_file(),
        "has_poster": poster_p.is_file(),
        "posters": posters,
        "versions": versions,
        "mtime": mtime,
        "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)) if mtime else None,
        "source": (data or {}).get("source") or "",
        "acts": len(ev.get("slots") or []),
    }


def load_event(root: Path, date_str: str, event_name: str) -> dict:
    st = nas_status(root)
    if not st["ok"]:
        return {"ok": False, "error": st["error"], "mounted": False}
    evdir = event_dir(root, date_str, event_name)
    # also allow exact folder if it already exists under a slightly different sanitised name
    if not evdir.is_dir():
        day = Path(root) / date_str[:4] / date_str
        if day.is_dir():
            for p in day.iterdir():
                if p.is_dir() and (p.name == event_name or p.name == sanitize_event_name(event_name)):
                    evdir = p
                    break
    info = _event_info(evdir, Path(root)) if evdir.is_dir() else {
        "date": date_str, "event": event_name, "venue": "", "path": str(evdir),
        "rel": str(Path(date_str[:4]) / date_str / sanitize_event_name(event_name)),
        "has_schedule": False, "has_confirmed": False, "has_poster": False,
        "posters": [], "versions": 0, "mtime": None, "mtime_iso": None, "acts": 0,
    }
    data = _read_json(evdir / "schedule.json") if (evdir / "schedule.json").is_file() else None
    confirmed = _read_json(evdir / "confirmed.json") if (evdir / "confirmed.json").is_file() else None
    rows = rows_from_schedule(data) if data else []
    meta = {
        "date": date_str,
        "event": (data or {}).get("name") or event_name,
        "venue": ((data or {}).get("events") or [{}])[0].get("venue") if data else "",
        "timezone": (data or {}).get("timezone") or default_timezone(date_str),
        "changeover_buffer_min": (data or {}).get("changeover_buffer_min", 3),
        "event_type": (data or {}).get("event_type") or "gig",
        "source": (data or {}).get("source") or "",
    }
    if data and not meta["venue"]:
        ev0 = (data.get("events") or [{}])[0]
        meta["venue"] = ev0.get("venue") or ""
    return {
        "ok": True,
        "mounted": True,
        "exists": evdir.is_dir(),
        "info": info,
        "meta": meta,
        "rows": rows,
        "schedule": data,
        "confirmed": confirmed,
        "hints": column_hints(meta["event_type"]),
        "mtime": info.get("mtime"),
        "folder": str(evdir),
    }


# ------------------------------------------------------------------ drafts (M5 only)

def draft_key(date_str: str, event_name: str) -> str:
    safe = sanitize_event_name(event_name).replace(" ", "_")
    return f"{date_str}--{safe}.json"


def save_draft(meta: dict, rows: list, *, drafts_dir: Path | None = None) -> Path:
    ddir = Path(drafts_dir or DRAFTS_DIR)
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / draft_key(meta.get("date") or "undated", meta.get("event") or "Untitled event")
    payload = {"meta": meta, "rows": rows, "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _atomic_write(path, payload)
    return path


def load_draft(date_str: str, event_name: str, *, drafts_dir: Path | None = None) -> dict | None:
    path = Path(drafts_dir or DRAFTS_DIR) / draft_key(date_str, event_name)
    return _read_json(path)


def delete_draft(date_str: str, event_name: str, *, drafts_dir: Path | None = None) -> None:
    path = Path(drafts_dir or DRAFTS_DIR) / draft_key(date_str, event_name)
    if path.is_file():
        path.unlink()


# ------------------------------------------------------------------ paste

_HEADER_ALIASES = {
    "act": {"act", "acts", "artist", "band", "name", "segment", "moment", "item"},
    "start": {"start", "start time", "from", "begins", "set start"},
    "end": {"end", "end time", "to", "until", "set end"},
    "doors": {"doors", "open", "open time", "door", "開場", "arrival"},
    "notes": {"notes", "note", "comments", "comment"},
    "date": {"date", "day"},
    "event": {"event", "show", "title"},
    "venue": {"venue", "place", "hall"},
}


def _header_key(cell: str) -> str | None:
    s = (cell or "").strip().lower()
    for key, aliases in _HEADER_ALIASES.items():
        if s in aliases:
            return key
    return None


def parse_paste(text: str) -> dict:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not raw.strip():
        raise ValueError("Nothing to paste.")
    first = raw.split("\n", 1)[0]
    delim = "\t" if "\t" in first else ("," if "," in first else "\t")
    lines = [ln.split(delim) for ln in raw.split("\n") if ln.strip()]
    if not lines:
        raise ValueError("Nothing to paste.")
    mapped = [_header_key(c) for c in lines[0]]
    has_header = any(mapped) and sum(1 for m in mapped if m) >= 2
    columns = []
    start = 0
    if has_header:
        start = 1
        columns = [m or f"col{i+1}" for i, m in enumerate(mapped)]
    else:
        default = ["act", "start", "end", "doors", "notes"]
        n = max(len(r) for r in lines)
        columns = (default + [f"col{i+1}" for i in range(n)])[:n]
    rows = []
    for r in lines[start:]:
        item = {columns[i]: (r[i].strip() if i < len(r) else "") for i in range(len(columns))}
        if "start" in item:
            item["start_parsed"] = parse_time(item.get("start"))
        if "end" in item:
            item["end_parsed"] = parse_time(item.get("end"))
        rows.append(item)
    preview = []
    for item in rows:
        preview.append({
            "act": item.get("act") or "",
            "start": item.get("start_parsed") or item.get("start") or "",
            "end": item.get("end_parsed") or item.get("end") or "",
            "doors": parse_time(item.get("doors")) or item.get("doors") or "",
            "notes": item.get("notes") or "",
        })
    return {"has_header": has_header, "columns": columns, "delimiter": "tab" if delim == "\t" else "comma",
            "rows": preview, "count": len(preview)}


# ------------------------------------------------------------------ history

def _history_dir(evdir: Path) -> Path:
    return evdir / ".history"


def _history_index(evdir: Path) -> dict:
    p = evdir / "history.json"
    data = _read_json(p) or {"versions": []}
    data.setdefault("versions", [])
    return data


def _change_summary(old: dict | None, new: dict, source: str) -> str:
    if not old:
        n = len((new.get("events") or [{}])[0].get("slots") or [])
        return f"Created with {n} act{'s' if n != 1 else ''} ({source})"
    oev = (old.get("events") or [{}])[0]
    nev = (new.get("events") or [{}])[0]
    bits = []
    if (oev.get("venue") or "") != (nev.get("venue") or ""):
        bits.append(f"venue {oev.get('venue') or '—'} → {nev.get('venue') or '—'}")
    oslots = {(sl.get("act"), sl.get("start"), sl.get("end")): sl for sl in oev.get("slots") or []}
    nslots = {(sl.get("act"), sl.get("start"), sl.get("end")): sl for sl in nev.get("slots") or []}
    added = [k for k in nslots if k not in oslots]
    removed = [k for k in oslots if k not in nslots]
    # detect time changes for same act
    by_act_old = {}
    for a, s, e in oslots:
        by_act_old.setdefault(a, []).append((s, e))
    by_act_new = {}
    for a, s, e in nslots:
        by_act_new.setdefault(a, []).append((s, e))
    changed = []
    for a, times in by_act_new.items():
        if a in by_act_old and times != by_act_old[a]:
            ot = ", ".join(f"{s}–{e}" for s, e in by_act_old[a])
            nt = ", ".join(f"{s}–{e}" for s, e in times)
            changed.append(f"{a} {ot} → {nt}")
    if changed:
        bits.append("updated " + "; ".join(changed[:4]))
    if added:
        bits.append("added " + ", ".join(a or "?" for a, *_ in added[:4]))
    if removed:
        bits.append("removed " + ", ".join(a or "?" for a, *_ in removed[:4]))
    if not bits:
        bits.append("saved with no row changes")
    return "; ".join(bits) + f" ({source})"


def write_history_copy(evdir: Path, data: dict, source: str, summary: str | None = None,
                       now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    idx = _history_index(evdir)
    versions = idx["versions"]
    n = (max((v.get("version") or 0) for v in versions) + 1) if versions else 1
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"v{n:03d}-{stamp}.json"
    hdir = _history_dir(evdir)
    hdir.mkdir(parents=True, exist_ok=True)
    dest = hdir / fname
    _atomic_write(dest, data)
    entry = {
        "version": n,
        "file": fname,
        "time": now.isoformat(timespec="seconds"),
        "source": source,
        "summary": summary or _change_summary(None, data, source),
        "confirmed_by": "panel",
    }
    versions.append(entry)
    idx["event"] = ((data.get("events") or [{}])[0]).get("event") or idx.get("event")
    idx["date"] = ((data.get("events") or [{}])[0]).get("date") or idx.get("date")
    idx["versions"] = versions
    _atomic_write(evdir / "history.json", idx)
    return entry


def history_list(evdir: Path) -> dict:
    idx = _history_index(evdir)
    versions = list(reversed(idx.get("versions") or []))
    return {"event": idx.get("event"), "date": idx.get("date"), "versions": versions}


def history_version(evdir: Path, version: int) -> dict | None:
    idx = _history_index(evdir)
    entry = next((v for v in idx.get("versions") or [] if v.get("version") == int(version)), None)
    if not entry:
        return None
    path = _history_dir(evdir) / entry["file"]
    if not path.is_file():
        return None
    return {"entry": entry, "data": _read_json(path)}


def history_diff(evdir: Path, version_a: int, version_b: int | None = None) -> dict:
    """Side-by-side: A is an older version, B is another version or the current schedule.json."""
    va = history_version(evdir, version_a)
    if not va:
        raise ValueError(f"version {version_a} not found")
    if version_b is None:
        current = _read_json(evdir / "schedule.json")
        vb = {"entry": {"version": "current", "file": "schedule.json", "source": (current or {}).get("source")},
              "data": current}
    else:
        vb = history_version(evdir, version_b)
        if not vb:
            raise ValueError(f"version {version_b} not found")
    def rows(data):
        return rows_from_schedule(data or {})
    ra, rb = rows(va["data"]), rows(vb["data"])
    n = max(len(ra), len(rb))
    lines = []
    for i in range(n):
        a = ra[i] if i < len(ra) else None
        b = rb[i] if i < len(rb) else None
        if a == b:
            status = "same"
        elif a is None:
            status = "added"
        elif b is None:
            status = "removed"
        else:
            status = "changed"
        lines.append({"status": status, "left": a, "right": b})
    return {
        "left": va["entry"],
        "right": vb["entry"],
        "rows": lines,
        "left_meta": {"timezone": (va["data"] or {}).get("timezone"),
                      "venue": ((va["data"] or {}).get("events") or [{}])[0].get("venue")},
        "right_meta": {"timezone": (vb["data"] or {}).get("timezone"),
                       "venue": ((vb["data"] or {}).get("events") or [{}])[0].get("venue")},
    }


def jobs_that_used_schedule(schedule_path: str | Path, *, jobs_dir: Path | None = None,
                            history_dir: Path | None = None) -> list[dict]:
    """Jobs that applied keywords from this schedule file (for the rollback warning)."""
    target = str(Path(schedule_path))
    found = []
    for base in (Path(jobs_dir or JOBS_DIR), Path(history_dir or HISTORY_DIR)):
        if not base.is_dir():
            continue
        for p in base.glob("*.json"):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            paths = []
            for key in ("schedule_path", "schedule_paths"):
                v = j.get(key)
                if isinstance(v, list):
                    paths.extend(str(x) for x in v)
                elif v:
                    paths.append(str(v))
            if target not in paths:
                continue
            added = j.get("added_keywords") or {}
            n = 0
            if isinstance(added, dict):
                for v in added.values():
                    n += len(v) if isinstance(v, list) else (1 if v else 0)
            if n == 0 and not (j.get("pass_results") or {}).get("schedule"):
                continue
            if j.get("undo"):
                continue
            found.append({
                "id": j.get("id") or p.stem,
                "folder": j.get("folder_name") or j.get("folder"),
                "status": j.get("status"),
                "added": n,
                "finished": j.get("finished"),
            })
    return found


# ------------------------------------------------------------------ confirm / rollback

def confirm_save(root: Path, meta: dict, rows: list[dict], *,
                 ack_warnings: bool = False, expected_mtime: float | None = None,
                 force: bool = False, drafts_dir: Path | None = None,
                 jobs_dir: Path | None = None, history_jobs: Path | None = None,
                 now: datetime | None = None, move_from: dict | None = None) -> dict:
    """Write schedule.json and a history copy. The only routine that should create event folders."""
    st = nas_status(root)
    if not st["ok"]:
        # keep / refresh draft
        try:
            save_draft(meta, rows, drafts_dir=drafts_dir)
        except Exception:
            pass
        return {"ok": False, "error": st["error"], "kept_draft": True, "mounted": False}

    result = validate_event({**meta, "schedules_root": str(root)}, rows,
                            confirmed=_load_confirmed(root, meta))
    if not result["can_save"]:
        return {"ok": False, "error": "Fix the red errors before saving.", "validation": result}
    if result["needs_ack"] and not ack_warnings:
        return {"ok": False, "error": "Tick to confirm the warnings before saving.",
                "needs_ack": True, "validation": result}

    data = build_schedule(meta, rows)
    evdir = event_dir(root, meta["date"], meta["event"])
    sched_path = evdir / "schedule.json"

    if sched_path.is_file() and expected_mtime is not None and not force:
        current = sched_path.stat().st_mtime
        if abs(current - float(expected_mtime)) > 0.5:
            return {"ok": False, "error": "This schedule changed since you opened it. Reload before saving, or confirm overwrite.",
                    "changed": True, "mtime": current}

    evdir.mkdir(parents=True, exist_ok=True)
    old = _read_json(sched_path) if sched_path.is_file() else None
    summary = _change_summary(old, data, data.get("source") or "manual")
    _atomic_write(sched_path, data)
    entry = write_history_copy(evdir, data, data.get("source") or "manual", summary, now=now)
    try:
        delete_draft(meta["date"], meta["event"], drafts_dir=drafts_dir)
    except Exception:
        pass

    applied = jobs_that_used_schedule(sched_path, jobs_dir=jobs_dir, history_dir=history_jobs)
    return {
        "ok": True,
        "path": str(sched_path),
        "folder": str(evdir),
        "history": entry,
        "mtime": sched_path.stat().st_mtime,
        "applied_jobs": applied,
        "validation": result,
    }


def _load_confirmed(root: Path, meta: dict) -> dict | None:
    evdir = event_dir(root, meta.get("date") or "", meta.get("event") or "")
    p = evdir / "confirmed.json"
    return _read_json(p) if p.is_file() else None


def rollback(root: Path, date_str: str, event_name: str, version: int, *,
             jobs_dir: Path | None = None, history_jobs: Path | None = None,
             now: datetime | None = None) -> dict:
    st = nas_status(root)
    if not st["ok"]:
        return {"ok": False, "error": st["error"], "mounted": False}
    evdir = event_dir(root, date_str, event_name)
    ver = history_version(evdir, version)
    if not ver:
        return {"ok": False, "error": f"Version {version} was not found."}
    data = deepcopy(ver["data"])
    data["source"] = "rollback"
    data["source_label"] = f"rolled back to v{int(version):03d}"
    sched_path = evdir / "schedule.json"
    applied = jobs_that_used_schedule(sched_path, jobs_dir=jobs_dir, history_dir=history_jobs)
    _atomic_write(sched_path, data)
    summary = f"Rolled back to v{int(version):03d}"
    entry = write_history_copy(evdir, data, "rollback", summary, now=now)
    return {
        "ok": True,
        "path": str(sched_path),
        "history": entry,
        "rolled_back_to": ver["entry"],
        "applied_jobs": applied,
        "warning": ("Keywords were already applied from an earlier version. "
                    "A rollback does not remove keywords. Use History & undo on that run if you need them taken off.")
                   if applied else None,
    }


def review_summary(root: Path, meta: dict, rows: list[dict]) -> dict:
    st = nas_status(root)
    evdir = event_dir(root, meta.get("date") or "0000-00-00", meta.get("event") or "Untitled event")
    exists = (evdir / "schedule.json").is_file()
    val = validate_event({**meta, "schedules_root": str(root)}, rows, confirmed=_load_confirmed(root, meta))
    data = build_schedule(meta, rows) if val["can_save"] or rows else None
    return {
        "mounted": st["ok"],
        "error": st.get("error"),
        "folder": str(evdir),
        "rel": f"{(meta.get('date') or '')[:4]}/{meta.get('date')}/{sanitize_event_name(meta.get('event') or 'Untitled event')}",
        "creates": not exists,
        "replaces": exists,
        "rows": len(rows),
        "acts": [((r.get("act") or ""), parse_time(r.get("start")), parse_time(r.get("end"))) for r in rows],
        "timezone": meta.get("timezone") or default_timezone(meta.get("date") or ""),
        "buffer_min": int(meta.get("changeover_buffer_min") or 3),
        "source": meta.get("source") or "manual",
        "validation": val,
        "schedule": data,
    }


# ------------------------------------------------------------------ manifest / pre-load

def manifest_path(root: Path) -> Path:
    return Path(root) / "manifest.json"


def load_manifest(root: Path) -> dict:
    p = manifest_path(root)
    data = _read_json(p)
    if not data:
        return {"version": 1, "entries": []}
    data.setdefault("version", 1)
    data.setdefault("entries", [])
    return data


def save_manifest(root: Path, data: dict) -> Path:
    p = manifest_path(root)
    # do not create Schedules root unless the caller already has it (confirm/load-trip)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, data)
    return p


def _parse_iso(ts: str) -> datetime:
    if not ts:
        raise ValueError("missing timestamp")
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"activation time must include a timezone offset: {ts}")
    return dt


def _format_activation(dt: datetime) -> str:
    # keep the original offset, including +09:00 for Japan
    return dt.isoformat(timespec="seconds")


def _entry_id(date_str: str, event_name: str) -> str:
    return f"{date_str}-{slug_id(event_name)}"


def upsert_manifest_entry(root: Path, entry: dict, *, write: bool = True) -> dict:
    man = load_manifest(root)
    eid = entry.get("id") or _entry_id(entry["date"], entry["event"])
    entry = dict(entry)
    entry["id"] = eid
    entries = man["entries"]
    for i, e in enumerate(entries):
        if e.get("id") == eid or (e.get("date") == entry.get("date") and e.get("event") == entry.get("event")):
            entries[i] = {**e, **entry}
            man["entries"] = entries
            if write:
                save_manifest(root, man)
            return entries[i]
    entries.append(entry)
    if write:
        save_manifest(root, man)
    return entry


def default_activation(meta: dict, rows: list[dict] | None = None) -> str:
    date_str = meta.get("date")
    tz_name = meta.get("timezone") or default_timezone(date_str)
    tz = ZoneInfo(tz_name)
    start = meta.get("start") or None
    if not start and rows:
        times = [parse_time(r.get("start")) for r in rows]
        times = [t for t in times if t]
        start = min(times, key=_minutes) if times else None
    if not start:
        ev = ((meta.get("events") or [{}])[0]) if "events" in meta else {}
        start = ev.get("start") or ev.get("doors") or "00:00"
    start = parse_time(start) or "00:00"
    dt = datetime.fromisoformat(f"{date_str}T{start}").replace(tzinfo=tz)
    return _format_activation(dt)


def apply_until_of(activation: str, days: int = APPLY_UNTIL_DAYS) -> str:
    return _format_activation(_parse_iso(activation) + timedelta(days=days))


def tick_manifest(root: Path, now: datetime | None = None, dates_with_photos: Iterable[str] | None = None,
                  write: bool = True) -> dict:
    """Advance pending → waiting (activation passed, no photos) and waiting → skipped (past apply_until)."""
    st = nas_status(root)
    if not st["ok"] or not manifest_path(root).is_file():
        return {"ok": st["ok"], "changed": [], "entries": load_manifest(root)["entries"] if st["ok"] and manifest_path(root).is_file() else []}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    photos = set(dates_with_photos or [])
    man = load_manifest(root)
    changed = []
    for e in man["entries"]:
        state = e.get("state") or "pending"
        try:
            act = _parse_iso(e["activation"])
            until = _parse_iso(e["apply_until"]) if e.get("apply_until") else act + timedelta(days=APPLY_UNTIL_DAYS)
        except Exception:
            continue
        date_str = e.get("date")
        has_photos = date_str in photos
        new_state = state
        if state in ("held", "skipped", "draft", "applied"):
            continue
        if now >= until and not has_photos and state in ("pending", "waiting"):
            new_state = "skipped"
        elif now >= act and not has_photos and state == "pending":
            new_state = "waiting"
        if new_state != state:
            e["state"] = new_state
            e["updated"] = now.isoformat(timespec="seconds")
            changed.append({"id": e.get("id"), "from": state, "to": new_state})
    if write and changed:
        save_manifest(root, man)
    return {"ok": True, "changed": changed, "entries": man["entries"]}


def pending_entries(root: Path, now: datetime | None = None, dates_with_photos: Iterable[str] | None = None,
                    write: bool = False) -> dict:
    tick = tick_manifest(root, now=now, dates_with_photos=dates_with_photos, write=write)
    entries = tick.get("entries") or []
    waiting_states = {"pending", "waiting"}
    waiting = [e for e in entries if (e.get("state") or "") in waiting_states]
    return {"entries": entries, "waiting": waiting, "waiting_count": len(waiting),
            "changed": tick.get("changed") or []}


def set_manifest_state(root: Path, entry_id: str, state: str, **extra) -> dict:
    if state not in VALID_STATES:
        raise ValueError(f"unknown state {state}")
    man = load_manifest(root)
    for e in man["entries"]:
        if e.get("id") == entry_id:
            e["state"] = state
            e["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
            e.update({k: v for k, v in extra.items() if v is not None})
            save_manifest(root, man)
            return e
    raise ValueError(f"manifest entry {entry_id} not found")


def remove_manifest_entry(root: Path, entry_id: str) -> dict:
    """Remove from the manifest only. Never deletes schedule files."""
    man = load_manifest(root)
    kept, removed = [], None
    for e in man["entries"]:
        if e.get("id") == entry_id:
            removed = e
        else:
            kept.append(e)
    if removed is None:
        raise ValueError(f"manifest entry {entry_id} not found")
    man["entries"] = kept
    save_manifest(root, man)
    return {"ok": True, "removed": removed, "deleted_files": False}


def load_trip_entries(rows: list[dict], *, timezone_fallback: str | None = None) -> list[dict]:
    """Build pending manifest entries from a pasted table or import list. Does not write."""
    out = []
    for r in rows:
        date_str = (r.get("date") or "").strip()
        event = (r.get("event") or r.get("name") or "").strip()
        if not date_str or not DATE_RE.match(date_str) or not event:
            continue
        venue = (r.get("venue") or "").strip()
        tz_name = r.get("timezone") or timezone_fallback or default_timezone(date_str)
        meta = {"date": date_str, "event": event, "venue": venue, "timezone": tz_name,
                "start": r.get("start") or r.get("activation_time")}
        if r.get("activation"):
            activation = r["activation"]
            if re.fullmatch(r"\d{1,2}:?\d{2}", str(activation).strip()) or parse_time(activation):
                meta["start"] = activation
                activation = default_activation(meta)
            else:
                # allow date+time without offset: attach tz
                try:
                    _parse_iso(activation)
                except Exception:
                    try:
                        naive = datetime.fromisoformat(activation)
                        activation = _format_activation(naive.replace(tzinfo=ZoneInfo(tz_name)))
                    except Exception:
                        activation = default_activation(meta)
        else:
            activation = default_activation(meta)
        until = r.get("apply_until") or apply_until_of(activation)
        rel = f"{date_str[:4]}/{date_str}/{sanitize_event_name(event)}"
        out.append({
            "id": _entry_id(date_str, event),
            "event_path": rel,
            "date": date_str,
            "event": event,
            "venue": venue,
            "activation": activation,
            "apply_until": until,
            "state": "pending",
            "job_ids": [],
        })
    if not out:
        raise ValueError("No events could be read. Include date and event name on each row.")
    return out


def confirm_load_trip(root: Path, rows: list[dict], *, timezone_fallback: str | None = None) -> dict:
    st = nas_status(root)
    if not st["ok"]:
        return {"ok": False, "error": st["error"], "mounted": False}
    entries = load_trip_entries(rows, timezone_fallback=timezone_fallback)
    man = load_manifest(root)
    by_id = {e.get("id"): i for i, e in enumerate(man["entries"])}
    added, updated = 0, 0
    for e in entries:
        if e["id"] in by_id:
            old = man["entries"][by_id[e["id"]]]
            man["entries"][by_id[e["id"]]] = {**old, **e, "state": old.get("state") if old.get("state") in ("applied", "held") else e["state"]}
            updated += 1
        else:
            man["entries"].append(e)
            by_id[e["id"]] = len(man["entries"]) - 1
            added += 1
    root = Path(root)
    # create the root only on this confirmed action
    save_manifest(root, man)
    return {"ok": True, "added": added, "updated": updated, "entries": entries}


# ------------------------------------------------------------------ auto-queue lookup

def import_folder_date(folder: Path | str) -> str | None:
    name = Path(folder).name
    m = DATE_RE.match(name)
    return m.group(0) if m else None


def schedule_files_for_date(root: Path, date_str: str) -> list[Path]:
    day = Path(root) / date_str[:4] / date_str
    if not day.is_dir():
        return []
    out = []
    for evdir in sorted(p for p in day.iterdir() if p.is_dir()):
        p = evdir / "schedule.json"
        if p.is_file():
            out.append(p)
    return out


def lookup_for_date(root: Path, date_str: str, now: datetime | None = None,
                    photos_present: bool = True, write_manifest: bool = False) -> dict:
    """Schedules that should run for a Camera Imports date folder.

    Includes every schedule.json under that date, plus manifest entries whose
    activation has passed and which are not held/skipped/draft.
    A second folder on the same date gets the same list.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    st = nas_status(root)
    if not st["ok"]:
        return {"ok": False, "error": st["error"], "paths": [], "entries": []}
    photos = {date_str} if photos_present else set()
    tick = tick_manifest(root, now=now, dates_with_photos=photos, write=write_manifest and manifest_path(root).is_file())
    paths = list(schedule_files_for_date(root, date_str))
    applying = []
    for e in tick.get("entries") or []:
        if e.get("date") != date_str:
            continue
        state = e.get("state") or "pending"
        if state in ("held", "skipped", "draft"):
            continue
        try:
            act = _parse_iso(e["activation"])
        except Exception:
            continue
        if now < act and state != "applied":
            continue
        # include even if schedule.json is the same as one already found
        ev_path = Path(root) / e.get("event_path", "")
        sched = ev_path / "schedule.json"
        if sched.is_file() and sched not in paths:
            paths.append(sched)
        applying.append(e)
        if write_manifest and manifest_path(root).is_file() and photos_present and state in ("pending", "waiting"):
            try:
                set_manifest_state(root, e["id"], "applied")
            except Exception:
                pass
    if not photos_present:
        # activation passed already handled by tick (pending → waiting)
        pass
    return {
        "ok": True,
        "date": date_str,
        "paths": [str(p) for p in paths],
        "entries": applying,
        "waiting": [e for e in (tick.get("entries") or []) if e.get("date") == date_str and e.get("state") == "waiting"],
        "skipped": [e for e in (tick.get("entries") or []) if e.get("date") == date_str and e.get("state") == "skipped"],
    }


def lookup_for_import_folder(root: Path, folder: Path, now: datetime | None = None,
                             write_manifest: bool = False) -> dict:
    date_str = import_folder_date(folder)
    if not date_str:
        return {"ok": True, "date": None, "paths": [], "entries": [], "reason": "folder name is not a date"}
    return lookup_for_date(root, date_str, now=now, photos_present=True, write_manifest=write_manifest)


def merge_schedule_files(paths: list[Path | str]) -> dict:
    events, notes = [], []
    name_bits, tzs, buffers, sources = [], [], [], []
    for p in paths:
        data = _read_json(Path(p))
        if not data:
            continue
        events.extend(data.get("events") or [])
        if data.get("name"):
            name_bits.append(data["name"])
        if data.get("timezone"):
            tzs.append(data["timezone"])
        if data.get("changeover_buffer_min") is not None:
            buffers.append(int(data["changeover_buffer_min"]))
        if data.get("source"):
            sources.append(data["source"])
    if not events:
        raise ValueError("no events in the schedule files to merge")
    tz = tzs[0] if tzs else "UTC"
    if len(set(tzs)) > 1:
        notes.append("Merged events with different timezones; using " + tz)
    return {
        "name": " + ".join(name_bits) if name_bits else "Merged schedules",
        "source": "merged",
        "timezone": tz,
        "changeover_buffer_min": max(buffers) if buffers else 3,
        "notes": "; ".join(notes),
        "merged_from": [str(p) for p in paths],
        "events": events,
    }


def resolve_job_schedule_paths(state: dict) -> list[Path]:
    raw = []
    v = state.get("schedule_paths")
    if isinstance(v, (list, tuple)):
        raw.extend(v)
    elif v:
        raw.append(v)
    v = state.get("schedule_path")
    if isinstance(v, (list, tuple)):
        raw.extend(v)
    elif v:
        raw.append(v)
    out, seen = [], set()
    for item in raw:
        p = Path(item)
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            out.append(p)
    return out


def prepared_schedule_path(paths: list[Path | str], work_dir: Path | None = None) -> Path | None:
    files = [Path(p) for p in paths if p and Path(p).is_file()]
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    dest_dir = Path(work_dir or MERGED_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    merged = merge_schedule_files(files)
    dest = dest_dir / f"_merged-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    _atomic_write(dest, merged)
    return dest


def attach_for_import_folder(folder: Path, *, home: Path | None = None, now: datetime | None = None,
                             write_manifest: bool = False) -> dict:
    """Used by auto-queue when a new Camera Imports date folder is queued."""
    try:
        import nas_paths as NP
        if home is not None:
            NP.HOME = Path(home)
        root = NP.schedules_root(home)
    except Exception:
        return {"paths": []}
    found = lookup_for_import_folder(root, folder, now=now, write_manifest=write_manifest)
    paths = found.get("paths") or []
    out = {"paths": paths, "date": found.get("date"), "entries": found.get("entries") or []}
    if len(paths) == 1:
        out["schedule_path"] = paths[0]
    elif len(paths) > 1:
        out["schedule_path"] = paths[0]
        out["schedule_paths"] = paths
    return out
