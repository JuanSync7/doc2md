"""
title: Unit — the converter-blind structural ground truth
kind: tests
layer: backend
summary: docx_source_structure reads the source's structure by its own route, mirrors only the converter's DECLARED policies, and measures every deliberate drop.
"""
import pytest

from backend.ingest import (docx_source_structure, markdown_to_text,
                            ooxml_markdown, ooxml_source_text, policy_drops)
from backend.validate import conversion_report, md_structure, structure_fidelity_report

pytestmark = pytest.mark.unit

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
STYLES = ('<w:styles %s>'
          '<w:style w:type="paragraph" w:styleId="Heading1">'
          '<w:name w:val="heading 1"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading2">'
          '<w:name w:val="heading 2"/></w:style>'
          '<w:style w:type="character" w:styleId="HTMLCode">'
          '<w:name w:val="HTML Code"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="SourceCode">'
          '<w:name w:val="Source Code"/></w:style>'
          '</w:styles>' % W)
NUMBERING = ('<w:numbering %s><w:abstractNum w:abstractNumId="0">'
             '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
             '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>'
             '</w:abstractNum>'
             '<w:num w:numId="2"><w:abstractNumId w:val="0"/></w:num>'
             '</w:numbering>' % W)
CODE = '<w:rPr><w:rStyle w:val="HTMLCode"/></w:rPr>'


def _run(text, rpr=""):
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, text)


def _p(runs, style=None, num=None):
    pr = []
    if style:
        pr.append('<w:pStyle w:val="%s"/>' % style)
    if num:
        pr.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>'
                  % (num[1], num[0]))
    ppr = ("<w:pPr>%s</w:pPr>" % "".join(pr)) if pr else ""
    return "<w:p>%s%s</w:p>" % (ppr, runs)


def _parts(body, rels=""):
    parts = {"word/document.xml":
             '<w:document %s><w:body>%s</w:body></w:document>' % (W, body),
             "word/styles.xml": STYLES, "word/numbering.xml": NUMBERING}
    if rels:
        parts["word/_rels/document.xml.rels"] = rels
    return parts


# ------------------------------------------------------ it reads what is there

def test_it_reads_headings_lists_marks_and_tables():
    body = (_p(_run("Runbook"), style="Heading1")
            + _p(_run("Step one."), num=(2, 0))
            + _p(_run("A note."), num=(2, 1))
            + _p(_run("bold", "<w:rPr><w:b/></w:rPr>") + _run(" and ")
                 + _run("italic", "<w:rPr><w:i/></w:rPr>"))
            + _p(_run("make verify", CODE)))
    facts = docx_source_structure(_parts(body))
    assert facts["headings"] == {1: 1}
    assert facts["list_items"] == {0: 1, 1: 1}
    assert facts["ordered_items"] == 1 and facts["bullet_items"] == 1
    assert facts["strong"] == 1 and facts["em"] == 1
    assert facts["code_spans"] == 1


def test_adjacent_runs_with_the_same_marks_are_one_span():
    # Word splits a single word across runs at every property boundary. Counting
    # runs would report three bold spans where a reader sees one — and the
    # converter, which coalesces, would then be graded as wrong for being right.
    body = _p(_run("Dma", "<w:rPr><w:b/></w:rPr>")
              + _run("Arbiter", "<w:rPr><w:b/></w:rPr>")
              + _run("Unit", "<w:rPr><w:b/></w:rPr>"))
    assert docx_source_structure(_parts(body))["strong"] == 1
    assert ooxml_markdown("docx", _parts(body)).strip() == "**DmaArbiterUnit**"


def test_a_toggle_turned_off_is_not_formatting():
    body = _p(_run("plain", '<w:rPr><w:b w:val="0"/></w:rPr>'))
    assert docx_source_structure(_parts(body))["strong"] == 0


def test_the_previous_formatting_of_a_tracked_change_is_not_the_live_one():
    body = _p(_run("live", '<w:rPr><w:rPrChange><w:rPr><w:b/></w:rPr>'
                           '</w:rPrChange></w:rPr>'))
    assert docx_source_structure(_parts(body))["strong"] == 0


# ------------------------------------------------ it mirrors declared policies

def test_a_one_by_one_table_is_layout_scaffolding_not_a_table():
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc></w:tr></w:tbl>' % _p(_run("Framed note.")))
    assert docx_source_structure(_parts(body))["tables"] == []


def test_a_paragraph_with_no_text_of_its_own_emits_nothing():
    body = _p("") + _p(_run("Real."))
    facts = docx_source_structure(_parts(body))
    assert facts["headings"] == {} and facts["list_items"] == {}


def test_consecutive_code_paragraphs_are_one_fenced_block():
    body = (_p(_run("$ make verify"), style="SourceCode")
            + _p(_run("ok"), style="SourceCode")
            + _p(_run("After.")))
    assert docx_source_structure(_parts(body))["code_blocks"] == 1
    assert ooxml_markdown("docx", _parts(body)).count("```") == 2


def test_a_heading_inside_a_data_table_is_cell_content_not_a_heading():
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Shouted"), style="Heading1"), _p(_run("Note")),
               _p(_run("a")), _p(_run("b"))))
    facts = docx_source_structure(_parts(body))
    assert facts["headings"] == {}
    table = facts["tables"][0]
    assert (table["rows"], table["cols"], table["has_header"]) == (2, 2, True)
    # Cell CONTENT, in place — geometry alone cannot see a transposition.
    assert table["cells"] == [(("shouted",), ("note",)), (("a",), ("b",))]


def test_only_an_external_http_hyperlink_counts_as_a_link():
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
            '<Relationship Id="r1" Type="x" Target="https://example.invalid/a" '
            'TargetMode="External"/>'
            '<Relationship Id="r2" Type="x" Target="mailto:a@example.invalid" '
            'TargetMode="External"/></Relationships>')
    body = (_p('<w:hyperlink r:id="r1">%s</w:hyperlink>' % _run("web"))
            + _p('<w:hyperlink r:id="r2">%s</w:hyperlink>' % _run("mail")))
    body = body.replace("<w:p>", '<w:p xmlns:r="http://schemas.openxmlformats.org/'
                                 'officeDocument/2006/relationships">')
    facts = docx_source_structure(_parts(body, rels))
    assert facts["links"] == 1, "mailto: is not rendered as a link today"


# ------------------------------------------------------- it measures the drops

def test_every_deliberate_drop_is_counted():
    body = ('<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="3"/></w:tcPr>%s</w:tc>'
            '</w:tr></w:tbl>%s'
            % (_p(_run("Group")),
               _p('<w:ins>%s</w:ins><w:del><w:r><w:delText>gone</w:delText></w:r>'
                  '</w:del>%s' % (_run("added"), _run(" kept")))))
    codes = dict((w["code"], w) for w in
                 policy_drops(_parts(body), ["word/document.xml",
                                             "word/embeddings/book.xlsx"]))
    assert codes["flattened_table_spans"]["horizontal"] == 2
    assert codes["tracked_changes_resolved"]["insertions"] == 1
    assert codes["tracked_changes_resolved"]["deletions"] == 1
    assert codes["dropped_embedded_objects"]["parts"] == 1


def test_a_list_format_markdown_cannot_write_is_counted_not_shrugged_at():
    # CommonMark has one ordered marker, the decimal digit. An "A. B. C." procedure
    # can only come out "1. 2. 3.": the POSITION survives, the label does not, and
    # a loss nobody counted reads exactly like a bug.
    numbering = ('<w:numbering %s><w:abstractNum w:abstractNumId="7">'
                 '<w:lvl w:ilvl="0"><w:numFmt w:val="upperLetter"/></w:lvl>'
                 '</w:abstractNum>'
                 '<w:num w:numId="8"><w:abstractNumId w:val="7"/></w:num>'
                 '</w:numbering>' % W)
    parts = _parts(_p(_run("Isolate the node."), num=(8, 0))
                   + _p(_run("Drain the node."), num=(8, 0)))
    parts["word/numbering.xml"] = numbering
    codes = dict((w["code"], w) for w in policy_drops(parts, []))
    assert codes["decimalised_list_numbering"]["count"] == 2
    assert codes["decimalised_list_numbering"]["formats"] == ["upperLetter"]
    # ...and the position is genuinely kept, so "see step B" still lands on item 2.
    assert docx_source_structure(parts)["ordered_numbers"] == [1, 2]


def test_a_text_box_anchored_in_a_step_is_counted():
    box = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>'
           '</w:pPr>%s<w:r><w:pict><v:shape %s><v:textbox><w:txbxContent>%s'
           '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
           % (_run("Drain the tier."), 'xmlns:v="urn:schemas-microsoft-com:vml"',
              _p(_run("The drain is idempotent."))))
    parts = _parts(box)
    codes = dict((w["code"], w) for w in policy_drops(parts, []))
    assert codes["lifted_text_boxes"]["count"] == 1
    # A text box in ordinary prose is not a step losing its callout.
    plain = _parts('<w:p>%s<w:r><w:pict><v:shape %s><v:textbox><w:txbxContent>%s'
                   '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
                   % (_run("Prose."), 'xmlns:v="urn:schemas-microsoft-com:vml"',
                      _p(_run("Aside."))))
    assert "lifted_text_boxes" not in [w["code"] for w in policy_drops(plain, [])]


# ------------------------------------------------------ it reads the numbering

def test_it_numbers_the_steps_the_way_word_does():
    # Per instance, per level, with every deeper level restarting the moment a
    # shallower one advances — so the sub-step of step 2 is 2.1, not 2.4.
    body = (_p(_run("one"), num=(2, 0)) + _p(_run("note"), num=(2, 1))
            + _p(_run("two"), num=(2, 0)) + _p(_run("note"), num=(2, 1))
            + _p(_run("three"), num=(2, 0)))
    facts = docx_source_structure(_parts(body))
    assert facts["ordered_numbers"] == [1, 2, 3]     # ilvl 1 is a bullet level here
    assert facts["bullet_items"] == 2


def test_a_start_override_restarts_a_second_instance_of_one_abstract_list():
    numbering = ('<w:numbering %s><w:abstractNum w:abstractNumId="3">'
                 '<w:lvl w:ilvl="0"><w:start w:val="4"/>'
                 '<w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="10"><w:abstractNumId w:val="3"/></w:num>'
                 '<w:num w:numId="11"><w:abstractNumId w:val="3"/>'
                 '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/>'
                 '</w:lvlOverride></w:num></w:numbering>' % W)
    parts = _parts(_p(_run("a"), num=(10, 0)) + _p(_run("b"), num=(10, 0))
                   + _p(_run("c"), num=(11, 0)))
    parts["word/numbering.xml"] = numbering
    assert docx_source_structure(parts)["ordered_numbers"] == [4, 5, 1]


def test_a_row_that_skips_grid_columns_keeps_its_values_in_place():
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:trPr><w:gridBefore w:val="1"/><w:gridAfter w:val="1"/>'
            '</w:trPr><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Register")), _p(_run("Offset")), _p(_run("Access")),
               _p(_run("0x04"))))
    table = docx_source_structure(_parts(body))["tables"][0]
    assert table["cols"] == 3
    assert table["cells"][1] == ((), ("0x04",), ())
    rep, fid, md = _agrees(body)
    assert rep["recall"] == 1.0 and fid["gate"] == "pass", fid["deltas"]
    assert "|  | 0x04 |  |" in md


# ------------------------------------------- the two sides agree on real input

def _agrees(body, rels=""):
    parts = _parts(body, rels)
    md = ooxml_markdown("docx", parts)
    rep = conversion_report(ooxml_source_text("docx", parts), md)
    fid = structure_fidelity_report(md_structure(md), docx_source_structure(parts))
    return rep, fid, md


@pytest.mark.parametrize("payload", [
    "a_b *c* [d](e) &lt;f&gt; ~g~",
    "kubectl apply -f [env](prod).yaml",
    "grep -E '^\\[payments\\]' /etc/app.ini",
    "DB_MAX_CONN_LIMIT=64 --dry_run=true",
])
def test_a_code_span_survives_whatever_markdown_it_contains(payload):
    # A code span is emitted UNESCAPED (backticks already make it literal), so it
    # is the one place a markdown construct reaches the stripper intact. Stripping
    # links before code spans deleted the target of any link pattern inside one —
    # `kubectl apply -f [env](prod).yaml` lost a token and failed the recall gate,
    # with the converter entirely in the right.
    rep, fid, md = _agrees(_p(_run(payload, CODE)))
    assert rep["recall"] == 1.0 and rep["valid"] is True, md
    assert fid["gate"] == "pass", fid["deltas"]


def test_a_pipe_inside_a_code_span_inside_a_table_cell():
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Cmd")), _p(_run("Note")),
               _p(_run("kubectl get pods | grep payments", CODE)), _p(_run("pipes"))))
    rep, fid, md = _agrees(body)
    assert rep["recall"] == 1.0 and rep["valid"] is True
    assert fid["gate"] == "pass"
    assert "`kubectl get pods \\| grep payments`" in md
    assert "grep payments" in markdown_to_text(md)


def test_a_vertical_merge_repeats_its_value_on_both_sides():
    # The converter flattens a rowspan by REPEATING the restart value down its
    # continuation rows. The ground truth has to apply the same declared policy,
    # or every merged table would look like a defect the moment cell content
    # started being compared.
    body = ('<w:tbl>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>%s</w:tc>'
            '<w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>'
            '<w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Rota")), _p(_run("Check")), _p(_run("payments")),
               _p(_run("pool")), _p(_run("depth"))))
    cells = docx_source_structure(_parts(body))["tables"][0]["cells"]
    assert cells[1][0] == ("payments",)
    assert cells[2][0] == ("payments",), "the merge value must repeat, not blank"
    rep, fid, _md = _agrees(body)
    assert rep["recall"] == 1.0 and fid["gate"] == "pass", fid["deltas"]
