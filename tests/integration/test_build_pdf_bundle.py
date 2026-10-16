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


def test_the_losslessness_adapter_delegates_to_the_validator(bpb):
    """The policy itself now lives in `backend.validate.pdf_coverage_report` and is
    unit-tested there — gate, buckets, denominators and all. What is left in the
    script is an ADAPTER, so what this file owns is the WIRING: the same inputs must
    reach the same verdict through it."""
    from backend.ingest import load_ingest_config
    from backend.validate import pdf_coverage_report
    cfg = load_ingest_config()
    src = "alpha beta gamma delta epsilon " * 40           # 200 tokens > min_tokens
    md = "# T\n\n" + src
    assert bpb._pdf_losslessness(src, md, "", "", cfg) == pdf_coverage_report(
        src, md, "", "", cfg.header_footer_min_frac, cfg.min_recall,
        cfg.min_tokens, cfg.content_min_recall)


def test_the_adapter_maps_each_config_field_to_the_right_threshold(bpb):
    """The refactor's own failure mode: four numbers in a row, and a swapped pair
    would be invisible in every happy-path assertion — the validator cannot catch it
    because by then the damage is already in the argument order. So each threshold
    is moved ALONE to a value that changes the answer.

    `min_tokens` is the readable one: the SAME half-lost document is real loss at a
    50-token floor and too small to judge at a 500-token one."""
    from backend.ingest import load_ingest_config
    kept, lost = "alpha beta gamma delta epsilon ", "zeta eta theta iota kappa "
    src, md = (kept + lost) * 40, "# T\n\n" + kept * 40   # 400 tokens, half dropped
    cfg = load_ingest_config()

    class _Cfg(object):
        def __init__(self, **kw):
            for f in ("header_footer_min_frac", "min_recall", "min_tokens",
                      "content_min_recall"):
                setattr(self, f, kw.get(f, getattr(cfg, f)))

    assert bpb._pdf_losslessness(src, md, "", "", cfg)[1] is True
    # below the token floor -> the ratio means nothing, so no loss is claimed
    assert bpb._pdf_losslessness(src, md, "", "", _Cfg(min_tokens=500))[1] is False
    # a recall floor this document clears -> not loss by the token signal
    assert bpb._pdf_losslessness(src, md, "", "", _Cfg(min_recall=0.4))[1] is False
    # a content floor this document clears -> not loss by the second signal
    assert bpb._pdf_losslessness(
        src, md, "", "", _Cfg(content_min_recall=0.1))[1] is False
    # The strip threshold reaches the GAP block rather than the verdict, so it needs
    # a paged document to bite on: a footer on 4 of 10 pages survives the configured
    # 0.5 and is BUCKETED as residual boilerplate; at 0.01 it is stripped before the
    # buckets ever see it, and those tokens leave the denominator entirely.
    paged = "\f".join("bodyline%d alpha beta gamma\n" % i
                      + ("kestrel confidential footer\n" if i < 4 else "")
                      for i in range(10))
    paged_md = "# T\n\n" + "".join("bodyline%d alpha beta gamma\n" % i
                                    for i in range(10))
    at_cfg = bpb._pdf_losslessness(paged, paged_md, "", "", cfg)[0]["gap"]
    aggressive = bpb._pdf_losslessness(
        paged, paged_md, "", "", _Cfg(header_footer_min_frac=0.01))[0]["gap"]
    assert at_cfg["residual_boiler"] == 12 and aggressive["residual_boiler"] == 0
    assert aggressive["n_source"] < at_cfg["n_source"]


def test_failure_report_is_lane_honest_and_failed(bpb):
    row = {"id": "d1", "rel": "a/spec.pdf", "src": "/abs/a/spec.pdf", "ext": "pdf"}
    rep = bpb._failure_report(row, "pdf", "boom", [{"code": "x"}])
    assert rep["status"] == "failed"
    assert rep["lane"] == "pdf"
    assert rep["losslessness"]["gate"] == "best-effort"    # never claims a pass
    assert rep["losslessness"]["error"] == "boom"
    assert rep["warnings"] == [{"code": "x"}]


def test_toolchain_warning_names_the_external_tools(bpb):
    # The PDF lane's provenance stamp (the soffice-version analogue): the warning
    # must NAME docling (+ docling-core) always, and poppler's pdftotext for the
    # pdf lane, versions best-effort — on a host where a tool/dist is absent the
    # name still appears, the version is simply omitted (never a crash, never a
    # silent skip).
    w = bpb._toolchain_warning("pdf")
    assert w["code"] == "pdf_toolchain"
    assert "docling" in w["detail"] and "docling-core" in w["detail"]
    assert "pdftotext" in w["detail"]
    # html lane: docling converts, but no poppler text layer is involved
    w2 = bpb._toolchain_warning("html")
    assert w2["code"] == "pdf_toolchain"
    assert "docling" in w2["detail"] and "pdftotext" not in w2["detail"]
    # stable across calls (the probes memoize; equality alone doesn't prove the
    # cache, but a changing stamp within one process would be a bug either way)
    assert bpb._toolchain_warning("pdf") == w


def test_every_failure_branch_withdraws_the_bundle_the_last_run_published():
    """The PDF lane's copy of the office lane's stale-bundle defect.

    `build_bundle` was fixed so a failed rebuild takes the PREVIOUS run's
    `document.md` out of publication — otherwise enrichment republishes a
    `knowledge.json` describing the stale body and `kb_lint` grades it clean, with
    every gate green over a document whose conversion failed. `build_pdf_bundle`
    had the same two branches and neither withdrew anything.

    This is a source-level invariant rather than an end-to-end run because the PDF
    lane needs docling (~2 GB, nightly ring only), and a check that silently skips
    on every ring is not a check. It is written against the AST, so it sees a NEW
    failure branch added later — which is the case that matters, and the one a
    fixture pinned to today's two branches would miss.
    """
    import ast

    src = open(os.path.join(REPO, "scripts", "build_pdf_bundle.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)

    def _calls(node):
        return set(
            n.func.attr for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute))

    def _returns_failed(node):
        for n in ast.walk(node):
            if not isinstance(n, ast.Return) or not isinstance(n.value, ast.Dict):
                continue
            for k, v in zip(n.value.keys, n.value.values):
                if (isinstance(k, ast.Str) and k.s == "status"
                        and isinstance(v, ast.Str) and v.s == "failed"):
                    return True
        return False

    # Every enclosing statement that returns a failed row must also withdraw.
    unguarded = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for stmt in ast.walk(fn):
            if not isinstance(stmt, (ast.If, ast.ExceptHandler)):
                continue
            if _returns_failed(stmt) and "_withdraw_published" not in _calls(stmt):
                unguarded.append("%s:%d" % (fn.name, stmt.lineno))
    assert not unguarded, (
        "these PDF failure branches return status=failed without withdrawing the "
        "previous run's artifacts, so a failed rebuild leaves a stale document.md "
        "that enrichment will republish: %s" % ", ".join(unguarded))

    # ...and a run that succeeds again supersedes what a failure withdrew, or the
    # marker outlives the failure and someone reads document.md.stale by mistake.
    assert "_clear_withdrawn" in src

    # One mechanism, shared with the office lane — not a second implementation that
    # can drift out of step with it.
    bpb_mod = _mod("build_pdf_bundle")
    for helper in ("_withdraw_published", "_clear_withdrawn", "_announce_withdrawn"):
        assert hasattr(bpb_mod.bb, helper)
