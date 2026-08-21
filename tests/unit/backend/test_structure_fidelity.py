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
    assert [d["fact"] for d in verdict["deltas"]] == ["headings"]


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
    # 13, not 11: `list_item_words` landed when a transposition attack showed
    # rows-and-columns could not see placement, and `ordered_numbers` when a
    # renumbered-but-equally-counted procedure showed neither could see a restart.
    assert verdict["compared"] == 13


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
    assert [d["fact"] for d in new["deltas"]] == ["headings"]


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
