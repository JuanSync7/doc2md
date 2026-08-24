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
                            ooxml_markdown, ooxml_source_text, furniture_drops,
                            markdown_to_text)
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

# A decimal list that nests -- the shape of every real procedure/runbook.
NUMBERING_DEC = ('<w:numbering %s>'
                 '<w:abstractNum w:abstractNumId="30">'
                 '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl>'
                 '<w:lvl w:ilvl="2"><w:numFmt w:val="decimal"/></w:lvl>'
                 '</w:abstractNum>'
                 '<w:abstractNum w:abstractNumId="40">'
                 '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl>'
                 '</w:abstractNum>'
                 '<w:num w:numId="3"><w:abstractNumId w:val="30"/></w:num>'
                 '<w:num w:numId="4"><w:abstractNumId w:val="40"/></w:num>'
                 '</w:numbering>' % W)

# The decimal procedure of NUMBERING_DEC plus a SEPARATE bullet instance -- what
# Word actually writes for "notes bulleted under step 2".
NUMBERING_SUBBULLET = NUMBERING_DEC.replace(
    '</w:numbering>',
    '<w:abstractNum w:abstractNumId="50">'
    '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>'
    '</w:abstractNum>'
    '<w:num w:numId="5"><w:abstractNumId w:val="50"/></w:num>'
    '</w:numbering>')


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


def test_nested_ordered_list_indents_to_the_parents_content_column():
    # CommonMark nests a child item only at its parent's CONTENT column -- 3 for
    # "1. ", not 2. At 2 the parent item CLOSES and the child renders as its SIBLING,
    # so a procedure with one sub-step renumbers every step after it: an operator
    # working an outage from the converted runbook performs the wrong action. Not a
    # single token moves, so neither the recall gate nor outline coverage can object.
    doc = _wdoc(_wp("acknowledge the page", num="3")
                + _wp("check pod health", num="3")
                + _wp("inspect the gateway log", num="3", ilvl=1)
                + _wp("drain the unhealthy pod", num="3"))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING_DEC})
    assert ("1. acknowledge the page\n"
            "2. check pod health\n"
            "   1. inspect the gateway log\n"
            "3. drain the unhealthy pod") in md


def test_nested_ordered_list_indents_compound_at_depth_two():
    doc = _wdoc(_wp("one", num="3") + _wp("two", num="3", ilvl=1)
                + _wp("three", num="3", ilvl=2))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING_DEC})
    # columns accumulate: 0 -> 3 -> 6, never a flat 2 per level.
    assert "1. one\n   1. two\n      1. three" in md


def test_ordered_child_of_a_bullet_parent_uses_the_bullet_content_column():
    # "- " is 2 wide, so the child indents by 2 here and by 3 under "1. ". A constant
    # is wrong in one direction or the other; the marker width is the rule.
    doc = _wdoc(_wp("prerequisite", num="4") + _wp("sub step", num="4", ilvl=1))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING_DEC})
    assert "- prerequisite\n  1. sub step" in md


def test_a_bullet_sublist_with_its_own_numid_still_nests_under_the_step():
    # Word gives a bullet sub-list inside a numbered procedure its OWN w:numId, so
    # "different numId" cannot mean "different list": keying the ancestor columns on
    # the numbering instance unparented the notes, and the two PEER bullets came out
    # as one bullet with a child. w:ilvl is the nesting, whatever instance carries it.
    doc = _wdoc(_wp("drain the tier", num="3")
                + _wp("the drain is idempotent", num="5", ilvl=1)
                + _wp("a stalled drain is read only", num="5", ilvl=1)
                + _wp("run the migration", num="3"))
    md = docx_markdown({"word/document.xml": doc,
                        "word/numbering.xml": NUMBERING_SUBBULLET})
    assert ("1. drain the tier\n"
            "   - the drain is idempotent\n"
            "   - a stalled drain is read only\n"
            "2. run the migration") in md


def test_a_paragraph_closes_the_list_so_the_next_item_restarts_at_column_zero():
    # A column-0 paragraph ends the list in the rendered markdown; carrying the old
    # ancestor columns across it would indent the next item into a code block.
    # The COLUMNS reset and the NUMBER does not: Word does not restart a procedure
    # because a paragraph got in the way, so the item after the interlude is 2.
    doc = _wdoc(_wp("one", num="3") + _wp("two", num="3", ilvl=1)
                + _wp("Interlude prose.") + _wp("three", num="3", ilvl=1))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING_DEC})
    assert "\n2. three" in md
    assert "    2. three" not in md


def test_docx_numid_zero_is_not_a_list():
    doc = _wdoc(_wp("plain again", num="0"))
    md = docx_markdown({"word/document.xml": doc})
    assert "- plain" not in md and "plain again" in md


# ------------------------------------- a renumbered procedure (quality-plan P0.1)
#
# CommonMark takes a list's start from its FIRST marker and then counts on its own.
# So every one of the constructs below used to reset a procedure to step 1 halfway
# down, at token_recall 1.0 with zero structural errors, because the number a
# reader acts on is not a token and the depth histogram does not move either.

# A decimal list whose level 0 is declared to start at 5, plus a bullet level for
# notes under it. `w:start` was read nowhere.
NUMBERING_START5 = ('<w:numbering %s>'
                    '<w:abstractNum w:abstractNumId="60">'
                    '<w:lvl w:ilvl="0"><w:start w:val="5"/>'
                    '<w:numFmt w:val="decimal"/></w:lvl>'
                    '<w:lvl w:ilvl="1"><w:start w:val="3"/>'
                    '<w:numFmt w:val="decimal"/></w:lvl>'
                    '</w:abstractNum>'
                    '<w:num w:numId="6"><w:abstractNumId w:val="60"/></w:num>'
                    # The same abstract numbering, restarted by this instance.
                    '<w:num w:numId="7"><w:abstractNumId w:val="60"/>'
                    '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/>'
                    '</w:lvlOverride></w:num>'
                    '</w:numbering>' % W)

# Word's built-in "List Number": the numbering lives in the STYLE, and NimbusStep
# only inherits it through w:basedOn.
NUMBERING_STYLES = ('<w:styles %s>'
                    '<w:style w:type="paragraph" w:styleId="ListNumber">'
                    '<w:name w:val="List Number"/>'
                    '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/>'
                    '</w:numPr></w:pPr></w:style>'
                    '<w:style w:type="paragraph" w:styleId="NimbusStep">'
                    '<w:name w:val="Nimbus Step"/>'
                    '<w:basedOn w:val="ListNumber"/></w:style>'
                    '</w:styles>' % W)


def test_a_picture_inside_a_step_does_not_restart_the_procedure():
    # THE realistic one: a screenshot in a runbook step. The sentinel used to be
    # emitted at column 0, which closes the list, so steps 3 and 4 came out as 1
    # and 2 — LibreOffice reads the same file as <ol start="3">.
    drawing = ('<w:r><w:drawing><wp:inline %s><a:graphic><a:graphicData>'
               '<pic:pic><pic:blipFill><a:blip r:embed="rId9"/></pic:blipFill>'
               '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
               '</w:r>'
               % ('xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
                  'wordprocessingDrawing" ' + A + " " + R +
                  ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/'
                  'picture"'))
    step2 = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/>'
             '</w:numPr></w:pPr><w:r><w:t>open the console</w:t></w:r>%s</w:p>'
             % drawing)
    doc = _wdoc(_wp("acknowledge the page", num="3") + step2
                + _wp("drain the pod", num="3") + _wp("restart the tier", num="3"))
    rels = ('<Relationships %s><Relationship Id="rId9" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/shot.png"/></Relationships>' % RELS)
    md = docx_markdown({"word/document.xml": doc,
                        "word/numbering.xml": NUMBERING_DEC,
                        "word/_rels/document.xml.rels": rels}, emit_images=True)
    # The picture is a list-item CONTINUATION at step 2's content column...
    assert "\n   <!-- ooxml-image:word/media/shot.png -->\n" in md
    # ...so the steps after it are still 3 and 4, not 1 and 2.
    assert "\n3. drain the pod" in md
    assert "\n4. restart the tier" in md


def test_a_text_box_anchored_in_a_step_does_not_restart_the_procedure():
    box = ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>'
           '</w:pPr><w:r><w:t>drain the tier</w:t></w:r>'
           '<w:r><w:pict><v:shape %s><v:textbox><w:txbxContent>'
           '<w:p><w:r><w:t>The drain is idempotent.</w:t></w:r></w:p>'
           '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
           % 'xmlns:v="urn:schemas-microsoft-com:vml"')
    doc = _wdoc(_wp("announce the freeze", num="3") + box
                + _wp("run the migration", num="3"))
    md = docx_markdown({"word/document.xml": doc, "word/numbering.xml": NUMBERING_DEC})
    assert "\n   The drain is idempotent.\n" in md   # lifted INTO step 2
    assert "\n3. run the migration" in md


def test_a_declared_start_is_the_first_marker():
    # `1. ` for a list Word numbers from 5 makes prose saying "see step 6" point at
    # step 2. CommonMark reads the start off the first marker, so "5." is the fix.
    doc = _wdoc(_wp("verify the backup", num="6") + _wp("cut over", num="6"))
    md = docx_markdown({"word/document.xml": doc,
                        "word/numbering.xml": NUMBERING_START5})
    assert "5. verify the backup\n6. cut over" in md


def test_a_nested_list_that_does_not_start_at_one_gets_its_own_paragraph_break():
    # CommonMark lets a list interrupt a paragraph only when an ordered marker reads
    # 1, so "3." written directly under its parent's text is swallowed as that
    # parent's prose and the sub-list VANISHES. One blank line closes the paragraph.
    doc = _wdoc(_wp("verify the backup", num="6")
                + _wp("check the checksum", num="6", ilvl=1))
    md = docx_markdown({"word/document.xml": doc,
                        "word/numbering.xml": NUMBERING_START5})
    assert "5. verify the backup\n\n   3. check the checksum" in md


def test_a_start_override_restarts_the_second_procedure():
    # Two instances of the same abstract numbering: the second overrides the start
    # back to 1. Adjacent items cannot be separated by a block, so the delimiter
    # changes instead — CommonMark's own way of beginning a new list.
    doc = _wdoc(_wp("verify the backup", num="6") + _wp("cut over", num="6")
                + _wp("announce the window", num="7"))
    md = docx_markdown({"word/document.xml": doc,
                        "word/numbering.xml": NUMBERING_START5})
    assert "5. verify the backup\n6. cut over\n1) announce the window" in md


def test_numbering_carried_by_a_paragraph_style_is_still_a_list():
    # The list VANISHED: three plain paragraphs, no markers, and the structural
    # ground truth was blind in exactly the same place, so nothing could object.
    doc = _wdoc(_wp("stop the writer", style="ListNumber")
                + _wp("flush the queue", style="NimbusStep")
                + _wp("start the writer", style="NimbusStep"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": NUMBERING_STYLES,
                        "word/numbering.xml": NUMBERING_DEC})
    assert "1. stop the writer\n2. flush the queue\n3. start the writer" in md


def test_a_paragraphs_own_numbering_beats_the_styles():
    doc = _wdoc(_wp("a bullet, not a step", style="ListNumber", num="4"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": NUMBERING_STYLES,
                        "word/numbering.xml": NUMBERING_DEC})
    assert "- a bullet, not a step" in md


def test_an_explicit_numid_zero_refuses_the_styles_numbering():
    # w:numId 0 means "this paragraph is NOT numbered" — it must not then inherit
    # numbering from the very style it is overriding.
    doc = _wdoc(_wp("plain prose", style="ListNumber", num="0"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": NUMBERING_STYLES,
                        "word/numbering.xml": NUMBERING_DEC})
    assert "1. plain prose" not in md and "plain prose" in md


def test_a_basedon_cycle_terminates():
    styles = ('<w:styles %s>'
              '<w:style w:type="paragraph" w:styleId="A">'
              '<w:name w:val="A"/><w:basedOn w:val="B"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="B">'
              '<w:name w:val="B"/><w:basedOn w:val="A"/></w:style>'
              '</w:styles>' % W)
    md = docx_markdown({"word/document.xml": _wdoc(_wp("body", style="A")),
                        "word/styles.xml": styles})
    assert "body" in md


def test_a_heading_style_derived_from_heading1_is_still_a_heading():
    # `NimbusH1 basedOn="Heading1"` is an <h1> to LibreOffice. Reading only the
    # style's OWN w:name turned a two-section document into one structureless blob.
    styles = ('<w:styles %s>'
              '<w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="NimbusH1">'
              '<w:name w:val="Nimbus Section"/>'
              '<w:basedOn w:val="Heading1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="NimbusSub">'
              '<w:name w:val="Nimbus Subsection"/>'
              '<w:basedOn w:val="NimbusH1"/>'
              '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
              '</w:styles>' % W)
    doc = _wdoc(_wp("Bring-up", style="NimbusH1") + _wp("Body prose.")
                + _wp("Straps", style="NimbusSub"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": styles})
    assert "# Bring-up" in md
    # The style's OWN outlineLvl wins over the level it would inherit.
    assert "## Straps" in md


def test_a_code_style_derived_from_a_code_style_is_still_code():
    styles = ('<w:styles %s>'
              '<w:style w:type="paragraph" w:styleId="Src">'
              '<w:name w:val="Source Code"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="KestrelShell">'
              '<w:name w:val="Kestrel Shell"/><w:basedOn w:val="Src"/></w:style>'
              '</w:styles>' % W)
    doc = _wdoc(_wp("kestrelctl drain", style="KestrelShell"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": styles})
    assert "```\nkestrelctl drain\n```" in md


# ------------------------------------ a row that does not start at grid column 0

def test_grid_before_keeps_every_value_in_its_own_column():
    # <w:gridBefore w:val="1"/> means the row begins at grid column 1. Counting only
    # the w:tc elements shifts every value one column LEFT, so a register NAMED
    # "0x04" publishes "RO" as its offset — a plausible table, wrong in every row.
    header = "<w:tr>%s%s%s</w:tr>" % (_wcell("Register"), _wcell("Offset"),
                                      _wcell("Access"))
    indented = ('<w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>%s%s</w:tr>'
                % (_wcell("0x04"), _wcell("RO")))
    md = docx_markdown({"word/document.xml":
                        _wdoc("<w:tbl>%s%s</w:tbl>" % (header, indented))})
    assert "| Register | Offset | Access |" in md
    assert "|  | 0x04 | RO |" in md


def test_grid_after_pads_the_right_hand_end():
    header = "<w:tr>%s%s%s</w:tr>" % (_wcell("Register"), _wcell("Offset"),
                                      _wcell("Access"))
    short = ('<w:tr><w:trPr><w:gridAfter w:val="1"/></w:trPr>%s%s</w:tr>'
             % (_wcell("PllLockMon"), _wcell("0x08")))
    md = docx_markdown({"word/document.xml":
                        _wdoc("<w:tbl>%s%s</w:tbl>" % (header, short))})
    assert "| PllLockMon | 0x08 |  |" in md


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
    # Single underscores flanked by word characters can neither open nor close
    # emphasis (CommonMark's flanking rule), so the sheet name is stored VERBATIM —
    # `## Assignment\_list\_ecc` rendered the same but put backslashes into the bytes
    # a BM25 index and a human grep actually search.
    assert "## Assignment_list_ecc" in md
    assert "\\_" not in md
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
    # `__x__` is BOLD in GFM: unescaped, a renderer (and markdown_to_text's _BOLD,
    # which carries no flanking guard) eats the underscores and glues
    # "dv3__dv3_tests__ecc" into "dv3dv3_testsecc" — real token loss. So a RUN of two
    # or more stays escaped. A SINGLE underscore between word characters cannot mean
    # anything to either, so escaping it would only corrupt the stored identifier.
    doc = _wdoc(_wp("run regression_results/dv3__dv3_tests__ecc_disable now"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "dv3\\_\\_dv3_tests\\_\\_ecc_disable" in md
    assert "regression_results" in md              # grep-able, byte for byte
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_screaming_snake_identifiers_are_stored_verbatim():
    # The stored bytes are what an embedder and a BM25 index see. `DB\_MAX\_CONN\_LIMIT`
    # renders correctly and matches nothing a person would ever search for.
    doc = _wdoc(_wp("set DB_MAX_CONN_LIMIT=64 and pass --dry_run=true"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "DB_MAX_CONN_LIMIT=64" in md
    assert "--dry_run=true" in md
    assert "\\_" not in md
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_underscore_that_could_open_emphasis_is_still_escaped():
    # Not word-flanked -> a real emphasis delimiter -> must stay escaped, or the
    # renderer and markdown_to_text both swallow the span between the pair.
    doc = _wdoc(_wp("use _lead and trail_ markers"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "\\_lead" in md and "trail\\_" in md
    rep = conversion_report(docx_source_text(parts), md)
    assert rep["valid"] is True, rep


# -------------------------------------------------- deliberate drops are NAMED

def test_dropped_headers_and_footers_are_named_and_measured():
    # end-goal.md permits dropping page furniture, but only when the drop is
    # "deliberate and visible". Before this, a Confidential banner vanished into a
    # report that said warnings: [] -- indistinguishable from a document with no
    # header at all.
    furn = {"word/header1.xml": _wdoc(_wp("Nimbus Confidential -- Project Kestrel")),
            "word/footer1.xml": _wdoc(_wp("Page 1 of 9"))}
    warns = furniture_drops(furn)
    assert len(warns) == 1
    w = warns[0]
    assert w["code"] == "dropped_headers_footers"
    assert w["parts"] == 2 and w["chars"] > 0
    assert "header1.xml" in w["detail"] and "footer1.xml" in w["detail"]


def test_an_empty_header_dropped_nothing_and_is_not_reported():
    assert furniture_drops({"word/header1.xml": _wdoc(_wp(""))}) == []
    assert furniture_drops({}) == []


def test_furniture_drops_ignores_body_parts():
    # Only page furniture is claimed; a body part reaching this function would mean
    # the reader is misrouting content into the "dropped" bucket.
    assert furniture_drops({"word/document.xml": _wdoc(_wp("real body text"))}) == []


def test_angle_bracket_signals_survive_in_cells():
    sheet = _sheet('<row><c t="inlineStr"><is><t>check &lt;prdata[31:0]&gt; toggles</t>'
                   "</is></c></row>")
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet}
    md = xlsx_markdown(parts)
    # `<` is escaped because `<prdata...` could open a raw HTML tag. The BUS SLICE
    # is not: a bracket is only syntax next to `](` or `][`, and this converter
    # never emits a link reference definition for a bare `[31:0]` to resolve
    # against. The signal name stays greppable in the stored bytes, which is the
    # whole point of not escaping what was never dangerous.
    assert "\\<prdata[31:0]> toggles" in md
    assert "\\[31:0\\]" not in md
    rep = conversion_report(xlsx_source_text(parts), md)
    assert rep["valid"] is True, rep


def test_a_bracket_that_could_form_a_link_is_still_escaped():
    # The other half of the rule: `](` is exactly the sequence that makes a
    # bracket dangerous, so prose containing one keeps its backslashes and the
    # link target survives into the text layer instead of being swallowed.
    doc = _wdoc(_wp("see [note](below) for the strap table"))
    parts = {"word/document.xml": doc}
    md = docx_markdown(parts)
    assert "\\[note\\](below)" in md
    assert markdown_to_text(md).strip().endswith("see [note](below) for the strap table")
    rep = conversion_report(docx_source_text(parts), md)
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


# ============================================================================
# Whitespace, flanking, brackets and line-leading markers
# ----------------------------------------------------------------------------
# Four families of defect that all come from the same mistake: deciding something
# about markdown from LESS text than the renderer will see. Whitespace was
# normalised for prose and applied to code too; a delimiter run was written
# without looking at its neighbours; the bracket exemption was decided per w:t
# instead of per LINE; and only a SINGLE leading marker character was neutralised,
# so every other block construct walked straight out of a body paragraph.

def _wpre(text, style=None):
    """A paragraph whose run PRESERVES whitespace — the shape Word writes for code."""
    ppr = ("<w:pPr><w:pStyle w:val=\"%s\"/></w:pPr>" % style) if style else ""
    return ('<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (ppr, text))


def _wrun(text, rpr=""):
    return ('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
            % ("<w:rPr>%s</w:rPr>" % rpr if rpr else "", text))


CODE_STYLES = ('<w:styles %s>'
               '<w:style w:type="paragraph" w:styleId="Pre">'
               '<w:name w:val="HTML Preformatted"/></w:style>'
               '<w:style w:type="character" w:styleId="Mono">'
               '<w:name w:val="HTML Code"/></w:style>'
               '</w:styles>' % W)


def _fidelity(parts):
    from backend.ingest import docx_source_structure
    from backend.validate import md_structure, structure_fidelity_report
    return structure_fidelity_report(md_structure(ooxml_markdown("docx", parts)),
                                     docx_source_structure(parts))


def _graded(parts):
    """(markdown, conversion_report) — every test here is graded, never asserted alone."""
    md = ooxml_markdown("docx", parts)
    return md, conversion_report(ooxml_source_text("docx", parts), md)


# ------------------------------------------------- raw means raw (code paragraphs)

def test_indentation_inside_a_code_style_paragraph_survives():
    # `if dev.ready:` with no body and an unconditional `return dev` is the OPPOSITE
    # program, and it is not even valid Python — yet both gates certified it, because
    # whitespace is not a token and the ground truth counts fences, not their content.
    lines = ["def configure(dev):", "    if dev.ready:",
             "        dev.write(0x04, 1)", "    return dev"]
    parts = {"word/document.xml":
             _wdoc("".join(_wpre(l, style="Pre") for l in lines)),
             "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "```\n" + "\n".join(lines) + "\n```\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_a_tab_inside_a_code_paragraph_is_a_tab_not_a_space():
    body = (_wpre("top", style="Pre")
            + '<w:p><w:pPr><w:pStyle w:val="Pre"/></w:pPr><w:r><w:tab/>'
              '<w:t xml:space="preserve">one</w:t></w:r></w:p>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "```\ntop\n\tone\n```\n"
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_a_soft_break_inside_a_code_paragraph_is_a_new_line():
    # One w:br used to weld two program lines into one statement.
    body = ('<w:p><w:pPr><w:pStyle w:val="Pre"/></w:pPr>'
            '<w:r><w:t xml:space="preserve">for i in range(3):</w:t></w:r>'
            '<w:r><w:br/><w:t xml:space="preserve">    print(i)</w:t></w:r></w:p>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "```\nfor i in range(3):\n    print(i)\n```\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_column_alignment_inside_a_listing_survives():
    parts = {"word/document.xml": _wdoc(_wpre("NAME      OFFSET   ACCESS", "Pre")),
             "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert "NAME      OFFSET   ACCESS" in md
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_a_blank_line_between_two_code_lines_is_kept():
    # Pressing Enter inside a shell transcript. The blank paragraph emits no block,
    # so it neither opens a fence nor closes one — which is what the structural
    # ground truth counts — but it IS a line of the program.
    body = (_wpre("first", "Pre")
            + '<w:p><w:pPr><w:pStyle w:val="Pre"/></w:pPr></w:p>'
            + _wpre("second", "Pre"))
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "```\nfirst\n\nsecond\n```\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_a_code_paragraph_alone_never_opens_a_fence_on_a_blank_line():
    # The converse: leading and trailing blank code paragraphs emit nothing at all,
    # so a listing that is only blank lines is not a fenced block.
    body = ('<w:p><w:pPr><w:pStyle w:val="Pre"/></w:pPr></w:p>'
            + _wpre("only line", "Pre")
            + '<w:p><w:pPr><w:pStyle w:val="Pre"/></w:pPr></w:p>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "```\nonly line\n```\n"
    assert rep["valid"] is True
    assert _fidelity(parts)["gate"] == "pass"


def test_internal_spacing_of_an_inline_code_span_survives():
    body = ('<w:p>' + _wrun("Run ")
            + _wrun("cmd   --flag", '<w:rStyle w:val="Mono"/>')
            + _wrun(" now.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": CODE_STYLES}
    md, rep = _graded(parts)
    assert md == "Run `cmd   --flag` now.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_prose_whitespace_is_still_collapsed_across_a_run_boundary():
    # The guard on the fix: prose normalisation is unchanged, INCLUDING across the
    # run boundary Word puts in the middle of "foo " + " bar".
    body = ('<w:p>' + _wrun("Reset  the ") + _wrun("  core\tnow.\n") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "Reset the core now.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0


# ------------------------------------------------- emphasis that can actually open

def test_bold_abutting_a_word_on_one_side_and_punctuation_on_the_other_renders():
    # `Field MODE**(2:0)**`: the opening run has a word character outside and
    # punctuation inside, so CommonMark cannot open it and the bold is GONE from the
    # render. One character of the span moves out so the delimiter flanks.
    body = ('<w:p>' + _wrun("Field MODE") + _wrun("(2:0)", "<w:b/>")
            + _wrun(" is read-only.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "Field MODE(**2:0)** is read-only.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_bold_ending_in_punctuation_against_a_following_word_renders():
    body = ('<w:p>' + _wrun("See ") + _wrun("Fig.", "<w:b/>") + _wrun("1 above.")
            + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "See **Fig**.1 above.\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_an_escaped_character_moves_out_of_a_span_whole():
    # `VDD` + bold `_core`: the escape `\_` is the punctuation that blocks the
    # delimiter, and splitting it would leave the backslash escaping the asterisk.
    body = ('<w:p>' + _wrun("VDD") + _wrun("_core", "<w:b/>")
            + _wrun(" must stay high.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "VDD\\_**core** must stay high.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_italic_gets_the_same_treatment_as_bold():
    body = ('<w:p>' + _wrun("Field MODE") + _wrun("(2:0)", "<w:i/>")
            + _wrun(" is read-only.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "Field MODE(*2:0)* is read-only.\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_delimiter_that_already_flanks_is_left_exactly_where_it_was():
    # Direction (b) for the flanking fix, three ways. A space-flanked span, a span
    # whose own edges are punctuation but whose NEIGHBOUR is whitespace, and the
    # recorded mid-word deviation `Dma**ArbiterUnit**` — which renders correctly and
    # must not start being rewritten.
    for runs, want in (
            ([("The ", ""), ("MODE", "<w:b/>"), (" field.", "")],
             "The **MODE** field.\n"),
            ([("the ", ""), ('"safe"', "<w:b/>"), (" mode.", "")],
             'the **"safe"** mode.\n'),
            ([("Dma", ""), ("ArbiterUnit", "<w:b/>"), (" here.", "")],
             "Dma**ArbiterUnit** here.\n")):
        parts = {"word/document.xml":
                 _wdoc('<w:p>%s</w:p>' % "".join(_wrun(t, pr) for t, pr in runs))}
        md, rep = _graded(parts)
        assert md == want
        assert rep["valid"] is True and rep["recall"] == 1.0
        assert _fidelity(parts)["gate"] == "pass"


def test_a_span_of_one_punctuation_character_keeps_its_markers():
    # The residual, pinned so it is a decision and not a surprise: nothing can move
    # out of a one-character span without emptying it, so the markers stay and the
    # fidelity gate reports the loss honestly rather than the converter hiding it.
    body = ('<w:p>' + _wrun("Note") + _wrun(".", "<w:b/>") + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, _rep = _graded(parts)
    assert md == "Note**.** end.\n"
    assert _fidelity(parts)["gate"] == "fail"


# ------------------------------------------ the bracket rule is about the LINE

def test_a_run_boundary_between_a_bracket_and_a_paren_cannot_fabricate_a_link():
    # Word splits a run at every rsid, proofErr, bookmark and field boundary, so
    # `[3]` and `(page 12)` routinely arrive separately. Deciding the exemption per
    # w:t left both bare and the join invented a link.
    body = ('<w:p>' + _wrun("See ") + _wrun("[3]") + _wrun("(page 12)")
            + _wrun(" for the timing.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "See \\[3\\](page 12) for the timing.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert markdown_to_text(md) == "See [3](page 12) for the timing."


def test_a_run_boundary_between_two_brackets_cannot_fabricate_a_reference_link():
    body = ('<w:p>' + _wrun("See ") + _wrun("[3]") + _wrun("[4]") + _wrun(" more.")
            + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "See \\[3\\]\\[4\\] more.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_a_bookmark_between_the_brackets_is_still_one_line():
    # A Word cross-reference TARGET is exactly a bookmarkStart/End pair.
    body = ('<w:p>' + _wrun("See ")
            + '<w:bookmarkStart w:id="1" w:name="_Ref1"/>' + _wrun("[3]")
            + '<w:bookmarkEnd w:id="1"/>' + _wrun("(p12)") + _wrun(" now.")
            + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "See \\[3\\](p12) now.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_the_bracket_exemption_still_holds_for_a_line_that_cannot_form_a_link():
    # Direction (b): the whole point of the exemption is that `[31:0]` stays
    # greppable. It is decided per LINE, so a genuine link in the SAME PARAGRAPH
    # does not freeze it either — the `](` the converter synthesises around a
    # hyperlink is not text the document wrote.
    body = ('<w:p>' + _wrun("Signal ") + _wrun("[31:0]") + _wrun(" is wide, see ")
            + '<w:hyperlink r:id="rId9">%s</w:hyperlink>' % _wrun("the map")
            + _wrun(" for pins [7:0].") + '</w:p>')
    rels = ('<Relationships %s><Relationship Id="rId9" Target="https://x.example/m"'
            ' TargetMode="External" Type="t"/></Relationships>' % RELS)
    parts = {"word/document.xml": '<w:document %s %s %s><w:body>%s</w:body></w:document>'
             % (W, MC, R, body),
             "word/_rels/document.xml.rels": rels}
    md, rep = _graded(parts)
    assert "Signal [31:0] is wide" in md
    assert "pins [7:0]." in md
    assert "[the map](https://x.example/m)" in md
    assert "\\[" not in md
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_the_bracket_decision_is_per_paragraph_not_per_document():
    body = (_wp("The tier owns the [payments] section.")
            + '<w:p>%s%s%s</w:p>' % (_wrun("See "), _wrun("[3]"), _wrun("(p12).")))
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert "[payments] section" in md          # its own line holds no `](`
    assert "See \\[3\\](p12)." in md           # this one does
    assert rep["valid"] is True and rep["recall"] == 1.0


# ------------------------------- a body paragraph is never a block it was not

def test_a_multi_hash_body_paragraph_does_not_become_a_heading():
    body = _wp("Kestrel build notes", style="Heading1") + _wp("## Build steps")
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLES}
    md, rep = _graded(parts)
    assert "\\## Build steps" in md
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"
    assert markdown_to_text(md).endswith("## Build steps")


def test_every_atx_heading_shape_is_neutralised_and_nothing_else_is():
    # 1..6 hashes with or without a following space open a heading; SEVEN do not,
    # and neither does a hash glued to a word — escaping those would put a visible
    # backslash into text that was never at risk.
    for text, want in (("# one", "\\# one"), ("## two", "\\## two"),
                       ("###### six", "\\###### six"), ("#", "\\#"), ("##", "\\##"),
                       ("####### seven", "####### seven"), ("#1 priority",
                                                            "#1 priority")):
        parts = {"word/document.xml": _wdoc(_wp("Body.") + _wp(text))}
        md, rep = _graded(parts)
        assert md.split("\n\n")[-1].strip() == want, text
        assert rep["valid"] is True and rep["recall"] == 1.0, text
        assert _fidelity(parts)["gate"] == "pass", text


def test_a_paragraph_of_dashes_is_not_deleted_as_a_thematic_break():
    # The gate-blind half: `-----` carries no tokens and no heading fact, so BOTH
    # gates passed while the paragraph was rendered away to a horizontal rule and
    # deleted outright from the text layer the knowledge base consumes.
    body = _wp("Kestrel build notes", style="Heading1") + _wp("-----")
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLES}
    md, rep = _graded(parts)
    assert md.endswith("\\-----\n")
    assert rep["valid"] is True
    assert markdown_to_text(md) == "Kestrel build notes\n\n-----"


def test_a_marker_run_paragraph_survives_whatever_the_marker_is():
    for text in ("---", "-----", "===", "=", "-", "- - -"):
        parts = {"word/document.xml": _wdoc(_wp("Body.") + _wp(text))}
        md, _rep = _graded(parts)
        assert markdown_to_text(md).split("\n")[-1] == text, text
        assert _fidelity(parts)["gate"] == "pass", text


def test_a_link_reference_definition_cannot_be_written_by_a_body_paragraph():
    # `[REG]: 0x04` is consumed WHOLE by CommonMark — the paragraph disappears from
    # the render — and markdown_to_text does not model definitions, so recall and
    # the fidelity gate both saw an intact document.
    parts = {"word/document.xml": _wdoc(_wp("Body.") + _wp("[REG]: 0x04"))}
    md, rep = _graded(parts)
    assert md.endswith("\\[REG]: 0x04\n")
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_lead_escaping_leaves_ordinary_prose_alone():
    # Direction (b): nothing that was not a block construct grows a backslash.
    for text in ("Then run make all.", "5 GHz is the ceiling", "-40 C minimum",
                 "#1 priority", "= sign in the middle = here", "a - b - c"):
        parts = {"word/document.xml": _wdoc(_wp(text))}
        md, rep = _graded(parts)
        assert md == text + "\n", text
        assert rep["valid"] is True and rep["recall"] == 1.0, text


# ---------------------------------------------------- emphasis carried by a STYLE

STYLE_MARKS = ('<w:styles %s>'
               '<w:style w:type="paragraph" w:styleId="Heading1">'
               '<w:name w:val="heading 1"/><w:rPr><w:b/></w:rPr></w:style>'
               '<w:style w:type="character" w:styleId="Strong">'
               '<w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>'
               '<w:style w:type="paragraph" w:styleId="Quote">'
               '<w:name w:val="Quote"/><w:rPr><w:i/></w:rPr></w:style>'
               '<w:style w:type="character" w:styleId="SubStrong">'
               '<w:name w:val="SubStrong"/><w:basedOn w:val="Strong"/></w:style>'
               '<w:style w:type="character" w:styleId="NotStrong">'
               '<w:name w:val="NotStrong"/><w:basedOn w:val="Strong"/>'
               '<w:rPr><w:b w:val="0"/></w:rPr></w:style>'
               '</w:styles>' % W)


def test_emphasis_carried_by_a_character_style_is_emitted():
    body = '<w:p>%s</w:p>' % _wrun("Save", '<w:rStyle w:val="Strong"/>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, rep = _graded(parts)
    assert md == "**Save**\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_emphasis_carried_by_a_paragraph_style_is_emitted():
    parts = {"word/document.xml": _wdoc(_wp("Quoted line.", style="Quote")),
             "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "*Quoted line.*\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_character_style_inherits_emphasis_through_basedOn():
    body = '<w:p>%s</w:p>' % _wrun("Save", '<w:rStyle w:val="SubStrong"/>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "**Save**\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_run_can_turn_its_styles_emphasis_back_off():
    body = ('<w:p>%s</w:p>'
            % _wrun("Save", '<w:rStyle w:val="Strong"/><w:b w:val="0"/>'))
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "Save\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_style_can_turn_off_what_it_is_based_on():
    body = '<w:p>%s</w:p>' % _wrun("Save", '<w:rStyle w:val="NotStrong"/>')
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "Save\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_heading_styles_own_bold_is_the_heading_not_a_span():
    # Stock Heading1..9 all carry <w:b/>. Honouring that would demand `# **Title**`
    # of every heading in every document; the ground truth suppresses it for the same
    # reason, so the two agree because they read the same rule out of markdown.
    parts = {"word/document.xml": _wdoc(_wp("Title here", style="Heading1")),
             "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "# Title here\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_run_style_inside_a_heading_still_counts():
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>%s%s</w:p>'
            % (_wrun("Bring up the "), _wrun("clock", '<w:rStyle w:val="Strong"/>')))
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert md == "# Bring up the **clock**\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_a_code_paragraph_carries_no_style_emphasis():
    styles = CODE_STYLES.replace('<w:name w:val="HTML Preformatted"/>',
                                 '<w:name w:val="HTML Preformatted"/>'
                                 '<w:rPr><w:b/></w:rPr>')
    parts = {"word/document.xml": _wdoc(_wpre("x = 1", "Pre")),
             "word/styles.xml": styles}
    md, _rep = _graded(parts)
    assert md == "```\nx = 1\n```\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_style_emphasis_reaches_a_table_cell():
    cell = ('<w:tc><w:p><w:pPr><w:pStyle w:val="Quote"/></w:pPr>%s</w:p></w:tc>'
            % _wrun("Free-running"))
    body = "<w:tbl><w:tr>%s%s</w:tr></w:tbl>" % (_wcell("CLK"), cell)
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLE_MARKS}
    md, _rep = _graded(parts)
    assert "| CLK | *Free-running* |" in md
    assert _fidelity(parts)["gate"] == "pass"


def test_two_emphasised_runs_written_against_each_other_still_open():
    # `**MODE**` written straight against `*(2:0)*` is ONE run of three asterisks to
    # CommonMark, so the neighbour's marker does not shield the second span: the
    # merged run has `E` outside and `(` inside and neither emphasis opens.
    body = ('<w:p>' + _wrun("The ") + _wrun("MODE", "<w:b/>")
            + _wrun("(2:0)", "<w:i/>") + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "The **MODE**(*2:0)* end.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_the_merged_run_is_fixed_from_whichever_side_can_move():
    # Mirror image: here it is the FIRST span whose closing delimiter cannot close,
    # so the character that moves comes off its end.
    body = ('<w:p>' + _wrun("The ") + _wrun("(2:0)", "<w:b/>")
            + _wrun("MODE", "<w:i/>") + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, rep = _graded(parts)
    assert md == "The **(2:0**)*MODE* end.\n"
    assert rep["valid"] is True and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_a_tilde_neighbour_does_not_merge_and_needs_no_shift():
    # Direction (b) for the merge rule: `~~` and `*` are two runs, each punctuation
    # to the other, so nothing moves.
    body = ('<w:p>' + _wrun("The ") + _wrun("MODE", "<w:b/>")
            + _wrun("safe", "<w:strike/>") + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, _rep = _graded(parts)
    assert md == "The **MODE**~~safe~~ end.\n"
    assert _fidelity(parts)["gate"] == "pass"


def test_bold_italic_on_one_run_is_shiftable_because_the_asterisks_are_one_run():
    body = ('<w:p>' + _wrun("The ") + _wrun("MODE") + _wrun("(2:0)", "<w:b/><w:i/>")
            + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, _rep = _graded(parts)
    assert md == "The MODE(***2:0)*** end.\n"
    assert _fidelity(parts)["gate"] == "pass"
