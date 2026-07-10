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
