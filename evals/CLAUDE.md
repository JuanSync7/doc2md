---
title: evals — agent rules
kind: rules
layer: n/a
status: stable
owner: TBD
summary: Local rules Claude must follow inside evals/.
---

# Agent rules — `evals/`

These rules are **local and authoritative** for this directory. They inherit
from the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more
specific (this) file wins.

## Rules

- **Stay 3.6 + stdlib.** The generator and runner execute on the bare host
  python3 — no external deps, no `from __future__ import annotations`, no PEP
  604 unions, type comments only.
- **Determinism is a gate, not a goal.** Hand-built corpus sources must be
  byte-identical across runs (fixed OOXML dates, fixed zip `date_time`, no
  unseeded randomness); `run_eval.py` regenerates and byte-compares every run.
- **Neutral content only.** Corpus text, filenames, comments and identifiers
  are for the FICTIONAL Nimbus Semiconductor / Kestrel project. Never reference
  real customers, real internal project names, or absolute host paths. Tool
  locations come from env vars (`DOC2MD_LIBREOFFICE`, `DOC2MD_PDF_PYTHON`) or
  PATH — never hardcoded.
- **Encode the truth.** When the pipeline's measured behavior falls short,
  expectations record the CURRENT truthful behavior with a `_note`/TODO —
  never a papered-over pass, and never a knowingly-wrong "desired" value.
- **Never commit generated artifacts.** Corpus and bundles live under `data/`
  (git-ignored). Only the generator, runner, and expectations are committed.
- **A skipped tool is a SKIP row.** Missing LibreOffice/poppler/PDF-python must
  surface as skipped expectations with the reason, never as silent passes.
