#!/usr/bin/env python3
"""Surya -> page JSON.

Surya returns layout blocks rather than bare lines: each carries a canonical
label, a reading-order index, and HTML in which superscripts survive. That maps
onto the skill's block taxonomy almost directly, so most of this file is
translation rather than inference.

Two things Surya does not get right on its own, and this fixes:

  quote glyphs   It emits ASCII " and ' for more than half the quotation marks
                 on a typographically set page. Measured on the sample spread:
                 9 straight doubles and 6 straight singles against 9 curly.
                 Since the book sets every quote typographically, a straight
                 glyph is always wrong, and which curly form to use is
                 recoverable from position.

  page breaks    Surya sees one page at a time and cannot know that a paragraph
                 continues onto the next. That is inferred here from terminal
                 punctuation and leading case.

Usage:
    python transcribe.py flat/*.jpg --out pages/
    python transcribe.py flat/*.jpg --out pages/ --start-order 7
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from pathlib import Path

# Surya's canonical layout labels -> our block types. Anything unrecognised
# becomes a paragraph, which is the safe default: it keeps the text rather
# than dropping it, and a human reviewing flagged pages can retype the label.
LABEL_MAP = {
    "page-header": "running_head",
    "pageheader": "running_head",
    "page-footer": "running_head",
    "pagefooter": "running_head",
    "section-header": "heading",
    "sectionheader": "heading",
    "title": "heading",
    "text": "paragraph",
    "list-item": "paragraph",
    "listitem": "paragraph",
    "footnote": "note",
    "caption": "caption",
    "picture": "figure",
    "figure": "figure",
    "table": "figure",
    "formula": "paragraph",
}

SENTENCE_FINAL = tuple(".!?" ) + ("”", "’", '"', "'", ")", "…")
ALLOWED_INLINE = {"em", "strong", "br", "sub", "sup", "span"}


def recurl(text: str) -> str:
    """Restore typographic quotes, deciding each by what precedes it.

    An opening quote follows nothing, whitespace, or an opening bracket or
    dash; everything else closes. A straight single between two letters is an
    apostrophe, which is the common case in running prose (one's, Teresa's)
    and must not be treated as a quote at all.
    """
    out = []
    for i, ch in enumerate(text):
        if ch not in ('"', "'"):
            out.append(ch)
            continue
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "'" and prev.isalpha() and nxt.isalpha():
            out.append("’")            # apostrophe inside a word
            continue
        if ch == "'" and prev.isalpha() and not nxt.isalpha():
            out.append("’")            # trailing possessive: Teresas'
            continue
        opening = (not prev) or prev.isspace() or prev in "([{—–-“‘"
        if ch == '"':
            out.append("“" if opening else "”")
        else:
            out.append("‘" if opening else "’")
    return "".join(out)


def clean_html(raw: str) -> str:
    """Keep the inline vocabulary the schema documents; drop everything else."""

    def keep(match: re.Match) -> str:
        tag = match.group(0)
        name = re.match(r"</?\s*([a-zA-Z0-9]+)", tag)
        if not name or name.group(1).lower() not in ALLOWED_INLINE:
            return ""
        return tag

    text = re.sub(r"<[^>]+>", keep, raw)
    return re.sub(r"[ \t]+", " ", text).strip()


def mark_noterefs(text: str) -> str:
    """Give every <sup> the data-n the renderer needs to link it to a note."""

    def annotate(match: re.Match) -> str:
        inner = match.group(1)
        digits = re.sub(r"\D", "", inner)
        if not digits or len(digits) > 3:
            return match.group(0)
        return f'<sup class="noteref" data-n="{int(digits)}">{digits}</sup>'

    return re.sub(r"<sup[^>]*>(.*?)</sup>", annotate, text, flags=re.S | re.I)


def split_header(text: str) -> tuple[str | None, str | None]:
    """A running head often carries the folio: '128 FIRE WITHIN'. Separate them."""
    stripped = text.strip()
    leading = re.match(r"^(\d{1,4})\s+(.*)$", stripped)
    if leading:
        return leading.group(1), leading.group(2).strip()
    trailing = re.match(r"^(.*?)\s+(\d{1,4})$", stripped)
    if trailing:
        return trailing.group(2), trailing.group(1).strip()
    if stripped.isdigit():
        return stripped, None
    return None, stripped or None


def transcribe(image: Path, order: int, predictor) -> dict:
    from PIL import Image

    started = time.perf_counter()
    img = Image.open(image).convert("RGB")
    try:
        result = predictor([img], full_page=True)[0]
    except TypeError:
        from surya.detection import DetectionPredictor
        result = predictor([img], det_predictor=DetectionPredictor())[0]
    elapsed = time.perf_counter() - started

    blocks_in = sorted(result.blocks, key=lambda b: getattr(b, "reading_order", 0))
    blocks_out: list[dict] = []
    folio: str | None = None

    for block in blocks_in:
        if getattr(block, "skipped", False) or getattr(block, "error", False):
            continue
        label = str(getattr(block, "label", "") or "").strip().lower()
        kind = LABEL_MAP.get(label, "paragraph")
        text = mark_noterefs(recurl(clean_html(getattr(block, "html", "") or "")))
        if not text:
            continue

        if kind == "running_head":
            number, head = split_header(html_mod.unescape(re.sub(r"<[^>]+>", "", text)))
            if number and folio is None:
                folio = number
                blocks_out.append({"type": "folio", "text": number})
            if head:
                blocks_out.append({"type": "running_head", "text": head})
            continue

        entry: dict = {"type": kind, "text": text}
        if kind == "note":
            digits = re.match(r"\s*(\d{1,3})[.\s]", re.sub(r"<[^>]+>", "", text))
            if digits:
                entry["n"] = int(digits.group(1))
        blocks_out.append(entry)

    return {
        "source": image.name,
        "side": "L" if "-L-" in image.name else ("R" if "-R-" in image.name else None),
        "folio": int(folio) if folio and folio.isdigit() else folio,
        "order": order,
        "blocks": blocks_out,
        "_seconds": round(elapsed, 1),
    }


def link_page_breaks(pages: list[dict]) -> None:
    """Flag paragraphs interrupted by a page break, in reading order.

    Surya sees one page at a time, so this is the one piece of structure that
    can only be recovered by looking at a pair. A body block that ends without
    terminal punctuation is almost certainly cut off; if the next page opens
    lower-case or on a closing quote, that is its other half.
    """
    body = lambda p: [b for b in p["blocks"] if b["type"] in ("paragraph", "block_quote")]
    for earlier, later in zip(pages, pages[1:]):
        head, tail = body(earlier), body(later)
        if not head or not tail:
            continue
        last_text = re.sub(r"<[^>]+>", "", head[-1]["text"]).rstrip()
        first_text = re.sub(r"<[^>]+>", "", tail[0]["text"]).lstrip()
        if not last_text or not first_text:
            continue
        if last_text.endswith(SENTENCE_FINAL):
            continue
        if first_text[0].islower() or first_text[0] in '”’)"\'':
            head[-1]["continues"] = True
            tail[0]["continued"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-order", type=int, default=1)
    args = parser.parse_args()

    try:
        from surya.recognition import RecognitionPredictor
    except ImportError:
        sys.exit("this skill needs Surya:  pip install surya-ocr  (plus a llama-server binary)")

    args.out.mkdir(parents=True, exist_ok=True)
    predictor = RecognitionPredictor()   # loads weights once, reused per page

    pages = []
    for offset, image in enumerate(sorted(args.images)):
        page = transcribe(image, args.start_order + offset, predictor)
        pages.append(page)
        print(f"   {image.name} -> folio {page['folio']}, "
              f"{len(page['blocks'])} blocks, {page.pop('_seconds')}s")

    link_page_breaks(pages)

    for page in pages:
        name = f"page-{page['folio']}.json" if page["folio"] is not None else f"page-{page['order']:04d}.json"
        (args.out / name).write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n")
    print(f"   wrote {len(pages)} page record(s) to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
