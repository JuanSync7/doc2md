---
title: scripts — agent rules
kind: rules
layer: n/a
status: template
owner: TBD
summary: Local rules Claude must follow inside scripts/.
---

# Agent rules — `scripts/`

These rules are **local and authoritative** for this directory. They inherit from the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more specific (this) file wins.

## Rules

- Scripts are entrypoints, not libraries — if `src/` needs it, it moves to `src/`.
- Each script is self-describing (`--help`) and safe to run twice.
