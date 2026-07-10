"""
title: Ingest config resolution (private)
layer: backend
public_api: no
summary: Resolve the [ingest] backend/markdown_dir from env > config file > defaults (3.6, no tomllib).
"""
# 3.6-compatible: Python 3.6 has no tomllib, so we parse the tiny flat [ingest]
# table ourselves. Only flat `key = "value"` / `key = value` lines are supported,
# which is all this section ever holds.
import os
from collections import namedtuple

from ._route import normalize_accept as _normalize_accept, SUPPORTED_EXTS as _SUPPORTED_EXTS

__all__ = ["IngestConfig", "load_ingest_config", "VALID_BACKENDS", "recommend_shards",
           "load_source_root"]

VALID_BACKENDS = ("native", "docling")
DEFAULT_BACKEND = "native"
DEFAULT_MARKDOWN_DIR = "data/markdown"
DEFAULT_ASSETS_DIR = "data/assets"
# Local OpenAI-compatible VLM (llama-server) for figure captions + scanned-PDF OCR.
DEFAULT_VLM_URL = "http://127.0.0.1:21717/v1/chat/completions"
DEFAULT_VLM_MODEL = "qwen2.5-vl-7b"
# Max tokens for a single VLM call (figure caption / full-page OCR). This bounds a
# page-OCR transcription, so it is a potential silent-truncation point on scanned
# pages (which have no text-layer ground truth to catch a cut tail) — kept generous
# and configurable. The caption client warns when a response actually hits the cap
# (finish_reason=length); the docling VlmPipeline OCR path does not surface that, so
# the generous default is the safeguard there.
DEFAULT_VLM_MAX_TOKENS = 8192
# Image-caption tool bounds (scripts/image_caption.py): downscale target + hard byte cap so
# a giant TIFF/PNG can't 413/OOM the VLM server. ~1568^2 px longest-edge.
DEFAULT_CAPTION_MAX_PIXELS = 2458624
DEFAULT_CAPTION_MAX_BYTES = 12000000
# Which source formats the system is allowed to ingest. Empty tuple = accept every
# format the router supports; otherwise a whitelist of bare extensions. Files whose
# format is unsupported or not accepted are reported (never silently converted).
DEFAULT_ACCEPT_FORMATS = ()  # () means "all supported formats"
# Validation / coverage-metric thresholds (used by the convert-time validator + the
# coverage sweep). All parameterized here so NOTHING is hardcoded downstream.
DEFAULT_MIN_RECALL = 0.80          # text recall below this fails validation (lossy)
DEFAULT_MIN_TOKENS = 50            # below this many source tokens, recall is too noisy to judge
DEFAULT_FALLBACK_MIN_RECALL = 0.50  # docling recall below this -> use the PDF text layer
DEFAULT_FALLBACK_MIN_TOKENS = 100  # ...only when the text layer has at least this many tokens
DEFAULT_HEADER_FOOTER_MIN_FRAC = 0.50  # a line on >= this fraction of pages is running boilerplate
# Concurrency planning: the system sizes the shard count from the ACTUAL machine
# (CPU cores, available RAM) at run time rather than a hardcoded shard count.
DEFAULT_THREADS_PER_SHARD = 4      # docling accelerator threads per converter process
DEFAULT_MEM_PER_SHARD_GB = 8       # RAM headroom to reserve per shard (peak ~6GB on big PDFs)
DEFAULT_MAX_SHARDS = 0             # hard cap on shards; 0 = no cap (CPU/RAM bound only)
# Explained-gap acceptance: a doc is lossy only if BOTH token recall < min_recall AND
# character-n-gram content recall < content_min_recall (else the gap is benign
# tokenization/furniture, not lost content). See _coverage.is_lossy_explained.
DEFAULT_CONTENT_MIN_RECALL = 0.95
# Fallback trigger: use the independent body (PDF text layer / native office text) when
# docling's markdown preserves less than this fraction of that body's CHARACTER content
# (char-n-gram) — i.e. the baseline is materially more complete. Lower than
# content_min_recall so a structurally-good docling md (small tokenization gap) is KEPT;
# only a real content shortfall falls back to plain-but-complete text.
DEFAULT_FALLBACK_CONTENT_MIN = 0.85
# Self-healing recovery: how an OOM/hang/transient/docling failure is retried. The
# escalation lane re-runs a memory/hang victim SOLO with more threads + reserved RAM.
DEFAULT_RETRY_ATTEMPTS = 2         # same-lane retries for a TRANSIENT failure
DEFAULT_ESCALATION_ATTEMPTS = 1    # solo bigger-lane re-runs for an OOM/HANG failure
DEFAULT_BIG_DOC_THREADS = 0        # threads for the solo escalation lane (0 = all cores)
DEFAULT_BIG_DOC_MEM_GB = 24        # RAM headroom reserved for the solo escalation lane
# Elastic admission (good-citizen concurrency). Total load is aimed at ~cpu cores:
# shrink above load_high_frac*cpu (yield to others), grow below load_low_frac*cpu.
DEFAULT_LOAD_HIGH_FRAC = 1.1
DEFAULT_LOAD_LOW_FRAC = 0.5
# Independent figure-region detection (PDF drawing objects, no docling opinion):
# a cluster of >= min_paths path/raster objects is a figure region; individual
# objects covering > max_frac of the page are frames/backgrounds (ignored); pad is
# the fractional gap across which nearby objects merge into one cluster.
DEFAULT_IMAGE_REGION_MIN_PATHS = 10
DEFAULT_IMAGE_REGION_PAD = 0.01
DEFAULT_IMAGE_REGION_MAX_FRAC = 0.85

# backend/markdown_dir drive the 3.6 build_index step; the rest are Tier-1/validation
# convert-time settings (scripts/docling_convert.py, 3.12): figure-crop store, VLM
# endpoint/model, caption/OCR enables, and the validator/metric thresholds above.
IngestConfig = namedtuple("IngestConfig", [
    "backend", "markdown_dir", "assets_dir", "vlm_url", "vlm_model",
    "enable_captions", "enable_vlm_ocr",
    "min_recall", "min_tokens", "fallback_min_recall", "fallback_min_tokens",
    "header_footer_min_frac", "content_min_recall", "fallback_content_min",
    "threads_per_shard", "mem_per_shard_gb", "max_shards",
    "retry_attempts", "escalation_attempts", "big_doc_threads", "big_doc_mem_gb",
    "load_high_frac", "load_low_frac",
    "image_region_min_paths", "image_region_pad", "image_region_max_frac",
    "vlm_max_tokens", "accept_formats",
    "caption_max_pixels", "caption_max_bytes",
])
# Defaults for the rightmost (validation/concurrency/recovery) fields so older 7-arg
# construction still works (namedtuple `defaults=` is 3.7+, but we must stay 3.6).
IngestConfig.__new__.__defaults__ = (
    DEFAULT_MIN_RECALL, DEFAULT_MIN_TOKENS, DEFAULT_FALLBACK_MIN_RECALL,
    DEFAULT_FALLBACK_MIN_TOKENS, DEFAULT_HEADER_FOOTER_MIN_FRAC, DEFAULT_CONTENT_MIN_RECALL,
    DEFAULT_FALLBACK_CONTENT_MIN,
    DEFAULT_THREADS_PER_SHARD, DEFAULT_MEM_PER_SHARD_GB, DEFAULT_MAX_SHARDS,
    DEFAULT_RETRY_ATTEMPTS, DEFAULT_ESCALATION_ATTEMPTS, DEFAULT_BIG_DOC_THREADS,
    DEFAULT_BIG_DOC_MEM_GB, DEFAULT_LOAD_HIGH_FRAC, DEFAULT_LOAD_LOW_FRAC,
    DEFAULT_IMAGE_REGION_MIN_PATHS, DEFAULT_IMAGE_REGION_PAD, DEFAULT_IMAGE_REGION_MAX_FRAC,
    DEFAULT_VLM_MAX_TOKENS, DEFAULT_ACCEPT_FORMATS,
    DEFAULT_CAPTION_MAX_PIXELS, DEFAULT_CAPTION_MAX_BYTES,
)


def recommend_shards(cpu, mem_avail_gb, threads_per_shard, mem_per_shard_gb, max_shards=0):
    # type: (int, float, int, float, int) -> tuple
    """Size concurrency from the ACTUAL machine, not a hardcoded shard count.

    shards = min(cpu // threads_per_shard, mem_avail_gb // mem_per_shard_gb[, max_shards]).
    Both the CPU and RAM ceilings are honored (whichever is scarcer binds), so the
    same command adapts to a 32-core/256GB box or a laptop. Returns
    ``(shards, threads_per_shard)``; always at least ``(1, 1)``.
    """
    tps = max(1, int(threads_per_shard))
    by_cpu = max(1, int(cpu) // tps) if cpu else 1
    by_mem = max(1, int(mem_avail_gb // mem_per_shard_gb)) if mem_per_shard_gb else by_cpu
    n = min(by_cpu, by_mem)
    if max_shards and max_shards > 0:
        n = min(n, int(max_shards))
    return max(1, n), tps

_TRUE = ("1", "true", "yes", "on")


def _as_float(val, default):
    # type: (object, float) -> float
    """Coerce to float; ``default`` on None/empty/malformed (never raises)."""
    if val is None:
        return default
    try:
        s = str(val).strip()
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def _as_int(val, default):
    # type: (object, int) -> int
    """Coerce to int; ``default`` on None/empty/malformed (never raises)."""
    if val is None:
        return default
    try:
        s = str(val).strip()
        return int(s) if s else default
    except (TypeError, ValueError):
        return default


def _as_bool(val, default=False):
    # type: (object, bool) -> bool
    """Coerce a config/env string to bool; ``default`` when val is None/empty."""
    if val is None:
        return default
    s = str(val).strip().lower()
    if not s:
        return default
    return s in _TRUE


def _as_ext_tuple(val, default):
    # type: (object, tuple) -> tuple
    """Parse a config accept-list into a tuple of normalized extensions.

    Delegates token parsing to the router's ``normalize_accept`` — the SINGLE accept
    parser — so ``[ingest].accept_formats`` and the ``--accept`` CLI flag agree
    exactly, including the ``"all"`` sentinel (which resolves to "accept everything"
    whether alone or mixed with other tokens). Preserves this layer's convention that
    "accept everything" is stored as ``default`` (an empty tuple, expanded to
    all-supported downstream) rather than baking the full format set into the config.
    Unknown tokens are kept (so a caller can warn on a typo). Never raises."""
    if val is None:
        return default
    exts = _normalize_accept(val)
    if exts == _SUPPORTED_EXTS:      # None / "" / "all" / every-format-listed -> accept all
        return default
    return tuple(sorted(exts))


def _repo_root():
    # src/backend/ingest/_config.py -> up 4 to repo root
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def _default_config_path(repo_root):
    local = os.path.join(repo_root, "config", "default.local.toml")
    if os.path.isfile(local):
        return local
    return os.path.join(repo_root, "config", "default.example.toml")


def _read_toml_section(path, section):
    # type: (str, str) -> dict
    """Read flat string/scalar keys from one ``[section]`` of a simple TOML file."""
    result = {}
    if not path or not os.path.isfile(path):
        return result
    cur = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                cur = line[1:-1].strip()
                continue
            if cur != section or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.split("#", 1)[0].strip()  # our values never contain '#'
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            result[key] = val
    return result


def load_source_root(repo_root=None, env=None, config_path=None):
    # type: (...) -> str
    """Resolve the source-documents root: ``$DOC2MD_SRC`` > ``[paths].source_docs`` > ``""``.

    The corpus location is deployment config, not code — scripts default their
    ``--src`` to this so no host path is ever hardcoded. Returns ``""`` when
    neither the env var nor the config file provides one (callers error out)."""
    env = os.environ if env is None else env
    repo_root = repo_root or _repo_root()
    src = env.get("DOC2MD_SRC")
    if src:
        return src
    paths = _read_toml_section(config_path or _default_config_path(repo_root), "paths")
    return paths.get("source_docs", "") or ""


def load_ingest_config(repo_root=None, env=None, config_path=None):
    # type: (...) -> IngestConfig
    """Resolve the ingest backend + markdown_dir.

    Precedence (highest first): env var, config-file ``[ingest]`` value, default.
      - backend:      ``DOC2MD_INGEST_BACKEND`` -> ``[ingest].backend`` -> ``native``
      - markdown_dir: ``DOC2MD_MARKDOWN_DIR``   -> ``[ingest].markdown_dir`` -> ``data/markdown``
    ``markdown_dir`` is resolved relative to the repo root when not absolute.
    Raises ``ValueError`` on an unknown backend.
    """
    env = os.environ if env is None else env
    repo_root = repo_root or _repo_root()
    toml = _read_toml_section(config_path or _default_config_path(repo_root), "ingest")

    backend = (env.get("DOC2MD_INGEST_BACKEND")
               or toml.get("backend")
               or DEFAULT_BACKEND).strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError("invalid ingest backend %r (expected one of: %s)"
                         % (backend, ", ".join(VALID_BACKENDS)))

    md = (env.get("DOC2MD_MARKDOWN_DIR")
          or toml.get("markdown_dir")
          or DEFAULT_MARKDOWN_DIR)
    if not os.path.isabs(md):
        md = os.path.join(repo_root, md)

    assets = (env.get("DOC2MD_ASSETS_DIR")
              or toml.get("assets_dir")
              or DEFAULT_ASSETS_DIR)
    if not os.path.isabs(assets):
        assets = os.path.join(repo_root, assets)

    vlm_url = (env.get("DOC2MD_VLM_URL") or toml.get("vlm_url") or DEFAULT_VLM_URL)
    vlm_model = (env.get("DOC2MD_VLM_MODEL") or toml.get("vlm_model") or DEFAULT_VLM_MODEL)
    enable_captions = _as_bool(env.get("DOC2MD_ENABLE_CAPTIONS", toml.get("enable_captions")))
    enable_vlm_ocr = _as_bool(env.get("DOC2MD_ENABLE_VLM_OCR", toml.get("enable_vlm_ocr")))

    min_recall = _as_float(env.get("DOC2MD_MIN_RECALL", toml.get("min_recall")), DEFAULT_MIN_RECALL)
    min_tokens = _as_int(env.get("DOC2MD_MIN_TOKENS", toml.get("min_tokens")), DEFAULT_MIN_TOKENS)
    fb_recall = _as_float(env.get("DOC2MD_FALLBACK_MIN_RECALL", toml.get("fallback_min_recall")),
                          DEFAULT_FALLBACK_MIN_RECALL)
    fb_tokens = _as_int(env.get("DOC2MD_FALLBACK_MIN_TOKENS", toml.get("fallback_min_tokens")),
                        DEFAULT_FALLBACK_MIN_TOKENS)
    hf_frac = _as_float(env.get("DOC2MD_HEADER_FOOTER_MIN_FRAC", toml.get("header_footer_min_frac")),
                        DEFAULT_HEADER_FOOTER_MIN_FRAC)
    content_min = _as_float(env.get("DOC2MD_CONTENT_MIN_RECALL", toml.get("content_min_recall")),
                            DEFAULT_CONTENT_MIN_RECALL)
    fb_content = _as_float(env.get("DOC2MD_FALLBACK_CONTENT_MIN", toml.get("fallback_content_min")),
                           DEFAULT_FALLBACK_CONTENT_MIN)
    tps = _as_int(env.get("DOC2MD_THREADS_PER_SHARD", toml.get("threads_per_shard")),
                  DEFAULT_THREADS_PER_SHARD)
    mps = _as_float(env.get("DOC2MD_MEM_PER_SHARD_GB", toml.get("mem_per_shard_gb")),
                    DEFAULT_MEM_PER_SHARD_GB)
    maxsh = _as_int(env.get("DOC2MD_MAX_SHARDS", toml.get("max_shards")), DEFAULT_MAX_SHARDS)
    retry_att = _as_int(env.get("DOC2MD_RETRY_ATTEMPTS", toml.get("retry_attempts")),
                        DEFAULT_RETRY_ATTEMPTS)
    esc_att = _as_int(env.get("DOC2MD_ESCALATION_ATTEMPTS", toml.get("escalation_attempts")),
                      DEFAULT_ESCALATION_ATTEMPTS)
    big_threads = _as_int(env.get("DOC2MD_BIG_DOC_THREADS", toml.get("big_doc_threads")),
                          DEFAULT_BIG_DOC_THREADS)
    big_mem = _as_float(env.get("DOC2MD_BIG_DOC_MEM_GB", toml.get("big_doc_mem_gb")),
                        DEFAULT_BIG_DOC_MEM_GB)
    load_hi = _as_float(env.get("DOC2MD_LOAD_HIGH_FRAC", toml.get("load_high_frac")),
                        DEFAULT_LOAD_HIGH_FRAC)
    load_lo = _as_float(env.get("DOC2MD_LOAD_LOW_FRAC", toml.get("load_low_frac")),
                        DEFAULT_LOAD_LOW_FRAC)
    img_paths = _as_int(env.get("DOC2MD_IMAGE_REGION_MIN_PATHS", toml.get("image_region_min_paths")),
                        DEFAULT_IMAGE_REGION_MIN_PATHS)
    img_pad = _as_float(env.get("DOC2MD_IMAGE_REGION_PAD", toml.get("image_region_pad")),
                        DEFAULT_IMAGE_REGION_PAD)
    img_max = _as_float(env.get("DOC2MD_IMAGE_REGION_MAX_FRAC", toml.get("image_region_max_frac")),
                        DEFAULT_IMAGE_REGION_MAX_FRAC)
    # Floor at 1: a 0/negative cap would silently break every VLM call (empty output),
    # so a misconfigured value falls back to the generous default rather than 0.
    vlm_max_tokens = _as_int(env.get("DOC2MD_VLM_MAX_TOKENS", toml.get("vlm_max_tokens")),
                             DEFAULT_VLM_MAX_TOKENS)
    if vlm_max_tokens < 1:
        vlm_max_tokens = DEFAULT_VLM_MAX_TOKENS
    accept_formats = _as_ext_tuple(env.get("DOC2MD_ACCEPT_FORMATS", toml.get("accept_formats")),
                                   DEFAULT_ACCEPT_FORMATS)
    cap_pixels = _as_int(env.get("DOC2MD_CAPTION_MAX_PIXELS", toml.get("caption_max_pixels")),
                         DEFAULT_CAPTION_MAX_PIXELS)
    cap_bytes = _as_int(env.get("DOC2MD_CAPTION_MAX_BYTES", toml.get("caption_max_bytes")),
                        DEFAULT_CAPTION_MAX_BYTES)

    return IngestConfig(backend=backend, markdown_dir=md, assets_dir=assets,
                        vlm_url=vlm_url, vlm_model=vlm_model,
                        enable_captions=enable_captions, enable_vlm_ocr=enable_vlm_ocr,
                        min_recall=min_recall, min_tokens=min_tokens,
                        fallback_min_recall=fb_recall, fallback_min_tokens=fb_tokens,
                        header_footer_min_frac=hf_frac, content_min_recall=content_min,
                        fallback_content_min=fb_content,
                        threads_per_shard=tps, mem_per_shard_gb=mps, max_shards=maxsh,
                        retry_attempts=retry_att, escalation_attempts=esc_att,
                        big_doc_threads=big_threads, big_doc_mem_gb=big_mem,
                        load_high_frac=load_hi, load_low_frac=load_lo,
                        image_region_min_paths=img_paths, image_region_pad=img_pad,
                        image_region_max_frac=img_max,
                        vlm_max_tokens=vlm_max_tokens, accept_formats=accept_formats,
                        caption_max_pixels=cap_pixels, caption_max_bytes=cap_bytes)
