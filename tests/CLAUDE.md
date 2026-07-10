---
title: tests — agent rules
kind: rules
layer: n/a
status: template
owner: TBD
summary: Local rules Claude must follow inside tests/.
---

# Agent rules — `tests/`

These rules are **local and authoritative** for this directory. They inherit from the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more specific (this) file wins.

## Rules

- A new `src/<pkg>/<mod>.py` REQUIRES a mirrored `tests/unit/<pkg>/test_<mod>.py`.
- Do NOT mirror integration/e2e/smoke to source files — name them by the scenario under test.
- Unit tests touch no network/disk/process. If you need those, it's an integration test.
- Test through the package's public API (`__init__`), not its `_*` internals — tests are callers too.
