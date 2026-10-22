"""Measured circular nib geometry for the first vector rendering slice."""
import math
from statistics import median

from config import HIGHLIGHT_OPACITY_PLAIN, pen_color_to_rgb


def stroke_widths(stroke):
    # rmscene normalizes both point encodings to quarter scene units.
    valid = [p.width / 4 for p in stroke.points
             if p.width is not None and math.isfinite(p.width) and p.width > 0]
    scale = stroke.thickness_scale
    fallback = 2 * scale if scale is not None and math.isfinite(scale) and scale > 0 else 2
    if stroke.tool not in (2, 15):
        # Fineliner selected size is already baked into recorded width.
        # Other families retain a constant-width approximation in this slice.
        return [median(valid) if valid else fallback] * len(stroke.points)
    # Ballpoint uses its local measurement; pressure must not be applied twice.
    return [p.width / 4 if p.width is not None and math.isfinite(p.width) and p.width > 0
            else fallback for p in stroke.points]


def ink_color(item, highlight=False):
    if item.color_rgba is not None:
        r, g, b, a = item.color_rgba
        return (r / 255, g / 255, b / 255), a / 255
    return pen_color_to_rgb(item.color), HIGHLIGHT_OPACITY_PLAIN if highlight else 1.0


def glyph_highlight_color(highlight):
    # Indexed snapped highlights can carry an opaque-black placeholder (13 observed).
    rgba = highlight.color_rgba
    if rgba == (0, 0, 0, 255) and highlight.color in (3, 4, 5):
        return pen_color_to_rgb(highlight.color), HIGHLIGHT_OPACITY_PLAIN
    color, opacity = ink_color(highlight, highlight=True)
    # Full RGBA alpha defines the color; the highlight still needs coverage opacity.
    if rgba is not None and rgba[3] == 255:
        opacity = HIGHLIGHT_OPACITY_PLAIN
    return color, opacity


def painted_bounds(stroke):
    sx, sy = stroke.geometry_scale
    widths = stroke_widths(stroke)
    return (min(p.x - w * abs(sx) / 2 for p, w in zip(stroke.points, widths)),
            min(p.y - w * abs(sy) / 2 for p, w in zip(stroke.points, widths)),
            max(p.x + w * abs(sx) / 2 for p, w in zip(stroke.points, widths)),
            max(p.y + w * abs(sy) / 2 for p, w in zip(stroke.points, widths)))


def draw_ink(page, stroke):
    """One nonzero-winding fill per variable stroke avoids alpha join seams."""
    if not stroke.points:
        return
    widths = stroke_widths(stroke)
    color, opacity = ink_color(stroke, stroke.is_highlight)
    sx, sy = stroke.geometry_scale
    shape = page.new_shape()
    points = [(p.x, p.y) for p in stroke.points]
    if sx == sy and min(widths) == max(widths) and len(set(points)) > 1:
        # Exact centerline and round joins, without the old control-point smoothing.
        shape.draw_polyline(points)
        shape.finish(color=color, width=widths[0] * abs(sx),
                     lineCap=1, lineJoin=1, closePath=False, stroke_opacity=opacity)
    else:
        def polygon(vertices):
            # All subpaths use the same winding, including at self intersections.
            area = sum(a[0] * b[1] - b[0] * a[1]
                       for a, b in zip(vertices, vertices[1:] + vertices[:1]))
            if area < 0:
                vertices.reverse()
            shape.draw_polyline(vertices + vertices[:1])

        # Disks and connecting trapezoids form a continuous swept circular nib.
        for (x, y), w in zip(points, widths):
            polygon([(x + math.cos(i * math.tau / 32) * w * sx / 2,
                      y + math.sin(i * math.tau / 32) * w * sy / 2)
                     for i in range(32)])
        for a, b, wa, wb in zip(points, points[1:], widths, widths[1:]):
            dx, dy = (b[0] - a[0]) / sx, (b[1] - a[1]) / sy
            length = math.hypot(dx, dy)
            if length == 0:
                continue
            nx, ny = -dy / length * sx / 2, dx / length * sy / 2
            polygon([(a[0] + nx * wa, a[1] + ny * wa),
                     (b[0] + nx * wb, b[1] + ny * wb),
                     (b[0] - nx * wb, b[1] - ny * wb),
                     (a[0] - nx * wa, a[1] - ny * wa)])
        shape.finish(color=None, fill=color, fill_opacity=opacity,
                     even_odd=False, closePath=False)
    shape.commit()
