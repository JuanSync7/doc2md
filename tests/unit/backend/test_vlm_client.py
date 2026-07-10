"""
title: Unit — scripts/_vlm VlmClient (caption client)
kind: tests
layer: backend
summary: Pure payload/parse helpers + caption retry/empty-retry logic, network mocked.
"""
import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(REPO, "scripts", "vlm_client.py")


def _mod():
    spec = importlib.util.spec_from_file_location("vlm_client", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- pure helpers -----------------------------------------------------------

def test_data_uri_png():
    m = _mod()
    uri = m.data_uri(b"\x89PNG\r\n", "png")
    assert uri.startswith("data:image/png;base64,")


def test_data_uri_jpg_normalized_to_jpeg():
    m = _mod()
    assert m.data_uri(b"\xff\xd8", "jpg").startswith("data:image/jpeg;base64,")


def test_build_chat_payload_shape():
    m = _mod()
    p = m.build_chat_payload("describe", "data:image/png;base64,AAA",
                             model="qwen2.5-vl-7b", max_tokens=200, temperature=0.1)
    assert p["model"] == "qwen2.5-vl-7b"
    assert p["max_tokens"] == 200
    assert p["temperature"] == 0.1
    content = p["messages"][0]["content"]
    kinds = [c["type"] for c in content]
    assert "text" in kinds and "image_url" in kinds
    img = next(c for c in content if c["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_chat_text_extracts_content():
    m = _mod()
    resp = {"choices": [{"message": {"content": "  a block diagram  "}}]}
    assert m.parse_chat_text(resp) == "a block diagram"


def test_parse_chat_text_handles_garbage():
    m = _mod()
    assert m.parse_chat_text({}) == ""
    assert m.parse_chat_text({"choices": []}) == ""
    assert m.parse_chat_text({"choices": [{}]}) == ""


# --- caption retry / empty-retry --------------------------------------------

def _client(m, **kw):
    return m.VlmClient("http://127.0.0.1:21717/v1/chat/completions", **kw)


def test_caption_returns_text(monkeypatch):
    m = _mod()
    c = _client(m, retries=2)
    c._post = lambda payload: {"choices": [{"message": {"content": "a real caption here"}}]}
    assert c.caption(b"img") == "a real caption here"


def test_caption_empty_then_good_retries(monkeypatch):
    m = _mod()
    c = _client(m, retries=2)
    calls = {"n": 0}

    def fake_post(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": "   "}}]}   # hollow/empty glitch
        return {"choices": [{"message": {"content": "good caption"}}]}

    c._post = fake_post
    assert c.caption(b"img") == "good caption"
    assert calls["n"] == 2


def test_caption_exception_then_good_retries(monkeypatch):
    m = _mod()
    c = _client(m, retries=2)
    calls = {"n": 0}

    def fake_post(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return {"choices": [{"message": {"content": "recovered caption"}}]}

    c._post = fake_post
    assert c.caption(b"img") == "recovered caption"
    assert calls["n"] == 2


def test_caption_all_empty_returns_blank():
    m = _mod()
    c = _client(m, retries=1)
    c._post = lambda payload: {"choices": [{"message": {"content": ""}}]}
    assert c.caption(b"img") == ""


# --- caption_result: transport vs useless (review fixes 3 & 4) ---------------

def _scripted_client(m, replies):
    """A VlmClient whose _post pops scripted responses; a response of Exception is raised."""
    c = m.VlmClient("http://x/v1/chat/completions", retries=2)
    seq = list(replies)

    def fake_post(payload):
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    c._post = fake_post
    return c


def _reply(text, fr="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": fr}]}


def test_caption_result_ok():
    m = _mod()
    r = _scripted_client(m, [_reply("a good caption")]).caption_result(b"x")
    assert r["ok"] is True and r["text"] == "a good caption"


def test_caption_result_empty_reply_is_ok_not_pending():
    # a genuine empty reply on the LAST retry, after an earlier genuine reply, is ok=True
    m = _mod()
    r = _scripted_client(m, [_reply(""), _reply(""), _reply("")]).caption_result(b"x")
    assert r["ok"] is True and r["text"] == ""      # heard back -> USELESS downstream, not pending


def test_caption_result_transport_down_is_pending():
    m = _mod()
    r = _scripted_client(m, [OSError("down"), OSError("down"), OSError("down")]).caption_result(b"x")
    assert r["ok"] is False                          # never heard back -> pending


def test_caption_result_ok_is_monotonic_across_a_late_blip():
    # empty genuine reply first (server up), then a transport blip on the retry -> still ok=True
    m = _mod()
    r = _scripted_client(m, [_reply(""), OSError("blip"), OSError("blip")]).caption_result(b"x")
    assert r["ok"] is True                            # we DID hear back once -> not pending


def test_caption_result_200_without_choices_is_pending():
    # a 200 whose body lacks choices (server error surfaced as 200) is NOT a real reply
    m = _mod()
    r = _scripted_client(m, [{"error": "overloaded"}, {"error": "overloaded"}, {"error": "overloaded"}]).caption_result(b"x")
    assert r["ok"] is False                           # disguised outage -> pending, never cached
