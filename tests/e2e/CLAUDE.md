---
title: tests/e2e — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for end-to-end tests.
---

# Agent rules — `tests/e2e/`

Inherits from `tests/CLAUDE.md`; the more specific wins.

## Rules

- **Run what a user runs.** Invoke the entrypoint, not the library behind it. A test
  here that imports past the CLI has stopped being end-to-end.
- **Assert on published artifacts**, not on internal state — this tier's whole value
  is judging the output a consumer receives.
- **Keep it in CI.** A slow tier drifts out of the workflow easily, and these tests
  were green by absence once already. If you add a file here, check it is inside the
  `tests-with-tools` invocation.
- Timing-sensitive tests are allowed here and must say so in their docstring, with
  the tolerance and why that tolerance is the right one.
