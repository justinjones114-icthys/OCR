#!/usr/bin/env python3
"""UNTESTED: local vision-language model -> page JSON.

This has never been run end to end. It was written on a machine with no GPU,
so the only thing verified is that it parses. Everything about the model's
actual behaviour -- whether it honours the schema, whether it invents text,
whether it labels blocks consistently -- is unknown.

Ask the model for JSON directly rather than for prose, because the failure
modes differ: a model asked for prose will happily narrate what it sees ("This
page discusses..."), whereas a schema gives it a shape to fill and makes a
refusal or a derailment obvious rather than subtle.

Usage:
    python transcribe_vlm.py flat/*.jpg --out pages/ --model qwen2.5vl:7b
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROMPT = """Transcribe this page from a printed book into JSON.

Return ONLY a JSON object, no commentary, with this shape:

{"folio": <the page number printed on the page, or null>,
 "blocks": [{"type": "...", "text": "..."}]}

Block types, in reading order:
  running_head  the book or chapter title printed in the top margin
  folio         the printed page number
  heading       a section heading within the page
  paragraph     body text
  block_quote   a quotation set off by indentation or smaller type
  note          the body of a footnote, if one is printed on this page
  caption       a figure or table caption
  figure        a non-text region; describe it briefly in text

Rules that matter more than fluency:

- Transcribe EXACTLY what is printed. Keep the original spelling, capitalisation
  and punctuation, including typographic quotes, em dashes and spaced ellipses.
  Keep apparent typos. Do not modernise, correct, expand or tidy anything.
- If a word is obscured or illegible, do not guess a plausible one. Write your
  best reading and list that exact substring in an "uncertain" array on the
  block. If unreadable, write [illegible].
- A word broken across lines is written whole: "contempla-" + "tion" is
  "contemplation". But keep the hyphen where the compound is normally
  hyphenated, as in "self-indulgences".
- Mark superscript note reference numbers inline as
  <sup class="noteref" data-n="N">N</sup>. These are references; the note bodies
  are usually elsewhere in the book, so do not invent them.
- Inline markup is limited to <em>, <strong>, <sup>, <sub> and
  <span class="sc"> for small capitals.
- Faint mirrored text showing through from the other side of the leaf is not
  content. Ignore it.
"""


def extract_json(reply: str) -> dict | None:
    """Models wrap JSON in prose or fences more often than not."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = reply.find("{"), reply.rfind("}")
        candidate = reply[start : end + 1] if start != -1 and end > start else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def transcribe(image: Path, order: int, model: str) -> dict | None:
    import ollama

    started = time.perf_counter()
    reply = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": PROMPT, "images": [str(image)]}],
        options={"temperature": 0},
    )
    elapsed = time.perf_counter() - started

    parsed = extract_json(reply["message"]["content"])
    if parsed is None:
        print(f"!! {image.name}: model did not return parseable JSON", file=sys.stderr)
        return None

    blocks = [
        b for b in parsed.get("blocks", [])
        if isinstance(b, dict) and b.get("text") and b.get("type")
    ]
    if not blocks:
        print(f"!! {image.name}: no usable blocks in the reply", file=sys.stderr)
        return None

    return {
        "source": image.name,
        "side": "L" if "-L-" in image.name else ("R" if "-R-" in image.name else None),
        "folio": parsed.get("folio"),
        "order": order,
        "blocks": blocks,
        "_seconds": round(elapsed, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5vl:7b")
    parser.add_argument("--start-order", type=int, default=1)
    args = parser.parse_args()

    try:
        import ollama  # noqa: F401
    except ImportError:
        sys.exit("needs the ollama client:  pip install ollama")

    print("!! this adapter is UNVALIDATED -- verify a page against the photograph "
          "before trusting the output", file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    pages = []
    for offset, image in enumerate(sorted(args.images)):
        page = transcribe(image, args.start_order + offset, args.model)
        if page is None:
            continue
        pages.append(page)
        print(f"   {image.name} -> folio {page['folio']}, "
              f"{len(page['blocks'])} blocks, {page.pop('_seconds')}s")

    # Page-break stitching is shared with the measured skill rather than
    # reimplemented, so both paths infer continuations the same way.
    sibling = Path(__file__).resolve().parents[2] / "ocr-pages" / "scripts"
    if sibling.exists():
        sys.path.insert(0, str(sibling))
        try:
            from transcribe import link_page_breaks
            link_page_breaks(pages)
        except ImportError:
            print("!! could not import link_page_breaks; continuations not marked",
                  file=sys.stderr)

    for page in pages:
        name = (f"page-{page['folio']}.json" if page["folio"] is not None
                else f"page-{page['order']:04d}.json")
        (args.out / name).write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n")
    print(f"   wrote {len(pages)} page record(s) to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
