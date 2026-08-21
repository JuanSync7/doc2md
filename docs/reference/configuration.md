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

| Flag | Default | What changes in the output |
|---|---|---|
| `--bundles` | `data/bundles` | Bundle root to walk. |
| `--vocab` | `$DOC2MD_VOCAB` / `config/vocab.local.yaml` / `config/vocab.yaml` | Which term list values are checked against. Changes `vocab_version` in every written block **and** `prompt_sha`, so it re-asks the model. |
| `--namespace` | `""` | Prefix for the derived `uid` — e.g. an org or corpus name, so ids from two corpora cannot collide. |
| `--only` / `--limit` | (none) / `0` | Narrow the pass. Skipped documents keep whatever they had. |
| `--force` | off | Re-ask the model for **every** field, not only the empty ones. Authored values are still never overwritten. |
| `--no-cache` | off | Ignore stored answers; **still writes the fresh ones**. |
| `--no-json-mode` | off | Stop sending `response_format: json_object`, for a server that rejects it. The reply is then brace-matched out of prose. |
| `--excerpt-chars` | `12000` | How much body the model sees. Truncation is announced in the prompt header. Raising it changes `prompt_sha` and re-asks the corpus. |
| `--keyword-limit` | `120` | How many tier-1 identifier candidates are offered as `keywords` seeds. |
| `--run-id` | UTC timestamp | Stamped into `meta.extraction.run_at`. A run that changes nothing does **not** re-stamp it — that is what keeps re-runs byte-identical. |
| `--fail-on-pending` | off | Exit `3` when any model-writable field is still empty. Off by default: a deterministic run leaves 20 fields pending *by design*, and that is a complete success, not a failure. |
| `--vlm-url` | `""` | **A model is used iff this is non-empty.** Omit it and the run is deterministic-only: tiers 0 and 1 fill, all 20 model-writable fields stay `pending`, and `doc_meta.gate` is `disabled`. |
| `--vlm-model` | `$DOC2MD_VLM_MODEL` / `qwen2.5-vl-7b` | Model name requested and recorded in `doc_meta.model`. Part of `prompt_sha`, so swapping models re-asks. |

### `replay_run.py` — repeat a recorded run

Cashes in the `run{}` block: reconstructs the command line that produced a bundle,
names every way this machine differs from the recorded one, and can re-execute and
compare the result.

| Flag | Default | What it does |
|---|---|---|
| `--report` | (required) | The `report.json` whose run to replay. Follows its `config_ref` into `runs.jsonl` for the resolved settings. |
| `--src` / `--out` | `""` | You supply these: the recorded `argv` carries `<src>` / `<out>` placeholders, because publishing an absolute host path is forbidden. `source_root_id` confirms you pointed at the same tree. |
| `--execute` | off | Actually run it. Without this, nothing is changed — the command and the divergences are printed. |
| `--compare` | off | After executing, compare `markdown_sha256` with the original and say `REPRODUCED` or `DIFFERENT`. |

Exit codes: `0` all clear, `3` divergences found (or the replay produced a
different hash), `1` a usage or I/O error. Divergences are **reported, never
auto-corrected** — a silent "close enough" is how a replay comes to mean nothing.

### `kb_lint.py` — the metadata linter

Read-only. Grades each document against the vocabulary, and the corpus against
the questions a single document structurally cannot answer.

| Flag | Default | What changes in the output |
|---|---|---|
| `--bundles` | `data/bundles` | Corpus to walk. |
| `--vocab` | resolved as above | The term list findings are graded against. |
| `--only` / `--limit` | (none) / `0` | Narrow the walk — and this **sets `partial`**, which deliberately skips the four corpus gates a truncated walk would invert (graph, skew, coverage, vocabulary usage). They are named in the output rather than silently dropped. |
| `--json` | `""` | Also write the full machine-readable report to this path. |
| `--strict` | off | Warnings fail the run too. Default: only errors do. |
| `--quiet` | off | Suppress per-document detail. Never hides a finding that decides the exit code. |
| `--suggest-aliases` | off | Print paste-ready `aliases:` entries for the spelling collisions found. Proposals only — nothing is written to `config/vocab.yaml`. |

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
   failed, and `3` only with `--fail-on-pending`.
4. **Five fields can never reach `filled` today.** `subtype`, `tags`, `keywords`,
   `topics` and `audience` are registries that ship empty, so even a fully
   classified document reports `pending: 5` until terms are promoted — and
   promotion is manual (`kb_lint` prints candidates; nothing writes the file).
5. **Promoting a term re-asks the whole corpus.** `prompt_sha` includes
   `vocab.version`, so bumping it invalidates every cached answer.
6. **`--force` blanks the caption attribution.** The captions themselves are
   carried forward by `image_id`, but `captions.model` and `captions.prompt_sha`
   are reset to `""`, leaving captions with no attributable model.
7. **A malformed env value is silently the default.** Only
   `DOC2MD_INGEST_BACKEND` complains.
8. **`--limit` and `--only` produce a partial corpus.** `kb_lint` says so
   (`partial`, with the skipped gates named); the bundle writers do not.
