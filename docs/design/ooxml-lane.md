---
title: The OOXML lane — deterministic office → markdown
kind: design
layer: backend
status: stable
owner: TBD
summary: Office (docx/pptx/xlsx + ODF/legacy via soffice) convert deterministically from OOXML to markdown, gated at token recall 1.0.
---

# The OOXML lane — deterministic office → markdown

**Status: shipped 2026-07-03. 544/544 corpus office docs valid at token recall
exactly 1.0.**

**Both office suites, one path.** Microsoft OOXML (docx/pptx/xlsx) converts
directly; LibreOffice/ODF and legacy binary (odt/odp/ods, doc/ppt/xls, rtf) are
pre-converted to their OOXML sibling by `soffice --convert-to` and then travel the
same single path (`scripts/office_convert.py`). There is no separate ODF converter
and no office-through-docling fallback — `route_format` is the single owner map.

**LibreOffice is self-contained.** So the legacy lane never depends on a host
install, `scripts/setup_libreoffice.py` packages a relocatable LibreOffice into
`vendor/libreoffice/` (see [`vendor/README.md`](../../vendor/README.md));
`find_soffice` discovers it from the repo root automatically — precedence is an
explicit `DOC2MD_LIBREOFFICE` override, then the vendored copy, then a system
`soffice` on PATH. Without any LibreOffice, legacy/ODF inputs fail with a clear
reason (never a silent skip).

**Accepted formats + warnings.** `--accept` (or `[ingest] accept_formats` /
`$DOC2MD_ACCEPT_FORMATS`) restricts which formats the system ingests; every file the
run will NOT convert (unsupported extension, or excluded by the accept-list) is
reported, never silently dropped.

**Validator lives apart.** The structural check (`validate_markdown`) and the
lossless gate (`conversion_report`) are in the sibling package `backend.validate`,
separate from this run path (`backend.ingest`).

**Text in images is a separate, additive lane.** This lane makes the TEXT lossless;
text baked as pixels inside embedded raster/metafile images (screenshots, diagrams,
formula images) is recovered by the opt-in figure-caption pass
([`image-captioning.md`](image-captioning.md)) — one shared, formula-safe VLM tool
called by the office, docling, and standalone-image lanes, with its own independent
second-pass validator. Embedded **SVG** text is captured deterministically here (no
model), gated at recall 1.0 like the rest.

## Why

Office files (docx/pptx/xlsx) are ZIP archives of XML in which every paragraph,
heading style, table row/cell, and spreadsheet value is explicitly tagged.
Converting them through a layout-inference engine (docling) re-derives structure
the file already states — and measurably drops content while doing it (docling's
docx backend truly lost ~23k tokens across 156 files; its xlsx reader covered
only 78%). A deterministic walk of the parts is lossless *by construction* and
converts the whole 544-doc office corpus in ~4 minutes on the plain 3.6 host
python, no models, no venv.

PDF stays with docling: a PDF is positioned glyphs, structure must be inferred,
and 100% is not physically available there. One format, one owner:

| lane | formats | converter |
|---|---|---|
| ooxml | docx, pptx, xlsx | `scripts/office_convert.py` (deterministic) |
| libreoffice | odt/odp/ods, doc/ppt/xls, rtf | `scripts/office_convert.py` — `soffice --convert-to` the OOXML sibling, then the ooxml lane |
| docling | pdf, html, htm | `scripts/docling_convert.py` (inference + measured gates) |
| passthrough | md, markdown, txt, text | `scripts/text_convert.py` — verbatim copy |
| fence | json, yaml, yml, toml, xml, csv, tsv, ini | `scripts/text_convert.py` — raw content in a self-sizing code fence |

`backend.ingest.route_format` is the single source of truth; every producer
consults it (via `classify_source` / `summarize_routes`), so a format is never
double-converted or silently unowned — each producer converts only its own lanes
and *reports* every file it will not convert (other lane, accept-declined, or
unsupported). An operator accept-list (`--accept` / `[ingest] accept_formats` /
`$DOC2MD_ACCEPT_FORMATS`) further restricts which formats are ingested. Each
producer's `--only` escalation lane is exempt from routing (explicit per-doc
requests are never vetoed); docling additionally keeps a verbatim `md` reader for
that exempt path only, never for normal ingestion.

## How it stays honest (the validator)

Every conversion is gated by `backend.ingest.conversion_report`:

1. **Losslessness** — multiset token recall of the *exhaustive ground truth*
   (`docx_source_text` / `pptx_source_text` / `xlsx_source_text`: every text
   run in the zip, walked structure-blind) into the markdown must be exactly
   **1.0**. The converter walks structure, the ground truth walks everything —
   a traversal bug in the converter cannot grade its own homework.
2. **Structure** — `validate_markdown`: consistent pipe-table columns, closed
   fences/front matter, no leaked OOXML tags, no control/replacement chars.

Records append to `data/markdown*/_coverage_ooxml.jsonl` in the same shape as
the docling lane's records, so skip/heal logic is shared. Sweep any markdown
tree with `scripts/validate_markdown.py` (structure for all files, losslessness
for office files when `--src` is given).
`scripts/office_convert.py --audit-parts` empirically lists any text-bearing
zip part the converter does not read (currently: none).

## Content policy (applies to converter AND ground truth alike)

- `mc:Fallback` subtrees skipped — they duplicate `mc:Choice`.
- Page furniture excluded: docx header/footer parts, pptx slide-number/date/
  footer placeholders and layout/master templates, xlsx print headers.
- `w:delText` (tracked deletions) and `w:instrText` (field code source)
  excluded; the field's *result* text is kept.
- SmartArt `diagrams/drawingN.xml` excluded — verified character-identical
  duplicate of `diagrams/dataN.xml`, which is converted.
- `xl/externalLinks/` excluded — cached cells of *other* workbooks; the
  referencing cells already carry their computed `<v>` locally.
- Included beyond the obvious body: footnotes/endnotes, Word/PowerPoint review
  comments, speaker notes, text boxes, SmartArt labels, chart titles + cached
  series/category values, xlsx cell comments, chartsheet names.
- Formulas: the cached **result** is converted, not the formula source.
- All literal text is markdown-escaped (`\_`, `\<`, `\[`, leading `15\.` …) so
  a GFM renderer shows the source characters exactly; silicon docs are full of
  `__paths__` and `<signal[31:0]>` that would otherwise be eaten as syntax.

## Layout produced

Front matter (title/author/version/dates from docProps) → body in document
order (headings from styles/outline levels, real GFM tables with `|---|`
separators and gridSpan-padded geometry, bullet/numbered lists from
numbering.xml, `[text](url)` hyperlinks) → `## Footnotes` / `## Endnotes` /
`## Comments` → pptx: `## Slide N — title` sections with `### Diagram`,
`### Chart`, `### Speaker notes`; xlsx: `## <sheet>` sections (first data row
as table header) plus `## Text boxes` / `## Comments` / `## Charts`.

## Flip / rollback

The lane writes to any `--out`. Production flip is one command (write into
`data/markdown` — ids are identical) after which `build_index.py` consumes the
new files; docling_convert already declines office by default. Note the
section-cards build in flight is keyed to the docling office markdown — recard
those 543 docs after flipping.
