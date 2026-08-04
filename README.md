# OCR

Photographs of printed pages into faithful, structured HTML — entirely on the
local machine.

Built around a photo of an open book: a two-page spread, shot handheld, with the
page curving toward the spine and a book-stand arm riding the margin. That's the
input the pipeline is tuned for, and most of its design follows from what that
photo actually contains rather than what page images are assumed to look like.

## Install

```bash
./install.sh          # copies both skills into ~/.claude/skills/
./install.sh --check  # report dependency status, change nothing
```

Then in any Claude Code session, ask for it in plain language — "OCR these",
"transcribe this book page", "get the text off this scan" — or invoke
`/ocr-pages`.

Requires `pillow numpy scipy surya-ocr` and a `llama-server` binary on PATH
(Surya's inference backend). `install.sh --check` tells you what's missing.

## Pipeline

The scripts have no Claude dependency and run standalone, which is what you
want for a whole book:

```bash
S=~/.claude/skills/ocr-pages/scripts
python $S/split_spread.py  photos/*.jpg   --out pageimgs/   # spread -> two pages
python $S/dewarp.py        pageimgs/*.jpg --out flat/       # flatten the geometry
python $S/transcribe.py    flat/*.jpg     --out pages/      # Surya -> page JSON
python $S/qa.py            pages/                           # exits 1 if flagged
python $S/render_html.py   pages/         --out build/      # per-page + concatenated
```

`qa.py`'s exit code makes it a gate, so `&&` between steps stops a batch when
something needs a human.

## Why it is shaped this way

Three findings from measuring it on a real spread, each of which changed the
design:

**The gutter is found by ink density, not brightness.** Brightness fails on real
photos — the far page's inner margin is often as dark as the gutter shadow, and
a lighting gradient moves the darkest column somewhere arbitrary. Ink density is
driven by the text blocks themselves.

**Dewarping is not optional, and outline-based perspective correction makes
things worse.** A bound page curving toward the spine is not a planar rectangle,
so forcing its outline square shears the interior. Straightening the text
baselines directly fixes keystone and curl together. Measured, dewarping took
Tesseract from 0/11 note markers to 7/11 and changed roughly two words in five.

**Surya beats Tesseract decisively on what matters here**: 11/11 note markers
with nothing spurious against 7/11 with seven false positives, and 98.6%
vocabulary against 94.5%. Markers survive as `<sup>` in its per-block HTML
rather than having to be reconstructed from glyph geometry.

Full numbers in `.claude/skills/ocr-pages/SKILL.md`.

## Layout

```
.claude/skills/ocr-pages/       the measured pipeline
.claude/skills/ocr-pages-vlm/   local-VLM variant — UNVALIDATED, see its SKILL.md
tests/fixtures/                 synthetic page records covering every structural case
install.sh
```

Working directories (`pageimgs/`, `flat/`, `pages/`, `build/`) are gitignored:
they hold page images and transcribed text from whatever is being scanned, which
is your material, not code.
