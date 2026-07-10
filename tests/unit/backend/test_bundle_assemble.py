"""
title: Unit — backend.bundle assemble_bundle
kind: tests
layer: backend
summary: The output-contract assembler — pure composition into document.md + structure.json + report.json.
"""
import hashlib
from collections import OrderedDict

import pytest
from backend.bundle import assemble_bundle

pytestmark = pytest.mark.unit

SRC = "Overview The CTRL register holds status bits Details more prose here"
BODY = ("# Overview\n\nThe CTRL register holds status bits.\n\n"
        "## Details\n\nmore prose here\n")


def _office(**kw):
    base = dict(doc_id="d1", source_relpath="specs/radar.docx", source_format="docx",
                lane="office", source_text=SRC, body_md=BODY)
    base.update(kw)
    return assemble_bundle(**base)


def _strip_fm(md):
    assert md.startswith("---\n")
    return md[md.index("\n---\n") + 5:]


def test_office_bundle_is_lossless_and_well_formed():
    b = _office()
    rep, st = b["report"], b["structure"]
    assert rep["losslessness"]["method"] == "ooxml-ground-truth"
    assert rep["losslessness"]["token_recall"] == 1.0
    assert rep["losslessness"]["gate"] == "pass"
    assert rep["status"] == "ok"
    assert st["doc_id"] == "d1" and st["lane"] == "office"
    assert [n["title"] for n in st["outline"]] == ["Overview"]
    assert [c["title"] for c in st["outline"][0]["children"]] == ["Details"]


def test_document_md_is_frontmatter_plus_body_verbatim():
    b = _office()
    md = b["document_md"]
    assert md.startswith("---\n")
    assert _strip_fm(md) == "\n" + BODY          # body is byte-for-byte preserved
    assert 'doc_id: "d1"' in md
    assert 'lane: "office"' in md
    assert 'lossless: "true"' in md
    assert 'source_relpath: "specs/radar.docx"' in md
    assert 'structure: "structure.json"' in md


def test_gate_scores_body_not_frontmatter():
    # Front matter must never supply tokens the body lost: the recall gate runs on
    # body_md alone. The discriminating case: "radar" appears in the front matter
    # (source_relpath = "specs/radar.docx") but NOT in the body. If the gate scored
    # frontmatter+body it would PASS; scoring the body alone it must FAIL.
    assert "radar" not in BODY
    b = _office(source_text=SRC + " radar")
    assert b["report"]["losslessness"]["gate"] == "fail"     # body-only scoring
    assert b["report"]["status"] == "failed"
    # sanity: with no extra token the same doc is lossless
    assert _office()["report"]["losslessness"]["token_recall"] == 1.0


def test_markdown_sha256_is_body_hash_in_report_and_frontmatter():
    b = _office()
    want = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
    assert b["report"]["markdown_sha256"] == want
    assert ('markdown_sha256: "%s"' % want) in b["document_md"]


def test_structure_summary_depth_and_largest():
    b = _office()
    summ = b["report"]["structure"]
    top = b["structure"]["outline"]
    assert summ["max_depth"] == 2                # H1 + H2
    # largest_section_tokens is the biggest subtree in the whole outline (here the H1)
    assert summ["largest_section_tokens"] == max(n["subtree_tokens"] for n in top)
    assert summ["largest_section_tokens"] > 0
    assert summ["has_toc"] is False


def test_structure_summary_carries_a_passing_coverage_gate():
    # Every report gets the outline-coverage block; a clean document covers every
    # content line, so the gate passes and status stays ok.
    b = _office()
    cov = b["report"]["structure"]["coverage"]
    assert cov["gate"] == "pass"
    assert cov["uncovered_lines"] == 0
    assert cov["covered_lines"] == cov["content_lines"]
    assert b["report"]["status"] == "ok"


def test_outline_loss_degrades_status_and_is_warned(monkeypatch):
    # THE guardrail: if the outline builder ever drops a region again (any future
    # content_start-class bug), the report must degrade and say where — never pass
    # silently. The fixed builder cannot under-cover, so we simulate a buggy one at
    # the assembler's seam.
    import backend.bundle._assemble as asm

    def buggy_outline(text, token_count=None):
        # build a real outline, then chop off its first top-level node
        from backend.sections import document_outline as real
        full = real(text, token_count=token_count)
        full["outline"] = full["outline"][1:]
        return full
    monkeypatch.setattr(asm, "document_outline", buggy_outline)

    b = _office()
    rep = b["report"]
    cov = rep["structure"]["coverage"]
    assert cov["gate"] == "degraded"
    assert cov["uncovered_lines"] > 0
    assert cov["first_uncovered"]                     # names the lost lines
    assert rep["status"] == "degraded"                # folded into status
    assert any(w["code"] == "outline_uncovered_content" for w in rep["warnings"])


def test_line_spans_are_body_relative_not_document_md():
    # The contract: line indices address the markdown BODY, not document.md (which
    # prepends a variable-length front-matter block). Verify the outline index lands on
    # the heading in body_md, and that the SAME index in document.md does not.
    b = _office()
    body_lines = BODY.split("\n")
    doc_lines = b["document_md"].split("\n")
    a = b["structure"]["outline"][0]["line_span"][0]
    assert body_lines[a].startswith("# Overview")     # body-relative: hits the heading
    assert doc_lines[a] != body_lines[a]              # document.md is offset by front matter
    assert doc_lines[a].strip() in ("---", "") or ":" in doc_lines[a]   # still inside front matter


def test_non_office_bundle_never_emits_a_pass_gate():
    # Even if a caller hands the assembler a pdf losslessness dict claiming gate=pass,
    # the emitted bundle must downgrade it and mark the document not-provably-lossless.
    b = assemble_bundle(doc_id="p", source_relpath="paper.pdf", source_format="pdf",
                        lane="pdf", source_text="anything", body_md="# P\n\nbody\n",
                        losslessness={"method": "pdf-text-coverage", "coverage": 0.2,
                                      "gate": "pass"})
    assert b["report"]["losslessness"]["gate"] == "best-effort"
    assert 'lossless: "false"' in b["document_md"]


def test_source_meta_flattened_with_prefix():
    meta = OrderedDict([("title", "Radar Spec"), ("author", "A. Engineer")])
    b = _office(source_meta=meta)
    md = b["document_md"]
    assert 'source_title: "Radar Spec"' in md
    assert 'source_author: "A. Engineer"' in md


def test_token_count_threads_into_outline_and_report():
    tc = lambda s: max(1, len(s.split()))
    b = _office(token_count=tc, token_model="whitespace-v1")
    assert b["structure"]["token_model"] == "whitespace-v1"
    # total_tokens equals the tokenizer applied line-by-line
    want = sum(tc(ln) for ln in BODY.split("\n"))
    assert b["structure"]["total_tokens"] == want
    assert b["report"]["content"]["tokens"] == want


def test_images_block_accounting_and_gate():
    body = ("# Figs\n\n![diagram](images/img-0001.png)\n\n"
            "![chart](images/img-0002.png)\n")
    b = assemble_bundle(doc_id="d", source_relpath="x.docx", source_format="docx",
                        lane="office", source_text="Figs", body_md=body,
                        extras={"images_extracted": 1})
    im = b["report"]["images"]
    assert im["referenced"] == 2
    assert im["extracted"] == 1
    assert im["gate"] == "degraded"              # a reference with no extracted file
    # a degraded image gate degrades an otherwise-ok doc, but never the losslessness gate
    assert b["report"]["status"] == "degraded"
    assert b["report"]["losslessness"]["gate"] == "pass"


def test_images_block_clean_case_passes():
    body = "# Figs\n\n![a](images/x.png)\n"
    b = assemble_bundle(doc_id="d", source_relpath="x.docx", source_format="docx",
                        lane="office", source_text="Figs", body_md=body,
                        extras={"images_extracted": 1, "image_files": 1})
    im = b["report"]["images"]
    assert im["referenced"] == 1 and im["extracted"] == 1 and im["gate"] == "pass"
    assert b["report"]["status"] == "ok"


def test_captions_block_built_pending():
    body = "# Figs\n\n![a](images/x.png)\n"
    b = assemble_bundle(doc_id="d", source_relpath="x.docx", source_format="docx",
                        lane="office", source_text="Figs", body_md=body,
                        extras={"images_extracted": 1, "image_files": 1,
                                "captions_enabled": True})
    cap = b["report"]["captions"]
    assert cap["enabled"] and cap["expected"] == 1 and cap["pending"] == 1
    assert cap["gate"] == "pending"              # captions enabled, none run yet


def test_non_office_lane_best_effort_losslessness():
    loss = {"method": "pdf-text-coverage", "coverage": 0.96, "ocr_used": False}
    b = assemble_bundle(doc_id="p", source_relpath="paper.pdf", source_format="pdf",
                        lane="pdf", source_text="", body_md="# Paper\n\nbody text\n",
                        losslessness=loss)
    assert b["report"]["losslessness"]["method"] == "pdf-text-coverage"
    assert b["report"]["losslessness"]["gate"] == "best-effort"
    assert 'lossless: "false"' in b["document_md"]      # never claims provable losslessness
    assert b["report"]["status"] == "ok"


def test_warnings_and_timing_passed_through():
    warns = [{"code": "libreoffice_preconvert", "detail": "odt -> docx"}]
    b = _office(warnings=warns, timing_ms={"convert": 10, "validate": 2},
                converter="doc2md-ooxml/9.9", source_sha256="abc123")
    assert b["report"]["warnings"] == warns
    assert b["report"]["timing_ms"] == {"convert": 10, "validate": 2}
    assert b["report"]["converter"] == "doc2md-ooxml/9.9"
    assert b["report"]["source_sha256"] == "abc123"
    assert 'source_sha256: "abc123"' in b["document_md"]
