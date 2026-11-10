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

    def test_renamed_kept(self):
        self.assertEqual(R.canonical_stem("Mk1 Escort.jpg"), "Mk1 Escort")
        self.assertEqual(R.canonical_stem("Britcar Porsche.jpg"), "Britcar Porsche")


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


if __name__ == "__main__":
    unittest.main()
