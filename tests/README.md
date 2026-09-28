---
title: Tests
kind: readme
layer: backend
status: stable
owner: TBD
summary: What each tier proves, how to run them, and the two environment rings that exist because an optional dependency once decided whether the suite passed.
---

# `tests/`

## Running them

```sh
make test          # the whole suite (submits to the Slurm farm when srun exists)
make verify        # the DONE-GATE: suite, CI ring, determinism, eval, idempotency,
                   # rubric, kb-lint — green here means safe to push
```

`make test` submits to the compute farm when `srun` is available, because a full run
pins CPUs on a shared host. `FARM=0` forces local.

## The tiers

| Tier | Proves | May it touch disk/network/process? |
|---|---|---|
| [`unit/`](unit/) | one module's behaviour, through its package's public API | **no** |
| [`integration/`](integration/) | two or more parts agreeing — a script against the library, a doc against the code | yes |
| [`e2e/`](e2e/) | the whole pipeline over a real corpus | yes |

Unit tests mirror source files (`src/<pkg>/<mod>.py` → `tests/unit/<pkg>/test_<mod>.py`).
Integration and e2e are named by **scenario**, never mirrored.

## The two environment rings

The suite must pass whether or not the optional dependencies are installed, and both
directions have bitten this repo:

| Ring | Command | Why |
|---|---|---|
| CI ring | `make test-ciring` | blocks PyYAML and Pillow, reproducing what CI installs |
| Pillow ring | `make test-pillow` | runs the branches that exist *only* when Pillow is present |

Four test files once built PNG bytes that were not decodable. They passed on the dev
host and in CI (no Pillow) and failed on a farm node with Pillow 5.1.1 — 34 tests, all
green everywhere anyone had looked. See [`pngsupport.py`](pngsupport.py) and
[`ciring/`](ciring/).

## Rules

A test whose verdict depends on the machine it runs on is not testing the code. If a
branch only exists under some dependency, test **both** branches deliberately — force
the absent one, and skip the present one with a reason.
