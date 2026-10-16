---
title: tests/ciring — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for the CI ring simulation.
---

# Agent rules — `tests/ciring/`

Inherits from `tests/CLAUDE.md`; the more specific wins.

## Rules

- This directory is **not collected by pytest**. It holds one `sitecustomize.py`
  that the interpreter imports at startup when this directory is on `PYTHONPATH`.
- Keep `BLOCKED` in step with what CI actually installs. If a CI workflow starts
  installing PyYAML or Pillow, this file is wrong and must change in the same PR.
- Never import project code here. It runs before anything else and a failure at
  import time is very hard to diagnose.
