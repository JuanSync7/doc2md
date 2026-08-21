---
title: The A-grade plan — fidelity, provenance, documentation
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [plan, fidelity, provenance, reproducibility, documentation, quality]
summary: Sequenced slices to take the four output dimensions to a checkable A — a wider losslessness gate, a report you can re-run from, and drift-tested references.
---

# The A-grade plan

Serves [`end-goal.md`](end-goal.md). Where [`roadmap.md`](roadmap.md) plans the
*PDF lane*, the *SDK* and *keel compliance*, this document plans the **quality of
what every lane already emits**: the markdown body, the report, the outline, and
the metadata.

## Why this document exists

A four-lens adversarial review of a live `IT_runbook.docx` run (2026-08-21) graded
the four output dimensions **C-, B-, C-, C**. The findings are reproduced in this
plan as slices. Two of them are worth stating up front because they change how the
project should think about itself.

**1. The office lane was declared closed on a gate that cannot see the defect.**
`roadmap.md` calls the office lane "a closed chapter (recall == 1.0, 544/544)".
Both of these are true at once:

- every source token reaches `document.md`, measured against a converter-blind
  XML walk, 544 documents at exactly 1.0; **and**
- a nested numbered procedure comes out **renumbered**, because
  `_ooxml_md.py:669-673` indents list levels by 2 spaces and a `1. ` marker needs 3.
  Step 3 renders as step 4. An operator working an outage from the converted
  runbook executes the wrong action for every step after the first sub-step.

Emphasis markers, list indentation and code fences are **not tokens**, so the gate
is structurally incapable of objecting. `structural_errors: 0`,
`losslessness.gate: "pass"`, `coverage.ratio: 1.0`, `kb_lint` exit 0 — the
pipeline's unanimous verdict on a semantically corrupted procedure is "perfect".

`end-goal.md` §1 already settles the principle: *"Structure **is** content… whose
headings flattened, whose table rows scrambled… is not lossless in any useful
sense."* The gate simply never implemented that sentence. **So the chapter reopens
— not to weaken the gate, but to widen it.** Gates are ratchets (`end-goal.md`
§2.4): `token_recall == 1.0` stays exactly as it is, and a second hard gate lands
beside it.

**2. `dropped_headers_footers` has zero emitters.** `output-contract.md` documents
the warning code and `end-goal.md` §1 *requires* it — "the drop is always
deliberate and visible, never an accident". `grep -rn dropped_headers_footers
--include=*.py` returns nothing. The live run silently discarded a
`Confidential — Project Kestrel` banner with `warnings: []`. The ungated PDF lane
reports its debt (`figure_text_tokens`) more honestly than the gated office lane
reports its drops.

## What "done" means here

The owner's goal, stated directly:

> convert lossless, and have all the reporting information needed to **re-run,
> re-establish, or repeat** a run based on the report — the switches that were
> used and the decisions that were taken, recorded per run.

That makes **reproducibility a product feature**, not an ops nicety, and it is the
spine of this plan (phase **R**). A conversion is worth what you can prove about it
*and repeat from it*.

---

## The rubric — what A means, checkably

A grade is not a feeling. Each dimension is A only when **every** condition below
is demonstrable by a command that returns a verdict. `make verify` (M6) will
eventually run all of them; until then each slice names its own check.

### A. `document.md` — the markdown body

| # | Condition | Verified by |
|---|---|---|
| A1 | A second hard gate, `structure_fidelity`, grades list nesting depth, emphasis runs, code runs, link count and table dimensions against an OOXML-derived ground truth; office lane fails on any mismatch | `report.json.structure_fidelity.gate == "pass"`, corpus-wide |
| A2 | Body text round-trips: markdown → text equals source text **as a sequence**, not only as a multiset | new `tests/unit/backend/test_validate_roundtrip.py` |
| A3 | No unconditional escaping — `DB_MAX_CONN_LIMIT`, `--dry_run=true`, `[payments]`, `<stderr>` survive verbatim in the stored bytes | fixture assertions |
| A4 | Every deliberate drop emits a named warning code carrying a **count** | `test_warning_vocabulary.py` — every code in the contract has an emitter, every emitter is in the contract |
| A5 | Adversarial fixtures exist for each of the above and are pinned in the eval corpus | `evals/run_eval.py` green |

### B. `report.json` — reporting and provenance

| # | Condition | Verified by |
|---|---|---|
| B1 | `run{}` records argv, resolved config **with the source of each value**, code identity (version + commit + dirty), interpreter, platform and external tool versions | `test_run_block.py` |
| B2 | `converter` is derived from package metadata + git commit, never a literal | two commits ⇒ two values |
| B3 | Every branch the pipeline chose is a structured `decisions[]` record, not prose | `test_decisions.py` |
| B4 | `manifest.jsonl` is a true run log: a row per document **per run**, including skips and failures, joined to `runs.jsonl` by `run_id` | `test_manifest_runlog.py` |
| B5 | `scripts/replay_run.py --report R` reproduces a byte-identical `markdown_sha256`, or names every divergence | integration test |
| B6 | No vacuous pass: an empty or sub-threshold source carries `n_source_tokens` and an `empty_source` code | fixture |
| B7 | Exit codes distinguish error from pending work | `test_exit_codes.py` |

### C. `structure.json` — the tree

| # | Condition | Verified by |
|---|---|---|
| C1 | Heading hierarchy is inferred when the extractor emits a flat level (PDF `##`) — no bundle has 9 top-level siblings whose titles are `1`, `1.1`, `1.1.1` | eval probe on `d073399c8516fb0f` |
| C2 | Node ids are content-derived and stable across an inserted heading | `test_outline_stability.py` |
| C3 | One anchor scheme, GFM-correct; `structure.json` anchors and `kb.body_anchors` agree on the whole corpus | `test_anchor_parity.py` |
| C4 | `max_depth` is tree depth; a largest-**leaf** token count is published | fixture |
| C5 | Tables are first-class nodes, addressable like images | contract + fixture |
| C6 | A body sentence cannot become a heading — the ALL-CAPS heuristic is bounded | `test_outline_heuristics.py` |

### D. metadata / knowledge — the KB substrate

| # | Condition | Verified by |
|---|---|---|
| D1 | A **no-model** run yields a titled, summarised, linked page: `title`, `abstract` and `links` are filled deterministically | live no-model run ⇒ `pending` excludes those three |
| D2 | Every page can link back to its source (`source.url`) | fixture |
| D3 | `id` is unique corpus-wide by construction | `test_id_uniqueness.py` at 1000 synthetic docs |
| D4 | One canonical identity, recorded in the contract | contract + linter |
| D5 | Every knowledge record carries a `ref` to the section that asserts it | schema + `request_spec` |
| D6 | No field ships that nothing reads and nothing fills | inventory test |
| D7 | Corpus gates stay sound at 1000 documents; every truncation is disclosed | scale test |

### E. documentation (gate on all of the above)

| # | Condition | Verified by |
|---|---|---|
| E1 | A product guide explains the pipeline end to end with a real worked example | `docs/guide.md` |
| E2 | Every switch and env var is documented **with its effect on the output**, and the doc cannot drift | `test_docs_config_parity.py` |
| E3 | The full controlled vocabulary is published and generated from `config/vocab.yaml` | `test_docs_vocab_parity.py` |
| E4 | Every key in every artifact is documented, and the doc cannot drift | `test_docs_schema_parity.py` |

---

## Sequencing

```
P0 stop the bleeding ─► P1 references (drift-tested) ─► P2 provenance (R)
                                                          │
                      P3 fidelity gate (F) ◄──────────────┘
                                │
                      P4 structure (S) ─► P5 metadata (K) ─► P6 regrade
```

P0 first because it ships wrong documents today. P1 second because the owner asked
for it and because a drift test makes every later phase update its own docs for
free. P2 before P3 so that when the fidelity gate starts failing bundles, the
report can say which code produced them.

Execution follows `roadmap.md`'s loop: one vertical slice per run, test first, both
rings green, tick the box here in the same PR.

---

## P0 — Stop the bleeding

Three defects that make a converted document *wrong* rather than *lesser*. Each
lands with a fixture that fails first.

- [x] **P0.1 — Ordered-list nesting.** `_ooxml_md.py:669-673` uses
      `"  " * ilvl`. CommonMark nests a child at the parent's **content column**:
      2 for `- `, 3 for `1. `. Indent by the running width of the ancestor
      markers, not a constant. Fixture: `docx` with a decimal parent + `ilvl=1`
      child, plus mixed bullet/decimal nesting.
      **Done:** rendered HTML contains a nested `<ol>` and the top-level item
      count equals the source's top-level count.
- [x] **P0.2 — Header/footer drops become visible.** Emit
      `dropped_headers_footers` with `{parts, lines}` whenever `header*.xml` /
      `footer*.xml` exist and are skipped. Extend to every structural drop the
      office lane makes today (tracked deletions, `mc:Fallback`).
      **Done:** the live runbook bundle carries the code; `grep` finds an emitter
      for every code named in `output-contract.md`.
- [x] **P0.3 — Escaping stops mangling identifiers.** `_MD_SPECIAL`
      (`_ooxml_md.py:263`) escapes ``* _ ` < [ ] ~`` unconditionally. CommonMark
      does not treat intra-word `_` as emphasis, and `<`/`[` only matter in
      specific positions. Escape by **position**, not by character class.
      **Done:** `DB_MAX_CONN_LIMIT`, `--dry_run=true`, `[payments]`, `<stderr>`
      appear verbatim in `document.md`; the round-trip test (A2) passes.

## P1 — The references (and the drift tests that keep them true)

The owner's first ask. New directory `docs/reference/` (needs `README.md` +
`CLAUDE.md` with frontmatter per `CONVENTIONS.md`).

- [x] **P1.1 — `docs/guide.md`.** What doc2md is, the four artifacts, the four
      stages, a real worked example (`IT_runbook.docx` in, bundle out), the
      no-model vs model story, and how verification works. The document this plan
      was born from.
- [x] **P1.2 — `docs/reference/configuration.md`.** Every CLI flag and every
      `DOC2MD_*` env var: default, precedence, and — the part that matters —
      **what changes in the output** when you move it. Includes the traps found:
      `DOC2MD_VLM_URL` is ignored by `enrich_metadata.py`; `--no-cache` persists
      in one script and doesn't in another; a malformed env value silently reverts
      to default.
- [x] **P1.3 — `docs/reference/vocabulary.md`, generated.**
      `scripts/render_vocab_doc.py` emits the full term list from
      `config/vocab.yaml`: every field, kind, tier, governance regime, the terms
      themselves, aliases, `maps_to` standards, and which file the field lands in.
      Committed output, regenerated by the script.
- [x] **P1.4 — `docs/reference/output-schema.md`.** Every key of `document.md`
      front matter, `structure.json`, `report.json`, `knowledge.json`,
      `manifest.jsonl`: type, when present, who writes it, which gate reads it.
- [x] **P1.5 — Drift tests.** `tests/integration/test_docs_parity.py`: every
      argparse flag appears in the config reference and vice versa; every
      `DOC2MD_*` string in the tree is documented; the vocabulary doc matches a
      fresh render; every report key emitted by a real build appears in the schema
      reference. **A later phase that adds a key and forgets the doc fails CI.**
- [x] **P1.6 — Correct the 9 stale claims** found in the sweep (`scaffold.py` and
      `build_index.py` do not exist; `--domain-file` undocumented; `--help`
      strings missing on `heal_supervisor`/`run_eval`; `validate_figures --src`
      mislabelled required; `docling_convert` returns 0 unconditionally).
- [x] **P1.7 — README points at the guide** instead of being a second, thinner
      copy of it.

## P2 — Provenance: a report you can re-run from

The owner's stated goal. Everything here lands in `report.json` and the run log.

- [x] **P2.1 — Code identity.** `backend.provenance.code_identity()` (3.6,
      stdlib, no subprocess — read `.git/HEAD` and the ref file directly) returns
      `{name, version, commit, dirty}`. `CONVERTER = "doc2md-ooxml/0.1.0"`
      (`build_bundle.py:71`) becomes derived. **Done:** two commits produce two
      `converter` values; a dirty tree is flagged.
- [x] **P2.2 — The `run{}` block.**
      ```jsonc
      "run": {
        "run_id": "...", "started_at": "...", "finished_at": "...",
        "entrypoint": "build_bundle",
        "argv": ["--src","<src>","--out","<out>","--tokenizer","tiktoken:cl100k_base"],
        "source_root_sha256": "...",        // identify the tree without an absolute path
        "code":   {"version":"0.1.0","commit":"81e94a7","dirty":false},
        "host":   {"python":"3.6.8","platform":"Linux-4.18.0"},
        "config": {"min_recall":{"value":0.80,"from":"default"},
                   "tokenizer":{"value":"tiktoken:cl100k_base","from":"flag"}},
        "tools":  {"soffice":"7.6.4.1"}
      }
      ```
      Path arguments are recorded as `<src>` / `<out>` placeholders — `CLAUDE.md`
      forbids absolute host paths — with `source_root_sha256` proving you pointed
      at the same tree. `config` records **value + provenance of the value**
      (`flag` / `env` / `toml` / `default`), which is what makes a run
      *re-establishable* on a different machine.
- [x] **P2.3 — `decisions[]`.** Every branch the pipeline chose, structured:
      `lane_selected`, `preconvert` (with soffice version), `ocr_routed` (with the
      area fraction that decided it), `body_source` (`docling|text-layer|hybrid`),
      `cache_hit`, `gate_coerced`, `image_inline_bailed`, `empty_source`. Shape:
      `{code, decision, reason, evidence:{}}`. `warnings[]` stays for *problems*;
      `decisions[]` carries *choices*. **Done:** the runbook bundle records the
      lane choice and the tokenizer choice; a soffice-preconverted `.odt` records
      the binary version.
- [x] **P2.4 — The manifest becomes a run log.** `manifest.jsonl` rows gain
      `run_id`, `ts`, `action` (`built|skipped|failed|forced`) and are written for
      **every** document on **every** run, skips included. New `runs.jsonl` at the
      output root: one row per run carrying the whole `run{}` block. Also fixes
      `images.orphans` being hardcoded `0` (`build_bundle.py:333`) and
      `captions.model`/`prompt_sha` being blanked by `--force`
      (`build_bundle.py:351`).
- [x] **P2.5 — `scripts/replay_run.py`.** `--report R [--src X --out Y]
      [--dry-run]` reconstructs the exact argv and reports every divergence before
      running: different commit, dirty tree, different interpreter, different
      resolved config, different source sha. **Done:** replaying a recorded run
      reproduces `markdown_sha256` byte-for-byte; mutating one env var makes
      replay say so.
- [x] **P2.6 — No vacuous pass.** The office lane publishes `n_source_tokens`
      (the PDF lane already does) and an `empty_source` code, so
      `token_recall: 1.0` can never be claimed over nothing. **Done:** a zero-byte
      `.docx` no longer reports in the same vocabulary as a real conversion.
- [x] **P2.7 — Exit-code semantics.** `enrich_metadata.py:651` returns 2 for
      *pending*, so a healthy no-model run can never exit 0 and breaks any
      `set -e` chain. Define `0` ok, `1` error, `3` pending, and
      `--fail-on-pending` for the strict lane. Documented in P1.2.
- [x] **P2.8 — `timing_ms` covers the whole document** (today it stops before the
      pixel write, verify, GC and caption-carry at `build_bundle.py:316-352`).

## P3 — Fidelity: make structure measurable

The keystone. P0 fixes three bugs; P3 is what stops the next three.

- [ ] **P3.1 — `backend.validate.md_structure`.** A stdlib, 3.6-safe reader of the
      *emitted markdown* that reports structural facts under CommonMark's rules:
      list nesting depth per item, emphasis runs, inline-code runs, fenced blocks,
      link count, table dimensions. It must implement the content-column
      continuation rule, because that is the rule P0.1 violated.
- [ ] **P3.2 — The OOXML structural ground truth.** Extend the existing
      converter-blind walk to yield the same fact vector from the source XML:
      list items per level (`w:numPr/w:ilvl`), bold/italic runs (`w:rPr`),
      hyperlinks, table grid dimensions. Shares no traversal code with the
      converter — same rule as the token gate.
- [ ] **P3.3 — The `structure_fidelity{}` gate.** Compare the two vectors; a
      mismatch is a **hard fail on the office lane** and `best-effort` on PDF, by
      the same structural coercion that protects the losslessness gate
      (`_mdcheck.py:317-318`). Ratchet, not replacement: `token_recall == 1.0`
      is untouched.
- [ ] **P3.4 — Emphasis.** `w:b` / `w:i` / `w:strike` → `**` / `*` / `~~` in the
      docx path (`_docx_p_text`, `_ooxml_md.py:516`), and the pptx equivalent.
      Today `w:rPr` is never inspected.
- [ ] **P3.5 — Code and monospace.** A run in a code character style, or a
      paragraph in a code paragraph style, becomes inline backticks or a fenced
      block. `kubectl get pods -n payments` must stop being indistinguishable from
      narration.
- [ ] **P3.6 — Table spans.** Resolve the open decision in `roadmap.md`: HTML
      island vs flattened-and-recorded. Whichever wins, a flattened span emits a
      named warning with a count.
- [ ] **P3.7 — Audit the remaining docx features** — footnotes, endnotes,
      comments, tracked changes, text boxes, equations, charts, embedded objects —
      and force each into exactly one of *converted*, *converted lossily +
      warning*, *dropped + warning*. No silent third state.
- [ ] **P3.8 — Adversarial fixtures in `gen_corpus.py`** for every item above,
      with pinned expectations.

## P4 — Structure: make the tree a tree

- [ ] **P4.1 — Heading-level inference for flat extractors.** `is_heading`
      (`_chunk.py:54`) returns the ATX hash count first, so the numbering branch is
      dead the moment docling emits `##` — which it always does. A real eval
      bundle has **9 flat siblings** carrying `1`, `1.1`, `1.1.1`, `1.1.1.1` in
      their titles. Infer levels from numbering when the document is
      single-level. Guarded so office bundles are unaffected.
- [ ] **P4.2 — Stable section identity.** `_chunk.py:29` already defines
      `Section(section_id, parent, fingerprint, …)` with sha1-derived ids — and
      `chunk_sections` has **zero production callers**. Ship `section_id`,
      `parent` and `fingerprint` into `structure.json` nodes. Positional
      `sec-0001` cannot back a permalink or an incremental re-index.
- [ ] **P4.3 — One anchor scheme.** `structure.json` publishes
      `"payments gateway it runbook"` (spaces); `kb._derive.body_anchors` — which
      is what `_lint._check_refs` grades `#fragment` refs against — publishes
      `payments-gateway-it-runbook`. Adopt the renderer-correct one everywhere and
      add a parity test. Also fix `normalize_title`'s `_NUM` requiring trailing
      whitespace (`1.2reference documents` keeps its number, `1.2 Scope` doesn't).
- [ ] **P4.4 — Honest summary numbers.** `max_depth` is `max(level)`, not tree
      depth (a 9-flat-sibling outline reports 2). `largest_section_tokens`
      includes the root, so any single-H1 document reports the whole document.
      Publish tree depth and a largest-**leaf** count.
- [ ] **P4.5 — Table nodes.** `"tables": 1` is an integer; the escalation matrix
      cannot be retrieved, cited or linked. Give tables the image treatment:
      `{table_id, line, rows, cols, has_header}`.
- [ ] **P4.6 — Bound the ALL-CAPS heuristic** (`_chunk.py:67`). A runbook callout
      — `DO NOT REBOOT THE PRIMARY` — currently becomes a root heading and
      reparents everything after it, with both gates still green.

## P5 — Metadata: fill the fields from evidence already on disk

The schema is good. The extractor ignores what the pipeline already proved.

- [ ] **P5.1 — Deterministic `title`.** `source_title` is read at
      `enrich_metadata.py:199` and used **only** to compute the slug; `title`
      stays PENDING. Fill it: `source_title` → first H1 → filename. Tier 0/1, so a
      no-model import stops producing pages titled by filename.
- [ ] **P5.2 — Deterministic `abstract` floor.** `structure.json` hands over the
      lede paragraph's exact span for free. Derive a bounded abstract at tier 1;
      a model may improve it, and authored-wins already protects a human edit.
- [ ] **P5.3 — Verified links.** The live run put a real outbound URL into
      `structure.json` `sec-0003.links[0]` at recall 1.0 and zero cost;
      `enrich_metadata.py` never opens `structure.json`. Feed harvested links into
      `knowledge.json` `links` at tier 0, distinguished in `_provenance` from
      model-proposed ones. **The pipeline currently discards its verified edges
      and keeps the hallucinated ones.**
- [ ] **P5.4 — A permalink.** `source.uri` is a relpath, so a wiki page cannot
      link home. Add `source.url` + `--source-base-url`.
- [ ] **P5.5 — `id` uniqueness by construction.** `slugify(title)` with no
      uniqueness check (`enrich_metadata.py:205`) collides on "Overview" and
      "Release Notes" across any real corpus, and a collision is a `kb_lint`
      ERROR a human must hand-fix. Disambiguate deterministically.
- [ ] **P5.6 — One canonical identity.** `uid: "it-runbook"` and
      `id: "payments-gateway-it-runbook"` are two namespaces for one document, and
      `kb_lint.py:223` resolves refs against **both**, so a corpus can grow two
      disjoint link graphs that both lint clean. Decide, record in the contract.
- [ ] **P5.7 — Section-anchored knowledge.** `request_spec` never tells the model
      that `relations` need `s`/`o`, that entities need `name`, or that `ref`
      exists — so `{"p": "runs_on"}` is a schema-valid relation and "which section
      says this" is unanswerable. Require a `ref` to a section anchor on every
      record.
- [ ] **P5.8 — `next_review_due`** derived from `last_reviewed + review_cadence`
      (tier 1). Four review-lifecycle fields exist and nothing computes the one
      that would make them actionable.
- [ ] **P5.9 — Prune what nothing reads.** `requirement_level` is declared
      RESERVED with no binding field; `classification` and `confidentiality` are
      two fields for one concept; `aliases` / `prerequisites` / `out_of_scope` have
      no consumer anywhere. Bind or delete. `_provenance` stores `tier`, which is a
      pure function of the field name — dropping it removes ~40% of the metadata
      bytes.
- [ ] **P5.10 — Entity group governance.** `vocab.group_type("gadgets")` is `""`,
      so an invented entity group with untyped members passes acceptance
      unchallenged and is written stamped `generated`.
- [ ] **P5.11 — Corpus soundness at scale.** `_PAIRWISE_CAP = 4000`
      (`_corpus.py:66`) falls back to first-two-character bucketing, which is
      explicitly unsound, exactly when the corpus is big enough to need it — and
      `keywords` is seeded from every SCREAMING_SNAKE token in every body. Replace
      with sound blocking, and `log()` every truncation.
- [ ] **P5.12 — One unreadable bundle must not silently disable corpus
      integrity.** `kb_lint.py:216` sets `partial` on any unreadable document,
      which sets `known_ids = None` and skips `see_also` resolution, graph, skew
      and coverage — reported as a single INFO line. At 1000 bundles that is a
      clean-looking all-clear.
- [ ] **P5.13 — Registry promotion should not invalidate the whole answer
      cache.** `prompt_sha` includes `vocab.version`, so every promotion round
      forces a full-corpus re-ask — and promotion is manual (`kb_lint` prints
      candidates; nothing writes `vocab.yaml`). Scope the cache key to the fields
      whose spec actually changed, and add a `--promote` writer.

## P6 — Regrade

- [ ] **P6.1** Re-run the four-lens adversarial review against a fresh live run
      after each of P0/P2/P3/P4/P5. Findings become new slices here; a lens may
      not award A without naming the evidence.
- [ ] **P6.2** Both rings green (host 3.6.8 and 3.6.8 with PyYAML blocked), evals
      green, drift tests green.
- [ ] **P6.3** Stop only when all four dimensions are A or better **and** every
      rubric row above has a passing check.

---

## Recorded deviations

Kept here so they are decisions, not drift.

| Decision | Alternative | Why this way |
|---|---|---|
| Widen the gate rather than relax the office "closed" claim | Accept token recall as the definition of lossless | `end-goal.md` §1 already says structure is content; the gate under-implemented the charter |
| `decisions[]` separate from `warnings[]` | One list | A choice is not a problem; mixing them means neither can be aggregated |
| argv recorded with `<src>`/`<out>` placeholders + a source-root hash | Record real paths | `CLAUDE.md` forbids absolute host paths; the hash still proves tree identity |
| Docs land in P1, before the features that will change them | Document last | The drift test makes every later phase update its own reference for free |
| `structure_fidelity` hard-fails office, `best-effort` on PDF | Uniform gate | Same asymmetry, same reason, as losslessness: PDF has no ground-truth semantic tree |
