#!/usr/bin/env python3
"""Generate the application icon without third-party dependencies."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "gemini_doh" / "assets"
DOC_ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _inside_round_rect(
    x: float,
    y: float,
    inset: float,
    radius: float,
) -> bool:
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


def _pixel(x: float, y: float) -> tuple[int, int, int, int]:
    if not _inside_round_rect(x, y, 0.012, 0.235):
        return (0, 0, 0, 0)

    background = _mix((31, 36, 26), (15, 17, 13), y)
    if not _inside_round_rect(x, y, 0.045, 0.205):
        background = _mix(background, (78, 86, 62), 0.55)

    accent = _mix((236, 255, 112), (170, 219, 49), y)

    dx = x - 0.5
    dy = y - 0.485
    distance = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)
    ring = abs(distance - 0.268) <= 0.051
    gap = -0.62 < angle < 0.62
    bar = 0.485 <= x <= 0.745 and abs(y - 0.485) <= 0.05

    if (ring and not gap) or bar:
        return (*accent, 255)

    # A small highlight keeps the icon readable at large sizes.
    if distance < 0.17 and y < 0.46:
        highlight = _mix(background, (48, 55, 40), 0.35)
        return (*highlight, 255)
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
    write_ico(ASSETS / "gemini-doh.ico")
    write_png(DOC_ASSETS / "icon.png")
    print(f"icon: {ASSETS / 'gemini-doh.ico'}")
    print(f"png:  {DOC_ASSETS / 'icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
