---
title: Design documents
kind: readme
layer: backend
status: stable
owner: TBD
summary: Why the pipeline is shaped the way it is — the arguments behind the contracts, kept separate from the contracts themselves.
---

# `docs/design/`

**Why**, not **what**. The binding statement of what an artifact contains lives in
[`../reference/output-schema.md`](../reference/output-schema.md); these files carry
the reasoning that produced it, including the alternatives that were rejected and
the measurements that decided between them.

| File | The question it answers |
|---|---|
| [`output-contract.md`](output-contract.md) | What is a bundle, and what may a downstream holder rely on? |
| [`ooxml-lane.md`](ooxml-lane.md) | How is an Office document converted, and how is the result proven lossless? |
| [`document-metadata.md`](document-metadata.md) | What metadata exists, who is allowed to write each field, and why a model is the ceiling rather than the floor? |
| [`image-captioning.md`](image-captioning.md) | How is a figure gated, captioned and checked? |

## Rules

A design doc records a decision **with its evidence**. A claim with no measurement
behind it belongs in the roadmap as an intention, not here as a design.
