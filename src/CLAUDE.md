---
title: src — agent rules
kind: rules
layer: n/a
status: template
owner: TBD
summary: Local rules Claude must follow inside src/.
---

# Agent rules — `src/`

These rules are **local and authoritative** for this directory. They inherit from the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more specific (this) file wins.

## Rules

- **Every export crosses an `__init__.py`.** Add public symbols to `__all__` and re-export them; keep implementation in `_*` modules.
- Respect the dependency direction: `app → {frontend, backend} → shared`. No back-edges, no FE↔BE direct imports.
- `shared/` must stay framework-free and import nothing else in `src/`.
- Each package needs a `shared/` only if it has domain-shared code, and a `util/` only if it has generic helpers — don't create empty ones.
