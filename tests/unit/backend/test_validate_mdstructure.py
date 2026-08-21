"""
title: Unit — md_structure reads markdown the way a renderer does
kind: tests
layer: backend
summary: Hand-written cases pin the CommonMark contract (list nesting, emphasis, code, links, tables); a differential against a real parser keeps it honest.
"""
# Two kinds of test, for two different failure modes.
#
# The HAND-WRITTEN cases pin the contract md_structure promises. They are the ones
# that would have caught P0.1: a child indented to content column 3 nests, the same
# child at column 2 does not, and "step 3" silently becomes "step 4". A test that
# only checked "some list came out" would have passed the bug.
#
# The DIFFERENTIAL case is the real defence. md_structure is a hand-rolled scanner,
# so its bugs are exactly the ones its author did not think of; asserting it against
# a second, independent CommonMark implementation is what makes it trustworthy —
# the same argument the losslessness gate makes with its converter-blind ground
# truth. It skips cleanly when no parser is importable, because the 3.6 office ring
# in CI has no PyPI access and must still run the hand-written half.
#
# ---------------------------------------------------------------------------
# WHAT THE DIFFERENTIAL DELIBERATELY DOES NOT COMPARE, and why. Every exclusion
# here is a real divergence, named rather than papered over:
#
#  1. TABLES and ``~~strike~~`` are GFM, not CommonMark. A stock CommonMark parser
#     reads a pipe table as a paragraph, so it cannot corroborate `tables` or
#     `strike` at all. Both are pinned by hand-written cases instead, and the
#     differential corpus contains no table and no `~~`.
#  2. FENCED vs INDENTED code blocks are not distinguished. md_structure reports a
#     single `code_blocks` total, so the differential compares the total (which both
#     reference parsers also carry) and not the split.
#  3. BLOCK QUOTE CONTENTS are counted as one quote each but not descended into for
#     block structure: a list inside a `>` quote contributes no list items to
#     md_structure and would to the reference. The corpus keeps quote bodies to
#     prose and inline markup, which IS compared.
#  4. INLINE SCANNING IS LINE AT A TIME. A code span or emphasis run split across a
#     soft line break (``a\n    ```x``` ``) is one construct to a real parser and
#     invisible to md_structure. Nothing this project emits does that; the corpus
#     contains no such sample.
#  5. PURE DELIMITER SOUP. md_structure implements flanking and the rule of three
#     but not cmark's `openers_bottom` bound, so a string of nothing but emphasis
#     delimiters (`*_**__**_`) can over-count by one. Measured at <0.1% over 80k
#     random delimiter strings, and unreachable for a run that encloses actual
#     text, so the corpus uses emphasis around words.
#  6. HTML blocks, link reference definitions and entity references: md_structure
#     has no concept of them and the corpus contains none.
# ---------------------------------------------------------------------------
import pytest

# Through the package boundary, not `._mdstructure` — tests are callers too
# (CONVENTIONS §3), and `md_structure` is in `backend.validate.__all__`.
from backend.validate import md_structure

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# (1) Hand-written cases: the contract
# --------------------------------------------------------------------------

# The P0.1 fixture, both ways round. `1. ` puts its content at column 3, so a child
# must reach column 3. At column 2 CommonMark closes the parent item and the child
# becomes the parent's SIBLING — which is how a renderer numbered "step 3" as 4.
NESTED_AT_COL_3 = ("1. Stop the service\n"
                   "2. Drain the queue\n"
                   "   1. Check the depth first\n"
                   "3. Restart\n")

SIBLING_AT_COL_2 = ("1. Stop the service\n"
                    "2. Drain the queue\n"
                    "  1. Check the depth first\n"
                    "3. Restart\n")


def test_child_indented_to_content_column_three_nests():
    got = md_structure(NESTED_AT_COL_3)
    assert got["list_items"] == {0: 3, 1: 1}
    assert got["max_list_depth"] == 2
    assert got["ordered_items"] == 4


def test_same_child_indented_to_column_two_does_not_nest():
    got = md_structure(SIBLING_AT_COL_2)
    assert got["list_items"] == {0: 4}
    assert got["max_list_depth"] == 1


def test_one_column_of_indent_is_the_whole_p0_1_bug():
    # The two documents carry identical tokens and identical item counts. The ONLY
    # observable difference is the depth histogram — which is precisely why the
    # token-recall gate graded the renumbered procedure as lossless.
    nested = md_structure(NESTED_AT_COL_3)
    flat = md_structure(SIBLING_AT_COL_2)
    assert nested["ordered_items"] == flat["ordered_items"] == 4
    assert nested["list_items"] != flat["list_items"]
    assert nested["max_list_depth"] > flat["max_list_depth"]


def test_bullet_child_needs_only_column_two():
    # A `- ` marker puts content at column 2, so the rule is per-marker, not a
    # constant: the same 2 columns that fail under `1. ` succeed under `- `.
    got = md_structure("- one\n- two\n  - two-a\n- three\n")
    assert got["list_items"] == {0: 3, 1: 1}
    assert got["bullet_items"] == 4


def test_four_space_indent_after_a_paragraph_is_a_code_block_not_a_list():
    got = md_structure("A paragraph.\n\n    - not a list item\n\nAfter.\n")
    assert got["code_blocks"] == 1
    assert got["list_items"] == {}
    assert got["bullet_items"] == 0


def test_indented_line_touching_a_paragraph_is_a_continuation_not_code():
    # An indented code block cannot interrupt a paragraph; without the blank line
    # above, those four spaces are just a soft-wrapped sentence.
    assert md_structure("A paragraph\n    still the paragraph\n")["code_blocks"] == 0


def test_consecutive_indented_lines_are_one_block_not_one_each():
    # Counts are of BLOCKS. A blank line inside indented code does not split it;
    # a non-indented line does.
    src = "a\n\n    c1\n    c2\n\n    c3\n\nb\n\n    c4\n"
    assert md_structure(src)["code_blocks"] == 2


@pytest.mark.parametrize("src,strong,em", [
    ("**a**", 1, 0),                      # strong is matched before em
    ("*a*", 0, 1),
    ("__a__", 1, 0),
    ("_a_", 0, 1),
    ("***a***", 1, 1),                    # em wrapping strong, not one of either
    ("**a** and **b**", 2, 0),
    ("**a *b* c**", 1, 1),
    ("DB_MAX_CONN_LIMIT", 0, 0),          # intraword `_` opens nothing (P0.3)
    ("snake_case_helper", 0, 0),
    ("--dry_run=true", 0, 0),
    ("a_b_c and x_y", 0, 0),
    ("\\*not\\* emphasis", 0, 0),         # escaped delimiters are literal text
    ("a * b * c", 0, 0),                  # space-flanked `*` is not a delimiter
    ("*a**b*", 0, 1),                     # rule of three: one em, not two
])
def test_emphasis_counts(src, strong, em):
    got = md_structure(src + "\n")
    assert (got["strong"], got["em"]) == (strong, em)


def test_a_star_inside_a_code_span_is_not_emphasis():
    got = md_structure("use `a * b` and `c * d` here\n")
    assert got["code_spans"] == 2
    assert (got["strong"], got["em"]) == (0, 0)


def test_code_span_shields_every_inline_construct_it_contains():
    got = md_structure("`**a**` and `[b](c)` and `![d](e)`\n")
    assert got["code_spans"] == 3
    assert (got["strong"], got["em"], got["links"], got["images"]) == (0, 0, 0, 0)


def test_gfm_table_dimensions():
    src = ("| Name | Qty | Note |\n"
           "| --- | ---: | :-: |\n"
           "| a | 1 | x |\n"
           "| b | 2 | y |\n")
    table = md_structure(src)["tables"][0]
    assert (table["rows"], table["cols"], table["has_header"]) == (3, 3, True)
    # Cell CONTENT is carried too, so the fidelity gate can see a transposition
    # that leaves the dimensions and the token multiset untouched.
    assert table["cells"][1] == (("a",), ("1",), ("x",))


def test_table_cells_are_still_scanned_for_inline_markup():
    src = ("| Name | Note |\n"
           "| --- | --- |\n"
           "| **a** | `b` |\n")
    got = md_structure(src)
    assert got["tables"][0]["cols"] == 2
    assert (got["strong"], got["code_spans"]) == (1, 1)


def test_fenced_blocks_are_counted_and_their_contents_are_not_markdown():
    src = ("before\n"
           "\n"
           "```python\n"
           "# **not** a heading, not strong\n"
           "- not a list item\n"
           "[not](a-link) and ![nor](an-image)\n"
           "```\n"
           "\n"
           "after\n")
    got = md_structure(src)
    assert got["code_blocks"] == 1
    assert got["headings"] == {}
    assert got["list_items"] == {}
    assert (got["strong"], got["links"], got["images"]) == (0, 0, 0)


def test_two_fences_are_two_blocks():
    src = "```\na\n```\n\n~~~\nb\n~~~\n"
    assert md_structure(src)["code_blocks"] == 2


def test_links_and_images_are_counted_separately():
    got = md_structure("[a](http://x) and ![b](y.png) and ![c](c.png)\n")
    assert (got["links"], got["images"]) == (1, 2)


def test_an_autolink_counts_as_a_link():
    got = md_structure("see <https://example.test/a?b=1> for more\n")
    assert got["links"] == 1


def test_angle_brackets_that_are_not_autolinks_count_as_nothing():
    # P0.3 requires `<stderr>` to survive verbatim; it must not read as a link.
    got = md_structure("writes to <stderr> when 3 < 4 > 2\n")
    assert got["links"] == 0


def test_an_image_inside_a_link_reports_both():
    got = md_structure("[![alt](i.png)](http://x)\n")
    assert (got["links"], got["images"]) == (1, 1)


def test_a_bare_bracket_label_is_not_a_link():
    got = md_structure("[label] alone and [ref][id] too\n")
    assert (got["links"], got["images"]) == (0, 0)


def test_headings_are_counted_by_level():
    got = md_structure("# a\n\n## b\n\n### c\n\n## d\n")
    assert got["headings"] == {1: 1, 2: 2, 3: 1}


def test_setext_underlines_are_headings_too():
    assert md_structure("Title\n=====\n\nSub\n---\n")["headings"] == {1: 1, 2: 1}


def test_a_rule_is_a_rule_when_no_paragraph_is_open():
    got = md_structure("intro\n\n---\n\ntail\n")
    assert got["headings"] == {}


def test_block_quotes_count_blocks_not_lines():
    assert md_structure("> one\n> two\n\n> three\n")["block_quotes"] == 2


def test_emphasis_inside_a_quote_is_still_seen():
    got = md_structure("> note: **required**, see `DB_MAX_CONN_LIMIT`\n")
    assert (got["block_quotes"], got["strong"], got["code_spans"]) == (1, 1, 1)


def test_empty_document_is_all_zeroes():
    got = md_structure("")
    assert got["list_items"] == {} and got["headings"] == {}
    assert got["max_list_depth"] == 0
    assert got["code_blocks"] == 0


def test_none_is_treated_as_empty():
    assert md_structure(None)["headings"] == {}


def test_strikethrough_pairs_are_counted():
    assert md_structure("~~gone~~ and ~~also gone~~\n")["strike"] == 2


# --------------------------------------------------------------------------
# The number a reader ACTS ON
# --------------------------------------------------------------------------
#
# CommonMark takes a list's start from its first marker and then counts up on its
# own, ignoring every later digit. So the number printed beside a step is a
# property of the LIST, not of the line, and reading the markers instead of the
# rendering is how a split procedure passed for a whole one.

def test_a_renderer_counts_the_list_not_the_markers():
    # Every later marker is ignored: three items starting at 1 print 1, 2, 3
    # whatever they were written as.
    assert md_structure("1. a\n1. b\n1. c\n")["ordered_numbers"] == [1, 2, 3]
    assert md_structure("1. a\n7. b\n4. c\n")["ordered_numbers"] == [1, 2, 3]


def test_a_start_other_than_one_is_carried():
    assert md_structure("5. five\n6. six\n")["ordered_numbers"] == [5, 6]


def test_a_block_between_two_runs_of_items_restarts_the_count():
    # THE defect, in the markdown the converter used to emit: item count, depth
    # histogram and token multiset are all identical to the unsplit procedure.
    split = md_structure("1. a\n2. b\n\nSome prose.\n\n1. c\n2. d\n")
    whole = md_structure("1. a\n2. b\n\nSome prose.\n\n3. c\n4. d\n")
    assert split["ordered_items"] == whole["ordered_items"] == 4
    assert split["list_items"] == whole["list_items"]
    assert split["ordered_numbers"] == [1, 2, 1, 2]
    assert whole["ordered_numbers"] == [1, 2, 3, 4]


def test_a_continuation_indented_into_the_item_does_not_restart_the_count():
    # A picture (or a callout) at the step's content column belongs to the step, so
    # the list is still open and the next item is 3 — which is what lets the
    # converter keep a screenshot inside a runbook step.
    src = ("1. a\n2. b\n\n   <!-- ooxml-image:word/media/shot.png -->\n\n3. c\n")
    assert md_structure(src)["ordered_numbers"] == [1, 2, 3]


def test_a_changed_delimiter_starts_a_new_list():
    # CommonMark's only way of restarting a list with no separating block. It is
    # what the converter emits when two adjacent procedures each begin at 1.
    got = md_structure("1. a\n2. b\n1) c\n2) d\n")
    assert got["ordered_numbers"] == [1, 2, 1, 2]
    assert got["list_items"] == {0: 4}


def test_a_sub_list_counts_separately_and_its_parent_resumes():
    got = md_structure("1. a\n2. b\n   1. b-one\n   2. b-two\n3. c\n")
    assert got["ordered_numbers"] == [1, 2, 1, 2, 3]


def test_a_bullet_at_the_same_depth_ends_the_ordered_list():
    got = md_structure("1. a\n- b\n1. c\n")
    assert got["ordered_numbers"] == [1, 1]


def test_a_heading_between_two_procedures_separates_them():
    got = md_structure("1. a\n2. b\n\n## Next\n\n1. c\n")
    assert got["ordered_numbers"] == [1, 2, 1]


def test_bullets_contribute_no_numbers():
    assert md_structure("- a\n- b\n")["ordered_numbers"] == []


# --------------------------------------------------------------------------
# (2) Differential against a real CommonMark implementation
# --------------------------------------------------------------------------
#
# The reference is whichever of marko / markdown-it-py / commonmark happens to be
# importable. We never shell out and never look at a virtualenv path: on the modern
# ring one of these is installed and the test runs, on the bare 3.6 office ring none
# is and the test skips with a reason that says so.

def _start(value):
    """A list's declared start. `or 1` would be wrong: `0.` is a legal ordered
    marker and its list really does begin at zero — the adapter that wrote it that
    way disagreed with md_structure about the one thing being compared."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _facts_marko(module, source):
    """Fact vector from marko's element tree."""
    facts = _blank_facts()
    doc = module.Markdown().parse(source)

    def walk(el, depth, ordered, counter):
        name = type(el).__name__
        if name in ("Heading", "SetextHeading"):
            facts["headings"][el.level] = facts["headings"].get(el.level, 0) + 1
        elif name == "ListItem":
            facts["list_items"][depth] = facts["list_items"].get(depth, 0) + 1
            facts["ordered_items" if ordered else "bullet_items"] += 1
            if ordered and counter is not None:
                # The reference agrees that the NUMBER belongs to the list: it comes
                # from the list's own start, counted up per item, never re-read off
                # the marker.
                facts["ordered_numbers"].append(counter[0])
                counter[0] += 1
        elif name in ("FencedCode", "CodeBlock"):
            facts["code_blocks"] += 1
            return                                    # contents are literal
        elif name == "StrongEmphasis":
            facts["strong"] += 1
        elif name == "Emphasis":
            facts["em"] += 1
        elif name == "CodeSpan":
            facts["code_spans"] += 1
            return
        elif name in ("Link", "AutoLink"):
            facts["links"] += 1
        elif name == "Image":
            facts["images"] += 1
        elif name == "Quote":
            facts["block_quotes"] += 1
        if name == "List":
            depth, ordered = depth + 1, bool(getattr(el, "ordered", False))
            counter = [_start(getattr(el, "start", 1))] if ordered else None
        children = getattr(el, "children", None)
        if isinstance(children, list):
            for child in children:
                walk(child, depth, ordered, counter)

    walk(doc, -1, False, None)
    return facts


def _facts_markdown_it(module, source):
    """Fact vector from markdown-it-py's flat token stream."""
    facts = _blank_facts()
    lists = []                                        # stack of [ordered?, next number]

    def inline(children):
        for tok in children or []:
            if tok.type == "strong_open":
                facts["strong"] += 1
            elif tok.type == "em_open":
                facts["em"] += 1
            elif tok.type == "code_inline":
                facts["code_spans"] += 1
            elif tok.type == "link_open":
                facts["links"] += 1
            elif tok.type == "image":
                facts["images"] += 1
                inline(tok.children)                  # alt text can hold links

    for tok in module.MarkdownIt("commonmark").parse(source):
        kind = tok.type
        if kind == "heading_open":
            level = int(tok.tag[1:])
            facts["headings"][level] = facts["headings"].get(level, 0) + 1
        elif kind in ("bullet_list_open", "ordered_list_open"):
            ordered = kind == "ordered_list_open"
            lists.append([ordered,
                          _start(tok.attrGet("start")) if ordered else 1])
        elif kind in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif kind == "list_item_open":
            depth = len(lists) - 1
            facts["list_items"][depth] = facts["list_items"].get(depth, 0) + 1
            facts["ordered_items" if lists[-1][0] else "bullet_items"] += 1
            if lists[-1][0]:
                facts["ordered_numbers"].append(lists[-1][1])
                lists[-1][1] += 1
        elif kind in ("fence", "code_block"):
            facts["code_blocks"] += 1
        elif kind == "blockquote_open":
            facts["block_quotes"] += 1
        elif kind == "inline":
            inline(tok.children)
    return facts


def _facts_commonmark(module, source):
    """Fact vector from commonmark.py's walker."""
    facts = _blank_facts()
    lists = []
    walker = module.Parser().parse(source).walker()
    while True:
        event = walker.nxt()
        if event is None:
            break
        node, entering = event["node"], event["entering"]
        kind = node.t
        if kind == "list":
            if entering:
                data = node.list_data or {}
                lists.append([data.get("type") == "ordered",
                              _start(data.get("start"))])
            else:
                lists.pop()
        elif not entering:
            continue
        elif kind == "heading":
            facts["headings"][node.level] = facts["headings"].get(node.level, 0) + 1
        elif kind == "item":
            depth = len(lists) - 1
            facts["list_items"][depth] = facts["list_items"].get(depth, 0) + 1
            facts["ordered_items" if lists[-1][0] else "bullet_items"] += 1
            if lists[-1][0]:
                facts["ordered_numbers"].append(lists[-1][1])
                lists[-1][1] += 1
        elif kind == "code_block":
            facts["code_blocks"] += 1
        elif kind == "code":
            facts["code_spans"] += 1
        elif kind == "strong":
            facts["strong"] += 1
        elif kind == "emph":
            facts["em"] += 1
        elif kind == "link":
            facts["links"] += 1
        elif kind == "image":
            facts["images"] += 1
        elif kind == "block_quote":
            facts["block_quotes"] += 1
    return facts


def _blank_facts():
    return {"headings": {}, "list_items": {}, "ordered_items": 0,
            "bullet_items": 0, "ordered_numbers": [], "strong": 0, "em": 0,
            "code_spans": 0, "code_blocks": 0, "links": 0, "images": 0,
            "block_quotes": 0}


def _load_reference():
    """(name, fn) for the first importable CommonMark parser, else (None, None)."""
    for name, adapter in (("marko", _facts_marko),
                          ("markdown_it", _facts_markdown_it),
                          ("commonmark", _facts_commonmark)):
        try:
            module = __import__(name)
        except ImportError:
            continue
        return name, adapter
    return None, None


REFERENCE_NAME, _reference_adapter = _load_reference()

_NO_PARSER = ("no CommonMark reference parser importable (tried marko, "
              "markdown_it, commonmark) — expected on the bare 3.6 ring, which "
              "has no PyPI access; the hand-written cases above still run")

# The corpus. The hand-written cases above, plus samples chosen to hurt: the
# indent rules a converter gets wrong, lazy continuations, list-vs-paragraph
# interruption, emphasis flanking, and nesting that crosses construct kinds.
CORPUS = [
    ("nested_at_col_3", NESTED_AT_COL_3),
    ("sibling_at_col_2", SIBLING_AT_COL_2),
    ("bullet_child_col_2", "- one\n- two\n  - two-a\n- three\n"),
    ("bullet_child_col_1", "- one\n- two\n - two-a\n- three\n"),
    ("indented_code_after_para", "A paragraph.\n\n    - not a list item\n\nAfter.\n"),
    ("indented_code_touching_para", "A paragraph\n    still the paragraph\n"),
    ("indented_code_runs", "a\n\n    c1\n    c2\n\n    c3\n\nb\n\n    c4\n"),
    ("indented_code_in_item", "- item\n\n      code in the item\n"),
    ("wide_marker_is_code", "-     five columns away\n"),
    ("strong", "**a**\n"),
    ("em", "*a*\n"),
    ("em_strong", "***a***\n"),
    ("strong_strong", "****a****\n"),
    ("em_wrapping_strong", "*foo**bar**baz*\n"),
    ("rule_of_three", "*a**b*\n"),
    ("nested_emphasis", "**a *b* c**\n"),
    ("underscore_emphasis", "__a__ and _b_ and _a_b_c_\n"),
    ("intraword_underscore", "DB_MAX_CONN_LIMIT and snake_case_helper\n"),
    ("dry_run_flag", "pass --dry_run=true to the runner\n"),
    ("punctuation_flanking", "**\"quoted\"** and *(paren)* and a*\"foo\"*\n"),
    ("escaped_delimiters", "\\*not\\* emphasis, \\_nor\\_ this\n"),
    ("star_in_code_span", "use `a * b` and `c * d` here\n"),
    ("code_span_shields", "`**a**` and `[b](c)` and `![d](e)`\n"),
    ("code_span_backtick_runs", "`a` `b` ``c ` d``\n"),
    ("unmatched_backtick", "a ` b and c\n"),
    ("fenced_block", "before\n\n```python\n# **not** strong\n- not a list\n"
                     "[not](a-link)\n```\n\nafter\n"),
    ("two_fences", "```\na\n```\n\n~~~\nb\n~~~\n"),
    ("fence_inside_fence", "````\n```\ninner\n```\n````\n"),
    ("fence_in_list", "- item\n\n  ```\n  code\n  ```\n"),
    ("links_and_images", "[a](http://x) and ![b](y.png) and ![c](c.png)\n"),
    ("autolink", "see <https://example.test/a?b=1> for more\n"),
    ("autolink_mail", "mail <a@b.test> or read <xml:id>\n"),
    ("not_autolink", "writes to <stderr> when 3 < 4 > 2\n"),
    ("image_in_link", "[![alt](i.png)](http://x)\n"),
    ("link_with_code_span", "[see `the code`](http://x)\n"),
    ("bare_label", "[label] alone and [ref][id] too\n"),
    ("headings_by_level", "# a\n\n## b\n\n### c\n\n## d\n"),
    ("headings_all_six", "# a\n## b\n### c\n#### d\n##### e\n###### f\n####### g\n"),
    ("heading_closed_and_empty", "## closed ##\n\n#\n\n#nothing\n"),
    ("heading_with_inline", "## a **b** `c` [d](http://e)\n"),
    ("heading_interrupts_para", "para\n#### four\n"),
    ("heading_indented_four", "para\n    #### four\n"),
    ("setext", "Title\n=====\n\nSub\n---\n"),
    ("setext_dash_only", "text\n-\n"),
    ("setext_indented", "para\n  ---  \n"),
    ("setext_not_lazy", "1. one\n2. two\n===\n"),
    ("rule_after_item", "- item\n---\n"),
    ("rule_alone", "intro\n\n---\n\ntail\n\n***\n\n___\n"),
    ("lazy_into_list", "1. one\ntext\n2. two\n"),
    ("ordered_two_cannot_interrupt", "text\n2. two\n"),
    ("ordered_one_can_interrupt", "text\n1. one\n"),
    ("bullet_can_interrupt", "text\n- item\n"),
    ("deep_bullets", "- a\n  - b\n    - c\n      - d\n"),
    ("deep_ordered", "1. a\n   1. b\n      1. c\n         1. d\n2. e\n"),
    ("mixed_nesting", "1. one\n   - bullet\n     1. deep\n2. two\n"),
    ("paren_markers", "1) one\n2) two\n   1) two-a\n"),
    ("mixed_markers", "- a\n* b\n+ c\n"),
    ("ordered_start_five", "5. five\n6. six\n"),
    ("ordered_markers_ignored", "1. a\n7. b\n4. c\n"),
    ("ordered_start_zero", "0. zero\n1. one\n"),
    ("split_by_a_paragraph", "1. a\n2. b\n\nprose\n\n1. c\n2. d\n"),
    ("continued_past_a_paragraph", "1. a\n2. b\n\nprose\n\n3. c\n4. d\n"),
    ("continuation_keeps_the_list", "1. a\n2. b\n\n   still item two\n\n3. c\n"),
    ("delimiter_flip_restarts", "1. a\n2. b\n1) c\n2) d\n"),
    ("sub_list_then_parent_resumes", "1. a\n2. b\n   1. b1\n   2. b2\n3. c\n"),
    ("sub_list_starting_at_three", "1. a\n\n   3. a1\n   4. a2\n2. b\n"),
    ("bullet_ends_the_ordered_list", "1. a\n- b\n1. c\n"),
    ("heading_between_procedures", "1. a\n2. b\n\n## next\n\n1. c\n"),
    ("nine_digit_marker", "123456789. big\n"),
    ("empty_items", "-\n- x\n"),
    ("multi_para_item", "- one\n\n  second para\n\n- two\n"),
    ("list_then_para", "- item\n\ntext\n"),
    ("tabbed_list", "- a\n\t- b\n"),
    ("tab_code", "a\n\n\tcode\n"),
    ("quote_two_blocks", "> one\n> two\n\n> three\n"),
    ("quote_inline", "> note: **required**, see `DB_MAX_CONN_LIMIT`\n"),
    ("quote_then_rule", "> a\n---\n"),
    ("empty", ""),
    ("only_blanks", "\n\n\n"),
    ("realistic_document",
     "# Runbook\n"
     "\n"
     "Intro with **bold**, *italic* and `inline code`.\n"
     "\n"
     "## Steps\n"
     "\n"
     "1. Stop the service\n"
     "2. Drain the queue\n"
     "   1. Check the depth first\n"
     "   2. Then drain\n"
     "3. Restart\n"
     "\n"
     "See [the docs](http://d/x) and ![a screenshot](s.png).\n"
     "\n"
     "```bash\n"
     "kubectl get pods -n payments\n"
     "```\n"
     "\n"
     "> Note: `DB_MAX_CONN_LIMIT` is one identifier, not emphasis.\n"),
]

# Exactly the facts both sides agree on. `tables` and `strike` are absent because
# they are GFM; see the exclusion list at the top of this module.
COMPARED = ("headings", "list_items", "ordered_items", "bullet_items",
            "ordered_numbers",
            "strong", "em", "code_spans", "code_blocks", "links", "images",
            "block_quotes")


@pytest.mark.skipif(_reference_adapter is None, reason=_NO_PARSER)
@pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
def test_matches_a_real_commonmark_parser(name, source):
    module = __import__(REFERENCE_NAME)
    expected = _reference_adapter(module, source)
    got = md_structure(source)
    ours = dict((k, got[k]) for k in COMPARED)
    theirs = dict((k, expected[k]) for k in COMPARED)
    assert ours == theirs, "%s disagrees with %s on %r" % (
        name, REFERENCE_NAME, source)


@pytest.mark.skipif(_reference_adapter is None, reason=_NO_PARSER)
def test_the_reference_actually_disagrees_when_md_structure_is_wrong():
    # A differential test that can never fail is a differential test that is not
    # wired up. Feed the reference the column-2 document and confirm it, too,
    # reports the flat list — i.e. the corpus above is really being graded.
    module = __import__(REFERENCE_NAME)
    assert _reference_adapter(module, SIBLING_AT_COL_2)["list_items"] == {0: 4}
    assert _reference_adapter(module, NESTED_AT_COL_3)["list_items"] == {0: 3, 1: 1}
