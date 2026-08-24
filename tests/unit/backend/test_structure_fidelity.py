"""
title: Unit — the structure_fidelity gate
kind: tests
layer: backend
summary: The second hard gate: proves it catches the structural corruption token recall is blind to, and that it never claims a pass it cannot prove.
"""
# docs/quality-plan.md opens on the finding this file exists to close: a nested
# numbered procedure came out RENUMBERED with token_recall == 1.0, structural_errors
# == 0 and the pipeline's unanimous verdict "perfect". The first test below
# reintroduces that exact defect and asserts both halves of the story — the old gate
# still passes it, the new one does not.
import importlib.util
import os
import zipfile

import pytest

from backend.ingest import docx_source_structure, ooxml_markdown, ooxml_source_text
from backend.validate import (conversion_report, md_structure,
                              structure_fidelity_report)
import backend.ingest._ooxml_md as ooxml

pytestmark = pytest.mark.unit

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _styles(extra=""):
    return ('<w:styles %s>'
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/></w:style>%s</w:styles>' % (W, extra))


def _numbering():
    return ('<w:numbering %s>'
            '<w:abstractNum w:abstractNumId="0">'
            '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
            '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl>'
            '</w:abstractNum>'
            '<w:num w:numId="2"><w:abstractNumId w:val="0"/></w:num>'
            '</w:numbering>' % W)


def _p(text, style=None, num=None, rpr=""):
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % style)
    if num:
        ppr.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>'
                   % (num[1], num[0]))
    pre = ("<w:pPr>%s</w:pPr>" % "".join(ppr)) if ppr else ""
    return ('<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (pre, rpr, text))


def _parts(body, styles=None, numbering=True):
    """``numbering`` is True for the default two-level decimal list, False for
    none, or a numbering.xml of the caller's own."""
    parts = {"word/document.xml":
             '<w:document %s><w:body>%s</w:body></w:document>' % (W, body),
             "word/styles.xml": styles or _styles()}
    if numbering is True:
        parts["word/numbering.xml"] = _numbering()
    elif numbering:
        parts["word/numbering.xml"] = numbering
    return parts


def _verdict(parts, markdown=None):
    md = ooxml_markdown("docx", parts) if markdown is None else markdown
    return structure_fidelity_report(md_structure(md), docx_source_structure(parts))


# ------------------------------------------------------------------ the keystone

PROCEDURE = _parts(
    _p("Bring-up", style="Heading1")
    + _p("Program the feedback divider.", num=(2, 0))
    + _p("Wait for the lock counter.", num=(2, 0))
    + _p("Poll the status register.", num=(2, 1))
    + _p("Release the downstream gates.", num=(2, 0)))


def test_the_renumbering_bug_that_token_recall_cannot_see_fails_this_gate(monkeypatch):
    # The defect: indent each level by a flat 2 columns. A `1. ` marker's content
    # column is 3, so CommonMark reads the child as a SIBLING and a renderer numbers
    # it 4 instead of 3.1 — an operator working an outage runs the wrong step.
    real = ooxml._list_indent

    def flat_indent(cols, depth, marker):
        real(cols, depth, marker)                   # keep the state machine honest
        return "  " * depth

    monkeypatch.setattr(ooxml, "_list_indent", flat_indent)
    corrupted = ooxml_markdown("docx", PROCEDURE)

    # Half one: the ORIGINAL gate is perfectly happy with the corrupted document.
    old = conversion_report(ooxml_source_text("docx", PROCEDURE), corrupted)
    assert old["recall"] == 1.0 and old["valid"] is True and old["errors"] == 0

    # Half two: the new gate is not, and says exactly what moved.
    verdict = structure_fidelity_report(md_structure(corrupted),
                                        docx_source_structure(PROCEDURE))
    assert verdict["gate"] == "fail"
    delta = [d for d in verdict["deltas"] if d["fact"] == "list_items"]
    assert delta, verdict
    assert delta[0]["source"] == {0: 3, 1: 1}
    assert delta[0]["markdown"] == {0: 4}, "the nested step became a sibling"


def test_the_healthy_converter_passes_the_same_document():
    assert _verdict(PROCEDURE)["gate"] == "pass"


# ---------------------------------------------------- what the gate now protects

def test_dropping_emphasis_is_a_failure():
    parts = _parts(_p("Never reboot", rpr="<w:rPr><w:b/></w:rPr>"))
    assert _verdict(parts)["gate"] == "pass"
    # ...and the same document converted by a converter blind to w:rPr:
    verdict = structure_fidelity_report(md_structure("Never reboot\n"),
                                        docx_source_structure(parts))
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["strong"]


def test_a_flattened_table_is_a_failure():
    body = ('<w:tbl><w:tblGrid><w:gridCol w:w="100"/><w:gridCol w:w="100"/>'
            '</w:tblGrid>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p("Signal"), _p("Width"), _p("irq"), _p("32")))
    parts = _parts(body, numbering=False)
    assert _verdict(parts)["gate"] == "pass"
    verdict = structure_fidelity_report(
        md_structure("Signal Width irq 32\n"), docx_source_structure(parts))
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["tables"]


def test_a_heading_demoted_to_prose_is_a_failure():
    parts = _parts(_p("Bring-up", style="Heading1") + _p("Body text."))
    verdict = structure_fidelity_report(md_structure("Bring-up\n\nBody text.\n"),
                                        docx_source_structure(parts))
    assert verdict["gate"] == "fail"
    # BOTH heading facts move, and they are meant to: the histogram loses its `1`
    # and the ordered path loses the entry that carried the title's words. A demoted
    # heading that moved only one of them would mean the two had drifted apart.
    assert [d["fact"] for d in verdict["deltas"]] == ["headings", "heading_path"]


# --------------------------------------------------- what it must NOT claim

def test_an_unmeasured_lane_never_claims_a_pass():
    # No source facts means no second opinion. "unmeasured" is the honest word;
    # "pass" would be the lie this whole gate exists to stop.
    verdict = structure_fidelity_report(md_structure("# Title\n"), {})
    assert verdict["gate"] == "unmeasured"
    assert verdict["compared"] == 0


def test_a_non_office_lane_is_best_effort_even_when_it_agrees():
    # Same asymmetry as losslessness, same reason: a PDF has no ground-truth
    # semantic tree, so a match is agreement, not proof.
    facts = docx_source_structure(PROCEDURE)
    md = ooxml_markdown("docx", PROCEDURE)
    assert structure_fidelity_report(md_structure(md), facts, lane="office")["gate"] == "pass"
    assert structure_fidelity_report(md_structure(md), facts, lane="pdf")["gate"] == "best-effort"


def test_absent_and_zero_are_the_same_thing():
    # A fact the source does not exhibit must not read as a delta just because one
    # side spelled it {} and the other {2: 0}.
    verdict = structure_fidelity_report(
        {"headings": {1: 1}, "list_items": {}, "ordered_items": 0, "bullet_items": 0,
         "strong": 0, "em": 0, "strike": 0, "code_spans": 0, "code_blocks": 0,
         "links": 0, "tables": []},
        {"headings": {1: 1}, "list_items": {2: 0}, "ordered_items": 0,
         "bullet_items": 0, "strong": 0, "em": 0, "strike": 0, "code_spans": 0,
         "code_blocks": 0, "links": 0, "tables": []})
    assert verdict["deltas"] == []
    assert verdict["gate"] == "pass"


# ------------------------------------------- the ground truth is its own opinion

def test_the_ground_truth_shares_no_traversal_with_the_converter():
    # The gate is worth nothing if both sides are the same code with the same bug.
    # This is a structural assertion, not a style one: the module that produces the
    # structural ground truth must not reach for the converter's walkers.
    import re

    import backend.ingest._ooxml_struct as struct
    source = open(struct.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    # CALLS, not mentions. Naming the converter's function in a comment to explain
    # which policy is being mirrored is exactly the documentation this file should
    # carry; calling it is what would destroy the independence.
    code = re.sub(r"#.*", "", source)
    code = re.sub(r'"""(?:.|\n)*?"""', "", code)
    for walker in ("_docx_blocks", "_docx_p_text", "_docx_cell_text",
                   "_docx_table_rows_md", "_p_style_info", "_docx_styles",
                   "_docx_numbering", "_gfm_table", "_join_blocks", "_list_indent",
                   "_collect_text", "_text_of", "_find_locals"):
        assert not re.search(r"\b%s\s*\(" % walker, code), (
            "%s calls the converter's %s — the second opinion has to be a second "
            "implementation" % (struct.__name__, walker))
        assert "import %s" % walker not in code, walker


def test_a_paragraph_the_converter_forgets_to_reach_is_caught():
    # A content control (w:sdt) wrapping a heading. The flat scan finds it through
    # the wrapper; a converter that failed to recurse would not, and the counts
    # would part company. Here both do, which is the point of asserting it.
    body = ('<w:sdt><w:sdtContent>%s</w:sdtContent></w:sdt>%s'
            % (_p("Wrapped heading", style="Heading1"), _p("Body.")))
    parts = _parts(body, numbering=False)
    assert docx_source_structure(parts)["headings"] == {1: 1}
    assert _verdict(parts)["gate"] == "pass"


def _gen_corpus():
    # The eval corpus builder is not importable as a package; load it by path so a
    # unit test can use the SAME fixtures the eval suite pins.
    here = os.path.abspath(__file__)                       # tests/unit/backend/<f>.py
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    spec = importlib.util.spec_from_file_location(
        "gen_corpus", os.path.join(repo, "evals", "gen_corpus.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_real_docx_on_disk_agrees_end_to_end(tmp_path):
    path = str(tmp_path / "spec.docx")
    _gen_corpus().build_spec_docx(path)
    with zipfile.ZipFile(path) as zf:
        parts = dict((n, zf.read(n).decode("utf-8", "replace")) for n in zf.namelist()
                     if n.endswith(".xml") or n.endswith(".rels"))
    verdict = _verdict(parts)
    assert verdict["gate"] == "pass", verdict["deltas"]
    # `compared` counts EVIDENCE, not the fact schema. This document has headings
    # (both the histogram and the ordered path), lists (both kinds, with numbers and
    # item text), one strong run, one link and a table: ten facts that observed
    # something. The ones it does not exhibit — em, strike, code spans, code blocks —
    # were 0 == 0, and counting them made the block claim thirteen checks where four
    # of them could not have failed.
    #
    # It was 13 while `compared` counted names, then 9 while the ground truth still
    # had no opinion on `heading_path`. It is 10 now that the ground truth supplies
    # the ordered heading fact, which is the rise the previous revision predicted.
    assert verdict["compared"] == 10
    # ...and there is nothing left for it to disclaim: every fact in the schema is
    # either evidenced on both sides or symmetrically absent, so the report carries
    # no `unmeasured` list at all.
    assert "unmeasured" not in verdict


# ------------------------------------- corruption that has no shape, only content
#
# Both of these passed EVERY gate — recall 1.0, fidelity pass, zero deltas, zero
# structural errors — until the compared fact set grew cell and item content. They
# are the reason `tables` carries `cells` and `list_item_words` exists: geometry
# and counts cannot see a value that moved.

def test_a_transposed_table_is_a_failure():
    body = ('<w:tbl>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p("Severity"), _p("Page"), _p("SEV1"), _p("payments rota"),
               _p("SEV2"), _p("platform rota")))
    parts = _parts(body, numbering=False)
    faithful = ooxml_markdown("docx", parts)
    assert _verdict(parts)["gate"] == "pass"

    # Swap the two rota values: SEV1 now pages the wrong team. Same dimensions,
    # same token multiset, same everything a counting gate can see.
    swapped = (faithful.replace("| SEV1 | payments rota |", "| SEV1 | @@ |")
                       .replace("| SEV2 | platform rota |", "| SEV2 | payments rota |")
                       .replace("@@", "platform rota"))
    old = conversion_report(ooxml_source_text("docx", parts), swapped)
    assert old["recall"] == 1.0 and old["valid"] is True, "the token gate is blind"

    verdict = structure_fidelity_report(md_structure(swapped),
                                        docx_source_structure(parts))
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["tables"]


# ------------------------------- corruption that renumbers rather than reorders
#
# A procedure split in two renders 1, 2, 1, 2. Every fact the gate compared before
# `ordered_numbers` is IDENTICAL between the whole procedure and the split one: the
# same items, at the same depths, with the same words, and the same token multiset.
# The number a reader acts on was the one thing nobody was reading.

def _blind(parts, markdown):
    """(old gate, new gate) for a hand-written corrupted conversion of ``parts``."""
    old = conversion_report(ooxml_source_text("docx", parts), markdown)
    new = structure_fidelity_report(md_structure(markdown),
                                    docx_source_structure(parts))
    return old, new


IMAGE_NS = ('xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
            'wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')

PICTURE_STEP = _parts(
    _p("Acknowledge the page.", num=(2, 0))
    + ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>'
       '</w:pPr><w:r><w:t>Open the console.</w:t></w:r>'
       '<w:r><w:drawing><wp:inline %s><a:graphic><a:graphicData><pic:pic>'
       '<pic:blipFill><a:blip r:embed="rId9"/></pic:blipFill></pic:pic>'
       '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
       % IMAGE_NS)
    + _p("Drain the pod.", num=(2, 0))
    + _p("Restart the tier.", num=(2, 0)))
PICTURE_STEP["word/_rels/document.xml.rels"] = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId9" Type="http://schemas.openxmlformats'
    '.org/officeDocument/2006/relationships/image" Target="media/shot.png"/>'
    '</Relationships>')


def test_a_screenshot_in_a_runbook_step_no_longer_renumbers_it():
    # The most realistic renumbering there is. The picture used to be emitted at
    # column 0, which closes the list, so the steps below it started again at 1 —
    # while LibreOffice reads the very same file as <ol start="3">.
    md = ooxml_markdown("docx", PICTURE_STEP, emit_images=True)
    assert "3. Drain the pod." in md and "4. Restart the tier." in md
    assert structure_fidelity_report(
        md_structure(md), docx_source_structure(PICTURE_STEP))["gate"] == "pass"

    # ...and the conversion this replaces: same tokens, same items, same depths.
    corrupted = md.replace("\n   <!-- ooxml-image:word/media/shot.png -->",
                           "\n\n<!-- ooxml-image:word/media/shot.png -->\n") \
                  .replace("3. Drain the pod.", "1. Drain the pod.") \
                  .replace("4. Restart the tier.", "2. Restart the tier.")
    old, new = _blind(PICTURE_STEP, corrupted)
    assert old["recall"] == 1.0 and old["valid"] is True, "the token gate is blind"
    assert md_structure(corrupted)["list_items"] == \
        docx_source_structure(PICTURE_STEP)["list_items"], "so is the depth histogram"
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["ordered_numbers"]
    assert new["deltas"][0]["source"] == [1, 2, 3, 4]
    assert new["deltas"][0]["markdown"] == [1, 2, 1, 2]


INTERLUDE = _parts(
    _p("Announce the freeze.", num=(2, 0))
    + _p("Drain the tier.", num=(2, 0))
    + _p("The drain is idempotent; rerun it if it stalls.")
    + _p("Run the migration.", num=(2, 0)))


def test_a_paragraph_between_steps_no_longer_restarts_them():
    md = ooxml_markdown("docx", INTERLUDE)
    assert "\n3. Run the migration." in md
    assert _verdict(INTERLUDE)["gate"] == "pass"
    old, new = _blind(INTERLUDE, md.replace("3. Run the", "1. Run the"))
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["ordered_numbers"]


START_AT_FIVE = _parts(
    _p("Verify the backup.", num=(9, 0)) + _p("Cut over.", num=(9, 0)),
    numbering=('<w:numbering %s><w:abstractNum w:abstractNumId="5">'
               '<w:lvl w:ilvl="0"><w:start w:val="5"/>'
               '<w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
               '<w:num w:numId="9"><w:abstractNumId w:val="5"/></w:num>'
               '</w:numbering>' % W))


def test_a_list_declared_to_start_at_five_is_graded():
    assert docx_source_structure(START_AT_FIVE)["ordered_numbers"] == [5, 6]
    md = ooxml_markdown("docx", START_AT_FIVE)
    assert md.startswith("5. Verify the backup.\n6. Cut over.")
    assert _verdict(START_AT_FIVE)["gate"] == "pass"
    old, new = _blind(START_AT_FIVE, "1. Verify the backup.\n2. Cut over.\n")
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    assert new["deltas"][0]["markdown"] == [1, 2]


STYLE_NUMBERED = _parts(
    _p("Stop the writer.", style="NimbusStep")
    + _p("Flush the queue.", style="NimbusStep")
    + _p("Start the writer.", style="NimbusStep"),
    styles=_styles(
        '<w:style w:type="paragraph" w:styleId="ListNumber">'
        '<w:name w:val="List Number"/>'
        '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
        '</w:style>'
        '<w:style w:type="paragraph" w:styleId="NimbusStep">'
        '<w:name w:val="Nimbus Step"/><w:basedOn w:val="ListNumber"/></w:style>'))


def test_numbering_carried_by_a_paragraph_style_is_seen_by_BOTH_sides():
    # The symmetry hole reappearing inside the structure gate: `_p_style_info` and
    # `_ppr_props` each read only the paragraph's own w:pPr, so the list vanished
    # from the markdown AND from the ground truth, and the two agreed perfectly
    # about a document that had lost its procedure.
    facts = docx_source_structure(STYLE_NUMBERED)
    assert facts["ordered_items"] == 3
    assert facts["ordered_numbers"] == [1, 2, 3]
    assert _verdict(STYLE_NUMBERED)["gate"] == "pass"

    flat = "Stop the writer.\n\nFlush the queue.\n\nStart the writer.\n"
    old, new = _blind(STYLE_NUMBERED, flat)
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    assert "ordered_numbers" in [d["fact"] for d in new["deltas"]]


# ------------------------------------- a row that does not start at grid column 0

GRID_BEFORE = _parts(
    '<w:tbl><w:tr>%s%s%s</w:tr>'
    '<w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>%s%s</w:tr></w:tbl>'
    % ("<w:tc>%s</w:tc>" % _p("Register"), "<w:tc>%s</w:tc>" % _p("Offset"),
       "<w:tc>%s</w:tc>" % _p("Access"), "<w:tc>%s</w:tc>" % _p("0x04"),
       "<w:tc>%s</w:tc>" % _p("RO")),
    numbering=False)


def test_a_row_starting_at_grid_column_one_is_graded():
    # Both sides stopped at w:trPr, so the gate AGREED with the bug: a register
    # named "0x04" published "RO" as its offset and every gate read green.
    facts = docx_source_structure(GRID_BEFORE)
    assert facts["tables"][0]["cells"][1] == ((), ("0x04",), ("ro",))
    md = ooxml_markdown("docx", GRID_BEFORE)
    assert "|  | 0x04 | RO |" in md
    assert _verdict(GRID_BEFORE)["gate"] == "pass"

    shifted = ("| Register | Offset | Access |\n| --- | --- | --- |\n"
               "| 0x04 | RO |  |\n")
    old, new = _blind(GRID_BEFORE, shifted)
    assert old["recall"] == 1.0 and old["valid"] is True, "the token gate is blind"
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["tables"]


# ------------------------------------------------- a heading style nobody named

DERIVED_HEADINGS = _parts(
    _p("Bring-up", style="NimbusH1") + _p("Body prose.")
    + _p("Straps", style="NimbusH1") + _p("More prose."),
    styles=_styles('<w:style w:type="paragraph" w:styleId="NimbusH1">'
                   '<w:name w:val="Nimbus Section"/>'
                   '<w:basedOn w:val="Heading1"/></w:style>'),
    numbering=False)


def test_a_custom_heading_style_is_a_heading_on_both_sides():
    # `NimbusH1 basedOn="Heading1"` is two <h1> to LibreOffice. Neither side
    # followed w:basedOn, so a two-section document became one structureless blob
    # and the report said `status: ok`.
    assert docx_source_structure(DERIVED_HEADINGS)["headings"] == {1: 2}
    md = ooxml_markdown("docx", DERIVED_HEADINGS)
    assert "# Bring-up" in md and "# Straps" in md
    assert _verdict(DERIVED_HEADINGS)["gate"] == "pass"

    blob = "Bring-up\n\nBody prose.\n\nStraps\n\nMore prose.\n"
    old, new = _blind(DERIVED_HEADINGS, blob)
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    # Flattening two sections into one blob costs both heading facts: the histogram
    # empties, and the ordered path loses the two entries that named the sections.
    assert [d["fact"] for d in new["deltas"]] == ["headings", "heading_path"]


def test_a_reordered_procedure_is_a_failure():
    parts = _parts(_p("Drain the tier.", num=(2, 0))
                   + _p("Run the migration.", num=(2, 0))
                   + _p("Re-enable writes.", num=(2, 0)))
    faithful = ooxml_markdown("docx", parts)
    assert _verdict(parts)["gate"] == "pass"

    swapped = (faithful.replace("1. Drain the tier.", "@@")
                       .replace("2. Run the migration.", "2. Drain the tier.")
                       .replace("@@", "1. Run the migration."))
    old = conversion_report(ooxml_source_text("docx", parts), swapped)
    assert old["recall"] == 1.0 and old["valid"] is True, "the token gate is blind"

    verdict = structure_fidelity_report(md_structure(swapped),
                                        docx_source_structure(parts))
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["list_item_words"]


# ---------------------------------- emphasis that stops at the end of its block
#
# The span counter used to coalesce over the whole document as one flat run stream,
# with no notion of the block a run lives in. Both halves of the gate broke: it
# failed a byte-perfect conversion of an ordinary bold table header, and — the
# blocker — it PASSED a conversion that had dropped the bold on every block but the
# first, because the deflated source count matched the damaged markdown exactly.

BOLD_BLOCKS = _parts(
    _p("Warning", rpr="<w:rPr><w:b/></w:rPr>")
    + _p("Caution", rpr="<w:rPr><w:b/></w:rPr>")
    + _p("Danger", rpr="<w:rPr><w:b/></w:rPr>"), numbering=False)


def test_three_bold_paragraphs_convert_faithfully():
    assert docx_source_structure(BOLD_BLOCKS)["strong"] == 3
    assert _verdict(BOLD_BLOCKS)["gate"] == "pass"


def test_emphasis_dropped_on_every_block_but_the_first_is_a_failure():
    old, new = _blind(BOLD_BLOCKS, "**Warning**\n\nCaution\n\nDanger\n")
    assert old["recall"] == 1.0 and old["valid"] is True, "the token gate is blind"
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["strong"]
    assert new["deltas"][0]["source"] == 3 and new["deltas"][0]["markdown"] == 1


BOLD_HEADER = _parts(
    '<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
    '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
    % (_p("Signal", rpr="<w:rPr><w:b/></w:rPr>"),
       _p("Width", rpr="<w:rPr><w:b/></w:rPr>"), _p("clk"), _p("1")),
    numbering=False)


def test_a_bold_table_header_is_not_a_gate_failure():
    # Close to universal in engineering documentation, and it used to hard-fail.
    assert _verdict(BOLD_HEADER)["gate"] == "pass", _verdict(BOLD_HEADER)["deltas"]


def test_a_header_cell_that_lost_its_bold_is_a_failure():
    old, new = _blind(BOLD_HEADER,
                      "| **Signal** | Width |\n| --- | --- |\n| clk | 1 |\n")
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["strong"]


# ------------------------------- a cell's own paragraph boundary is real content

WELDABLE_CELL = _parts(
    '<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
    '<w:tr><w:tc>%s</w:tc><w:tc>%s%s</w:tc></w:tr></w:tbl>'
    % (_p("Tier"), _p("Owner"), _p("payments"), _p("Primary"), _p("Rota")),
    numbering=False)


def test_a_cell_whose_paragraphs_were_welded_into_one_word_is_a_failure():
    # The ground truth used to erase the boundary on ITS side too ("PrimaryRota"),
    # so a converter that destroyed the cell's block structure compared equal and
    # the `cells` fact — which exists to catch cell-content corruption — was blind
    # to it.
    cells = docx_source_structure(WELDABLE_CELL)["tables"][0]["cells"]
    assert cells[1][1] == ("primary", "rota"), "no fabricated token on the source side"
    _old, new = _blind(WELDABLE_CELL,
                       "| Tier | Owner |\n| --- | --- |\n| payments | PrimaryRota |\n")
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["tables"]


# ------------------------------------------ fences, diagrams and figures agree

def test_a_blank_line_inside_a_listing_is_not_a_second_listing():
    code = _styles('<w:style w:type="paragraph" w:styleId="SourceCode">'
                   '<w:name w:val="Source Code"/></w:style>')
    parts = _parts(_p("$ make verify", style="SourceCode") + "<w:p/>"
                   + _p("ok", style="SourceCode"), styles=code, numbering=False)
    assert ooxml_markdown("docx", parts) == "```\n$ make verify\nok\n```\n"
    assert _verdict(parts)["gate"] == "pass", _verdict(parts)["deltas"]


def test_a_listing_really_split_in_two_is_still_seen():
    code = _styles('<w:style w:type="paragraph" w:styleId="SourceCode">'
                   '<w:name w:val="Source Code"/></w:style>')
    parts = _parts(_p("$ make verify", style="SourceCode") + _p("Then check the log.")
                   + _p("ok", style="SourceCode"), styles=code, numbering=False)
    assert docx_source_structure(parts)["code_blocks"] == 2
    assert _verdict(parts)["gate"] == "pass"
    old, new = _blind(parts, "```\n$ make verify\nThen check the log.\nok\n```\n")
    assert old["recall"] == 1.0
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["code_blocks"]


def test_a_smartart_diagram_agrees_end_to_end():
    diagram = ('<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/'
               'drawingml/2006/diagram" xmlns:a="http://schemas.openxmlformats.org/'
               'drawingml/2006/main"><dgm:ptLst>%s</dgm:ptLst></dgm:dataModel>'
               % "".join('<dgm:pt><dgm:t><a:p><a:r><a:t>%s</a:t></a:r></a:p>'
                         '</dgm:t></dgm:pt>' % t
                         for t in ("Ingest", "Validate", "Publish")))
    parts = _parts(_p("The pipeline has three stages."), numbering=False)
    parts["word/diagrams/data1.xml"] = diagram
    assert "- Ingest" in ooxml_markdown("docx", parts)
    assert _verdict(parts)["gate"] == "pass", _verdict(parts)["deltas"]
    # ...and a converter that lost the diagram's points is still caught.
    old, new = _blind(parts, "The pipeline has three stages.\n\n## Diagrams\n\n"
                             "Ingest Validate Publish\n")
    assert old["recall"] == 1.0
    assert new["gate"] == "fail"


def test_an_embedded_svg_figure_section_agrees_end_to_end():
    parts = _parts(_p("Body."), numbering=False)
    parts["word/media/fig1.svg"] = ('<svg xmlns="http://www.w3.org/2000/svg">'
                                    '<text>Clock domain</text></svg>')
    assert "## Figures" in ooxml_markdown("docx", parts)
    assert _verdict(parts)["gate"] == "pass", _verdict(parts)["deltas"]


def test_a_nested_table_agrees_on_the_number_of_tables():
    inner = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
             '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
             % (_p("k"), _p("v"), _p("x"), _p("y")))
    parts = _parts('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
                   '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
                   % (_p("Name"), _p("Detail"), _p("thing"), inner),
                   numbering=False)
    src = docx_source_structure(parts)
    md = md_structure(ooxml_markdown("docx", parts))
    assert len(src["tables"]) == len(md["tables"]) == 1


# ------------------------------------- emphasis carried by a STYLE, on both sides
#
# The ground truth reads emphasis out of the style cascade, the way Word resolves
# it. For one commit it was the only side that did: the converter read DIRECT run
# formatting only, so it deleted emphasis arriving through w:rStyle or a paragraph
# style, and the two implementations disagreed — which was the gate working, not a
# gate to be silenced. `_ooxml_md._run_marks` has since learned the same cascade,
# INDEPENDENTLY (its own reader over w:style/w:rPr, its own basedOn resolution), so
# the disagreement is closed from the converter's end. If this ever goes red again,
# fix whichever side stopped reading the document — never restore agreement by
# making a side blind.

STYLE_EMPHASIS = _parts(
    _p("Save", rpr='<w:rPr><w:rStyle w:val="Strong"/></w:rPr>')
    + _p("Quoted line.", style="Quote"),
    styles=_styles('<w:style w:type="character" w:styleId="Strong">'
                   '<w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>'
                   '<w:style w:type="paragraph" w:styleId="Quote">'
                   '<w:name w:val="Quote"/><w:rPr><w:i/></w:rPr></w:style>'),
    numbering=False)


def test_style_borne_emphasis_is_measured_not_agreed_away():
    src = docx_source_structure(STYLE_EMPHASIS)
    assert (src["strong"], src["em"]) == (1, 1), "the document says so"
    # A markdown that CARRIES the emphasis is what a faithful conversion looks like.
    good = structure_fidelity_report(md_structure("**Save**\n\n*Quoted line.*\n"), src)
    assert good["gate"] == "pass", good["deltas"]
    # ...and it is now what the converter emits: `_ooxml_md._run_marks` resolves the
    # same cascade (independently), so the two sides agree BECAUSE the document's
    # emphasis survives the conversion, not because either side stopped looking.
    verdict = _verdict(STYLE_EMPHASIS)
    assert verdict["gate"] == "pass", verdict["deltas"]
    assert ooxml_markdown("docx", STYLE_EMPHASIS) == "**Save**\n\n*Quoted line.*\n"


# ---------------------------------------------------------------- ordered prose
#
# THE HOLE THIS SECTION CLOSES. Until `heading_path` existed, structure_fidelity had
# no ordered, text-bearing fact for any block that was not a list item or a table
# cell. `headings` is a level->count histogram and body paragraphs contribute
# nothing at all, so exchanging two section titles — prose reattached to the wrong
# chapter — passed BOTH hard gates with zero deltas, recall 1.0 and status "ok".
# It is the same blind spot `ordered_numbers` closed for procedures and `cells`
# closed for tables, applied to the last two constructs that never got it.
#
# INTERIM STATE, STATED PLAINLY. `md_structure` emits `heading_path` and the gate
# compares it, but `backend.ingest.docx_source_structure` does not produce it yet,
# so on a real docx the fact is reported in `unmeasured` rather than graded. The
# tests below therefore split in two: the ones that pin the COMPARISON supply the
# fact explicitly, and `test_the_ground_truth_does_not_supply_heading_path_yet`
# pins the honest gap. When the ground-truth half lands, that last test is the one
# to delete, and `test_a_real_docx_on_disk_agrees_end_to_end` re-baselines from
# compared 9 / unmeasured ["heading_path", "thematic_breaks"] to compared 10 with
# no `unmeasured` key. Do NOT close the gap by removing the fact.

def _spec_markdown():
    """The corpus spec document's real markdown, and its structural ground truth."""
    parts = _corpus_spec_parts()
    return ooxml_markdown("docx", parts), docx_source_structure(parts)


def _corpus_spec_parts():
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "spec.docx")
    _gen_corpus().build_spec_docx(path)
    with zipfile.ZipFile(path) as zf:
        return dict((n, zf.read(n).decode("utf-8", "replace")) for n in zf.namelist()
                    if n.endswith(".xml") or n.endswith(".rels"))


def test_exchanging_two_section_titles_is_a_failure():
    md, source = _spec_markdown()
    # The ground truth as it will read once it carries the fact. Taken from the
    # FAITHFUL markdown, which is what the converter-blind side must agree with.
    source = dict(source)
    source["heading_path"] = md_structure(md)["heading_path"]
    assert structure_fidelity_report(md_structure(md), source)["gate"] == "pass"

    damaged = (md.replace("# 1 Introduction\n", "# @@\n")
                 .replace("# 2 Clock architecture\n", "# 1 Introduction\n")
                 .replace("# @@\n", "# 2 Clock architecture\n"))
    assert damaged != md
    # Everything the gate used to look at is untouched: same tokens, same heading
    # histogram, same lists, same table.
    assert (md_structure(damaged)["headings"] == md_structure(md)["headings"])
    assert conversion_report(ooxml_source_text("docx", _corpus_spec_parts()),
                             damaged)["recall"] == 1.0
    verdict = structure_fidelity_report(md_structure(damaged), source)
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["heading_path"]


def test_a_heading_whose_text_was_swapped_with_body_prose_is_a_failure():
    parts = _parts(_p("Register map", style="Heading1") + _p("Every offset is byte."))
    source = docx_source_structure(parts)
    faithful = ooxml_markdown("docx", parts)
    # The ground truth derives `heading_path` from the XML itself. It used to be
    # injected here from the markdown side, which made this test a check on the
    # comparator alone; reading the real source side makes it a check on the gate.
    assert structure_fidelity_report(md_structure(faithful), source)["gate"] == "pass"
    swapped = "# Every offset is byte.\n\nRegister map\n"
    verdict = structure_fidelity_report(md_structure(swapped), source)
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["heading_path"]


def test_the_ground_truth_supplies_the_ordered_heading_fact(tmp_path):
    # This was the last half-open finding of the review. `md_structure` published
    # `heading_path` before `docx_source_structure` had any opinion about it, so on
    # a REAL docx the ordered fact reported UNMEASURED — and two exchanged section
    # titles still passed both hard gates. An unmeasured fact is an honest skip, but
    # a skip is not a check, so the hole stayed open until the source side could
    # answer. It can now, and this test is the proof on a real document rather than
    # a hand-built fixture.
    path = str(tmp_path / "spec.docx")
    _gen_corpus().build_spec_docx(path)
    with zipfile.ZipFile(path) as zf:
        parts = dict((n, zf.read(n).decode("utf-8", "replace")) for n in zf.namelist()
                     if n.endswith(".xml") or n.endswith(".rels"))
    source = docx_source_structure(parts)
    faithful = ooxml_markdown("docx", parts)

    # The two heading facts are written together and must never drift: one entry in
    # the ordered path for every heading the histogram counted.
    assert len(source["heading_path"]) == sum(source["headings"].values())
    assert source["heading_path"] == md_structure(faithful)["heading_path"]

    verdict = structure_fidelity_report(md_structure(faithful), source)
    assert verdict["gate"] == "pass"
    assert "unmeasured" not in verdict          # GRADED, not skipped

    # THE DAMAGE the fact exists for: exchange the titles of two same-level
    # sections. Every other fact is untouched — the level histogram is identical,
    # and so is the token multiset, which is exactly why the FIRST gate reads a
    # clean 1.0 over a document whose prose is now filed under the wrong chapter.
    heads = [ln for ln in faithful.splitlines() if ln.startswith("#")]
    first, second = heads[1], heads[2]
    hashes_a, title_a = first.split(" ", 1)
    hashes_b, title_b = second.split(" ", 1)
    swapped = faithful.replace(first, "\x01", 1)
    swapped = swapped.replace(second, "%s %s" % (hashes_b, title_a), 1)
    swapped = swapped.replace("\x01", "%s %s" % (hashes_a, title_b), 1)
    assert swapped != faithful

    old, new = _blind(parts, swapped)
    assert old["recall"] == 1.0 and old["valid"] is True
    assert new["gate"] == "fail"
    assert [d["fact"] for d in new["deltas"]] == ["heading_path"]


def test_a_paragraph_that_became_a_horizontal_rule_is_a_failure():
    # A body paragraph of `-----` is drawn as an <hr /> and its characters leave
    # the document. It carries no ASCII token, so recall reads a vacuous 1.0 and
    # said so for as long as no fact counted the break.
    faithful = "# Notes\n\nConfigure the PLL.\n\n\\-----\n\nThen verify.\n"
    source = md_structure(faithful)
    assert structure_fidelity_report(md_structure(faithful), source)["gate"] == "pass"
    damaged = faithful.replace("\\-----", "-----")
    verdict = structure_fidelity_report(md_structure(damaged), source)
    assert verdict["gate"] == "fail"
    assert [d["fact"] for d in verdict["deltas"]] == ["thematic_breaks"]


def test_a_multi_paragraph_table_cell_is_not_a_gate_failure():
    # The false FAIL that made the second gate unusable on real Word documents: a
    # GFM cell cannot hold a newline, so the converter joins the cell's paragraphs
    # with `<br>` — and the reader tokenised the tag into a phantom word `br` that
    # no source document can produce. Token recall was exactly 1.0 throughout, and
    # build_bundle dropped the document anyway.
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p("Signal"), _p("Notes"), _p("CLK"),
               _p("Free-running.") + _p("Do not gate.")))
    parts = _parts(body, numbering=False)
    md = ooxml_markdown("docx", parts)
    assert "Free-running.<br>Do not gate." in md
    verdict = _verdict(parts)
    assert verdict["gate"] == "pass", verdict["deltas"]
    # ... and a cell whose second paragraph really was dropped still fails.
    lost = md.replace("Free-running.<br>Do not gate.", "Free-running.")
    assert _verdict(parts, markdown=lost)["gate"] == "fail"


# ------------------------------------------------- what `compared` actually means

def test_compared_counts_evidence_not_the_fact_schema():
    # A one-paragraph memo exhibits no list, no table, no emphasis and no code.
    # Counting the fact NAMES the source supplied reported thirteen checks on a
    # document where twelve of them were 0 == 0 and could not have failed.
    parts = _parts(_p("Just one sentence."), numbering=False)
    verdict = _verdict(parts)
    assert verdict["gate"] == "pass"
    assert verdict["compared"] == 0
    # A document that exhibits something counts it — here TWO facts, because one
    # heading is evidence for the level histogram and for the ordered path alike.
    with_heading = _parts(_p("Bring-up", style="Heading1") + _p("Body."),
                          numbering=False)
    assert _verdict(with_heading)["compared"] == 2


def test_a_partial_ground_truth_names_what_it_did_not_measure():
    source = {"headings": {1: 1}, "strong": 0}
    verdict = structure_fidelity_report(md_structure("# Title\n"), source)
    assert verdict["gate"] == "pass"
    assert "tables" in verdict["unmeasured"]
    assert "headings" not in verdict["unmeasured"]
    # An entirely absent ground truth already says `unmeasured` in `method` and
    # `gate`; listing all fifteen facts there would be noise, not information.
    assert "unmeasured" not in structure_fidelity_report(md_structure("# T\n"), {})
