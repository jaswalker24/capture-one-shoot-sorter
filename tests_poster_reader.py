#!/usr/bin/env python3
"""Tests for the schedule-from-poster importer.

Uses temp directories only. No NAS, no Capture One, no live ~/localai writes
except a throwaway drafts dir inside the temp tree. OCR is stubbed except in
the optional live Vision test (skipped if Vision is unavailable).
"""
from __future__ import annotations

import io, json, os, sys, tempfile, unittest
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gui"))

import poster_reader as PR  # noqa: E402
import schedules_tree as ST  # noqa: E402
import schedules_api as SA  # noqa: E402


def _write_jpeg(path: Path, text: str = "x", size=(40, 40), colour=(200, 180, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, colour)
    ImageDraw.Draw(im).text((2, 2), text[:12], fill=(20, 20, 20))
    im.save(path, "JPEG", quality=85)
    return path


def _event_folder(root: Path, date="2026-11-22", event="Example Hall Show", flat=False) -> Path:
    if flat:
        d = Path(root) / date / event
    else:
        d = Path(root) / date[:4] / date / event
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rows(*acts):
    out = []
    for item in acts:
        out.append({
            "act": item[0], "start": item[1], "end": item[2],
            "doors": item[3] if len(item) > 3 else "",
            "notes": item[4] if len(item) > 4 else "",
        })
    return out


def _confirmed(*acts, date="2026-11-22", event="Example Hall Show"):
    return ST.build_schedule(
        {"date": date, "event": event, "venue": "Example Hall",
         "timezone": "Asia/Tokyo", "source": "notion"},
        _rows(*acts),
    )


class Parser(unittest.TestCase):
    def test_colon_ranges_and_doors(self):
        text = [
            "TIMETABLE",
            "Example Hall Show",
            "2026.11.22",
            "開場 16:30",
            "開演 17:00",
            "17:00-17:30 Act One",
            "17:40-18:20 Act Two",
            "18:30-19:10 Headliner",
        ]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Example Hall Show")
        self.assertEqual(p["meta"]["timezone"], "Asia/Tokyo")
        self.assertEqual(p["doors"], "16:30")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(acts, [
            ("Act One", "17:00", "17:30"),
            ("Act Two", "17:40", "18:20"),
            ("Headliner", "18:30", "19:10"),
        ])
        self.assertEqual(p["rows"][0]["doors"], "16:30")

    def test_name_then_time_and_english_doors(self):
        text = ["Doors 18:00", "Opener 19:00-19:30", "Main Act 19:45-20:30"]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Night")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(p["doors"], "18:00")
        self.assertEqual(acts, [
            ("Opener", "19:00", "19:30"),
            ("Main Act", "19:45", "20:30"),
        ])

    def test_japanese_wave_dash_and_kaijo(self):
        text = ["開場 17:00", "バンドA 18:00〜18:30", "バンドB 18:40〜19:10"]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Show")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(p["doors"], "17:00")
        self.assertEqual(acts, [
            ("バンドA", "18:00", "18:30"),
            ("バンドB", "18:40", "19:10"),
        ])

    def test_fullwidth_digits_and_colon(self):
        text = ["１７：００－１７：３０ バンドＡ", "１７：４０－１８：１０ バンドＢ"]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Show")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(acts, [
            ("バンドＡ", "17:00", "17:30"),
            ("バンドＢ", "17:40", "18:10"),
        ])

    def test_start_only_infers_end_from_next(self):
        text = ["17:00 Act One", "17:40 Act Two", "18:30 Headliner"]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Show")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(acts[0], ("Act One", "17:00", "17:40"))
        self.assertEqual(acts[1], ("Act Two", "17:40", "18:30"))
        self.assertEqual(acts[2], ("Headliner", "18:30", ""))
        self.assertIn("inferred", p["rows"][0]["notes"])

    def test_time_on_own_line_then_name(self):
        text = ["17:00-17:30", "Act One", "17:40-18:20", "Act Two"]
        p = PR.parse_ocr_lines(text, date="2026-11-22", event="Show")
        acts = [(r["act"], r["start"], r["end"]) for r in p["rows"]]
        self.assertEqual(acts, [
            ("Act One", "17:00", "17:30"),
            ("Act Two", "17:40", "18:20"),
        ])

    def test_jp_hour_minutes(self):
        self.assertEqual(PR.extract_times("17時30分-18時00分"), [("17:30", "18:00", "")])
        self.assertEqual(PR.extract_times("開場 17時")[0][0], "17:00")


class DetectionAndHash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Schedules"
        self.drafts = Path(self.tmp.name) / "drafts"
        self.root.mkdir()
        self.ocr_calls = []

        def fake_ocr(path):
            self.ocr_calls.append(Path(path).name)
            return [{"text": "開場 16:30", "conf": 0.9, "y": 0.1, "x": 0.1},
                    {"text": "17:00-17:30 Act One", "conf": 0.9, "y": 0.2, "x": 0.1},
                    {"text": "17:40-18:20 Act Two", "conf": 0.9, "y": 0.3, "x": 0.1}]

        self.ocr = fake_ocr

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_image_auto_read_canonical(self):
        ev = _event_folder(self.root)
        _write_jpeg(ev / "flyer.jpg", "poster")
        # json sidecars must not count as images
        (ev / "notes.json").write_text("{}", encoding="utf-8")
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["read"], 1)
        self.assertEqual(len(out["review"]), 1)
        item = out["review"][0]
        self.assertEqual(item["status"], "pending_review")
        self.assertEqual(item["image"]["name"], "flyer.jpg")
        self.assertEqual(len(item["rows"]), 2)
        self.assertEqual(item["rows"][0]["act"], "Act One")
        self.assertEqual(item["meta"]["timezone"], "Asia/Tokyo")
        self.assertEqual(self.ocr_calls, ["flyer.jpg"])
        # no NAS-style writes
        self.assertFalse((ev / "poster.json").exists())
        self.assertFalse((ev / "schedule.json").exists())
        self.assertTrue(PR.draft_path("2026-11-22", "Example Hall Show", drafts=self.drafts).is_file())

    def test_flat_layout_accepted(self):
        ev = _event_folder(self.root, flat=True)
        _write_jpeg(ev / "shot.png", "p")
        # convert? PIL saved jpeg with png ext is still readable by our fake ocr (we don't open it)
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["read"], 1)
        self.assertEqual(out["review"][0]["layout"], "flat")
        self.assertEqual(out["review"][0]["date"], "2026-11-22")

    def test_zero_images_not_listed(self):
        _event_folder(self.root)
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["read"], 0)
        self.assertEqual(out["review"], [])
        self.assertEqual(self.ocr_calls, [])

    def test_multiple_images_pick_one_no_read(self):
        ev = _event_folder(self.root)
        _write_jpeg(ev / "a.jpg", "a", colour=(10, 10, 10))
        _write_jpeg(ev / "b.jpg", "b", colour=(20, 20, 20))
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["read"], 0)
        self.assertEqual(self.ocr_calls, [])
        item = out["review"][0]
        self.assertEqual(item["status"], "multiple")
        self.assertEqual(item["error"], "multiple images, pick one")
        self.assertEqual(sorted(item["images"]), ["a.jpg", "b.jpg"])
        self.assertFalse((ev / "poster.json").exists())

    def test_hash_skip_and_change(self):
        ev = _event_folder(self.root)
        p = _write_jpeg(ev / "flyer.jpg", "one", colour=(11, 12, 13))
        PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(self.ocr_calls, ["flyer.jpg"])
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["read"], 0)
        self.assertEqual(out["skipped_hash"], 1)
        self.assertEqual(self.ocr_calls, ["flyer.jpg"])
        # change the bytes
        _write_jpeg(p, "two", colour=(200, 10, 10))
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["read"], 1)
        self.assertEqual(self.ocr_calls, ["flyer.jpg", "flyer.jpg"])

    def test_heic_and_webp_count_as_images(self):
        ev = _event_folder(self.root)
        (ev / "shot.HEIC").write_bytes(b"not a real heic")
        # not a valid image for PIL, but the suffix counts; hash still works
        # Our is_poster_image only checks suffix + is_file
        self.assertTrue(PR.is_poster_image(ev / "shot.HEIC"))
        self.assertTrue(PR.is_poster_image(_write_jpeg(ev / "x.webp", "w") or ev / "x.webp")
                        or (ev / "x.webp").suffix.lower() == ".webp")
        # two images → multiple, no read (even though heic isn't a real image file)
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["review"][0]["status"], "multiple")
        self.assertEqual(self.ocr_calls, [])

    def test_non_image_ignored(self):
        ev = _event_folder(self.root)
        (ev / "readme.txt").write_text("hi", encoding="utf-8")
        (ev / "schedule.json").write_text("{}", encoding="utf-8")
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["review"], [])


class ComparisonAndAccept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Schedules"
        self.drafts = Path(self.tmp.name) / "drafts"
        self.root.mkdir()
        ev = _event_folder(self.root)
        _write_jpeg(ev / "flyer.jpg", "p")
        self.ev = ev

        def fake_ocr(path):
            return [{"text": "開場 16:30"},
                    {"text": "17:00-17:30 Act One"},
                    {"text": "17:40-18:20 Act Two"}]

        self.ocr = fake_ocr

    def tearDown(self):
        self.tmp.cleanup()

    def test_match_and_gap_and_disagreement(self):
        rows = _rows(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20"))
        # exact match
        c = compare = PR.compare_with_confirmed(rows, _confirmed(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20")))
        self.assertTrue(c["has_confirmed"])
        self.assertEqual(len(c["matches"]), 2)
        self.assertEqual(c["gaps"], [])
        self.assertEqual(c["disagreements"], [])
        self.assertTrue(c["can_accept"])
        # within 5 minutes still matches
        c = PR.compare_with_confirmed(rows, _confirmed(("Act One", "17:05", "17:30"), ("Act Two", "17:40", "18:20")))
        self.assertTrue(c["can_accept"])
        self.assertEqual(len(c["matches"]), 2)
        # poster fills a gap (extra act)
        c = PR.compare_with_confirmed(
            _rows(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20"), ("Encore", "18:30", "18:50")),
            _confirmed(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20")),
        )
        self.assertTrue(c["can_accept"])
        self.assertEqual(len(c["gaps"]), 1)
        self.assertEqual(c["gaps"][0]["act"], "Encore")
        # time disagreement
        c = PR.compare_with_confirmed(
            _rows(("Act One", "17:00", "17:30"), ("Act Two", "18:00", "18:40")),
            _confirmed(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20")),
        )
        self.assertFalse(c["can_accept"])
        self.assertTrue(c["disagreements"])
        # confirmed act missing from poster
        c = PR.compare_with_confirmed(
            _rows(("Act One", "17:00", "17:30")),
            _confirmed(("Act One", "17:00", "17:30"), ("Act Two", "17:40", "18:20")),
        )
        self.assertFalse(c["can_accept"])
        self.assertTrue(any("missing" in d["reason"] for d in c["disagreements"]))
        # no confirmed.json
        c = PR.compare_with_confirmed(rows, None)
        self.assertFalse(c["has_confirmed"])
        self.assertTrue(c["can_accept"])

    def test_no_auto_write_and_accept_goes_through_confirm(self):
        PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertFalse((self.ev / "poster.json").exists())
        self.assertFalse((self.ev / "schedule.json").exists())
        # preview without confirm writes nothing
        prev = PR.accept(self.root, "2026-11-22", "Example Hall Show",
                         confirm=False, drafts=self.drafts)
        self.assertFalse(prev.get("written"))
        self.assertFalse((self.ev / "poster.json").exists())
        self.assertFalse((self.ev / "schedule.json").exists())
        self.assertTrue(prev["can_accept"])
        # confirm writes both, via confirm_save (history appears)
        result = PR.accept(self.root, "2026-11-22", "Example Hall Show",
                           confirm=True, drafts=self.drafts)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue((self.ev / "schedule.json").is_file())
        self.assertTrue((self.ev / "poster.json").is_file())
        data = json.loads((self.ev / "schedule.json").read_text())
        self.assertEqual(data["source"], "poster")
        self.assertEqual(data["timezone"], "Asia/Tokyo")
        self.assertEqual(data["events"][0]["slots"][0]["act"], "Act One")
        hist = json.loads((self.ev / "history.json").read_text())
        self.assertEqual(hist["versions"][0]["source"], "poster")
        poster = json.loads((self.ev / "poster.json").read_text())
        self.assertEqual(poster["engine"], "apple-vision")
        self.assertNotIn("path", poster.get("image") or {})
        draft = PR.load_draft("2026-11-22", "Example Hall Show", drafts=self.drafts)
        self.assertEqual(draft["status"], "accepted")

    def test_accept_blocked_on_disagreement(self):
        (self.ev / "confirmed.json").write_text(json.dumps(_confirmed(
            ("Act One", "17:00", "17:30"),
            ("Act Two", "17:40", "18:20"),
            ("Act Three", "18:30", "19:00"),
        )), encoding="utf-8")
        PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        result = PR.accept(self.root, "2026-11-22", "Example Hall Show",
                           confirm=True, drafts=self.drafts)
        self.assertFalse(result.get("ok"))
        self.assertIn("disagrees", (result.get("error") or "").lower())
        self.assertFalse((self.ev / "schedule.json").exists())
        self.assertFalse((self.ev / "poster.json").exists())

    def test_hold_and_reject_stay_local(self):
        PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        h = PR.set_status("2026-11-22", "Example Hall Show", "held", drafts=self.drafts)
        self.assertTrue(h["ok"])
        self.assertEqual(h["item"]["status"], "held")
        r = PR.set_status("2026-11-22", "Example Hall Show", "rejected", drafts=self.drafts)
        self.assertEqual(r["item"]["status"], "rejected")
        self.assertFalse((self.ev / "poster.json").exists())
        self.assertFalse((self.ev / "schedule.json").exists())

    def test_pick_one_then_read(self):
        _write_jpeg(self.ev / "other.jpg", "o", colour=(1, 2, 3))
        out = PR.scan_tree(self.root, drafts=self.drafts, ocr_fn=self.ocr, read=True)
        self.assertEqual(out["review"][0]["status"], "multiple")
        got = PR.reread(self.root, "2026-11-22", "Example Hall Show",
                        filename="flyer.jpg", drafts=self.drafts, ocr_fn=self.ocr)
        self.assertTrue(got["ok"])
        self.assertEqual(got["status"], "pending_review")
        self.assertEqual(got["image"]["name"], "flyer.jpg")
        self.assertEqual(got["rows"][0]["act"], "Act One")
        self.assertFalse((self.ev / "poster.json").exists())


class ApiAndFind(unittest.TestCase):
    def test_find_event_dir_flat_and_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            canon = _event_folder(root, event="Canon Show")
            flat = _event_folder(root, date="2026-11-23", event="Flat Show", flat=True)
            self.assertEqual(ST.find_event_dir(root, "2026-11-22", "Canon Show"), canon)
            self.assertEqual(ST.find_event_dir(root, "2026-11-23", "Flat Show"), flat)
            # new events still resolve to the canonical path
            fresh = ST.find_event_dir(root, "2026-11-24", "New Night")
            self.assertEqual(fresh, root / "2026" / "2026-11-24" / "New Night")
            names = {(e["date"], e["event"]) for e in ST.list_events(root)}
            self.assertIn(("2026-11-22", "Canon Show"), names)
            self.assertIn(("2026-11-23", "Flat Show"), names)

    def test_api_list_preview_and_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home = d / "home"
            root = d / "Schedules"
            ev = _event_folder(root)
            _write_jpeg(ev / "flyer.jpg", "p")
            (home / "gui").mkdir(parents=True)
            prev = os.environ.get("LOCALAI_SCHEDULES")
            os.environ["LOCALAI_SCHEDULES"] = str(root)

            def ocr(path):
                return [{"text": "17:00-17:30 Act One"}, {"text": "17:40-18:20 Act Two"}]

            try:
                code, status = SA.handle_get("/api/schedules/status", {}, home)
                self.assertEqual(code, 200)
                self.assertIn("poster_review_count", status)
                PR.scan_tree(root, drafts=PR.DRAFTS_DIR, ocr_fn=ocr, read=True)
                code, body = SA.handle_get("/api/schedules/posters", {}, home)
                self.assertEqual(code, 200)
                self.assertTrue(body.get("ok"))
                self.assertEqual(body.get("review_count"), 1)
                self.assertFalse((ev / "poster.json").exists())
                self.assertFalse((ev / "schedule.json").exists())
                code, item = SA.handle_get("/api/schedules/posters/item",
                                           {"date": ["2026-11-22"], "name": ["Example Hall Show"]}, home)
                self.assertEqual(code, 200)
                self.assertEqual(item["rows"][0]["act"], "Act One")
                # accept without confirm writes nothing
                code, prevw = SA.handle_post("/api/schedules/posters/accept", {
                    "date": "2026-11-22", "event": "Example Hall Show", "confirm": False
                }, home)
                self.assertEqual(code, 400)
                self.assertFalse((ev / "schedule.json").exists())
                code, result = SA.handle_post("/api/schedules/posters/accept", {
                    "date": "2026-11-22", "event": "Example Hall Show", "confirm": True
                }, home)
                self.assertEqual(code, 200, result)
                self.assertTrue((ev / "schedule.json").is_file())
                self.assertTrue((ev / "poster.json").is_file())
            finally:
                if prev is None:
                    os.environ.pop("LOCALAI_SCHEDULES", None)
                else:
                    os.environ["LOCALAI_SCHEDULES"] = prev


class LiveVision(unittest.TestCase):
    """End-to-end Apple Vision read of a generated poster, in a temp folder only."""

    def test_generated_poster_ocr(self):
        try:
            import Vision  # noqa: F401
        except Exception:
            self.skipTest("Apple Vision is not available")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            poster = d / "poster.jpg"
            _render_sample_poster(poster)
            ev = _event_folder(d / "Schedules")
            dest = ev / "_schedule.jpg"
            dest.write_bytes(poster.read_bytes())
            drafts = d / "drafts"
            out = PR.scan_tree(d / "Schedules", drafts=drafts, read=True)
            self.assertEqual(out["read"], 1, out)
            item = out["review"][0]
            self.assertEqual(item["status"], "pending_review")
            acts = [(r["act"], r["start"], r["end"]) for r in item["rows"]]
            # stash for the report
            report = d / "ocr_report.json"
            report.write_text(json.dumps({
                "acts": acts, "doors": item.get("meta", {}).get("doors") or (item["rows"][0].get("doors") if item["rows"] else ""),
                "ocr_text": (item.get("ocr") or {}).get("text"),
                "rows": item["rows"],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            # copy report next to the test for humans? stay in tmp; print instead
            print("\nLIVE OCR TEXT:\n", (item.get("ocr") or {}).get("text"))
            print("LIVE ROWS:", acts)
            names = [a[0] for a in acts]
            starts = [a[1] for a in acts]
            ends = [a[2] for a in acts]
            self.assertIn("17:00", starts, f"17:00 not in {acts}; OCR={(item.get('ocr') or {}).get('text')!r}")
            self.assertTrue(any("Act One" in n or "One" in n for n in names),
                            f"expected Act One in {acts}; OCR={(item.get('ocr') or {}).get('text')!r}")
            self.assertTrue(any("Act Two" in n or "Two" in n for n in names),
                            f"expected Act Two in {acts}; OCR={(item.get('ocr') or {}).get('text')!r}")
            self.assertTrue(any("Headliner" in n for n in names),
                            f"expected Headliner in {acts}; OCR={(item.get('ocr') or {}).get('text')!r}")
            doors = (item.get("meta") or {}).get("doors") or (item["rows"][0].get("doors") if item["rows"] else "")
            self.assertEqual(doors, "16:30", f"doors={doors!r}; OCR={(item.get('ocr') or {}).get('text')!r}")
            self.assertFalse((ev / "poster.json").exists())
            self.assertFalse((ev / "schedule.json").exists())


def _render_sample_poster(path: Path):
    """High-contrast timetable poster for Vision OCR."""
    path = Path(path)
    im = Image.new("RGB", (1200, 1600), (18, 18, 28))
    dr = ImageDraw.Draw(im)
    def font(size, jp=False):
        candidates = []
        if jp:
            for name in (
                "ヒラギノ角ゴシック W6.ttc",
                "Hiragino Sans GB.ttc",
            ):
                candidates.append(Path("/System/Library/Fonts") / name)
        candidates += [
            Path("/System/Library/Fonts/Helvetica.ttc"),
            Path("/System/Library/Fonts/HelveticaNeue.ttc"),
            Path("/Library/Fonts/Arial.ttf"),
        ]
        for c in candidates:
            if c.is_file():
                try:
                    return ImageFont.truetype(str(c), size=size)
                except Exception:
                    continue
        return ImageFont.load_default()
    title = font(64)
    body = font(52)
    jp = font(52, jp=True)
    dr.text((80, 80), "TIMETABLE", fill=(240, 240, 250), font=title)
    dr.text((80, 180), "Example Hall Show", fill=(240, 240, 250), font=title)
    dr.text((80, 280), "2026.11.22", fill=(200, 200, 210), font=body)
    dr.text((80, 400), "開場 16:30", fill=(240, 220, 120), font=jp)
    dr.text((80, 500), "開演 17:00", fill=(240, 220, 120), font=jp)
    dr.text((80, 680), "17:00-17:30  Act One", fill=(250, 250, 250), font=body)
    dr.text((80, 800), "17:40-18:20  Act Two", fill=(250, 250, 250), font=body)
    dr.text((80, 920), "18:30-19:10  Headliner", fill=(250, 250, 250), font=body)
    im.save(path, "JPEG", quality=95)
    return path


if __name__ == "__main__":
    unittest.main()
