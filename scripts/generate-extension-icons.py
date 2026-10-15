from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "extension" / "assets" / "icons"


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def cubic(p0, p1, p2, p3, steps=24):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def rounded_rect_alpha(x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float) -> float:
    if x < left or x > right or y < top or y > bottom:
        return 0.0
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    dx = x - cx
    dy = y - cy
    dist = (dx * dx + dy * dy) ** 0.5
    return max(0.0, min(1.0, radius + 0.5 - dist))


def point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def line_distance(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    wx, wy = x - ax, y - ay
    denom = vx * vx + vy * vy
    t = 0 if denom == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    px, py = ax + t * vx, ay + t * vy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def polyline_distance(x: float, y: float, pts: list[tuple[float, float]]) -> float:
    return min(line_distance(x, y, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    a = sa / 255.0
    out_a = a + da / 255.0 * (1.0 - a)
    if out_a <= 0:
        return (0, 0, 0, 0)
    r = (sr * a + dr * (da / 255.0) * (1.0 - a)) / out_a
    g = (sg * a + dg * (da / 255.0) * (1.0 - a)) / out_a
    b = (sb * a + db * (da / 255.0) * (1.0 - a)) / out_a
    return (round(r), round(g), round(b), round(out_a * 255))


def write_png(path: Path, width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels[y * width + x])
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def render_icon(size: int, supersample: int = 4) -> list[tuple[int, int, int, int]]:
    canvas = size * supersample
    pixels: list[tuple[int, int, int, int]] = []
    c1 = (11, 16, 32)
    c2 = (23, 37, 84)
    arc1 = cubic((31, 31), (49, 19), (80, 18), (99, 34))
    arc2 = cubic((32, 96), (51, 109), (78, 110), (99, 99))
    tail = [(47, 108), (47, 91), (70, 91)]

    for py in range(size):
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(supersample):
                for sx in range(supersample):
                    x = ((px * supersample + sx + 0.5) / canvas) * 128
                    y = ((py * supersample + sy + 0.5) / canvas) * 128
                    col = (0, 0, 0, 0)

                    bg_a = rounded_rect_alpha(x, y, 6, 6, 122, 122, 30)
                    if bg_a > 0:
                        t = (x + y) / 256
                        bg = (lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t), round(255 * bg_a))
                        col = blend(col, bg)

                    bubble_a = rounded_rect_alpha(x, y, 17, 39, 111, 94, 21)
                    if point_in_poly(x, y, tail):
                        bubble_a = 1.0
                    if bubble_a > 0:
                        col = blend(col, (248, 250, 252, round(255 * bubble_a)))

                    for x1, x2 in [(37, 44), (52, 59), (67, 74), (82, 89)]:
                        d = line_distance(x, y, (x1, 66), (x2, 66))
                        if d <= 3.2:
                            col = blend(col, (14, 165, 233, round(255 * max(0.0, min(1.0, 3.2 - d)))))

                    for cx, cy, r in [(28, 28, 4.5), (101, 101, 4.5)]:
                        d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                        if d <= r + 0.5:
                            col = blend(col, (56, 189, 248, round(255 * max(0.0, min(1.0, r + 0.5 - d)))))

                    for arc in (arc1, arc2):
                        d = polyline_distance(x, y, arc)
                        if d <= 2.2:
                            col = blend(col, (56, 189, 248, round(224 * max(0.0, min(1.0, 2.2 - d)))))

                    for i in range(4):
                        acc[i] += col[i]
            n = supersample * supersample
            pixels.append(tuple(round(v / n) for v in acc))
    return pixels


for icon_size in (16, 32, 48, 128):
    out = ICON_DIR / f"icon{icon_size}.png"
    write_png(out, icon_size, icon_size, render_icon(icon_size))
    print(f"generated {out.relative_to(ROOT)}")
