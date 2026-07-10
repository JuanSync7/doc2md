"""
title: config package
layer: n/a
summary: Importable configuration. TOML defaults load via backend.ingest._config; settings.py holds Python config a TOML scalar can't express (pluggable callables).
"""
# Making config/ an importable package lets the pipeline resolve Python-valued config
# (e.g. the tokenizer callable in settings.py) without a heavy dep leaking into the
# 3.6/stdlib backend. Committed defaults + env overrides only — never secrets.
