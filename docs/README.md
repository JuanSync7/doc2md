---
title: doc2md documentation
kind: readme
layer: cross-cutting
status: stable
owner: TBD
summary: Map of the documentation — what each file is for and which one answers your question.
---

# `docs/`

Organised by **purpose and audience**, never by source file — except
[`reference/`](reference/), which may thinly mirror the packages.

## Start here

| If you want to… | Read |
|---|---|
| know what this project is FOR and what "done" means | [`end-goal.md`](end-goal.md) — the charter, and the two jobs |
| use the pipeline end to end | [`guide.md`](guide.md) |
| know what is being worked on next | [`roadmap.md`](roadmap.md) |
| know how output quality is judged | [`quality-plan.md`](quality-plan.md) — the rubric, as predicates |

## Reference (look-up, not narrative)

| File | Answers |
|---|---|
| [`reference/output-schema.md`](reference/output-schema.md) | every key in every published artifact, and what it means |
| [`reference/configuration.md`](reference/configuration.md) | every switch and environment variable, and what it changes in the output |
| [`reference/vocabulary.md`](reference/vocabulary.md) | the controlled vocabulary — **generated**, never hand-edited |

## Design (why it is built this way)

| File | Covers |
|---|---|
| [`design/output-contract.md`](design/output-contract.md) | the bundle contract: four artifacts, cross-referenced by id and hash |
| [`design/ooxml-lane.md`](design/ooxml-lane.md) | the deterministic Office converter and its two hard gates |
| [`design/document-metadata.md`](design/document-metadata.md) | the metadata tiers and the knowledge payload |
| [`design/image-captioning.md`](design/image-captioning.md) | the figure gate and the shared caption tool |

## Rules

Frontmatter is required on every file here (`kind: doc|spec|design|adr`), and
`scripts/check_structure.py` fails the build when it is missing or invalid. ADRs are
immutable once accepted — supersede, never edit.
