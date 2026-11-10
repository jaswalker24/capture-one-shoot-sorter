#!/usr/bin/env python3
"""Unit tests for auto-queue last-run cutoff / auto-trigger (no Capture One, no NAS)."""
from __future__ import annotations
import json, os, sys, tempfile, time, unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "gui"))
import autoqueue as AQ  # noqa: E402


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").timestamp()


class AutoqueueCutoff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aq-"))
        self.imports = self.tmp / "imports"
        self.gui = self.tmp / "gui"
        self.jobs = self.gui / "jobs"
        self.hist = self.gui / "history"
        for d in (self.imports / "2026", self.gui, self.jobs, self.hist):
            d.mkdir(parents=True)
        # Point the module at the temp tree (never the live ~/localai files)
        self._orig = {k: getattr(AQ, k) for k in
                      ("CFG", "STATE", "IMPORTS", "GUI", "HISTORY_DIRS", "HOME")}
        AQ.GUI = self.gui
        AQ.CFG = self.gui / "autoqueue.json"
        AQ.STATE = self.gui / "autoqueue-state.json"
        AQ.IMPORTS = self.imports
        AQ.HISTORY_DIRS = (self.jobs, self.hist)
        AQ.HOME = self.tmp
        self.births = {}
        self._real_birth = AQ._birth
        AQ._birth = lambda p: self.births.get(p, self.births.get(p.name, self._real_birth(p)))

    def tearDown(self):
        AQ._birth = self._real_birth
        for k, v in self._orig.items():
            setattr(AQ, k, v)

    def _folder(self, name, appeared, images=1, year="2026"):
        f = self.imports / year / name
        f.mkdir(parents=True, exist_ok=True)
        self.births[f] = appeared
        for i in range(images):
            p = f / f"IMG_{i:04d}.CR3"
            p.write_bytes(b"raw")
            os.utime(p, (appeared, appeared))
            self.births[p] = appeared
        return f

    def _cfg(self, **kw):
        cfg = {**AQ.DEFAULTS, "cutoff": "2026-11-10T08:00", **kw}
        AQ.save_cfg(cfg)
        return cfg

    def test_parse_and_fmt(self):
        t = AQ.parse_cutoff("2026-11-10T08:00")
        self.assertEqual(AQ.fmt_cutoff(t), "2026-11-10T08:00")
        self.assertEqual(AQ.parse_cutoff("2026-11-10 08:00:00"), t)
        self.assertIsNone(AQ.parse_cutoff(""))
        self.assertIsNone(AQ.parse_cutoff("nope"))

    def test_later_date_is_newer(self):
        cut = _ts("2026-11-10T08:00")
        f = self._folder("2026-11-11", _ts("2026-11-11T09:00"))
        self.assertTrue(AQ.is_newer(f, cut))
        self.assertIn("after cutoff day", AQ.newer_reason(f, cut))

    def test_older_date_not_newer_even_if_copied_late(self):
        cut = _ts("2026-11-10T08:00")
        f = self._folder("2026-11-07_BrandsHatch", _ts("2026-11-10T18:00"))
        self.assertFalse(AQ.is_newer(f, cut))

    def test_same_day_created_after_cutoff(self):
        cut = _ts("2026-11-10T08:00")
        f = self._folder("2026-11-10", _ts("2026-11-10T15:30"))
        self.assertTrue(AQ.is_newer(f, cut))
        self.assertIn("same-day", AQ.newer_reason(f, cut))

    def test_same_day_already_there_not_newer(self):
        cut = _ts("2026-11-10T08:00")
        f = self._folder("2026-11-10", _ts("2026-11-10T07:00"))
        self.assertFalse(AQ.is_newer(f, cut))

    def test_same_day_more_files_later_does_not_reopen(self):
        # Folder existed before cutoff; a file arriving later must not make it "new".
        cut = _ts("2026-11-10T08:00")
        f = self._folder("2026-11-10", _ts("2026-11-10T07:00"), images=1)
        late = f / "IMG_9999.CR3"
        late.write_bytes(b"raw")
        os.utime(late, (_ts("2026-11-10T18:00"), _ts("2026-11-10T18:00")))
        self.births[late] = _ts("2026-11-10T18:00")
        self.assertFalse(AQ.is_newer(f, cut))

    def test_ensure_cutoff_initialises_to_now(self):
        now = _ts("2026-11-10T08:05")
        self.assertEqual(AQ.ensure_cutoff(now), "2026-11-10T08:05")
        # second call leaves it alone
        self.assertEqual(AQ.ensure_cutoff(_ts("2026-11-10T18:00")), "2026-11-10T08:05")

    def test_preview_lists_only_newer(self):
        self._cfg(cutoff="2026-11-10T08:00")
        old = self._folder("2026-11-07_BrandsHatch", _ts("2026-11-07T09:00"))
        same_early = self._folder("2026-11-10-morning", _ts("2026-11-10T07:30"))
        same_late = self._folder("2026-11-10-evening", _ts("2026-11-10T15:00"))
        nxt = self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        names = {p["folder"] for p in AQ.preview()}
        self.assertEqual(names, {"2026-11-10-evening", "2026-11-12"})
        self.assertNotIn(old.name, names)
        self.assertNotIn(same_early.name, names)

    def test_preview_skips_ran_and_existing_jobs(self):
        self._cfg(cutoff="2026-11-10T08:00")
        a = self._folder("2026-11-11", _ts("2026-11-11T10:00"))
        b = self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        AQ._save_state({"baseline": [], "seen": {}, "queued": {}, "ran": {"2026-11-11": {"job": "x", "result": "ok"}}, "events": []})
        names = {p["folder"] for p in AQ.preview(lambda: {str(b)})}
        self.assertEqual(names, set())

    def test_poll_requires_stable_and_c1(self):
        self._cfg(enabled=True, cutoff="2026-11-10T08:00", poll_min=1, stable_min=20)
        f = self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        # freeze file mtimes in the past so "newest" can be old once the signature is stable
        for p in f.iterdir():
            os.utime(p, (_ts("2026-11-12T10:00"), _ts("2026-11-12T10:00")))
        queued = []
        def enqueue(folder, cfg):
            queued.append(folder.name); return "job-1"
        c1 = {"on": False}
        t0 = _ts("2026-11-12T11:00")
        r1 = AQ.poll(enqueue, lambda: set(), lambda: None, force=True, now=t0)
        self.assertEqual(r1.get("queued"), [])
        self.assertEqual(queued, [])
        # second look, still not 20 min old and C1 off
        r2 = AQ.poll(enqueue, lambda: set(), lambda: None, force=True, now=t0 + 60)
        self.assertEqual(r2.get("queued"), [])
        # 21 minutes later, stable, but C1 still false
        r3 = AQ.poll(enqueue, lambda: set(), lambda: False, force=True, now=t0 + 21 * 60)
        # monkeypatch C1
        # The poll above already ran with c1 false. Do it with a stub.
        self.assertEqual(queued, [])
        orig = AQ.c1_import_status
        AQ.c1_import_status = lambda folder: False
        r3 = AQ.poll(enqueue, lambda: set(), lambda: None, force=True, now=t0 + 22 * 60)
        self.assertEqual(queued, [])
        AQ.c1_import_status = lambda folder: True
        r4 = AQ.poll(enqueue, lambda: set(), lambda: None, force=True, now=t0 + 23 * 60)
        self.assertEqual(queued, ["2026-11-12"])
        AQ.c1_import_status = orig

    def test_poll_off_does_nothing(self):
        self._cfg(enabled=False, cutoff="2026-11-10T08:00")
        self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        r = AQ.poll(lambda *a: "x", lambda: set(), lambda: None, force=True)
        self.assertEqual(r, {"skipped": "off"})

    def test_settle_advances_cutoff_and_keeps_midrun_folder(self):
        self._cfg(cutoff="2026-11-10T08:00")
        # A finished at queue-time 11:00; B appeared at 11:30 (after A was queued, before A finished)
        a = self._folder("2026-11-11", _ts("2026-11-11T10:00"))
        b = self._folder("2026-11-11-late", _ts("2026-11-11T11:30"))
        job = {"id": "job-a", "status": "done", "created": "2026-11-11T11:00:00",
               "finished": "2026-11-11T13:00:00", "verify": {"rating_or_colour_changed": 0}}
        (self.hist / "job-a.json").write_text(json.dumps(job))
        st = {"baseline": [], "seen": {}, "queued": {"2026-11-11": "job-a"}, "ran": {}, "events": []}
        moved = AQ.settle_finished(st)
        self.assertEqual(moved, ["2026-11-11"])
        self.assertEqual(AQ.load_cfg()["cutoff"], "2026-11-11T11:00")
        self.assertEqual(st["ran"]["2026-11-11"]["result"], "ok")
        names = {p["folder"] for p in AQ.preview()}
        self.assertIn("2026-11-11-late", names)
        self.assertNotIn("2026-11-11", names)

    def test_failed_job_does_not_advance_or_requeue(self):
        self._cfg(cutoff="2026-11-10T08:00")
        self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        job = {"id": "job-f", "status": "error", "created": "2026-11-12T11:00:00"}
        (self.hist / "job-f.json").write_text(json.dumps(job))
        st = {"baseline": [], "seen": {}, "queued": {"2026-11-12": "job-f"}, "ran": {}, "events": []}
        moved = AQ.settle_finished(st)
        AQ._save_state(st)
        self.assertEqual(moved, [])
        self.assertEqual(AQ.load_cfg()["cutoff"], "2026-11-10T08:00")
        self.assertEqual(st["ran"]["2026-11-12"]["result"], "failed")
        self.assertEqual(AQ.preview(), [])

    def test_preview_follows_approved_apply_job(self):
        self._cfg(cutoff="2026-11-10T08:00")
        prev = {"id": "job-p", "status": "done", "created": "2026-11-12T11:00:00",
                "approved_job": "job-apply"}
        apply = {"id": "job-apply", "status": "done", "created": "2026-11-12T12:00:00",
                 "verify": {"rating_or_colour_changed": 0}}
        (self.hist / "job-p.json").write_text(json.dumps(prev))
        (self.jobs / "job-apply.json").write_text(json.dumps(apply))
        st = {"baseline": [], "seen": {}, "queued": {"2026-11-12": "job-p"}, "ran": {}, "events": []}
        self._folder("2026-11-12", _ts("2026-11-12T10:00"))
        moved = AQ.settle_finished(st)
        self.assertEqual(moved, ["2026-11-12"])
        self.assertEqual(AQ.load_cfg()["cutoff"], "2026-11-12T11:00")

    def test_default_stays_off(self):
        self.assertFalse(AQ.DEFAULTS["enabled"])
        self.assertFalse(AQ.load_cfg()["enabled"])

    def test_mp4_ignored_in_signature(self):
        f = self._folder("2026-11-12", _ts("2026-11-12T10:00"), images=0)
        (f / "clip.MP4").write_bytes(b"mov")
        (f / "IMG_0001.CR3").write_bytes(b"raw")
        sig = AQ.signature(f)
        self.assertEqual(sig["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
