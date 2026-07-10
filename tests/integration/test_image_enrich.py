"""
title: Integration — office figure enrichment (extract -> gate -> caption -> inline)
kind: tests
layer: backend
summary: Real docx zips through the corpus-global enrichment with a mock VLM; body diagram captioned, header logo dropped, records written.
"""
import importlib.util
import json
import os
import struct
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _png(w=40, h=30, tag=b"A"):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 8 + tag


def _rels(*pairs):
    r = "".join('<Relationship Id="rId%d" Type="%s" Target="%s"/>' % (i, t, tgt)
                for i, (t, tgt) in enumerate(pairs, 1))
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + r + "</Relationships>")


def _docx(path, diagram_png, logo_png):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document %s><w:body><w:p><w:r><w:t>Body text.</w:t></w:r></w:p>"
                   "</w:body></w:document>" % W)
        z.writestr("word/_rels/document.xml.rels", _rels((IMG, "media/diagram.png")))
        z.writestr("word/_rels/header1.xml.rels", _rels((IMG, "media/logo.png")))
        z.writestr("word/media/diagram.png", diagram_png)   # BODY -> caption
        z.writestr("word/media/logo.png", logo_png)         # CHROME -> dropped by gate


GOOD = ("This is a detailed block diagram of the memory controller showing the arbiter, "
        "the read and write queues, and the DDR PHY interface.")


class _Client(object):
    def __init__(self):
        self.calls = 0
        self.model = "mock"
    def caption_result(self, image_bytes, fmt="png", prompt=""):
        self.calls += 1
        return {"text": GOOD, "finish_reason": "stop", "ok": True}


def test_extract_office_classifies_body_and_chrome(tmp_path):
    en = _mod("image_enrich")
    p = str(tmp_path / "spec.docx")
    _docx(p, _png(tag=b"D"), _png(tag=b"L"))
    cands = en.extract_office(p, "docx")
    by_part = dict((c["part"], c) for c in cands)
    assert by_part["word/media/diagram.png"]["ref"] == "body"
    assert by_part["word/media/logo.png"]["ref"] == "chrome"
    assert all(c["fmt"] == "png" and c["n_bytes"] > 0 and c["sha"].startswith("sha256:")
               for c in cands)


def test_enrich_captions_body_drops_chrome(tmp_path):
    en = _mod("image_enrich")
    src = tmp_path / "src"; out = tmp_path / "md"; assets = tmp_path / "assets"
    src.mkdir(); out.mkdir()
    _docx(str(src / "spec.docx"), _png(tag=b"D"), _png(tag=b"L"))
    from backend.ingest import doc_id
    did = doc_id("spec.docx")
    (out / (did + ".md")).write_text("---\ntitle: Spec\n---\n\nBody text.\n", encoding="utf-8")

    client = _Client()
    rc = en.main(["--src", str(src), "--out", str(out), "--assets", str(assets)],
                 client=client)
    assert rc == 0
    md = (out / (did + ".md")).read_text(encoding="utf-8")
    assert "memory controller" in md                 # body diagram caption inlined
    assert "## Figures (captioned images)" in md
    # exactly one model call (the body diagram); the header logo was gated out (chrome)
    assert client.calls == 1
    # records: diagram captured, logo dropped-chrome, nothing pending
    recs = [json.loads(l) for l in open(str(assets / "_figures.jsonl"))]
    by_part = dict((r["part"], r) for r in recs)
    assert by_part["word/media/diagram.png"]["outcome_kind"] == "OK"
    assert by_part["word/media/logo.png"]["kept"] is False
    assert by_part["word/media/logo.png"]["reason"] == "chrome"
    # the captioned asset was dumped
    assert os.path.isdir(str(assets / did))


def test_enrich_is_idempotent(tmp_path):
    en = _mod("image_enrich")
    src = tmp_path / "src"; out = tmp_path / "md"; assets = tmp_path / "assets"
    src.mkdir(); out.mkdir()
    _docx(str(src / "spec.docx"), _png(tag=b"D"), _png(tag=b"L"))
    from backend.ingest import doc_id
    did = doc_id("spec.docx")
    (out / (did + ".md")).write_text("Body text.\n", encoding="utf-8")

    en.main(["--src", str(src), "--out", str(out), "--assets", str(assets)], client=_Client())
    once = (out / (did + ".md")).read_text(encoding="utf-8")
    # second run: cache hit, section regenerated -> byte-identical markdown, no dupe section
    en.main(["--src", str(src), "--out", str(out), "--assets", str(assets)], client=_Client())
    twice = (out / (did + ".md")).read_text(encoding="utf-8")
    assert once == twice
    assert twice.count("## Figures (captioned images)") == 1
