#!/usr/bin/env python3
"""Split photographs of open book spreads into single-page images.

The gutter is found by ink density, not brightness. Brightness fails on real
photos because the inner margin of the far page is often as dark as the gutter
shadow, and a lighting gradient across the shot moves the darkest column
somewhere arbitrary. Ink density -- how many text pixels sit in each column --
is driven by the text blocks themselves, so the widest quiet band between them
is the gutter regardless of how the page was lit.

Usage:
    python split_spread.py PHOTO.jpg [more.jpg ...] --out pages/
    python split_spread.py PHOTO.jpg --out pages/ --single
    python split_spread.py PHOTO.jpg --out pages/ --no-split
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("ocr-pages needs Pillow and numpy:  pip install pillow numpy")

# The vision model gains nothing above 2576px on the long edge, and every extra
# pixel is upload time you don't get back.
MAX_EDGE = 2576

# A stroke is "ink" when it is this much darker than the locally blurred
# background. Local comparison is what makes this survive uneven lighting.
INK_DELTA = 22
BACKGROUND_BLUR = 60

# Columns holding at least this fraction of the busiest column's ink count as
# text. Low enough to catch sparse lines, high enough to ignore JPEG noise.
TEXT_COLUMN_FRACTION = 0.06

# Gutters narrower than this fraction of the page width are suspicious: the
# valley may be a paragraph indent or the space between two columns of type.
CONFIDENT_GUTTER_FRACTION = 0.012


def ink_mask(img: Image.Image) -> "np.ndarray":
    """Boolean mask of pixels darker than their local background."""
    grey = img.convert("L")
    background = grey.filter(ImageFilter.BoxBlur(BACKGROUND_BLUR))
    return (np.asarray(background, np.int16) - np.asarray(grey, np.int16)) > INK_DELTA


def find_gutter(img: Image.Image) -> tuple[int, int, str]:
    """Return (x, valley_width, confidence) for the spread's gutter."""
    width, height = img.size
    mask = ink_mask(img)

    # Ignore the top and bottom sixth: that is where fingers, book-stand arms
    # and the desk edge live, and none of them say anything about the gutter.
    band = mask[int(0.15 * height) : int(0.85 * height)]
    profile = band.sum(axis=0).astype(float)
    profile = np.convolve(profile, np.ones(9) / 9, mode="same")

    if profile.max() == 0:
        return width // 2, 0, "low"

    has_text = profile > (TEXT_COLUMN_FRACTION * profile.max())
    columns = np.where(has_text)[0]
    if columns.size == 0:
        return width // 2, 0, "low"

    left, right = int(columns.min()), int(columns.max())
    span = right - left

    # Only look in the middle of the text area. A spread's gutter is near the
    # centre by construction, and the outer margins are quieter than any gutter.
    lo = left + int(0.30 * span)
    hi = left + int(0.70 * span)

    runs: list[tuple[int, int, int]] = []
    start = None
    for x in range(lo, hi):
        if not has_text[x]:
            if start is None:
                start = x
        elif start is not None:
            runs.append((x - start, start, x))
            start = None
    if start is not None:
        runs.append((hi - start, start, hi))

    if not runs:
        return width // 2, 0, "low"

    valley_width, start, end = max(runs)
    confidence = (
        "high" if valley_width >= CONFIDENT_GUTTER_FRACTION * width else "low"
    )
    return (start + end) // 2, valley_width, confidence


def downscale(img: Image.Image, max_edge: int) -> Image.Image:
    if max(img.size) <= max_edge:
        return img
    scale = max_edge / max(img.size)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS)


def process(path: Path, out_dir: Path, mode: str, max_edge: int) -> dict:
    img = ImageOps.exif_transpose(Image.open(path))
    stem = path.stem
    record: dict = {"source": path.name, "size": list(img.size), "mode": mode}

    if mode == "spread":
        gutter, valley, confidence = find_gutter(img)
        record.update(gutter_x=gutter, valley_width=valley, confidence=confidence)
        sides = {
            "L": img.crop((0, 0, gutter, img.height)),
            "R": img.crop((gutter, 0, img.width, img.height)),
        }
    else:
        record.update(confidence="high")
        sides = {"": img}

    outputs = []
    for side, page in sides.items():
        name = f"{stem}-{side}.jpg" if side else f"{stem}.jpg"
        target = out_dir / name
        downscale(page, max_edge).convert("RGB").save(target, quality=92)
        outputs.append({"side": side or None, "file": name})
    record["pages"] = outputs

    (out_dir / f"{stem}-split.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--single", action="store_true", help="photos are already single pages"
    )
    parser.add_argument(
        "--no-split", action="store_true", help="only downscale, never cut"
    )
    parser.add_argument("--max-edge", type=int, default=MAX_EDGE)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    mode = "single" if (args.single or args.no_split) else "spread"

    warnings = 0
    for path in args.images:
        if not path.exists():
            print(f"!! {path}: not found", file=sys.stderr)
            warnings += 1
            continue
        record = process(path, args.out, mode, args.max_edge)
        files = ", ".join(p["file"] for p in record["pages"])
        if record["confidence"] == "low":
            warnings += 1
            print(
                f"!! {path.name}: gutter unclear (valley {record.get('valley_width', 0)}px) "
                f"-> {files}  CHECK THE CUT BEFORE TRANSCRIBING",
                file=sys.stderr,
            )
        else:
            print(f"   {path.name} -> {files}")

    if warnings:
        print(f"\n{warnings} image(s) need a look before you transcribe.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
