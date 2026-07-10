---
title: Conventions
kind: doc
layer: n/a
status: template
owner: TBD
tags: [conventions, frontmatter, taxonomy]
summary: Single source of truth for labeling (frontmatter) and the directory taxonomy.
---
# Conventions

This file defines **how files are labeled** and **what each directory
means**. It is the contract that `README.md`/`CLAUDE.md` files and coding
agents follow. If you change the scheme, change it here first.

## 1. Frontmatter (labeling for sort/route)

Every Markdown doc (`README.md`, `CLAUDE.md`, design docs, specs) starts
with a YAML frontmatter block. This is what lets tools, agents, and
humans sort and route files without reading the body.

```yaml
---
title: Backend                       # human title
kind: package                        # see KINDS below
layer: backend                       # see LAYERS below
status: stable                       # draft|stable|deprecated|template (ADRs: proposed|accepted|superseded)
owner: team-or-person
public_api: src/backend/__init__.py  # the boundary file, or "none"
tags: [server, domain]
summary: One-line purpose, <=120 chars.
---
```

**KINDS:** `readme` `rules` `package` `module` `tests` `test-doc` `doc`
`spec` `design` `adr` `config` `script` `agent` `mcp` `api` `wiki`
`demo` `model` `eval` `container` `ops`.

**LAYERS:** `frontend` `backend` `shared` `app` `cross-cutting` `n/a`.

### Code files
Source files can't carry YAML, so the **module docstring** is the
label, and `__all__` is the machine-checkable public API:

```python
"""
title: Example feature
layer: backend
public_api: yes        # this module is re-exported from the package __init__
summary: Does the one thing this feature does.
"""
__all__ = ["do_thing", "Thing"]
```

## 2. Directory taxonomy

| Dir | kind | What goes in | What does NOT |
|-----|------|--------------|---------------|
| `src/frontend` | package | UI, client, view logic | server/domain logic |
| `src/backend`  | package | services, domain, persistence | UI rendering |
| `src/shared`   | package | contracts/types used by BOTH FE+BE | anything FE- or BE-only |
| `src/app`      | package | entrypoints, DI/wiring, CLI/`__main__` | business logic |
| `tests/unit`   | tests | fast, isolated; **mirrors `src/`** | network/disk/process |
| `tests/integration` | tests | 2+ real components, by scenario | full-stack journeys |
| `tests/e2e`    | tests | full system, user journeys | unit-level asserts |
| `tests/smoke`  | tests | "is it alive" post-deploy checks | exhaustive cases |
| `test-docs`    | test-doc | test plans, coverage register, strategy | the tests themselves |
| `docs`         | doc | architecture, specs, design, guides, ADRs | API code |
| `agents`       | agent | autonomous/LLM agent brains | the MCP/API transport |
| `mcp`          | mcp | MCP servers exposing tools | business logic (call into `src/`) |
| `api`          | api | HTTP handlers + OpenAPI specs | business logic (call into `src/`) |
| `wiki`         | wiki | browsable index/knowledge site (optional) | source of truth |
| `scripts`      | script | dev/CI automation, one-shots | importable library code |
| `config`       | config | committed defaults + `*.example.*` | secrets |
| `demo`         | demo | runnable examples | tests |
| `containers`   | container | Dockerfiles, compose, build context | app code |
| `evals`        | eval | eval datasets + harness for agents/models | unit tests |
| `ops`          | ops | deploy, IaC, runbooks, dashboards | app code |
| `models`       | model | model backends the app/agents run on (adapters + registry, e.g. Claude Code headless) | domain logic (that's `src/`) |

## 3. The `__init__.py` boundary rule (the important one)

A package's `__init__.py` **is its public API**. Callers import from the
package, never from a submodule:

```python
from myproj.backend import do_thing        # ✅ through the boundary
from myproj.backend._impl import do_thing  # ❌ reaching inside
```

- `__init__.py` lists `__all__` and re-exports only the public symbols.
- Implementation modules are prefixed `_` (e.g. `_impl.py`, `_service.py`).
- Cross-package contracts are **ABCs / `typing.Protocol`** in
  `contracts.py`, re-exported from `__init__.py`. Depend on the
  contract, not the concrete class.
- **Polyglot analogs:** TS → a single `index.ts` barrel; Go → package
  exports (capitalized identifiers); Rust → `pub` in `mod.rs`; Java →
  package-private by default + a public façade.

## 4. `shared/` vs `util/`

- **`shared/`** — domain-meaningful things shared across a section
  (types, models, contracts, constants).
- **`util/`** — generic, domain-agnostic helpers (string, fs, time,
  retry). If a helper "knows" about your domain, it belongs in `shared/`.
- Don't pre-create either in a tiny package. One of each per section,
  max. `util/` is not a junk drawer — review it like any other module.

## 5. Hidden (dot) files & directories

Split them into **committed config** (part of the project) vs
**generated/local** (gitignored). Scaffold the first kind, never the
second.

| Dot path | Commit? | Purpose |
|----------|---------|---------|
| `.gitignore` `.editorconfig` `.gitattributes` | ✅ | repo hygiene |
| `.github/workflows/` or `.gitlab-ci.yml` | ✅ | CI definitions |
| `.claude/` (`settings.json`, `skills/`, `commands/`) | ✅ | agent config shared with the team |
| `.env.example` | ✅ | documents required env vars (no real values) |
| `.vscode/` `.idea/` | ⚠️ optional | editor config — commit only a minimal shared subset |
| `.env` `.env.local` | ❌ | real secrets — gitignored |
| `.venv/` `.pytest_cache/` `.mypy_cache/` `.ruff_cache/` | ❌ | tool caches/venvs — generated, gitignored |

Rule: a dot-dir that holds **decisions** (CI, agent rules, editor norms)
is committed and may carry a `README.md`; a dot-dir that holds
**generated state or secrets** is gitignored and never scaffolded.

## 6. Enforcement (this is checked, not just documented)

`scripts/check_structure.py` (run via `make check`, in CI, and as a
pre-commit hook) fails the build if the conventions above drift:

| Rule | What it verifies |
|------|------------------|
| Frontmatter | every `README.md`/`CLAUDE.md` (+ `docs/**`, `test-docs/**` md) has the required keys with valid `kind`/`layer`/`status` |
| Documented dirs (§2) | every taxonomy directory that exists has both `README.md` and `CLAUDE.md` |
| Package boundary (§3) | every `src/` dir with `.py` has an `__init__.py` that defines `__all__` |
| `__init__` is the API (§3) | no absolute import of another package's `_private` module |

Missing `owner` is a warning, not a failure. If you change the scheme
(KINDS / LAYERS / STATUSES), update **both** this file and the constants
at the top of `scripts/check_structure.py`.
