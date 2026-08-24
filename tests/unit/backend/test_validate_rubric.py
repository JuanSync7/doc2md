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
            assert row.check and not row.target and not row.selector, row


def test_every_suite_row_names_the_specific_checks_that_demonstrate_it():
    # A row graded by "did this pytest FILE exit 0?" cannot tell "the condition I
    # assert is checked" from "that file is green for unrelated reasons": delete
    # the demonstration and the row keeps saying pass; add an unrelated green test
    # and it earns the row on the condition's behalf. Fifteen of the thirty-three
    # rows were graded that way, which is why P6.3 stayed open.
    for row in RUBRIC_ROWS:
        if row.kind == "suite":
            assert row.selector, "%s names no specific evidence" % row.rid


def test_no_two_suite_rows_are_graded_by_the_same_evidence():
    # E2, E3 and E4 all pointed at tests/integration/test_docs_parity.py, so ONE
    # green file supplied three separate A-grades in a five-row dimension.
    owner = {}
    for row in RUBRIC_ROWS:
        if row.kind != "suite":
            continue
        for name in row.selector:
            key = (row.target, name)
            assert key not in owner, ("%s and %s are both graded by %s::%s"
                                      % (owner.get(key), row.rid, key[0], key[1]))
            owner[key] = row.rid


def test_a_suite_row_records_what_the_runner_OBSERVED_not_an_exit_code():
    # A pytest target whose tests were all skipped exits 0. The runner reports what
    # it saw and the rubric records it verbatim, because "unknown" and "passing"
    # are the two things this module exists to keep apart.
    seen = {r.rid: r for r in grade(
        {"bundles": [], "suites": {"A2": (SKIP, "test_x was skipped in y")}})}
    assert seen["A2"].status == SKIP
    assert "skipped" in seen["A2"].evidence
    seen = {r.rid: r for r in grade(
        {"bundles": [], "suites": {"A2": (FAIL, "test_x no longer exists in y")}})}
    assert seen["A2"].status == FAIL
    assert "no longer exists" in seen["A2"].evidence


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
    # read is now an unproven row, not a passing one — and the bundle that
    # published the unresolvable anchor is named, because "nothing was
    # cross-checked anywhere" was the wrong denominator for a per-document fault.
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("Scope", "scope", "s1")], markdown=""))
    assert status == FAIL
    assert "scope" in evidence


def test_a_hash_inside_a_fenced_block_makes_no_anchor_addressable():
    # `# reset the board` in a shell transcript is a comment, not a heading: no
    # renderer emits a fragment for it, so it cannot vouch for a published anchor.
    from backend.validate._rubric import _c3_anchors
    status, evidence = _c3_anchors(_one_bundle(
        [_node("Reset the board", "reset-the-board", "s1")],
        markdown="```\n# Reset the board\nkestrelctl board reset --wait\n```\n"))
    assert status == FAIL
    assert "reset-the-board" in evidence


# ------------------------------------------- dimension C grades PER DOCUMENT
#
# The failure every test below guards is one shape: a corpus-wide denominator lets
# a healthy sibling stand in for the document that lost everything the row grades.
# That is how all 32 rows once passed over a document with every heading deleted,
# and each of these rows had its own version of it.

_TABLE = "| pin | net |\n|---|---|\n| A1 | clk |\n"


def _b(did, markdown, nodes, lane="office"):
    """One bundle, complete enough for any dimension-C row to grade it."""
    return {"doc_id": did, "markdown": markdown,
            "structure": {"outline": nodes},
            "report": {"lane": lane,
                       "structure": {"max_depth": 1, "largest_leaf_tokens": 3}}}


def _healthy(did="aaaaaaaa"):
    node = _node("Overview", "overview", "s1")
    node["tables"] = [{"table_id": "t1", "line": 3, "rows": 2, "cols": 2}]
    return _b(did, "## Overview\n\n" + _TABLE, [node])


def test_a_bundle_whose_body_renders_no_heading_is_not_covered_by_a_sibling():
    # The unstyled .docx: `is_heading` reads its ALL-CAPS/numbered paragraphs as
    # sections, so structure.json advertises `1-scope` and `2-register-map` over a
    # body that renders not one `#`. Both anchors resolve nowhere. Alone, the
    # bundle failed; beside a healthy sibling it used to be skipped outright and
    # the row printed "every one of them resolves in the body of its own bundle".
    from backend.validate._rubric import _c3_anchors
    damaged = _b("bbbbbbbb",
                 "**1 Scope**\n\nbody\n\n**2 Register map**\n\nbody\n",
                 [_node("1 Scope", "1-scope", "s1"),
                  _node("2 Register map", "2-register-map", "s2")])
    status, evidence = _c3_anchors({"bundles": [_healthy(), damaged]})
    assert status == FAIL, evidence
    assert "bbbbbbbb" in evidence and "1-scope" in evidence


def test_an_ordinary_two_document_corpus_still_passes_the_anchor_row():
    # The other direction: a gate that starts rejecting valid input is worse than
    # the bug it fixed.
    from backend.validate._rubric import _c3_anchors
    second = _b("bbbbbbbb", "## Scope\n\nbody\n\n### Limits\n\nbody\n",
                [_node("Scope", "scope", "s1"), _node("Limits", "limits", "s2")])
    status, evidence = _c3_anchors({"bundles": [_healthy(), second]})
    assert status == PASS, evidence


def test_a_bundle_with_no_outline_at_all_is_not_counted_as_inspected():
    # C1's evidence said "3 bundle(s) inspected" over a corpus in which one bundle
    # published no structure.json at all. Both numbers are reported now.
    from backend.validate._rubric import _c1_hierarchy
    status, evidence = _c1_hierarchy(
        {"bundles": [_healthy(), _b("bbbbbbbb", "## Scope\n\nbody\n", [])]})
    assert status == PASS, evidence
    assert "1 of 2" in evidence


def test_a_document_that_lost_its_whole_outline_is_not_covered_by_a_sibling():
    from backend.validate._rubric import _c4_summary_numbers
    status, evidence = _c4_summary_numbers(
        {"bundles": [_healthy(), _b("bbbbbbbb", "## Scope\n\nbody\n", [])]})
    assert status == FAIL, evidence
    assert "bbbbbbbb" in evidence


def test_a_bundle_with_no_body_to_build_a_tree_from_stays_exempt():
    from backend.validate._rubric import _c4_summary_numbers
    status, evidence = _c4_summary_numbers(
        {"bundles": [_healthy(), _b("bbbbbbbb", "", [])]})
    assert status == PASS, evidence


def test_a_document_whose_tables_lost_their_nodes_is_not_covered_by_a_sibling():
    # The bundle still RENDERS a GFM table; its outline addresses none of it. The
    # row used to pass on the strength of a different bundle's table.
    from backend.validate._rubric import _c5_table_nodes
    damaged = _b("bbbbbbbb", "## Registers\n\n" + _TABLE,
                 [_node("Registers", "registers", "s1")])
    status, evidence = _c5_table_nodes({"bundles": [_healthy(), damaged]})
    assert status == FAIL, evidence
    assert "bbbbbbbb" in evidence


def test_pipe_art_inside_a_fence_does_not_demand_a_table_node():
    # `_tables_in` deliberately excludes pipe art inside a fenced block, so a
    # rubric that demanded a node for a shell transcript's ASCII art would fail a
    # bundle the producer handled correctly.
    from backend.validate._rubric import _c5_table_nodes
    transcript = _b("bbbbbbbb", "## Transcript\n\n```\n" + _TABLE + "```\n",
                    [_node("Transcript", "transcript", "s1")])
    status, evidence = _c5_table_nodes({"bundles": [_healthy(), transcript]})
    assert status == PASS, evidence


def test_a_lane_with_no_semantic_tree_is_unmeasured_rather_than_failed():
    # The same asymmetry structure_fidelity already records: the office lane has a
    # converter-blind ground truth and hard-fails; docling's flat outline may
    # legitimately be unable to address a table, and unmeasured is not a pass.
    from backend.validate._rubric import _c5_table_nodes
    pdf = _b("bbbbbbbb", "## Registers\n\n" + _TABLE,
             [_node("Registers", "registers", "s1")], lane="pdf")
    status, evidence = _c5_table_nodes({"bundles": [_healthy(), pdf]})
    assert status == PASS, evidence
    assert "unmeasured" in evidence


# ------------------------------------------- B6: the row's own vacuous pass
#
# B6 exists to stop a recall being claimed over a token count nobody stated. Its
# guard was `if loss and "n_source_tokens" not in loss`, so a report carrying NO
# losslessness block at all skipped both checks, fell out of the loop, and was
# counted in "N recall(s) reported alongside the token count they are over" — the
# row asserting there are no vacuous passes, passing vacuously.

def _measured(did, n_tokens=120, gate=PASS):
    return {"doc_id": did, "markdown": "## Scope\n\nbody\n",
            "report": {"lane": "office",
                       "losslessness": {"method": "ooxml-ground-truth",
                                        "token_recall": 1.0, "gate": gate,
                                        "n_source_tokens": n_tokens}}}


def test_a_bundle_that_measured_no_losslessness_at_all_is_not_a_pass():
    from backend.validate._rubric import _b6_no_vacuous_pass
    silent = {"doc_id": "bbbbbbbb", "markdown": "## Scope\n\nbody\n",
              "report": {"lane": "office"}}
    status, evidence = _b6_no_vacuous_pass(
        {"bundles": [_measured("aaaaaaaa"), silent]})
    assert status == FAIL, evidence
    assert "bbbbbbbb" in evidence
    assert "nothing to grade" in evidence


def test_an_ordinary_corpus_still_passes_the_no_vacuous_pass_row():
    # The other direction: two documents that DID state their denominator must
    # keep passing, or the fix is worse than the hole.
    from backend.validate._rubric import _b6_no_vacuous_pass
    status, evidence = _b6_no_vacuous_pass(
        {"bundles": [_measured("aaaaaaaa"), _measured("bbbbbbbb", 4000)]})
    assert status == PASS, evidence
    assert "2 recall(s)" in evidence


def test_a_recall_with_no_token_count_still_fails():
    from backend.validate._rubric import _b6_no_vacuous_pass
    naked = {"doc_id": "bbbbbbbb", "report": {
        "lane": "office", "losslessness": {"token_recall": 1.0, "gate": PASS}}}
    status, evidence = _b6_no_vacuous_pass({"bundles": [naked]})
    assert status == FAIL and "n_source_tokens" in evidence


def test_an_empty_document_may_still_pass_when_it_says_so():
    # Zero tokens with the `empty_source` warning is an honest, declared shape and
    # was never the target of this row.
    from backend.validate._rubric import _b6_no_vacuous_pass
    empty = _measured("bbbbbbbb", 0)
    empty["report"]["warnings"] = [{"code": "empty_source", "count": 0}]
    status, evidence = _b6_no_vacuous_pass({"bundles": [empty]})
    assert status == PASS, evidence


# ------------------------------------------- markdown has TWO kinds of code block
#
# `_prose_lines` masked FENCED code and knew nothing about INDENTED code, so pipe
# art in a four-space listing was counted as a live GFM table and `_c5_table_nodes`
# hard-failed a bundle for not publishing a node for it. That is a rubric row
# failing a correct document, which is worse than the bug it guards.

def _indented(text):
    return "".join("    %s\n" % line for line in text.splitlines())


def test_pipe_art_in_an_indented_code_block_is_not_a_table():
    from backend.validate._rubric import _gfm_tables
    listing = "Run it like this:\n\n" + _indented(_TABLE)
    assert _gfm_tables(listing) == 0
    # ... and the same table at column 0 still counts, so the mask is a mask and
    # not a blindfold.
    assert _gfm_tables("Run it like this:\n\n" + _TABLE) == 1


def test_an_indented_listing_does_not_demand_a_table_node():
    from backend.validate._rubric import _c5_table_nodes
    listing = _b("bbbbbbbb", "## Listing\n\nRun it like this:\n\n" + _indented(_TABLE),
                 [_node("Listing", "listing", "s1")])
    status, evidence = _c5_table_nodes({"bundles": [_healthy(), listing]})
    assert status == PASS, evidence


def test_a_table_inside_a_list_item_is_still_a_table():
    # The column four spaces are measured from is the ITEM's content column, so a
    # list item's own table is prose. Masking it would quietly excuse the bundle
    # that never published a node for it.
    from backend.validate._rubric import _gfm_tables
    md = "-   step one\n\n" + "".join("    %s\n" % l for l in _TABLE.splitlines())
    assert _gfm_tables(md) == 1


def test_an_indented_line_that_continues_a_paragraph_is_not_code():
    # CommonMark §4.4: an indented chunk cannot INTERRUPT a paragraph. A wrapped,
    # over-indented sentence is prose the renderer joins to the line above.
    from backend.validate._rubric import _prose_lines
    lines = _prose_lines("A wrapped sentence\n        that continues here.\n")
    assert lines[1].strip() == "that continues here."


def test_an_indented_listing_after_a_blank_line_is_code():
    from backend.validate._rubric import _prose_lines
    assert _prose_lines("Prose.\n\n        listing line\n")[2] == ""


def test_a_tilde_line_inside_a_backtick_fence_does_not_unmask_the_rest():
    # The fence CLOSER must repeat the opener's character at its own length; this
    # mask toggled on any fence line, so a `~~~` inside a transcript re-opened the
    # document and the pipe art below it was read as a table.
    from backend.validate._rubric import _gfm_tables
    assert _gfm_tables("```\n~~~\n" + _TABLE + "```\n") == 0


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
