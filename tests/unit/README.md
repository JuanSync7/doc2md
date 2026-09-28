---
title: Unit tests
kind: readme
layer: backend
status: stable
owner: TBD
summary: One module, through its package's public API, with no disk, network or process.
---

# `tests/unit/`

Mirrors the source tree: `src/<pkg>/<mod>.py` gets `tests/unit/<pkg>/test_<mod>.py`.
A new source module is not done until its mirror exists.

## What a unit test may do

Nothing but import and call. **No disk, no network, no subprocess** — if you need
those, it is an integration test and belongs one directory up.

## Testing through the public API

Import from the package (`from backend.ingest import tokenize`), not from a `_*`
submodule. Tests are callers too, and a test that reaches into a private module
pins an implementation detail the package never promised.

The **documented exception** is the boundary tests — `test_job_boundary.py`,
`test_ingest_struct_common.py`, the `__all__` drift checks — which read private
modules with `ast` precisely in order to police the rule. `scripts/check_structure.py`
reports those as advisory warnings rather than errors, so the exception stays
visible and countable instead of quietly becoming the norm.
