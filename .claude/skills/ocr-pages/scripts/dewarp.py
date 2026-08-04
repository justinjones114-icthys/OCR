#!/usr/bin/env python3
"""Flatten a photographed page by straightening its text lines.

The obvious approach -- find the sheet's outline, perspective-correct it to a
rectangle -- does not work on a bound book, and makes things actively worse. A
page curving toward the spine is not a planar rectangle, so its outline is not
the projection of one; forcing that outline square shears the interior and
leaves the text more slanted than it started.

What actually matters to an OCR engine is only that baselines are horizontal
and evenly spaced. So measure the baselines themselves and map them flat. That
corrects keystone and page curl in one pass, because both distortions show up
the same way: text lines that aren't straight.

Local engines need this. Tesseract and its relatives assume horizontal
baselines and degrade sharply without them; a vision model reads warped text
happily and barely notices.

Usage:
    python dewarp.py page.jpg --out flat/
    python dewarp.py pages/*.jpg --out flat/ --debug
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps
    from scipy import ndimage
except ImportError:  # pragma: no cover
    sys.exit("dewarp needs Pillow, numpy and scipy:  pip install pillow numpy scipy")

INK_DELTA = 22
BACKGROUND_BLUR = 60

MERGE_WIDTH = 31       # horizontal dilation that glues letters into a line blob
MIN_LINE_FRACTION = 0.30  # a real text line spans much of the text column
FIT_BINS = 40          # x-bins per line; binning first stops thick blobs skewing the fit
FIT_DEGREE = 2         # page curl is gentle -- a parabola fits it, higher orders ring
MIN_LINES = 5          # below this the estimate is guesswork; leave the page alone


def ink_mask(grey: Image.Image) -> np.ndarray:
    """Pixels darker than their local background, so lighting gradients don't count."""
    background = grey.filter(ImageFilter.BoxBlur(BACKGROUND_BLUR))
    return (np.asarray(background, np.int16) - np.asarray(grey, np.int16)) > INK_DELTA


def find_baselines(mask: np.ndarray) -> list[np.ndarray]:
    """Fit a curve to each text line. Returns polynomial coefficients per line."""
    h, w = mask.shape
    merged = ndimage.binary_dilation(mask, np.ones((1, MERGE_WIDTH), bool))
    merged = ndimage.binary_closing(merged, np.ones((3, 3), bool))

    labels, count = ndimage.label(merged)
    if count == 0:
        return []

    fits = []
    objects = ndimage.find_objects(labels)
    for index, slices in enumerate(objects, start=1):
        if slices is None:
            continue
        ys_slice, xs_slice = slices
        if (xs_slice.stop - xs_slice.start) < MIN_LINE_FRACTION * w:
            continue

        ys, xs = np.nonzero(labels[slices] == index)
        ys = ys + ys_slice.start
        xs = xs + xs_slice.start

        # Bin along x and take the mean y per bin. A raw least-squares fit over
        # every pixel lets a dense cluster (a long word, a blot) drag the curve.
        edges = np.linspace(xs.min(), xs.max() + 1, FIT_BINS + 1)
        which = np.clip(np.digitize(xs, edges) - 1, 0, FIT_BINS - 1)
        centres, means = [], []
        for b in range(FIT_BINS):
            sel = which == b
            if sel.sum() < 3:
                continue
            centres.append((edges[b] + edges[b + 1]) / 2)
            means.append(ys[sel].mean())
        if len(centres) < FIT_DEGREE + 2:
            continue

        fits.append(np.polyfit(np.array(centres), np.array(means), FIT_DEGREE))

    # Order top to bottom by where each line sits at mid-page.
    fits.sort(key=lambda p: np.polyval(p, mask.shape[1] / 2))
    return fits


def straighten(img: Image.Image, fits: list[np.ndarray]) -> tuple[Image.Image, float]:
    """Map each fitted baseline onto a horizontal line, interpolating between them."""
    arr = np.asarray(img, np.float32)
    h, w = arr.shape[:2]

    xs = np.arange(w)
    # Where each line currently sits, per column, and where it should sit.
    source = np.stack([np.polyval(p, xs) for p in fits])        # (lines, w)
    target = np.array([np.polyval(p, w / 2) for p in fits])     # (lines,)

    drift = float(np.abs(source - target[:, None]).max())

    rows = np.arange(h, dtype=np.float32)
    sample_y = np.empty((h, w), np.float32)
    for x in range(w):
        col_src = source[:, x]
        order = np.argsort(col_src)
        src_sorted = col_src[order]
        dst_sorted = target[order]
        # np.interp clamps beyond the ends, which would compress the head and
        # foot margins into the first and last baseline. Extend linearly instead.
        sample_y[:, x] = np.interp(rows, dst_sorted, src_sorted)
        head = rows < dst_sorted[0]
        foot = rows > dst_sorted[-1]
        if head.any():
            sample_y[head, x] = src_sorted[0] + (rows[head] - dst_sorted[0])
        if foot.any():
            sample_y[foot, x] = src_sorted[-1] + (rows[foot] - dst_sorted[-1])

    sample_x = np.tile(xs.astype(np.float32), (h, 1))
    if arr.ndim == 2:
        out = ndimage.map_coordinates(arr, [sample_y, sample_x], order=1, mode="nearest")
    else:
        out = np.dstack([
            ndimage.map_coordinates(arr[..., c], [sample_y, sample_x], order=1, mode="nearest")
            for c in range(arr.shape[2])
        ])
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), drift


def process(path: Path, out_dir: Path, debug: bool) -> dict:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    mask = ink_mask(img.convert("L"))
    fits = find_baselines(mask)
    report = {"source": path.name, "lines": len(fits), "drift_px": 0.0, "applied": False}

    if len(fits) >= MIN_LINES:
        flat, drift = straighten(img, fits)
        report["drift_px"] = round(drift, 1)
        # Below a couple of pixels there is nothing to gain and resampling only
        # costs sharpness, which OCR notices.
        if drift > 2:
            img = flat
            report["applied"] = True

    target = out_dir / f"{path.stem}-flat.jpg"
    img.save(target, quality=94)
    report["output"] = target.name

    if debug:
        Image.fromarray((mask * 255).astype(np.uint8)).save(out_dir / f"{path.stem}-ink.png")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.images:
        r = process(path, args.out, args.debug)
        state = "flattened" if r["applied"] else "left as-is"
        print(
            f"   {path.name} -> {r['output']}  "
            f"({r['lines']} baselines, {r['drift_px']}px drift, {state})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
