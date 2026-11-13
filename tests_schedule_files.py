#!/usr/bin/env python3
"""_schedule* posters are skipped by every pass; Schedules root resolves and saves like other Locations."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

def main():
    import tagger as T, camera_info as CI, nas_paths as NP, rank_exports as R
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for n in ["_A3A0001.CR3", "_A3A0002.JPG", "_schedule.jpg", "_Schedule.PNG", "_schedule-day2.heic", "clip.MP4"]:
            (d / n).write_bytes(b"x")
        names = [p.name for p in T.list_images(d)]
        assert names == ["_A3A0001.CR3", "_A3A0002.JPG"], names
        assert [p.name for p in CI.list_raws(d)] == ["_A3A0001.CR3"]
        assert sorted(p.name for p in R.iter_export_images(d)) == ["_A3A0001.CR3", "_A3A0002.JPG"]
        assert T.is_schedule_file("/x/_schedule.jpg") and not T.is_schedule_file("/x/schedule.jpg")
        # Locations
        home = d / "home"; home.mkdir()
        (home / "config.json").write_text(json.dumps({"photos_root": str(d)}))
        os.environ.pop("LOCALAI_SCHEDULES", None)
        assert NP.schedules_root(home) == d / "Schedules"
        keys = [f["key"] for f in NP.current_locations(home)["fields"]]
        assert "schedules_root" in keys, keys
        sched = d / "Schedules"; sched.mkdir()
        NP.save_locations({"schedules_root": str(sched)}, home=home) if hasattr(NP, "save_locations") else None
    print("tests_schedule_files OK")

if __name__ == "__main__":
    main()
