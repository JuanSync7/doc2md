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

Optional corpus-level index (object-store friendly): a `manifest.jsonl`, one line
per document `{doc_id, source_relpath, lane, status, markdown_sha256}` — an index,
not a source of truth; every fact in it also lives in the bundle.

## `document.md` frontmatter

Self-describing per file — this **is** the document↔markdown mapping (no external
lookup needed):

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
---
```

## `structure.json` — the document outline

The **faithful heading tree** of the document: every heading, nested by level, with
token counts and image placement. This is **not** `chunk_sections` — that is the
size-bounded RAG *derivation*. The outline is the *map*; the RAG system chunks from
the outline + markdown. Both share the heading helpers (`is_heading`,
`normalize_title`) so they agree on what a heading is.

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
  "token_model": "cl100k_base",        // or "char-estimate/4" when no tokenizer
  "total_tokens": 12345,
  "outline": [
    {
      "id": "sec-0001",
      "level": 1,
      "title": "System Design",
      "anchor": "system-design",         // normalize_title, disambiguated within doc
      "line_span": [10, 120],            // [l0, l1) into the markdown BODY (see note below)
      "self_tokens": 900,                // body tokens before children
      "subtree_tokens": 4200,            // incl. all descendants
      "tables": 2,
      "images": [
        {
          "image_id": "219f951a5046d997",         // sha16 of the image bytes
          "ref": "images/219f951a5046d997.png",
          "line": 42,                    // placement in the markdown BODY (see note below)
          "alt": "",                     // deterministic; empty in the office pass today
          "caption": null                // VLM enrichment; null until captioning runs
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
  "source_sha256": "…",
  "markdown_sha256": "…",
  "status": "ok",                        // ok | degraded | failed
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
    "tables": 6, "images": 4, "lists": 12, "code_blocks": 2, "formulas": 0
  },
  "structure": {
    "max_depth": 4, "largest_section_tokens": 4200, "has_toc": true,
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
    { "code": "libreoffice_preconvert", "detail": "odt → docx via soffice" },
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
  "timing_ms": { "convert": 812, "validate": 143 }
}
```

Fields beyond the raw lossless flag, and why they earn their place:

- `markdown_sha256` — determinism check + cache key; a re-run that changes it is a
  regression to investigate.
- `converter` version — provenance/reproducibility.
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
- `warnings[]` — every deliberate drop (headers/footers, tracked deletions,
  `mc:Fallback`), every fallback (LibreOffice pre-convert, OCR pages), and every image
  hygiene event (`image_bytes_missing`, `orphan_images_removed`, `images_not_in_outline`)
  is named, never silent. PDF-lane additions: `ocr_transcription` (scanned source),
  `pdf_text_layer_fallback` (docling markdown provably dropped body content the text
  layer holds — the layer is used instead), `pdf_content_loss` (measured real loss
  under the explained-gap model; degrades status), `image_inline_bailed`
  (placeholder/picture count mismatch: positional binding unsafe, no pixels written —
  a detected, gated loss, never a mis-bound figure).

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
