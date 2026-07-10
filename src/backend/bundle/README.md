---
title: backend.bundle
kind: package
layer: backend
status: draft
owner: TBD
public_api: src/backend/bundle/__init__.py
tags: [bundle, output-contract, doc2md, structure, report]
summary: Assemble the doc2md output bundle (document.md + structure.json + report.json) from body markdown, the heading outline, and the validator report.
---

# backend.bundle

The **output-contract** layer: it turns one converted document into the doc2md
bundle every downstream consumer reads. It is the single place that knows the
bundle shape, so the Office lane and the (future) PDF lane emit byte-identical
structure.

It sits **above** `sections`, `validate`, and `ingest` and composes their public
APIs — it adds no conversion or validation logic of its own:

- `backend.sections.document_outline` → the faithful heading tree + token counts
  (`structure.json`)
- `backend.validate.build_report` → the losslessness verdict + metrics + status
  (`report.json`), validator-only, **no LLM**
- `backend.ingest.front_matter` → the YAML block that maps source → markdown
  (`document.md`)

It is **pure**: strings and dicts in, a bundle dict out. All file I/O is the
caller's (`scripts/build_bundle.py`). See the design spec (doc2md
`docs/design/output-contract.md`) for the full schema and the Office-vs-PDF
losslessness asymmetry.

## Public API

- `assemble_bundle(doc_id, source_relpath, source_format, lane, source_text,
  body_md, ...)` → `{"document_md": str, "structure": dict, "report": dict}`
  - `body_md` is the markdown **body only** (no front matter): the gate scores the
    body and the assembler prepends its own front matter.
  - `token_count` (optional `str -> int`) is threaded into both the outline and the
    report so their token counts agree; `token_model` names it.
  - `lane` selects how losslessness is graded — office is a hard `recall == 1.0`
    gate; other lanes pass an explicit best-effort `losslessness` dict.
