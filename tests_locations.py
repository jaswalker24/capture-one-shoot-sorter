#!/usr/bin/env python3
"""Locations (NAS path) preview, save-with-backup, and 2025 Exports root.

No Capture One, no NAS, no live ~/localai writes.
"""
from __future__ import annotations
import json, os, shutil, sys, tempfile, unittest
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
            "keyword_library": "Trip Keywords",
        }, indent=2))

    def test_save_backs_up_and_keeps_other_keys(self):
        photos = self.tmp / "Photos"
        out = NP.save_locations({
            "photos_root": str(photos),
            "imports_root": str(photos / "Camera Imports"),
            "exports_root": str(photos / "Camera Exports"),
            "exports_2025_root": str(photos / "2025 Exports"),
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
        self.assertEqual(new["exports_2025_root"], str(photos / "2025 Exports"))
        self.assertEqual(new["keyword_library"], "Trip Keywords")
        self.assertEqual(new["rank"]["weights"]["aes"], 0.15)
        # no second backup when unchanged
        out2 = NP.save_locations({
            "photos_root": str(photos),
            "imports_root": str(photos / "Camera Imports"),
            "exports_root": str(photos / "Camera Exports"),
            "exports_2025_root": str(photos / "2025 Exports"),
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
        cfg["exports_2025_root"] = "/old/2025"
        self.cfg.write_text(json.dumps(cfg, indent=2))
        # clearing optional must not require the path to exist
        out = NP.save_locations({"exports_2025_root": ""}, home=self.tmp, require_readable=True)
        self.assertTrue(out["changed"])
        new = json.loads(self.cfg.read_text())
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

    def test_exports_2025_derived_and_override(self):
        self.assertEqual(NP.exports_2025_root(self.tmp), self.tmp / "Photos" / "2025 Exports")
        # explicit config wins even if the folder is a custom location
        custom = self.tmp / "custom-2025"
        custom.mkdir()
        cfg = json.loads((self.tmp / "config.json").read_text())
        cfg["exports_2025_root"] = str(custom)
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.assertEqual(NP.exports_2025_root(self.tmp), custom)

    def test_rank_exports_honours_config(self):
        prev_home = R.HOME
        R.HOME = self.tmp
        self.addCleanup(lambda: setattr(R, "HOME", prev_home))
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("LOCALAI_PHOTOS", "LOCALAI_IMPORTS", "LOCALAI_EXPORTS", "LOCALAI_EXPORTS_2025"):
                os.environ.pop(k, None)
            self.assertEqual(R.imports_root(), self.tmp / "Photos" / "Camera Imports")
            self.assertEqual(R.exports_2025_root(), self.tmp / "Photos" / "2025 Exports")
            roots = R.list_export_roots()
            labels = {r["label"]: r for r in roots}
            self.assertEqual(Path(labels["2025 Exports"]["path"]), self.tmp / "Photos" / "2025 Exports")
            self.assertTrue(labels["2025 Exports"]["available"])


class Browse(unittest.TestCase):
    def test_non_darwin_explains_paste(self):
        with mock.patch.object(NP.sys, "platform", "linux"):
            r = NP.choose_folder("Choose the Camera Imports folder")
        self.assertFalse(r["ok"])
        self.assertIn("Paste the path", r["error"])


if __name__ == "__main__":
    unittest.main()
