#!/usr/bin/env python3
"""Apply confident tagger proposals to Lightroom Classic XMP sidecars (additive).

Dry run unless --apply. Only proposed_keywords are written (never tentative_keywords,
never captions). SHA-256 of each original is re-checked before applying.

Sidecars live next to the image as <stem>.xmp. Image bytes are never modified.
Existing sidecar fields are preserved: this writer only inserts missing
dc:subject and lr:hierarchicalSubject entries. It never changes xmp:Rating,
xmp:Label, crs:* develop settings, or any other existing field.

JPEG, TIFF, DNG, HEIC and PNG normally store metadata inside the file. This
edition still writes a sidecar for them (it does not embed) and reports that
Lightroom must use Metadata > Read Metadata from Files to pick the keywords up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree as ET

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))

IMAGE_EXT = {
    ".cr3", ".cr2", ".crw", ".dng", ".arw", ".nef", ".nrw", ".raf",
    ".orf", ".rw2", ".pef", ".srw", ".3fr", ".fff", ".iiq",
    ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".png",
}
# MP4/MOV ignored (not in IMAGE_EXT)
EMBEDDED_EXT = {".dng", ".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic", ".heif"}
SCHEDULE_PREFIX = "_schedule"

XMP_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC_NS = "http://purl.org/dc/elements/1.1/"
XAP_NS = "http://ns.adobe.com/xap/1.0/"
LR_NS = "http://ns.adobe.com/lightroom/1.0/"
CRS_NS = "http://ns.adobe.com/camera-raw-settings/1.0/"

XMP_TOOLKIT = "Trips Local AI for Lightroom"

MINIMAL_XMP = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{toolkit}">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _sha_of(p: Path) -> str:
    try:
        import shacache
        return shacache.sha256_cached(p)
    except Exception:
        return sha256(p)


def xmp_backup_enabled() -> bool:
    cfg_path = HOME / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and "xmp_backup" in cfg:
                return bool(cfg["xmp_backup"])
        except Exception:
            pass
    return True


def sidecar_path(image: Path) -> Path:
    return image.with_suffix(".xmp")


def is_schedule_file(name: str) -> bool:
    return Path(name).name.lower().startswith(SCHEDULE_PREFIX)


def is_embedded_format(path: Path) -> bool:
    return path.suffix.lower() in EMBEDDED_EXT


def hier_from_keyword(keyword: str) -> str:
    """General/Rank/Best -> General|Rank|Best. Already-piped paths are left alone."""
    s = (keyword or "").strip()
    if not s:
        return ""
    if "|" in s and "/" not in s:
        return s
    return s.replace("/", "|")


def keyword_from_hier(hier: str) -> str:
    return (hier or "").replace("|", "/")


def subject_terms(hier: str) -> list[str]:
    """Every level of a hierarchical path, plus the leaf (Lightroom's usual dc:subject set)."""
    parts = [p for p in (hier or "").split("|") if p]
    return parts


def _local(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _ns(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _is_xmp_rating_or_label(attr_or_tag: str, want: str) -> bool:
    loc = _local(attr_or_tag)
    if loc != want:
        return False
    ns = _ns(attr_or_tag)
    if not ns:
        return True
    return "ns.adobe.com/xap/1.0" in ns or ns in (XAP_NS, "adobe:ns:meta/")


def _is_crs(attr_or_tag: str) -> bool:
    ns = _ns(attr_or_tag)
    return bool(ns) and "camera-raw-settings" in ns


def parse_xmp(text: str) -> ET.Element:
    # Lightroom packets often start with an xpacket PI; ElementTree can take that.
    cleaned = text
    if cleaned.startswith("\ufeff"):
        cleaned = cleaned[1:]
    return ET.fromstring(cleaned)


def _bag_texts(elem: ET.Element) -> list[str]:
    out = []
    for child in elem.iter():
        if _local(child.tag) == "li" and child.text and child.text.strip():
            out.append(child.text.strip())
    return out


def read_sidecar(text: str) -> dict:
    """Parse a sidecar into keywords, rating, label and a frozen crs snapshot."""
    root = parse_xmp(text)
    subjects: list[str] = []
    hier: list[str] = []
    rating = None
    label = None
    crs_parts: list[str] = []

    for elem in root.iter():
        for attr, val in elem.attrib.items():
            if _is_xmp_rating_or_label(attr, "Rating"):
                rating = val
            elif _is_xmp_rating_or_label(attr, "Label"):
                label = val
            if _is_crs(attr):
                crs_parts.append(f"@{_local(attr)}={val}")
        if _is_xmp_rating_or_label(elem.tag, "Rating") and rating is None:
            rating = (elem.text or "").strip() or (elem.attrib.get("{%s}value" % RDF_NS) if False else None) or (elem.text or "").strip()
        if _is_xmp_rating_or_label(elem.tag, "Label") and label is None:
            label = (elem.text or "").strip()
        if _is_crs(elem.tag):
            body = (elem.text or "").strip()
            attrs = " ".join(f"{_local(k)}={v}" for k, v in elem.attrib.items())
            lis = _bag_texts(elem)
            crs_parts.append(f"{_local(elem.tag)}[{attrs}]{body}|{'|'.join(lis)}")
        loc = _local(elem.tag)
        if loc == "subject" and (_ns(elem.tag) in ("", DC_NS) or "purl.org/dc/" in _ns(elem.tag) or elem.tag.endswith("subject")):
            # Only the dc:subject element itself, not nested random 'subject'
            if _ns(elem.tag) in ("", DC_NS) or "purl.org/dc/" in _ns(elem.tag):
                subjects.extend(_bag_texts(elem))
        if loc == "hierarchicalSubject":
            hier.extend(_bag_texts(elem))

    # Dedup, keep order
    def _uniq(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return {
        "subjects": _uniq(subjects),
        "hierarchical": _uniq(hier),
        "rating": rating,
        "label": label,
        "crs": tuple(crs_parts),
        "keywords": _uniq([keyword_from_hier(h) for h in hier] + subjects),
    }


def has_keyword(info: dict, keyword: str) -> bool:
    hier = hier_from_keyword(keyword)
    if not hier:
        return False
    if hier in (info.get("hierarchical") or []):
        return True
    # Flat keyword that was only stored in dc:subject
    if "/" not in keyword and "|" not in keyword:
        if keyword in (info.get("subjects") or []):
            return True
    # Path stored with slashes in hierarchicalSubject (odd but possible)
    if keyword in (info.get("hierarchical") or []):
        return True
    if keyword_from_hier(hier) in (info.get("keywords") or []) and hier.replace("|", "/") in [
        keyword_from_hier(h) for h in (info.get("hierarchical") or [])
    ]:
        return True
    return False


# ---------------------------------------------------------------------------
# Surgical text merge: only insert into the keyword bags (or create them).
# Everything else, including odd namespaces, is left as it was.
# ---------------------------------------------------------------------------

_BAG_RE_TMPL = (
    r"<(?P<el>(?:[\w.-]+:)?{name})\b(?P<elattrs>[^>]*)>"
    r"(?P<body>\s*<(?P<bag>(?:[\w.-]+:)?(?:Bag|Seq|Alt))\b[^>]*>)"
    r"(?P<lis>.*?)"
    r"(?P<end></(?P=bag)\s*>)"
)


def _bag_re(name: str) -> re.Pattern:
    return re.compile(_BAG_RE_TMPL.format(name=re.escape(name)), re.DOTALL | re.IGNORECASE)


def _detect_li_tag(lis: str) -> str:
    m = re.search(r"<((?:[\w.-]+:)?li)\b", lis, re.IGNORECASE)
    return m.group(1) if m else "rdf:li"


def _detect_indent(lis: str) -> str:
    m = re.search(r"\n([ \t]+)<(?:[\w.-]+:)?li\b", lis, re.IGNORECASE)
    if m:
        return m.group(1)
    if lis.strip():
        return "    "
    return "    "


def _escape_li(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _unescape_li(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
    )


def _insert_lis(lis: str, values: list[str], already: set[str]) -> tuple[str, list[str]]:
    added = []
    if not values:
        return lis, added
    li_tag = _detect_li_tag(lis)
    indent = _detect_indent(lis)
    # existing li texts in this bag (from the raw XML, unescaped)
    existing = set(already)
    for m in re.finditer(
        rf"<((?:[\w.-]+:)?li)\b[^>]*>(.*?)</\1\s*>", lis, re.DOTALL | re.IGNORECASE
    ):
        existing.add(_unescape_li(re.sub(r"\s+", " ", m.group(2)).strip()))
    chunk = ""
    for v in values:
        if v in existing:
            continue
        existing.add(v)
        added.append(v)
        chunk += f"\n{indent}<{li_tag}>{_escape_li(v)}</{li_tag}>"
    if not chunk:
        return lis, added
    # Keep a tidy newline before the closing bag tag if the bag wasn't empty
    if lis.strip():
        if not lis.endswith("\n"):
            chunk = chunk  # insert before </bag>; caller keeps end tag
        return lis.rstrip() + chunk + "\n" + indent[:-2] if len(indent) >= 2 else lis.rstrip() + chunk + "\n  ", added
    # empty bag
    return "\n" + chunk + "\n   ", added


def _ensure_xmlns(xml: str, prefix: str, uri: str) -> str:
    if re.search(rf"xmlns:{re.escape(prefix)}\s*=", xml):
        return xml

    def add(m: re.Match) -> str:
        tag = m.group(0)
        insertion = f' xmlns:{prefix}="{uri}"'
        if tag.endswith("/>"):
            return tag[:-2] + insertion + " />"
        if tag.endswith(">"):
            return tag[:-1] + insertion + ">"
        return tag

    # Prefer rdf:Description; fall back to rdf:RDF
    new, n = re.subn(
        r"<(?:[\w.-]+:)?Description\b[^>]*?/?>", add, xml, count=1
    )
    if n:
        return new
    new, n = re.subn(r"<(?:[\w.-]+:)?RDF\b[^>]*>", add, xml, count=1)
    return new if n else xml


def _expand_self_closing_description(xml: str) -> str:
    def expand(m: re.Match) -> str:
        tag = m.group(0)
        inner = tag[1:-2].rstrip()  # drop < and />
        # Keep the original attributes byte-for-byte (minus the trailing /)
        raw = tag
        if raw.endswith("/>"):
            open_tag = raw[:-2].rstrip() + ">"
            return open_tag + "\n  </rdf:Description>"
        return raw

    # Only the self-closing Description
    return re.sub(
        r"<(?:[\w.-]+:)?Description\b[^>]*/>", expand, xml, count=1
    )


def _description_close_re() -> re.Pattern:
    return re.compile(r"</(?:[\w.-]+:)?Description\s*>", re.IGNORECASE)


def _bag_block(el_prefix: str, name: str, bag_prefix: str, values: list[str]) -> str:
    lis = "".join(f"\n      <{bag_prefix}li>{_escape_li(v)}</{bag_prefix}li>" for v in values)
    return (
        f"   <{el_prefix}{name}>\n"
        f"    <{bag_prefix}Bag>{lis}\n"
        f"    </{bag_prefix}Bag>\n"
        f"   </{el_prefix}{name}>\n"
    )


def _rdf_prefix(xml: str) -> str:
    m = re.search(r"<(([\w.-]+):)?RDF\b", xml)
    if m and m.group(2):
        return m.group(2) + ":"
    # Default xmlns is rdf? Still use rdf: for new tags; xmlns:rdf will be ensured.
    return "rdf:"


def merge_keywords_into_xmp(text: str, keywords: list[str]) -> tuple[str, list[str], list[str]]:
    """Insert missing keywords into dc:subject and lr:hierarchicalSubject.

    Returns (new_xml, added_hier_paths, already_present_original_keywords).
    """
    xml = text
    if xml.startswith("\ufeff"):
        # Keep the BOM on the way out
        has_bom = True
        xml = xml[1:]
    else:
        has_bom = False

    info = read_sidecar(xml) if xml.strip() else {
        "subjects": [], "hierarchical": [], "rating": None, "label": None, "crs": (), "keywords": [],
    }
    already = []
    to_hier: list[str] = []
    to_subj: list[str] = []
    seen_h, seen_s = set(info.get("hierarchical") or []), set(info.get("subjects") or [])

    for kw in keywords:
        hier = hier_from_keyword(kw)
        if not hier:
            continue
        if has_keyword(info, kw) or hier in seen_h:
            already.append(kw)
            continue
        to_hier.append(hier)
        seen_h.add(hier)
        for term in subject_terms(hier):
            if term not in seen_s:
                to_subj.append(term)
                seen_s.add(term)

    if not to_hier and not to_subj:
        return (text, [], already)

    xml = _ensure_xmlns(xml, "dc", DC_NS)
    xml = _ensure_xmlns(xml, "lr", LR_NS)
    xml = _ensure_xmlns(xml, "rdf", RDF_NS)
    if re.search(r"<(?:[\w.-]+:)?Description\b[^>]*/>", xml):
        xml = _expand_self_closing_description(xml)

    rdf_p = _rdf_prefix(xml)

    def merge_bag(xml_in: str, names: tuple[str, ...], values: list[str], ns_hint: str) -> str:
        if not values:
            return xml_in
        for name in names:
            cre = _bag_re(name)
            m = cre.search(xml_in)
            if not m:
                continue
            new_lis, _added = _insert_lis(m.group("lis"), values, set())
            return xml_in[: m.start("lis")] + new_lis + xml_in[m.end("lis"):]
        # No existing bag: insert a new block before </rdf:Description>
        el_prefix = ns_hint
        block = _bag_block(el_prefix, names[0], rdf_p, values)
        close = _description_close_re()
        m = close.search(xml_in)
        if not m:
            # Last resort: append before xpacket end or at end of xmpmeta
            end = re.search(r"</(?:[\w.-]+:)?xmpmeta\s*>", xml_in, re.IGNORECASE)
            if end:
                return xml_in[: end.start()] + block + xml_in[end.start():]
            return xml_in + block
        return xml_in[: m.start()] + block + xml_in[m.start():]

    xml = merge_bag(xml, ("subject",), to_subj, "dc:")
    xml = merge_bag(xml, ("hierarchicalSubject",), to_hier, "lr:")

    if has_bom:
        xml = "\ufeff" + xml
    return xml, to_hier, already


def minimal_sidecar(keywords: list[str]) -> str:
    xml = MINIMAL_XMP.format(toolkit=XMP_TOOLKIT)
    xml, _, _ = merge_keywords_into_xmp(xml, keywords)
    return xml


def _write_atomic(path: Path, data: str, backup: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.is_file():
        bak = Path(str(path) + ".bak")
        bak.write_bytes(path.read_bytes())
    # Same directory so os.replace is atomic on the same volume
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply_to_sidecar(image: Path, keywords: list[str], apply: bool, backup: bool | None = None) -> dict:
    """Merge keywords into the sidecar for one image. Image bytes are never touched.

    Returns a dict with applied, skipped, failure, before, after, note.
    Each applied/skipped entry is one keyword.
    """
    backup = xmp_backup_enabled() if backup is None else backup
    sidecar = sidecar_path(image)
    existed = sidecar.is_file()
    original = sidecar.read_text(encoding="utf-8") if existed else ""
    before = read_sidecar(original) if original.strip() else {
        "subjects": [], "hierarchical": [], "rating": None, "label": None, "crs": (), "keywords": [],
    }
    image_bytes_before = None
    if image.is_file() and is_embedded_format(image):
        # Cheap size+mtime stamp; tests may also compare full bytes
        st = image.stat()
        image_bytes_before = (st.st_size, st.st_mtime_ns)

    applied, skipped, failures = [], [], []
    note = None
    if is_embedded_format(image):
        note = (
            "this format usually stores metadata inside the file; "
            "a sidecar was written and Lightroom must use "
            "Metadata > Read Metadata from Files to pick the keywords up"
        )

    if not keywords:
        return {
            "applied": applied, "skipped": skipped, "failures": failures,
            "before": before, "after": before, "note": note,
            "sidecar": str(sidecar), "created": False, "image_untouched": True,
        }

    if existed:
        new_xml, added_hier, already = merge_keywords_into_xmp(original, keywords)
    else:
        pending = [k for k in keywords if k]
        already = []
        added_hier = [hier_from_keyword(k) for k in pending]
        new_xml = minimal_sidecar(pending)

    for kw in keywords:
        if not kw:
            continue
        if kw in already or has_keyword(before, kw):
            skipped.append({"filename": image.name, "keyword": kw, "reason": "already applied"})
        elif hier_from_keyword(kw) in added_hier:
            applied.append({"filename": image.name, "keyword": kw, "path": str(sidecar)})
        else:
            # merge decided not to add it and it wasn't already present
            skipped.append({"filename": image.name, "keyword": kw, "reason": "already applied"})

    if apply and added_hier:
        try:
            _write_atomic(sidecar, new_xml, backup=backup and existed)
        except Exception as e:
            # Move would-be applied into failures; nothing was written (or replace failed)
            for a in applied:
                failures.append({"filename": a["filename"], "keyword": a.get("keyword"), "reason": str(e)})
            applied = []
            after = before
            return {
                "applied": applied, "skipped": skipped, "failures": failures,
                "before": before, "after": after, "note": note,
                "sidecar": str(sidecar), "created": False, "image_untouched": True,
            }
        after = read_sidecar(sidecar.read_text(encoding="utf-8"))
        # Protected-field check
        if after.get("rating") != before.get("rating") or after.get("label") != before.get("label"):
            failures.append({
                "filename": image.name,
                "reason": f"post-check: rating/label changed (before rating={before.get('rating')!r} label={before.get('label')!r}, "
                          f"after rating={after.get('rating')!r} label={after.get('label')!r})",
            })
        if after.get("crs") != before.get("crs"):
            failures.append({"filename": image.name, "reason": "post-check: crs develop settings changed"})
        # Existing keywords must still be present
        lost = [k for k in (before.get("hierarchical") or []) if k not in (after.get("hierarchical") or [])]
        lost += [k for k in (before.get("subjects") or []) if k not in (after.get("subjects") or [])]
        if lost:
            failures.append({"filename": image.name, "reason": "post-check: lost=" + ",".join(lost)})
    else:
        after = before
        if not apply:
            for a in applied:
                a["dry"] = True

    image_untouched = True
    if image_bytes_before and image.is_file():
        st = image.stat()
        image_untouched = (st.st_size, st.st_mtime_ns) == image_bytes_before

    return {
        "applied": applied, "skipped": skipped, "failures": failures,
        "before": {
            "filename": image.name, "keywords": before.get("keywords") or [],
            "rating": before.get("rating"), "color": before.get("label"),
        },
        "after": {
            "filename": image.name, "keywords": after.get("keywords") or [],
            "rating": after.get("rating"), "color": after.get("label"),
        },
        "note": note, "sidecar": str(sidecar),
        "created": bool(apply and added_hier and not existed),
        "image_untouched": image_untouched,
        "embedded_format": is_embedded_format(image),
    }


def snap_folder(folder: Path) -> dict:
    """Read rating and colour (xmp:Label) from sidecars in a folder. No catalog needed."""
    folder = Path(folder)
    out = {}
    if not folder.is_dir():
        return out
    try:
        names = sorted(folder.iterdir())
    except OSError:
        return out
    for p in names:
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
            continue
        if p.name.startswith(".") or is_schedule_file(p.name):
            continue
        sc = sidecar_path(p)
        info = {"rating": None, "color": None, "name": p.name, "keywords": []}
        if sc.is_file():
            try:
                parsed = read_sidecar(sc.read_text(encoding="utf-8"))
                info["rating"] = parsed.get("rating")
                info["color"] = parsed.get("label")
                info["keywords"] = parsed.get("keywords") or []
            except Exception:
                pass
        # Key by filename (stable, no Capture One variant id)
        out[p.name] = info
    return out


def write_snap(path: Path, folder: Path) -> dict:
    data = snap_folder(folder)
    Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def remove_keywords_from_sidecar(image: Path, keywords: list[str], apply: bool, backup: bool | None = None) -> dict:
    """Remove only the listed keywords from a sidecar. Everything else is left alone."""
    backup = xmp_backup_enabled() if backup is None else backup
    sidecar = sidecar_path(image)
    removed, skipped, failures = [], [], []
    if not sidecar.is_file():
        for kw in keywords:
            skipped.append({"filename": image.name, "keyword": kw, "reason": "no sidecar"})
        return {"removed": removed, "skipped": skipped, "failures": failures, "sidecar": str(sidecar)}
    original = sidecar.read_text(encoding="utf-8")
    try:
        before = read_sidecar(original)
    except Exception as e:
        return {"removed": [], "skipped": [], "failures": [{"filename": image.name, "reason": f"parse error: {e}"}],
                "sidecar": str(sidecar)}

    drop_hier = {hier_from_keyword(k) for k in keywords if k}
    # Also accept already-piped forms
    drop_hier |= {k for k in keywords if k and "|" in k}

    def _drop_lis(lis: str, should_drop) -> tuple[str, list[str]]:
        gone = []

        def repl(m: re.Match) -> str:
            text = _unescape_li(re.sub(r"\s+", " ", m.group(2)).strip())
            if should_drop(text):
                gone.append(text)
                return ""
            return m.group(0)

        new = re.sub(
            r"<((?:[\w.-]+:)?li)\b[^>]*>(.*?)</\1\s*>",
            repl,
            lis,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return new, gone

    xml = original
    # hierarchicalSubject: drop exact paths we added
    cre = _bag_re("hierarchicalSubject")
    m = cre.search(xml)
    dropped_h = []
    if m:
        new_lis, dropped_h = _drop_lis(m.group("lis"), lambda t: t in drop_hier or keyword_from_hier(t) in {keyword_from_hier(h) for h in drop_hier})
        xml = xml[: m.start("lis")] + new_lis + xml[m.end("lis"):]

    # Remaining hierarchical paths after the drop (from the updated XML if we apply, else computed)
    remaining_hier = [h for h in (before.get("hierarchical") or []) if h not in drop_hier and keyword_from_hier(h) not in {keyword_from_hier(x) for x in drop_hier}]
    still_needed = set()
    for h in remaining_hier:
        still_needed.update(subject_terms(h))

    drop_subj = set()
    for kw in keywords:
        hier = hier_from_keyword(kw)
        for term in subject_terms(hier):
            if term not in still_needed:
                drop_subj.add(term)

    cre = _bag_re("subject")
    m = cre.search(xml)
    dropped_s = []
    if m and drop_subj:
        new_lis, dropped_s = _drop_lis(m.group("lis"), lambda t: t in drop_subj)
        xml = xml[: m.start("lis")] + new_lis + xml[m.end("lis"):]

    for kw in keywords:
        hier = hier_from_keyword(kw)
        if hier in dropped_h or keyword_from_hier(hier) in {keyword_from_hier(h) for h in dropped_h}:
            removed.append({"filename": image.name, "keyword": kw, "path": str(sidecar)})
        elif not has_keyword(before, kw):
            skipped.append({"filename": image.name, "keyword": kw, "reason": "not present"})
        else:
            # Present but the surgical drop missed it
            if apply:
                failures.append({"filename": image.name, "keyword": kw, "reason": "could not locate in sidecar"})
            else:
                removed.append({"filename": image.name, "keyword": kw, "path": str(sidecar), "dry": True})

    if not apply:
        for r in removed:
            r["dry"] = True
        return {"removed": removed, "skipped": skipped, "failures": failures, "sidecar": str(sidecar)}

    if xml != original and (dropped_h or dropped_s):
        try:
            _write_atomic(sidecar, xml, backup=backup)
        except Exception as e:
            return {"removed": [], "skipped": skipped,
                    "failures": failures + [{"filename": image.name, "reason": str(e)}],
                    "sidecar": str(sidecar)}
        after = read_sidecar(sidecar.read_text(encoding="utf-8"))
        if after.get("rating") != before.get("rating") or after.get("label") != before.get("label"):
            failures.append({"filename": image.name, "reason": "post-check: rating/label changed"})
        if after.get("crs") != before.get("crs"):
            failures.append({"filename": image.name, "reason": "post-check: crs develop settings changed"})
    return {"removed": removed, "skipped": skipped, "failures": failures, "sidecar": str(sidecar)}


def apply_proposals(proposals_json, apply=False, prefix=None, library=None, backup=None):
    """Apply confident rows from a proposals file. API matches the old apply_c1 entry point.

    `library` is accepted and ignored (Lightroom has no keyword-library gate).
    """
    data = json.load(open(proposals_json))
    folder = Path(data["folder"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = prefix or f"/tmp/trip-localai-lr-apply-{stamp}"
    backup = xmp_backup_enabled() if backup is None else backup
    items, skipped_policy = [], []
    for r in data.get("images") or []:
        fn = r.get("filename") or ""
        if is_schedule_file(fn):
            skipped_policy.append({"filename": fn, "reason": "schedule file"})
            continue
        if r.get("error"):
            skipped_policy.append({"filename": fn, "reason": "tagger error"})
            continue
        if r.get("needs_review"):
            skipped_policy.append({
                "filename": fn,
                "reason": "needs_review: " + "; ".join(r.get("review_reasons", [])),
                "keywords": r.get("proposed_keywords", []) + r.get("tentative_keywords", []),
            })
            continue
        if not r.get("proposed_keywords"):
            continue
        p = folder / fn
        if r.get("sha256"):
            try:
                if p.is_file() and _sha_of(p) != r["sha256"]:
                    skipped_policy.append({"filename": fn, "reason": "sha256 mismatch"})
                    continue
            except OSError:
                skipped_policy.append({"filename": fn, "reason": "sha256 mismatch"})
                continue
        items.append({
            "filename": fn, "path": str(p), "sha256": r.get("sha256"),
            "keywords": r.get("proposed_keywords", []),
        })

    plan = {"folder": str(folder), "items": items, "skipped_by_policy": skipped_policy, "writer": "xmp"}
    plan_path = prefix + "-plan.json"
    Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(plan, open(plan_path, "w"), indent=1)

    res = {
        "applied": [], "skipped": list(skipped_policy), "failures": [],
        "before": {}, "after": {}, "notes": [], "writer": "xmp",
    }
    for it in items:
        p = Path(it["path"])
        one = apply_to_sidecar(p, it["keywords"], apply=apply, backup=backup)
        res["applied"].extend(one["applied"])
        res["skipped"].extend(one["skipped"])
        res["failures"].extend(one["failures"])
        res["before"][it["filename"]] = one["before"]
        res["after"][it["filename"]] = one["after"]
        if one.get("note"):
            res["notes"].append({"filename": it["filename"], "note": one["note"],
                                 "embedded_format": one.get("embedded_format")})

    Path(prefix + "-before.json").write_text(json.dumps(res["before"], indent=1), encoding="utf-8")
    Path(prefix + "-result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    action = "APPLIED" if apply else "DRY RUN"
    n_var = len({a["filename"] for a in res["applied"]})
    print(
        f"{action}: {len(res['applied'])} keywords/fields on {n_var} sidecars; "
        f"skipped {len(res['skipped'])} (+{len(skipped_policy)} images by policy); "
        f"failures {len(res['failures'])}"
    )
    print("files:", prefix + "-{before,plan,result}.json")
    return res


def undo_plan(plan_json, apply=False, prefix=None, backup=None):
    """Remove keywords listed in an undo plan {folder, items:[{filename, keywords}]}."""
    data = json.load(open(plan_json)) if not isinstance(plan_json, dict) else plan_json
    folder = Path(data.get("scope_folder") or data.get("folder") or "")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = prefix or f"/tmp/trip-localai-lr-undo-{stamp}"
    res = {"removed": [], "skipped": [], "failures": [], "writer": "xmp"}
    for it in data.get("items") or []:
        p = folder / it["filename"]
        one = remove_keywords_from_sidecar(p, it.get("keywords") or [], apply=apply, backup=backup)
        res["removed"].extend(one["removed"])
        res["skipped"].extend(one["skipped"])
        res["failures"].extend(one["failures"])
    Path(prefix + "-result.json").parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(prefix + "-result.json", "w"), indent=1)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("proposals_json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prefix")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()
    apply_proposals(a.proposals_json, a.apply, a.prefix, backup=(False if a.no_backup else None))
