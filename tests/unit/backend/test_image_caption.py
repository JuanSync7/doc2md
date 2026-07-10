"""
title: Unit — the shared caption tool (scripts/image_caption.py)
kind: tests
layer: backend
summary: Outcome classification, prepare/probe, content-addressed cache, and caption_image with a mock VLM.
"""
import importlib.util
import os
import struct

import pytest

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "image_caption", os.path.join(REPO, "scripts", "image_caption.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _png(w=8, h=8):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 4


class _Client(object):
    """Mock VlmClient.caption_result: pops scripted replies, counts calls."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.model = "mock-vlm"

    def caption_result(self, image_bytes, fmt="png", prompt=""):
        self.calls += 1
        return self.replies.pop(0)


GOOD = ("This is a block diagram of the clock generator showing the PLL feeding three "
        "divider stages into the SoC fabric.")


# --- classify_reply ---------------------------------------------------------

def test_classify_ok_useless_furniture():
    m = _mod()
    assert m.classify_reply(GOOD, "stop", "x").kind == m.OK
    assert m.classify_reply("too short", "stop", "x").kind == m.USELESS
    assert m.classify_reply("", "stop", "x").kind == m.USELESS
    assert m.classify_reply("This is a company logo of a stylized letter A in blue.",
                            "stop", "x").kind == m.FURNITURE


def test_classify_marks_truncated():
    m = _mod()
    assert m.classify_reply(GOOD, "length", "x").truncated is True
    assert m.classify_reply(GOOD, "stop", "x").truncated is False


# --- prepare_png / probe ----------------------------------------------------

def test_prepare_passes_valid_png():
    m = _mod()
    png, err = m.prepare_png(_png(), "png", max_bytes=10_000_000, render_metafile=None)
    assert err is None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_prepare_undecodable():
    m = _mod()
    _, err = m.prepare_png(b"not an image at all", "png", max_bytes=10_000_000)
    assert err == m.UNDECODABLE


def test_prepare_metafile_without_renderer_fails():
    m = _mod()
    emf = bytearray(88); struct.pack_into("<I", emf, 0, 1); emf[40:44] = b" EMF"
    _, err = m.prepare_png(bytes(emf), "emf", max_bytes=10_000_000, render_metafile=None)
    assert err == m.RENDER_FAILED


def test_prepare_metafile_with_renderer():
    m = _mod()
    emf = bytearray(88); struct.pack_into("<I", emf, 0, 1); emf[40:44] = b" EMF"
    png, err = m.prepare_png(bytes(emf), "emf", max_bytes=10_000_000,
                             render_metafile=lambda b: _png())
    assert err is None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_prepare_too_large_without_pil():
    m = _mod()
    big = _png() + b"\x00" * 5000
    _, err = m.prepare_png(big, "png", max_bytes=1000)   # no PIL on host -> can't downscale
    assert err == m.TOO_LARGE


# --- CaptionCache -----------------------------------------------------------

def test_cache_put_get_and_persist(tmp_path):
    m = _mod()
    p = str(tmp_path / "_captions.jsonl")
    c = m.CaptionCache(p)
    key = "sha256:abc"
    c.put(key, m.Outcome(m.OK, "a caption", "mock", False))
    assert c.get(key)["kind"] == m.OK
    # reload from disk -> persisted
    c2 = m.CaptionCache(p)
    assert c2.get(key)["caption"] == "a caption"


def test_cache_glob_merges_shards_last_wins(tmp_path):
    m = _mod()
    import json
    (tmp_path / "_captions.w0.jsonl").write_text(
        json.dumps({"key": "sha256:a", "kind": m.OK, "caption": "old"}) + "\n", encoding="utf-8")
    (tmp_path / "_captions.w1.jsonl").write_text(
        json.dumps({"key": "sha256:a", "kind": m.OK, "caption": "new"}) + "\n", encoding="utf-8")
    c = m.CaptionCache(str(tmp_path / "_captions.jsonl"))
    assert c.get("sha256:a")["caption"] == "new"


# --- caption_image (integration of the above with a mock client) ------------

class _Cfg(object):
    caption_max_bytes = 10_000_000
    caption_max_pixels = 2_458_624


def test_caption_image_ok_then_cache_hit(tmp_path):
    m = _mod()
    cache = m.CaptionCache(str(tmp_path / "_captions.jsonl"))
    client = _Client([{"text": GOOD, "finish_reason": "stop", "ok": True}])
    oc = m.caption_image(_png(), "png", cache, client, _Cfg())
    assert oc.kind == m.OK and client.calls == 1
    # a second identical image is served from cache -> no new model call
    oc2 = m.caption_image(_png(), "png", cache, client, _Cfg())
    assert oc2.kind == m.OK and client.calls == 1


def test_caption_image_unavailable_not_cached_and_retries(tmp_path):
    m = _mod()
    cache = m.CaptionCache(str(tmp_path / "_captions.jsonl"))
    # first call: transport down (ok=False) -> UNAVAILABLE, NOT cached
    client = _Client([{"text": "", "finish_reason": "", "ok": False},
                      {"text": GOOD, "finish_reason": "stop", "ok": True}])
    oc = m.caption_image(_png(), "png", cache, client, _Cfg())
    assert oc.kind == m.UNAVAILABLE
    assert cache.get(m.caption_cache_key(_png())) is None      # never cached a pending
    # retry succeeds
    oc2 = m.caption_image(_png(), "png", cache, client, _Cfg())
    assert oc2.kind == m.OK and client.calls == 2


def test_caption_image_furniture_dropped_and_cached(tmp_path):
    m = _mod()
    cache = m.CaptionCache(str(tmp_path / "_captions.jsonl"))
    client = _Client([{"text": "This is a company logo, a blue letter A.", "finish_reason": "stop", "ok": True}])
    oc = m.caption_image(_png(), "png", cache, client, _Cfg())
    assert oc.kind == m.FURNITURE
    assert cache.get(m.caption_cache_key(_png()))["kind"] == m.FURNITURE   # terminal -> cached
