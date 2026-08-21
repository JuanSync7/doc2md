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
<out>/manifest.jsonl
```

`doc_id` is `sha1(source_relpath)[:16]`. `image_id` is `sha256(bytes)[:16]`.
Those two are the only join keys.

**A failed document publishes `report.json` only** — no `document.md`. That is why
run provenance has to live in the report as well as the front matter.

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
| `outline` | list | The heading tree; node shape below. |

### Outline node

| Key | Type | Meaning |
|---|---|---|
| `id` | str | `sec-0001`, document-order counter. **Positional** — inserting a heading shifts every later id (`quality-plan.md` C2). |
| `level` | int | Heading level (ATX hash count). |
| `title` | str | Heading text. |
| `anchor` | str | Dedup key from `normalize_title`, disambiguated within the document as `base#2`. **Not a URL fragment** — the repo's ref-resolving anchors come from `kb.body_anchors` instead (`quality-plan.md` C3). |
| `line_span` | [int, int] | Half-open `[l0, l1)` into the body. |
| `self_tokens` | int | Body tokens before children. |
| `subtree_tokens` | int | Including all descendants. `parent.subtree == parent.self + Σ children.subtree` holds arithmetically. |
| `tables` | int | Count only — tables are not addressable nodes yet (`quality-plan.md` C5). |
| `images` | list | Image nodes; shape below. |
| `links` | list | `{text, url, line}` harvested from this node's own body. URLs are verbatim: resolving one to another `doc_id` is the consumer's job. |
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
| `tools` | map | External binaries and their versions, e.g. `{"soffice": "7.6.4.1"}`. Present only when one was used. |
| `config_ref` | str | `runs.jsonl#<run_id>` — where the full resolved configuration is. It is identical for every bundle in a run, so it is stored once rather than duplicated into every report. Named, not silently omitted. |

### `decisions[]` — what the pipeline chose

`warnings[]` carries **problems**; `decisions[]` carries **choices**. Keeping them
apart is what makes "how many documents took the text-layer fallback" a countable
question instead of a grep through prose.

| Key | Type | Meaning |
|---|---|---|
| `code` | str | One of a **closed** list (below). An unnamed decision is one nobody can aggregate. |
| `chose` | any | What was chosen. |
| `reason` | str | Why, in one human phrase. |
| `evidence` | map | The numbers that decided it, when there were any. |

Codes: `lane_selected`, `preconvert`, `ocr_routed`, `body_source`,
`tokenizer_selected`, `cache_hit`, `gate_coerced`, `empty_source`,
`captions_carried`, `skipped_existing`.

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
| `max_depth` | int | `max(level)`, not tree depth (`quality-plan.md` C4). |
| `largest_section_tokens` | int | Includes the root, so a single-H1 document reports the whole document (C4). |
| `has_toc` | bool | Mirrors `structure.json`. |
| `coverage.content_lines` | int | Non-blank body lines. |
| `coverage.covered_lines` | int | Lines inside some outline node's span. |
| `coverage.toc_lines` | int | Intentional table-of-contents furniture skip. |
| `coverage.uncovered_lines` | int | Body lines the outline **lost**. |
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
`prompt_sha`, `gate`. Also never touches `status`. One deliberate difference from
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
| `entities` | groups | `{group: [{name, type, …}]}`. |
| `relations` | records | `[{s, p, o, …qualifiers}]`. |
| `decisions`, `risks`, `open_questions` | records | Record lists; see [`vocabulary.md`](vocabulary.md). |
| `links` | groups | `{category: [...]}`. |
| `*_proposed` | list | Registry proposals, always following their own field so promotion evidence never lands in a different file from the values it is evidence about. |
| `_provenance` | map | Splits with its fields, so neither file is a fragment. |

A field present in **both** files is an **error** (`meta_collisions`), not a
silent merge — the loser would be rewritten away by the next run. A file that
exists but will not parse is also an error that stops the document being written:
treating it as "no knowledge yet" would let the next run regenerate over entities
a person corrected by hand.

---

## `manifest.jsonl` — the corpus index

One JSON object per line, appended by both writers into the shared `--out` root.

| Key | Type | Meaning |
|---|---|---|
| `doc_id` | str | Join key. |
| `source_relpath` | str | De-identified source path. |
| `lane` | str | `office` \| `pdf`. |
| `status` | str | `ok` \| `degraded` \| `failed`. |
| `markdown_sha256` | str | Empty on failure. |
| `source_sha256` | str | Hash of the source bytes; `""` on a row for a document this run did not read. |
| `error` | str | Failure reason, `""` on success. |
| `run_id` | str | The run that wrote this row. Joins to `runs.jsonl`. |
| `ts` | str | UTC timestamp of the row. |
| `action` | str | What this run **did**: `built`, `forced`, `skipped` (already present), `deferred` (`--limit` cut it). |

**An index, not a source of truth** — every fact in it also lives in the bundle —
but it *is* a run log: one row per document per run, skips and deferrals included.
Without the skip rows the number of runs is unrecoverable from disk, which is
precisely what stopped the old manifest being a log of anything.

## `runs.jsonl` — one row per run

At the output root, beside `manifest.jsonl`. Each row is the full `run{}` block —
**including** the `config` map that the per-document copies reference rather than
duplicate — plus:

| Key | Type | Meaning |
|---|---|---|
| `started_at` / `finished_at` | str | UTC bounds of the run. |
| `counts` | map | `ok`, `degraded`, `failed`, `skipped`, `deferred`. |
| `documents` | int | Rows this run wrote to the manifest. |
| `corpus_sha256` | str | Content identity of the document set: `sha256` over sorted `doc_id:source_sha256` pairs. Two runs over the same documents agree; one changed byte does not. |
| `config` | map | Every resolved setting as `{value, from}`, where `from` is `flag`, `env`, `file` or `default`. Absolute paths appear as `<path:sha16>` so a value stays comparable without being disclosed. `_env_present` lists the `DOC2MD_*` names that were merely set — a variable whose value equals the default is invisible to the resolution diff and still matters when re-establishing the run elsewhere. |

### Side logs

| File | Written by | Contents |
|---|---|---|
| `_kb_meta.jsonl` | `enrich_metadata.py` | The answer cache: `{key, doc_id, reply, ts}`, keyed on `sha(body)` + `sha(model+prompt+vocab_version)`. |
| `_kb_meta_coverage.jsonl` | `enrich_metadata.py` | One row per document per run: counts, rejections, proposals, revalidations. Appended even when nothing changed. |
| `_caption_coverage.jsonl` | `caption_bundles.py` | Per-image caption verdicts, for measuring how a prompt performs. |
