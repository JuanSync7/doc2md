---
title: Agent rules — config
kind: rules
layer: n/a
status: draft
owner: TBD
summary: Local rules for config — committed defaults only, no secrets; settings.py resolves pluggable callables lazily so the backend stays dependency-free.
---
# Agent rules — `config/`

Inherits from the root rules; the more specific wins.

## Rules

- **Committed defaults only. No secrets** — no API keys, no machine-specific absolute
  paths. Local/real values live in environment variables or a gitignored
  `config/settings_local.py`.
- **`settings.py` resolves pluggable callables LAZILY.** A heavy dependency (a
  tokenizer lib) may be imported only inside its resolver function, never at module
  import — so importing `config.settings` on the bare 3.6 host with the default `char`
  backend pulls in nothing. Keep it that way.
- **Fail loud on misconfiguration.** A configured-but-unavailable backend must raise a
  clear error with an install hint, never silently fall back to a different tokenizer
  under a truthful label (that would report wrong counts as if correct).
- The backend (`src/backend/**`) must never import `config` — configuration flows IN
  as plain values/callables (dependency direction stays one-way).
