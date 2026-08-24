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


# ------------------------------------------- a block is where a span has to stop

def test_a_span_never_crosses_a_paragraph_boundary():
    # Coalescing used to run over the whole document as one flat run stream, so two
    # paragraphs that merely ENDED and BEGAN with the same marks fused into a single
    # span. Markdown cannot carry emphasis across a blank line, so neither may this:
    # three bold paragraphs are three bold spans, and reporting one both failed the
    # correct markdown and PASSED a markdown that had dropped two of the three.
    bold = "<w:rPr><w:b/></w:rPr>"
    body = (_p(_run("Warning", bold)) + _p(_run("Caution", bold))
            + _p(_run("Danger", bold)))
    assert docx_source_structure(_parts(body))["strong"] == 3


def test_a_span_never_crosses_a_table_cell_boundary():
    # A bold header row is close to universal in engineering documentation, and it
    # is N spans, one per cell — never one span spanning the row.
    bold = "<w:rPr><w:b/></w:rPr>"
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Signal", bold)), _p(_run("Width", bold)),
               _p(_run("clk")), _p(_run("1"))))
    assert docx_source_structure(_parts(body))["strong"] == 2


def test_a_link_is_its_own_span_boundary_inside_one_paragraph():
    # A hyperlink's text is rendered on its own and then wrapped, so a bold lead-in,
    # a bold link and a bold tail are three spans in one paragraph, not one.
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="r1" Type="x" '
            'Target="https://example.invalid/a" TargetMode="External"/>'
            '</Relationships>')
    bold = "<w:rPr><w:b/></w:rPr>"
    body = _p(_run("See ", bold)
              + '<w:hyperlink r:id="r1">%s</w:hyperlink>' % _run("the spec", bold)
              + _run(" now", bold))
    body = body.replace("<w:p>", '<w:p xmlns:r="http://schemas.openxmlformats.org/'
                                 'officeDocument/2006/relationships">')
    parts = _parts(body, rels)
    assert docx_source_structure(parts)["strong"] == 3
    assert ooxml_markdown("docx", parts).count("**") == 6


# ----------------------------------------------- a block boundary is not a space

def test_a_multi_paragraph_cell_never_welds_two_words_into_one():
    # "Primary rota" + "Standby rota" read as the token `rotastandby`, a word that
    # is in no document anywhere — and the shipped report.json said the source
    # contained it. Worse, with the boundary gone from the source side a markdown
    # that had WELDED the two paragraphs into one word compared equal and passed.
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Tier")), _p(_run("Owner")), _p(_run("payments")),
               _p(_run("Primary rota")) + _p(_run("Standby rota"))))
    cells = docx_source_structure(_parts(body))["tables"][0]["cells"]
    assert cells[1][1] == ("primary", "rota", "standby", "rota")


def test_a_line_break_inside_a_list_item_is_a_word_boundary():
    body = _p('<w:r><w:t>Isolate the rail</w:t><w:br/><w:t>Wait</w:t></w:r>',
              num=(2, 1))
    facts = docx_source_structure(_parts(body))
    assert facts["list_item_words"] == [("isolate", "the", "rail", "wait")]


def test_a_cell_of_two_empty_paragraphs_is_still_blank():
    # The separator must be whitespace, or an "empty" cell stops reading as empty
    # and every blank-row/vMerge rule built on that reading moves.
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Tier")), _p(_run("Owner")), _p(_run("payments")),
               _p(""), _p("")))
    cells = docx_source_structure(_parts(body))["tables"][0]["cells"]
    assert cells[1][1] == ()


# ----------------------------------------------- a nested table is not a table

def test_a_nested_table_is_flattened_into_its_cell_not_counted_again():
    inner = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
             '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
             % (_p(_run("k")), _p(_run("v")), _p(_run("x")), _p(_run("y"))))
    body = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
            '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
            % (_p(_run("Name")), _p(_run("Detail")), _p(_run("thing")), inner))
    tables = docx_source_structure(_parts(body))["tables"]
    assert len(tables) == 1, "GFM has no cell that can hold a table"
    assert tables[0]["cells"][1][1] == ("k", "v", "x", "y")


def test_a_table_inside_a_layout_wrapper_is_still_a_table():
    # A 1x1 table is scaffolding that gets unwrapped, so what it wraps is top level.
    inner = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
             '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
             % (_p(_run("a")), _p(_run("b")), _p(_run("c")), _p(_run("d"))))
    body = '<w:tbl><w:tr><w:tc>%s%s</w:tc></w:tr></w:tbl>' % (_p(_run("Framed.")),
                                                              inner)
    assert len(docx_source_structure(_parts(body))["tables"]) == 1


# --------------------------------------------------- fences break where blocks do

def _code_parts(body):
    return _parts(body)


def test_a_blank_paragraph_inside_a_listing_does_not_split_the_fence():
    # A paragraph with no text of its own emits no block, so it cannot end a fence.
    # A blank line inside a shell transcript is part of the transcript.
    for gap in ("<w:p/>", _p("", style="SourceCode"),
                _p('<w:r><w:t xml:space="preserve">  </w:t></w:r>',
                   style="SourceCode")):
        body = (_p(_run("$ make verify"), style="SourceCode") + gap
                + _p(_run("ok"), style="SourceCode"))
        parts = _parts(body)
        assert docx_source_structure(parts)["code_blocks"] == 1, gap
        assert ooxml_markdown("docx", parts).count("```") == 2, gap


def test_a_table_between_two_listings_ends_the_first_fence():
    table = ('<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr>'
             '<w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
             % (_p(_run("a")), _p(_run("b")), _p(_run("c")), _p(_run("d"))))
    body = (_p(_run("one"), style="SourceCode") + table
            + _p(_run("two"), style="SourceCode"))
    parts = _parts(body)
    assert docx_source_structure(parts)["code_blocks"] == 2
    assert ooxml_markdown("docx", parts).count("```") == 4


def test_a_picture_between_two_listings_ends_the_first_fence():
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="r9" Type="x" '
            'Target="media/image1.png"/></Relationships>')
    pic = ('<w:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
           'relationships"><w:r><w:drawing><a:blip r:embed="r9"/></w:drawing>'
           '</w:r></w:p>')
    body = (_p(_run("one"), style="SourceCode") + pic
            + _p(_run("two"), style="SourceCode"))
    parts = _parts(body, rels)
    assert docx_source_structure(parts)["code_blocks"] == 2
    assert ooxml_markdown("docx", parts, True).count("```") == 4


# ----------------------------------------- emphasis is read from the DOCUMENT

_MARK_STYLES = ('<w:styles %s>'
                '<w:style w:type="paragraph" w:styleId="Heading1">'
                '<w:name w:val="heading 1"/><w:rPr><w:b/></w:rPr></w:style>'
                '<w:style w:type="paragraph" w:styleId="SourceCode">'
                '<w:name w:val="Source Code"/></w:style>'
                '<w:style w:type="character" w:styleId="Strong">'
                '<w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>'
                '<w:style w:type="character" w:styleId="Louder">'
                '<w:name w:val="Louder"/><w:basedOn w:val="Strong"/></w:style>'
                '<w:style w:type="paragraph" w:styleId="Quote">'
                '<w:name w:val="Quote"/><w:rPr><w:i/></w:rPr></w:style>'
                '</w:styles>' % W)


def _mark_parts(body):
    return {"word/document.xml":
            '<w:document %s><w:body>%s</w:body></w:document>' % (W, body),
            "word/styles.xml": _MARK_STYLES, "word/numbering.xml": NUMBERING}


def test_emphasis_carried_by_a_character_style_is_emphasis():
    # HTML->Word and pandoc's docx writer never emit <w:b/>; they emit this. So
    # does Word's own Styles gallery, which is the accessibility-recommended way.
    body = _p(_run("Press ") + _run("Save", '<w:rPr><w:rStyle w:val="Strong"/></w:rPr>')
              + _run(" now."))
    assert docx_source_structure(_mark_parts(body))["strong"] == 1


def test_a_character_style_inherits_its_emphasis_through_basedOn():
    body = _p(_run("Save", '<w:rPr><w:rStyle w:val="Louder"/></w:rPr>'))
    assert docx_source_structure(_mark_parts(body))["strong"] == 1


def test_emphasis_carried_by_a_paragraph_style_is_emphasis():
    # Word's stock Quote / Intense Quote / Caption / Subtitle all keep italic here.
    body = _p(_run("Quoted line."), style="Quote")
    assert docx_source_structure(_mark_parts(body))["em"] == 1


def test_a_heading_styles_own_bold_is_the_heading_not_a_span():
    # Every stock Heading1..9 carries <w:b/>. `# Title` already renders bold, so
    # counting it would demand `# **Title**` of every heading in every document.
    body = _p(_run("Bring-up"), style="Heading1")
    facts = docx_source_structure(_mark_parts(body))
    assert facts["headings"] == {1: 1} and facts["strong"] == 0


def test_a_fenced_line_carries_no_inline_marks():
    # Inside a fence `**` is two asterisks, not bold: there is no span to lose.
    body = _p(_run("make", "<w:rPr><w:b/></w:rPr>") + _run(" verify"),
              style="SourceCode")
    parts = _mark_parts(body)
    assert docx_source_structure(parts)["strong"] == 0
    assert "**" not in ooxml_markdown("docx", parts)


def test_a_run_can_turn_its_styles_emphasis_back_off():
    # Word toggles are tri-state in BOTH directions.
    body = _p(_run("Plain", '<w:rPr><w:rStyle w:val="Strong"/>'
                            '<w:b w:val="0"/></w:rPr>'))
    assert docx_source_structure(_mark_parts(body))["strong"] == 0


# --------------------------------------- embedded sections, as the document has them

_DIAGRAM_XML = (
    '<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/'
    'diagram" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<dgm:ptLst>%s</dgm:ptLst></dgm:dataModel>'
    % "".join('<dgm:pt><dgm:t><a:p><a:r><a:t>%s</a:t></a:r></a:p></dgm:t></dgm:pt>' % t
              for t in ("Ingest", "Validate", "Publish")))
_CHART_XML = ('<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/'
              '2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/'
              '2006/main"><c:title><a:t>%s</a:t></c:title></c:chartSpace>')
_SVG_XML = '<svg xmlns="http://www.w3.org/2000/svg"><text>Clock domain</text></svg>'


def _embedded_parts(**extra):
    parts = _parts(_p(_run("Body.")))
    parts.update(extra)
    return parts


def test_a_smartart_diagram_is_a_bullet_list_not_a_bare_heading():
    facts = docx_source_structure(
        _embedded_parts(**{"word/diagrams/data1.xml": _DIAGRAM_XML}))
    assert facts["headings"] == {2: 1}
    assert facts["bullet_items"] == 3 and facts["list_items"] == {0: 3}
    assert facts["list_item_words"] == [("ingest",), ("validate",), ("publish",)]


def test_two_chart_parts_are_one_charts_section():
    facts = docx_source_structure(_embedded_parts(**{
        "word/charts/chart1.xml": _CHART_XML % "Latency",
        "word/charts/chart2.xml": _CHART_XML % "Throughput"}))
    assert facts["headings"] == {2: 1}


def test_a_chart_that_shows_nothing_is_not_a_section():
    facts = docx_source_structure(_embedded_parts(**{
        "word/charts/chart1.xml": '<c:chartSpace xmlns:c="http://schemas.'
                                  'openxmlformats.org/drawingml/2006/chart"/>'}))
    assert facts["headings"] == {}


def test_an_embedded_svg_with_labels_is_a_figures_section():
    facts = docx_source_structure(
        _embedded_parts(**{"word/media/fig1.svg": _SVG_XML}))
    assert facts["headings"] == {2: 1}


def test_the_embedded_section_kinds_do_not_cancel_each_other_out():
    # Two charts used to over-count by one and an SVG figure to under-count by one,
    # so a document holding both agreed with the markdown by accident.
    facts = docx_source_structure(_embedded_parts(**{
        "word/media/fig1.svg": _SVG_XML,
        "word/charts/chart1.xml": _CHART_XML % "Latency",
        "word/charts/chart2.xml": _CHART_XML % "Throughput"}))
    assert facts["headings"] == {2: 2}
