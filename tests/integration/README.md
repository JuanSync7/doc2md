---
title: Integration tests
kind: readme
layer: backend
status: stable
owner: TBD
summary: Two or more parts agreeing — a script against its library, a document against the code that implements it.
---

# `tests/integration/`

Named by **scenario**, never mirrored to a source file. These may touch disk,
subprocesses and the real corpus.

## What lives here

| Scenario | Files |
|---|---|
| a writer end to end | `test_build_bundle.py`, `test_build_pdf_bundle.py` |
| the gates can actually FAIL | `test_office_gate_red_direction.py` |
| the eval harness gates | `test_run_eval_gate.py` |
| the docs match the code | `test_docs_parity.py` |
| the knowledge layer end to end | `test_enrich_metadata.py`, `test_caption_bundles.py` |

## The parity tests

`test_docs_parity.py` is the reason a reference entry is not done until its table is
updated: it reads `docs/reference/*.md` and fails when a published key, switch,
warning code or decision code is undocumented — or documented and gone. It has caught
a documented `knowledge.json` sample that the writer would have rejected outright.

## Rules

A test that needs a tool (`soffice`, `pdftotext`, docling) **skips with a reason**
rather than failing — and CI has a job that installs those tools and asserts the
skip count, because a skip reads exactly like a pass.
