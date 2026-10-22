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
    "wifi-hust-b1": "remarkable-wifi-hust-b1"
}

PROFILE_ORDER: list[str] = ["hotspot", "home", "wifi-hust-b1", "usb"]

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

RM_WIDTH: float = 1920.0 
RM_HEIGHT: float = 2560.0 

# Derived dimensions
BLANK_PAGE_WIDTH_PT: float = RM_WIDTH
BLANK_PAGE_HEIGHT_PT: float = RM_HEIGHT
RM_HALF_WIDTH: float = RM_WIDTH/2.0 
RM_HEIGHT_OFFSET: float = 0

SCALE: float = 72.0 / 227.0  # Native Pen fixture export transform.

# =============================================================================================================================
# PEN RENDERING CONFIGURATION
# =============================================================================================================================

# Line width
BASE_LINE_WIDTH: float = 1.0
MIN_LINE_WIDTH: float = 1.0

# Highlight opacity
HIGHLIGHT_OPACITY_BLEND: float = 0.75
HIGHLIGHT_OPACITY_PLAIN: float = 0.35

# Highlight width behavior
HIGHLIGHT_WIDTH_MIN: float = 10.0
HIGHLIGHT_WIDTH_SCALE: float = 15.0

# RGB bytes measured from the supplied native Pen test export.
PEN_COLOR_MAP: Dict[int, Tuple[float, float, float]] = {
    key: tuple(channel / 255 for channel in rgb)
    for key, rgb in {
        0: (0, 0, 0), 1: (144, 144, 144), 2: (255, 255, 255),
        3: (254, 253, 96), 4: (169, 250, 92), 5: (255, 85, 207),
        6: (48, 74, 224), 7: (194, 49, 50), 8: (0, 0, 0),
        10: (145, 218, 113), 11: (116, 210, 232),
        12: (192, 127, 210), 13: (250, 231, 25),
    }.items()
}
DEFAULT_PEN_COLOR: Tuple[float, float, float] = (0.0, 0.0, 0.0)


def pen_color_to_rgb(color_int: int) -> Tuple[float, float, float]:
    # ID 9 requires explicit RGBA; unknown/missing colors fall back deterministically.
    return PEN_COLOR_MAP.get(color_int, DEFAULT_PEN_COLOR)

# =============================================================================================================================
# MISC
# =============================================================================================================================

# Known document UUIDs (user-defined references)
UUIDS: list[str] = [
    # "c08b42a6-5be9-4517-9d63-38ae279538c2",     # graph of agent
    # "ac6c6386-7180-4d1e-aa5a-409c47135a3d",     # nhap copy
    # "18bb2b29-03a8-47d9-aa03-813a02e168ec",     # theory note art
    # "fcf5d7ba-95e1-421b-a7d8-e03f1d634897",     # piano helper
    # "4db1e9a6-4e7b-4e6a-a8dc-c9cbc1f907ca",     # learning multi agent coordination for enhancing target coverage in dns
    # "4c4df02c-0609-401f-8278-36f500b0b35e",     # Anthropic's paper about natural language autoencoder
    # "2b683e50-82bf-425d-95a2-21dd7909c84f",     # how to paper
    # "886c158f-84d5-40ba-9958-b081cb792cb9",     # CGM code graph model
    # "1294ca90-df87-48de-8c11-d24d1d80cfde",     # attention is all you need     
]
