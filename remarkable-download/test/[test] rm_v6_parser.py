from dataclasses import dataclass
from typing import List, Optional
import math

@dataclass
class Point:
    x: float
    y: float
    pressure: Optional[float] = None


@dataclass
class Stroke:
    tool: Optional[int]
    color: Optional[int]
    width: Optional[float]
    points: List[Point]
    block_index: int

    def is_highlighter(self) -> bool:
        # Heuristic (you can refine this later)
        return (self.tool == 18) or (self.width and self.width > 3)

    def average_pressure(self) -> float:
        pressures = [p.pressure for p in self.points if p.pressure is not None]
        
        if not pressures:
            return 0.0  # or None if you want to signal "no data"
        
        return sum(pressures) / len(pressures)


    def print_average_pressure(self):
        avg = self.average_pressure()
        print(f"Stroke {self.block_index} - Average pressure: {avg:.2f}")


@dataclass
class Rectangle:
    x: float
    y: float
    w: float
    h: float


@dataclass
class Highlight:
    text: str
    color: Optional[int]
    rectangles: List[Rectangle]
    block_index: int

from pathlib import Path
from typing import List, Tuple
import rmscene


def parse_rm_v6(path: Path) -> Tuple[List[Stroke], List[Highlight]]:
    strokes: List[Stroke] = []
    highlights: List[Highlight] = []

    with path.open("rb") as f:
        for i, block in enumerate(rmscene.read_blocks(f)):
            print(f"Reading block {i}")
            item = getattr(block, "item", None)
            if not item:
                print("Skipping block (no item)")
                continue

            val = getattr(item, "value", None)
            if val is None:
                print("Skipping block (no item.value)")
                continue

            # =========================
            # 1. Stroke extraction
            # =========================
            stroke = _parse_stroke(val, i)
            if stroke:
                strokes.append(stroke)
                continue

            # =========================
            # 2. Highlight extraction (GlyphRange)
            # =========================
            highlight = _parse_highlight(val, i)
            if highlight:
                highlights.append(highlight)

    return strokes, highlights

def _parse_stroke(val, block_index: int) -> Optional[Stroke]:
    points = getattr(val, "points", None)
    if not points:
        return None

    parsed_points: List[Point] = []

    for p in points:
        x = getattr(p, "x", None)
        y = getattr(p, "y", None)

        if x is None or y is None:
            continue

        parsed_points.append(
            Point(
                x=x,
                y=y,
                pressure=getattr(p, "pressure", None)
            )
        )

    if not parsed_points:
        return None

    return Stroke(
        tool=getattr(val, "tool", None),
        color=getattr(val, "color", None),
        width=getattr(val, "width", None),
        points=parsed_points,
        block_index=block_index
    )

def _parse_highlight(val, block_index: int) -> Optional[Highlight]:
    # Detect GlyphRange explicitly
    if val.__class__.__name__ != "GlyphRange":
        return None

    text = getattr(val, "text", None)
    if not text:
        return None

    rectangles_raw = getattr(val, "rectangles", [])
    rectangles: List[Rectangle] = []

    for r in rectangles_raw:
        rectangles.append(
            Rectangle(
                x=getattr(r, "x", 0.0),
                y=getattr(r, "y", 0.0),
                w=getattr(r, "w", 0.0),
                h=getattr(r, "h", 0.0),
            )
        )

    return Highlight(
        text=text,
        color=getattr(val, "color", None),
        rectangles=rectangles,
        block_index=block_index
    )

def render_stroke_pressure(stroke, out_path="stroke_pressure.png", scale=1.0, padding=50):
    from PIL import Image, ImageDraw

    pts = stroke.points

    xs = [p.x for p in pts]
    ys = [p.y for p in pts]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    width = int((max_x - min_x) * scale + 2 * padding)
    height = int((max_y - min_y) * scale + 2 * padding)

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    def norm(p):
        return (
            (p.x - min_x) * scale + padding,
            (p.y - min_y) * scale + padding
        )

    for i in range(len(pts) - 1):
        p1 = norm(pts[i])
        p2 = norm(pts[i + 1])

        # pressure → stroke width
        pressure = (pts[i].pressure + pts[i + 1].pressure) / 2
        width = max(1, int(       (pressure/50)  ** 2   )        )  # tune this

        draw.line([p1, p2], fill="black", width=width)

    img.save(out_path)
    print(f"Saved to {out_path}")



from pathlib import Path

# =============================================================================================================================
# IDENTIFIERS
# =============================================================================================================================

GOA_FIRST_PAGE_UUID: str = "9d966e03-3330-47f3-bc61-e884cf9450a9"
GOA_SEVENTH_PAGE_UUID: str = "3c09a2b4-2925-45c0-ae47-627e5c2360b4"

NHAP_FIRST_PAGE_UUID: str = "1e323c02-c480-48b9-bfdb-33aef3d076a4"

# Folder structure (relative, not absolute)
GRAPH_OF_AGENT_DIR: Path = Path("graph_of_agent_RM") / "c08b42a6-5be9-4517-9d63-38ae279538c2"
NHAP_DIR: Path = Path("nhap_rm") / "ac6c6386-7180-4d1e-aa5a-409c47135a3d"


# =============================================================================================================================
# BASE PATH (NO HARDCODED HOME)
# =============================================================================================================================

# Option 1 (recommended): project-relative
BASE_DIR: Path = Path(__file__).resolve().parent / "remarkable-download"

# Option 2 (alternative): configurable via env
# import os
# BASE_DIR: Path = Path(os.getenv("RM_BASE_DIR", Path.cwd() / "remarkable-download"))


# =============================================================================================================================
# PATH RESOLUTION
# =============================================================================================================================

rm_file_path: Path = BASE_DIR / NHAP_DIR / f"{NHAP_FIRST_PAGE_UUID}.rm"
json_output_path: Path = BASE_DIR / "dump.json"
image_output_path: Path = BASE_DIR / "test_render.png"


# =============================================================================================================================
# EXECUTION
# =============================================================================================================================

strokes, highlights = parse_rm_v6(rm_file_path)



for stroke in strokes:
    print("tool", stroke.tool)
    print("color", stroke.color)
    print("width", stroke.width)
    # print(stroke.print_average_pressure())
    # render_stroke_pressure(stroke,out_path=out_path, scale = 1.0, padding = 50)
    print(stroke.points)
    print("block index", stroke.block_index)

for i in range(0, 3):
    print("="*60)
print("Highlights:", highlights)



for h in highlights:
    print(h.text)