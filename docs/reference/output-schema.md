---
title: Output schema reference
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [reference, schema, bundle, report, structure, knowledge, manifest]
summary: Every key of every artifact doc2md writes — type, when it is present, who writes it, and which gate reads it.
---

# Output schema reference

Key-by-key. For *why* the bundle has this shape, read the binding contract,
[`../design/output-contract.md`](../design/output-contract.md); for how the whole
pipeline fits together, [`../guide.md`](../guide.md).

Bound to the code by `tests/integration/test_docs_parity.py`: a key a real build
writes that is missing from this file fails CI.

## The bundle

```
<out>/<doc_id>/
  document.md      # markdown body + front matter (pipeline keys + the meta: block)
  structure.json   # the heading tree, with token counts, images and links
  report.json      # what was measured — validator only, no model output
  knowledge.json   # the extracted graph payload — only once enrichment has run
  images/          # <sha16>.<ext>, byte-verified
  *.stale          # withdrawn by a FAILED rebuild — see below; not published
<out>/manifest.jsonl
```

`doc_id` is `sha1(source_relpath)[:16]`. `image_id` is `sha256(bytes)[:16]`.
Those two are the only join keys.

**A failed document publishes `report.json` only** — no `document.md`. That is why
run provenance has to live in the report as well as the front matter.

**On a rebuild, that invariant is enforced by withdrawal, not by hope.** A failing
conversion over a directory that already holds a good bundle **renames** the
previous run's `document.md`, `structure.json`, `knowledge.json` and `images/` to
`document.md.stale`, `structure.json.stale`, `knowledge.json.stale` and
`images.stale/`, and prints a `WITHDRAWN` line. A `.stale` name is matched by no
selector — `enrich_metadata.py` and `kb_lint.py` walk `document.md`,
`caption_bundles` walks `structure.json` — so it is unpublished rather than
published under another name, and a later successful build deletes it. Renamed and
not deleted on purpose: when the failure is environmental (soffice missing, a
truncated source copy) deleting would cost the only good copy of the bundle. So you
will meet `document.md.stale` in a bundle directory, and it means "this bundle's
last successful body, kept for recovery, not for reading".

**Enrichment refuses a failed bundle.** `scripts/enrich_metadata.py` does not select
a directory whose own `report.json` says `status: "failed"`; it prints a `REFUSED`
line to stderr and — exactly like a directory with no `document.md` — gets **no
`manifest.jsonl` row**. Only an explicit `failed` counts: an absent or unreadable
report is not a verdict, and a `degraded` bundle is published and still enriched.

---

## `document.md` — front matter

Written by the bundle writer. Every value is emitted double-quoted, so a YAML 1.1
reader cannot retype `no`, `on` or `0644`.

| Key | Type | Present | Meaning |
|---|---|---|---|
| `doc_id` | str | always | Bundle key; `sha1(source_relpath)[:16]`. |
| `source_format` | str | always | `docx`, `pdf`, `md`, … — the **effective** format after any soffice pre-convert. |
| `lane` | str | always | `office` or `pdf`. |
| `source_relpath` | str | always | De-identified path relative to `--src`. Never an absolute host path. |
| `source_sha256` | str | always | Hash of the source bytes. |
| `markdown_sha256` | str | always | Hash of the **body only** — not the whole file. This is what makes the front matter freely rewritable and every line span stable. |
| `converter` | str | always | Converter identity, **derived**: `doc2md-<lane>/<version>+<commit7>`, with `.dirty` appended when the checkout had uncommitted changes. Two builds from different code can never claim the same stamp. |
| `lossless` | str | always | `"true"` on the office lane, `"false"` on PDF. Scope is **token** losslessness. |
| `structure` / `report` / `images` | str | always | Sibling artifact names, so the file is self-describing. |
| `generated_run` | str | always | The run id (`--run-id`, else a UTC timestamp). |
| `source_*` | str | when declared | Source core properties, flattened under a `source_` prefix so they can never collide with a pipeline key: `source_title`, `source_author`, `source_version`, `source_created`, `source_modified`, `source_last_modified_by`, `source_company`, `source_app_version`. An absent property is **omitted, never written empty**. |
| `meta` | map | after enrichment | The tiered descriptor block — see [`vocabulary.md`](vocabulary.md) for every field, and `knowledge.json` below for the half that lives elsewhere. |

#### `meta` — identity, the floor, and the permalink

**One identity, and it is `id`.** v2 shipped two — `id` from the title and `uid`
from the path — and `kb_lint` resolved references against **both**, so a corpus
could grow two disjoint link graphs that each linted clean. From schema v3:

| Key | Written from | Notes |
|---|---|---|
| `id` | the source path, slugified per segment, **extension included** (`specs/Kestrel Clock Spec.docx` → `specs/kestrel-clock-spec.docx-ed1c4542`) | **THE identity.** Unique by construction: a path is unique within a corpus, so two documents cannot collide, and the value depends on nothing but that path — not on the corpus, the iteration order, or which neighbours were present. **The extension is part of the identity** and keeps its dot: stripping it made `spec.docx`, `spec.pptx` and `spec.xlsx` — an ordinary trio in a real corpus — one id (P7.9). Slugification is lossy, so a path whose slug is not a faithful **character-for-character** rendering of itself carries an 8-hex fingerprint — `sha1` of the **exact** path, extension and all, first 8 hex — while slug-clean paths keep a readable id with no suffix (`specs/kestrel-clock-spec.docx` → itself). Faithfulness is compared before lowercasing, because on a case-sensitive filesystem `spec.docx` and `Spec.docx` are two files. **Authored wins** — write one by hand and it survives a rename forever. |
| `uid` | `id` | A **deprecated alias**, recomputed every run so the two can never drift. Kept so a v2 consumer keeps resolving; it will go at the next schema bump. |
| `slug` | the title | The title's URL form. **Not an identity** — nothing resolves against it, so two documents called "Overview" sharing a slug costs nothing. |
| `source.uri` | `source_relpath` | A filesystem path. May contain spaces, `#`, `?` — **not** a URL. |
| `version` | the source's own declared revision | Extracted, present only when the source declares one. |
| `extraction` | this run | `run_at` / `schema` / `extractor` / `converter` — prov:Activity. Re-stamped only when something actually moved, which is what keeps a re-run byte-identical. |
| `word_count`, `reading_time_minutes` | the body | Derived, markdown stripped first. |
| `source.url` | `--source-base-url` + `uri` | The permalink. Always a valid URI reference: absolute when a base is configured, relative (percent-encoded) when it is not. Never invented — doc2md does not know where you serve your files. |

**MIGRATION.** A bundle enriched under v2 is migrated by the next
`enrich_metadata.py` run, with no model: `id` is rewritten from the title slug to
the path-derived value and `uid` follows it. Anything that pointed at the OLD id —
a `see_also`, a hand-written wiki link — no longer resolves, and `kb_lint` reports
it as `see-also-unresolved` rather than silently dropping it. To keep an old id,
write it into `meta.id` by hand: an authored value outranks the derivation
permanently. Bundles that were never enriched are unaffected.

**THE DETERMINISTIC FLOOR.** `title`, `abstract` and `links` are tier-2 fields that
a **no-model run still fills**, from evidence the pipeline already measured. Each
records in `_provenance` which floor it came from, and that decides what a model
may do to it: `extracted` is evidence and is protected, `derived` is a guess and is
overwritable. See [`../guide.md`](../guide.md#3-without-a-model-and-with-one) for
the table.

**`_provenance` carries no `tier`.** It was `field(name).tier` — recomputable for
free from the field name, never read back, and about 40% of the bytes of every
provenance block. Removed in v3. Every machine-written value now carries a
`value_sha` instead (it used to be `generated` only), which is what lets the next
run tell a hand correction from the value it wrote itself, whatever tier wrote it.

Two invariants hold for anything written here: **no line is ever exactly `---`**
(three separate strippers find the closing fence by scanning for that line), and
**no raw control character is emitted** (the validator raises a hard `bad-chars`
error for one, even inside front matter).

---

## `structure.json` — the outline

Deterministic, no model. Line indices are **body-relative** — they index the
bytes `markdown_sha256` covers, not `document.md` as a whole, so a consumer
indexing the file directly must strip the front matter first.

| Key | Type | Meaning |
|---|---|---|
| `doc_id` | str | Join key. |
| `source_format` | str | Mirrors the front matter. |
| `lane` | str | `office` \| `pdf`. |
| `markdown_sha256` | str | The exact body every `line_span` indexes. **Verify it before trusting a span** — the files are written sequentially, not transactionally. |
| `token_model` | str | The tokenizer behind every count, e.g. `cl100k_base` or `char-estimate/4`. |
| `total_tokens` | int | Whole-document count under that tokenizer. |
| `has_toc` | bool | A table-of-contents region was detected and skipped as furniture. |
| `levels_inferred` | bool | `true` when `level` was read from the titles' section numbering because the extractor emitted **one** level for the whole document (docling always emits `##`). Fires only on ≥ 3 headings, all the same level, ≥ 60% of them numbered, at least one number actually nested, **and the numbers reading as a nested outline**. That last guard has two halves, and it needs both: (i) every number deeper than the document's shallowest one has to extend a number the document already stated, **and** (ii) every stated parent's children have to start at `1` (`0` is allowed, for `1.0 Introduction`); gaps are fine, so a spec whose `2.3` was deleted still reads `2.1, 2.2, 2.4` as an outline. Half (i) alone was an overclaim: an **ascending series states its own integer parts on the way up**, so `1 GB / 1.5 GB / 2 GB / 2.5 GB`, `2 mm / 2.5 mm / 3 mm / 3.5 mm` and `1 h / 2 h / 2.5 h / 4 h` all satisfied it and were reshaped into two-level trees. Half (ii) is what actually separates them: a numbering's first subsection is `x.1`, while a magnitude's fractional neighbour is whatever the quantity happens to be. Five sibling power rails (`3 V / 3.3 V / 5 V`) stay five siblings. Published because a reshaped tree must not be a silent one. |
| `outline` | list | The heading tree; node shape below. Built from prose only: a line inside a fenced code block is a transcript, so a shell comment (`# reset the board`) never becomes a node — it used to, and the node's span then ran past the closing fence, publishing a section the document never had over a code block that no longer terminates. An unclosed fence runs to the end of the body, as CommonMark renders it. |

### Outline node

| Key | Type | Meaning |
|---|---|---|
| `id` | str | `sec-0001`, document-order counter. **Positional** — inserting a heading shifts every later id, so it addresses a *place*, not a section. Kept because published bundles and the worked example already index by it; use `section_id` for anything that has to survive an edit. |
| `section_id` | str | `sha1(anchor)[:16]` — **content-derived** identity (`quality-plan.md` C2). Unchanged by an insertion, a table edit or a child's rewrite; changes when the heading itself is renamed, which is when the section really became another section. Document-scoped: the corpus key is `(doc_id, section_id)`, and `doc_id` is at the top of this same file. Same input `chunk_sections` hashes, so a chunk store and the outline name the same section the same way. |
| `parent` | str \| null | The parent node's `section_id`; `null` at top level. Lets a consumer walk the tree without re-nesting it, and lets a flattened node still say where it belongs. |
| `level` | int | Heading level — the length of the **leading run** of ATX hashes, never the count of hashes on the line, so `# Issue #42 metastability` is level 1 and not level 2. Or the depth inferred from the title's section numbering when `levels_inferred` is `true`. |
| `title` | str | Heading text. |
| `anchor` | str | **The URL fragment** a renderer emits for this heading: `sections.gfm_anchor(title)` — lowercase, everything that is not a word character, whitespace or a hyphen dropped, then every **run** of whitespace-and-hyphens collapsed to a single `-` and trimmed, **section number kept** (`1.2 Scope` → `12-scope`, `Overview - part 2` → `overview-part-2`, `Reset<TAB>sequence` → `reset-sequence`). A repeated title takes the renderer's suffix (`overview`, then `overview-1`), so the published set is exactly what `kb.body_anchors` makes addressable and a `#fragment` that resolves in the KB is the one advertised here (`quality-plan.md` C3, held by `tests/unit/backend/test_anchor_parity.py`). Two nodes whose anchors collide — a `## Overview 1` beside a second `## Overview` — collide identically in all three implementations, so a `ref` is ambiguous rather than silently wrong. Falls back to `section` when a title slugs to nothing. The `(preamble)` node and that `section` fallback are the two anchors with no matching fragment in the body: they name a region and an unaddressable heading, not link targets, so neither is a valid `ref`. |
| `line_span` | [int, int] | Half-open `[l0, l1)` into the body. |
| `self_tokens` | int | Body tokens before children. |
| `subtree_tokens` | int | Including all descendants. `parent.subtree == parent.self + Σ children.subtree` holds arithmetically. |
| `fingerprint` | str | `sha1` of this node's **own** body (children excluded), markdown stripped and whitespace/case normalised, first 16 hex. Partitions the document exactly like `self_tokens`, so a child's edit does not invalidate its ancestors, and the same prose re-extracted through another lane fingerprints the same. Pair with `section_id` to answer "same section, changed content?". |
| `tables` | list | Table nodes for every table **starting** in this node's own body; shape below. A list, not a count, since `quality-plan.md` C5 — a count told you the escalation matrix existed and gave you no way to cite it. Pipe art drawn inside a fenced code block is code, so it is never a table node. |
| `images` | list | Image nodes; shape below. |
| `links` | list | `{text, url, line}` harvested from this node's own body. URLs are verbatim: resolving one to another `doc_id` is the consumer's job. Link-shaped text inside a fenced code block is code, not connectivity, so the count matches `content.links`. |
| `children` | list | Nested nodes, same shape, arbitrarily deep. |

### Image node

| Key | Type | Meaning |
|---|---|---|
| `image_id` | str | `sha256(bytes)[:16]` — the same value appears in the filename, the body reference and here. |
| `ref` | str | `images/<sha16>.<ext>`. |
| `line` | int | Placement in the body. |
| `alt` | str | Deterministic; empty in the office pass today. |
| `caption` | str \| null | `null` until the caption stage runs. A VLM output, so it lives here and never in the report. |
| `bytes` | int | **Measured** from the extracted file. |
| `width` / `height` | int | Omitted for metafiles and unknown headers — never guessed. |

### Table node

| Key | Type | Meaning |
|---|---|---|
| `table_id` | str | `tbl-<sha1(anchor + normalised header row + ordinal within the section)[:10]>`. **Content-derived, not positional**: inserting a paragraph — or a whole earlier section — above the table moves `line` but not `table_id`, so a citation survives an edit. Two tables sharing a header row are kept apart by their section (different anchors) and, within one section, by the ordinal. Editing the header row *does* mint a new id: a citation that names the columns no longer describes it. |
| `line` | int | The header row's placement in the body. |
| `rows` | int | Header row **plus** data rows — the same convention `validate.md_structure` reports, so the outline and the fidelity gate state the same number. |
| `cols` | int | Cells in the header row (an escaped `\|` is a literal pipe, not a boundary). |
| `has_header` | bool | `True` by construction for a GFM pipe table, which is *defined* by its header + delimiter. The key exists so a future HTML-island table (`quality-plan.md` P3.6) can report `False` without a schema change. |

---

## `report.json` — what was measured

Validator only. Contains no model output; a caption or a classification never
appears here, only the *gate* over it.

### Identity and provenance

| Key | Type | Meaning |
|---|---|---|
| `doc_id`, `lane`, `source_format`, `source_relpath`, `source_sha256`, `markdown_sha256` | str | Mirror the front matter, so a failed document still carries them. |
| `converter` | str | Converter identity — see the front-matter table. |
| `generated_run` | str | Run id — present here because a failed document's report is its only artifact. |
| `token_model` | str | Names the tokenizer behind `content.tokens`. |
| `status` | str | `ok` \| `degraded` \| `failed`. `degraded` = converted with a named, measured deficiency; `failed` = no valid markdown. |
| `timing_ms` | map | `convert`, `validate`. `validate` is stamped after the pixel write, verification, orphan GC and caption-carry, so the two numbers account for the whole document. |

### `losslessness{}` — the gate

| Key | Type | Present | Meaning |
|---|---|---|---|
| `method` | str | always | `ooxml-ground-truth` \| `pdf-text-coverage` \| `pdf-ocr-transcription`. |
| `gate` | str | always | `pass` \| `fail` \| `best-effort`. A non-office lane is **structurally coerced** off `pass`: a buggy writer cannot talk the report into a claim its lane cannot support. |
| `token_recall` | float | office, pdf-text | Multiset recall. The office gate is `== 1.0` exactly; a miss fails and the markdown is withheld. |
| `content_recall` | float | office, pdf-text | Char-n-gram recall. Binding only when the source carries at least 3 non-ASCII alphanumeric characters. |
| `missing_tokens` | list | when recall < 1 | The tokens that did not survive. |
| `n_source_tokens` | int | pdf-text | The denominator. (`quality-plan.md` P2.6 adds it to the office lane.) |
| `figure_text_tokens` | int | pdf-text | Text buried in figure regions — excluded from the body metric and surfaced as **debt**, the one loss class only the VLM caption stage can recover. |
| `ocr_used` | bool | pdf | Whether OCR produced the text. |
| `note` | str | pdf-ocr | `"scanned source: no independent text layer to measure against"` — an explicit *unmeasured*, never an invented pass. |

### `structure_fidelity{}` — the second gate

Token recall answers *"is every word still here?"*. This answers *"does it still
mean the same thing?"* — and they are different questions. Emphasis markers, list
indentation and table geometry contain no tokens, so a nested numbered procedure
once came out **renumbered** at `token_recall: 1.0` with `structural_errors: 0`.
An operator working an outage from that document would have run the wrong step.

The verdict compares two independently-derived fact vectors: what a CommonMark
renderer sees in the emitted markdown (`backend.validate.md_structure`) against
what the source XML says should be there (`backend.ingest.docx_source_structure`,
which shares no traversal code with the converter — the same rule that makes the
token gate trustworthy).

| Key | Type | Present | Meaning |
|---|---|---|---|
| `method` | str | always | `ooxml-structure-ground-truth` \| `unmeasured`. |
| `gate` | str | always | `pass` \| `fail` \| `best-effort` \| `unmeasured`. **A hard fail on the office lane**: the markdown and the pixels are withheld, exactly as for a recall miss. Non-office lanes are coerced to `best-effort` for the same reason losslessness is — a PDF has no ground-truth semantic tree, so a match is agreement, not proof. |
| `compared` | int | always | How many of the facts below **observed something on this document** — evidence, not schema. A fact both sides read as absent-or-zero is not counted, because a number that cannot fall is not a measurement: a one-sentence memo reports `0` and a real office bundle reports around `9` of the fifteen, not `15`. `0` means nothing was graded, which is why the gate then reads `unmeasured` rather than `pass`. |
| `unmeasured` | list | when a ground truth is present but partial | The names of facts the ground truth did not supply, so they were compared against nothing. Omitted entirely when there is no ground truth at all — `method` and `gate` already read `unmeasured` there. `gate: "pass"` answers only for what was measured, and this list is what makes "everything measured" auditable rather than a claim. |
| `deltas` | list | always | Every disagreement, as `{fact, source, markdown}`. Empty on a pass. A gate that reported only pass/fail would teach nobody anything; this names what moved. |

The compared facts are a **closed** list of **fifteen**: `headings` (count by
level), `heading_path` (the tokens of every heading title, with its level, in
document order), `list_items` (count by nesting depth), `ordered_items`,
`bullet_items`, `ordered_numbers` (the number a renderer **prints** beside each
ordered item, in order), `strong`, `em`, `strike`, `code_spans`, `code_blocks`,
`links`, `tables` (rows × cols **and the content of every cell**),
`list_item_words` (the text of every list item, in order), and `thematic_breaks`
(horizontal rules — a docx paragraph can never legitimately render as one, so any
count above zero on the markdown side is a substitution). Widening it is a
deliberate edit with a test behind it, never a side effect of adding a field; the
executable list is `backend.validate._mdcheck._FIDELITY_FACTS`.

The last three exist because counting is not enough, and that was demonstrated
rather than assumed. Swap two values between rows of an escalation table, or swap
two steps of a numbered procedure, and the dimensions, the counts and the token
multiset are all **identical** — `token_recall: 1.0`, `structure_fidelity: pass`,
`structural_errors: 0` — while the document now pages the wrong rota and instructs
the wrong step. Content is compared as tokens (`[a-z0-9]+` after lowercasing), so
escaping, emphasis markers and pipes are not differences; a vertical merge is
forward-filled on **both** sides, because repeating the value is declared policy
rather than a defect.

`ordered_numbers` is the same argument for the *number*. CommonMark takes a list's
start from its first marker and then counts up on its own, so the digit beside a
step is a property of the list, not of the line: a screenshot dropped between step
2 and step 3 splits one list into two and the second one prints 1, 2 again — with
the same item count, the same depth histogram, the same words and the same tokens
as the whole procedure. The source side derives the number Word shows (per
numbering instance, per level, `w:start` and `w:startOverride` honoured, deeper
levels restarting when a shallower one advances); the markdown side derives the
number a renderer shows. A split list, a wrongly merged one and an ignored start
are then all the same kind of difference. `w:lvlRestart` is not read on either
side, so a document that overrides the default restart is the one case both sides
agree about wrongly.

Not compared, on purpose: `images`, because at gate time they are HTML-comment
sentinels rather than links and the `images{}` block already grades them against
the pixels on disk.

Measured on **docx only** so far. pptx (every paragraph is a bullet) and xlsx
(every sheet is one table) report `unmeasured`: claiming to grade them without
writing a second implementation would be the exact dishonesty this gate exists to
end.

### `run{}` — what this run was

The block that makes a run repeatable. Present on **every** report including
failures, because a failed document publishes `report.json` and nothing else — and
a failure is exactly the run somebody needs to reconstruct.

| Key | Type | Meaning |
|---|---|---|
| `entrypoint` | str | Which writer produced this, e.g. `build_bundle`. |
| `run_id` | str | Joins to `manifest.jsonl` and `runs.jsonl`. |
| `argv` | list | The switches that were used, verbatim — **except** path values, which are replaced by `<src>` / `<out>` / `<path>`. The root `CLAUDE.md` forbids an absolute host path in published output. |
| `source_root_id` | str | `sha256(abspath)[:16]` of `--src`. Answers "was this the same tree?" without saying where it is. |
| `code.name` / `code.version` / `code.commit` / `code.dirty` | str/bool | The converter's identity, read from `pyproject.toml` and `.git`. `dirty` is **omitted when unknown**, never reported `false`. |
| `host.python` / `host.implementation` / `host.platform` / `host.executable_basename` | str | The interpreter and OS. Never `platform.node()` — a hostname is neither needed to repeat a run nor safe to publish. |
| `tools` | map | External binaries and their versions, e.g. `{"soffice": "7.6.4.1"}`. Present only when one was used. `replay_run.py` re-asks each of these on the replay machine and reports a mismatch — **or the absence of a probe for that tool**, because an unverified toolchain is not an unchanged one. |
| `config_ref` | str | `runs.jsonl#<run_id>` — where the full resolved configuration is. It is identical for every bundle in a run, so it is stored once rather than duplicated into every report. Named, not silently omitted. Resolved by `(run_id, entrypoint)`: both the writer and the enricher take `--run-id`, and pinning both to the same id is normal, so an id alone does not identify a row. |

### `runs[]` — every stage that wrote into this report

A bundle is not written once. `build_bundle.py` converts it; `enrich_metadata.py`
then rewrites `meta.id`, `meta.uid` and `meta.source.url` in the same files. Each
of those is a run with its own switches, and until they were recorded here the
switches that decided three graded fields (`D2`/`D3`/`D4`) lived in no artifact at
all — the same enrichment run with `--namespace acme.internal` left `report.json`,
`runs.jsonl` and `manifest.jsonl` byte-identical.

Each entry has exactly the shape of `run{}` above. Oldest first, **one entry per
`entrypoint`** — a stage that runs again replaces its own entry rather than
appending, so the list cannot grow without bound. `run{}` is `runs[0]`: it stays
the run that produced the *markdown*, because a later stage overwriting it would
trade the conversion's provenance for its own.

A rebuild (`--force`) starts the history over with the writer's entry alone, which
is correct — the earlier stages no longer describe these bytes, and the metadata
overlay has to be re-run anyway.

`scripts/replay_run.py --report R --stage enrich_metadata` replays a chosen entry;
with no `--stage` it replays `run{}`.

### `decisions[]` — what the pipeline chose

`warnings[]` carries **problems**; `decisions[]` carries **choices**. Keeping them
apart is what makes "how many documents took the text-layer fallback" a countable
question instead of a grep through prose.

| Key | Type | Meaning |
|---|---|---|
| `code` | str | One of a **closed** list (below). An unnamed decision is one nobody can aggregate. |
| `stage` | str | The `run.entrypoint` of the stage that took this branch. Two entrypoints write into this one list, so an unattributed record can say neither which run chose it nor which records a re-running stage may replace. A stage rewrites exactly its own records and leaves every other stage's alone. |
| `chose` | any | What was chosen. |
| `reason` | str | Why, in one human phrase. |
| `evidence` | map | The numbers that decided it, when there were any. Values are scrubbed of absolute host paths on the way in, like every other published value. |

Codes: `lane_selected`, `preconvert`, `ocr_routed`, `body_source`,
`tokenizer_selected`, `cache_hit`, `gate_coerced`, `empty_source`,
`captions_carried`, `skipped_existing`, `metadata_tier`, `vocabulary_selected`,
`identity_namespace`, `permalink_base`.

`lane_selected` is the conversion stage's and carries one `evidence` sub-key:

| Code | `chose` | `evidence` | What it decided |
|---|---|---|---|
| `lane_selected` | `office` \| `pdf` \| `text` \| `legacy` | `ext` | Which converter read the document, and the lower-cased source extension it routed on (`scripts/build_bundle.py`, `scripts/build_pdf_bundle.py`). The lane decides which gates apply at all — the office lane hard-fails `structure_fidelity`, the PDF lane reports `best-effort` — so a bundle whose lane is unexplained cannot be told from one whose gate was never run. |

The last four are the enrichment stage's, and each moves a field the rubric grades:

| Code | `chose` | `evidence` | What it decided |
|---|---|---|---|
| `metadata_tier` | `model` \| `deterministic` | `model` | Whether tier-2 fields were filled or left `pending`. A no-model run is a complete success, not a failure — this is what says which one you are reading. |
| `vocabulary_selected` | `v<n>` | `schema_version` | Which closed term list every value was graded against, and therefore which values were rejected. |
| `identity_namespace` | the namespace, or `(none)` | `namespace` | The prefix `meta.id` and its `uid` alias were derived under. Changing it rewrites every id in the corpus. |
| `permalink_base` | `absolute` \| `relative` | `base` | Whether `meta.source.url` is a clickable permalink or a relative URI reference. |

### `content{}` — what the markdown contains

`chars`, `tokens`, `headings`, `tables`, `images`, `links`, `lists`,
`code_blocks`, `formulas`. Counts, no gate.

### `savings{}` — the exchange rate

`source_repr` (`ooxml-xml`), `source_chars`, `markdown_chars`, `reduction_ratio`,
`saved_pct`. **Emitted only when the source side was actually measured** — the
PDF lane omits the block rather than inventing a number. Informational: no gate,
never moves `status`. Note the denominator is decompressed OOXML *including
markup*, which is not a "before" any consumer would have shipped downstream.

### `structure{}` — outline summary and the coverage gate

| Key | Type | Meaning |
|---|---|---|
| `max_depth` | int | **Tree depth** — how deep the outline actually nests, not `max(level)`. The two differ exactly when the hierarchy was never built: nine flat siblings titled `1`, `1.1`, `1.1.1` carry levels up to 2 and nest not at all, so this reports 1 (`quality-plan.md` C4). |
| `largest_section_tokens` | int | The biggest `subtree_tokens` anywhere in the outline. Includes the root, so on a single-H1 document it restates `total_tokens`. |
| `largest_leaf_tokens` | int | The biggest `subtree_tokens` among **leaf** nodes — the largest unit a consumer actually retrieves or embeds. This is the budget signal; `largest_section_tokens` is the containment one. `0` when the outline is empty. |
| `has_toc` | bool | Mirrors `structure.json`. |
| `coverage.content_lines` | int | Non-blank body lines. |
| `coverage.covered_lines` | int | Lines inside some outline node's span. |
| `coverage.toc_lines` | int | Intentional table-of-contents furniture skip. |
| `coverage.uncovered_lines` | int | Body lines the outline **lost**, counted two ways. A line outside every node's `line_span` is uncovered; **and** a line the body marks up as an ATX heading that no outline node *opens on* is uncovered even when an ancestor node's span contains it. The second half is what makes this number falsifiable at all: line coverage alone was `0` by construction for every possible input, because a dropped heading's lines are re-attributed to its ancestor, whose span already covers them. "Does a node open here?" is the one question an ancestor cannot backfill. Fenced lines and detected TOC furniture are excluded — the outline is right not to open a node on those — so `covered_lines + toc_lines` no longer necessarily equals `content_lines` on a damaged document. |
| `coverage.ratio` | float | `(covered + toc) / content`. |
| `coverage.gate` | str | `pass` \| `degraded`. Any uncovered line degrades `status` and adds an `outline_uncovered_content` warning. Never touches losslessness — the text is still whole. |

### `images{}` — the pixel-side gate

`referenced` (body `![](images/…)` links, the ground truth), `unique_files`,
`extracted`, `missing`, `orphans`, `orphans_removed`, `verified` (files re-hashed
from disk whose `sha16` matches their own name), `gate` (`pass` \| `degraded`). A
degraded gate degrades `status` but never fails losslessness.

`orphans` is what **remains** after the per-build sweep, so a non-zero value means
the GC itself failed and the gate says so; `orphans_removed` is how many it took
out. Two questions — "are there orphans now" and "how much churn is this corpus
seeing" — and one number could only ever answer whichever you asked first. Body images are HTML-comment
sentinels the recall metric cannot see, which is exactly why this block exists.

### `captions{}` — overlay coverage

`enabled`, `expected`, `captioned`, `furniture`, `useless`, `pending`, `model`,
`prompt_sha`, `gate` (`disabled` \| `pending` \| `incomplete` \| `complete`).
**Never touches `status`**: captioning is a re-runnable overlay, so an
un-captioned but lossless document stays `ok`.

### `doc_meta{}` — metadata overlay coverage

`enabled` (a model was reachable), `schema_version`, `vocab_version`, `expected`
(counted over the **merged** view, so it includes the fields stored in
`knowledge.json`), `filled`, `authored`, `invalid`, `pending`, `model`,
`prompt_sha`, `gate`. Also never touches `status`. Note `gate` reads `pending`
only while **nothing** has been filled, so since the deterministic floor landed a
model outage reports `incomplete` — the same way an authored title has always read.
`pending` is the number that says whether to re-run. One deliberate difference from
`captions{}`: a non-zero `invalid` keeps the gate off `complete`, because a value
outside a closed vocabulary is worse than an absent one — it silently becomes a
new term for everything that groups by that field.

### `structural_errors` / `structural_warnings`

Counts from the markdown tree validator (bad characters, table column mismatches,
heading jumps).

### `warnings[]`

`{code, detail}`, plus code-specific numeric fields. Every deliberate drop, every
fallback and every hygiene event is **named, never silent**.

| Code | Lane | Extra fields | Meaning |
|---|---|---|---|
| `dropped_headers_footers` | office | `parts`, `chars` | Page furniture excluded by policy, with the size of what was dropped. Does not degrade `status`. |
| `libreoffice_preconvert` | office | — | A legacy format was converted to its OOXML sibling first; names the soffice **version**. |
| `image_bytes_missing` | both | — | A picture sentinel had no bytes in the package. Always mirrors `images.missing`. |
| `orphan_images_removed` | both | — | Files on disk with no reference, swept by the per-build GC. |
| `images_not_in_outline` | both | — | An image link attached to no outline node, so it is uncaptionable. |
| `outline_uncovered_content` | both | — | Body lines fell outside every outline node; degrades `status`. |
| `pdf_toolchain` | pdf | — | The lane's provenance stamp: docling + docling-core always, poppler when it supplied the ground-truth layer. On every pdf/html report, failures included. |
| `ocr_transcription` | pdf | — | The source was scanned. |
| `pdf_text_layer_fallback` | pdf | — | Docling's markdown provably dropped body content the text layer holds, so the layer was used instead. |
| `pdf_content_loss` | pdf | — | Measured real loss under the explained-gap model; degrades `status`. |
| `image_inline_bailed` | pdf | — | Placeholder/picture count mismatch: positional binding was unsafe, so no pixels were written. A detected, gated loss — never a mis-bound figure. |
| `flattened_table_spans` | office | `horizontal`, `vertical` | GFM has no colspan or rowspan. A horizontal span keeps its text in the left-most column and pads the rest; a vertical span is **repeated** down its continuation rows so each row stays self-contained for row-wise chunking. Counted from the source grid, so the number is right even for the formats whose converter does not read the attribute. |
| `tracked_changes_resolved` | office | `insertions`, `deletions`, `moves` | The source still carries revision marks. The **final** view is taken: insertions are live text, deletions are dropped, `w:moveFrom` is skipped as a stale copy. Correct, and previously silent — a reader had no way to know the document they were handed was still under revision. |
| `decimalised_list_numbering` | office | `count`, `formats` | CommonMark has exactly one ordered marker, the decimal digit, so a list Word labels `A.` / `iii.` / `01.` can only be written `1.`, `2.`, `3.`. The **position** survives — prose saying "see step B" still lands on the second item — and the label does not. `formats` names the `w:numFmt` values involved. Does not degrade `status`. |
| `lifted_text_boxes` | office | `count` | Text boxes anchored inside a **numbered step**. A box is always lifted out of its anchor paragraph (a pipe table cannot live inside a sentence); where the lifted content allows it, the converter re-indents it to the step's content column so the step stays whole, and where it does not — the box holds its own list, or a code paragraph whose fence must sit at column 0 — the box is emitted beside the list instead. Either way the reader is not looking at what Word drew, so the anchors are counted. The step numbering is unaffected: the counters survive a list being closed. |
| `dropped_embedded_objects` | office | `parts` | Embedded OLE objects (`word/embeddings/*`) are not converted: an embedded document is a document, and this lane converts one file at a time. `--audit-parts` cannot see these either — it inspects only members ending in `.xml`. Counted from the **effective** package, so this fires on the LibreOffice lane too: the member list is taken from the file the reader actually opened (the soffice-produced sibling for a legacy or ODF source), not from the pre-conversion source — which is an ODF package or a CFB binary and could never contain `word/embeddings/*`, so the check was dead on that whole lane. |
| `empty_source` | both | `source_tokens` | The source carried no gradeable text, so `token_recall: 1.0` is vacuous rather than evidence. Without this code a zero-byte upload reported in exactly the vocabulary of a real conversion. |

---

## `knowledge.json` — the extracted graph payload

Written by `scripts/enrich_metadata.py`, never by the bundle writer. **A bundle
that has not been enriched, or whose payload is empty, simply does not have one**
— the same way it has no captions until the caption stage runs.

| Key | Type | Meaning |
|---|---|---|
| `doc_id`, `id`, `uid` | str | Header — repeated from `document.md` so a graph loader never opens the markdown. |
| `schema_version`, `vocab_version` | int | Which inventory and term list wrote this. |
| `markdown_sha256` | str | The body these claims were extracted from. |
| `entities` | groups | `{group: [{name, type, ref, …}]}`. |
| `relations` | records | `[{s, p, o, ref, …qualifiers}]`. |
| `decisions`, `risks`, `open_questions` | records | Record lists; see [`vocabulary.md`](vocabulary.md). |
| `links` | groups | `{category: [{url, ref, title, line, source}]}`. |

**Every knowledge record cites the section that asserts it.** `ref` is a
`#anchor` from the same set `kb.body_anchors` publishes, and it is **required**:
`{"p": "runs_on"}` used to be a schema-valid relation, which made "which section
says this?" unanswerable and the claim uncheckable. A record arriving without a
`ref`, or with one that names no heading in this document, is rejected on the way
in (`missing-required-ref` / `ref-not-an-anchor` in the run's rejection log) rather
than stored and linted later. Required alongside it: `s`/`p`/`o` on a relation,
`name` on an entity, `url` on a link.

**`links` records say where each edge came from.** A record's `source` is
`extracted` when the URL was harvested out of the body (via `structure.json`, at
recall 1.0) and `generated` when a model proposed it. Per-record, because per-field
is the wrong grain: one `source: extracted` on the block would vouch for the
guesses too. The field-level `_provenance.links.source` reports the **weakest**
origin present, for the same reason. A model may add records beside the harvested
ones; it can never delete one or overwrite it with a different reading of the same
URL.
| `*_proposed` | list | Registry proposals, always following their own field so promotion evidence never lands in a different file from the values it is evidence about. |
| `_provenance` | map | Splits with its fields, so neither file is a fragment. |

A field present in **both** files is an **error** (`meta_collisions`), not a
silent merge — the loser would be rewritten away by the next run. A file that
exists but will not parse is also an error that stops the document being written:
treating it as "no knowledge yet" would let the next run regenerate over entities
a person corrected by hand.

---

## `manifest.jsonl` — the corpus index

One JSON object per line, appended into the shared root by **every** stage that
writes a bundle — both converters and `enrich_metadata.py`.

| Key | Type | Meaning |
|---|---|---|
| `doc_id` | str | Join key. |
| `source_relpath` | str | De-identified source path. |
| `lane` | str | `office` \| `pdf`. |
| `status` | str | `ok` \| `degraded` \| `failed`. Always the **conversion** verdict. An enrichment row reads it out of `report.json` rather than reporting its own health here: one column carrying two vocabularies would make `ok` mean "converted" on some rows and "nothing outstanding" on others, with nothing to tell them apart. |
| `markdown_sha256` | str | Empty on failure. On an enrichment row it is the body those claims were extracted from — enrichment never touches the body, so the value is carried, not recomputed. |
| `source_sha256` | str | Hash of the source bytes; `""` on a row for a document this run did not read. Carried onto enrichment rows from the report, so `corpus_sha256` over an enrichment run names the same corpus the build named. |
| `error` | str | Failure reason, `""` on success. |
| `run_id` | str | The run that wrote this row. Joins to `runs.jsonl` together with `stage`. |
| `ts` | str | UTC timestamp of the row. |
| `stage` | str | Which entrypoint wrote the row: `build_bundle`, `build_pdf_bundle`, `enrich_metadata`. The same `run_id` may legitimately name a build **and** an enrichment (pinning both with `--run-id` is how two stages are made comparable), so this is the other half of the join key. |
| `action` | str | What this run **did**. Writers: `built`, `forced`, `skipped` (already present), `deferred` (`--limit` cut it). `enrich_metadata`: `enriched` (metadata written), `unchanged` (the run converged and wrote nothing — the byte-identical re-run), `failed`, `deferred`. |

**An index, not a source of truth** — every fact in it also lives in the bundle —
but it *is* a run log: one row per document per run, skips and deferrals included.
Without the skip rows the number of runs is unrecoverable from disk, which is
precisely what stopped the old manifest being a log of anything; and without the
enrichment rows, a stage that rewrote `meta.id` across the whole corpus left the
log looking exactly as it had before it ran.

## `runs.jsonl` — one row per run

At the output root, beside `manifest.jsonl`. Each row is the full `run{}` block —
**including** the `config` map that the per-document copies reference rather than
duplicate — plus:

| Key | Type | Meaning |
|---|---|---|
| `entrypoint` | str | Which stage this row is for: `build_bundle`, `build_pdf_bundle`, `enrich_metadata`. Rows are keyed by `(run_id, entrypoint)`, not by `run_id` alone. |
| `started_at` / `finished_at` | str | UTC bounds of the run. |
| `counts` | map | Writers: `ok`, `degraded`, `failed`, `skipped`, `deferred`. `enrich_metadata`: `ok`, `incomplete`, `failed`, `unchanged`, `deferred`. |
| `documents` | int | Rows this run wrote to the manifest. |
| `corpus_sha256` | str | Content identity of the document set: `sha256` over sorted `doc_id:source_sha256` pairs. Two runs over the same documents agree; one changed byte does not. Skipped and deferred rows carry no hash and are excluded, so the value names the documents the run actually **read** — which is what keeps it checkable under `--only` and `--limit`. `replay_run.py` re-hashes exactly those documents in the tree you hand it and reports a mismatch. |
| `config` | map | Every resolved setting as `{value, from}`, where `from` is `flag`, `env`, `file` or `default`. Absolute paths appear as `<path:sha16>` so a value stays comparable without being disclosed. `_env_present` lists the `DOC2MD_*` names that were merely set — a variable whose value equals the default is invisible to the resolution diff and still matters when re-establishing the run elsewhere. |

Keys prefixed **`cli.`** are a stage's own switches, resolved through argparse
rather than through the ingest loader — `cli.namespace`, `cli.source_base_url`,
`cli.excerpt_chars` and the rest of `enrich_metadata.py`'s options. Their `from` is
`flag`, `env` or `default`, and when it is `env` the record also carries `env`, the
name of the variable it came from. That is the one setting no command line shows
you: `--source-base-url` defaults to `$DOC2MD_SOURCE_BASE_URL`, so a run that took
the permalink base from the environment recorded nothing in `argv`, and a person
re-establishing it elsewhere would rebuild the corpus with different links and no
artifact would say why.

### Side logs

| File | Written by | Contents |
|---|---|---|
| `_kb_meta.jsonl` | `enrich_metadata.py` | The answer cache: `{key, doc_id, reply, ts}`, keyed on `sha(body)` + `sha(model+prompt+vocab_version)`. |
| `_kb_meta_coverage.jsonl` | `enrich_metadata.py` | One row per document per run: counts, rejections, proposals, revalidations, `floor` — the fields the deterministic floor filled without a model — and the bundle facts the manifest row is built from (`source_relpath`, `lane`, `markdown_sha256`, `source_sha256`, `bundle_status`). Appended even when nothing changed. A *diagnostic* log, not the run log: `manifest.jsonl` is the run log, and enrichment now appends to it. |
| `_caption_coverage.jsonl` | `caption_bundles.py` | Per-image caption verdicts, for measuring how a prompt performs. |
