---
title: Config
kind: config
layer: n/a
status: draft
owner: TBD
public_api: config/settings.py
tags: [config, tokenizer, vocabulary]
summary: Committed configuration — the controlled vocabulary and Python settings for pluggable components (the tokenizer). No secrets.
---

# Config

Committed configuration defaults. **No secrets** (any model/API key is read from the
environment, never committed). Machine/local overrides go in environment variables or
a gitignored `config/settings_local.py`.

## `settings.py`

Importable Python config for wiring components a static value can't express — chiefly
the **tokenizer**, which is a `str -> int` *callable*. The document pipeline
(`structure.json` section sizes, `report.json` token counts, the chunker windows)
measures size in real tokens when a tokenizer is configured, else a `~4-chars/token`
estimate. Resolving the callable here keeps the Python-3.6 / stdlib-only backend free
of any heavy tokenizer dependency — it only ever receives the callable.

```python
from config.settings import get_token_counter
token_count, token_model = get_token_counter()   # honors config + env; default: char estimate
```

Backends (set `TOKENIZER["backend"]` or `$DOC2MD_TOKENIZER_BACKEND`): `char` (default,
no dep) · `tiktoken` · `huggingface` · `callable` (plug your own function). A
configured-but-unavailable backend fails loud rather than silently miscounting.
`scripts/build_bundle.py --tokenizer <backend[:model]>` overrides per run.

## `vocab.yaml`

The **controlled vocabularies** for document metadata: which values each governed
field may take, how each is governed (`closed` / `registry` / `ref`), the aliases
that normalise variant spellings, the SKOS `broader` hierarchy, and the `maps_to`
standard term for every field and value that has one.

Editing this file is a governance act, not a config tweak — a closed list is a
schema change, and a registry admits terms by promotion rather than by hand. Bump
`version:` when values move; the metadata cache is keyed on it, so a bump re-asks
exactly the affected documents.

```
DOC2MD_VOCAB=/path/to/vocab.yaml   # or drop a config/vocab.local.yaml beside it
python3 scripts/kb_lint.py --bundles data/bundles --strict
python3 scripts/kb_lint.py --bundles data/bundles --suggest-aliases
```

The `lint:` block holds the thresholds every gate reads, so tuning a gate is an edit
here rather than a code change:

| key | gate |
|---|---|
| `facet_warn` · `facet_fail` · `facet_min_uses` | cardinality — is a field a facet, and is the sample big enough to say? |
| `promote_at` · `singleton_rate_max` | registry health — when a proposal becomes a term, and when the registry has stopped grouping |
| `synonym_similarity` | how alike two spellings must look before the corpus gate asks about them |
| `corpus_min_docs` | the floor below which corpus rates are reported but not graded |
| `coverage_thin` | the share below which a model-written field is a question about the extractor |
| `similar_ok` | recorded answers — `field:a\|b` pairs that are deliberately distinct |

`similar_ok` is the escape hatch for both corpus spelling gates. A similarity finding
is a *question*, and a collision can occasionally be two genuinely different things
(`/etc/a-b` and `/etc/a_b` are two files); without somewhere to record the answer,
the first would be asked forever and the second would fail every build with nothing a
person could do about it. The finding prints the exact entry to paste.

It is parsed by `backend.ingest.parse_block`, a strict stdlib YAML **subset** (no
anchors, no flow mappings, no multi-document streams) — the CI 3.6 ring has no
PyYAML, so keep hand edits inside that subset. See
[`docs/design/document-metadata.md`](../docs/design/document-metadata.md).
