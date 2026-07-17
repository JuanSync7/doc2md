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

Serves `docs/end-goal.md`. The office lane is a **closed chapter** (recall ==
1.0, 544/544): no new work there except regressions the eval catches, and any
change to shared metric code (`tokenize`, `coverage`) re-runs the office
corpus before merge. This document plans the rest: a properly implemented,
maximally lossless PDF lane; doc2md as an SDK; keel-template compliance.

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
character. The `docling` extra is **unpinned**: each fresh install resolves
latest (2.113.0 as of the baseline), so behavior floats.

- [x] Stand up the local PDF ring: `uv venv --python 3.12` + `pip install -e
      '.[docling]'`, set `DOC2MD_PDF_PYTHON`, run the full eval locally,
      record the baseline (pass/fail/skip counts + per-doc recalls) as a
      dated table in `evals/README.md` so the next fresh run can diff it.
      *(2026-07-17: 19 pass / 2 fail / 0 skip — reproduces both CI runs
      (dispatch + scheduled nightly) character for character; see the dated
      baseline in `evals/README.md`, including the `TORCHDYNAMO_DISABLE=1`
      and modelscope-flakiness footguns.)*
- [ ] Pin the PDF toolchain: exact `docling`/`docling-core` pins in the
      extra; prefetch model artifacts (`artifacts_path` /
      `DOCLING_ARTIFACTS_PATH`, `docling-tools models download`) so CI and
      local run the same models; stamp docling + poppler versions into
      report warnings (the soffice version already is).
- [ ] Give the eval harness an expected-fail mechanism: an `xfail: true` +
      `_note` marker in `expectations.json` that `run_eval.py` reports as
      XFAIL (and XPASS as a failure to re-encode), so truthful-but-undesired
      pinned behavior doesn't leave the nightly permanently red and useless.
- [ ] Re-encode the two failing pdf expectations truthfully for the pinned
      toolchain (dataflow's knife-edge probe gets its real fix in M1).
- [ ] Wire `explain_gap`/`GapReport` (built and exported from
      `backend.ingest`, currently uncalled outside its tests) into the pdf
      `losslessness` block: bucket counts + `absent_top`, so every report
      says *why* recall < 1.0.

## M1 — Trustworthy measurement (self-validation hardening)

Context: the converter-blind gate is ~80% built — `_pdf_losslessness` grades
docling's markdown against `pdftotext` (shares no code with docling), with
`strip_running_lines` + digit-masked repetition handling furniture and a
two-signal loss verdict (`token_recall < 0.80` AND `content_recall < 0.95`).
Remaining holes: no NFKC/ligature fold (a residual `ﬁ` fakes loss), the
exclusion set partly trusts docling's own picture bboxes (self-grading), the
OCR path measures nothing, and a diagram-only digital PDF misroutes to OCR.

- [ ] Move the gate policy into `src/`: `_pdf_losslessness` →
      `backend.validate.pdf_coverage_report` (pure strings-in/dict-out, 3.6 +
      stdlib; poppler subprocess calls stay in the script), with a mirrored
      `tests/unit/backend/` file — *before* any policy change, so the office
      coercion invariant (`gate` never `pass` off-lane) is test-protected.
- [ ] NFKC + ligature normalization applied symmetrically to both sides —
      as a **pdf-lane-only entry point**, not inside the shared `tokenize`
      (office 1.0 gate untouched; if it ever moves into shared code, re-run
      the full office corpus first).
- [ ] Make the exclusion set converter-blind at convert time: use
      `_pdf_drawn_boxes` (pypdfium2, the PDF's own drawing objects) instead
      of docling's `_picture_boxes` for figure-region text — removes the last
      docling-judges-docling input from the measurement. pypdfium2 stays in
      the PDF-lane script; only its box *output* crosses into the 3.6-stdlib
      validate function.
- [ ] Fix the OCR routing: area-weighted text-layer probe (a diagram-only
      digital PDF must not trip full-doc OCR); when a thin text layer exists,
      score the OCR output against it; record RapidOCR per-box confidence
      (mean/min) so scans get *some* measured signal.
- [ ] Extract figures on the OCR path (today: none — placeholders bail and
      the images gate degrades spuriously; also a hard prerequisite for M4).
- [ ] Stress fixtures, before the features that fix them: hyphenation +
      ligature doc, per-page-varying footer ("Page 3 of 120"), non-dot-leader
      TOC, and a multi-column reading-order fixture (that one *encodes
      measured truth* — docling's reading-order model owns the fix; if it
      falls short it's an xfail with a `_note`, not a slice here).
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
| `schema_version` in bundle files | parked earlier — revisit when the SDK (M5) freezes the contract for external consumers | park until M5 |
