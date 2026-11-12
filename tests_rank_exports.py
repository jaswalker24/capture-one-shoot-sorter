#!/usr/bin/env python3
"""Unit tests for export-stem matching, date parsing, labels and rank AUC — no NAS access."""
import json, math, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rank_exports as R


class StemTests(unittest.TestCase):
    def test_plain_canon(self):
        self.assertEqual(R.canonical_stem("_A3A3277.jpg"), "_A3A3277")
        self.assertEqual(R.canonical_stem("_MGL5677.jpg"), "_MGL5677")
        self.assertEqual(R.canonical_stem("IMGL3276.jpg"), "IMGL3276")

    def test_suffixes(self):
        self.assertEqual(R.canonical_stem("_A3A3277 1.jpg"), "_A3A3277")
        self.assertEqual(R.canonical_stem("_A3A9461-2.jpg"), "_A3A9461")
        self.assertEqual(R.canonical_stem("_A3A8918-2.jpg"), "_A3A8918")
        self.assertEqual(R.canonical_stem("_A3A1817 1.jpg"), "_A3A1817")
        self.assertEqual(R.canonical_stem("_A3A1913_1.jpg"), "_A3A1913")
        self.assertEqual(R.canonical_stem("_A3A1913-Edit.jpg"), "_A3A1913")
        self.assertEqual(R.canonical_stem("_A3A1913-Enhanced-NR.jpg"), "_A3A1913")
        self.assertEqual(R.canonical_stem("AA7O9866-Enhanced-NR.jpg"), "AA7O9866")
        self.assertEqual(R.canonical_stem("AA7O1171 (1).jpg"), "AA7O1171")
        self.assertEqual(R.canonical_stem("AA7O0146-1.jpg"), "AA7O0146")
        self.assertEqual(R.canonical_stem("AA7O0078-2.jpg"), "AA7O0078")
        self.assertEqual(R.canonical_stem("029A4324-2.JPG"), "029A4324")
        self.assertEqual(R.canonical_stem("AA7O9827-Enhanced-NR.jpg"), "AA7O9827")

    def test_renamed_kept(self):
        self.assertEqual(R.canonical_stem("Mk1 Escort.jpg"), "Mk1 Escort")
        self.assertEqual(R.canonical_stem("Britcar Porsche.jpg"), "Britcar Porsche")


class DiscoverEventTests(unittest.TestCase):
    def test_year_exports_event_and_loose(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bucket = root / "2025 Exports"
            (bucket / "2025-CSF2025-Andy").mkdir(parents=True)
            (bucket / "2025-Hakone").mkdir()
            (bucket / "Archive").mkdir()
            (bucket / "2025-CSF2025-Andy" / "AA7O1336.jpg").write_bytes(b"x")
            (bucket / "AA7O9790.jpg").write_bytes(b"x")
            # dated child still counted as dated
            (bucket / "2025-07-26").mkdir()
            found = R.discover_export_folders(root)
            kinds = {(f["name"], f.get("kind")) for f in found}
            self.assertIn(("2025-CSF2025-Andy", "event"), kinds)
            self.assertIn(("2025-Hakone", "event"), kinds)
            self.assertIn(("2025 Exports (root files)", "event-root"), kinds)
            self.assertIn(("2025-07-26", "dated"), kinds)
            self.assertFalse(any(f["name"] == "Archive" for f in found))

    def test_parse_rejects_event_names(self):
        self.assertIsNone(R.parse_export_folder_date("2025-CSF2025-Andy"))
        self.assertIsNone(R.parse_export_folder_date("2025-Hakone"))
        self.assertIsNone(R.parse_export_folder_date("2025-1"))


class DateTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(R.parse_export_folder_date("2026-10-04"), ("2026-10-04", ""))
        self.assertEqual(R.parse_export_folder_date("2026-1-17"), ("2026-01-17", ""))
        self.assertEqual(R.parse_export_folder_date("2026-4-07"), ("2026-04-07", ""))
        self.assertEqual(R.parse_export_folder_date("2026-4-7"), ("2026-04-07", ""))
        self.assertEqual(R.parse_export_folder_date("2026-6-23"), ("2026-06-23", ""))
        self.assertEqual(R.parse_export_folder_date("2026-06-231"), ("2026-06-23", "1"))
        self.assertEqual(R.parse_export_folder_date("2026-6-23C1"), ("2026-06-23", "C1"))
        self.assertEqual(R.parse_export_folder_date("2026-11-07BritCarIntoTheNight"),
                         ("2026-11-07", "BritCarIntoTheNight"))
        self.assertEqual(R.parse_export_folder_date("2026-7-12 CSF26"), ("2026-07-12", "CSF26"))
        self.assertEqual(R.parse_export_folder_date("20190706"), ("2019-07-06", ""))
        self.assertIsNone(R.parse_export_folder_date("Archive"))
        self.assertIsNone(R.parse_export_folder_date("2025-CSF2025-Andy"))

    def test_map_import(self):
        years = {"2026": [
            "2026-10-04", "2026-06-23", "2026-11-07_BrandsHatch_IntoTheNight",
            "2026-01-17", "2026-07-11",
        ]}
        self.assertEqual(R.map_date_to_import("2026-10-04", "", years), "2026-10-04")
        self.assertEqual(R.map_date_to_import("2026-06-23", "C1", years), "2026-06-23")
        self.assertEqual(R.map_date_to_import("2026-11-07", "BritCarIntoTheNight", years),
                         "2026-11-07_BrandsHatch_IntoTheNight")
        self.assertEqual(R.map_date_to_import("2026-01-17", "", years), "2026-01-17")
        self.assertIsNone(R.map_date_to_import("2026-08-20", "", years))


class TimeAndAucTests(unittest.TestCase):
    def test_normalize_time(self):
        self.assertEqual(R.normalize_time("2026:11:07 14:05:06"), "2026-11-07 14:05:06")
        self.assertEqual(R.normalize_time("2026-11-07T14:29:45.23"), "2026-11-07 14:29:45.23")
        self.assertEqual(R.normalize_time("2026-11-07T12:10:58.23"), "2026-11-07 12:10:58.23")
        self.assertEqual(R.normalize_time("2026:11:07 14:05:06.28+00:00"), "2026-11-07 14:05:06.28")
        self.assertEqual(R.normalize_time("2026-11-07T14:05:06.28"), "2026-11-07 14:05:06.28")
        self.assertEqual(R.time_second("2026-11-07 14:05:06.28"), "2026-11-07 14:05:06")

    def test_auc_and_score(self):
        # perfect ranking
        self.assertEqual(R._auc([3, 2, 1, 0], [True, True, False, False]), 1.0)
        self.assertEqual(round(R._auc([1, 0], [True, False]), 3), 1.0)
        w = R.DEFAULT_WEIGHTS
        r = dict(sharp_pct=1, aes_pct=0, burst_best=0, burst_other=0, blur=0)
        self.assertEqual(R.rank_score(r, w), 0.5)
        r2 = dict(sharp_pct=0, aes_pct=0, burst_best=0, burst_other=0, blur=1)
        self.assertEqual(R.rank_score(r2, w), -0.3)


class LabelTests(unittest.TestCase):
    def test_combined_labels(self):
        # synthetic index + positives
        rows = [
            {"sha256": "a", "filename": "_A3A0001.CR3", "folder": "shoot",
             "capture_time": "2026-01-01T10:00:00", "camera": "x",
             "sharp_subject": 800, "coherence": 0.1, "aesthetic": 5.0, "path": "/a", "stem": "_A3A0001", "time": "2026-01-01 10:00:00"},
            {"sha256": "b", "filename": "_A3A0002.CR3", "folder": "shoot",
             "capture_time": "2026-01-01T10:00:01", "camera": "x",
             "sharp_subject": 100, "coherence": 0.1, "aesthetic": 3.0, "path": "/b", "stem": "_A3A0002", "time": "2026-01-01 10:00:01"},
        ]
        # monkeypatch ratings and bursts and min_indexed
        orig_r, orig_b = R.load_ratings, R.load_bursts
        R.load_ratings = lambda fo: ({"_A3A0002": 5}, "snap.json")
        R.load_bursts = lambda fo: {}
        try:
            labels, summ = R.build_labels({"shoot": rows}, {"shoot": {"_A3A0001"}}, min_indexed=1)
        finally:
            R.load_ratings, R.load_bursts = orig_r, orig_b
        recs = labels["shoot"]
        by = {r["stem"]: r for r in recs}
        self.assertTrue(by["_A3A0001"]["y"] and by["_A3A0001"]["exported"] and not by["_A3A0001"]["y_star"])
        self.assertTrue(by["_A3A0002"]["y"] and by["_A3A0002"]["y_star"] and not by["_A3A0002"]["exported"])
        self.assertEqual(summ[0]["positives_combined"], 2)
        self.assertEqual(summ[0]["negatives"], 0)


class AdoptTests(unittest.TestCase):
    def test_adopt_only_if_better(self):
        result = {
            "before": {"overall": {"auc_combined_macro": 0.70, "auc_export_macro": 0.65}},
            "after_loso_macro": {"auc_combined_macro": 0.80, "auc_export_macro": 0.70},
        }
        ok, _ = R.should_adopt(result)
        self.assertTrue(ok)
        result["after_loso_macro"]["auc_combined_macro"] = 0.70
        ok, _ = R.should_adopt(result)
        self.assertFalse(ok)
        result["after_loso_macro"]["auc_combined_macro"] = 0.81
        result["after_loso_macro"]["auc_export_macro"] = 0.40
        ok, _ = R.should_adopt(result)
        self.assertFalse(ok)


    def test_year_bucket_as_root(self):
        """Training can point at the sibling 2025 Exports folder itself."""
        with tempfile.TemporaryDirectory() as tmp:
            bucket = Path(tmp) / "2025 Exports"
            (bucket / "2025-Hakone").mkdir(parents=True)
            (bucket / "2025-Hakone" / "a.jpg").write_bytes(b"x")
            found = R.discover_export_folders(bucket)
            kinds = {(f["name"], f["kind"]) for f in found}
            self.assertIn(("2025-Hakone", "event"), kinds)

if __name__ == "__main__":
    unittest.main()


class LogisticTests(unittest.TestCase):
    def _recs(self, n_pos=20, n_neg=40):
        recs = []
        # positives: sharp, aesthetic, often burst-best; negatives: blur / burst-other
        for i in range(n_pos):
            recs.append({
                "sharp_pct": 0.8 + 0.2 * (i / max(1, n_pos - 1)),
                "aes_pct": 0.7 + 0.2 * (i / max(1, n_pos - 1)),
                "burst_best": 1.0 if i % 2 == 0 else 0.0,
                "burst_other": 0.0,
                "blur": 0.0,
                "y": True, "y_export": i < n_pos - 2, "y_star": i >= 2,
                "filename": f"P{i:03d}.CR3",
            })
        for i in range(n_neg):
            recs.append({
                "sharp_pct": 0.3 * (i / max(1, n_neg - 1)),
                "aes_pct": 0.2 * (i / max(1, n_neg - 1)),
                "burst_best": 0.0,
                "burst_other": 1.0 if i % 3 == 0 else 0.0,
                "blur": 1.0 if i % 2 == 0 else 0.0,
                "y": False, "y_export": False, "y_star": False,
                "filename": f"N{i:03d}.CR3",
            })
        return recs

    def test_logistic_fits_and_scores(self):
        recs = self._recs()
        labels = {"s1": recs[:30], "s2": recs[30:]}
        # both shoots need pos and neg
        labels = {"s1": self._recs(12, 18), "s2": self._recs(10, 20)}
        model = R.fit_logistic(labels, ["s1", "s2"])
        self.assertIsNotNone(model)
        self.assertEqual(model["type"], "logistic")
        self.assertEqual(len(model["coef"]), 5)
        ev = R.eval_model(labels, model)
        self.assertGreater(ev["overall"]["auc_combined_macro"], 0.9)

    def test_loso_may_prefer_logistic(self):
        labels = {"s1": self._recs(15, 25), "s2": self._recs(12, 20), "s3": self._recs(10, 22)}
        # a weak baseline so a fitted model can improve
        weak = {"sharp": 0.15, "aes": 0.05, "burst_best": 0.0, "burst_other": 0.0, "blur": 0.0}
        result = R.leave_one_shoot_out(labels, weak)
        self.assertIn("after_loso_macro_logistic", result)
        self.assertIn("candidate_model", result)
        # baseline should already be decent on this separable data, logistic should be high
        self.assertGreaterEqual(result["after_loso_macro_logistic"]["auc_combined_macro"], 0.9)
        adopted, reason = R.should_adopt(result)
        self.assertTrue(adopted, reason)
        self.assertIn(result["candidate_model"]["type"], ("logistic", "weighted"))
