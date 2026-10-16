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
from backend.validate import conversion_report, md_structure

import pytest

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


def test_outline_level_nine_is_body_text_and_not_a_tenth_heading():
    # w:outlineLvl runs 0..9: 0..8 are outline levels 1..9, and 9 is Word's
    # "Body Text" -- the paragraph stating it is NOT in the outline. Reading it as
    # a level made level 10, which min(level, 6) then published as an h6, so every
    # paragraph an author had ever marked Body Text became a heading and the
    # structure.json outline reparented the real sections underneath it.
    doc = _wdoc('<w:p><w:pPr><w:outlineLvl w:val="9"/></w:pPr>'
                '<w:r><w:t>Ordinary body prose.</w:t></w:r></w:p>'
                + _wp("Real Heading", style="Heading1"))
    md = docx_markdown({"word/document.xml": doc, "word/styles.xml": STYLES})
    assert "###### Ordinary body prose." not in md
    assert "Ordinary body prose." in md and "\n# Real Heading" in md
    assert md.count("#") == 1


def test_outline_level_eight_is_still_the_ninth_heading_level():
    # The bound is a bound, not a ban: 8 is "Level 9" and stays a heading. A fix
    # that stopped 9 by refusing every deep outline would be worse than the bug.
    doc = _wdoc('<w:p><w:pPr><w:outlineLvl w:val="8"/></w:pPr>'
                '<w:r><w:t>Deepest</w:t></w:r></w:p>')
    assert "###### Deepest" in docx_markdown({"word/document.xml": doc})


def test_the_cell_geometry_a_tracked_change_replaced_is_not_the_live_one():
    # w:tcPrChange holds the cell properties a revision REPLACED, exactly as
    # w:pPrChange/w:trPrChange hold theirs. Reading the stale w:gridSpan out of it
    # widened a two-column row to three and pushed every value one column right --
    # a plausible table, wrong in every row.
    stale = ('<w:tc><w:tcPr><w:tcPrChange w:id="1" w:author="a" w:date="x">'
             '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr></w:tcPrChange></w:tcPr>'
             '<w:p><w:r><w:t>CTRL</w:t></w:r></w:p></w:tc>')
    tbl = ("<w:tbl><w:tr>%s%s</w:tr><w:tr>%s%s</w:tr></w:tbl>"
           % (_wcell("Register"), _wcell("Offset"), stale, _wcell("0x04")))
    md = docx_markdown({"word/document.xml": _wdoc(tbl)})
    assert "| CTRL | 0x04 |" in md
    assert "| CTRL |  | 0x04 |" not in md


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


def test_pptx_slides_fall_back_to_numeric_part_order_with_titles():
    """No `ppt/presentation.xml`, so the deck cannot say what its order is and the
    parts are sorted numerically — `slide10` after `slide2`, never lexically.

    `## Slide N` counts POSITION in both branches, so `slide10.xml` publishes as
    `## Slide 3` here. That is a deliberate change from numbering by part name, and
    the argument is that a heading must mean ONE thing: a reader cannot act on
    `## Slide 10` without knowing whether it is the tenth slide or merely the tenth
    file, and in the fallback we do not know the deck's order anyway — claiming ten
    slides exist when three do is a second guess on top of the first. Every corpus
    deck numbers its parts 1..n contiguously, so both readings agree there and this
    changes no shipped byte."""
    parts = {
        "ppt/slides/slide2.xml": _slide(_sp("Second body")),
        "ppt/slides/slide10.xml": _slide(_sp("Tenth body")),
        "ppt/slides/slide1.xml": _slide(_sp("Widget Power Plan", ph="title")
                                        + _sp("Agenda item")),
    }
    md = pptx_markdown(parts)
    assert "## Slide 1 — Widget Power Plan" in md
    assert md.index("Agenda item") < md.index("Second body") < md.index("Tenth body")
    assert [l for l in md.split("\n") if l.startswith("## ")] == [
        "## Slide 1 — Widget Power Plan", "## Slide 2", "## Slide 3"]


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


def test_a_span_of_one_punctuation_character_now_keeps_its_EMPHASIS():
    """Was `test_a_span_of_one_punctuation_character_keeps_its_markers`, and it pinned
    a RESIDUAL: nothing can move out of a one-character span without emptying it, so
    `**` sat against a letter on one side and a `.` on the other, could not
    left-flank, and the emphasis was lost — the markers staying as literal text while
    the fidelity gate reported the loss honestly.

    P9.9 removed the residual rather than the honesty. The separator is exactly the
    thing the shift could not be: it changes what sits OUTSIDE the delimiter without
    touching the span's own characters. Measured against marko 2.2.3:
    `Note**.** end.` is `strong=0` and `Note<!---->**.** end.` is `strong=1`, so the
    bold the document draws is now the bold the reader sees, and the gate passes
    because nothing was lost."""
    body = ('<w:p>' + _wrun("Note") + _wrun(".", "<w:b/>") + _wrun(" end.") + '</w:p>')
    parts = {"word/document.xml": _wdoc(body)}
    md, _rep = _graded(parts)
    assert md == "Note<!---->**.** end.\n"
    assert _fidelity(parts)["gate"] == "pass"


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


# ============================================================================
# A block a document never wrote (quality-plan P9.5, defects 3 and 4)
# ============================================================================
#
# `_esc` neutralises INLINE syntax. It does not neutralise the constructs that open
# a BLOCK, which is what `_esc_lead` is for — and `_esc_lead` was reaching the docx
# heading and paragraph paths and the chart caption, but none of the eight places a
# converter puts source text at the start of a block.
#
# A list item's content column IS a block start. After `- `, CommonMark will open a
# heading, a nested list, a blockquote, a fence, a link reference definition or a
# thematic break exactly as it would at column 0. Measured on the shipped corpus deck
# with a single bullet's text replaced by `- - -`:
#
#     pristine   bullet_items=16  thematic_breaks=0  recall=1.0  valid=True
#     poisoned   bullet_items=15  thematic_breaks=1  recall=1.0  valid=True
#
# The bullet DELETED ITSELF — emitted as `- - - -`, which is a thematic break — and
# both gates certified it. The failure mode is the worse of the two available: a
# thematic break carries no tokens for recall to miss and no heading for the fact
# vector to catch, so the paragraph is simply gone.
#
# Every expectation below was checked against marko 2.2.3, a real CommonMark
# implementation, rather than against this project's own reader — the question
# "what does this markdown MEAN" is not one the converter's author may answer.
# The differential test at the end of this section runs that check for real when a
# reference parser is importable.

def _pptx_graded(parts):
    """(markdown, conversion_report) for a deck — graded, never asserted alone."""
    md = pptx_markdown(parts)
    return md, conversion_report(pptx_source_text(parts), md)


# Source texts that open a block, and what each would have become. Kept as one
# table so a construct added here is automatically demanded of every emission
# site below, instead of each site growing its own ad-hoc pair.
_OPENS_A_BLOCK = [
    ("- - -", "a thematic break: the item vanishes, carrying no tokens with it"),
    ("---", "a thematic break"),
    ("## Rollout", "an ATX heading the document never had"),
    ("15. step", "a nested ordered list whose marker markdown_to_text swallows"),
    ("1) step", "a nested ordered list under the paren marker"),
    ("> quote", "a block quote"),
    ("+ item", "a nested bullet list"),
    ("[r]: http://x", "a link reference definition: CommonMark eats the whole line"),
]


@pytest.mark.parametrize("text,becomes", _OPENS_A_BLOCK,
                         ids=[t for t, _ in _OPENS_A_BLOCK])
def test_a_slide_bullet_never_opens_a_block_it_did_not_write(text, becomes):
    """The body-placeholder path — the one measured above on the real corpus."""
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Kept") + _sp(text))}
    md, rep = _pptx_graded(parts)
    facts = md_structure(md)
    assert facts["bullet_items"] == 2, "%r became %s" % (text, becomes)
    assert facts["thematic_breaks"] == 0
    assert facts["headings"] == {2: 1}, "only the `## Slide 1` heading is real"
    assert facts["ordered_items"] == 0
    assert rep["valid"] and rep["recall"] == 1.0


@pytest.mark.parametrize("text,becomes", _OPENS_A_BLOCK,
                         ids=[t for t, _ in _OPENS_A_BLOCK])
def test_a_bare_txbody_paragraph_never_opens_a_block_it_did_not_write(text, becomes):
    """A txBody outside a p:sp — a connector, or an exotic shape type. It renders
    through a SECOND emission site with the same defect, so fixing only the first
    would leave the loss reachable by any deck PowerPoint happens to write that way."""
    body = '<p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody>' % text
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Kept") + body)}
    md, rep = _pptx_graded(parts)
    facts = md_structure(md)
    assert facts["bullet_items"] == 2, "%r became %s" % (text, becomes)
    assert facts["thematic_breaks"] == 0
    assert facts["headings"] == {2: 1}
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_speaker_note_title_never_opens_a_block_it_did_not_write():
    """The notes TITLE lands as a bare paragraph at column zero, where every
    construct is live — unlike a slide title, which is safe only because
    `## Slide N — ` is already in front of it.

    No corpus deck reaches this line: the fixtures give their notes shapes
    `ph="body"`, so the branch that emits a notes title has never once run under
    test. An emission site nothing exercises is not a site that works."""
    notes = ('<p:notes %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:notes>'
             % (P, _sp("## Fabricated", ph="title") + _sp("real note")))
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/notesSlides/notesSlide1.xml": notes}
    md, rep = _pptx_graded(parts)
    facts = md_structure(md)
    assert facts["headings"] == {2: 1, 3: 1}, (
        "`## Slide 1` and `### Speaker notes` are the only real headings; "
        "the note's own text must not become a third")
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_deck_comment_never_opens_a_block_it_did_not_write():
    """Comments render as a bullet list, so a comment reading `- - -` deletes
    itself exactly as a slide bullet does."""
    cm = ('<p:cmLst %s><p:cm><p:text>- - -</p:text></p:cm>'
          '<p:cm><p:text>keep this</p:text></p:cm></p:cmLst>' % P)
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/comments/modernComment_1.xml": cm}
    md, rep = _pptx_graded(parts)
    facts = md_structure(md)
    assert facts["thematic_breaks"] == 0
    assert facts["bullet_items"] == 3, "the body bullet plus BOTH comments"
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_smartart_item_never_opens_a_block_it_did_not_write():
    """`_diagram_list_md` is reached from all three formats, so one unescaped
    diagram label is a loss in a deck, a document and a workbook at once."""
    data = ('<dgm:dataModel xmlns:dgm="urn:d" %s><dgm:ptLst>'
            '<dgm:pt><dgm:t><a:p><a:r><a:t>- - -</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '<dgm:pt><dgm:t><a:p><a:r><a:t>kept</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '</dgm:ptLst></dgm:dataModel>' % A)
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Body")),
             "ppt/diagrams/data1.xml": data}
    md, rep = _pptx_graded(parts)
    assert md_structure(md)["thematic_breaks"] == 0
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_docx_list_item_never_opens_a_block_it_did_not_write():
    """The same defect, one format over. The docx HEADING and PARAGRAPH paths call
    `_esc_lead`; the list-item path never did, so a bulleted `- - -` measured
    `bullet_items 7 -> 6, thematic_breaks 0 -> 1` on the corpus specification at
    `recall: 1.0, valid: True`."""
    parts = {"word/document.xml": _wdoc(_wp("kept", num="1") + _wp("- - -", num="1"))}
    md, rep = _graded(parts)
    facts = md_structure(md)
    assert facts["bullet_items"] == 2
    assert facts["thematic_breaks"] == 0
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_docx_footnote_never_opens_a_block_it_did_not_write():
    parts = {"word/document.xml": _wdoc(_wp("Body")),
             "word/footnotes.xml":
                 ('<w:footnotes %s><w:footnote w:id="1"><w:p><w:r><w:t>15. step'
                  '</w:t></w:r></w:p></w:footnote></w:footnotes>' % W)}
    md, rep = _graded(parts)
    assert md_structure(md)["ordered_items"] == 0
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_workbook_comment_never_opens_a_block_it_did_not_write():
    parts = {"xl/workbook.xml": ('<workbook %s><sheets><sheet name="S" sheetId="1"/>'
                                 '</sheets></workbook>' % S),
             "xl/comments1.xml": ('<comments %s><commentList><comment ref="A1">'
                                  '<text><t>- - -</t></text></comment></commentList>'
                                  '</comments>' % S)}
    md = xlsx_markdown(parts)
    rep = conversion_report(xlsx_source_text(parts), md)
    assert md_structure(md)["thematic_breaks"] == 0
    assert rep["valid"] and rep["recall"] == 1.0


def test_the_escape_is_only_added_where_a_block_could_actually_open():
    """The other half of the contract, and the reason this is not "escape
    everything": a backslash a renderer hides is still a byte a BM25 index, an
    embedder and a human grep can see — `_esc_special` makes that argument for
    inline escapes and it applies here too. Text that was never at risk stays
    verbatim, and the cases below are the ones a careless rule would take:
    `#hashtag` (no space, so not a heading), seven hashes (six is the maximum),
    a pipe row (GFM needs a delimiter line under it), a version or a temperature
    range that merely opens with a digit or a dash."""
    safe = ["#hashtag", "####### seven hashes is not a heading",
            "| a | b |", "  spaces are stripped before this point", "3.5 volts",
            "10.0.0.1 is the gateway", "-40C to 125C", "--verbose", "1.2.3-rc1"]
    parts = {"ppt/slides/slide1.xml": _slide("".join(_sp(t) for t in safe))}
    md, rep = _pptx_graded(parts)
    assert "\\" not in md, md
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_line_of_equals_signs_is_escaped_even_though_an_item_cannot_underline():
    """The one deliberate over-escape, recorded rather than hidden.

    `_LEAD_RULE` escapes a line of `=` because a setext underline turns the line
    ABOVE it into a heading and takes its own characters out of the render. Inside a
    list item there is no line above, so `- ===` was never at risk — but the rule
    stays whole, because `_esc_fig` lead-escapes EVERY line of a multi-line caption
    and there the line above is real. One rule with one meaning is worth more than a
    saved backslash on a rare string, and the round trip is unaffected:
    `markdown_to_text` strips it back off, so no token moves.

    Narrowing this to "first line of a block only" is a real improvement and a
    separate change — it would move bytes on the docx paragraph path, which no
    fixture in this slice demands."""
    parts = {"ppt/slides/slide1.xml": _slide(_sp("==="))}
    md, rep = _pptx_graded(parts)
    assert "- \\===" in md
    assert markdown_to_text(md).strip().endswith("===")
    assert rep["valid"] and rep["recall"] == 1.0


# ------------------------------------------------- the deck's order, not the disk's

# PowerPoint does NOT renumber slide parts when a user drags a slide. It rewrites
# `p:sldIdLst` in `ppt/presentation.xml` and leaves `ppt/slides/slideN.xml` exactly
# where it was. So "sort the part names numerically" is not a reading of the deck at
# all — it is a reading of the ORDER THE SLIDES WERE FIRST CREATED IN, and for any
# deck anyone has ever reordered the two disagree.
#
# Measured before this landed, on the shipped corpus deck: rewriting `sldIdLst` to
# swap slides 2 and 3 left the markdown BYTE-IDENTICAL. `ppt/presentation.xml` was
# not even in `OOXML_MAIN_PARTS["pptx"]`, so the converter never received it, and
# `p:sldIdLst` had zero references anywhere in the module. The xlsx lane in the same
# file had read `xl/workbook.xml` for sheet order all along.
#
# The token gate cannot see it: recall compares MULTISETS, so order is invisible to
# it by construction. `structure_fidelity` can, since P9.6 gave the deck a truth that
# derives the order again by its own reader — the graded half of that lives in
# `tests/integration/test_office_gate_red_direction.py`, over the real corpus deck.
# These stay unit tests on the emitted markdown, which is the other question: what
# the heading actually SAYS, and that it means position rather than filename.

def _pres(rels_and_ids, extra=""):
    """(presentation.xml, its .rels) for a deck whose sldIdLst lists ``rels_and_ids``
    as (rId, target) pairs in presentation order."""
    ids = "".join('<p:sldId id="%d" r:id="%s"/>' % (256 + n, rid)
                  for n, (rid, _t) in enumerate(rels_and_ids))
    pres = ('<p:presentation %s><p:sldIdLst>%s%s</p:sldIdLst></p:presentation>'
            % (P, ids, extra))
    rels = ('<Relationships %s>%s</Relationships>'
            % (RELS, "".join('<Relationship Id="%s" Type="http://schemas.openxmlformats'
                             '.org/officeDocument/2006/relationships/slide" '
                             'Target="%s"/>' % (rid, t) for rid, t in rels_and_ids)))
    return {"ppt/presentation.xml": pres,
            "ppt/_rels/presentation.xml.rels": rels}


def _deck(order, titles=None):
    """A deck of len(titles) slide parts, presented in ``order`` (part numbers)."""
    titles = titles or {}
    parts = {}
    for n in sorted(set(list(order) + list(titles))):
        parts["ppt/slides/slide%d.xml" % n] = _slide(
            _sp(titles.get(n, "Title %d" % n), ph="title") + _sp("body %d" % n))
    parts.update(_pres([("rId%d" % (i + 2), "slides/slide%d.xml" % n)
                        for i, n in enumerate(order)]))
    return parts


def _headings(md):
    return [l for l in md.split("\n") if l.startswith("## ")]


def test_slides_are_emitted_in_the_decks_order_not_the_filename_order():
    """The drag PowerPoint actually performs: slide 5 moved to position 2, parts
    left unrenumbered."""
    parts = _deck([1, 5, 3, 4, 2], {5: "Rollout plan", 1: "Overview"})
    md = pptx_markdown(parts)
    assert _headings(md) == ["## Slide 1 — Overview", "## Slide 2 — Rollout plan",
                             "## Slide 3 — Title 3", "## Slide 4 — Title 4",
                             "## Slide 5 — Title 2"]
    assert md.index("body 5") < md.index("body 3") < md.index("body 2")


def test_the_slide_number_counts_position_in_the_deck_not_the_part_name():
    """`## Slide 2` must mean "the second slide you see", which is the only reading
    a reader of the markdown can act on. Numbering by part name published a deck
    whose headings claimed an order its own content did not have."""
    parts = _deck([7, 3], {7: "First", 3: "Second"})
    md = pptx_markdown(parts)
    assert _headings(md) == ["## Slide 1 — First", "## Slide 2 — Second"]


def test_a_reordered_deck_still_binds_each_slides_own_notes_and_rels():
    """The trap in this change. Slide POSITION and slide PART NUMBER are now two
    different numbers, and everything that reaches for a satellite part —
    `ppt/slides/_rels/slideN.xml.rels`, the `notesSlideN.xml` fallback — must keep
    using the PART number. Conflating them silently attaches slide 5's speaker
    notes to whichever slide happens to sit in position 5."""
    parts = _deck([5, 1], {5: "Fifth", 1: "First"})
    notes = ('<p:notes %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:notes>'
             % (P, _sp("notes belonging to part five", ph="body")))
    parts["ppt/notesSlides/notesSlide5.xml"] = notes
    md = pptx_markdown(parts)
    first, second = md.index("## Slide 1"), md.index("## Slide 2")
    assert first < md.index("notes belonging to part five") < second, (
        "the notes of part 5 must render under the slide that IS part 5, which is "
        "now position 1")


def test_a_deck_with_no_presentation_part_keeps_the_filename_order():
    """The fallback is the behaviour this change replaces, so it can never make a
    deck worse than it already was."""
    parts = {"ppt/slides/slide2.xml": _slide(_sp("B")),
             "ppt/slides/slide10.xml": _slide(_sp("C")),
             "ppt/slides/slide1.xml": _slide(_sp("A"))}
    md = pptx_markdown(parts)
    assert _headings(md) == ["## Slide 1", "## Slide 2", "## Slide 3"]
    assert md.index("- A") < md.index("- B") < md.index("- C")


def test_an_unparseable_presentation_part_keeps_the_filename_order():
    parts = _deck([2, 1])
    parts["ppt/presentation.xml"] = "<p:presentation><unclosed>"
    assert _headings(pptx_markdown(parts)) == ["## Slide 1 — Title 1",
                                               "## Slide 2 — Title 2"]


def test_an_empty_slide_id_list_keeps_the_filename_order():
    parts = _deck([2, 1])
    parts["ppt/presentation.xml"] = '<p:presentation %s><p:sldIdLst/></p:presentation>' % P
    assert _headings(pptx_markdown(parts)) == ["## Slide 1 — Title 1",
                                               "## Slide 2 — Title 2"]


def test_missing_presentation_rels_keeps_the_filename_order():
    parts = _deck([2, 1])
    del parts["ppt/_rels/presentation.xml.rels"]
    assert _headings(pptx_markdown(parts)) == ["## Slide 1 — Title 1",
                                               "## Slide 2 — Title 2"]


def test_a_dangling_slide_id_is_skipped_and_its_slide_still_publishes():
    """A relationship id nothing resolves cannot say where its slide goes — but it
    must not be able to make the slide vanish either. Token recall demands 1.0, so
    a dropped slide fails the whole document; publishing it after the ordered ones
    keeps every word and says plainly that the deck did not place it."""
    parts = _deck([1, 2])
    parts["ppt/presentation.xml"] = parts["ppt/presentation.xml"].replace(
        'r:id="rId3"', 'r:id="rIdMissing"')
    md = pptx_markdown(parts)
    assert "## Slide 1 — Title 1" in md
    assert "body 2" in md and "Title 2" in md
    assert conversion_report(pptx_source_text(parts), md)["recall"] == 1.0


def test_a_slide_part_the_deck_never_lists_still_publishes():
    """A deleted-but-not-purged slide. It is not in the presentation, so it has no
    position — but its words are still in the package, and the gate counts them."""
    parts = _deck([1])
    parts["ppt/slides/slide9.xml"] = _slide(_sp("orphaned copy"))
    md = pptx_markdown(parts)
    assert "## Slide 1 — Title 1" in md
    assert "orphaned copy" in md
    assert "## Slide 2" not in md, (
        "an unlisted part has no position in the deck and must not claim one")
    assert conversion_report(pptx_source_text(parts), md)["recall"] == 1.0


def test_a_slide_listed_twice_is_published_once():
    """Two sldId entries pointing at one part is a malformed deck; publishing the
    slide twice would double every one of its tokens against a ground truth that
    read the part once."""
    parts = _deck([1, 2])
    parts.update(_pres([("rId2", "slides/slide1.xml"),
                        ("rId3", "slides/slide2.xml"),
                        ("rId4", "slides/slide1.xml")]))
    md = pptx_markdown(parts)
    assert _headings(md) == ["## Slide 1 — Title 1", "## Slide 2 — Title 2"]
    assert md.count("- body 1") == 1


@pytest.mark.parametrize("target", ["slides/slide2.xml", "../slides/slide2.xml",
                                    "/ppt/slides/slide2.xml", "./slides/slide2.xml",
                                    "ppt/slides/slide2.xml"])
def test_every_spec_legal_relationship_target_form_resolves(target):
    """A Target is a URI reference relative to the part that holds it, and real
    producers write all of these. A form this misses reads as a dangling id and
    silently drops the deck back to filename order."""
    parts = _deck([2, 1])
    parts.update(_pres([("rId2", target), ("rId3", "slides/slide1.xml")]))
    assert _headings(pptx_markdown(parts)) == ["## Slide 1 — Title 2",
                                               "## Slide 2 — Title 1"]


def test_a_sldid_pointing_at_something_that_is_not_a_slide_is_ignored():
    parts = _deck([1, 2])
    parts.update(_pres([("rId2", "notesSlides/notesSlide1.xml"),
                        ("rId3", "slides/slide2.xml"),
                        ("rId4", "slides/slide1.xml")]))
    md = pptx_markdown(parts)
    assert _headings(md) == ["## Slide 1 — Title 2", "## Slide 2 — Title 1"]


def test_pptx_slide_order_is_public_and_reports_the_order_it_read():
    """`scripts/` records which order it published under, and a script may never
    reach into a private module to find out (CLAUDE.md, the `__init__` boundary).
    An empty result is the honest answer for "this deck could not tell me", and is
    what the caller turns into the `part-name` decision."""
    from backend.ingest import pptx_slide_order
    assert pptx_slide_order(_deck([1, 5, 3])) == ["ppt/slides/slide1.xml",
                                                  "ppt/slides/slide5.xml",
                                                  "ppt/slides/slide3.xml"]
    assert pptx_slide_order({"ppt/slides/slide1.xml": _slide(_sp("x"))}) == []
    assert pptx_slide_order({}) == []


# ------------------------------------------- a block that needs TWO lines to open

def test_svg_figure_labels_never_fabricate_a_table():
    """`svg_text` joins every <text> label of one image with newlines, so a figure's
    labels arrive as adjacent BARE lines in a single block — and a GFM table needs
    exactly that: a delimiter row directly under a line holding a pipe.

    `_esc_lead` cannot see this one, because the danger is a property of the PAIR of
    lines rather than of either alone. Measured before the fix, on a drawing whose
    three labels happen to spell a table: `md_structure` reported
    `tables: [{rows: 2, cols: 2, has_header: True}]` at token recall 1.0 — a
    two-column table fabricated out of a picture, which is a silent structural
    invention for a deck and, where the fact is graded, a false FAILURE for a
    document."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text>| Path | Cycles |</text><text>| --- | --- |</text>'
           '<text>| display read | 40 |</text></svg>')
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    rep = conversion_report(ooxml_source_text("docx", parts), md)
    assert md_structure(md)["tables"] == [], md
    assert rep["valid"] and rep["recall"] == 1.0
    assert _fidelity(parts)["gate"] == "pass"


def test_a_delimiter_row_is_escaped_wherever_it_lands():
    """The one rule in `_esc_block_start` that does not ask what the line says on its
    own, because it cannot: a table needs a delimiter row directly under a line
    holding a pipe, so the hazard belongs to a PAIR of lines and no emitter can see
    its own neighbour. `svg_text` joins a figure's labels with newlines and
    `_join_blocks` stacks consecutive list items with a single newline — both put two
    pieces of source text on adjacent lines, and the bulleted form is the worse of
    the two because `md_structure` does not see the table at all (marko builds
    `ul,li,table,thead,…`; the fact vector reports `tables: 0`).

    A first line is escaped too. It is a delimiter row for whatever comes after it,
    and the emitter does not know what that is."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text>--- | ---</text><text>sensor front end</text></svg>')
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    assert "\\--- | ---" in md
    assert md_structure(md)["tables"] == []
    assert conversion_report(ooxml_source_text("docx", parts), md)["valid"]


def test_the_delimiter_rule_fires_only_on_table_punctuation():
    """A line matching it is made ONLY of dashes, colons, pipes and spaces, so it
    carries no token and escaping it costs nothing worth having. Anything with a
    word in it is ordinary text and keeps its bytes."""
    labels = ["a | b", "up-to-date", "3 - 5 V", "range: -40 to 125",
              "path/to/-file", "|pipe start", "end pipe|"]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">%s</svg>'
           % "".join("<text>%s</text>" % l for l in labels))
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/media/image1.svg": svg}
    md = ooxml_markdown("docx", parts)
    assert "\\" not in md.split("## Figures")[1], md
    assert conversion_report(ooxml_source_text("docx", parts), md)["valid"]


def test_bulleted_labels_never_build_a_table_between_two_items():
    """The site the pair-rule version missed: consecutive list items are joined with
    a single newline, so item two is directly under item one exactly as two bare
    lines are."""
    items = ["| Path | Cycles |", "| --- | --- |", "| display read | 40 |"]
    parts = {"ppt/slides/slide1.xml": _slide("".join(_sp(t) for t in items))}
    md, rep = _pptx_graded(parts)
    assert md_structure(md)["tables"] == [], md
    assert md_structure(md)["bullet_items"] == 3
    assert rep["valid"] and rep["recall"] == 1.0


# The site matters more than the string here. A bullet's leading `- ` is itself a
# list marker, so `markdown_to_text` strips THAT and leaves whatever follows alone —
# which means a bullet can never expose a disagreement about what counts as an
# ordered marker. Only text at COLUMN ZERO can. The first version of this test used a
# pptx bullet and was green while the paragraph path was broken.
_MARKER_SITES = ["docx_paragraph", "docx_heading", "pptx_bullet"]


def _at_site(site, text):
    """The same source text, emitted at one of the places a converter puts it."""
    if site == "pptx_bullet":
        parts = {"ppt/slides/slide1.xml": _slide(_sp("kept") + _sp(text))}
        return pptx_markdown(parts), pptx_source_text(parts)
    body = '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text
    parts = {"word/document.xml": _wdoc(_wp("kept") + body)}
    if site == "docx_heading":
        parts = {"word/document.xml": _wdoc(
                     _wp("kept")
                     + '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                       '<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text),
                 "word/styles.xml":
                     '<w:styles %s><w:style w:styleId="Heading1" w:type="paragraph">'
                     '<w:name w:val="heading 1"/></w:style></w:styles>' % W}
    return docx_markdown(parts), docx_source_text(parts)


@pytest.mark.parametrize("site", _MARKER_SITES)
@pytest.mark.parametrize("text", ["1234567890. bill of materials",
                                  "12345678901234567890. serial",
                                  "20240931123. lot"])
def test_an_ordered_marker_longer_than_commonmark_allows_round_trips(site, text):
    """CommonMark caps an ordered marker at NINE digits, so a ten-digit opener is
    ordinary prose and must not be escaped — the same argument `_esc_special` makes
    about brackets and underscores.

    But THREE regexes read a marker, and they have to agree or the document is
    caught between them: `_ooxml_md._LEAD_LIST_NUM` decides what to escape,
    `_mdstructure._ORDERED` decides what the structure gate sees, and
    `_markdown._LIST` decides what the TOKEN gate sees. Capping only the first two
    left the third stripping a marker no renderer would form, so
    `1234567890. bill of materials` came out unescaped, lost its number on the way
    back through `markdown_to_text`, and the document REFUSED TO PUBLISH at
    `recall: 0.833, missing: [('1234567890', 1)]` — a faithful document failing."""
    md, src = _at_site(site, text)
    rep = conversion_report(src, md)
    assert "\\." not in md, "%r is not a marker and must not be escaped: %r" % (text, md)
    assert rep["valid"] and rep["recall"] == 1.0, rep


@pytest.mark.parametrize("site", _MARKER_SITES)
def test_a_marker_commonmark_does_accept_is_still_escaped(site):
    """The other side of the cap: nine digits IS a marker, at every site."""
    md, src = _at_site(site, "123456789. still a marker")
    rep = conversion_report(src, md)
    assert "123456789\\. still a marker" in md, md
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_list_marker_with_no_content_at_all_still_deletes_itself():
    """The subtlest case in this slice, and the one that caught a bad measurement.

    A bullet whose ENTIRE text is `15.` emits `- 15.`, and CommonMark reads that as a
    bullet holding an EMPTY nested ordered list: the characters are gone from the
    render, while `markdown_to_text` still reports them, so the token gate says
    `recall: 1.0, valid: True`. Measured through marko 2.2.3 on real converter output:

        - alpha / - 15. / - beta   renders as   "alpha beta"

    `_LEAD_LIST_NUM` and `_LEAD_MARK` both required a SPACE after the marker, so none
    of `15.`, `10)`, `1.`, `1)` or `+` was escaped. (`-` alone is caught by
    `_LEAD_RULE` and `*` alone by `_esc`, which is why `+` was the only bullet marker
    left uncovered.)

    The first pass of this measurement concluded the opposite, because it rendered
    `"- 15."` with no trailing newline — and marko parses that as literal text. Real
    converter output always ends in a newline. Any check of what markdown MEANS has to
    be made on the bytes the converter actually emits."""
    bare = ("15.", "10)", "1.", "1)", "+")
    parts = {"ppt/slides/slide1.xml":
             _slide(_sp("alpha") + "".join(_sp(t) for t in bare) + _sp("beta"))}
    md, rep = _pptx_graded(parts)
    facts = md_structure(md)
    assert facts["ordered_items"] == 0, md
    assert facts["bullet_items"] == len(bare) + 2
    assert facts["list_items"] == {0: len(bare) + 2}, "nothing nests inside anything"
    assert rep["valid"] and rep["recall"] == 1.0
    for t in bare:
        assert markdown_to_text(md).find(t) >= 0


# --------------------------------------- a heading that eats its own last character

@pytest.mark.parametrize("text", ["Drain procedure #", "Rev ##", "Section # ", "###"])
def test_a_heading_keeps_a_trailing_hash_the_document_wrote(text):
    """CommonMark lets an ATX heading end with an optional CLOSING SEQUENCE of `#`s
    and deletes it from the render. A heading whose own text ends in a hash — a
    revision marker, an issue reference — therefore comes out one character short,
    and BOTH gates certify it: `#` carries no token, so recall stays 1.0, and the
    heading is still a heading of the same level, so no fact moves.

    Measured through marko 2.2.3: `# Drain procedure #` renders the words
    `['Drain', 'procedure']`."""
    styles = ('<w:styles %s><w:style w:styleId="Heading1" w:type="paragraph">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            '<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text)
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": styles}
    md, rep = _graded(parts)
    assert "\\#" in md, md
    assert markdown_to_text(md).strip() == text.strip()
    assert rep["valid"] and rep["recall"] == 1.0
    assert md_structure(md)["headings"] == {1: 1}


# `# leading` is deliberately absent: a heading whose TEXT starts with a hash is
# escaped by `_esc_lead`, which is a different rule with a different reason, and
# folding the two into one assertion would hide either one breaking.
@pytest.mark.parametrize("text", ["Bug #42", "C# guide", "no hash here",
                                  "issue #7 and #8", "F#"])
def test_a_heading_without_a_closing_sequence_keeps_its_bytes(text):
    """A `#` that is not a trailing run preceded by whitespace was never at risk."""
    styles = ('<w:styles %s><w:style w:styleId="Heading1" w:type="paragraph">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            '<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text)
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": styles}
    md, rep = _graded(parts)
    assert "\\#" not in md, md
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_sheet_named_with_a_trailing_hash_keeps_it():
    parts = {"xl/workbook.xml": ('<workbook %s><sheets>'
                                 '<sheet name="Rev #" sheetId="1"/></sheets>'
                                 '</workbook>' % S)}
    md = xlsx_markdown(parts)
    assert md.strip() == "## Rev \\#"
    assert markdown_to_text(md).strip() == "Rev #"


def test_a_slide_titled_with_a_trailing_hash_keeps_it():
    parts = {"ppt/slides/slide1.xml": _slide(_sp("Errata #", ph="title"))}
    md, rep = _pptx_graded(parts)
    assert "## Slide 1 — Errata \\#" in md
    assert rep["valid"] and rep["recall"] == 1.0


# ------------------------------- who escapes an embedded section's text

@pytest.mark.parametrize("text,fact", [("- - -", "thematic_breaks"),
                                       ("## H2", "headings"),
                                       ("- + nested", "bullet_items")])
def test_a_text_box_never_opens_a_block_it_did_not_write(text, fact):
    """`_embedded_sections` used to decide whether a value was a pre-formatted
    bullet list by asking whether it STARTED WITH `- ` — a guess, and wrong for
    exactly the text that matters. A drawing whose whole content is `- - -` was
    taken for a list, passed through unescaped and published as a thematic break:
    the text box gone from the document at recall 1.0, gate pass.

    Each renderer now escapes its own text, because only it knows what it built."""
    draw = ('<xdr:wsDr xmlns:xdr="urn:x" %s><xdr:sp><xdr:txBody>'
            '<a:p><a:r><a:t>%s</a:t></a:r></a:p></xdr:txBody></xdr:sp></xdr:wsDr>'
            % (A, text))
    parts = {"xl/workbook.xml": ('<workbook %s><sheets>'
                                 '<sheet name="S" sheetId="1"/></sheets></workbook>' % S),
             "xl/drawings/drawing1.xml": draw}
    md = xlsx_markdown(parts)
    rep = conversion_report(xlsx_source_text(parts), md)
    facts = md_structure(md)
    assert facts["thematic_breaks"] == 0
    assert facts["headings"] == {2: 2}, "the sheet and the section, and nothing else"
    assert rep["valid"] and rep["recall"] == 1.0


def test_a_smartart_bullet_list_is_still_passed_through_as_a_list():
    """The other direction: removing the guess must not stop a renderer that really
    did build a bullet list from emitting one."""
    data = ('<dgm:dataModel xmlns:dgm="urn:d" %s><dgm:ptLst>'
            '<dgm:pt><dgm:t><a:p><a:r><a:t>first node</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '<dgm:pt><dgm:t><a:p><a:r><a:t>second node</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '</dgm:ptLst></dgm:dataModel>' % A)
    parts = {"word/document.xml": _wdoc(_wp("Body.")), "word/diagrams/data1.xml": data}
    md, rep = _graded(parts)
    assert "- first node\n- second node" in md
    assert md_structure(md)["bullet_items"] == 2
    assert rep["valid"] and rep["recall"] == 1.0


# ----------------------------------------- a deck that lies about its own order

# The contract for every one of these is the same and is not negotiable: the reader
# NEVER raises, and no slide's TEXT is ever dropped. A dropped slide fails token
# recall outright — measured at 0.72 on a five-slide deck — so a malformed ordering
# record must cost ORDER and nothing else.
_MALFORMED = [
    ("no sldId at all", "", ""),
    ("sldId carrying no r:id", '<p:sldId id="256"/>', ""),
    ("a dangling relationship id", '<p:sldId id="256" r:id="rNope"/>', ""),
    ("an empty target", '<p:sldId id="256" r:id="rId2"/>', ""),
    ("a percent-encoded target", '<p:sldId id="256" r:id="rId2"/>', "slides%2fslide1.xml"),
    ("an absolute url target", '<p:sldId id="256" r:id="rId2"/>', "http://x/slides/slide1.xml"),
    ("a backslashed target", '<p:sldId id="256" r:id="rId2"/>', "slides\\slide1.xml"),
    ("a target escaping the package", '<p:sldId id="256" r:id="rId2"/>', "../../etc/passwd"),
    ("a target naming the presentation", '<p:sldId id="256" r:id="rId2"/>', "presentation.xml"),
    ("a sldIdLst buried in a wrapper", "", ""),
]


@pytest.mark.parametrize("label,ids,target", _MALFORMED,
                         ids=[m[0].replace(" ", "_") for m in _MALFORMED])
def test_a_malformed_ordering_record_costs_order_and_never_text(label, ids, target):
    parts = {"ppt/slides/slide1.xml": _slide(_sp("alpha")),
             "ppt/slides/slide2.xml": _slide(_sp("beta"))}
    body = '<p:sldIdLst>%s</p:sldIdLst>' % ids
    if label.startswith("a sldIdLst buried"):
        body = '<p:x>%s</p:x>' % body
    parts["ppt/presentation.xml"] = '<p:presentation %s>%s</p:presentation>' % (P, body)
    parts["ppt/_rels/presentation.xml.rels"] = (
        '<Relationships %s>%s</Relationships>'
        % (RELS, ('<Relationship Id="rId2" Type="t" Target="%s"/>' % target)
           if target else ""))
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0 and rep["valid"], md
    assert "alpha" in md and "beta" in md


def test_a_relationship_target_that_is_not_a_slide_is_not_ordered():
    """Slide-ness is decided in ONE place — `_SLIDE_PART`, the same predicate the
    notes binding and the rels lookup use. Ordering a part the rest of the lane does
    not recognise would put it in the sequence and nowhere else, and the part number
    the heading needs would not exist to be read."""
    from backend.ingest import pptx_slide_order
    parts = {"ppt/slides/slide1.xml": _slide(_sp("one")),
             "ppt/slides/notslide1.xml": _slide(_sp("not a slide part")),
             "ppt/presentation.xml":
                 '<p:presentation %s><p:sldIdLst><p:sldId id="256" r:id="rId2"/>'
                 '</p:sldIdLst></p:presentation>' % P,
             "ppt/_rels/presentation.xml.rels":
                 '<Relationships %s><Relationship Id="rId2" Type="t" '
                 'Target="slides/notslide1.xml"/></Relationships>' % RELS}
    assert pptx_slide_order(parts) == []
    md, rep = _pptx_graded(parts)
    assert "## Slide 1" in md and rep["valid"]


def test_the_same_slide_listed_five_thousand_times_publishes_once():
    parts = {"ppt/slides/slide1.xml": _slide(_sp("only body")),
             "ppt/presentation.xml":
                 '<p:presentation %s><p:sldIdLst>%s</p:sldIdLst></p:presentation>'
                 % (P, '<p:sldId id="256" r:id="rId2"/>' * 5000),
             "ppt/_rels/presentation.xml.rels":
                 '<Relationships %s><Relationship Id="rId2" Type="t" '
                 'Target="slides/slide1.xml"/></Relationships>' % RELS}
    md, rep = _pptx_graded(parts)
    assert md.count("- only body") == 1
    assert rep["recall"] == 1.0


# ------------------------------------- a deck's table states its own column count
#
# DrawingML spells a merge as ATTRIBUTES on `a:tc`: the origin carries `gridSpan`
# (or `rowSpan`) and every position it covers is still present, marked `hMerge` (or
# `vMerge`) and EMPTY. GFM has no colspan, so the emptiness is right — but an
# always-empty TRAILING column looks exactly like the styled-but-valueless one
# `_gfm_table` trims, and trimming it loses a column the deck declares.

def _tc(text, attrs=""):
    return ('<a:tc%s><a:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></a:txBody>'
            '<a:tcPr/></a:tc>' % (attrs, text))


def _tbl(rows, cols, inner=""):
    """A graphicFrame table declaring ``cols`` columns; ``rows`` is a list of rows,
    each a list of (text, tc-attributes)."""
    grid = "".join('<a:gridCol w="100"/>' for _ in range(cols))
    trs = "".join('<a:tr h="1">%s</a:tr>'
                  % ("".join(_tc(t, at) for t, at in row) + inner) for row in rows)
    return ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
            '<a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>' % (grid, trs))


def test_a_span_in_the_last_column_does_not_narrow_a_slide_table():
    """Measured before the floor landed: a two-column table published as ONE, and
    the loss is invisible to every other signal — the covered cell is empty, so it
    carries no token for recall to miss and the markdown is well-formed GFM."""
    parts = {"ppt/slides/slide1.xml": _slide(_tbl(
        [[("Owner", ' gridSpan="2"'), ("", ' hMerge="1"')],
         [("fabric team", ""), ("", "")]], 2))}
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    assert md_structure(md)["tables"][0]["cols"] == 2


def test_a_column_the_grid_declares_but_nothing_fills_is_still_a_column():
    """The same rule, and it has to be the same rule: a deck STATES its grid, so a
    width is read rather than inferred. Inferring made an emptied column and a
    span-covered one indistinguishable."""
    parts = {"ppt/slides/slide1.xml": _slide(_tbl(
        [[("a", ""), ("b", ""), ("", "")], [("c", ""), ("d", ""), ("", "")]], 3))}
    assert md_structure(pptx_markdown(parts))["tables"][0]["cols"] == 3


def test_a_table_nested_in_a_cell_does_not_widen_its_owner():
    """A nested table declares a grid of its own. Counting every `a:gridCol` in the
    subtree made the owner as wide as both put together, and GFM has no cell that
    can hold a table anyway — the inner rows flatten into the owning cell."""
    inner = ('<a:tbl><a:tblGrid><a:gridCol w="1"/><a:gridCol w="1"/></a:tblGrid>'
             '<a:tr h="1">%s%s</a:tr></a:tbl>' % (_tc("in1"), _tc("in2")))
    parts = {"ppt/slides/slide1.xml": _slide(
        _tbl([[("outer", "")]], 1, inner=""))}
    parts["ppt/slides/slide1.xml"] = _slide(
        '<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
        '<a:tblGrid><a:gridCol w="1"/></a:tblGrid><a:tr h="1">'
        '<a:tc><a:txBody><a:p><a:r><a:t>outer</a:t></a:r></a:p></a:txBody>%s</a:tc>'
        '</a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>' % inner)
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    assert md_structure(md)["tables"][0]["cols"] == 1


# ------------------------------------ a bullet may not be indented onto thin air
#
# CommonMark nests a child item only under a parent that EXISTS. A deck's outline
# level is a free integer, so `"  " * lvl` can indent an item for a depth nothing
# opened — and past its parent's content column by four, an indent stops making a
# list item at all. docx grew the clamp for this in P0.1 (`_list_indent`); the deck
# path did not, and the three measurements below are what that cost.

def _body(*levelled):
    paras = "".join('<a:p>%s<a:r><a:t>%s</a:t></a:r></a:p>'
                    % ('<a:pPr lvl="%d"/>' % lvl if lvl else "", text)
                    for lvl, text in levelled)
    return ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
            '</p:nvSpPr><p:txBody>%s</p:txBody></p:sp>' % paras)


def test_a_bullet_two_levels_below_its_parent_is_clamped_to_the_open_level():
    parts = {"ppt/slides/slide1.xml": _slide(_body((0, "top"), (2, "skipped")))}
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    assert md_structure(md)["list_items"] == {0: 1, 1: 1}


def test_a_bullet_three_levels_below_its_parent_is_not_swallowed_by_it():
    """The worst of the three, because nothing in the report moves. Six columns of
    indent under a parent whose content column is two is a LAZY CONTINUATION: the
    item's text is absorbed into the paragraph above it, the item stops existing,
    and every token is still present — so `recall` reads 1.0 and the bullet is
    simply gone. Measured: `list_items {0: 2}` for a slide holding three."""
    parts = {"ppt/slides/slide1.xml":
             _slide(_body((0, "top"), (3, "swallowed"), (0, "home")))}
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    assert md_structure(md)["list_items"] == {0: 2, 1: 1}


def test_a_slide_whose_first_bullet_is_deep_does_not_open_a_code_block():
    """With no ancestor at all the deepest markdown can put an item is depth 0.
    Indenting it four columns made the `- ` marker literal text inside an indented
    CODE BLOCK — a listing the deck never wrote, and no list item at all."""
    parts = {"ppt/slides/slide1.xml": _slide(_body((2, "starts deep"), (0, "home")))}
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    facts = md_structure(md)
    assert facts["code_blocks"] == 0
    assert facts["list_items"] == {0: 2}


def test_a_block_between_two_items_restarts_the_nesting():
    """A table ends the list, so the item after it has no open ancestor however
    deep the deck says it is. The clamp is per-list, not per-slide."""
    parts = {"ppt/slides/slide1.xml": _slide(
        _body((0, "before")) + _tbl([[("cell", "")]], 1) + _body((1, "after")))}
    md = pptx_markdown(parts)
    assert md_structure(md)["list_items"] == {0: 2}


def test_the_deck_ground_truth_agrees_with_every_clamped_case():
    from backend.ingest import pptx_source_structure
    from backend.validate import structure_fidelity_report
    """The clamp is a fact about MARKDOWN, so both readers must reach it — the
    converter by indenting, the ground truth by counting open ancestors — and the
    gate is what proves they did."""
    for shapes in (_body((0, "top"), (2, "skipped")),
                   _body((0, "top"), (3, "swallowed"), (0, "home")),
                   _body((2, "starts deep"), (0, "home")),
                   _body((0, "a"), (1, "b"), (2, "c"), (0, "d"), (2, "e"))):
        parts = {"ppt/slides/slide1.xml": _slide(shapes)}
        verdict = structure_fidelity_report(md_structure(pptx_markdown(parts)),
                                            pptx_source_structure(parts))
        assert verdict["gate"] == "pass", (shapes, verdict["deltas"])


# ------------------------------- a picture must not renumber the steps after it
#
# `_join_blocks` separates any non-`li` block with a blank line, so a column-0
# sentinel CLOSES the list it interrupts and every item after it restarts at depth
# 0. docx already indents a picture that shares a step's paragraph (`item_pad`,
# added when a screenshot renumbered a runbook); a picture in a paragraph of its
# OWN, and every deck picture, went out at column 0. Both are ordinary documents —
# a screenshot between a step and its sub-step is what a runbook looks like — and
# `build_bundle.py` always converts with images ON, so the loss is live, not
# hypothetical. Measured on a two-item nested list with one picture between them:
#
#     emit_images=False   list_items {0: 1, 1: 1}   gate pass
#     emit_images=True    list_items {0: 2}         gate FAIL
#
# A faithful conversion of a real document, refusing to publish, in both formats.

def _pic_rels(part="ppt/media/image1.png", rid="rId9"):
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="%s" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/%s"/></Relationships>'
            % (rid, part.rsplit("/", 1)[-1]))


def test_a_slide_picture_between_two_bullets_keeps_the_nesting():
    pic = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="pic"/></p:nvPicPr>'
           '<p:blipFill><a:blip r:embed="rId9"/></p:blipFill></p:pic>')
    parts = {"ppt/slides/slide1.xml":
             _slide(_body((0, "parent")) + pic + _body((1, "child"))),
             "ppt/slides/_rels/slide1.xml.rels": _pic_rels(),
             "ppt/media/image1.png": "PNG"}
    facts = md_structure(pptx_markdown(parts, emit_images=True))
    assert facts["list_items"] == {0: 1, 1: 1}


def test_a_slide_picture_outside_a_list_is_still_at_column_zero():
    """The indent is a continuation of an OPEN item, never decoration: with no list
    open the sentinel sits where it always did."""
    pic = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="pic"/></p:nvPicPr>'
           '<p:blipFill><a:blip r:embed="rId9"/></p:blipFill></p:pic>')
    parts = {"ppt/slides/slide1.xml": _slide(pic),
             "ppt/slides/_rels/slide1.xml.rels": _pic_rels(),
             "ppt/media/image1.png": "PNG"}
    md = pptx_markdown(parts, emit_images=True)
    assert "\n<!-- ooxml-image:ppt/media/image1.png -->" in md
    assert "\n  <!-- ooxml-image" not in md


def test_a_slide_table_after_a_bullet_still_ends_the_list():
    """A table is a block of its own — it really does end the list — so the picture
    after it must NOT be indented onto an item that has closed."""
    pic = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="pic"/></p:nvPicPr>'
           '<p:blipFill><a:blip r:embed="rId9"/></p:blipFill></p:pic>')
    parts = {"ppt/slides/slide1.xml":
             _slide(_body((0, "before")) + _tbl([[("cell", "")]], 1) + pic),
             "ppt/slides/_rels/slide1.xml.rels": _pic_rels(),
             "ppt/media/image1.png": "PNG"}
    md = pptx_markdown(parts, emit_images=True)
    assert "\n<!-- ooxml-image:ppt/media/image1.png -->" in md


def test_a_deck_with_a_picture_inside_a_list_passes_its_own_gate():
    from backend.ingest import pptx_source_structure
    from backend.validate import structure_fidelity_report
    pic = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="pic"/></p:nvPicPr>'
           '<p:blipFill><a:blip r:embed="rId9"/></p:blipFill></p:pic>')
    parts = {"ppt/slides/slide1.xml":
             _slide(_body((0, "parent")) + pic + _body((1, "child"), (0, "next"))),
             "ppt/slides/_rels/slide1.xml.rels": _pic_rels(),
             "ppt/media/image1.png": "PNG"}
    for flag in (False, True):
        verdict = structure_fidelity_report(
            md_structure(pptx_markdown(parts, emit_images=flag)),
            pptx_source_structure(parts))
        assert verdict["gate"] == "pass", (flag, verdict["deltas"])


def test_a_picture_in_its_own_docx_paragraph_keeps_the_nesting():
    """The docx half of the same defect. `item_pad` covers a picture that shares a
    step's paragraph; a picture in a paragraph of its own — which is how Word
    documents actually carry a screenshot — went out at column 0 and closed the
    list underneath it."""
    from backend.ingest import docx_source_structure
    from backend.validate import structure_fidelity_report
    body = (_wp("parent", num="1", ilvl=0)
            + '<w:p><w:r><w:drawing xmlns:r="http://schemas.openxmlformats.org'
              '/officeDocument/2006/relationships"><wp:inline xmlns:wp="wp">'
              '<a:graphic xmlns:a="a"><a:graphicData><pic:pic xmlns:pic="pic">'
              '<pic:blipFill><a:blip r:embed="rId9"/></pic:blipFill>'
              '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
              '</w:r></w:p>'
            + _wp("child", num="1", ilvl=1))
    parts = {"word/document.xml": _wdoc(body),
             "word/numbering.xml": NUMBERING,
             "word/_rels/document.xml.rels":
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
             'relationships"><Relationship Id="rId9" Type="http://schemas.'
             'openxmlformats.org/officeDocument/2006/relationships/image" '
             'Target="media/image1.png"/></Relationships>',
             "word/media/image1.png": "PNG"}
    facts = md_structure(docx_markdown(parts, emit_images=True))
    assert facts["list_items"] == {0: 1, 1: 1}
    verdict = structure_fidelity_report(facts, docx_source_structure(parts))
    assert verdict["gate"] == "pass", verdict["deltas"]


def test_a_picture_that_merely_follows_a_list_is_not_pulled_into_it():
    """The indent exists to keep the items BELOW a picture at their depth. With no
    item below, it would only claim the picture sits inside the last bullet — which
    the source does not say — so it settles back to column 0. Measured: without
    this, `office/kestrel-clock-spec.docx` moved a byte for no gate benefit."""
    pic = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="pic"/></p:nvPicPr>'
           '<p:blipFill><a:blip r:embed="rId9"/></p:blipFill></p:pic>')
    parts = {"ppt/slides/slide1.xml": _slide(_body((0, "last bullet")) + pic),
             "ppt/slides/_rels/slide1.xml.rels": _pic_rels(),
             "ppt/media/image1.png": "PNG"}
    md = pptx_markdown(parts, emit_images=True)
    assert "\n<!-- ooxml-image:ppt/media/image1.png -->" in md


# ------------------------------- a clamp must be STICKY, or peers become ancestors
#
# `_list_indent` used to clamp to the HEIGHT OF THE STACK — "one deeper than the
# deepest item currently open" — which is right for the FIRST item at a skipped
# level and wrong for every one after it, because each successive item finds the
# stack one entry taller. Three PEER bullets at outline level 2 published as a
# three-deep chain: "release fabric traffic" became a sub-step of "hold PLL bypass",
# and "run the smoke suite" a sub-step of that. A swept comparison of every level
# sequence of length 2-4 over levels 0-3 found 199 of 336 misrepresented.
#
# The rule that is actually true of markdown is CONTAINMENT: an item's depth is how
# many STRICTLY SHALLOWER ancestors are still open above it. Two items at the same
# source level are siblings whatever their level is, and a skipped level costs one
# step of depth once rather than compounding.

_LEVEL_SEQUENCES = [
    ([0, 1, 2, 3], [0, 1, 2, 3], "contiguous nesting is untouched"),
    ([0, 2, 2], [0, 1, 1], "two peers below a skipped level are PEERS"),
    ([0, 2, 2, 2], [0, 1, 1, 1], "and stay peers however many there are"),
    ([0, 1, 3, 3], [0, 1, 2, 2], "a skip deeper in the tree behaves the same"),
    ([2, 2, 2], [0, 0, 0], "a slide that opens deep has no ancestors at all"),
    ([0, 1, 1, 0], [0, 1, 1, 0], "an ordinary outline"),
    ([0, 3, 1], [0, 1, 1], "a dedent to a level that was never opened"),
    ([1, 0, 1], [0, 0, 1], "a shallower item closes the deeper one"),
]


@pytest.mark.parametrize("levels,depths,why", _LEVEL_SEQUENCES,
                         ids=["-".join(str(l) for l in r[0])
                              for r in _LEVEL_SEQUENCES])
def test_a_deck_bullet_sits_at_the_depth_its_containment_implies(levels, depths, why):
    parts = {"ppt/slides/slide1.xml":
             _slide(_body(*[(lvl, "item %d" % i)
                            for i, lvl in enumerate(levels)]))}
    md, rep = _pptx_graded(parts)
    assert rep["recall"] == 1.0
    got = [d for kind, d in md_structure(md)["block_sequence"] if kind == "li"]
    assert got == depths, "%s: %s -> %s, wanted %s" % (why, levels, got, depths)


@pytest.mark.parametrize("levels,depths,why", _LEVEL_SEQUENCES,
                         ids=["-".join(str(l) for l in r[0])
                              for r in _LEVEL_SEQUENCES])
def test_the_deck_ground_truth_reaches_the_same_containment(levels, depths, why):
    """Independently: the converter by indenting, the truth by counting ancestors,
    `md_structure` by scanning the columns that came out. The gate is what proves
    all three met."""
    from backend.ingest import pptx_source_structure
    from backend.validate import structure_fidelity_report
    parts = {"ppt/slides/slide1.xml":
             _slide(_body(*[(lvl, "item %d" % i)
                            for i, lvl in enumerate(levels)]))}
    facts = pptx_source_structure(parts)
    assert [d for kind, d in facts["block_sequence"] if kind == "li"] == depths, why
    verdict = structure_fidelity_report(md_structure(pptx_markdown(parts)), facts)
    assert verdict["gate"] == "pass", verdict["deltas"]


def test_a_docx_list_reaches_the_same_containment():
    """`_list_indent` is shared by both lanes, so the staircase was a docx defect
    too — and the docx TRUTH read `w:ilvl` raw, which made an ordinary Word runbook
    with one skipped level fail the gate and refuse to publish."""
    from backend.ingest import docx_source_structure
    from backend.validate import structure_fidelity_report
    body = "".join(_wp("item %d" % i, num="1", ilvl=lvl)
                   for i, lvl in enumerate([0, 2, 2, 1, 0]))
    parts = {"word/document.xml": _wdoc(body), "word/numbering.xml": NUMBERING}
    md = docx_markdown(parts)
    assert [d for kind, d in md_structure(md)["block_sequence"] if kind == "li"] \
        == [0, 1, 1, 1, 0]
    verdict = structure_fidelity_report(md_structure(md),
                                        docx_source_structure(parts))
    assert verdict["gate"] == "pass", verdict["deltas"]


def test_a_level_below_zero_is_still_a_top_level_item():
    """`ST_TextIndentLevelType` is 0-8, so a negative level is malformed input —
    but a ground truth that reported `list_items {-1: 1}` would fail a document no
    renderer can disagree about."""
    from backend.ingest import pptx_source_structure
    parts = {"ppt/slides/slide1.xml": _slide(_body((-1, "alpha"), (0, "beta")))}
    assert md_structure(pptx_markdown(parts))["list_items"] == {0: 2}
    assert pptx_source_structure(parts)["list_items"] == {0: 2}


# ============================================================ P9.8a: a deck's runs
#
# A deck's emphasis and hyperlinks were dropped, and the drop was HONEST — it was
# counted (`dropped_shape_emphasis`, `dropped_shape_links`) and the structural truth
# left the facts OUT of its vector rather than stating a zero, because stating
# `strong: 0` over a deck that draws bold would certify the loss instead of catching
# it. That is why every deck report carried `unmeasured: [strong, em, strike, links]`.
#
# Retiring that list is not a matter of stating the zeros. It is a matter of the
# converter no longer losing anything, which is what these tests are for. The
# rendering machinery is the docx lane's — `_render_runs` takes `[(marks, text)]` and
# already owns CommonMark's flanking rules, the coalescing that stops `**a****b**`,
# and the `**~~x~~**` delimiter cases. Only the DrawingML READER is new.

def _rsp(runs, ph=None, lvl=None):
    """A shape whose single paragraph is an explicit list of (rPr, text) runs."""
    phx = '<p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr>' % (
        '<p:ph type="%s"/>' % ph if ph else "")
    ppr = '<a:pPr lvl="%d"/>' % lvl if lvl else ""
    body = "".join('<a:r>%s<a:t>%s</a:t></a:r>' % (rpr, t) for rpr, t in runs)
    return ('<p:sp>%s<p:txBody><a:p>%s%s</a:p></p:txBody></p:sp>'
            % (phx, ppr, body))


@pytest.mark.parametrize("rpr,want", [
    ('<a:rPr b="1"/>', "**both**"),
    ('<a:rPr i="1"/>', "*both*"),
    ('<a:rPr strike="sngStrike"/>', "~~both~~"),
    ('<a:rPr strike="dblStrike"/>', "~~both~~"),
    ('<a:rPr b="1" i="1"/>', "***both***"),
    # The OFF spellings a deck really writes. `b="0"` is not bold, and neither is
    # `strike="noStrike"` — emitting markers for those would invent emphasis the
    # slide does not draw, which fails a faithful conversion just as surely as
    # dropping it.
    ('<a:rPr b="0"/>', "both"),
    ('<a:rPr strike="noStrike"/>', "both"),
    ('<a:rPr/>', "both"),
])
def test_a_deck_run_emits_the_emphasis_it_draws(rpr, want):
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(
        _rsp([("", "Sign-off needs "), (rpr, "both"), ("", " now")]))})
    assert "- Sign-off needs %s now" % want in md


def test_adjacent_runs_with_one_mark_coalesce_into_one_span():
    """Not tidiness. A deck splits a word across runs at any property boundary, so
    wrapping each run on its own emits `**Dma****Arbiter**`, which the text layer's
    non-greedy bold pattern mis-pairs into a stray literal `**`. The docx lane
    already solves this; the deck reader must feed the same solver."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(
        _rsp([('<a:rPr b="1"/>', "Dma"), ('<a:rPr b="1"/>', "Arbiter")]))})
    assert "- **DmaArbiter**" in md
    assert "****" not in md


def test_a_deck_hyperlink_renders_as_a_link_with_its_target():
    """DrawingML puts the link INSIDE the run's properties (`a:hlinkClick` under
    `a:rPr`), not around a span of runs the way `w:hyperlink` does — so this is a
    genuinely different read, not the docx walk with different tag names."""
    parts = {
        "ppt/slides/slide1.xml": _slide(_rsp([
            ("", "Full numbers live in the "),
            ('<a:rPr><a:hlinkClick xmlns:r="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships" r:id="rId9"/></a:rPr>',
             "fabric spec"),
            ("", ", not here")])),
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId9" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/hyperlink" Target="https://example.invalid/fabric" '
            'TargetMode="External"/></Relationships>',
    }
    md = pptx_markdown(parts)
    assert ("- Full numbers live in the [fabric spec](https://example.invalid/fabric)"
            ", not here") in md


def test_an_internal_jump_keeps_its_text_and_emits_no_link():
    """A slide-to-slide jump has no URL a reader outside the deck can follow, and
    `[text]()` is a dead link in the stored bytes. The docx lane makes the same call
    for an internal `w:hyperlink`: keep the text, drop the address."""
    parts = {
        "ppt/slides/slide1.xml": _slide(_rsp([
            ('<a:rPr><a:hlinkClick xmlns:r="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships" r:id="rId5" action="ppaction://hlinksldjump"/>'
             '</a:rPr>', "see the backup")])),
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId5" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/slide" Target="slide4.xml"/></Relationships>',
    }
    md = pptx_markdown(parts)
    assert "- see the backup" in md
    assert "](" not in md


def test_emphasis_markers_in_the_source_text_are_still_escaped():
    """The marks are the converter's, so the TEXT's own asterisks must stay text.
    A bullet reading `set *ready* high` that came out as real emphasis was the
    fabrication the conditional zeros were written to catch; now that the deck emits
    real emphasis, escaping is the only thing keeping the two apart."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(
        _rsp([("", "set *ready* high")]))})
    assert "- set \\*ready\\* high" in md


def test_a_deck_that_draws_nothing_is_byte_identical_to_the_old_walk():
    """The regression that matters most: eight shipped documents must not move a
    byte. A run with no `a:rPr` is the overwhelmingly common case, and the new
    reader has to join those exactly as the flat text walk did — including the
    whitespace collapse across `a:br` and across run boundaries."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(
        _rsp([("", "Reset the "), ("", "clock  tree"), ("", " now")]))})
    assert "- Reset the clock tree now" in md


# ========================================================= P9.8c: a workbook's cells
#
# The same move as the deck, one layer down. A workbook states emphasis per CELL, via
# the style index `@s` into `cellXfs -> fonts`, rather than per run — so a bold header
# cell is one bold span covering the whole cell, and a struck-through row is one
# strike span per cell. `dropped_cell_emphasis` counted exactly that and the report
# carried `unmeasured: [strong, em, strike, links]` on every workbook.

XF = ('<styleSheet %s>'
      '<fonts count="4"><font/><font><b/></font><font><i/></font>'
      '<font><strike/></font></fonts>'
      '<cellXfs count="4"><xf fontId="0"/><xf fontId="1"/><xf fontId="2"/>'
      '<xf fontId="3"/></cellXfs></styleSheet>' % S)


def _styled_book(rows, extra=None):
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/styles.xml": XF, "xl/worksheets/sheet1.xml": _sheet(rows)}
    parts.update(extra or {})
    return parts


@pytest.mark.parametrize("style,want", [
    ("1", "**Corner**"),
    ("2", "*Corner*"),
    ("3", "~~Corner~~"),
    ("0", "Corner"),
])
def test_a_styled_cell_emits_the_emphasis_its_font_carries(style, want):
    md = xlsx_markdown(_styled_book(
        '<row r="1"><c r="A1" s="%s" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Margin</t></is></c></row>' % style))
    assert "| %s | Margin |" % want in md, md


def test_a_cell_with_no_text_gains_no_markers():
    """`****` in an empty cell would be literal asterisks the workbook never wrote,
    and a GFM row of them is a delimiter row waiting to happen."""
    md = xlsx_markdown(_styled_book(
        '<row r="1"><c r="A1" s="1" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" s="1"/>'
        '<c r="C1" s="1" t="inlineStr"><is><t>Margin</t></is></c></row>'))
    assert "| **Corner** |  | **Margin** |" in md, md
    assert "****" not in md


def test_a_cell_hyperlink_renders_as_a_link():
    """A workbook attaches the link to the CELL, in a `<hyperlinks>` block keyed by
    `@ref`, rather than to a run — so this is a third distinct read, not the deck's
    with different tag names."""
    sheet = ('<worksheet %s xmlns:r="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>the fabric spec</t></is>'
             '</c><c r="B1" t="inlineStr"><is><t>owner</t></is></c></row>'
             '</sheetData><hyperlinks><hyperlink ref="A1" r:id="rId9"/>'
             '</hyperlinks></worksheet>' % S)
    parts = {"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
             "xl/worksheets/sheet1.xml": sheet,
             "xl/worksheets/_rels/sheet1.xml.rels":
                 '<Relationships %s><Relationship Id="rId9" '
                 'Target="https://example.invalid/fabric" TargetMode="External"/>'
                 '</Relationships>' % RELS}
    md = xlsx_markdown(parts)
    assert "[the fabric spec](https://example.invalid/fabric)" in md, md


def test_a_workbook_with_no_styles_is_byte_identical_to_the_old_walk():
    md = xlsx_markdown({"xl/workbook.xml": WB, "xl/_rels/workbook.xml.rels": WB_RELS,
                        "xl/worksheets/sheet1.xml": _sheet(
                            '<row r="1"><c r="A1" t="inlineStr"><is><t>Corner</t>'
                            '</is></c><c r="B1"><v>0.94</v></c></row>')})
    assert "| Corner | 0.94 |" in md


# ============================================== P9.8b: a deck's automatic numbering
#
# `a:buAutoNum` makes a paragraph an ORDERED item. Markdown holds that perfectly
# well, so the deck lane dropping it was a pure loss: `1. 2. 3.` rendered as three
# identical `-` bullets, and a reader could not tell a SEQUENCE from a set. Token
# recall could not see it — the ordinal is not a token the deck stores, it is drawn —
# and the structural truth deliberately OMITTED `ordered_items` rather than state a
# zero that would certify the drop, so every deck report carried it in `unmeasured`.
#
# THE CASCADE is what makes this more than a tag read. A paragraph inherits its
# bullet from, in order: its own `a:pPr`, the shape's `a:txBody/a:lstStyle`, the
# slide LAYOUT's placeholder `a:lstStyle`, and finally the slide MASTER's
# `p:txStyles/p:bodyStyle`. Reading only the first says "no numbering" over a deck
# whose entire body list is numbered from the master.

def _numbered_shape(paras, ph="body", lst="", idx="1"):
    """A body shape whose paragraphs carry (level, pPr_inner, text)."""
    body = "".join('<a:p><a:pPr lvl="%d">%s</a:pPr><a:r><a:t>%s</a:t></a:r></a:p>'
                   % (lvl, inner, text) for lvl, inner, text in paras)
    return ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="%s" idx="%s"/></p:nvPr></p:nvSpPr>'
            '<p:txBody>%s%s</p:txBody></p:sp>' % (ph, idx, lst, body))


AUTO = '<a:buAutoNum type="arabicPeriod"/>'


def test_an_auto_numbered_paragraph_renders_as_an_ordered_item():
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape([
        (0, AUTO, "Reset the clock tree"),
        (0, AUTO, "Release fabric traffic")]))})
    assert "1. Reset the clock tree" in md, md
    assert "2. Release fabric traffic" in md, md


def test_a_plain_bullet_between_numbered_items_restarts_the_sequence():
    """What a renderer really shows. An unordered item ends the ordered list, so the
    next numbered item starts at 1 again — which is exactly the `1, 2, 1, 2` shape
    `ordered_numbers` exists to make visible."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape([
        (0, AUTO, "one"), (0, AUTO, "two"),
        (0, "", "an aside"),
        (0, AUTO, "three")]))})
    assert "1. one" in md and "2. two" in md
    assert "- an aside" in md
    assert "1. three" in md, md


def test_a_declared_start_is_honoured():
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape([
        (0, '<a:buAutoNum type="arabicPeriod" startAt="5"/>', "five"),
        (0, AUTO, "six")]))})
    assert "5. five" in md and "6. six" in md, md


def test_numbering_inherited_from_the_shapes_own_list_style_still_numbers():
    """The first cascade step. `a:lstStyle` on the shape sets the default for every
    paragraph in it, and reading only each paragraph's own `a:pPr` publishes a whole
    numbered body list as plain bullets."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape(
        [(0, "", "one"), (0, "", "two")],
        lst='<a:lstStyle><a:lvl1pPr>%s</a:lvl1pPr></a:lstStyle>' % AUTO))})
    assert "1. one" in md and "2. two" in md, md


def test_numbering_inherited_from_the_slide_master_still_numbers():
    """The last cascade step, and the one the old code called out as the reason it
    could only answer "could be numbered" rather than counting: layout and master
    parts were not read at all, so neither side of either gate could see them."""
    parts = {
        "ppt/slides/slide1.xml": _slide(_numbered_shape(
            [(0, "", "one"), (0, "", "two")])),
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
        "ppt/slideLayouts/slideLayout1.xml":
            '<p:sldLayout %s><p:cSld><p:spTree/></p:cSld></p:sldLayout>' % P,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        "ppt/slideMasters/slideMaster1.xml":
            '<p:sldMaster %s><p:cSld><p:spTree/></p:cSld><p:txStyles><p:bodyStyle>'
            '<a:lvl1pPr>%s</a:lvl1pPr></p:bodyStyle></p:txStyles></p:sldMaster>'
            % (P, AUTO),
    }
    md = pptx_markdown(parts)
    assert "1. one" in md and "2. two" in md, md


def test_bu_none_beats_an_inherited_number():
    """A paragraph that says `a:buNone` draws NO bullet, whatever the master says.
    Letting the inherited number win would number a line the slide shows plain."""
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape(
        [(0, '<a:buNone/>', "a lead-in"), (0, "", "one")],
        lst='<a:lstStyle><a:lvl1pPr>%s</a:lvl1pPr></a:lstStyle>' % AUTO))})
    assert "- a lead-in" in md, md
    assert "1. one" in md, md


def test_an_ordered_item_that_opens_a_block_is_still_escaped():
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(_numbered_shape([
        (0, AUTO, "- - -")]))})
    assert "1. \\- - -" in md, md


# =========== P9.9: two adjacent spans CommonMark cannot tell apart without help
#
# `***alpha***` immediately followed by `*beta*` emits a delimiter run of FOUR
# asterisks, and CommonMark reads ONE em span where the document draws two. Measured
# at HEAD on an ordinary Word paragraph of that shape: `structure_fidelity` fails on
# `em`, `token_recall` reads 0.0 (the text layer mis-pairs it and leaves a literal
# `*beta*`), and the document REFUSES TO PUBLISH. A `**~~x~~**` span after a word
# character is the same family through a different rule — the `**` cannot LEFT-FLANK
# because its outer neighbour is a letter and its inner neighbour is a `~`.
#
# The separator is an EMPTY HTML COMMENT. It renders as nothing, carries no token, and
# breaks the delimiter run; `markdown_to_text` and `_mdstructure._words` both remove
# it to NOTHING, because it only ever stands where the two spans are adjacent with no
# whitespace — between two halves of one word.
#
# Inserted only where it is NEEDED. Emphasis with spaces around it, which is almost
# all emphasis, is untouched — verified below and by every shipped document staying
# byte-identical.

_SEP = "<!---->"


def _runs_md(segments):
    from backend.ingest._ooxml_md import _render_runs
    return _render_runs(segments)


@pytest.mark.parametrize("segments,want", [
    # The merge: two asterisk runs written against each other become one.
    ([(("strong", "em"), "alpha"), (("em",), "beta")],
     "***alpha***" + _SEP + "*beta*"),
    ([(("em",), "alpha"), (("strong", "em"), "beta")],
     "*alpha*" + _SEP + "***beta***"),
    ([(("strong",), "alpha"), (("strong", "em"), "beta")],
     "**alpha**" + _SEP + "***beta***"),
    # The flank block: `**` after a letter, with a `~` just inside it.
    ([((), "alpha"), (("strong", "strike"), "beta")],
     "alpha" + _SEP + "**~~beta~~**"),
    ([(("strike", "em"), "alpha"), ((), "beta")],
     "*~~alpha~~*" + _SEP + "beta"),
    # NOT needed: a marker against a letter that is not itself next to punctuation.
    ([((), "pre"), (("strong",), "fix")], "pre**fix**"),
    ([(("strong",), "Dma"), ((), "Arbiter")], "**Dma**Arbiter"),
    # NOT needed: the overwhelmingly common shape, emphasis with spaces around it.
    ([((), "set "), (("strong",), "ready"), ((), " high")], "set **ready** high"),
    # NOT needed: different delimiter characters do not merge.
    ([(("strong",), "alpha"), (("strike",), "beta")], "**alpha**~~beta~~"),
])
def test_the_separator_lands_exactly_where_the_delimiters_would_lie(segments, want):
    assert _runs_md(segments) == want


def test_the_separator_is_never_the_first_thing_on_a_line():
    """A line that STARTS with `<!--` is an HTML block to CommonMark, and nothing in
    it is parsed as inline markdown at all — measured, marko reads no emphasis in
    `<!---->**~~x~~**`. It can only ever sit BETWEEN two groups, so the first group
    always shields it, and this pins that."""
    for segs in ([(("strong", "strike"), "beta")],
                 [(("strong", "em"), "a"), (("em",), "b")]):
        assert not _runs_md(segs).startswith("<!--")


def test_a_deck_bullet_with_two_adjacent_spans_publishes_them_both():
    md = pptx_markdown({"ppt/slides/slide1.xml": _slide(
        _rsp([('<a:rPr b="1" i="1"/>', "alpha"), ('<a:rPr i="1"/>', "beta")]))})
    assert "- ***alpha***<!---->*beta*" in md, md
