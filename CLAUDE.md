---
title: Root agent rules
kind: rules
layer: n/a
status: stable
owner: TBD
summary: Global rules for any agent working in the doc2md repo.
---
# Agent rules — repo root

Read **`CONVENTIONS.md`** before doing structural work; it is the source of truth
for labeling and the directory taxonomy. Each directory's own `CLAUDE.md` adds
local rules that override these where more specific.

## Always
- **Respect the `__init__.py` boundary.** Import a package's public symbols from
  the package, never from its private (`_*`) submodules. When you add a public
  symbol, add it to `__all__` and re-export it.
- **Label new dirs.** A new directory is not done until it has a `README.md` and
  `CLAUDE.md` with valid frontmatter (see CONVENTIONS).
- **Keep the transport thin.** Domain logic lives in `src/`; `scripts/` are
  entrypoints that call into it. Never duplicate conversion logic in a script.
- **Mirror unit tests.** A new `src/<pkg>/<mod>.py` gets a matching
  `tests/unit/<pkg>/test_<mod>.py`. Integration/e2e tests go by scenario.

## doc2md specifics
- **`src/backend/ingest` and `src/backend/validate` stay Python 3.6-compatible
  and stdlib-only** — the deterministic Office lane runs on a bare 3.6 host: no
  `from __future__ import annotations`, no PEP 604 `X | None`, no
  `subprocess.run(capture_output=)`, no `tomllib`. Use type comments.
- **The PDF lane (`scripts/docling_convert.py`) may use Python 3.9+ / PyTorch** —
  keep those heavy deps out of `src/backend/ingest`, which must import on 3.6.
- **Paths derive from the repo root** (`__dirname` / `__file__`) or from
  configuration/env — never hardcode absolute host paths.
- **Losslessness is measured, not assumed.** Every Office conversion is graded by
  an independent, converter-blind ground truth at token recall exactly 1.0
  (`backend.validate.conversion_report`). Don't weaken that gate.

## Never
- Reach into another package's internals to "save an import".
- Put conversion/business logic in `scripts/` (they orchestrate; `src/` converts).
- Commit source documents or anything derived from them, or vendored binaries
  (see `.gitignore`).
