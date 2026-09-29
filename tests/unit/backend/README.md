---
title: Unit tests — backend
kind: readme
layer: backend
status: stable
owner: TBD
summary: Mirrors src/backend, one file per module, plus the boundary tests that police the rules themselves.
---

# `tests/unit/backend/`

One file per `src/backend` module, named `test_<pkg>_<mod>.py` for the private
modules of a package (`test_ingest_coverage.py` ↔ `src/backend/ingest/_coverage.py`).

## The boundary tests

A few files here are not module mirrors — they assert the repo's own rules and are
the documented reason private imports appear in this directory at all:

| File | Polices |
|---|---|
| `test_job_boundary.py` | Job 1 never imports Job 2, and the permitted direction still happens |
| `test_ingest_struct_common.py` | every module a shared helper reaches, including absolute imports |
| `test_field_inventory.py` | every published artifact key is documented |
| `test_anchor_parity.py` | the three independent anchor implementations agree |
| `test_warning_vocabulary.py` | every warning code emitted is one the contract names |

These read private modules with `ast` deliberately. That is the exception
`tests/unit/CLAUDE.md` records, and `scripts/check_structure.py` reports it as a
warning rather than an error so it stays countable.
