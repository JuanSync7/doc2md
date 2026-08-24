---
title: Agent rules — src/backend/provenance/
kind: rules
layer: backend
status: draft
owner: TBD
public_api: src/backend/provenance/__init__.py
tags: [rules, provenance, reproducibility]
summary: Local rules — pure functions, no absolute paths in output, unknowns omitted rather than defaulted.
---

# Agent rules — `src/backend/provenance/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- **Python 3.6-compatible, stdlib-only.** This runs in the same offline pipeline as
  `ingest` and `validate`. No PEP 604 unions, no f-strings; use type comments.
- **Pure functions.** Strings and dicts in, an `OrderedDict` out. Every disk touch —
  reading `.git`, asking `soffice` its version, hashing a tree — belongs to the
  caller in `scripts/`. The one deliberate exception is `_code.py`, which reads
  `.git/HEAD` and `pyproject.toml`: those are *the checkout describing itself*, they
  are plain file reads, and no caller could supply them without duplicating the
  parsing. It still never shells out; `dirty` is passed in.
- **Never emit an absolute host path.** Root `CLAUDE.md` forbids it and a bundle is
  published output. Redact to a placeholder or hash to an id — a location must stay
  answerable as *"was this the same tree?"* without being disclosed.
- **Never record the machine.** No `platform.node()`, no user name, no home
  directory. Repeating a run does not require knowing whose desk it ran on.
- **An unknown is omitted, never defaulted.** Leave the key out rather than writing
  `false` or `""`. An unverified "clean tree" is the one claim a reader would act
  on, so it must not be inventable.
- **`decisions[]` is choices; `warnings[]` is problems.** Do not put a branch in
  `warnings[]` because it was easier, and do not widen `DECISION_CODES` without
  adding the code to `docs/reference/output-schema.md` — the parity test enforces it.
- **Derive, do not duplicate.** `config_provenance` uses the real loader as its own
  oracle rather than restating the precedence chain. Any new provenance fact should
  reach for the same trick before it reaches for a table.
