"""
rm_sync — reMarkable document sync and render tool.
"""

import sys
from pathlib import Path

# Ensure this package directory is on sys.path so flat imports
# (e.g. ``from client import RsyncClient``) resolve correctly
# both when running directly and when imported as a package.
_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)
