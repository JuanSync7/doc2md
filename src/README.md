---
title: Source
kind: readme
layer: cross-cutting
status: stable
owner: TBD
summary: The two jobs and the one-way dependency between them — a document that loses nothing, and an index whose every link is walkable.
---

# `src/`

Everything importable. Scripts in [`../scripts/`](../scripts/) are entrypoints that
call into here; conversion logic never lives in a script.

## The two jobs

`docs/end-goal.md` names two jobs with **two different standards**, and they live in
different packages so they can be worked on in parallel:

| | Job 1 — the document | Job 2 — the knowledge |
|---|---|---|
| Standard | nothing may be **lost** | nothing may be **claimed that cannot be followed** |
| Gate | `token_recall == 1.0` (office, hard) | every record cited, every edge joins two declared nodes |
| Package | `backend/ingest`, `backend/validate` | `backend/kb` |

## Packages

| Package | Role |
|---|---|
| [`backend/ingest`](backend/ingest/) | the run path: routing, converters, measurement primitives |
| [`backend/validate`](backend/validate/) | the judge: markdown checks, the losslessness and fidelity gates, the rubric |
| [`backend/kb`](backend/kb/) | the knowledge layer: schema, controlled vocabulary, enrichment, corpus lint |
| [`backend/sections`](backend/sections/) | outline and chunking |
| [`backend/bundle`](backend/bundle/) | the pure assembler that turns measurements into the published artifacts |
| [`backend/provenance`](backend/provenance/) | run records, decisions, and the replay contract |

## The dependency direction

`kb` reads `ingest`'s measurement primitives; **nothing in `ingest` or `validate`
imports `kb`**. That direction is enforced by `tests/unit/backend/test_job_boundary.py`,
because a gate that imported the knowledge layer would make a losslessness verdict a
function of a per-deployment vocabulary — and a document that converted cleanly
yesterday could then fail today for a reason that has nothing to do with the document.

## Rules

Python 3.6-compatible and stdlib-only in `ingest` and `validate` (the Office lane
runs on a bare 3.6 host). Public symbols come from each package's `__init__`, never
from a `_*` submodule.
