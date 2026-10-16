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
  footer placeholders and layout/master templates, xlsx print headers. The docx
  header/footer parts are read *once, separately*, purely to **measure** the drop
  and name it (`dropped_headers_footers`, with a part count and a char count) —
  they never enter `parts`, so neither the converter nor the ground truth can see
  them and the exclusion stays symmetric. A drop this lane makes by policy is
  never a silent one, and never degrades `status`.
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
- Literal text is markdown-escaped **by position, not by character class**
  (`\<`, `\[`, leading `15\.`, `\_\_` …) so a GFM renderer shows the source
  characters exactly; silicon docs are full of `__paths__` and `<signal[31:0]>`
  that would otherwise be eaten as syntax.

  "By position" means every position a markdown BLOCK can open, and the two that
  are easy to miss are the reason this line reads the way it does. A **list item's
  content column** is a block start exactly as column 0 is: after `- `, CommonMark
  opens a heading, a nested list, a quote, a fence or a thematic break just as it
  would at the left margin — so a slide bullet reading `- - -` came out as
  `- - - -`, a thematic break, and **deleted itself** at token recall 1.0. And an
  ATX heading may end with a **closing sequence** of hashes, which the renderer
  eats: `# Drain procedure #` published as "Drain procedure". Neither loss carries
  a token, so neither gate could see it; both are now escaped where they arise.
  A **pair** of adjacent lines can open a block no single line can — a GFM
  delimiter row under a line holding a pipe — so SVG figure labels, which arrive
  as adjacent bare lines, are checked against their predecessor.

  The one exception is a **single**
  underscore flanked by word characters: CommonMark's flanking rule means it can
  neither open nor close emphasis (and `markdown_to_text`'s `_ITALIC` already
  mirrors that), so escaping it bought no safety and put backslashes into the
  bytes a BM25 index and a human grep search — `DB_MAX_CONN_LIMIT` is stored
  verbatim. A run of two or more stays escaped: `__x__` *is* strong emphasis to
  `_BOLD`, which carries no flanking guard, so unescaping there would move the
  recall gate.
- **List nesting follows the parent's content column, never a fixed indent.**
  CommonMark nests a child item only at the column after its parent's marker and
  the space following it — 2 under `- ` but **3** under `1. `. A fixed two-space
  indent closed the parent item and rendered the child as its sibling, silently
  **renumbering** every subsequent step of an ordered procedure. Indentation is
  not a token, so no recall or coverage gate could object; `structure_fidelity` is
  the gate that can, and it grades the depth a RENDERER really shows.
- **Rendered depth is CONTAINMENT: how many strictly shallower ancestors are still
  open above the item.** An item closes every ancestor at its own level or deeper
  and then sits one step in from what remains, so **two items at the same source
  level are siblings whatever that level is**, and an item deeper than anything
  open costs one step rather than the level it names — a child of a parent that is
  not there cannot be written, and an invented indent of four or more columns past
  the open item stops being a list item at all and is absorbed above as a lazy
  continuation, every token still present. The earlier statement of this rule was a
  CLAMP to the stack's HEIGHT, "one deeper than the deepest thing open", which is
  right for the first item at a skipped level and wrong for every one after it:
  three peer bullets at outline level 2 published as a three-deep chain, and a sweep
  of every level sequence of length 2–4 over levels 0–3 found 199 of 336
  misrepresented. It survived because the ground truth had been written to mirror
  the converter, so the delta CANCELLED and the deck shipped at `gate: pass,
  deltas: [], recall: 1.0`. Both readers now derive containment independently.
  So an outline level a source skips is genuinely lost, and the loss is counted
  rather than invented or ignored — `flattened_list_levels`, on both docx and pptx.
  (`flattened_bullet_formatting` is the deck's *other* list receipt and a different
  loss: the `buAutoNum`/`buChar`/`buNone` cascade flattened to a plain `-`.)

## Layout produced

Front matter (title/author/version/dates from docProps) → body in document
order (headings from styles/outline levels, real GFM tables with `|---|`
separators and gridSpan-padded geometry, bullet/numbered lists from
numbering.xml, `[text](url)` hyperlinks) → `## Footnotes` / `## Endnotes` /
`## Comments` → pptx: `## Slide N — title` sections (**N is the slide's position
in the deck**, read from `p:sldIdLst` in `ppt/presentation.xml` — *not* the number
in its part name, which PowerPoint leaves alone when a slide is dragged) with
`### Diagram`,
`### Chart`, `### Speaker notes`; xlsx: `## <sheet>` sections (first data row
as table header) plus `## Text boxes` / `## Comments` / `## Charts`.

**Emphasis, hyperlinks and ordinals are emitted by all three formats** (P9.8), and
the three readers are genuinely different because the three FORMATS are: Word states
a run's properties as child elements of `w:rPr`; DrawingML states them as ATTRIBUTES
of `a:rPr` and puts the hyperlink INSIDE them rather than around a span of runs; a
workbook states them per CELL, through `@s` into `cellXfs` into `fonts`, with links
in the sheet's own `<hyperlinks>` block keyed by `@ref`. All three feed the same
`_render_runs`, which owns CommonMark's flanking rules and the coalescing that stops
a word split across runs from emitting `**Dma****Arbiter**`.

A deck's automatic numbering resolves the full bullet cascade — the paragraph's own
`a:pPr`, then the shape's `a:txBody/a:lstStyle`, then the slide LAYOUT's matching
placeholder, then the slide MASTER's `p:txStyles/p:bodyStyle` — followed by
RELATIONSHIP rather than by filename, so a deck with two masters resolves each layout
to the master its own rels name. `a:buNone` is a declaration and beats an inherited
number: a paragraph the slide draws plain must not come out numbered. What markdown
still cannot hold is a custom bullet GLYPH and an item with no marker at all, and
both keep their counted receipt (`flattened_bullet_formatting`).

**Two adjacent spans need help that markdown does not give.** `***alpha***` written
straight against `*beta*` is a delimiter run of FOUR asterisks, and CommonMark pairs
it as ONE em span; `**~~beta~~**` after a letter cannot even OPEN, because its outer
neighbour is a word character and its inner one is a `~`. Both were pre-existing
publish-blockers on ordinary Word: the first failed `em` **and** took token recall to
0.0, the second lost its emphasis silently. The converter now writes an empty HTML
comment (`<!---->`) between two spans whose delimiters would fuse or fail to flank —
it renders as nothing, carries no token, and both of this project's readers remove it
to nothing, because it only ever stands where two spans meet with no whitespace, which
is to say between two halves of one word. It is written only where it is needed, so
emphasis with spaces around it — almost all emphasis — is untouched.

**A marker is markup, so it does not split a word.** `_mdstructure._words` reduces
text to `[a-z0-9]+` runs, which made `**Dma**Arbiter` read as two tokens where the
source correctly reads one — `list_item_words` disagreed and the document refused to
publish at `token_recall: 1.0`. It now strips unescaped runs of `*`, `~` and backtick
before tokenising, stepping around code spans (code is verbatim, so an asterisk inside
one is the document's). That is safe because `_esc` escapes all three whenever the
document's own text holds them, and the invariant
`_words(_esc(src)) == _struct_common._words(src)` is checked over an exhaustive short
alphabet and a seeded wide sample rather than argued. `_` is deliberately not in the
set: this converter never emits it as a marker and leaves it unescaped inside an
identifier, so stripping it would JOIN two tokens the source separates.

An INTERNAL link — a slide-to-slide jump, a workbook `location` — keeps its text and
loses its destination in every format, because `[text]()` is a dead link in the
stored bytes and the target is no address a reader outside the document could follow.
That loss is on neither side of the token gate, so it is counted
(`dropped_shape_links` / `dropped_cell_links`) and the destination is quoted.

## Flip / rollback

The lane writes to any `--out`. Production flip is one command (write into
`data/markdown` — ids are identical) after which `build_index.py` consumes the
new files; docling_convert already declines office by default. Note the
section-cards build in flight is keyed to the docling office markdown — recard
those 543 docs after flipping.
