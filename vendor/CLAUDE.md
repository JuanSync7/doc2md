---
title: vendor — agent rules
kind: rules
layer: n/a
status: stable
owner: TBD
summary: Local rules Claude must follow inside vendor/.
---
# Agent rules — `vendor/`

These rules are **local and authoritative** for this directory. They inherit from
the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more specific
(this) file wins.

## Rules

- **Everything here is generated, never hand-authored.** Third-party runtimes are
  materialized by a `scripts/` bootstrapper (e.g. `setup_libreoffice.py`). Do not
  edit extracted files, and do not hand-write launchers — change the generator and
  re-run it.
- **No code imports from `vendor/`.** Lanes locate vendored tools through a
  discovery helper (`office_convert.find_soffice`), never a hardcoded path into
  this tree. Discovery derives the location from the repo root (`__file__`).
- **Don't commit the heavy regenerable parts** (`libreoffice/root`,
  `libreoffice/lohome`) — they rebuild from the carried `rpms/`. Treat this tree
  like `data/`: a build artifact, not source.
- **Keep it relocatable.** Anything written here (wrappers, configs) must derive
  its paths at runtime from its own location — never bake in an absolute
  `/scratch/...` path.
