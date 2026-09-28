---
title: Roadmap — PDF fidelity, SDK, keel compliance
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [roadmap, pdf, docling, sdk, keel]
summary: The living plan toward docs/end-goal.md — sequenced milestones broken into vertical slices an execution loop can work through.
---

# Roadmap

Serves `docs/end-goal.md`. This document plans a properly implemented,
maximally lossless PDF lane; doc2md as an SDK; keel-template compliance.

**Correction (2026-08-21): the office lane is not a closed chapter.** It was
recorded here as closed on `token_recall == 1.0, 544/544`, which remains true
and unweakened — but list nesting, emphasis and code fences are not tokens, so
that gate is structurally blind to them. A live run proved a nested numbered
procedure comes out **renumbered**. `end-goal.md` §1 already binds us ("structure
**is** content"); the gate never implemented it. The chapter reopens to *widen*
the gate, never to relax it — see **[`quality-plan.md`](quality-plan.md)**, which
owns output quality (body fidelity, run provenance, the outline, metadata) and
its own A-grade rubric. Changes to shared metric code (`tokenize`, `coverage`)
still re-run the office corpus before merge.

## How to execute (the loop)

Each run works **one vertical slice** end to end — ralph-style, this file is
the persistent plan state:

1. Pick the topmost unchecked slice whose milestone prerequisites are met.
   The two chains below are independent: once M0 is done, M5→M6 slices may
   interleave with M1–M4 rather than waiting for them.
2. **Eval/test first (TDD):** add the corpus fixture + `expectations.json`
   probe and/or the failing mirrored unit test that defines the slice's
   "done". For *fidelity* slices, fixtures precede features — a conversion
   improvement that moves no measured number doesn't exist. Infra slices
   (pins, renames, packaging) instead state their own done-check.
3. Implement. Policy in `src/` (3.6 + stdlib for `backend.*`), orchestration
   in `scripts/`, heavy deps only behind `DOC2MD_PDF_PYTHON`.
4. Gates green: pytest on 3.6 + 3.12 rings, `evals/run_eval.py`, and (once M6
   lands) `make verify`. The gate is the judge of done, not the diff.
5. PR with independent review; adjudicate findings; squash-merge; tick the
   box here (same PR).
6. Stop. Next run repeats from 1 with fresh context.

Bounded passes: if a slice doesn't converge in a run, split it here rather
than pushing a half-slice.

## Sequencing

```
M0 ground the loop ─► M1 trustworthy measurement ─► M2 structure ─► M3 tables ─► M4 VLM transposition
        └────────────► M5 SDK rename/packaging ─► M6 keel compliance          (independent chain)
```

Measurement (M1) precedes improvement (M2–M4) on purpose: every feature must
land against a gate that already can't be fooled. The `backend` → `doc2md`
rename (M5) precedes the keel frontmatter/labeling sweep (M6) so the sweep
isn't done twice.

---

## M0 — Ground the loop (make the signals real)

Context: the `eval-pdf` CI ring (2026-07-16 dispatch + 2026-07-17 scheduled
nightly, byte-identical results): **19 pass, 2 fail** —
`pdf/kestrel-clock-spec.pdf` `toc_lines 1 < 9` (docling emits the whole TOC,
dot leaders intact, as one merged line, which the line-anchored TOC matcher
can count only once) and `pdf/kestrel-dataflow.pdf` expected-degraded but
converted `ok` (OCR-routed, but no picture placeholder is detected so
nothing degrades). The local ring (stood up 2026-07-17, see the dated
baseline in `evals/README.md`) reproduces both failures character for
character. The `docling` extra and the model weights are pinned as of
2026-07-17 (docling 2.113.0 / docling-core 2.87.1 / rapidocr 3.9.1; HF
commits + RapidOCR sha256s via `scripts/prefetch_docling_models.py` +
`DOCLING_ARTIFACTS_PATH`). Residual float, named: docling-ibm-models,
docling-parse, torch and transformers still resolve within docling's ranges —
a constraints file is a candidate follow-up if variance recurs.

- [x] Stand up the local PDF ring: `uv venv --python 3.12` + `pip install -e
      '.[docling]'`, set `DOC2MD_PDF_PYTHON`, run the full eval locally,
      record the baseline (pass/fail/skip counts + per-doc recalls) as a
      dated table in `evals/README.md` so the next fresh run can diff it.
      *(2026-07-17: 19 pass / 2 fail / 0 skip — reproduces both CI runs
      (dispatch + scheduled nightly) character for character; see the dated
      baseline in `evals/README.md`, including the `TORCHDYNAMO_DISABLE=1`
      and modelscope-flakiness footguns.)*
- [x] Pin the PDF toolchain: exact `docling`/`docling-core` pins in the
      extra; prefetch model artifacts (`artifacts_path` /
      `DOCLING_ARTIFACTS_PATH`, `docling-tools models download`) so CI and
      local run the same models; stamp docling + poppler versions into
      report warnings (the soffice version already is).
      *(2026-07-17: pins in pyproject; `scripts/prefetch_docling_models.py`
      pins the model WEIGHTS at exact HF commits + sha256-verified RapidOCR
      files — `docling-tools models download` was rejected: it refetches
      floating `main` revisions, the very drift to kill. Hermeticity proven
      offline; `pdf_toolchain` warning stamped + eval-asserted; CI caches
      the artifacts. Eval unchanged at 19/2/0.)*
- [x] Give the eval harness an expected-fail mechanism: an `xfail: true` +
      `_note` marker in `expectations.json` that `run_eval.py` reports as
      XFAIL (and XPASS as a failure to re-encode), so truthful-but-undesired
      pinned behavior doesn't leave the nightly permanently red and useless.
      *(2026-09-25: two rules keep it honest — an `xfail` with no `_note` is a
      FAIL, because a marker nobody can review is how a defect becomes
      permanent; and an XPASS gates, so a marker cannot outlive the defect it
      describes. A stray-key typo is still a FAIL under the marker: `xfail`
      says the DOCUMENT behaves undesirably, not that the EXPECTATION is well
      formed. Verified both directions on the real corpus.)*
- [x] Re-encode the two failing pdf expectations truthfully for the pinned
      toolchain (dataflow's knife-edge probe gets its real fix in M1).
      *(2026-09-25: they needed DIFFERENT remedies, which is what made the pair
      worth doing together.*
      *`kestrel-clock-spec.pdf` — `toc_lines: got 1, want >= 9`. Docling emits
      the whole Table of Contents as one MERGED line, so 1 is the truth. The
      floor is re-encoded to 1 with a `_note`; an `xfail` would have switched
      off twenty live checks on an otherwise healthy document (recall 0.9925,
      images pass over 3) to silence one. The .docx of the same document still
      meets 9, which is how we know it is the PDF lane's defect and not the
      fixture's. Real fix: TOC-shape robustness in `is_toc_line`, M2.*
      *`kestrel-dataflow.pdf` — this one got WORSE in a way that made the gates
      read BETTER, and that is why it is the `xfail`. Docling now detects no
      picture at all, so nothing bails: `images.gate` reads `pass` over ZERO
      images and `status` reads `ok`, over a diagram-only PDF reduced to one
      line of 19 tokens with 0 headings and 0 images. Encoding that as expected
      would make the eval assert a destroyed document is fine, so the row keeps
      pinning the older HONEST outcome (degraded, with the bail warnings) and
      carries the marker. Nightly: `24 pass, 0 fail, 0 skip (1 xfail, 0 xpass)`,
      exit 0 — green for the first time since it was pinned.)*
- [x] Wire `explain_gap`/`GapReport` (built and exported from
      `backend.ingest`, currently uncalled outside its tests) into the pdf
      `losslessness` block: bucket counts + `absent_top`, so every report
      says *why* recall < 1.0.
      *(2026-09-25: `_pdf_losslessness` now takes the RAW page-delimited text
      rather than a de-boilerplated one and strips it itself — `explain_gap`
      applies the strip internally so it can ALSO see the sub-threshold
      repeated lines, and handing it pre-stripped text makes `residual_boiler`
      read 0 and re-counts every one of those tokens as `absent`, overstating
      loss on exactly the documents the block exists to explain. Taking the raw
      text as the parameter rather than an optional extra is what makes that
      unable to happen.*
      *Measured on the pinned toolchain, `pdf/kestrel-clock-spec.pdf`:
      `recall 0.9925` decomposes to `n_source 420, covered 402, numeric 6,
      residual_boiler 12, absent 0` — the whole shortfall is page numbers and
      a running footer, and nothing is unaccounted for. The buckets partition
      the source, which is asserted.*
      *Published is not the same as checked — `explain_gap` was built,
      exported and called by nothing for two milestones, and a field nobody
      reads is the same shape. `gap_absent_max` gates it, and is pinned at 0 on
      that document: a far stronger statement than a `token_recall_min` floor,
      which tolerates real loss as long as there is little of it. Asking about
      a gap a report does not carry is a FAIL, never a silent pass — the OCR
      path publishes no block, having no text layer to decompose.)*

## M1 — Trustworthy measurement (self-validation hardening)

Context: the converter-blind gate is ~80% built — `_pdf_losslessness` grades
docling's markdown against `pdftotext` (shares no code with docling), with
`strip_running_lines` + digit-masked repetition handling furniture and a
two-signal loss verdict (`token_recall < 0.80` AND `content_recall < 0.95`).
Remaining holes: no NFKC/ligature fold (a residual `ﬁ` fakes loss), the
exclusion set partly trusts docling's own picture bboxes (self-grading), the
OCR path measures nothing, and a diagram-only digital PDF misroutes to OCR.

- [x] Move the gate policy into `src/`: `_pdf_losslessness` →
      `backend.validate.pdf_coverage_report` (pure strings-in/dict-out, 3.6 +
      stdlib; poppler subprocess calls stay in the script), with a mirrored
      `tests/unit/backend/` file — *before* any policy change, so the office
      coercion invariant (`gate` never `pass` off-lane) is test-protected.
      *(2026-09-26: done. The script keeps a 3-line adapter that unpacks the
      config; the validator REFUSES to default its four thresholds, so they
      cannot drift from `ingest.toml`, and the integration test moves each one
      alone to prove the argument order. The move also corrected an overstated
      claim about the raw-text signature: pre-stripping at the same threshold is
      a no-op, so the real teeth are a caller stripping at its OWN threshold —
      measured at 0.77 recall against the document and a flattering 1.00 against
      a pre-stripped copy of it.)*
- [x] NFKC + ligature normalization applied symmetrically to both sides —
      as a **pdf-lane-only entry point**, not inside the shared `tokenize`
      (office 1.0 gate untouched; if it ever moves into shared code, re-run
      the full office corpus first).
      *(2026-09-26: `backend.ingest.normalize_pdf_text` — NFKC plus the
      invisible marks NFKC does NOT touch (soft hyphen, ZW\*, word joiner,
      BOM), which split a word for the ASCII tokenizer exactly as a ligature
      does. Applied inside `pdf_coverage_report` to source, markdown,
      furniture and figure text alike, so the symmetry is structural rather
      than a caller's discipline. The office `tokenize` is untouched and a
      test asserts it still reads `con\ufb01dential` as two fragments.
      CAVEAT, measured: no document in the corpus contains a ligature or an
      invisible mark, so the fold is a no-op on all 17 and is proven at unit
      level only. The asymmetry it fixes — poppler returning the font's glyph
      where docling returns the letters — needs a PDF whose font actually
      ligates, which LibreOffice does not produce deterministically from the
      synthetic sources. That is the "hyphenation + ligature doc" stress
      fixture below, and it is a harder fixture than it reads.)*
- [x] Make the exclusion set converter-blind at convert time: use
      `_pdf_drawn_boxes` (pypdfium2, the PDF's own drawing objects) instead
      of docling's `_picture_boxes` for figure-region text — removes the last
      docling-judges-docling input from the measurement. pypdfium2 stays in
      the PDF-lane script; only its box *output* crosses into the 3.6-stdlib
      validate function.
      *(2026-09-26: done, but NOT as "instead of" — measurement said that
      would have been a downgrade. On `pdf/kestrel-clock-spec.pdf` the
      register map's ruling lines form a path cluster, so `_pdf_drawn_boxes`
      claims the whole table and 55 tokens of real body text would leave the
      ground truth with it: swapping one detector for the other trades a
      circular exclusion for an over-wide one. Implemented instead as
      `backend.ingest.intersect_boxes` + `dc._figure_regions`: a region is a
      figure only where BOTH detectors agree, and only over their overlap, so
      each can merely SHRINK the exclusion and any disagreement leaves the
      text counting against the converter. Docling's claim is no longer
      SUFFICIENT to excuse anything, which is the part that mattered.
      Numerically a no-op on the corpus (clock-spec's agreed regions hold no
      text, so `figure_text_tokens` was already 0) — this is a guarantee, not
      a fix, and it is proven by injecting a full-page docling picture claim
      and showing it buys no exclusion. Two gaps left, both narrower than the
      original: the HTML lane has no drawing objects to consult, so its
      exclusion stays circular; and a measure-only sweep has no docling boxes
      to agree with, so it uses the independent detector alone — not
      circular, but over-wide, so a swept document reads slightly kinder than
      the same document at convert time.)*
- [x] Fix the OCR routing: area-weighted text-layer probe (a diagram-only
      digital PDF must not trip full-doc OCR).
      *(2026-09-26: `pdf_has_text_layer` now uses TWO signals. Clearing
      `min_chars_per_page` in every window is digital on the spot and the
      expensive step never runs; a THIN window is judged by the PDF's own
      objects (`_page_raster_fracs`) — a page whose content is a raster
      covering >= `scan_cover_min` needs OCR whatever text it also carries (a
      scan with a burnt-in header stamp), while a page thin because it is
      mostly vector art is digital. Measured: dataflow 79 chars/page and 12
      vector paths, no raster -> digital; the scan 0 chars and a page-sized
      raster on every page -> OCR; clock-spec unchanged. A missing pypdfium2
      is NO evidence rather than evidence of absence, so it keeps the old
      OCR verdict. `pdf/kestrel-dataflow.pdf` now converts through the
      digital path and its real text layer is measured — but it stays XFAIL,
      because the routing bug was not its only one: see below.)*
- [ ] Extract figures on the OCR path (today: none — placeholders bail and
      the images gate degrades spuriously; also a hard prerequisite for M4).
      *(2026-09-26, widened by measurement: the OCR path is no longer the only
      gap. `pdf/kestrel-dataflow.pdf` now routes correctly to the DIGITAL path
      and docling's layout model still finds no picture at all in a vector-only
      page — 0 images, 0 headings, the whole document 19 tokens of the
      diagram's labels on one line. A floor may have to come from the PDF's own
      drawing clusters rather than from docling.)*

- [x] Stop `images.gate` reading `pass` over ZERO images.
      *(2026-09-27: `images.source_images` is the denominator, and the gate has
      three states like the losslessness one — source known to hold 0 is
      `pass`, known to hold more than arrived is `degraded`, unknown is
      `unmeasured` and degrades nothing. An EXCESS is never loss, since one
      picture can be referenced twice and dedupe to one file. The PDF lane
      supplies it on the digital path from `drawn_image_floor` over the PDF's
      own drawing objects — a LOWER bound, at most one per page, because the
      dataflow diagram is five two-path rectangles and a floor that counted
      parts would accuse a correct conversion. `pdf/kestrel-dataflow.pdf` now
      reads `source_images: 1, gate: degraded, status: degraded`. Its exclusion
      detector could not supply this: at `image_region_min_paths` 10 every one
      of those clusters is rejected, correctly, because a two-path cluster must
      not EXCUSE text sitting over it — counting figures is a weaker question
      than excusing text and gets a weaker threshold (area >= 5% of a page).)*

- [ ] Give the OFFICE lane a converter-blind picture count. Eight office
      documents now read `images.gate: unmeasured` because their only
      available count comes from `ooxml_image_parts`, which reads the
      CONVERTER's own sentinels — circular, so it cannot be the denominator. A
      raw `word/media/*` count is not it either: it includes header, footer and
      theme images the converter deliberately drops, so it would report loss on
      a correct conversion. The honest count is body-part `<a:blip>` /
      `<pic:pic>` references taken from the source XML on the ground-truth
      side. Until then `unmeasured` is the truthful reading and degrades
      nothing; five eval rows carry a `_note` saying so.

- [ ] Count a scanned page's raster as a figure. `_pdf_drawn_area_fracs` drops
      page-covering objects as frames, so a scan reads 0 drawn area — right for
      a white background rect, wrong for the page image that IS the content. The
      OCR path therefore passes `None` rather than a floor of 0, so it reads
      `unmeasured` instead of claiming the source held no pictures. Closes
      together with "extract figures on the OCR path".
- [~] Stress fixtures, before the features that fix them.
      *(2026-09-27: the HYPHENATION + LIGATURE fixture has landed as
      `office/kestrel-ligature.docx` and its derived `pdf/kestrel-ligature.pdf`,
      and it pins two things at once. (1) The fold WORKS on a real document:
      measured with `normalize_pdf_text` removed the PDF scores 0.7976 over 84
      source tokens, with it 0.9041 over 73 — so without the fold this document
      sits BELOW `min_recall` and reports content loss it did not suffer. Every
      U+FB01/U+FB02 word is covered. (2) A real docling defect is now pinned
      instead of invisible: the 7 tokens still absent are exactly the words the
      source spells with U+FB00 (ff) or U+FB03 (ffi) — docling drops or
      MISPLACES those glyphs, emitting `o set flow` and `hyphen ff ation` where
      the source reads `offset flow` and `hyphenation`, while fi/fl come
      through. `gap_absent_max: 7` is a CEILING on a known bug, not a target.
      The office half of the same document is deliberately boring at recall 1.0
      — both its sides read the same OOXML, so a ligature is invisible to that
      gate, and `md_contains` asserts the glyphs survive VERBATIM because
      folding them there would be a silent rewrite of the document's own
      characters. Corpus generation stays byte-identical on 3.6.8 and 3.12.
      NOTE, measured: LibreOffice does not auto-ligate, so the font-substitution
      asymmetry (poppler returning a glyph where docling returns letters) cannot
      be manufactured from a synthetic source — the literal glyphs are the part
      that is deterministic on any host.)*

      *(2026-09-28: the LABELLED FIGURE fixture has landed as
      `office/kestrel-clocktree.pptx` + derived `pdf/kestrel-clocktree.pdf`,
      and it is the first document in this corpus with a real figure-text
      debt. Measured: `figure_text_tokens: 19`, `gap.image_text: 19`,
      `absent: 0` — the whole source accounted for and ALL of it trapped in the
      figure, while the office lane reads the same 12 labels as ordinary list
      text at recall 1.0. Same words, two lanes, two correct-but-opposite
      answers, which is what makes the debt falsifiable. It had to be DENSE:
      `_pdf_drawn_boxes` keeps a merged cluster only at
      `image_region_min_paths` (10) or more, so the dataflow deck's five
      far-apart boxes each stay a rejected two-path cluster while these twelve,
      packed edge to edge, merge into one cluster of twenty-four. New eval
      probe `figure_text_tokens_min` gates it as a FLOOR: a FALL means the
      figure-region probe stopped seeing a figure, silently returning those
      words to the body ground truth to be judged as converter loss.)*

      Still open: a per-page-varying footer ("Page 3 of 120"), a non-dot-leader
      TOC, and a multi-column reading-order fixture (that one *encodes measured
      truth* — docling's reading-order model owns the fix; if it falls short
      it's an xfail with a `_note`, not a slice here).

- [ ] Make `caption_is_useful` mean what a reader assumes. Found by running
      the caption path with Claude as the model, looking at each figure and
      writing an honest caption: every corpus image is a decorative colour
      grid, the captions said so explicitly ("no labels, axes, connectors or
      text of any kind"), and the run reported **`useful=17 (100%)`,
      `useless=0`**. The function is honest about what it does — a SHAPE check
      for length, letter ratio and runaway repetition, built against a CPU
      VLM's degenerate output — but `useful` then means "the model answered in
      well-formed prose", while the caption gate reads as "the figure was
      recovered". Same vacuity family as `token_recall` over no tokens and
      `images.gate` over no images.
      The fix is NOT a keyword hack for "decorative": that is a semantic
      judgement a word list will get wrong in both directions. `end-goal.md`
      §4 already names the real check — captions "precision-checked against
      the text layer's region words, burning down `figure_text_tokens`" —
      which is objective, uses evidence the PDF lane already extracts
      (`_image_region_text`), and answers the question that matters: did the
      caption bring back the words trapped in the figure? It needs the fixture
      above to be measurable, so it is sequenced after it.
- [ ] HTML lane coverage: a ground truth exists (`_source_text` uses
      `html_to_text`, independent of docling's HTML backend) but nothing
      exercises it — no HTML fixture in the eval corpus, and the
      figure-region exclusion path is PDF-only. Add an HTML fixture +
      probes; verify the exclusion semantics honestly for HTML.

## M2 — Structure: proper Markdown headers back from PDF

Context: docling's layout model only *labels* section headers; historically
everything exported flat. Since ~v2.109 (July 2026) `PdfPipelineOptions.
heading_hierarchy_options` infers real levels — precedence: PDF bookmarks →
heading numbering → font size/style — but it is **off by default**. This is
the single biggest structural win available, and it needs the M0 pin
(≥ 2.109) to exist at all.

- [ ] Enable + verify `heading_hierarchy_options` on a live install (confirm
      sub-flag defaults: `use_bookmarks`, `use_numbering`, `use_style`,
      `max_level`; font-size fallback needs `generate_parsed_pages=True`).
- [ ] Corpus first: a bookmark-bearing PDF fixture (LibreOffice's PDF export
      can emit the outline from Writer headings — verify) and a
      numbered-headings fixture; then pdf `max_depth` / `outline_titles`
      probes, which today aren't trusted enough to assert at all.
- [ ] TOC-shape robustness in `is_toc_line`/`content_start` (merged
      single-line TOCs — the observed shape behind nightly failure #1, whose
      end-anchored leader pattern can match a physical line only once —
      plus spaced/absent dot leaders, tab leaders, table-form TOCs).
- [ ] Hybrid overlay fallback: replace today's all-or-nothing text-layer
      fallback (`md = src_stripped`, figures zeroed) with a merge — docling's
      heading lines + image placeholders overlaid on the pdftotext stream.
      Pure helper in `backend.ingest`; anchor alignment must normalize
      hyphenation/ligatures and tolerate multi-column linearization; a
      misplaced anchor must surface as `outline_coverage` failure, not pass
      silently.
      **Tautology trap (design constraint):** once pdftotext supplies the
      body, token-recall-vs-pdftotext is ~1.0 by construction and stops
      informing. The report must carry body-source provenance
      (`docling | text-layer | hybrid`), and quality then rides on the
      structure metrics + the docling-vs-layer disagreement it fell back
      over — never on the vacuous recall alone.

## M3 — Tables

Context: TableFormer (v2 since docling 2.78) infers table structure; known
failure modes: merged/borderless-column pathologies, flattened multi-row
headers (a Markdown-grid limitation as much as a model one), and — worst for
us — **dropped cell text**. `repair_split_tokens` already repairs
wrapped-cell splits provably against the raw layer.

Recorded deviation from the original ask ("tables the same way as images —
via VLM"): tables that exist *natively* in a digital PDF go deterministic
(TableFormer + text-layer cell matching = exact glyphs, measurable against
the layer) — strictly better than a VLM for that class. VLM transposition
(M4) applies to tables-as-images and diagrams, where no text layer exists.

- [ ] Table fixtures first: borderless, merged-cell/rowspan, multi-row
      header tables in `gen_corpus.py`, expectations encoding measured truth.
- [ ] Mode policy: `TableFormerMode.ACCURATE` + `do_cell_matching=True`
      (cell text taken from the PDF text layer = exact glyphs), with the
      documented `do_cell_matching=False` fallback for merged-column cases.
- [ ] A table-scoped content check: recall of the text-layer words inside
      each table's bbox against the emitted table — catches dropped cells
      specifically, not diluted across the whole page.
- [ ] Span fidelity decision (output-contract): per-table HTML island in the
      Markdown where a pipe table can't represent spans, vs. accept
      flattening and record it. Decide once, in the contract.

## M4 — Figures & vector diagrams: VLM transposition

Context: text inside figures is tracked as `figure_text_tokens` debt —
"exactly what the VLM caption stage exists to bring back". The scaffolding
exists (`vlm_client.py`, `caption_bundles.py`, `_make_vlm_ocr_converter`
with a verbatim-GFM prompt at temperature 0 against the local llama-server)
but isn't wired into the bundle writer. Vector text drawn as curves is
invisible to both the text layer and OCR — the VLM is the *only* recovery
path for that class.

- [ ] Contract decision first: where does transposed content live? Captions
      today sit in `structure.json`/report only — invisible to a Markdown
      consumer. If a transposed table/diagram belongs in `document.md` (it
      does, per the end goal), that's an output-contract change to design
      deliberately (e.g. fenced transcription block after the image ref).
- [ ] Wire the VLM-OCR converter into `build_pdf_bundle.py`'s scan path
      (fixes RapidOCR's dropped shape labels; needs M1's OCR-path figure
      extraction).
- [ ] Per-figure transposition in the caption stage: prompt for faithful
      markdown transcription of diagrams/tables-as-images; content-addressed
      cache (image sha + prompt + model) as today.
- [ ] **Hallucination gate:** recall is blind to insertions — free text is
      fine for a converter but fatal for a generative model. Score VLM output
      against the figure region's own text-layer words
      (`_image_region_text`): low precision ⇒ reject/flag the transposition.
      No unverifiable VLM text enters `document.md` unmarked.
- [ ] CI story: runners have no llama-server. Options: local cron ring on
      this host (real corpus), recorded VLM responses as fixtures for CI, or
      skip-with-SKIP-row. Choose; never a silent pass.

## M5 — SDK packaging

Context: `pyproject.toml` exists (name `doc2md`, src-layout, stdlib-free
core, `docling` extra already gated on `python_version >= '3.9'`) but the
wheel would ship a top-level package literally named **`backend`** — a
collision hazard and wrong public name. Scripts find `src/` via `sys.path`
hacks and import each other, so no console entry point is possible without
promotion. The 3.6 floor is a *source-compatibility* constraint of the bare
host (which runs from a checkout and can't pip-install modern wheels anyway)
— CI ring 1 enforces it; the wheel doesn't have to.

- [ ] Rename `src/backend/` → `src/doc2md/` (~74 import statements as of
      2026-07-16 — recount at execution, M1–M4 add more;
      scoped rewrite of import patterns only —
      "backend" is also a domain word here; plus `tests/unit/backend/` →
      `tests/unit/doc2md/` and doc sweeps). Keep a thin `backend` shim
      re-exporting from `doc2md` for the deployed checkout, excluded from the
      wheel. **Do this before M6's frontmatter sweep.**
- [ ] Promote stranded domain logic from `scripts/` into `src/`:
      `bundle_inputs` (self-described "SINGLE source of the losslessness
      guards"), part/media loaders, the writer helpers
      (`_write_atomic`/`_verify_images`/`_gc_orphans`/`_carry_captions`),
      `vlm_client` — each with mirrored unit tests; scripts become thin
      argparse shims (root rule finally holds).
- [ ] Facade: `doc2md.convert(path, ...) -> Bundle`, `doc2md.convert_tree`,
      `Bundle.markdown/.structure/.report/.images/.status/.write()` —
      **preserving withhold-on-gate-fail** (a facade that publishes markdown
      the script lane would withhold is a contract regression). PDF path
      lazy-imports docling and raises with an actionable
      `pip install 'doc2md[pdf]'` message.
- [ ] Console script `doc2md` (`convert`, `validate`, `caption`,
      `setup-libreoffice`); move `config/settings.py` under `src/doc2md/`
      (currently outside the wheel — installed consumers silently lose
      tokenizer wiring today).
- [ ] Extras: `pdf` (alias `docling`), `tokenizers`, `dev`; **decide the
      floor once** (recommendation: wheel `requires-python = ">=3.9"`, 3.6
      stays a CI-enforced source constraint — this couples with M6's
      `config/project.json`, whose `backend.python` must match pyproject);
      replace the placeholder LICENSE (release blocker — the TODO text is
      already baked into built metadata).
- [ ] Ship `config/vocab.yaml` as package data (or an importable default).
      `backend.kb.vocab_path()` resolves it four directories up from the
      module, which is the repo root in a checkout and site-packages in a
      wheel — so an installed `load_vocab()` fails unless `$DOC2MD_VOCAB`
      is set. It fails loud, not silently, but the default is broken
      off-tree and packaging is the right place to fix it.

## M6 — Keel compliance

Context: doc2md's CONVENTIONS.md is derived from an older keel revision and
§6 promises enforcement that doesn't exist here (no `Makefile`, no
`check_structure.py`, no pre-commit). Keel's thesis is "checked, not just
documented". Backend-only instantiation is first-class in keel
(`frontend_stack: none`, `transports: []`) — absence is compliant when
`config/project.json` records it.

- [ ] Makefile with keel's target vocabulary adapted to the two-interpreter
      reality (`PY` for the 3.6-safe gate lane, `PDF_PY` from
      `DOC2MD_PDF_PYTHON`); `make verify` = structure check + lint +
      typecheck + tests = the done-gate; CI calls make targets instead of
      raw pytest.
- [ ] Port `check_structure.py` (it's deliberately 3.6-safe in keel) and
      run the frontmatter corpus-key sweep it checks: add `id`, `created`,
      `updated`, `visibility`, `canonical` to every labeled doc — **after**
      the M5 rename so `public_api:` paths and `tests/unit/doc2md/` are final.
- [ ] Adopt the `AGENT.md`-canonical scheme: rename each `CLAUDE.md` →
      `AGENT.md` + `CLAUDE.md` symlink (keel `check_I`).
- [ ] `config/project.json` facts manifest (`backend.python` matching
      pyproject — coupled to the M5 floor decision); resolve `config/`'s
      code-in-config deviation (`settings.py` moves in M5; what remains is
      data) or document it as an owned exception.
- [ ] Fix labeling gaps against our own rule: `docs/`, `src/`, `tests/`,
      `src/backend/` lack `README.md`; `docs/design/` lacks both; declare
      `data/` and `vendor/` in the CONVENTIONS §2 table (currently outside
      the taxonomy entirely).
- [ ] Refresh CONVENTIONS.md from current keel (corpus-core keys, `tool`
      kind, §7–§18 doctrine) and adopt ruff/mypy policy with 3.6 adaptations
      (drop `FA`/`FURB` families — they push post-3.6 idioms; record the
      pruning in `config/practices.json` so ruleset parity is honest).
- [ ] `test-docs/` (strategy + coverage register) and resolve the two
      permanently-deselected `test_coverage_flow.py` tests (restore the
      never-committed `scripts/coverage_report.py` or delete them — an
      unfinished slice either way).
- [ ] Housekeeping: `CHANGELOG.md`, commit `.claude/`, optional hand-written
      `.copier-answers.yml` (`backend_python: ">=3.6"`, `frontend_stack:
      none`, `transports: []`) to enable future `copier update` — review
      diffs, don't auto-accept (keel's jinja defaults assume ≥3.10).

---

## Open decisions (decide once, record in the contract)

| Decision | Options | Leaning |
|----------|---------|---------|
| PDF gate end-state | permanent `best-effort` vs a new `text-verified` value when recall clears a hard floor | keep `best-effort` + rich measured block until M1–M2 numbers justify a stronger claim |
| Wheel python floor | `>=3.6` (true but useless — no 3.6 consumer can install a modern wheel) vs `>=3.9` | `>=3.9`; 3.6 remains CI-enforced for the checkout host |
| Transposed figure content | caption-only (invisible to md consumers) vs inlined into `document.md` | inline, marked as VLM-derived, hallucination-gated (M4) |
| Table spans | HTML islands vs flattened pipes + recorded loss | decide with M3 fixtures in hand |
| `schema_version` in bundle files | parked earlier — revisit when the SDK (M5) freezes the contract for external consumers | park until M5, but note the METADATA block now carries its own `schema_version` + `vocab_version` (they invalidate different work); a bundle-wide version is still open |
| Metadata registry seeding | seed `tags`/`topics`/`audience` from a first corpus pass vs ship empty and promote at >= 3 documents | ship empty — seeding guesses the corpus shape at document zero, which is what the registry regime exists to avoid |
| Entity spelling collisions | ERROR (two graph nodes for one thing) vs WARN (two real paths can differ only in punctuation) | ERROR, with `lint.similar_ok` as the recorded escape hatch — an un-silenceable false positive would fail every build with nothing a person could do about it |
| Corpus-wide cardinality | re-grade `distinct/used` across the corpus vs measure the mirror question | measure the mirror: every graded facet is closed-governed, so membership is already the stricter test. `vocabulary_usage` reports terms no document draws on instead |
| Where the knowledge payload lives | one `meta:` block in front matter vs a `knowledge.json` sidecar | **split (schema v2)** — measured ~5:1 knowledge:descriptors on a real document, so front matter charged every body-only consumer for a relation table it discards. Every other derived artefact in the bundle was already a sibling JSON file; this was the only one that was not |
| Seam between descriptor and knowledge | curate a hand-picked field list vs derive it from the inventory | derive it: `records`/`groups` and not `authored_only`. Mechanical so it cannot drift into taste, with one carve-out — `accountable_roles` is a record list but an accountability statement, so it stays with the document |
