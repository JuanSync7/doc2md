---
title: The doc2md output contract — bundle, structure, report
kind: design
layer: backend
status: proposed
owner: TBD
summary: Every document (Office or PDF) yields one identical bundle — markdown+frontmatter, a deterministic structure outline, a validator-only report, and extracted images — collated by doc_id and image_id.
---

# The doc2md output contract

**Status: proposed 2026-07-08.** One document in → one **bundle** out, with the
**same shape for every lane** (Office and PDF). The lanes differ only in *how*
losslessness is measured, and the report states that difference honestly.

## Why a fixed contract

doc2md is the boundary between "raw documents" and every downstream consumer (a
RAG index, object storage, dashboards). A downstream system should never
have to re-parse the markdown to learn the document's hierarchy, never have to
re-run a converter to learn whether a conversion was lossless, and never have to
guess which extracted image belongs where. Those three facts are *outputs*, not
inferences:

- **Structure** (heading hierarchy + token counts + image placement) → `structure.json`
- **Losslessness + QA metrics** (validator-only, no LLM) → `report.json`
- **Content** → `document.md` (+ frontmatter) and `images/`

## The bundle

One directory per document, keyed by a stable `doc_id`:

```
<out>/<doc_id>/
  document.md        # markdown + YAML frontmatter (the source ↔ markdown map)
  structure.json     # heading outline + token counts + image↔markdown map (+ captions if enriched)
  report.json        # validator-only losslessness + QA metrics (NO LLM)
  knowledge.json     # extracted graph payload — only once enrichment has run
  images/            # extracted pixels, content-addressed by image_id (<sha16>.<ext>)
    219f951a5046d997.png
    …
```

**Collation.** The only join keys are `doc_id` (bundle) and `image_id` (image).
`image_id` is **content-addressed** — the first 16 hex of the sha256 of the exact
image bytes — so it appears in exactly three places that re-collate without ambiguity:
the markdown reference `![alt](images/<sha16>.png)`, the `structure.json` image node,
and the file name under `images/`. Content addressing also **deduplicates for free**:
a logo or diagram reused across pages is stored once and every occurrence links to it
(the same sha is the caption-cache key, so it is also captioned once).

**Both lanes emit this identical shape.** An Office bundle and a PDF bundle are
interchangeable to a consumer; only `report.losslessness` reveals the lane.

Corpus-level **run log** (object-store friendly): `manifest.jsonl`, one line per
document **per run** — `{doc_id, source_relpath, lane, status, markdown_sha256,
source_sha256, error, run_id, ts, action}` — beside `runs.jsonl`, one line per run
carrying the full `run{}` block, the counts and a `corpus_sha256`. Still an index,
not a source of truth; every fact in it also lives in the bundle. It logs skips and
deferrals too: a run that writes no row for a document it decided not to rebuild
makes the number of runs unrecoverable from disk, which is what stopped the earlier
manifest being a log of anything.

## `document.md` frontmatter

Self-describing per file — this **is** the document↔markdown mapping (no external
lookup needed). Note the scope: the SOURCE mapping and the descriptors are complete
here; the extracted knowledge is not, and lives in `knowledge.json` beside it, which
carries its own copy of the join keys for the same reason.

```yaml
---
doc_id: 9f3a…                       # stable bundle key
source_format: docx
lane: office                        # office | pdf
source_relpath: <de-identified relative path>   # never an absolute host path
source_sha256: …
markdown_sha256: …                  # determinism / cache key
converter: doc2md-ooxml/0.1.0
lossless: true                      # office; PDF sets false and reports coverage
structure: structure.json
report: report.json
images: images/
generated_run: <run-id>             # stamped by the caller, never Date.now() in-lib

# Source core properties, flattened under a `source_` prefix so they can never
# collide with a pipeline key. Only the fields the source actually declares are
# emitted — an absent one is omitted, never written empty.
source_title: Kestrel Radar Spec
source_author: N. Engineer
source_version: "3"
source_created: 2026-03-04T09:12:00Z
source_modified: 2026-05-19T16:40:00Z
source_last_modified_by: N. Engineer
source_company: Nimbus Semiconductor
source_app_version: "16.0000"
---
```

Every value is rendered double-quoted, and the block is emitted and parsed by one
codec (`backend.ingest.render_front_matter` / `split_front_matter`). Two invariants
hold for anything written here: **no line is ever exactly `---`** (three separate
strippers locate the closing fence by scanning for that line) and **no raw control
character is emitted** (the validator raises a hard `bad-chars` error for those even
inside front matter). Quoting every string is also what makes the block safe to read
with a YAML 1.1 parser, which would otherwise retype an unquoted `no`, `on` or
`0644`.

### The `meta` block

A converted document may additionally carry a `meta:` mapping holding the tiered
document **descriptors** — identity, classification, tags, audience, ownership —
governed by a controlled vocabulary. It is nested under one key precisely so the
enrichment stage can rewrite its own block wholesale without touching a pipeline key,
and so a `title` can never collide with `source_title`.

The graph-shaped half of that metadata (entities, relations, decisions, risks,
open_questions, links) lives in [`knowledge.json`](#knowledgejson--the-extracted-graph-payload)
instead — see there for the seam and why it is drawn where it is.

Because `markdown_sha256` covers the **body only** and every line index is
body-relative, the `meta` block can be added, rewritten or reordered after
conversion without invalidating a hash, a span or an image index. That is what makes
metadata backfillable without re-converting a corpus.

The tiers, the vocabulary and who may write each field are specified in
[`document-metadata.md`](document-metadata.md).

## `structure.json` — the document outline

The **faithful heading tree** of the document: every heading, nested by level, with
token counts and image placement. This is **not** `chunk_sections` — that is the
size-bounded RAG *derivation*. The outline is the *map*; the RAG system chunks from
the outline + markdown. Both share the heading helpers (`is_heading`,
`gfm_anchor`, `normalize_title`) so they agree on what a heading is and what it is
addressable by.

Produced by the **deterministic layer, no LLM.** Token counts come from an injected
tokenizer (the same `token_count` callable the chunker takes); with no tokenizer,
counts fall back to the char estimate and `token_model` records that. The tokenizer is
wired in `config/settings.py` (`get_token_counter` → `(callable, token_model)`;
backends: `char` default / `tiktoken` / `huggingface` / `callable`), resolved outside
the 3.6/stdlib backend so no tokenizer dependency leaks in. `build_bundle.py
--tokenizer <backend[:model]>` overrides per run.

```jsonc
{
  "doc_id": "…",
  "source_format": "docx",
  "lane": "office",
  "markdown_sha256": "…",              // the exact body bytes every line_span indexes —
                                       //   verify against document.md/report.json before
                                       //   trusting a span (files are written sequentially,
                                       //   not transactionally)
  "token_model": "cl100k_base",        // or "char-estimate/4" when no tokenizer
  "total_tokens": 12345,
  "levels_inferred": false,              // true when the nesting was read from the titles'
                                         //   section numbering because the extractor emitted
                                         //   one level for the whole document
  "outline": [
    {
      "id": "sec-0001",                  // POSITIONAL — addresses a place, not a section
      "section_id": "3f2a10c4d5e6b708",  // sha1(anchor)[:16] — content-derived, survives an
                                         //   inserted heading; the corpus key is (doc_id, this)
      "parent": null,                    // parent node's section_id; null at top level
      "level": 1,
      "title": "System Design",
      "anchor": "system-design",         // THE url fragment a renderer emits for this heading,
                                         //   section number kept ("1.2 Scope" -> "12-scope");
                                         //   repeats take the renderer's suffix ("-1", "-2"),
                                         //   so the set matches kb.body_anchors exactly
      "line_span": [10, 120],            // [l0, l1) into the markdown BODY (see note below)
      "self_tokens": 900,                // body tokens before children
      "subtree_tokens": 4200,            // incl. all descendants
      "fingerprint": "9c1f0b7a4e2d6531",  // sha1 of this node's OWN body, markdown stripped
      "tables": [                        // tables are NODES, addressable like images
        {
          "table_id": "tbl-7c4a1e9b02",  // sha1(anchor + normalised header + ordinal)[:10] —
                                         //   content-derived, so an edit above it does not
                                         //   move it; editing the header row does
          "line": 55,                    // the header row's placement in the BODY
          "rows": 5,                     // header row + data rows (md_structure's convention)
          "cols": 4,
          "has_header": true
        }
      ],
      "images": [
        {
          "image_id": "219f951a5046d997",         // sha16 of the image bytes
          "ref": "images/219f951a5046d997.png",
          "line": 42,                    // placement in the markdown BODY (see note below)
          "alt": "",                     // deterministic; empty in the office pass today
          "caption": null,               // VLM enrichment; null until captioning runs
          "bytes": 48123,                // MEASURED from the extracted bytes (writer probe);
          "width": 640, "height": 480    //   dims omitted for metafiles/unknown headers —
                                         //   never guessed
        }
      ],
      "links": [                         // hyperlinks in this section's own body (the image
        {                                //   pattern applied to connectivity — pure harvest)
          "text": "Radar ICD v2",
          "url": "https://…/radar_icd.docx",   // verbatim; resolving it to another doc_id
          "line": 47                     //   (the doc->doc edge) is the CONSUMER's job
        }
      ],
      "children": [ /* nested headings, same node shape */ ]
    }
  ]
}
```

`self_tokens` vs `subtree_tokens` is what makes the outline useful for **validating
contextual chunking**: find any heading whose `subtree_tokens` blows the embedding
budget, and you know it must split — no re-tokenizing the markdown to find out.

**Line indices are body-relative.** Every `line_span` and image `line` indexes the
markdown **body** — the exact bytes `markdown_sha256` covers — not `document.md` as a
whole. The body is frontmatter-independent, so these indices stay stable across runs
even though the front matter carries a per-run `generated_run`; a consumer that wants
to index `document.md` directly must first strip its leading front-matter block.

**Links are the fourth output: connectivity.** The founding rule of this contract —
a consumer never re-parses the markdown to learn a fact — extends to hyperlinks:
each node's `links[]` harvests the `[text](url)` references from that section's own
body (children carry their own), exactly as image nodes harvest `![](…)`. Unlike
images there are no bytes, no integrity gate and no enrichment stage — a link is
pure text, so this is harvest-only. URLs are verbatim: mapping a URL to another
corpus document (the knowledge-graph doc→doc edge) is corpus-level inference and
stays the consumer's job.

**Image captions live here, never in the report.** A caption is a VLM (LLM) output;
the report is validator-only by contract. The deterministic core always emits the
image↔markdown *mapping* (`image_id`, `ref`, `line`, `alt`); the separable
captioning stage later populates `caption` in place. So `structure.json` is
produced without a VLM and *enriched* with one — captioning stays a detachable
shared stage.

## `report.json` — validator-only QA

Comes **entirely from the validator**; contains **no LLM output**. Its job is to
answer "did this conversion succeed, was it lossless, and how do we know?" plus
enough metrics for a dashboard to triage without opening the markdown.

```jsonc
{
  "doc_id": "…",
  "lane": "office",
  "source_format": "docx",
  "converter": "doc2md-ooxml/0.1.0",
  "generated_run": "20260710T120000Z",   // run provenance (a failed doc's ONLY artifact
                                         //   is report.json, so it lives here too)
  "run": { /* what this run WAS -- see below */ },
  "decisions": [ /* what it CHOSE -- see below */ ],
  "source_sha256": "…",
  "markdown_sha256": "…",
  "status": "ok",                        // ok | degraded | failed
  "token_model": "cl100k_base",          // names the tokenizer behind content.tokens
  "losslessness": {                      // OFFICE (deterministic, gated):
    "method": "ooxml-ground-truth",
    "token_recall": 1.0,                 // hard gate == 1.0
    "missing_tokens": [],                // populated only when recall < 1.0
    "gate": "pass"                       // pass | fail
  },
  // PDF instead reports MEASURED best-effort coverage — there is NO ground-truth
  // semantic tree to grade against, so a "pass" is not claimable (build_report
  // coerces any non-office gate to best-effort structurally). Token recall and
  // char-n-gram content recall are scored against the PDF's own text layer
  // (pdftotext), de-boilerplated and with furniture/figure-region text excluded
  // apple-to-apple. Measured REAL loss (both recalls low) degrades status via a
  // pdf_content_loss warning:
  //   "losslessness": { "method": "pdf-text-coverage", "token_recall": 0.9964,
  //                     "content_recall": 0.9867, "n_source_tokens": 1100,
  //                     "missing_tokens": [], "figure_text_tokens": 214,
  //                     "ocr_used": false, "gate": "best-effort" }
  // figure_text_tokens = text buried inside figure regions (excluded from the body
  // metric — figure content, not lost body text) — the one loss class only the VLM
  // caption stage can recover, surfaced per doc so that debt is visible.
  // A scanned PDF has no independent layer at all:
  //   "losslessness": { "method": "pdf-ocr-transcription", "ocr_used": true,
  //                     "gate": "best-effort" }
  "content": {
    "chars": 48210, "tokens": 12345, "headings": 34,
    "tables": 6, "images": 4, "links": 9, "lists": 12, "code_blocks": 2, "formulas": 0
  },
  "savings": {                           // representation savings (office lane; MEASURED):
    "source_repr": "ooxml-xml",          // what the markdown replaced
    "source_chars": 655360,              // decompressed chars of every XML part parsed
    "markdown_chars": 48210,             // chars only — measured identically both sides;
    "reduction_ratio": 13.59,            //   token views are derivable, not stored
    "saved_pct": 92.64
    // informational only — no gate, never moves status. Omitted when the writer did
    // not measure the source side (e.g. PDF: binary glyphs, no raw-text repr).
  },
  "structure": {
    "max_depth": 4,                      // TREE depth, not max(level)
    "largest_section_tokens": 4200,      // biggest subtree anywhere (includes the root)
    "largest_leaf_tokens": 1350,         // biggest LEAF — the unit actually retrieved
    "has_toc": true,
    "coverage": {                        // OUTLINE-COVERAGE gate (structure-side recall):
      "content_lines": 812,              // non-blank lines in the markdown body
      "covered_lines": 809,              // lines inside some outline node's line_span
      "toc_lines": 3,                    // intentional TOC-furniture skip (dot-leaders)
      "uncovered_lines": 0,              // body lines the outline LOST (real content)
      "ratio": 1.0,                      // (covered + toc) / content
      "gate": "pass"                     // pass | degraded  (degrades status)
    }
  },
  "warnings": [
    // the soffice VERSION is named: it is the one external binary in the office lane
    { "code": "libreoffice_preconvert", "detail": "odt -> docx via soffice (LibreOffice 7.6.4.1)" },
    { "code": "dropped_headers_footers" }
  ],
  "images": {                            // DETERMINISTIC image-extraction integrity gate:
    "referenced": 4,                     // ![](images/..) links in the body (ground truth)
    "unique_files": 3,                   // distinct content-addressed files (dedup)
    "extracted": 4, "missing": 0,        // sentinels resolved to bytes / dropped (no bytes)
    "orphans": 0,                        // files on disk with no reference (GC'd to 0)
    "verified": 3,                       // files whose on-disk sha16 == filename (intact)
    "gate": "pass"                       // pass | degraded  (degrades status, NOT recall)
  },
  "captions": {                          // OVERLAY coverage gate (written by the caption pass):
    "enabled": true, "expected": 3,      // captions on? / unique images to caption
    "captioned": 3, "furniture": 0,      // useful captions stored / model-classed furniture
    "useless": 0, "pending": 0,          // failed useful gate / no terminal verdict yet
    "model": "qwen2.5-vl-7b", "prompt_sha": "cb1255a7f554",
    "gate": "complete"                   // disabled | pending | incomplete | complete
  },
  "doc_meta": {                          // OVERLAY coverage gate (metadata enrichment):
    "enabled": true,                     // a model was reachable this run
    "schema_version": 3, "vocab_version": 2,   // field inventory / term list revisions
                                         // v3 = one identity, section-anchored records
    "expected": 16,                      // counted over the MERGED view: 5 of these
                                         // fields live in knowledge.json, not here
    "filled": 14,                        // fields with a valid value
    "authored": 2, "invalid": 0,         // written by a PERSON / present but off-vocabulary
    "pending": 6,                        // still empty — backfillable without re-converting
    "model": "qwen2.5-vl-7b", "prompt_sha": "b4f8b3ac61a5",
    "gate": "incomplete"                 // disabled | pending | incomplete | complete
  },
  "timing_ms": { "convert": 812, "validate": 143 }
}
```

Fields beyond the raw lossless flag, and why they earn their place:

- `markdown_sha256` — determinism check + cache key; a re-run that changes it is a
  regression to investigate.
- `converter` version — provenance/reproducibility. **Derived**
  (`doc2md-<lane>/<version>+<commit7>`, `.dirty` when the checkout had uncommitted
  changes), never a literal: a frozen stamp meant that when a real converter bug was
  found, nothing on disk said which bundles came from the broken code.
- `run{}` — **what this run was**: the entrypoint, the run id, the `argv` that was
  used (path values redacted to `<src>`/`<out>` — the root `CLAUDE.md` forbids an
  absolute host path in published output), a `source_root_id`, the code identity, the
  interpreter and platform, the external tool versions, and a `config_ref` naming
  where the resolved configuration lives. The full configuration is stored once per
  run in `runs.jsonl` rather than duplicated into every bundle; the omission is
  named, not silent. `scripts/replay_run.py` reconstructs the command from this block
  and reports every divergence before running anything.
- `decisions[]` — **what this run chose**: `{code, chose, reason, evidence}` for the
  lane, the tokenizer, an OCR route, a text-layer fallback, a cache hit, a coerced
  gate, an empty source. `warnings[]` carries PROBLEMS; a choice is not a problem,
  and mixing the two makes "how many documents took the fallback" a grep through
  prose instead of a count. The code list is closed, so an unnamed decision cannot
  quietly become a category of one.
- `savings{}` — the measured exchange rate of the conversion: how many chars of raw
  source representation each markdown char replaced. The source side is the
  decompressed size of every XML part the converter actually parsed (returned by
  `bundle_inputs` as `source_repr_chars`), so the number is measured, never estimated
  from file size. Chars only, measured identically on both sides — token views are
  derivable by the consumer under its own tokenizer, so they are not stored.
  Informational only: no gate, never touches `status`; omitted for lanes with no
  raw-text source representation (PDF).
- `generated_run` — run provenance in the report itself: a **failed** document
  publishes `report.json` only (no `document.md`), so without this field a failure
  would carry no run id at all.
- `token_model` — names the tokenizer behind `content.tokens` (mirrors
  `structure.json`), so the number is self-describing without opening the sibling.
- `status: ok|degraded|failed` — a dashboard triages without parsing internals
  (`degraded` = converted with warnings OR a degraded image gate; `failed` = no valid
  markdown). The **caption gate never touches `status`** — captioning is a re-runnable
  overlay, so an un-captioned but lossless doc stays `ok`.
- `images{}` — the pixel-side twin of the text gate. Body images are HTML-comment
  sentinels the token-recall metric cannot see, so a dropped, un-extracted, orphaned or
  corrupt picture would be an **invisible** loss. `verified` re-hashes each written file
  and checks its content `sha16` matches its content-addressed name (the bytes landed
  intact); `orphans` is swept to 0 by a GC on every build (a replaced figure leaves no
  stale file); `missing` (bytes absent from the package) is always mirrored by an
  `image_bytes_missing` warning. `gate: degraded` **degrades `status`** but never fails
  the losslessness gate — the text is still whole. A `![](images/..)` link that never
  attached to a section (outline gap) is surfaced as an `images_not_in_outline` warning,
  because such an image is uncaptionable.
- `structure.coverage{}` — the structure-side twin of the recall gate. The recall gate
  proves every source token reached `document.md`; nothing proved those lines then
  reached the outline in `structure.json` (navigation, carding and captioning all walk
  the outline). This block measures back from the **built** outline (union of every
  node's `line_span`) against the body and classifies every non-blank line: covered,
  intentional TOC furniture, or **uncovered = lost structure**. Any uncovered line sets
  `gate: degraded` (plus an `outline_uncovered_content` warning naming the first
  offending line numbers) and degrades `status` — an outline-builder bug can no longer
  drop a region silently. Never touches losslessness: the text is still whole.
- `captions{}` — written by the caption enrichment pass into `report.json` the same way
  the office lane records losslessness, but touching **only this block**. `gate` is
  `disabled` (captioning off), `pending` (built, never run), `incomplete` (a run left
  images uncaptioned — re-run when the VLM is up), or `complete` (every expected image
  reached a terminal verdict). This is the caption-coverage analogue of the recall gate.
- `doc_meta{}` — the same overlay shape for document-level metadata enrichment, kept
  just as separate from `status`: a lossless document must not read as degraded merely
  because no model has classified it yet. `expected` counts model-writable fields — the
  denominator is the SCHEMA, not what a run attempted, so a skipped field reports as
  pending instead of quietly shrinking the target. One deliberate difference from
  `captions{}`: a non-zero `invalid` keeps the gate off `complete`, because a value
  outside a closed vocabulary is worse than an absent one — it silently becomes a new
  term for everything that groups by that field. The values themselves live in
  `document.md`; only the gate over them lives here. See
  [`document-metadata.md`](document-metadata.md).
- `warnings[]` — every deliberate drop (headers/footers, tracked deletions,
  `mc:Fallback`), every fallback (LibreOffice pre-convert, OCR pages), and every image
  hygiene event (`image_bytes_missing`, `orphan_images_removed`, `images_not_in_outline`)
  is named, never silent. PDF-lane additions: `pdf_toolchain` (the provenance stamp —
  the pdf-lane analogue of the soffice version naming above: docling + docling-core
  always, poppler's `pdftotext` when it supplies the ground-truth layer; on every
  pdf/html report, including failure reports), `ocr_transcription` (scanned source),
  `pdf_text_layer_fallback` (docling markdown provably dropped body content the text
  layer holds — the layer is used instead), `pdf_content_loss` (measured real loss
  under the explained-gap model; degrades status), `image_inline_bailed`
  (placeholder/picture count mismatch: positional binding unsafe, no pixels written —
  a detected, gated loss, never a mis-bound figure).

## `knowledge.json` — the extracted graph payload

Written by `scripts/enrich_metadata.py`, never by the bundle writer. A bundle that has
not been enriched simply does not have one — the same way it has no captions until the
captioning stage runs.

```json
{
  "doc_id": "mem-spec",
  "id": "specs/mem",
  "uid": "specs/mem",
  "schema_version": 3,
  "vocab_version": 2,
  "markdown_sha256": "…",

  "entities":       { "hosts": [ … ], "software": [ … ] },
  "relations":      [ { "s": "arbiter", "p": "requires", "o": "ddr-phy" } ],
  "decisions":      [ … ],
  "risks":          [ … ],
  "open_questions": [ … ],
  "links":          { "internal": [ … ] },
  "_provenance":    { "relations": { "tier": 2, "source": "generated", … } }
}
```

**Why this is a file and not front matter.** The metadata a document carries is two
different things wearing one name. *Descriptors* — `id`, `title`, `type`, `tags`,
`status`, `owner` — describe the document, are small and bounded, are what retrieval
filters on, and are what markdown tooling reads for free; they stay in
`document.md`. *Knowledge* — entities, relations, decisions, risks, links —
describes the world the document talks about, is unbounded, and is consumed by a
graph loader that never wants the prose. Measured on a real enriched document, the
knowledge payload was ~19k characters against ~3.5k of descriptors, roughly **5:1**:
leaving it in front matter makes every consumer that only wants the body pay for a
relation table it will discard. `structure.json` and `report.json` already set the
precedent — in this bundle every derived, machine-consumed artefact is a sibling JSON
file, and this was the only one that was not.

**The seam is mechanical**, so it cannot drift into a matter of taste: a field lives
in `knowledge.json` iff it is a `records`/`groups` field **and** is not
`authored_only`. The carve-out matters — `accountable_roles` is a record list, but it
is a statement about who stands behind the document, so it belongs with the document.
A `<field>_proposed` slot always follows its field, so promotion evidence never ends
up in a different file from the values it is evidence about.

**Self-contained.** The header repeats `doc_id`, `id`, `uid`, the two versions and
`markdown_sha256`, so a graph loader never has to open `document.md` at all — the same
property `structure.json` has, and the reason the split is worth doing rather than
merely tidier. `_provenance` splits with its fields, so neither file is a fragment
that must be joined before it can be read.

**One view for everything that reads it.** The layout is confined to
`backend.kb._schema` (`in_knowledge`, `split_meta`, `merge_meta`,
`knowledge_document`, `knowledge_payload`, `meta_collisions`); the scripts only do the
disk touch. The linter and the enricher work on a single merged mapping, so no rule in
`backend.kb` has to care. A field present in BOTH files is an ERROR (`meta_collisions`)
rather than a silent merge — the loser would be rewritten away by the next run. `kb_lint` still tells a person
which file a finding belongs to (`knowledge.json:relations[1].p`), because otherwise
the split trades a token cost for a scavenger hunt.

**Absent vs unreadable are different states.** No file means "not enriched yet". A
file that exists but will not parse is an ERROR that stops the document being written
— treating it as "no knowledge yet" would let the next run regenerate over entities
and relations a person corrected by hand, which is exactly what *authored always wins*
exists to prevent.

## The lane asymmetry (stated once, honestly)

| | Office lane | PDF lane |
|---|---|---|
| Source is a lossless semantic tree | **yes** (OOXML) | **no** (glyphs at coordinates) |
| Conversion | deterministic | model inference (docling) |
| `losslessness.method` | `ooxml-ground-truth` | `pdf-text-coverage` |
| Gate | `token_recall == 1.0` (hard) | best-effort coverage |
| `lossless` frontmatter | `true` | `false` |
| Writer | `scripts/build_bundle.py` (py3.6) | `scripts/build_pdf_bundle.py` (py3.12 + docling) |

Both writers share one `--out` root, one manifest, and every non-losslessness gate
(image integrity, outline coverage, captions). The PDF writer additionally applies
two convert-time fidelity passes, both anchored to the PDF's own text layer:
**raw-vocab repair** (`backend.ingest.repair_split_tokens` — rejoins identifiers a
layout model split across a wrapped table cell, iff the joined form exists verbatim
in the raw layer; zero false joins by construction) and the **text-layer fallback**
(when docling's markdown provably dropped body content, the de-boilerplated text
layer is used and recorded).

The bundle shape is identical so consumers are lane-agnostic; the *report* never
pretends PDF conversion is provably lossless when it structurally cannot be. See
[`ooxml-lane.md`](ooxml-lane.md) for the Office gate and
[`document-metadata.md`](document-metadata.md) for the metadata tiers and the
controlled vocabulary, and
[`image-captioning.md`](image-captioning.md) for the captioning stage that
populates `structure.json` image captions.

## Build order

1. **Deterministic core** (this doc's no-LLM parts), Office lane first:
   - `document_outline(text, token_count=None)` in `backend.sections` — the heading
     tree with `self_tokens`/`subtree_tokens` and image placement. Reuses
     `is_heading`/`normalize_title`; 3.6-safe, stdlib-only.
   - report emitter in `backend.validate` — assembles `report.json` from the
     existing converter-blind ground truth (extend, don't weaken, the recall gate).
   - bundle writer (a `scripts/` entrypoint) — orchestrates convert → validate →
     outline → write bundle. No conversion logic in the script.
   - **image extraction (done)** — the OOXML converter emits a positional
     `<!-- ooxml-image:PART -->` sentinel per body picture (an HTML comment, so the
     recall gate never moves); the bundle writer resolves each to a content-addressed
     file under `images/` and an `![](images/<sha16>.ext)` link, and populates the
     `structure.json` image nodes. docx (drawings + VML), pptx (pics + graphicFrame
     objects incl. `mc:Choice` zoom/OLE), and xlsx (drawing pictures, grouped under
     `## Images`). SVG stays text; alt/caption fill later. Opt-in (`emit_images`), so
     the legacy markdown lane is byte-identical. The writer then **verifies** the bytes
     on disk (content `sha16` == filename), **GCs orphaned** files from a prior build,
     and records the `images{}` integrity gate — so a degraded gate is caught, not
     silently shipped.
2. **Mirror into the PDF lane** — same bundle, `pdf-text-coverage` losslessness.
3. **Caption enrichment (done)** — `scripts/caption_bundles.py` walks each bundle's
   `structure.json` image nodes, captions every unique image (content-addressed cache,
   so each is captioned once) through the shared formula-safe caption tool + VLM, and
   fills the node `caption` in place, then records the coverage verdict in the
   `report.json` `captions{}` block (gate + counts + model). It stays a **detachable
   overlay**: `document.md`, `status` and the losslessness verdict are never touched, and
   a VLM outage leaves an image PENDING (`caption: null`, gate `incomplete`) for a re-run.
   The prompt is **tunable and measurable** — `--domain`/`--domain-file` prepends
   corpus-level grounding, `--prompt-file` replaces the prompt wholesale, and
   `_caption_coverage.jsonl` + the printed useful/furniture/pending pass-rate report how
   the current prompt performs (iterate with `--no-cache`).
   - **Incremental / non-destructive rebuild** — the two stages are decoupled by a
     content+model+prompt cache, so replacing one figure re-captions only that image. A
     `build_bundle --force` rebuild **carries unchanged images' captions forward** (by
     `image_id`), so it never destroys prior enrichment; only new/changed images need the
     (cache-fast) caption pass re-run.
4. **Document metadata (done)** — `scripts/enrich_metadata.py` writes the `meta` block
   into each `document.md`'s front matter and the `doc_meta` gate into `report.json`, and
   `scripts/kb_lint.py` grades a whole corpus against the controlled vocabulary. The
   second detachable overlay, and it follows the caption stage's shape deliberately: the
   body is never touched, a model outage leaves fields PENDING, and answers are cached on
   content + model + prompt + vocabulary version. What it adds is **governance** — values
   come from a closed vocabulary or a promotion-gated registry, a model may not write an
   accountability field at all, and an authored value is never overwritten. See
   [`document-metadata.md`](document-metadata.md).
