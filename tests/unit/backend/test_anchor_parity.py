"""
title: Unit — one anchor scheme, corpus-wide
kind: tests
layer: backend
summary: structure.json anchors, the rubric's gfm_anchor and kb.body_anchors are the same scheme over an adversarial title corpus.
"""
# quality-plan.md C3. Three places in this repo turn a heading into a `#fragment`:
#
#   * backend.sections.gfm_anchor   — what structure.json PUBLISHES
#   * backend.validate.gfm_anchor   — what the rubric GRADES against
#   * backend.kb.heading_anchor     — what kb._lint._check_refs RESOLVES refs against
#
# They are separate implementations on purpose (the grader must not share code with
# the producer, and backend.sections may only depend on backend.ingest). This file is
# the thing that stops "separate" from becoming "divergent": a link that resolves in
# the KB must be the one structure.json advertises.
import pytest

from backend.kb import body_anchors, heading_anchor
from backend.sections import document_outline, gfm_anchor, normalize_title
from backend.validate import gfm_anchor as rubric_anchor

pytestmark = pytest.mark.unit

# Titles that have actually broken one of the three: a dotted section number, the
# same number with the space lost, a plain multiword title, punctuation a slugifier
# would turn into separators, leading/trailing whitespace, a repeat, and non-ASCII.
#
# The second block is the corpus this file was missing while the copies diverged in
# production. Every entry below is a case where `kb.heading_anchor` disagreed with
# the two that collapse `[-\s]+`: it joined on `str.split()`, so the source's own
# hyphens survived beside the ones it added, and its "keep letters, digits, spaces
# and hyphens" class named a LITERAL SPACE, so a tab or an NBSP was DELETED where
# the other two hyphenated it. `# Overview - part 2` published `overview-part-2` in
# structure.json and `overview---part-2` in the KB — one heading, two fragments,
# and `kb._lint._check_refs` grading the live one as dead.
TITLES = [
    "1.2 Scope",
    "1.2reference documents",
    "1 Introduction",
    "2.1.1.1 Timing budget",
    "Payments Gateway IT Runbook",
    "C++ / Rust",
    "  spaced  ",
    "DB_MAX_CONN_LIMIT tuning",
    "Übersicht der Änderungen",
    "接口规范",
    "Overview",
    "Overview",
    "3D layout",
    # --- the cases that actually diverged -----------------------------------
    "Overview - part 2",             # hyphen-space-hyphen: the reported defect
    "Reset -- sequence",             # a doubled hyphen is still one separator
    "Trailing hyphen -",             # ...and a dangling one is trimmed, not kept
    "Reset\tsequence",               # a TAB is spacing, not a character to delete
    "Reset sequence",           # ...and so is a non-breaking space
    "Timing — budget",          # em dash: dropped, and its spaces collapse
    "Power—rail",               # ...with no spaces around it, it joins the words
    "Overview 1",                    # a trailing digit LOOKS like the -1 suffix
]

# A title of pure punctuation slugs to "" in all three implementations, so it makes
# nothing addressable: it is the one input where the outline's published anchor is
# deliberately NOT a rendered fragment (see the two tests at the bottom).
EMPTY_SLUG_TITLE = "***"


@pytest.mark.parametrize("title", TITLES + [EMPTY_SLUG_TITLE])
def test_the_three_implementations_agree_on_every_title(title):
    assert gfm_anchor(title) == rubric_anchor(title), "sections vs the rubric"
    assert gfm_anchor(title) == heading_anchor(title), "sections vs kb"


@pytest.mark.parametrize("title,want", [
    ("Overview - part 2", "overview-part-2"),
    ("Reset -- sequence", "reset-sequence"),
    ("Trailing hyphen -", "trailing-hyphen"),
    ("Reset\tsequence", "reset-sequence"),
    ("Reset sequence", "reset-sequence"),
    ("Timing — budget", "timing-budget"),
    ("Power—rail", "powerrail"),
    ("Overview 1", "overview-1"),
])
def test_the_agreed_scheme_is_the_collapsing_one(title, want):
    """Agreement alone is not enough — the three could agree on a WRONG rule.

    This pins the rule itself, the one `docs/reference/output-schema.md` publishes:
    a run of spacing and hyphens, of any length and any mixture, is ONE hyphen, and
    a leading or trailing one is trimmed. `str.split()`-joining produced
    `overview---part-2` for the first row and `resetsequence` for the fourth.
    """
    assert gfm_anchor(title) == want
    assert rubric_anchor(title) == want
    assert heading_anchor(title) == want


def test_structure_anchors_are_exactly_what_the_kb_makes_addressable():
    """The end-to-end claim: every anchor structure.json publishes for a document is
    an anchor kb.body_anchors reports for the same body — including the renderer's
    `-1` suffix on the repeated title."""
    body = "".join("## %s\nbody of %s\n" % (t, t.strip()) for t in TITLES)
    published = set(n["anchor"] for n in document_outline(body)["outline"])
    assert published == body_anchors(body)
    assert "overview-1" in published            # the repeat is disambiguated, not lost


def test_a_trailing_digit_and_the_disambiguating_suffix_collide_identically():
    """`## Overview` twice and a `## Overview 1` all want `overview-1`.

    That collision is real and is NOT what this test is pinning away: the anchor is
    defined as the fragment a renderer emits, and a renderer that slugs `Overview 1`
    to `overview-1` collides with its own suffix too. What matters for a `ref` is
    that the three implementations collide the SAME way, so a fragment that resolves
    in one resolves in all — an anchor that pointed at one heading in structure.json
    and a different one in the KB would be a silently wrong citation rather than a
    visibly ambiguous one.
    """
    body = "## Overview\na\n## Overview\nb\n## Overview 1\nc\n"
    published = [n["anchor"] for n in document_outline(body)["outline"]]
    assert published == ["overview", "overview-1", "overview-1"]
    assert body_anchors(body) == set(published)


def test_the_section_number_is_part_of_the_anchor():
    # Decided deliberately (see backend.sections._chunk): the anchor is the fragment
    # the RENDERER emits, and a renderer keeps the digits and drops the dot. Stripping
    # the number would make every ref into a numbered section a dead link.
    assert gfm_anchor("1.2 Scope") == "12-scope"
    assert gfm_anchor("2.1.1.1 Timing budget") == "2111-timing-budget"
    # ...and the number is what keeps two same-named sections apart with no suffix.
    assert gfm_anchor("2.1 Overview") != gfm_anchor("3.1 Overview")


def test_a_lost_space_after_the_section_number_is_handled_like_a_present_one():
    # normalize_title's _NUM used to require trailing whitespace, so "1.2 Scope" lost
    # its number and "1.2reference documents" kept it — the same document, two rules.
    assert normalize_title("1.2 Scope") == "scope"
    assert normalize_title("1.2reference documents") == "reference documents"
    assert normalize_title("1 Introduction") == "introduction"
    # A BARE integer glued to a letter is a word, not a section number.
    assert normalize_title("3D layout") == "3d layout"
    assert normalize_title("4K capture") == "4k capture"
    # A title that is nothing but a number keeps it rather than normalizing to "".
    assert normalize_title("1") == "1"


def test_a_long_heading_publishes_the_anchor_the_renderer_really_emits():
    """A heading over 120 characters used to be dropped from the outline entirely.
    Now that it is a node, the second half of that fix has to hold too: the title is
    published VERBATIM, so its anchor is the whole slug. A `[:120]` clip on the title
    would advertise the slug of a TRUNCATED heading — a fragment no renderer emits,
    which is precisely what the rubric's anchor cross-check grades as unaddressable.
    """
    long_title = ("Reset and Initialisation Sequence for the Kestrel Fabric Bridge, "
                  "Including the Optional Retry Path and the Timeout Handling Rules")
    assert len(long_title) > 120
    body = "# Kestrel Databook\nintro\n## %s\nbody\n" % long_title
    out = document_outline(body)["outline"]
    node = out[0]["children"][0]
    assert node["title"] == long_title                     # not clipped
    assert node["anchor"] == gfm_anchor(long_title)
    # ...and the three implementations still agree on it, end to end.
    assert set(n["anchor"] for n in [out[0], node]) <= body_anchors(body)
    assert heading_anchor(long_title) == rubric_anchor(long_title) == node["anchor"]


def test_an_anchorless_title_still_yields_an_addressable_node():
    # A heading of pure punctuation slugs to "" in every implementation; the outline
    # must still hand the consumer something to address the node by.
    assert gfm_anchor("***") == ""
    assert document_outline("# ***\nbody\n")["outline"][0]["anchor"] == "section"
