#!/usr/bin/env python3
"""Runtime folder resolution for Capture One Shoot Sorter.

Temp directories only. No NAS, no Capture One, no live runtime writes.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_home


class AppHome(unittest.TestCase):
    def test_new_install_default(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            with mock.patch.dict(os.environ, {}, clear=True):
                # Ensure override keys are absent even if the parent env set them.
                os.environ.pop("LOCALAI_HOME", None)
                os.environ.pop("SHOOT_SORTER_C1_HOME", None)
                self.assertEqual(app_home.home(base), base / "shoot-sorter-c1")

    def test_existing_localai_is_kept(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            legacy = base / "localai"
            legacy.mkdir()
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop("LOCALAI_HOME", None)
                os.environ.pop("SHOOT_SORTER_C1_HOME", None)
                self.assertEqual(app_home.home(base), legacy)

    def test_localai_home_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            (base / "localai").mkdir()
            custom = base / "custom"
            with mock.patch.dict(os.environ, {"LOCALAI_HOME": str(custom)}, clear=True):
                self.assertEqual(app_home.home(base), custom)

    def test_shoot_sorter_home_used_without_localai_home(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            (base / "localai").mkdir()
            custom = base / "custom-c1"
            env = {"SHOOT_SORTER_C1_HOME": str(custom)}
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ.pop("LOCALAI_HOME", None)
                self.assertEqual(app_home.home(base), custom)

    def test_localai_home_wins_over_shoot_sorter_home(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            a = base / "a"
            b = base / "b"
            with mock.patch.dict(
                os.environ,
                {"LOCALAI_HOME": str(a), "SHOOT_SORTER_C1_HOME": str(b)},
                clear=True,
            ):
                self.assertEqual(app_home.home(base), a)

    def test_product_names(self):
        self.assertEqual(app_home.APP_NAME, "Capture One Shoot Sorter")
        self.assertEqual(app_home.LAUNCHD_LABEL, "com.shootsorter.c1.gui")
        self.assertIn("com.trips.localai.gui", app_home.launchd_labels())
        self.assertIn("com.grok.localai.gui", app_home.launchd_labels())
        self.assertEqual(app_home.DEFAULT_KEYWORD_LIBRARY, "Trip Keywords")
        self.assertIn("Grok Keywords", app_home.LEGACY_KEYWORD_LIBRARIES)
        self.assertIn("Trips Local AI", app_home.LEGACY_CULL_GROUPS)


class PanelTitles(unittest.TestCase):
    def test_index_uses_new_name(self):
        html = Path(__file__).resolve().parent.joinpath("gui/static/index.html").read_text()
        self.assertIn("<title>Capture One Shoot Sorter</title>", html)
        self.assertIn("<h1>Capture One Shoot Sorter</h1>", html)
        self.assertNotIn("Trips Local AI", html)


if __name__ == "__main__":
    unittest.main()
