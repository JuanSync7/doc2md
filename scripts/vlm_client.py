"""
title: VLM caption client for the offline converter (private script helper)
layer: backend
public_api: no
summary: POST figure crops to a local OpenAI-compatible VLM (llama-server) for captions; pure payload/parse split + retry/empty-retry. 3.6-safe stdlib.
"""
# Private helper for scripts/docling_convert.py ONLY (not a library for src/). Network
# lives here, out of the 3.6 ingest policy package. Stdlib only (urllib/json/base64),
# 3.6-compatible. Scanned-PDF OCR does NOT use this — that goes through docling's own
# VlmPipeline+ApiVlmOptions; this client captions the SURVIVING figure crops.
import base64
import json
import sys
import time

import urllib.request
import urllib.error

# Default figure-caption prompt: a single self-contained paragraph for the search index,
# stating the visual TYPE (the docling classifier can't name timing/waveform/block
# diagrams) then the concrete content. Mirrors llm_lab/harness/run_vision.py.
CAPTION_PROMPT = (
    "You are converting a figure extracted from a document into TEXT, so that a reader — or a "
    "search index — gets the figure's full information WITHOUT seeing it. The document used a "
    "picture to tell part of its story; tell that same story in words.\n"
    "Write flowing, self-contained prose that could sit in the document body as an ordinary "
    "paragraph. Do NOT open with 'This image is…', 'The figure shows…', or a label of the "
    "visual type — state the actual content directly, in the register of a technical document.\n"
    "Recover the information in whatever form preserves it best:\n"
    "- Mostly TEXT (a scanned page, screenshot, table, or code listing): TRANSCRIBE it "
    "faithfully and completely — preserve the wording, labels, numbers and reading order; "
    "render a table row by row. Aim for a near-lossless transcription, not a summary.\n"
    "- A DIAGRAM (block, schematic, flowchart, state, sequence, or timing/waveform): describe "
    "what it conveys AND how the parts relate — the connections and the direction of signal or "
    "data flow; the ordered steps and the condition on each transition; or, for a waveform, "
    "each signal's transitions and their timing cause-and-effect (e.g. 'when CLK rises, DATA is "
    "latched and VALID goes high one cycle later; REQ stays high until ACK is asserted').\n"
    "- A CHART or plot: give the axes and units, each series' trend, and notable values, peaks "
    "or crossovers.\n"
    "- A PHOTO or rendering: describe concretely what is shown.\n"
    "Quote every visible label, name and number exactly as written. Describe ONLY what is "
    "actually visible — never invent or infer details that are not in the image. Match the "
    "length to the content: a sentence or two for a trivial figure, a full detailed paragraph "
    "(or more) for a dense diagram or a text-heavy scan."
)


def data_uri(image_bytes, fmt="png"):
    # type: (bytes, str) -> str
    """``data:image/<fmt>;base64,...`` URI for an image, fmt normalized (jpg -> jpeg)."""
    fmt = (fmt or "png").lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"
    return "data:image/%s;base64,%s" % (fmt, base64.b64encode(image_bytes).decode("ascii"))


def build_chat_payload(prompt, image_data_uri, model="qwen2.5-vl-7b",
                       max_tokens=384, temperature=0.2):
    # type: (str, str, str, int, float) -> dict
    """OpenAI chat-completions payload with one user turn = text prompt + image_url."""
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]}],
    }


def parse_chat_text(resp):
    # type: (dict) -> str
    """Extract ``choices[0].message.content`` from an OpenAI response; "" on any miss."""
    try:
        return (resp["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def parse_finish_reason(resp):
    # type: (dict) -> str
    """``choices[0].finish_reason`` ("stop"/"length"/...); "" on any miss. ``"length"``
    means the model was cut off at ``max_tokens`` — the caption/OCR tail was truncated."""
    try:
        return resp["choices"][0].get("finish_reason", "") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def has_choice(resp):
    # type: (dict) -> bool
    """True only if ``resp`` is a genuine chat reply — a ``choices[0].message`` structure.
    A 200 whose body lacks ``choices`` (a server error surfaced as 200, or garbage JSON) is
    NOT a real reply and must be treated like a transport failure, not an empty caption."""
    try:
        return isinstance(resp["choices"][0]["message"], dict)
    except (KeyError, IndexError, TypeError):
        return False


def _health_url(chat_url):
    # type: (str) -> str
    """Derive the llama-server /health URL from the chat-completions URL."""
    i = chat_url.find("/v1/")
    base = chat_url[:i] if i != -1 else chat_url.rstrip("/")
    return base + "/health"


class VlmClient:
    """Caption figure crops via a local OpenAI-compatible VLM (llama-server)."""

    def __init__(self, url, model="qwen2.5-vl-7b", max_tokens=384, temperature=0.2,
                 timeout=600, retries=2):
        # type: (str, str, int, float, int, int) -> None
        self.url = url
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries

    def _post(self, payload):
        # type: (dict) -> dict
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def caption(self, image_bytes, fmt="png", prompt=CAPTION_PROMPT):
        # type: (bytes, str, str) -> str
        """Return a caption for one image, or "" if the model never yields usable text.

        Retries on transport error AND on empty output (the hollow-table glitch we saw
        on CPU VLMs), up to ``retries`` extra attempts.
        """
        payload = build_chat_payload(prompt, data_uri(image_bytes, fmt),
                                     self.model, self.max_tokens, self.temperature)
        for attempt in range(self.retries + 1):
            try:
                resp = self._post(payload)
                txt = parse_chat_text(resp)
                if txt:
                    if parse_finish_reason(resp) == "length":
                        # The response was CUT OFF at the cap -> the caption tail is lost.
                        # Surface it (the documented safeguard) so the operator can raise
                        # vlm_max_tokens instead of silently indexing a truncated caption.
                        sys.stderr.write(
                            "  [vlm] WARNING: caption hit the %d-token cap (finish_reason="
                            "length) -> tail truncated; raise vlm_max_tokens\n" % self.max_tokens)
                    return txt
            except Exception:
                pass   # transient transport/decoding error -> retry
        return ""

    def caption_result(self, image_bytes, fmt="png", prompt=CAPTION_PROMPT):
        # type: (bytes, str, str) -> dict
        """Like ``caption`` but returns ``{"text", "finish_reason", "ok"}`` so the caller can
        tell a TRANSPORT failure (``ok=False`` -> the figure is pending, retry later, never
        cache) apart from a genuine empty/useless reply (``ok=True``, empty text). This is the
        distinction the shared caption tool needs to avoid recording a VLM outage as a
        permanent drop."""
        payload = build_chat_payload(prompt, data_uri(image_bytes, fmt),
                                     self.model, self.max_tokens, self.temperature)
        got_reply = False        # sticky: True once ANY attempt returns a genuine chat reply
        for attempt in range(self.retries + 1):
            try:
                resp = self._post(payload)
                if not has_choice(resp):
                    continue       # 200 without choices == disguised outage -> retry, then pending
                got_reply = True
                txt = parse_chat_text(resp)
                if txt:
                    return {"text": txt, "finish_reason": parse_finish_reason(resp), "ok": True}
                # genuine empty reply -> retry the empty-output glitch, but we DID hear back
            except Exception:
                pass               # transport/decode error -> keep retrying; ok stays monotonic
        return {"text": "", "finish_reason": "", "ok": got_reply}

    def healthy(self, timeout=5):
        # type: (int) -> bool
        """True if the llama-server answers /health 200 (server is up + model loaded)."""
        try:
            with urllib.request.urlopen(_health_url(self.url), timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def wait_healthy(self, timeout=900, poll=2):
        # type: (int, int) -> bool
        """Block until /health is 200 or ``timeout`` seconds elapse."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.healthy():
                return True
            time.sleep(poll)
        return False
