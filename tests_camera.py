#!/usr/bin/env python3
"""Unit tests for camera_info classification (no originals, no Capture One)."""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import camera_info as C

def test_lens_short_names_no_slashes():
    cases = {
        "EF50mm f/1.2L USM": ("Canon 50mm f1.2", 1.2),
        "Canon EF 50mm f/1.2L USM": ("Canon 50mm f1.2", 1.2),
        "RF50mm F1.2 L USM": ("Canon 50mm f1.2", 1.2),
        "EF85mm f/1.4L IS USM": ("Canon 85mm f1.4", 1.4),
        "EF35mm f/2 IS USM": ("Canon 35mm f2", 2.0),
        "EF135mm f/2L USM": ("Canon 135mm f2", 2.0),
        "150-600mm F5-6.3 DG OS HSM | Contemporary 015": ("Sigma 150-600", None),
        "EF70-200mm f/4L IS USM": ("Canon 70-200 f4", 4.0),
        "RF70-200mm F4 L IS USM": ("Canon 70-200 f4", 4.0),
    }
    for raw, (name, mx) in cases.items():
        got, gmx = C.short_lens(raw)
        assert got == name, (raw, got, name)
        assert gmx == mx, (raw, gmx, mx)
        assert "/" not in got
    # unknown fallback strips slashes and Sigma-style tails
    u, _ = C.short_lens("ACME 24-70mm f/2.8 | Sports 99")
    assert u == "ACME 24-70mm f2.8"
    assert C.short_lens("") == (None, None)

def test_as_float_and_focal_band():
    assert C.as_float("1/800") == 1 / 800
    assert C.as_float("50.0 mm") == 50.0
    assert C.as_float("+1.3") == 1.3
    assert abs(C.as_float("-2/3") - (-2 / 3)) < 1e-9
    assert C.as_float(1.2) == 1.2
    assert C.as_float(None) is None
    assert C.focal_band(24) == "Wide"
    assert C.focal_band(34.9) == "Wide"
    assert C.focal_band(35) == "Normal"
    assert C.focal_band(50) == "Normal"
    assert C.focal_band(85) == "Tele"
    assert C.focal_band(200) == "Tele"
    assert C.focal_band(299) == "Tele"
    assert C.focal_band(300) == "Super Tele"
    assert C.focal_band(600) == "Super Tele"
    assert C.focal_band(None) is None

def test_wide_open_uses_known_max_not_exif_1_3():
    # R6 writes MaxAperture 1.3 for the 50mm f/1.2; FNumber 1.2 is still wide open.
    assert C.is_wide_open(1.2, 1.2)
    assert C.is_wide_open(1.2, 1.3)  # 0.15 stops
    # 1.4 is just over 1/3 stop from 1.2 (0.44 stops) — not wide open against known max
    assert not C.is_wide_open(1.4, 1.2)
    # At 600mm the Sigma's max is ~6.3; 6.3 and 5.6 (1/3 stop) are wide open, 8 is not
    assert C.is_wide_open(6.3, 6.3)
    assert C.is_wide_open(5.6, 6.3)  # slightly wider than reported max still counts
    assert C.is_wide_open(5.6, 5.6)
    assert not C.is_wide_open(8.0, 6.3)
    assert not C.is_wide_open(None, 1.2)

def test_slow_shutter_is_aware():
    # 50mm, no IS: 1/40 is slow, 1/80 is not
    assert C.is_slow_shutter(1 / 40, 50, False)
    assert not C.is_slow_shutter(1 / 80, 50, False)
    # 50mm with 2 stops of IS: threshold is 1/12.5; 1/125 is not slow
    assert not C.is_slow_shutter(1 / 125, 50, True)
    # 600mm with IS: threshold 1/150. 1/125 is slow, 1/160 is not (typical pan)
    assert C.is_slow_shutter(1 / 125, 600, True)
    assert not C.is_slow_shutter(1 / 160, 600, True)
    # absolute floor
    cfg = {**C.DEFAULTS, "slow_shutter_abs": 1 / 60}
    assert C.is_slow_shutter(1 / 50, 50, True, cfg)
    assert not C.is_slow_shutter(1 / 80, 50, True, cfg)

def test_iso_ec_flags():
    assert C.iso_flag(6399) is None
    assert C.iso_flag(6400) == "High ISO"
    assert C.iso_flag(12799) == "High ISO"
    assert C.iso_flag(12800) == "Very High ISO"
    assert C.iso_flag(None) is None
    assert C.notable_ec(0) is None
    assert C.notable_ec(0.3) is None
    assert C.notable_ec(2 / 3) == "Plus"
    assert C.notable_ec(1) == "Plus"
    assert C.notable_ec(-2 / 3) == "Minus"
    assert C.notable_ec(-1) == "Minus"

def test_shutter_drive_af_flash_orientation():
    assert C.electronic_shutter("Electronic")
    assert C.electronic_shutter("Electronic (Silent)")
    assert not C.electronic_shutter("Electronic First Curtain")
    assert not C.electronic_shutter("Electronic First-Curtain")
    assert not C.electronic_shutter("Mechanical")
    assert not C.electronic_shutter("")
    assert C.high_speed_drive("Continuous, High+")
    assert C.high_speed_drive("Continuous, High")
    assert not C.high_speed_drive("Continuous, Low")
    assert not C.high_speed_drive("Single")
    assert C.af_mode("AI Servo AF (Live View)") == "Servo"
    assert C.af_mode("One-Shot AF") == "One-Shot"
    assert C.af_mode("Manual focus") == "Manual"
    assert C.af_area("Face + Tracking") == "Face + Tracking"
    assert C.af_area("Spot AF") == "Spot"
    assert C.af_area("Large Zone AF: Vertical") == "Large Zone"
    assert C.subject_detect("Animals") == "Animals"
    assert C.subject_detect("Vehicles") == "Vehicles"
    assert C.subject_detect("None") is None
    assert C.eye_on("Auto")
    assert not C.eye_on("Off")
    assert C.flash_fired("Flash fired")
    assert not C.flash_fired("No Flash")
    assert C.orientation_flag("Horizontal (normal)") == "Landscape"
    assert C.orientation_flag("Rotate 270 CW") == "Portrait"
    assert C.orientation_flag("Rotate 90 CW") == "Portrait"
    assert C.orientation_flag(6) == "Portrait"
    assert C.orientation_flag(1) == "Landscape"
    assert C.is_on_off("On (2)") is True
    assert C.is_on_off("Off") is False

def test_keywords_for_and_no_slashes_in_leaves():
    meta = dict(
        lens_short="Canon 50mm f1.2", focal_band="Normal", iso=10000,
        wide_open=True, slow_shutter=False, ec=0, high_speed=True,
        electronic=False, af="Servo", af_area="Face + Tracking",
        subject="Animals", eye=True, is_on=True, flash=False,
        orientation="Portrait", clipped_highlights=True, crushed_shadows=False,
    )
    kws = C.keywords_for(meta)
    assert "General/Camera/Lens/Canon 50mm f1.2" in kws
    assert "General/Camera/Focal/Normal" in kws
    assert "General/Camera/High ISO" in kws
    assert "General/Camera/Very High ISO" not in kws
    assert "General/Camera/Wide Open" in kws
    assert "General/Camera/Drive/High Speed" in kws
    assert "General/Camera/Shutter/Electronic" not in kws
    assert "General/Camera/AF/Servo" in kws
    assert "General/Camera/AF Area/Face + Tracking" in kws
    assert "General/Camera/Subject/Animals" in kws
    assert "General/Camera/Eye Detection" in kws
    assert "General/Camera/IS/On" in kws
    assert "General/Orientation/Portrait" in kws
    assert "General/Quality/Clipped Highlights" in kws
    assert "General/Quality/Crushed Shadows" not in kws
    for k in kws:
        # every path leaf must be slash-free (keyword library uses / as the separator)
        leaf = k.rsplit("/", 1)[-1]
        assert "/" not in leaf

def test_normalize_gig_sample_record():
    r = {
        "LensModel": "EF50mm f/1.2L USM",
        "LensType": "Canon EF 50mm f/1.2L USM",
        "FocalLength": "50.0 mm",
        "MaxAperture": 1.3,
        "FNumber": 1.2,
        "ExposureTime": "1/800",
        "ISO": 2500,
        "ExposureCompensation": 0,
        "ContinuousDrive": "Continuous, High+",
        "ShutterMode": "Electronic First Curtain",
        "FocusMode": "AI Servo AF (Live View)",
        "AFAreaMode": "Face + Tracking",
        "SubjectToDetect": "Animals",
        "EyeDetection": "Auto",
        "ImageStabilization": "On (2)",
        "Orientation": "Horizontal (normal)",
        "Flash": "No Flash",
        "WhiteBalance": "Auto (ambience priority)",
        "PictureStyle": "Standard",
        "HighlightTonePriority": "Off",
        "SerialNumber": 263029002983,
        "SubSecDateTimeOriginal": "2026:06:23 11:20:18.01+00:00",
        "Model": "Canon EOS R6",
    }
    m = C.normalize(r)
    assert m["lens_short"] == "Canon 50mm f1.2"
    assert m["focal_band"] == "Normal"
    assert m["wide_open"] is True
    assert m["iso"] == 2500
    assert m["electronic"] is False
    assert m["high_speed"] is True
    assert m["af"] == "Servo"
    assert m["subject"] == "Animals"
    assert m["is_on"] is True
    assert m["slow_shutter"] is False
    assert m["flash"] is False
    assert m["orientation"] == "Landscape"
    assert m["serial"] == "263029002983"
    assert m["capture_time"].startswith("2026-06-23T11:20:18.01")
    assert m["white_balance"] == "Auto (ambience priority)"
    kws = C.keywords_for(m)
    assert "General/Camera/Shutter/Electronic" not in kws
    assert "General/Camera/High ISO" not in kws
    assert "General/Orientation/Landscape" in kws

def test_normalize_sigma_uses_lensmodel_not_lenstype():
    r = {
        "LensModel": "150-600mm F5-6.3 DG OS HSM | Contemporary 015",
        "LensType": "Sigma 70-200mm f/2.8 DG OS HSM | S or other Sigma Lens",
        "FocalLength": "600.0 mm",
        "MaxAperture": 6.2,
        "FNumber": 6.3,
        "ExposureTime": "1/160",
        "ISO": 200,
        "ExposureCompensation": 0,
        "ContinuousDrive": "Continuous, High+",
        "ShutterMode": "Electronic First Curtain",
        "FocusMode": "AI Servo AF (Live View)",
        "AFAreaMode": "Face + Tracking",
        "SubjectToDetect": "Vehicles",
        "EyeDetection": "Auto",
        "ImageStabilization": "On (2)",
        "Orientation": "Horizontal (normal)",
        "Flash": "No Flash",
        "SerialNumber": 263029002983,
        "SubSecDateTimeOriginal": "2026:11:07 19:00:00.10+00:00",
    }
    m = C.normalize(r)
    assert m["lens_short"] == "Sigma 150-600"
    assert m["focal_band"] == "Super Tele"
    assert m["wide_open"] is True   # 6.3 vs EXIF max 6.2 is ~0.05 stop
    assert m["slow_shutter"] is False  # 1/160 vs IS-aware 1/150
    assert m["subject"] == "Vehicles"
    kws = C.keywords_for(m)
    assert "General/Camera/Lens/Sigma 150-600" in kws
    assert "General/Camera/Focal/Super Tele" in kws
    assert "General/Camera/Subject/Vehicles" in kws
    assert not any("70-200" in k for k in kws)

def test_clip_fractions_synthetic():
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        print("skip test_clip_fractions_synthetic (no PIL/numpy)")
        return
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    arr[:] = 40
    # 2% blown white, 40% crushed
    arr[:2, :, :] = 255
    arr[60:, :, :] = 0
    im = Image.fromarray(arr, "RGB")
    hi, lo = C.clip_fractions(im, 253, 2)
    assert abs(hi - 0.02) < 1e-6
    assert abs(lo - 0.40) < 1e-6
    ch, cs, _, _ = C.clip_flags(im, C.DEFAULTS)
    assert ch is True          # 2% > 0.8%
    assert cs is True          # 40% > 35%
    arr2 = np.zeros((100, 100, 3), dtype=np.uint8)
    arr2[:] = 40
    arr2[:1, :50, :] = 255     # 0.5% blown — below conservative threshold
    im2 = Image.fromarray(arr2, "RGB")
    ch2, cs2, hi2, lo2 = C.clip_flags(im2, C.DEFAULTS)
    assert ch2 is False and cs2 is False

if __name__ == "__main__":
    for fn, f in list(globals().items()):
        if fn.startswith("test_") and callable(f):
            f()
            print("ok", fn)
    print("all passed")
