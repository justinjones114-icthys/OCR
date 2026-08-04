---
name: ocr-pages-vlm
description: >-
  EXPERIMENTAL and UNVALIDATED variant of `ocr-pages` that swaps the Surya
  recognition step for a local vision-language model (Qwen2.5-VL or similar via
  ollama). Use this ONLY when someone explicitly asks for the VLM path, asks to
  evaluate a local vision model against Surya, or is working on a machine where
  Surya cannot run. For ordinary "OCR these pages" requests, including photos of
  book spreads, use `ocr-pages` instead — that one is measured and this one is
  not.
---

# ocr-pages-vlm — status: TBD

**Nothing here has been measured.** The `ocr-pages` skill was benchmarked on a
real photographed spread and its numbers are in its own SKILL.md. This variant
has not been run end to end even once, because the machine it was written on had
no GPU. Treat every claim below as a hypothesis.

Before relying on it, run the same QA gates the measured path uses and compare
against the published Surya figures. If it does not beat them, use `ocr-pages`.

## What this changes, and what it doesn't

Only the recognition step differs. The rest of the pipeline is shared, and this
skill deliberately does not duplicate it — use the scripts in the sibling skill:

| Step | Script | Status |
|---|---|---|
| Split spread | `../ocr-pages/scripts/split_spread.py` | measured, unchanged |
| Flatten | `../ocr-pages/scripts/dewarp.py` | measured, unchanged |
| **Recognize** | **`scripts/transcribe_vlm.py`** | **untested** |
| QA gates | `../ocr-pages/scripts/qa.py` | measured, unchanged |
| Render HTML | `../ocr-pages/scripts/render_html.py` | measured, unchanged |

The page JSON contract in `../ocr-pages/references/schema.json` is unchanged, so
if the adapter produces valid records everything downstream works untouched.

## Setup

```bash
ollama pull qwen2.5vl:7b     # ~16GB VRAM at fp16; a 3B variant fits smaller cards
pip install ollama
python scripts/transcribe_vlm.py flat/*.jpg --out pages/ --model qwen2.5vl:7b
```

## Why this might be worth trying

Surya's one measured weakness is that it emits ASCII quote glyphs on a
typographically set page, which `ocr-pages` repairs in post-processing with a
positional re-curling pass. A VLM reading the page as an image may preserve the
original glyphs directly, and may also handle unusual layouts — drop caps, marginalia,
mixed scripts — that a layout model trained on ordinary book pages will
mis-segment.

## Why it might not

Three specific risks, in the order they are likely to bite:

**Fluent invention.** This is the one that matters. A layout-and-recognition
model like Surya fails by producing garbage, which the vocabulary gate catches
immediately. A language model fails by producing *plausible text that is not on
the page*, which no automated check will catch and no reader will question. The
prompt below leans hard on this, but prompt instructions are not a guarantee.

**Silent reformatting.** Instruction-tuned models tidy things: straightening the
book's own inconsistent punctuation, normalising spelling, expanding
abbreviations, dropping a running head it judges to be noise. Each of those
quietly violates the verbatim contract.

**No layout labels.** Surya returns canonical block labels and reading order for
free, which is where `ocr-pages` gets its block taxonomy. A VLM has to be asked
for structure in the prompt and will be less consistent about it.

## What to measure before trusting it

Run against the same spread that produced the published Surya numbers, then:

1. `python ../ocr-pages/scripts/qa.py pages/` — vocabulary rate, marker
   sequence, folio continuity.
2. Marker recall specifically. Surya scored 11/11 with nothing spurious. Below
   that is a regression, not a tradeoff.
3. **Read one page against the photograph yourself.** The invention risk is not
   detectable any other way, and a single careful comparison is worth more here
   than any aggregate metric.

Record whatever you measure in this file and delete the TBD banner.
