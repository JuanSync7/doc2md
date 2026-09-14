"""
title: Unit — backend.ingest markdown_to_text
kind: tests
layer: backend
summary: Mirrors src/backend/ingest/_markdown.py. The strip preserves prose tokens, drops syntax.
"""
import pytest
from backend.ingest import markdown_to_text, collapse_table_padding

pytestmark = pytest.mark.unit


def test_collapse_padding_removes_alignment_spaces():
    padded = (
        "| Bits   | Name                 | Access |\n"
        "|--------|----------------------|--------|\n"
        "| 25     | IC_SAR4_SMBUS_ARP_EN | R/W    |\n"
    )
    out = collapse_table_padding(padded)
    assert out == (
        "| Bits | Name | Access |\n"
        "| --- | --- | --- |\n"
        "| 25 | IC_SAR4_SMBUS_ARP_EN | R/W |\n"
    )


def test_collapse_padding_is_content_lossless():
    # Stripping cell padding must not change the prose the grep shadow sees.
    padded = "| Owen Carter   | Lead Engineer    |\n|-----|-----|\n| a | b |\n"
    assert markdown_to_text(collapse_table_padding(padded)) == markdown_to_text(padded)


def test_collapse_padding_preserves_alignment_colons():
    sep = "| :----- | ----: | :---: |"
    assert collapse_table_padding(sep) == "| :--- | ---: | :---: |"


def test_collapse_padding_idempotent():
    padded = "| a   | b   |\n|-----|-----|\n| 1   | 2   |\n"
    once = collapse_table_padding(padded)
    assert collapse_table_padding(once) == once


def test_collapse_padding_leaves_prose_untouched():
    prose = "# Heading\n\nSome paragraph with | a stray pipe in prose.\n"
    assert collapse_table_padding(prose) == prose


def test_collapse_padding_preserves_internal_cell_spaces():
    # only LEADING/TRAILING cell whitespace is padding; internal spaces are content.
    padded = "| 0x000 RW   | GEN_BGGEN_DIS   |\n"
    assert collapse_table_padding(padded) == "| 0x000 RW | GEN_BGGEN_DIS |\n"


def test_bold_does_not_split_phrase():
    # The motivating grep case: emphasis must not break an entity phrase.
    assert markdown_to_text("**Silicon** Operations") == "Silicon Operations"


def test_italic_and_strike_unwrap():
    assert markdown_to_text("the *fast* path") == "the fast path"
    # strikethrough text is still prose (may contain entities) -> keep the tokens
    assert markdown_to_text("~~old~~ new") == "old new"


def test_links_resolve_to_anchor_text():
    assert markdown_to_text("[Owen Carter](mailto:owen@x.com)") == "Owen Carter"
    assert markdown_to_text("see [the spec](https://x/y)") == "see the spec"


def test_image_resolves_to_alt_text():
    assert markdown_to_text("![AXI4 diagram](img/axi.png)") == "AXI4 diagram"


def test_autolink_keeps_url_text():
    assert markdown_to_text("<https://example.com/a>") == "https://example.com/a"


def test_headings_lose_marker_keep_text():
    assert markdown_to_text("## Slide 3") == "Slide 3"
    assert markdown_to_text("#### ECO flow ####") == "ECO flow"


def test_table_cells_join_with_space_never_fuse():
    md = "| Owen Carter | Lead Engineer |\n|---|---|\n| Jane Doe | QA |"
    out = markdown_to_text(md)
    assert "Owen Carter Lead Engineer" in out
    assert "Jane Doe QA" in out
    # separator row dropped, no pipes survive, cells not fused
    assert "|" not in out
    assert "CarterLead" not in out


def test_inline_code_unwraps():
    assert markdown_to_text("the `AXI4` bus") == "the AXI4 bus"


def test_code_fence_markers_dropped_content_kept():
    md = "```python\nx = AXI4_BASE\n```"
    out = markdown_to_text(md)
    assert "AXI4_BASE" in out
    assert "```" not in out


def test_list_markers_stripped():
    assert markdown_to_text("- first\n- second") == "first\nsecond"
    assert markdown_to_text("1. alpha\n2. beta") == "alpha\nbeta"


def test_blockquote_marker_stripped():
    assert markdown_to_text("> quoted text") == "quoted text"


def test_horizontal_rule_and_setext_dropped():
    assert markdown_to_text("Title\n===\n\nbody\n\n---") == "Title\n\nbody"


def test_escaped_punctuation_unescaped():
    assert markdown_to_text(r"a \* literal asterisk") == "a * literal asterisk"


def test_empty_input():
    assert markdown_to_text("") == ""
    assert markdown_to_text(None) == ""


# ---------------------------------------------------------------------------
# EMPHASIS: CommonMark's intraword ban belongs to `_` alone (finding idx 19).
#
# The office converter emits mid-word emphasis on purpose — Word stores a partly
# formatted word as two adjacent runs — and the recorded `Dma**ArbiterUnit**`
# deviation is justified BY this stripper undoing it before the recall gate
# tokenises. That justification was only true for `**`: `_ITALIC` applied the `_`
# rule to `*` as well, so `*n*th` kept its markers, the token `nth` went missing and
# a correctly converted document was thrown away at recall 0.667.
# Every expectation below was rendered through marko 2.2.3 and markdown-it-py 4.2.0.
# ---------------------------------------------------------------------------

def test_intraword_italic_is_unwrapped_the_way_a_renderer_shows_it():
    # <em> in both reference parsers, so the token the source held is `nth`.
    assert markdown_to_text("Set the *n*th bit.") == "Set the nth bit."
    assert markdown_to_text("two *Foo*s here.") == "two Foos here."
    assert markdown_to_text("re*start* now.") == "restart now."
    assert markdown_to_text("The MODE*MODE* end.") == "The MODEMODE end."
    # `*` between digits emphasises too — 2<em>3</em>4 — however little it looks it.
    assert markdown_to_text("2*3*4") == "234"
    assert markdown_to_text("a*b*c*d*e") == "abcde"


def test_the_intraword_ban_still_holds_for_underscore():
    # P0.3's contract, and the reason the fix had to be per-delimiter: one
    # identifier must reach the KB and the BM25 index as one token, not fused.
    assert markdown_to_text("DB_MAX_CONN_LIMIT") == "DB_MAX_CONN_LIMIT"
    assert markdown_to_text("snake_case_helper") == "snake_case_helper"
    assert markdown_to_text("pass --dry_run=true to the runner") == \
        "pass --dry_run=true to the runner"
    assert markdown_to_text("_x_y and *n*th") == "_x_y and nth"


def test_whitespace_flanked_asterisks_are_still_literal():
    # `(?=\S)` / `(?<=\S)` are load-bearing: neither reference parser emphasises
    # here, so neither may this.
    assert markdown_to_text("2 * 3 * 4") == "2 * 3 * 4"
    assert markdown_to_text("a * b * c") == "a * b * c"
    assert markdown_to_text("one*two") == "one*two"
    assert markdown_to_text("***all*** of it") == "all of it"
    assert markdown_to_text(r"\*not\* emphasis") == "*not* emphasis"
    assert markdown_to_text("use `a * b` and `c * d` here") == "use a * b and c * d here"


# ---------------------------------------------------------------------------
# MARKER RUNS: only delete what a renderer deletes (finding idx 35, reader half).
# ---------------------------------------------------------------------------

def test_a_marker_run_with_no_paragraph_above_it_is_prose_not_an_underline():
    # `===` is a setext UNDERLINE only under a paragraph. Standing alone it is an
    # ordinary paragraph (`<p>===</p>` in marko), and deleting it removed real
    # characters from the text layer while recall read a vacuous 1.0 — a marker run
    # carries no ASCII token to go missing.
    assert markdown_to_text("===") == "==="
    assert markdown_to_text("==") == "=="
    assert markdown_to_text("a\n\n===\n\nb") == "a\n\n===\n\nb"
    # An ATX heading is a closed block: the `===` beneath it is a paragraph.
    assert markdown_to_text("# H\n===\n") == "H\n==="
    # Two hyphens are neither a rule nor a table delimiter row.
    assert markdown_to_text("--") == "--"


def test_real_furniture_is_still_dropped():
    # The converse direction: everything a renderer really does swallow.
    assert markdown_to_text("Title\n===\n\nbody\n\n---") == "Title\n\nbody"
    assert markdown_to_text("Sub\n---\n") == "Sub"
    assert markdown_to_text("-----") == ""          # a thematic break, <hr />
    assert markdown_to_text("* * *") == ""
    assert markdown_to_text("___") == ""
    assert markdown_to_text("| a | b |\n| --- | --- |\n| 1 | 2 |") == "a b\n\n1 2"


# ------------------------------------------ a marker only ONE of the readers saw

# Three regexes in this project read an ordered marker, and they have to agree or a
# document is caught between them: `_ooxml_md._LEAD_LIST_NUM` decides what the
# converter ESCAPES, `_mdstructure._ORDERED` decides what the STRUCTURE gate sees,
# and `_markdown._LIST` — here — decides what the TOKEN gate sees. Capping the first
# two at CommonMark's nine digits and leaving this one unbounded made a faithful
# document REFUSE TO PUBLISH: the converter correctly left `1234567890. bill of
# materials` unescaped, this reader stripped the number anyway, and the gate reported
# `recall: 0.833, missing: [('1234567890', 1)]`. Every expectation below was rendered
# through marko 2.2.3.

def test_a_ten_digit_opener_is_not_a_list_marker():
    """CommonMark caps an ordered marker at nine digits. `<p>1234567890. x</p>` is
    what every parser renders, so every character is text and none of it is syntax."""
    assert markdown_to_text("1234567890. bill of materials") == "1234567890. bill of materials"
    assert markdown_to_text("12345678901234567890. serial") == "12345678901234567890. serial"


def test_a_nine_digit_opener_still_is_one():
    """The other side of the cap, so it cannot be widened by accident."""
    assert markdown_to_text("123456789. nine digits") == "nine digits"
    assert markdown_to_text("1. ordinary step") == "ordinary step"
    assert markdown_to_text("10) paren marker") == "paren marker"


def test_a_headings_content_is_not_a_list():
    """A line belongs to exactly ONE block. Running the list stripper over a line
    already recognised as a heading deleted the number from `## 1. Overview`, so a
    workbook with a sheet named `1. Overview` — or any numbered heading — published
    nothing for it and failed the token gate at recall 0.500."""
    assert markdown_to_text("## 1. Overview") == "1. Overview"
    assert markdown_to_text("# 10. Registers\n\nbody") == "10. Registers\n\nbody"
    assert markdown_to_text("### - dash sheet") == "- dash sheet"
    # A real list under a real heading is still stripped.
    assert markdown_to_text("## Steps\n\n1. first\n2. second") == "Steps\n\nfirst\nsecond"


# ================================== P9.9: the empty comment is a SEPARATOR, not text
#
# `_render_runs` emits `<!---->` between two adjacent emphasis spans whose delimiter
# runs would otherwise merge or fail to flank (`***a***` followed by `*b*` is four
# asterisks, and CommonMark reads ONE em span where the document draws two). It is an
# HTML comment because a comment renders as nothing and carries no token.
#
# "Carries no token" is only true if THIS reader agrees. A comment was substituted
# with a SPACE — right for `<!-- ooxml-image:x.png -->`, which stands between blocks
# and must not weld two words together, and wrong for an empty one inserted between
# two halves of a single word: the source run pair `alpha` + `beta` is the one token
# `alphabeta`, and a space there split it in two and took token recall to 0.0.

def test_an_empty_comment_leaves_no_trace_at_all():
    assert markdown_to_text("alpha<!---->beta") == "alphabeta"


def test_a_comment_with_content_still_separates():
    """The image sentinel stands between BLOCKS. Removing it outright would weld the
    last word of one to the first word of the next, so only the EMPTY form — the one
    this converter writes between two halves of a single word — vanishes."""
    from backend.ingest import tokenize
    assert tokenize(markdown_to_text("one<!-- ooxml-image:x.png -->Two")) \
        == ["one", "two"]


def test_an_escaped_empty_comment_is_prose_about_a_comment():
    """Same exemption the content-bearing rule already has: a converter-escaped
    `\\<!---->` is a document that was TALKING about markup, and its characters are
    on the source side of the recall gate."""
    assert "<!---->" in markdown_to_text("the marker \\<!----> is empty")


def test_the_separator_is_invisible_to_the_token_stream():
    from backend.ingest import tokenize
    assert tokenize(markdown_to_text("***alpha***<!---->*beta*")) == ["alphabeta"]
