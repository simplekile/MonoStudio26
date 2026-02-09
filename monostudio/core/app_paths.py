"""
Base path for app resources (monostudio_data, fonts).
Works in development (repo root) and when frozen (PyInstaller onefile/onedir).
"""

from __future__ import annotations

import sys
from pathlib import Path


def get_app_base_path() -> Path:
    """
    Root directory containing monostudio_data/ and fonts/.
    - Development: repo root (parent of monostudio/).
    - Frozen (PyInstaller): sys._MEIPASS (onedir/onefile extracted files).
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    # Development: from this file monostudio/core/app_paths.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]
