---
title: Data
kind: doc
layer: n/a
status: stable
owner: TBD
public_api: none
tags: [generated, gitignored]
summary: Generated conversion output — markdown, bundles, coverage records, and the synthetic eval corpus. Never committed.
---

# Data

Everything under this directory is **generated** and git-ignored (only these
two labeling docs are committed). It derives from source documents or from the
eval generator and is rebuilt by the scripts — never hand-edited.

| Path | Producer | Contents |
|------|----------|----------|
| `markdown/` | `scripts/office_convert.py`, `text_convert.py`, `docling_convert.py` | `<doc_id>.md` + `_coverage*.jsonl` records |
| `bundles/` | `scripts/build_bundle.py`, `build_pdf_bundle.py` | per-document output bundles (`document.md`, `structure.json`, `report.json`, `images/`) |
| `eval_corpus/` (+ `eval_corpus.manifest.json`) | `evals/gen_corpus.py` | the deterministic synthetic eval corpus (fictional content) |
| `eval_bundles/`, `eval_bundles_text/` | `evals/run_eval.py` | eval outputs for the office/PDF and text lanes |
