---
title: tests/unit/backend — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for the backend unit mirrors.
---

# Agent rules — `tests/unit/backend/`

Inherits from `tests/unit/CLAUDE.md`; the more specific wins.

## Rules

- **`src/backend/<pkg>/_<mod>.py` → `test_<pkg>_<mod>.py`.** The prefix is what keeps
  two packages' same-named modules apart.
- **Gate changes need a red-direction proof.** This package is almost entirely gates;
  a gate nobody has watched fail is not a gate. Inject the fault, watch it fire,
  revert — and say in the test's docstring what you injected.
- **Measured numbers, not round ones.** `assert absent == 160` with a comment saying
  why 40 of the 200 landed in `short` is worth more than `assert absent > 0`.
- When a test expectation and the code disagree, find out which is wrong before
  changing either. Several of this suite's assertions were wrong and the code right.
