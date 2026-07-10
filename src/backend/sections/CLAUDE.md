---
title: Agent rules — backend.sections
kind: rules
layer: backend
status: draft
owner: TBD
summary: Local rules for the deterministic structure layer — heading outline + chunker, 3.6/stdlib, no LLM.
---
# Agent rules — `src/backend/sections/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- **Deterministic, no LLM, no disk.** Pure functions over markdown strings. Image
  captions are populated by the separate captioning stage, never here (the outline
  emits `caption: null`).
- **Stays Python 3.6-compatible and stdlib-only** — it runs in the same offline
  pipeline as `ingest`/`validate`. Only cross-package dependency allowed is
  `backend.ingest` (for `markdown_to_text`), through its public API. Use type comments.
- **The outline is a map, the chunker is a derivation.** `document_outline` reflects
  the document's true heading hierarchy; `chunk_sections` is size-bounded retrieval
  chunking. Keep them separate; they only share the heading helpers.
- **`self_tokens`/`subtree_tokens` must partition** (parent == self + Σ children) and
  line indices stay **body-relative** — don't break either; both are contract.
- Keep the public surface in `__init__.py`/`__all__`; implementation in `_*` modules.
