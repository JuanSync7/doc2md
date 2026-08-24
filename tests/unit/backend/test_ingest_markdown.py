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
