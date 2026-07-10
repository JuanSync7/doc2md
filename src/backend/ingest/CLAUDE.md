---
title: backend.ingest — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for the ingest package.
---

# Agent rules — `src/backend/ingest/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- Stays **Python 3.6-compatible** and **stdlib-only** — this runs inside the 3.6
  pipeline. No `tomllib`, no PEP 604 unions, no f-strings-only assumptions; use
  type comments.
- Pure policy, no model/network/heavy deps. The docling model call belongs in
  `scripts/docling_convert.py` (Python 3.12), not here.
- Keep the public surface in `__init__.py`/`__all__`; implementation in `_*`
  modules. `doc_id` is the **single source of truth** for ids — never re-hash a
  path anywhere else.
