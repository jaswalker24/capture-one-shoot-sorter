#!/usr/bin/env python3
"""HTTP helpers for the Schedules tab. Returns (status_code, dict)."""
from __future__ import annotations

from pathlib import Path

import nas_paths as NP
import schedules_tree as ST


def _root(home: Path) -> Path:
    NP.HOME = Path(home)
    ST.HOME = Path(home)
    ST.DRAFTS_DIR = Path(home) / "gui" / "schedule-drafts"
    ST.JOBS_DIR = Path(home) / "gui" / "jobs"
    ST.HISTORY_DIR = Path(home) / "gui" / "history"
    ST.MERGED_DIR = Path(home) / "schedules"
    return NP.schedules_root(home)


def _drafts(home: Path) -> Path:
    return Path(home) / "gui" / "schedule-drafts"


def _photos_dates(home: Path) -> set[str]:
    try:
        imports = NP.imports_root(home)
    except Exception:
        return set()
    dates = set()
    if not imports.is_dir():
        return dates
    try:
        years = [p for p in imports.iterdir() if p.is_dir() and p.name.isdigit()]
    except Exception:
        return dates
    for y in years:
        try:
            for d in y.iterdir():
                ds = ST.import_folder_date(d)
                if ds:
                    dates.add(ds)
        except Exception:
            continue
    return dates


def _meta_from(data: dict) -> dict:
    return {
        "date": (data.get("date") or "").strip(),
        "event": (data.get("event") or data.get("name") or "").strip(),
        "venue": (data.get("venue") or "").strip(),
        "timezone": data.get("timezone") or "",
        "changeover_buffer_min": int(data.get("changeover_buffer_min") or data.get("buffer_min") or 3),
        "event_type": data.get("event_type") or "gig",
        "source": data.get("source") or "manual",
        "status": data.get("status") or "confirmed",
    }


def handle_get(path: str, qs: dict, home: Path) -> tuple[int, dict]:
    home = Path(home)
    root = _root(home)

    def q(name, default=""):
        v = qs.get(name) or [default]
        return v[0]

    if path == "/api/schedules/status":
        st = ST.nas_status(root)
        pend = {"waiting_count": 0, "entries": []}
        if st["ok"]:
            pend = ST.pending_entries(root, dates_with_photos=_photos_dates(home), write=False)
        return 200, {
            "ok": st["ok"],
            "mounted": st.get("mounted", False),
            "exists": st.get("exists", False),
            "error": st.get("error"),
            "root": str(root),
            "waiting_count": pend.get("waiting_count") or 0,
        }

    if path == "/api/schedules/events":
        st = ST.nas_status(root)
        if not st["ok"]:
            return 200, {"ok": False, "error": st["error"], "mounted": False, "events": [], "root": str(root)}
        pend = ST.pending_entries(root, dates_with_photos=_photos_dates(home), write=False)
        return 200, {
            "ok": True, "mounted": True, "exists": st["exists"], "root": str(root),
            "events": ST.list_events(root),
            "waiting_count": pend.get("waiting_count") or 0,
        }

    if path == "/api/schedules/event":
        date_str, name = q("date"), q("name") or q("event")
        if not date_str or not name:
            return 400, {"error": "date and name required"}
        out = ST.load_event(root, date_str, name)
        if out.get("ok"):
            draft = ST.load_draft(date_str, name, drafts_dir=_drafts(home))
            out["draft"] = draft
        return (200 if out.get("ok") or out.get("error") else 400), out

    if path == "/api/schedules/draft":
        date_str, name = q("date"), q("name") or q("event")
        draft = ST.load_draft(date_str, name, drafts_dir=_drafts(home))
        return 200, {"ok": True, "draft": draft}

    if path == "/api/schedules/defaults":
        date_str = q("date")
        typ = q("event_type") or "gig"
        return 200, {
            "timezone": ST.default_timezone(date_str) if date_str else "Europe/London",
            "changeover_buffer_min": 3,
            "event_type": typ,
            "hints": ST.column_hints(typ),
            "japan_trip": bool(date_str and ST.default_timezone(date_str) == "Asia/Tokyo"),
        }

    if path == "/api/schedules/history":
        date_str, name = q("date"), q("name") or q("event")
        if not date_str or not name:
            return 400, {"error": "date and name required"}
        evdir = ST.event_dir(root, date_str, name)
        if not evdir.is_dir():
            return 404, {"error": "event folder not found"}
        return 200, {"ok": True, **ST.history_list(evdir)}

    if path == "/api/schedules/history/diff":
        date_str, name = q("date"), q("name") or q("event")
        try:
            a = int(q("a") or q("version") or "0")
            b = q("b")
            b = int(b) if b else None
        except ValueError:
            return 400, {"error": "version numbers required"}
        evdir = ST.event_dir(root, date_str, name)
        try:
            return 200, {"ok": True, **ST.history_diff(evdir, a, b)}
        except Exception as e:
            return 400, {"error": str(e)}

    if path in ("/api/schedules/manifest", "/api/schedules/pending"):
        st = ST.nas_status(root)
        if not st["ok"]:
            return 200, {"ok": False, "error": st["error"], "mounted": False,
                         "entries": [], "waiting": [], "waiting_count": 0}
        pend = ST.pending_entries(root, dates_with_photos=_photos_dates(home), write=False)
        return 200, {"ok": True, "mounted": True, "root": str(root), **pend}

    if path == "/api/schedules/lookup":
        date_str = q("date")
        if not date_str:
            return 400, {"error": "date required"}
        found = ST.lookup_for_date(root, date_str, photos_present=date_str in _photos_dates(home),
                                   write_manifest=False)
        return 200, found

    return 404, {"error": "unknown schedules endpoint"}


def handle_post(path: str, data: dict, home: Path) -> tuple[int, dict]:
    home = Path(home)
    root = _root(home)
    data = data or {}
    drafts = _drafts(home)

    if path == "/api/schedules/validate":
        meta = _meta_from(data)
        if not meta["timezone"] and meta["date"]:
            meta["timezone"] = ST.default_timezone(meta["date"])
        confirmed = None
        evdir = ST.event_dir(root, meta["date"], meta["event"]) if meta["date"] and meta["event"] else None
        if evdir and (evdir / "confirmed.json").is_file():
            confirmed = ST._read_json(evdir / "confirmed.json")
        val = ST.validate_event({**meta, "schedules_root": str(root)}, data.get("rows") or [], confirmed)
        return 200, {"ok": True, "validation": val, "schedule": ST.build_schedule(meta, data.get("rows") or []) if (data.get("rows") and meta["date"] and meta["event"]) else None}

    if path == "/api/schedules/draft":
        meta = _meta_from(data)
        if not meta["date"] or not meta["event"]:
            return 400, {"error": "date and event name required to save a draft"}
        path_out = ST.save_draft(meta, data.get("rows") or [], drafts_dir=drafts)
        return 200, {"ok": True, "path": str(path_out), "on_nas": False}

    if path == "/api/schedules/preview-paste":
        try:
            preview = ST.parse_paste(data.get("text") or data.get("paste") or "")
        except Exception as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True, "preview": preview}

    if path == "/api/schedules/review":
        meta = _meta_from(data)
        if not meta["timezone"] and meta["date"]:
            meta["timezone"] = ST.default_timezone(meta["date"])
        return 200, {"ok": True, **ST.review_summary(root, meta, data.get("rows") or [])}

    if path == "/api/schedules/confirm":
        if not data.get("confirm"):
            return 400, {"error": "Nothing was written. Send confirm=true after reviewing."}
        meta = _meta_from(data)
        if not meta["timezone"] and meta["date"]:
            meta["timezone"] = ST.default_timezone(meta["date"])
        result = ST.confirm_save(
            root, meta, data.get("rows") or [],
            ack_warnings=bool(data.get("ack_warnings")),
            expected_mtime=data.get("mtime"),
            force=bool(data.get("force")),
            drafts_dir=drafts,
            jobs_dir=home / "gui" / "jobs",
            history_jobs=home / "gui" / "history",
        )
        return (200 if result.get("ok") else 400), result

    if path == "/api/schedules/rollback":
        if not data.get("confirm"):
            return 400, {"error": "Send confirm=true to roll back. This writes a new version and does not delete history."}
        date_str, name = (data.get("date") or "").strip(), (data.get("event") or data.get("name") or "").strip()
        try:
            version = int(data.get("version"))
        except (TypeError, ValueError):
            return 400, {"error": "version required"}
        result = ST.rollback(root, date_str, name, version,
                             jobs_dir=home / "gui" / "jobs",
                             history_jobs=home / "gui" / "history")
        return (200 if result.get("ok") else 400), result

    if path == "/api/schedules/manifest/load-trip":
        if not data.get("confirm"):
            # preview only
            try:
                rows = data.get("rows") or ST.parse_paste(data.get("text") or data.get("paste") or "").get("rows")
                entries = ST.load_trip_entries(rows or [], timezone_fallback=data.get("timezone"))
            except Exception as e:
                return 400, {"error": str(e)}
            return 200, {"ok": True, "preview": True, "entries": entries, "count": len(entries)}
        try:
            rows = data.get("rows")
            if not rows and (data.get("text") or data.get("paste")):
                rows = ST.parse_paste(data.get("text") or data.get("paste")).get("rows")
            result = ST.confirm_load_trip(root, rows or [], timezone_fallback=data.get("timezone"))
        except Exception as e:
            return 400, {"error": str(e)}
        return (200 if result.get("ok") else 400), result

    if path == "/api/schedules/manifest/hold":
        try:
            entry = ST.set_manifest_state(root, data.get("id") or "", "held")
        except Exception as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True, "entry": entry}

    if path == "/api/schedules/manifest/activate":
        try:
            entry = ST.set_manifest_state(root, data.get("id") or "", "pending",
                                          activation=data.get("activation"))
            # Activate now: set activation to now in the entry's timezone/offset
            if not data.get("activation"):
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                # keep a +offset form
                entry = ST.set_manifest_state(root, data.get("id") or "", "pending", activation=now)
        except Exception as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True, "entry": entry}

    if path == "/api/schedules/manifest/remove":
        try:
            result = ST.remove_manifest_entry(root, data.get("id") or "")
        except Exception as e:
            return 400, {"error": str(e)}
        return 200, result

    return 404, {"error": "unknown schedules endpoint"}
