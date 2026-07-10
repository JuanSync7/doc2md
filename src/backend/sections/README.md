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

- `document_outline(text, token_count=None)` → `{"total_tokens", "has_toc", "outline": [node, …]}`
  - Each node: `id, level, title, anchor, line_span, self_tokens, subtree_tokens,
    tables, images, children`. `self_tokens`/`subtree_tokens` partition cleanly
    (parent == self + Σ children). Excludes table rows and list items from headings.
    Image `caption` is left `null` for the separable captioning stage. Line indices
    are **body-relative** (see `docs/design/output-contract.md`).
- `chunk_sections(doc_id, text, token_count=None)` → `[Section(...)]`
  - Heading-anchored, size-bounded chunks with stable ids + content fingerprints.
  - `token_count`: optional `str -> int` tokenizer. Given, budgets are measured in
    real tokens; omitted, char-based sizing (byte-for-byte stable).
- `is_heading(line)`, `normalize_title(s)` — shared heading helpers.
