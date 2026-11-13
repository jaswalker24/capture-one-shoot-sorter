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
WEEKDAY_PART = r"[（(]?(?:月|火|水|木|金|土|日|mon|tue|wed|thu|fri|sat|sun|[a-z])[）)]?"
DATE_TOKEN_RE = re.compile(
    r"(?:"
    r"\d{4}[.\/年\-]\s*(?:0?[1-9]|1[0-2])[.\/月]\s*(?:0?[1-9]|[12]\d|3[01])日?"
    r"|(?:0?[1-9]|1[0-2])[\/月]\s*(?:0?[1-9]|[12]\d|3[01])日?"
    r"|(?:0?[1-9]|1[0-2])\.(?:0?[1-9]|[12]\d|3[01])日?(?=\s*" + WEEKDAY_PART + r")"
    r")"
    r"(?:\s*" + WEEKDAY_PART + r")?",
    re.I,
)
DATE_LINE_RE = re.compile(
    r"^\s*(?:(?:\d{4}[.\/年\-]\s*)?\d{1,2}[.\/月\-]\s*\d{1,2}日?)"
    r"(?:\s*" + WEEKDAY_PART + r")?\s*$",
    re.I,
)
# time token after fullwidth normalisation and date masking
TIME_TOKEN = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
TIME_TOKEN_DOT = re.compile(r"(?<!\d)([01]?\d|2[0-3])\.([0-5]\d)(?!\d)")
TIME_JP = re.compile(r"(?<!\d)([01]?\d|2[0-3])時(?:([0-5]\d)分?)?")
RANGE_SEP = r"[-~〜～–—－−]"
SALE_RE = re.compile(
    r"(一般発売|先行発売|先行抽選|先行(?:受付|販売|発売)|抽選|"
    r"受付開始|チケット|前売|当日券|当日販売|発売|販売|物販|通販|"
    r"on\s*sale|pre[-\s]?sale|tickets?(?:\s+on)?\s*sale|"
    r"lottery|box\s*office)",
    re.I,
)
# Broader admission/ticket words used when the line is not a leading set-time row
SALE_SOFT_RE = re.compile(r"(受付|予約|当日|先行|sale|ticket|presale|lottery)", re.I)
PRICE_RE = re.compile(
    r"(料金|入場料|チケット代|\d+\s*円|¥\s*\d|yen\b|price\s*:|"
    r"(?:adv|door)\s*[·•・,.]?\s*\d|[+＋]?\s*idr?[i1]nk|[+＋]?\s*1?\s*drink\b)",
    re.I,
)
PARALLEL_RE = re.compile(
    r"[（(]\s*(?:並行[A-Da-d0-9０-９]*|終演後|物販|cheki|チェキ)[^）)]*[）)]"
)
MERCH_LINE_RE = re.compile(
    r"(終演後\s*物販|終演後物販|中間物販|並行物販|物販のみ|"
    r"after[-\s]?show\s*(?:sale|merch)|merch(?:andise)?\s*sale)",
    re.I,
)
ADJUST_RE = re.compile(r"^調整時間$")
CHANGEOVER_RE = re.compile(r"^(調整時間|転換リハ|転換|changeover(?:\s*reh(?:earsal)?)?)$", re.I)
DOORS_TIME_RE = re.compile(
    r"(?:開場|open(?:ing)?|doors?)\s*[/.:：]?\s*(\d{1,2}:\d{2})"
    r"|(\d{1,2}:\d{2})\s*(?:開場|open(?:ing)?|doors?)",
    re.I,
)
START_TIME_RE = re.compile(
    r"(?:開演|start(?:s)?)\s*[/.:：]?\s*(\d{1,2}:\d{2})"
    r"|(\d{1,2}:\d{2})\s*(?:開演|start(?:s)?)",
    re.I,
)

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


def _parse_date_token(tok: str, event_year: int | None = None):
    """Return a date or None from a token like 2026.11.22, 11/1, 11月1日."""
    from datetime import date as _date
    t = normalize_text(tok)
    t = re.sub(WEEKDAY_PART, "", t, flags=re.I).strip()
    t = t.replace("年", "-").replace("月", "-").replace("日", "")
    t = t.replace(".", "-").replace("/", "-")
    parts = [p for p in t.split("-") if p]
    if len(parts) == 3:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2 and event_year:
        y, m, d = int(event_year), int(parts[0]), int(parts[1])
    else:
        return None
    try:
        return _date(y, m, d)
    except ValueError:
        return None


def line_dates(text: str, event_date: str | None = None):
    from datetime import date as _date
    ev = None
    ev_year = None
    if event_date:
        try:
            ev = _date.fromisoformat(event_date[:10])
            ev_year = ev.year
        except ValueError:
            ev = None
    found = []
    for m in DATE_TOKEN_RE.finditer(normalize_text(text)):
        d = _parse_date_token(m.group(0), ev_year)
        if d:
            found.append(d)
    return found, ev


def has_foreign_date(text: str, event_date: str | None = None) -> bool:
    found, ev = line_dates(text, event_date)
    if not found or ev is None:
        return False
    return any(d != ev for d in found)


def is_date_only_line(text: str) -> bool:
    return bool(DATE_LINE_RE.match(normalize_text(text)))


def _clean_label(s: str) -> str:
    s = re.sub(r"[\s/|,;:：·・\-]+", " ", s or "").strip(" /|,;:：·・-")
    return re.sub(r"\s+", " ", s).strip()


def mask_dates(text: str) -> str:
    """Replace date tokens with spaces so 11.22 or 11/1 is not read as a time."""
    return DATE_TOKEN_RE.sub(" ", normalize_text(text))


def split_parallel_clauses(text: str) -> tuple[str, list[str]]:
    """Lift （並行A 11:55-12:55） / （終演後 21:35-22:35） off the set line."""
    s = normalize_text(text)
    clauses = [m.group(0) for m in PARALLEL_RE.finditer(s)]
    if not clauses:
        return s, []
    leftover = PARALLEL_RE.sub(" ", s)
    leftover = re.sub(r"\s+", " ", leftover).strip()
    return leftover, clauses


def is_sale_line(text: str, *, event_date: str | None = None, has_leading_set: bool = False) -> bool:
    t = normalize_text(text)
    if MERCH_LINE_RE.search(t) or SALE_RE.search(t) or PRICE_RE.search(t):
        return True
    if has_foreign_date(t, event_date):
        return True
    if has_leading_set:
        return False
    # admission / ticket words on a line that is not a timetable row
    if SALE_SOFT_RE.search(t):
        return True
    return False


def extract_times(text: str) -> list[tuple[str, str | None, str]]:
    """Return list of (start, end_or_None, label beside this time).

    Dates are masked first so 2026.11.22 or 11/1(土) is not read as 11:22.
    Each time keeps only the label next to it (not the whole line), so
    '11:15-11:30 OPEN/11:20-11:30 Act' becomes doors + one act.
    """
    s = mask_dates(text)
    if not s.strip():
        return []
    def _jp_sub(m):
        hh, mm = m.group(1), m.group(2) or "00"
        return f"{int(hh):02d}:{int(mm):02d}"
    s = TIME_JP.sub(_jp_sub, s)

    range_re = re.compile(
        rf"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*{RANGE_SEP}\s*([01]?\d|2[0-3]):([0-5]\d)(?!\d)"
    )
    spans = []
    for m in range_re.finditer(s):
        st = _time_from_groups(m.group(1), m.group(2))
        en = _time_from_groups(m.group(3), m.group(4))
        if st and en:
            spans.append((st, en, m.start(), m.end()))
    if not spans:
        # dotted ranges such as 19.30-20.00, after dates have been masked
        dot_range = re.compile(
            rf"(?<!\d)([01]?\d|2[0-3])\.([0-5]\d)\s*{RANGE_SEP}\s*([01]?\d|2[0-3])\.([0-5]\d)(?!\d)"
        )
        for m in dot_range.finditer(s):
            st = _time_from_groups(m.group(1), m.group(2))
            en = _time_from_groups(m.group(3), m.group(4))
            if st and en:
                spans.append((st, en, m.start(), m.end()))
    if not spans:
        for m in TIME_TOKEN.finditer(s):
            st = _time_from_groups(m.group(1), m.group(2))
            if st:
                spans.append((st, None, m.start(), m.end()))
    # dotted singles (19.30) only when no colon/dotted ranges and not a leftover date
    if not spans:
        for m in TIME_TOKEN_DOT.finditer(s):
            st = _time_from_groups(m.group(1), m.group(2))
            if st:
                spans.append((st, None, m.start(), m.end()))
    if not spans:
        return []
    out = []
    for i, (st, en, a, b) in enumerate(spans):
        before = s[:a] if i == 0 else ""
        after_end = spans[i + 1][2] if i + 1 < len(spans) else len(s)
        label = _clean_label(before + " " + s[b:after_end])
        out.append((st, en, label))
    return out


def _is_skip_line(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return True
    if SKIP_LINE_RE.match(t):
        return True
    if is_date_only_line(t):
        return True
    return False


def _clean_act_name(name: str) -> str:
    name = PARALLEL_RE.sub(" ", name or "")
    name = re.sub(r"(?<=\w)@(?=\w)", " ", name)
    name = name.replace("®", "").replace("™", "").replace("©", "")
    # trailing OCR junk from icons / catalogue marks, e.g. 001▶X, 0o1▶X, ※1▶X, 以
    name = re.sub(r"\s*※\S*$", "", name)
    name = re.sub(r"\s+[0oO]{1,2}[0-9iIlDX]{0,4}[▶■].*$", "", name)
    name = re.sub(r"\s+\d{1,2}[▶■]\S*$", "", name)
    name = re.sub(r"\s+[0oO]{1,2}[0-9iIl]{1,3}[DX]*\s*$", "", name)
    name = re.sub(r"\s+[0oO]?\d{2,}[A-Za-z0-9]*$", "", name)
    name = re.sub(r"\s+[▶■区以回]+\S*$", "", name)
    # keep internal : and - (for example Act:One, Able-Two); only collapse other punctuation
    name = re.sub(r"[\s/|,;·・]+", " ", name).strip(" /|,;·・")
    name = re.sub(r"\s+", " ", name).strip().strip("-:：")
    return name


def _other_item(kind: str, text: str, *, act: str = "", start: str = "", end: str = "") -> dict:
    return {
        "kind": kind,
        "text": (text or "").strip(),
        "act": (act or "").strip(),
        "start": start or "",
        "end": end or "",
    }



def _extract_doors_and_start(text: str) -> tuple[str | None, str | None]:
    t = normalize_text(text)
    def _g(m):
        return ST.parse_time(m.group(1) or m.group(2))
    d = DOORS_TIME_RE.search(t)
    s = START_TIME_RE.search(t)
    return ( _g(d) if d else None, _g(s) if s else None )


def _is_junk_label(name: str) -> bool:
    n = _clean_act_name(name)
    if not n:
        return True
    if re.fullmatch(r"[\W_ł$]+", n):
        return True
    if len(n) <= 2 and not re.search(r"[\u3040-\u9fffA-Za-z]", n):
        return True
    if re.search(r"\$\d|ł", n) and len(n) < 24:
        return True
    return False


def _is_header_noise(name: str) -> bool:
    n = _clean_act_name(name)
    return bool(re.fullmatch(
        r"(time\s*table|timetable|presents?|idol|band|-?\s*act\s*-?|"
        r"ol|open|start|ticket|tickets|shibuya|cyclone|midtown|hall|venue)",
        n, re.I,
    ))


def _is_weak_name(text: str) -> bool:
    """Merch, prices, sale copy or OCR junk — not an act name."""
    n = _clean_act_name(text)
    if _is_junk_label(n) or _is_header_noise(n):
        return True
    if MERCH_LINE_RE.search(n or text) or SALE_RE.search(n or text) or PRICE_RE.search(n or text):
        return True
    return False


def _name_right_of_time(frags: list[dict], time_frag: dict, *, y_tol: float = 0.022, allow_weak: bool = False) -> dict | None:
    """Pick the leftmost real label to the right of a timetable time, same row.

    Merch windows and price fragments further right must not steal the slot.
    """
    ty, tx = time_frag["y"], time_frag["x"]
    candidates = []
    for g in frags:
        if g is time_frag:
            continue
        if g.get("is_time_slot") or g.get("used"):
            continue
        if g["x"] <= tx + 0.01:
            continue
        dy = abs(g["y"] - ty)
        if dy > y_tol:
            continue
        # a non-leading time is a merch / parallel annotation, not a name
        if g.get("times") and not g.get("is_time_slot"):
            continue
        if (not allow_weak) and _is_weak_name(g.get("text") or ""):
            continue
        candidates.append((g["x"], dy, g))
    if not candidates:
        return None
    # Same-column names share nearly the same x, so x-first sorting
    # would pick a neighbour row. Cluster around the closest y first,
    # then take the leftmost label on that row.
    best_dy = min(dy for _x, dy, _g in candidates)
    row = [(x, dy, g) for x, dy, g in candidates if dy <= best_dy + 0.01]
    row.sort()
    return row[0][2]


def _parse_spatial(raw: list[dict], *, date_str: str, event_name: str,
                   venue_name: str, tz: str) -> dict:
    """Pair timetable times with the name on the same row to their right.

    Used when OCR boxes have x/y. Left-column lineup, ticket prices and
    merch windows are not treated as set times.
    """
    other: list[dict] = []
    doors = None
    show_start = None
    notes_bits = []
    frags = []

    for rec in raw:
        original = rec.get("text") or ""
        text_line = normalize_text(original)
        x = float(rec.get("x") or 0)
        y = float(rec.get("y") or 0)
        if not text_line:
            continue

        d, s = _extract_doors_and_start(text_line)
        if d and not doors:
            doors = d
        if s and not show_start:
            show_start = s

        if _is_skip_line(text_line):
            continue

        stripped, clauses = split_parallel_clauses(text_line)
        for cl in clauses:
            times_cl = extract_times(cl)
            st = times_cl[0][0] if times_cl else ""
            en = times_cl[0][1] if times_cl else ""
            other.append(_other_item("parallel", cl, start=st or "", end=en or ""))

        work = (stripped or "").strip()
        # Garbled merch OCR such as "($1T RE 14:20-15:20łł" — not a leading set time
        leading = bool(work and re.match(r"^\d{1,2}:\d{2}", mask_dates(work).strip()))
        if (not work) or (re.search(r"\$\d", text_line) and not leading):
            if extract_times(text_line) and not leading:
                times_cl = extract_times(text_line)
                st = times_cl[0][0] if times_cl else ""
                en = times_cl[0][1] if times_cl else ""
                other.append(_other_item("merch", text_line, start=st or "", end=en or ""))
            if not work:
                continue

        times = extract_times(work)
        # A fragment is a "time slot" if it is (or starts with) a set time
        is_time_slot = bool(times and re.match(r"^\d{1,2}:\d{2}", mask_dates(work).strip()))
        frags.append({
            "text": work,
            "orig": text_line,
            "x": x, "y": y,
            "times": times,
            "is_time_slot": is_time_slot,
        })

    acts: list[dict] = []

    def add_named(name: str, start: str, end: str, *, source_text: str = ""):
        nonlocal doors
        name = _clean_act_name(name)
        if not name or _is_junk_label(name) or _is_header_noise(name):
            return
        if event_name and _names_match(name, event_name):
            return
        if CHANGEOVER_RE.match(name):
            other.append(_other_item("break", source_text or f"{start}–{end} {name}",
                                     act=name, start=start or "", end=end or ""))
            return
        if MERCH_LINE_RE.search(name) or SALE_RE.search(name):
            kind = "merch" if MERCH_LINE_RE.search(name) else "sale"
            other.append(_other_item(kind, source_text or f"{start}–{end} {name}",
                                     act=name, start=start or "", end=end or ""))
            return
        if PRICE_RE.search(name):
            other.append(_other_item("price", source_text or name, start=start or "", end=end or ""))
            return
        acts.append({"act": name, "start": start or "", "end": end or "", "doors": "", "notes": ""})

    # Each timetable time picks the name on the same row to its right,
    # or the label already on the time fragment (name and time on one line).
    for f in frags:
        if not f["is_time_slot"]:
            continue
        for st, en, label in f["times"]:
            # doors/start already captured; skip using OPEN/START as an act
            if DOORS_RE.search(f["text"]) or SHOW_START_RE.search(f["text"]):
                leftover = _clean_act_name(SHOW_START_RE.sub("", DOORS_RE.sub("", label or f["text"])))
                if not leftover or leftover.lower() in {"open", "start", "doors", "opening"}:
                    continue
            name = "" if _is_junk_label(label or "") else _clean_act_name(label or "")
            # Same-fragment leftover wins: act, changeover or merch already
            # on this line. Don't look right and steal a neighbour.
            if name and not _is_header_noise(name):
                add_named(name, st, en, source_text=f["text"])
                continue
            # Look right on the same row
            right = _name_right_of_time(frags, f)
            if right:
                right["used"] = True
                add_named(right["text"], st, en, source_text=f"{f['text']} {right['text']}")
            elif name:
                add_named(name, st, en, source_text=f["text"])
            elif MERCH_LINE_RE.search(f["text"]) or SALE_RE.search(f["text"]):
                kind = "merch" if MERCH_LINE_RE.search(f["text"]) else "sale"
                other.append(_other_item(kind, f["text"], start=st or "", end=en or ""))
            else:
                # merch / sale label to the right of a bare timetable slot
                # (e.g. 16:10-17:10 中間物販, 21:00-22:00 終演後物販)
                label = _name_right_of_time(frags, f, allow_weak=True)
                if label and (MERCH_LINE_RE.search(label["text"]) or SALE_RE.search(label["text"])):
                    label["used"] = True
                    kind = "merch" if MERCH_LINE_RE.search(label["text"]) else "sale"
                    nm = _clean_act_name(label["text"])
                    other.append(_other_item(
                        kind, f"{f['text']} {label['text']}", act=nm,
                        start=st or "", end=en or "",
                    ))

    # leftover left-column sale / price / merch copy (not used as a slot label)
    for f in frags:
        if f.get("is_time_slot") or f.get("used"):
            continue
        work = f["text"]
        if PRICE_RE.search(work) or is_sale_line(work, event_date=date_str, has_leading_set=False):
            kind = "price" if PRICE_RE.search(work) else "sale"
            if MERCH_LINE_RE.search(work):
                kind = "merch"
            other.append(_other_item(kind, work))
            f["used"] = True

    # infer missing ends (should be rare when both start and end are printed)
    timed = [r for r in acts if r.get("start")]
    for i, row in enumerate(timed):
        if row.get("end"):
            continue
        if i + 1 < len(timed) and timed[i + 1].get("start"):
            nxt = timed[i + 1]["start"]
            if nxt != row["start"]:
                row["end"] = nxt
                row["notes"] = "end inferred from next start"

    if doors and acts:
        acts[0]["doors"] = doors

    used_idx = set()
    for row in acts:
        for i, item in enumerate(other):
            if i in used_idx or item.get("act") or item.get("kind") != "parallel":
                continue
            item["act"] = row["act"]
            if row["act"] and row["act"] not in (item.get("text") or ""):
                item["text"] = f"{row['act']} {item['text']}".strip()
            used_idx.add(i)
            break

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
            "start": show_start or "",
        },
        "rows": acts,
        "other": other,
        "doors": doors or "",
        "start": show_start or "",
        "notes": notes_bits,
    }


def parse_ocr_lines(lines, *, date: str | None = None, event: str | None = None,
                    timezone: str | None = None, venue: str | None = None) -> dict:
    """Parse OCR lines (strings or {text,...} dicts) into editor rows and meta.

    Sale, presale, lottery, ticket, merch and parallel-session times are kept
    in `other` and are not used as acts, doors or show start.
    """
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
    # Use spatial pairing only when OCR boxes actually spread in x.
    # String-only tests (and Vision lines without boxes) stay sequential.
    # Synthetic string rows are stamped at x=0, so they must not take this path.
    xs = [float(r["x"]) for r in raw
          if isinstance(r.get("x"), (int, float)) and isinstance(r.get("y"), (int, float))]
    if len(xs) >= 2 and (max(xs) - min(xs)) > 0.05:
        return _parse_spatial(raw, date_str=date_str, event_name=event_name,
                              venue_name=venue_name, tz=tz)
    doors = None
    notes_bits = []
    acts: list[dict] = []
    other: list[dict] = []
    show_start = None

    pending_time = None  # (start, end) waiting for an act name on a nearby line
    pending_name = None

    def add_act(name, start, end, notes=""):
        name = _clean_act_name(name)
        if not name:
            return
        if DOORS_RE.search(name) or SHOW_START_RE.fullmatch(name.strip()):
            return
        if SKIP_LINE_RE.match(name) or is_date_only_line(name):
            return
        if CHANGEOVER_RE.match(name) or MERCH_LINE_RE.search(name) or SALE_RE.search(name):
            other.append(_other_item(
                "break" if CHANGEOVER_RE.match(name) else ("merch" if MERCH_LINE_RE.search(name) else "sale"),
                f"{start or ''}–{end or ''} {name}".strip(" –"),
                act=name, start=start or "", end=end or "",
            ))
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
        original = rec.get("text") or ""
        text_line = normalize_text(original)
        if not text_line:
            continue
        d, s = _extract_doors_and_start(text_line)
        if d and not doors:
            doors = d
        if s and not show_start:
            show_start = s
        if _is_skip_line(text_line):
            continue

        vm = VENUE_RE.search(text_line)
        if vm and not venue_name:
            venue_name = vm.group(2).strip()
            rest_v = VENUE_RE.sub("", text_line).strip()
            if not rest_v:
                continue
            text_line = rest_v

        stripped, clauses = split_parallel_clauses(text_line)
        for cl in clauses:
            times_cl = extract_times(cl)
            st = times_cl[0][0] if times_cl else ""
            en = times_cl[0][1] if times_cl else ""
            other.append(_other_item("parallel", cl, start=st or "", end=en or ""))

        work = (stripped or "").strip()
        if not work:
            # a lone （並行物販 …） / （終演後物販） clause — keep pending act names
            continue
        times = extract_times(work)
        has_leading_set = bool(times and re.match(r"^\d{1,2}:\d{2}", mask_dates(work).strip()))
        if is_sale_line(work, event_date=date_str, has_leading_set=has_leading_set) and not has_leading_set:
            # sale / price / merch / a date that is not the show date
            if times:
                for st, en, lab in times:
                    other.append(_other_item(
                        "sale",
                        work,
                        act=_clean_act_name(lab),
                        start=st or "",
                        end=en or "",
                    ))
            else:
                kind = "price" if PRICE_RE.search(work) else "sale"
                other.append(_other_item(kind, work))
            pending_name = None
            continue

        # merch or adjustment on an otherwise timed row (e.g. 21:35-22:35 終演後物販)
        if times and (MERCH_LINE_RE.search(work) or (times[0][2] and (MERCH_LINE_RE.search(times[0][2]) or CHANGEOVER_RE.match(_clean_act_name(times[0][2]) or "")))):
            for st, en, lab in times:
                name = _clean_act_name(lab)
                kind = "break" if ADJUST_RE.match(name or "") else "merch"
                other.append(_other_item(kind, work, act=name, start=st or "", end=en or ""))
            pending_name = None
            continue

        is_doors = bool(DOORS_RE.search(work))
        is_show = bool(SHOW_START_RE.search(work)) and not is_doors

        if is_doors and times:
            act_times = []
            for st, en, label in times:
                lab = DOORS_RE.sub("", label or "").strip(" :：-/")
                lab_full = label or ""
                if (not lab) or DOORS_RE.search(lab_full) or re.fullmatch(r"(open|doors?|opening|開場)", lab, re.I):
                    if doors is None:
                        doors = st
                elif SHOW_START_RE.search(lab_full) and not _clean_act_name(SHOW_START_RE.sub("", lab)):
                    notes_bits.append(f"Show start {st}")
                else:
                    act_times.append((st, en, label))
            if not act_times:
                pending_name = None
                continue
            times = act_times
            is_doors = False

        if is_show and times:
            leftover_show = SHOW_START_RE.sub("", times[0][2] or "").strip(" :：-")
            leftover_show = _clean_act_name(leftover_show)
            if not leftover_show:
                notes_bits.append(f"Show start {times[0][0]}")
                continue

        if times:
            leftover = times[0][2]
            leftover = DOORS_RE.sub("", leftover or "")
            leftover = SHOW_START_RE.sub("", leftover)
            leftover = _clean_act_name(leftover)
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
                    if pending_name and not (event_name and _names_match(pending_name, event_name)):
                        add_act(pending_name, st, en)
                        pending_name = None
                    else:
                        pending_time = (st, en)
                        pending_name = None
            else:
                for st, en, bit in times:
                    name = _clean_act_name(bit)
                    if name and event_name and _names_match(name, event_name):
                        continue
                    if name:
                        add_act(name, st, en)
                    else:
                        pending_time = (st, en)
            continue

        # no times on this line
        if pending_time:
            add_act(text_line, pending_time[0], pending_time[1])
            pending_time = None
            continue
        if event_name and _names_match(text_line, event_name):
            continue
        if venue_name and _names_match(text_line, venue_name):
            continue
        if PRICE_RE.search(text_line) or SALE_RE.search(text_line) or SALE_SOFT_RE.search(text_line):
            other.append(_other_item("info" if not PRICE_RE.search(text_line) else "price", text_line))
            continue
        if not DOORS_RE.search(text_line) and not SHOW_START_RE.search(text_line) and not VENUE_RE.search(text_line):
            if len(text_line) <= 1:
                continue
            if pending_name and not acts:
                if not event_name:
                    event_name = pending_name
                pending_name = text_line
            elif pending_name:
                if not event_name:
                    event_name = pending_name
                pending_name = text_line
            else:
                pending_name = text_line

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

    # attach an act name to parallel clauses that sit on the same row, when still blank
    used_idx = set()
    for row in acts:
        for i, item in enumerate(other):
            if i in used_idx or item.get("act") or item.get("kind") != "parallel":
                continue
            # if this parallel clause was taken from the same act line, we already
            # only have the clause text; try to stamp the preceding act
            # (clauses are recorded in reading order just before the act is added,
            # so the nearest preceding act-less parallel is a reasonable match).
            if not item.get("act"):
                item["act"] = row["act"]
                if row["act"] and row["act"] not in (item.get("text") or ""):
                    item["text"] = f"{row['act']} {item['text']}".strip()
                used_idx.add(i)
                break

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
            "start": show_start or "",
        },
        "rows": acts,
        "other": other,
        "doors": doors or "",
        "start": show_start or "",
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
        "other": [],
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
        "other": parsed.get("other") or [],
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
                rec["other"] = got.get("other") or []
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



def reparse_draft(rec: dict, *, confirmed: dict | None = None) -> dict:
    """Re-run the parser on OCR already stored in a local draft. No NAS writes."""
    rec = dict(rec or {})
    lines = ((rec.get("ocr") or {}).get("lines")) or []
    if not lines and (rec.get("ocr") or {}).get("text"):
        lines = (rec["ocr"]["text"] or "").splitlines()
    parsed = parse_ocr_lines(
        lines,
        date=rec.get("date") or "",
        event=rec.get("event") or "",
        timezone=(rec.get("meta") or {}).get("timezone") or rec.get("timezone") or None,
        venue=(rec.get("meta") or {}).get("venue") or rec.get("venue") or "",
    )
    rec["rows"] = parsed["rows"]
    rec["other"] = parsed.get("other") or []
    rec["meta"] = parsed["meta"]
    rec["venue"] = parsed["meta"].get("venue") or rec.get("venue") or ""
    rec["timezone"] = parsed["meta"].get("timezone") or rec.get("timezone") or ""
    rec["comparison"] = compare_with_confirmed(rec["rows"], confirmed)
    rec["error"] = None
    if rec.get("status") in ("error", "rejected", "held", "pending_review", None, ""):
        rec["status"] = "pending_review"
    rec["parsed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return rec


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
        rec["other"] = got.get("other") or []
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
