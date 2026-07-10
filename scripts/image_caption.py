#!/usr/bin/env python3
"""The shared image-caption tool — ONE image->text path for every lane.

docling (PDF crops), office (embedded raster/metafile), and the standalone-image lane all
call ``caption_image`` here; none bakes in its own copy. It is:

  * content-addressed — keyed by ``caption_cache_key`` (sha256 of the EXACT bytes the model
    sees), so the same picture (a reused logo, a shared diagram) is captioned ONCE
    corpus-wide and the caption is reused;
  * typed — returns an ``Outcome`` whose ``kind`` distinguishes a useful caption (OK), a
    neutral keep (USELESS), model-identified furniture (FURNITURE), a decode/size/render
    failure (UNDECODABLE/TOO_LARGE/RENDER_FAILED), and a transient VLM outage (UNAVAILABLE)
    that is NEVER cached and marks the figure pending;
  * formula-safe by construction — the gate upstream never drops informative images; here a
    formula caption is OK and only the model's own "this is a logo" verdict drops furniture.

The pure policy (cache key, useful/furniture classification, gate) lives in backend.ingest
and is unit-tested under 3.6; only the file I/O, PIL downscale, soffice render, and VlmClient
wiring live here (py3.12 + network + optional PIL).
"""
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _HERE)   # sibling vlm_client

from backend.ingest import (  # noqa: E402
    caption_cache_key, cache_last_wins, caption_is_useful, caption_type_is_furniture,
    sniff_image_format)

# Outcome kinds. Terminal kinds are recorded and (for the content-addressable ones) cached;
# UNAVAILABLE is non-terminal (pending) and NEVER cached, so a re-run retries it.
OK = "OK"                    # useful caption -> inline
USELESS = "USELESS"          # model replied but caption unusable -> keep image, neutral alt
FURNITURE = "FURNITURE"      # model's own verdict: logo/icon/... -> drop
UNDECODABLE = "UNDECODABLE"  # bytes are not a decodable image -> drop, flagged
TOO_LARGE = "TOO_LARGE"      # exceeds the byte cap and cannot be downscaled -> drop, flagged
RENDER_FAILED = "RENDER_FAILED"  # metafile (emf/wmf) could not be rendered -> LOSS (maybe a formula)
UNAVAILABLE = "UNAVAILABLE"  # VLM transport failure -> pending, retry, never cached

_TERMINAL = frozenset([OK, USELESS, FURNITURE, UNDECODABLE, TOO_LARGE, RENDER_FAILED])
_CACHEABLE = frozenset([OK, USELESS, FURNITURE])   # content-addressable model verdicts

Outcome = namedtuple("Outcome", ["kind", "text", "model", "truncated"])


def classify_reply(text, finish_reason, model):
    # type: (str, str, str) -> Outcome
    """Turn a model reply into an Outcome. Furniture (the model's own stated type) is dropped;
    a useful caption is OK; anything else is USELESS (keep image with a neutral alt, never a
    hard drop). ``finish_reason == "length"`` flags a truncated caption."""
    truncated = (finish_reason == "length")
    if caption_type_is_furniture(text):
        return Outcome(FURNITURE, text, model, truncated)
    if caption_is_useful(text):
        return Outcome(OK, text, model, truncated)
    return Outcome(USELESS, text, model, truncated)


def _decodes(image_bytes):
    # type: (bytes) -> object
    """True/False if PIL can/can't actually DECODE the bytes; None if PIL is unavailable.
    A magic-byte sniff alone passes a corrupt-but-headed image on to the VLM, where it fails
    as a transport error and gets stuck PENDING forever — so when PIL is present we truly
    decode and mark a failure terminal UNDECODABLE."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        Image.open(io.BytesIO(image_bytes)).verify()
        return True
    except Exception:
        return False


def _downscale(image_bytes, max_pixels):
    # type: (bytes, int) -> bytes
    """Downscale to <= max_pixels via PIL if available, returning PNG bytes, else None.
    PIL is optional (absent on this host) — without it, oversized images are skipped
    (TOO_LARGE) rather than risking a 413/OOM on the VLM server."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        if max_pixels and (im.width * im.height) > max_pixels:
            import math
            scale = math.sqrt(float(max_pixels) / (im.width * im.height))
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


def prepare_png(image_bytes, fmt, max_bytes, render_metafile=None, max_pixels=None):
    # type: (bytes, str, int, object, int) -> tuple
    """``(png_bytes, err)`` — decode-probe + metafile-render + size-bound the input BEFORE it
    reaches the model. ``err`` is None on success, else a terminal Outcome kind
    (UNDECODABLE / TOO_LARGE / RENDER_FAILED). Metafiles (emf/wmf) are rendered to PNG first."""
    fmt = (fmt or sniff_image_format(image_bytes) or "").lower().lstrip(".")
    if fmt in ("emf", "wmf"):
        if render_metafile is None:
            return (b"", RENDER_FAILED)
        try:
            png = render_metafile(image_bytes)
        except Exception:
            png = None
        if not png:
            return (b"", RENDER_FAILED)
        image_bytes = png
    if not sniff_image_format(image_bytes):
        return (b"", UNDECODABLE)
    if _decodes(image_bytes) is False:            # real decode (when PIL present) — corrupt bytes
        return (b"", UNDECODABLE)
    if max_bytes and len(image_bytes) > max_bytes:
        png = _downscale(image_bytes, max_pixels)
        if png is None or len(png) > max_bytes:
            return (b"", TOO_LARGE)
        image_bytes = png
    return (image_bytes, None)


class CaptionCache(object):
    """Content-addressed caption store. Loads every ``_captions*.jsonl`` shard (glob +
    last-wins per key, the repo convention), appends new terminal outcomes to ``path``."""

    def __init__(self, path):
        # type: (str) -> None
        self.path = path
        self._dir = os.path.dirname(path) or "."
        self._d = self._load()

    def _load(self):
        recs = []
        for fp in sorted(glob.glob(os.path.join(self._dir, "_captions*.jsonl"))):
            try:
                with open(fp, encoding="utf-8") as f:
                    for line in f:
                        try:
                            recs.append(json.loads(line))
                        except ValueError:
                            continue
            except OSError:
                pass
        return cache_last_wins(recs, "key")

    def get(self, key):
        # type: (str) -> dict
        return self._d.get(key)

    def put(self, key, outcome):
        # type: (str, Outcome) -> None
        rec = {"key": key, "kind": outcome.kind, "caption": outcome.text,
               "model": outcome.model, "truncated": bool(outcome.truncated),
               "ts": int(time.time())}
        try:
            os.makedirs(self._dir, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        self._d[key] = rec


def caption_image(image_bytes, fmt, cache, client, cfg, render_metafile=None,
                  prompt=None, key_suffix=""):
    # type: (bytes, str, CaptionCache, object, object, object, object, str) -> Outcome
    """Caption one image: probe/render/size -> cache lookup -> VLM (once) -> classify -> cache.

    Never captions the same content twice (cache keyed by the exact model-input bytes). A VLM
    transport failure returns UNAVAILABLE and is NOT cached, so the figure stays pending for a
    later run. Only terminal model verdicts (OK/USELESS/FURNITURE) are cached.

    ``prompt`` (when given) overrides the client's default caption prompt — used for
    CONTEXT-GROUNDED captioning, where the prompt carries the figure's document context.
    ``key_suffix`` is appended to the content-address cache key so an image captioned under
    DIFFERENT context (a different prompt) caches separately instead of returning the
    image-only caption. Both default to the legacy image-only behavior."""
    max_bytes = getattr(cfg, "caption_max_bytes", 12_000_000)
    max_pixels = getattr(cfg, "caption_max_pixels", None)
    png, err = prepare_png(image_bytes, fmt, max_bytes, render_metafile, max_pixels)
    if err is not None:
        return Outcome(err, "", "", False)
    key = caption_cache_key(png)
    if key_suffix:
        key = key + ":" + key_suffix
    hit = cache.get(key)
    if hit is not None:
        return Outcome(hit.get("kind", USELESS), hit.get("caption", ""),
                       hit.get("model", ""), hit.get("truncated", False))
    res = (client.caption_result(png, "png", prompt) if prompt is not None
           else client.caption_result(png, "png"))
    if not res.get("ok"):
        return Outcome(UNAVAILABLE, "", "", False)     # pending -> never cached
    oc = classify_reply(res.get("text", ""), res.get("finish_reason", ""),
                        getattr(client, "model", ""))
    if oc.kind in _CACHEABLE:
        cache.put(key, oc)
    return oc


# --- real-world helpers (I/O; not exercised by the pure unit tests) ----------

def soffice_metafile_renderer(soffice, timeout=120):
    # type: (str, int) -> object
    """A ``render_metafile(bytes)->png_bytes`` backed by LibreOffice, each render in an
    ISOLATED user profile so concurrent workers don't contend on the default profile lock.
    Returns None (=> RENDER_FAILED) on any failure."""
    def _render(image_bytes):
        tmp = tempfile.mkdtemp(prefix="doc2md_emf_")
        try:
            src = os.path.join(tmp, "in.emf")
            with open(src, "wb") as f:
                f.write(image_bytes)
            prof = "-env:UserInstallation=file://%s" % os.path.join(tmp, "profile")
            subprocess.check_output(
                [soffice, "--headless", prof, "--convert-to", "png", "--outdir", tmp, src],
                stderr=subprocess.STDOUT, timeout=timeout)
            out = os.path.join(tmp, "in.png")
            if os.path.isfile(out):
                with open(out, "rb") as f:
                    return f.read()
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _render
