---
title: Config
kind: config
layer: n/a
status: draft
owner: TBD
public_api: config/settings.py
tags: [config, tokenizer]
summary: Committed configuration — Python settings for pluggable components (the tokenizer) that a scalar can't express. No secrets.
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
