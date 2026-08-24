"""
title: Unit — body text round-trips as a SEQUENCE, not merely as a multiset
kind: tests
layer: backend
summary: The office lane must replay source tokens IN ORDER; the gate cannot see order.
"""
# Rubric row A2 (docs/quality-plan.md): "Body text round-trips: markdown -> text
# equals source text AS A SEQUENCE, not only as a multiset".
#
# WHY THIS FILE EXISTS -- the losslessness gate is order-blind by construction.
# backend.validate.conversion_report grades a conversion with
# backend.ingest.coverage, which is multiset token recall
# (src/backend/ingest/_coverage.py):
#
#     src = Counter(tokenize(source_text))
#     tgt = Counter(tokenize(target_text))
#     ...
#     for tok, n in src.items():
#         have = tgt.get(tok, 0)
#         hit = n if have >= n else have
#         covered += hit
#
# Counter() throws the sequence away and keeps only per-token counts, and the
# scoring loop iterates src.items() -- token identity and multiplicity, never
# position. A converter that emitted every word of a runbook in reverse, or that
# interleaved two table columns, therefore scores recall exactly 1.0 and the gate
# says valid=True. The negative tests at the bottom of this file DEMONSTRATE that
# rather than assert it in prose.
#
# THE PROPERTY PINNED HERE
#     tokenize(markdown_to_text(ooxml_markdown(ext, parts)))
#         == tokenize(ooxml_source_text(ext, parts))
# -- the same tokens in the same order, compared as LISTS. The two sides are
# independent walks of the same OOXML parts (the converter's structural walk vs
# the exhaustive converter-blind ground truth), so equality is a real claim about
# the converter, not a tautology.
#
# THE REGISTRY OF DELIBERATE DIVERGENCES
# Strict equality does not hold everywhere, and where it does not the cause is a
# decision somebody made, not a bug. Each one is named here with the function and
# the file:line that performs it (line numbers as of this commit), and each has
# its own pinning test below, so a NEW divergence that nobody decided on still
# fails this file.
#
#   LIFT -- a true REORDERING; the source tokens are not even an ordered
#   subsequence of the markdown tokens:
#     L1  A docx text box (w:txbxContent) is pulled out of the paragraph it is
#         anchored in and emitted as its own block AFTER that paragraph.
#         _docx_p_text diverts it   -- src/backend/ingest/_ooxml_md.py:772-774
#         _docx_blocks emits it     -- src/backend/ingest/_ooxml_md.py:955-956
#         (a box anchored inside a table lands after the whole table:
#          src/backend/ingest/_ooxml_md.py:988-989)
#         The ground truth walks document order, so it sees the box text where it
#         sits mid-paragraph. Equality survives only when the box happens to be
#         the last thing in its paragraph.
#     L2  pptx speaker notes are interleaved per slide (slide 1, its notes,
#         slide 2, its notes), while the ground truth reads all slides first and
#         the notes parts afterwards.
#         pptx_markdown     -- src/backend/ingest/_ooxml_md.py:1333-1338
#         pptx_source_text  -- src/backend/ingest/_ooxml_md.py:1384-1387
#     L3  Embedded docx SmartArt and chart text is GROUPED BY KIND -- every
#         diagram under "## Diagrams", then every chart under "## Charts" --
#         while the ground truth walks sorted(parts), where "word/charts/..."
#         sorts before "word/diagrams/...". A docx carrying both therefore has
#         them in opposite relative order on the two sides.
#         _embedded_sections -- src/backend/ingest/_ooxml_md.py:605-622,
#                               driven from :1060-1062
#         docx_source_text   -- src/backend/ingest/_ooxml_md.py:1079-1083
#
#   INJECTION -- extra tokens the converter ADDS; the source tokens stay in
#   order, so they remain an ordered SUBSEQUENCE of the markdown tokens, and the
#   comparison is defined over that subsequence:
#     I1  Synthetic trailing section headings "## Footnotes" / "## Endnotes" /
#         "## Comments" -- _docx_notes_section, src/.../_ooxml_md.py:1008,
#         called from docx_markdown, src/.../_ooxml_md.py:1054-1059.
#     I2  Synthetic "## Diagrams" / "## Charts" headings -- _embedded_sections,
#         src/.../_ooxml_md.py:618, called from src/.../_ooxml_md.py:1060-1062.
#     I3  A synthetic "## Figures" heading for embedded SVG label text --
#         ooxml_markdown, src/backend/ingest/_ooxml_md.py:1663.
#     I4  A vertically merged (w:vMerge) cell's value is REPEATED down its
#         continuation rows, because GFM has no rowspan --
#         _docx_table_rows_md, src/backend/ingest/_ooxml_md.py:872-874.
#     I5  Paragraphs inside one table cell are joined with a literal "<br>",
#         which markdown_to_text does not strip, so the text layer gains a "br"
#         token -- _docx_cell_text, src/backend/ingest/_ooxml_md.py:836.
#         This one is an ARTIFACT of the text stripper, not a conversion
#         decision; it is pinned so it stays visible, and the pin should be
#         deleted if markdown_to_text learns to drop inline HTML breaks.
#
# Unit test: pure policy on hand-built parts dicts -- no disk, no zipfile, no
# network. Public API only (backend.ingest / backend.validate).
from backend.ingest import (markdown_to_text, ooxml_markdown, ooxml_source_text,
                            tokenize)
from backend.validate import conversion_report

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' + A +
     ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
SVG = 'xmlns="http://www.w3.org/2000/svg"'
DGM = 'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"'

STYLES = ('<w:styles %s>'
          '<w:style w:type="paragraph" w:styleId="Heading1">'
          '<w:name w:val="heading 1"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading2">'
          '<w:name w:val="heading 2"/></w:style>'
          '<w:style w:type="character" w:styleId="CodeChar">'
          '<w:name w:val="Code Char"/></w:style>'
          '</w:styles>' % W)

NUMBERING = ('<w:numbering %s>'
             '<w:abstractNum w:abstractNumId="10">'
             '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
             '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl>'
             '</w:abstractNum>'
             '<w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>'
             '</w:numbering>' % W)


# ------------------------------------------------------------ fixture builders

def _wdoc(body):
    # type: (str) -> str
    return '<w:document %s %s %s><w:body>%s</w:body></w:document>' % (W, MC, R, body)


def _wp(text, style=None, num=None, ilvl=0):
    # type: (str, object, object, int) -> str
    ppr = ""
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    if num is not None:
        ppr += ('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%s"/></w:numPr>'
                % (ilvl, num))
    if ppr:
        ppr = "<w:pPr>%s</w:pPr>" % ppr
    return "<w:p>%s<w:r><w:t>%s</w:t></w:r></w:p>" % (ppr, text)


def _wcell(*paras, **kw):
    # type: (*str, **str) -> str
    """One w:tc; ``pr`` injects raw w:tcPr children (gridSpan, vMerge, ...)."""
    pr = kw.get("pr", "")
    inner = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % t for t in paras)
    return "<w:tc>%s%s</w:tc>" % (("<w:tcPr>%s</w:tcPr>" % pr) if pr else "",
                                  inner or "<w:p/>")


def _wtable(rows):
    # type: (list) -> str
    return "<w:tbl>%s</w:tbl>" % "".join("<w:tr>%s</w:tr>" % "".join(r) for r in rows)


def _sp(text, ph=None):
    # type: (str, object) -> str
    holder = '<p:ph type="%s"/>' % ph if ph else ""
    return ('<p:sp><p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr><p:txBody><a:p><a:r>'
            '<a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>' % (holder, text))


def _slide(shapes, tag="p:sld"):
    # type: (str, str) -> str
    return ('<%s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></%s>'
            % (tag, P, shapes, tag))


# ------------------------------------------------------------- the measurement

def roundtrip_tokens(parts, ext="docx"):
    # type: (dict, str) -> tuple
    """``(source_tokens, markdown_tokens)`` for one hand-built OOXML parts dict.

    ``source_tokens`` is the converter-blind ground truth (``ooxml_source_text``)
    tokenized; ``markdown_tokens`` is the converter's markdown (``ooxml_markdown``)
    pushed back through ``markdown_to_text`` -- the exact text layer the recall
    gate scores -- and tokenized with the exact tokenizer the gate uses. Both are
    LISTS, in document order: comparing them is the sequence property, comparing
    their Counters would be the (order-blind) gate."""
    md_tokens = tokenize(markdown_to_text(ooxml_markdown(ext, parts)))
    src_tokens = tokenize(ooxml_source_text(ext, parts))
    return src_tokens, md_tokens


def assert_sequence_round_trip(parts, ext="docx"):
    # type: (dict, str) -> list
    """The strict property: the two token LISTS are equal, element for element.

    Also asserts the multiset gate agrees (recall 1.0), so a test that passes here
    is pinning ORDER on top of a conversion that is already lossless -- never
    instead of it. Returns the token list."""
    src_tokens, md_tokens = roundtrip_tokens(parts, ext)
    rep = conversion_report(ooxml_source_text(ext, parts), ooxml_markdown(ext, parts))
    assert rep["recall"] == 1.0 and rep["valid"] is True, rep
    assert md_tokens == src_tokens, _diff(src_tokens, md_tokens)
    return md_tokens


def splice(src_tokens, md_tokens):
    # type: (list, list) -> tuple
    """``(is_ordered_subsequence, injected)`` for the source inside the markdown.

    Greedy leftmost matching, which is exact for subsequence existence: walk the
    markdown tokens, consuming source tokens in order; anything the walk could not
    consume the source with is ``injected`` -- the tokens the converter ADDED
    (synthetic section headings, a repeated merge cell). When the flag is False the
    source is not even an ordered subsequence, i.e. content was genuinely MOVED,
    and ``injected`` is meaningless."""
    i = 0
    injected = []  # type: list
    for tok in md_tokens:
        if i < len(src_tokens) and src_tokens[i] == tok:
            i += 1
        else:
            injected.append(tok)
    return i == len(src_tokens), injected


def assert_injections_only(parts, expected_injected, ext="docx"):
    # type: (dict, list, str) -> None
    """The relaxed property for INJECTION rows of the registry: the source tokens
    survive as an ordered SUBSEQUENCE -- nothing was moved -- and the tokens the
    converter added are exactly ``expected_injected``, in order. Naming them is
    what keeps this from being a fudge: an unlisted addition fails."""
    src_tokens, md_tokens = roundtrip_tokens(parts, ext)
    ok, injected = splice(src_tokens, md_tokens)
    assert ok, ("source tokens are not an ordered subsequence of the markdown "
                "tokens -- content MOVED, not merely got added\n"
                + _diff(src_tokens, md_tokens))
    assert injected == expected_injected, _diff(expected_injected, injected)


def _diff(expected, got):
    # type: (list, list) -> str
    """First divergence plus both sequences -- pytest's list diff on 40+ short
    tokens is unreadable, and the INDEX is the whole point of this file."""
    at = len(expected)
    for i, pair in enumerate(zip(expected, got)):
        if pair[0] != pair[1]:
            at = i
            break
    return ("sequences diverge at index %d (expected %r, got %r)\nexpected: %r\n"
            "     got: %r" % (at,
                              expected[at] if at < len(expected) else None,
                              got[at] if at < len(got) else None,
                              expected, got))


# ------------------------------------------- the property holds: body constructs

def test_prose_and_headings_round_trip_in_order():
    parts = {"word/document.xml": _wdoc(
        _wp("Overview", style="Heading1")
        + _wp("The block has three clocks.")
        + _wp("Clocking", style="Heading2")
        + _wp("Each clock is independently gated.")),
        "word/styles.xml": STYLES}
    assert assert_sequence_round_trip(parts) == [
        "overview", "the", "block", "has", "three", "clocks", "clocking",
        "each", "clock", "is", "independently", "gated"]


def test_nested_lists_round_trip_in_order():
    # Depth must not move an item: a runbook whose sub-step floats to the end of
    # the procedure still scores recall 1.0.
    parts = {"word/document.xml": _wdoc(
        _wp("acknowledge the page", num="1")
        + _wp("check pod health", num="1")
        + _wp("inspect the gateway log", num="1", ilvl=1)
        + _wp("drain the unhealthy pod", num="1")),
        "word/numbering.xml": NUMBERING}
    assert assert_sequence_round_trip(parts) == [
        "acknowledge", "the", "page", "check", "pod", "health", "inspect",
        "the", "gateway", "log", "drain", "the", "unhealthy", "pod"]


def test_table_round_trips_row_major():
    # The interleaving case the multiset gate cannot see: emitting this table
    # column-major keeps every token and scores 1.0 (proved below in
    # test_transposing_a_table_passes_the_multiset_gate_but_fails_the_sequence).
    parts = {"word/document.xml": _wdoc(_wtable([
        [_wcell("Signal"), _wcell("Width"), _wcell("Direction")],
        [_wcell("irq out"), _wcell("32"), _wcell("output")],
        [_wcell("clk ref"), _wcell("1"), _wcell("input")]]))}
    assert assert_sequence_round_trip(parts) == [
        "signal", "width", "direction",
        "irq", "out", "32", "output",
        "clk", "ref", "1", "input"]


def test_hyperlink_round_trips_with_its_url_dropped_not_reordered():
    rels = ('<Relationships %s><Relationship Id="rId9" '
            'Target="https://example.com/spec" TargetMode="External"/>'
            '</Relationships>' % RELS)
    parts = {"word/document.xml": _wdoc(
        '<w:p><w:r><w:t>See </w:t></w:r>'
        '<w:hyperlink r:id="rId9"><w:r><w:t>the spec</w:t></w:r></w:hyperlink>'
        '<w:r><w:t> for wiring.</w:t></w:r></w:p>'),
        "word/_rels/document.xml.rels": rels}
    # markdown_to_text resolves [text](url) to its visible text, so the URL
    # contributes no tokens on either side and the link stays in place.
    assert assert_sequence_round_trip(parts) == [
        "see", "the", "spec", "for", "wiring"]


def test_bold_italic_and_code_runs_round_trip_in_order():
    runs = ('<w:p><w:r><w:t>The </w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>reset</w:t></w:r>'
            '<w:r><w:t> line is </w:t></w:r>'
            '<w:r><w:rPr><w:i/></w:rPr><w:t>active low</w:t></w:r>'
            '<w:r><w:t> driven by </w:t></w:r>'
            '<w:r><w:rPr><w:rStyle w:val="CodeChar"/></w:rPr>'
            '<w:t>reset_n</w:t></w:r>'
            '<w:r><w:t> at boot.</w:t></w:r></w:p>')
    parts = {"word/document.xml": _wdoc(runs), "word/styles.xml": STYLES}
    assert assert_sequence_round_trip(parts) == [
        "the", "reset", "line", "is", "active", "low", "driven", "by",
        "reset", "n", "at", "boot"]


def test_fenced_code_paragraphs_round_trip_in_order():
    # _join_blocks fuses consecutive code paragraphs into ONE fence; the fusion
    # must not reorder the transcript.
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Code">'
              '<w:name w:val="HTML Preformatted"/></w:style></w:styles>' % W)
    code = ('<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:r><w:t>%s</w:t>'
            '</w:r></w:p>')
    parts = {"word/document.xml": _wdoc(
        _wp("Run this:") + code % "kubectl drain node" + code % "kubectl get pods"
        + _wp("Then wait.")),
        "word/styles.xml": styles}
    assert assert_sequence_round_trip(parts) == [
        "run", "this", "kubectl", "drain", "node", "kubectl", "get", "pods",
        "then", "wait"]


def test_a_text_box_at_the_end_of_its_paragraph_round_trips_in_order():
    # The lift (registry L1) is a no-op here because the box is already the last
    # thing in the paragraph -- the interesting case is the next test.
    parts = {"word/document.xml": _wdoc(
        '<w:p><w:r><w:t>Anchor paragraph.</w:t></w:r>'
        '<w:r><w:txbxContent><w:p><w:r><w:t>Boxed callout.</w:t></w:r></w:p>'
        '</w:txbxContent></w:r></w:p>' + _wp("Following paragraph."))}
    assert assert_sequence_round_trip(parts) == [
        "anchor", "paragraph", "boxed", "callout", "following", "paragraph"]


def test_xlsx_sheet_round_trips_in_order():
    # The office lane is not only docx; a spreadsheet's sheet name and cells must
    # replay in workbook/row-major order too.
    s = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    wb = ('<workbook %s %s><sheets><sheet name="Ports" sheetId="1" r:id="rId1"/>'
          '</sheets></workbook>' % (s, R))
    wbrels = ('<Relationships %s><Relationship Id="rId1" '
              'Target="worksheets/sheet1.xml"/></Relationships>' % RELS)
    sheet = ('<worksheet %s><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Signal</t></is></c>'
             '<c r="B1" t="inlineStr"><is><t>Width</t></is></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>irq out</t></is></c>'
             '<c r="B2"><v>32</v></c></row></sheetData></worksheet>' % s)
    parts = {"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": wbrels,
             "xl/worksheets/sheet1.xml": sheet}
    assert assert_sequence_round_trip(parts, "xlsx") == [
        "ports", "signal", "width", "irq", "out", "32"]


# ------------------------------------- registry L1/L2: the deliberate REORDERINGS

def test_L1_a_text_box_is_lifted_out_of_its_anchoring_paragraph():
    # _docx_p_text (src/backend/ingest/_ooxml_md.py:772-774) diverts w:txbxContent
    # into `boxes`; _docx_blocks (:955-956) emits it as its own block AFTER the
    # paragraph. The ground-truth walk sees it mid-paragraph, where it sits in the
    # XML, so the sequences legitimately differ. Pinned exactly, not waived.
    parts = {"word/document.xml": _wdoc(
        '<w:p><w:r><w:t>Anchor start.</w:t></w:r>'
        '<w:r><w:txbxContent><w:p><w:r><w:t>Boxed callout.</w:t></w:r></w:p>'
        '</w:txbxContent></w:r>'
        '<w:r><w:t> Anchor end.</w:t></w:r></w:p>' + _wp("Following paragraph."))}
    src_tokens, md_tokens = roundtrip_tokens(parts)
    assert src_tokens == ["anchor", "start", "boxed", "callout", "anchor", "end",
                          "following", "paragraph"]
    assert md_tokens == ["anchor", "start", "anchor", "end", "boxed", "callout",
                         "following", "paragraph"], _diff(src_tokens, md_tokens)
    # It really is a MOVE, not an addition: the source is not even an ordered
    # subsequence of the markdown, so the relaxed comparison rejects it too.
    assert splice(src_tokens, md_tokens)[0] is False
    # ...and the multiset gate is happy either way. That is the whole problem.
    rep = conversion_report(ooxml_source_text("docx", parts),
                            ooxml_markdown("docx", parts))
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_L1_a_text_box_anchored_in_a_table_lands_after_the_whole_table():
    # Same lift, wider throw: _docx_blocks (src/.../_ooxml_md.py:988-989) emits a
    # box anchored inside a cell after the entire table, never inside a pipe row.
    tbl = _wtable([['<w:tc><w:p><w:r><w:t>Cell one</w:t></w:r>'
                    '<w:r><w:txbxContent><w:p><w:r><w:t>boxed in cell</w:t>'
                    '</w:r></w:p></w:txbxContent></w:r></w:p></w:tc>',
                    _wcell("Cell two")]])
    parts = {"word/document.xml": _wdoc(_wp("Before.") + tbl + _wp("After."))}
    src_tokens, md_tokens = roundtrip_tokens(parts)
    assert src_tokens == ["before", "cell", "one", "boxed", "in", "cell",
                          "cell", "two", "after"]
    assert md_tokens == ["before", "cell", "one", "cell", "two",
                         "boxed", "in", "cell", "after"], _diff(src_tokens, md_tokens)


def test_L3_diagram_and_chart_text_are_grouped_by_kind_not_by_part_name():
    # Found by this file, not assumed: a docx carrying BOTH a SmartArt diagram and
    # a chart emits them in the opposite relative order on the two sides.
    #   converter    -- _embedded_sections (src/.../_ooxml_md.py:605-622), driven by
    #                   the fixed (Diagrams, Charts) pattern list at :1060-1062, so
    #                   it groups BY KIND: every diagram, then every chart.
    #   ground truth -- docx_source_text (src/.../_ooxml_md.py:1079-1083) walks
    #                   sorted(parts), and "word/charts/..." sorts before
    #                   "word/diagrams/...", so it emits charts first.
    # Systematic, not incidental: 'c' < 'd' for every part name either regex can
    # match. Grouping by kind is the deliberate half; the ground truth's ordering
    # is an artifact of the sort. Pinned so the swap cannot silently grow.
    parts = {"word/document.xml": _wdoc(_wp("Body prose here.")),
             "word/diagrams/data1.xml": _DGM, "word/charts/chart1.xml": _CHART}
    src_tokens, md_tokens = roundtrip_tokens(parts)
    assert src_tokens == ["body", "prose", "here", "latency", "42", "node", "one"]
    assert md_tokens == ["body", "prose", "here", "diagrams", "node", "one",
                         "charts", "latency", "42"], _diff(src_tokens, md_tokens)
    assert splice(src_tokens, md_tokens)[0] is False         # a MOVE, not an addition
    rep = conversion_report(ooxml_source_text("docx", parts),
                            ooxml_markdown("docx", parts))
    assert rep["valid"] is True and rep["recall"] == 1.0


def test_L2_pptx_interleaves_speaker_notes_per_slide():
    # pptx_markdown (src/.../_ooxml_md.py:1333-1338) emits each slide's notes right
    # after that slide; pptx_source_text (:1384-1387) reads every slide first and
    # the notes parts afterwards. Deliberate on the converter's side -- a deck reads
    # slide-then-notes -- so the ground truth, not the markdown, is the odd one out.
    rels = ('<Relationships %s><Relationship Id="rId1" '
            'Target="../notesSlides/notesSlide%%d.xml"/></Relationships>' % RELS)
    parts = {
        "ppt/slides/slide1.xml": _slide(_sp("Alpha title", ph="title")
                                        + _sp("alpha body")),
        "ppt/slides/slide2.xml": _slide(_sp("Beta title", ph="title")
                                        + _sp("beta body")),
        "ppt/slides/_rels/slide1.xml.rels": rels % 1,
        "ppt/slides/_rels/slide2.xml.rels": rels % 2,
        "ppt/notesSlides/notesSlide1.xml": _slide(_sp("note one"), tag="p:notes"),
        "ppt/notesSlides/notesSlide2.xml": _slide(_sp("note two"), tag="p:notes"),
    }
    src_tokens, md_tokens = roundtrip_tokens(parts, "pptx")
    assert src_tokens == ["alpha", "title", "alpha", "body",
                          "beta", "title", "beta", "body",
                          "note", "one", "note", "two"]
    assert md_tokens == ["slide", "1", "alpha", "title", "alpha", "body",
                         "speaker", "notes", "note", "one",
                         "slide", "2", "beta", "title", "beta", "body",
                         "speaker", "notes", "note", "two"], _diff(src_tokens,
                                                                   md_tokens)
    assert splice(src_tokens, md_tokens)[0] is False        # a MOVE, not an addition


# ------------------------------------ registry I1-I5: the deliberate INJECTIONS

def test_I1_trailing_note_sections_inject_only_their_heading_tokens():
    # The note CONTENT does not move: docx_source_text reads document, footnotes,
    # endnotes, comments in that order (src/.../_ooxml_md.py:1074-1078) and
    # docx_markdown appends the sections in the same order (:1054-1059). Only the
    # synthetic "## <title>" heading word (_docx_notes_section, :1008) is new.
    foot = ('<w:footnotes %s><w:footnote w:type="separator" w:id="0"><w:p/>'
            '</w:footnote><w:footnote w:id="1"><w:p><w:r><w:t>foot alpha</w:t>'
            '</w:r></w:p></w:footnote></w:footnotes>' % W)
    end = ('<w:endnotes %s><w:endnote w:id="1"><w:p><w:r><w:t>end beta</w:t>'
           '</w:r></w:p></w:endnote></w:endnotes>' % W)
    com = ('<w:comments %s><w:comment w:id="1"><w:p><w:r><w:t>comment gamma</w:t>'
           '</w:r></w:p></w:comment></w:comments>' % W)
    parts = {"word/document.xml": _wdoc(_wp("Body prose here.")),
             "word/footnotes.xml": foot, "word/endnotes.xml": end,
             "word/comments.xml": com}
    assert_injections_only(parts, ["footnotes", "endnotes", "comments"])
    src_tokens, md_tokens = roundtrip_tokens(parts)
    assert md_tokens == ["body", "prose", "here",
                         "footnotes", "foot", "alpha",
                         "endnotes", "end", "beta",
                         "comments", "comment", "gamma"], _diff(src_tokens, md_tokens)


_DGM = ('<dgm:dataModel %s %s><dgm:ptLst><dgm:pt><dgm:t><a:p><a:r>'
        '<a:t>Node one</a:t></a:r></a:p></dgm:t></dgm:pt></dgm:ptLst>'
        '</dgm:dataModel>' % (DGM, A))
_CHART = ('<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/'
          'drawingml/2006/chart" %s><c:title><a:t>Latency</a:t></c:title>'
          '<c:ser><c:v>42</c:v></c:ser></c:chartSpace>' % A)


def test_I2_an_embedded_diagram_section_injects_only_its_heading():
    parts = {"word/document.xml": _wdoc(_wp("Body prose here.")),
             "word/diagrams/data1.xml": _DGM}
    assert_injections_only(parts, ["diagrams"])


def test_I2_an_embedded_chart_section_injects_only_its_heading():
    parts = {"word/document.xml": _wdoc(_wp("Body prose here.")),
             "word/charts/chart1.xml": _CHART}
    assert_injections_only(parts, ["charts"])


def test_I3_the_svg_figures_section_injects_only_its_heading_token():
    svg = '<svg %s><text>Clock domain</text><text>Reset tree</text></svg>' % SVG
    parts = {"word/document.xml": _wdoc(_wp("Body prose here.")),
             "word/media/diagram1.svg": svg}
    assert_injections_only(parts, ["figures"])


def test_I4_a_vertical_merge_repeats_the_restart_cell_down_its_rows():
    # GFM has no rowspan, so _docx_table_rows_md (src/.../_ooxml_md.py:872-874)
    # forward-fills the continuation rows. That ADDS an occurrence the source
    # lacks; it must not MOVE anything.
    tbl = _wtable([
        [_wcell("Domain"), _wcell("Signal")],
        [_wcell("core", pr='<w:vMerge w:val="restart"/>'), _wcell("clk a")],
        [_wcell(pr='<w:vMerge/>'), _wcell("clk b")]])
    parts = {"word/document.xml": _wdoc(tbl)}
    assert_injections_only(parts, ["core"])


def test_a_multi_paragraph_cell_injects_nothing_at_all():
    # This row USED to be registry entry I5: _docx_cell_text joins a cell's
    # paragraphs with "<br>", and markdown_to_text stripped no inline HTML, so the
    # text layer gained a literal "br" token for every multi-paragraph cell. That
    # was an artifact of the stripper, not a conversion decision -- free to the
    # recall gate (which is recall, and forgives extra target tokens) but landing
    # in the body an embedder and a BM25 index actually read. The stripper now
    # drops the joiner, so the correct assertion is the STRONGER one: the cell
    # round-trips with nothing added.
    parts = {"word/document.xml": _wdoc(_wtable([
        [_wcell("line one", "line two"), _wcell("other")]]))}
    assert_injections_only(parts, [])
    assert_sequence_round_trip(parts)


def test_a_converter_escaped_br_is_prose_not_a_joiner():
    # The other side of the strip: a document that TALKS about <br> keeps it.
    from backend.ingest import markdown_to_text
    assert markdown_to_text("the \\<br> tag") == "the <br> tag"


# ---------------------------------------------------- the property has teeth

def _body_of(parts):
    # type: (dict) -> tuple
    """``(markdown, source_text)`` for a parts dict, without the token step."""
    return ooxml_markdown("docx", parts), ooxml_source_text("docx", parts)


SWAP_PARTS = {"word/document.xml": _wdoc(
    _wp("Runbook", style="Heading1")
    + _wp("First step is to drain the node.")
    + _wp("Second step is to reboot the node.")
    + _wp("Third step is to verify the node.")),
    "word/styles.xml": STYLES}


def test_swapping_two_body_paragraphs_passes_the_multiset_gate_but_fails_the_sequence():
    # THE POINT OF THIS FILE. Take a conversion that is correct, then move two
    # paragraphs past each other in the markdown -- no token added, none removed.
    md, src = _body_of(SWAP_PARTS)
    assert_sequence_round_trip(SWAP_PARTS)                  # the baseline is in order
    swapped = md.replace(
        "First step is to drain the node.\n\nSecond step is to reboot the node.",
        "Second step is to reboot the node.\n\nFirst step is to drain the node.")
    assert swapped != md
    # The gate: still perfect. An operator following this runbook reboots before
    # draining, and nothing in report.json objects.
    rep = conversion_report(src, swapped)
    assert rep["valid"] is True
    assert rep["recall"] == 1.0
    assert rep["n_missing"] == 0
    # The sequence check: fails, which is what makes it a measurement.
    src_tokens = tokenize(src)
    swapped_tokens = tokenize(markdown_to_text(swapped))
    assert sorted(swapped_tokens) == sorted(src_tokens)     # same multiset, exactly
    assert swapped_tokens != src_tokens
    # and the relaxed subsequence comparison has teeth here too, so a test that
    # can only use that form (an INJECTION row) is still measuring something.
    assert splice(src_tokens, swapped_tokens)[0] is False


def test_reversing_the_whole_body_passes_the_multiset_gate_but_fails_the_sequence():
    # The extreme the module comment names: every word of the runbook, backwards.
    md, src = _body_of(SWAP_PARTS)
    reversed_md = " ".join(reversed(markdown_to_text(md).split()))
    rep = conversion_report(src, reversed_md)
    assert rep["valid"] is True and rep["recall"] == 1.0
    src_tokens = tokenize(src)
    rev_tokens = tokenize(reversed_md)
    assert sorted(rev_tokens) == sorted(src_tokens)
    assert rev_tokens == list(reversed(src_tokens))
    assert rev_tokens != src_tokens


def test_transposing_a_table_passes_the_multiset_gate_but_fails_the_sequence():
    # The other order-blind failure the module comment names: two table columns
    # interleaved. Every cell survives, so recall stays 1.0 while every row of the
    # rendered table now pairs the wrong signal with the wrong width.
    parts = {"word/document.xml": _wdoc(_wtable([
        [_wcell("Signal"), _wcell("Width")],
        [_wcell("irq out"), _wcell("32")],
        [_wcell("clk ref"), _wcell("1")]]))}
    assert_sequence_round_trip(parts)                       # the baseline is row-major
    md, src = _body_of(parts)
    transposed = ("| Signal | irq out | clk ref |\n"
                  "| --- | --- | --- |\n"
                  "| Width | 32 | 1 |\n")
    rep = conversion_report(src, transposed)
    assert rep["valid"] is True and rep["recall"] == 1.0
    src_tokens = tokenize(src)
    trans_tokens = tokenize(markdown_to_text(transposed))
    assert sorted(trans_tokens) == sorted(src_tokens)
    assert trans_tokens != src_tokens
    assert splice(src_tokens, trans_tokens)[0] is False


# ------------------------------------- the guard: no UNDECIDED divergence sneaks in

def test_a_document_using_every_construct_diverges_only_where_the_registry_says():
    # One document exercising prose, a heading, a nested ordered list, a table, a
    # hyperlink, formatted runs, a lifted text box and a footnote section. Its
    # expected markdown sequence is CONSTRUCTED from the ground truth by applying
    # the two registered divergences that apply -- L1 (text-box lift) and I1 (the
    # "## Footnotes" heading) -- and nothing else. A third divergence, from any
    # future converter change nobody wrote down, fails right here.
    rels = ('<Relationships %s><Relationship Id="rId9" '
            'Target="https://example.com/spec" TargetMode="External"/>'
            '</Relationships>' % RELS)
    foot = ('<w:footnotes %s><w:footnote w:id="1"><w:p><w:r>'
            '<w:t>Per ISO 26262 clause five.</w:t></w:r></w:p></w:footnote>'
            '</w:footnotes>' % W)
    body = (_wp("Runbook", style="Heading1")
            + _wp("Drain the node before maintenance.")
            + _wp("acknowledge the page", num="1")
            + _wp("inspect the gateway log", num="1", ilvl=1)
            + _wtable([[_wcell("Signal"), _wcell("Width")],
                       [_wcell("irq out"), _wcell("32")]])
            + '<w:p><w:r><w:t>See </w:t></w:r>'
              '<w:hyperlink r:id="rId9"><w:r><w:t>the spec</w:t></w:r>'
              '</w:hyperlink><w:r><w:t> for wiring.</w:t></w:r></w:p>'
            + '<w:p><w:r><w:t>The </w:t></w:r>'
              '<w:r><w:rPr><w:b/></w:rPr><w:t>reset</w:t></w:r>'
              '<w:r><w:t> line is </w:t></w:r>'
              '<w:r><w:rPr><w:i/></w:rPr><w:t>active low</w:t></w:r>'
              '<w:r><w:t> driven by </w:t></w:r>'
              '<w:r><w:rPr><w:rStyle w:val="CodeChar"/></w:rPr>'
              '<w:t>reset_n</w:t></w:r><w:r><w:t> at boot.</w:t></w:r></w:p>'
            + '<w:p><w:r><w:t>Anchor start.</w:t></w:r>'
              '<w:r><w:txbxContent><w:p><w:r><w:t>Boxed callout.</w:t></w:r>'
              '</w:p></w:txbxContent></w:r>'
              '<w:r><w:t> Anchor end.</w:t></w:r></w:p>'
            + _wp("Closing prose."))
    parts = {"word/document.xml": _wdoc(body), "word/styles.xml": STYLES,
             "word/numbering.xml": NUMBERING,
             "word/_rels/document.xml.rels": rels, "word/footnotes.xml": foot}
    src_tokens, md_tokens = roundtrip_tokens(parts)
    rep = conversion_report(ooxml_source_text("docx", parts),
                            ooxml_markdown("docx", parts))
    assert rep["valid"] is True and rep["recall"] == 1.0

    expected = list(src_tokens)
    # L1: the text box moves from its anchor to just after the paragraph.
    box_at = expected.index("boxed")
    lifted = expected[box_at:box_at + 2]                    # ["boxed", "callout"]
    del expected[box_at:box_at + 2]
    expected[expected.index("end") + 1:expected.index("end") + 1] = lifted
    # I1: the synthetic "## Footnotes" heading opens the trailing note section.
    expected.insert(expected.index("per"), "footnotes")

    assert md_tokens == expected, _diff(expected, md_tokens)
    # and, stated once concretely, so the construction above cannot quietly
    # rewrite itself into agreement with a broken converter:
    assert md_tokens == [
        "runbook", "drain", "the", "node", "before", "maintenance",
        "acknowledge", "the", "page", "inspect", "the", "gateway", "log",
        "signal", "width", "irq", "out", "32",
        "see", "the", "spec", "for", "wiring",
        "the", "reset", "line", "is", "active", "low", "driven", "by",
        "reset", "n", "at", "boot",
        "anchor", "start", "anchor", "end", "boxed", "callout",
        "closing", "prose",
        "footnotes", "per", "iso", "26262", "clause", "five"]
