---
title: Evals
kind: eval
layer: backend
status: stable
owner: TBD
public_api: none
tags: [eval, corpus, regression]
summary: Deterministic synthetic corpus generator + eval harness — the regression suite for every conversion lane.
---

# Evals

The real evaluation corpus is confidential and lives outside the repo, so this
directory builds an **artificial corpus** — plausible engineering documents for a
fictional company (*Nimbus Semiconductor*, project *Kestrel*) — and asserts the
pipeline's measured behavior over it. It is the committable regression/eval
suite for doc2md.

## Files

| File | What it is |
|------|------------|
| `gen_corpus.py` | Deterministic corpus generator. Hand-builds OOXML (docx/xlsx/pptx) by writing the XML parts directly (`zipfile` + string templates), writes the text-lane files, and derives legacy formats (doc/rtf/odt/xls/ppt) + digital PDFs via LibreOffice and a scanned PDF via poppler + Pillow. |
| `run_eval.py` | The harness: generates (or reuses) the corpus, runs the office/text/PDF lanes, then checks `expectations.json` and prints a pass/fail table (nonzero exit on failure). |
| `expectations.json` | Per-document expected lane, status, gates, and targeted content probes, keyed by corpus relpath. `_note` keys document truthfully-encoded pipeline gaps (TODOs). |

Generated artifacts go to `data/eval_corpus/` (sources, plus a sibling
`eval_corpus.manifest.json`) and `data/eval_bundles/` / `data/eval_bundles_text/`
(outputs) — all git-ignored; only the generator + expectations are committed.

## Running

```sh
# office + text lanes only (host python3; needs LibreOffice for legacy formats)
DOC2MD_LIBREOFFICE=/path/to/soffice python3 evals/run_eval.py

# full run including the PDF lane (a python 3.9+ interpreter with docling)
DOC2MD_LIBREOFFICE=/path/to/soffice \
DOC2MD_PDF_PYTHON=/path/to/venv/bin/python \
python3 evals/run_eval.py

python3 evals/run_eval.py --no-lanes     # re-check existing outputs only
python3 evals/run_eval.py --regen        # force corpus regeneration
```

Tool locations come **only** from env vars / PATH — nothing is hardcoded.
Missing tools degrade to SKIP rows (the derived corpus files simply are not
generated), never to silent passes. A pytest wrapper lives at
`tests/e2e/test_eval_corpus.py` and skips cleanly on bare hosts.

## Feature matrix (what each document exercises)

| Corpus file | Features exercised | Expected gate |
|-------------|-------------------|---------------|
| `office/kestrel-clock-spec.docx` | 4-level headings; dot-leader TOC with content on the immediately-next block (the once-regressed first-heading skip); nested bullet + decimal lists; gridSpan + vMerge merged table cells; long `Snake_Case`/CamelCase identifiers; split text runs; 3 embedded PNGs; an external `w:hyperlink` (structure.json `links[]` + `content.links` + `savings` probes); header/footer + PAGE furniture; markdown-special characters | ok, recall 1.0, coverage pass, images pass, `max_depth` 4, savings ≥ 4x, link node verbatim |
| `office/kestrel-readme.docx` | trivial happy path | ok, recall 1.0 |
| `office/kestrel-registers.xlsx` | 3 sheets; shared + inline strings; typed columns (hex strings, ints, floats, bools, ISO dates); merged cells; formulas with cached values; nearly-empty sheet | ok, recall 1.0 |
| `office/kestrel-overview.pptx` | title/bullets/table/shapes/notes slides; nested bullet levels; speaker notes | ok, recall 1.0 |
| `office/kestrel-dataflow.pptx` | text-in-shape only (diagram-like) | ok, recall 1.0 |
| `legacy/*.doc/.rtf/.odt/.xls/.ppt` | LibreOffice pre-convert lane (`libreoffice_preconvert`); `.doc` additionally probes hyperlink survival through soffice (verbatim link node) | ok, recall 1.0 vs the converted sibling |
| `pdf/kestrel-clock-spec.pdf` | digital PDF: TOC, tables with identifiers (raw-vocab repair), figures, furniture exclusion | ok, best-effort, recall ≥ 0.97, images pass |
| `pdf/kestrel-dataflow.pdf` | diagram-only PDF (known edge: misrouted to OCR by the text-layer threshold) | degraded — truthful current behavior, see `_note` |
| `pdf/kestrel-clock-spec-scan.pdf` | scanned/rasterized PDF → OCR lane | degraded + `ocr_transcription` |
| `text/*.md/.txt` | passthrough lane (verbatim); the `.md` carries the TOC-adjacency regression probe | valid, recall 1.0 |
| `text/*.csv/.tsv/.json/.yaml` | fence lane (verbatim fenced) | valid, recall 1.0 |
| `text/synth-flow.tcl` | unsupported format: routed to no lane, reported | never converted |
