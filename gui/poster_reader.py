#!/usr/bin/env python3
"""Read a schedule poster image into a local draft, then Confirm writes schedule.json.

Apple Vision OCR (already used by the catalog OCR pass) is the text source.
A deterministic parser turns the lines into acts, times, doors and notes.
Results stay in ~/localai/gui/poster-drafts until the user Confirms; nothing
is written into the Schedules tree on scan or read. Posters are never moved,
renamed or trashed. The poster never applies keywords.
"""
from __future__ import annotations

import hashlib, json, os, re, time
from datetime import datetime
from pathlib import Path

import schedules_tree as ST

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai"))
DRAFTS_DIR = HOME / "gui" / "poster-drafts"
THUMBS_DIR = HOME / "gui" / "poster-thumbs"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
SKIP_JSON = {".json"}
OCR_LANGS = ["ja-JP", "en-US"]
OCR_MIN_CONF = 0.3
CLASH_TOLERANCE_MIN = ST.CLASH_TOLERANCE_MIN

# statuses shown on the review page
REVIEW_STATUSES = ("pending_review", "multiple", "held", "error")
HIDDEN_STATUSES = ("accepted", "rejected")

DOORS_RE = re.compile(
    r"(開場|open(?:ing)?(?:\s*time)?|doors?)\b",
    re.I,
)
SHOW_START_RE = re.compile(r"(開演|show\s*start|start(?:s)?)\b", re.I)
VENUE_RE = re.compile(r"(会場|venue)\b[:：]?\s*(.+)", re.I)
SKIP_LINE_RE = re.compile(
    r"^(time\s*table|timetable|タイムテーブル|セットリスト|set\s*list|"
    r"schedule|スケジュール|出演者|出演|acts?|line[- ]?up|出演アーティスト)$",
    re.I,
)
DATE_LINE_RE = re.compile(
    r"^\s*(?:\d{4}[.\/年\-]\s*\d{1,2}[.\/月\-]\s*\d{1,2}日?|"
    r"\d{1,2}[.\/]\s*\d{1,2}(?:[.\/]\s*\d{2,4})?)\s*$",
)
# time token after fullwidth normalisation
TIME_TOKEN = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
TIME_JP = re.compile(r"(?<!\d)([01]?\d|2[0-3])時(?:([0-5]\d)分?)?")
RANGE_SEP = r"[-~〜～–—－−]"

_FW_DIGITS = str.maketrans("０１２３４５６７８９：．，", "0123456789:.,")


def drafts_dir(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / "gui" / "poster-drafts"
    return DRAFTS_DIR


def thumbs_dir(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / "gui" / "poster-thumbs"
    return THUMBS_DIR


def is_poster_image(path: Path) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")


def event_images(evdir: Path) -> list[Path]:
    evdir = Path(evdir)
    if not evdir.is_dir():
        return []
    out = []
    try:
        kids = list(evdir.iterdir())
    except Exception:
        return []
    for p in sorted(kids, key=lambda x: x.name.lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in SKIP_JSON:
            continue
        if is_poster_image(p):
            out.append(p)
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def draft_filename(date_str: str, event_name: str) -> str:
    return ST.draft_key(date_str, event_name)


def draft_path(date_str: str, event_name: str, *, drafts: Path | None = None) -> Path:
    return (drafts or DRAFTS_DIR) / draft_filename(date_str, event_name)


def load_draft(date_str: str, event_name: str, *, drafts: Path | None = None) -> dict | None:
    p = draft_path(date_str, event_name, drafts=drafts)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_draft(data: dict, *, drafts: Path | None = None) -> Path:
    d = drafts or DRAFTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = draft_path(data["date"], data["event"], drafts=d)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def folder_date_and_name(evdir: Path, root: Path) -> tuple[str, str, str]:
    """Return (date, event_name, layout) from a folder path."""
    evdir = Path(evdir)
    name = evdir.name
    parent = evdir.parent.name
    if ST.DATE_RE.match(parent):
        date_str = parent
        layout = "flat" if evdir.parent.parent.resolve() == Path(root).resolve() else "canonical"
        return date_str, name, layout
    return "", name, "unknown"


def discover_event_folders(root: Path) -> list[dict]:
    """Walk canonical and flat layouts. Never creates folders."""
    root = Path(root)
    st = ST.nas_status(root)
    if not st["ok"] or not root.is_dir():
        return []
    out = []
    for evdir in ST.iter_event_dirs(root):
        date_str, name, layout = folder_date_and_name(evdir, root)
        if not date_str:
            continue
        images = event_images(evdir)
        out.append({
            "date": date_str,
            "event": name,
            "folder": evdir,
            "layout": layout,
            "images": images,
        })
    return out


# ------------------------------------------------------------------ OCR

def ocr_image(path: Path, langs: list[str] | None = None) -> list[dict]:
    """Apple Vision text recognition with bounding boxes (top-left origin)."""
    import Vision
    from Foundation import NSURL

    langs = langs or list(OCR_LANGS)
    url = NSURL.fileURLWithPath_(str(Path(path)))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(langs)
    req.setUsesLanguageCorrection_(True)
    try:
        req.setAutomaticallyDetectsLanguage_(True)
    except Exception:
        pass
    handler.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        conf = float(cand[0].confidence())
        if conf < OCR_MIN_CONF:
            continue
        b = obs.boundingBox()
        out.append({
            "text": str(cand[0].string()),
            "conf": round(conf, 3),
            "x": float(b.origin.x),
            "y": float(1 - (b.origin.y + b.size.height)),
            "w": float(b.size.width),
            "h": float(b.size.height),
        })
    out.sort(key=lambda r: (round(r["y"], 3), r["x"]))
    return out


# Cyrillic / Greek letters that OCR often substitutes in Latin titles
_LOOKALIKES = str.maketrans({
    "Н": "H", "н": "h", "А": "A", "а": "a", "В": "B", "Е": "E", "е": "e",
    "К": "K", "М": "M", "О": "O", "о": "o", "Р": "P", "р": "p", "С": "C", "с": "c",
    "Т": "T", "Х": "X", "у": "y", "І": "I", "і": "i",
})


def normalize_text(s: str) -> str:
    s = (s or "").translate(_FW_DIGITS)
    s = s.replace("〜", "-").replace("～", "-").replace("–", "-").replace("—", "-")
    s = s.replace("－", "-").replace("−", "-")
    s = re.sub(r"[ \t\u3000]+", " ", s).strip()
    return s



def _names_match(a: str, b: str) -> bool:
    def fold(s):
        s = normalize_text(s).translate(_LOOKALIKES).lower()
        return re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", "", s)
    fa, fb = fold(a or ""), fold(b or "")
    return bool(fa and fb and (fa == fb or fa in fb or fb in fa))


def _time_from_groups(hh, mm) -> str | None:
    return ST.parse_time(f"{int(hh):02d}:{int(mm):02d}")


def extract_times(text: str) -> list[tuple[str, str | None, str]]:
    """Return list of (start, end_or_None, remainder_without_this_range).

    Scans for 'HH:MM-HH:MM' first, then lone 'HH:MM' or 'HH時MM分'.
    Remainder is the original text with all time tokens removed (once).
    """
    s = normalize_text(text)
    if not s:
        return []
    # Japanese 17時30分 / 17時 → 17:30 / 17:00
    def _jp_sub(m):
        hh, mm = m.group(1), m.group(2) or "00"
        return f"{int(hh):02d}:{int(mm):02d}"
    s = TIME_JP.sub(_jp_sub, s)

    ranges = []
    range_re = re.compile(
        rf"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)\s*{RANGE_SEP}\s*([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)"
    )
    used = []
    for m in range_re.finditer(s):
        st = _time_from_groups(m.group(1), m.group(2))
        en = _time_from_groups(m.group(3), m.group(4))
        if st and en:
            ranges.append((st, en, m.start(), m.end()))
            used.append(m)
    singles = []
    if not ranges:
        for m in TIME_TOKEN.finditer(s):
            st = _time_from_groups(m.group(1), m.group(2))
            if st:
                singles.append((st, None, m.start(), m.end()))
    items = ranges or singles
    if not items:
        return []
    # remainder: strip all matched tokens
    leftover = s
    for _st, _en, a, b in sorted(items, key=lambda x: -x[2]):
        leftover = leftover[:a] + " " + leftover[b:]
    leftover = re.sub(r"[\s/|,;:：·・\-]+", " ", leftover).strip(" /|,;:：·・-")
    leftover = re.sub(r"\s+", " ", leftover).strip()
    return [(st, en, leftover) for st, en, *_ in items]


def _is_skip_line(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return True
    if SKIP_LINE_RE.match(t):
        return True
    if DATE_LINE_RE.match(t):
        return True
    return False


def parse_ocr_lines(lines, *, date: str | None = None, event: str | None = None,
                    timezone: str | None = None, venue: str | None = None) -> dict:
    """Parse OCR lines (strings or {text,...} dicts) into editor rows and meta."""
    raw = []
    for item in lines or []:
        if isinstance(item, dict):
            raw.append(item)
        else:
            raw.append({"text": str(item), "conf": 1.0, "y": len(raw) * 0.05, "x": 0})

    date_str = (date or "").strip()
    event_name = (event or "").strip()
    venue_name = (venue or "").strip()
    tz = timezone or (ST.default_timezone(date_str) if date_str else "Europe/London")
    doors = None
    notes_bits = []
    acts: list[dict] = []

    pending_time = None  # (start, end) waiting for an act name on a nearby line
    pending_name = None

    def add_act(name, start, end, notes=""):
        name = (name or "").strip()
        if not name:
            return
        # don't treat doors/show-start labels as act names
        if DOORS_RE.search(name) or SHOW_START_RE.fullmatch(name.strip()):
            return
        if SKIP_LINE_RE.match(name) or DATE_LINE_RE.match(name):
            return
        if len(name) > 80:
            name = name[:80].strip()
        acts.append({
            "act": name,
            "start": start or "",
            "end": end or "",
            "doors": "",
            "notes": notes,
        })

    for rec in raw:
        text = normalize_text(rec.get("text") or "")
        if not text:
            continue
        if _is_skip_line(text):
            continue

        vm = VENUE_RE.search(text)
        if vm and not venue_name:
            venue_name = vm.group(2).strip()
            # if the line is only a venue label, skip further
            rest_v = VENUE_RE.sub("", text).strip()
            if not rest_v:
                continue
            text = rest_v

        times = extract_times(text)
        is_doors = bool(DOORS_RE.search(text))
        is_show = bool(SHOW_START_RE.search(text)) and not is_doors

        if is_doors and times:
            doors = doors or times[0][0]
            # leftover after removing doors label + time may be a venue
            leftover = times[0][2]
            leftover = DOORS_RE.sub("", leftover).strip(" :：-")
            if leftover and not venue_name and len(leftover) > 1:
                # ignore bare words like OPEN
                if not re.fullmatch(r"(open|doors?|opening|開場)", leftover, re.I):
                    pass
            continue

        if is_show and times:
            leftover_show = SHOW_START_RE.sub("", times[0][2] or "").strip(" :：-")
            if not leftover_show:
                # "開演 18:00" with no act name — not an act
                notes_bits.append(f"Show start {times[0][0]}")
                continue

        if times:
            # one or more timed acts on this line
            leftover = times[0][2]
            leftover = DOORS_RE.sub("", leftover)
            leftover = SHOW_START_RE.sub("", leftover)
            leftover = leftover.strip(" :：-/")
            if len(times) == 1:
                st, en, _ = times[0]
                if leftover:
                    if event_name and _names_match(leftover, event_name):
                        pending_time = (st, en)
                    else:
                        add_act(leftover, st, en)
                        pending_time = None
                        pending_name = None
                else:
                    # time only — attach to a pending name, else remember
                    if pending_name and not (event_name and _names_match(pending_name, event_name)):
                        add_act(pending_name, st, en)
                        pending_name = None
                    else:
                        pending_time = (st, en)
                        pending_name = None
            else:
                # multiple ranges; if one leftover name, only first gets it (unusual)
                for i, (st, en, bit) in enumerate(times):
                    name = bit or leftover if i == 0 else bit
                    if name:
                        add_act(name, st, en)
                    else:
                        pending_time = (st, en)
            continue

        # no times on this line
        if pending_time:
            add_act(text, pending_time[0], pending_time[1])
            pending_time = None
            continue
        # event / venue titles are not acts
        if event_name and _names_match(text, event_name):
            continue
        if venue_name and _names_match(text, venue_name):
            continue
        # a bare name may belong to the next time line
        if not DOORS_RE.search(text) and not SHOW_START_RE.search(text) and not VENUE_RE.search(text):
            if pending_name and not acts:
                # two names in a row; the first might be the event title
                if not event_name:
                    event_name = pending_name
                pending_name = text
            elif pending_name:
                # previous pending name never got a time — keep as event or skip
                if not event_name:
                    event_name = pending_name
                pending_name = text
            else:
                pending_name = text

    if pending_name and not event_name:
        event_name = pending_name

    # infer missing ends from the next start (common on gig posters)
    timed = [r for r in acts if r.get("start")]
    for i, row in enumerate(timed):
        if row.get("end"):
            continue
        if i + 1 < len(timed) and timed[i + 1].get("start"):
            nxt = timed[i + 1]["start"]
            if nxt != row["start"]:
                row["end"] = nxt
                note = (row.get("notes") or "").strip()
                infer = "end inferred from next start"
                row["notes"] = f"{note}; {infer}" if note else infer

    if doors and acts:
        acts[0]["doors"] = doors

    return {
        "meta": {
            "date": date_str,
            "event": event_name,
            "venue": venue_name,
            "timezone": tz,
            "changeover_buffer_min": 3,
            "event_type": "gig",
            "source": "poster",
            "doors": doors or "",
        },
        "rows": acts,
        "doors": doors or "",
        "notes": notes_bits,
    }


# ------------------------------------------------------------------ comparison

def _slots_from_schedule(data: dict) -> list[tuple[str, str, str]]:
    out = []
    events = data.get("events") or ([data] if data.get("event") else [])
    for ev in events:
        for sl in ev.get("slots") or []:
            st, en = ST.parse_time(sl.get("start")), ST.parse_time(sl.get("end"))
            if not st or not en:
                continue
            for a in ST._acts_of(sl):
                if a:
                    out.append(((a or "").strip(), st, en))
    return out


def _slots_from_rows(rows: list[dict]) -> list[tuple[str, str, str]]:
    out = []
    for r in rows or []:
        st, en = ST.parse_time(r.get("start")), ST.parse_time(r.get("end"))
        act = (r.get("act") or "").strip()
        if act and st and en:
            out.append((act, st, en))
    return out


def _time_close(a: str, b: str) -> bool:
    da = abs(ST._minutes(a) - ST._minutes(b))
    da = min(da, 24 * 60 - da)
    return da <= CLASH_TOLERANCE_MIN


def compare_with_confirmed(rows: list[dict], confirmed: dict | None) -> dict:
    """Flag matches, gaps the poster fills, and disagreements.

    can_accept is True when there is no confirmed.json, or when every
    confirmed act is matched and any extra poster acts only fill gaps.
    Disagreements (time clashes, confirmed acts missing from the poster)
    block Accept. Order-only differences are a warning and do not block.
    """
    poster = _slots_from_rows(rows)
    if not confirmed:
        return {
            "has_confirmed": False,
            "matches": [{"act": a, "start": st, "end": en} for a, st, en in poster],
            "gaps": [],
            "disagreements": [],
            "order_warning": False,
            "can_accept": True,
        }
    confirmed_slots = _slots_from_schedule(confirmed)
    used = set()
    matches, disagreements = [], []
    for a, st, en in poster:
        found = None
        name_hits = []
        for j, (ca, cst, cen) in enumerate(confirmed_slots):
            if j in used:
                continue
            if a.lower() != ca.lower():
                continue
            name_hits.append(j)
            if _time_close(st, cst) and _time_close(en, cen):
                found = j
                break
        if found is not None:
            used.add(found)
            matches.append({"act": a, "start": st, "end": en})
        elif name_hits:
            j = name_hits[0]
            used.add(j)
            ca, cst, cen = confirmed_slots[j]
            disagreements.append({
                "act": a,
                "poster": {"start": st, "end": en},
                "confirmed": {"start": cst, "end": cen},
                "reason": f"{a} is {st}–{en} on the poster and {cst}–{cen} in confirmed.json.",
            })
        else:
            # unmatched poster act — a gap the poster fills, recorded below
            pass

    gaps = []
    for i, (a, st, en) in enumerate(poster):
        if any(m["act"].lower() == a.lower() and _time_close(m["start"], st) and _time_close(m["end"], en)
               for m in matches):
            continue
        if any(d["act"].lower() == a.lower() and d["poster"]["start"] == st and d["poster"]["end"] == en
               for d in disagreements):
            continue
        gaps.append({"act": a, "start": st, "end": en, "side": "poster"})

    for j, (ca, cst, cen) in enumerate(confirmed_slots):
        if j not in used:
            disagreements.append({
                "act": ca,
                "poster": None,
                "confirmed": {"start": cst, "end": cen},
                "reason": f"Confirmed act {ca} {cst}–{cen} is missing from the poster.",
            })

    bnames = [a for a, *_ in poster if any(m["act"].lower() == a.lower() for m in matches)]
    cnames = [a for a, *_ in confirmed_slots if any(m["act"].lower() == a.lower() for m in matches)]
    order_warning = bnames != cnames and sorted(x.lower() for x in bnames) == sorted(x.lower() for x in cnames)

    return {
        "has_confirmed": True,
        "matches": matches,
        "gaps": gaps,
        "disagreements": disagreements,
        "order_warning": order_warning,
        "can_accept": not disagreements,
    }


# ------------------------------------------------------------------ scan / read

def _blank_item(info: dict, status: str, **extra) -> dict:
    images = info.get("images") or []
    rec = {
        "date": info["date"],
        "event": info["event"],
        "venue": "",
        "timezone": ST.default_timezone(info["date"]),
        "folder": str(info["folder"]),
        "layout": info.get("layout") or "canonical",
        "image": None,
        "images": [p.name for p in images],
        "image_count": len(images),
        "status": status,
        "ocr": None,
        "rows": [],
        "meta": {
            "date": info["date"],
            "event": info["event"],
            "venue": "",
            "timezone": ST.default_timezone(info["date"]),
            "changeover_buffer_min": 3,
            "event_type": "gig",
            "source": "poster",
        },
        "comparison": None,
        "read_at": None,
        "error": None,
    }
    rec.update(extra)
    return rec


def _load_confirmed_for(root: Path, date_str: str, event_name: str, evdir: Path | None = None) -> dict | None:
    folder = evdir or ST.find_event_dir(root, date_str, event_name)
    p = Path(folder) / "confirmed.json"
    return ST._read_json(p) if p.is_file() else None


def read_image(path: Path, *, date: str, event: str, venue: str = "",
               timezone: str | None = None, ocr_fn=None, confirmed: dict | None = None) -> dict:
    """OCR + parse one image. Does not write anywhere."""
    ocr_fn = ocr_fn or ocr_image
    lines = ocr_fn(Path(path)) or []
    tz = timezone or ST.default_timezone(date)
    parsed = parse_ocr_lines(lines, date=date, event=event, timezone=tz, venue=venue)
    meta = parsed["meta"]
    # folder name wins for the event title (the poster may also name the night)
    meta["event"] = event or meta.get("event") or ""
    meta["date"] = date
    meta["timezone"] = tz
    if venue and not meta.get("venue"):
        meta["venue"] = venue
    rows = parsed["rows"]
    comparison = compare_with_confirmed(rows, confirmed)
    text = "\n".join(
        (ln.get("text") if isinstance(ln, dict) else str(ln)) for ln in lines
    )
    return {
        "ocr": {
            "engine": "apple-vision",
            "langs": list(OCR_LANGS),
            "lines": lines,
            "text": text,
        },
        "rows": rows,
        "meta": meta,
        "comparison": comparison,
        "doors": parsed.get("doors") or "",
        "notes": parsed.get("notes") or [],
    }


def _should_reread(existing: dict | None, sha: str, force: bool) -> bool:
    if force:
        return True
    if not existing:
        return True
    img = existing.get("image") or {}
    if img.get("sha256") != sha:
        return True
    # same hash: skip even if status is pending (already read)
    return False


def scan_tree(root: Path, *, drafts: Path | None = None, ocr_fn=None,
              read: bool = True, force: bool = False, now: datetime | None = None) -> dict:
    """Discover posters and auto-read folders that hold exactly one image.

    Never writes into `root`. Drafts go to the local drafts directory only.
    Does not start jobs. Zero images are ignored. Several images are listed
    as 'multiple images, pick one' without reading.
    """
    drafts = drafts or DRAFTS_DIR
    root = Path(root)
    st = ST.nas_status(root)
    items = []
    read_count = skipped_hash = 0
    if not st["ok"]:
        return {"ok": False, "error": st.get("error"), "mounted": False,
                "items": [], "read": 0, "skipped_hash": 0}
    if not root.is_dir():
        return {"ok": True, "mounted": True, "exists": False,
                "items": [], "read": 0, "skipped_hash": 0}

    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for info in discover_event_folders(root):
        images = info["images"]
        existing = load_draft(info["date"], info["event"], drafts=drafts)
        if len(images) == 0:
            # keep a held/pending draft visible if the image vanished? drop from scan list
            continue
        if len(images) > 1:
            rec = existing if existing and existing.get("status") in ("held", "rejected", "accepted") and existing.get("image_count") == len(images) else None
            if rec is None:
                rec = _blank_item(info, "multiple",
                                  error="multiple images, pick one")
                rec["read_at"] = stamp
                save_draft(rec, drafts=drafts)
            items.append(rec)
            continue

        img = images[0]
        try:
            sha = file_sha256(img)
        except Exception as e:
            rec = _blank_item(info, "error", error=f"Could not hash the image: {e}")
            rec["image"] = {"name": img.name, "sha256": "", "path": str(img)}
            save_draft(rec, drafts=drafts)
            items.append(rec)
            continue

        if not _should_reread(existing, sha, force) or not read:
            if existing:
                # refresh image list / folder but keep the previous read
                existing["images"] = [img.name]
                existing["image_count"] = 1
                existing["folder"] = str(info["folder"])
                items.append(existing)
                if existing.get("image", {}).get("sha256") == sha:
                    skipped_hash += 1
                continue
            if not read:
                # listing only: do not invent a blank draft or read the image
                continue

        rec = _blank_item(info, "pending_review")
        rec["image"] = {"name": img.name, "sha256": sha, "path": str(img)}
        if read:
            try:
                confirmed = _load_confirmed_for(root, info["date"], info["event"], info["folder"])
                got = read_image(img, date=info["date"], event=info["event"],
                                 timezone=ST.default_timezone(info["date"]),
                                 ocr_fn=ocr_fn, confirmed=confirmed)
                rec["ocr"] = got["ocr"]
                rec["rows"] = got["rows"]
                rec["meta"] = got["meta"]
                rec["comparison"] = got["comparison"]
                rec["venue"] = got["meta"].get("venue") or ""
                rec["read_at"] = stamp
                rec["status"] = "pending_review"
                rec["error"] = None
                read_count += 1
            except Exception as e:
                rec["status"] = "error"
                rec["error"] = f"Could not read the poster: {e}"
                rec["read_at"] = stamp
        save_draft(rec, drafts=drafts)
        items.append(rec)

    review = [i for i in items if i.get("status") in REVIEW_STATUSES]
    return {
        "ok": True,
        "mounted": True,
        "exists": True,
        "items": items,
        "review": review,
        "review_count": len(review),
        "read": read_count,
        "skipped_hash": skipped_hash,
    }


def list_review(root: Path, *, drafts: Path | None = None, scan: bool = False,
                ocr_fn=None) -> dict:
    drafts = drafts or DRAFTS_DIR
    if scan:
        return scan_tree(root, drafts=drafts, ocr_fn=ocr_fn, read=True)
    # merge current folders with saved drafts (no OCR)
    found = scan_tree(root, drafts=drafts, ocr_fn=ocr_fn, read=False)
    return found


def reread(root: Path, date_str: str, event_name: str, *, filename: str | None = None,
           drafts: Path | None = None, ocr_fn=None) -> dict:
    """Force a new read of one image. Still writes only a local draft."""
    drafts = drafts or DRAFTS_DIR
    evdir = ST.find_event_dir(root, date_str, event_name)
    if not evdir.is_dir():
        return {"ok": False, "error": "Event folder not found."}
    images = event_images(evdir)
    chosen = None
    if filename:
        candidate = evdir / Path(filename).name
        if is_poster_image(candidate):
            chosen = candidate
        else:
            return {"ok": False, "error": "That file is not an image in this event folder."}
    elif len(images) == 1:
        chosen = images[0]
    elif len(images) == 0:
        return {"ok": False, "error": "No image in this event folder."}
    else:
        return {"ok": False, "error": "multiple images, pick one",
                "images": [p.name for p in images]}

    date_str2, name, layout = folder_date_and_name(evdir, root)
    date_str, event_name = date_str2 or date_str, name or event_name
    info = {"date": date_str, "event": event_name, "folder": evdir,
            "layout": layout, "images": images}
    sha = file_sha256(chosen)
    confirmed = _load_confirmed_for(root, date_str, event_name, evdir)
    rec = _blank_item(info, "pending_review")
    rec["image"] = {"name": chosen.name, "sha256": sha, "path": str(chosen)}
    rec["images"] = [p.name for p in images]
    rec["image_count"] = len(images)
    try:
        got = read_image(chosen, date=date_str, event=event_name,
                         timezone=ST.default_timezone(date_str),
                         ocr_fn=ocr_fn, confirmed=confirmed)
        rec["ocr"] = got["ocr"]
        rec["rows"] = got["rows"]
        rec["meta"] = got["meta"]
        rec["comparison"] = got["comparison"]
        rec["venue"] = got["meta"].get("venue") or ""
        rec["read_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        rec["status"] = "pending_review"
        rec["error"] = None
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = f"Could not read the poster: {e}"
    save_draft(rec, drafts=drafts)
    rec["ok"] = rec["status"] != "error"
    if rec["status"] == "error":
        rec["ok"] = False
    else:
        rec["ok"] = True
    return rec


def set_status(date_str: str, event_name: str, status: str, *,
               drafts: Path | None = None, rows: list | None = None) -> dict:
    rec = load_draft(date_str, event_name, drafts=drafts)
    if not rec:
        return {"ok": False, "error": "No poster draft for this event."}
    if status not in ("held", "rejected", "pending_review", "accepted"):
        return {"ok": False, "error": f"Unknown status {status!r}."}
    rec["status"] = status
    if rows is not None:
        rec["rows"] = rows
    save_draft(rec, drafts=drafts)
    return {"ok": True, "item": rec}


def preview_accept(root: Path, date_str: str, event_name: str, rows: list[dict] | None = None,
                   *, drafts: Path | None = None, meta_update: dict | None = None) -> dict:
    """Validation + comparison. No writes."""
    rec = load_draft(date_str, event_name, drafts=drafts) or {}
    use_rows = rows if rows is not None else rec.get("rows") or []
    meta = dict(rec.get("meta") or {})
    meta.setdefault("date", date_str)
    meta.setdefault("event", event_name)
    meta.setdefault("source", "poster")
    if meta_update:
        meta.update({k: v for k, v in meta_update.items() if v is not None})
    if not meta.get("timezone"):
        meta["timezone"] = ST.default_timezone(date_str)
    evdir = ST.find_event_dir(root, date_str, event_name)
    confirmed = _load_confirmed_for(root, date_str, event_name, evdir)
    comparison = compare_with_confirmed(use_rows, confirmed)
    val = ST.validate_event({**meta, "schedules_root": str(root)}, use_rows, confirmed)
    can_accept = bool(comparison.get("can_accept")) and bool(val.get("can_save"))
    return {
        "ok": True,
        "meta": meta,
        "rows": use_rows,
        "validation": val,
        "comparison": comparison,
        "can_accept": can_accept,
        "folder": str(evdir),
        "has_confirmed": bool(confirmed),
    }


def accept(root: Path, date_str: str, event_name: str, *,
           rows: list[dict] | None = None, confirm: bool = False,
           ack_warnings: bool = False, meta_update: dict | None = None,
           drafts: Path | None = None, jobs_dir: Path | None = None,
           history_jobs: Path | None = None) -> dict:
    """Build schedule.json (and poster.json) only through confirm_save, after Confirm."""
    preview = preview_accept(root, date_str, event_name, rows,
                             drafts=drafts, meta_update=meta_update)
    if not confirm:
        return {**preview, "ok": False, "written": False,
                "error": "Nothing was written. Send confirm=true after reviewing."}
    if not preview["comparison"].get("can_accept"):
        return {
            "ok": False,
            "error": "The poster disagrees with confirmed.json, which is the master. "
                     "Edit the rows to match or to fill gaps, or Hold this poster. Nothing was written.",
            "comparison": preview["comparison"],
            "validation": preview["validation"],
            "written": False,
        }
    if not preview["validation"].get("can_save"):
        return {"ok": False, "error": "Fix the red errors before saving. Nothing was written.",
                "validation": preview["validation"], "written": False}
    meta = preview["meta"]
    meta["source"] = "poster"
    result = ST.confirm_save(
        root, meta, preview["rows"],
        ack_warnings=ack_warnings,
        drafts_dir=(Path(drafts).parent / "schedule-drafts") if drafts else None,
        jobs_dir=jobs_dir,
        history_jobs=history_jobs,
    )
    if not result.get("ok"):
        return {**result, "written": False, "comparison": preview["comparison"]}

    # Write poster.json beside schedule.json only now, after Confirm.
    evdir = Path(result.get("folder") or ST.find_event_dir(root, date_str, event_name))
    rec = load_draft(date_str, event_name, drafts=drafts) or {}
    poster_record = {
        "source": "poster",
        "source_label": "read from a poster",
        "engine": ((rec.get("ocr") or {}).get("engine") or "apple-vision"),
        "image": rec.get("image") or {},
        "read_at": rec.get("read_at"),
        "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ocr_text": ((rec.get("ocr") or {}).get("text") or ""),
        "rows": preview["rows"],
        "timezone": meta.get("timezone"),
        "comparison": preview["comparison"],
    }
    # store only the file name for the image, not a machine-local path, if we can
    img = dict(poster_record["image"] or {})
    if "path" in img:
        img["name"] = img.get("name") or Path(img["path"]).name
        img.pop("path", None)
    poster_record["image"] = img
    ST._atomic_write(evdir / "poster.json", poster_record)

    rec["status"] = "accepted"
    rec["rows"] = preview["rows"]
    rec["meta"] = meta
    rec["comparison"] = preview["comparison"]
    rec["accepted_at"] = poster_record["confirmed_at"]
    if drafts or rec:
        try:
            save_draft(rec, drafts=drafts)
        except Exception:
            pass
    result["poster_json"] = str(evdir / "poster.json")
    result["comparison"] = preview["comparison"]
    result["written"] = True
    return result


# ------------------------------------------------------------------ thumbnails

def resolve_image(root: Path, date_str: str, event_name: str, filename: str | None = None) -> Path | None:
    root = Path(root).resolve()
    evdir = ST.find_event_dir(root, date_str, event_name)
    if not evdir.is_dir():
        return None
    try:
        ev_res = evdir.resolve()
        ev_res.relative_to(root)
    except ValueError:
        return None
    images = event_images(evdir)
    if filename:
        p = (evdir / Path(filename).name).resolve()
        try:
            p.relative_to(ev_res)
        except ValueError:
            return None
        return p if is_poster_image(p) else None
    if len(images) == 1:
        return images[0]
    rec = load_draft(date_str, event_name)
    name = ((rec or {}).get("image") or {}).get("name")
    if name:
        p = (evdir / Path(name).name).resolve()
        try:
            p.relative_to(ev_res)
        except ValueError:
            return None
        if is_poster_image(p):
            return p
    return None


def make_thumbnail(src: Path, dest: Path, max_px: int = 640) -> Path | None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    try:
        from PIL import Image
        im = Image.open(src)
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        im.thumbnail((max_px, max_px))
        im.save(dest, "JPEG", quality=85)
        return dest
    except Exception:
        pass
    # macOS sips handles HEIC
    try:
        import subprocess
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", str(max_px), str(src), "--out", str(dest)],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and dest.is_file():
            return dest
    except Exception:
        pass
    return None


def thumbnail_bytes(root: Path, date_str: str, event_name: str, filename: str | None = None,
                    *, home: Path | None = None) -> tuple[bytes, str] | None:
    src = resolve_image(root, date_str, event_name, filename)
    if not src:
        return None
    sha = file_sha256(src)
    dest = thumbs_dir(home) / f"{sha[:16]}.jpg"
    if not dest.is_file():
        if not make_thumbnail(src, dest):
            # last resort: if it's already jpeg, return the original (could be large)
            if src.suffix.lower() in {".jpg", ".jpeg"}:
                return src.read_bytes(), "image/jpeg"
            return None
    return dest.read_bytes(), "image/jpeg"


def start_scan_thread(get_root, get_home, tick: float = 90.0):
    """Periodic light scan inside the GUI process. Never starts photo jobs."""
    import threading

    def loop():
        # delay the first pass so startup is quiet
        time.sleep(min(tick, 15))
        while True:
            try:
                home = Path(get_home())
                root = Path(get_root())
                # re-bind module homes so drafts land under the live LOCALAI_HOME
                global HOME, DRAFTS_DIR, THUMBS_DIR
                HOME = home
                DRAFTS_DIR = home / "gui" / "poster-drafts"
                THUMBS_DIR = home / "gui" / "poster-thumbs"
                ST.HOME = home
                st = ST.nas_status(root)
                if st.get("ok") and root.is_dir():
                    scan_tree(root, drafts=DRAFTS_DIR, read=True)
            except Exception:
                pass
            time.sleep(tick)

    t = threading.Thread(target=loop, name="localai-poster-scan", daemon=True)
    t.start()
    return t
