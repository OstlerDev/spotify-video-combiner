"""Regenerate ``assets/app-icon.ico`` from ``assets/app-icon.png``.

Designed for pixel-art source images: every output size is produced via
nearest-neighbor scaling from a clean integer-resolution master so the
result stays crisp instead of getting blurry from bilinear downscale.

Pipeline:
    1. Auto-detect the upscale factor of the source PNG by sampling
       horizontal/vertical run lengths (the most common run length is the
       size of one logical art pixel).
    2. Downsample the source to its native pixel-art resolution by picking
       the center sample of each block (true nearest, no anti-aliasing).
    3. From that master, build each target size with nearest-neighbor.
    4. Pack everything into a single ``.ico`` using PNG-compressed entries
       (Vista+, supported by every Windows version PyInstaller targets).

Run with::

    python scripts/build_icon.py
"""

from __future__ import annotations

import struct
from collections import Counter
from io import BytesIO
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PNG = REPO_ROOT / "assets" / "app-icon.png"
OUTPUT_ICO = REPO_ROOT / "assets" / "app-icon.ico"

# Sizes Windows asks for across DPI scales (100 / 125 / 150 / 175 / 200%
# titlebar + taskbar + Alt-Tab combinations) plus the 256 entry that
# Explorer's "Extra large icons" view uses.
TARGET_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)


def detect_native_resolution(image: Image.Image) -> int:
    """Return the side length of the source's underlying pixel grid.

    Samples color-change run lengths along several rows + columns; the
    statistical mode is the size of one logical art pixel in source px.
    """
    width, height = image.size
    pixels = image.load()
    runs: Counter[int] = Counter()

    for y in range(0, height, max(1, height // 64)):
        x = 0
        while x < width - 1:
            start = x
            base = pixels[x, y]
            while x < width - 1 and pixels[x + 1, y] == base:
                x += 1
            runs[x - start + 1] += 1
            x += 1

    for x in range(0, width, max(1, width // 64)):
        y = 0
        while y < height - 1:
            start = y
            base = pixels[x, y]
            while y < height - 1 and pixels[x, y + 1] == base:
                y += 1
            runs[y - start + 1] += 1
            y += 1

    block = runs.most_common(1)[0][0]
    return max(1, round(width / block))


def downsample_to_native(image: Image.Image, native: int) -> Image.Image:
    """Pick one source pixel per logical block (true nearest, no blending)."""
    width, height = image.size
    out = Image.new("RGBA", (native, native))
    src = image.load()
    dst = out.load()
    for ny in range(native):
        sy = min(height - 1, int((ny + 0.5) * height / native))
        for nx in range(native):
            sx = min(width - 1, int((nx + 0.5) * width / native))
            dst[nx, ny] = src[sx, sy]
    return out


def build_ico(master: Image.Image, sizes: tuple[int, ...], out_path: Path) -> None:
    """Write a multi-resolution ICO with PNG-compressed entries."""
    images: list[tuple[int, bytes]] = []
    for size in sorted(set(sizes)):
        scaled = master.resize((size, size), Image.NEAREST)
        buf = BytesIO()
        scaled.save(buf, format="PNG", optimize=True)
        images.append((size, buf.getvalue()))

    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))

    data_offset = 6 + 16 * len(images)
    entry_blob = bytearray()
    data_blob = bytearray()
    for size, png_bytes in images:
        # Width / height of 0 means 256 in the ICO format.
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entry_blob += struct.pack(
            "<BBBBHHII",
            w,
            h,
            0,                  # color count (0 for >=256-color images)
            0,                  # reserved
            1,                  # color planes
            32,                 # bits per pixel
            len(png_bytes),
            data_offset,
        )
        data_blob += png_bytes
        data_offset += len(png_bytes)

    out.write(entry_blob)
    out.write(data_blob)
    out_path.write_bytes(out.getvalue())


def main() -> None:
    if not SOURCE_PNG.is_file():
        raise SystemExit(f"missing source: {SOURCE_PNG}")

    source = Image.open(SOURCE_PNG).convert("RGBA")
    native = detect_native_resolution(source)
    print(f"source     : {SOURCE_PNG.relative_to(REPO_ROOT)} ({source.size[0]}x{source.size[1]})")
    print(f"native grid: {native}x{native} pixels of art")

    master = downsample_to_native(source, native)
    build_ico(master, TARGET_SIZES, OUTPUT_ICO)

    print(f"wrote      : {OUTPUT_ICO.relative_to(REPO_ROOT)} "
          f"({OUTPUT_ICO.stat().st_size:,} bytes)")
    print(f"sizes      : {', '.join(f'{s}x{s}' for s in sorted(TARGET_SIZES))}")


if __name__ == "__main__":
    main()
