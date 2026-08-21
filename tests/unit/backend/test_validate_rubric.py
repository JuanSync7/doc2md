"""
title: Unit — the executable rubric
kind: tests
layer: backend
summary: The grader that decides "is this an A" is itself graded: A is reserved for a clean sweep, a skip never counts as a pass, and no row can pass over an empty corpus.
"""
# A rubric that can be talked into an A is worse than no rubric. These tests are
# about the grader's own integrity, not about doc2md's output: they pin that the
# letter is earned, that an unproven row cannot masquerade as a passing one, and
# that every artifact predicate actually fails when its condition is violated.
import pytest

from backend.validate import (DIMENSIONS, RUBRIC_ROWS, gfm_anchor, grade, letter,
                              summarize)
from backend.validate._rubric import FAIL, PASS, SKIP, Result

pytestmark = pytest.mark.unit


def _result(dim, rid, status):
    return Result(dim, rid, "condition", status, "evidence")


# ------------------------------------------------------------ the letter itself

def test_a_is_reserved_for_a_clean_sweep():
    assert letter([_result("A", "A1", PASS), _result("A", "A2", PASS)]) == "A"


def test_a_single_skip_is_not_an_a():
    # docs/quality-plan.md: "a skipped row is an unproven row, and an unproven row
    # is not an A". This is the line that stops a missing test from reading green.
    rows = [_result("A", "A%d" % i, PASS) for i in range(9)]
    rows.append(_result("A", "A9", SKIP))
    assert letter(rows) != "A"


def test_a_single_failure_is_not_an_a():
    rows = [_result("A", "A%d" % i, PASS) for i in range(9)]
    rows.append(_result("A", "A9", FAIL))
    assert letter(rows) != "A"


def test_overall_is_a_only_when_every_dimension_is():
    every = []
    for dim, _label in DIMENSIONS:
        every.append(_result(dim, dim + "1", PASS))
    assert summarize(every)["overall"] == "A"
    every[-1] = Result(every[-1].dim, every[-1].rid, "c", FAIL, "e")
    assert summarize(every)["overall"] != "A"


def test_the_counts_add_up():
    rows = [_result("A", "A1", PASS), _result("A", "A2", FAIL),
            _result("B", "B1", SKIP)]
    s = summarize(rows)
    assert (s["passed"], s["failed"], s["skipped"], s["total"]) == (1, 1, 1, 3)


# --------------------------------------------------------------- the row table

def test_every_row_is_answerable():
    for row in RUBRIC_ROWS:
        assert row.dim in dict(DIMENSIONS), row
        assert row.condition and row.rid, row
        if row.kind == "suite":
            assert row.target and not row.check, row
        else:
            assert row.check and not row.target, row


def test_row_ids_are_unique():
    ids = [r.rid for r in RUBRIC_ROWS]
    assert len(ids) == len(set(ids)), sorted(ids)


def test_a_suite_that_was_not_run_is_a_skip_not_a_pass():
    results = grade({"bundles": [], "suites": {}})
    suites = [r for r in results if r.rid in
              [x.rid for x in RUBRIC_ROWS if x.kind == "suite"]]
    assert suites and all(r.status == SKIP for r in suites)


def test_an_empty_corpus_passes_nothing_it_cannot_see():
    # The failure mode this guards: a grader run against no bundles reporting a
    # cheerful A because every loop had nothing to iterate over.
    results = grade({"bundles": [], "manifest": [], "runs": [], "docs": {},
                     "suites": {}})
    artifact = [r for r in results
                if r.rid in [x.rid for x in RUBRIC_ROWS if x.kind == "artifact"]]
    passed = [r.rid for r in artifact if r.status == PASS]
    assert not passed, "these rows passed over an empty corpus: %s" % passed


def test_a_check_that_raises_is_a_failure_not_a_crash():
    # One malformed bundle must not take the whole grade down with it: the row
    # fails, names the exception, and the other 29 rows still report.
    results = grade({"bundles": [{"report": None}], "suites": {}})
    assert len(results) == len(RUBRIC_ROWS)


# ------------------------------------------------------- the anchor row's edges
#
# The first two cases below were false FAILURES until the outline work measured
# them: the rubric must grade what a renderer actually emits, not an idealised
# slug. The rest are about the row's OTHER half — the cross-check that every
# published anchor resolves in the body — which read `knowledge["body_anchors"]`,
# a key nothing has ever written to knowledge.json. `if known:` was false for every
# bundle ever graded, so half of C3 had never run; these tests exist so it cannot
# quietly stop running again.

def _md(*titles):
    """A markdown body whose ATX headings are exactly ``titles``."""
    return "".join("## %s\n\nbody\n\n" % t for t in titles)


def _one_bundle(nodes, markdown=None):
    bundle = {"doc_id": "d1", "structure": {"outline": nodes}}
    if markdown is None:
        markdown = _md(*[n["title"] for n in nodes if n["title"] != "(preamble)"])
    bundle["markdown"] = markdown
    return {"bundles": [bundle], "suites": {}}


def _node(title, anchor, nid="s"):
    return {"id": nid, "title": title, "anchor": anchor, "children": []}


def test_a_repeated_heading_may_carry_the_renderers_ordinal_suffix():
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle([
        _node("Overview", "overview", "s1"),
        _node("Overview", "overview-1", "s2")]))
    assert status == PASS, evidence


def test_a_headless_region_has_no_anchor_to_agree_with():
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("(preamble)", "preamble", "s0"), _node("Scope", "scope", "s1")]))
    assert status == PASS, evidence


def test_a_genuinely_wrong_anchor_still_fails():
    from backend.validate._rubric import _c3_anchors
    status, _ = _c3_anchors(_one_bundle([_node("Scope", "reference documents")]))
    assert status == FAIL


def test_a_title_that_slugs_to_nothing_may_carry_the_section_placeholder():
    # `***` slugs empty in all three implementations, so the outline publishes the
    # `section` fallback to keep the node addressable. It names no fragment any
    # renderer emits, so the cross-check leaves it alone — but the row must not
    # report it as a wrong slug either.
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("***", "section", "s1"), _node("Scope", "scope", "s2")],
        markdown=_md("***", "Scope")))
    assert status == PASS, evidence


def test_an_anchor_no_heading_in_the_body_offers_is_a_dead_link():
    # THE case the dead branch was written for: structure.json advertises a
    # fragment, document.md has no heading that resolves it. A consumer following
    # the published `ref` lands nowhere.
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("Escalation matrix", "escalation-matrix", "s1")],
        markdown=_md("Drain procedure")))
    assert status == FAIL
    assert "escalation-matrix" in evidence


# ------------------------------------------------ the flattened-hierarchy row
#
# C1 asks whether a numbered outline was published as a flat list of siblings. It
# used to answer by counting titles that merely LOOKED numeric, which made it fire
# on a document whose numbers are measurements — and a rubric row that fails a
# correct document is worse than one that misses a broken one.

def _outline(*titles):
    return {"bundles": [{"doc_id": "d1", "markdown": _md(*titles),
                         "structure": {"outline": [
                             _node(t, "a%d" % i, "s%d" % i)
                             for i, t in enumerate(titles)]}}],
            "suites": {}}


def test_a_nested_numbering_published_flat_still_fails():
    from backend.validate._rubric import _c1_hierarchy
    status, evidence = _c1_hierarchy(_outline("1", "1.1", "1.1.1", "1.1.1.1", "2"))
    assert status == FAIL
    assert "1.1" in evidence


def test_measurements_in_sibling_titles_are_not_a_flattened_hierarchy():
    # Five power rails, correctly published as five siblings. `1.8` is not a
    # subsection of `5`, and the row must not say it is.
    from backend.validate._rubric import _c1_hierarchy
    status, evidence = _c1_hierarchy(_outline(
        "5 V rail", "1.8 V rail", "3.3 V rail", "12 V rail", "2.5 V rail"))
    assert status == PASS, evidence


def test_real_siblings_at_one_depth_are_not_a_flattened_hierarchy():
    # `2.4`, `2.5`, `2.6` with no `2` heading of their own ARE siblings.
    from backend.validate._rubric import _c1_hierarchy
    status, evidence = _c1_hierarchy(_outline("2.4 Reset", "2.5 Clocks", "2.6 Power"))
    assert status == PASS, evidence


def test_the_cross_check_cannot_pass_by_having_nothing_to_read():
    # The failure this row actually shipped with: a bundle carrying no anchor data
    # made the check vacuous and the row still said PASS. A body the rubric cannot
    # read is now an unproven row, not a passing one.
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("Scope", "scope", "s1")], markdown=""))
    assert status == FAIL
    assert "cross-checked" in evidence


# ------------------------------------------------------------- the slug we share

@pytest.mark.parametrize("title,want", [
    ("Payments Gateway IT Runbook", "payments-gateway-it-runbook"),
    ("1.2 Scope", "12-scope"),
    ("  spaced  out  ", "spaced-out"),
    ("C++ / Rust", "c-rust"),
    ("", ""),
])
def test_gfm_anchor(title, want):
    assert gfm_anchor(title) == want
