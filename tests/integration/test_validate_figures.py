"""
title: Integration — the second-pass figure validator MEASURES losslessness
kind: tests
layer: backend
summary: Independent byte-magic ground truth from the ORIGINAL source; proves it catches unaccounted/orphan/pending/mis-gated figures.
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


def _png(tag=b"A"):
    ihdr = struct.pack(">II", 40, 30) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 8 + tag


def _rels(*pairs):
    r = "".join('<Relationship Id="rId%d" Type="%s" Target="%s"/>' % (i, t, tgt)
                for i, (t, tgt) in enumerate(pairs, 1))
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + r + "</Relationships>")


def _docx(path, extra_members=None):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    members = {
        "word/document.xml": "<w:document %s><w:body/></w:document>" % W,
        "word/_rels/document.xml.rels": _rels((IMG, "media/diagram.png")),
        "word/_rels/header1.xml.rels": _rels((IMG, "media/logo.png")),
        "word/media/diagram.png": _png(b"D"),
        "word/media/logo.png": _png(b"L"),
    }
    members.update(extra_members or {})
    with zipfile.ZipFile(path, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
    return path


def _run_enrich_and_validate(tmp_path, extra=None, client=None, mutate=None):
    en = _mod("image_enrich")
    vf = _mod("validate_figures")
    from backend.ingest import doc_id
    src = tmp_path / "src"; out = tmp_path / "md"; assets = tmp_path / "assets"
    src.mkdir(); out.mkdir()
    _docx(str(src / "spec.docx"), extra)
    did = doc_id("spec.docx")
    (out / (did + ".md")).write_text("Body.\n", encoding="utf-8")

    class _Client(object):
        model = "mock"
        def __init__(self): self.calls = 0
        def caption_result(self, image_bytes, fmt="png", prompt=""):
            self.calls += 1
            return {"text": ("This is a detailed block diagram of the memory controller "
                             "with the arbiter, queues and DDR PHY interface."),
                    "finish_reason": "stop", "ok": True}
    en.main(["--src", str(src), "--out", str(out), "--assets", str(assets)],
            client=client or _Client())
    if mutate:
        mutate(str(out / (did + ".md")), str(assets), did)
    rc = vf.main(["--src", str(src), "--out", str(out), "--assets", str(assets)])
    return rc, vf, str(assets), did


def test_healthy_corpus_is_lossless_exit_0(tmp_path):
    rc, vf, assets, did = _run_enrich_and_validate(tmp_path)
    assert rc == 0                                    # diagram captured, logo dropped-chrome


def test_unaccounted_image_fails(tmp_path):
    # an image the extractor's /media/ glob MISSES (stored under embeddings/) has no record
    # -> the byte-magic ground truth still sees it -> unaccounted -> FAIL.
    extra = {"word/embeddings/oleObject1.png": _png(b"Z")}
    rc, vf, assets, did = _run_enrich_and_validate(tmp_path, extra=extra)
    assert rc != 0


def test_missing_asset_orphan_fails(tmp_path):
    def _delete_asset(md_path, assets, did):
        import glob as _g
        for f in _g.glob(os.path.join(assets, did, "*")):
            os.remove(f)                               # captured figure's asset removed
    rc, _, _, _ = _run_enrich_and_validate(tmp_path, mutate=_delete_asset)
    assert rc != 0


def test_pending_is_incomplete_nonzero(tmp_path):
    class _Down(object):
        model = "mock"
        def caption_result(self, image_bytes, fmt="png", prompt=""):
            return {"text": "", "finish_reason": "", "ok": False}   # VLM down -> pending
    rc, _, _, _ = _run_enrich_and_validate(tmp_path, client=_Down())
    assert rc != 0


def test_render_failed_is_loss_not_lossless(tmp_path):
    # a metafile that could not be rendered had its pixel-text (maybe a formula) LOST -> the
    # validator must NOT report lossless (it was previously counted as a clean 'kept').
    def _tamper(md_path, assets, did):
        cov = os.path.join(assets, "_figures.jsonl")
        recs = [json.loads(l) for l in open(cov)]
        for r in recs:
            if r["part"].endswith("diagram.png"):
                r["outcome_kind"] = "RENDER_FAILED"; r["reason"] = "render_failed"
        open(cov, "w").write("".join(json.dumps(r) + "\n" for r in recs))
    rc, _, _, _ = _run_enrich_and_validate(tmp_path, mutate=_tamper)
    assert rc != 0


def test_misgated_formula_fails(tmp_path):
    # tamper a record: mark the BODY diagram dropped as 'chrome'. The validator re-derives
    # ref from the source (body) and must FAIL the non-converging drop.
    def _tamper(md_path, assets, did):
        cov = os.path.join(assets, "_figures.jsonl")
        recs = [json.loads(l) for l in open(cov)]
        for r in recs:
            if r["part"].endswith("diagram.png"):
                r["kept"] = False; r["reason"] = "chrome"; r["outcome_kind"] = ""
        open(cov, "w").write("".join(json.dumps(r) + "\n" for r in recs))
    rc, _, _, _ = _run_enrich_and_validate(tmp_path, mutate=_tamper)
    assert rc != 0
