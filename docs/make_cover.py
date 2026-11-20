#!/usr/bin/env python3
"""Textless cover: a sleeping database under a ceiling, with a broom gathering
idle resources. Palette from publishing-kit's make-cover.py.

    python3 docs/make_cover.py      # writes docs/devto-cover.<hash>.jpg and docs/builder-cover.<hash>.jpg
"""
import hashlib
import io
import math
import pathlib
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SURFACE = (26, 26, 25)
PAPER = (236, 234, 226)
PAPER_2 = (200, 198, 188)
INK_3 = (128, 127, 120)
RULE = (58, 58, 55)
BLUE = (57, 135, 229)
ORANGE = (217, 89, 38)
OUT = pathlib.Path(__file__).resolve().parent


def render(w: int, h: int) -> Image.Image:
    W, H = w * 2, h * 2
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    paint(None, ImageDraw.Draw(glow), W, H)
    glow = glow.filter(ImageFilter.GaussianBlur(40 * H / 578))
    img = ImageChops.add(Image.new("RGB", (W, H), SURFACE), glow)
    paint(ImageDraw.Draw(img), None, W, H)
    return img.resize((w, h), Image.LANCZOS)


class _Null:
    def __getattr__(self, _):
        return lambda *a, **k: None


def paint(d, g, W, H):
    d = d or _Null()
    g = g or _Null()
    u = H / 578  # layout unit: 1 at dev.to height
    cx, cy = W * 0.60, H * 0.60

    # Ceiling: the most the database can bill. A dashed orange line above the cylinder.
    ceil_y = cy - 205 * u
    x = W * 0.38
    while x < W * 0.80:
        d.line([(x, ceil_y), (x + 26 * u, ceil_y)], fill=ORANGE, width=int(5 * u))
        g.line([(x, ceil_y), (x + 26 * u, ceil_y)], fill=(90, 40, 20), width=int(16 * u))
        x += 44 * u

    # Database cylinder, asleep: paper body with blue bands.
    rw, rh, body = 120 * u, 34 * u, 190 * u
    top = cy - body / 2
    g.ellipse([cx - rw - 30 * u, top - 30 * u, cx + rw + 30 * u, top + body + rh + 30 * u], fill=(30, 50, 80))
    d.rectangle([cx - rw, top, cx + rw, top + body], fill=PAPER)
    d.ellipse([cx - rw, top + body - rh, cx + rw, top + body + rh], fill=PAPER)
    for i in (1, 2):
        yb = top + body * i / 3
        d.arc([cx - rw, yb - rh, cx + rw, yb + rh], 0, 180, fill=BLUE, width=int(7 * u))
    d.ellipse([cx - rw, top - rh, cx + rw, top + rh], fill=PAPER_2)
    d.ellipse([cx - rw + 10 * u, top - rh + 8 * u, cx + rw - 10 * u, top + rh - 8 * u], fill=PAPER)

    # Sleep: three rising rings, smaller as they go up.
    for i, r in enumerate((15, 11, 8)):
        x0 = cx + rw + 30 * u + i * 34 * u
        y0 = top - 22 * u - i * 30 * u
        d.ellipse([x0 - r * u, y0 - r * u, x0 + r * u, y0 + r * u], outline=BLUE, width=int(4 * u))

    # Moon, top right, under the ceiling's right end.
    mx, my, mr = W * 0.90, H * 0.17, 40 * u
    g.ellipse([mx - mr * 1.8, my - mr * 1.8, mx + mr * 1.8, my + mr * 1.8], fill=(60, 60, 55))
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=PAPER)
    d.ellipse([mx - mr + 22 * u, my - mr - 10 * u, mx + mr + 22 * u, my + mr - 10 * u], fill=SURFACE)

    # Idle resources, dim outlines scattered on the floor at left.
    rnd = random.Random(7)
    floor = cy + body / 2 + 20 * u
    shapes = []
    while len(shapes) < 11:
        sx = W * 0.07 + rnd.random() * W * 0.24
        sy = floor - 18 * u - rnd.random() * 140 * u
        if all(math.hypot(sx - a, sy - b) > 46 * u for a, b, *_ in shapes):
            shapes.append((sx, sy, rnd.choice("sch"), rnd.choice((ORANGE, INK_3, INK_3))))
    for sx, sy, kind, col in shapes:
        r = 13 * u
        if kind == "s":
            d.rectangle([sx - r, sy - r, sx + r, sy + r], outline=col, width=int(4 * u))
        elif kind == "c":
            d.ellipse([sx - r, sy - r, sx + r, sy + r], outline=col, width=int(4 * u))
        else:
            pts = [(sx + r * math.cos(math.pi / 3 * k), sy + r * math.sin(math.pi / 3 * k)) for k in range(6)]
            d.polygon(pts, outline=col, width=int(4 * u))

    # Broom: a handle leaning in from the right of the pile, bristles sweeping left.
    bx, by = W * 0.36, floor + 4 * u
    d.line([(bx + 110 * u, by - 250 * u), (bx + 12 * u, by - 40 * u)], fill=PAPER_2, width=int(9 * u))
    bristles = [(bx - 40 * u, by), (bx + 52 * u, by), (bx + 30 * u, by - 46 * u), (bx - 6 * u, by - 46 * u)]
    d.polygon(bristles, fill=ORANGE)
    for k in range(7):
        t = k / 6
        x0 = bx - 40 * u + t * 92 * u
        d.line([(x0, by), (bx - 6 * u + t * 36 * u, by - 46 * u)], fill=(170, 70, 30), width=int(2 * u))

    # Floor line.
    d.line([(W * 0.05, floor + 6 * u), (W * 0.95, floor + 6 * u)], fill=RULE, width=int(3 * u))




for name, (w, h) in {"devto-cover": (1376, 578), "builder-cover": (1200, 675)}.items():
    buf = io.BytesIO()
    render(w, h).save(buf, "JPEG", quality=90)
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:8]
    path = OUT / f"{name}.{digest}.jpg"
    path.write_bytes(buf.getvalue())
    print(f"wrote {path.name}  {w}x{h}  {len(buf.getvalue()) // 1024} KB")
