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
| A1 | A second hard gate, `structure_fidelity`, grades twelve facts against a converter-blind OOXML ground truth — headings by level, list items by nesting depth, ordered/bullet totals, emphasis and strike runs, code spans and fenced blocks, links, table geometry **and every cell's content**, and every list item's text **in order**. The office lane fails on any mismatch; a format with no second implementation reports `unmeasured`, never `pass` | `report.json.structure_fidelity.gate == "pass"`, corpus-wide |
| A2 | Body text round-trips: markdown → text equals source text **as a sequence**, not only as a multiset | new `tests/unit/backend/test_validate_roundtrip.py` |
| A3 | No unconditional escaping: a character is escaped only when it could actually be syntax. `DB_MAX_CONN_LIMIT`, `--dry_run=true`, `[payments]`, `snake_case_helper` survive verbatim in the stored bytes | `backend.validate._rubric.ADVERSARIAL_PROBES`, asserted against the adversarial fixture |
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
      70-sample corpus (`tests/unit/backend/test_validate_mdstructure.py`, 111 tests on the
      modern ring, 40 on the bare 3.6 ring). That pass found and fixed eight real
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

The compared fact vector is now **thirteen** facts: `ordered_numbers` joined it,
because a per-depth *count* and a *total* both stay constant when a list splits or
restarts — which is precisely why five separate constructs could renumber a
procedure with every gate green.

- [ ] **P7.12 — The regrade's remaining findings, not yet triaged.** The six
      lenses produced far more than the blockers above; the full report is in the
      session transcript. Carried forward rather than dropped, roughly in order of
      harm: a `--force` rebuild can publish a `knowledge.json` describing the
      previous document; `replay --compare` can print `REPRODUCED` for a bundle it
      did not reproduce, and its exit codes cannot distinguish the two; a second
      run over a changed corpus keeps the stale bundle and logs a clean skip;
      `section_id` can collide and produce a self-parent cycle; `fingerprint` is
      byte-identical across the exact P0.1 renumbering bug; nothing verifies
      `markdown_sha256` in the metadata lane, so a stale one propagates into
      `knowledge.json`; the `ref` rule does not bind for a mapping-shaped
      `entities` group; every table's first row is forced into the header, so a
      headerless table silently relabels a data row; foot/endnote and comment
      ANCHORS are still deleted from the body while the notes survive as
      unattributable bullets. **None of these is speculative** — each was
      reproduced end to end.

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
| Table spans **flatten into real GFM rows**, and every flattened span is counted in a `flattened_table_spans` warning | Emit a raw-HTML `<table>` island per spanned table | Four table detectors in this repo are pipe-shaped — `content.tables`, `structure.json`'s node tables, `_chunk._table_headers`, and P3.1's `md_structure` — so an island is invisible to all of them and reads as a *missing* table to the fidelity gate landing in the same phase. Worse, it would **lie** to that gate: `md_structure` counts `**bold**` inside an island that CommonMark renders as literal asterisks, which is exactly the "trust the emitter's intent" failure P3 exists to stop. Flattening keeps rows × cols equal to the OOXML grid and keeps each row self-contained for row-wise chunking — the reason `vMerge` is already forward-filled — and costs only the visual span, which the warning makes measurable instead of silent |
| `<stderr>` is escaped (`\<stderr>`) while `[payments]` is not | Store every identifier verbatim | A bracket alone can never be a link — this converter emits no link reference definition for a shortcut reference to resolve against — so escaping it bought nothing and cost grep-ability. `<stderr>` matches CommonMark's raw-HTML tag-name production, so unescaped a renderer **swallows it**: the choice is a visible backslash or an invisible identifier, and a rubric row that claimed otherwise was corrected rather than the probe list quietly trimmed to fit |
| Emphasis is emitted faithfully even when a run boundary falls **mid-word** (`Dma**ArbiterUnit**`) | Suppress markers that would sit inside a word, to keep the identifier greppable | The `\_` decision (P0.3) removed pure noise: a single intraword underscore can never be emphasis, so escaping it bought nothing. `**` is not noise — it carries the fact that the document bolded that text. `markdown_to_text` strips the markers before tokenizing, so the identifier is intact in the text layer the index and the KB actually consume; what is lost is a raw grep of `document.md` for the unsplit identifier. Recorded in `evals/expectations.json` so the tradeoff is visible where it bites |
