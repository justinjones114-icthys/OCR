# Fixtures

`fixtures/page-42.json` and `page-43.json` are a synthetic two-page record —
invented text, no copyrighted source — that exercises every structural feature
the renderer has to get right:

- page furniture (`running_head`, `folio`) kept per page, dropped when concatenated
- a paragraph interrupted mid-sentence by the page break, rejoined via
  `continues` / `continued`, with the page anchor spliced inside it
- `noteref` markers resolved to `note` bodies
- an `uncertain` span marked for review
- a compound hyphen (`self-supporting`) that must survive
- `[illegible]` passed through untouched

Render and eyeball:

    python .claude/skills/ocr-pages/scripts/render_html.py tests/fixtures/ --out /tmp/ocr-check/
