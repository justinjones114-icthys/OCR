---
name: ocr-pages
description: >-
  Turn photographs or scans of printed pages into structured, verbatim HTML —
  splitting two-page spreads at the gutter, transcribing exactly what is on the
  page, tagging semantic blocks (running heads, folios, headings, paragraphs,
  block quotes, note references and note bodies), rejoining line-break
  hyphenation, and stitching paragraphs that run across a page break. Use this
  skill whenever someone has images of printed pages and wants the text out of
  them — including "OCR these", "transcribe this book page", "digitize my
  notes", "get the text off this scan", "make these pages searchable", or when
  they simply drop in a photo of an open book — even if they never say "OCR".
  Also use it when they want page images turned into HTML, Markdown, or an
  ebook-like document.
---

# ocr-pages

Photographs of printed pages into a faithful, structured document.

The hard part of this job is not reading the letters — vision handles that well.
The hard part is **not quietly improving the text**, and keeping enough structure
that the result is still a document rather than a wall of words. Most of this
skill is about those two things.

## Workflow

1. **Split** any two-page spread into single pages (`scripts/split_spread.py`).
2. **Flatten** each page (`scripts/dewarp.py`) — required for the local-engine
   path, optional when you are reading the image yourself.
3. **Recognize**: either look at the page image and transcribe it directly, or
   run a local OCR engine (`scripts/ocr_engines.py`). Either way the output is
   page JSON, one file per page, following the rules below.
4. **Render** the JSON into per-page and concatenated HTML
   (`scripts/render_html.py`).

Steps 1, 2 and 4 are deterministic and belong in the scripts — don't
reimplement them inline. Step 3 is the judgment.

### Choosing between reading it yourself and running an engine

Reading the image directly is far more accurate, especially on superscripts and
typographic punctuation, and it needs no dewarping. Prefer it for a handful of
pages.

Reach for a local engine when the volume is large, when the pages must not
leave the machine, or when the material is under copyright and bulk
reproduction through a hosted model is not appropriate. The tradeoff is real
and measured — see the benchmark section below.

## Step 1 — Split

```bash
python scripts/split_spread.py PHOTO.jpg [more.jpg ...] --out pages/
```

Detects the gutter by finding the widest vertical band of *no ink* near the
centre, cuts there, and writes `<stem>-L.jpg` / `<stem>-R.jpg` downscaled to
2576px on the long edge (the most resolution the vision model can use — beyond
that you pay tokens for nothing).

It also writes `<stem>-split.json` with a `confidence` field. If confidence is
`low`, the gutter valley was narrow or ambiguous — open the two crops and check
the cut before transcribing, because a bad split silently truncates a column of
text and the transcription will look perfectly plausible.

Use `--single` for photos that are already one page, and `--no-split` to only
downscale. Photos of a *flat* single page need no geometry work at all.

## Step 2 — Transcribe

Read one page image at a time and write `pages/page-<folio>.json`. Work from the
image itself, not from what you expect the text to say.

### Transcribe verbatim — this is the whole job

You are producing a *record of what is printed*, and someone will quote from it
believing it is exact. That makes the ordinary instinct to smooth prose into
something clean actively harmful here. So:

- Keep archaic and non-standard spelling, inconsistent capitalisation, and the
  author's punctuation, including typographic quotes (`"` `"` `'` `'`), em and en
  dashes, and ellipses spelled as `. . .` if that's how they're set.
- Keep apparent typos. A misprint in the source is data, not a defect to fix.
- Never summarise, paraphrase, condense, or "tidy" a sentence, and never skip a
  line because it looks like boilerplate.
- Don't translate, don't modernise, don't expand abbreviations.

### When you can't read something, say so — never guess

The one failure mode that matters here is fluent invention: a word that is
wrong but reads perfectly, which no downstream check will catch and no reader
will question. A visible gap is far cheaper than a plausible fabrication.

If a word or phrase is obscured (a finger, a page-holder clip, glare, the
gutter shadow) or genuinely illegible, transcribe your best reading and list
that exact substring in the block's `uncertain` array. If you cannot read it at
all, use `[illegible]` in the text.

Resist the pull of context. Knowing what word *should* follow is exactly what
makes a confident wrong reading feel right.

### Blocks

Every page is a list of blocks in reading order:

| `type` | What it is |
|---|---|
| `running_head` | Book or chapter title printed in the top margin |
| `folio` | The printed page number |
| `heading` | A section heading within the page |
| `paragraph` | Body text |
| `block_quote` | A quotation set off by indentation or smaller type |
| `note` | The *body* of a footnote or endnote |
| `caption` | Figure or table caption |
| `figure` | A non-text region — describe it in `text` |

`running_head` and `folio` are page furniture: kept per-page for provenance,
suppressed in the concatenated document so they don't interleave into the prose.

### Note references vs. note bodies

These are two different things and conflating them loses data.

A **note reference** is the superscript marker inside a sentence. It is inline
markup, not a block:

```
...you have not yet attained union".<sup class="noteref" data-n="89">89</sup>
```

A **note body** is the text of the note itself. In a footnoted book it sits at
the foot of the page; in an endnoted book it lives in back matter, hundreds of
pages away, and **is simply not on this page**. When you see markers 82–92 in
running text with nothing at the foot of the page, the correct output is
inline `noteref` markup and *no* `note` blocks. Don't invent note bodies to
pair with the markers, and don't drop the markers because their bodies are
missing — the renderer links them up later if and when the notes get scanned.

### Inline markup

`text` holds a restricted set of inline HTML, and nothing else:

- `<em>` — italic
- `<strong>` — bold
- `<span class="sc">` — small capitals
- `<sup class="noteref" data-n="N">N</sup>` — a note reference marker
- `<sup>` / `<sub>` — other superscripts and subscripts

Escape `&`, `<`, `>` that are part of the text itself. No other tags, no
attributes beyond those shown — the renderer's CSS assumes exactly this
vocabulary.

### Line breaks and hyphenation

Transcribe into flowing text. Line breaks inside a paragraph are an artifact of
the page's column width, not of the writing, so don't preserve them.

That makes end-of-line hyphens easy in the common case: a word broken across
lines is just written whole. `contempla-` / `tion` is `contemplation`;
`self-` / `ishness` is `selfishness`.

The case needing judgment is a compound that happens to break *at* its own
hyphen. Keep the hyphen if the word is hyphenated in ordinary running text
(`self-` / `evident` → `self-evident`), drop it if it isn't. Hyphens and
slashes that fall mid-line are never line-break artifacts — `fifty-two` and
`brotherly/sisterly` pass through untouched.

Poetry, addresses, and tabular material are the exception: there the line breaks
carry meaning. Use `<br>` inside a paragraph block for those.

### Paragraphs that cross a page break

A paragraph interrupted by the end of the page needs to be readable both ways —
faithful per page, continuous when concatenated. Mark both halves:

- last block of the earlier page: `"continues": true`
- first block of the later page: `"continued": true`

Transcribe each half exactly as it appears; the renderer joins them. Note that
the join often falls mid-word or mid-phrase — page 128 ending `...you have not
yet attained` and 129 opening `union".` is one sentence, and only the flags make
that recoverable.

### Page JSON

```json
{
  "source": "IMG_2859.jpeg",
  "side": "L",
  "folio": 128,
  "order": 1,
  "blocks": [
    { "type": "running_head", "text": "FIRE WITHIN" },
    { "type": "folio", "text": "128" },
    { "type": "paragraph", "continued": true,
      "text": "repeatedly explains a lack of growth by underlining our reluctance..." },
    { "type": "paragraph", "continues": true,
      "uncertain": ["brotherly/sisterly"],
      "text": "St. Teresa singles out brotherly/sisterly love as a prime example..." }
  ]
}
```

`folio` is the number *printed on the page* — use it as the page's identity
rather than filename order, so re-shoots and out-of-order captures still land in
the right place. If the page carries no folio (chapter openers usually don't),
set it to `null` and rely on `order`, which is just the page's position in the
capture sequence.

Full field reference: `references/schema.json`.

## Step 3b — Local engines, and picking one

```bash
python scripts/dewarp.py pages/*.jpg --out flat/
python scripts/bench.py --raw pages/ --flat flat/ --expect-noterefs 82-92
```

`bench.py` compares engines on your own pages without needing a reference
transcription — the thing nobody has, and the reason engine choice usually gets
made on impressions. It scores recall of note markers you already know are on
the spread, pairwise agreement between engines, and the share of output tokens
that are real words.

Measured on one photographed spread of dense serif type carrying eleven note
markers, on a 4-core CPU with no GPU:

| config | dictionary rate | note markers | seconds |
|---|---|---|---|
| `tesseract/raw` | 85.7% | 0 / 11 | 14.4 |
| `tesseract/flat` | 94.5% | 7 / 11, plus 7 false | 5.8 |
| `surya/flat` | 98.6% | 11 / 11, none false | 564 |

Agreement between the two flattened runs was 90.2%; between raw and flattened
Tesseract, only 60% — meaning dewarping changed roughly two words in five.

Three things follow.

**Dewarping is not optional for a local engine.** It is the difference between
finding no markers and finding most of them, and it makes Tesseract nearly
three times faster because the engine stops fighting the geometry.

**Surya is the one to reach for if note references matter.** It returns layout
blocks with labels and reading order rather than bare lines, and superscripts
survive in its per-block HTML, so markers come out exactly right instead of
being reconstructed from glyph geometry.

**Ignore that seconds column.** Surya is a vision-language model and this was
CPU-only; on a GPU it is a different measurement entirely. The accuracy columns
are the ones that transfer. Tesseract remains the sensible choice when there is
no GPU and no appetite for model downloads — accept that you will be reviewing
every marker by hand.

## Step 4 — Render

```bash
python scripts/render_html.py pages/ --out build/
```

Writes `build/pages/page-<folio>.html` for each page and `build/book.html`
concatenated, both self-contained (CSS inlined, no external assets). The
concatenated document drops page furniture, joins `continues`/`continued` pairs
into single paragraphs, marks each page boundary with a citable anchor, and
resolves `noteref` markers to `note` bodies wherever both exist.

Uncertain spans render with a dotted underline so a human skimming the output
can see exactly where to check the photo, rather than having to trust the whole
document equally.

## Checking the result

Before calling a batch done, verify the things that fail silently:

- Every page in, every page out — a dropped page leaves no trace in the HTML.
- Folios run consecutively. A gap means a missed or misread page.
- Spot-check the first and last line of each page against the image. Those are
  where splitting and framing errors land.
- Skim the dotted-underline spans. If there are none at all across a long
  document, that is itself suspicious — real photographs of real books have
  ambiguous words in them.
