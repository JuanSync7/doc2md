---
title: data — agent rules
kind: rules
layer: n/a
status: stable
owner: TBD
summary: Local rules Claude must follow inside data/.
---

# Agent rules — `data/`

These rules are **local and authoritative** for this directory. They inherit
from the root `CLAUDE.md` and `CONVENTIONS.md`; where they conflict, the more
specific (this) file wins.

## Rules

- **Everything here is generated.** Rebuild via `scripts/` or `evals/`; never
  hand-edit an artifact (markdown, bundle, coverage record, corpus file).
- **Never commit contents.** `data/*` is git-ignored except these labeling
  docs; do not force-add generated files or source documents.
- **Do not treat outputs as fixtures.** Tests and evals must regenerate what
  they need (tmp dirs or the eval generator), not depend on files lying here.
