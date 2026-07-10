"""
title: Integration — build_pdf_bundle mirrors the office bundle gates for the docling lane
kind: tests
layer: backend
summary: The PDF writer's docling-free core — planning, measured best-effort losslessness, failure reports.
"""
# Integration (not unit): loads the script module (which wires the sibling lane
# scripts + config). The docling conversion itself is NOT exercised here — it needs
# the 3.12 venv — but everything measurable without a model is.
import importlib.util
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def bpb():
    return _mod("build_pdf_bundle")


def test_plan_rows_have_stable_ids_and_ext(bpb):
    rows = bpb.plan([("/abs/a/spec.pdf", "a/spec.pdf"),
                     ("/abs/b/page.html", "b/page.html")], "/out")
    assert [r["ext"] for r in rows] == ["pdf", "html"]
    assert all(r["id"] and r["rel"] and r["src"] for r in rows)
    # same rel -> same doc_id (the cross-lane collation contract)
    again = bpb.plan([("/other/mount/spec.pdf", "a/spec.pdf")], "/x")
    assert again[0]["id"] == rows[0]["id"]


def test_losslessness_block_is_measured_and_never_a_pass_gate(bpb):
    from backend.ingest import load_ingest_config
    cfg = load_ingest_config()
    src = "alpha beta gamma delta epsilon " * 40          # 200 tokens, > min_tokens
    md = "# T\n\n" + src
    loss, real = bpb._pdf_losslessness(src, md, "", "", cfg)
    assert loss["method"] == "pdf-text-coverage"
    assert loss["token_recall"] == 1.0
    assert loss["gate"] == "best-effort"                   # never "pass", even at 1.0
    assert real is False and loss["missing_tokens"] == []


def test_losslessness_flags_real_content_loss(bpb):
    from backend.ingest import load_ingest_config
    cfg = load_ingest_config()
    kept = "alpha beta gamma delta epsilon "
    lost = "zeta eta theta iota kappa "
    src = (kept + lost) * 40                               # half the content dropped
    md = "# T\n\n" + kept * 40
    loss, real = bpb._pdf_losslessness(src, md, "", "", cfg)
    assert real is True                                     # explained-gap: BOTH low
    assert loss["token_recall"] < cfg.min_recall
    assert loss["missing_tokens"]                           # names what went missing
    assert loss["gate"] == "best-effort"


def test_losslessness_excludes_furniture_and_image_text(bpb):
    # Furniture (running headers) and figure-region text are NOT body loss: with the
    # excludes supplied, the same "missing" tokens no longer count against recall.
    from backend.ingest import load_ingest_config
    cfg = load_ingest_config()
    body = "alpha beta gamma delta epsilon " * 40
    furniture = "CONFIDENTIAL page footer " * 10
    src = body + furniture
    md = "# T\n\n" + body
    loss_blind, _ = bpb._pdf_losslessness(src, md, "", "", cfg)
    loss_fair, real = bpb._pdf_losslessness(src, md, furniture, "", cfg)
    assert loss_fair["token_recall"] > loss_blind["token_recall"]
    assert loss_fair["token_recall"] == 1.0 and real is False


def test_losslessness_surfaces_figure_text_debt(bpb):
    # Text living inside figure regions is excluded from the body metric but must be
    # VISIBLE: it is the one loss class only the VLM caption stage can recover.
    from backend.ingest import load_ingest_config
    cfg = load_ingest_config()
    body = "alpha beta gamma delta epsilon " * 40
    fig_text = "state machine IDLE ACTIVE RESET arrows"
    loss, real = bpb._pdf_losslessness(body + fig_text, "# T\n\n" + body,
                                       "", fig_text, cfg)
    assert loss["figure_text_tokens"] == len(fig_text.split())
    assert loss["token_recall"] == 1.0 and real is False   # excluded, not penalized


def test_failure_report_is_lane_honest_and_failed(bpb):
    row = {"id": "d1", "rel": "a/spec.pdf", "src": "/abs/a/spec.pdf", "ext": "pdf"}
    rep = bpb._failure_report(row, "pdf", "boom", [{"code": "x"}])
    assert rep["status"] == "failed"
    assert rep["lane"] == "pdf"
    assert rep["losslessness"]["gate"] == "best-effort"    # never claims a pass
    assert rep["losslessness"]["error"] == "boom"
    assert rep["warnings"] == [{"code": "x"}]
