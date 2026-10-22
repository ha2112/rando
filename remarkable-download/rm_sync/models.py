"""
models.py
=========
All data-classes and enumerations used across the rm-rebuilder pipeline.

All classes use frozen-friendly Python dataclasses with full type hints.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import FrozenSet, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pen / tool type identifiers
# ---------------------------------------------------------------------------

class PenType(IntEnum):
    """Tool identities verified against rmscene.scene_items.Pen."""

    PAINTBRUSH_1 = 0
    PENCIL_1 = 1
    BALLPOINT_1 = 2
    MARKER_1 = 3
    FINELINER_1 = 4
    HIGHLIGHTER = 5
    ERASER = 6
    MECH_PENCIL_1 = 7
    ERASER_AREA = 8
    PAINTBRUSH_2 = 12
    MECH_PENCIL_2 = 13
    PENCIL_2 = 14
    BALLPOINT_2 = 15
    MARKER_2 = 16
    FINELINER_2 = 17
    HIGHLIGHTER_2 = 18
    CALLIGRAPHY = 21
    SHADER = 23


HIGHLIGHT_TOOL_IDS: FrozenSet[int] = frozenset({5, 18})


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------

@dataclass
class PageInfo:
    """One entry from the ``cPages.pages`` array in the ``.content`` JSON.

    Attributes:
        page_number: 0-based index of this page in the *original* cPages array
            (CRDT source order, before any sorting).  Used to disambiguate note
            pages that have no ``redir``.
        uuid:        Page UUID; also the stem of the corresponding ``.rm`` file
            (e.g. ``"a1b2c3d4-…"`` → ``<uuid-dir>/a1b2c3d4-….rm``).
        redir:       0-based index into the base PDF that this page displays.
            ``None`` for blank note pages that are not backed by a PDF page
            (bug fix #3 — these must be inserted as blank pages in the correct
            position, not appended at the end).
        template:    Name of the background template (e.g. ``"Blank"``,
            ``"Lined"``).  ``None`` when absent.
        modified:    Last-modified UNIX timestamp from the content JSON.
            ``None`` when absent or unparseable.
    """

    page_number: int
    uuid: str
    redir: Optional[int]
    template: Optional[str]
    modified: Optional[int]
    orientation: str = "portrait"  

    @property
    def is_landscape(self) -> bool:
        return self.orientation == "landscape"


    @property
    def is_note_page(self) -> bool:
        """Return ``True`` if this page is a blank note page (no base PDF page).

        A note page inserted mid-PDF must become a blank page inserted at the
        correct position in the output document (bug fix #3).

        Returns:
            bool: ``True`` when ``redir`` is ``None``.
        """
        return self.redir is None


@dataclass
class DocumentMeta:
    """Document-level metadata parsed from the ``.metadata`` JSON file.

    Attributes:
        uuid:     Document UUID (the filename stem under xochitl/).
        name:     Human-visible document name (``visibleName`` JSON key).
        doc_type: ``"DocumentType"`` for documents, ``"CollectionType"`` for
            folders.
        parent:   UUID of the parent folder, or ``None`` for root-level items.
        version:  Integer schema version from the metadata file.
        has_pdf:  ``True`` when a ``.pdf`` file was present in the local
            workspace after download.  Determines whether to open a base PDF
            or create a blank notebook.
    """

    uuid: str
    name: str
    doc_type: str
    parent: Optional[str]
    version: int
    has_pdf: bool


@dataclass
class Point:
    x: float
    y: float
    pressure: Optional[float] = None
    width: Optional[float] = None
    speed: Optional[float] = None
    direction: Optional[float] = None

@dataclass
class Stroke:
    """Decoded measurements remain in rmscene units even after placement.

    geometry_scale transforms the circular scene nib along with its center.
    Pressure is raw (0..255), width is quarter scene units; direction is not tilt.
    """

    tool: Optional[int]
    color: Optional[int]
    thickness_scale: Optional[float]
    points: List[Point]
    block_index: int
    color_rgba: Optional[Tuple[int, int, int, int]] = None
    geometry_scale: Tuple[float, float] = (1.0, 1.0)
    scene_order: int = 0
    item_id: Optional[str] = None
    scene_offset: Tuple[float, float] = (0.0, 0.0)

    @property
    def is_highlight(self) -> bool:
        return self.tool in HIGHLIGHT_TOOL_IDS


@dataclass
class Highlight:
    text: str
    color: Optional[int]
    rectangles: List[Rectangle]
    block_index: int
    color_rgba: Optional[Tuple[int, int, int, int]] = None
    scene_order: int = 0
    item_id: Optional[str] = None
    scene_offset: Tuple[float, float] = (0.0, 0.0)

@dataclass
class Rectangle:
    x: float
    y: float
    w: float
    h: float