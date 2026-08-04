#!/usr/bin/env python3
"""Adapters that make different OCR engines answer the same question.

Each adapter takes a page image and returns a Page: plain text plus word boxes.
Everything downstream -- superscript promotion, the benchmark, the page JSON --
works off that shape, so swapping engines never reaches past this file.

Engines are optional. Import failures are reported as unavailability rather
than raised, so a machine with only Tesseract still runs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float = -1.0
    line: int = 0

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class Page:
    text: str
    words: list[Word] = field(default_factory=list)
    seconds: float = 0.0
    engine: str = ""
    noterefs: set[int] = field(default_factory=set)


# --------------------------------------------------------------------------
# Superscript detection by geometry, then re-reading
#
# No engine reports "this 82 is a note marker", and on Tesseract the marker is
# not even a separate token -- it comes back glued on as `ners".82`. So word
# boxes are useless here and this works at the character level.
#
# Two things were learned the hard way and are worth keeping:
#
# Height is a poor discriminator. A line's median character height is its
# x-height, and a superscript digit is only a little shorter than that (19px
# against 22px on the test page). The *raise* is what separates them cleanly:
# a marker's foot sits well above the baseline while body digits sit on it.
#
# Locating a marker is not the same as reading it. At around 20px Tesseract
# misreads the glyph itself -- `high.84` came back as `high.8*`. So geometry
# only nominates a region; the digits are recovered by cropping that region,
# upscaling it, and re-reading with the alphabet constrained to 0-9.
# --------------------------------------------------------------------------

RAISE_RATIO = 0.25       # foot must clear the baseline by this share of x-height
MIN_GLYPH_RATIO = 0.60   # smaller than this is punctuation, not a digit
MAX_GLYPH_RATIO = 1.05   # larger than this is body type
DIGIT_CONFIG = "--psm 8 -c tessedit_char_whitelist=0123456789"


def _char_boxes(img, config: str) -> list[dict]:
    import pytesseract

    height = img.size[1]
    boxes = []
    for line in pytesseract.image_to_boxes(img, config=config).splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        char, x1, y1, x2, y2 = parts[0], *map(int, parts[1:5])
        if y2 - y1 <= 2:
            continue
        boxes.append({
            "c": char, "l": x1, "r": x2,
            "top": height - y2, "bot": height - y1,
            "h": y2 - y1, "cy": (2 * height - y2 - y1) / 2,
        })
    return boxes


def _group_lines(boxes: list[dict]) -> list[list[dict]]:
    if not boxes:
        return []
    boxes.sort(key=lambda c: c["cy"])
    heights = sorted(c["h"] for c in boxes)
    tolerance = 0.75 * heights[len(heights) // 2]
    lines, current = [], [boxes[0]]
    for box in boxes[1:]:
        if abs(box["cy"] - current[-1]["cy"]) <= tolerance:
            current.append(box)
        else:
            lines.append(current)
            current = [box]
    lines.append(current)
    return lines


def find_noterefs_tesseract(image: Path, config: str = "--psm 6") -> set[int]:
    import pytesseract
    from PIL import Image

    img = Image.open(image)
    width, height = img.size
    found: set[int] = set()

    for line in _group_lines(_char_boxes(img, config)):
        if len(line) < 15:          # too short to establish a reliable baseline
            continue
        heights = sorted(c["h"] for c in line)
        x_height = heights[len(heights) // 2]
        bottoms = sorted(c["bot"] for c in line)
        baseline = bottoms[len(bottoms) // 2]

        candidates = [
            c for c in sorted(line, key=lambda c: c["l"])
            if c["bot"] < baseline - RAISE_RATIO * x_height
            and MIN_GLYPH_RATIO * x_height <= c["h"] <= MAX_GLYPH_RATIO * x_height
        ]

        clusters, run = [], []
        for c in candidates:
            if run and c["l"] - run[-1]["r"] <= 0.6 * x_height:
                run.append(c)
            else:
                if run:
                    clusters.append(run)
                run = [c]
        if run:
            clusters.append(run)

        for cluster in clusters:
            pad = int(0.35 * x_height)
            box = (
                max(0, min(c["l"] for c in cluster) - pad),
                max(0, min(c["top"] for c in cluster) - pad),
                min(width, max(c["r"] for c in cluster) + pad),
                min(height, max(c["bot"] for c in cluster) + pad),
            )
            crop = img.crop(box)
            crop = crop.resize((crop.width * 5, crop.height * 5), Image.LANCZOS)
            digits = re.sub(r"\D", "", pytesseract.image_to_string(crop, config=DIGIT_CONFIG))
            if digits and 1 <= len(digits) <= 3:
                found.add(int(digits))
    return found


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------


def tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        import shutil
        return shutil.which("tesseract") is not None
    except ImportError:
        return False


def run_tesseract(image: Path, psm: int = 6) -> Page:
    import pytesseract
    from PIL import Image

    started = time.perf_counter()
    img = Image.open(image)
    config = f"--psm {psm}"
    data = pytesseract.image_to_data(
        img, config=config, output_type=pytesseract.Output.DICT
    )
    elapsed = time.perf_counter() - started

    words, line_key, line_ids = [], {}, 0
    for i, token in enumerate(data["text"]):
        if not token.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in line_key:
            line_key[key] = line_ids
            line_ids += 1
        words.append(
            Word(
                text=token,
                left=data["left"][i], top=data["top"][i],
                width=data["width"][i], height=data["height"][i],
                conf=float(data["conf"][i]), line=line_key[key],
            )
        )
    text = pytesseract.image_to_string(img, config=config)
    page = Page(text=text, words=words, seconds=elapsed, engine="tesseract")
    page.noterefs = find_noterefs_tesseract(image, config)
    return page


def surya_available() -> bool:
    try:
        import surya  # noqa: F401
        return True
    except ImportError:
        return False


# Surya's call signature has changed across releases -- older builds wanted a
# det_predictor keyword, current ones take (images, layout_results, full_page).
# Predictors are cached because construction loads model weights.
_SURYA_CACHE: dict = {}


def run_surya(image: Path) -> Page:
    from PIL import Image
    from surya.recognition import RecognitionPredictor

    if "rec" not in _SURYA_CACHE:
        _SURYA_CACHE["rec"] = RecognitionPredictor()
    predictor = _SURYA_CACHE["rec"]

    started = time.perf_counter()
    img = Image.open(image).convert("RGB")
    try:
        predictions = predictor([img], full_page=True)
    except TypeError:
        from surya.detection import DetectionPredictor
        if "det" not in _SURYA_CACHE:
            _SURYA_CACHE["det"] = DetectionPredictor()
        predictions = predictor([img], det_predictor=_SURYA_CACHE["det"])
    elapsed = time.perf_counter() - started

    words, lines = [], []
    for line_no, line in enumerate(predictions[0].text_lines):
        lines.append(line.text)
        x0, y0, x1, y1 = [int(v) for v in line.bbox]
        # Surya returns whole lines. Apportion the box across tokens so the
        # geometric superscript test still has something to work with; it is
        # coarser than per-word boxes and will under-detect.
        tokens = line.text.split()
        if not tokens:
            continue
        step = max(1, (x1 - x0) // max(1, len(tokens)))
        for i, tok in enumerate(tokens):
            words.append(
                Word(tok, x0 + i * step, y0, step, y1 - y0, line=line_no)
            )
    return Page(text="\n".join(lines), words=words, seconds=elapsed, engine="surya")


def vlm_available() -> bool:
    try:
        import ollama  # noqa: F401
        return True
    except ImportError:
        return False


VLM_PROMPT = (
    "Transcribe this page image exactly as printed. Preserve original spelling, "
    "punctuation and typographic quotes. Mark superscript note reference numbers "
    "as [[n]]. Do not summarise, correct or explain anything. Output only the "
    "transcription."
)


def run_vlm(image: Path, model: str = "qwen2.5vl:7b") -> Page:
    """Local vision model via ollama. Needs roughly 16GB VRAM for a 7B at fp16."""
    import ollama

    started = time.perf_counter()
    reply = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": VLM_PROMPT, "images": [str(image)]}],
    )
    elapsed = time.perf_counter() - started
    text = reply["message"]["content"]
    # This path has no boxes, so markers come from the [[n]] convention above
    # rather than from geometry.
    return Page(text=text, words=[], seconds=elapsed, engine=f"vlm:{model}")


ENGINES = {
    "tesseract": (tesseract_available, run_tesseract),
    "surya": (surya_available, run_surya),
    "vlm": (vlm_available, run_vlm),
}
