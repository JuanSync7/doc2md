"""
title: Unit — outline node identity survives an edit
kind: tests
layer: backend
summary: section_id/parent/fingerprint are content-derived, so inserting a heading does not move the ids of the sections that did not change.
"""
# quality-plan.md C2/P4.2. `id` is a document-order counter: insert one heading at the
# top and every later `sec-NNNN` shifts by one, which silently repoints every
# permalink, card and incremental-index entry that used it. `section_id` is derived
# from the heading instead, so only the sections that actually changed change.
import pytest

from backend.sections import document_outline

pytestmark = pytest.mark.unit

BEFORE = ("# Runbook\n"
          "intro prose\n"
          "## Failover\n"
          "failover prose\n"
          "## Rollback\n"
          "rollback prose\n"
          "# Appendix\n"
          "appendix prose\n")

# The same document with ONE new section inserted at the very top.
AFTER = ("# Change Log\n"
         "added the failover runbook\n") + BEFORE


def _by_title(outline, acc=None):
    acc = {} if acc is None else acc
    for n in outline:
        acc[n["title"]] = n
        _by_title(n["children"], acc)
    return acc


def test_positional_ids_move_but_content_ids_do_not():
    before, after = _by_title(document_outline(BEFORE)["outline"]), \
        _by_title(document_outline(AFTER)["outline"])
    unchanged = ["Runbook", "Failover", "Rollback", "Appendix"]

    # the positional id is exactly what the plan says it is — positional
    assert [before[t]["id"] for t in unchanged] == ["sec-0001", "sec-0002",
                                                    "sec-0003", "sec-0004"]
    assert [after[t]["id"] for t in unchanged] == ["sec-0002", "sec-0003",
                                                   "sec-0004", "sec-0005"]
    # ...and the content-derived one is not
    for title in unchanged:
        assert before[title]["section_id"] == after[title]["section_id"], title
        assert before[title]["fingerprint"] == after[title]["fingerprint"], title
        assert before[title]["parent"] == after[title]["parent"], title


def test_section_ids_are_unique_within_a_document():
    outline = document_outline(AFTER)["outline"]
    ids = [n["section_id"] for n in _by_title(outline).values()]
    assert len(ids) == len(set(ids))
    # a repeated title is still two nodes with two ids (the anchor disambiguates)
    two = document_outline("# Overview\na\n# Details\nb\n# Overview\nc\n")["outline"]
    assert two[0]["section_id"] != two[2]["section_id"]


def test_parent_points_at_the_parents_section_id():
    outline = document_outline(BEFORE)["outline"]
    root = outline[0]
    assert root["parent"] is None
    assert [c["parent"] for c in root["children"]] == [root["section_id"]] * 2
    assert outline[1]["parent"] is None                # "# Appendix" is a sibling


def test_the_fingerprint_tracks_the_sections_own_body_only():
    # Editing a CHILD must not invalidate the parent's fingerprint — that is what
    # makes "only genuinely changed sections must be re-carded" true.
    edited = BEFORE.replace("rollback prose", "rollback prose, now with a caveat")
    before, after = _by_title(document_outline(BEFORE)["outline"]), \
        _by_title(document_outline(edited)["outline"])
    assert before["Rollback"]["fingerprint"] != after["Rollback"]["fingerprint"]
    assert before["Runbook"]["fingerprint"] == after["Runbook"]["fingerprint"]
    assert before["Failover"]["fingerprint"] == after["Failover"]["fingerprint"]


def test_the_fingerprint_ignores_markdown_that_carries_no_words():
    # Format-agnostic by construction (markdown is stripped first), so the same prose
    # re-extracted through another lane fingerprints the same.
    plain = document_outline("# H\nthe CHI protocol matters\n")["outline"][0]
    bold = document_outline("# H\nthe **CHI** protocol matters\n")["outline"][0]
    assert plain["fingerprint"] == bold["fingerprint"]


def test_renaming_a_section_is_the_one_thing_that_moves_its_id():
    renamed = BEFORE.replace("## Rollback", "## Roll Back")
    before, after = _by_title(document_outline(BEFORE)["outline"]), \
        _by_title(document_outline(renamed)["outline"])
    assert "Rollback" not in after and "Roll Back" in after
    assert before["Rollback"]["section_id"] != after["Roll Back"]["section_id"]
    # its siblings and its parent are untouched
    assert before["Failover"]["section_id"] == after["Failover"]["section_id"]
    assert before["Runbook"]["section_id"] == after["Runbook"]["section_id"]
    assert after["Roll Back"]["parent"] == after["Runbook"]["section_id"]
