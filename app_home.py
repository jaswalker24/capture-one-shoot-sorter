#!/usr/bin/env python3
"""Runtime paths and names for Trips Local AI for Lightroom."""
import os
from pathlib import Path

APP_NAME = "Trips Local AI for Lightroom"
DEFAULT_HOME_DIR = "localai-lr"
DEFAULT_PORT = 8766
LAUNCHD_LABEL = "com.trips.localai-lr.gui"
PANEL_BUNDLE_ID = "com.trips.localai-lr.panel"
DOCK_APP_NAME = "Trips Local AI for Lightroom"


def home() -> Path:
    return Path(os.environ.get("LOCALAI_HOME") or (Path.home() / DEFAULT_HOME_DIR))


def gui_port() -> int:
    return int(os.environ.get("LOCALAI_GUI_PORT", str(DEFAULT_PORT)))
