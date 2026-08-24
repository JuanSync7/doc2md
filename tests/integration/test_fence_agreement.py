"""
title: Integration — every reader of a fenced block agrees where the code is
kind: tests
layer: backend
summary: A `~~~` line inside a ``` listing does not close it, in all five independent fence scanners.
"""
# WHY THIS EXISTS. Five modules decide independently which lines of a markdown body
# are CODE rather than prose, and they are separate on purpose (see the module
# docstrings — a shared helper would mean one bug lands identically everywhere and
# cancels out). All five had copied the same WRONG rule: any run of three backticks
# OR three tildes toggled the state.
#
# So a listing that merely MENTIONS the other fence character — a document about
# markdown, a diff of a README, shell output containing `~~~` — closed early. The
# damage was not subtle and it was not one-directional:
#
#   * `backend.sections.document_outline` published a `#` comment from inside the
#     listing as a level-1 heading, AND lost the real heading after the true closer,
#     because from there the mask was inverted;
#   * `backend.kb.body_anchors` then published an anchor for the invented heading —
#     and `_check_refs` grades a `ref` against exactly that set;
#   * `backend.ingest.markdown_to_text` processed the rest of the listing as prose,
#     stripping inline markup out of text a renderer shows verbatim — in the text
#     layer the recall gate tokenises;
#   * `backend.validate.validate_markdown` reported `fence-unclosed` on a correct
#     document, which reaches `status: "failed"` and makes `build_bundle` WITHDRAW
#     a previously-good bundle.
#
# The rule is CommonMark 4.5: a closer repeats the OPENER's character with a run at
# least as long, and carries nothing after it but spaces. This test pins all five
# against one body, so a future edit cannot fix one and leave the others.
import pytest

from backend.ingest import markdown_to_text
from backend.kb import body_anchors
from backend.sections import document_outline, fenced_lines
from backend.validate import md_structure, validate_markdown

pytestmark = pytest.mark.integration

# One body, exercising the exact shape. The listing mentions `~~~`, then contains a
# line that looks like a heading, and a REAL heading follows the true closer.
BODY = (
    "# Notes\n"
    "\n"
    "```\n"
    "example ~~~ marker\n"
    "~~~\n"
    "# reset the board\n"
    "```\n"
    "\n"
    "## Real Heading\n"
    "\n"
    "Body prose.\n"
)


def test_a_tilde_line_inside_a_backtick_listing_does_not_close_it():
    lines = BODY.splitlines()
    mask = fenced_lines(lines)
    # lines 2..6 are the fence and its contents; 8 is the real heading.
    assert mask[2:7] == [True] * 5, mask
    assert mask[8] is False, "the real heading was masked as code"


def test_the_outline_keeps_the_real_heading_and_invents_none():
    titles = []
    stack = list(document_outline(BODY)["outline"])
    while stack:
        node = stack.pop(0)
        titles.append(node.get("title"))
        stack.extend(node.get("children") or [])
    assert "Real Heading" in titles, (
        "the heading after the listing's true closer vanished from the outline")
    assert "reset the board" not in titles, (
        "a comment inside the listing was published as a section")


def test_no_anchor_is_published_for_a_line_inside_the_listing():
    anchors = body_anchors(BODY)
    assert "real-heading" in anchors
    assert "reset-the-board" not in anchors, (
        "a knowledge record could cite a fragment no renderer emits")


def test_the_text_layer_keeps_the_listing_verbatim():
    text = markdown_to_text(BODY)
    # Inside a fence, `# reset the board` is literal text, not a heading: the hash
    # must survive. Processing it as prose would strip the marker.
    assert "# reset the board" in text
    assert "example ~~~ marker" in text


def test_the_validator_does_not_report_a_correct_document_as_broken():
    codes = [i.code for i in validate_markdown(BODY)]
    assert "fence-unclosed" not in codes, codes
    assert md_structure(BODY)["code_blocks"] == 1


# --------------------------------------------------- direction (b): still correct

@pytest.mark.parametrize("body,blocks", [
    ("# A\n\n```\ncode\n```\n\n## B\n", 1),          # plain
    ("# A\n\n~~~\ncode\n~~~\n\n## B\n", 1),          # tilde-fenced, closed by tildes
    ("# A\n\n```python\ncode\n```\n\n## B\n", 1),    # info string
    ("# A\n\n```\ncode\n`````\n\n## B\n", 1),        # a LONGER closer is still a closer
    ("# A\n\n````\ncode\n```\nstill code\n````\n\n## B\n", 1),   # shorter run does not close
])
def test_ordinary_fences_still_open_and_close(body, blocks):
    """The bound must not become a blindfold: every legitimate fence still works,
    and the real heading after it is still a heading."""
    assert md_structure(body)["code_blocks"] == blocks
    titles = []
    stack = list(document_outline(body)["outline"])
    while stack:
        node = stack.pop(0)
        titles.append(node.get("title"))
        stack.extend(node.get("children") or [])
    assert "A" in titles and "B" in titles, titles
    assert "fence-unclosed" not in [i.code for i in validate_markdown(body)]


def test_an_unclosed_fence_still_runs_to_the_end_of_the_document():
    """What a renderer does with one, so the mask must match it."""
    body = "# A\n\n```\ncode\n# not a heading\n"
    titles = [n.get("title") for n in document_outline(body)["outline"]]
    assert titles == ["A"], titles
