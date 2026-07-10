---
title: backend.validate — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for the validate package (the output-validator layer).
---

# Agent rules — `src/backend/validate/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- This is the **validator** layer — output checks and the lossless conversion
  gate. Keep converters, routing, and config OUT of here; those are the run path
  (`backend.ingest`). The separation is the whole point of this directory.
- Depend on `backend.ingest` for measurement primitives (`coverage`,
  `markdown_to_text`) through its public API. Never the reverse — `ingest` must
  not import `validate`, so the dependency stays one-way.
- Stays **Python 3.6-compatible** and **stdlib-only**: pure policy on markdown
  strings, no disk/network/models. File I/O belongs in the `scripts/` runners.
- Keep the public surface in `__init__.py`/`__all__`; implementation in `_*`
  modules.
