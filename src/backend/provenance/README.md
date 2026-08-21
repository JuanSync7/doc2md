---
title: backend.provenance
kind: package
layer: backend
status: draft
owner: TBD
public_api: src/backend/provenance/__init__.py
tags: [provenance, reproducibility, run, decisions, doc2md]
summary: What a run was — code identity, resolved configuration with the source of each value, and every branch the pipeline chose.
---

# backend.provenance

A pipeline whose pitch is **measured** losslessness has to be able to say which
instrument produced any given number, and to let somebody run it again and get the
same one. This package owns those facts.

It is separate from its neighbours on purpose:

| Package | Answers |
|---|---|
| `validate` | Was the **conversion** faithful? |
| `bundle` | What shape does the **artifact** take? (pure composition, no disk) |
| **`provenance`** | What was the **run**? Which code, which settings, which choices? |

## The two halves

**`run{}` — the inputs.** What was asked for, by which code, on what.

```jsonc
"run": {
  "entrypoint": "build_bundle",
  "run_id": "20260821T125543Z",
  "argv": ["--src", "<src>", "--out", "<out>", "--tokenizer", "tiktoken:cl100k_base"],
  "source_root_id": "9f3a1c…",              // identifies the tree, not its location
  "code":   {"name": "doc2md", "version": "0.1.0", "commit": "81e94a7…", "dirty": false},
  "host":   {"python": "3.6.8", "platform": "Linux-4.18.0-…"},
  "config": {"min_recall": {"value": 0.8, "from": "default"},
             "vlm_url":    {"value": "http://…", "from": "env"}},
  "tools":  {"soffice": "7.6.4.1"}
}
```

**`decisions[]` — the choices.** Which lane, which fallback, cache hit or miss.

```jsonc
{"code": "ocr_routed", "stage": "build_pdf_bundle", "chose": "ocr",
 "reason": "no usable text layer", "evidence": {"text_chars": 12, "pages": 9}}
```

`stage` is the `entrypoint` of the run that took the branch, applied in one place
per writer by `stamp_stage`. A bundle is written by more than one stage — the
converter, then `enrich_metadata` re-deriving `meta.id` and the permalink — and
they share one `decisions[]`. Without the attribution a record can say neither
which run chose it nor which records a re-running stage may replace, so a re-run
either grows the list forever or deletes another stage's choices.

`warnings[]` already carries **problems**. A choice is not a problem, and mixing
the two means neither can be aggregated — "how many documents took the text-layer
fallback" is unanswerable when the answer is buried in a prose `detail` string.
`DECISION_CODES` is a closed list for the same reason: an unnamed decision is one
nobody can count, and a typo would silently create a category of one.

## Two design decisions worth knowing

**Configuration provenance is derived by difference, not by a table.**
`config_provenance` resolves the configuration three times — normally, without the
environment, and without the environment or the file — and reads the source off
which resolution changed the value. The real loader is its own oracle, so there is
no second copy of the precedence rules to drift from the first. `_env_present`
additionally records which `DOC2MD_*` names were merely *set*, because a variable
whose value happens to equal the default is invisible to the diff and still
matters to somebody re-establishing the run.

**An unknown is omitted, never defaulted.** `code_identity(dirty=None)` leaves the
key out rather than writing `false`. "Clean" is precisely the claim a reader would
act on, so an unverified one is worse than none. Same reasoning as the report
omitting `savings` for a lane whose source side was never measured.

## Paths never appear

The root `CLAUDE.md` forbids absolute host paths in any artifact, and a bundle is
published output. So `redact_argv` keeps every switch and replaces path *values*
with `<src>` / `<out>` placeholders, and `path_id` hashes a location into something
that answers "was this the same tree?" without saying where it is.

**The test is on the VALUE, never on the flag name.** Matching flag names was
unsound twice over. argparse accepts unambiguous *prefixes*, so a set keyed on
`--src` never fired for `--sr /tmp/x`; and switches nobody had listed take paths
routinely — `--only /abs/spec.docx`, `--tokenizer "char:/home/me/models/tok"`.
Anything shaped like an absolute path is now redacted to `<path:sha16>` whatever
carried it, including inside a compound value and inside a whole command line in
one argv element. A URL is not a host path and survives verbatim: it is a switch
that decided the output. `safe_value` applies the same shape test to any value on
its way into an artifact — config entries, `chose`, `evidence` — so no two callers
can end up disagreeing about what a path looks like. `corpus_id`
does the stronger version from the manifest rows — `sha256` over sorted
`doc_id:source_sha256` pairs, so two runs over the same documents agree and one
changed byte does not.

## Public API

- `code_identity(root, name="doc2md", dirty=None)`, `git_commit(root)`,
  `package_version(root)` — read from the checkout; plain file reads, so they work
  with no `git` binary, in a linked worktree, and on a detached HEAD.
- `host_identity()` — interpreter and platform. Never `platform.node()`: a hostname
  is neither needed to repeat a run nor safe beside de-identified source paths.
- `run_block(...)`, `decision(code, chose, reason, evidence=None)`,
  `stamp_stage(decisions, stage)`, `DECISION_CODES`.
- `config_provenance(now, without_env, without_file, env_names=())`.
- `redact_argv(argv, paths=None)`, `safe_value(value)`, `path_id(path)`,
  `corpus_id(rows)`.

See [`docs/reference/output-schema.md`](../../../docs/reference/output-schema.md)
for the keys as shipped, and [`docs/quality-plan.md`](../../../docs/quality-plan.md)
phase P2 for what this is for.
