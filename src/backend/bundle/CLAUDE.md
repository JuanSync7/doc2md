---
title: Agent rules — backend.bundle
kind: rules
layer: backend
status: draft
owner: TBD
summary: Local rules for the output-bundle assembler — pure composition of sections + validate + ingest, no disk, no LLM.
---
# Agent rules — `src/backend/bundle/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- **Pure composition only.** This package assembles the bundle from other packages'
  public APIs (`sections`, `validate`, `ingest`). It must add **no** conversion,
  validation, or chunking logic of its own — if a rule about *content* belongs
  anywhere, it belongs in the package that owns it, not here.
- **No disk, no network, no LLM.** Strings and dicts in, a bundle dict out. File
  writing lives in `scripts/build_bundle.py`. Image captions are populated by the
  separate captioning stage, never here.
- **Stays Python 3.6-compatible and stdlib-only** — it runs in the same offline
  pipeline as `ingest`/`validate`. No PEP 604 unions, no f-string-only assumptions;
  use type comments.
- **The bundle shape is the contract.** Keep it in sync with the design spec
  (doc2md `docs/design/output-contract.md`); both lanes must emit the same shape,
  and `report.json` must stay lane-honest about losslessness.
- Keep the public surface in `__init__.py`/`__all__`; implementation in `_*` modules.
