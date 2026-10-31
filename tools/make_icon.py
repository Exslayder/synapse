#!/usr/bin/env python3
"""Generate the Synapse icon without third-party dependencies.

The mark is a neural «S»: a signal path made of two arcs and a straight
synapse, drawn with supersampling so it stays crisp from 16 to 256 px.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "synapse" / "assets"
DOC_ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

# Geometry of the S path in unit coordinates (y grows downwards).
TOP_CENTER = (0.5, 0.375)
BOTTOM_CENTER = (0.5, 0.625)
ARC_RADIUS = 0.205
STROKE = 0.068


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _inside_round_rect(x: float, y: float, inset: float, radius: float) -> bool:
    left = top = inset
    right = bottom = 1.0 - inset
    half_w = (right - left) / 2.0
    half_h = (bottom - top) / 2.0
    cx = left + half_w
    cy = top + half_h
    qx = abs(x - cx) - (half_w - radius)
    qy = abs(y - cy) - (half_h - radius)
    distance = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
    return distance <= 0.0


def _distance_to_segment(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    length = dx * dx + dy * dy
    if not length:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _distance_to_arc(
    x: float,
    y: float,
    center: tuple[float, float],
    radius: float,
    start: float,
    end: float,
) -> float:
    """Distance to an arc; angles in degrees, y axis pointing down."""
    cx, cy = center
    angle = math.degrees(math.atan2(y - cy, x - cx)) % 360.0
    start %= 360.0
    end %= 360.0
    inside = (start <= angle <= end) if start <= end else (angle >= start or angle <= end)
    if inside:
        return abs(math.hypot(x - cx, y - cy) - radius)
    p1 = (
        cx + radius * math.cos(math.radians(start)),
        cy + radius * math.sin(math.radians(start)),
    )
    p2 = (
        cx + radius * math.cos(math.radians(end)),
        cy + radius * math.sin(math.radians(end)),
    )
    return min(math.hypot(x - p1[0], y - p1[1]), math.hypot(x - p2[0], y - p2[1]))


def _s_distance(x: float, y: float) -> float:
    top_left = (TOP_CENTER[0] - ARC_RADIUS, TOP_CENTER[1])
    bottom_right = (BOTTOM_CENTER[0] + ARC_RADIUS, BOTTOM_CENTER[1])
    # Upper arc: right -> top -> left (180..360 in screen coordinates).
    top = _distance_to_arc(x, y, TOP_CENTER, ARC_RADIUS, 180.0, 360.0)
    # Lower arc: right -> bottom -> left (0..180 in screen coordinates).
    bottom = _distance_to_arc(x, y, BOTTOM_CENTER, ARC_RADIUS, 0.0, 180.0)
    # The middle stroke crosses from the upper-left to the lower-right end.
    spine = _distance_to_segment(x, y, top_left[0], top_left[1], bottom_right[0], bottom_right[1])
    return min(top, bottom, spine)


def _pixel(x: float, y: float) -> tuple[int, int, int, int]:
    if not _inside_round_rect(x, y, 0.012, 0.235):
        return (0, 0, 0, 0)

    background = _mix((27, 32, 48), (10, 12, 19), y)
    if not _inside_round_rect(x, y, 0.045, 0.205):
        background = _mix(background, (62, 74, 112), 0.5)

    # Soft light from the top-left keeps the tile from looking flat.
    glow = max(0.0, 1.0 - math.hypot(x - 0.24, y - 0.16) / 0.62)
    background = _mix(background, (38, 45, 68), glow * 0.42)

    distance = _s_distance(x, y)
    accent = _mix((190, 201, 255), (106, 123, 240), (y - 0.1) / 0.8)
    if distance <= STROKE:
        # A slightly brighter core makes the mark crisp at 16 px.
        core = max(0.0, 1.0 - distance / STROKE)
        accent = _mix(accent, (224, 230, 255), core * 0.35)
        return (*accent, 255)
    if distance <= STROKE + 0.035:
        halo = 1.0 - (distance - STROKE) / 0.035
        background = _mix(background, (92, 106, 196), halo * 0.38)
    return (*background, 255)


def render(size: int) -> bytes:
    """Return straight-alpha RGBA bytes for one icon size."""
    if size <= 0:
        raise ValueError("size must be positive")
    scale = SUPERSAMPLE
    out = bytearray(size * size * 4)

    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(scale):
                y = (py + (sy + 0.5) / scale) / size
                for sx in range(scale):
                    x = (px + (sx + 0.5) / scale) / size
                    pr, pg, pb, pa = _pixel(x, y)
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            index = (py * size + px) * 4
            if a:
                out[index] = min(255, round(r / a))
                out[index + 1] = min(255, round(g / a))
                out[index + 2] = min(255, round(b / a))
                out[index + 3] = round(a / (scale * scale))
            else:
                out[index + 3] = 0
    return bytes(out)


def _bmp_entry(rgba: bytes, size: int) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        size * size * 4,
        0,
        0,
        0,
        0,
    )
    rows = bytearray()
    stride = size * 4
    for y in range(size - 1, -1, -1):
        row = rgba[y * stride : (y + 1) * stride]
        for x in range(size):
            r, g, b, a = row[x * 4 : x * 4 + 4]
            rows += bytes((b, g, r, a))
    mask_stride = ((size + 31) // 32) * 4
    return header + bytes(rows) + bytes(mask_stride * size)


def write_ico(path: Path) -> None:
    images = [(size, _bmp_entry(render(size), size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(entries) + bytes(payload))


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int = 256) -> None:
    rgba = render(size)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw += rgba[y * size * 4 : (y + 1) * size * 4]
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> int:
    write_ico(ASSETS / "synapse.ico")
    write_png(DOC_ASSETS / "icon.png")
    write_png(ASSETS / "icon.png")
    print(f"icon: {ASSETS / 'synapse.ico'}")
    print(f"png:  {DOC_ASSETS / 'icon.png'}")
    print(f"png:  {ASSETS / 'icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
