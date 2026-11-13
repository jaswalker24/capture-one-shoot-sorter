#!/usr/bin/env python3
"""Runtime folder and product names for Capture One Shoot Sorter.

New installs use ~/shoot-sorter-c1. If ~/localai already exists, that
folder is kept so a live install is not moved. LOCALAI_HOME (or
SHOOT_SORTER_C1_HOME) still overrides both.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Capture One Shoot Sorter"
DEFAULT_HOME_DIR = "shoot-sorter-c1"
LEGACY_HOME_DIR = "localai"
LAUNCHD_LABEL = "com.shootsorter.c1.gui"
LEGACY_LAUNCHD_LABELS = ("com.trips.localai.gui", "com.grok.localai.gui")
PANEL_BUNDLE_ID = "com.shootsorter.c1.panel"
LEGACY_PANEL_BUNDLE_IDS = ("com.trips.localai.panel", "com.grok.localai.panel")
DOCK_APP_NAME = "Capture One Shoot Sorter"
DEFAULT_KEYWORD_LIBRARY = "Trip Keywords"
LEGACY_KEYWORD_LIBRARIES = ("Grok Keywords",)
DEFAULT_CULL_GROUP = "Capture One Shoot Sorter"
LEGACY_CULL_GROUPS = ("Trips Local AI", "Trip Local AI", "Grok Local AI")
ENV_HOME_KEYS = ("LOCALAI_HOME", "SHOOT_SORTER_C1_HOME")


def home(path_home: Path | None = None) -> Path:
    """Return the runtime folder, honouring env vars and an existing ~/localai."""
    for key in ENV_HOME_KEYS:
        val = os.environ.get(key)
        if val:
            return Path(val)
    base = path_home if path_home is not None else Path.home()
    legacy = base / LEGACY_HOME_DIR
    if legacy.is_dir():
        return legacy
    return base / DEFAULT_HOME_DIR


def launchd_labels() -> tuple[str, ...]:
    return (LAUNCHD_LABEL,) + LEGACY_LAUNCHD_LABELS
