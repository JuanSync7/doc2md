"""
title: Python settings — pluggable runtime wiring
layer: n/a
summary: Committed Python config for components a TOML value can't express — chiefly the pluggable tokenizer resolver.
"""
# config/settings.py — committed Python configuration for wiring pluggable components
# that a TOML scalar cannot express (a tokenizer is a CALLABLE, not a string).
#
# NO secrets here (same rule as default.example.toml). Machine/local overrides go in
# environment variables or a gitignored config/settings_local.py that re-assigns these.
#
# WHY here and not in src/backend: the deterministic pipeline (structure.json section
# sizes, report token counts, the RAG chunker windows) stays Python-3.6 + stdlib-only.
# It measures size in TOKENS only when handed a `str -> int` callable, else it falls back
# to a ~4-chars/token estimate. That callable is resolved HERE — so a heavy tokenizer lib
# (tiktoken/transformers) is imported in this config layer, NEVER in the backend, and the
# default "char" path needs no dependency at all.
import os

# --- Tokenizer wiring ---------------------------------------------------------------
# Configure via this dict OR the matching env vars (env WINS). Backends:
#   "char"        (default) no dependency; the pipeline's built-in ~4-chars/token estimate.
#   "tiktoken"    OpenAI BPE; `model` is an encoding id (e.g. "cl100k_base"). pip install tiktoken.
#   "huggingface" any HF tokenizer; `model` is a repo id / local path. pip install transformers.
#   "callable"    you set TOKENIZER["callable"] to your own `str -> int` function (batched
#                 remote clients, custom rules, a served tokenizer API — anything).
TOKENIZER = {
    "backend": os.environ.get("DOC2MD_TOKENIZER_BACKEND", "char"),
    "model": os.environ.get("DOC2MD_TOKENIZER_MODEL", "cl100k_base"),
    "callable": None,          # required when backend == "callable"
}


def get_token_counter(override=None):
    # type: (dict) -> tuple
    """Resolve the configured tokenizer to ``(token_count, token_model)``.

    ``token_count`` is a ``str -> int`` callable or ``None`` (use the built-in char
    estimate). ``token_model`` is the human label recorded in structure.json /
    report.json. Heavy tokenizer libraries are imported LAZILY here — never at module
    import, never in the backend — so the default "char" path stays pure-stdlib/3.6.

    A configured-but-unavailable backend fails LOUD (a clear ``RuntimeError`` with an
    install hint) rather than silently returning wrong counts under a truthful label.
    ``override`` (e.g. from a ``--tokenizer`` flag) shallow-merges over ``TOKENIZER``.
    """
    cfg = dict(TOKENIZER)
    if override:
        cfg.update({k: v for k, v in override.items() if v is not None})
    backend = (cfg.get("backend") or "char").strip().lower()

    if backend in ("", "char", "none"):
        return None, "char-estimate/4"

    if backend == "callable":
        fn = cfg.get("callable")
        if not callable(fn):
            raise RuntimeError("TOKENIZER backend 'callable' needs a str->int function set "
                               "in config.settings.TOKENIZER['callable'].")
        return fn, (cfg.get("model") or "callable")

    if backend == "tiktoken":
        try:
            import tiktoken
        except ImportError:
            raise RuntimeError("TOKENIZER backend 'tiktoken' needs the tiktoken package "
                               "(pip install tiktoken), or set backend='char'.")
        model = cfg.get("model") or "cl100k_base"
        enc = tiktoken.get_encoding(model)
        # disallowed_special=() so arbitrary document text never raises on special tokens.
        return (lambda s: len(enc.encode(s or "", disallowed_special=()))), model

    if backend in ("huggingface", "hf"):
        try:
            from transformers import AutoTokenizer
        except ImportError:
            raise RuntimeError("TOKENIZER backend 'huggingface' needs transformers "
                               "(pip install transformers), or set backend='char'.")
        model = cfg.get("model")
        if not model:
            raise RuntimeError("TOKENIZER backend 'huggingface' needs a model id in "
                               "TOKENIZER['model'] / $DOC2MD_TOKENIZER_MODEL.")
        tok = AutoTokenizer.from_pretrained(model)
        return (lambda s: len(tok.encode(s or "", add_special_tokens=False))), ("hf:%s" % model)

    raise RuntimeError("unknown TOKENIZER backend %r "
                       "(use char | tiktoken | huggingface | callable)." % backend)
