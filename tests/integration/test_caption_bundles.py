"""
title: Integration — bundle caption enrichment fills structure.json (mock VLM)
kind: tests
layer: backend
summary: build a real bundle, caption its images through caption_bundles with a stub VLM; structure.json captions filled, images captioned once (dedup), coverage + pass-rate recorded, prompt grounding threaded, idempotent via cache.
"""
import importlib.util
import json
import os
import struct
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PIC = ('xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"')
IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
GOOD = ("This is a detailed block diagram of the memory controller showing the arbiter, "
        "the read and write queues, and the DDR PHY interface and their connections.")


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _png(w, h, tag):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 8 + tag


def _draw(rid):
    return ('<w:p><w:r><w:drawing %s %s><wp:inline><a:graphic><a:graphicData>'
            '<pic:pic><pic:blipFill><a:blip r:embed="%s"/></pic:blipFill></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
            % (A, PIC, rid))


def _docx(path):
    # two distinct images + a THIRD reference reusing the first (dedup path)
    doc = ('<w:document %s %s><w:body>'
           '<w:p><w:r><w:t>Figures below.</w:t></w:r></w:p>%s%s%s'
           '</w:body></w:document>'
           % (W, R, _draw("rId1"), _draw("rId2"), _draw("rId3")))
    rels = ('<Relationships %s>'
            '<Relationship Id="rId1" Target="media/a.png" Type="%s"/>'
            '<Relationship Id="rId2" Target="media/b.png" Type="%s"/>'
            '<Relationship Id="rId3" Target="media/a.png" Type="%s"/>'
            '</Relationships>' % (RELS, IMG, IMG, IMG))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/a.png", _png(40, 30, b"AAAA"))
        z.writestr("word/media/b.png", _png(50, 20, b"BBBB"))


class _StubClient(object):
    """Records prompts + counts calls; returns GOOD unless told to fail/be-furniture."""

    def __init__(self, text=GOOD, ok=True, model="stub-vl"):
        self.calls = 0
        self.prompts = []
        self.model = model
        self._text = text
        self._ok = ok

    def caption_result(self, image_bytes, fmt="png", prompt=""):
        self.calls += 1
        self.prompts.append(prompt)
        return {"text": self._text, "finish_reason": "stop", "ok": self._ok}

    def healthy(self, timeout=5):
        return True


def _build(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "bundles"; src.mkdir()
    _docx(str(src / "figs.docx"))
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    return str(out)


def _images(structure):
    out = []
    def walk(ns):
        for n in ns:
            out.extend(n["images"]); walk(n["children"])
    walk(structure["outline"])
    return out


def _struct(out):
    did = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))][0]
    return did, json.load(open(os.path.join(out, did, "structure.json"), encoding="utf-8"))


def test_captions_fill_structure_and_dedup(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    # pre-state: 3 image references (a reused), captions all null
    _, st0 = _struct(out)
    assert len(_images(st0)) == 3 and all(im["caption"] is None for im in _images(st0))

    stub = _StubClient()
    rc = cb.main(["--bundles", out, "--no-cache"], client=stub)
    assert rc == 0
    # only TWO unique images -> two VLM calls (the reused image is captioned once)
    assert stub.calls == 2
    _, st1 = _struct(out)
    imgs = _images(st1)
    assert len(imgs) == 3 and all(im["caption"] == GOOD for im in imgs)   # applied to all refs
    # coverage records one row per reference, all useful, none furniture
    cov = [json.loads(l) for l in open(os.path.join(out, "_caption_coverage.jsonl"))]
    assert len(cov) == 3 and all(r["useful"] and not r["furniture"] for r in cov)
    assert all(r["kind"] == "OK" and r["model"] == "stub-vl" for r in cov)


def test_domain_grounding_is_threaded_into_the_prompt(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    stub = _StubClient()
    cb.main(["--bundles", out, "--no-cache", "--domain",
             "These figures come from semiconductor SoC engineering specifications."],
            client=stub)
    assert stub.prompts and all(
        "semiconductor SoC engineering" in p for p in stub.prompts)   # grounding prepended


def _docx_sectioned(path):
    # a heading + prose + an image, so the figure has a section path AND surrounding text
    doc = ('<w:document %s %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Clock Architecture</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>The PLL drives three clock domains across the fabric.</w:t></w:r></w:p>'
           '%s'
           '</w:body></w:document>' % (W, R, _draw("rId1")))
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    rels = ('<Relationships %s><Relationship Id="rId1" Target="media/a.png" Type="%s"/>'
            '</Relationships>' % (RELS, IMG))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/a.png", _png(40, 30, b"AAAA"))


def test_document_context_is_threaded_into_the_prompt(tmp_path):
    cb = _mod("caption_bundles")
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "b"; src.mkdir()
    _docx_sectioned(str(src / "clocks.docx"))
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    stub = _StubClient()
    cb.main(["--bundles", str(out), "--no-cache"], client=stub)     # context ON by default
    p = stub.prompts[0]
    assert "Section: Clock Architecture" in p                       # heading path grounded
    assert "PLL drives three clock domains" in p                    # surrounding text grounded
    assert "describe only what is actually visible" in p            # anti-hallucination guard


def test_no_context_flag_produces_image_only_prompt(tmp_path):
    cb = _mod("caption_bundles")
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "b"; src.mkdir()
    _docx_sectioned(str(src / "clocks.docx"))
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    stub = _StubClient()
    cb.main(["--bundles", str(out), "--no-cache", "--no-context"], client=stub)
    p = stub.prompts[0]
    assert "Clock Architecture" not in p and "Ground the caption" not in p   # no context block


def test_furniture_verdict_leaves_caption_null(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    # a model reply whose own verdict is "logo" and names no informative type -> dropped
    stub = _StubClient(text="This is a company logo.")
    cb.main(["--bundles", out, "--no-cache"], client=stub)
    _, st = _struct(out)
    assert all(im["caption"] is None for im in _images(st))            # not stored
    cov = [json.loads(l) for l in open(os.path.join(out, "_caption_coverage.jsonl"))]
    assert all(r["kind"] == "FURNITURE" for r in cov)


def test_cache_makes_rerun_recaption_nothing(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    stub = _StubClient()
    cb.main(["--bundles", out], client=stub)          # cached run (no --no-cache)
    first = stub.calls
    assert first == 2
    stub2 = _StubClient()
    cb.main(["--bundles", out], client=stub2)          # cache hit -> zero new VLM calls
    assert stub2.calls == 0
    _, st = _struct(out)
    assert all(im["caption"] == GOOD for im in _images(st))            # still captioned


def test_swapping_the_model_recaptions(tmp_path):
    # incremental roadmap: caption with model A, later re-caption with a STRONGER model.
    # The cache key includes the model, so the swap must re-run (not return A's stale caption).
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    a = _StubClient(text="Caption from the small model.", model="qwen-7b")
    cb.main(["--bundles", out], client=a)
    assert a.calls == 2
    b = _StubClient(text="Much richer caption from the big model.", model="bedrock-big")
    cb.main(["--bundles", out], client=b)              # different model -> cache MISS -> re-run
    assert b.calls == 2                                # not zero: it re-captioned
    _, st = _struct(out)
    assert all(im["caption"] == "Much richer caption from the big model."
               for im in _images(st))                 # structure.json now carries the new model's text
    # and re-running the big model again is a free cache hit
    c = _StubClient(text="unused", model="bedrock-big")
    cb.main(["--bundles", out], client=c)
    assert c.calls == 0


def _report(out):
    did = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))][0]
    return json.load(open(os.path.join(out, did, "report.json"), encoding="utf-8"))


def test_caption_run_reflects_coverage_in_report(tmp_path):
    # the enrichment reflects its verdict in report.json's captions block — the same way
    # the office lane reflects losslessness — while leaving status/losslessness untouched.
    cb = _mod("caption_bundles")
    out = _build(tmp_path)                              # 2 unique images
    rep0 = _report(out)
    assert rep0["captions"]["gate"] in ("disabled", "pending")   # built, not yet captioned
    stub = _StubClient()
    cb.main(["--bundles", out, "--no-cache"], client=stub)
    cap = _report(out)["captions"]
    assert cap["enabled"] and cap["expected"] == 2
    assert cap["captioned"] == 2 and cap["pending"] == 0
    assert cap["gate"] == "complete" and cap["model"] == "stub-vl"
    # the overlay must not disturb the deterministic verdict
    assert _report(out)["losslessness"]["gate"] == "pass"
    assert _report(out)["status"] in ("ok", "degraded")


def test_furniture_and_outage_are_reflected_in_report(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    cb.main(["--bundles", out, "--no-cache"], client=_StubClient(text="This is a company logo."))
    cap = _report(out)["captions"]
    assert cap["furniture"] == 2 and cap["captioned"] == 0 and cap["gate"] == "complete"

    two = tmp_path / "two"; two.mkdir()
    out2 = _build(two)
    cb.main(["--bundles", out2, "--no-cache"], client=_StubClient(ok=False))   # VLM down
    cap2 = _report(out2)["captions"]
    assert cap2["pending"] == 2 and cap2["gate"] != "complete"


def test_pending_when_vlm_unavailable(tmp_path):
    cb = _mod("caption_bundles")
    out = _build(tmp_path)
    stub = _StubClient(ok=False)                       # transport failure -> UNAVAILABLE
    rc = cb.main(["--bundles", out, "--no-cache"], client=stub)
    assert rc == 2                                     # incomplete -> re-run when up
    _, st = _struct(out)
    assert all(im["caption"] is None for im in _images(st))            # left pending, not stored
