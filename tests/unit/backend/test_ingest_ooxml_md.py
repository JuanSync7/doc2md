"""
title: Unit — OOXML -> markdown deterministic converters
kind: tests
layer: backend
summary: docx/pptx/xlsx parts convert to structured markdown that passes the lossless gate.
"""
# Pure policy on parts dicts ({part_name: xml_string}) — no disk, no zipfile.
# Every kitchen-sink test ends with conversion_report(source_text, markdown)
# asserting valid=True: the converter is always graded against the independent
# exhaustive ground truth, exactly as office_convert.py will grade it.
from backend.ingest import (docx_markdown, pptx_markdown, xlsx_markdown,
                            docx_source_text, pptx_source_text, xlsx_source_text,
                            ooxml_markdown, ooxml_source_text)
from backend.validate import conversion_report

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' + A +
     ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


# ------------------------------------------------------------------------ helpers

def _wp(text, style=None, num=None, ilvl=0):
    ppr = ""
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    if num is not None:
        ppr += '<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%s"/></w:numPr>' % (ilvl, num)
    if ppr:
        ppr = "<w:pPr>%s</w:pPr>" % ppr
    return "<w:p>%s<w:r><w:t>%s</w:t></w:r></w:p>" % (ppr, text)


def _wdoc(body):
    return '<w:document %s %s><w:body>%s</w:body></w:document>' % (W, MC, body)


STYLES = ('<w:styles %s>'
          '<w:style w:type="paragraph" w:styleId="Heading1">'
          '<w:name w:val="heading 1"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading2">'
          '<w:name w:val="heading 2"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Outlined">'
          '<w:name w:val="My Section Style"/>'
          '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
          '</w:styles>' % W)

NUMBERING = ('<w:numbering %s>'
             '<w:abstractNum w:abstractNumId="10">'
             '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
             '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>'
             '</w:abstractNum>'
             '<w:abstractNum w:abstractNumId="20">'
             '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
             '</w:abstractNum>'
             '<w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>'
             '<w:num w:numId="2"><w:abstractNumId w:val="20"/></w:num>'
             '</w:numbering>' % W)


def _wcell(*paras):
    return "<w:tc>%s" % "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % t
                                for t in paras) + "</w:tc>"


# --------------------------------------------------------------------------- docx

def test_docx_headings_from_style_names_and_outline_level():
    doc = _wdoc(_wp("Overview", style="Heading1") + _wp("Clocking", style="Heading2")
                + _wp("Deep Dive", style="Outlined") + _wp("Plain prose."))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": STYLES})
    assert "# Overview" in md
    assert "## Clocking" in md
    assert "### Deep Dive" in md            # outlineLvl 2 -> level 3
    assert "\n\nPlain prose.\n" in md or md.endswith("Plain prose.\n")


def test_docx_split_runs_stay_verbatim():
    doc = _wdoc("<w:p>" + "".join("<w:r><w:t>%s</w:t></w:r>" % s
                                  for s in ("Fo", "oW", "id", "get"))
                + "</w:p>")
    md = docx_markdown({"word/document.xml": doc})
    assert "FooWidget" in md


def test_docx_bullet_and_numbered_lists_with_nesting():
    doc = _wdoc(_wp("first bullet", num="1") + _wp("nested bullet", num="1", ilvl=1)
                + _wp("step one", num="2"))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING})
    assert "- first bullet\n  - nested bullet" in md
    assert "1. step one" in md


def test_docx_numid_zero_is_not_a_list():
    doc = _wdoc(_wp("plain again", num="0"))
    md = docx_markdown({"word/document.xml": doc})
    assert "- plain" not in md and "plain again" in md


def test_docx_table_renders_gfm_with_separator_and_escaped_pipes():
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_wcell("Reg"), _wcell("Meaning"),
              _wcell("CTRL"), _wcell("enable|disable select")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "| Reg | Meaning |" in md
    assert "| --- | --- |" in md
    assert "| CTRL | enable\\|disable select |" in md


def test_docx_gridspan_pads_columns_and_multiparagraph_cells_use_br():
    tbl = ('<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
           '<w:p><w:r><w:t>Spanning header</w:t></w:r></w:p></w:tc></w:tr>'
           "<w:tr>%s%s</w:tr></w:tbl>"
           % (_wcell("left"), _wcell("line one", "line two")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "| Spanning header |  |" in md
    assert "| left | line one<br>line two |" in md


def _vcell(text=None, vmerge=None):
    # vmerge=None -> plain cell; "restart" -> start of a vertical merge; "" -> continue
    tcpr = ('<w:tcPr><w:vMerge%s/></w:tcPr>'
            % ('' if vmerge == "" else ' w:val="%s"' % vmerge)) if vmerge is not None else ""
    body = "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % text if text is not None else "<w:p/>"
    return "<w:tc>%s%s</w:tc>" % (tcpr, body)


def test_docx_vmerge_forward_fills_the_restart_value_into_continuation_rows():
    # A vertical merge: "A" spans three rows. GFM has no rowspan, so each row
    # repeats "A" -> every row is self-contained (better for row-wise RAG chunking).
    tbl = ("<w:tbl>"
           "<w:tr>%s%s</w:tr>"
           "<w:tr>%s%s</w:tr>"
           "<w:tr>%s%s</w:tr>"
           "</w:tbl>"
           % (_vcell("A", "restart"), _wcell("apple"),
              _vcell(vmerge=""), _wcell("banana"),
              _vcell(vmerge=""), _wcell("cherry")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "| A | apple |" in md
    assert "| A | banana |" in md
    assert "| A | cherry |" in md


def test_docx_genuinely_empty_cell_is_not_over_filled():
    # A blank cell with NO vMerge must stay blank -- we only forward-fill true
    # merge-continuations, never ordinary empty cells.
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_wcell("X"), _wcell("Y"), _vcell(text=None), _wcell("Z")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "|  | Z |" in md          # blank stays blank
    assert "| X | Z |" not in md     # NOT filled from the row above


def test_docx_vmerge_forward_fill_keeps_recall_lossless():
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_vcell("Region", "restart"), _wcell("north"),
              _vcell(vmerge=""), _wcell("south")))
    parts = {"word/document.xml": _wdoc(tbl)}
    md = docx_markdown(parts)
    assert conversion_report(docx_source_text(parts), md)["valid"] is True


def test_docx_vmerge_continuation_with_stray_text_never_drops_tokens():
    # Defensive: a malformed continuation cell that carries its OWN text must keep
    # it (recall > repetition) rather than be overwritten by the restart value.
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_vcell("A", "restart"), _wcell("apple"),
              _vcell("stray", ""), _wcell("banana")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "stray" in md             # own text survives (no token loss)


def test_ooxml_svg_figure_labels_are_extracted_into_a_figures_section():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text>Clock domain</text><text><tspan>Reset</tspan> tree</text></svg>')
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    assert "## Figures" in md
    assert "Clock domain" in md and "Reset tree" in md


def test_ooxml_svg_label_leading_ordered_number_survives_the_lossless_gate():
    # SVG diagram callouts often read "1. Do X" / "2. Do Y". Emitted as figure
    # lines they must NOT be swallowed as ordered-list markers -- the leading digit
    # is real document text and has to round-trip through the gate.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text>1. Configure the PLL</text>'
           '<text>2. Enable the clock</text>'
           '<text>10) Final step</text></svg>')
    parts = {"word/document.xml": _wdoc(_wp("Body paragraph.")),
             "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    rep = conversion_report(ooxml_source_text("docx", parts), md)
    assert rep["valid"] is True
    assert rep["recall"] == 1.0


def test_ooxml_svg_label_leading_heading_and_quote_markers_survive_the_gate():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text># not a heading</text><text>&gt; not a quote</text>'
           '<text>- not a bullet</text></svg>')
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    rep = conversion_report(ooxml_source_text("docx", parts), md)
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_docx_sdt_wrapped_rows_and_paragraphs_are_not_lost():
    # Content controls wrap a row and a body paragraph; both must survive.
    tbl = ("<w:tbl><w:tr>%s</w:tr><w:sdt><w:sdtContent><w:tr>%s</w:tr>"
           "</w:sdtContent></w:sdt></w:tbl>" % (_wcell("visible"), _wcell("wrapped row")))
    body = tbl + "<w:sdt><w:sdtContent>%s</w:sdtContent></w:sdt>" % _wp("wrapped para")
    md = docx_markdown({"word/document.xml": _wdoc(body)})
    assert "wrapped row" in md
    assert "wrapped para" in md


def test_docx_alternatecontent_fallback_never_duplicates():
    body = ("<w:p><mc:AlternateContent><mc:Choice><w:r><w:t>once only</w:t></w:r>"
            "</mc:Choice><mc:Fallback><w:r><w:t>once only</w:t></w:r></mc:Fallback>"
            "</mc:AlternateContent></w:p>")
    md = docx_markdown({"word/document.xml": _wdoc(body)})
    assert md.count("once only") == 1
    src = docx_source_text({"word/document.xml": _wdoc(body)})
    assert src.count("once only") == 1


def test_docx_textbox_content_becomes_its_own_block():
    body = ("<w:p><w:r><w:t>Anchor paragraph.</w:t></w:r>"
            "<w:r><w:txbxContent><w:p><w:r><w:t>Boxed callout text.</w:t></w:r></w:p>"
            "</w:txbxContent></w:r></w:p>")
    md = docx_markdown({"word/document.xml": _wdoc(body)})
    assert "Anchor paragraph." in md
    assert "Boxed callout text." in md
    assert "Anchor paragraph. Boxed callout text." not in md   # separate blocks


def test_docx_hyperlink_renders_as_markdown_link():
    rels = ('<Relationships %s><Relationship Id="rId9" Target="https://example.com/spec"'
            ' TargetMode="External"/></Relationships>' % RELS)
    body = ('<w:p><w:hyperlink r:id="rId9"><w:r><w:t>the spec</w:t></w:r></w:hyperlink>'
            "</w:p>")
    doc = ('<w:document %s %s %s><w:body>%s</w:body></w:document>' % (W, MC, R, body))
    md = docx_markdown({"word/document.xml": doc,
                        "word/_rels/document.xml.rels": rels})
    assert "[the spec](https://example.com/spec)" in md
    # without rels the text still survives, unlinked
    md2 = docx_markdown({"word/document.xml": doc})
    assert "the spec" in md2 and "](" not in md2


def test_docx_footnotes_and_endnotes_sections():
    foot = ('<w:footnotes %s><w:footnote w:type="separator" w:id="0"><w:p/></w:footnote>'
            '<w:footnote w:id="1"><w:p><w:r><w:t>Per ISO 26262-5.</w:t></w:r></w:p>'
            "</w:footnote></w:footnotes>" % W)
    md = docx_markdown({"word/document.xml": _wdoc(_wp("Body.")),
                        "word/footnotes.xml": foot})
    assert "## Footnotes" in md
    assert "- Per ISO 26262-5." in md


def test_docx_malformed_or_missing_document_is_empty():
    assert docx_markdown({}) == ""
    assert docx_markdown({"word/document.xml": "<w:document"}) == ""
    assert docx_source_text({"word/document.xml": "<broken"}) == ""


def test_docx_kitchen_sink_passes_the_lossless_gate():
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_wcell("Signal"), _wcell("Width"), _wcell("irq_out"), _wcell("32")))
    foot = ('<w:footnotes %s><w:footnote w:id="1"><w:p><w:r>'
            "<w:t>footnote text here</w:t></w:r></w:p></w:footnote></w:footnotes>" % W)
    body = (_wp("Overview", style="Heading1") + _wp("The block has three clocks.")
            + _wp("bullet alpha", num="1") + tbl
            + "<w:p><w:r><w:txbxContent><w:p><w:r><w:t>boxed note</w:t></w:r></w:p>"
              "</w:txbxContent></w:r></w:p>")
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLES,
             "word/numbering.xml": NUMBERING, "word/footnotes.xml": foot}
    md = docx_markdown(parts)
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


# --------------------------------------------------------------------------- pptx

def _sp(text, ph=None, lvl=None):
    phx = '<p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr>' % (
        '<p:ph type="%s"/>' % ph if ph else "")
    ppr = '<a:pPr lvl="%d"/>' % lvl if lvl else ""
    return ('<p:sp>%s<p:txBody><a:p>%s<a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>'
            % (phx, ppr, text))


def _slide(shapes):
    return ('<p:sld %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>' % (P, shapes))


def test_pptx_slides_ordered_numerically_with_titles():
    parts = {
        "ppt/slides/slide2.xml": _slide(_sp("Second body")),
        "ppt/slides/slide10.xml": _slide(_sp("Tenth body")),
        "ppt/slides/slide1.xml": _slide(_sp("Widget Power Plan", ph="title")
                                        + _sp("Agenda item")),
    }
    md = pptx_markdown(parts)
    assert "## Slide 1 — Widget Power Plan" in md
    assert md.index("## Slide 1") < md.index("## Slide 2") < md.index("## Slide 10")
    assert "- Agenda item" in md


def test_pptx_chrome_placeholders_are_dropped_everywhere():
    parts = {"ppt/slides/slide1.xml":
             _slide(_sp("Real content") + _sp("7", ph="sldNum") + _sp("2026-07-03", ph="dt"))}
    md = pptx_markdown(parts)
    src = pptx_source_text(parts)
    assert "Real content" in md and "Real content" in src
    assert "- 7" not in md and "2026" not in md and "2026" not in src


def test_pptx_bullet_levels_indent():
    parts = {"ppt/slides/slide1.xml": _slide(_sp("top point") + _sp("sub point", lvl=1))}
    md = pptx_markdown(parts)
    assert "- top point\n  - sub point" in md


def test_pptx_table_renders_gfm():
    tbl = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
           '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Mode</a:t></a:r></a:p></a:txBody></a:tc>'
           '<a:tc><a:txBody><a:p><a:r><a:t>Power</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
           '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Sleep</a:t></a:r></a:p></a:txBody></a:tc>'
           '<a:tc><a:txBody><a:p><a:r><a:t>2 mW</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
           '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(tbl)})
    assert "| Mode | Power |" in md
    assert "| --- | --- |" in md
    assert "| Sleep | 2 mW |" in md


def test_pptx_group_shapes_recurse():
    grouped = "<p:grpSp>%s%s</p:grpSp>" % (_sp("inside group A"), _sp("inside group B"))
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(grouped)})
    assert "inside group A" in md and "inside group B" in md


def test_pptx_speaker_notes_attach_to_their_slide():
    notes = ('<p:notes %s><p:cSld><p:spTree>'
             '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
             '<p:txBody><a:p><a:r><a:t>Mention the lock budget.</a:t></a:r></a:p>'
             '</p:txBody></p:sp></p:spTree></p:cSld></p:notes>' % P)
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/notesSlides/notesSlide1.xml": notes}
    md = pptx_markdown(parts)
    assert "### Speaker notes" in md
    assert "Mention the lock budget." in md


def test_pptx_diagram_and_chart_parts_are_included_via_rels_or_orphans():
    diagram = ('<dgm %s><pt><t><a:p><a:r><a:t>Fetch</a:t></a:r></a:p></t></pt>'
               '<pt><t><a:p><a:r><a:t>Decode</a:t></a:r></a:p></t></pt></dgm>'
               % A).replace("dgm", "dgmRoot", 1).replace("</dgm>", "</dgmRoot>")
    chart = ('<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" %s>'
             '<c:chart><c:title><a:p><a:r><a:t>Yield trend</a:t></a:r></a:p></c:title>'
             '<c:ser><c:cat><c:pt><c:v>Q1</c:v></c:pt></c:cat>'
             '<c:val><c:pt><c:v>97.5</c:v></c:pt></c:val></c:ser></c:chart>'
             '</c:chartSpace>' % A)
    rels = ('<Relationships %s><Relationship Id="rId3" '
            'Target="../diagrams/data1.xml"/></Relationships>' % RELS)
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/slides/_rels/slide1.xml.rels": rels,
             "ppt/diagrams/data1.xml": diagram,
             "ppt/charts/chart1.xml": chart}      # chart unreferenced -> orphan section
    md = pptx_markdown(parts)
    assert "### Diagram" in md and "- Fetch" in md and "- Decode" in md
    assert "## Embedded objects" in md and "Yield trend" in md and "97.5" in md


def test_pptx_kitchen_sink_passes_the_lossless_gate():
    tbl = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
           '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>K</a:t></a:r></a:p></a:txBody></a:tc>'
           '<a:tc><a:txBody><a:p><a:r><a:t>V</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
           '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    parts = {
        "ppt/slides/slide1.xml": _slide(_sp("Roadmap", ph="title") + _sp("point one")
                                        + _sp("sub", lvl=1) + tbl),
        "ppt/slides/slide2.xml": _slide("<p:grpSp>%s</p:grpSp>" % _sp("grouped text")),
        "ppt/notesSlides/notesSlide1.xml":
            ('<p:notes %s><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr>'
             '<p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r>'
             '<a:t>note text</a:t></a:r></a:p></p:txBody></p:sp>'
             '</p:spTree></p:cSld></p:notes>' % P),
    }
    md = pptx_markdown(parts)
    rep = conversion_report(pptx_source_text(parts), md)
    assert rep["valid"] is True, rep


# --------------------------------------------------------------------------- xlsx

WB = ('<workbook %s %s><sheets>'
      '<sheet name="Summary" sheetId="1" r:id="rId1"/>'
      '<sheet name="FMEDA Detail" sheetId="2" r:id="rId2"/>'
      '</sheets></workbook>' % (S, R))
WB_RELS = ('<Relationships %s>'
           '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
           '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
           '</Relationships>' % RELS)
SST = ('<sst %s><si><t>Component</t></si><si><t>Failure rate</t></si>'
       '<si><r><t>PLL</t></r><r><t xml:space="preserve"> core</t></r></si>'
       '</sst>' % S)


def _sheet(rows):
    return '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (S, rows)


def test_xlsx_sheets_render_as_sections_with_tables():
    sheet1 = _sheet('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1.5E-9</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/sharedStrings.xml": SST, "xl/worksheets/sheet1.xml": sheet1}
    md = xlsx_markdown(parts)
    assert "## Summary" in md
    assert "## FMEDA Detail" in md          # named even when its part is absent
    assert "| Component | Failure rate |" in md
    assert "| --- | --- |" in md
    assert "| PLL core | 1.5E-9 |" in md    # rich-text si concatenated verbatim


def test_xlsx_cell_types_resolve():
    sheet = _sheet('<row><c t="inlineStr"><is><t>inline text</t></is></c>'
                   '<c t="b"><v>1</v></c><c t="e"><v>#DIV/0!</v></c>'
                   '<c><v>42</v></c><c t="str"><v>cached result</v></c></row>'
                   '<row><c t="b"><v>0</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    for expect in ("inline text", "TRUE", "#DIV/0!", "42", "cached result", "FALSE"):
        assert expect in md, expect


def test_xlsx_column_positions_align_from_refs():
    # B and D populated; A/C empty -> cells padded so the pipe geometry is stable.
    sheet = _sheet('<row r="1"><c r="B1"><v>10</v></c><c r="D1"><v>20</v></c></row>'
                   '<row r="2"><c r="B2"><v>30</v></c><c r="D2"><v>40</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    assert "|  | 10 |  | 20 |" in md
    assert "|  | 30 |  | 40 |" in md


def test_xlsx_shared_string_indices_never_leak_as_numbers():
    sheet = _sheet('<row><c t="s"><v>2</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/sharedStrings.xml": SST, "xl/worksheets/sheet1.xml": sheet}
    src = xlsx_source_text(parts)
    assert "PLL core" in src
    assert "2" not in src.split()          # the index itself is not content


def test_xlsx_drawing_textboxes_and_comments_included():
    drawing = ('<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/'
               'spreadsheetDrawing" %s><xdr:sp><xdr:txBody><a:p><a:r>'
               '<a:t>See errata sheet rev B</a:t></a:r></a:p></xdr:txBody></xdr:sp>'
               '</xdr:wsDr>' % A)
    comments = ('<comments %s><commentList><comment ref="A1"><text><r>'
                '<t>double-check this rate</t></r></text></comment></commentList>'
                '</comments>' % S)
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/drawings/drawing1.xml": drawing, "xl/comments1.xml": comments}
    md = xlsx_markdown(parts)
    assert "## Text boxes" in md and "See errata sheet rev B" in md
    assert "## Comments" in md and "double-check this rate" in md


def test_xlsx_kitchen_sink_passes_the_lossless_gate():
    sheet1 = _sheet('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1.5E-9</v></c></row>')
    sheet2 = _sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>note cell</t></is></c>'
                    '<c r="C1" t="b"><v>1</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/sharedStrings.xml": SST,
             "xl/worksheets/sheet1.xml": sheet1, "xl/worksheets/sheet2.xml": sheet2}
    md = xlsx_markdown(parts)
    rep = conversion_report(xlsx_source_text(parts), md)
    assert rep["valid"] is True, rep


# ------------------------------------------------- adversarial-review regressions

def test_docx_moved_away_content_is_not_duplicated():
    # w:moveFrom holds the OLD copy of relocated content; only w:moveTo is live.
    body = ('<w:p><w:moveFrom w:id="1"><w:r><w:t>stale copy</w:t></w:r></w:moveFrom>'
            '<w:moveTo w:id="2"><w:r><w:t>live copy</w:t></w:r></w:moveTo></w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md = docx_markdown(parts)
    src = docx_source_text(parts)
    assert "live copy" in md and "stale" not in md
    assert "live copy" in src and "stale" not in src


def test_docx_stale_tracked_change_style_is_ignored():
    # pPrChange carries the PREVIOUS style; it must not turn prose into headings.
    body = ('<w:p><w:pPr><w:pPrChange w:id="1"><w:pPr>'
            '<w:pStyle w:val="Heading1"/></w:pPr></w:pPrChange></w:pPr>'
            '<w:r><w:t>ordinary caption text</w:t></w:r></w:p>')
    md = docx_markdown({"word/document.xml": _wdoc(body), "word/styles.xml": STYLES})
    assert "# ordinary" not in md and "ordinary caption text" in md


def test_docx_1x1_layout_table_unwraps_to_body_blocks():
    tbl = ("<w:tbl><w:tr><w:tc>"
           + _wp("Framed section prose.", style="Heading2")
           + _wp("More framed prose.") + "</w:tc></w:tr></w:tbl>")
    md = docx_markdown({"word/document.xml": _wdoc(tbl), "word/styles.xml": STYLES})
    assert "|" not in md                      # no pipe table for layout scaffolding
    assert "## Framed section prose." in md
    assert "More framed prose." in md


def test_docx_deltext_and_instrtext_are_excluded_everywhere():
    body = ('<w:p><w:r><w:delText>deleted words</w:delText></w:r>'
            '<w:r><w:instrText>TOC \\o "1-3"</w:instrText></w:r>'
            '<w:r><w:t>kept words</w:t></w:r></w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    assert "deleted" not in docx_markdown(parts)
    assert "deleted" not in docx_source_text(parts)
    assert "kept words" in docx_markdown(parts)


def test_pptx_notes_bind_via_relationship_not_filename():
    # Spec-legal: slide2's notes live in notesSlide1.xml, bound by the slide rels.
    notes = ('<p:notes %s><p:cSld><p:spTree>'
             '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
             '<p:txBody><a:p><a:r><a:t>bound note text</a:t></a:r></a:p>'
             '</p:txBody></p:sp></p:spTree></p:cSld></p:notes>' % P)
    rels = ('<Relationships %s><Relationship Id="rId7" '
            'Target="../notesSlides/notesSlide1.xml"/></Relationships>' % RELS)
    parts = {"ppt/slides/slide2.xml": _slide(_sp("Body")),
             "ppt/slides/_rels/slide2.xml.rels": rels,
             "ppt/notesSlides/notesSlide1.xml": notes}
    md = pptx_markdown(parts)
    assert "bound note text" in md
    rep = conversion_report(pptx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_pptx_orphan_notes_part_is_rescued():
    notes = ('<p:notes %s><p:cSld><p:spTree>'
             '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
             '<p:txBody><a:p><a:r><a:t>orphan note</a:t></a:r></a:p>'
             '</p:txBody></p:sp></p:spTree></p:cSld></p:notes>' % P)
    parts = {"ppt/slides/slide2.xml": _slide(_sp("Body")),
             "ppt/notesSlides/notesSlide9.xml": notes}   # no slide9, no rels
    md = pptx_markdown(parts)
    assert "orphan note" in md
    rep = conversion_report(pptx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_xlsx_dotslash_rels_target_and_unlinked_sheet_are_not_lost():
    # './worksheets/…' is a spec-legal OPC target form; and a sheet part the
    # workbook resolution misses entirely must still render (and the ground
    # truth must still count it — it sweeps parts, not rels).
    rels = ('<Relationships %s>'
            '<Relationship Id="rId1" Target="./worksheets/sheet1.xml"/>'
            '</Relationships>' % RELS)
    sheet1 = _sheet('<row><c t="inlineStr"><is><t>dot slash cell</t></is></c></row>')
    orphan = _sheet('<row><c t="inlineStr"><is><t>orphan cell payload</t></is></c></row>')
    parts = {"xl/workbook.xml": ('<workbook %s %s><sheets>'
                                 '<sheet name="Rates" sheetId="1" r:id="rId1"/>'
                                 '</sheets></workbook>' % (S, R)),
             "xl/_rels/workbook.xml.rels": rels,
             "xl/worksheets/sheet1.xml": sheet1,
             "xl/worksheets/sheet7.xml": orphan}
    md = xlsx_markdown(parts)
    assert "dot slash cell" in md
    assert "orphan cell payload" in md
    src = xlsx_source_text(parts)
    assert "orphan cell payload" in src        # ground truth is rels-independent
    rep = conversion_report(src, md)
    assert rep["valid"] is True, rep


def test_xlsx_sheet_names_are_markdown_escaped_in_headings():
    wb = ('<workbook %s %s><sheets>'
          '<sheet name="Assignment_list_ecc" sheetId="1" r:id="rId1"/>'
          '</sheets></workbook>' % (S, R))
    parts = {"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": _sheet('<row><c><v>1</v></c></row>')}
    md = xlsx_markdown(parts)
    assert "## Assignment\\_list\\_ecc" in md
    rep = conversion_report(xlsx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_xlsx_multiletter_columns_align():
    # AA = column 26 (0-based); a bug in base-26 math is invisible to recall.
    sheet = _sheet('<row r="1"><c r="Z1"><v>25</v></c><c r="AA1"><v>26</v></c>'
                   '<c r="AB1"><v>27</v></c></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    row = [line for line in md.split("\n") if "25" in line][0]
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells.index("25") + 1 == cells.index("26")
    assert cells.index("26") + 1 == cells.index("27")
    assert cells.index("25") == 25


def test_strikethrough_tildes_survive():
    doc = _wdoc(_wp("range ~~deprecated~~ replaced"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "\\~\\~deprecated\\~\\~" in md
    assert conversion_report(docx_source_text(parts), md)["valid"] is True


def test_exact_recall_contract_one_token_in_hundreds_fails():
    words = " ".join("tok%d" % i for i in range(300))
    doc = _wdoc(_wp(words))
    parts = {"word/document.xml": doc}
    src = docx_source_text(parts)
    md = docx_markdown(parts).replace("tok177 ", "")   # lose exactly one token
    rep = conversion_report(src, md)
    assert rep["valid"] is False and rep["n_missing"] == 1


def test_gfm_table_trims_always_empty_trailing_columns():
    sheet = _sheet('<row r="1"><c r="A1"><v>1</v></c><c r="B1" t="inlineStr">'
                   '<is><t>x</t></is></c><c r="P1"/></row>'
                   '<row r="2"><c r="A2"><v>2</v></c><c r="B2" t="inlineStr">'
                   '<is><t>y</t></is></c><c r="P2"/></row>')
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    assert "| 1 | x |" in md and "| 1 | x |  |" not in md


# -------------------------------------------------------------- review comments

def test_docx_review_comments_render_and_count_in_ground_truth():
    comments = ('<w:comments %s><w:comment w:id="1" w:author="Rana">'
                '<w:p><w:r><w:t>Latency figure needs a source.</w:t></w:r></w:p>'
                '</w:comment></w:comments>' % W)
    parts = {"word/document.xml": _wdoc(_wp("Body text.")),
             "word/comments.xml": comments}
    md = docx_markdown(parts)
    assert "## Comments" in md
    assert "- Latency figure needs a source." in md
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_pptx_comments_render_legacy_and_modern():
    legacy = ('<p:cmLst %s><p:cm authorId="0"><p:text>Fix the diagram arrow.</p:text>'
              '</p:cm></p:cmLst>' % P)
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/comments/comment1.xml": legacy}
    md = pptx_markdown(parts)
    assert "## Comments" in md and "Fix the diagram arrow." in md
    rep = conversion_report(pptx_source_text(parts), md)
    assert rep["valid"] is True, rep


# --------------------------------------------------------------- markdown escaping

def test_underscore_paths_survive_rendering_and_the_gate():
    # `__` is BOLD in GFM: unescaped, a renderer (and markdown_to_text) eats the
    # underscores and glues "dv3__dv3_tests" into "dv3dv3_tests" — token loss.
    doc = _wdoc(_wp("run regression_results/dv3__dv3_tests__ecc_disable now"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "dv3\\_\\_dv3\\_tests" in md
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_angle_bracket_signals_survive_in_cells():
    sheet = _sheet('<row><c t="inlineStr"><is><t>check &lt;prdata[31:0]&gt; toggles</t>'
                   "</is></c></row>")
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    assert "\\<prdata\\[31:0\\]>" in md
    rep = conversion_report(xlsx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_leading_numbered_line_is_not_swallowed_as_list_marker():
    # A plain paragraph "15. Verify lock" would render as an ordered list whose
    # marker (the 15) disappears from the text layer.
    doc = _wdoc(_wp("15. Verify lock time"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "15\\. Verify lock time" in md
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


# ----------------------------------------------------------------------- dispatch

def test_dispatch_by_extension():
    doc = {"word/document.xml": _wdoc(_wp("hello"))}
    assert "hello" in ooxml_markdown("docx", doc)
    assert "hello" in ooxml_markdown(".DOCX", doc)
    assert ooxml_markdown("pdf", doc) == ""
    assert "hello" in ooxml_source_text("docx", doc)
    assert ooxml_source_text("pdf", doc) == ""

# --- opt-in image sentinels (deterministic raster/metafile extraction) -------
from backend.ingest import ooxml_image_parts                          # noqa: E402

_IMG_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_DRAW_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"')


def _rels(triples):
    # triples: [(rId, target, type_or_None)]
    items = "".join(
        '<Relationship Id="%s" Target="%s"%s/>'
        % (i, tgt, (' Type="%s"' % typ) if typ else "")
        for i, tgt, typ in triples)
    return '<Relationships %s>%s</Relationships>' % (RELS, items)


def _wp_drawing(rid):
    return ('<w:p><w:r><w:drawing %s><wp:inline><a:graphic><a:graphicData>'
            '<pic:pic><pic:blipFill><a:blip r:embed="%s"/></pic:blipFill></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
            % (_DRAW_NS, rid))


def _wp_vml(rid):
    return ('<w:p><w:r><w:pict %s><v:shape><v:imagedata r:id="%s"/></v:shape>'
            '</w:pict></w:r></w:p>' % (_DRAW_NS, rid))


def test_docx_default_emits_no_sentinel_and_is_byte_identical():
    body = _wp("Intro.") + _wp_drawing("rId5") + _wp("After.")
    parts = {"word/document.xml": _wdoc(body),
             "word/_rels/document.xml.rels": _rels([("rId5", "media/image1.png", _IMG_TYPE)])}
    legacy = docx_markdown(parts)                       # default emit_images=False
    assert "ooxml-image" not in legacy                  # no sentinel by default
    assert docx_markdown(parts, False) == legacy        # explicit False identical


def test_docx_emit_images_places_sentinel_in_reading_order():
    body = _wp("Intro.") + _wp_drawing("rId5") + _wp("After.")
    parts = {"word/document.xml": _wdoc(body),
             "word/_rels/document.xml.rels": _rels([("rId5", "media/image1.png", _IMG_TYPE)])}
    md = docx_markdown(parts, emit_images=True)
    assert ooxml_image_parts(md) == ["word/media/image1.png"]   # rId -> package path
    # positioned between the two paragraphs, in order
    assert md.index("Intro.") < md.index("ooxml-image") < md.index("After.")
    # and it never moves the recall gate (sentinel is a comment)
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] and rep["recall"] == 1.0


def test_docx_vml_imagedata_is_extracted_too():
    parts = {"word/document.xml": _wdoc(_wp_vml("rId9")),
             "word/_rels/document.xml.rels": _rels([("rId9", "media/logo.emf", _IMG_TYPE)])}
    assert ooxml_image_parts(docx_markdown(parts, True)) == ["word/media/logo.emf"]


def test_docx_image_rels_ignore_svg_external_and_nonimage():
    # svg is handled as text (not pixels); external + hyperlink rels are not images
    body = _wp_drawing("rId1") + _wp_drawing("rId2") + _wp_drawing("rId3")
    parts = {"word/document.xml": _wdoc(body),
             "word/_rels/document.xml.rels": _rels([
                 ("rId1", "media/diagram.svg", _IMG_TYPE),          # svg -> dropped
                 ("rId2", "http://x/y.png", None),                  # (no type, external-ish)
                 ("rId3", "media/photo.png", _IMG_TYPE)])}          # real raster -> kept
    parts["word/_rels/document.xml.rels"] = (
        '<Relationships %s>'
        '<Relationship Id="rId1" Target="media/diagram.svg" Type="%s"/>'
        '<Relationship Id="rId2" Target="http://x/y.png" TargetMode="External" Type="%s"/>'
        '<Relationship Id="rId3" Target="media/photo.png" Type="%s"/>'
        '</Relationships>' % (RELS, _IMG_TYPE, _IMG_TYPE, _IMG_TYPE))
    assert ooxml_image_parts(docx_markdown(parts, True)) == ["word/media/photo.png"]


def _sld(shapes):
    return ('<p:sld %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>' % (P, shapes))


def _p_pic(rid):
    return ('<p:pic><p:blipFill><a:blip r:embed="%s"/></p:blipFill></p:pic>' % rid)


def test_pptx_pic_emits_sentinel_and_default_does_not():
    slide = _sld('<p:sp><p:txBody><a:p><a:r><a:t>Title text</a:t></a:r></a:p>'
                 '</p:txBody></p:sp>' + _p_pic("rId2"))
    parts = {"ppt/slides/slide1.xml": slide,
             "ppt/slides/_rels/slide1.xml.rels":
                 _rels([("rId2", "../media/image1.png", _IMG_TYPE)])}
    assert "ooxml-image" not in pptx_markdown(parts)               # default off
    md = pptx_markdown(parts, emit_images=True)
    assert ooxml_image_parts(md) == ["ppt/media/image1.png"]       # ../media resolved
    assert conversion_report(pptx_source_text(parts), md)["recall"] == 1.0


def test_pptx_alternatecontent_image_in_choice_not_double_counted():
    # modern PowerPoint: the picture lives in mc:Choice (graphicFrame) with a <p:pic>
    # duplicate in mc:Fallback. It must be counted EXACTLY ONCE (Choice wins, Fallback skipped).
    gf = ('<p:graphicFrame><a:graphic><a:graphicData>'
          '<p:oleObj><p:blipFill><a:blip r:embed="rId3"/></p:blipFill></p:oleObj>'
          '</a:graphicData></a:graphic></p:graphicFrame>')
    shapes = ('<mc:AlternateContent %s><mc:Choice Requires="v">%s</mc:Choice>'
              '<mc:Fallback>%s</mc:Fallback></mc:AlternateContent>'
              % (MC, gf, _p_pic("rId4")))
    parts = {"ppt/slides/slide1.xml": _sld(shapes),
             "ppt/slides/_rels/slide1.xml.rels": _rels([
                 ("rId3", "../media/zoom.png", _IMG_TYPE),
                 ("rId4", "../media/fallback.png", _IMG_TYPE)])}
    got = ooxml_image_parts(pptx_markdown(parts, True))
    assert got == ["ppt/media/zoom.png"]           # Choice only, Fallback skipped -> once


def _xdr_drawing(rid):
    return ('<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/'
            'spreadsheetDrawing" %s><xdr:pic><xdr:blipFill><a:blip r:embed="%s"/>'
            '</xdr:blipFill></xdr:pic></xdr:wsDr>' % (_DRAW_NS, rid))


def test_xlsx_drawing_image_emits_in_images_section():
    parts = {
        "xl/workbook.xml": '<workbook %s><sheets><sheet name="S1" sheetId="1"/></sheets>'
                           '</workbook>' % S,
        "xl/worksheets/sheet1.xml": '<worksheet %s><sheetData/></worksheet>' % S,
        "xl/drawings/drawing1.xml": _xdr_drawing("rId1"),
        "xl/drawings/_rels/drawing1.xml.rels":
            _rels([("rId1", "../media/image1.png", _IMG_TYPE)])}
    assert "ooxml-image" not in xlsx_markdown(parts)               # default off
    md = xlsx_markdown(parts, emit_images=True)
    assert ooxml_image_parts(md) == ["xl/media/image1.png"]
    assert "## Images" in md


def test_ooxml_dispatcher_threads_emit_images_flag():
    parts = {"word/document.xml": _wdoc(_wp_drawing("rId5")),
             "word/_rels/document.xml.rels": _rels([("rId5", "media/i.png", _IMG_TYPE)])}
    assert ooxml_image_parts(ooxml_markdown("docx", parts, emit_images=True)) == \
        ["word/media/i.png"]
    assert "ooxml-image" not in ooxml_markdown("docx", parts)      # default off
