---
title: Configuration reference
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [reference, configuration, cli, environment, switches]
summary: Every CLI switch and DOC2MD_* environment variable, and what each one changes in the produced bundle.
---

# Configuration reference

Every knob, and — the part that matters — **what moves in the output when you turn
it**. A flag whose entry only restates its `--help` string is not documented; see
[`CLAUDE.md`](CLAUDE.md).

Bound to the code by `tests/integration/test_docs_parity.py`: a flag or
`DOC2MD_*` variable that exists but is not listed here fails CI, and vice versa.

## How a value resolves

```
--flag  >  $DOC2MD_*  >  config/default.local.toml [ingest]  >  default.example.toml  >  built-in
```

Two things about this chain are worth knowing before you rely on it:

- **A malformed value silently reverts to the default.** `_as_int` / `_as_float` /
  `_as_bool` / `_as_ext_tuple` never raise, so `DOC2MD_MIN_RECALL=high` gives you
  `0.80` and no warning. The one exception is `DOC2MD_INGEST_BACKEND`, which
  raises `ValueError` on an unknown value.
- **Paths derive from the repo root or from configuration, never from a hardcoded
  host path** (root `CLAUDE.md`). `--src`/`--out` defaults resolve through
  `load_source_root()` and `<repo>/data/…`.

---

## The everyday pipeline

Four stages, in order. Every stage is safe to run twice.

### `build_bundle.py` — the office/text lane writer

Reads source documents, converts them, validates the conversion, builds the
outline, and writes one bundle per document. Python 3.6, stdlib only.

| Flag | Default | What changes in the output |
|---|---|---|
| `--src` | `$DOC2MD_SRC` / `[paths].source_docs` | Which tree is walked. Also fixes `doc_id`, which is `sha1(source_relpath)[:16]` — **two corpora with the same internal layout produce the same ids**. |
| `--out` | `data/bundles` | Where bundles and `manifest.jsonl` land. Shared with the PDF writer: both lanes write one root and one manifest. |
| `--accept` | all supported | Comma-separated extensions. Anything else is not walked at all — no bundle, no manifest row, no failure record. |
| `--only` | (none) | Repeatable. Build only this `doc_id` or source basename; everything else is untouched, not rebuilt, not removed. |
| `--limit` | `0` (no cap) | Stop after N documents. The manifest then describes a **partial** corpus with nothing marking it partial. |
| `--force` | off | Rebuild bundles that already exist and are not `failed`. Carries unchanged images' captions forward by `image_id`, so it does not destroy caption enrichment — but it *does* reset `captions.model` and `captions.prompt_sha` to `""` (see Traps). |
| `--run-id` | UTC timestamp | The value stamped into `generated_run` in front matter and `report.json`. Pin it to make two runs comparable; a failed document's report is the only artifact carrying it. |
| `--tokenizer` | `char` | `backend[:model]`, e.g. `tiktoken:cl100k_base`. Changes every `self_tokens`/`subtree_tokens`/`total_tokens` in `structure.json` and `content.tokens` in `report.json`, and is named in `token_model` so the numbers stay self-describing. **Does not** move any gate — recall is computed on its own tokenizer. |

### `build_pdf_bundle.py` — the docling lane writer

Same bundle shape, different losslessness method. Needs Python 3.9+ and the
`docling` extra.

Shares `--src --out --accept --only --limit --force --run-id --tokenizer` with
identical meaning, plus:

| Flag | Default | What changes in the output |
|---|---|---|
| `--ocr` | `auto` | `auto` routes a page to OCR when no usable text layer is found; `on` forces it; `off` never OCRs. Forcing OCR makes `losslessness.method` `pdf-ocr-transcription`, which has **no independent layer to measure against** — the report then carries no recall numbers at all, by design. |
| `--threads` | `0` (library default) | Caps docling/torch CPU threads. Throughput only; no output key moves. |

### `caption_bundles.py` — the figure-caption overlay

Walks each bundle's `structure.json` image nodes and fills `caption` in place.
Never touches `document.md`, `status`, or the losslessness verdict.

| Flag | Default | What changes in the output |
|---|---|---|
| `--bundles` | `data/bundles` | Which bundle root to walk. |
| `--only` / `--limit` | (none) / `0` | Narrow the pass. Un-visited images stay `caption: null` and the gate stays `incomplete`. |
| `--domain` | `""` | Corpus-level grounding text prepended to the caption prompt. Changes `captions.prompt_sha`, so it **invalidates the caption cache** and re-captions everything. |
| `--domain-file` | `""` | Same, read from a file. Overrides `--domain`. (Missing from the script's own docstring.) |
| `--prompt-file` | `""` | Replaces the base instruction wholesale. Same cache consequence. |
| `--no-context` | off | Stops sending the surrounding body lines, so captions are image-only. Usually lowers the useful rate; measurable in `_caption_coverage.jsonl`. |
| `--context-radius` | `12` | Body lines of surrounding text sent as context. |
| `--no-cache` | off | Ignore stored captions and re-ask. Here the fresh answers go to a **throwaway** per-pid cache that is deleted at exit — unlike `enrich_metadata --no-cache`, which persists them. |
| `--vlm-url` / `--vlm-model` | from config | The VLM endpoint and model name. The model name is recorded in `captions.model`. |

### `enrich_metadata.py` — document metadata (tiers 0/1/2)

Writes the `meta:` block into `document.md` front matter, the graph payload into
`knowledge.json`, and the `doc_meta` gate into `report.json`.

**This stage is a writer, and it records itself like one.** Every run appends a
`runs.jsonl` row (`entrypoint: enrich_metadata`, the resolved configuration with
the source of each value), one `manifest.jsonl` row per document per run
(`stage: enrich_metadata`, `action` one of `enriched` / `unchanged` / `failed` /
`deferred`), an entry in each report's `runs[]`, and `decisions[]` records carrying
`stage: enrich_metadata`. Before that it recorded nothing at all: a run with
`--namespace` and `--source-base-url` rewrote `meta.id`, `meta.uid` and
`meta.source.url` while `report.json`, `runs.jsonl` and `manifest.jsonl` stayed
byte-identical, so the switches that produced the corpus you are reading were
recoverable from no artifact. `replay_run.py --stage enrich_metadata` replays it.

| Flag | Default | What changes in the output |
|---|---|---|
| `--bundles` | `data/bundles` | Bundle root to walk. Also where this stage's `manifest.jsonl` and `runs.jsonl` rows land — the same files the writers append to, since it is the same root. Recorded as `<src>` in `run.argv`, because it is the root a replay must be handed back. |
| `--vocab` | `$DOC2MD_VOCAB` / `config/vocab.local.yaml` / `config/vocab.yaml` | Which term list values are checked against. Changes `vocab_version` in every written block **and** `prompt_sha`, so it re-asks the model. Recorded as a `vocabulary_selected` decision. How a path here reaches `argv` depends on the **value**, never on the flag name: an **absolute** path is redacted to `<path:sha16>` (an identity, so "was this the same file?" stays answerable) and `replay_run.py` then refuses to reconstruct that run rather than guessing a vocabulary file — supply it by hand; a **relative** path (`config/vocab.yaml`) is recorded verbatim and replays fine, because a relative path discloses nothing. The same is true of `--status-file`, `--worker-cmd`, `--expectations` and every other switch: there is no list of "path-ish" flag names any more, and there was never any safety in one. |
| `--namespace` | `""` | Prefix for the derived `id` (and its `uid` alias) — e.g. an org or corpus name, so ids from two corpora cannot collide. Changing it **rewrites every id in the corpus**, which orphans every `see_also` that used the old one. Recorded verbatim in `run.argv`, as `cli.namespace` in the run row, and as an `identity_namespace` decision in every report — so the corpus can say which namespace produced the ids in it. |
| `--source-base-url` | `$DOC2MD_SOURCE_BASE_URL` / `""` | Where the source documents are served. Sets `meta.source.url`, the page's link home. With a base it is absolute (`https://intranet/docs/specs/a%20b.docx`); **without one it is still written**, as a relative URI reference — the same information as `source.uri` but percent-encoded and forward-slashed, so it can be pasted into a link instead of only read. Trailing slashes on the base are ignored. Recorded as `cli.source_base_url` in the run row **with the source of the value**, which matters here more than anywhere: taken from the environment it appears in no `argv`, so a run reproduced without it would silently publish a corpus of different links. Also a `permalink_base` decision (`absolute` or `relative`) in every report. |
| `--only` / `--limit` | (none) / `0` | Narrow the pass. Skipped documents keep whatever they had. |
| `--force` | off | Re-ask the model for **every** field, not only the empty ones. Authored values are still never overwritten. |
| `--no-cache` | off | Ignore stored answers; **still writes the fresh ones**. |
| `--no-json-mode` | off | Stop sending `response_format: json_object`, for a server that rejects it. The reply is then brace-matched out of prose. |
| `--excerpt-chars` | `12000` | How much body the model sees. Truncation is announced in the prompt header. Raising it changes `prompt_sha` and re-asks the corpus. |
| `--keyword-limit` | `120` | How many tier-1 identifier candidates are offered as `keywords` seeds. |
| `--run-id` | UTC timestamp | Stamped into `meta.extraction.run_at`. A run that changes nothing does **not** re-stamp it — that is what keeps re-runs byte-identical. |
| `--fail-on-pending` | off | Exit `3` when any model-writable field is still empty. Off by default: a deterministic run leaves 13 fields pending *by design*, and that is a complete success, not a failure. Outranked by `4` (below), because a model that never answered is the *cause* of the pending fields and the more useful answer. |
| `--vlm-url` | `""` | **A model is used iff this is non-empty.** Which way it went is a `metadata_tier` decision (`model` or `deterministic`) in every report. Omit it and the run is deterministic-only — but not empty: `title`, `abstract` and `links` are still filled from the source's own title property, its lede paragraph and the URLs harvested into `structure.json`, and only the 13 fields that need judgement stay `pending`. `doc_meta.gate` is `disabled`. Naming an endpoint that never answers is **not** the same run: same artifacts, exit `4`. |
| `--vlm-model` | `$DOC2MD_VLM_MODEL` / `qwen2.5-vl-7b` | Model name requested and recorded in `doc_meta.model`. Part of `prompt_sha`, so swapping models re-asks. |

Exit codes:

| code | meaning |
|---|---|
| `0` | every tier this run was asked for ran. A no-model run leaves the whole model tier `pending` and still exits `0` — that is a complete deterministic run, and `report.json` agrees (`doc_meta.gate: "disabled"`). |
| `1` | a document could not be read, parsed or written. |
| `4` | **a model was asked for and never answered** — `--vlm-url` unreachable at the health check, or `ok: false` for document after document. The artifacts are the ones a deterministic run writes (`pending`, never guessed, always re-runnable); what differs is that nobody *chose* this, so it is not a success. A nightly backfill against a model that had been down for a week used to report success every night. |
| `3` | with `--fail-on-pending` only: work is outstanding. |

### `replay_run.py` — repeat a recorded run

Cashes in the `run{}` block: reconstructs the command line that produced a bundle,
names every way this machine differs from the recorded one, and can re-execute and
compare the result.

| Flag | Default | What it does |
|---|---|---|
| `--report` | (required) | The `report.json` whose run to replay. Follows its `config_ref` into `runs.jsonl` for the resolved settings, matching on `(run_id, entrypoint)` — pinning a build and an enrichment to the same `--run-id` is normal, so an id alone does not name a row. |
| `--stage` | `""` (the writer) | Which recorded run to replay: an `entrypoint` from the report's `runs[]`, e.g. `enrich_metadata`. A bundle is written by more than one stage, and which one you are repeating has to be sayable. An unknown name lists what the report has and exits `1`. |
| `--src` | `""` | The root the replay READS: source documents for the writers, the bundle root for `enrich_metadata`. **Always applied, whether or not the recorded run named one.** A run that took the default recorded no `--src` switch at all, so a replay that only filled placeholders printed a command with no `--src` — which reads from whatever `$DOC2MD_SRC` resolves to *now*, silently replaying a different corpus while the divergence list said "different directory". |
| `--out` | `""` | The root the replay WRITES. Same forcing rule. Stages that rewrite in place (`enrich_metadata`) take none, and the tool does not ask you for one. |
| `--execute` | off | Actually run it. Without this, nothing is changed — the command and the divergences are printed. |
| `--compare` | off | After executing, compare `markdown_sha256` with the original and say `REPRODUCED` or `DIFFERENT`. Needs `--execute` (without it the dry-run line says so instead of silently ignoring the flag), and needs an `--out` that is **not** the root being replayed — comparing a bundle with itself matches by construction and is refused with exit `1`. When either side published no `markdown_sha256` (a failed document publishes none) the answer is **UNVERIFIED** at exit `4`, never `REPRODUCED`: comparing `""` with `""` used to print `REPRODUCED` with an empty hash. |

**What counts as a divergence** — six classes, each a way the replay could produce
a different answer. Every one of them can also come back **UNVERIFIED**, meaning
the class was never compared; that is a third answer, not a quiet pass:

| Kind | Compared against | Why it matters |
|---|---|---|
| `code` | `pyproject.toml` + `.git` here | A different commit, or either checkout dirty. |
| `host` | this interpreter | `python` and `implementation`. |
| `tools` | a probe per external binary | `run.tools` re-asked here — today `soffice`. A tool with **no probe** on this interpreter is reported as UNVERIFIED, not passed over: "I could not check the toolchain" is not "the toolchain is the same". |
| `config` | the ingest loader, re-resolved | Any resolved setting whose value moved, or that no longer exists. **UNVERIFIED** when `runs.jsonl` has no row for this `(run_id, entrypoint)`, or the row carries no config block — the settings were then compared against nothing. |
| `env` | `os.environ` | The `DOC2MD_*` names that were merely **present** (`_env_present`), added or removed — a variable equal to the default moves no value and still changes what a person must set up. Plus any `cli.*` setting whose value came from a variable that now reads differently. **UNVERIFIED** on a missing run row, for the same reason. |
| `source` / `corpus` | the tree you passed as `--src` | This document's `source_sha256`, re-hashed; and `corpus_sha256` recomputed over exactly the documents the recorded run read (from its manifest rows, so `--only` and `--limit` runs stay checkable). `source_root_id` only ever answered "same *directory*?"; an edited file in the same directory used to replay clean and then produce different markdown. Both are **UNVERIFIED whenever no `--src` was given** — nothing was opened, so nothing was hashed. Also UNVERIFIED for a bundles read root, where `source_relpath` does not resolve (a correct, documented skip — but now a *reported* one rather than a silent one), for a report recording no `source_sha256`, and for an entrypoint the tool has no table entry for. The `corpus <sha>` header line is labelled `(recorded)` so it stops reading as evidence. |

Exit codes:

| code | meaning |
|---|---|
| `0` | everything applicable was compared, and none of it moved. |
| `3` | a divergence was **demonstrated** — or the replay produced a different `markdown_sha256`, or produced no bundle at all. |
| `4` | **nothing diverged, but at least one class could not be compared.** Explicitly *not* an all-clear. Also the answer when `--execute` ran the command and **the command itself exited non-zero**: the replay did not complete, so nothing downstream of it was compared, and `--compare` is refused rather than allowed to read a bundle an *earlier* replay left at `--out`. |
| `1` | a usage or I/O error, which now includes `--compare` pointed at the root being replayed. |

These four codes are this tool's vocabulary and nothing else's. The exit code of the
command a replay executes is **printed, never returned** — it used to be returned
verbatim, so a replayed `build_bundle` exiting `1` (a document failed) came back as
replay's "usage or I/O error", its `3` came back as "a divergence was demonstrated"
— a verdict about the recorded run that nothing had computed — and a `2` came back
as a code replay does not define at all.

`4` exists because the two-code scheme had no way to say "I did not check". Folding
"could not compare" into `3` would call an unchecked class a divergence; folding it
into `0` is the defect that made a `--report R` run with the losslessness threshold
halved print `no divergences … same resolved settings` and exit `0`. Note the
consequence: **`--report R` with no `--src` can never return `0`**, because the two
byte checks are exactly the ones that need a tree. Pass `--src` to get a `0`.
Anything treating `rc == 0` as "the bundle is fine" now gets a stronger guarantee;
anything treating `rc != 0` as failure sees a new value. Divergences are **reported,
never auto-corrected** — a silent "close enough" is how a replay comes to mean
nothing.

### `grade_output.py` — the rubric, as a command

Builds a small corpus (including a deliberately adversarial document), converts it,
enriches it with **no model**, and evaluates every row of the rubric in
[`../quality-plan.md`](../quality-plan.md). Prints a letter per output dimension.

| Flag | Default | What changes in the output |
|---|---|---|
| `--workdir` | a temp dir | Where the graded corpus is built. Supplying it also **keeps** it — the artifacts are the evidence behind each verdict, so a named workdir is never deleted. |
| `--keep` | off | Keep a temp workdir too, and print its path. Use when a row fails and you want to read the bundle that failed it. |
| `--no-suites` | off | Grade artifacts only. The rows backed by a pytest target report `skip` — and **a skip is never an A**, so this is for a fast inner loop, never for claiming a grade. |
| `--json` | off | Emit `{summary, rows}` instead of the table, for CI. **`--json` owns stdout completely** — every progress, suite and "artifacts kept in …" line goes to stderr, so `--keep --json` is safe and the log and the piped document are the same bytes. On the exit-`2` break path it emits a *different* document: `{"error": {"stage", "message", "detail"}}` and **only** that key — no `summary`, no `rows`. **A consumer must branch on `error` before reading `summary`**, and one that does not will raise rather than read a broken run as "no row failed". Exit `2` now covers any unexpected break (previously a traceback and exit `1`); a real failing grade is still exit `1`, deliberately outside that handler, because a verdict about real output must never be dressed up as a tooling failure. |

Exit codes: `0` every dimension is A, `1` not yet, `2` the graded run itself broke.
A row whose named test file does not exist yet reports `skip`, not `fail`: an
absent check is a missing measurement, and the rubric refuses to score it either way.

### `kb_lint.py` — the metadata linter

Read-only. Grades each document against the vocabulary, and the corpus against
the questions a single document structurally cannot answer.

| Flag | Default | What changes in the output |
|---|---|---|
| `--bundles` | `data/bundles` | Corpus to walk. |
| `--vocab` | resolved as above | The term list findings are graded against. |
| `--only` / `--limit` | (none) / `0` | Narrow the walk — and this **sets `partial`**, which deliberately skips the four corpus gates a truncated walk would invert (graph, skew, coverage, vocabulary usage). Each skipped gate gets its own `corpus-check-skipped` INFO naming the question it stopped answering, not one joined list. `partial` also refuses `--promote`. |
| `--json` | `""` | Also write the full machine-readable report to this path. Adds `corpus_metrics.synonyms.<field>.swept` / `.not_swept` — how many terms the similarity sweep covered, and how many it did not. |
| `--strict` | off | Warnings fail the run too. Default: only errors do. Note `synonym-sweep-scoped` and `registry-flood` are warnings, so a flooded registry fails a `--strict` run. |
| `--quiet` | off | Suppress per-document detail. Never hides a finding that decides the exit code. |
| `--suggest-aliases` | off | Print paste-ready `aliases:` entries for the spelling collisions found. Proposals only — nothing is written to `config/vocab.yaml`. |
| `--promote` | off | **Writes `config/vocab.yaml`** (or `--vocab`): every registry term on `>= promote_at` documents is spliced into that vocabulary's `values:` and the file's `version:` is bumped by one. Only the `values:` entry is rewritten, so comments, `rationale` and `rules` survive. Idempotent — a second run finds the terms already governed, writes nothing, and leaves the file byte-identical. Refused outright on an `--only`/`--limit` walk (a document-frequency count over a chosen subset promotes terms the corpus never voted for) and refused if the rewritten file does not load back through `load_vocab`. After it runs, the promoted terms stop being reported in `<field>_proposed` only once `enrich_metadata.py` re-runs over each document. |

---

## Specialist and one-time tools

| Script | Purpose | Notable flags |
|---|---|---|
| `office_convert.py` | The OOXML lane on its own, flat `.md` output | `--validate-only`, `--audit-parts` (separates deliberately-dropped parts from genuinely unread text), `--report` |
| `text_convert.py` | Passthrough/fence lane, flat `.md` | `--validate-only`, `--report` |
| `docling_convert.py` | The docling producer, flat `.md` | `--captions`, `--vlm-ocr`, `--assets-dir`, `--measure-only`, `--trust-existing-md`, `--auto-shards`, `--shard`, `--queue`, `--worker-id`, `--reclaim`, `--fallback-only`, `--no-coverage`, `--dry-run` |
| `heal_supervisor.py` | Elastic self-healing supervisor for the PDF lane | `--max-workers`, `--ramp-secs`, `--tick`, `--stall-secs`, `--busy-ticks`, `--drain-secs`, `--max-respawns`, `--worker-cmd`, `--status-file`, `--status-only` |
| `image_enrich.py` | Corpus-global figure captioning, flat layout | `--assets` |
| `validate_markdown.py` | Second-pass markdown tree validator | `--md-dir`, `--json`, `--strict` |
| `coverage_report.py` | Corpus-wide coverage summary: worst documents first, with the tokens and the figures actually lost | `--dir` (default `data/bundles`, which is **gitignored**), `--worst`, `--min-tokens` (below this a recall ratio is noise, and the count excluded is reported), `--fail-under` (makes it a CI step — see below), `--json` |

`--fail-under` has **two** failure modes, and a CI author needs both. It exits `1`
when the lossless fraction is below the threshold, **and** it exits `1` when no
coverage records were found at all: nothing measured is not a pass. That second one
bites on a fresh clone, where the default `--dir data/bundles` is gitignored and
empty — `coverage_report.py --fail-under 1.0` there now correctly exits `1` instead
of reporting a lossless corpus over zero documents. `--fail-under 0.0` still means
"never fail", including over an empty directory, and `--json` still prints its
(empty) array on stdout with the reason on stderr.
| `validate_figures.py` | Second-pass figure auditor | `--assets`, `--explain` |
| `prefetch_docling_models.py` | Pin and materialise the model weights | `--dest` |
| `setup_libreoffice.py` | Vendor a relocatable LibreOffice | `--rpms`, `--force`, `--uninstall` |
| `render_vocab_doc.py` | Generate [`vocabulary.md`](vocabulary.md) | `--vocab`, `--out`, `--check` |
| `replay_run.py` | Reconstruct (and optionally re-execute) the run behind a bundle | `--report`, `--src`, `--out`, `--execute`, `--compare` |
| `evals/run_eval.py` | The pipeline regression suite | `--expectations`, `--corpus`, `--text-out`, `--regen` (rebuild the corpus even if it exists), `--skip-pdf` (never run the PDF lane, even with `DOC2MD_PDF_PYTHON` set), `--no-lanes` (re-check existing bundles only) |
| `evals/gen_corpus.py` | Synthetic corpus generator | `--out`, `--handbuilt-only` (skip everything needing soffice or the PDF interpreter) |

---

## Environment variables

### Paths and routing

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_SRC` | `[paths].source_docs` | Source root; backs `--src` on nearly every script. |
| `DOC2MD_MARKDOWN_DIR` | `data/markdown` | Flat-markdown output dir. Relative paths resolve against the repo root. |
| `DOC2MD_ASSETS_DIR` | `data/assets` | Figure-crop store. |
| `DOC2MD_ACCEPT_FORMATS` | all supported | Whitelist of extensions; `"all"` means accept everything. Overridden by a non-empty `--accept`. |
| `DOC2MD_INGEST_BACKEND` | `native` | `native` or `docling`. **Raises on an unknown value** — the one non-forgiving setting. |
| `DOC2MD_LIBREOFFICE` | (search) | Explicit `soffice` path. Precedence: this > `vendor/libreoffice` > `PATH`. |
| `DOC2MD_PDF_PYTHON` | unset | The 3.12+docling interpreter. Unset means the PDF lane is **SKIPPED** in evals, never silently passed. |
| `DOC2MD_VOCAB` | `config/vocab.yaml` | Vocabulary file; `config/vocab.local.yaml` is tried in between. |
| `DOC2MD_SOURCE_BASE_URL` | `""` | Where the sources are served, for `meta.source.url`. `--source-base-url` overrides it. Empty leaves the url **relative**, not absent — see the flag. |

### Measurement thresholds (these move gates)

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_MIN_RECALL` | `0.80` | Token recall below this counts as lossy. Note the office lane's own gate is the stricter `== 1.0`. |
| `DOC2MD_MIN_TOKENS` | `50` | Below this many source tokens, recall is too noisy to judge. |
| `DOC2MD_CONTENT_MIN_RECALL` | `0.95` | Char-n-gram floor. A document is lossy only when **both** recalls fall short — the "explained gap" model. |
| `DOC2MD_FALLBACK_MIN_RECALL` | `0.50` | Below this, docling's markdown is replaced by the PDF text layer. |
| `DOC2MD_FALLBACK_MIN_TOKENS` | `100` | …only when the text layer carries at least this many tokens. |
| `DOC2MD_FALLBACK_CONTENT_MIN` | `0.85` | Char-content trigger for the same fallback; deliberately below `CONTENT_MIN_RECALL`. |
| `DOC2MD_HEADER_FOOTER_MIN_FRAC` | `0.50` | A line on at least this share of pages is running furniture. |

### Tokenizer

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_TOKENIZER_BACKEND` | `char` | `char` / `tiktoken` / `huggingface` / `callable`. A configured-but-uninstalled backend raises with an install hint rather than mislabelling counts. |
| `DOC2MD_TOKENIZER_MODEL` | `cl100k_base` | Encoding id or HF repo id. Both are overridable per run by `--tokenizer`. |

### Model / VLM

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_VLM_URL` | `http://127.0.0.1:21717/v1/chat/completions` | Used by `docling_convert.py` and `caption_bundles.py`. **Not by `enrich_metadata.py`** (see Traps). |
| `DOC2MD_VLM_MODEL` | `qwen2.5-vl-7b` | Model name for captions and VLM-OCR. |
| `DOC2MD_VLM_MAX_TOKENS` | `8192` | Bounds one VLM call. Floored at 1, so a `0` reverts rather than emptying every call. A silent truncation point on scanned pages, which have no ground truth. |
| `DOC2MD_ENABLE_CAPTIONS` | `false` | Turns captioning on (OR'd with `--captions`). Both bundle writers read it purely to stamp `captions.enabled`. |
| `DOC2MD_ENABLE_VLM_OCR` | `false` | VLM transcription instead of RapidOCR (OR'd with `--vlm-ocr`). |
| `DOC2MD_CAPTION_MAX_PIXELS` | `2458624` | Downscale target before a caption call. |
| `DOC2MD_CAPTION_MAX_BYTES` | `12000000` | Hard byte cap so a huge image cannot 413/OOM the server. |

### Figure-region detection (PDF)

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_IMAGE_REGION_MIN_PATHS` | `10` | A cluster of at least N drawing objects is a figure region. |
| `DOC2MD_IMAGE_REGION_PAD` | `0.01` | Fractional gap across which nearby objects merge into one cluster. |
| `DOC2MD_IMAGE_REGION_MAX_FRAC` | `0.85` | Objects covering more than this share of a page are frames, ignored. |

### Throughput and self-healing (PDF lane)

| Variable | Default | Effect |
|---|---|---|
| `DOC2MD_THREADS_PER_SHARD` | `4` | Accelerator threads per converter process. |
| `DOC2MD_MEM_PER_SHARD_GB` | `8` | RAM reserved per shard (peak ~6 GB on large PDFs). |
| `DOC2MD_MAX_SHARDS` | `0` (uncapped) | Hard cap; otherwise CPU/RAM bind. |
| `DOC2MD_RETRY_ATTEMPTS` | `2` | Same-lane attempts for a transient failure. |
| `DOC2MD_ESCALATION_ATTEMPTS` | `1` | Solo bigger-lane re-runs after an OOM/HANG. |
| `DOC2MD_BIG_DOC_THREADS` | `0` (all cores) | Threads for the escalation lane. |
| `DOC2MD_BIG_DOC_MEM_GB` | `24` | RAM reserved for the escalation lane. |
| `DOC2MD_LOAD_HIGH_FRAC` | `1.1` | Shrink workers above `frac × cpu` load (yield to other users). |
| `DOC2MD_LOAD_LOW_FRAC` | `0.5` | Grow workers below `frac × cpu` load. |
| `DOC2MD_RAMP_SECS` | `120` | Supervisor `--ramp-secs` default. |
| `DOC2MD_HEAL_TICK` | `15` | Supervisor `--tick` default. |
| `DOC2MD_STALL_SECS` | `600` | Supervisor `--stall-secs` default. |
| `DOC2MD_BUSY_TICKS` | `50` | Supervisor `--busy-ticks` default. |
| `DOC2MD_HEAL_WORKER_CMD` | (built-in) | Supervisor `--worker-cmd` default; a testing override. |
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | — | **Written, not read**: set from `--threads` before torch imports, via `setdefault`, so a pre-existing value wins. |

### Test-only

| Variable | Effect |
|---|---|
| `DOC2MD_EVAL_PDF` | With `DOC2MD_PDF_PYTHON`, gates the PDF eval e2e test. |

### Operator conventions (no code reads these)

| Variable | Why it matters |
|---|---|
| `DOCLING_ARTIFACTS_PATH` | Read by **docling itself**, produced by `scripts/prefetch_docling_models.py` and set in CI. When set, docling is local-or-fail — nothing downloads at convert time. |
| `TORCHDYNAMO_DISABLE=1` | Mandatory on the Rocky 8 host: gcc 8.5 cannot build TorchInductor kernels, so docling's picture classifier kills every digital PDF without it. **Nothing in the repo sets or checks this** — export it yourself. |
| `TRANSFORMERS_OFFLINE` | Documentation-only, same as above. |

---

## Traps and sharp edges

Recorded because they cost time, not because they are elegant.

1. **`DOC2MD_VLM_URL` does not configure enrichment.** `enrich_metadata.py` reads
   `cfg.vlm_model` and `cfg.vlm_max_tokens` from config but never `cfg.vlm_url`,
   so a model is used **only** when `--vlm-url` is passed explicitly. Captions and
   the PDF lane do honour the variable — the two entry points disagree.
2. **`--no-cache` means two different things.** `enrich_metadata.py` persists the
   fresh answers; `caption_bundles.py` throws them away at exit.
3. **Exit codes say what happened, not what is left to do.** `enrich_metadata.py`
   used to return `2` whenever any field was pending, so a healthy deterministic run
   could never exit `0` and broke every `set -e` chain — while `report.json` called
   the same state `doc_meta.gate: "disabled"`. Now: `0` success, `1` a document
   failed, `4` a model was asked for and never answered, and `3` only with
   `--fail-on-pending`. The `4` is the other half of the same rule: "what happened"
   has to include *the tier you asked for did not run*, which `0` was also saying.
4. **Five fields ship as empty registries.** `subtype`, `tags`, `keywords`,
   `topics` and `audience` start with `values: []`, so a fully classified document
   reports `pending: 5` until terms are promoted. `kb_lint --promote` is what
   writes them; without it the candidates are printed and nothing changes.
5. **Promoting a term re-asks the whole corpus.** `prompt_sha` includes
   `vocab.version`, so bumping it — which `--promote` does — invalidates every
   cached answer, including for documents whose field specs did not change.
6. **A flooded registry reads as healthy.** `singleton_rate` is computed over
   *promoted* terms, so a registry holding 24,000 un-promoted proposals reports
   `rate=0.00 ok`. The `registry-flood` warning is the check that sees it: it
   fires when a registry field holds more distinct terms than the corpus has
   documents and at least a quarter of them are used once.
7. **The similarity sweep is complete, but its SCOPE can narrow.** Blocking is
   sound at every corpus size — nothing is skipped for being big. Past
   `lint.similarity_max_terms` (default 4000) distinct terms in one registry
   field, the sweep is scoped to terms on `>= promote_at` documents and says so
   in a `synonym-sweep-scoped` warning carrying both counts. The exact-collision
   check still covers every term.
8. **`--force` blanks the caption attribution.** The captions themselves are
   carried forward by `image_id`, but `captions.model` and `captions.prompt_sha`
   are reset to `""`, leaving captions with no attributable model.
9. **A malformed env value is silently the default.** Only
   `DOC2MD_INGEST_BACKEND` complains.
10. **`--limit` and `--only` produce a partial corpus.** `kb_lint` says so
    (`partial`, with each skipped gate named individually); the bundle writers do
    not.
11. **An unreadable document is a finding about that document, not about the
    corpus.** It used to set `partial`, which switched off `see_also` resolution
    and the four whole-corpus gates for every *other* document — at 1000 bundles,
    a clean-looking all-clear over a corpus nobody checked. Now every gate still
    runs, the unreadable file gets its own `corpus-unreadable` error, and one
    `corpus-denominator` warning names each check whose denominator excludes it. A
    `see_also` that would have resolved into the unreadable document is still
    reported unresolved — with the count of unreadable documents named in the
    finding, so it cannot be mistaken for a confirmed dead link.
