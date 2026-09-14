"""
title: Unit — the warning vocabulary is closed in both directions
kind: tests
layer: backend
summary: Every documented warning code has an emitter, every emitted code is documented, and every code that stands for a loss carries the count behind it.
"""
# This test exists because of a specific failure. `dropped_headers_footers` was
# documented in the output contract and REQUIRED by end-goal.md §1 ("the drop is
# always deliberate and visible, never an accident") — and had zero emitters. A live
# run discarded a "Confidential — Project Kestrel" banner and reported
# `warnings: []`. Prose promised something no code delivered, and nothing noticed.
#
# So the vocabulary is now closed from both ends: a documented code with no emitter
# fails here, and so does an emitted code nobody documented.
import os
import re

import pytest

pytestmark = pytest.mark.unit

_HERE = os.path.abspath(__file__)                          # tests/unit/backend/<f>.py
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
SCHEMA = os.path.join(REPO, "docs", "reference", "output-schema.md")
SEARCH_ROOTS = ("src", "scripts")

# Codes a caller supplies rather than the code inventing — they are emitted by the
# PDF lane's toolchain probe and by docling, which this repo does not own. Listed
# rather than pattern-matched, so adding one is a deliberate act.
CALLER_SUPPLIED = frozenset((
    "pdf_toolchain", "pdf_content_loss", "pdf_text_layer_fallback",
    "ocr_transcription", "image_inline_bailed", "figure_text_debt",
))

# Codes that stand for CONTENT the reader does not get. Each must publish the size
# of the loss: "some spans were flattened" is a shrug, "6 horizontal, 1 vertical"
# is something an operator can act on.
#
# WIDENED DELIBERATELY, because the list was not the rule it looked like. It never
# named `cells`, `sheets` or `rows` — the count keys every workbook drop publishes —
# and the assertion below never noticed, because the fixture it ran over was a
# hand-built .docx and `xlsx_policy_drops` was never called. Nine codes were exempt
# from the one rule this file exists to enforce. The set is spelled out rather than
# loosened to a pattern: a permissive regex would happily accept `first` (a quoted
# locator) or `formats` (a list of names) as evidence of a size.
_COUNT_KEY = re.compile(r"^(parts|chars|cells|sheets|rows|shapes|runs|links|"
                        r"paragraphs|slides|horizontal|vertical|insertions|"
                        r"deletions|moves|source_tokens|removed|count|n_[a-z_]+)$")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _emitted_codes():
    """Every warning code any module can actually produce."""
    codes = set()
    for base in SEARCH_ROOTS:
        for root, _dirs, files in os.walk(os.path.join(REPO, base)):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                text = _read(os.path.join(root, fn))
                codes |= set(re.findall(r'"code":\s*"([a-z][a-z0-9_]+)"', text))
    return codes


def _documented_codes():
    """Codes named in the output schema's closed `warnings[]` vocabulary table.

    Anchored to that one section on purpose: matching every table in the document
    would sweep up `argv`, `anchor`, `alt` and every other first-column key, and a
    test that cannot fail is worse than no test."""
    doc = _read(SCHEMA)
    section = re.search(r"^### `warnings\[\]`$(.*?)(?=^#{1,3} |\Z)", doc,
                        re.S | re.M)
    assert section, "no `### `warnings[]`` section in %s" % SCHEMA
    return set(re.findall(r"^\|\s*`([a-z][a-z0-9_]+)`\s*\|", section.group(1), re.M))


def test_every_documented_warning_code_has_an_emitter():
    documented = _documented_codes()
    assert documented, "no warning-code table found in %s" % SCHEMA
    orphans = sorted(documented - _emitted_codes() - CALLER_SUPPLIED)
    assert not orphans, (
        "documented but never emitted — prose promising behaviour no code "
        "delivers, which is exactly how dropped_headers_footers hid: %s" % orphans)


def test_every_emitted_warning_code_is_documented():
    undocumented = sorted(_emitted_codes() - _documented_codes())
    assert not undocumented, (
        "emitted but undocumented — a reader meeting this code in a report has "
        "nowhere to look it up: %s" % undocumented)


def test_a_deliberate_drop_carries_the_size_of_the_loss():
    from backend.ingest import furniture_drops, policy_drops

    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    header = ('<w:hdr %s><w:p><w:r><w:t>Confidential — Project Kestrel</w:t>'
              '</w:r></w:p></w:hdr>' % W)
    table = ('<w:document %s><w:body><w:tbl>'
             '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="3"/></w:tcPr>'
             '<w:p><w:r><w:t>Group</w:t></w:r></w:p></w:tc></w:tr>'
             '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
             '<w:p><w:r><w:t>RW</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc></w:tr>'
             '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>'
             '<w:tc><w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>d</w:t></w:r></w:p></w:tc></w:tr>'
             '</w:tbl></w:body></w:document>' % W)

    records = furniture_drops({"word/header1.xml": header})
    records += policy_drops({"word/document.xml": table},
                            ["word/document.xml", "word/embeddings/sheet1.xlsx"])
    assert records, "the fixture drops things; nothing reported them"
    seen = set()
    for rec in records:
        seen.add(rec["code"])
        assert rec.get("detail"), "%s has no human detail" % rec["code"]
        counts = [k for k in rec if _COUNT_KEY.match(k)]
        assert counts, "%s names a loss but publishes no count" % rec["code"]
        assert any(rec[k] for k in counts), "%s reports a loss of nothing" % rec["code"]
    assert seen == {"dropped_headers_footers", "flattened_table_spans",
                    "dropped_embedded_objects"}, seen


def test_every_format_s_drops_carry_the_size_of_the_loss():
    """The same rule, over the formats the docx fixture above cannot reach.

    It ran on a hand-built `.docx` and therefore only ever graded three codes; the
    nine a workbook and a deck emit were exempt from the one contract this file
    exists to enforce, and `cells`/`sheets`/`rows` were not even in `_COUNT_KEY`.
    A rule that cannot reach two thirds of its subjects is not a rule."""
    from backend.ingest import pptx_policy_drops, xlsx_policy_drops

    A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' + A
         + ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
           'relationships"')
    deck = {
        "ppt/slides/slide1.xml":
            # The chrome shape carries ONLY chrome. Alt text, emphasis, a
            # hyperlink and a bullet declaration all sit on a body shape, because a
            # chrome shape's text is dropped whole — counting its bold runs as
            # "emphasis lost while every character is kept" would be false, and the
            # scans now exclude it.
            '<p:sld show="0" %s><p:cSld><p:spTree>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="f"/>'
            '<p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr><p:txBody>'
            '<a:p><a:r><a:t>Nimbus Confidential</a:t></a:r></a:p></p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="4" name="s" descr="A coverage plot"/>'
            '<p:nvPr/></p:nvSpPr><p:txBody>'
            # `a:buChar`, not `a:buAutoNum`: an auto-numbered step keeps its
            # ordinal now (P9.8b) and is graded as `ordered_items`, so it is no
            # longer a flattening. A custom glyph still is — markdown has one
            # bullet character and no way to ask for another.
            '<a:p><a:pPr><a:buChar char="\u00bb"/></a:pPr>'
            '<a:r><a:rPr b="1" strike="sngStrike">'
            '<a:hlinkClick r:id="rId7"/></a:rPr><a:t>the spec</a:t></a:r></a:p>'
            '<a:p><a:pPr lvl="2"/><a:r><a:t>a level with no parent</a:t></a:r></a:p>'
            '</p:txBody></p:sp>'
            '</p:spTree></p:cSld></p:sld>' % P,
        # An INTERNAL jump, not an external url. `pptx_markdown` emits an external
        # link as a real `[text](url)` (P9.8a), so an external one is no longer a
        # loss and no longer has a receipt; a slide-to-slide jump still loses its
        # destination and is what `dropped_shape_links` reports now.
        "ppt/slides/_rels/slide1.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId7" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slide4.xml"/>'
            '</Relationships>',
    }
    N = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    book = {
        "xl/workbook.xml":
            '<workbook %s><sheets><sheet name="Scratch" sheetId="1" state="hidden"/>'
            '</sheets></workbook>' % N,
        "xl/worksheets/sheet1.xml":
            '<worksheet %s><sheetData><row r="1"><c r="A1" s="1"><f>SUM(B1:B2)</f>'
            '<v>3</v></c><c r="B1" s="1"><f>1+1</f></c></row></sheetData>'
            '</worksheet>' % N,
        "xl/styles.xml":
            '<styleSheet %s><numFmts><numFmt numFmtId="200" formatCode="yyyy-mm-dd"/>'
            '</numFmts><fonts><font><b/><strike/></font></fonts>'
            '<cellXfs><xf numFmtId="0" fontId="0"/>'
            '<xf numFmtId="200" fontId="0" applyNumberFormat="1"/></cellXfs>'
            '</styleSheet>' % N,
    }
    records = pptx_policy_drops(deck) + xlsx_policy_drops(book)
    assert records, "the fixtures drop things; nothing reported them"
    for rec in records:
        assert rec.get("detail"), "%s has no human detail" % rec["code"]
        counts = [k for k in rec if _COUNT_KEY.match(k)]
        assert counts, "%s names a loss but publishes no count" % rec["code"]
        assert any(rec[k] for k in counts), "%s reports a loss of nothing" % rec["code"]
    seen = set(r["code"] for r in records)
    # `dropped_shape_emphasis` is deliberately absent: the deck converter emits bold,
    # italic and strikethrough now (P9.8a), so there is no loss left to report and the
    # code was retired rather than left firing over a faithful conversion. The same
    # three marks are graded per span by `structure_fidelity` instead.
    assert {"dropped_slide_chrome", "dropped_shape_links",
            "dropped_shape_alt_text", "flattened_bullet_formatting",
            "flattened_list_levels", "hidden_content_published"} <= seen, seen
    assert {"dropped_cell_formulas", "empty_cell_formulas"} <= seen, seen


def test_a_document_that_loses_nothing_reports_nothing():
    # The other half of the contract. A warning list that is never empty is a
    # warning list nobody reads.
    from backend.ingest import furniture_drops, policy_drops

    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    plain = ('<w:document %s><w:body><w:p><w:r><w:t>Just prose.</w:t></w:r></w:p>'
             '</w:body></w:document>' % W)
    assert furniture_drops({}) == []
    assert policy_drops({"word/document.xml": plain}, ["word/document.xml"]) == []
