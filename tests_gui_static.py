#!/usr/bin/env python3
"""Panel logo / favicon assets and static Content-Type (no live server)."""
from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "gui" / "static"
SERVER = ROOT / "gui" / "server.py"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _static_content_type():
    text = SERVER.read_text()
    start = text.index("def _static_content_type")
    end = text.index("\nclass Handler", start)
    ns = {"Path": Path}
    exec(text[start:end], ns)  # noqa: S102 — load the helper without importing the GUI server
    return ns["_static_content_type"]


class LogoAssets(unittest.TestCase):
    def test_png_sizes(self):
        self.assertEqual(_png_size(STATIC / "logo.png"), (256, 256))
        self.assertEqual(_png_size(STATIC / "favicon.png"), (32, 32))
        self.assertEqual(_png_size(STATIC / "apple-touch-icon.png"), (180, 180))

    def test_pages_reference_icons(self):
        index = (STATIC / "index.html").read_text()
        self.assertIn('href="/favicon.png"', index)
        self.assertIn('href="/apple-touch-icon.png"', index)
        self.assertIn('src="/logo.png"', index)
        self.assertGreaterEqual(index.count('src="/logo.png"'), 2)  # login + header
        self.assertIn('id="loginView"', index)
        login_at = index.index('id="loginView"')
        header_at = index.index("<header>")
        self.assertIn('src="/logo.png"', index[login_at:header_at])
        self.assertIn('src="/logo.png"', index[header_at:header_at + 800])
        for name in ("review.html", "people.html"):
            html = (STATIC / name).read_text()
            self.assertIn('href="/favicon.png"', html)
            self.assertIn('href="/apple-touch-icon.png"', html)


class StaticContentType(unittest.TestCase):
    def test_png_is_image_png(self):
        fn = _static_content_type()
        self.assertEqual(fn("logo.png"), "image/png")
        self.assertEqual(fn(STATIC / "favicon.png"), "image/png")
        self.assertEqual(fn("apple-touch-icon.png"), "image/png")
        self.assertTrue(fn("style.css").startswith("text/css"))
        self.assertTrue(fn("app.js").startswith("application/javascript"))
        self.assertIn("text/html", fn("index.html"))

    def test_serve_static_uses_helper_and_is_unauthenticated(self):
        text = SERVER.read_text()
        self.assertIn("ctype = _static_content_type(path)", text)
        # Static files (logo, favicon) are served after the /api/ auth gate, not behind it.
        api = text.index('if path.startswith("/api/"):')
        static = text.index("# static")
        self.assertGreater(static, api)
        self.assertIn('return self._serve_static(path)', text[static:])


if __name__ == "__main__":
    unittest.main()
