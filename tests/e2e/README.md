---
title: End-to-end tests
kind: readme
layer: backend
status: stable
owner: TBD
summary: The whole pipeline over the real corpus — the slowest tier, and the only one that runs what a user runs.
---

# `tests/e2e/`

The full pipeline, exercised the way a user exercises it: generate the corpus, run
the lanes, grade the output.

| File | Runs |
|---|---|
| `test_eval_corpus.py` | the eval harness over the generated corpus |
| `test_heal_supervisor_e2e.py` | the retry/escalation supervisor |

## Why this tier exists separately

Unit and integration tests can both be green while the thing a user actually types
is broken — a missing switch, a wrong default, a stage that no longer calls the next
one. This tier is the only one that would notice.

These ran in **no CI job at all** until the `tests-with-tools` job was added; eight
tests were green by absence. That is the failure mode this directory is most prone
to, which is why the job that runs them asserts its tools arrived.

## Rules

Slow is acceptable here and nowhere else. If a test does not need the whole pipeline,
it belongs one tier up.
