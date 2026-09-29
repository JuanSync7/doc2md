---
title: docs/design — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for design documents.
---

# Agent rules — `docs/design/`

Inherits from the root, `CONVENTIONS.md` and `docs/CLAUDE.md`; the more specific
(this) file wins.

## Rules

- **Why, not what.** The binding key-by-key statement lives in
  `docs/reference/output-schema.md`. If you find yourself listing fields here,
  the content belongs there and this file should link to it.
- **Every claim carries its measurement.** "docling loses ff ligatures" is a design
  note only with the numbers and the fixture that show it. Without them it is a
  guess, and guesses belong in `docs/roadmap.md`.
- **Record the rejected alternative.** A design that only states what was chosen
  invites the same debate again in six months.
- Superseded reasoning is dated and kept, not deleted — the history of why a gate
  exists is what stops it being weakened by someone who never saw the failure.
