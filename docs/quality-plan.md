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

---

## Read this first — the grade, as of 2026-08-24

> **`scripts/grade_output.py` prints `OVERALL A (34 pass, 0 fail, 0 skip, of 34)`.
> The honest grade is not an A.** Both of those sentences are true at once, and the
> gap between them is the single most important thing this document records.

A third adversarial review (2026-08-23, logged as **P8** below) filed **65
findings**. Every one was independently reproduced by a skeptic whose job was to
refute it, and every one survived. **All 65 are now fixed**, together with the six
follow-on items the review's own write-up left open. The rubric got materially
*harder* to satisfy in the same pass — and still printed A, because **a rubric can
only grade the question it asks**, which is verbatim the lesson P6.3 was written to
record and is now demonstrated a second time.

| dimension | grader says | honest, after this review | why the difference |
|---|---|---|---|
| A `document.md` | A | **A** | The second hard gate grew from thirteen facts to fifteen and got much sharper. `heading_path` and `thematic_breaks` were reported **`unmeasured` on every real office document** for as long as the ground-truth half was unwritten, so the attack `heading_path` exists to catch still published — that was P6.3's own definition of the failure, one phase later, in the fact added to stop it. `docx_source_structure` supplies both now, derived from the XML rather than from `md_structure`, so the fact is **graded**. Measured on the real corpus: the three office documents convert `gate: pass` with no `unmeasured` list at all, and exchanging two section titles in `kestrel-clock-spec.docx` leaves the token multiset **byte-identical** (`token_recall: 1.0`, `valid: true`) while `structure_fidelity` reports `fail` with `heading_path` the only delta. |
| B `report.json` | A | **A** | `replay_run.py` can no longer print an all-clear over a class it never compared (new exit `4`), the path-redaction leaks are closed, and a damaged package no longer orphans a whole run's `runs.jsonl` row. The two items left open at first write are closed: `code_identity()` no longer crashes under a non-UTF-8 locale (all five `open()` carry `encoding="utf-8"`; verified by running it under `LC_ALL=C`, where `getpreferredencoding` really is `ANSI_X3.4-1968`), and `--compare` pointed at the bundle being replayed now **refuses** — `"would compare … with itself"`, exit `1` — instead of printing `REPRODUCED`. Comparing an in-place stage stays impossible by construction; the change is that it is now *reported* rather than faked. |
| C `structure.json` | A | **A−** | The coverage gate can fail for the first time; the numbered-heading and measurement-series heuristics are bounded; long ATX headings survive. Open: nothing measures the converse — a heading the heuristics *invented* — because the native-text lanes legitimately carry unmarked headings. |
| D metadata | A | **B+** | Ten linter gates that were structurally incapable of firing now fire, and the skew gate answers the question it exists for. Open and known: neither `kb_lint` nor rubric row D5 resolves a `ref` back to the section whose `line_span` contains the record, so **a record filed under the wrong section still lints clean** — the same blind spot that hid idx 9 for as long as it existed. |
| E documentation | A | **A** | This pass corrected the `meta.id` derivation, two v2-shaped payloads printed under v3 headers, a deleted field described as a live safety boundary, three disagreeing fact counts, and eight "Verified by" cells that named files which have never existed. Both items left open at first write are closed: `decisions[].evidence.ext` is documented in its own block (and `_UNDOCUMENTED_TODAY` is now **empty**, which the equality assertion keeps that way), and the doc-drift guard no longer grades only the `schema_version` literal — it round-trips the documented `knowledge.json` sample through the real `accept_model_meta`. Proven against the defect it missed: restoring the v2-shaped relation makes it fail with `missing-required-ref`, where the old guard saw a matching `3` and said nothing. |

**Nothing here is a reason to distrust the gates that exist.** Every fix in P8 was
verified in both directions — the damage must fail, *and* ordinary documents must
still pass — and the eval corpus (19 pass, 0 fail, 3 skip) and all three test rings
stayed green throughout. The point is narrower and worth keeping in front of the
reader: **the grader's A is a statement about 34 questions, not about the output.**
P6.3 stays unticked.

---

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

**The "Verified by" column is a promise, and it was broken.** Eight of these cells
named test files and an eval probe that have never existed (`test_run_block.py`,
`test_decisions.py`, `test_manifest_runlog.py`, `test_exit_codes.py`,
`test_docs_config_parity.py`, `test_docs_vocab_parity.py`,
`test_docs_schema_parity.py`, and "eval probe on `d073399c8516fb0f`" — an id in a
shape `evals/expectations.json` does not even use, since it keys by source path).
They were introduced by PR #7 in the same commit that ticked the slices they
justify, so they were stale at birth rather than drifted. The conditions were all
genuinely verified — by `_rubric` predicates and by other files — but a rubric that
points at nothing is exactly the class of claim P1.6 and P7.11 exist to stamp out.
Every cell below now names something that resolves. The table was also **short by
five rows**: the executable rubric grades 34, and `C1s`, `C6s`, `D3s`, `D5s` and
`E3s` appeared nowhere here, so a reader could not reconstruct what
`scripts/grade_output.py` runs in either direction. Rows whose id ends in `s` are
**suite** rows (answered by named tests); the rest are **artifact** rows (answered
by a predicate reading the graded corpus).

### A. `document.md` — the markdown body

| # | Kind | Condition | Verified by |
|---|---|---|---|
| A1 | artifact | A second hard gate, `structure_fidelity`, grades **fifteen** facts against a converter-blind OOXML ground truth: `headings` (by level), `heading_path` (each heading's title tokens, with its level, in order), `list_items` (by nesting depth), `ordered_items`, `bullet_items`, `ordered_numbers` (the number a renderer **prints**, in order), `strong`, `em`, `strike`, `code_spans`, `code_blocks`, `links`, `tables` (rows × cols **and every cell's content**), `list_item_words` (every list item's text, in order), and `thematic_breaks`. The office lane fails on any mismatch; a format with no second implementation reports `unmeasured`, never `pass`; a fact the ground truth does not supply is named in `structure_fidelity.unmeasured` rather than silently skipped | `_rubric._a1_structure_gate` via `scripts/grade_output.py` — `report.json.structure_fidelity.gate == "pass"` corpus-wide. The closed list is `backend.validate._mdcheck._FIDELITY_FACTS`; **do not restate its length here without re-counting it**, which is how this row came to say "twelve" |
| A2 | suite | Body text round-trips: markdown → text equals source text **as a sequence**, not only as a multiset | `tests/unit/backend/test_validate_roundtrip.py` (`test_swapping_two_body_paragraphs_passes_the_multiset_gate_but_fails_the_sequence` + 3 more) |
| A3 | artifact | No unconditional escaping: a character is escaped only when it could actually be syntax. `DB_MAX_CONN_LIMIT`, `--dry_run=true`, `[payments]`, `snake_case_helper` survive verbatim in the stored bytes | `_rubric._a3_verbatim`, asserting `backend.validate._rubric.ADVERSARIAL_PROBES` against the adversarial fixture |
| A4 | suite | Every deliberate drop emits a named warning code carrying a **count** | `tests/unit/backend/test_warning_vocabulary.py` (`test_every_documented_warning_code_has_an_emitter`, `test_every_emitted_warning_code_is_documented`, …) — closed in both directions |
| A5 | suite | Adversarial fixtures exist for each of the above and are pinned in the eval corpus | `evals/run_eval.py`, graded on the **fixture** `office/kestrel-adversarial.docx` — not on the eval exiting 0, because eighteen green siblings do not make a claim about that one true. The whole eval census is printed in the row's evidence |

### B. `report.json` — reporting and provenance

| # | Kind | Condition | Verified by |
|---|---|---|---|
| B1 | artifact | `run{}` records argv, resolved config **with the source of each value**, code identity (version + commit + dirty), interpreter, platform and external tool versions | `_rubric._b1_run_block`, over the graded corpus's own `report.json` |
| B2 | artifact | `converter` is derived from package metadata + git commit, never a literal | `_rubric._b2_converter` (two commits ⇒ two values) |
| B3 | artifact | Every branch the pipeline chose is a structured `decisions[]` record, not prose | `_rubric._b3_decisions` |
| B4 | artifact | `manifest.jsonl` is a true run log: a row per document **per run**, including skips and failures, joined to `runs.jsonl` by `run_id` **and `stage`** | `_rubric._b4_runlog` |
| B5 | suite | `scripts/replay_run.py --report R` reproduces a byte-identical `markdown_sha256`, or names every divergence | `tests/integration/test_replay_run.py` (`test_replay_reproduces_the_exact_markdown_hash`, `test_a_changed_setting_is_named_before_anything_runs`, + 4 more). **Deliberately silent about `--compare`**, and see P8 — this row reported green over two defects (a missing `runs.jsonl` row and a no-`--src` call, both of which used to print an all-clear) because it named no test that exercised them |
| B6 | artifact | No vacuous pass: an empty or sub-threshold source carries `n_source_tokens` and an `empty_source` code | `_rubric._b6_no_vacuous_pass` |
| B7 | suite | Exit codes distinguish error from pending work | `tests/integration/test_enrich_metadata.py` (`test_the_exit_code_says_whether_the_corpus_needs_another_run`, …) |

### C. `structure.json` — the tree

| # | Kind | Condition | Verified by |
|---|---|---|---|
| C1 | artifact | Heading hierarchy is inferred when the extractor emits a flat level (PDF `##`) — no bundle has 9 top-level siblings whose titles are `1`, `1.1`, `1.1.1` | `_rubric._c1_hierarchy`, over every bundle that publishes an outline. A corpus where **no** bundle publishes one is `nothing to grade`, not a pass |
| C1s | suite | …and the inference fires only on the signature it claims: both halves, so a rule that never fires cannot satisfy the negative one | `tests/unit/backend/test_outline_heuristics.py` (`test_a_flat_extractor_gets_its_hierarchy_back_from_the_numbering`, `test_an_ascending_measurement_series_is_not_a_hierarchy`, …) |
| C2 | suite | Node ids are content-derived and stable across an inserted heading | `tests/unit/backend/test_outline_stability.py` (`test_positional_ids_move_but_content_ids_do_not`, …) |
| C3 | artifact | One anchor scheme, GFM-correct; `structure.json` anchors and `kb.body_anchors` agree on the whole corpus | `_rubric._c3_anchors`, **per bundle** — a bundle whose own `document.md` renders no heading now fails on its own account instead of being excluded and then counted as agreeing. Cross-checked against `tests/unit/backend/test_anchor_parity.py` |
| C4 | artifact | `max_depth` is tree depth; a largest-**leaf** token count is published | `_rubric._c4_summary_numbers`, per bundle: a non-empty body with an empty outline is a FAIL, not a skip |
| C5 | artifact | Tables are first-class nodes, addressable like images | `_rubric._c5_table_nodes`, per bundle. Every **office** bundle whose own body renders a GFM table must publish at least one table node; a non-office lane with a rendered table and no node is `unmeasured`, never passed |
| C6 | artifact | The shouted callout in the graded corpus stayed body text | `_rubric._c6_allcaps`. **Narrowed on purpose**: it greps one ALL-CAPS literal in one fixture, which is all the graded corpus contains to ask. It reported PASS over a real defect in the *numbered* branch, so the general claim now lives in C6s rather than being overstated here |
| C6s | suite | A body sentence cannot become a heading — all three heuristics (shouted, numbered, keyword) are bounded **in both directions** | `tests/unit/backend/test_outline_heuristics.py`, six named tests: the shouted, numbered and keyword body sentences that must not become headings, **and** the label form of each that must still open a section — because a bound that never fires would satisfy the negative half by recognising nothing at all |

### D. metadata / knowledge — the KB substrate

| # | Kind | Condition | Verified by |
|---|---|---|---|
| D1 | artifact | A **no-model** run yields a titled, summarised, linked page: `title`, `abstract` and `links` are filled deterministically | `_rubric._d1_no_model_page` — a live no-model run whose `pending` set excludes those three |
| D2 | artifact | Every page can link back to its source (`source.url`) | `_rubric._d2_permalink` |
| D3 | artifact | `id` is unique corpus-wide by construction | `_rubric._d3_ids_unique`, over the graded corpus |
| D3s | suite | …and by construction rather than by luck, at scale | `tests/unit/backend/test_id_uniqueness.py` (`test_a_thousand_colliding_titles_produce_a_thousand_distinct_ids`, `test_two_paths_that_slugify_alike_still_get_distinct_ids`, …) |
| D4 | artifact | One canonical identity, recorded in the contract | `_rubric._d4_one_identity` |
| D5 | artifact | Every knowledge record present cites the section that asserts it | `_rubric._d5_records_cite`. **Honest limit**: it checks a `ref` is *present*, not that it resolves to the section the record's `line` falls in, so it reported green over a real misfiling of every harvested link. See P8 |
| D5s | suite | …and a record that cannot say which section asserts it is refused rather than accepted | `tests/unit/backend/test_kb_enrich.py` (`test_a_record_that_cannot_say_which_section_asserts_it_is_refused`, …) |
| D6 | suite | No field ships that nothing reads and nothing fills | `tests/unit/backend/test_field_inventory.py` (`test_every_field_names_a_consumer_and_the_claim_is_true`, …) |
| D7 | suite | Corpus gates stay sound at 1000 documents; every truncation is disclosed | `tests/integration/test_kb_lint_corpus.py` (`test_the_corpus_gates_stay_sound_and_affordable_at_a_thousand_documents`, …) |

### E. documentation (gate on all of the above)

| # | Kind | Condition | Verified by |
|---|---|---|---|
| E1 | artifact | A product guide explains the pipeline end to end with a real worked example | `_rubric._e1_guide`, reading `docs/guide.md` |
| E2 | suite | Every switch and env var is documented **with its effect on the output**, and the doc cannot drift | `tests/integration/test_docs_parity.py`: `test_every_cli_flag_is_documented`, `test_every_documented_flag_exists`, `test_every_environment_variable_is_documented` |
| E3 | suite | The full controlled vocabulary is published and generated from `config/vocab.yaml` | `tests/integration/test_docs_parity.py`: `test_vocabulary_reference_is_not_stale`, `test_every_vocabulary_term_appears_in_the_reference` |
| E3s | suite | …and the shipped vocabulary itself loads, self-validates, and stays inside the strict YAML subset | `tests/integration/test_shipped_vocabulary.py` (`test_the_shipped_vocabulary_loads_with_no_arguments_and_self_validates`, …) |
| E4 | suite | Every key in every artifact is documented **in the block that documents that artifact**, and the doc cannot drift | `tests/integration/test_docs_parity.py`: `test_every_report_and_structure_key_is_documented`, `test_every_manifest_and_frontmatter_key_is_documented`, `test_every_emitted_warning_code_is_documented`, `test_every_decision_code_is_documented` |

E2, E3 and E4 all point at `test_docs_parity.py`, but they no longer point at *the
same evidence*: the file's checks are **partitioned** between them, no test backs
two rows, and none is left over to back a row by accident. A suite row is answered
only by the tests it names — so it fails when one is deleted or renamed, an
unrelated green test in the file cannot earn it, and an unrelated red one does not
sink it (that failure belongs to some other row, or to none, and if to none the fix
is a row).

---

## Sequencing

```
P0 stop the bleeding ─► P1 references (drift-tested) ─► P2 provenance (R)
                                                          │
                      P3 fidelity gate (F) ◄──────────────┘
                                │
                      P4 structure (S) ─► P5 metadata (K) ─► P6 regrade
                                                                │
                      P8 third review ◄── P7 close the regrade ◄┘
```

P0 first because it ships wrong documents today. P1 second because the owner asked
for it and because a drift test makes every later phase update its own docs for
free. P2 before P3 so that when the fidelity gate starts failing bundles, the
report can say which code produced them.

P6 is a **regrade**, not an endpoint, and P7 and P8 are what it produced: each
review attacks the gates the previous phase built, and its findings become the next
phase's slices. That loop is the mechanism, so the phase list is expected to keep
growing — a plan that stopped adding phases would mean the reviews had stopped
finding anything, which has not yet happened once.

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
      **Done:** `DB_MAX_CONN_LIMIT`, `--dry_run=true`, `[payments]` and
      `snake_case_helper` are stored verbatim: a single intraword `_` can never
      open emphasis under CommonMark's flanking rule, and a bracket is only syntax
      next to `](` or `][`. **`<stderr>` is NOT** — and an earlier draft of this
      row claimed it was. `<stderr>` matches CommonMark's raw-HTML tag-name
      production, so unescaped a renderer swallows it; escaping it is the only way
      it renders as itself. The rule is "escape what could be syntax", not "escape
      nothing", and the two are different claims. Recorded as a deviation rather
      than quietly dropped from the probe list.
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

- [x] **P3.1 — `backend.validate.md_structure`.** A stdlib, 3.6-safe reader of the
      *emitted markdown* that reports structural facts under CommonMark's rules:
      list nesting depth per item, emphasis runs, inline-code runs, fenced blocks,
      link count, table dimensions. It implements the content-column continuation
      rule, because that is the rule P0.1 violated. **Done:** and its correctness
      is not asserted, it is *differentially tested* against `marko` over a
      70-sample corpus (`tests/unit/backend/test_validate_mdstructure.py`).
      **That sentence used to be an assumption, and it drifted.** It recorded "111
      tests on the modern ring, 40 on the bare 3.6 ring" while *no CI job installed
      a parser at all*, so a green CI carried zero evidence from any of the 86
      differential cases — including the anti-vacuity guard written to prove the
      differential was wired up. It is now **enforced rather than counted**:
      `.github/workflows/ci.yml`'s `tests-modern` job installs `marko==2.2.3`
      (pinned, because the cases are graded against whatever the reference says)
      and a step runs that file alone and **fails the job if it reports a single
      skip**. The 3.6 ring keeps skipping, for its own documented reason — marko
      needs 3.8+ and that ring reproduces the offline bare-3.6 host. For the
      record the split is now 144 / 58, but the mechanism is the claim; a bare
      count is precisely what drifted. That pass found and fixed eight real
      bugs in the first draft — setext headings never counted, `***a***` losing its
      em, a link containing a code span vanishing, per-line instead of per-block
      counting, and CommonMark's rule of three. Adversarial fuzz disagreement with
      marko: 2.74% → 0.089%.
- [x] **P3.2 — The OOXML structural ground truth.** `backend.ingest.
      docx_source_structure`, in its own module so a helper cannot be shared by
      accident. **Its mechanism is deliberately different**, not a retyped copy:
      the converter dispatches recursively child by child, this walks a flat
      `iter()` stream and answers "where am I?" with an ancestor predicate over a
      parent map. That is what would catch a converter that forgot to recurse
      through a `w:sdt` content control. A test asserts it names none of the
      converter's twelve walkers.
- [x] **P3.3 — The `structure_fidelity{}` gate.** Compares the two vectors over a
      **closed** list of eleven facts; a mismatch is a hard fail on the office lane
      and `best-effort` on PDF, and an unmeasured lane reports `unmeasured` rather
      than a free pass. **The proof it works:** reintroduce the P0.1 defect and
      `conversion_report` still returns `recall 1.0, valid True, errors 0` while
      the new gate fails and names the delta — `list_items` source `{0:3, 1:1}`,
      markdown `{0:4}`, one nested step promoted to a sibling. Both halves are
      pinned in `tests/unit/backend/test_structure_fidelity.py`.
- [x] **P3.4 — Emphasis.** `w:b` / `w:i` / `w:strike` → `**` / `*` / `~~`, plus
      `w:rStyle` → backticks. Toggles are tri-state (a run inside a bold style can
      turn bold *off*), `w:rPrChange` is never read, and **adjacent identically
      formatted runs are coalesced** — Word splits one word across runs at every
      property boundary, so wrapping each run alone emits `**Dma****Arbiter**`,
      which `markdown_to_text` mis-pairs into a stray literal `**`.
- [x] **P3.5 — Code and monospace.** A run in a code character style becomes an
      inline span with a fence widened past any backticks it contains; consecutive
      code-styled *paragraphs* fuse into one fenced block, because one `w:p` is one
      line and five separate fences would be five separate programs. Fenced content
      is emitted **unescaped** — `markdown_to_text` keeps fenced lines verbatim and
      never unescapes them, so an escaped `\_` inside a fence would survive into the
      text layer and break recall.
- [x] **P3.6 — Table spans: flatten and record.** Decision below. Found and fixed a
      real bug on the way: `_gfm_table`'s trailing-empty-column trim could not tell
      span padding from a styled-but-valueless spreadsheet column, so a one-row
      table whose only cell spanned two columns silently lost its grid width — and
      under P3.3 that would have hard-failed a table the converter handled
      correctly. Callers that know their grid now pass it as a floor.
- [~] **P3.7 — Audit the remaining docx features.** A 33-row audit now exists
      (feature × current handling × does the ground truth see it × verdict), and it
      found that **20 of the 33 losses are silent for one structural reason**:
      converter and ground truth share `_collect_text`, so a drop implemented
      inside it is *symmetric* and recall reads a vacuous 1.0. Closed in this
      phase, highest harm first:
      * `word/charts/chartEx*.xml` — every modern chart type (waterfall, treemap,
        funnel, sunburst, box-and-whisker) matched **no** part pattern, so its
        labels and cached values were dropped from both sides. Real text loss,
        reporting recall 1.0. Now read.
      * `flattened_table_spans`, `tracked_changes_resolved`,
        `dropped_embedded_objects` — three drops that were correct and silent, now
        named with their counts, and the vocabulary is closed in both directions by
        `tests/unit/backend/test_warning_vocabulary.py` (the test that would have
        caught `dropped_headers_footers` having zero emitters).
      Still open, and deliberately not attempted here: footnote back-references,
      comment author/date/anchor, equation structure (needs a symmetric change to
      the ground truth or recall breaks), image alt text, list start numbers,
      `mailto:`/internal hyperlink targets, pptx and xlsx merged cells. Each is
      named in the audit with its harm and its cost.
- [x] **P3.8 — Adversarial fixtures in `gen_corpus.py`** for every item above,
      with pinned expectations. The eval also grew an **unknown-key guard**: an
      expectation key no checker reads now fails loudly, because a typo that looks
      like a check and proves nothing is the same vacuous pass `n_source_tokens`
      exists to prevent.

## P4 — Structure: make the tree a tree

- [x] **P4.1 — Heading-level inference for flat extractors.** Fires only on the
      signature of an extractor with no levels to give: three or more headings,
      **all one level**, at least 60% carrying a section number, and at least one
      number actually nested. Any variation in level and the extractor's own
      levels are trusted verbatim, so office bundles are untouched. A reshaped
      tree must never be a silent one, so `structure.json` now publishes
      `levels_inferred`.
- [x] **P4.2 — Stable section identity.** Nodes carry `section_id`
      (`sha1(anchor)[:16]` — the same input `chunk_sections` hashes, so a future
      chunk store and the outline name the same section the same way), `parent`,
      and a `fingerprint` over the node's **own** body, so a child's edit does not
      invalidate its ancestors. Positional `sec-0001` is kept: a grep proved it has
      no code consumer, but published bundles and the worked example index by it,
      and removing it buys nothing.
- [x] **P4.3 — One anchor scheme.** Decision: **the section number stays** —
      `1.2 Scope` → `12-scope`. An anchor's only job is to be the fragment a
      renderer emits, and every common renderer drops the dot and keeps the
      digits; `kb._lint._check_refs` grades `#fragment` refs against
      `kb.body_anchors`, which is derived from rendered heading text, so stripping
      the number would report every ref into a numbered section as a dead link. It
      also keeps `2.1 Overview` and `3.1 Overview` distinct with no suffix.
      `sections.gfm_anchor` is a deliberate **re-implementation** of
      `validate.gfm_anchor`, not an import — the layer that grades anchors must not
      share code with the layer that produces them, the same converter-blind rule
      as the token gate — and `test_anchor_parity.py` is what keeps the three
      implementations honest. `normalize_title`'s `_NUM` is fixed so a *dotted*
      number may be glued to its title (`1.2reference documents`) while a bare
      integer may not, because `3D layout` must not become `d layout`.
- [x] **P4.4 — Honest summary numbers.** `max_depth` is tree depth;
      `largest_leaf_tokens` is published beside the unchanged
      `largest_section_tokens`, because leaves are what a consumer retrieves and
      embeds.
- [x] **P4.5 — Table nodes.** `{table_id, line, rows, cols, has_header}`, with
      `table_id = tbl-sha1(section anchor + normalised header row + ordinal)[:10]`
      — content-derived, so inserting an earlier section moves `line` and not the
      id. `rows` and `cols` use the same conventions as `md_structure`, so the
      outline and the fidelity gate state the same number about the same table.
- [x] **P4.6 — Bound the ALL-CAPS heuristic.** The separating idea is
      grammatical: a heading is a noun-phrase **label**, a callout is a **clause**.
      Bounded by length, word count, terminal punctuation, and the presence of a
      determiner / pronoun / auxiliary / modal / negation — with `OF`, `AND`,
      `FOR`, `IN`, `TO`, `ON` deliberately absent so `THEORY OF OPERATION` and
      `TERMS AND ABBREVIATIONS` survive. Stated honestly: a callout with no clause
      word and six or fewer words (`POWER DOWN ALL NODES FIRST`) is still promoted.
      Perfect separation is not available from shape alone; the bound removes the
      common class.

## P5 — Metadata: fill the fields from evidence already on disk

The schema is good. The extractor ignores what the pipeline already proved.

- [x] **P5.1 — Deterministic `title`.** `title` **stays tier 2** — naming a
      document is judgement, and a model that read the body can beat any rule.
      What changed is that it is never PENDING, because a floor and a ceiling are
      different things. *Which floor a model may replace is decided by where the
      floor came from*: `source_title` is `extracted` (the author typed it into
      the document's own properties — evidence, protected, refused with
      `kept-extracted`), while a first heading or a filename stem is `derived`,
      because both are inferences that can be junk (`1. Introduction`, `Copy of
      report FINAL v3`). Authored-wins is untouched and still checked first.
- [x] **P5.2 — Deterministic `abstract` floor.** Scans from the outline's first
      `line_span` so detected TOC furniture is behind it, and falls back to the
      top of the body when there is no `structure.json` — a floor must not be
      conditional on a file that may be absent.
- [x] **P5.3 — Verified links.** The grep the plan asked for, run on HEAD:
      `enrich_metadata.py` had **no** match for `structure` or `outline` and one
      for `links` — a docstring. It never opened the file holding the evidence.
      Now every outline `{text, url, line}` becomes a `links` record carrying
      `ref: "#<section anchor>"` at `source: extracted`. The origin is recorded
      **per record, not per field**: a single `extracted` stamp on the block would
      vouch for the model's guesses too, so the field-level provenance reports the
      *weakest* origin present. A document with no links still gets a provenance
      record — "looked, found none" must never read as "never looked".
- [x] **P5.4 — A permalink.** `--source-base-url` / `DOC2MD_SOURCE_BASE_URL` →
      `meta.source.url`. With no base the url is still written, percent-encoded
      and relative: `uri` is a filesystem path (spaces, `#`, `?`) and cannot be
      pasted into a link, `url` always can. A base is never invented.
- [x] **P5.5 + P5.6 — one decision, not two.** Uniqueness and canonical identity
      are the same problem, and solving them separately is what produced two
      namespaces in the first place.
- [x] **P5.7 — Section-anchored knowledge.** `relations` now require `s/p/o/ref`,
      records and group members require a `ref`, and `request_spec` publishes the
      required keys plus a **SECTION ANCHORS** block — so the constraint shapes
      generation instead of only costing an answer. Rejections are named
      (`missing-required-<keys>`, `ref-not-a-fragment`, `ref-not-an-anchor`). With
      no anchors supplied the check is **skipped, never passed**: this package
      never sees the body and must not pretend to have resolved a pointer.
- [x] **P5.8 — `next_review_due`** derived from `last_reviewed + review_cadence`.
      The `authored_only` question answered rather than dodged: the flag means *no
      tier-2 machinery may write it* — a model must not guess a review date. This
      is not a guess. Both inputs are authored, the rule is arithmetic, and the
      output restates a commitment the person already made. Nothing is written
      when either input is missing, and a model's value is still refused outright.
- [x] **P5.9 — Prune what nothing reads.** Deleted with the reason recorded:
      `classification` (a second spelling of `confidentiality`), `aliases`,
      `prerequisites`, `out_of_scope`, and — found by the new inventory test —
      `short_title`. `requirement_level` deleted from `vocab.yaml`, which had said
      to delete it if body-level extraction never landed; it did not.
      `_provenance.tier` dropped (a pure function of the field name), and in
      exchange `value_sha` is now written for **every** machine source rather than
      only `generated` — which is what makes a hand-edited harvested link
      detectable. `SCHEMA_VERSION` 2 → 3, because an inventory move is exactly
      what that number versions. Existing values of removed fields are *carried*,
      not deleted; they simply stop being requested or graded.
- [x] **P5.10 — Entity group governance.** An invented group with untyped members
      no longer passes acceptance unchallenged.
- [x] **P5.11 — Corpus soundness at scale.** Measured before deciding, over 1000
      synthetic specs: **24,630** distinct `keywords` terms, **44.5%** used by a
      single document, 6,526 promotion candidates printed on one ~100 KB line —
      and `keywords.singleton_rate` reported **`0.000 → ok`** throughout, because
      it counts only *promoted* terms. The registry was drowning and the health
      line said fine.
      `_PAIRWISE_CAP`'s unsound first-two-character bucketing is replaced by an
      inverted index over multiset **bigrams** with a per-pair overlap floor —
      **complete**, zero false negatives, not an approximation. The width is
      derived, not chosen: a shingle width `q` gives a guarantee only when
      `t > 2(q-1)/(2q-1)`, so at the shipped threshold 0.78 trigrams are *unsound*
      — the trigram version was built and measured, and it **dropped 616 of
      101,044 true pairs while reporting itself complete**. Verified against the
      exhaustive sweep: 101,044 pairs both ways, 0 missed, 0 extra, 59.6 s → 15.3 s.
      Orientation is preserved because `difflib.ratio()` is **not symmetric**
      (0.786 one way, 0.429 the other on a measured pair). Two new findings —
      `synonym-sweep-scoped` and `registry-flood` — carry the excluded counts, so
      a narrowed sweep can never read as a complete one.
- [x] **P5.12 — One unreadable bundle no longer buys the corpus an amnesty.** The
      enumerated list of what a single unparseable file used to switch off *for
      every other document*: `see_also` resolution, the whole graph report
      (dangling refs, incomplete relations, endpoint coverage, orphans), both
      schema-skew gates, all three coverage gates, and vocabulary usage — reported
      as one INFO line. `partial` now means only "the operator asked for a subset",
      which is the legitimate reason it exists; an unreadable document is an ERROR
      **about that document**, plus a warning naming each affected check with its
      `N of M` denominator. Whatever is still skipped is named individually.
- [x] **P5.13 — Promotion no longer invalidates the whole answer cache**, and
      `kb_lint --promote` writes `config/vocab.yaml` through the same restricted
      YAML reader that parses it, so the round-trip is closed.

## P6 — Regrade

- [x] **P6.1** Re-run the four-lens adversarial review against a fresh live run.
      Run 2026-08-21 with **six** lenses rather than four — the four output
      dimensions, plus one whose only job was to make the pipeline ship a
      corrupted document with every gate green, and one attacking **the grader
      itself**, on the principle that a rubric which can be talked into an A is
      worth less than no rubric. Findings become new slices here.
- [x] **P6.2** Every ring green, measured rather than assumed:

      | Ring | What it reproduces | Result |
      |---|---|---|
      | host `python3` (3.6.8) | the interpreter the office lane must run on | 984 passed, 0 failed |
      | 3.6.8 with PyYAML blocked via `sys.meta_path` | CI's `python:3.6-slim`, which installs only pytest | 984 passed, 0 failed |
      | `python3.11`, pytest only, no Pillow | CI's `tests-modern` | 984 passed, 0 failed |
      | `evals/run_eval.py --skip-pdf` | the pinned corpus | 19 pass, 0 fail, 3 skip |
      | `scripts/grade_output.py` | this rubric | 32 pass, 0 fail, 0 skip |

      Those are the **2026-08-21** figures and are kept as a dated measurement, not
      as a current one. After P7 and P8 the same five rings read 1373 passed /
      90 skipped, 19/0/3, and `OVERALL A (34 pass, 0 fail, 0 skip, of 34)` — the row
      count moved 32 → 33 → 34 as `C1s`/`D3s`/`D5s`/`E3s` and then `C6s` were added.
      A number in a log entry describes the day it was taken; the numbers that must
      track the code live in the rubric tables above.

      Note the repo's own `.venv` reports ~20 failures in the caption and figure
      tests. Those are the **known Pillow artifact** — the venv carries Pillow as
      a docling dependency, which changes the synthetic-PNG path; neither the host
      nor CI's `tests-modern` has it, and the failures are confined to
      `image_caption` / `caption_bundles` / `validate_figures`, none of which this
      work touches. Reproducing CI honestly meant finding an interpreter with
      pytest and no Pillow, not shrugging at the venv.
- [x] **P6.4 — A defect the regrade itself surfaced.** The eval went 19/0, then
      18/2, then 17/3 on consecutive runs while other work used LibreOffice.
      Chasing "flaky" rather than shrugging at it found the cause: the office
      lane's `soffice --convert-to` carried **no `-env:UserInstallation`**, so
      every invocation shared `~/.config/libreoffice` — and LibreOffice
      single-instances on that profile, so a second concurrent conversion attaches
      to the first process and silently converts nothing. `evals/gen_corpus.py`
      had isolated its profile all along; the lane had not. Any sharded corpus,
      CI matrix, or second user on the host was racing. Fixed with a throwaway
      profile per conversion; six consecutive eval runs under the same load are
      now clean, and `test_each_soffice_conversion_gets_its_own_user_profile`
      keeps it that way. *A flaky test is a defect report you have not read yet.*
- [x] **P6.5 — The regrade broke its own gate, and the gate got wider.** Before
      trusting the six lenses, I attacked `structure_fidelity` myself, and it
      fell. Swapping two values between rows of an escalation table — SEV1 now
      paging the platform rota instead of the payments rota — passed **every**
      gate: `token_recall: 1.0`, `structure_fidelity: pass`, zero deltas, zero
      structural errors. So did swapping two steps of a numbered procedure. The
      compared facts were counts and *dimensions*; a value that moves without
      changing either is invisible to all of them, and the token gate is a
      multiset, so order was never in scope there either.
      Closed by comparing **content in place**: `tables` now carries the tokens of
      every cell, and `list_item_words` the text of every item in order. Content
      is compared as tokens, so escaping and emphasis markers are not differences,
      and a vertical merge is forward-filled on *both* sides because repeating the
      value is declared policy. Both attacks are pinned as regression tests that
      assert the old gate is blind and the new one is not. No false failures: the
      whole eval corpus and the adversarial fixture still pass.
- [ ] **P6.3 — NOT YET. The A was claimed, attacked, and taken away.**
      `grade_output.py` printed `OVERALL A (32 pass, 0 fail, 0 skip)` and all three
      rings were green. Six adversarial lenses then reproduced, end to end through
      `build_bundle.py` and cross-checked against LibreOffice reading the same
      bytes, enough to refute it:

      | dimension | the grader said | the review found |
      |---|---|---|
      | A `document.md` | A | **D** |
      | B `report.json` | A | **C** |
      | C `structure.json` | A | **D** |
      | D metadata | A | **C** |
      | E documentation | A | **B+** |

      The decisive piece of evidence: a Word document whose headings use a
      corporate template style (`NimbusH1 basedOn="Heading1"` — the most ordinary
      thing a real `.docx` does) converts with **every heading deleted**.
      LibreOffice renders two `<h1>`; doc2md emits four undifferentiated
      paragraphs, `structure.json` publishes a single `(preamble)` node, and the
      report says `status: ok`, `token_recall: 1.0`,
      `structure_fidelity.gate: "pass"`, `deltas: []`, `warnings: []`. That is
      verbatim `end-goal.md` §1's own definition of a document that "is not
      lossless in any useful sense", shipping with the pipeline's unanimous green
      light — one phase after the phase meant to make it impossible.

      **The lesson is about the rubric, not only the code.** Thirty-two rows all
      passed over a document that had been silently destroyed, because every row
      graded a property the corpus under grade happened to exhibit. A rubric is a
      set of questions, and a question nobody thought to ask is indistinguishable
      from a question that passes.

      What the review could *not* take away is worth recording too: the P0.1 bug is
      dead; `token_recall` never moved off 1.0 under any drop that could be
      constructed; the new content facts caught every permutation, transposition
      and cell-rotation attempted; the `empty_source` / `unmeasured` /
      `_nothing_to_grade` discipline held. The gate that exists is well built — it
      is simply not yet a gate on everything still broken.

      **Re-examined after P8 (2026-08-24). Still not ticked, and the reason has
      changed.** The two structural gaps that originally blocked this row are
      closed, so it is worth being precise about what remains.

      *What the rubric asks now that it did not before* — all five of these landed
      in P8 and every one makes the rubric strictly harder to satisfy:

      1. **A suite row names the specific tests that demonstrate it**, and is
         answered from *their* outcomes. Fifteen rows previously asked only "did
         this FILE exit 0?", so any green test in the file earned the row. Now a row
         **fails** when a named test is deleted or renamed, an unrelated green test
         cannot earn it, and an unrelated red one cannot sink it. This bit during
         development: the grader printed `FAIL C1s … the evidence this row is graded
         on has disappeared` and `OVERALL B+`.
      2. **A skip is no longer a pass.** `run_suites` read a subprocess exit code as
         a verdict, so a wholly-skipped check, a collection error, an import failure
         and a missing target all reported PASS. Each is now `skip`, and a skip is
         not a demonstration.
      3. **Dimension C grades per document.** `_c1`, `_c3`, `_c4` and `_c5`
         accumulated their denominators corpus-wide, so a bundle that lost its
         entire outline, or every one of its table nodes, was covered by a healthy
         sibling. Worse, `_c3_anchors` *excluded* a bundle whose body renders no
         heading and then printed an evidence line asserting every anchor resolved.
      4. **E2, E3 and E4 no longer share one file's verdict.** The nine checks in
         `test_docs_parity.py` are partitioned between them; no test backs two rows.
      5. **`--json` cannot emit a document that reads as a grade when the run
         broke.** The exit-2 path emits `{"error": …}` and only that.

      *Why it is still not ticked.* The rubric printed **A, 34 of 34** on the tree
      that had just produced 65 confirmed findings, and it printed A again after
      they were fixed. The row count and the strictness both went up and the letter
      did not move — which is the same signal P6.3 recorded the first time. Two
      concrete things stand between here and a defensible A, and neither is a matter
      of wording:

      *(The third — "the A1 gate cannot see a heading-title swap on any real
      document" — is now closed. `docx_source_structure` supplies `heading_path` and
      `thematic_breaks`, so no office bundle reports an `unmeasured` list any more
      and the swap fails the gate. Re-measured on the shipped corpus after
      `--regen`: `kestrel-adversarial.docx` compares **14** of 15,
      `kestrel-clock-spec.docx` **10**, `kestrel-readme.docx` **2** — a one-heading
      README exhibits two facts and honestly says so, which is what `compared`
      counting evidence rather than schema is for.)*
      - **Four rows still report green over a class they cannot see.** D5 checks a
        `ref` is present, not that it resolves to the right section (it passed over
        idx 9 throughout). B5 names six tests, none of which exercised the
        missing-row or no-`--src` verdicts (it passed over idx 27 and idx 51). C6
        greps one literal (it passed over idx 5). E4 grades whether
        `test_docs_parity.py` passes, not what it can see (it passed over idx 33).
        In each case the *defect* is fixed and the *row* is unchanged.
      - **Three PDF expectations are never evaluated on any ring but the nightly
        one**, because `grade_output.py` hardcodes `--skip-pdf`. A5 now prints that
        census in its evidence, and no row grades it. Closing it needs a policy
        decision — a PDF-half row would make dimension A permanently non-A off the
        nightly ring — not a plan edit.

      Ticking this row requires the ground-truth half of `heading_path`, rows that
      grade the four classes above, and an answer on `--skip-pdf`. Until then the
      letter at the top of this document is the honest one and `grade_output.py`'s
      is the narrower one.

## P7 — Close what the regrade opened

Every item was reproduced end to end and cross-checked against an independent
Office implementation reading the same file. None is theoretical.

- [x] **P7.1 — A renumbered or dissolved procedure, five ways.** The literal
      marker `1.` is emitted for every ordered item and no list start is ever
      written; `w:start`, `w:lvlOverride` and `w:startOverride` are read nowhere.
      A picture inside step 2 closes the list, so steps 3 and 4 render as 1 and 2
      (LibreOffice emits `<ol start="3">`). Numbering carried by a paragraph
      *style* — Word's built-in *List Number* — dissolves the list entirely, and
      **both sides are blind for the same reason**: the `_collect_text` symmetry
      hole of P3.7, reappearing inside the structure gate.
- [x] **P7.2 — `w:gridBefore` shifts a row one column left.** A register map
      publishes a register *named* `0x04` at *offset* `RO`, `status: ok`.
- [x] **P7.3 — `w:basedOn` is never followed**, so a template heading style
      deletes every heading (above).
- [x] **P7.4 — Four heuristics fabricate or reparent outline nodes.** The keyword
      branch is unbounded: `^(chapter|section|appendix|part)\s+[0-9IVXLA-Z]` under
      `re.I` makes `[A-Z]` match *any* letter, so `"Section prose."` is a level-1
      heading. `is_heading` returns `s.count("#")` — the count anywhere in the line
      — so a heading reading `Issue #42 metastability` is published at level 2.
      There is no fence tracking, so a shell comment in a transcript becomes a
      section. `_infer_levels` fires on office bundles despite a comment claiming
      it cannot.
- [x] **P7.5 — The anchor schemes disagree and C3's cross-check is dead code.**
      `# Overview - part 2` → `overview-part-2` from `sections`,
      `overview---part-2` from `kb`. `_rubric._c3_anchors` reads
      `knowledge["body_anchors"]`, which is never written, so half that row has
      never run.
- [x] **P7.6 — Enrichment records no provenance at all**, while deciding
      `meta.id`, `meta.uid` and `meta.source.url` — the fields rows D2/D3/D4 grade.
      Against the owner's goal ("the switches that were used… recorded per run"),
      this is the central B failure.
- [x] **P7.7 — Absolute host paths reach published artifacts.** `redact_argv`
      matches flag names by exact string while argparse accepts prefixes, so
      `--sr /tmp/x` sails through; `--only` and `--tokenizer` are not redacted at
      all. Two `CLAUDE.md` files forbid this, and the guard test passed only
      because it spelled its flags out in full.
- [x] **P7.8 — `replay_run.py` discards the `--src` it was handed** when the
      recorded run used the default, and prints a command with no `--src` — which
      reads whatever `$DOC2MD_SRC` resolves to at replay time. `divergences()`
      also names three classes it never reads: `run.tools`, `_env_present`, and
      the `corpus_sha256` it prints.
- [x] **P7.9 — `unique_id` was not unique by construction.** `spec.docx`,
      `spec.pptx` and `spec.xlsx` — an ordinary trio in a real corpus — all became
      `specs/spec`, and both writing scripts exited 0. The extension was stripped
      *before* the faithfulness test, so it was excluded from the comparison and
      from the id. Fixed by keeping the extension with its dot and comparing
      **character for character** rather than after lowercasing: on a
      case-sensitive filesystem `spec.docx` and `Spec.docx` are two files, and
      both rendered to one id. 80 paths → 80 distinct ids, verified.
- [x] **P7.10 — D5 and `kb_lint` disagreed about a lede hyperlink.** A URL in the
      prose above the first heading sits in a region a renderer emits no fragment
      for, so `harvested_links` correctly omits `ref` — and the rubric called that
      a failure while the linter called the same bundle clean. The record does
      carry the exact body line it came from, which answers "which part of the
      document says this?" better than a fragment that resolves nowhere. A
      *model-proposed* record gets no such latitude.
- [x] **P7.11 — Two documentation claims were false.** `quality-plan.md` said
      `<stderr>` survives verbatim; it does not, and cannot — `<stderr>` matches
      CommonMark's raw-HTML tag-name production, so unescaped a renderer swallows
      it. The claim was corrected and recorded as a deviation rather than the
      probe list quietly trimmed to fit. `provenance/CLAUDE.md` asserted that "the
      parity test enforces" decision-code documentation; no such test existed, so
      one was written rather than the claim softened.

**How P7.1–P7.8 were closed, and how that was checked.** Each fix was verified in
**both** directions, which is the discipline the phase is really about: the
corruption must now fail the gate, *and* ordinary documents must still pass it. A
gate that refuses more correct documents than it catches wrong ones is not a gate.
Reintroducing each defect in the converter alone now produces
`token_recall: 1.0, gate: pass` from the token gate and `structure_fidelity:
fail` with a named delta — `ordered_numbers [1,2,3,4,5…] vs [1,2,3,1,2…]`,
`tables` with the row's cells one column out, `headings {1: 2} vs {}` — exit 1,
and no `document.md` written.

Two of the fixes came with **named drops** rather than silence, because CommonMark
cannot express what Word meant: `decimalised_list_numbering` (an `A.`/`iii.` list
keeps its position and loses its label) and `lifted_text_boxes`. A visible loss
beats a silent one.

The compared fact vector grew to **thirteen** facts here: `ordered_numbers` joined
it, because a per-depth *count* and a *total* both stay constant when a list splits
or restarts — which is precisely why five separate constructs could renumber a
procedure with every gate green. **It is now fifteen** — see P8, where
`heading_path` and `thematic_breaks` joined for the same reason. Both are compared,
and on a real office document both are reported **`unmeasured`**, because
`docx_source_structure` does not produce them yet: the emitted half and the
comparison exist, the ground-truth half does not. Say that rather than "the vector
is fifteen" and stop, which would be the same shape of claim this phase exists to
refuse. The authority is `backend.validate._mdcheck._FIDELITY_FACTS`; this paragraph
is a log of *when* each fact arrived, which is why the earlier "eleven" at P3.3
stands rather than being back-dated.

- [~] **P7.12 — The regrade's remaining findings.** The six lenses produced far more
      than the blockers above; the full report is in the session transcript. Carried
      forward rather than dropped, roughly in order of harm. **P8 closed four of the
      nine**; the entry stays open for the rest, and each line below says which fix
      closed it or why it stands. **None of these is speculative** — each was
      reproduced end to end.

      | # | Finding | Status |
      |---|---|---|
      | 1 | A `--force` rebuild can publish a `knowledge.json` describing the previous document | **partly closed** by P8's withdrawal fix (idx 7). A rebuild that **fails** can no longer leave the previous `document.md`/`structure.json`/`knowledge.json` standing — they are renamed `.stale` and the bundle is refused by enrichment. A rebuild that **succeeds** overwrites the body and the tree but still does not touch `knowledge.json`, so the window between a successful rebuild and the next `enrich_metadata` run is unchanged and open. Do not read the `.stale` fix as closing this row. |
      | 2 | `replay --compare` can print `REPRODUCED` for a bundle it did not reproduce, and its exit codes cannot distinguish the two | **closed** by P8 (g9). `REPRODUCED` may now only be printed when two hashes were actually compared: two empty hashes report UNVERIFIED at exit `4`, `--out` pointing at the root being replayed is refused at exit `1`, and `--compare` without `--execute` says so. **Residual, still open:** nothing verifies the replayed bundle beyond that one `markdown_sha256`, and `--compare` on an in-place stage (`enrich_metadata`) is a meaningless comparison — it now refuses or reports UNVERIFIED instead of lying, but whether to refuse it outright is a policy call. |
      | 3 | A second run over a changed corpus keeps the stale bundle and logs a clean skip | open, untouched. |
      | 4 | `section_id` can collide and produce a self-parent cycle | open, untouched. |
      | 5 | `fingerprint` is byte-identical across the exact P0.1 renumbering bug | open, untouched. |
      | 6 | Nothing verifies `markdown_sha256` in the metadata lane, so a stale one propagates into `knowledge.json` | open, untouched. |
      | 7 | The `ref` rule does not bind for a mapping-shaped `entities` group | **closed for the list shapes** by P8 (idx 12), and the original wording *understated* the defect: it bound for neither the list shape nor any record list — all 13 declared required slots were unenforceable. It stays true and open for the **mapping** shape, which is exempt **by design** (`_schema.py:229`): a mapping keys its members by name, so demanding `name` there would reject the one shape that cannot omit it. |
      | 8 | Every table's first row is forced into the header, so a headerless table silently relabels a data row | open, untouched. |
      | 9 | Foot/endnote and comment ANCHORS are deleted from the body while the notes survive as unattributable bullets | open, untouched. |

      **A tenth, added by P8 and not on the original list:** a `build_bundle.py` run
      interrupted at any point (Ctrl-C, SIGTERM, OOM) leaves every finished bundle
      pointing at a `runs.jsonl` row that is never written — `_append_run` runs only
      after the whole loop and `build_one` is not wrapped in a `try/except`, while
      the PDF lane already is. One `kill -INT` produced 213 such bundles. P8 fixed
      the *reachable* trigger (a damaged package no longer aborts the batch) and made
      those bundles **report** honestly instead of printing an all-clear, but it did
      not stop them being produced.

---

## P8 — The third review: 65 confirmed findings

A twelve-lens adversarial review of the P7 tip (2026-08-23) filed **65 findings**
against the code, the tests, the CI configuration and this document. Every one was
then handed to a **skeptic whose job was to refute it**, working from the code
rather than the report; every one survived, and eleven came back *wider* than
filed. That two-stage process is why this list has no "probably" in it and why the
`Status` column can be trusted: a finding here is a defect that was reproduced
twice, by two parties with opposite incentives.

**62 fixed, 3 open.** Every fix was verified in **both** directions — a regression
test that fails on the code as it was and passes after, *and* evidence that ordinary
correct documents still pass — and the tree was measured at each handover rather
than assumed. Where a fix was verified in only one direction it is not recorded as
fixed. The rings moved **1129 → 1373 passed** (all three), entirely from added
regression tests; skips moved 85 → 90 (five new CommonMark differential cases that
run only where a parser is installed); the eval held at 19 pass / 0 fail / 3 skip
throughout, and no eval expectation was ever weakened — the corpus **gained**
adversarial shapes instead.

**Three verdicts changed for the right reason, and they are improvements, not
regressions:** rubric row `C6` was renamed to what it actually measures rather than
being left overclaiming; `structure_fidelity.compared` dropped from 13 to 9 on a
real corpus bundle because it now counts facts that **observed something** rather
than facts the schema lists; and `meta_coverage` can now report `invalid > 0` on
payloads it previously called `complete`.

### The shape of what was found

Eight of the sixty-five are worth naming as a class, because they are the same
defect wearing different clothes and they are the reason this review was worth
running: **a gate that reports success over the damage it is looking at.**

- `_c3_anchors` **excluded** every bundle whose body offered no anchor and then
  printed an evidence line asserting all anchors resolved (idx 4).
- `run_suites` read a subprocess exit code as a verdict, so a wholly-skipped check,
  a collection error and a missing file all read as PASS (idx 48).
- `_count_marked_spans` fused formatted runs across every block boundary, so the
  ground truth independently produced the *converter's* wrong answer and agreed
  with it (idx 2); `_run_marks` mirrored the converter's "direct formatting only"
  rule, so a document whose emphasis lives in a character style read zero on both
  sides (idx 21).
- `outline_coverage`'s `uncovered_lines` was **0 by construction for every possible
  input** — a dropped heading's lines are re-attributed to its ancestor, whose span
  already covers them (idx 28).
- `meta_coverage` could not count a `groups`/`records` field invalid, so `doc_meta`
  reported `complete` over a payload `kb_lint` was reporting as six ERRORs (idx 11).
- The required-key rule was a *filter over vocabulary-bound sub-keys*, so none of
  the thirteen declared required slots was ever enforced (idx 12).
- `test_docs_parity` matched key **leaf names** against a bag of words harvested
  from anywhere in the reference, so `structure_fidelity.ratio`/`.note`/`.reason`
  were "documented" by three unrelated blocks (idx 33).

The mirror class is here too and matters as much: **gates that failed correct
documents.** A docx table cell with two paragraphs failed the new fidelity gate on
a byte-perfect conversion, because the markdown reader tokenised the `<br>` the
converter joins cell paragraphs with into a phantom word `br` (idx 17). Intraword
italic (`*n*th`) failed the recall gate although both marko and markdown-it render
it as `<em>` (idx 19). A fully-governed document was hard-failed by a cardinality
rule that is arithmetically incapable of meaning anything on a closed vocabulary
(idx 13). Those were fixed by **correcting the rule**, never by relaxing it into
silence — and where a rule could not be made correct it now reports `unmeasured`.

### Status of every finding

Grouped by the area that owns the fix. Severity is the reviewer's; `Status` is what
the evidence supports, not what was attempted.

**OOXML structural ground truth** — `src/backend/ingest/_ooxml_struct.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 2 | blocker | `_count_marked_spans` fused formatted runs across paragraph and cell boundaries, so the gate passed markdown that dropped every emphasis after the first block | **fixed** — runs are filed under their nearest block ancestor and coalesced per block |
| 3 | blocker | `_own_text` welded sibling paragraphs in a table cell into one fabricated token (`primary`+`rota` → `primaryrota`) | **fixed** — new block-aware `_block_text` keeps document block boundaries as a space |
| 21 | major | The ground truth mirrored the converter's "direct formatting only" rule, so style-borne emphasis read zero on both sides and agreed | **fixed** on both sides — the ground truth resolves the style cascade (`w:basedOn`, tri-state toggles), and the converter was taught the same cascade independently |
| 22 | major | A nested table counted as a second table although the converter flattens it into its cell | **fixed** |
| 23 | major | Chart/SmartArt/SVG section accounting was wrong four ways, including two errors that cancelled | **fixed** — one section per kind, only when that kind carries text; embedded SVG figures were never counted at all |
| 24 | major | A blank paragraph split the fence count and a table did not | **fixed** — `code_run` resets exactly when a block is actually emitted |

**OOXML → markdown converter** — `src/backend/ingest/_ooxml_md.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | blocker | `raw=True` did not mean raw: code paragraphs lost all indentation and internal spacing, publishing `if dev.ready:` with no body at recall 1.0 and a matching fence count | **fixed** — tabs, breaks and leading indentation survive; `rstrip` only |
| 18 | major | Emphasis markers glued to an adjacent word emitted delimiters CommonMark cannot open, so the bold was lost from the render | **fixed** — CommonMark's flanking rule is applied and one rendering unit is moved out of the span when the delimiter is blocked. Verified against marko 2.2.3 and markdown-it-py 4.2.0; fidelity-gate failures over a 3240-case fuzz fell 786 → 84 |
| 20 | major | The bracket-escape exemption was decided per **run**, so adjacent runs `[3]` and `(page 12)` fabricated a link | **fixed** — the test is made against the assembled paragraph text |
| 36 | major | A body paragraph starting with `##` became a heading; a paragraph of `-----` or `[REG]: 0x04` was **deleted from the render** with both gates green | **fixed** — `_esc_lead` neutralises the block-opening shapes and nothing else |

**Markdown reader and the fidelity gate** — `src/backend/validate/_mdstructure.py`, `_mdcheck.py`, `src/backend/ingest/_markdown.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 17 | major | Any docx table cell with two paragraphs **failed** the gate on a byte-perfect conversion (phantom token `br`) | **fixed** — the reader strips the one unescaped `<br>` this project emits; `\<br>` (the document talking *about* the tag) stays content |
| 19 | major | Intraword italic (`*n*th`) **failed** the recall gate although the markdown is correct CommonMark | **fixed** — the intraword ban belongs to `_` alone. Recall failures over a 6075-case fuzz fell 1512 → 216 |
| 35 | major | A paragraph of hyphens was deleted from the render *and* from the text layer, with every gate green | **fixed** on both halves — the reader only deletes what a renderer deletes, and `thematic_breaks` is now a compared fact |
| 45 | minor | A fenced block's close was container-blind, so a mis-indented closing fence made a damaged document's facts byte-identical to the correct one | **fixed** — the close is judged against the container's content column |
| 47 | minor | `---` after a GFM table deleted the whole table from the fact vector and invented an h2 | **fixed** — table candidacy resolves before the setext rule, with GFM's column-parity requirement |
| 25 | major | No ordered, text-bearing heading fact: **exchanging two section titles passes both hard gates** | **fixed**, in two halves. `md_structure` emits `heading_path` and the compared vector grades it; `docx_source_structure` now derives the same fact from the XML — same `[a-z0-9]+` tokenisation, `min(level, 6)` because markdown stops at `######`, and written in the *same function* as the level histogram so the two cannot drift apart. The synthetic headings (`## Footnotes`, `## Charts`, `## Figures`, …) are emitted in render order, which a count-only fact never had to get right. Verified on the real corpus in both directions: three office documents `gate: pass` with nothing `unmeasured`, and exchanging two of `kestrel-clock-spec.docx`'s titles keeps `token_recall: 1.0` while the gate reports `fail` on `heading_path` alone. `compared` for that document rose 9 → 10, the rise the previous revision predicted |

**Outline and chunking** — `src/backend/sections/`

| # | Sev | Finding | Status |
|---|---|---|---|
| 5 | blocker | Ordinary prose starting with a number became a level-1 heading and reparented the document | **fixed** — the numbered branch is bounded by the same label-vs-clause test the keyword branch uses, plus a title-word cap |
| 6 | blocker | An ATX heading over 120 characters was silently deleted from `structure.json` while `document.md` kept it | **fixed** — the length cap bounds the three heuristics and not explicit markup; the title clip that would have published a truncated anchor is gone |
| 28 | major | The outline-coverage gate could **never** fail | **fixed** — an ATX-marked heading line that no node *opens on* is uncovered even when an ancestor's span contains it. Verified by re-running the idx-6 damage through the real builder with only the new check in place: `uncovered_lines: 1` where the old check said 0. A 20,000-document fuzz produced no false degrade |
| 29 | major | `_is_nested_outline` accepted any ascending measurement series, reshaping five power rails into a two-level tree | **fixed** — every stated parent's children must start at 1. Deliberately weaker than "contiguous", so `2.1, 2.2, 2.4` is still believed |

**Metadata enrichment** — `src/backend/kb/_enrich.py`, `scripts/enrich_metadata.py`, `scripts/build_bundle.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 0 | blocker | A deterministic field was fingerprinted once and never refreshed, so the **second** change to `--namespace` was silently ignored | **fixed** — every deterministic field is re-stamped every run, and an inherited hand-written value is recorded as `authored` rather than credited to a rule that no longer produces it |
| 7 | blocker | A failed rebuild left the previous run's bundle published, and enrichment then certified it | **fixed** — a failing conversion **withdraws** the previous artifacts to `.stale` (renamed, not deleted: an environmental failure must not cost the only good copy), and enrichment refuses a bundle whose report says `failed`. **The same defect is still live on the PDF lane** (`scripts/build_pdf_bundle.py`) — see "still open" below |
| 9 | major | `harvested_links` re-derived the section anchor from the node **title**, so two chapters with a `## Overview` each filed both links under `#overview` | **fixed** — it reads the anchor the outline publishes |
| 10 | major | Revalidation judged harvested evidence by the model-answer rules and permanently deleted it | **fixed** — evidence records are carried through; and the laundering hole is closed in the same change, so a model cannot exempt its own guesses by claiming `source: extracted` |
| 11 | major | `meta_coverage` could never count a `groups`/`records` field invalid | **fixed**, kind-aware. Absent is still `pending`, never `invalid` |

**KB linting and corpus gates** — `src/backend/kb/_lint.py`, `_corpus.py`, `_derive.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 8 | major | `body_anchors` read ATX headings inside fenced code blocks | **fixed** — the P7.4 fence rule, restated independently in `kb` (the three anchor implementations stay separate on purpose) |
| 12 | major | None of the thirteen declared required record keys was enforceable | **fixed** — required-key checking runs independently of the vocabulary loop and covers record lists *and* list-shaped group members. `ref` is version-gated to WARN below schema v3, because every v2 block legitimately lacks it |
| 13 | major | The cardinality gate **hard-failed a valid, fully-governed document** | **fixed at the root**, which is arithmetic: `distinct` is capped by the term count, so an adverse verdict on a closed vocabulary is only reachable where it is forced by the size of the term list rather than evidenced by the document. Those rows now report `sparse`. See the deviations table — this is a stated reduction in what the per-document gate can catch, not a silent one |
| 14 | major | Skew measured the corpus against itself, so a uniformly stale corpus reported zero to backfill | **fixed** — `current` is the running code's version; a corpus *ahead* of this checkout is not called behind |
| 40 | minor | A wholly-unused closed vocabulary was reported by neither gate (a monotonicity inversion: using it *less* produced *fewer* findings) | **fixed** — new `vocab-dead` INFO, with the deferral to `coverage-absent` made conditional on that gate actually covering the vocabulary |
| 41 | minor | A case-only collision could never be recorded as deliberate in `lint.similar_ok` | **fixed** — the raw pair is stored too; and the hint now names *every* unaccepted pair a group needs, not the first two |
| 42 | minor | `_NON_SLUG_U` kept a literal `^`, so a non-ASCII title yielded a slug containing `^` | **fixed** |
| 43, 52 | minor | `alias_suggestions` key order was not reproducible — set-iteration order leaked into a committed JSON artifact | **fixed** (one cause, two filings) — sorted at the emission point, not the traversal point. Pinned by an integration test over **separate processes** under four `PYTHONHASHSEED` values; the pre-existing determinism test calls `main()` twice in one process and is structurally incapable of catching this |
| 44 | minor | One mis-typed list field counted as two errors | **fixed** |

**The restricted YAML reader** — `src/backend/ingest/_yamlblock.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 15 | major | A block scalar blind-sliced an under-indented line, shaving its leading characters, deleting it outright when short, and manufacturing a paragraph break inside a folded block | **fixed** — it now raises, naming the line, its indent and the required indent |
| 16 | major | An apostrophe in a plain scalar opened a phantom quote state, merging flow-list items and absorbing trailing comments | **fixed** — three scanners that disagreed with each other and with the value parser are rewritten on one `_quote_mask`; a quote opens a scalar only at a token start. A 6000-document differential against PyYAML went from **499 silent divergences to 0** |

**The rubric and the grader** — `src/backend/validate/_rubric.py`, `scripts/grade_output.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 4 | blocker | `_c3_anchors` dropped any bundle whose body offered no anchor, then asserted they all resolved | **fixed** — such a bundle fails on its own account and is named. `_rendered_anchors` also reads through a fence mask, so a `#` in a shell transcript no longer shelters a published anchor |
| 26 | major | Dimension C accumulated its denominator corpus-wide, so a document that lost every table node was covered by a sibling | **fixed** — all four artifact rows in C grade per document |
| 48 | minor | `run_suites` turned a subprocess exit code into a PASS, so wholly-skipped checks read as proven | **fixed** — rows are answered from per-test outcomes; a skip, a collection error and a missing target are all `skip` |
| 62 | minor | `--json` printed pytest failure lines to stdout, so the machine-readable mode emitted invalid JSON | **fixed** — `--json` owns stdout completely |
| 64 | minor | `--json` emitted nothing at all on its documented exit-2 path | **fixed** — it emits `{"error": …}` and only that, so a consumer keyed on `summary` raises rather than reading a break as "no row failed" |

**Provenance and replay** — `src/backend/provenance/`, `scripts/replay_run.py`

| # | Sev | Finding | Status |
|---|---|---|---|
| 27 | blocker-class | `replay_run` printed "no divergences … same resolved settings" and exited 0 when the `runs.jsonl` row could not be joined — with the losslessness threshold halved | **fixed** — three classes now emit `UNVERIFIED` notes and a new exit code `4` |
| 49 | minor | Flag-prefix redaction rewrote ordinary non-path switches to `<path>` (`--ex 4000` → `--ex <path>`) | **fixed** — the flag-name list is deleted; the test is on the **value**, which is what the docstring always claimed |
| 50 | minor | An absolute host path escaped through `file://`, a bare `//abs`, `~user/`, a `k=/abs` pair and a dict | **fixed** — all five, so the leak invariant is *narrower* on balance despite deleting the flag list |
| 51 | minor | `_source_divergences` returned silently when it opened no file, while the verdict still claimed "same source bytes" | **fixed** — every way it declines to hash now says so |
| 53 | minor | `package_version`'s `[project]` guard was dead code, so a version from an earlier table won | **fixed** — the table is located, not assumed to start the file. The existing test could not fail and was rewritten so it can |

**Scripts and the transport layer**

| # | Sev | Finding | Status |
|---|---|---|---|
| 37 | major | `dropped_embedded_objects` could **never** fire on the LibreOffice lane — the member list was read from the pre-conversion source | **fixed** at the root: the effective package's member list is threaded out of the reader that opened it |
| 38 | major | One package with an unsupported zip compression method aborted the whole batch and orphaned every finished bundle's `config_ref` | **fixed** — an explicit `_ZIP_READ_ERRORS` tuple (not a blanket `except Exception`: an unreadable package must become the **named** `unreadable-zip` failure and a real bug must still crash loudly) |
| 39 | major | `coverage_report --fail-under` could not fail when **no** coverage records were found | **fixed** — nothing measured is not a pass. `--fail-under 0.0` still means "never fail" |
| 61 | minor | `open()` without `encoding=` crashed the docs generator under a non-UTF-8 locale | **PARTLY FIXED.** `scripts/render_vocab_doc.py` is fixed and pinned by a subprocess test under `LC_ALL=C`. **`src/backend/provenance/_code.py` still has five encoding-less `open()`**, and that half is worse: `package_version()` raises `UnicodeDecodeError` on `pyproject.toml`, the guard `except (IOError, OSError)` does not catch a `ValueError` subclass, so it escapes through `code_identity()` and **kills every `build_bundle.py` run mid-conversion**. Reproduced on this host today. See "still open" |
| 54 | minor | Four modules carry no `__all__` | **PARTLY FIXED** — `_covsummary.py` declares one. `_ooxml_struct.py`, `_mdstructure.py` and `_rubric.py` remain the only modules under `src/backend` without one (32 of 36 have one). Behaviourally inert: there is no `import *` anywhere. Note the reviewer's proposed fix was **refuted** on both halves and must not be applied — re-exporting these helpers from the package `__init__` would widen a surface two `CLAUDE.md` files require to stay tight, and flipping `public_api: no` would break the only consistent label signal in the tree |

**Tests and CI** — `.github/workflows/ci.yml`, `tests/`

| # | Sev | Finding | Status |
|---|---|---|---|
| 30, 32, 46 | major/minor | The CommonMark differential — 86 of the 90 skips, *including the anti-vacuity guard written to prove the differential is wired up* — ran on **neither** CI ring, so a green CI carried zero evidence from any of it | **fixed** (one cause, three filings) — `tests-modern` installs `marko==2.2.3` (pinned) and a step **fails the job if that file reports a single skip**. The 3.6 ring keeps skipping for its own documented reason. Measured both ways: a real regression that the hand-written half cannot see goes from *green* to a named failure |
| 31, 63 | major/minor | CI deselected the only two tests covering `scripts/coverage_report.py`, on the stale grounds that the script "was never committed" — it is committed (PR #8) | **fixed** — all four `--deselect` lines and the stale comment are gone; both tests pass on all three rings |
| 33 | major | `test_docs_parity` accepted undocumented artifact keys by cross-block leakage: it matched leaf names against a 440-word bag harvested from anywhere in the reference | **fixed** — the check is path-aware and block-scoped. It surfaced exactly one genuinely undocumented published key, which was **named in a self-expiring ledger** rather than waved through |
| 55 | minor | `kb._lint`'s container-shape gate had zero tests — deleting it left the whole suite green while four mis-typed containers went unreported | **fixed** — a test asserting the sorted `where` list of all six findings, not merely that a code appears |
| 56 | minor | `record-untyped`'s ERROR severity — which alone decides `kb_lint`'s exit code — was untested; downgrading it to WARN turned a corpus gate from exit 1 to exit 0 silently | **fixed** — and it is the only test in the repo that catches the downgrade |

Three CI gaps found in the same pass and closed with them: **`grade_output.py` ran in
no job at all** (the grade could slide from A to D with every check green — it is now
a step whose floor is the script's own exit code), **`kb_lint.py` ran in no job**, and
three jobs had no `timeout-minutes`.

**Documentation** — this file and the reference set

| # | Sev | Finding | Status |
|---|---|---|---|
| 34 | major | `output-contract.md` and `document-metadata.md` printed **v2-shaped payloads under v3 headers**: the sample relation carries no `ref` (schema v3 rejects it as `missing-required-ref` and stores nothing), `_provenance` shows a `tier` removed at v3, and `vocab_version: 1` where the shipped vocabulary is 2 | **fixed** in all four places, verified by round-tripping the documented sample through `accept_model_meta` — it was rejected before and is accepted now. **The root cause is not fixed:** the guard written for exactly this class (`test_shipped_vocabulary.py`) greps only the `schema_version` integer, so bumping the header line satisfies it while the block's body stays v2-shaped. See "still open" |
| 57 | minor | `output-schema.md` documented the wrong derivation for `meta.id`, the corpus join key — the worked example was verbatim the **pre-P7.9** output, wrong in both halves (it drops the extension, and its fingerprint is of the extension-less path) | **fixed**, verified by recomputing `unique_id` for the documented example and byte-comparing |
| 58 | minor | `document-metadata.md` named `classification` — deleted at v3 — as an authored-only safety field | **fixed**, and the doc now states the real set (the eight `authored_only=True` entries). The reviewer's harm framing was **wrong** and is recorded here so it is not re-derived: `confidentiality` is the actual boundary, it is closed-vocab and governed, and `kb_lint` does emit `unknown-field` naming `classification` — so no safety boundary was ever ungoverned. This was doc/code drift on a contract file |
| 59 | minor | The rubric cited seven test files and an eval probe that **have never existed**, and the published table was five rows short of the executable one | **fixed** — every cell names something that resolves, and `C1s`/`C6s`/`D3s`/`D5s`/`E3s` are published |
| 60 | minor | Three published fact counts disagreed with the code and with each other | **fixed** for the two live claims (rubric row A1's "twelve", and `output-contract.md`'s "5 of these fields live in knowledge.json" — it is six, and that line was wrong when it was written, not drifted). P3.3's "eleven" is **deliberately left**: it is a chronological log of what that slice shipped, and rewriting it would erase the record and make the later narration incoherent |

### Closed after the first write-up

The six items below were listed here as open when the P8 write-up was drafted,
because each crossed a file-ownership boundary the group that found it could not
cross. All six are now fixed, and each was verified in **both** directions — the
damage must fail, *and* ordinary input must still pass.

1. **idx 25 — the ground-truth half of `heading_path` (and `thematic_breaks`).**
   The highest-value item on the list, and the only one that let a damaged document
   publish. Closed: see its row above for the measured evidence.
2. **idx 61 — five encoding-less `open()` in `src/backend/provenance/_code.py`.**
   Closed with `encoding="utf-8"` on all five, and nothing else: widening the guard
   to `except Exception` or adding `errors="replace"` would have turned the crash
   into a silently empty or garbled provenance block, which is strictly worse.
   Verified by running `code_identity()` in a subprocess under `LC_ALL=C`.
3. **idx 54 — `__all__` on the remaining three modules.** Closed, and a check
   confirms every symbol each package re-exports is declared in its module's
   `__all__`, in both directions.
4. **The PDF lane's copy of idx 7.** Closed by calling the office lane's *own*
   `_withdraw_published` / `_clear_withdrawn` — one mechanism, not a second that can
   drift out of step. Pinned by an AST-level invariant rather than a fixture,
   because the PDF lane needs docling (nightly ring only) and a check that skips on
   every ring is not a check: it walks every branch that returns `status: failed`
   and fails if one of them does not withdraw. Confirmed to fail on the unfixed
   code, naming the branch.
5. **`decisions[].evidence.ext` is published and undocumented.** Closed in one
   change, as the equality assertion requires. `_UNDOCUMENTED_TODAY` is now empty.
6. **The doc-drift guard graded only `schema_version`.** Closed: it now round-trips
   the documented `knowledge.json` sample through the real `accept_model_meta`,
   staying tolerant of the deliberate `…` elisions by dropping elided records rather
   than emptying them — a substitution that emptied them would grade a record the
   doc never made a claim about. Proven against the defect it missed: restoring the
   v2-shaped relation fails it with `missing-required-ref`.

### Still open, and what each needs

1. **Four rubric rows cannot see the class they report on** (D5, B5, C6, E4) — see
   P6.3.
2. **Two residual false FAILs in the text layer**, both loud and never false
   passes: `markdown_to_text('*X****Y***')` leaves stray asterisks where both
   reference parsers emphasise (108 of 6075 fuzz paragraphs), and `_BOLD`'s
   non-greedy pairing mis-splits a four-asterisk delimiter run. A real fix needs
   delimiter-run pairing — a fourth CommonMark emphasis implementation in this
   repo — not a regex tweak.
3. **`_check_refs` and rubric D5 resolve only that a cited anchor exists**, so a
   record filed under the wrong section lints clean. The remedy is the same for
   both: resolve each `ref` back to the outline node whose `line_span` contains the
   record's `line`. It needs `scripts/kb_lint.py` to pass `document_outline(body)`
   instead of a flat anchor set.
4. **The graded corpus cannot exercise most of this.** It contains no enriched
    bundle with real records, no legacy document with an embedded OLE object, no
    numbered or keyword body sentence, no over-long ATX heading and no ascending
    measurement series. Every fix above is therefore pinned by unit and integration
    tests, and the eval and the rubric grade almost none of it over a real document.
    Adding those fixtures is purely additive and is the cheapest single way to make
    the rubric mean more.

### Two claims withdrawn

Recorded because a refuted claim that quietly disappears is how a plan drifts.

- **`idx 54`'s proposed fix was wrong in both halves** (re-export from the package,
  flip `public_api`), and applying it would have made the tree worse. Only the
  bare `__all__` is correct.
- **`idx 58`'s stated harm was wrong** — no safety boundary was ungoverned. The
  defect was real; the reason given for it was not.

---

## Recorded deviations

Kept here so they are decisions, not drift. The list grew a great deal in P8;
it is grouped by area below, in the same voice throughout.

### Cross-cutting

| Decision | Alternative | Why this way |
|---|---|---|
| Widen the gate rather than relax the office "closed" claim | Accept token recall as the definition of lossless | `end-goal.md` §1 already says structure is content; the gate under-implemented the charter |
| `decisions[]` separate from `warnings[]` | One list | A choice is not a problem; mixing them means neither can be aggregated |
| argv recorded with `<src>`/`<out>` placeholders + a source-root hash | Record real paths | `CLAUDE.md` forbids absolute host paths; the hash still proves tree identity |
| Docs land in P1, before the features that will change them | Document last | The drift test makes every later phase update its own reference for free |
| `structure_fidelity` hard-fails office, `best-effort` on PDF | Uniform gate | Same asymmetry, same reason, as losslessness: PDF has no ground-truth semantic tree |
| Table spans **flatten into real GFM rows**, and every flattened span is counted in a `flattened_table_spans` warning | Emit a raw-HTML `<table>` island per spanned table | Four table detectors in this repo are pipe-shaped — `content.tables`, `structure.json`'s node tables, `_chunk._table_headers`, and P3.1's `md_structure` — so an island is invisible to all of them and reads as a *missing* table to the fidelity gate landing in the same phase. Worse, it would **lie** to that gate: `md_structure` counts `**bold**` inside an island that CommonMark renders as literal asterisks, which is exactly the "trust the emitter's intent" failure P3 exists to stop. Flattening keeps rows × cols equal to the OOXML grid and keeps each row self-contained for row-wise chunking — the reason `vMerge` is already forward-filled — and costs only the visual span, which the warning makes measurable instead of silent |
| `<stderr>` is escaped (`\<stderr>`) while `[payments]` is not | Store every identifier verbatim | A bracket alone can never be a link — this converter emits no link reference definition for a shortcut reference to resolve against — so escaping it bought nothing and cost grep-ability. `<stderr>` matches CommonMark's raw-HTML tag-name production, so unescaped a renderer **swallows it**: the choice is a visible backslash or an invisible identifier, and a rubric row that claimed otherwise was corrected rather than the probe list quietly trimmed to fit |
| Emphasis is emitted faithfully even when a run boundary falls **mid-word** (`Dma**ArbiterUnit**`) | Suppress markers that would sit inside a word, to keep the identifier greppable | The `\_` decision (P0.3) removed pure noise: a single intraword underscore can never be emphasis, so escaping it bought nothing. `**` is not noise — it carries the fact that the document bolded that text. `markdown_to_text` strips the markers before tokenizing, so the identifier is intact in the text layer the index and the KB actually consume; what is lost is a raw grep of `document.md` for the unsplit identifier. Recorded in `evals/expectations.json` so the tradeoff is visible where it bites. **Reaffirmed in P8:** that justification was only ever true for `**`; after idx 19 it is true for `*` as well, so the entry stands as written rather than needing a caveat |

### The structural ground truth (`backend.ingest.docx_source_structure`)

| Decision | Alternative | Why this way |
|---|---|---|
| The ground truth assumes the **image-emitting** converter configuration: a paragraph holding only a picture counts as a block that ends a fenced code run | Ignore pictures, matching the no-image configuration | A picture *is* a block in the rendered markdown, so this is a fact about the document. `scripts/build_bundle.py` converts with `emit_images=True` and is the only path that runs `structure_fidelity`; the legacy `office_convert.convert_one` path emits no image block and would read source 2 / markdown 1 for a picture between two listings — that path is graded by token recall only, so it is not gated |
| Style-derived emphasis is suppressed on **heading** paragraphs and, with direct run formatting, entirely inside **code** paragraphs | Count every mark the style cascade resolves | Both are statements about markdown, not about the converter. `# Title` already renders bold, so counting a stock `Heading1`'s `<w:b/>` would demand `# **Title**` of every heading in every document; inside a fence `**` is two asterisks, so there is no span there to lose. A run's own `w:rStyle` marks inside a heading still count |
| `w:docDefaults/w:rPrDefault` and the `w:default="1"` paragraph style are **not** resolved — only an explicit `w:pStyle`/`w:rStyle` | Resolve the full cascade | A document declaring bold document-wide is not emphasising anything; resolving it would mark every run in the file. Both sides read zero, symmetrically |
| `_own_text` (no separators) is kept for run-level reads; the block-aware `_block_text` is used only where **content is compared across sibling blocks** — cell text, note text, `list_item_words`, span text | Change `_own_text` itself | Concatenating with nothing between is correct *inside* one paragraph (Word splits one word across runs at every rsid boundary) and wrong *across* blocks. Changing `_own_text` would move all eight of its call sites, including the row-width and vMerge-blank rules. The separator is a space and nothing else, because anything matching `[a-z0-9]+` would be a fabricated token in its own right — the exact bug being fixed |
| `tables[].cells` compares token **tuples**, so an intra-cell paragraph boundary the converter renders as a *space* rather than `<br>` is still invisible to that fact | Compare cell text verbatim | The welded-into-one-**word** case — the reported false pass — is now caught. A boundary turned into a space keeps the same tokens on both sides, so closing it needs a different fact, not a stricter comparison |
| A table nested inside a **1×1 layout** table is *not* skipped, although a table nested in a data table is | Skip every nested table | The layout wrapper is scaffolding the converter unwraps, so the inner table really is top level. GFM has no cell that can hold a table, so a table nested in a *data* table genuinely flattens into its owning cell |

### The OOXML → markdown converter

| Decision | Alternative | Why this way |
|---|---|---|
| When a `*`/`**`/`~~` delimiter cannot flank, **one rendering unit of the span's own text moves outside the markers**, so `(2:0)` bolded in the source emits as `Field MODE(**2:0)**` | Fall back to inline `<strong>`/`<em>` HTML | CommonMark cannot express emphasis whose delimiter has a word character outside and punctuation inside — the asterisks print literally and the emphasis is gone from the render. Inline HTML collides head-on with the table-span row above (raw-HTML islands are invisible to all four pipe-shaped table detectors) and would need `_mdstructure._scan_inline` and `markdown_to_text` taught the tags. Moving the minimum — one unit, only when the delimiter is actually blocked — keeps the emphasis in the render and the character in the text; marko and markdown-it both confirm the result |
| The shift is **not** applied when a span carries two delimiter kinds (`**~~x~~**`) or is a single punctuation character; those keep markers a renderer prints literally | Drop the emphasis markers instead | The delimiter's inner neighbour is another marker and cannot move; dropping the markers would delete a fact the document carries. Either way the fidelity gate reports `strong 1 vs 0` and the document does not publish, so the loss stays **measured** rather than hidden |
| `_esc`'s `](`/`][` bracket test is made against the assembled **paragraph** text (`_p_literal`), which holds a hyperlink's display text but not the `](url)` the converter synthesises around it | A post-pass over the finished markdown line | The finished line contains the converter's *own* link syntax, so a paragraph holding one genuine link would freeze every unrelated `[31:0]` back into `\[31:0\]` — a silent regression against the recorded `[payments]` decision. Reading only what the document wrote keeps the exemption where it was earned and still sees the danger a run boundary creates |
| `_esc_lead` escapes 1–6 leading hashes (with or without a following space), a line of only `-` or only `=`, and `[label]:`; it does **not** escape 7+ hashes or `#1 priority` | Escape any leading marker character | None of the unescaped shapes is a block construct in CommonMark, so escaping them would put a visible backslash into text that was never at risk — the same argument the `[payments]` and `\_` rows make. The escaped shapes all render the paragraph *away*: `-----`, `===` and `[REG]: 0x04` carry no token and no heading fact, so both gates passed while the paragraph was deleted from the render |
| A code paragraph is emitted even when **empty**, and an empty one is kept when it falls between two code lines and dropped at the ends | Drop every empty code paragraph | A blank line inside a shell transcript is part of the transcript. The keep/drop rule is the exact mirror of the ground truth's, so the `code_blocks` count still agrees on both sides |
| Inside a code paragraph, `w:tab` renders as a tab and `w:br`/`w:cr` as a newline, and the text is `rstrip`ped **only** | One shared whitespace normalisation for prose and code | Whitespace *is* the semantics of code: collapsing it published `if dev.ready:` with no body and an unconditional `return dev`, at token recall 1.0 with the fence count matching, because whitespace is not a token and the gate compares fence **count**. `rstrip` only, because `markdown_to_text` rstrips fenced lines anyway |
| The fidelity gate still compares only the **number** of fenced blocks, never their content | Compare fenced content, or its line count and per-line leading-whitespace profile | Known and deliberately left: the damage idx 1 describes is fixed at the source but remains invisible to the gate, so the next regression of that class would be silent again. Worth filing as its own slice — it is named here rather than discovered again |

### The markdown reader and the comparison

| Decision | Alternative | Why this way |
|---|---|---|
| `structure_fidelity.compared` counts facts that **observed something** on the document (evidence), not facts the ground truth supplied a key for (schema). A real corpus bundle reads 9, not 13 | Keep counting supplied keys | Counting names told the reader "thirteen things were checked" about a memo where four of them were `0 == 0` and could not have failed. The number is a coverage claim, and a coverage claim that cannot fall is not a measurement |
| `structure_fidelity` gained an optional `unmeasured` list naming facts a **present** ground truth did not supply; `gate` still answers only for what was measured | Fail the gate whenever any fact is unmeasured, or say nothing | Failing would reject the entire corpus for a fact nobody produces yet — a false FAIL worse than the bug. Saying nothing is exactly how `heading_path` could be added, look done, and grade nothing. Naming the gap keeps `pass` meaning "everything measured agreed" while making "everything measured" auditable. The key is omitted when the ground truth is absent **entirely**, because `method` and `gate` already read `unmeasured` there |
| `heading_path` landed in two halves — the emitted side and the comparison first, the ground-truth producer after — and reported `unmeasured` on a real docx in between | Wait and land both halves together | File ownership: the two sides live in different modules, and the point of the second implementation is that it is written *independently*. Landing the emitted side first meant the damage tests could prove the comparison works before anything depended on it. The interim was `unmeasured`, never `pass`, and a test pinned that state so it could not be mistaken for done — which is the only reason the gap was visible enough to close. Both halves are in now, and the fact is graded |
| `markdown_to_text`'s italic rule is now **two** patterns: `*` has no intraword guard, `_` keeps the old one verbatim | One shared pattern for both delimiters | CommonMark's intraword ban applies to `_` only. Sharing it made `*n*th`, `two *Foo*s` and `re*start*` — all `<em>` in marko and markdown-it — fail the recall gate at 0.667 on a **correct** conversion, 1512 of 6075 fuzz paragraphs. Dropping the guard for both would fuse `DB_MAX_CONN_LIMIT` into `DBMAXCONNLIMIT` in the text layer the KB and the BM25 index read, which is P0.3's contract |
| `markdown_to_text` deletes a `=`/`-` marker run only when a paragraph is open above it (a lone `-` excepted), and a table delimiter row only when the line contains a pipe | Delete any marker-run line, as before | `===` standing alone renders as `<p>===</p>`; deleting it removed real characters from the text layer at token recall 1.0, because a marker run carries no ASCII token to go missing. A lone `-` stays deleted: it is an empty bullet and renders blank either way |
| `md_structure` closes a fence only when the closing run sits within three columns of the **container's** content column; a non-blank line that dedents below it ends the block **without** closing it, and is re-read as a block | Judge the close against the opening fence's own column | `fence_col <= indent` breaks `"   ```\ncode\n```"`, which both reference parsers close. Measuring from the container is what makes a mis-indented col-0 close read the way marko and markdown-it read it — 1 item / 2 fences / 0 headings, not the 3 / 1 / 1 that made a damaged document's facts byte-identical to a correct one |
| The GFM table candidate is resolved **before** the setext underline rule, and a table now requires header and delimiter rows to have the same number of cells | Leave the setext rule first | `---` on the line after a table's last row deleted the entire table from the fact vector and invented an h2 no renderer shows. The parity check is not optional cosmetics: without it, resolving earlier would flush `a \| b \| c` over `---\|---` as a bogus 2-column table where GFM sees a paragraph and a setext h2 |
| With a `===` trailer the table is flushed at its real rows and the `===` becomes a following paragraph; GFM appends it as one more table **row** | Re-open the table to absorb the line | Off by one row, versus losing the whole table. Pinned by a test rather than left undiscovered |
| `_mdstructure._words` strips an **unescaped** `<br>` — the one project-specific rule in an otherwise converter-blind reader | Teach it nothing, and stop emitting `<br>` | A GFM cell cannot hold a newline, so `<br>` is how a multi-paragraph cell is written and what a renderer draws a line break for; the letters "br" are never rendered and no source document can produce that token. Removing the emission instead would destroy the paragraph boundary the ground truth now reads correctly. `(?<!\\)` keeps the escaped `\<br>` — the document talking *about* the tag — as content |
| `markdown_to_text('*X****Y***')` still leaves stray asterisks where a renderer emphasises (108 of 6075 fuzz paragraphs) | Rewrite the emphasis rules as a delimiter-run pairer | Out of scope for the finding that surfaced it, and it needs a real CommonMark emphasis implementation rather than a regex. It is a **false FAIL** — the gate reports it and the document does not publish — so the loss stays measured |

### Outline detection and chunking

| Decision | Alternative | Why this way |
|---|---|---|
| A short, capitalised measurement with no verb and no terminal stop (`100 MHz`, `8 GB DDR4`) is still promoted to a heading in un-marked-up text | Reject measurements outright | Nothing in its *shape* separates it from the real heading `5 V rail`. Deciding that needs a part-of-speech model; this layer is deliberately model-free. Pinned by a test rather than left implicit |
| A genuine numbered heading written in lower case after its number (`4.2 reset sequence`), or whose title runs past eight words, is now **missed** in un-marked-up text | Keep the branch unbounded | The same direction of trade P4.7 recorded for the keyword branch: the second error costs one node, the first costs the whole tree below it. Both are confined to un-marked-up text — once the converter emits `##` the ATX branch answers first and carries no bound at all |
| The `len(s) > 120` cap no longer applies to the ATX form, so `structure.json` can publish a heading title of any length | Keep one cap for every form | An explicit `##` is the document *asserting* a heading; the cap exists to stop the un-marked-up heuristics swallowing a run-on paragraph. Titles are published verbatim (the `[:120]` clip was removed in the same change) because `anchor = gfm_anchor(title)` must be the fragment the rendered heading is reachable by |
| A flat-extractor document whose `1.1` the extractor lost, leaving `1.2` as `1`'s only stated child, is no longer level-inferred and stays flat | Infer anyway | The safe direction: the outline stays as flat as the extractor stated it rather than being given a hierarchy on evidence indistinguishable from a magnitude series. Gaps *within* a stated set are still believed (`2.1, 2.2, 2.4`) |
| `uncovered_lines` now counts an ATX-marked heading line no node opens on, so `covered_lines + toc_lines` no longer necessarily equals `content_lines` on a damaged document | Keep pure line coverage | Line coverage alone was 0 by construction for **every possible input**, so the gate could not fail. A dropped heading's lines are backfilled by its ancestor, which is exactly the loss the check exists to find. `report.json`'s schema and the `outline_report` gate rule are unchanged |
| Nothing measures the **converse** — a heading the heuristics *invented* | Add a symmetric check | It is not measurable from ATX evidence, because the native-text lanes legitimately carry headings with no `#` at all. That direction is bounded at the source in `is_heading` instead, and the docstring says so |

### Metadata enrichment and the bundle lifecycle

| Decision | Alternative | Why this way |
|---|---|---|
| A withdrawn bundle leaves `document.md.stale` / `structure.json.stale` / `knowledge.json.stale` / `images.stale/` in the directory | Delete the previous artifacts outright | A failed conversion must stop publishing the previous run's artifacts, but deleting them would cost the only good copy when the failure is environmental (soffice absent, a truncated copy). `.stale` names are matched by no selector, so they are unpublished rather than published under another name, and a later successful build removes them |
| A refused bundle (conversion `failed`) gets **no** `manifest.jsonl` row | Add a `refused` action | `_manifest_row` raises on an unknown action *by design*, and the action vocabulary is a documented closed list — so adding one is a two-file change. A refused bundle is simply not selected, exactly like a directory with no `document.md`; the selector's question is "which bundles are published", and this one is not |
| A hand-corrected deterministic field's `_provenance` now reads `source: authored` where it used to keep saying `derived` with a fingerprint that no longer matched | Keep the original source label | The mismatch was how the protection was *detected*, and it still is on the run that first sees the edit; from then on the block simply says what the value is. `is_authored` returns True for `authored` regardless of fingerprint, so the value stays protected forever |
| The first enrichment run after the P8 fix can rewrite `document.md` **once** on a bundle whose stamps had already drifted, before converging again | Migrate silently, or never re-stamp | Only for a bundle where a previous run legitimately changed a deterministic value: its fingerprints were describing older values, and the first fixed run repairs them. A bundle whose stamps were never stale is byte-identical on the very first run |
| A `links`/`entities` block's group **order** can change once, on the first run after the revalidation split | Preserve the incoming order | Evidence groups are emitted first, which is the order every subsequent run already produces; the value is re-fingerprinted with it and the run converges immediately |
| `meta_coverage` may now report `invalid > 0` (and `doc_meta.gate: incomplete`) for a document that previously reported `complete` | Treat the change as a regression | That *is* the fix: for `entities`/`links`/`relations`/`decisions`/`risks` the membership test was structurally incapable of failing, so the gate reported `complete` over payloads `kb_lint` was reporting as six ERRORs. Reachable only through a payload the pipeline itself cannot write. Note it can change a `--fail-on-pending` chain's exit code |

### KB linting and corpus gates

| Decision | Alternative | Why this way |
|---|---|---|
| Per-document cardinality can no longer condemn a **closed-vocabulary** facet, so on today's schema `facet-not-a-facet` and `facet-thin` cannot fire through `lint_document` at all | Keep grading the ratio, or delete the check | Arithmetic, not a relaxation: `distinct` is capped by the term count, so an adverse verdict is only reachable while `used < terms/warn`, and in that range it is *forced by the size of the term list* rather than evidenced by the document — 9 relations over 9 of 17 governed predicates was an ERROR whose only passing edit was deleting relations. Those rows now report `sparse`: reported and aggregatable, neither pass nor fail. The rule stays alive and pinned for ungoverned/registry facets, which is the regime the ratio is a discovery heuristic for; for a closed field, membership answers the stricter question and `vocab-unused`/`vocab-dead` answer the mirror one corpus-wide. **Stated out loud rather than discovered later**, and repeated in `kb_lint --help` |
| `record-uncited` is a **WARN**, not an ERROR, on a block declaring `schema_version < 3` | ERROR at every version | `ref` became required at v3, so every knowledge.json written under v2 legitimately lacks it; grading those as ERRORs would turn every legacy bundle red on the first run after the bump — the same run the (now working) skew gate hands the operator as a backfill work list. The finding is still made, and it is an ERROR for an **unstamped** block, which is treated as current because it is hand-authored and being written now |
| A record cites its source with a `ref` **or** with `source: extracted` + an integer `line` | Demand a `ref` from everything | The linter deliberately mirrors rubric row D5 word for word. A URL in the lede sits above the first heading, where a renderer emits no fragment, so a `ref` there would be a dead link and the line is the better answer (P7.10). Two gates disagreeing about what counts as a citation would make one of them wrong about every harvested link in the corpus. A model-proposed record gets no such latitude |
| `lint.similar_ok` now also suppresses a case-only `synonym-collision` on a registry field | Leave registry collisions unsuppressable | The same acceptance path serves both reports, so `tags:RHEL8\|rhel8` now binds where before it silently did nothing. The tool still *prints* the better remedy (add an alias, so normalisation happens before grouping), but an operator who deliberately records the pair as two distinct terms is now obeyed — which is the documented purpose of the hatch |
| `skew_report(docs)` with no arguments keeps the old corpus-relative reading; only the caller that knows the code's version passes it | Change the default | Keyword-with-default preserves API compatibility, and the corpus-relative reading is honest exactly when the code's own version is genuinely unknown. `corpus_findings` always passes them. Note `partial=True` still skips skew entirely, inherited from the whole-corpus set — the authoritative comparison *is* valid on a subset, so running it under `partial` is a defensible future change |
| `vocab-dead` stays silent for a vocabulary bound only to `authored_only` fields | Report every unused vocabulary | `document_status`, `confidentiality` and `review_cadence` are legitimately empty until a person signs off, and `coverage_report` declines to warn about authored-only fields by documented policy ("a fact about the organisation rather than about the pipeline"). Firing here would contradict that on every fresh corpus. INFO throughout, so no exit code moves |
| `VERDICT_SPARSE` now means "the sample cannot answer the question", not only "too few values" — a 20-relation document can be `sparse` | Add a `VERDICT_UNMEASURED` constant | The new constant would need a re-export from the package `__init__`, which was outside that change's file set. If the distinction should be visible in `kb_lint.json`, it is a one-line change in three files |

### The restricted YAML reader

| Decision | Alternative | Why this way |
|---|---|---|
| A non-blank line inside a `\|`/`>` block that is **less indented** than the block's own first line now raises, naming the line | Keep slicing blind | Blind slicing shaved leading characters, deleted a short line outright, and inside a folded block manufactured a paragraph break that split the author's sentence. PyYAML raises on the same input. Behaviour change worth knowing: a hand-maintained vocabulary already committed with such a slip was previously loading with corrupted prose and now fails to load at all, surfaced as `VocabularyError`. That is the intended loud failure; the shipped `config/vocab.yaml` is clean |
| A quote opens a quoted scalar **only at the start of a token** — start of line, or after `[`, `,`, a `- `, or a `key: ` | Open on a quote at any offset (as three scanners did), or only in first position (as the value parser did) | The module disagreed with itself about the same bytes, so `title: The Owner's Manual  # from OCR` absorbed the comment into the title permanently and `[don't, isn't]` merged into one item. A naive "column 0 only" rule would have truncated every machine-rendered `title: "a # b"` at its interior `#` — silent data loss on the one path that already worked. Inside a single-quoted scalar `''` is an escaped apostrophe, implemented rather than relied on as an accident |
| `[a: b]` is refused as a flow mapping | Keep guessing a plain string | The brace-less spelling of `{a: b}`, which the subset already rejects; it used to return `"a: b"` where PyYAML returns `{'a': 'b'}`. Only an unquoted `:` at the end of an item or followed by space/tab/comma triggers it, so a bare URL (`[http://x/ns#, a]`, the form `config/vocab.yaml` uses) and a quoted `["a: b", c]` still parse. Found by fuzzing, not filed as a finding, and closed in the same pass because it is the same silent-misparse class in the same scanner |

### The rubric and the grader

| Decision | Alternative | Why this way |
|---|---|---|
| `_rubric` **re-implements** the fenced-code mask and the GFM table-separator test instead of importing them from `backend.sections` / `_mdcheck` | Import the producer's helpers | The rubric grades what those modules produced. A check that asked the producer where the code is, or how many tables it found, would only be asking a module whether it agrees with itself — the same reason the fallback anchor and `gfm_anchor` are spelled out there rather than imported. Independent restatement **is** the measurement |
| A suite row is answered **only** by the tests it names, so an unrelated *failing* test in the same file does not fail the row | Fail a row when its file is red | The row asserts one condition; a green file cannot demonstrate it and a red file cannot refute it. That unrelated test belongs to some other row, or to none — and if to none, the fix is a row, not a false failure here. The complementary half (an unrelated *green* test cannot earn a row) is the defect this change exists to close |
| Row C5 hard-fails the office lane for an unaddressed table but reports non-office lanes `unmeasured` | One uniform rule | The same asymmetry `structure_fidelity` already records: the office lane has a converter-blind ground truth, docling's flat outline may legitimately be unable to address a table. `unmeasured` is printed in the evidence and is never counted as a pass |
| Row A5 is graded on **one** eval fixture, not on the eval exiting 0, and the eval's other skips are printed rather than graded | Grade the eval's exit code | "Adversarial fixtures are pinned" is a claim about one expectation; eighteen green siblings do not make it true, and the row must fail if that expectation is dropped. The remaining 3 PDF skips are a **declared** exclusion (the grader hardcodes `--skip-pdf`) printed in the row's evidence — no row claims them, which is why the census is there |
| Row C6 asks only about one ALL-CAPS literal in one fixture; the general claim is row C6s over the unit suite | Extend C6's probe list | The graded corpus contains no numbered or keyword body sentence to ask C6 of, and the rubric cannot re-derive `is_heading`'s bound without reimplementing the code it grades. C6 is now named for exactly what it measures instead of overclaiming |
| `grade_output.py --json` emits `{"error": …}` — with no `summary` and no `rows` — on exit 2 | Emit `{summary, rows}` with an error field | The error document must not be readable as a grade with zero failing rows. A consumer keyed on `summary` raises, which is the correct outcome for "the run broke" |

### Provenance and replay

| Decision | Alternative | Why this way |
|---|---|---|
| `replay_run.py` has a **fourth** exit code, `4`, that no other tool in the tree uses: "nothing diverged, but at least one class was never compared" | Fold it into `0` or `3` | A verdict may only report what was computed. Folding "could not compare" into `3` would call an unchecked class a divergence; folding it into `0` is the defect this pass fixed. A third state needs a third code |
| `replay_run.py --report R` with no `--src` can now **never** return 0 | Keep the inspect-only all-clear | The source and corpus byte checks are the two that need a tree, so the inspect-only mode always has two unverified classes. That is the honest answer: the mode was returning "all clear" over a corpus it had not looked at. Pass `--src` to get a 0 |
| An unprobeable external tool and a recorded `corpus_sha256` with no manifest rows moved from exit `3` to exit `4` | Leave them at 3 | They were already worded UNVERIFIED and were always the same kind of answer as the new cases; leaving them at `3` would mean two spellings of one concept. Neither is a demonstrated difference, so neither may claim to be one |
| `divergences()` returns three-field `Note(kind, text, compared)` records, not `(kind, text)` pairs | Keep the pair and add a parallel list | A caller that ignores `compared` would read "I could not check" as "this machine differs", or the reverse. The three-field record makes the old two-value unpack **raise** instead of silently mis-reading |
| `provenance` no longer keeps a list of "path-ish" flag names; values that are not absolute paths are published verbatim on those flags | Keep the list as defence in depth | The list bought no safety (every absolute path is redacted whatever switch carried it) and actively corrupted the record: prefix-expanded against argparse's abbreviations, `--ex 4000` was recorded as `--ex <path>`. A relative path discloses nothing. The leak invariant is **narrower** on balance, because the same change closed `file://`, `//abs`, `~user/` and buried `k=/abs` |
| `file:///abs/…` is redacted to `file://<path:sha16>`, which makes a run that used it unreplayable by `command_for` | Treat every URL as safe | A `file://` base is an absolute host path wearing a scheme, and `provenance/CLAUDE.md` forbids recording one. The `<path:id>` form keeps "was this the same share?" answerable, and refusing to reconstruct is this module's existing, deliberate behaviour for any path it cannot know. `http(s)://` bases are unaffected and still replay byte-identically |
| `package_version` returns `""` for a `[project]` table declaring `dynamic = ["version"]`, even when a `[tool.*]` table nearby states one | Fall back to any version found | An absent version degrades the converter stamp to `doc2md-ooxml/0+<sha7>`, which is honest. Reporting a build tool's version would stamp every bundle — and the graded `converter` string — with a converter version that does not exist |

### Scripts, tests and CI

| Decision | Alternative | Why this way |
|---|---|---|
| `office_convert.zip_members` is kept although it now has no production caller | Delete it | It is the honest reader of "every member of *this* file", and its docstring now carries the trap that caused idx 37 (on the LibreOffice lane the file that matters is the soffice-**produced** package). The test suite uses it to assert the negative half of the defect. Deleting it would delete the record of why the bug happened |
| The zip readers catch an explicit `_ZIP_READ_ERRORS` tuple (including `RuntimeError`, `NotImplementedError`, `EOFError`, `zlib.error`) rather than `except Exception` | Catch everything | An unreadable package must become the **named** `unreadable-zip` failure; a genuine bug in that module must still crash loudly rather than be laundered into one. `BaseException` is never caught, so Ctrl-C still interrupts |
| A damaged package still reports the single error string `unreadable-zip`, not the decompressor's message | Add a new error vocabulary | Distinguishing "corrupt deflate" from "a valid zip with no main parts" needs a new out-parameter and a new vocabulary, and `unreadable-zip` is asserted downstream. The conflation is **pre-existing**; the failure is still loud, named, and per-document |
| `read_media` returns `{}` when only a media member is undecompressable, even though the XML parts read fine | Fail the document | Not a silent loss: every sentinel with no bytes is counted and raises `image_bytes_missing` ("never a silent drop"). Degrading to a **measured** figure loss is strictly better than the previous crash |
| `worst_documents` / `figure_losses` are in their module's `__all__` but **not** in `backend.validate.__all__` | Re-export them from the package | `__all__` on a source file is that *file's* public API; the package surface is a separate, deliberately tighter thing. These two are internal halves of the coverage summary that no runner calls; promoting them would freeze an internal shape as a contract |
| The four modules named in idx 54 keep `public_api: no` in their docstrings | Flip the label | All 25 private modules in the repo say `no`, the enforcer CONVENTIONS names does not exist, and nothing in CI reads the label. Flipping four would break the only consistent signal the tree has. If the label is to change it must change repo-wide |
| The locale-independence suite forces the C locale in a **subprocess** and skips if that fails to produce an ASCII preferred encoding | Monkeypatch the encoding in-process | `open()`'s default is resolved by a version-dependent bootstrap at call time, so monkeypatching is interpreter-specific and would silently stop testing anything. The skip is honest-unmeasured, not a pass |
| The CommonMark differential is made mandatory from `ci.yml` (a step that fails when the file reports any skip) rather than from a `DOC2MD_REQUIRE_MD_ORACLE` env var | Read an env var inside the test | Any literal `DOC2MD_*` under `tests/` is enforced as documented configuration by the parity test, and this **is not configuration** — it is one ring's obligation, so it belongs in that ring's workflow. Spelling the name to dodge that regex would be exactly the dishonesty the gate exists to prevent |
| `marko` is pinned to an exact version in `tests-modern` and is **not** added to `pyproject.toml`'s `dev` extra | Pin loosely, or add it to `dev` | The 86 differential cases are graded against whatever the reference says, so an unpinned bump reddens CI for reasons unrelated to doc2md. It is out of `dev` because installing `dev` on the 3.6 ring would break that job outright (marko needs 3.8+) and because the parser must never become importable from `src/backend/**` |
| `test_docs_parity.py` carries a `_UNDOCUMENTED_TODAY` ledger with exactly one entry, asserted as an **equality** | Suppress the key, or leave the check loose | Block-scoping surfaced one genuinely undocumented published key whose doc file was outside that change's ownership, so the gap is named and counted rather than hidden. The equality makes the ledger self-expiring: a new gap fails, and *closing* this one fails too until the entry is deleted, so it cannot quietly become the norm |
| The block→section mapping in the parity check is an explicit table, not a regex over heading text | Derive it from the headings | The reference's headings do not name JSON blocks one-to-one — `report.json` identity keys live under "Identity and provenance", node shapes under "Outline node" / "Image node" / "Table node". Guessing the mapping reddens keys that are correctly documented today |
| The CI `kb_lint` step runs an offline `enrich_metadata` pass over the eval bundles first | Lint the bundles as they are | Without it the linter reports `graded=0 no-metadata=14` and exits 0 — a gate over nothing, which is the shape of check this project exists to refuse. The no-model pass fills the deterministic floor and exits 0 by design, giving the linter 14 real documents. The remaining weakness — no record-level content to grade — is named in P8 rather than passed over |
| `grade_output.py` is wired into CI with no threshold flag; the floor is "exit 0", which the script defines as `overall == "A"` | Invent a separate threshold | Asserting the script's own exit code keeps the floor and the rubric in one place; a separate threshold would let the two drift. Note this makes CI enforce the **grader's** A, which the top of this document explains is narrower than the honest grade |
| Two **new** integration test files rather than additions to existing ones | Extend the nearest existing file | `coverage_report.py` and `render_vocab_doc.py` had no test home at all, the existing files belonged to other changes in the same pass, and integration tests here are named by **scenario** rather than mirrored to a source file |
