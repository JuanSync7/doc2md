---
title: Contributing to doc2md
kind: doc
layer: cross-cutting
status: stable
owner: TBD
summary: How to run the gates, what the merge bar is, and the handful of rules in this repo that are not obvious from the code.
---

# Contributing

## The merge gate, in one command

```sh
make verify
```

Green means safe to push, and it means the same thing CI means — the targets and the
CI jobs run the same commands. It covers: the structure checker, the test suite, the
CI dependency ring, corpus determinism across Python 3.6 and 3.12, the office eval,
idempotency, the quality rubric, and the knowledge-layer lint.

It does **not** cover the PDF lane, which needs the 3.12 venv and the pinned docling
weights:

```sh
make eval-pdf
```

## Running tests

```sh
make test          # the suite; submits to the Slurm farm when srun exists
make test-ciring   # the suite as CI sees it — no PyYAML, no Pillow
make test-pillow   # the branches that exist only WITH Pillow
make check         # CONVENTIONS.md's structure and labelling rules
make where         # which runner and interpreters you are actually using
```

**Tests go to the compute farm by default.** A full run pins CPUs on a shared host,
so `make test` uses `srun` when it is available. `FARM=0 make test` forces local.
If you invoke `srun` yourself, pass `--mem` — measured, a full suite run without it
is OOM-killed by the cgroup, and the error looks nothing like a test failure.

**Three dependency rings, and the suite must pass in all of them.** PyYAML and
Pillow are optional, and both directions of drift have bitten this repo: a test that
passed only where Pillow was *absent* stayed green on the dev host and in CI, and
turned 34 tests red the first time the suite ran on a farm node that had it. If your
change touches a branch that only exists under an optional dependency, test **both**
branches deliberately — force the absent one, skip the present one with a reason.

## The bar

Everything below is a rule this repo has learned the hard way. None of them are
style preferences.

### Measured, not assumed

A claim about the output needs a number, and the number needs a command a reviewer
can re-run. "The ligature fold works" is worth nothing; "recall 0.7976 → 0.9041 on
`pdf/kestrel-ligature.pdf`, measured with the fold removed" is the same sentence
with evidence.

### A gate nobody has watched fail is not a gate

When you add or change a gate, **prove the red direction**: break the code
deliberately, watch the test fail, put it back. Say in the test's docstring what you
injected. Several gates here once passed over damage because nobody had checked they
could fail.

### A skip reads exactly like a pass

If a test, a lane or a check can be silently absent, something must assert it ran.
The workflow does this four times over — the CommonMark differential, the
red-direction suite, the Pillow-only branches, and `--require` for corpus areas —
because each one was, at some point, green over work it never did.

### Omission is not zero

A fact the pipeline cannot establish is recorded as *unmeasured* or *pending*, never
as `0` or `pass`. A stated zero is a claim about the document; an absent field is a
claim about nobody having looked, and the two must never read the same. This is why
`n_source_tokens`, `images.source_images` and `figure_text` exist.

### Gates are ratchets

They may be widened and hardened, never weakened. If a change makes a gate report
*less*, that is a finding to discuss, not a fix to land. Changes to shared metric
code (`tokenize`, `coverage`) require re-running the full office corpus first.

### The two jobs have two standards

`docs/end-goal.md` is the charter. Job 1 (the document) may lose nothing. Job 2 (the
knowledge) may claim nothing that cannot be followed. They live in different packages
and `tests/unit/backend/test_job_boundary.py` keeps them there — a gate that imported
the knowledge layer would make a losslessness verdict depend on a per-deployment
vocabulary.

## Things the tooling will refuse

These fail the build, so it is cheaper to know them first:

| If you… | …you must also |
|---|---|
| add a CLI flag | document what it changes in the **output** in `docs/reference/configuration.md` |
| add a published artifact key | add it to `docs/reference/output-schema.md` |
| emit a new warning code | add it to the contract's warning vocabulary |
| emit a new `decision` code | add it to `DECISION_CODES` **and** the schema doc |
| add `src/<pkg>/<mod>.py` | add `tests/unit/<pkg>/test_<mod>.py` |
| add a directory under `src`, `tests`, `docs`, `scripts`, `config`, `evals` | give it a `README.md` **and** a `CLAUDE.md` with valid frontmatter |
| change `config/vocab.yaml` | regenerate `docs/reference/vocabulary.md` (it is generated — never hand-edit) |

`tests/integration/test_docs_parity.py` and `scripts/check_structure.py` enforce
these. They are not bureaucracy: each one exists because a documented contract and
the code drifted apart, and a reader trusted the document.

## Compatibility

`src/backend/ingest` and `src/backend/validate` are **Python 3.6-compatible and
stdlib-only** — the deterministic Office lane runs on a bare 3.6 host. That means no
f-strings in those packages' public path, no `X | None`, no builtin generics, no
dataclasses, no walrus; use type **comments**. The PDF lane
(`scripts/docling_convert.py`) may use 3.9+ and PyTorch, and those dependencies must
not leak into `ingest`.

## Adding a corpus fixture

The eval corpus is synthetic, byte-deterministic and neutral (fictional
Nimbus/Kestrel content). Never commit a real source document.

1. Add a builder to `evals/gen_corpus.py` and register it in `HANDBUILT` (and
   `DERIVED_OFFICE` if a PDF should be derived from it).
2. `make corpus` to regenerate.
3. Run the lane and **read what it actually produced** before writing expectations.
4. Add expectations to `evals/expectations.json` that encode the **measured truth**,
   not the desired truth. A shortfall carries a `_note` saying what is wrong and what
   would fix it — or `"xfail": true` with a `_note` when today's behaviour is worse
   than what is pinned. A papered-over pass is the one thing the eval exists to
   refuse.

## Commits

Explain **why**, with the measurement. The commit log here is the project's
reasoning, and several fixes were only possible because an old message recorded what
had already been tried.
