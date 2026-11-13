#!/usr/bin/env python3
"""Compatibility alias. This Lightroom edition writes XMP sidecars via apply_xmp."""
from apply_xmp import (  # noqa: F401
    HOME,
    IMAGE_EXT,
    apply_proposals,
    sidecar_path,
    snap_folder,
    write_snap,
)
