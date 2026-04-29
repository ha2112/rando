"""
config.py
=========
Centralized configuration for the reMarkable sync tool.

This module defines:
- SSH connection settings
- Remote/local filesystem paths
- Canvas geometry constants
- Pen rendering parameters
- Utility helpers

Edit values here to customize behavior.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Dict, Tuple


# =============================================================================================================================
# SSH CONFIGURATION
# =============================================================================================================================


SSH_CONNECT_TIMEOUT: int = 15
SSH_BANNER_TIMEOUT: int = 20
SCP_TIMEOUT: int = 60

PROFILES: dict[str, str] = {
    "usb": "remarkable-usb",
    "home": "remarkable-home",
    "hotspot": "remarkable-hotspot",
}

PROFILE_ORDER: list[str] = ["usb", "home", "hotspot"]

# Manual override (optional)
MANUAL_HOST: str | None = None
MANUAL_USER: str = "root"
MANUAL_PASSWORD: str | None = None  # Prefer SSH key auth
MANUAL_SSH_KEY: str | None = None   # Path to private key
MANUAL_PORT: int = 22


# =============================================================================================================================
# FILESYSTEM PATHS
# =============================================================================================================================


# Remote paths (reMarkable)
RM_ROOT: str = "/home/root/.local/share/remarkable/xochitl"
TEMPLATE_REMOTE_DIR: str = "/usr/share/remarkable/templates"

# Local paths
_BASE_DIR: Path = Path.home() / "Downloads" / "RemarkableSync"

DONE_DIR: Path = _BASE_DIR / "done"
LOG_DIR: Path = _BASE_DIR / "logs"
TEMPLATE_DIR: Path = _BASE_DIR / "templates"
STATE_FILE: Path = _BASE_DIR / ".ssh_state.json"

# Ensure required directories exist
for directory in (DONE_DIR, LOG_DIR, TEMPLATE_DIR):
    directory.mkdir(parents=True, exist_ok=True)


# =============================================================================================================================
# CANVAS CONFIGURATION
# =============================================================================================================================

# Calibration factor used to fine-tune the effective canvas resolution.
# Adjust this value if stroke alignment or scaling appears incorrect.
_X_FACTOR: float = 0.0

# Base portrait canvas dimensions (units ≈ PDF points at 72 DPI)
RM_WIDTH: float = 1920.0 - (_X_FACTOR * 3)
RM_HEIGHT: float = 2560.0 - (_X_FACTOR * 4)

# Derived dimensions
BLANK_PAGE_WIDTH_PT: float = RM_WIDTH
BLANK_PAGE_HEIGHT_PT: float = RM_HEIGHT
RM_HALF_WIDTH: float = RM_WIDTH / 2.0

# =============================================================================================================================
# PEN RENDERING CONFIGURATION
# =============================================================================================================================

# Line width
BASE_LINE_WIDTH: float = 1.5
MIN_LINE_WIDTH: float = 1.0

# Highlight opacity
HIGHLIGHT_OPACITY_BLEND: float = 0.75
HIGHLIGHT_OPACITY_PLAIN: float = 0.35

# Highlight width behavior
HIGHLIGHT_WIDTH_MIN: float = 10.0
HIGHLIGHT_WIDTH_SCALE: float = 15.0

# Static pen colors (RGB in range [0, 1])
PEN_COLOR_MAP: Dict[int, Tuple[float, float, float]] = {
    0: (0.00, 0.00, 0.00),  # Black
    1: (0.50, 0.50, 0.50),  # Gray
    2: (1.00, 1.00, 1.00),  # White
    6: (0.00, 0.40, 1.00),  # Blue
    7: (0.90, 0.00, 0.00),  # Red
}

DEFAULT_PEN_COLOR: Tuple[float, float, float] = (0.0, 0.0, 0.0)

# Dynamic highlight colors (used when color_int == 9)
HIGHLIGHT_COLOR_POOL: list[Tuple[float, float, float]] = [
    (1.0, 0.60, 0.80),   # Light pink
    (1.0, 0.50, 0.00),   # Orange
    (0.10, 0.78, 0.78),  # Teal
]

def pen_color_to_rgb(color_int: int) -> Tuple[float, float, float]:
    """
    Convert a reMarkable pen color code to an RGB tuple.

    Args:
        color_int: Integer color code from reMarkable stroke data.

    Returns:
        Tuple[float, float, float]: RGB values in range [0, 1].

    Notes:
        - Color code 9 is treated as a highlight and mapped randomly.
        - Unknown codes fallback to DEFAULT_PEN_COLOR.
    """
    if color_int == 9:
        return random.choice(HIGHLIGHT_COLOR_POOL)
    return PEN_COLOR_MAP.get(color_int, DEFAULT_PEN_COLOR)

# =============================================================================================================================
# MISC
# =============================================================================================================================

# Known document UUIDs (user-defined references)
UUIDS: list[str] = [
    "c08b42a6-5be9-4517-9d63-38ae279538c2",  # graph of agent
    "ac6c6386-7180-4d1e-aa5a-409c47135a3d",  # nhap copy
    "18bb2b29-03a8-47d9-aa03-813a02e168ec",  # theory note art
    "fcf5d7ba-95e1-421b-a7d8-e03f1d634897",  # piano helper
]
