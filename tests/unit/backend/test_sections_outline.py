"""
title: Unit — backend.sections document_outline
kind: tests
layer: backend
summary: Faithful heading tree — nesting by level, partitioning token counts, image/table placement, no LLM.
"""
import pytest
from backend.sections import document_outline, outline_coverage

pytestmark = pytest.mark.unit


def _toks(s):
    return max(1, len(s) // 4)


def test_flat_headings_are_all_top_level():
    text = "# A\nbody a\n# B\nbody b\n# C\nbody c\n"
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["A", "B", "C"]
    assert all(n["level"] == 1 and n["children"] == [] for n in out["outline"])


def test_nesting_by_level():
    text = "# Top\nintro\n## Child1\nc1 body\n## Child2\nc2 body\n### Grand\ng body\n"
    out = document_outline(text)
    assert len(out["outline"]) == 1
    top = out["outline"][0]
    assert top["title"] == "Top"
    assert [c["title"] for c in top["children"]] == ["Child1", "Child2"]
    assert [g["title"] for g in top["children"][1]["children"]] == ["Grand"]


def test_self_vs_subtree_tokens_partition():
    # subtree_tokens of a parent == its self_tokens + sum of children subtree_tokens.
    text = "# Top\n" + ("word " * 40) + "\n## Child\n" + ("word " * 40) + "\n"
    out = document_outline(text, token_count=_toks)
    top = out["outline"][0]
    child_sum = sum(c["subtree_tokens"] for c in top["children"])
    assert top["subtree_tokens"] == top["self_tokens"] + child_sum
    assert top["self_tokens"] < top["subtree_tokens"]        # child adds tokens


def _assert_partition(nodes):
    """Recursively assert every node's subtree == self + sum(children.subtree)."""
    for nd in nodes:
        assert nd["subtree_tokens"] == nd["self_tokens"] + sum(
            c["subtree_tokens"] for c in nd["children"]), nd["title"]
        _assert_partition(nd["children"])


@pytest.mark.parametrize("tc", [None, _toks])
def test_partition_invariant_holds_under_level_jumps_recursively(tc):
    # A non-monotone tree with a level jump (H1 -> H3), deep nesting, a sibling that pops
    # back up, and a preamble. The partition must hold at EVERY node, in char and token
    # mode alike — this is what makes subtree_tokens a trustworthy budget signal.
    text = ("front matter prose\n"
            "# Alpha\nalpha body one two three\n"
            "### DeepJump\ndeep body words here\n"
            "#### Deeper\ndeeper still more words\n"
            "## BackUpTwo\nback up body text\n"
            "# Beta\nbeta body final words here\n")
    out = document_outline(text, token_count=tc)
    _assert_partition(out["outline"])
    # top-level subtrees plus any preamble tile the whole document exactly
    assert sum(n["subtree_tokens"] for n in out["outline"]) == out["total_tokens"]


def test_total_tokens_covers_document():
    text = "# A\n" + ("alpha " * 30) + "\n# B\n" + ("beta " * 30) + "\n"
    out = document_outline(text, token_count=_toks)
    assert out["total_tokens"] == sum(_toks(ln) for ln in text.split("\n"))
    # top-level subtree_tokens sum to total (headings cover the whole doc here)
    assert sum(n["subtree_tokens"] for n in out["outline"]) == out["total_tokens"]


def test_preamble_is_top_level_leaf_not_a_parent():
    # Content before the first heading must not adopt the headings that follow it.
    text = "some front matter prose here\n# Real Heading\nbody\n"
    out = document_outline(text)
    assert out["outline"][0]["title"] == "(preamble)"
    assert out["outline"][0]["children"] == []
    assert out["outline"][1]["title"] == "Real Heading"


def test_images_attach_to_their_section_with_null_caption():
    text = "# Fig Section\nsee ![a diagram](images/img-0003.png) here\n"
    out = document_outline(text)
    imgs = out["outline"][0]["images"]
    assert len(imgs) == 1
    assert imgs[0]["image_id"] == "img-0003"
    assert imgs[0]["ref"] == "images/img-0003.png"
    assert imgs[0]["alt"] == "a diagram"
    assert imgs[0]["caption"] is None            # deterministic layer never captions


def test_numbered_list_items_are_not_headings_but_section_numbers_are():
    # THE real-corpus bug: a numbered LIST ("1. Foo") must not explode into one heading
    # per bullet, but a hierarchical SECTION number ("1.2 Bar") must still be a heading.
    text = ("# Introduction\n"
            "1. Set forth the context of the system.\n"
            "2. Do not describe every port.\n"
            "3. Keep it top-down.\n"
            "## 1.2 Constraints\n"
            "body of constraints\n")
    out = document_outline(text)
    top = out["outline"]
    assert [n["title"] for n in top] == ["Introduction"]      # not 4+ nodes
    # the numbered list stays as body under Introduction; the sub-section survives
    assert [c["title"] for c in top[0]["children"]] == ["1.2 Constraints"]


def test_bulleted_lists_do_not_become_headings():
    text = "# Features\n- alpha\n- beta\n- gamma\n"
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["Features"]
    assert out["outline"][0]["children"] == []


def test_tables_counted_in_owning_section():
    text = ("# T\n| Bits | Name |\n|------|------|\n| 0 | A |\n| 1 | B |\n"
            "## Sub\nno table here\n")
    out = document_outline(text)
    assert out["outline"][0]["tables"] == 1
    assert out["outline"][0]["children"][0]["tables"] == 0


def test_anchors_disambiguate_repeats():
    text = "# Overview\na\n# Details\nb\n# Overview\nc\n"
    out = document_outline(text)
    anchors = [n["anchor"] for n in out["outline"]]
    assert anchors == ["overview", "details", "overview#2"]


def test_ids_are_sequential_and_unique():
    text = "# A\nx\n## B\ny\n# C\nz\n"
    out = document_outline(text)

    def _ids(nodes):
        for nd in nodes:
            yield nd["id"]
            for i in _ids(nd["children"]):
                yield i

    ids = list(_ids(out["outline"]))
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_empty_doc_yields_empty_outline():
    out = document_outline("\n\n   \n")
    assert out["outline"] == []
    assert out["has_toc"] is False


def test_has_toc_detected_and_toc_lines_not_in_outline():
    # A leading dotted-leader TOC block is skipped; the real headings after it form
    # the outline, and has_toc records that a TOC was present.
    text = ("Introduction .......... 1\n"
            "Methods .......... 2\n"
            "Results .......... 3\n"
            "\n"
            "# Introduction\nintro body\n"
            "# Methods\nmethod body\n")
    out = document_outline(text)
    assert out["has_toc"] is True
    assert [n["title"] for n in out["outline"]] == ["Introduction", "Methods"]


def test_lone_number_line_does_not_skip_real_content():
    # REGRESSION: a stray bare-number line ("1") deep in the body must NOT be mistaken
    # for a table of contents and swallow every heading above it (the content_start bug
    # that silently dropped ~533 leading lines — headings and a figure — from an outline).
    text = ("# Overview\nintro body here\n"
            "## Setup\nsetup body\n"
            "1\n"                                  # a lone number in the middle of the doc
            "## Results\nresults body\n")
    out = document_outline(text)
    assert out["has_toc"] is False                 # no real TOC -> nothing skipped

    def all_titles(nodes):
        acc = []
        for n in nodes:
            acc.append(n["title"])
            acc.extend(all_titles(n["children"]))
        return acc
    titles = all_titles(out["outline"])
    assert "Overview" in titles and "Results" in titles   # nothing above the "1" was dropped


def test_false_toc_with_a_heading_is_not_skipped():
    # SELF-CHECK: even a dot-leader-looking line can't trigger a skip when the candidate
    # region contains a real heading — a genuine TOC never does, so we skip nothing.
    text = ("# Real Heading\n"
            "Something ....... 4\n"                # looks TOC-ish, but a heading precedes it
            "# Next\nbody\n")
    out = document_outline(text)
    assert out["has_toc"] is False
    assert [n["title"] for n in out["outline"]][:1] == ["Real Heading"]


def test_toc_immediately_followed_by_heading_keeps_the_heading():
    # REGRESSION (off-by-one): with no blank line between the last TOC entry and the
    # first heading, the old "+2" skip swallowed the heading — an UNCHECKED line the
    # self-check never saw. The skip must end exactly at the checked TOC run.
    text = ("Introduction .......... 1\n"
            "Methods .......... 2\n"
            "# Introduction\nintro body\n"    # directly after the TOC, no blank
            "# Methods\nmethod body\n")
    out = document_outline(text)
    assert out["has_toc"] is True
    assert [n["title"] for n in out["outline"]] == ["Introduction", "Methods"]


def test_outline_coverage_full_on_plain_doc():
    # Preamble + heading nodes tile the whole document: nothing uncovered, no TOC.
    text = "front matter prose\n# A\nbody a\n## A1\nsub body\n# B\nbody b\n"
    out = document_outline(text)
    cov = outline_coverage(text, out["outline"])
    assert cov["uncovered_lines"] == 0
    assert cov["toc_lines"] == 0
    assert cov["covered_lines"] == cov["content_lines"]


def test_outline_coverage_classifies_intentional_toc_skip():
    # A genuine skipped TOC is accounted as toc_lines — intentional, not loss.
    text = ("Contents\n"
            "Intro .......... 1\n"
            "Methods .......... 2\n"
            "\n"
            "# Intro\nbody\n# Methods\nbody\n")
    out = document_outline(text)
    cov = outline_coverage(text, out["outline"])
    assert cov["uncovered_lines"] == 0
    assert cov["toc_lines"] == 3                  # header + two dot-leader entries
    assert cov["covered_lines"] + cov["toc_lines"] == cov["content_lines"]


def test_outline_coverage_detects_a_dropped_region():
    # The guardrail itself: hand a coverage check an outline whose spans MISS real
    # content (simulating any future builder bug) and it must count the loss and
    # name the first offending lines — this is what turns silent structure loss
    # into a degraded report.
    text = "# Lost\nlost body\n# Kept\nkept body\n"
    truncated = [{"line_span": [2, 4], "children": []}]   # only "Kept" covered
    cov = outline_coverage(text, truncated)
    assert cov["uncovered_lines"] == 2                    # "# Lost" + "lost body"
    assert cov["first_uncovered"] == [0, 1]
    assert cov["covered_lines"] == 2


def test_line_span_covers_whole_subtree():
    text = "# Top\nintro\n## Child\nc body\n# Next\nn body\n"
    out = document_outline(text)
    top = out["outline"][0]
    lines = text.split("\n")
    # Top's span runs from its heading line to the start of "Next" (its sibling).
    assert top["line_span"][0] == lines.index("# Top")
    assert top["line_span"][1] == lines.index("# Next")
