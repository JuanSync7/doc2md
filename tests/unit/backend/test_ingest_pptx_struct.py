"""
title: Unit — the converter-blind pptx token ground truth
kind: tests
layer: backend
summary: pptx_source_text reads every word a deck holds by its own flat scan, excludes chrome by deriving the placeholder role itself, and no longer cancels a converter bug.
"""
# WHY THIS EXISTS. `pptx_source_text` used to live in the converter's module and call
# the converter's helpers, so a bug in one of them was applied to BOTH halves of the
# losslessness comparison and the gate cancelled to a clean pass. Measured on the
# shipped `kestrel-overview.pptx` before the split:
#
#     bug _sp_ph_type (both sides)  -> recall 1.0, n_source 108 -> 20, valid True
#
# Eighty-two per cent of a deck's text gone at a perfect score. These tests pin the
# reader's behaviour AND the property that makes it worth having: it must disagree
# with a broken converter.
import pytest

from backend.ingest import pptx_source_text

pytestmark = pytest.mark.unit

P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _para(*runs):
    return "<a:p>%s</a:p>" % "".join('<a:r><a:t>%s</a:t></a:r>' % r for r in runs)


def _shape(body, ph_type=None, ph_idx=None):
    """One shape. A placeholder role is declared in p:nvSpPr/p:nvPr/p:ph — which is
    where the ground truth looks for it."""
    ph = ""
    if ph_type is not None:
        at = ' type="%s"' % ph_type
        if ph_idx is not None:
            at += ' idx="%s"' % ph_idx
        ph = "<p:ph%s/>" % at
    return ('<p:sp><p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr>'
            '<p:txBody>%s</p:txBody></p:sp>' % (ph, body))


def _slide(*shapes):
    return {"ppt/slides/slide1.xml":
            '<p:sld %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, "".join(shapes))}


# ------------------------------------------------------- it reads what is there

def test_every_run_on_every_slide_is_in_the_denominator():
    parts = _slide(_shape(_para("Escalation policy")),
                   _shape(_para("Page the on-call engineer")))
    text = pptx_source_text(parts)
    assert "Escalation policy" in text
    assert "Page the on-call engineer" in text


def test_adjacent_runs_concatenate_with_no_invented_space():
    """PowerPoint splits one word across runs at a formatting boundary — bolding
    the middle of a word is enough. A space here would invent a word break and put
    two tokens in the denominator that the document does not contain."""
    parts = _slide(_shape(_para("Xbar", "Route", "Cfg")))
    assert "XbarRouteCfg" in pptx_source_text(parts)


def test_a_paragraph_boundary_does_separate_words():
    parts = _slide(_shape(_para("first") + _para("second")))
    assert "first second" in pptx_source_text(parts)


def test_an_explicit_break_separates_words():
    parts = _slide(_shape('<a:p><a:r><a:t>top</a:t></a:r><a:br/>'
                          '<a:r><a:t>bottom</a:t></a:r></a:p>'))
    assert "top bottom" in pptx_source_text(parts)


def test_slides_are_read_in_part_number_order_not_string_order():
    """slide10 sorts before slide2 as a string. Order does not change a token
    multiset, so the gate cannot see the difference — but a reader of this output
    can, and a truth that scrambles its own output invites a false diagnosis."""
    parts = {}
    for n, word in ((1, "alpha"), (2, "beta"), (10, "omega")):
        parts["ppt/slides/slide%d.xml" % n] = (
            '<p:sld %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, _shape(_para(word))))
    assert pptx_source_text(parts) == "alpha beta omega"


def test_speaker_notes_diagrams_charts_and_comments_all_count():
    parts = _slide(_shape(_para("body")))
    parts["ppt/notesSlides/notesSlide1.xml"] = (
        '<p:notes %s %s>%s</p:notes>' % (P, A, _shape(_para("remind them"))))
    parts["ppt/diagrams/data1.xml"] = (
        '<dgm:dataModel xmlns:dgm="d" %s><dgm:ptLst>'
        '<dgm:pt><dgm:t>%s</dgm:t></dgm:pt></dgm:ptLst></dgm:dataModel>'
        % (A, _para("sensor front end")))
    parts["ppt/charts/chart1.xml"] = (
        '<c:chart xmlns:c="c" %s>%s<c:v>41.5</c:v></c:chart>' % (A, _para("Latency")))
    parts["ppt/comments/modernComment_x.xml"] = (
        '<p188:cm xmlns:p188="p188" %s>%s</p188:cm>' % (A, _para("check this")))
    text = pptx_source_text(parts)
    for want in ("body", "remind them", "sensor front end", "Latency", "41.5",
                 "check this"):
        assert want in text, want


def test_a_cached_chart_value_is_padded_so_two_do_not_fuse():
    parts = _slide(_shape(_para("x")))
    parts["ppt/charts/chart1.xml"] = (
        '<c:chart xmlns:c="c" %s><c:v>40</c:v><c:v>60</c:v></c:chart>' % A)
    assert "40 60" in pptx_source_text(parts)


# ------------------------------------------------- it excludes what it must, by
# ------------------------------------------------- deriving the role ITSELF

@pytest.mark.parametrize("role", ["sldNum", "dt", "ftr"])
def test_a_chrome_placeholder_is_outside_the_denominator(role):
    """A banner repeated on every slide is furniture, not body text. Both sides
    exclude it under the same declared policy — otherwise they are not comparing
    the same document — but this side DERIVES the role rather than asking the
    converter's predicate."""
    parts = _slide(_shape(_para("real body text")),
                   _shape(_para("Nimbus Semiconductor Confidential"), ph_type=role))
    text = pptx_source_text(parts)
    assert "real body text" in text
    assert "Confidential" not in text


def test_a_content_placeholder_is_body_text():
    """The bound must not become a blindfold: only the three chrome roles are
    furniture. A title or body placeholder is exactly the text a deck is FOR."""
    parts = _slide(_shape(_para("Escalation policy"), ph_type="title"),
                   _shape(_para("Page the on-call engineer"), ph_type="body", ph_idx=1))
    text = pptx_source_text(parts)
    assert "Escalation policy" in text and "Page the on-call engineer" in text


def test_a_ph_that_is_not_a_placeholder_declaration_does_not_mute_a_shape():
    """The role lives in p:nvSpPr/p:nvPr. An element that merely shares the local
    name `ph`, somewhere else in the shape, is not a role declaration — reading it
    as one would silently delete real body text from the denominator."""
    body = ('<p:txBody><a:p><a:r><a:t>keep me</a:t></a:r>'
            '<a:ph type="ftr"/></a:p></p:txBody>')
    parts = {"ppt/slides/slide1.xml":
             '<p:sld %s %s><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr>'
             '%s</p:sp></p:spTree></p:cSld></p:sld>' % (P, A, body)}
    assert "keep me" in pptx_source_text(parts)


def test_a_fallback_subtree_is_not_counted_twice():
    """AlternateContent holds Choice and Fallback renderings of the SAME shape.
    Counting both would put every word in the denominator twice, and a conversion
    that emitted each once would read as 50% recall."""
    parts = {"ppt/slides/slide1.xml":
             '<p:sld %s %s><p:cSld><p:spTree><mc:AlternateContent xmlns:mc="mc">'
             '<mc:Choice>%s</mc:Choice><mc:Fallback>%s</mc:Fallback>'
             '</mc:AlternateContent></p:spTree></p:cSld></p:sld>'
             % (P, A, _shape(_para("once")), _shape(_para("once")))}
    assert pptx_source_text(parts).split().count("once") == 1


# ------------------------------------------------------- it degrades, not raises

def test_a_malformed_slide_costs_that_slide_and_not_the_deck():
    parts = {"ppt/slides/slide1.xml": "<p:sld><unclosed>",
             "ppt/slides/slide2.xml":
             '<p:sld %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
             % (P, A, _shape(_para("survives")))}
    assert pptx_source_text(parts) == "survives"


def test_a_deck_with_no_slides_is_empty_not_an_error():
    assert pptx_source_text({}) == ""
    assert pptx_source_text({"docProps/core.xml": "<x/>"}) == ""


# =========================================================== the STRUCTURAL truth
#
# Everything above answers "which words does this deck hold?". Everything below
# answers the second question — "does the markdown still MEAN what the deck meant?"
# — and it is a different kind of claim, because a token multiset is blind to
# arrangement by construction. Measured on the shipped `kestrel-reordered.pptx`: a
# deck published in the order its slides were DRAFTED rather than the order it
# SHOWS them reports `recall: 1.0, n_source: 135` either way. Five slides in the
# wrong order, every word present, nothing to see.
#
# THE TWO JUSTIFICATIONS every fact below must satisfy. A fact belongs here only if
# it is (i) a statement about the SOURCE DOCUMENT, or (ii) a statement about what
# MARKDOWN can hold. It may never be a statement about what this repo's converter
# happens to do — making a ground truth agree with the converter is how a gate
# certifies its own bugs, and it is the one failure mode this module cannot recover
# from, because the report then says `pass` and means nothing.

from backend.ingest import pptx_source_structure                    # noqa: E402
from backend.validate import md_structure, structure_fidelity_report  # noqa: E402


def _pres(order, rels=None):
    """A presentation part naming slides by position, plus its rels.

    ``order`` is a list of slide PART numbers in the order the deck SHOWS them —
    which is what `p:sldIdLst` records and what a slide's filename does not."""
    ids = "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, n)
                  for i, n in enumerate(order))
    targets = rels if rels is not None else \
        dict((n, "slides/slide%d.xml" % n) for n in order)
    rel_xml = "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats'
                      '.org/officeDocument/2006/relationships/slide" Target="%s"/>'
                      % (n, t) for n, t in sorted(targets.items()))
    return {"ppt/presentation.xml":
            '<p:presentation %s %s><p:sldIdLst>%s</p:sldIdLst></p:presentation>'
            % (P, R, ids),
            "ppt/_rels/presentation.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">%s</Relationships>' % rel_xml}


def _deck(slides, order=None, extra=None):
    """``slides`` is {part_number: shapes_xml}; ``order`` the presentation order."""
    parts = {}
    for n, shapes in slides.items():
        parts["ppt/slides/slide%d.xml" % n] = (
            '<p:sld %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, shapes))
    if order is not None:
        parts.update(_pres(order))
    parts.update(extra or {})
    return parts


def _bullets(*levelled):
    """One body shape whose paragraphs carry the given (outline level, text)."""
    paras = "".join('<a:p>%s<a:r><a:t>%s</a:t></a:r></a:p>'
                    % ('<a:pPr lvl="%d"/>' % lvl if lvl else "", text)
                    for lvl, text in levelled)
    return ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
            '</p:nvSpPr><p:txBody>%s</p:txBody></p:sp>' % paras)


def _title(text):
    return _shape(_para(text), ph_type="title")


# ------------------------------------------------- the slide is the section, and
# ------------------------------------------------- its NUMBER is its position

def test_every_slide_is_one_level_two_heading():
    facts = pptx_source_structure(_deck({1: _title("Rollout"), 2: _title("Risks")}))
    assert facts["headings"] == {2: 2}


def test_the_heading_carries_the_slide_position_and_its_title_words():
    facts = pptx_source_structure(_deck({1: _title("Rollout plan")}))
    assert facts["heading_path"] == [(2, ("slide", "1", "rollout", "plan"))]


def test_a_slide_with_no_title_is_still_a_heading():
    """Losing a title must cost the TITLE, never the section: a slide of nothing
    but shapes is still one slide, and a truth that skipped it would let a
    converter drop the whole section at a perfect score."""
    facts = pptx_source_structure(_deck({1: _bullets((0, "sensor front end"))}))
    assert facts["heading_path"] == [(2, ("slide", "1"))]


def test_the_position_comes_from_the_deck_and_not_from_the_filename():
    """PowerPoint does not renumber slide parts when a user drags a slide: it
    rewrites `p:sldIdLst` and leaves `slideN.xml` where it was. So the part number
    is the order the slides were DRAFTED in, and reading it as the position
    publishes a deck nobody ever presented."""
    facts = pptx_source_structure(_deck(
        {1: _title("first"), 2: _title("last"), 3: _title("middle")},
        order=[1, 3, 2]))
    assert facts["heading_path"] == [(2, ("slide", "1", "first")),
                                     (2, ("slide", "2", "middle")),
                                     (2, ("slide", "3", "last"))]


def test_a_deck_that_cannot_state_an_order_falls_back_to_the_part_numbers():
    """No presentation part is not an error — a deck still has slides, and they
    still have to publish. slide10 sorts before slide2 as a STRING, so the fallback
    is numeric."""
    facts = pptx_source_structure(_deck({2: _title("second"), 10: _title("tenth")}))
    assert facts["heading_path"] == [(2, ("slide", "1", "second")),
                                     (2, ("slide", "2", "tenth"))]


def test_a_slide_the_deck_never_lists_publishes_after_the_ordered_ones():
    """A deleted-but-not-purged slide has no position and may not claim one — but
    its words are in the package and the token gate counts them, so dropping it
    would fail the document outright."""
    facts = pptx_source_structure(_deck(
        {1: _title("shown"), 7: _title("orphan")}, order=[1]))
    assert facts["heading_path"] == [(2, ("slide", "1", "shown")),
                                     (2, ("slide", "unlisted", "slide7", "orphan"))]


def test_a_relationship_that_resolves_to_nothing_is_not_a_position():
    """An `r:id` naming a target that is not a slide part in this package cannot
    put anything at that position. Counting it would shift every slide after it."""
    parts = _deck({1: _title("real")}, order=[1])
    parts.update(_pres([1, 4], rels={1: "slides/slide1.xml",
                                     4: "slides/slide4.xml"}))
    facts = pptx_source_structure(parts)
    assert facts["heading_path"] == [(2, ("slide", "1", "real"))]


# ------------------------------------------------------------- the body bullets

def test_a_body_paragraph_is_a_bullet_at_its_outline_level():
    facts = pptx_source_structure(_deck({1: _bullets(
        (0, "the legacy bus saturates"), (1, "arbitration stalls the pipe"),
        (1, "no quality of service"), (0, "kestrel moves to a crossbar"))}))
    assert facts["list_items"] == {0: 2, 1: 2}
    assert facts["bullet_items"] == 4
    assert facts["list_item_words"][1] == ("arbitration", "stalls", "the", "pipe")


def test_a_level_more_than_one_deeper_than_its_parent_is_clamped():
    """NOT a concession to the converter — a statement about what markdown can
    hold. CommonMark nests a child item only under a parent that exists, and there
    is no parent at level 1 here, so no renderer on earth shows this item two deep.
    A truth that demanded depth 2 would fail every faithful conversion of a deck
    whose author skipped an outline level, for ever, with no fix available.

    The LOSS is real and is reported — as a counted `flattened_list_levels`
    warning, which is the same shape the lane already uses for a table span GFM
    cannot hold."""
    facts = pptx_source_structure(_deck({1: _bullets((0, "top"), (2, "skipped"))}))
    assert facts["list_items"] == {0: 1, 1: 1}
    assert facts["block_sequence"][1:] == [("li", 0), ("li", 1)]


def test_a_bullet_that_opens_a_slide_below_level_zero_starts_at_the_top():
    """There is no ancestor at all, so the deepest markdown can put it is depth 0."""
    facts = pptx_source_structure(_deck({1: _bullets((3, "starts deep"))}))
    assert facts["list_items"] == {0: 1}


def test_a_block_between_two_items_restarts_the_nesting():
    """A table ends the list. The item after it has no open ancestor, so it is a
    top-level item again however deep the deck says it is."""
    tbl = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
           '<a:tblGrid><a:gridCol w="1"/></a:tblGrid>'
           '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>cell</a:t></a:r></a:p>'
           '</a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic>'
           '</p:graphicFrame>')
    facts = pptx_source_structure(_deck(
        {1: _bullets((0, "before")) + tbl + _bullets((1, "after"))}))
    assert facts["block_sequence"] == [("h", 2), ("li", 0), ("table", (1, 1)),
                                       ("li", 0)]


def test_a_chrome_placeholder_contributes_no_bullet():
    """The same policy the token side applies, derived here the same independent
    way. A banner repeated on every slide is furniture; counting it would put a
    bullet in the truth that the markdown correctly does not have."""
    facts = pptx_source_structure(_deck(
        {1: _bullets((0, "real body text"))
            + _shape(_para("Nimbus Confidential"), ph_type="ftr")}))
    assert facts["bullet_items"] == 1


def test_a_title_placeholder_is_the_heading_and_not_also_a_bullet():
    facts = pptx_source_structure(_deck({1: _title("Rollout") + _bullets((0, "x"))}))
    assert facts["bullet_items"] == 1
    assert facts["heading_path"] == [(2, ("slide", "1", "rollout"))]


def test_a_second_title_placeholder_is_body_text():
    """Only one title can be in the heading. The second must not vanish — its words
    are in the package and the token gate counts them."""
    facts = pptx_source_structure(_deck({1: _title("first") + _title("second")}))
    assert facts["heading_path"] == [(2, ("slide", "1", "first"))]
    assert facts["bullet_items"] == 1


def test_an_empty_paragraph_is_no_bullet_at_all():
    facts = pptx_source_structure(_deck({1: _bullets((0, ""), (0, "real"))}))
    assert facts["bullet_items"] == 1


# ------------------------------------------------------------------- the tables

def _tbl(rows, cols=None, attrs=""):
    grid = "".join('<a:gridCol w="100"/>' % () for _ in range(cols or len(rows[0])))
    trs = ""
    for row in rows:
        tcs = "".join('<a:tc%s><a:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p>'
                      '</a:txBody></a:tc>' % (at, txt) for txt, at in row)
        trs += '<a:tr h="1">%s</a:tr>' % tcs
    return ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl%s>'
            '<a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>' % (attrs, grid, trs))


def test_a_table_is_stated_with_its_geometry_and_every_cell():
    facts = pptx_source_structure(_deck({1: _tbl([
        [("Path", ""), ("Cycles", "")],
        [("display read", ""), ("40", "")]])}))
    assert facts["tables"] == [{"rows": 2, "cols": 2, "has_header": True,
                                "cells": [(("path",), ("cycles",)),
                                          (("display", "read"), ("40",))]}]
    assert facts["block_sequence"] == [("h", 2), ("table", (2, 2))]


def test_a_horizontal_span_keeps_the_grid_width():
    """DrawingML writes a span as ATTRIBUTES on `a:tc`: the origin carries
    `gridSpan`, and the cells it covers are present but marked `hMerge`. They are
    real grid positions, so the table is two columns wide and the covered one is
    empty — which is exactly what the source holds."""
    facts = pptx_source_structure(_deck({1: _tbl([
        [("Corner", ' gridSpan="2"'), ("", ' hMerge="1"')],
        [("ssg", ""), ("0.94", "")]])}))
    assert facts["tables"][0]["rows"] == 2
    assert facts["tables"][0]["cols"] == 2
    assert facts["tables"][0]["cells"][0] == (("corner",), ())


def test_a_span_in_the_last_column_does_not_narrow_the_table():
    """The covered cell is empty, and an always-empty trailing column looks exactly
    like a styled-but-valueless one. The declared `a:tblGrid` is the difference: a
    deck STATES its column count, so the width is read and never inferred."""
    facts = pptx_source_structure(_deck({1: _tbl([
        [("Owner", ' gridSpan="2"'), ("", ' hMerge="1"')],
        [("fabric", ""), ("", "")]])}))
    assert facts["tables"][0]["cols"] == 2


def test_a_table_nested_in_a_cell_is_not_a_table_of_its_own():
    """GFM has no cell that can hold a table, so the inner rows are flattened into
    the owning cell. Counting it twice would claim the slide holds two tables."""
    inner = ('<a:tbl><a:tblGrid><a:gridCol w="1"/></a:tblGrid><a:tr><a:tc>'
             '<a:txBody><a:p><a:r><a:t>inner</a:t></a:r></a:p></a:txBody>'
             '</a:tc></a:tr></a:tbl>')
    outer = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
             '<a:tblGrid><a:gridCol w="1"/></a:tblGrid><a:tr><a:tc><a:txBody>'
             '<a:p><a:r><a:t>outer</a:t></a:r></a:p></a:txBody>%s</a:tc></a:tr>'
             '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>' % inner)
    facts = pptx_source_structure(_deck({1: outer}))
    assert len(facts["tables"]) == 1


# --------------------------------------------------------- the satellite sections

def test_speaker_notes_open_a_level_three_heading():
    parts = _deck({1: _title("Rollout")}, extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId2" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/notesSlide" '
        'Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        "ppt/notesSlides/notesSlide1.xml":
        '<p:notes %s %s>%s</p:notes>' % (P, A, _bullets((0, "remind them")))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"] == [(2, ("slide", "1", "rollout")),
                                     (3, ("speaker", "notes"))]
    assert facts["list_item_words"][-1] == ("remind", "them")


def test_the_notes_title_is_prose_and_never_a_bullet():
    """The notes heading shape renders as a paragraph under `### Speaker notes`,
    not as an item — and a paragraph is deliberately not a block kind, because
    neither side of this gate has an opinion about where prose lands."""
    parts = _deck({1: _title("Rollout")}, extra={
        "ppt/notesSlides/notesSlide1.xml":
        '<p:notes %s %s>%s%s</p:notes>'
        % (P, A, _shape(_para("Timing"), ph_type="title"),
           _bullets((0, "body of the note")))})
    facts = pptx_source_structure(parts)
    assert facts["bullet_items"] == 1
    assert facts["block_sequence"][-1] == ("li", 0)


def test_notes_with_nothing_in_them_open_no_section():
    parts = _deck({1: _title("Rollout")}, extra={
        "ppt/notesSlides/notesSlide1.xml": '<p:notes %s %s/>' % (P, A)})
    assert pptx_source_structure(parts)["headings"] == {2: 1}


def test_a_diagram_is_a_heading_over_one_bullet_per_point():
    parts = _deck({1: _title("Flow")}, extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId2" Type="d" '
        'Target="../diagrams/data1.xml"/></Relationships>',
        "ppt/diagrams/data1.xml":
        '<dgm:dataModel xmlns:dgm="d" %s><dgm:ptLst>'
        '<dgm:pt>%s</dgm:pt><dgm:pt>%s</dgm:pt></dgm:ptLst></dgm:dataModel>'
        % (A, _para("sensor front end"), _para("dsp core"))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"][-1] == (3, ("diagram",))
    assert facts["bullet_items"] == 2
    assert facts["block_sequence"] == [("h", 2), ("h", 3), ("li", 0), ("li", 0)]


def test_a_chart_is_a_heading_over_prose_and_no_bullets():
    parts = _deck({1: _title("Latency")}, extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId2" Type="c" '
        'Target="../charts/chart1.xml"/></Relationships>',
        "ppt/charts/chart1.xml":
        '<c:chart xmlns:c="c" %s>%s</c:chart>' % (A, _para("Latency by path"))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"][-1] == (3, ("chart",))
    assert facts["bullet_items"] == 0


def test_an_embedded_part_no_slide_references_lands_in_its_own_section():
    parts = _deck({1: _title("Flow")}, extra={
        "ppt/diagrams/data1.xml":
        '<dgm:dataModel xmlns:dgm="d" %s><dgm:ptLst><dgm:pt>%s</dgm:pt>'
        '</dgm:ptLst></dgm:dataModel>' % (A, _para("orphan node"))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"][-1] == (2, ("embedded", "objects"))
    assert facts["bullet_items"] == 1


def test_comments_are_one_section_of_bullets():
    parts = _deck({1: _title("Rollout")}, extra={
        "ppt/comments/modernComment_1.xml":
        '<p188:cm xmlns:p188="p188" %s>%s</p188:cm>' % (A, _para("check this")),
        "ppt/comments/modernComment_2.xml":
        '<p188:cm xmlns:p188="p188" %s>%s</p188:cm>' % (A, _para("and this"))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"][-1] == (2, ("comments",))
    assert facts["bullet_items"] == 2


def test_an_embedded_svg_appends_a_figures_section():
    parts = _deck({1: _title("Flow")}, extra={
        "ppt/media/image1.svg":
        '<svg xmlns="http://www.w3.org/2000/svg"><text>tx clock</text></svg>'})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"][-1] == (2, ("figures",))


def test_a_textless_chart_or_diagram_is_no_section_at_all():
    parts = _deck({1: _title("Flow")}, extra={
        "ppt/charts/chart1.xml": '<c:chart xmlns:c="c" %s/>' % A,
        "ppt/diagrams/data1.xml": '<dgm:dataModel xmlns:dgm="d" %s/>' % A})
    assert pptx_source_structure(parts)["headings"] == {2: 1}


# ------------------------------------------- what it states, and what it refuses
# ------------------------------------------- to state

def test_the_facts_a_deck_cannot_exhibit_are_stated_as_zero():
    """A zero is falsifiable and that is the point. A deck holds no construct that
    renders as a horizontal rule or a code fence, so any of those in the markdown is
    a SUBSTITUTION — document text replaced by punctuation carrying none of it — and
    a substitution deletes the characters it replaces, so token recall reads a
    vacuous 1.0 over the damage. The stated zero is the only handle there is.

    Measured before this landed: a slide bullet reading `- - -` was emitted
    `- - - -`, which CommonMark reads as a thematic break. `bullet_items` 16 -> 15,
    `thematic_breaks` 0 -> 1, `recall` 1.0, `warnings` empty."""
    facts = pptx_source_structure(_deck({1: _bullets((0, "body"))}))
    for fact in ("thematic_breaks", "code_spans", "code_blocks"):
        assert facts[fact] == 0, fact


def test_an_ordered_marker_is_now_a_stated_count_whether_or_not_the_deck_numbers():
    """Was `test_an_ordered_marker_is_a_stated_zero_only_when_the_deck_has_no_numbering`.

    `a:buAutoNum` makes a paragraph an ordered item. For as long as the converter
    dropped the ordinal, stating `ordered_items: 0` over a deck that HAS one would
    have certified the drop — the ground truth inheriting the converter's blind spot
    on the axis it exists to police — so the key was left out and the report named it
    in `unmeasured`. P9.8b taught the converter the whole cascade, so both keys are
    always present and always real: the LAST two facts to leave that list."""
    plain = pptx_source_structure(_deck({1: _bullets((0, "body"))}))
    assert plain["ordered_items"] == 0 and plain["ordered_numbers"] == []
    numbered = pptx_source_structure(_deck({1: (
        '<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>'
        '<a:p><a:pPr><a:buAutoNum type="arabicPeriod"/></a:pPr>'
        '<a:r><a:t>first step</a:t></a:r></a:p></p:txBody></p:sp>')}))
    assert numbered["ordered_items"] == 1
    assert numbered["ordered_numbers"] == [1]


def test_the_facts_the_converter_once_dropped_are_now_counted_in_both_directions():
    """Was `test_the_facts_the_converter_drops_are_omitted_only_when_the_deck_has_them`,
    and the rule it pinned has been SATISFIED rather than abandoned.

    The rule: a fact the converter drops must be OMITTED, not stated as zero, because
    a stated `strong: 0` over a deck that draws bold makes this module inherit the
    converter's blind spot on the axis it exists to police. That rule ended the only
    way it is allowed to — the converter stopped dropping them (P9.8a) — so the key
    is now always present and always a real count.

    Both directions still have to hold, and the second is what the omission rule
    could never give: a fact nobody states is a fact nobody can see FABRICATED.
    Measured with the converter's escaping reduced to the identity, a bullet reading
    `set *ready* high` renders real emphasis over words the deck wrote literally, at
    `gate: pass, recall: 1.0` — and that was invisible for exactly as long as `em`
    was `unmeasured`.

    `ordered_items` is still conditional, and deliberately: `a:buAutoNum` is real
    ordering `pptx_markdown` does not emit yet. It is the last of these, and the rule
    above is why it stays omitted until P9.8b makes it countable."""
    plain = pptx_source_structure(_deck({1: _bullets((0, "body"))}))
    for fact in ("strong", "em", "strike", "links"):
        assert plain[fact] == 0, fact

    def run(rpr, text):
        return ('<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r>%s'
                '<a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>' % (rpr, text))
    link_rels = {"ppt/slides/_rels/slide1.xml.rels": _LINK_RELS.replace(
        'Id="rId9"', 'Id="rId7"')}
    for fact, rpr, extra in (
            ("strong", '<a:rPr b="1"/>', None),
            ("em", '<a:rPr i="1"/>', None),
            ("strike", '<a:rPr strike="sngStrike"/>', None),
            ("links", '<a:rPr><a:hlinkClick xmlns:r="http://schemas.'
                      'openxmlformats.org/officeDocument/2006/'
                      'relationships" r:id="rId7"/></a:rPr>', link_rels)):
        facts = pptx_source_structure(_deck({1: run(rpr, "marked")}, extra=extra))
        assert facts[fact] == 1, (fact, facts)


def test_a_bold_footer_does_not_make_the_whole_deck_unmeasurable():
    """A chrome shape's text is dropped WHOLE, so its marks are not a lost emphasis —
    they are part of a loss `dropped_slide_chrome` already reports. Counting them
    pushed `strong` into `unmeasured` for a deck whose body carries no emphasis at
    all, which is a real fact traded for a false one."""
    facts = pptx_source_structure(_deck(
        {1: _bullets((0, "plain body"))
            + ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr>'
               '<p:txBody><a:p><a:r><a:rPr b="1"/><a:t>Confidential</a:t></a:r>'
               '</a:p></p:txBody></p:sp>')}))
    assert facts["strong"] == 0


def test_it_declares_the_structure_no_fact_in_the_vector_can_express():
    facts = pptx_source_structure(_deck({1: _bullets((0, "body"))}))
    assert facts["_blind_to"]


def test_a_deck_with_nothing_in_it_states_facts_rather_than_nothing():
    """An empty dict would read `unmeasured` — a clean bill of health from a truth
    that stated nothing at all."""
    from backend.validate._mdcheck import _FIDELITY_FACTS
    facts = pptx_source_structure({})
    assert any(f in facts for f in _FIDELITY_FACTS)


# ------------------------------------------- it is its OWN reader, end to end

def test_it_does_not_import_the_converters_slide_order_reader():
    """The one import that would undo this module. `pptx_slide_order` lives in the
    converter and is what `pptx_markdown` numbers its headings from; sharing it puts
    one reader on BOTH sides of the comparison, so a bug in it reorders the markdown
    and the ground truth identically and the gate reports `pass` over a deck nobody
    presented. Measured in P9.5, and it is the reason the plan's own instruction to
    put that function here was rejected."""
    import ast
    import backend.ingest._pptx_struct as mod
    tree = ast.parse(open(mod.__file__.replace(".pyc", ".py")).read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append("." * node.level + (node.module or ""))
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    # The AST and not the source text: naming the trap in a comment is how the next
    # reader knows not to fall into it, so only a real binding may fail this.
    assert "pptx_slide_order" not in imported
    assert not [m for m in imported if "_ooxml_md" in m]
    # A CLOSED whitelist, not a blacklist: a new relative import has to be argued for
    # here before it can land. Both entries import nothing from the package and are
    # called on exactly ONE side of this comparison — `_ooxml_leaf` is XML syntax,
    # `_struct_common` is mechanism shared with the other two SOURCE-side truths and
    # with neither the converter nor the markdown reader (enforced by
    # tests/unit/backend/test_ingest_struct_common.py).
    assert sorted(m for m in imported if m.startswith(".")) == [
        "._ooxml_leaf", "._struct_common"], imported


# ================================================== the drops, named and counted
#
# end-goal.md §1: a drop is always DELIBERATE AND VISIBLE, never an accident. A drop
# nobody counted reads exactly like a bug — and worse, it reads like nothing at all.
# Every class below was measured on a constructed deck: each is INVISIBLE to token
# recall, most of them because the text is excluded from the denominator too, so a
# symmetric exclusion cannot move a recall metric however much it removes.

from backend.ingest import pptx_policy_drops                        # noqa: E402


def _codes(parts):
    return dict((w["code"], w) for w in pptx_policy_drops(parts))


def test_a_deck_that_loses_nothing_says_nothing():
    """The other half of the contract. A warning list that is never empty is a
    warning list nobody reads."""
    assert pptx_policy_drops(_deck({1: _title("Rollout") + _bullets((0, "x"))})) == []


def test_a_page_furniture_placeholder_is_counted_because_nothing_else_can_see_it():
    """Measured: a footer, a date and a slide number carrying 59 characters between
    them leave the markdown BYTE-IDENTICAL and `n_source` unchanged at 8 — both
    halves of the token gate exclude the same shapes, and a symmetric exclusion
    cannot move recall. `structure_fidelity` is blind to it too: a shape excluded
    from both sides contributes to none of the sixteen compared facts. This count is
    the only record there can be."""
    parts = _deck({1: _bullets((0, "real body"))
                   + _shape(_para("Nimbus Confidential"), ph_type="ftr")
                   + _shape(_para("02/09/2026"), ph_type="dt")
                   + _shape(_para("7"), ph_type="sldNum")})
    drop = _codes(parts)["dropped_slide_chrome"]
    assert drop["shapes"] == 3
    assert (drop["footers"], drop["dates"], drop["slide_numbers"]) == (1, 1, 1)
    assert drop["chars"] == len("Nimbus Confidential02/09/20267")
    assert "Nimbus Confidential" in drop["first"]


def test_the_authored_banner_and_the_regenerated_field_are_told_apart():
    """The `dropped_cell_formulas` / `empty_cell_formulas` lesson: one sentence for
    both would tell a reader that their `Confidential` banner and their page number
    were the same kind of loss. A date and a slide number are values PowerPoint
    regenerates; losing those loses nothing."""
    numbers_only = _deck({1: _bullets((0, "body"))
                          + _shape(_para("7"), ph_type="sldNum")})
    detail = _codes(numbers_only)["dropped_slide_chrome"]["detail"]
    assert "AUTHORED" not in detail
    authored = _deck({1: _bullets((0, "body"))
                      + _shape(_para("Nimbus Confidential"), ph_type="ftr")})
    assert "AUTHORED" in _codes(authored)["dropped_slide_chrome"]["detail"]


def test_run_emphasis_is_a_counted_FACT_now_and_not_a_drop():
    """Was `test_run_emphasis_is_counted_per_mark`, over `dropped_shape_emphasis`.

    That code counted bold, italic and strikethrough runs the converter did not
    emit, and its detail carried a paragraph about a struck-through line reading as
    live copy once the marks are gone. The converter emits all three now (P9.8a), so
    the WARNING is retired and the same three marks are graded per span by the
    structure gate instead — a stronger statement, because a warning says a loss
    happened and a fact says how much and gets compared."""
    parts = _deck({1: (
        '<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p>'
        '<a:r><a:rPr b="1"/><a:t>bold</a:t></a:r>'
        '<a:r><a:rPr i="1"/><a:t>italic</a:t></a:r>'
        '<a:r><a:rPr strike="sngStrike"/><a:t>gone</a:t></a:r>'
        '</a:p></p:txBody></p:sp>')})
    assert "dropped_shape_emphasis" not in _codes(parts)
    st = pptx_source_structure(parts)
    assert (st["strong"], st["em"], st["strike"]) == (1, 1, 1), st


def test_a_mark_switched_off_is_not_emphasis():
    parts = _deck({1: ('<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p>'
                       '<a:r><a:rPr b="0" strike="noStrike"/><a:t>plain</a:t></a:r>'
                       '</a:p></p:txBody></p:sp>')})
    assert "dropped_shape_emphasis" not in _codes(parts)
    st = pptx_source_structure(parts)
    assert (st["strong"], st["em"], st["strike"]) == (0, 0, 0), st


def test_a_hyperlink_quotes_the_destination_it_destroyed():
    """A destination is on NEITHER side of the token gate — it is markup, never slide
    text — so nothing else in the report can see this loss, which is why it is quoted
    rather than merely counted.

    Repointed in P9.8a at the loss that REMAINS. An external url is emitted as a real
    `[text](url)` now, so quoting it would be a receipt for nothing; a slide-to-slide
    jump still loses its destination, and the property this test is about — the
    receipt names what it destroyed — is unchanged."""
    parts = _deck({1: (
        '<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r>'
        '<a:rPr><a:hlinkClick xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships" r:id="rId7"/></a:rPr>'
        '<a:t>the fabric spec</a:t></a:r></a:p></p:txBody></p:sp>')},
        order=[1], extra={
            "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId7" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slide4.xml"/></Relationships>'})
    drop = _codes(parts)["dropped_shape_links"]
    assert drop["links"] == 1
    assert "slide4.xml" in drop["first"]
    assert "Slide 1" in drop["first"]


def test_alt_text_is_counted_because_nothing_reads_it_at_all():
    """Prose a sighted reader never sees and a screen reader does, dropped with no
    signal of any kind: `p:cNvPr/@descr` is read by neither half of the token gate,
    so recall is a clean 1.0 over the whole of it."""
    parts = _deck({1: (
        '<p:pic><p:nvPicPr><p:cNvPr id="9" name="plot" '
        'descr="Coverage trend over six regressions"/></p:nvPicPr></p:pic>')})
    drop = _codes(parts)["dropped_shape_alt_text"]
    assert drop["shapes"] == 1
    assert drop["chars"] == len("Coverage trend over six regressions")
    assert "Coverage trend" in drop["first"]


def test_the_bullet_cascade_is_one_code_for_the_two_decisions_that_remain():
    """`buChar` and `buNone` are two spellings of one converter decision — the
    paragraph publishes as a plain `-` — so they are one code with two counts rather
    than two codes. Neither is really a choice: markdown has exactly ONE bullet glyph
    and no way to write an item with no marker at all.

    `buAutoNum` was the third, and it left in P9.8b. It was the one of the three
    markdown CAN hold, which is why it was the one that became a graded fact
    (`ordered_items`, `ordered_numbers`) instead of staying a counted loss — and why
    this test now asserts the field is GONE rather than that it counts two."""
    def para(pr, text):
        return '<a:p><a:pPr>%s</a:pPr><a:r><a:t>%s</a:t></a:r></a:p>' % (pr, text)
    parts = _deck({1: ('<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>%s</p:txBody>'
                       '</p:sp>' % (
                           para('<a:buAutoNum type="arabicPeriod"/>', "reset")
                           + para('<a:buAutoNum type="arabicPeriod"/>', "release")
                           + para('<a:buChar char="»"/>', "custom")
                           + para('<a:buNone/>', "lead-in")))})
    drop = _codes(parts)["flattened_bullet_formatting"]
    assert drop["paragraphs"] == 2
    assert (drop["custom_char"], drop["suppressed"]) == (1, 1)
    assert "auto_numbered" not in drop
    st = pptx_source_structure(parts)
    assert (st["ordered_items"], st["ordered_numbers"]) == (2, [1, 2]), st


def test_a_list_style_default_is_not_a_paragraph_that_lost_its_bullet():
    """`a:buChar` inside `a:lstStyle` is a DEFAULT for a level, not a paragraph's own
    declaration. Counting it would report a loss on decks that have none, and — the
    part that matters — would make `ordered_items` unmeasurable on a deck whose
    paragraphs are all plain."""
    parts = _deck({1: ('<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>'
                       '<a:lstStyle><a:lvl1pPr><a:buChar char="•"/>'
                       '</a:lvl1pPr></a:lstStyle>'
                       '<a:p><a:r><a:t>plain</a:t></a:r></a:p></p:txBody></p:sp>')})
    assert "flattened_bullet_formatting" not in _codes(parts)
    assert pptx_source_structure(parts)["ordered_items"] == 0


def test_a_hidden_slide_is_disclosed_and_named_by_position():
    """The deck does not SHOW it and the markdown publishes it as a peer of the
    slides that are shown — the same disclosure a hidden worksheet gets, so it is
    the same code. Measured: the markdown of a deck whose second slide is
    `show="0"` is byte-identical to the same deck without it."""
    parts = _deck({1: _title("Pricing"), 2: _title("Backup pricing")}, order=[1, 2])
    parts["ppt/slides/slide2.xml"] = parts["ppt/slides/slide2.xml"].replace(
        "<p:sld ", '<p:sld show="0" ', 1)
    drop = _codes(parts)["hidden_content_published"]
    assert drop["slides"] == 1
    assert drop["slide_positions"] == [2]


def test_a_slide_shown_explicitly_is_not_hidden():
    parts = _deck({1: _title("Pricing")}, order=[1])
    parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace(
        "<p:sld ", '<p:sld show="1" ', 1)
    assert "hidden_content_published" not in _codes(parts)


def test_the_chrome_counts_sum_to_the_number_of_shapes_dropped():
    """One sentence is built from all four numbers, so they have to agree. Counted
    per `p:ph` DECLARATION they did not: a shape carrying two of them reported two
    placeholders and twice its own characters, and a chrome role declared on a
    `p:pic` — which `_chrome_shapes` correctly does not exclude, so nothing was
    dropped — was reported as furniture that had been."""
    parts = _deck({1: (
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/><p:ph type="dt"/></p:nvPr>'
        '</p:nvSpPr><p:txBody>%s</p:txBody></p:sp>'
        '<p:pic><p:nvPicPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvPicPr>'
        '</p:pic>' % _para("banner"))})
    drop = _codes(parts)["dropped_slide_chrome"]
    assert drop["shapes"] == 1
    assert drop["footers"] + drop["dates"] + drop["slide_numbers"] == drop["shapes"]
    assert drop["chars"] == len("banner")


def test_a_hidden_slide_the_deck_never_lists_is_not_given_a_position():
    """A part number is not a position. On parts 1..3 with `sldIdLst = [3, 1]` and
    part 2 hidden and unlisted, publishing "position 2" sent a reader to
    `## Slide 2 — Alpha`, which is part 1 — a slide the deck fully SHOWS. The
    locator named the wrong slide, not merely a missing one."""
    parts = _deck({1: _title("Alpha"), 2: _title("Backup"), 3: _title("Gamma")},
                  order=[3, 1])
    parts["ppt/slides/slide2.xml"] = parts["ppt/slides/slide2.xml"].replace(
        "<p:sld ", '<p:sld show="0" ', 1)
    drop = _codes(parts)["hidden_content_published"]
    assert drop["slides"] == 1
    assert drop["slide_positions"] == []
    assert "slide2" in drop["detail"] and "no position" in drop["detail"]


def test_a_hyperlink_in_speaker_notes_is_located_by_its_slide():
    """`_positions` was keyed on slides while the scan walks notes too, so a notes
    part fell through to its raw package path — and on a reordered deck
    `notesSlide1.xml` belongs to the slide published FIFTH."""
    parts = _deck({1: _title("first"), 2: _title("second")}, order=[2, 1], extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId2" Type="notesSlide" '
        'Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        "ppt/notesSlides/notesSlide1.xml":
        '<p:notes %s %s><p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r>'
        '<a:rPr><a:hlinkClick xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships" r:id="rId9"/></a:rPr>'
        '<a:t>escalation rota</a:t></a:r></a:p></p:txBody></p:sp></p:notes>'
        % (P, A),
        "ppt/notesSlides/_rels/notesSlide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId9" Type="slide" '
        'Target="slide2.xml"/>'
        '</Relationships>'})
    first = _codes(parts)["dropped_shape_links"]["first"]
    assert first.startswith("Speaker notes of Slide 2"), first
    assert "notesSlide1.xml" not in first


def test_a_fallback_diagram_point_is_not_counted_twice():
    """`mc:AlternateContent` holds Choice and Fallback renderings of the SAME
    diagram. `_walk_text(pt)` builds its parent map from the point DOWN, so it could
    not see a Fallback above it and the duplicate published as a second bullet."""
    parts = _deck({1: _title("Flow")}, extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId2" Type="d" '
        'Target="../diagrams/data1.xml"/></Relationships>',
        "ppt/diagrams/data1.xml":
        '<dgm:dataModel xmlns:dgm="d" xmlns:mc="mc" %s><dgm:ptLst>'
        '<dgm:pt>%s</dgm:pt><mc:AlternateContent><mc:Choice>'
        '<dgm:pt>%s</dgm:pt></mc:Choice><mc:Fallback><dgm:pt>%s</dgm:pt>'
        '</mc:Fallback></mc:AlternateContent></dgm:ptLst></dgm:dataModel>'
        % (A, _para("sensor front end"), _para("dsp core"), _para("dsp core"))})
    assert pptx_source_structure(parts)["bullet_items"] == 2


def test_an_embedded_part_two_slides_show_publishes_under_each_of_them():
    """Deduping document-wide cost the second slide its `### Diagram` heading, its
    bullets and their place in the sequence — six deltas at recall 1.0, on a deck
    the converter had rendered correctly."""
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId2" Type="d" '
            'Target="../diagrams/data1.xml"/></Relationships>')
    parts = _deck({1: _title("one"), 2: _title("two")}, order=[1, 2], extra={
        "ppt/slides/_rels/slide1.xml.rels": rels,
        "ppt/slides/_rels/slide2.xml.rels": rels,
        "ppt/diagrams/data1.xml":
        '<dgm:dataModel xmlns:dgm="d" %s><dgm:ptLst><dgm:pt>%s</dgm:pt>'
        '</dgm:ptLst></dgm:dataModel>' % (A, _para("sensor front end"))})
    facts = pptx_source_structure(parts)
    assert facts["heading_path"].count((3, ("diagram",))) == 2
    assert facts["bullet_items"] == 2


def test_two_embedded_parts_publish_in_the_slides_own_relationship_order():
    """`rId10` sorts before `rId9` as a STRING. The markdown publishes them in
    document order, so sorting the ids put the truth's sections the other way round
    and failed a faithful conversion of any slide carrying two embedded parts."""
    parts = _deck({1: _title("one")}, order=[1], extra={
        "ppt/slides/_rels/slide1.xml.rels":
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        '<Relationship Id="rId9" Type="d" Target="../diagrams/data1.xml"/>'
        '<Relationship Id="rId10" Type="c" Target="../charts/chart1.xml"/>'
        '</Relationships>',
        "ppt/diagrams/data1.xml":
        '<dgm:dataModel xmlns:dgm="d" %s><dgm:ptLst><dgm:pt>%s</dgm:pt>'
        '</dgm:ptLst></dgm:dataModel>' % (A, _para("node")),
        "ppt/charts/chart1.xml":
        '<c:chart xmlns:c="c" %s>%s</c:chart>' % (A, _para("Latency"))})
    assert pptx_source_structure(parts)["heading_path"][1:] == [
        (3, ("diagram",)), (3, ("chart",))]


def test_an_auto_numbered_list_style_is_read_through_to_the_count():
    """Was `test_an_auto_numbered_list_style_makes_the_ordinal_unmeasurable`.

    `a:buAutoNum` in the SHAPE's `a:lstStyle` numbers every paragraph in it. The
    per-paragraph reader could not see it, so the whole body list read as plain
    bullets and the fact had to be withheld. The cascade resolves it now, and the
    same declaration produces a real ordinal instead of an absence."""
    parts = _deck({1: (
        '<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>'
        '<a:lstStyle><a:lvl1pPr><a:buAutoNum type="arabicPeriod"/></a:lvl1pPr>'
        '</a:lstStyle>'
        '<a:p><a:r><a:t>reset the clock tree</a:t></a:r></a:p></p:txBody></p:sp>')})
    st = pptx_source_structure(parts)
    assert (st["ordered_items"], st["bullet_items"]) == (1, 0), st


def test_an_auto_numbered_footer_does_not_make_the_deck_unmeasurable():
    """A chrome paragraph publishes nothing, so it can neither lose an ordinal nor
    take `ordered_items` away from a deck whose body has none."""
    parts = _deck({1: _bullets((0, "body"))
                   + ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/></p:nvPr>'
                      '</p:nvSpPr><p:txBody><a:p><a:pPr>'
                      '<a:buAutoNum type="arabicPeriod"/></a:pPr>'
                      '<a:r><a:t>1 of 8</a:t></a:r></a:p></p:txBody></p:sp>')})
    facts = pptx_source_structure(parts)
    assert facts["ordered_items"] == 0
    assert "flattened_bullet_formatting" not in _codes(parts)


def test_a_level_markdown_could_not_hold_is_counted():
    parts = _deck({1: _bullets((0, "top"), (2, "child"), (2, "peer"))})
    drop = _codes(parts)["flattened_list_levels"]
    assert drop["count"] == 2
    assert "flattened_list_levels" not in _codes(
        _deck({1: _bullets((0, "top"), (1, "child"))}))


# ============================================ P9.8a: the facts that were UNMEASURED
#
# `strong`, `em`, `strike` and `links` were left OUT of this vector — not stated as
# zero — for as long as `pptx_markdown` dropped them. That was the right call and the
# report said so by name: a stated `strong: 0` over a deck that draws bold would have
# CERTIFIED the loss on the very axis this module exists to police, which is what the
# whole omission-is-not-zero rule is for.
#
# The converter emits them now, so the omission has to end — and it ends by COUNTING,
# never by writing the zero the old code was right to refuse. The unit is the SPAN a
# renderer shows, not the run the deck stores: a deck splits a word across runs at
# any property boundary, and `**Dma****Arbiter**` is one bold span to every reader of
# markdown. Measured against marko 2.2.3, `***x***` is one strong AND one em.

def _emph_shape(runs, ph_type="body"):
    """A body shape whose single paragraph is an explicit list of (rPr, text)."""
    body = "".join('<a:r>%s<a:t>%s</a:t></a:r>' % (rpr, t) for rpr, t in runs)
    return ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="%s" idx="1"/></p:nvPr></p:nvSpPr>'
            '<p:txBody><a:p>%s</a:p></p:txBody></p:sp>' % (ph_type, body))


@pytest.mark.parametrize("rpr,fact", [
    ('<a:rPr b="1"/>', "strong"),
    ('<a:rPr i="1"/>', "em"),
    ('<a:rPr strike="sngStrike"/>', "strike"),
    ('<a:rPr strike="dblStrike"/>', "strike"),
])
def test_a_marked_run_is_one_span_in_the_fact_vector(rpr, fact):
    parts = _slide(_emph_shape([("", "Sign-off needs "), (rpr, "both"), ("", " now")]))
    st = pptx_source_structure(parts)
    assert st[fact] == 1, st


def test_a_run_that_is_bold_and_italic_is_one_of_each():
    """What marko says, not what seems tidy: `***x***` renders as strong containing
    em, so both facts see it once."""
    parts = _slide(_emph_shape([('<a:rPr b="1" i="1"/>', "both")]))
    st = pptx_source_structure(parts)
    assert (st["strong"], st["em"]) == (1, 1), st


def test_adjacent_runs_with_the_same_mark_are_one_span_not_two():
    """The independence claim in miniature. The converter coalesces because emitting
    `**Dma****Arbiter**` corrupts the text layer; this side must reach the SAME
    number by reading the source, or a faithful deck fails for counting runs."""
    parts = _slide(_emph_shape([('<a:rPr b="1"/>', "Dma"), ('<a:rPr b="1"/>', "Arbiter")]))
    assert pptx_source_structure(parts)["strong"] == 1


def test_two_marked_runs_separated_by_plain_text_are_two_spans():
    parts = _slide(_emph_shape([('<a:rPr b="1"/>', "Dma"), ("", " and "),
                                ('<a:rPr b="1"/>', "Arbiter")]))
    assert pptx_source_structure(parts)["strong"] == 2


@pytest.mark.parametrize("rpr", ['<a:rPr b="0"/>', '<a:rPr strike="noStrike"/>',
                                 '<a:rPr/>', ""])
def test_an_off_spelling_is_not_emphasis(rpr):
    """A deck writes `b="0"` and `strike="noStrike"` all the time. Counting those
    would demand emphasis the render correctly does not draw, and fail every
    faithful conversion of an ordinary deck."""
    parts = _slide(_emph_shape([("", "plain "), (rpr, "text")]))
    st = pptx_source_structure(parts)
    assert (st["strong"], st["em"], st["strike"]) == (0, 0, 0), st


def test_a_deck_that_draws_no_emphasis_states_the_zero_rather_than_omitting_it():
    """The end of `unmeasured`. Before the converter emitted emphasis, leaving the
    key out was the only honest option; now that it does, the zero is a real claim
    about the source and a markdown `**` appearing anyway is a FABRICATION the gate
    must catch."""
    st = pptx_source_structure(_slide(_bullets((0, "Reset the clock tree"))))
    for fact in ("strong", "em", "strike", "links"):
        assert st[fact] == 0, fact


_LINK_RPR = ('<a:rPr><a:hlinkClick xmlns:r="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships" r:id="rId9"/></a:rPr>')
_LINK_RELS = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
              'relationships"><Relationship Id="rId9" Type="http://schemas.'
              'openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
              'Target="https://example.invalid/spec" TargetMode="External"/>'
              '</Relationships>')


def test_an_external_hyperlink_is_one_link():
    parts = _slide(_emph_shape([("", "see "), (_LINK_RPR, "the spec")]))
    parts["ppt/slides/_rels/slide1.xml.rels"] = _LINK_RELS
    assert pptx_source_structure(parts)["links"] == 1


def test_an_internal_jump_is_not_a_link_on_either_side():
    """The converter keeps a slide-to-slide jump's TEXT and drops its address,
    because `[text]()` is a dead link. A truth that counted it would demand a link
    the render is right not to draw."""
    parts = _slide(_emph_shape([("", "see "), (_LINK_RPR, "the backup")]))
    parts["ppt/slides/_rels/slide1.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId9" Type="http://schemas.openxmlformats.'
        'org/officeDocument/2006/relationships/slide" Target="slide4.xml"/>'
        '</Relationships>')
    assert pptx_source_structure(parts)["links"] == 0


def test_slide_chrome_draws_emphasis_nobody_publishes():
    """A bold footer is not a bold span in the document: the shape is excluded from
    the render entirely, so counting it would fail a faithful deck."""
    chrome = ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr>'
              '<p:txBody><a:p><a:r><a:rPr b="1"/><a:t>Confidential</a:t></a:r>'
              '</a:p></p:txBody></p:sp>')
    parts = _slide(chrome, _bullets((0, "Reset the clock tree")))
    assert pptx_source_structure(parts)["strong"] == 0


# ------------------------------------- the span rule, against a real markdown reader
#
# The two sides have to agree about what a SPAN is, and "I reasoned about it" is not
# how this project settles that. The converter coalesces adjacent identically-marked
# runs and the truth counts transitions; both are claims about what a CommonMark
# renderer shows, so the honest check is to render the deck and read the result back
# with `md_structure` — the same reader the gate compares against, which is itself
# differentially tested against marko 2.2.3.
#
# Exhaustive over every sequence of up to three runs drawn from a mark alphabet, so
# it covers the cases that are easy to get wrong by thinking: bold-bold (one span),
# bold-plain-bold (two), bold-boldItalic (two, because the mark SETS differ), and a
# whitespace-only run separating two bold runs (two, because it renders).

_MARK_ALPHABET = (
    ("", ""),
    ('<a:rPr b="1"/>', "strong"),
    ('<a:rPr i="1"/>', "em"),
    ('<a:rPr b="1" i="1"/>', "strong+em"),
    ('<a:rPr strike="sngStrike"/>', "strike"),
)


def _emph_deck(runs):
    body = "".join('<a:r>%s<a:t>%s</a:t></a:r>' % (rpr, t) for rpr, t in runs)
    return _slide('<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
                  '</p:nvSpPr><p:txBody><a:p>%s</a:p></p:txBody></p:sp>' % body)


def _sequences():
    import itertools
    words = ("alpha", "beta", "gamma")
    out = []
    for n in (1, 2, 3):
        for combo in itertools.product(_MARK_ALPHABET, repeat=n):
            runs = tuple((rpr, words[i]) for i, (rpr, _name) in enumerate(combo))
            out.append((runs, "|".join(name or "plain" for _r, name in combo)))
    return out


# THE ONE ADJACENCY THE RENDERER COULD NOT EXPRESS — fixed in P9.9, and this is where
# it was found. A `strong+em` run immediately followed by an `em` run emitted
# `***alpha****beta*`, a delimiter run of FOUR asterisks, and CommonMark read one em
# span where the deck draws two. It was 1 of 25 adjacent mark pairs here and a great
# deal more once combined marks were included; measured pre-existing and cross-format,
# an ordinary Word paragraph of that shape refused to publish at HEAD.
#
# `_render_runs` now writes an empty html comment between two spans whose delimiter
# runs would fuse or fail to flank. The family below was carried as
# `xfail(strict=True)` for exactly one slice, which is what turned it RED the moment
# the fix landed — a silently-passing xfail is how a known defect becomes a forgotten
# one, and this matrix is now green with no exemptions at all.


@pytest.mark.parametrize("runs,label", _sequences(), ids=[s[1] for s in _sequences()])
def test_the_truth_predicts_what_a_markdown_reader_sees(runs, label):
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    parts = _emph_deck(runs)
    truth = pptx_source_structure(parts)
    seen = md_structure(ooxml_markdown("pptx", parts, True))
    for fact in ("strong", "em", "strike"):
        assert truth[fact] == seen[fact], (
            "%s: source says %s=%d, the rendered markdown shows %d\n%s"
            % (label, fact, truth[fact], seen[fact],
               ooxml_markdown("pptx", parts, True)))


def test_a_whitespace_run_between_two_bold_runs_separates_them():
    """The case the transition rule exists for. It renders as a space, so the render
    shows two bold spans — and a truth that skipped whitespace-only runs would say
    one and fail a faithful deck."""
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    parts = _emph_deck((('<a:rPr b="1"/>', "alpha"), ("", " "),
                        ('<a:rPr b="1"/>', "beta")))
    assert pptx_source_structure(parts)["strong"] == 2
    assert md_structure(ooxml_markdown("pptx", parts, True))["strong"] == 2


# ------------------------------------------- the receipts for a loss that has ENDED
#
# A warning is a receipt for something the render really loses. When the converter
# stops losing it, the receipt has to go — a drop code that fires over a document
# nothing was dropped from is worse than no code at all, because it teaches a reader
# to discount the whole vocabulary.

def test_a_deck_that_draws_emphasis_no_longer_reports_it_as_dropped():
    """`dropped_shape_emphasis` counted bold, italic and strikethrough runs that
    `pptx_markdown` did not emit. It emits all three now, so the code is retired
    rather than left firing over a faithful conversion."""
    parts = _slide(_emph_shape([("", "Sign-off needs "),
                                ('<a:rPr b="1"/>', "both"),
                                ('<a:rPr i="1"/>', " now"),
                                ('<a:rPr strike="sngStrike"/>', " week six")]))
    assert "dropped_shape_emphasis" not in _codes(parts)


def test_an_external_hyperlink_is_no_longer_reported_as_dropped():
    parts = _slide(_emph_shape([("", "see "), (_LINK_RPR, "the spec")]))
    parts["ppt/slides/_rels/slide1.xml.rels"] = _LINK_RELS
    assert "dropped_shape_links" not in _codes(parts)


def test_an_internal_jump_still_loses_its_target_and_still_says_so():
    """`dropped_shape_links` NARROWS rather than retires. A slide-to-slide jump has
    no address a reader outside the deck could follow, so the render keeps the text
    and drops the link — a real loss, on NEITHER side of the token gate, and the only
    place it can be seen is here."""
    parts = _slide(_emph_shape([("", "see "), (_LINK_RPR, "the backup")]))
    parts["ppt/slides/_rels/slide1.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId9" Type="http://schemas.openxmlformats.'
        'org/officeDocument/2006/relationships/slide" Target="slide4.xml"/>'
        '</Relationships>')
    drop = _codes(parts)["dropped_shape_links"]
    assert drop["links"] == 1
    assert "slide4.xml" in drop["first"]


# ==================================== P9.8b: the last two facts in `unmeasured`
#
# `a:buAutoNum` makes a paragraph an ORDERED item — a construct markdown holds
# perfectly well, which is why `ordered_items` sat in `unmeasured` rather than being
# stated zero: the deck converter dropped the ordinal, and a stated zero would have
# certified the drop. It emits `1.` `2.` `3.` now, so these become real counts.
#
# The CASCADE is derived here from the source, independently of `_ooxml_md`'s. That
# duplication is the point: two readers resolving `a:pPr` -> shape `a:lstStyle` ->
# layout placeholder -> master `p:bodyStyle` by different code agree only when both
# are right, and a single shared resolver would make a bug in it cancel.

_AUTO = '<a:buAutoNum type="arabicPeriod"/>'


def _numbered(paras, lst="", ph="body", idx="1"):
    body = "".join('<a:p><a:pPr lvl="%d">%s</a:pPr><a:r><a:t>%s</a:t></a:r></a:p>'
                   % (lvl, inner, text) for lvl, inner, text in paras)
    return ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="%s" idx="%s"/></p:nvPr></p:nvSpPr>'
            '<p:txBody>%s%s</p:txBody></p:sp>' % (ph, idx, lst, body))


def test_auto_numbered_paragraphs_are_ordered_items_not_bullets():
    st = pptx_source_structure(_slide(_numbered([
        (0, _AUTO, "one"), (0, _AUTO, "two")])))
    assert st["ordered_items"] == 2, st
    assert st["bullet_items"] == 0, st
    assert st["ordered_numbers"] == [1, 2], st


def test_a_plain_item_between_numbered_ones_restarts_the_sequence():
    """The `1, 2, 1, 2` shape `ordered_numbers` exists for. Counts and depths cannot
    see a list broken in two; the numbers a reader SEES can."""
    st = pptx_source_structure(_slide(_numbered([
        (0, _AUTO, "one"), (0, _AUTO, "two"),
        (0, "", "an aside"),
        (0, _AUTO, "three")])))
    assert st["ordered_numbers"] == [1, 2, 1], st
    assert (st["ordered_items"], st["bullet_items"]) == (3, 1), st


def test_a_declared_start_is_what_the_reader_sees():
    st = pptx_source_structure(_slide(_numbered([
        (0, '<a:buAutoNum type="arabicPeriod" startAt="5"/>', "five"),
        (0, _AUTO, "six")])))
    assert st["ordered_numbers"] == [5, 6], st


def test_numbering_inherited_from_the_shapes_list_style_is_still_ordered():
    st = pptx_source_structure(_slide(_numbered(
        [(0, "", "one"), (0, "", "two")],
        lst='<a:lstStyle><a:lvl1pPr>%s</a:lvl1pPr></a:lstStyle>' % _AUTO)))
    assert st["ordered_items"] == 2 and st["bullet_items"] == 0, st


def test_numbering_inherited_from_the_slide_master_is_still_ordered():
    """The step the old code named as the reason it could only answer "could be
    numbered": layout and master parts were not read at all, so neither side of
    either gate could see a deck numbered from its theme."""
    parts = _slide(_numbered([(0, "", "one"), (0, "", "two")]))
    parts.update({
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
        "ppt/slideLayouts/slideLayout1.xml":
            '<p:sldLayout %s %s><p:cSld><p:spTree/></p:cSld></p:sldLayout>' % (P, A),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        "ppt/slideMasters/slideMaster1.xml":
            '<p:sldMaster %s %s><p:cSld><p:spTree/></p:cSld><p:txStyles>'
            '<p:bodyStyle><a:lvl1pPr>%s</a:lvl1pPr></p:bodyStyle></p:txStyles>'
            '</p:sldMaster>' % (P, A, _AUTO)})
    st = pptx_source_structure(parts)
    assert st["ordered_items"] == 2 and st["bullet_items"] == 0, st


def test_bu_none_beats_an_inherited_number_here_too():
    st = pptx_source_structure(_slide(_numbered(
        [(0, '<a:buNone/>', "a lead-in"), (0, "", "one")],
        lst='<a:lstStyle><a:lvl1pPr>%s</a:lvl1pPr></a:lstStyle>' % _AUTO)))
    assert (st["ordered_items"], st["bullet_items"]) == (1, 1), st
    assert st["ordered_numbers"] == [1], st


def test_a_deck_with_no_numbering_states_the_zero_rather_than_omitting_it():
    """The LAST entry in `unmeasured` for this lane. From here a deck report names
    every one of the sixteen facts or explains itself in `blind_to`."""
    st = pptx_source_structure(_slide(_bullets((0, "Reset the clock tree"))))
    assert st["ordered_items"] == 0 and st["ordered_numbers"] == []


def test_an_auto_numbered_paragraph_is_no_longer_a_flattened_bullet():
    """`flattened_bullet_formatting` NARROWS. It counted three losses in one code —
    an auto-numbered step losing its ordinal, a custom glyph, and a no-bullet
    paragraph gaining one. The ordinal is emitted now (P9.8b) and graded as
    `ordered_items`/`ordered_numbers`, so it stops being a loss; the other two are
    still real, because markdown has exactly one bullet glyph and no way to say
    `this item has no marker`."""
    parts = _slide(_numbered([(0, _AUTO, "one"), (0, _AUTO, "two")]))
    assert "flattened_bullet_formatting" not in _codes(parts)


def test_a_custom_glyph_and_a_suppressed_bullet_are_still_flattened():
    parts = _slide(_numbered([(0, '<a:buChar char="»"/>', "a glyph"),
                              (0, '<a:buNone/>', "no bullet at all")]))
    drop = _codes(parts)["flattened_bullet_formatting"]
    assert (drop["paragraphs"], drop["custom_char"], drop["suppressed"]) == (2, 1, 1)
    assert "auto_numbered" not in drop, (
        "the ordinal is no longer lost, so the code must not still carry a field "
        "counting it")


# ------------------------------------- the two cascades, driven rather than read
#
# `_ooxml_md` and this module resolve the bullet cascade with separate code, on
# purpose: one shared resolver would put the same bug on both sides of the gate and
# the delta would cancel. The cost of that choice is that they can DRIFT, and a drift
# is a faithful deck failing to publish — the worst outcome this project has.
#
# A review compared them line by line and found no divergence. That is not the same
# as evidence, and this project's own rule is that losslessness is measured: this
# drives every combination of the four cascade levels against a real conversion and
# asks the markdown reader who was right.

_CASCADE_DECLS = (("none", ""), ("auto", _AUTO), ("char", '<a:buChar char="»"/>'),
                  ("off", "<a:buNone/>"))


def _cascade_deck(own, shape, layout, master, lvl=0, ph="body", idx="1"):
    """One deck stating a bullet at each of the four cascade levels independently."""
    lvl_tag = "a:lvl%dpPr" % (lvl + 1)
    body = ('<a:p><a:pPr lvl="%d">%s</a:pPr><a:r><a:t>step one</a:t></a:r></a:p>'
            '<a:p><a:pPr lvl="%d">%s</a:pPr><a:r><a:t>step two</a:t></a:r></a:p>'
            % (lvl, own, lvl, own))
    shape_lst = ('<a:lstStyle><%s>%s</%s></a:lstStyle>' % (lvl_tag, shape, lvl_tag)
                 if shape else "")
    layout_lst = ('<a:lstStyle><%s>%s</%s></a:lstStyle>' % (lvl_tag, layout, lvl_tag)
                  if layout else "")
    parts = {
        "ppt/slides/slide1.xml":
            '<p:sld %s %s><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr>'
            '<p:ph type="%s" idx="%s"/></p:nvPr></p:nvSpPr><p:txBody>%s%s</p:txBody>'
            '</p:sp></p:spTree></p:cSld></p:sld>' % (P, A, ph, idx, shape_lst, body),
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="t/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
        "ppt/slideLayouts/slideLayout1.xml":
            '<p:sldLayout %s %s><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr>'
            '<p:ph type="%s" idx="%s"/></p:nvPr></p:nvSpPr><p:txBody>%s</p:txBody>'
            '</p:sp></p:spTree></p:cSld></p:sldLayout>' % (P, A, ph, idx, layout_lst),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="t/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        "ppt/slideMasters/slideMaster1.xml":
            '<p:sldMaster %s %s><p:cSld><p:spTree/></p:cSld><p:txStyles><p:bodyStyle>'
            '<%s>%s</%s></p:bodyStyle></p:txStyles></p:sldMaster>'
            % (P, A, lvl_tag, master, lvl_tag),
    }
    return parts


def _cascade_ids():
    import itertools
    return [(o, s, la, ma)
            for o, s, la, ma in itertools.product(_CASCADE_DECLS, repeat=4)]


@pytest.mark.parametrize("combo", _cascade_ids(),
                         ids=["%s>%s>%s>%s" % (c[0][0], c[1][0], c[2][0], c[3][0])
                              for c in _cascade_ids()])
def test_the_two_cascades_agree_on_every_combination(combo):
    """256 decks, one per (own, shape, layout, master) declaration. The converter
    renders each and `md_structure` reads the result back; this module states what it
    expects. A disagreement anywhere is a faithful deck that cannot publish."""
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    parts = _cascade_deck(*[c[1] for c in combo])
    truth = pptx_source_structure(parts)
    seen = md_structure(ooxml_markdown("pptx", parts, True))
    for fact in ("ordered_items", "bullet_items", "ordered_numbers", "list_items"):
        assert truth[fact] == seen[fact], (
            "%s: source says %s=%r, the rendered markdown shows %r\n%s"
            % ("/".join(c[0] for c in combo), fact, truth[fact], seen[fact],
               ooxml_markdown("pptx", parts, True)))


@pytest.mark.parametrize("lvl", [0, 1, 2, 4])
def test_the_two_cascades_agree_at_every_outline_level(lvl):
    """`a:lvl1pPr` is level 0 — the file counts from one and every reader here counts
    from zero. An off-by-one applies the wrong level's bullet, and would do it in ONE
    of the two implementations."""
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    parts = _cascade_deck("", "", "", _AUTO, lvl=lvl)
    truth = pptx_source_structure(parts)
    seen = md_structure(ooxml_markdown("pptx", parts, True))
    assert truth["ordered_items"] == seen["ordered_items"] == 2, (truth, seen)


@pytest.mark.parametrize("ph,idx", [("body", "1"), ("body", None), (None, "1"),
                                    (None, None), ("subTitle", "3")])
def test_the_two_cascades_agree_however_the_placeholder_is_declared(ph, idx):
    """A layout placeholder is matched by `(type, idx)`, then `(type, None)`, then the
    master's catch-all. A shape with a type and no idx, or an idx and no type, is
    ordinary in a real deck and is where two hand-written matchers drift."""
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    parts = _cascade_deck("", "", _AUTO, "", ph=ph or "body", idx=idx or "")
    truth = pptx_source_structure(parts)
    seen = md_structure(ooxml_markdown("pptx", parts, True))
    assert truth["ordered_items"] == seen["ordered_items"], (truth, seen)
    assert truth["bullet_items"] == seen["bullet_items"], (truth, seen)


def test_the_two_cascades_agree_inside_a_group_shape():
    """A `p:grpSp` nests shapes inside shapes. The converter RECURSES into it and the
    truth walks a flat stream deciding membership by looking UP a parent map, so a
    grouped shape is exactly where the two ways of finding "which placeholder am I
    in" can part company."""
    from backend.ingest import ooxml_markdown
    from backend.validate import md_structure
    inner = ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
             '</p:nvSpPr><p:txBody>'
             '<a:p><a:pPr lvl="0"/><a:r><a:t>step one</a:t></a:r></a:p>'
             '<a:p><a:pPr lvl="0"/><a:r><a:t>step two</a:t></a:r></a:p>'
             '</p:txBody></p:sp>')
    parts = _cascade_deck("", "", "", _AUTO)
    parts["ppt/slides/slide1.xml"] = (
        '<p:sld %s %s><p:cSld><p:spTree><p:grpSp><p:nvGrpSpPr><p:nvPr/>'
        '</p:nvGrpSpPr>%s</p:grpSp></p:spTree></p:cSld></p:sld>' % (P, A, inner))
    truth = pptx_source_structure(parts)
    seen = md_structure(ooxml_markdown("pptx", parts, True))
    for fact in ("ordered_items", "bullet_items", "ordered_numbers"):
        assert truth[fact] == seen[fact], (fact, truth[fact], seen[fact],
                                           ooxml_markdown("pptx", parts, True))
