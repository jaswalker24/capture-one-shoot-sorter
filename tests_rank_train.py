#!/usr/bin/env python3
"""Tests for rank-training helpers, jobrunner train jobs, and adopt/rollback.

No Capture One, no NAS, no live ~/localai writes.
"""
from __future__ import annotations
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "gui"))

import rank_exports as R
import train_rank_exports as TR
import apply_c1 as AC
import jobrunner as JR


class ExportRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="roots-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Camera Exports" / "2026-01-02").mkdir(parents=True)
        (self.tmp / "2025 Exports" / "2025-Hakone").mkdir(parents=True)
        (self.tmp / "Camera Exports" / "2026-01-02" / "IMG_0001.jpg").write_bytes(b"x")
        (self.tmp / "2025 Exports" / "2025-Hakone" / "AA7O1336.jpg").write_bytes(b"x")

    def test_list_and_discover_many(self):
        with mock.patch.object(R, "PHOTOS", self.tmp), mock.patch.object(R, "EXPORTS_ROOT", self.tmp / "Camera Exports"):
            # photos_root() reads env/config; pass photos explicitly
            roots = R.list_export_roots(photos=self.tmp)
            labels = {r["label"]: r for r in roots}
            self.assertTrue(labels["Camera Exports"]["available"])
            self.assertTrue(labels["Other Exports"]["available"])
            found = R.discover_export_folders_many([r["path"] for r in roots if r["available"]])
            names = {f["name"] for f in found}
            self.assertIn("2026-01-02", names)
            self.assertIn("2025-Hakone", names)


class NeedsIndex(unittest.TestCase):
    def test_small_or_missing_index(self):
        positives = {"2026-01-02": {"IMG_0001"}, "2026-02-03": {"IMG_0002"}}
        index = {"2026-01-02": [{"filename": "IMG_0001.CR3"}] * 10}
        need = R.folders_needing_index(positives, index, min_indexed=50)
        folders = {n["folder"] for n in need}
        self.assertEqual(folders, {"2026-01-02", "2026-02-03"})
        skipped = R.folders_needing_index(positives, {"2026-01-02": [{}] * 60, "2026-02-03": [{}] * 60}, min_indexed=50)
        self.assertEqual(skipped, [])


class RankConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rankcfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = self.tmp / "config.json"
        self.cfg.write_text(json.dumps({
            "rank": {
                "weights": {"sharp": 0.5, "aes": 0.15, "burst_best": 0.3, "burst_other": 0.1, "blur": 0.3},
                "model": "weighted",
            }
        }))
        self.home_orig = TR.HOME
        TR.HOME = self.tmp
        TR.OUTDIR = self.tmp / "out" / "rank-exports"
        TR.OUTDIR.mkdir(parents=True)
        self.addCleanup(lambda: setattr(TR, "HOME", self.home_orig))

    def test_adopt_and_rollback(self):
        report = {
            "stamp": "20261112-test",
            "train": {
                "candidate_weights": {"sharp": 0.15, "aes": 0.45, "burst_best": 0.0, "burst_other": 0.1, "blur": 0.0},
                "candidate_model": {"type": "weighted", "weights": {"sharp": 0.15, "aes": 0.45, "burst_best": 0.0, "burst_other": 0.1, "blur": 0.0}},
                "adopt_reason": "held-out combined macro 0.60 -> 0.70 (weighted)",
            },
        }
        (TR.OUTDIR / "train-latest.json").write_text(json.dumps(report))
        out = TR.adopt_from_report()
        self.assertTrue(out["ok"])
        rank = json.loads(self.cfg.read_text())["rank"]
        self.assertEqual(rank["weights"]["aes"], 0.45)
        self.assertEqual(rank["previous_weights"]["aes"], 0.15)
        self.assertTrue(rank["adopted"])
        TR.rollback_rank()
        rank2 = json.loads(self.cfg.read_text())["rank"]
        self.assertEqual(rank2["weights"]["aes"], 0.15)
        self.assertEqual(rank2["previous_weights"]["aes"], 0.45)
        self.assertFalse(rank2["adopted"])


class KeywordLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kwlib-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.prev = AC.HOME
        AC.HOME = self.tmp
        self.addCleanup(lambda: setattr(AC, "HOME", self.prev))

    def test_default_is_trip_keywords(self):
        self.assertEqual(AC.keyword_library(), "Trip Keywords")

    def test_config_override(self):
        (self.tmp / "config.json").write_text(json.dumps({"keyword_library": "Grok Keywords"}))
        self.assertEqual(AC.keyword_library(), "Grok Keywords")

    def test_explicit_name_wins(self):
        (self.tmp / "config.json").write_text(json.dumps({"keyword_library": "Grok Keywords"}))
        self.assertEqual(AC.keyword_library("Trip Keywords"), "Trip Keywords")


class TrainJob(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trainjob-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.jobs = self.tmp / "gui" / "jobs"
        self.jobs.mkdir(parents=True)
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        # stub trainer that writes a report and does not touch C1
        stub = self.tools / "train_rank_exports.py"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "home = Path(os.environ['LOCALAI_HOME'])\n"
            "out = home / 'out' / 'rank-exports'\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "rep = {'stamp': 'test', 'train': {'adopt': False, 'adopt_reason': 'dry', "
            "'candidate_weights': {'sharp': 0.2}, 'before': {'overall': {'auc_combined_macro': 0.5}}, "
            "'after_loso_macro': {'auc_combined_macro': 0.5}}, 'wrote_capture_one': False}\n"
            "(out / 'train-latest.json').write_text(json.dumps(rep))\n"
            "print('report:', out / 'train-latest.json')\n"
        )
        self.home_jr = JR.HOME
        JR.HOME = self.tmp
        self.addCleanup(lambda: setattr(JR, "HOME", self.home_jr))
        # snap must never be called
        self.snap = mock.patch.object(JR, "snap", side_effect=AssertionError("snap must not run during training"))
        self.snap.start()
        self.addCleanup(self.snap.stop)

    def test_run_rank_train_no_c1(self):
        job_path = self.jobs / "train-1.json"
        state = {
            "id": "train-1",
            "kind": "rank_train",
            "folder": str(self.tmp),
            "folder_name": "Rank training",
            "export_roots": [str(self.tmp / "Camera Exports")],
            "index_missing": False,
            "auto_adopt": False,
            "apply_each": True,  # even if a caller set this, train must not apply
            "log": [],
        }
        job_path.write_text(json.dumps(state))
        env = os.environ.copy()
        env["LOCALAI_HOME"] = str(self.tmp)
        with mock.patch.dict(os.environ, {"LOCALAI_HOME": str(self.tmp)}):
            JR.run_job(job_path)
        done = json.loads(job_path.read_text())
        self.assertEqual(done["status"], "done")
        self.assertTrue(done.get("dry_run"))
        self.assertFalse(done.get("apply_each"))
        self.assertFalse(done.get("apply_rank_keywords"))
        self.assertTrue((self.tmp / "out" / "rank-exports" / "train-latest.json").is_file())
        self.assertIn("RANK TRAIN", "\n".join(done.get("log") or []))


if __name__ == "__main__":
    unittest.main()
