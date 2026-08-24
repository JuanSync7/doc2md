---
title: backend.sections
kind: package
layer: backend
status: draft
owner: TBD
public_api: src/backend/sections/__init__.py
tags: [sections, outline, chunking, structure]
summary: Deterministic markdown structure — the faithful heading outline (structure.json) and a tokenizer-pluggable chunker.
---

# backend.sections

Deterministic, format-agnostic **structure** derived from markdown. It operates on
the lossless markdown any lane produces, never on a converter's internal tree, so
office and PDF outputs get identical structure. 3.6-compatible, stdlib-only (plus
`backend.ingest.markdown_to_text` for markdown stripping).

Two distinct things live here — a *map* and a *derivation*:

- **`document_outline`** — the faithful heading tree of the document (EVERY heading,
  nested by level). This feeds `structure.json`. It is a map of the document, not a
  chunking; a consumer reads the hierarchy instead of re-parsing the markdown.
- **`chunk_sections`** — a size-bounded chunker for downstream retrieval (RAG). It is
  a derivation *from* the markdown, orthogonal to the outline; both share the heading
  helpers so they agree on what a heading is.

## Public API

- `document_outline(text, token_count=None)` → `{"total_tokens", "has_toc",
  "levels_inferred", "outline": [node, …]}`
  - Each node: `id, section_id, parent, level, title, anchor, line_span, self_tokens,
    subtree_tokens, fingerprint, tables, images, links, children`.
    `self_tokens`/`subtree_tokens` partition cleanly (parent == self + Σ children), and
    `fingerprint` covers the node's own body so it partitions the same way. `id` is
    positional; `section_id` is content-derived and is what survives an inserted
    heading. Excludes table rows and list items from headings. Tables and images are
    addressable nodes; image `caption` is left `null` for the separable captioning
    stage. Line indices are **body-relative** (see `docs/design/output-contract.md`).
  - `levels_inferred` is `true` when the nesting was read from the titles' section
    numbering because the extractor emitted a single level for the whole document
    **and** those numbers read as a nested outline (every nested number extends one
    the document already stated) — so a docx written with one heading style is never
    reshaped by digits that are measurements rather than section numbers.
- `chunk_sections(doc_id, text, token_count=None)` → `[Section(...)]`
  - Heading-anchored, size-bounded chunks with stable ids + content fingerprints.
  - `token_count`: optional `str -> int` tokenizer. Given, budgets are measured in
    real tokens; omitted, char-based sizing (byte-for-byte stable).
- `is_heading(line)`, `normalize_title(s)`, `gfm_anchor(title)`,
  `fenced_lines(lines)` — shared helpers. `gfm_anchor` is the one anchor scheme: the
  `#fragment` a renderer emits for a heading — lowercase, non-word/space/hyphen
  characters dropped, every run of spacing-and-hyphens collapsed to one `-` — kept in
  step with `validate.gfm_anchor` and `kb.heading_anchor` by
  `tests/unit/backend/test_anchor_parity.py`. `is_heading` returns the length of the
  **leading** ATX hash run, never the count of hashes on the line.
  `fenced_lines` is the per-line "this is code, not prose" mask both the outline and
  the chunker filter through, so a shell comment in a transcript is never a heading
  and pipe art in a fence is never a table.
