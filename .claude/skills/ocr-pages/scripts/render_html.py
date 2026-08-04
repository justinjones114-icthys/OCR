#!/usr/bin/env python3
"""Render page JSON into per-page and concatenated HTML.

Two views of the same data, because they answer different questions:

  pages/page-<folio>.html   what this physical page says, furniture and all,
                            so it can be checked against the photograph
  book.html                 the text as prose -- furniture dropped, paragraphs
                            rejoined across page breaks, notes linked

Usage:
    python render_html.py pages/ --out build/
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

# Exactly the vocabulary SKILL.md permits in `text`. Anything else a model
# emits is stripped rather than trusted -- the stylesheet below is written
# against this list, and unknown markup would render as a surprise.
ALLOWED = {
    "em": set(),
    "strong": set(),
    "br": set(),
    "sub": set(),
    "sup": {"class", "data-n"},
    "span": {"class"},
    "a": {"href", "class", "id"},
}

FURNITURE = {"running_head", "folio"}
TAG_RE = re.compile(r"<[^>]+>")

CSS = """
:root { color-scheme: light dark; }
body { margin: 0 auto; max-width: 38rem; padding: 3rem 1.25rem 6rem;
       font: 1.05rem/1.7 Georgia, 'Iowan Old Style', serif;
       color: #1c1a17; background: #fbfaf7; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e1d8; background: #16150f; }
  .folio, .running-head, .pagemark { color: #8b8578; }
  .pagemark { border-color: #34312a; }
  .unc { border-bottom-color: #b58b3a; }
}
h1 { font-size: 1.5rem; letter-spacing: .02em; }
.running-head, .folio { font-size: .78rem; letter-spacing: .14em;
       text-transform: uppercase; color: #8a8375; margin: 0 0 .35rem; }
h2 { font-size: .95rem; letter-spacing: .18em; text-transform: uppercase;
     text-align: center; font-weight: 600; margin: 2.75rem 0 1.25rem; }
p { margin: 0 0 .1rem; text-indent: 1.4em; text-align: justify;
    hyphens: auto; }
p.opening { text-indent: 0; margin-top: 1.1rem; }
blockquote { margin: 1.1rem 0 1.1rem 1.6rem; font-size: .96rem; }
.sc { font-variant: small-caps; letter-spacing: .04em; }
sup.noteref { font-size: .68em; vertical-align: super; line-height: 0; }
sup.noteref a { text-decoration: none; color: #9a5b2d; padding: 0 .1em; }
.unc { border-bottom: 1.5px dotted #b8791f; }
.pagemark { display: block; margin: 1.6rem 0; font-size: .7rem;
    letter-spacing: .16em; text-transform: uppercase; color: #a09889;
    border-top: 1px solid #e6e0d4; padding-top: .5rem; text-decoration: none; }
.notes { margin-top: 3rem; border-top: 1px solid #e6e0d4; padding-top: 1rem;
    font-size: .9rem; }
.notes p { text-indent: 0; margin-bottom: .5rem; }
.figure { font-style: italic; color: #6b6459; text-indent: 0; }
"""


def sanitize(text: str) -> str:
    """Drop any markup outside the documented allowlist, keeping the text."""

    def keep(match: re.Match) -> str:
        tag = match.group(0)
        name = re.match(r"</?\s*([a-zA-Z0-9]+)", tag)
        if not name or name.group(1).lower() not in ALLOWED:
            return ""
        allowed_attrs = ALLOWED[name.group(1).lower()]
        for attr in re.findall(r'([a-zA-Z-]+)\s*=', tag):
            if attr.lower() not in allowed_attrs:
                return ""
        return tag

    return TAG_RE.sub(keep, text)


def mark_uncertain(text: str, spans: list[str]) -> str:
    """Underline uncertain readings, without ever editing inside a tag."""
    for span in spans:
        if not span:
            continue
        parts = re.split(r"(<[^>]+>)", text)
        for i, part in enumerate(parts):
            if part.startswith("<") or span not in part:
                continue
            parts[i] = part.replace(
                span, f'<span class="unc" title="uncertain reading">{span}</span>', 1
            )
            break
        text = "".join(parts)
    return text


def link_noterefs(text: str, known: set[int]) -> str:
    def repl(match: re.Match) -> str:
        n = int(match.group(1))
        inner = match.group(2)
        if n not in known:
            return match.group(0)
        return (
            f'<sup class="noteref" id="ref-{n}">'
            f'<a href="#note-{n}">{inner}</a></sup>'
        )

    return re.sub(
        r'<sup class="noteref" data-n="(\d+)">(.*?)</sup>', repl, text, flags=re.S
    )


def block_html(block: dict, opening: bool = False) -> str:
    kind = block.get("type", "paragraph")
    text = mark_uncertain(sanitize(block.get("text", "")), block.get("uncertain", []))
    if kind == "running_head":
        return f'<p class="running-head">{text}</p>'
    if kind == "folio":
        return f'<p class="folio">{text}</p>'
    if kind == "heading":
        return f"<h2>{text}</h2>"
    if kind == "block_quote":
        return f"<blockquote>{text}</blockquote>"
    if kind == "figure":
        return f'<p class="figure">[{text}]</p>'
    if kind in ("note", "caption"):
        anchor = f' id="note-{block["n"]}"' if kind == "note" and "n" in block else ""
        return f'<p class="note"{anchor}>{text}</p>'
    cls = ' class="opening"' if opening else ""
    return f"<p{cls}>{text}</p>"


def document(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def load_pages(src: Path) -> list[dict]:
    files = sorted(src.glob("page-*.json"))
    if not files:
        sys.exit(f"no page-*.json found in {src}")
    pages = []
    for f in files:
        try:
            pages.append(json.loads(f.read_text()))
        except json.JSONDecodeError as exc:
            sys.exit(f"{f.name}: invalid JSON -- {exc}")
    pages.sort(key=lambda p: (p.get("order") is None, p.get("order", 0), f"{p.get('folio')}"))
    return pages


def render_page(page: dict) -> str:
    folio = page.get("folio")
    label = f"page {folio}" if folio is not None else page.get("source", "page")
    parts, first_body = [], True
    for block in page.get("blocks", []):
        opening = block.get("type") == "paragraph" and first_body and not block.get("continued")
        if block.get("type") not in FURNITURE:
            first_body = False
        parts.append(block_html(block, opening))
    return document(label, "\n".join(parts))


def render_book(pages: list[dict], title: str) -> str:
    known_notes = {
        b["n"]
        for p in pages
        for b in p.get("blocks", [])
        if b.get("type") == "note" and "n" in b
    }

    body: list[str] = [f"<h1>{html.escape(title)}</h1>"]
    notes: list[str] = []
    pending: str | None = None  # an unterminated paragraph awaiting its other half

    for page in pages:
        folio = page.get("folio")
        anchor = f"p{folio}" if folio is not None else f"i{page.get('order', 0)}"
        mark = (
            f'<a class="pagemark" id="{anchor}" href="#{anchor}">'
            f'page {folio if folio is not None else "?"}</a>'
        )
        opened = False

        for block in page.get("blocks", []):
            kind = block.get("type", "paragraph")
            if kind in FURNITURE:
                continue
            if kind == "note":
                notes.append(block_html(block))
                continue

            rendered = block_html(block, opening=not opened and not block.get("continued"))
            rendered = link_noterefs(rendered, known_notes)

            if block.get("continued") and pending is not None:
                # Splice the page marker inside the rejoined paragraph so the
                # boundary stays citable without breaking the sentence.
                inner = re.sub(r"^<p[^>]*>|</p>$", "", rendered)
                body.append(pending[: -len("</p>")] + " " + mark + " " + inner + "</p>")
                pending = None
                opened = True
                continue

            if not opened:
                body.append(mark)
                opened = True

            if block.get("continues"):
                pending = rendered
            else:
                body.append(rendered)

        if not opened:
            body.append(mark)

    if pending:
        body.append(pending)
    if notes:
        body.append('<div class="notes"><h2>Notes</h2>' + "\n".join(notes) + "</div>")
    return document(title, "\n".join(body))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path, help="directory of page-*.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="Transcription")
    args = parser.parse_args()

    pages = load_pages(args.src)
    (args.out / "pages").mkdir(parents=True, exist_ok=True)

    for page in pages:
        folio = page.get("folio")
        name = f"page-{folio}.html" if folio is not None else f"page-{page.get('order', 0)}.html"
        (args.out / "pages" / name).write_text(render_page(page))

    (args.out / "book.html").write_text(render_book(pages, args.title))
    print(f"   {len(pages)} page(s) -> {args.out}/pages/ and {args.out}/book.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
