---
name: ocr-pages
description: >-
  Turn photographs or scans of printed pages into structured, verbatim HTML —
  splitting two-page spreads at the gutter, flattening the page geometry,
  transcribing with Surya, tagging semantic blocks (running heads, folios,
  headings, paragraphs, block quotes, note references and note bodies),
  rejoining line-break hyphenation, stitching paragraphs that run across a page
  break, and gating the result with quality checks that flag only the pages
  worth reviewing. Use this skill whenever someone has images of printed pages
  and wants the text out of them — including "OCR these", "transcribe this book
  page", "digitize my notes", "get the text off this scan", "make these pages
  searchable", or when they simply drop in a photo of an open book — even if
  they never say "OCR". Also use it when they want page images turned into
  HTML, Markdown, or an ebook-like document.
---

# ocr-pages

Photographs of printed pages into a faithful, structured document, entirely on
the local machine.

The hard part of this job is not reading the letters. It is **not quietly
improving the text**, keeping enough structure that the result is still a
document, and knowing which pages to distrust. Most of this skill is about
those three things.

## The pipeline

```bash
python scripts/split_spread.py PHOTO.jpg    --out pageimgs/   # spread -> two pages
python scripts/dewarp.py       pageimgs/*.jpg --out flat/     # flatten the geometry
python scripts/transcribe.py   flat/*.jpg     --out pages/    # Surya -> page JSON
python scripts/qa.py           pages/                         # flag pages to review
python scripts/render_html.py  pages/         --out build/    # per-page + concatenated
```

Every step is deterministic and belongs in its script — don't reimplement any
of it inline. Your judgment is needed in exactly one place: the pages `qa.py`
flags. See **Reviewing flagged pages** below, which is the part of this skill
that actually needs a reader.

## Why Surya, and why the geometry step is not optional

Measured on one photographed spread of dense serif type carrying eleven note
markers, 4-core CPU, no GPU:

| config | vocabulary | note markers | seconds |
|---|---|---|---|
| Tesseract, un-flattened | 85.7% | 0 / 11 | 14.4 |
| Tesseract, flattened | 94.5% | 7 / 11, plus 7 spurious | 5.8 |
| **Surya, flattened** | **98.6%** | **11 / 11, none spurious** | 564 |

Agreement between the two flattened runs was 90.2%; between raw and flattened
Tesseract, only 60% — meaning **dewarping changed roughly two words in five.**
Skipping it does not cost you a little accuracy, it costs you the note markers
entirely.

Surya wins because of what it returns, not just how well it reads: layout
blocks carrying a canonical label and a reading-order index, with superscripts
surviving in per-block HTML. Markers are therefore *read* rather than
reconstructed from glyph geometry, and the block taxonomy below mostly falls
out of the engine.

That seconds column does not transfer — Surya is a vision-language model and
this was CPU-only. On a GPU it is a different measurement.

## What `transcribe.py` already handles

Don't redo these by hand; they are implemented and tested:

- **Layout labels → block types**, via Surya's canonical labels.
- **Folio extraction** from a combined running head (`128 FIRE WITHIN` splits
  into a `folio` and a `running_head` block).
- **Quote re-curling.** Surya emits ASCII `"` and `'` for more than half the
  quotation marks on a typographically set page — 9 straight doubles and 6
  straight singles against 9 curly, measured. Since the book sets every quote
  typographically, a straight glyph is always wrong, and which curly form to
  use is recoverable from position. This is Surya's one measured defect.
- **Note reference markup**, normalised to
  `<sup class="noteref" data-n="N">N</sup>`.
- **Cross-page paragraph stitching**, inferred from terminal punctuation and
  leading case, since Surya sees one page at a time and cannot know.

Verified correct on the sample spread: line-break hyphens joined
(`contempla-`/`tion`), genuine compounds kept (`self-indulgences`,
`fifty-two`), slashes intact (`brotherly/sisterly`), em dashes and spaced
ellipses preserved, show-through ghost text excluded, and — the subtle one —
the book's *own inconsistency* about quote-and-period order reproduced rather
than tidied (`corners".⁸²` outside, `conscience."⁸⁵` inside).

## Block taxonomy

| `type` | What it is |
|---|---|
| `running_head` | Book or chapter title printed in the top margin |
| `folio` | The printed page number |
| `heading` | A section heading within the page |
| `paragraph` | Body text |
| `block_quote` | A quotation set off by indentation or smaller type |
| `note` | The *body* of a footnote or endnote |
| `caption` | Figure or table caption |
| `figure` | A non-text region, described in `text` |

`running_head` and `folio` are page furniture: kept per page for provenance,
suppressed in the concatenated document so they don't interleave into prose.

**Note references are not note bodies.** A reference is the superscript marker
inside a sentence — inline markup, not a block. A body is the note's text. In an
endnoted book the markers are on the page and the bodies are hundreds of pages
away in back matter, so a page showing markers 82–92 with nothing at its foot
should produce inline `noteref` markup and *no* `note` blocks. Never invent
bodies to pair with markers; the renderer links them if and when the notes get
scanned.

Full field reference: `references/schema.json`.

## Reviewing flagged pages

`qa.py` exists because OCR fails quietly: a dropped page leaves no hole, a
misread word reads as fluently as a correct one, and a truncated column looks
like a short page. Rather than trusting every page equally, it scores them and
names the few that need eyes.

It checks folio continuity (gaps mean a page was never shot, or the split
dropped one), note-marker sequence (markers run upward through a book, so an
out-of-order marker is a misread and a gap is a miss), vocabulary rate against
a system word list, stitching sanity, and surviving straight quote glyphs. It
exits non-zero when anything is flagged, so it works as a gate.

**For each flagged page, open the photograph and compare.** This is the one
place a reader beats the pipeline, and the rules that matter are:

Transcribe verbatim. You are producing a record someone will quote believing it
is exact, which makes the ordinary instinct to smooth prose actively harmful.
Keep archaic spelling, inconsistent capitalisation, the author's punctuation,
and apparent typos — a misprint in the source is data, not a defect.

When you can't read something, say so rather than guess. The failure that
matters is fluent invention: a word that is wrong but reads perfectly, which no
automated check catches and no reader questions. Put your best reading in the
text and list that exact substring in the block's `uncertain` array; use
`[illegible]` if you cannot read it at all. Resist context — knowing what word
*should* follow is exactly what makes a confident wrong reading feel right.

Uncertain spans render with a dotted underline, so a reviewer can see where to
check rather than having to trust the whole document equally. If a long
document has no uncertain spans at all, that is itself suspicious.

## Inline markup

`text` holds a restricted set of inline HTML and nothing else:

- `<em>`, `<strong>` — italic, bold
- `<span class="sc">` — small capitals
- `<sup class="noteref" data-n="N">N</sup>` — a note reference
- `<sup>`, `<sub>` — other super/subscripts

Escape `&`, `<`, `>` that belong to the text. The renderer's CSS assumes exactly
this vocabulary and strips anything else.

## Line breaks and hyphenation

Text flows; line breaks inside a paragraph are an artifact of column width, not
of the writing. A word broken across lines is written whole
(`contempla-`/`tion` → `contemplation`). Keep the hyphen only where the compound
is hyphenated in ordinary running text (`self-` / `evident` → `self-evident`).
Hyphens and slashes falling mid-line are never line-break artifacts.

Poetry, addresses and tabular matter are the exception — there the breaks carry
meaning, so use `<br>` inside the block.

## Requirements

```bash
pip install pillow numpy scipy surya-ocr
```

Surya also needs a `llama-server` binary on PATH (`brew install llama.cpp`, or
build the `llama-server` target from the llama.cpp source). Set
`LLAMA_CPP_BINARY` if it lives somewhere unusual. `qa.py` reads a system word
list from `/usr/share/dict/words` and degrades gracefully without one.

An alternative recognition step using a local vision-language model exists in
the sibling `ocr-pages-vlm` skill. It is unvalidated — prefer this one.
