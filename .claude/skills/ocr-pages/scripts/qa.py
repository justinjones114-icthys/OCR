#!/usr/bin/env python3
"""Quality gates over transcribed page JSON.

OCR fails quietly. A dropped page leaves no hole, a misread word reads as
fluently as a correct one, and a truncated column looks like a short page. So
rather than trusting the output evenly, this scores every page and flags the
ones worth a human's attention -- which on a clean book should be a handful,
not all of them.

Each check exists because it catches a failure that is otherwise invisible:

  folios       Gaps and duplicates are how you discover a page was never shot,
               or was shot twice, or that the gutter split silently dropped one.

  markers      Note numbers run upward through a book. An out-of-sequence
               marker is a misread; a gap is a miss. This is the cheapest
               correctness signal available and needs no reference text.

  vocabulary   The share of tokens that are real words. Garbage is
               self-identifying, and a page that dips below its neighbours is
               usually a page where the geometry went wrong.

  stitching    A paragraph flagged as continuing should not end in a full stop,
               and its other half should not begin with a capital. When both
               are violated the pairing is probably spurious.

  quotes       Straight glyphs on a typographically set page mean the
               re-curling pass missed something. Unbalanced counts mean a
               quotation lost an end.

Usage:
    python qa.py pages/ --min-dictionary 0.90
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
DICT_PATHS = [Path("/usr/share/dict/words"), Path("/usr/share/dict/american-english")]
FURNITURE = {"running_head", "folio"}
SENTENCE_FINAL = tuple(".!?") + ("”", "’", "…")


def load_dictionary() -> set[str]:
    for path in DICT_PATHS:
        if path.exists():
            return {w.strip().lower() for w in path.read_text(errors="ignore").splitlines()}
    return set()


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def page_text(page: dict) -> str:
    return " ".join(
        plain(b["text"]) for b in page["blocks"] if b["type"] not in FURNITURE
    )


def dictionary_rate(text: str, words: set[str]) -> float:
    if not words:
        return float("nan")
    tokens = [t.lower().strip("'’-") for t in WORD_RE.findall(text)]
    tokens = [t for t in tokens if len(t) > 1]
    return sum(t in words for t in tokens) / len(tokens) if tokens else 0.0


def markers_in(page: dict) -> list[int]:
    found = []
    for block in page["blocks"]:
        found += [int(m) for m in re.findall(r'data-n="(\d{1,3})"', block["text"])]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("--min-dictionary", type=float, default=0.90)
    parser.add_argument("--min-words", type=int, default=40)
    args = parser.parse_args()

    files = sorted(args.src.glob("page-*.json"))
    if not files:
        sys.exit(f"no page-*.json in {args.src}")
    pages = [json.loads(f.read_text()) for f in files]
    pages.sort(key=lambda p: (p.get("order") is None, p.get("order", 0)))
    dictionary = load_dictionary()

    flags: dict[str, list[str]] = {}

    def flag(page: dict, message: str) -> None:
        key = f"folio {page.get('folio')}" if page.get("folio") is not None else f"order {page.get('order')}"
        flags.setdefault(key, []).append(message)

    # --- per page -------------------------------------------------------
    print(f"{'page':<12}{'words':>7}{'dict':>8}{'markers':>9}  blocks")
    print("-" * 58)
    for page in pages:
        text = page_text(page)
        words = len(WORD_RE.findall(text))
        rate = dictionary_rate(text, dictionary)
        marks = markers_in(page)
        kinds = {}
        for b in page["blocks"]:
            kinds[b["type"]] = kinds.get(b["type"], 0) + 1
        summary = " ".join(f"{k}×{v}" for k, v in sorted(kinds.items()))
        shown = "  n/a" if rate != rate else f"{rate:.1%}"
        label = str(page.get("folio")) if page.get("folio") is not None else f"#{page.get('order')}"
        print(f"{label:<12}{words:>7}{shown:>8}{len(marks):>9}  {summary}")

        if rate == rate and rate < args.min_dictionary:
            flag(page, f"vocabulary {rate:.1%} below {args.min_dictionary:.0%} — check the geometry")
        if words < args.min_words:
            flag(page, f"only {words} words — page may be truncated or mostly image")
        if not any(b["type"] == "folio" for b in page["blocks"]):
            flag(page, "no printed folio found — ordering falls back to capture sequence")

        # quotes
        body = text
        straight = len(re.findall(r'["\']', body))
        if straight:
            flag(page, f"{straight} straight quote glyph(s) survived re-curling")
        if body.count("“") != body.count("”"):
            flag(page, f"unbalanced double quotes ({body.count('“')} open, {body.count('”')} close)")

        # stitching sanity
        for b in page["blocks"]:
            t = plain(b["text"]).strip()
            if b.get("continues") and t.endswith(SENTENCE_FINAL):
                flag(page, "block marked 'continues' but ends in terminal punctuation")
            if b.get("continued") and t[:1].isupper():
                flag(page, "block marked 'continued' but starts with a capital")

    # --- across pages ---------------------------------------------------
    folios = [p["folio"] for p in pages if isinstance(p.get("folio"), int)]
    print("\nsequence")
    if len(folios) >= 2:
        gaps = [(a, b) for a, b in zip(folios, folios[1:]) if b != a + 1]
        dupes = {f for f in folios if folios.count(f) > 1}
        print(f"  folios {folios[0]}–{folios[-1]} over {len(folios)} page(s)")
        for a, b in gaps:
            print(f"  !! folio jumps {a} -> {b}: {b - a - 1} page(s) missing" if b > a + 1
                  else f"  !! folio goes backwards {a} -> {b}")
        if dupes:
            print(f"  !! duplicate folios: {sorted(dupes)}")
        if not gaps and not dupes:
            print("  folios consecutive, no duplicates")
    else:
        print("  too few folios to check continuity")

    all_marks = [m for p in pages for m in markers_in(p)]
    if all_marks:
        out_of_order = [(a, b) for a, b in zip(all_marks, all_marks[1:]) if b < a]
        gaps = [(a, b) for a, b in zip(all_marks, all_marks[1:]) if b > a + 1]
        print(f"  markers {all_marks[0]}–{all_marks[-1]}, {len(all_marks)} found")
        for a, b in out_of_order:
            print(f"  !! marker {b} follows {a} — out of sequence, likely a misread")
        for a, b in gaps:
            print(f"  !! markers jump {a} -> {b} — {b - a - 1} missing")
        if not out_of_order and not gaps:
            print("  markers strictly consecutive")
    else:
        print("  no note markers found")

    # --- verdict --------------------------------------------------------
    print("\nreview queue")
    if flags:
        for key, messages in flags.items():
            print(f"  {key}")
            for m in messages:
                print(f"      - {m}")
        print(f"\n{len(flags)} of {len(pages)} page(s) need a look.")
    else:
        print("  nothing flagged.")
    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
