---
title: Scripts
kind: script
layer: n/a
status: draft
owner: TBD
public_api: none
tags: [scripts, entrypoints, cli, pipeline]
summary: Entrypoints for every pipeline stage — thin orchestration over src/, never conversion logic.
---

# Scripts

Executable entrypoints, not importable library code. Domain logic lives in
`src/`; a script reads arguments, walks the disk, calls into the domain and
writes files. Anything a second caller would need belongs in `src/`.

Every script is self-describing (`--help`) and safe to run twice.

## The pipeline, in order

| Script | Stage |
|---|---|
| `build_bundle.py` | Office + text lanes → one bundle per document |
| `build_pdf_bundle.py` | PDF/HTML lane → the same bundle shape |
| `caption_bundles.py` | VLM figure captions (detachable overlay) |
| `enrich_metadata.py` | Document metadata, tiers 0/1/2 (detachable overlay) |
| `kb_lint.py` | Per-document and corpus-wide vocabulary grading (read-only) |

## Lane internals and one-time tools

| Script | Purpose |
|---|---|
| `office_convert.py` / `text_convert.py` | The individual lanes, flat `.md` output |
| `docling_convert.py` | The docling producer (Python 3.12) |
| `heal_supervisor.py` | Elastic supervisor for long PDF runs |
| `image_enrich.py`, `image_caption.py`, `vlm_client.py` | Figure captioning pieces |
| `validate_markdown.py`, `validate_figures.py` | Second-pass validators |
| `prefetch_docling_models.py` | Pin and materialise the model weights |
| `setup_libreoffice.py` | Vendor a relocatable LibreOffice |
| `render_vocab_doc.py` | Generate `docs/reference/vocabulary.md` |

Every flag and environment variable, with what it changes in the output, is in
[`docs/reference/configuration.md`](../docs/reference/configuration.md).
