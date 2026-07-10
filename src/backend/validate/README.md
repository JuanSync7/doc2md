---
title: backend.validate
kind: package
layer: backend
status: stable
owner: TBD
public_api: src/backend/validate/__init__.py
tags: [validation, markdown, coverage-gate]
summary: The validator layer — markdown structural checks and the lossless conversion gate, kept separate from the run path.
---

# backend.validate

The **validator** layer, deliberately in its own directory so it stays cleanly
separable from the **run path** (`backend.ingest`: routing, converters, config).
The run path *produces* markdown; this package *checks and gates* it.

## Public API (`__init__.py`)

| Symbol | What it does |
|--------|--------------|
| `validate_markdown(md)` | Structural check of a markdown string — consistent pipe-table columns, closed fences/front-matter, no leaked OOXML tags, no control/replacement chars. Returns a sorted list of `MdIssue(line, code, severity, message)`. |
| `conversion_report(source_text, md[, content_min])` | The lossless **gate**: does every source token survive into the markdown? Multiset token recall (must be exactly `1.0` for the OOXML lane) + structural validation, with a unicode char-3gram fallback for CJK/Cyrillic text where the ASCII token metric is blind. Returns a dict with `valid`, `recall`, `content_recall`, missing-token detail, and the structural issues. |
| `MdIssue` | The named tuple a structural finding is reported as. |

## Layering

`validate` sits **above** `ingest`: it imports the measurement primitives
(`coverage`, `markdown_to_text`) from `backend.ingest`, and nothing in `ingest`
imports back. The dependency is one-way, so the two directories never entangle.

The runnable entry points live in `scripts/` (thin I/O): `validate_markdown.py`
sweeps a markdown tree, and `office_convert.py` gates every conversion it writes.

## Rules

Stays **Python 3.6-compatible** and **stdlib-only** (pure policy on strings, no
disk/network/models), like the rest of `backend/`.
