---
title: tests/integration — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for integration tests.
---

# Agent rules — `tests/integration/`

Inherits from `tests/CLAUDE.md`; the more specific wins.

## Rules

- **Name by scenario, never by source file.** Mirrors belong in `tests/unit/`.
- **A missing tool is a SKIP with a reason**, never a silent pass and never a hard
  failure. The `tests-with-tools` CI job runs the tools and asserts they were found.
- **Use `tmp_path`.** Nothing here may write into the repo or into `data/`.
- **Load a script by path** (`importlib.util.spec_from_file_location`) rather than
  importing it — `scripts/` is not a package, and the loader is what makes the
  transport layer testable at all.
- When a test asserts on stdout, assert on the thing a human reads (a census line, a
  verdict word), not on incidental formatting.
