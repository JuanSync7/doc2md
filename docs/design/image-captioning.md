---
title: Image captioning — one shared tool, every lane
kind: design
layer: backend
status: reviewed
owner: TBD
summary: A formula-safe figure gate (evolved from the shipped one) + content-addressed VLM caption tool + an independent second-pass validator, called identically by the docling, office, and standalone-image lanes.
---

# Image captioning — one shared tool, every lane

**Status: reviewed (2026-07-07)** — hardened against a 43-finding adversarial critique
(6 blockers). Deterministic text is already lossless (OOXML lane at recall 1.0; SVG vector
labels captured — see [`ooxml-lane.md`](ooxml-lane.md)). The remaining gap is **text that
exists only as pixels** inside embedded raster/metafile images — screenshots, block/timing
diagrams, and **formula images**. This document specifies the one shared mechanism that
recovers that text for **every** lane, and the runtime validator that proves it was done.

## Prime directive (the invariant every rule bends to)

> **An informative image is NEVER dropped before the model sees it. A formula rendered as
> an image is informative.** Furniture (logos/icons/watermarks) is removed only on signals
> a formula can never carry — and when unsure, we caption it (once, cached) and let the
> model decide. Cheap-but-wrong (waste one cached call on a logo) always beats
> cheap-but-lossy (drop a formula).

## Goals / non-goals

**Goals**
- ONE image→text path, called identically by docling (PDF), office (docx/pptx/xlsx), and a
  new standalone-image lane; a `.png` source tomorrow just works.
- A **formula-safe** gate that filters furniture *cheaply* but never an informative image.
- A **content-addressed cache**: the same image is captioned **once** corpus-wide.
- An **independent second-pass validator** that *measures* losslessness/correctness from the
  ORIGINAL sources, not from the pass's own output.

**Non-goals**
- Not in any text losslessness *gate*. Image text has no ground truth (a PDF can't hit
  recall 1.0), so it is tracked by **figure coverage**, never token recall.
- No heavy deps in the deterministic lanes. The VLM lives behind one client.
- Off by default (`enable_captions`); the deterministic lanes are unaffected when off.

## Why furniture-filtering is formula-safe here (the critique's #1 blocker)

Cross-doc **recurrence and byte-size CANNOT independently drop** — a reused schematic or a
standardized formula image recurs across a document series and is small (1–5 KB), so
`n_docs` cannot tell a 48-doc logo from a 5-doc shared formula. Therefore:

- **The cache, not a drop, handles reuse.** A logo in 48 docs is captioned **once** (keyed
  by content hash) and that one caption is reused — 47 calls saved *without dropping anything*.
- **Furniture is removed on formula-impossible signals only:** (a) **chrome placement** —
  referenced only from headers/footers/slide-number-date-footer placeholders (never body);
  (b) docling's **deny-class** classifier (logo/icon/stamp/…); (c) the **model's own verdict**
  after captioning — the prompt already asks it to name the type, so a caption whose stated
  type is "logo/icon/watermark/stamp/signature/QR/barcode" is dropped *post-hoc*.
- A small, unique, body-placed image (the formula case) matches none of these → it is
  captioned and kept. **`test_formula_image_is_never_gated_out` pins this.**

Recurrence/size are still **recorded** (for the validator and analytics) and drive the
cache; they never hard-drop.

## Architecture — layers and the interpreter split

```
  ┌── pure policy (backend.ingest, py3.6, stdlib) — EVOLVE the shipped surface ───────────┐
  │  gate_figures(pictures)         → [FigureDecision(keep, reason)]   (extended, None-safe)│
  │  caption_is_useful(text)         caption_type_is_furniture(text)   (NEW post-filter)    │
  │  image_markdown(caption, path)   inline_image_captions(md, fills)  (id-sentinel, NEW)   │
  │  figure_outcome / figure_coverage / FigureCoverage / FIG_* (extended reasons)           │
  │  caption_cache_key(bytes)  cache_merge(records)                    (NEW pure cache core) │
  └───────────────▲───────────────────────────────────────────────▲────────────────────────┘
                  │ (one policy, both interpreters import it)       │
  ┌── extractors (per lane, dump candidate + bytes + id-placeholder)┴─┐  ┌─ caption tool (py3.12) ┐
  │  docling: PDF crops (refactor: dump sha-named crops + metadata)    │  │ scripts/image_caption.py│
  │  office : zip media + rels ref-location (pure classify + zip I/O)  │  │  caption_image() ->     │
  │  image  : the file itself (ROUTE_IMAGE)                            │  │   Outcome(kind,text,…)  │
  │  svg    : deterministic svg_text (NO model — text lane, shipped)   │  │  • decode-probe + downscale│
  └───────────────┬───────────────────────────────────────────────────┘  │  • soffice render emf/wmf │
                  │ candidates + bytes to data/assets/<doc_id>/           │  • VlmClient (1 net edge) │
                  ▼                                                        │  • CaptionCache (file I/O)│
  ┌── enrichment: ONE corpus-GLOBAL stage (py3.12) ──────────────────────┘└───────────▲───────────┘
  │  1. collect ALL candidates from all lanes → build sha→{n_docs,occ} index           │
  │  2. per candidate: gate_figures → (kept?) → caption_image(cached) ──────────────────┘
  │  3. classify outcome; inline_image_captions by id; append _figures.<shard>.jsonl
  └── run by a batch driver; resumable via cache; shard-suffixed record files

  ┌── second pass — INDEPENDENT validator (scripts/validate_figures.py) ─────────────────┐
  │  re-opens ORIGINAL sources; byte-magic ground truth; own non-zero exit; pending-aware │
  └───────────────────────────────────────────────────────────────────────────────────────┘
```

**Interpreter split.** Deterministic text/asset extraction stays in `office_convert.py`
(py3.6, stdlib, no network) and the docling text pass. The VLM is py3.12+network, so the
enrichment is a separate stage over the dumped asset store. **All *policy* — the gate, the
cache KEY/merge semantics, inlining, coverage — is pure py3.6 in `backend.ingest`**, imported
by both interpreters and unit-tested under 3.6. Only file-open/write and the VlmClient wiring
live in `scripts/image_caption.py`. (Fixes the critique's CaptionCache interpreter split.)

## The gate — evolve `gate_figures` in place (no fork)

`gate_figures(pictures)` keeps its name/return type (`[FigureDecision(keep, reason)]`,
input order) so the four shipped consumers (`docling_convert.py`, three test files) keep
working. Each `picture` dict gains OPTIONAL keys (missing ⇒ `None` ⇒ "unknown", never a
crash). All comparisons guard `x is not None and …` (py3.6: `None < 0.02` raises).

| key | source | used by rule |
|---|---|---|
| `cls` | docling classifier (None for office) | deny-class |
| `area` | page-area fraction, or None (office) | tiny / deny re-admit |
| `sha` | **canonical content hash** (see below) | within-doc dup |
| `ref` | `body` / `chrome` / None — **body-wins across occurrences** | chrome |
| `n_bytes`, `fmt`, `n_docs`, `occ_in_doc` | recorded; **do NOT hard-drop** | (analytics + cache) |

**Rules (in order); a formula matches none:**
1. `cls in DENY_CLASSES and not (area is not None and area >= AREA_READMIT)` → drop `deny:<cls>`.
2. `area is not None and area < AREA_MIN` → drop `tiny`.
3. `ref == "chrome"` → drop `chrome`. `ref` is `body` if **any** occurrence is body
   (body-wins); `chrome` only if **all** placements are header/footer/slide-number-date-footer
   placeholders (not all masters/layouts — pptx content lives there too).
4. `sha` seen earlier in this doc → drop `dup` (keep-first; unchanged shipped behavior).
5. else → keep `keep`.

There is **no recurrence rule and no size rule** in the gate. `n_docs`/`n_bytes` are
recorded but never drop (critique blocker #1). Furniture that slips past rules 1–4 (a unique
body logo in office, where there is no `cls`) is caught **after** captioning by
`caption_type_is_furniture` (below), so it costs one cached call and never risks a formula.

`caption_type_is_furniture(caption)` (NEW, pure): the CAPTION_PROMPT makes the model state
the visual TYPE first; if that leading type is logo/icon/watermark/stamp/signature/QR/barcode
→ True → drop. Keyed only on the model's OUTPUT, generic, unit-tested with formula captions
asserting False.

## Canonical content hash (one hash, everywhere)

`caption_cache_key(image_bytes) = "sha256:" + sha256(image_bytes).hexdigest()` over the
**exact bytes handed to `caption_image`** (the PNG *after* any soffice render / downscale).
The SAME value is the `sha` in `_figures.jsonl`, the `sha` in the gate candidate, and the key
in `_captions.jsonl`. The shipped docling `sha1(_png_bytes)[:16]` is migrated to it. (Fixes
the critique's unjoinable-hash blocker.) Byte-sha is byte-identity, not visual-identity — a
logo re-encoded PNG-vs-JPEG under-dedups; that only wastes cached calls, never drops a
formula, so it is accepted (documented best-effort).

## The caption tool + cache (`scripts/image_caption.py`, py3.12)

`caption_image(image_bytes, fmt, cache, client, cfg) -> Outcome` where
`Outcome = namedtuple(kind, text, model, prompt_version, truncated)` and `kind` is one of:

| kind | meaning | terminal? | markdown result |
|---|---|---|---|
| `OK` | useful caption | terminal | inline caption (`FIG_CAPTURED`) |
| `USELESS` | model replied, `caption_is_useful` False, not furniture | terminal | keep image, **neutral alt** (`FIG_NO_CAPTION`) |
| `FURNITURE` | `caption_type_is_furniture` True | terminal | drop |
| `UNDECODABLE` | bytes fail a PIL `verify()` probe | terminal | drop, flagged |
| `TOO_LARGE` | exceeds `caption_max_pixels/bytes` even after downscale | terminal | drop, flagged |
| `RENDER_FAILED` | metafile soffice render failed/blank/absent | terminal **LOSS** | neutral alt, **counts against coverage** |
| `UNAVAILABLE` | transport error/timeout/non-200/health-fail | **NON-terminal (pending)** | leave sentinel; **never cached** |

Flow: decode-probe (`PIL.Image.open(BytesIO).verify()`; multi-frame GIF/TIFF flattened to a
single PNG) → downscale to `caption_max_pixels` (longest edge ~1568) → `key =
caption_cache_key(png)` → cache hit returns cached `OK/USELESS/FURNITURE` (never a cached
`UNAVAILABLE`) → else `client.caption(png)` → classify via `caption_is_useful` +
`caption_type_is_furniture` + `finish_reason` (truncated flag) → cache **only terminal**
outcomes, storing `{caption, kind, model, prompt_version, max_tokens, truncated, ts}`.
`UNAVAILABLE` is never cached, so a re-run retries it. Metafiles are rendered PNG-first via
`soffice --convert-to png` with a **per-worker isolated profile**
(`-env:UserInstallation=file:///<tmp>`), a timeout, and `RENDER_FAILED` on any failure.

## Per-lane extractors — same shape, id-sentinel placeholders

Each lane, at deterministic-extraction time, dumps image bytes to
`data/assets/<doc_id>/<canonical-sha>.<fmt>` and emits a **stable id sentinel** at the image
position: `<!-- figure:<doc_id>:<n> -->`. Sentinels are id-addressable and idempotent — the
enrichment pass replaces a sentinel by id from a `fills` dict; a re-run only ever replaces a
remaining sentinel, never double-substitutes (fixes the positional-list / re-run / escaping
blocker). `image_markdown` escaping is extended to `)`, backtick, `|`, newline, and the
sentinel token. A kept-but-useless figure gets a **non-empty typed neutral alt**
(`![figure](path)`), so a truly-empty `![]()` never occurs and the validator's
placeholder-resolution check is unambiguous.

- **office** (`_media.py` pure classify + `office_convert.py` I/O): enumerate media, compute
  canonical sha (of the raw media bytes; metafiles re-hashed post-render by the tool),
  `n_bytes`, `fmt`; resolve `ref` per-occurrence from which part's rels point at the media
  (`document.xml`/`slideN.xml` = body; `header*/footer*` and slide-number/date/footer
  placeholders = chrome), **body-wins**. Reconcile referenced rels vs present media parts:
  a dangling `r:embed` with no media part → terminal `media-missing` record. SVG stays
  deterministic (`svg_text`, shipped) and is NOT sent to the model.
- **docling** (real refactor, scoped): dump each BODY crop as `<canonical-sha>.png` +
  persist a candidate record (`area`, `ref=body`, sha) at extraction time; emit id sentinels
  instead of `<!-- image -->`; carry over the count-bail/alignment guard and `FigureCoverage`.
- **standalone image** (`ROUTE_IMAGE`, new): a raster file is a one-figure doc. `.svg` source
  → the **text lane** (`svg_text`), not the model image lane.

## The enrichment pass — ONE corpus-global stage

Recurrence needs corpus scope, so enrichment is a **single global stage** run after all lanes
have dumped candidates+bytes (not per-producer): (1) load every candidate, build
`sha → {n_docs, occ_in_doc}`; (2) `gate_figures`; (3) `caption_image` for kept; (4) classify
outcome; (5) `inline_image_captions(md, fills_by_id)`; (6) append
`data/assets/_figures.<shard>.jsonl`. Sharded/resumable; per-shard files merged by
glob + last-wins per `(doc_id, fig_id)` and per `sha` (the repo's convention —
`docling_convert.py` `_coverage.wN.jsonl`). `--recaption` and re-runs are idempotent
(re-inline from the surviving sentinel). `--compact` rewrites the jsonl keeping the last
record per key.

**`_figures.jsonl` record** (rich enough for every validator check to JOIN and re-derive):
`{doc_id, fig_id, sha, rel_path, fmt, n_bytes, ref, n_docs, occ_in_doc, area, cls, kept,
reason, outcome_kind, caption, caption_sha, captions_enabled, model, prompt_version,
truncated, ts}`.

## Second pass — the INDEPENDENT validator (`scripts/validate_figures.py`)

**Not a test. A runtime measurement** that re-derives ground truth from the **ORIGINAL
sources** so it cannot grade its own homework (critique blocker #3). Inputs: **source root**
(required), the markdown tree, the asset store, `_figures.<*>.jsonl`, `_captions.<*>.jsonl`.

1. **Independent ground truth.** Re-open every source and enumerate images by **byte magic**,
   NOT by the extractor's `*/media/*` path glob — scan every zip entry / PDF XObject and admit
   anything whose leading bytes match a known image signature (PNG/JPEG/GIF/BMP/TIFF/EMF/WMF/
   WEBP/SVG) wherever it lives (`embeddings/`, `customXml`, VML `v:imagedata`, DrawingML
   fallbacks). This is the byte-level analogue of `office_convert.py --audit-parts`. Dedup by
   content hash. `n_source_images` drives pass/fail.
2. **Accountability.** Every ground-truth image maps to a record whose state is one of:
   `captured` / `kept-neutral-alt` / `dropped-with-reason` / `pending`. **`n_unaccounted =
   n_source_images − mapped > 0 ⇒ FAIL** (a never-extracted image = silent loss).
3. **Independent drop re-derivation.** For every `dropped` record, RE-derive its signals from
   the source (recompute `ref` from the rels; recompute `n_docs`/`occ` from the union of all
   records' shas) and **hard-fail any drop whose signals don't converge**, with extra scrutiny
   on the formula class (small + unique + body + emf/wmf that was dropped). A mis-gated formula
   ⇒ FAIL, not a silent pass.
4. **Placeholder resolution.** Every `captured`/`kept-neutral-alt` figure's sentinel is
   replaced and its `![alt](rel)` asset file EXISTS on disk; any leftover sentinel or orphan
   `![](assets/…)` whose asset is missing ⇒ FAIL.
5. **Caption quality (scoped).** For `captured` records only, re-run `caption_is_useful` on
   the ACTUAL inlined caption (joined via `rel_path`), and require it be non-empty AND
   byte-equal to the `_captions.jsonl` entry for its `sha`.
6. **Pending / incomplete.** Any `pending` (`UNAVAILABLE`) record ⇒ the doc is **INCOMPLETE**,
   excluded from `lossless`, and forces exit non-zero — distinguishing "not yet done" from
   "done". `RENDER_FAILED`/`media-missing` count as **LOSS** (potential formula text gone),
   not clean coverage.
7. **Opt-in scoping.** A per-doc `captions_enabled` marker: accountability and
   "unresolved-sentinel = FAIL" run only where captioning was enabled; a captions-OFF doc with
   source images but no records is ACCOUNTED (deterministic placeholders only), not a FAIL. A
   captions-ON doc whose source has images but has zero records is a hard FAIL (closes the
   empty-artifacts vacuous-pass trap).

Own report type (`FigureAudit`: `n_source_images, n_captured, n_kept, n_dropped, n_pending,
n_unaccounted, n_loss, lossless`) and its **own non-zero exit** — it is a GATE, not a
reporter (unlike `coverage_report.py`). `--explain` lists offending figures.

## Config knobs (`[ingest]`, wired into `_config.py` + `default.example.toml`)

```
enable_captions      = false      # master switch (exists)
vlm_max_tokens       = 8192       # caption/OCR cap (exists; wired + warns on finish_reason=length)
caption_max_pixels   = 2458624    # ~1568^2 longest-edge downscale target
caption_max_bytes    = 12000000   # skip (too-large) above this even after downscale
# NOTE: no furniture_min_docs / icon thresholds — recurrence/size never drop (see §formula-safe)
```

## Implementation status (2026-07-07)

**Built + TDD-tested (348 tests green; live-smoke against the real VLM passing):**
- Pure policy (`backend.ingest`): formula-safe `gate_figures` (None-safe, chrome, recurrence-
  never-drops), `caption_type_is_furniture`, `caption_cache_key`, `cache_last_wins`,
  `figure_sentinel`/`inline_figures`, extended `image_markdown` escaping + `FIG_GATED_CHROME`;
  `sniff_image_format`/`image_dimensions` (`_imageprobe.py`); `resolve_media_refs`/`is_body_part`
  (`_media.py`).
- Shared caption tool `scripts/image_caption.py` (Outcome kinds, `prepare_png` probe/render/size,
  `CaptionCache`, `caption_image`) + `VlmClient.caption_result` (transport-vs-useless).
- Enrichment pass `scripts/image_enrich.py` (office lane: extract → gate → caption → append,
  idempotent, corpus index).
- Second-pass validator `scripts/validate_figures.py` (independent byte-magic ground truth,
  own non-zero exit; proven to catch unaccounted/orphan/pending/mis-gated).
- Config knobs wired (`caption_max_pixels`/`caption_max_bytes`).

**Scoped follow-ups (not yet built):** the docling refactor onto the shared tool (docling is
py3.12-only and not installed in this env, so untestable here — its current inline path keeps
working); `ROUTE_IMAGE` for standalone image files (corpus has none today); LibreOffice/legacy
office media (needs the soffice pre-convert sibling); the full captioning batch run.

## Rollout

1. **Deterministic, no model (safe now):** SVG text (shipped) + office media asset dump +
   id-sentinel placeholders + `_figures.jsonl` candidate records with `captions_enabled=false`.
2. Caption tool + cache + global enrichment, opt-in; refactor docling onto the shared tool.
3. `ROUTE_IMAGE` for standalone images.
4. Run the validator; then a captioning batch (resumable via cache), then re-validate.

Text lanes stay at recall 1.0 throughout — captions are additive and never enter the text gate.

## Test plan (TDD)

- **Unit (pure, py3.6):** `gate_figures` truth table incl. **`test_formula_image_is_never_gated_out`**
  (1.5 KB unique body EMF/PNG, `area=None`, `n_docs=5` ⇒ keep — proves None-safety AND
  recurrence-never-drops); deny-class + AREA_READMIT; tiny; chrome body-wins; within-doc dup;
  `caption_type_is_furniture` (formula caption ⇒ False, "This is a company logo…" ⇒ True);
  `caption_is_useful`; `image_markdown` escaping (`)`,`|`,backtick,newline,sentinel) +
  `inline_image_captions` id-sentinel round-trip + **idempotent re-run**; `caption_cache_key`
  determinism + `cache_merge` last-wins; extended `figure_outcome`/`figure_coverage` reasons.
- **Integration:** office media classify (ref body-wins from rels; dangling r:embed ⇒
  media-missing); caption tool with a **mock** VLM (cache hit skips call; UNAVAILABLE not
  cached + pending; UNDECODABLE on garbage bytes; FURNITURE dropped; truncated flagged);
  metafile render path with a stub soffice (RENDER_FAILED on failure).
- **E2E:** tiny corpus — a docx with a logo reused in 3 docs + a unique diagram + a **small
  unique formula image** + a standalone .png — through extract→gate→caption(mock)→inline→
  **validate_figures**: assert logo captioned-once-then-FURNITURE-dropped, diagram+formula
  captured, standalone captured, validator `lossless` & exit 0. Then (a) delete one asset →
  validator FAILs orphan; (b) blank one caption → FAILs quality; (c) mark one UNAVAILABLE →
  INCOMPLETE non-zero; (d) hand-drop a formula with reason=chrome whose source ref is body →
  drop-re-derivation FAILs. Proves the validator MEASURES.
- **Live smoke:** a handful of real corpus images through the actual VLM server.
