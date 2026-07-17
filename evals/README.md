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

## Baselines (local PDF ring)

Dated full-run results on the development host; a fresh run diffs itself
against the latest entry. CI's nightly `eval-pdf` job is the same harness on
`ubuntu-latest`.

### 2026-07-17 — first full local run (all three lanes)

One-time setup, from the repo root:

```sh
uv venv --python 3.12 .venv
uv pip install -p .venv/bin/python -e '.[docling]'
```

Run:

```sh
TORCHDYNAMO_DISABLE=1 \
DOC2MD_LIBREOFFICE="$(command -v soffice)" \
DOC2MD_PDF_PYTHON="$PWD/.venv/bin/python" \
python3 evals/run_eval.py
```

**Result: 19 pass, 2 fail, 0 skip** (`run_eval.py` exits nonzero while the
two truthfully-reported shortfalls below remain un-encoded). The FAIL detail
strings are identical, character for character, to the first nightly
`eval-pdf` CI run (2026-07-16): the local ring reproduces the nightly
exactly.

Toolchain (measured; currently **unpinned** — the `docling` extra resolves
latest at install time; pinning + model prefetch is the next M0 slice):

| Component | Version |
|---|---|
| CPython (uv-managed, `.venv/`) | 3.12.13 |
| docling / docling-core | 2.113.0 / 2.87.1 |
| docling-ibm-models / docling-parse | 3.13.3 / 7.8.0 |
| torch / transformers | 2.13.0 / 5.14.1 |
| rapidocr (torch engine, PP-OCRv4 `ch` set) / pypdfium2 | 3.9.1 / 5.12.1 |
| LibreOffice / poppler (pdftotext, pdftoppm) | 6.4.7.2 / 20.11.0 |

Per-document, bundle lanes (text lane: all 6 `text/*` docs valid at recall
1.0; `text/synth-flow.tcl` correctly routed to no lane):

| Document | Eval | Status | Method | token_recall | content_recall |
|---|---|---|---|---|---|
| `legacy/kestrel-clock-spec.doc` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9990 |
| `legacy/kestrel-clock-spec.odt` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9990 |
| `legacy/kestrel-clock-spec.rtf` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9990 |
| `legacy/kestrel-overview.ppt` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9834 |
| `legacy/kestrel-registers.xls` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9811 |
| `office/kestrel-clock-spec.docx` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9990 |
| `office/kestrel-dataflow.pptx` | PASS | ok | ooxml-ground-truth | 1.0000 | 1.0000 |
| `office/kestrel-overview.pptx` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9834 |
| `office/kestrel-readme.docx` | PASS | ok | ooxml-ground-truth | 1.0000 | 1.0000 |
| `office/kestrel-registers.xlsx` | PASS | ok | ooxml-ground-truth | 1.0000 | 0.9820 |
| `pdf/kestrel-clock-spec-scan.pdf` | PASS | degraded | pdf-ocr-transcription | — | — |
| `pdf/kestrel-clock-spec.pdf` | FAIL | ok | pdf-text-coverage | 0.9925 | 0.9855 |
| `pdf/kestrel-dataflow.pdf` | FAIL | ok | pdf-ocr-transcription | — | — |

(`—` = unmeasured: the OCR path has no independent text layer to grade
against; the report says so via `losslessness.note`.)

The two FAILs are truthful current behavior diverging from expectations that
were encoded against an older toolchain — the expectation re-encode/xfail is
a later M0 slice; the real fixes live in M1/M2:

- `pdf/kestrel-clock-spec.pdf` — `coverage.toc_lines: got 1, want >= 9`.
  docling 2.113 emits this TOC without dot leaders, so `is_toc_line`
  recognizes only one line. Everything else passes (recall 0.9925, images
  3/3, `has_toc` true). Real fix: TOC-shape robustness (roadmap M2).
- `pdf/kestrel-dataflow.pdf` — `status: ok, want degraded`. The diagram-only
  PDF still routes to OCR (`ocr_transcription` warning, losslessness
  unmeasured) but no picture placeholder is detected, so the images gate
  passes and nothing degrades the status. The encoded expectation
  (placeholder bails → degraded) now matches neither CI nor local. Real fix:
  area-weighted routing + OCR-path figure extraction (roadmap M1).

Environment notes — what a fresh stand-up hits:

- `TORCHDYNAMO_DISABLE=1` is **required** where the host compiler cannot
  build TorchInductor kernels (gcc 8.5 on this RHEL 8.10 host): docling
  2.113's picture classifier `torch.compile`s its model by default, and
  without the variable the whole `StandardPdfPipeline` fails with
  `InductorError: CppCompileError`. Dynamo off means eager execution; on
  this corpus the results match CI (which compiles) exactly.
- The first run downloads models at runtime: docling's layout/TableFormer/
  classifier weights from Hugging Face, and RapidOCR's PP-OCRv4 det/cls/rec
  models + rec dictionary from `modelscope.cn` (sha256-verified into the
  venv's `rapidocr/models/`). The modelscope fetch proved flaky from this
  network — a failed attempt surfaces as `DownloadFileException` and the
  OCR-dependent docs FAIL; rerunning retries it. Prefetching/pinning model
  artifacts lands in the next M0 slice.

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
