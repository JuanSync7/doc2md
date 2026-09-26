"""
title: Unit — the PDF/HTML best-effort coverage block
kind: tests
layer: backend
summary: The off-lane losslessness policy as a pure function — buckets that partition the source, a gate that can never read "pass", and thresholds that must be supplied rather than defaulted.
"""
# This block is the ONLY losslessness statement a non-office document gets. It used
# to live in `scripts/build_pdf_bundle.py`, where it was domain policy inside a
# transport module and reachable only by loading a script by path. The move into
# `backend.validate` is what makes these tests unit tests at all.
#
# Two properties here are load-bearing and easy to lose in a refactor:
#
#   * the gate is structurally incapable of saying "pass" — no ground-truth semantic
#     tree exists for this lane, so a pass is not claimable, at any recall;
#   * the function takes the RAW page-delimited extraction, not a de-boilerplated
#     one, because `explain_gap` applies the strip itself in order to see the
#     sub-threshold repeats a running footer leaves behind.
import inspect

import pytest

from backend.validate import pdf_coverage_report
from backend.validate import _pdfcoverage
from backend.ingest import strip_running_lines

pytestmark = pytest.mark.unit

# The policy numbers. Passed explicitly on every call for the same reason the
# function requires them: a default in the validator would be a SECOND copy of the
# config, free to drift from the one the lane actually runs on.
FRAC, MIN_RECALL, MIN_TOKENS, CONTENT_MIN = 0.5, 0.8, 50, 0.95


def _report(src, md, furniture="", image_text=""):
    return pdf_coverage_report(src, md, furniture, image_text,
                               FRAC, MIN_RECALL, MIN_TOKENS, CONTENT_MIN)


def test_the_module_declares_its_public_surface_and_it_does_not_drift():
    """CONVENTIONS §1: ``__all__`` is the machine-checkable public API."""
    assert _pdfcoverage.__all__ == ["pdf_coverage_report"]


def test_the_thresholds_have_no_defaults():
    """A defaulted threshold is a second copy of the policy, free to drift silently
    from `ingest.toml`. Requiring them means a caller cannot forget to pass config."""
    sig = inspect.getargspec(pdf_coverage_report) if not hasattr(
        inspect, "signature") else inspect.signature(pdf_coverage_report)
    params = list(sig.parameters.values()) if hasattr(sig, "parameters") else None
    if params is not None:
        defaulted = [p.name for p in params if p.default is not inspect.Parameter.empty]
        assert defaulted == [], "policy thresholds must be supplied, not defaulted"


def test_the_gate_is_best_effort_even_at_perfect_recall():
    src = "alpha beta gamma delta epsilon " * 40           # 200 tokens > min_tokens
    loss, real = _report(src, "# T\n\n" + src)
    assert loss["method"] == "pdf-text-coverage"
    assert loss["token_recall"] == 1.0
    assert loss["gate"] == "best-effort"                   # never "pass", ever
    assert real is False and loss["missing_tokens"] == []


def test_no_input_can_make_the_gate_say_pass():
    """The lane-asymmetry contract has to hold STRUCTURALLY, not on inspection of the
    happy path: a perfect document, an empty one and a catastrophic one all agree."""
    perfect = "alpha beta gamma " * 40
    for src, md in ((perfect, perfect), ("", ""), (perfect, ""), ("", perfect)):
        loss, _ = _report(src, md)
        assert loss["gate"] == "best-effort"


def test_real_loss_is_flagged_by_both_signals_together():
    kept, lost = "alpha beta gamma delta epsilon ", "zeta eta theta iota kappa "
    src, md = (kept + lost) * 40, "# T\n\n" + kept * 40
    loss, real = _report(src, md)
    assert real is True                                    # explained-gap: BOTH low
    assert loss["token_recall"] < MIN_RECALL
    assert loss["missing_tokens"]                          # names what went missing
    assert loss["gate"] == "best-effort"


def test_furniture_and_figure_text_leave_the_body_ground_truth():
    body = "alpha beta gamma delta epsilon " * 40
    furniture = "kestrel confidential draft"
    image_text = "figure axis label"
    loss, real = _report(body + " " + furniture + " " + image_text,
                         "# T\n\n" + body, furniture, image_text)
    assert loss["token_recall"] == 1.0                     # neither is body loss
    assert real is False
    assert loss["figure_text_tokens"] == 3                 # but the debt is VISIBLE


def test_the_buckets_partition_the_source():
    """`covered + fused + numeric + image_text + residual_boiler + short + absent`
    is the whole source. A reader can subtract; nothing hides in a rounding gap.
    Asserted over a NON-EMPTY denominator, because 0 == 0 would prove nothing."""
    body = "alpha beta gamma 1234 delta\n"
    src = ("Page 1 of 2\n" + body) + "\f" + ("Page 2 of 2\n" + body)
    loss, _ = _report(src, "# T\n\nalpha beta gamma delta\n")
    g = loss["gap"]
    assert g["n_source"] > 0
    assert (g["covered"] + g["fused"] + g["numeric"] + g["image_text"]
            + g["residual_boiler"] + g["short"] + g["absent"]) == g["n_source"]


def test_real_loss_lands_in_absent_and_is_named():
    """The bucket that matters. `absent_top` is what turns "0.50" into a sentence a
    person can act on — and the split is the point: of 200 missing occurrences only
    160 are chaseable. `eta` is three characters, and a token that short cannot be
    substring-matched honestly against the target, so its 40 occurrences are claimed
    by `short` first. 160 is the number a person should act on; the other 40 are a
    measurement artefact nobody can fix."""
    kept, lost = "alpha beta gamma delta epsilon ", "zeta eta theta iota kappa "
    loss, real = _report((kept + lost) * 40, "# T\n\n" + kept * 40)
    assert real is True
    assert (loss["gap"]["absent"], loss["gap"]["short"]) == (160, 40), loss["gap"]
    assert dict(loss["absent_top"])["zeta"] == 40
    assert "eta" not in dict(loss["absent_top"])


def test_a_clean_document_still_states_its_zero():
    """`absent: 0` is a claim about the document; an ABSENT block is a claim about
    nobody having looked. The stated zero is what makes a later non-zero readable."""
    src = "alpha beta gamma delta epsilon " * 40
    loss, _ = _report(src, "# T\n\n" + src)
    assert loss["gap"]["absent"] == 0
    assert loss["absent_top"] == []


def test_the_gap_denominator_is_not_the_body_denominator():
    """Two denominators, both named, neither blurred: the body metric EXCLUDES
    furniture and figure text from its ground truth, the gap BUCKETS them."""
    body = "alpha beta gamma delta epsilon " * 40
    image_text = "figure axis label caption"
    loss, _ = _report(body + " " + image_text, "# T\n\n" + body, "", image_text)
    assert loss["gap"]["n_source"] > loss["n_source_tokens"]
    assert loss["gap"]["image_text"] >= 1


def test_the_raw_text_is_the_contract_and_pre_stripping_flatters_the_metric():
    """THE row this signature exists for. This function owns the de-boilerplate
    policy and applies it ONCE, at one threshold, to text nobody has pre-processed.

    A caller that strips first — at its own threshold — does not merely duplicate
    the work: those lines leave the ground truth entirely, so the gap denominator
    stops describing the document and the body recall FLATTERS the conversion. Here
    the same markdown scores 0.77 against the document and a clean 1.00 against the
    pre-stripped one, with the footer no longer explained but simply gone. Taking
    the raw extraction as the parameter, rather than an optional extra, is what
    makes that unable to happen by accident."""
    pages = ["bodyline%d alpha beta gamma\n" % i
             + ("kestrel confidential footer\n" if i < 4 else "") for i in range(10)]
    src = "\f".join(pages)
    md = "# T\n\n" + "".join("bodyline%d alpha beta gamma\n" % i for i in range(10))
    raw_loss, _ = _report(src, md)
    # 0.1 rather than FRAC: a caller stripping with its OWN threshold is exactly the
    # drift this signature forbids, and at FRAC the strip is a no-op on a footer
    # that appears on 4 of 10 pages.
    pre, _ = _report(strip_running_lines(src, 0.1), md)
    assert raw_loss["gap"]["residual_boiler"] == 12      # explained, and still counted
    assert pre["gap"]["residual_boiler"] == 0            # gone, not explained
    assert pre["gap"]["n_source"] < raw_loss["gap"]["n_source"]
    assert pre["token_recall"] == 1.0 and raw_loss["token_recall"] < 0.8


def test_the_block_is_json_shaped_and_key_ordered():
    """The block is written verbatim into report.json, so key ORDER is part of the
    artifact's diff-readability, not an implementation detail."""
    src = "alpha beta gamma delta epsilon " * 40
    loss, _ = _report(src, "# T\n\n" + src)
    assert list(loss) == ["method", "token_recall", "content_recall",
                          "n_source_tokens", "missing_tokens", "figure_text_tokens",
                          "gap", "absent_top", "ocr_used", "gate"]
    assert loss["ocr_used"] is False


def test_it_touches_no_disk_network_or_process():
    """Pure strings-in/dict-out: the poppler and pypdfium2 subprocess calls stay in
    the script. This is what lets the policy be a UNIT test at all. Checked over the
    IMPORT GRAPH rather than the source text, so prose about `subprocess` in a
    comment cannot fail the test and an aliased import cannot pass it."""
    import ast
    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(_pdfcoverage))):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"collections", "backend"}, sorted(imported)
