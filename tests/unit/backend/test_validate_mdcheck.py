"""
title: Unit — markdown structural validator + lossless conversion gate
kind: tests
layer: backend
summary: validate_markdown finds broken tables/fences/leaks; conversion_report gates on 100% recall.
"""
# Pure policy on markdown strings — no disk. This is the validation system every
# converter lane's output must pass: structure well-formed AND source tokens 100%.
import pytest
from backend.validate import (validate_markdown, conversion_report, build_report,
                              image_report, caption_report, outline_report)

pytestmark = pytest.mark.unit


def test_image_report_gate_pass_when_intact():
    b = image_report(referenced=3, extracted=3, unique_files=2, missing=0,
                     orphans=0, verified=2)
    assert b["gate"] == "pass"


@pytest.mark.parametrize("kw", [
    {"missing": 1},                    # a referenced picture had no bytes
    {"orphans": 1},                    # a file on disk with no reference
    {"verified": 1},                   # a file failed its content-hash check
    {"extracted": 2},                  # a body reference did not resolve
])
def test_image_report_gate_degrades_on_any_defect(kw):
    base = dict(referenced=3, extracted=3, unique_files=2, missing=0,
                orphans=0, verified=2)
    base.update(kw)
    assert image_report(**base)["gate"] == "degraded"


def test_outline_report_gate_passes_when_everything_accounted():
    # covered + intentional TOC = all content -> pass, ratio 1.0.
    b = outline_report(content_lines=10, covered_lines=8, toc_lines=2,
                       uncovered_lines=0)
    assert b["gate"] == "pass"
    assert b["ratio"] == 1.0
    assert "first_uncovered" not in b            # nothing to triage


def test_outline_report_gate_degrades_on_any_uncovered_content():
    # A single lost content line degrades — the structure-side analogue of the
    # recall gate: loss is never a pass, however small.
    b = outline_report(content_lines=10, covered_lines=9, toc_lines=0,
                       uncovered_lines=1, first_uncovered=[4])
    assert b["gate"] == "degraded"
    assert b["ratio"] == 0.9
    assert b["first_uncovered"] == [4]


def test_outline_report_empty_document_is_vacuously_pass():
    b = outline_report(0, 0, 0, 0)
    assert b["gate"] == "pass"
    assert b["ratio"] == 1.0


def test_caption_report_gate_states():
    assert caption_report(False, 5, 0, 0, 0, 5)["gate"] == "disabled"
    assert caption_report(True, 0, 0, 0, 0, 0)["gate"] == "complete"
    assert caption_report(True, 5, 0, 0, 0, 5)["gate"] == "pending"     # nothing attempted
    assert caption_report(True, 5, 3, 1, 0, 1)["gate"] == "incomplete"  # ran, one left
    assert caption_report(True, 5, 4, 1, 0, 0)["gate"] == "complete"    # every image resolved


def _codes(issues):
    return [i.code for i in issues]


def test_clean_markdown_has_no_issues():
    md = ("---\ntitle: \"Spec\"\n---\n\n# Overview\n\nSome prose here.\n\n"
          "| Reg | Offset |\n| --- | --- |\n| CTRL | 0x00 |\n| STAT | 0x04 |\n")
    assert validate_markdown(md) == []


def test_table_column_mismatch_is_an_error():
    md = ("| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n")
    issues = validate_markdown(md)
    assert "table-columns" in _codes(issues)
    assert any(i.severity == "error" for i in issues)
    assert issues[0].line == 3          # the offending row, 1-indexed


def test_pipe_block_without_separator_is_a_warning():
    md = "CTRL | 0x00 | rw\nSTAT | 0x04 | ro\n"
    issues = validate_markdown(md)
    assert "table-no-separator" in _codes(issues)
    assert all(i.severity == "warning" for i in issues)


def test_escaped_pipes_do_not_change_column_count():
    md = ("| Field | Meaning |\n| --- | --- |\n| MODE | either a\\|b select |\n")
    assert validate_markdown(md) == []


def test_unclosed_fence_and_front_matter_are_errors():
    assert "fence-unclosed" in _codes(validate_markdown("```c\nint x;\n"))
    assert "frontmatter-unclosed" in _codes(validate_markdown("---\ntitle: x\n"))


def test_table_rules_do_not_fire_inside_code_fences():
    md = "```\na | b | c\nd | e\n```\n"
    assert validate_markdown(md) == []


def test_leaked_ooxml_tags_are_errors():
    issues = validate_markdown("body <w:t>raw</w:t> leaked")
    assert "xml-leak" in _codes(issues)


def test_control_and_replacement_chars_are_errors():
    assert "bad-chars" in _codes(validate_markdown("a\x00b"))
    assert "bad-chars" in _codes(validate_markdown(u"pll � lock"))


def test_heading_level_jump_is_a_warning():
    issues = validate_markdown("# Top\n\n### Jumped\n")
    assert "heading-jump" in _codes(issues)
    assert all(i.severity == "warning" for i in issues)


def test_conversion_report_passes_at_full_recall_and_clean_structure():
    src = "The PLL locks within 50 us after reset deasserts."
    md = "# Clocking\n\nThe PLL locks within 50 us after reset deasserts.\n"
    rep = conversion_report(src, md)
    assert rep["valid"] is True
    assert rep["recall"] == 1.0
    assert rep["errors"] == 0


def test_conversion_report_fails_on_any_missing_token():
    src = "The PLL locks within 50 us after reset deasserts."
    md = "The PLL locks within 50 us after reset.\n"    # "deasserts" lost
    rep = conversion_report(src, md)
    assert rep["valid"] is False
    assert rep["n_missing"] >= 1
    assert any(tok == "deasserts" for tok, _ in rep["missing_top"])


def test_conversion_report_fails_on_structural_error_even_at_full_recall():
    src = "alpha beta"
    md = "alpha beta\n\n| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n"
    rep = conversion_report(src, md)
    assert rep["recall"] == 1.0
    assert rep["valid"] is False
    assert rep["errors"] >= 1


# ── build_report: the bundle report verdict ───────────────────────────────────
def test_build_report_office_lossless_is_ok_and_passes_gate():
    src = "# Overview\nThe CTRL register holds status bits.\n"
    md = "# Overview\n\nThe CTRL register holds status bits.\n"
    rep = build_report(src, md, lane="office")
    assert rep["losslessness"]["method"] == "ooxml-ground-truth"
    assert rep["losslessness"]["token_recall"] == 1.0
    assert rep["losslessness"]["gate"] == "pass"
    assert rep["losslessness"]["missing_tokens"] == []
    assert rep["status"] == "ok"
    assert len(rep["markdown_sha256"]) == 64


def test_build_report_office_missing_tokens_fail_the_gate():
    src = "alpha beta gamma delta epsilon zeta\n"
    md = "alpha beta gamma\n"                         # dropped half the tokens
    rep = build_report(src, md, lane="office")
    assert rep["losslessness"]["token_recall"] < 1.0
    assert rep["losslessness"]["gate"] == "fail"
    assert rep["losslessness"]["missing_tokens"]      # names what was lost
    assert rep["status"] == "failed"


def test_build_report_structural_error_forces_failed():
    src = "A B\n"
    md = "A B\n\n| X | Y |\n| --- | --- |\n| 1 | 2 | 3 |\n"   # bad table row
    rep = build_report(src, md, lane="office")
    assert rep["structural_errors"] >= 1
    assert rep["status"] == "failed"


def test_build_report_warning_only_is_degraded():
    # A heading jump is a warning, not an error; recall stays 1.0 -> degraded, not failed.
    src = "Title Deep body\n"
    md = "# Title\n\n### Deep\n\nbody\n"
    rep = build_report(src, md, lane="office")
    assert rep["losslessness"]["gate"] == "pass"
    assert rep["structural_warnings"] >= 1
    assert rep["status"] == "degraded"


def test_build_report_content_metrics_counted():
    md = ("# H1\n\n## H2\n\n- one\n- two\n\n"
          "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
          "![a fig](images/img-0001.png)\n\n```\ncode\n```\n")
    rep = build_report("x", md, lane="office")
    c = rep["content"]
    assert c["headings"] == 2
    assert c["tables"] == 1
    assert c["images"] == 1
    assert c["lists"] == 2
    assert c["code_blocks"] == 1
    assert c["chars"] == len(md)


def test_build_report_non_office_uses_supplied_coverage_no_hard_gate():
    md = "# Doc\n\nsome extracted text\n"
    loss = {"method": "pdf-text-coverage", "coverage": 0.97, "ocr_used": False}
    rep = build_report("", md, lane="pdf", losslessness=loss)
    assert rep["losslessness"]["method"] == "pdf-text-coverage"
    assert rep["losslessness"]["coverage"] == 0.97
    assert rep["losslessness"]["gate"] == "best-effort"   # defaulted, never "pass"
    assert rep["status"] == "ok"


def test_build_report_tokenizer_changes_token_count():
    md = "# H\n\n" + ("word " * 40) + "\n"
    est = build_report("x", md, lane="office")["content"]["tokens"]
    tok = build_report("x", md, lane="office",
                       token_count=lambda s: max(1, len(s.split())))["content"]["tokens"]
    assert est != tok and tok > 0


def test_build_report_non_office_cannot_claim_a_pass_gate():
    # A non-office lane has no ground-truth tree; even if a caller mistakenly supplies
    # gate="pass", build_report must coerce it to best-effort — the asymmetry is
    # structural, not a matter of trusting the caller.
    for supplied in ({"method": "pdf-text-coverage", "coverage": 0.3, "gate": "pass"},
                     {"method": "pdf-text-coverage", "coverage": 0.99}):   # gate omitted
        rep = build_report("some source", "# Doc\n\nbody\n", lane="pdf",
                           losslessness=supplied)
        assert rep["losslessness"]["gate"] == "best-effort"
        assert rep["status"] in ("ok", "degraded")     # never a hard pass/fail for pdf
