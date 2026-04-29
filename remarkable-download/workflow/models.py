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
    """reMarkable tool/pen type identifiers as used in rmscene v6 Pen enum.

    Values match the ``Pen`` IntEnum in the ``rmscene`` library source
    (``scene_stream.py``).  Both version-1 (legacy) and version-2 (current
    firmware) variants are listed where they differ.

    Attributes:
        PAINTBRUSH_1:        Original brush (pressure-sensitive).
        PENCIL_1:            Original pencil.
        BALLPOINT_1:         Original ballpoint.
        MARKER_1:            Original marker (felt tip).
        FINELINER_1:         Original fineliner.
        HIGHLIGHTER:         Primary highlighter tool (firmware v2+). **Key for
                             bug fix #4** — strokes with this tool ID must be
                             rendered as translucent wide highlights.
        ERASER:              Standard eraser.
        MECH_PENCIL_1:       Mechanical pencil v1.
        ERASER_AREA:         Rectangle eraser.
        PAINTBRUSH_2:        Updated brush.
        MECH_PENCIL_2:       Mechanical pencil v2.
        PENCIL_2:            Updated pencil.
        BALLPOINT_2:         Updated ballpoint.
        FINELINER_2:         Updated fineliner.
        MARKER_2:            Updated marker.
        HIGHLIGHTER_2:       Updated highlighter. Also covered by bug fix #4.
        CALLIGRAPHY:         Calligraphy pen.
        SHADER:              Shader / fill tool.
    """

    PAINTBRUSH_1  = 12
    PENCIL_1      = 14
    BALLPOINT_1   = 15
    MARKER_1      = 16
    FINELINER_1   = 17
    HIGHLIGHTER   = 18    # ← primary highlighter (bug fix #4)
    ERASER        = 0
    MECH_PENCIL_1 = 13
    ERASER_AREA   = 0
    PAINTBRUSH_2  = 12
    MECH_PENCIL_2 = 13
    PENCIL_2      = 14
    BALLPOINT_2   = 15
    FINELINER_2   = 17
    MARKER_2      = 16
    HIGHLIGHTER_2 = 18   # ← updated highlighter (bug fix #4)
    CALLIGRAPHY   = 21
    SHADER        = 23


# ---------------------------------------------------------------------------
# Highlight detection sets
# ---------------------------------------------------------------------------

#: Tool IDs whose strokes should be rendered as highlights (bug fix #4).
HIGHLIGHT_TOOL_IDS: FrozenSet[int] = frozenset({
    int(PenType.HIGHLIGHTER),    
    int(PenType.HIGHLIGHTER_2),  
})

#: Color indices that represent highlight colours (yellow=3, green=4, pink=5).
#: Used as a secondary detection path when the tool ID alone is ambiguous
#: (e.g., older .rm files that encode the highlighter with a different tool ID
#: but always use one of the three standard highlight colours).
HIGHLIGHT_COLOR_IDS: FrozenSet[int] = frozenset({3, 4, 5})


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

@dataclass
class Stroke:
    """A single decoded stroke from an ``.rm`` binary file.

    Coordinates in ``points`` are in whichever space the producing code
    placed them (raw device units from ``StrokeProcessor.decode_rm_file``, or
    transformed PDF-point coordinates from ``StrokeProcessor.scale_strokes``).

    Attributes:
        tool:            Integer tool/pen ID (see :class:`PenType`).
        color:           Integer colour index (see ``PEN_COLOR_MAP`` in
            ``config.py``).
        thickness_scale: Pen pressure / size multiplier baked into the stroke
            by the device.  Typical range 0.5 – 3.0.
        points:          Ordered list of ``(x, y)`` coordinate pairs.
    """

    tool: Optional[int]
    color: Optional[int]
    width: Optional[float]
    points: List[Point]
    block_index: int

    @property
    def is_highlight(self) -> bool:
        """Return ``True`` if this stroke was drawn with a highlighter tool.

        Detection uses two independent signals joined by OR, making it robust
        against tool-ID variations across firmware versions (bug fix #4):

        1. ``self.tool`` is in :data:`HIGHLIGHT_TOOL_IDS` (primary check).
        2. ``self.color`` is in :data:`HIGHLIGHT_COLOR_IDS` — yellow, green, or
           pink (secondary / fallback check).

        Returns:
            bool: ``True`` when either signal fires.
        """
        return (
            self.tool in HIGHLIGHT_TOOL_IDS
            or self.color in HIGHLIGHT_COLOR_IDS
        )


@dataclass
class Highlight:
    text: str
    color: Optional[int]
    rectangles: List[Rectangle]
    block_index: int

@dataclass
class Rectangle:
    x: float
    y: float
    w: float
    h: float