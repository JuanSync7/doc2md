---
title: doc2md
kind: doc
layer: n/a
status: stable
owner: TBD
public_api: none
tags: [overview, conversion, markdown, bundles]
summary: Deterministic, lossless document -> Markdown conversion with measured fidelity gates.
---

# doc2md

Deterministic **document → Markdown** conversion for RAG ingestion, with an
independent second-pass validator: every conversion is *measured*, never
assumed. Office documents are converted from their OOXML XML directly and hard-gated
at **token recall = 1.0**; PDF/HTML go through a best-effort lane whose loss is
quantified and surfaced, never hidden.

## What it produces

One **bundle per document** (see
[`docs/design/output-contract.md`](docs/design/output-contract.md) — the
binding contract):

```
<out>/<doc_id>/
  document.md      # the converted body, frontmatter-stamped (hashes, lane, run)
  structure.json   # heading outline + figure nodes with line spans
  report.json      # measured gates: losslessness, outline coverage, image integrity
  images/          # content-addressed (<sha16>.<ext>), byte-verified figures
<out>/manifest.jsonl
```

`report.json` carries an honest `status`: `ok`, `degraded` (converted, with a
named, measured deficiency), or `failed`. Nothing "passes" silently: office
lanes must hit recall 1.0 against a converter-blind ground truth; the PDF lane
reports token/content recall, figure-region text debt, and OCR use with a
`best-effort` gate that never claims a pass.

## Lanes

| lane | formats | how | runtime |
|---|---|---|---|
| ooxml | docx, xlsx, pptx | direct XML → Markdown, deterministic | Python 3.6+, stdlib |
| libreoffice | doc, xls, ppt, odt, rtf, … | vendored soffice → OOXML sibling → ooxml lane | Python 3.6+ |
| passthrough / fence | md, txt, csv, json, … | verbatim / fenced | Python 3.6+, stdlib |
| pdf / html | pdf, html | docling (+ RapidOCR for scanned), raw-vocab repair, text-layer fallback | Python 3.9+ (3.12 venv) |

## Quickstart

```bash
# Office + text lanes (stdlib, runs on bare Python 3.6):
python3 scripts/build_bundle.py --src /path/to/documents --out /path/to/bundles

# PDF lane (needs the docling extra in a modern venv; pinned model weights):
uv venv --python 3.12 && uv pip install -e '.[docling]'
.venv/bin/python scripts/prefetch_docling_models.py
export DOCLING_ARTIFACTS_PATH="$PWD/vendor/docling-artifacts"
.venv/bin/python scripts/build_pdf_bundle.py --src /path/to/documents --out /path/to/bundles --accept pdf

# Legacy-format support (doc/xls/ppt/rtf, emf/wmf rendering), one-time:
python3 scripts/setup_libreoffice.py --rpms /path/to/libreoffice/rpms

# Optional VLM figure captions (separate overlay stage, cache-keyed):
python3 scripts/caption_bundles.py --bundles /path/to/bundles --vlm-url http://127.0.0.1:8000
```

Both writers share the same output root and `manifest.jsonl`; re-runs skip
completed bundles (`--force` rebuilds).

## Layout

- `src/backend/` — the domain: `ingest` (routing, conversion, repair),
  `sections` (outline), `validate` (independent fidelity checks), `bundle`
  (report assembly). `ingest`/`validate` are Python 3.6 + stdlib only.
- `scripts/` — entrypoints (converters, writers, validators); no domain logic.
- `tests/` — `unit/` mirrors `src/`, `integration`/`e2e` by scenario.
- `vendor/` — self-contained LibreOffice (build artifact, not committed).
- `docs/design/` — the output contract and lane designs.

See [`CONVENTIONS.md`](CONVENTIONS.md) for the repo taxonomy and
`CLAUDE.md` files for per-directory agent rules.
