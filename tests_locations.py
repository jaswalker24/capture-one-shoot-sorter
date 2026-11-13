#!/usr/bin/env python3
"""Locations (NAS path) preview, save-with-backup, and Other Exports root.

No Capture One, no NAS, no live ~/localai writes.
"""
from __future__ import annotations
import json, os, shutil, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import nas_paths as NP
import rank_exports as R


class Preview(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="locprev-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "2025").mkdir()
        (self.tmp / "2026").mkdir()
        (self.tmp / "readme.txt").write_text("x")
        nested = self.tmp / "2026" / "2026-01-02"
        nested.mkdir()
        (nested / "IMG_0001.CR3").write_bytes(b"raw")
        (nested / "more").mkdir()
        (nested / "more" / "deep.jpg").write_bytes(b"y")

    def test_shallow_counts_only(self):
        prev = NP.preview_path(str(self.tmp))
        self.assertTrue(prev["readable"])
        self.assertEqual(prev["dirs"], 2)
        self.assertEqual(prev["files"], 1)
        self.assertIn("2025", prev["sample_dirs"])
        self.assertIn("year folders: 2025, 2026", prev["summary"])
        self.assertIn("not a recursive", prev["summary"])
        # nested RAW and deep.jpg must not be counted
        self.assertEqual(prev["files"], 1)

    def test_missing(self):
        prev = NP.preview_path(str(self.tmp / "nope"))
        self.assertFalse(prev["exists"])
        self.assertIn("does not exist", prev["error"])

    def test_not_a_folder(self):
        prev = NP.preview_path(str(self.tmp / "readme.txt"))
        self.assertTrue(prev["exists"])
        self.assertFalse(prev["is_dir"])
        self.assertIn("Not a folder", prev["error"])

    def test_empty(self):
        prev = NP.preview_path("  ")
        self.assertEqual(prev["error"], "No path entered")


class SaveLocations(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="locsave-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Photos" / "Camera Imports" / "2026").mkdir(parents=True)
        (self.tmp / "Photos" / "Camera Exports" / "2026-01-02").mkdir(parents=True)
        (self.tmp / "Photos" / "2025 Exports" / "2025-Hakone").mkdir(parents=True)
        self.cfg = self.tmp / "config.json"
        self.cfg.write_text(json.dumps({
            "imports_root": "/old/imports",
            "exports_root": "/old/exports",
            "rank": {"weights": {"aes": 0.15}},
            "keyword_library": "Custom Keywords",
        }, indent=2))

    def test_save_backs_up_and_keeps_other_keys(self):
        photos = self.tmp / "Photos"
        out = NP.save_locations({
            "photos_root": str(photos),
            "imports_root": str(photos / "Camera Imports"),
            "exports_root": str(photos / "Camera Exports"),
            "other_exports_root": str(photos / "2025 Exports"),
        }, home=self.tmp)
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertTrue(out["backup"])
        bak = Path(out["backup"])
        self.assertTrue(bak.is_file())
        self.assertIn("config.json.bak-locations-", bak.name)
        old = json.loads(bak.read_text())
        self.assertEqual(old["imports_root"], "/old/imports")
        new = json.loads(self.cfg.read_text())
        self.assertEqual(new["imports_root"], str(photos / "Camera Imports"))
        self.assertEqual(new["photos_root"], str(photos))
        self.assertEqual(new["other_exports_root"], str(photos / "2025 Exports"))
        self.assertEqual(new["keyword_library"], "Custom Keywords")
        self.assertEqual(new["rank"]["weights"]["aes"], 0.15)
        # no second backup when unchanged
        out2 = NP.save_locations({
            "photos_root": str(photos),
            "imports_root": str(photos / "Camera Imports"),
            "exports_root": str(photos / "Camera Exports"),
            "other_exports_root": str(photos / "2025 Exports"),
        }, home=self.tmp)
        self.assertFalse(out2["changed"])
        self.assertIsNone(out2["backup"])
        backups = list(self.tmp.glob("config.json.bak-locations-*"))
        self.assertEqual(len(backups), 1)

    def test_refuse_missing_required(self):
        with self.assertRaises(ValueError) as cm:
            NP.save_locations({"imports_root": str(self.tmp / "missing")}, home=self.tmp)
        self.assertIn("does not exist", str(cm.exception))
        # original config preserved
        cfg = json.loads(self.cfg.read_text())
        self.assertEqual(cfg["imports_root"], "/old/imports")

    def test_unknown_key_refused(self):
        with self.assertRaises(ValueError):
            NP.save_locations({"catalog_root": str(self.tmp)}, home=self.tmp)

    def test_clear_optional(self):
        cfg = json.loads(self.cfg.read_text())
        cfg["other_exports_root"] = "/old/2025"
        cfg["exports_2025_root"] = "/legacy/2025"
        self.cfg.write_text(json.dumps(cfg, indent=2))
        # clearing optional must not require the path to exist, and drops the legacy key too
        out = NP.save_locations({"other_exports_root": ""}, home=self.tmp, require_readable=True)
        self.assertTrue(out["changed"])
        new = json.loads(self.cfg.read_text())
        self.assertNotIn("other_exports_root", new)
        self.assertNotIn("exports_2025_root", new)

    def test_trailing_slash_normalized(self):
        photos = self.tmp / "Photos"
        NP.save_locations({"imports_root": str(photos / "Camera Imports") + "/"}, home=self.tmp)
        new = json.loads(self.cfg.read_text())
        self.assertEqual(new["imports_root"], str(photos / "Camera Imports"))


class Resolvers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="locres-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Photos" / "Camera Imports").mkdir(parents=True)
        (self.tmp / "Photos" / "Camera Exports").mkdir(parents=True)
        (self.tmp / "Photos" / "2025 Exports").mkdir()
        (self.tmp / "config.json").write_text(json.dumps({
            "photos_root": str(self.tmp / "Photos"),
            "imports_root": str(self.tmp / "Photos" / "Camera Imports"),
            "exports_root": str(self.tmp / "Photos" / "Camera Exports"),
        }))

    def test_other_exports_derived_and_override(self):
        self.assertEqual(NP.other_exports_root(self.tmp), self.tmp / "Photos" / "2025 Exports")
        self.assertEqual(NP.exports_2025_root(self.tmp), self.tmp / "Photos" / "2025 Exports")
        custom = self.tmp / "custom-other"
        custom.mkdir()
        cfg = json.loads((self.tmp / "config.json").read_text())
        cfg["other_exports_root"] = str(custom)
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.assertEqual(NP.other_exports_root(self.tmp), custom)

    def test_legacy_exports_2025_root_fallback(self):
        custom = self.tmp / "legacy-2025"
        custom.mkdir()
        cfg = json.loads((self.tmp / "config.json").read_text())
        cfg["exports_2025_root"] = str(custom)
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.assertEqual(NP.other_exports_root(self.tmp), custom)

    def test_legacy_env_fallback(self):
        custom = self.tmp / "legacy-env"
        custom.mkdir()
        with mock.patch.dict(os.environ, {"LOCALAI_EXPORTS_2025": str(custom)}):
            self.assertEqual(NP.other_exports_root(self.tmp), custom)

    def test_rank_exports_honours_config(self):
        prev_home = R.HOME
        R.HOME = self.tmp
        self.addCleanup(lambda: setattr(R, "HOME", prev_home))
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("LOCALAI_PHOTOS", "LOCALAI_IMPORTS", "LOCALAI_EXPORTS",
                       "LOCALAI_EXPORTS_2025", "LOCALAI_OTHER_EXPORTS"):
                os.environ.pop(k, None)
            self.assertEqual(R.imports_root(), self.tmp / "Photos" / "Camera Imports")
            self.assertEqual(R.other_exports_root(), self.tmp / "Photos" / "2025 Exports")
            roots = R.list_export_roots()
            labels = {r["label"]: r for r in roots}
            self.assertIn("Other Exports", labels)
            self.assertNotIn("2025 Exports", labels)
            self.assertEqual(Path(labels["Other Exports"]["path"]), self.tmp / "Photos" / "2025 Exports")
            self.assertTrue(labels["Other Exports"]["available"])


class Browse(unittest.TestCase):
    def test_non_darwin_explains_paste(self):
        with mock.patch.object(NP.sys, "platform", "linux"):
            r = NP.choose_folder("Choose the Camera Imports folder")
        self.assertFalse(r["ok"])
        self.assertIn("Paste the path", r["error"])


class TrainingSources(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loctrain-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Photos" / "Camera Imports").mkdir(parents=True)
        (self.tmp / "Photos" / "Camera Exports").mkdir(parents=True)
        (self.tmp / "config.json").write_text(json.dumps({
            "imports_root": str(self.tmp / "Photos" / "Camera Imports"),
            "exports_root": str(self.tmp / "Photos" / "Camera Exports"),
        }))

    def test_defaults_export_roots_on(self):
        src = NP.training_sources(self.tmp)
        self.assertTrue(src["exports_root"])
        self.assertTrue(src["other_exports_root"])
        self.assertTrue(src["imports_root"])
        locs = NP.current_locations(self.tmp)
        by = {f["key"]: f for f in locs["fields"]}
        self.assertTrue(by["exports_root"]["training_source"])
        self.assertTrue(by["exports_root"]["training_enabled"])
        self.assertTrue(by["other_exports_root"]["training_enabled"])
        self.assertTrue(by["imports_root"]["training_enabled"])
        self.assertFalse(by["photos_root"]["training_source"])

    def test_save_training_sources_backs_up(self):
        out = NP.save_locations({}, home=self.tmp, training_sources_update={
            "exports_root": True,
            "other_exports_root": False,
            "imports_root": False,
        })
        self.assertTrue(out["changed"])
        self.assertTrue(out["backup"])
        cfg = json.loads((self.tmp / "config.json").read_text())
        self.assertEqual(cfg["training"]["sources"]["other_exports_root"], False)
        self.assertEqual(cfg["training"]["sources"]["imports_root"], False)
        self.assertEqual(NP.training_sources(self.tmp)["other_exports_root"], False)

    def test_legacy_training_source_key(self):
        cfg = json.loads((self.tmp / "config.json").read_text())
        cfg["training"] = {"sources": {"exports_2025_root": False, "exports_root": True}}
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.assertFalse(NP.training_sources(self.tmp)["other_exports_root"])
        self.assertTrue(NP.training_sources(self.tmp)["exports_root"])


class StarLabels(unittest.TestCase):
    def test_build_labels_can_ignore_stars(self):
        # Minimal synthetic rows: one exported, one 3-star only, one negative
        from unittest.mock import patch
        index = {"2026-01-02": [
            {"filename": "IMG_EXP.CR3", "sharp": 0.8, "aes": 0.5},
            {"filename": "IMG_STAR.CR3", "sharp": 0.7, "aes": 0.4},
            {"filename": "IMG_NEG.CR3", "sharp": 0.2, "aes": 0.2},
        ]}
        positives = {"2026-01-02": {"IMG_EXP"}}
        feats = [
            {"filename": "IMG_EXP.CR3", "sharp": 0.8, "aes": 0.5, "burst_best": 0, "burst_other": 0, "blur": 0},
            {"filename": "IMG_STAR.CR3", "sharp": 0.7, "aes": 0.4, "burst_best": 0, "burst_other": 0, "blur": 0},
            {"filename": "IMG_NEG.CR3", "sharp": 0.2, "aes": 0.2, "burst_best": 0, "burst_other": 0, "blur": 0},
        ]
        ratings = {"IMG_EXP": 0, "IMG_STAR": 5, "IMG_NEG": 0}
        with patch.object(R, "load_bursts", return_value={}), \
             patch.object(R, "quality_features", return_value=feats), \
             patch.object(R, "load_ratings", return_value=(ratings, None)):
            labels, summary = R.build_labels(index, positives, min_indexed=1, use_star_labels=True)
            recs = {Path(r["filename"]).stem: r for r in labels["2026-01-02"]}
            self.assertTrue(recs["IMG_EXP"]["y"])
            self.assertTrue(recs["IMG_STAR"]["y"])
            self.assertFalse(recs["IMG_NEG"]["y"])
            self.assertEqual(summary[0]["positives_star_only"], 1)
            labels2, summary2 = R.build_labels(index, positives, min_indexed=1, use_star_labels=False)
            recs2 = {Path(r["filename"]).stem: r for r in labels2["2026-01-02"]}
            self.assertTrue(recs2["IMG_EXP"]["y"])
            self.assertFalse(recs2["IMG_STAR"]["y"])
            self.assertFalse(recs2["IMG_NEG"]["y"])
            self.assertEqual(summary2[0]["positives_star_only"], 0)


class ListLocationDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loclist-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "allowed"
        (self.root / "Camera Imports").mkdir(parents=True)
        (self.root / "Camera Exports").mkdir()
        (self.root / "2025 Exports").mkdir()
        (self.root / "skip-file.txt").write_text("no")
        (self.root / "Camera Imports" / "2026-01-02").mkdir()
        (self.root / ".hidden").mkdir()
        (self.root / "@eaDir").mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret").mkdir()
        link = self.root / "escape"
        try:
            link.symlink_to(outside)
            self.linked = True
        except OSError:
            self.linked = False
        self.roots = [self.root]

    def _list(self, path):
        return NP.list_location_dir(str(path), allowed_roots=self.roots)

    def test_lists_immediate_dirs_only(self):
        out = self._list(self.root)
        self.assertTrue(out["ok"])
        names = [e["name"] for e in out["entries"]]
        self.assertEqual(names, ["2025 Exports", "Camera Exports", "Camera Imports"] + (["escape"] if False else []))
        # files, hidden, @eaDir must not appear; escape symlink to outside must be skipped
        self.assertNotIn("skip-file.txt", names)
        self.assertNotIn(".hidden", names)
        self.assertNotIn("@eaDir", names)
        self.assertNotIn("escape", names)
        # no recursion: 2026-01-02 is a grandchild
        self.assertNotIn("2026-01-02", names)
        # no child counts
        self.assertTrue(all("count" not in e and "dirs" not in e for e in out["entries"]))

    def test_traversal_rejected(self):
        # path outside allowed roots is not listed; we get an error (and a fallback)
        out = NP.list_location_dir(str(self.tmp / "outside"), allowed_roots=self.roots)
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("error"))
        self.assertEqual(out["entries"], [])

    def test_dotdot_cannot_escape(self):
        sneaky = self.root / "Camera Imports" / ".." / ".." / "outside"
        out = self._list(sneaky)
        self.assertFalse(out["ok"])
        self.assertEqual(out["entries"], [])

    def test_missing_path_falls_back_to_ancestor(self):
        missing = self.root / "Camera Imports" / "does-not-exist"
        out = self._list(missing)
        self.assertTrue(out["ok"])
        self.assertEqual(Path(out["path"]), (self.root / "Camera Imports").resolve())
        self.assertTrue(out.get("note"))
        names = [e["name"] for e in out["entries"]]
        self.assertEqual(names, ["2026-01-02"])

    def test_up_disabled_at_allowed_root(self):
        out = self._list(self.root)
        self.assertFalse(out["can_up"])
        self.assertIsNone(out["parent"])
        inner = self._list(self.root / "Camera Imports")
        self.assertTrue(inner["can_up"])
        self.assertEqual(Path(inner["parent"]), self.root.resolve())

    def test_breadcrumbs_stay_inside_roots(self):
        out = self._list(self.root / "Camera Imports")
        crumbs = out["breadcrumbs"]
        self.assertTrue(crumbs)
        self.assertTrue(all(Path(c["path"]) == self.root.resolve() or
                            Path(c["path"]).is_relative_to(self.root.resolve())
                            for c in crumbs))


class ChooseFolderJob(unittest.TestCase):
    def setUp(self):
        # isolate the in-memory job map
        self._prev = dict(NP._CHOOSE_JOBS)
        NP._CHOOSE_JOBS.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        NP._CHOOSE_JOBS.clear()
        NP._CHOOSE_JOBS.update(self._prev)

    def test_missing_job(self):
        out = NP.choose_folder_job_status("nope")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "missing")
        self.assertIsNone(out.get("path"))

    def test_start_returns_immediately_and_does_not_write_a_path_until_done(self):
        gate = threading.Event()
        def slow_choose(prompt):
            gate.wait(timeout=2)
            return {"ok": True, "cancelled": False, "path": "/Volumes/chosen", "preview": {"readable": True}}
        with mock.patch.object(NP, "choose_folder", side_effect=slow_choose):
            start = NP.start_choose_folder_job("Choose")
            self.assertTrue(start["ok"])
            self.assertTrue(start["job_id"])
            self.assertEqual(start["status"], "pending")
            self.assertNotIn("path", start)  # start payload has no path to apply
            mid = NP.choose_folder_job_status(start["job_id"])
            self.assertIn(mid["status"], ("pending", "running"))
            self.assertFalse(mid.get("ok") and mid.get("path"))
            gate.set()
            for _ in range(50):
                done = NP.choose_folder_job_status(start["job_id"])
                if done.get("status") in ("done", "error", "cancelled"):
                    break
                time.sleep(0.02)
            self.assertEqual(done["status"], "done")
            self.assertTrue(done["ok"])
            self.assertEqual(done["path"], "/Volumes/chosen")

    def test_cancelled_and_error_jobs_have_no_path_to_apply(self):
        with mock.patch.object(NP, "choose_folder",
                               return_value={"ok": False, "cancelled": True, "error": "Cancelled"}):
            start = NP.start_choose_folder_job("Choose")
            for _ in range(50):
                done = NP.choose_folder_job_status(start["job_id"])
                if done.get("status") != "pending" and done.get("status") != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(done["status"], "cancelled")
            self.assertFalse(done.get("ok"))
            self.assertTrue(done.get("cancelled"))
            self.assertFalse(done.get("path"))
        with mock.patch.object(NP, "choose_folder",
                               return_value={"ok": False, "cancelled": False, "error": "no display"}):
            start = NP.start_choose_folder_job("Choose")
            for _ in range(50):
                done = NP.choose_folder_job_status(start["job_id"])
                if done.get("status") not in ("pending", "running"):
                    break
                time.sleep(0.02)
            self.assertEqual(done["status"], "error")
            self.assertFalse(done.get("ok"))
            self.assertFalse(done.get("path"))

    def test_choose_folder_on_non_mac_does_not_block(self):
        with mock.patch.object(NP.sys, "platform", "linux"):
            out = NP.choose_folder("Choose")
        self.assertFalse(out["ok"])
        self.assertFalse(out.get("path"))
        self.assertIn("Mac", out["error"])



if __name__ == "__main__":
    unittest.main()
