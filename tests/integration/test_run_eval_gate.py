"""
title: Integration — the eval's own gate gates
kind: tests
layer: backend
summary: A FAIL row in run_eval's table must make the process exit non-zero — including the stray-expectation-key guard, which printed FAIL and exited 0.
"""
# The eval is read by CI through ONE number: its exit code. A guard that prints
# FAIL and returns 0 is not a guard, and the stray-expectation-key guard exists
# precisely so a typo cannot silently switch a check off — so it failing to gate
# switched off the thing that stops checks being switched off.
import importlib.util
import json
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPECTATIONS = os.path.join(REPO, "evals", "expectations.json")


def _run_eval():
    spec = importlib.util.spec_from_file_location(
        "run_eval_under_test", os.path.join(REPO, "evals", "run_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _harness(tmp_path, expectations):
    """An eval run with no lanes: only the expectation table is under test.

    The corpus manifest is empty on purpose — this test is about the harness's
    verdict arithmetic, not about any document.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with open(str(corpus) + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"files": {}}, fh)
    exp_path = tmp_path / "expectations.json"
    with open(str(exp_path), "w", encoding="utf-8") as fh:
        json.dump(expectations, fh)
    return ["--no-lanes", "--corpus", str(corpus),
            "--bundles", str(tmp_path / "bundles"),
            "--text-out", str(tmp_path / "text"),
            "--expectations", str(exp_path)]


# `text/synth-flow.tcl` is routed unsupported, so this expectation holds with no
# artifacts on disk at all: the checker asks the ROUTER, not a bundle.
_CLEAN = {"text/synth-flow.tcl": {"kind": "unsupported"}}


def test_a_correct_expectation_table_still_exits_zero(tmp_path, capsys):
    """The other direction: the guard must not have become a blanket refusal."""
    rc = _run_eval().main(_harness(tmp_path, _CLEAN))
    out = capsys.readouterr().out
    assert "0 fail" in out
    assert rc == 0


def test_a_typod_expectation_key_makes_the_eval_exit_non_zero(tmp_path, capsys):
    """`tokens_recal` is not `token_recall_min`. A key no checker reads proves
    nothing, so the row is a FAIL — and the FAIL has to reach the exit code, which
    is the only thing CI looks at."""
    typo = {"text/synth-flow.tcl": {"kind": "unsupported", "tokens_recal": 1.0}}
    rc = _run_eval().main(_harness(tmp_path, typo))
    out = capsys.readouterr().out
    assert "tokens_recal" in out and "1 fail" in out
    assert rc == 1, "the eval printed FAIL and exited %r" % rc


def test_a_probe_written_for_the_wrong_lane_is_a_typo_too(tmp_path, capsys):
    """`check_text` never opens a report.json, so `has_toc` on a text expectation
    is read by nobody. A key set that was not per-kind called that fine."""
    misplaced = {"text/design-notes.md": {"kind": "text", "lane": "text",
                                          "has_toc": True}}
    rc = _run_eval().main(_harness(tmp_path, misplaced))
    out = capsys.readouterr().out
    assert "has_toc" in out
    assert rc == 1


def test_every_row_of_the_printed_table_agrees_with_the_exit_code(tmp_path, capsys):
    """The invariant behind both: the number of FAIL rows decides the code. A
    checker failure and a guard failure must count the same."""
    mod = _run_eval()
    both = {"text/synth-flow.tcl": {"kind": "unsupported", "tokens_recal": 1.0},
            "office/kestrel-readme.docx": {"kind": "bundle", "lane": "office"}}
    rc = mod.main(_harness(tmp_path, both))
    out = capsys.readouterr().out
    assert "2 fail" in out                      # a stray key AND a missing bundle
    assert rc == 1
    assert out.count("\nFAIL ") == 2


def test_the_committed_expectations_are_clean_under_the_per_kind_rule():
    """The tightened guard must accept the real table — a gate that starts
    rejecting the corpus it grades is worse than the hole it closed."""
    mod = _run_eval()
    with open(EXPECTATIONS, encoding="utf-8") as fh:
        expectations = json.load(fh)
    stray = dict((rel, mod.unknown_keys(exp)) for rel, exp in expectations.items()
                 if mod.unknown_keys(exp))
    assert stray == {}, "evals/expectations.json carries keys no checker reads"


# ===================================== the expected-fail marker (roadmap M0)
#
# The nightly ring has been red for months over two PDF expectations that encode
# TRUTHFUL behaviour nobody wants yet. A permanently-red gate is not a gate: nobody
# reads the number, so the day a real regression lands it changes nothing. The
# alternatives were to delete the expectations (which stops measuring the thing) or
# to encode the desired-but-false answer (which makes the eval lie).
#
# `xfail: true` is the third option: keep grading it, keep printing what it really
# does, and stop gating on it — with the pressure kept on by making an unexpected
# PASS a FAILURE, so a fixed document cannot sit under a stale marker.

def _xfail_exp(**extra):
    """A bundle expectation that cannot hold — no bundle exists on disk."""
    exp = {"kind": "bundle", "lane": "office"}
    exp.update(extra)
    return {"office/kestrel-readme.docx": exp}


def test_an_expected_failure_is_reported_and_does_not_gate(tmp_path, capsys):
    """The whole point. The row still prints what the document really did — the
    eval does not stop measuring it — but the exit code stops carrying it."""
    rc = _run_eval().main(_harness(tmp_path, _xfail_exp(
        xfail=True, _note="docling drops the knife-edge probe; real fix is M1")))
    out = capsys.readouterr().out
    assert "\nXFAIL" in out, out
    assert "0 fail" in out
    assert "1 xfail" in out
    assert rc == 0


def test_an_unexpected_pass_gates(tmp_path, capsys):
    """The pressure valve. Without this, a marker outlives the defect it describes
    and the expectation silently stops asserting anything — the same vacuous pass
    the `n_source_tokens` floor and the stray-key guard exist to prevent."""
    passing = {"text/synth-flow.tcl": {"kind": "unsupported", "xfail": True,
                                       "_note": "stale marker"}}
    rc = _run_eval().main(_harness(tmp_path, passing))
    out = capsys.readouterr().out
    assert "\nXPASS" in out, out
    assert "1 fail" in out
    assert rc == 1


def test_an_expected_failure_must_say_why(tmp_path, capsys):
    """An `xfail` with no `_note` is a silenced check. The note is what makes the
    marker reviewable — and what a later reader needs to decide whether it still
    applies."""
    rc = _run_eval().main(_harness(tmp_path, _xfail_exp(xfail=True)))
    out = capsys.readouterr().out
    assert "_note" in out
    assert "1 fail" in out
    assert rc == 1


def test_a_typod_key_is_still_a_failure_under_an_expected_failure(tmp_path, capsys):
    """`xfail` says the DOCUMENT behaves in an undesired way. It says nothing about
    the EXPECTATION being well formed, so the guard that catches a check nobody
    reads must still gate — otherwise a marker would switch off the thing that
    stops checks being switched off."""
    rc = _run_eval().main(_harness(tmp_path, _xfail_exp(
        xfail=True, _note="why", tokens_recal=1.0)))
    out = capsys.readouterr().out
    assert "tokens_recal" in out and "1 fail" in out
    assert "XFAIL" not in out
    assert rc == 1


def test_a_document_that_was_never_generated_is_skipped_not_expected_to_fail(
        tmp_path, capsys):
    """A fixture the corpus could not build has produced no evidence either way —
    the scanned-PDF one skips whenever Pillow is absent. Reporting XFAIL there would
    claim a measurement nobody took, and would hide the missing fixture behind a
    marker that says the opposite."""
    rel = "pdf/kestrel-clock-spec-scan.pdf"
    exp = {rel: {"kind": "bundle", "lane": "pdf", "xfail": True, "_note": "why"}}
    args = _harness(tmp_path, exp)
    manifest = os.path.join(os.path.dirname(args[args.index("--corpus") + 1]),
                            os.path.basename(args[args.index("--corpus") + 1])
                            + ".manifest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"files": {rel: {"kind": "skipped",
                                   "reason": "scan-tools-unavailable"}}}, fh)
    rc = _run_eval().main(args)
    out = capsys.readouterr().out
    assert "\nSKIP" in out and "scan-tools-unavailable" in out
    assert "XFAIL" not in out
    assert rc == 0


# ============================== the gap block is CHECKED, not merely published
#
# `explain_gap` was built, exported from `backend.ingest`, covered by its own unit
# tests — and called by nothing, for two milestones. A field nobody reads is the same
# shape: the report would carry `absent: 0` and no expectation would notice the day it
# stopped being 0. `gap_absent_max` is the probe that makes it a measurement.

def _gap_harness(tmp_path, absent, with_block=True):
    """A bundle on disk whose report carries (or omits) a gap block."""
    import os
    bundles = tmp_path / "bundles"
    d = bundles / "deadbeefdeadbeef"
    os.makedirs(str(d))
    loss = {"method": "pdf-text-coverage", "gate": "best-effort",
            "token_recall": 0.99}
    if with_block:
        loss["gap"] = {"n_source": 100, "covered": 100 - absent, "fused": 0,
                       "numeric": 0, "image_text": 0, "residual_boiler": 0,
                       "short": 0, "absent": absent}
    with open(str(d / "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": "deadbeefdeadbeef", "lane": "pdf",
                   "source_relpath": "pdf/x.pdf", "status": "ok",
                   "losslessness": loss, "warnings": []}, fh)
    return bundles


def test_an_unexplained_gap_over_the_ceiling_fails(tmp_path, capsys):
    from backend.ingest import doc_id
    rel = "pdf/x.pdf"
    exp = {rel: {"kind": "bundle", "lane": "pdf", "gap_absent_max": 0}}
    args = _harness(tmp_path, exp)
    bundles = _gap_harness(tmp_path, absent=7)
    os.rename(str(bundles / "deadbeefdeadbeef"), str(bundles / doc_id(rel)))
    rc = _run_eval().main(args)
    out = capsys.readouterr().out
    assert "gap.absent" in out and "1 fail" in out
    assert rc == 1


def test_a_fully_explained_gap_passes(tmp_path, capsys):
    """Recall below 1.0 with `absent: 0` is a document whose whole shortfall is page
    numbers and running furniture — nothing for a person to chase."""
    from backend.ingest import doc_id
    rel = "pdf/x.pdf"
    exp = {rel: {"kind": "bundle", "lane": "pdf", "gap_absent_max": 0}}
    args = _harness(tmp_path, exp)
    bundles = _gap_harness(tmp_path, absent=0)
    os.rename(str(bundles / "deadbeefdeadbeef"), str(bundles / doc_id(rel)))
    rc = _run_eval().main(args)
    out = capsys.readouterr().out
    assert "0 fail" in out, out
    assert rc == 0


def test_asking_about_a_gap_a_report_does_not_carry_is_a_failure(tmp_path, capsys):
    """The vacuity guard. An OCR-path report has no gap block at all — there is no
    independent text layer to decompose — so an expectation asking about one is
    mis-set, and answering it with silence would be the passing-over-nothing this
    harness exists to refuse."""
    from backend.ingest import doc_id
    rel = "pdf/x.pdf"
    exp = {rel: {"kind": "bundle", "lane": "pdf", "gap_absent_max": 0}}
    args = _harness(tmp_path, exp)
    bundles = _gap_harness(tmp_path, absent=0, with_block=False)
    os.rename(str(bundles / "deadbeefdeadbeef"), str(bundles / doc_id(rel)))
    rc = _run_eval().main(args)
    out = capsys.readouterr().out
    assert "gap" in out and "1 fail" in out
    assert rc == 1


# ================================ the figure-text debt, gated (roadmap M1)
#
# `figure_text_tokens` is the one loss class the TEXT gates cannot recover: words
# the PDF holds inside a figure region, excluded from the body ground truth because
# they are figure content rather than lost body text. It is the number the VLM
# caption stage exists to burn down — and until `office/kestrel-clocktree.pptx`
# landed it was 0 on every document in the corpus, because every corpus figure was a
# decorative colour grid.
#
# A debt that is published but never asserted is a debt nobody notices paying off,
# or quietly growing. So it gets a probe, with the same two-sided argument the gap
# ceiling uses: a MISSING field is a failure, never a silent pass.

def _figtext_harness(tmp_path, tokens, with_field=True):
    """A bundle whose report carries (or omits) figure_text_tokens."""
    import os
    bundles = tmp_path / "bundles"
    d = bundles / "deadbeefdeadbeef"
    os.makedirs(str(d))
    loss = {"method": "pdf-text-coverage", "gate": "best-effort", "token_recall": 1.0}
    if with_field:
        loss["figure_text_tokens"] = tokens
    with open(str(d / "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": "deadbeefdeadbeef", "lane": "pdf",
                   "source_relpath": "pdf/x.pdf", "status": "ok",
                   "losslessness": loss, "warnings": []}, fh)
    return bundles


def _place(tmp_path, bundles, rel):
    from backend.ingest import doc_id
    os.rename(str(bundles / "deadbeefdeadbeef"), str(bundles / doc_id(rel)))


def test_a_figure_text_debt_at_or_above_the_floor_passes(tmp_path, capsys):
    rel = "pdf/x.pdf"
    args = _harness(tmp_path, {rel: {"kind": "bundle", "lane": "pdf",
                                     "figure_text_tokens_min": 19}})
    _place(tmp_path, _figtext_harness(tmp_path, 19), rel)
    assert _run_eval().main(args) == 0


def test_a_figure_text_debt_that_fell_below_the_floor_fails(tmp_path, capsys):
    """A FALL is the interesting direction, which is why this is a floor and not a
    ceiling. These tokens are recovered by an independent text-layer probe, so the
    count dropping means the probe stopped seeing a figure it used to see — the
    exclusion silently narrowed and those words are now being judged as body text
    the converter lost. Nothing else in the report would say so."""
    rel = "pdf/x.pdf"
    args = _harness(tmp_path, {rel: {"kind": "bundle", "lane": "pdf",
                                     "figure_text_tokens_min": 19}})
    _place(tmp_path, _figtext_harness(tmp_path, 4), rel)
    rc = _run_eval().main(args)
    out = capsys.readouterr().out
    assert rc != 0 and "figure_text_tokens" in out


def test_asking_about_a_figure_debt_the_report_does_not_carry_is_a_failure(
        tmp_path, capsys):
    """The OCR path publishes no figure-text count at all — there is no independent
    text layer to recover one from. An expectation asking about it there is mis-set,
    and answering a mis-set question with silence is the passing-over-nothing this
    harness exists to refuse."""
    rel = "pdf/x.pdf"
    args = _harness(tmp_path, {rel: {"kind": "bundle", "lane": "pdf",
                                     "figure_text_tokens_min": 1}})
    _place(tmp_path, _figtext_harness(tmp_path, 0, with_field=False), rel)
    rc = _run_eval().main(args)
    assert rc != 0 and "figure_text_tokens" in capsys.readouterr().out
