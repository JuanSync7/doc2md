"""
title: Unit — the converter-blind xlsx token ground truth
kind: tests
layer: backend
summary: xlsx_source_text resolves every cell by its own typed reader over every worksheet part, so a bug in the converter's reader can no longer cancel against it.
"""
# WHY THIS EXISTS. This is the module the measurement pointed at. `xlsx_source_text`
# used to call `_cell_value` — the SAME function `xlsx_markdown` asks what a cell
# says — so a bug in it was applied to both halves of the comparison and the gate
# reported a clean pass over real damage. Measured on the shipped
# `kestrel-registers.xlsx` before the split:
#
#     bug _cell_value (both sides)      -> recall 1.0, n_source 107 -> 73, valid True
#     bug _shared_strings (both sides)  -> recall 1.0, n_source 107 -> 106, valid True
#
# The old code's own comment claimed reading worksheet parts directly stopped a bug
# "zeroing both sides". That was true about GEOMETRY and false about VALUES.
import pytest

from backend.ingest import (ooxml_markdown, xlsx_policy_drops,
                            xlsx_source_structure, xlsx_source_text)
from backend.validate import md_structure, structure_fidelity_report

pytestmark = pytest.mark.unit

SS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _wb(*names):
    sheets = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
                     % (n, i + 1, i + 1) for i, n in enumerate(names))
    return {"xl/workbook.xml":
            '<workbook %s xmlns:r="urn:r"><sheets>%s</sheets></workbook>' % (SS, sheets)}


def _sheet(rows, part="xl/worksheets/sheet1.xml"):
    body = "".join('<row r="%d">%s</row>' % (i + 1, r) for i, r in enumerate(rows))
    return {part: '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (SS, body)}


def _c(ref, value, t=None):
    at = ' t="%s"' % t if t else ""
    return '<c r="%s"%s>%s</c>' % (ref, at, value)


# ------------------------------------------------------- typed cell resolution

def test_a_shared_string_is_resolved_to_its_text_not_its_index():
    """The whole point of a typed resolver: `<v>0</v>` under `t="s"` is an INDEX.
    Counting the digit would put a token in the denominator the workbook does not
    contain, and lose the one it does."""
    parts = _sheet([_c("A1", "<v>0</v>", "s")])
    parts["xl/sharedStrings.xml"] = ('<sst %s><si><t>ClkGateCtrl</t></si></sst>' % SS)
    text = xlsx_source_text(parts)
    assert "ClkGateCtrl" in text and text.strip() != "0"


def test_a_rich_shared_string_concatenates_its_runs_with_no_space():
    parts = _sheet([_c("A1", "<v>0</v>", "s")])
    parts["xl/sharedStrings.xml"] = (
        '<sst %s><si><r><t>Xbar</t></r><r><t>Route</t></r></si></sst>' % SS)
    assert "XbarRoute" in xlsx_source_text(parts)


def test_a_phonetic_guide_is_not_counted_as_content():
    """rPh is a furigana reading OF the base text. Counting it duplicates every
    guided word in the denominator, and no faithful conversion emits it twice."""
    parts = _sheet([_c("A1", "<v>0</v>", "s")])
    parts["xl/sharedStrings.xml"] = (
        '<sst %s><si><t>Kestrel</t><rPh sb="0" eb="3"><t>KES</t></rPh></si></sst>' % SS)
    text = xlsx_source_text(parts)
    assert "Kestrel" in text and "KES" not in text


@pytest.mark.parametrize("raw,t,want", [
    ("<v>1</v>", "b", "TRUE"),
    ("<v>0</v>", "b", "FALSE"),
    ("<is><t>inline text</t></is>", "inlineStr", "inline text"),
    ("<v>32</v>", None, "32"),
    ("<v>0.125</v>", "n", "0.125"),
    ("<v>#REF!</v>", "e", "#REF!"),
    ("<v>computed</v>", "str", "computed"),
])
def test_every_declared_cell_type_resolves_to_what_the_reader_sees(raw, t, want):
    assert want in xlsx_source_text(_sheet([_c("A1", raw, t)]))


def test_a_formula_contributes_its_cached_result_and_not_its_expression():
    """The converter publishes the cached RESULT. Putting the EXPRESSION in the
    denominator would make every workbook holding a formula fail a gate it should
    pass — tokens no faithful conversion could ever produce. That the expression is
    lost is real and belongs to a counted warning, not to this side."""
    parts = _sheet([_c("A1", "<f>SUM(C3:C5)</f><v>412.5</v>")])
    text = xlsx_source_text(parts)
    assert "412.5" in text
    assert "SUM" not in text


def test_an_out_of_range_shared_string_index_is_empty_not_an_exception():
    parts = _sheet([_c("A1", "<v>99</v>", "s"), _c("B1", "<v>kept</v>", "str")])
    parts["xl/sharedStrings.xml"] = '<sst %s><si><t>only</t></si></sst>' % SS
    assert "kept" in xlsx_source_text(parts)


# ------------------------------------------------------- what counts as content

def test_a_tab_name_is_body_text():
    """The converter renders a tab name as the section heading for its grid, so it
    is content and belongs in the denominator."""
    parts = _wb("RegisterMap", "PowerBudget")
    parts.update(_sheet([_c("A1", "<v>1</v>")]))
    text = xlsx_source_text(parts)
    assert "RegisterMap" in text and "PowerBudget" in text


def test_a_sheet_element_outside_the_sheets_list_is_not_a_tab():
    """A tab is declared as a child of <sheets>. Reading any `sheet` element
    anywhere in the part would invent tab names out of unrelated markup."""
    parts = {"xl/workbook.xml":
             '<workbook %s xmlns:r="urn:r"><sheets>'
             '<sheet name="Real" sheetId="1" r:id="rId1"/></sheets>'
             '<definedNames><sheet name="NotATab"/></definedNames></workbook>' % SS}
    text = xlsx_source_text(parts)
    assert "Real" in text and "NotATab" not in text


def test_cells_are_read_from_every_worksheet_part_not_through_the_rels():
    """Deliberate asymmetry with the converter, which resolves parts through the
    workbook's relationship graph. A rels-resolution bug on that side must show up
    as recall < 1.0 rather than removing the same content from both halves — so a
    sheet the rels cannot reach is still in the denominator."""
    parts = _wb("Listed")
    parts.update(_sheet([_c("A1", "<v>listed value</v>", "str")]))
    parts.update(_sheet([_c("A1", "<v>orphan value</v>", "str")],
                        part="xl/worksheets/sheet9.xml"))
    text = xlsx_source_text(parts)
    assert "listed value" in text and "orphan value" in text


def test_text_boxes_comments_and_chart_text_all_count():
    parts = _sheet([_c("A1", "<v>grid</v>", "str")])
    parts["xl/drawings/drawing1.xml"] = (
        '<xdr:wsDr xmlns:xdr="x" xmlns:a="a"><a:p><a:r><a:t>floating note</a:t>'
        '</a:r></a:p></xdr:wsDr>')
    parts["xl/comments1.xml"] = (
        '<comments %s><commentList><comment ref="A1"><text><t>check this</t>'
        '</text></comment></commentList></comments>' % SS)
    parts["xl/charts/chart1.xml"] = (
        '<c:chart xmlns:c="c" xmlns:a="a"><a:t>Power</a:t><c:v>182.5</c:v></c:chart>')
    text = xlsx_source_text(parts)
    for want in ("grid", "floating note", "check this", "Power", "182.5"):
        assert want in text, want


def test_an_empty_cell_contributes_nothing():
    parts = _sheet([_c("A1", "") + _c("B1", "<v></v>") + _c("C1", "<v>real</v>", "str")])
    assert xlsx_source_text(parts).strip() == "real"


# ------------------------------------------------------- it degrades, not raises

def test_a_malformed_sheet_costs_that_sheet_and_not_the_workbook():
    parts = _sheet([_c("A1", "<v>survives</v>", "str")])
    parts["xl/worksheets/sheet2.xml"] = "<worksheet><unclosed>"
    assert "survives" in xlsx_source_text(parts)


def test_a_workbook_with_nothing_in_it_is_empty_not_an_error():
    assert xlsx_source_text({}) == ""
    assert xlsx_source_text({"xl/workbook.xml": "<broken"}) == ""


# ============================================================ the STRUCTURAL truth
#
# `xlsx_source_structure` predicts the facts a faithful conversion of this workbook
# must exhibit. Every expectation below was MEASURED off the real converter first
# (scratchpad probe, every branch of `xlsx_markdown`) and then written down here, so
# a disagreement is a real defect rather than a guess about what the converter does.
#
# THE RULE THIS MODULE MUST NOT BREAK: every fact is justifiable either as a
# statement about the SOURCE DOCUMENT or as a statement about what MARKDOWN can
# hold. "Because the converter does X" is not admissible — where the source and the
# converter disagree, the disagreement is the gate WORKING.

R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def _book(*tabs):
    """``tabs``: ``(name, rid_or_None)`` in workbook order."""
    xml = "".join('<sheet name="%s" sheetId="%d"%s/>'
                  % (n, i + 1, (' r:id="%s"' % rid) if rid else "")
                  for i, (n, rid) in enumerate(tabs))
    return {"xl/workbook.xml":
            '<workbook %s %s><sheets>%s</sheets></workbook>' % (SS, R, xml)}


def _rels(*pairs):
    x = "".join('<Relationship Id="%s" Type="t" Target="%s"/>' % p for p in pairs)
    return {"xl/_rels/workbook.xml.rels":
            '<Relationships %s>%s</Relationships>' % (REL, x)}


def _grid(part, rows):
    """``rows``: list of ``(ref, inner_xml, t_or_None)`` lists."""
    body = "".join(
        '<row r="%d">%s</row>'
        % (i + 1, "".join('<c r="%s"%s>%s</c>' % (ref, (' t="%s"' % t) if t else "", inner)
                          for ref, inner, t in row))
        for i, row in enumerate(rows))
    return {part: '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (SS, body)}


def _v(x):
    return "<v>%s</v>" % x


def _simple():
    """Two tabs, both carrying a grid — the ordinary case."""
    p = {}
    p.update(_book(("RegisterMap", "rId1"), ("Notes", "rId2")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet2.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml",
                   [[("A1", _v(1), None), ("B1", _v(2), None)],
                    [("A2", _v(3), None), ("B2", _v(4), None)]]))
    p.update(_grid("xl/worksheets/sheet2.xml",
                   [[("A1", "<is><t>note</t></is>", "inlineStr")]]))
    return p


# ------------------------------------------------- headings and their order

def test_every_tab_is_a_heading_in_workbook_order():
    facts = xlsx_source_structure(_simple())
    assert facts["headings"] == {2: 2}
    assert facts["heading_path"] == [(2, ("registermap",)), (2, ("notes",))]


def test_a_tab_whose_part_cannot_be_resolved_still_names_itself():
    """The converter emits the heading before it looks for the grid, so a broken
    relationship costs the TABLE and never the tab's name. A ground truth that
    predicted no heading there would fail a conversion that behaved correctly."""
    p = _simple()
    p.update(_rels(("rId1", "worksheets/sheet1.xml"), ("rId2", "nowhere.xml")))
    facts = xlsx_source_structure(p)
    # ## RegisterMap, ## Notes (no grid), ## Sheet (unlinked): sheet2
    assert facts["headings"] == {2: 3}
    assert [t for _, t in facts["heading_path"]][:2] == [("registermap",), ("notes",)]
    assert facts["block_sequence"] == [
        ("h", 2), ("table", (2, 2)), ("h", 2), ("h", 2), ("table", (1, 1))]


def test_a_tab_with_no_relationship_id_is_a_heading_with_no_grid():
    p = {}
    p.update(_book(("Ghost", None), ("Real", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml", [[("A1", _v(9), None)]]))
    facts = xlsx_source_structure(p)
    assert facts["heading_path"] == [(2, ("ghost",)), (2, ("real",))]
    assert facts["block_sequence"] == [("h", 2), ("h", 2), ("table", (1, 1))]


def test_a_worksheet_the_workbook_never_lists_is_reached_anyway():
    """A sheet part no relationship points at still renders, under a synthesised
    heading — losing the tab's NAME must never cost its CONTENT. The literal is
    compared word for word, so it is part of the fact."""
    p = {}
    p.update(_book(("Listed", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml", [[("A1", _v(1), None)]]))
    p.update(_grid("xl/worksheets/sheet9.xml", [[("A1", _v(2), None)]]))
    facts = xlsx_source_structure(p)
    assert facts["heading_path"] == [
        (2, ("listed",)), (2, ("sheet", "unlinked", "sheet9"))]


# ------------------------------------------------- the grid

def test_a_grid_is_a_table_with_its_geometry_and_every_cell():
    facts = xlsx_source_structure(_simple())
    assert [(t["rows"], t["cols"]) for t in facts["tables"]] == [(2, 2), (1, 1)]
    assert facts["tables"][0]["cells"] == [(("1",), ("2",)), (("3",), ("4",))]


def test_a_sheet_whose_cells_are_all_empty_emits_no_table():
    """`_gfm_table` drops every blank row, so an empty sheet renders as its heading
    and nothing else. Predicting a 0x0 table here would fail a correct conversion."""
    p = {}
    p.update(_book(("Blank", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml", [[("A1", "", None)]]))
    facts = xlsx_source_structure(p)
    assert facts["tables"] == []
    assert facts["block_sequence"] == [("h", 2)]


def test_the_width_comes_from_the_last_column_anything_uses():
    """A value at C1 makes the table three wide even though A1 and B1 are empty,
    and the row below pads out to match. THE ADDRESS IS THE FACT: reading a row's
    width from how many `<c>` elements it has would place every value one column
    left of where the workbook puts it — the `w:gridBefore` scar, in a spreadsheet."""
    p = {}
    p.update(_book(("Sparse", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml",
                   [[("C1", _v(7), None)],
                    [("A2", _v(1), None), ("B2", _v(2), None)]]))
    facts = xlsx_source_structure(p)
    assert [(t["rows"], t["cols"]) for t in facts["tables"]] == [(2, 3)]
    assert facts["tables"][0]["cells"] == [((), (), ("7",)), (("1",), ("2",), ())]


def test_a_blank_row_separates_two_regions():
    """A BLANK ROW IS A SEPARATOR, not a row to skip past. It is how a spreadsheet
    says "these are two different tables", and Excel's own current-region selection
    stops at one.

    Reading the sheet as a single grid published the SECOND region's header line as
    a data row of the first — the shipped fixture rendered `| Corner | Margin |` and
    `| SSG 0.72V 125C | 0.94 |` inside the power budget, so a reader saw a rail
    drawing 0.94 mW. Both gates passed it: every word was present, and this side
    dropped the same blank row, so the error cancelled. Two implementations agreeing
    on a mistake is the exact failure the second gate exists to prevent."""
    p = {}
    p.update(_book(("Gapped", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p.update(_grid("xl/worksheets/sheet1.xml",
                   [[("A1", _v(1), None)], [("A2", "", None)], [("A3", _v(3), None)]]))
    facts = xlsx_source_structure(p)
    assert [(t["rows"], t["cols"]) for t in facts["tables"]] == [(1, 1), (1, 1)]
    assert facts["block_sequence"] == [("h", 2), ("table", (1, 1)), ("table", (1, 1))]


def test_an_empty_row_element_is_seen_even_though_it_holds_no_cells():
    """`<row r="5"/>` with no `<c>` at all is the ordinary way Excel writes the
    separator. A reader that grouped cells by their owning row could not see one —
    it contributed nothing to group — so the split landed on the converter side
    alone and the two implementations disagreed about a document they both read
    correctly. Rows are ENUMERATED here, not inferred from their cells."""
    p = {}
    p.update(_book(("Gapped", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p["xl/worksheets/sheet1.xml"] = (
        '<worksheet %s><sheetData>'
        '<row r="1"><c r="A1"><v>1</v></c></row>'
        '<row r="2"/>'
        '<row r="3"><c r="A3"><v>3</v></c></row>'
        '</sheetData></worksheet>' % SS)
    assert [(t["rows"], t["cols"])
            for t in xlsx_source_structure(p)["tables"]] == [(1, 1), (1, 1)]
    md = ooxml_markdown("xlsx", p)
    assert structure_fidelity_report(md_structure(md),
                                     xlsx_source_structure(p))["gate"] == "pass"
    assert md.count("| --- |") == 2, md


def test_leading_and_trailing_blanks_open_no_region():
    """A run of blank rows is ONE separator, and blanks at either end separate
    nothing — otherwise a sheet with a decorative gap at the top would publish an
    empty table."""
    p = {}
    p.update(_book(("Padded", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p["xl/worksheets/sheet1.xml"] = (
        '<worksheet %s><sheetData>'
        '<row r="1"/><row r="2"/>'
        '<row r="3"><c r="A3"><v>1</v></c></row>'
        '<row r="4"/><row r="5"/>'
        '<row r="6"><c r="A6"><v>2</v></c></row>'
        '<row r="7"/>'
        '</sheetData></worksheet>' % SS)
    assert [(t["rows"], t["cols"])
            for t in xlsx_source_structure(p)["tables"]] == [(1, 1), (1, 1)]


# ------------------------------------------------- the satellite sections

def test_a_cell_comment_is_a_bullet_and_is_graded_as_one():
    """`_embedded_sections` renders comments as `- item` lines, which a renderer
    reads as a LIST. Stating `list_items: 0` for every workbook — as an earlier
    design did — would have let a dropped comment pass the gate."""
    p = _simple()
    p["xl/comments1.xml"] = (
        '<comments %s><commentList>'
        '<comment ref="A1"><text><t>first note</t></text></comment>'
        '<comment ref="B2"><text><t>second note</t></text></comment>'
        '</commentList></comments>' % SS)
    facts = xlsx_source_structure(p)
    assert facts["list_items"] == {0: 2}
    assert facts["bullet_items"] == 2
    assert facts["list_item_words"] == [("first", "note"), ("second", "note")]
    assert facts["block_sequence"][-3:] == [("h", 2), ("li", 0), ("li", 0)]


def test_text_boxes_and_charts_are_headings_over_prose_not_over_lists():
    """Both render as a paragraph, and a paragraph is not a graded block: only the
    heading they sit under is."""
    p = _simple()
    p["xl/drawings/drawing1.xml"] = (
        '<xdr:wsDr xmlns:xdr="x" xmlns:a="a"><a:p><a:r><a:t>floating label</a:t>'
        '</a:r></a:p></xdr:wsDr>')
    p["xl/charts/chart1.xml"] = (
        '<c:chart xmlns:c="c" xmlns:a="a"><a:t>Power</a:t><c:v>182.5</c:v></c:chart>')
    facts = xlsx_source_structure(p)
    assert [t for _, t in facts["heading_path"]][-2:] == [("text", "boxes"), ("charts",)]
    assert facts["list_items"] == {}
    assert facts["block_sequence"][-2:] == [("h", 2), ("h", 2)]


def test_a_textless_chart_or_drawing_is_no_section_at_all():
    """`_embedded_sections` emits the heading only when some part of that kind
    carries text. A heading per PART was wrong four ways for docx; the same trap
    is here."""
    p = _simple()
    p["xl/charts/chart1.xml"] = '<c:chart xmlns:c="c"><c:ser/></c:chart>'
    assert xlsx_source_structure(p)["headings"] == {2: 2}


def test_an_embedded_svg_appends_a_figures_section():
    """`ooxml_markdown` appends `## Figures` for EVERY format when an SVG media
    part carries text — so a workbook ground truth that ignored it would report one
    heading short on any workbook holding a vector diagram."""
    p = _simple()
    p["xl/media/rails.svg"] = ('<svg xmlns="http://www.w3.org/2000/svg">'
                               '<text>rail label</text></svg>')
    facts = xlsx_source_structure(p)
    assert [t for _, t in facts["heading_path"]][-1] == ("figures",)


def test_the_images_section_exists_only_when_images_are_emitted():
    """`## Images` is a real heading the converter adds under `emit_images`, and
    `scripts/build_bundle.py` converts with it ON. The flag therefore has to reach
    the ground truth, or every bundled workbook with a picture reads one heading
    short."""
    p = _simple()
    p["xl/drawings/drawing1.xml"] = (
        '<xdr:wsDr xmlns:xdr="x" xmlns:a="a"><xdr:pic><a:blip r:embed="rId1" %s/>'
        '</xdr:pic></xdr:wsDr>' % R)
    p["xl/drawings/_rels/drawing1.xml.rels"] = (
        '<Relationships %s><Relationship Id="rId1" Type="t" '
        'Target="../media/shot.png"/></Relationships>' % REL)
    p["xl/media/shot.png"] = "\x89PNG"
    assert [t for _, t in xlsx_source_structure(p)["heading_path"]][-1] != ("images",)
    with_imgs = xlsx_source_structure(p, emit_images=True)
    assert [t for _, t in with_imgs["heading_path"]][-1] == ("images",)


# ------------------------------------------------- what it states, and what it
# ------------------------------------------------- refuses to state

def test_the_facts_a_workbook_cannot_exhibit_are_stated_as_zero():
    """A stated zero is FALSIFIABLE and that is why it is worth stating: a workbook
    has no construct that renders as a fence, an ordered list or a horizontal rule,
    so any of those appearing in the markdown is a SUBSTITUTION — text replaced by
    punctuation that carries none of it. Omitting them would leave the substitution
    invisible, which is the `thematic_breaks` argument from docx, restated."""
    facts = xlsx_source_structure(_simple())
    for fact in ("code_spans", "code_blocks", "ordered_items", "thematic_breaks"):
        assert facts[fact] == 0, fact
    assert facts["ordered_numbers"] == []


def test_the_facts_the_converter_once_dropped_are_now_counted():
    """THE ANTI-PATTERN GUARD, and the rule it pins has been SATISFIED rather than
    abandoned.

    It read: a workbook's bold header row and its cell hyperlinks are real formatting
    `xlsx_markdown` does not emit, so stating `strong: 0` would make the ground truth
    inherit the converter's blind spot on the very axis it exists to police — it
    would CERTIFY the drop, and omitting the key puts it in `unmeasured` by name
    instead. The converter emits all four now (P9.8c), so the keys are present and
    are real counts; a zero here is a falsifiable claim about the SOURCE, which is
    the direction omission could never cover — a fact nobody states is a fact nobody
    can see fabricated."""
    facts = xlsx_source_structure(_simple())
    for fact in ("strong", "em", "strike", "links"):
        assert facts[fact] == 0, fact


def test_it_declares_the_structure_no_fact_in_the_vector_can_express():
    """`blind_to` is the honest half of a `pass`: a cell's ADDRESS, its formula, its
    number format, which row became the header. None of them has a name in the
    closed list, so the gate never claimed them, and saying so is what stops
    `pass` being read as "nothing was lost"."""
    blind = xlsx_source_structure(_simple())["_blind_to"]
    for claim in ("cell_addresses", "formulas", "number_formats", "header_row"):
        assert claim in blind, claim


def test_a_workbook_with_nothing_in_it_states_facts_rather_than_nothing():
    """An empty result must still be a MEASUREMENT: every key present, so the
    report reads `compared: 0` with the facts named, not `method: unmeasured`."""
    facts = xlsx_source_structure({})
    assert facts["headings"] == {} and facts["tables"] == []
    assert facts["block_sequence"] == []
    assert "code_blocks" in facts


# ============================================================ the measured drops
#
# A drop nobody counted reads exactly like a bug — and `=SUM(C3:C5)` in the shipped
# corpus workbook was worse than that: absent from the markdown, absent from the
# recall denominator AND absent from the warnings, so a reader had no hint that
# anything had been decided at all. Each warning below names WHAT was lost, HOW
# MUCH, and WHAT WAS KEPT.

def _styled(cellxfs, fonts="", numfmts=""):
    return {"xl/styles.xml":
            '<styleSheet %s>%s<fonts>%s</fonts><cellXfs>%s</cellXfs></styleSheet>'
            % (SS, numfmts, fonts, cellxfs)}


def test_a_dropped_formula_is_named_with_its_expression():
    """`first` quotes one, `sheet!ref = expression`, so the claim is checkable
    against the source rather than merely asserted."""
    p = _grid("xl/worksheets/sheet2.xml",
              [[("C6", "<f>SUM(C3:C5)</f><v>412.5</v>", None)]])
    codes = dict((w["code"], w) for w in xlsx_policy_drops(p))
    w = codes["dropped_cell_formulas"]
    assert w["cells"] == 1
    assert w["first"] == "sheet2!C6 = SUM(C3:C5)"
    assert "cached RESULT is published" in w["detail"]


def test_a_cell_with_no_formula_raises_no_formula_warning():
    p = _grid("xl/worksheets/sheet1.xml", [[("A1", _v(412.5), None)]])
    assert [w["code"] for w in xlsx_policy_drops(p)] == []


def test_a_date_serial_published_raw_is_counted():
    """46027 and 2026-01-05 share not one character. The number is exact; the
    presentation is not carried, and a reader handed the serial cannot recover the
    date without the format."""
    p = _grid("xl/worksheets/sheet1.xml", [[("A1", _v(46027), None)]])
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<c r="A1">', '<c r="A1" s="1">')
    p.update(_styled('<xf numFmtId="0"/><xf numFmtId="14"/>'))
    codes = dict((w["code"], w) for w in xlsx_policy_drops(p))
    assert codes["unformatted_cell_values"]["cells"] == 1


def test_a_precision_only_format_is_not_reported_as_a_lost_presentation():
    """The bound must not become a blindfold. `0.00` shows 1 as `1.00`, which is
    the same NUMBER differently spaced — reporting it beside a date serial would
    bury the class that actually costs a reader the value."""
    p = _grid("xl/worksheets/sheet1.xml", [[("A1", _v(1), None)]])
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<c r="A1">', '<c r="A1" s="1">')
    p.update(_styled('<xf numFmtId="0"/><xf numFmtId="2"/>'))
    assert "unformatted_cell_values" not in [w["code"] for w in xlsx_policy_drops(p)]


def test_a_custom_format_is_read_from_its_code_not_its_id():
    """A custom numFmtId is >= 164 and carries no built-in meaning, so the
    formatCode is the only place the intent is written."""
    p = _grid("xl/worksheets/sheet1.xml", [[("A1", _v(46027), None)]])
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<c r="A1">', '<c r="A1" s="1">')
    p.update(_styled('<xf numFmtId="0"/><xf numFmtId="165"/>',
                     numfmts='<numFmts><numFmt numFmtId="165" '
                             'formatCode="yyyy-mm-dd"/></numFmts>'))
    assert "unformatted_cell_values" in [w["code"] for w in xlsx_policy_drops(p)]


def test_a_text_cell_under_a_date_format_is_not_a_lost_presentation():
    """A string is already the text a reader sees, whatever format sits on it."""
    p = _grid("xl/worksheets/sheet1.xml",
              [[("A1", "<is><t>2026-01-05</t></is>", "inlineStr")]])
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<c r="A1" t="inlineStr">', '<c r="A1" s="1" t="inlineStr">')
    p.update(_styled('<xf numFmtId="0"/><xf numFmtId="14"/>'))
    assert "unformatted_cell_values" not in [w["code"] for w in xlsx_policy_drops(p)]


def test_struck_through_and_bold_cells_are_a_FACT_now_and_not_a_drop():
    """Was `test_struck_through_and_bold_cells_are_counted`, over
    `dropped_cell_emphasis` — the receipt for emphasis `xlsx_markdown` did not emit,
    whose detail explained that a struck-through row means CANCELLED and reads as
    live data once the marks are gone.

    It emits both now (P9.8c), so the WARNING is retired and the same marks are
    graded per span by the structure gate instead. That is strictly stronger: a
    warning says a loss happened, a fact says how much and is compared against what
    a markdown reader actually sees."""
    p = _grid("xl/worksheets/sheet1.xml",
              [[("A1", _v(1), None), ("B1", _v(2), None)]])
    p["xl/worksheets/sheet1.xml"] = (p["xl/worksheets/sheet1.xml"]
                                     .replace('<c r="A1">', '<c r="A1" s="1">')
                                     .replace('<c r="B1">', '<c r="B1" s="2">'))
    p.update(_styled('<xf fontId="0"/><xf fontId="1"/><xf fontId="2"/>',
                     fonts='<font/><font><b/></font><font><strike/></font>'))
    assert "dropped_cell_emphasis" not in [w["code"] for w in xlsx_policy_drops(p)]
    st = xlsx_source_structure(p)
    assert (st["strong"], st["strike"]) == (1, 1), st


def test_a_font_property_switched_off_is_not_emphasis():
    p = _grid("xl/worksheets/sheet1.xml", [[("A1", _v(1), None)]])
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<c r="A1">', '<c r="A1" s="1">')
    p.update(_styled('<xf fontId="0"/><xf fontId="1"/>',
                     fonts='<font/><font><b val="0"/></font>'))
    assert "dropped_cell_emphasis" not in [w["code"] for w in xlsx_policy_drops(p)]


def test_hidden_sheets_and_rows_are_disclosed_not_suppressed():
    """Dropping hidden content would be a losslessness failure; publishing it
    unmarked is a disclosure the reader is owed. So it is converted AND counted."""
    p = {}
    p.update(_book(("Real", "rId1"), ("Scratch", "rId2")))
    p["xl/workbook.xml"] = p["xl/workbook.xml"].replace(
        '<sheet name="Scratch" sheetId="2" r:id="rId2"/>',
        '<sheet name="Scratch" sheetId="2" r:id="rId2" state="hidden"/>')
    p.update(_grid("xl/worksheets/sheet1.xml", [[("A1", _v(1), None)]]))
    p["xl/worksheets/sheet1.xml"] = p["xl/worksheets/sheet1.xml"].replace(
        '<row r="1">', '<row r="1" hidden="1">')
    codes = dict((w["code"], w) for w in xlsx_policy_drops(p))
    assert codes["hidden_content_published"]["sheets"] == 1
    assert codes["hidden_content_published"]["rows"] == 1


def test_a_workbook_that_loses_nothing_says_nothing():
    """No drop, no warning. A vocabulary that fires on every document teaches
    nobody anything."""
    assert xlsx_policy_drops(_simple()) == []


# ============================== the bound must not become a blindfold
#
# A gate is only useful if it stays quiet on a FAITHFUL conversion. Each case below
# was a real false FAIL found by probing the two implementations against each other
# on shapes a workbook can legitimately hold — a cell whose text merely LOOKS like
# markdown, a malformed cell address, a picture whose bytes never made it into the
# package. A false FAIL is not a harmless over-caution: it costs the markdown and
# the images, and it teaches a reader to distrust the gate.

@pytest.mark.parametrize("value", [
    "| pipe", "**bold**", "- item", "# head", "> quote", "---", "a<br>b",
    "~~strike~~", "[text](url)", "![img](url)", "`code`", "1. step", "***",
    "    indented", "\\backslash", "_under_",
])
def test_a_cell_whose_text_looks_like_markdown_still_passes(value):
    """The converter ESCAPES these, so a renderer shows the characters literally and
    both sides must agree on that. `[text](url)` is the one that caught a real
    defect: the emitted reader reduced `\\[text\\](url)` to its "link text" and
    dropped the `url` token the document really holds."""
    p = _simple()
    p.update(_grid("xl/worksheets/sheet1.xml",
                   [[("A1", "<is><t xml:space='preserve'>%s</t></is>" % value,
                      "inlineStr"),
                     ("B1", "<is><t>ok</t></is>", "inlineStr")]]))
    md = ooxml_markdown("xlsx", p)
    verdict = structure_fidelity_report(md_structure(md), xlsx_source_structure(p))
    assert verdict["gate"] == "pass", (value, verdict["deltas"])


def test_a_malformed_cell_address_falls_back_the_same_way_on_both_sides():
    """Excel never writes a `$` into a cell's own `r` attribute — that belongs to
    formulas and defined names. Tolerating one here placed a value the converter's
    stricter reader had already given up on, so the two disagreed about a BROKEN
    document: a false FAIL. Both now fall back to append order."""
    p = _simple()
    p.update(_grid("xl/worksheets/sheet1.xml", [[]]))
    p["xl/worksheets/sheet1.xml"] = (
        '<worksheet %s><sheetData><row r="1">'
        '<c r="$B$1"><v>9</v></c></row></sheetData></worksheet>' % SS)
    md = ooxml_markdown("xlsx", p)
    assert structure_fidelity_report(md_structure(md),
                                     xlsx_source_structure(p))["gate"] == "pass"


def test_a_picture_whose_bytes_are_missing_is_still_a_declared_picture():
    """The converter resolves the relationship and emits its sentinel whether or not
    the media part made it into the package, so the `## Images` heading appears
    either way. Requiring the bytes here was over-clever: it failed a workbook over
    a packaging defect the report already names separately as `images_missing`."""
    p = _simple()
    p["xl/drawings/drawing1.xml"] = (
        '<xdr:wsDr xmlns:xdr="x" xmlns:a="a"><xdr:pic><a:blip r:embed="rId1" %s/>'
        '</xdr:pic></xdr:wsDr>' % R)
    p["xl/drawings/_rels/drawing1.xml.rels"] = (
        '<Relationships %s><Relationship Id="rId1" Type="t" '
        'Target="../media/gone.png"/></Relationships>' % REL)
    md = ooxml_markdown("xlsx", p, True)
    verdict = structure_fidelity_report(md_structure(md),
                                        xlsx_source_structure(p, True))
    assert verdict["gate"] == "pass", verdict["deltas"]
    assert [t for _, t in xlsx_source_structure(p, True)["heading_path"]][-1] == (
        "images",)


def test_an_svg_is_a_figure_and_never_a_picture():
    """An embedded SVG is extracted as TEXT, so it lands in `## Figures` and must
    not also open `## Images` — which would invent a heading on one side only."""
    p = _simple()
    p["xl/drawings/drawing1.xml"] = (
        '<xdr:wsDr xmlns:xdr="x" xmlns:a="a"><xdr:pic><a:blip r:embed="rId1" %s/>'
        '</xdr:pic></xdr:wsDr>' % R)
    p["xl/drawings/_rels/drawing1.xml.rels"] = (
        '<Relationships %s><Relationship Id="rId1" Type="t" '
        'Target="../media/rails.svg"/></Relationships>' % REL)
    p["xl/media/rails.svg"] = ('<svg xmlns="http://www.w3.org/2000/svg">'
                               '<text>rail label</text></svg>')
    md = ooxml_markdown("xlsx", p, True)
    assert structure_fidelity_report(md_structure(md),
                                     xlsx_source_structure(p, True))["gate"] == "pass"
    titles = [t for _, t in xlsx_source_structure(p, True)["heading_path"]]
    assert ("figures",) in titles and ("images",) not in titles


@pytest.mark.parametrize("shape,why", [
    ([[("AA1", "<v>7</v>", None), ("AB1", "<v>8</v>", None)]],
     "a value past column Z, where base-26 has no zero digit"),
    ([[("A1", "<v>1</v>", None)], [("A2", "", None)], [("A3", "<v>3</v>", None)]],
     "a blank row between two full ones, which the renderer drops"),
])
def test_grid_shapes_a_workbook_can_legitimately_hold(shape, why):
    p = _simple()
    p.update(_grid("xl/worksheets/sheet1.xml", shape))
    md = ooxml_markdown("xlsx", p)
    verdict = structure_fidelity_report(md_structure(md), xlsx_source_structure(p))
    assert verdict["gate"] == "pass", (why, verdict["deltas"])


def test_cells_with_no_address_at_all_fall_back_to_append_order():
    """`<c>` with no `r` attribute is legal-ish and both readers must place it the
    same way — by position in the row — or a document neither can address becomes a
    disagreement about nothing."""
    p = _simple()
    p["xl/worksheets/sheet1.xml"] = (
        '<worksheet %s><sheetData><row r="1"><c><v>1</v></c><c><v>2</v></c>'
        '</row></sheetData></worksheet>' % SS)
    md = ooxml_markdown("xlsx", p)
    verdict = structure_fidelity_report(md_structure(md), xlsx_source_structure(p))
    assert verdict["gate"] == "pass", verdict["deltas"]


# ================================================ P9.8c: the workbook's UNMEASURED
#
# Same move as the deck, one layer down. A workbook states emphasis per CELL — `@s`
# into `cellXfs -> fonts`, all positional — so a bold header cell is ONE bold span
# covering the whole cell and a struck-through row is one strike span per cell. The
# converter emits them now, so the keys stop being omitted and start being counted.

_XF = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
       '<fonts count="4"><font/><font><b/></font><font><i/></font>'
       '<font><strike/></font></fonts>'
       '<cellXfs count="4"><xf fontId="0"/><xf fontId="1"/><xf fontId="2"/>'
       '<xf fontId="3"/></cellXfs></styleSheet>')


def _drop_codes(parts):
    return dict((w["code"], w) for w in xlsx_policy_drops(parts))


def _styled_sheet(rows_xml, links="", styles=None, rels=None):
    """One tab whose sheet part is supplied verbatim, so a test can state `@s`
    attributes and a `<hyperlinks>` block that the grid helpers do not model."""
    p = {}
    p.update(_book(("Corners", "rId1")))
    p.update(_rels(("rId1", "worksheets/sheet1.xml")))
    p["xl/worksheets/sheet1.xml"] = (
        '<worksheet %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships"><sheetData>%s</sheetData>%s</worksheet>'
        % (SS, rows_xml, links))
    p["xl/styles.xml"] = styles if styles is not None else _XF
    if rels:
        p["xl/worksheets/_rels/sheet1.xml.rels"] = rels
    return p


@pytest.mark.parametrize("style,fact", [("1", "strong"), ("2", "em"), ("3", "strike")])
def test_a_styled_cell_is_one_span_in_the_fact_vector(style, fact):
    st = xlsx_source_structure(_styled_sheet(
        '<row r="1"><c r="A1" s="%s" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Margin</t></is></c></row>' % style))
    assert st[fact] == 1, st


def test_a_workbook_that_draws_nothing_states_the_zero_rather_than_omitting_it():
    """The end of `unmeasured` for this lane. The zero is now a real claim about the
    SOURCE, which is what makes a `**` appearing in the markdown anyway a fabrication
    the gate can see — the direction the omission rule could never cover."""
    st = xlsx_source_structure(_styled_sheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Margin</t></is></c></row>'))
    for fact in ("strong", "em", "strike", "links"):
        assert st[fact] == 0, fact


def test_an_empty_styled_cell_is_not_a_span():
    """It renders nothing, so there is nothing to emphasise. Counting it would demand
    markers the converter is right not to write."""
    assert xlsx_source_structure(_styled_sheet(
        '<row r="1"><c r="A1" s="1" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" s="1"/>'
        '<c r="C1" s="1" t="inlineStr"><is><t>Margin</t></is></c></row>'
    ))["strong"] == 2


def test_an_external_cell_hyperlink_is_one_link():
    parts = _styled_sheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>the spec</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>owner</t></is></c></row>',
        links='<hyperlinks><hyperlink ref="A1" r:id="rId9"/></hyperlinks>',
        rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
             'relationships"><Relationship Id="rId9" '
             'Target="https://example.invalid/spec" TargetMode="External"/>'
             '</Relationships>')
    assert xlsx_source_structure(parts)["links"] == 1


def test_an_internal_workbook_jump_is_not_a_link_on_either_side():
    """A `location`-only hyperlink jumps within the same workbook. It has no address
    a reader outside it could follow, so the render keeps the text and drops it —
    and a truth that counted it would fail a faithful conversion."""
    parts = _styled_sheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>see Rails</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>owner</t></is></c></row>',
        links='<hyperlinks><hyperlink ref="A1" location="Rails!A1"/></hyperlinks>')
    assert xlsx_source_structure(parts)["links"] == 0


def test_a_bold_cell_is_no_longer_reported_as_dropped():
    """`dropped_cell_emphasis` counted the bold, italic and struck cells
    `xlsx_markdown` did not emit. It emits all three now (P9.8c), so the code is
    retired rather than left firing over a faithful conversion — a drop code that
    reports a loss nobody suffered teaches a reader to discount the vocabulary."""
    parts = _styled_sheet(
        '<row r="1"><c r="A1" s="1" t="inlineStr"><is><t>Corner</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Margin</t></is></c></row>')
    assert "dropped_cell_emphasis" not in _drop_codes(parts)


def test_an_external_cell_hyperlink_is_no_longer_reported_as_dropped():
    parts = _styled_sheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>the spec</t></is></c></row>',
        links='<hyperlinks><hyperlink ref="A1" r:id="rId9"/></hyperlinks>',
        rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
             'relationships"><Relationship Id="rId9" '
             'Target="https://example.invalid/spec" TargetMode="External"/>'
             '</Relationships>')
    assert "dropped_cell_links" not in _drop_codes(parts)


def test_an_internal_workbook_jump_still_loses_its_target_and_still_says_so():
    """`dropped_cell_links` NARROWS rather than retires: a `location` jump stays
    inside the workbook, so there is no address to write and the loss is real."""
    parts = _styled_sheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>see Rails</t></is></c></row>',
        links='<hyperlinks><hyperlink ref="A1" location="Rails!A1"/></hyperlinks>')
    drop = _drop_codes(parts)["dropped_cell_links"]
    assert drop["cells"] == 1
    assert "Rails!A1" in drop["first"]
