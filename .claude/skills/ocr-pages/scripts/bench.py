#!/usr/bin/env python3
"""Compare OCR engines on your own pages, without a ground-truth transcription.

Writing a reference transcription by hand is the thing nobody does, so engine
choice usually gets made on vibes. These three metrics need no reference and
between them catch what actually goes wrong:

  noteref recall   You know which note markers are on the spread even if you
                   have not transcribed a word of it. Superscripts are the
                   hardest part of the job, so scoring them directly is worth
                   more than any aggregate.

  agreement        Pairwise word-level similarity between configurations.
                   Independent engines rarely fail the same way, so where two
                   agree they are probably both right, and where they diverge
                   is where the page is genuinely hard.

  dictionary rate  Share of alphabetic tokens that are real words. Garbage
                   output is self-identifying and this catches it cheaply.

Usage:
    python bench.py --raw pageimgs/ --flat flat/ --expect-noterefs 82-92
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ocr_engines as oe  # noqa: E402

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
DICT_PATHS = [Path("/usr/share/dict/words"), Path("/usr/share/dict/american-english")]


def load_dictionary() -> set[str]:
    for path in DICT_PATHS:
        if path.exists():
            return {w.strip().lower() for w in path.read_text(errors="ignore").splitlines()}
    return set()


def normalise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def agreement(a: str, b: str) -> float:
    ta, tb = normalise(a), normalise(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def dictionary_rate(text: str, words: set[str]) -> float:
    if not words:
        return float("nan")
    tokens = [t.lower().strip("'’-") for t in WORD_RE.findall(text)]
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return 0.0
    return sum(t in words for t in tokens) / len(tokens)


def markers_from(page: oe.Page) -> set[int]:
    """Markers an engine reported, from whichever channel it has.

    Tesseract fills `noterefs` geometrically; a VLM is asked to emit [[n]] in
    its transcription instead, since it has no boxes to measure.
    """
    found = set(page.noterefs)
    found |= {int(m.group(1)) for m in re.finditer(r"\[\[(\d{1,3})\]\]", page.text)}
    return found


def parse_range(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, help="split but un-dewarped page images")
    parser.add_argument("--flat", type=Path, help="dewarped page images")
    parser.add_argument("--expect-noterefs", default="", help='e.g. "82-92"')
    parser.add_argument("--engines", default="tesseract,surya,vlm")
    args = parser.parse_args()

    expected = parse_range(args.expect_noterefs)
    dictionary = load_dictionary()
    if not dictionary:
        print("!! no system word list; dictionary rate will be blank", file=sys.stderr)

    inputs = []
    for label, folder in (("raw", args.raw), ("flat", args.flat)):
        if folder and folder.exists():
            inputs.append((label, sorted(p for p in folder.glob("*.jpg"))))
    if not inputs:
        sys.exit("nothing to benchmark: pass --raw and/or --flat")

    requested = [e.strip() for e in args.engines.split(",") if e.strip()]
    available = []
    for name in requested:
        if name not in oe.ENGINES:
            print(f"!! unknown engine {name!r}", file=sys.stderr)
            continue
        probe, runner = oe.ENGINES[name]
        if probe():
            available.append((name, runner))
        else:
            print(f"   skipping {name}: not installed here", file=sys.stderr)
    if not available:
        sys.exit("no engines available")

    results: dict[str, dict] = {}
    for engine_name, runner in available:
        for prep, images in inputs:
            config = f"{engine_name}/{prep}"
            texts, seconds, markers, boxes = [], 0.0, set(), 0
            for image in images:
                try:
                    page = runner(image)
                except Exception as exc:  # a missing model should not kill the run
                    print(f"!! {config} failed on {image.name}: {exc}", file=sys.stderr)
                    break
                texts.append(page.text)
                seconds += page.seconds
                markers |= markers_from(page)
                boxes += len(page.words)
            else:
                joined = "\n".join(texts)
                results[config] = {
                    "text": joined,
                    "seconds": seconds,
                    "markers": markers,
                    "words": len(normalise(joined)),
                    "boxes": boxes,
                    "dict": dictionary_rate(joined, dictionary),
                }

    if not results:
        sys.exit("every engine failed")

    print(f"\n{'config':<20}{'words':>7}{'dict':>8}{'noterefs':>11}{'secs':>8}")
    print("-" * 54)
    for config, r in results.items():
        hit = len(r["markers"] & expected) if expected else len(r["markers"])
        total = f"/{len(expected)}" if expected else ""
        rate = "  n/a" if r["dict"] != r["dict"] else f"{r['dict']:.1%}"
        print(f"{config:<20}{r['words']:>7}{rate:>8}{f'{hit}{total}':>11}{r['seconds']:>8.1f}")

    if expected:
        print("\nnoteref detail (expected " + ", ".join(map(str, sorted(expected))) + ")")
        for config, r in results.items():
            missing = sorted(expected - r["markers"])
            spurious = sorted(r["markers"] - expected)
            note = []
            if missing:
                note.append("missed " + ",".join(map(str, missing)))
            if spurious:
                note.append("false " + ",".join(map(str, spurious[:8])))
            print(f"  {config:<18}{'; '.join(note) if note else 'all found, none spurious'}")

    configs = list(results)
    if len(configs) > 1:
        print("\npairwise agreement")
        for i, a in enumerate(configs):
            for b in configs[i + 1 :]:
                score = agreement(results[a]["text"], results[b]["text"])
                print(f"  {a:<18} vs {b:<18} {score:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
