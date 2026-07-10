---
title: backend.ingest
kind: package
layer: backend
status: stable
owner: TBD
public_api: src/backend/ingest/__init__.py
tags: [ingest, docling, markdown]
summary: Source-text ingestion — stable doc ids, docling/native backend selection, and markdown stripping.
---

# backend.ingest

Turns raw corpus documents into the text the pipeline indexes. This package owns
the *policy* (which backend, how to strip markdown, how to id a doc); the
entrypoint scripts (`scripts/build_index.py`, `scripts/docling_convert.py`) own
the *I/O* and inject it.

See [`docs/design/docling-ingestion.md`](../../../docs/design/docling-ingestion.md)
for the architecture and rationale.

## Public API

- `doc_id(rel_path)` — stable `sha1(rel_path)[:16]` id; the filename for a doc's
  text/markdown. Shared by both scripts so they agree without a side channel.
- `select_source(rel_path, native_extract, markdown_dir, backend, ...)` — returns
  a `SourceText(text, origin)`: docling markdown when available, else native
  extraction (per-doc fallback).
- `markdown_to_text(md)` — strip GFM markdown to clean prose for the grep
  entity-linker (the `data/text_lc/` shadow). Never fuses table cells.
- `load_ingest_config(...)` / `IngestConfig` — resolve `[ingest].backend` and
  `markdown_dir` (+ Tier-1 assets/VLM settings) from env > config file > defaults.

### Figures (Tier 1)

- `gate_figures` / `caption_is_useful` / `image_markdown` / `inline_image_captions`
  — decide which figures to keep, judge a VLM caption, and inline captioned images
  into the markdown placeholders.
- `figure_outcome` / `figure_coverage` / `FigureCoverage` (+ the `FIG_*` outcome
  constants) — the figure-loss instrument: classify each BODY figure's fate
  (captured / intentionally gated / LOST to bad-crop or alignment-bail) and
  aggregate into a per-doc report with a `lossless` flag. Runs inside
  `_caption_and_inline` and is embedded under the `figures` key of each coverage
  record so figure loss is reported beside text loss.

### Coverage + provenance (Tier 2)

- `coverage(source_text, target_text)` / `tokenize` / `CoverageReport` — the
  lossless-ness instrument: multiset token recall of source content into the
  produced markdown, with the highest-loss missing tokens named. Runs per-doc
  during conversion (`docling_convert.py` -> `data/markdown/_coverage*.jsonl`,
  summarized by `scripts/coverage_report.py`).
- `core_properties` / `pdf_info_meta` / `front_matter` (`_provenance.py`) — the
  provenance block BOTH markdown lanes prepend: `core_properties` reads a docx/
  pptx/xlsx `docProps/core.xml`, `pdf_info_meta` reads a `pdfinfo`-style dict
  (junk auto-titles filtered), and `front_matter` renders either as a `---`-fenced
  YAML block, so every document — office or PDF — carries title/author/dates.

### Routing (single owner per format)

- `route_format(ext)` — the extension → converter-lane map (`ROUTE_OOXML`,
  `ROUTE_DOCLING`, `ROUTE_LIBREOFFICE`, `ROUTE_FENCE`, `ROUTE_PASSTHROUGH`,
  `ROUTE_UNSUPPORTED`); one format, one owner, so nothing is double-converted.
- `classify_source(name, accept)` / `summarize_routes(names, accept)` — layer an
  operator accept-list on the map: the single call every producer consults to
  decide a file's fate and to build the "these files were NOT converted"
  (unsupported / accept-declined) warning. `supported_formats` / `normalize_accept`
  / `ext_of` / `SUPPORTED_EXTS` support it.
- `ooxml_markdown` / `ooxml_source_text` (+ the per-format `docx_*`/`pptx_*`/
  `xlsx_*`) — the deterministic OOXML→markdown converters and their independent
  exhaustive ground truth (`scripts/office_convert.py` is the zip I/O + gate).

> The **validators** — `validate_markdown` and the lossless `conversion_report`
> gate — live in the sibling package [`backend.validate`](../validate/README.md),
> deliberately separated from this run path.
