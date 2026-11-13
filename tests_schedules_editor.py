#!/usr/bin/env python3
"""Tests for the Schedules tab: editor, history, manifest, multi-schedule, sanitiser.

Uses temp directories only. No NAS, no Capture One, no live ~/localai writes
except a throwaway drafts dir inside the temp tree.
"""
from __future__ import annotations

import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gui"))

import schedules_tree as ST  # noqa: E402
import schedule_tag as TAG  # noqa: E402


def _rows(*acts):
    """acts: (act, start, end, doors='', notes='')"""
    out = []
    for item in acts:
        act, start, end = item[0], item[1], item[2]
        doors = item[3] if len(item) > 3 else ""
        notes = item[4] if len(item) > 4 else ""
        out.append({"act": act, "start": start, "end": end, "doors": doors, "notes": notes})
    return out


def _meta(date="2026-11-22", event="Example Hall Show", venue="Example Hall", **kw):
    d = {"date": date, "event": event, "venue": venue,
         "timezone": kw.pop("timezone", ST.default_timezone(date)),
         "changeover_buffer_min": kw.pop("changeover_buffer_min", 3),
         "event_type": kw.pop("event_type", "gig"),
         "source": kw.pop("source", "manual")}
    d.update(kw)
    return d


class SanitiserAndTime(unittest.TestCase):
    def test_sanitize_replaces_illegal_keeps_japanese(self):
        cleaned = ST.sanitize_event_name('Example Hall / City: "Night"?')
        self.assertNotRegex(cleaned, r'[\\/:*?"<>|]')
        self.assertIn("Example Hall", cleaned)
        self.assertIn("Osaka", cleaned)
        self.assertIn("Night", cleaned)
        self.assertFalse(cleaned.endswith("-"))
        self.assertEqual(ST.sanitize_event_name("  ヘップホール  "), "ヘップホール")
        self.assertEqual(ST.sanitize_event_name("..."), "Untitled event")
        self.assertEqual(ST.sanitize_event_name(""), "Untitled event")
        self.assertEqual(ST.sanitize_event_name("A" * 300)[:3], "AAA")
        self.assertLessEqual(len(ST.sanitize_event_name("A" * 300).encode()), 240)

    def test_parse_time_accepts_compact_and_colon(self):
        self.assertEqual(ST.parse_time("1930"), "19:30")
        self.assertEqual(ST.parse_time("19:30"), "19:30")
        self.assertEqual(ST.parse_time("19.30"), "19:30")
        self.assertEqual(ST.parse_time("930"), "09:30")
        self.assertEqual(ST.parse_time("9:05"), "09:05")
        self.assertIsNone(ST.parse_time("25:00"))
        self.assertIsNone(ST.parse_time("abc"))
        self.assertIsNone(ST.parse_time(""))

    def test_default_timezone_japan_trip_window(self):
        self.assertEqual(ST.default_timezone("2026-11-19"), "Asia/Tokyo")
        self.assertEqual(ST.default_timezone("2026-11-28"), "Asia/Tokyo")
        self.assertEqual(ST.default_timezone("2026-11-22"), "Asia/Tokyo")
        self.assertEqual(ST.default_timezone("2026-11-18"), "Europe/London")
        self.assertEqual(ST.default_timezone("2026-06-23"), "Europe/London")


class Validation(unittest.TestCase):
    def test_red_blank_act_and_end_equals_start(self):
        v = ST.validate_event(_meta(), _rows(("", "19:00", "19:30"), ("Act", "19:30", "19:30")))
        codes = {e["code"] for e in v["errors"]}
        self.assertIn("blank_act", codes)
        self.assertIn("end_not_after", codes)
        self.assertFalse(v["can_save"])

    def test_red_incomplete_times(self):
        v = ST.validate_event(_meta(), _rows(("Act", "19:00", "")))
        self.assertTrue(any(e["code"] == "incomplete_times" for e in v["errors"]))
        self.assertFalse(v["can_save"])

    def test_amber_midnight_overlap_gap_doors_duplicate(self):
        rows = _rows(
            ("Act One", "19:00", "20:00", "20:30"),  # doors after first start
            ("Act Two", "19:30", "20:30"),            # overlap
            ("Act Two", "19:30", "20:30"),            # duplicate
            ("Late", "23:30", "00:15"),               # midnight
            ("Encore", "02:00", "02:30"),             # gap after midnight end 00:15 is 105 min
        )
        v = ST.validate_event(_meta(), rows)
        self.assertTrue(v["can_save"])
        self.assertTrue(v["needs_ack"])
        codes = {w["code"] for w in v["warnings"]}
        self.assertIn("midnight", codes)
        self.assertIn("overlap", codes)
        self.assertIn("duplicate", codes)
        self.assertIn("doors_after_start", codes)
        self.assertIn("gap", codes)

    def test_tba_row_without_times_is_ok(self):
        v = ST.validate_event(_meta(), _rows(("Act One", "19:00", "19:40"), ("Act TBA", "", "")))
        self.assertTrue(v["can_save"])
        self.assertFalse(any(e["code"] == "blank_act" for e in v["errors"]))
        built = ST.build_schedule(_meta(), _rows(("Act One", "19:00", "19:40"), ("Act TBA", "", "")))
        ev = built["events"][0]
        self.assertEqual(ev["acts_tba"], ["Act TBA"])
        self.assertEqual(len(ev["slots"]), 1)

    def test_clash_with_confirmed(self):
        confirmed = ST.build_schedule(_meta(), _rows(
            ("Act One", "19:00", "19:40"),
            ("Act Two", "19:50", "20:30"),
        ))
        # 4-minute drift is a match; missing act and a new act clash
        close = _rows(("Act One", "19:04", "19:44"), ("Act Three", "21:00", "21:30"))
        v = ST.validate_event(_meta(), close, confirmed=confirmed)
        self.assertTrue(v["can_save"])
        msgs = " ".join(w["message"] for w in v["warnings"] if w["code"] == "clash")
        self.assertIn("Act Two", msgs)
        self.assertIn("Act Three", msgs)

    def test_clash_within_five_minutes_is_not_a_clash(self):
        confirmed = ST.build_schedule(_meta(), _rows(("Act One", "19:00", "19:40")))
        v = ST.validate_event(_meta(), _rows(("Act One", "19:05", "19:45")), confirmed=confirmed)
        self.assertFalse(any(w["code"] == "clash" for w in v["warnings"]))


class MidnightCrossing(unittest.TestCase):
    def test_load_schedule_rolls_end_to_next_day(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            data = ST.build_schedule(_meta(), _rows(
                ("Opener", "22:00", "22:40"),
                ("Headliner", "23:30", "00:15"),
            ))
            p.write_text(json.dumps(data), encoding="utf-8")
            sched, tz = TAG.load_schedule(str(p))
            ev = sched["events"][0]
            self.assertEqual(ev["_end"].date().isoformat(), "2026-11-23")
            head = ev["slots"][1]
            self.assertEqual(head["_s"].isoformat(), "2026-11-22T23:30:00+09:00")
            self.assertEqual(head["_e"].isoformat(), "2026-11-23T00:15:00+09:00")
            mid = datetime.fromisoformat("2026-11-23T00:05:00+09:00")
            m = TAG.match(mid, (sched, tz), tz)
            self.assertIn("Gig/Act/Headliner", m["keywords"])
            after = datetime.fromisoformat("2026-11-23T00:20:00+09:00")
            m2 = TAG.match(after, (sched, tz), tz)
            self.assertNotIn("Gig/Act/Headliner", m2["keywords"])


class HistoryAndRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Schedules"
        self.drafts = Path(self.tmp.name) / "drafts"
        self.jobs = Path(self.tmp.name) / "jobs"
        self.jobs.mkdir()
        self.histjobs = Path(self.tmp.name) / "histjobs"
        self.histjobs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, rows, **kw):
        return ST.confirm_save(self.root, _meta(**kw), rows, ack_warnings=True,
                               drafts_dir=self.drafts, jobs_dir=self.jobs, history_jobs=self.histjobs)

    def test_confirm_writes_history_and_rollback_appends(self):
        r1 = self._save(_rows(("Act One", "19:00", "19:40"), ("Act Two", "19:50", "20:30")))
        self.assertTrue(r1["ok"], r1)
        evdir = Path(r1["folder"])
        self.assertTrue((evdir / "schedule.json").is_file())
        self.assertTrue((evdir / "history.json").is_file())
        versions = ST.history_list(evdir)["versions"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["source"], "manual")
        r2 = self._save(_rows(("Act One", "19:00", "19:40"), ("Act Two", "19:55", "20:40")))
        self.assertTrue(r2["ok"])
        versions = ST.history_list(evdir)["versions"]
        self.assertEqual(len(versions), 2)
        self.assertIn("Act Two", versions[0]["summary"])  # newest first
        # rollback to v1
        rb = ST.rollback(self.root, "2026-11-22", "Example Hall Show", 1,
                         jobs_dir=self.jobs, history_jobs=self.histjobs)
        self.assertTrue(rb["ok"], rb)
        self.assertEqual(rb["history"]["source"], "rollback")
        current = json.loads((evdir / "schedule.json").read_text())
        self.assertEqual(current["events"][0]["slots"][1]["start"], "19:50")
        versions = ST.history_list(evdir)["versions"]
        self.assertEqual(len(versions), 3)
        self.assertEqual(versions[0]["source"], "rollback")
        # nothing deleted
        hist_files = list((evdir / ".history").glob("v*.json"))
        self.assertEqual(len(hist_files), 3)

    def test_rollback_warns_if_keywords_already_applied(self):
        r1 = self._save(_rows(("Act One", "19:00", "19:40")))
        job = {
            "id": "job-1",
            "schedule_path": r1["path"],
            "added_keywords": {"a.jpg": ["Gig/Act/Act One"]},
            "status": "done",
            "folder_name": "2026-11-22",
        }
        (self.jobs / "job-1.json").write_text(json.dumps(job))
        self._save(_rows(("Act One", "19:10", "19:50")))
        rb = ST.rollback(self.root, "2026-11-22", "Example Hall Show", 1,
                         jobs_dir=self.jobs, history_jobs=self.histjobs)
        self.assertTrue(rb["applied_jobs"])
        self.assertIn("does not remove keywords", rb["warning"])

    def test_mtime_check_and_nas_unmounted_keeps_draft(self):
        r1 = self._save(_rows(("Act One", "19:00", "19:40")))
        stale = r1["mtime"] - 10
        r2 = ST.confirm_save(self.root, _meta(), _rows(("Act One", "19:00", "19:50")),
                             ack_warnings=True, expected_mtime=stale,
                             drafts_dir=self.drafts)
        self.assertFalse(r2["ok"])
        self.assertTrue(r2.get("changed"))
        # unmounted volume: use a path under a fake /Volumes name that cannot exist...
        # We cannot unmount, so simulate via a root whose volume_for is None but we
        # monkeypatch nas_status through a root on a missing volume path that
        # volume_for recognises. /Volumes/no-such-volume-localai-test
        missing = Path("/Volumes/no-such-volume-localai-test/Schedules")
        if ST.volume_for(missing) is not None and not ST.volume_for(missing).exists():
            d = ST.confirm_save(missing, _meta(), _rows(("Act One", "19:00", "19:40")),
                                ack_warnings=True, drafts_dir=self.drafts)
            self.assertFalse(d["ok"])
            self.assertTrue(d.get("kept_draft"))
            self.assertIsNotNone(ST.load_draft("2026-11-22", "Example Hall Show", drafts_dir=self.drafts))

    def test_history_diff_marks_changed_rows(self):
        self._save(_rows(("Act One", "19:00", "19:40")))
        self._save(_rows(("Act One", "19:00", "19:50"), ("Act Two", "20:00", "20:30")))
        evdir = ST.event_dir(self.root, "2026-11-22", "Example Hall Show")
        diff = ST.history_diff(evdir, 1, 2)
        statuses = {row["status"] for row in diff["rows"]}
        self.assertIn("changed", statuses)
        self.assertIn("added", statuses)


class ManifestAndTwoFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Schedules"
        self.root.mkdir()
        self.drafts = Path(self.tmp.name) / "drafts"

    def tearDown(self):
        self.tmp.cleanup()

    def _event(self, date="2026-11-22", event="Example Hall Show", rows=None):
        rows = rows or _rows(("Act One", "19:00", "19:40"), ("Act Two", "19:50", "20:30"))
        return ST.confirm_save(self.root, _meta(date=date, event=event), rows,
                               ack_warnings=True, drafts_dir=self.drafts)

    def test_load_trip_and_state_machine(self):
        self._event()
        trip = ST.confirm_load_trip(self.root, [{
            "date": "2026-11-22", "event": "Example Hall Show", "venue": "Example Hall",
            "activation": "2026-11-22T19:00:00+09:00",
        }])
        self.assertTrue(trip["ok"])
        self.assertEqual(trip["added"], 1)
        entry = trip["entries"][0]
        self.assertEqual(entry["state"], "pending")
        self.assertTrue(entry["activation"].endswith("+09:00") or "+09:00" in entry["activation"])
        self.assertEqual(entry["apply_until"], "2026-12-06T19:00:00+09:00")

        before = datetime.fromisoformat("2026-11-22T10:00:00+09:00")
        tick = ST.tick_manifest(self.root, now=before, dates_with_photos=[], write=True)
        self.assertEqual(tick["entries"][0]["state"], "pending")

        after_open = datetime.fromisoformat("2026-11-22T19:05:00+09:00")
        tick = ST.tick_manifest(self.root, now=after_open, dates_with_photos=[], write=True)
        self.assertEqual([c["to"] for c in tick["changed"]], ["waiting"])
        self.assertEqual(tick["entries"][0]["state"], "waiting")

        late = datetime.fromisoformat("2026-12-07T19:01:00+09:00")
        tick = ST.tick_manifest(self.root, now=late, dates_with_photos=[], write=True)
        self.assertEqual(tick["entries"][0]["state"], "skipped")

    def test_lookup_two_folders_same_date_and_late_photos(self):
        self._event()
        ST.confirm_load_trip(self.root, [{
            "date": "2026-11-22", "event": "Example Hall Show", "venue": "Example Hall",
            "activation": "2026-11-22T18:00:00+09:00",
        }])
        now = datetime.fromisoformat("2026-11-22T18:05:00+09:00")
        # activation passed, no folder yet
        none = ST.lookup_for_date(self.root, "2026-11-22", now=now, photos_present=False, write_manifest=True)
        # schedule.json is still listed from the tree; state should be waiting
        man = ST.load_manifest(self.root)
        self.assertEqual(man["entries"][0]["state"], "waiting")

        # folder arrives later (even days later)
        later = datetime.fromisoformat("2026-11-25T12:00:00+09:00")
        a = ST.lookup_for_import_folder(self.root, Path("/tmp/imports/2026/2026-11-22"), now=later, write_manifest=True)
        b = ST.lookup_for_import_folder(self.root, Path("/tmp/imports/2026/2026-11-22-card2"), now=later, write_manifest=True)
        self.assertEqual(a["paths"], b["paths"])
        self.assertTrue(a["paths"])
        self.assertTrue(a["paths"][0].endswith("schedule.json"))
        self.assertEqual(ST.load_manifest(self.root)["entries"][0]["state"], "applied")

        # past apply_until with no photos would skip — already applied stays applied
        late = datetime.fromisoformat("2026-12-20T00:00:00+09:00")
        ST.tick_manifest(self.root, now=late, dates_with_photos=[], write=True)
        self.assertEqual(ST.load_manifest(self.root)["entries"][0]["state"], "applied")

    def test_held_and_remove_do_not_delete_files(self):
        saved = self._event()
        ST.confirm_load_trip(self.root, [{"date": "2026-11-22", "event": "Example Hall Show",
                                         "activation": "2026-11-22T18:00:00+09:00"}])
        eid = ST.load_manifest(self.root)["entries"][0]["id"]
        ST.set_manifest_state(self.root, eid, "held")
        now = datetime.fromisoformat("2026-11-22T19:00:00+09:00")
        found = ST.lookup_for_date(self.root, "2026-11-22", now=now, photos_present=True, write_manifest=True)
        # tree file is still used; held only excludes the manifest-driven apply flag
        self.assertTrue(found["paths"])  # schedule.json still present
        # but held entry is not in applying list
        self.assertFalse(found["entries"])
        removed = ST.remove_manifest_entry(self.root, eid)
        self.assertFalse(removed["deleted_files"])
        self.assertTrue(Path(saved["path"]).is_file())
        self.assertEqual(ST.load_manifest(self.root)["entries"], [])

    def test_folder_name_suffix_still_matches_date(self):
        self.assertEqual(ST.import_folder_date("2026-11-22"), "2026-11-22")
        self.assertEqual(ST.import_folder_date("/photos/2026/2026-11-22-card2"), "2026-11-22")
        self.assertIsNone(ST.import_folder_date("Example Hall"))


class MultiSchedulePass(unittest.TestCase):
    def test_merge_and_match_two_events_same_day(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a = ST.build_schedule(_meta(event="Example Hall Show"), _rows(("Act One", "17:00", "17:40")))
            b = ST.build_schedule(_meta(event="Evening Bill", event_id="evening"),
                                  _rows(("Act Two", "19:00", "19:40")))
            # fix id for second
            b["events"][0]["id"] = "evening-bill"
            pa, pb = d / "a.json", d / "b.json"
            pa.write_text(json.dumps(a), encoding="utf-8")
            pb.write_text(json.dumps(b), encoding="utf-8")
            merged = ST.merge_schedule_files([pa, pb])
            self.assertEqual(len(merged["events"]), 2)
            dest = ST.prepared_schedule_path([pa, pb], work_dir=d / "work")
            self.assertIsNotNone(dest)
            self.assertNotEqual(dest, pa)
            sched, tz = TAG.load_schedule(str(dest))
            t1 = datetime.fromisoformat("2026-11-22T17:10:00+09:00")
            t2 = datetime.fromisoformat("2026-11-22T19:10:00+09:00")
            m1, m2 = TAG.match(t1, (sched, tz), tz), TAG.match(t2, (sched, tz), tz)
            self.assertIn("Gig/Event/Example Hall Show", m1["keywords"])
            self.assertIn("Gig/Act/Act One", m1["keywords"])
            self.assertIn("Gig/Event/Evening Bill", m2["keywords"])
            self.assertIn("Gig/Act/Act Two", m2["keywords"])

    def test_resolve_and_skip_when_none(self):
        self.assertEqual(ST.resolve_job_schedule_paths({"schedule_path": ""}), [])
        self.assertIsNone(ST.prepared_schedule_path([]))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "one.json"
            p.write_text(json.dumps(ST.build_schedule(_meta(), _rows(("A", "19:00", "19:30")))), encoding="utf-8")
            self.assertEqual(ST.prepared_schedule_path([p, Path(d) / "missing.json"]), p)
            paths = ST.resolve_job_schedule_paths({"schedule_path": str(p), "schedule_paths": [str(p)]})
            self.assertEqual(paths, [p])

    def test_jobrunner_helper_skip_matches_v104(self):
        # The runner must still skip when no file is chosen.
        self.assertEqual(ST.resolve_job_schedule_paths({}), [])
        # and jobrunner still has the skip log for an empty path
        text = (ROOT / "jobrunner.py").read_text()
        self.assertIn('schedule skipped: no schedule file chosen', text)


class DraftsAndPaste(unittest.TestCase):
    def test_draft_roundtrip_and_paste_preview(self):
        with tempfile.TemporaryDirectory() as d:
            drafts = Path(d)
            path = ST.save_draft(_meta(), _rows(("Act One", "1930", "20:00")), drafts_dir=drafts)
            self.assertTrue(str(path).startswith(str(drafts)))
            loaded = ST.load_draft("2026-11-22", "Example Hall Show", drafts_dir=drafts)
            self.assertEqual(loaded["rows"][0]["act"], "Act One")
            preview = ST.parse_paste("act\tstart\tend\nAct One\t1930\t20:00\nAct Two\t20:10\t20:40")
            self.assertTrue(preview["has_header"])
            self.assertEqual(preview["rows"][0]["start"], "19:30")
            self.assertEqual(preview["count"], 2)
            preview2 = ST.parse_paste("Opener\t18:00\t18:30")
            self.assertFalse(preview2["has_header"])
            self.assertEqual(preview2["rows"][0]["act"], "Opener")

    def test_build_schedule_source_and_load_schedule(self):
        data = ST.build_schedule(_meta(source="manual"), _rows(("Act One", "19:00", "19:40", "18:30")))
        self.assertEqual(data["source"], "manual")
        self.assertEqual(data["timezone"], "Asia/Tokyo")
        ev = data["events"][0]
        self.assertEqual(ev["doors"], "18:30")
        self.assertEqual(ev["start"], "19:00")
        self.assertEqual(ev["status"], "confirmed")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            sched, tz = TAG.load_schedule(str(p))
            t = datetime.fromisoformat("2026-11-22T19:10:00+09:00")
            m = TAG.match(t, (sched, tz), tz)
            self.assertIn("Gig/Act/Act One", m["keywords"])


if __name__ == "__main__":
    unittest.main()
