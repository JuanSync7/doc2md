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
                              image_report, caption_report, outline_report,
                              savings_report)

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


def test_a_run_that_produced_no_usable_caption_is_not_complete():
    # `useless` is a TERMINAL verdict, so it drives `pending` to zero while leaving
    # the image with nothing a reader can use. Three images, three captions the
    # useful gate threw away, and the block reported `complete`: coverage claimed
    # over zero coverage. This is the hole doc_meta_report's `invalid` was already
    # closed for, and caption_report is its twin.
    b = caption_report(True, 3, 0, 0, 3, 0)
    assert b["useless"] == 3 and b["captioned"] == 0
    assert b["gate"] == "incomplete"
    # One useless caption among four good ones is still not a finished run.
    assert caption_report(True, 5, 4, 0, 1, 0)["gate"] == "incomplete"


def test_furniture_is_a_finished_outcome_and_still_completes():
    # The other direction: a caption the model deliberately declined to write for a
    # spacer rule is CORRECT, not a failure. A gate that refused to complete over
    # furniture would make every deck of decorative images permanently incomplete.
    assert caption_report(True, 2, 0, 2, 0, 0)["gate"] == "complete"
    assert caption_report(True, 4, 2, 2, 0, 0)["gate"] == "complete"


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


# ── the fence PAIR, not the fence line ────────────────────────────────────────
#
# CommonMark closes a fenced block only with the opener's OWN character, at a run
# at least as long, on a line carrying nothing else. This reader toggled on any
# fence line instead, so a `~~~` inside a ``` block closed it: the rest of the
# document was read as code, the ``` that really closed it opened a phantom block,
# and the resulting `fence-unclosed` error made build_report say status="failed"
# over markdown a renderer is perfectly happy with — which WITHDRAWS a good bundle.

def test_a_tilde_line_inside_a_backtick_fence_does_not_close_it():
    md = ("# Title\n\nBody.\n\n```\nsome code\n~~~\nmore code\n```\n\nTail.\n")
    assert validate_markdown(md) == []
    assert build_report("Title Body some code more code Tail", md,
                        lane="office")["status"] == "ok"


def test_a_backtick_line_inside_a_tilde_fence_does_not_close_it():
    md = "~~~\nliteral ``` in a listing\n~~~\n"
    assert validate_markdown(md) == []


def test_a_shorter_run_does_not_close_a_longer_fence():
    # A four-backtick fence exists precisely so a three-backtick run can be shown
    # verbatim inside it.
    md = "````\n```\nnested sample\n```\n````\n"
    assert validate_markdown(md) == []


def test_a_longer_run_closes_a_shorter_fence():
    # The rule is "at least as long", not "exactly as long".
    md = "```\ncode\n`````\n"
    assert validate_markdown(md) == []


def test_a_fence_line_carrying_text_after_it_is_not_a_closer():
    # `` ```done `` is an info string, not a close, so this block never ends and
    # the error is real. The fix must not silence the case it was written for.
    assert "fence-unclosed" in _codes(validate_markdown("```\ncode\n```done\n"))


def test_a_tilde_run_inside_a_backtick_fence_is_not_a_second_code_block():
    # The count came from delimiters//2, so two tilde lines inside one block read
    # as two blocks. It is counted on the OPENER now.
    md = "```\nfirst\n~~~\nsecond\n~~~\nthird\n```\n"
    assert build_report("x", md, lane="office")["content"]["code_blocks"] == 1


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


def test_savings_report_measures_the_exchange_rate():
    # 40k chars of raw XML -> 4k chars of markdown: 10x reduction, 90% saved.
    # Chars only — measured on both sides; token views are derivable, not stored.
    b = savings_report(source_repr_chars=40000, markdown_chars=4000)
    assert b["source_repr"] == "ooxml-xml"
    assert b["source_chars"] == 40000 and b["markdown_chars"] == 4000
    assert b["reduction_ratio"] == 10.0
    assert b["saved_pct"] == 90.0
    assert "source_tokens_est" not in b          # deliberately chars-only


def test_savings_report_empty_edges_never_divide_by_zero():
    # 0 -> 0 (empty source): nothing saved, ratio a neutral 1.0.
    b = savings_report(0, 0)
    assert b["reduction_ratio"] == 1.0 and b["saved_pct"] == 0.0
    # real source -> 0-char markdown: the GATES fail such a doc; this block just
    # reports a 0 ratio rather than raising.
    b = savings_report(5000, 0)
    assert b["reduction_ratio"] == 0.0


# ================================ a pass over nothing is not a pass
#
# Found on `pdf/kestrel-dataflow.pdf`: a page that is entirely a vector diagram
# converted to 19 tokens and ZERO images, and the block read
# `{referenced: 0, extracted: 0, missing: 0, ...}, gate: pass` — so `status` read
# `ok` on a document whose only real content was never extracted.
#
# It is exactly the vacuity the office text gate already closed by carrying
# `n_source_tokens`: with no denominator, a gate cannot tell "every image came
# through" from "nobody looked for one". Three states, not two, and they mirror the
# losslessness vocabulary this repo already uses:
#
#   source known to hold 0   -> `pass`        an honest claim about the document
#   source known to hold >0  -> `degraded`    something was there and is not here
#   source count unknown     -> `unmeasured`  a claim about nobody having looked

def test_a_document_with_no_images_and_a_source_that_had_none_still_passes():
    """The common case must stay cheap and green: a prose document genuinely has no
    pictures, and saying so is a claim about the document."""
    b = image_report(referenced=0, extracted=0, unique_files=0, missing=0,
                     orphans=0, verified=0, source_images=0)
    assert b["gate"] == "pass" and b["source_images"] == 0


def test_zero_images_out_of_a_source_that_had_some_is_degraded():
    """The dataflow case. Nothing is `missing` by the old definition — no reference
    was made, so no reference failed — which is precisely why the old gate passed."""
    b = image_report(referenced=0, extracted=0, unique_files=0, missing=0,
                     orphans=0, verified=0, source_images=1)
    assert b["gate"] == "degraded"


def test_fewer_images_than_the_source_held_is_degraded_even_when_all_resolved():
    """Every reference resolving is not the same as every picture arriving. The old
    block could only compare the markdown against itself."""
    b = image_report(referenced=2, extracted=2, unique_files=2, missing=0,
                     orphans=0, verified=2, source_images=5)
    assert b["gate"] == "degraded"


def test_an_unknown_source_count_reads_unmeasured_rather_than_pass():
    """A lane with no converter-blind way to count the source's pictures must not
    claim a pass. `unmeasured` is the same word the fidelity gate uses for the same
    reason, and it does NOT degrade a document — it says nobody could tell."""
    b = image_report(referenced=0, extracted=0, unique_files=0, missing=0,
                     orphans=0, verified=0)
    assert b["gate"] == "unmeasured"
    assert b["source_images"] is None


def test_an_unknown_source_count_still_reports_a_real_defect():
    """`unmeasured` is about the DENOMINATOR. A picture that was referenced and
    whose bytes went missing is a defect either way, and must not be softened into
    "we could not tell"."""
    b = image_report(referenced=3, extracted=2, unique_files=3, missing=1,
                     orphans=0, verified=3)
    assert b["gate"] == "degraded"


def test_more_images_than_the_source_held_is_not_a_defect():
    """One source picture can legitimately be emitted twice (a logo reused in two
    sections dedupes to one file but two references), so an excess is not loss and
    must not be reported as one."""
    b = image_report(referenced=3, extracted=3, unique_files=1, missing=0,
                     orphans=0, verified=1, source_images=1)
    assert b["gate"] == "pass"


def test_the_denominator_is_stated_in_the_block():
    """Stated on every document, like `n_source_tokens`: a reader can only judge
    `referenced: 0` against what the source held, and an absent key is a claim about
    nobody having looked that reads identically to a zero."""
    b = image_report(referenced=1, extracted=1, unique_files=1, missing=0,
                     orphans=0, verified=1, source_images=1)
    assert list(b) == ["referenced", "unique_files", "extracted", "missing",
                       "orphans", "orphans_removed", "verified", "source_images",
                       "gate"]


# ================================ token bloat, measured (roadmap M1)
#
# The charter's Job 1 is "as close as possible an exact replica of the original but
# WITHOUT all the extra values that cause token bloat". Half that sentence had no
# metric, no gate and no rubric row — the word "bloat" appeared nowhere in
# end-goal.md, roadmap.md, quality-plan.md, README.md or the rubric.
#
# Measured with a real subword tokenizer over the 21-document corpus, the answer was
# not the markdown syntax anyone would have guessed:
#
#     front matter 39.4%   prose 45.3%   body markup 15.3%
#
# On `pdf/kestrel-dataflow.pdf`, 241 tokens of front matter wrap 19 tokens of
# content — 92.7%. Eleven of twenty-one documents are more than half front matter.
# You cannot ratchet what you do not measure, so the split is published per document.

def test_the_token_split_accounts_for_the_whole_file():
    """The three parts partition the document, so a reader can subtract. If they did
    not sum, a bucket could absorb bloat and the block would be decoration — the
    same argument the losslessness gap buckets make."""
    from backend.validate import token_split
    doc = ("---\ntitle: T\ndoc_id: abc\n---\n\n# Heading\n\nSome prose here.\n"
           "\n- a list item\n")
    s = token_split(doc)
    assert s["frontmatter"] + s["body"] == s["total"]
    assert s["prose"] + s["markup"] == s["body"]


def test_a_document_that_is_mostly_frontmatter_says_so():
    """The number that matters for retrieval: a consumer embedding document.md
    wholesale pays this on every single query."""
    from backend.validate import token_split
    doc = ("---\n" + "\n".join("key%02d: %s" % (i, "x" * 40) for i in range(20))
           + "\n---\n\nhi\n")
    s = token_split(doc)
    assert s["frontmatter_ratio"] > 0.8


def test_a_document_with_no_frontmatter_is_all_body():
    from backend.validate import token_split
    s = token_split("# Just a heading\n\nand prose.\n")
    assert s["frontmatter"] == 0 and s["frontmatter_ratio"] == 0.0
    assert s["body"] == s["total"]


def test_markup_is_what_the_prose_does_not_account_for():
    """`markup` is a RESIDUAL, deliberately: it is body tokens minus the tokens of
    the body rendered to text. That counts every syntax character — pipes, hashes,
    brackets, escapes, sentinels — without needing a list of what markup is, which
    would go stale the moment a converter emitted something new."""
    from backend.validate import token_split
    plain = token_split("alpha beta gamma delta\n")
    table = token_split("| alpha | beta |\n| --- | --- |\n| gamma | delta |\n")
    assert plain["markup"] < table["markup"]
    assert plain["prose"] == table["prose"]


def test_an_empty_document_reports_zeroes_not_a_division_error():
    from backend.validate import token_split
    s = token_split("")
    assert s["total"] == 0 and s["frontmatter_ratio"] == 0.0


def test_the_estimator_is_named_so_nobody_reads_it_as_exact():
    """Without a tokenizer the count is a ~4-chars/token estimate, measured wrong by
    -52.9% to +11.8% against a real subword tokenizer on this corpus. A number that
    wrong must say what it is; `method` is how a reader knows whether the budget
    they are reading is usable."""
    from backend.validate import token_split
    assert token_split("hello world")["method"] == "char-estimate/4"
    assert token_split("hello world", token_count=lambda s: 1)["method"] == "supplied"


def test_a_supplied_tokenizer_is_used_for_every_part():
    """A split where the parts and the total used different counters would not add
    up, which is the one thing this block must never do."""
    from backend.validate import token_split
    one = lambda s: 1 if s.strip() else 0
    s = token_split("---\na: b\n---\n\nhi there\n", token_count=one)
    assert s["frontmatter"] + s["body"] == s["total"]
    assert s["method"] == "supplied"
