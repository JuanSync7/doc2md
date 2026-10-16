---
title: backend
kind: readme
layer: backend
status: stable
owner: TBD
summary: A namespace, not a facade — the public API lives in the subpackages, each with its own boundary.
---

# `src/backend/`

A **namespace**. `backend/__init__.py` re-exports nothing and declares `__all__ = []`
to say so: importing from here would create a second, competing boundary beside the
subpackages that already have one.

| Subpackage | Public API | Role |
|---|---|---|
| [`ingest/`](ingest/) | `backend.ingest` | the run path — routing, converters, and the measurement primitives everything else grades with |
| [`validate/`](validate/) | `backend.validate` | the judge — markdown structure, the losslessness and fidelity gates, the executable rubric |
| [`kb/`](kb/) | `backend.kb` | the knowledge layer — schema, controlled vocabulary, enrichment, corpus lint |
| [`sections/`](sections/) | `backend.sections` | heading outline and chunking |
| [`bundle/`](bundle/) | `backend.bundle` | the pure assembler: measurements in, published artifacts out |
| [`provenance/`](provenance/) | `backend.provenance` | run records, decisions, and what `replay_run.py` can verify |

## Layering

`validate` sits **above** `ingest` and imports its primitives; `ingest` never imports
back. `kb` also reads `ingest` and never `validate` — the knowledge layer has its own
judge (`kb_lint`) with its own standard, because applying a hard losslessness gate to
an index that is *supposed* to be smaller than the source would be a category error.

Both directions are asserted by `tests/unit/backend/test_job_boundary.py`.
