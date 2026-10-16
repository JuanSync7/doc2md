"""
title: Integration — a bug in the converter must make a faithful document FAIL
kind: tests
layer: backend
summary: Fault injection into the converter's own readers; each one must move token recall off 1.0 instead of cancelling against a ground truth that shares it.
"""
# WHY THIS EXISTS. The losslessness gate is worth exactly as much as the independence
# of its two halves, and for the deck and workbook lanes it was worth much less than
# it looked: `pptx_source_text` and `xlsx_source_text` lived in the converter's module
# and called the converter's helpers, so a bug in one of them was applied to BOTH
# sides of the comparison, the error cancelled, and the gate reported a clean pass
# over real damage. Measured on the shipped corpus before the split:
#
#     bug _cell_value    (xlsx, both sides)  recall 1.0   n_source 107 -> 73
#     bug _sp_ph_type    (pptx, both sides)  recall 1.0   n_source 108 -> 20
#     bug _shared_strings(xlsx, both sides)  recall 1.0   n_source 107 -> 106
#     control: bug _md_cell (converter only) recall 0.40  valid False   <- caught
#
# That is the NimbusH1 mechanism inside the machinery built to prevent it. These tests
# are the standing proof that it stays fixed. They reach into `_ooxml_md` deliberately:
# the property under test IS the module boundary, so the test has to name both sides
# of it. Every other test in this repo goes through the package's public API.
#
# NOTE ON DIRECTION. Token recall asks "of the words the SOURCE has, how many
# survived?". It therefore catches a converter bug (the numerator falls) but CANNOT
# catch a ground-truth bug that merely deletes from the denominator — the question
# shrinks and the answer stays 1.0. That asymmetry is real, is why
# `n_source_tokens_min` was added to the eval expectations as a floor, and is pinned
# below so nobody mistakes recall for a two-way check.
import pytest

from backend.ingest import _ooxml_md, ooxml_markdown, ooxml_source_text
from backend.validate import conversion_report

pytestmark = pytest.mark.integration

SS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _workbook():
    return {
        "xl/workbook.xml":
            '<workbook %s xmlns:r="urn:r"><sheets>'
            '<sheet name="RegisterMap" sheetId="1" r:id="rId1"/></sheets></workbook>' % SS,
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="t" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml":
            '<sst %s><si><t>Register</t></si><si><t>ClkGateCtrl</t></si>'
            '<si><t>DmaArbiter</t></si></sst>' % SS,
        "xl/worksheets/sheet1.xml":
            '<worksheet %s><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c>'
            '<c r="B2"><v>16384</v></c><c r="C2" t="b"><v>1</v></c></row>'
            '<row r="3"><c r="A3" t="s"><v>2</v></c>'
            '<c r="B3"><v>20480</v></c><c r="C3" t="b"><v>0</v></c></row>'
            '</sheetData></worksheet>' % SS,
    }


def _deck():
    def sp(text, ph=None):
        decl = '<p:ph type="%s"/>' % ph if ph else ""
        return ('<p:sp><p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr><p:txBody>'
                '<a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>' % (decl, text))
    return {"ppt/slides/slide1.xml":
            '<p:sld %s %s><p:cSld><p:spTree>%s%s%s</p:spTree></p:cSld></p:sld>'
            % (P, A,
               sp("Escalation policy", "title"),
               sp("Page the on-call fabric engineer first"),
               sp("Nimbus Semiconductor Confidential", "ftr"))}


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _document():
    """A docx carrying a FOOTNOTE, because that is where the docx sharing was.

    The converter reads body paragraphs run by run, but renders the footnote
    section with `_text_of` — and `docx_source_text` used to call `_text_of` for
    that same part. So the shared surface was the notes and comments, not the body,
    and the fixture has to contain one for the injection to mean anything."""
    def p(text, style=None):
        pr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
        return ('<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                % (pr, text))
    body = (p("Clock domain crossing", "Heading1")
            + '<w:p><w:r><w:t>Every boundary needs a synchroniser</w:t></w:r>'
              '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
            + '<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>'
              % (p("Primary rota"), p("Standby rota")))
    return {"word/document.xml":
            '<w:document %s><w:body>%s</w:body></w:document>' % (W, body),
            "word/footnotes.xml":
            '<w:footnotes %s><w:footnote w:id="2"><w:p><w:r>'
            '<w:t>measured on silicon in week nine</w:t></w:r></w:p>'
            '</w:footnote></w:footnotes>' % W,
            "word/styles.xml":
            '<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/></w:style></w:styles>' % W}


def _measure(ext, parts):
    return conversion_report(ooxml_source_text(ext, parts), ooxml_markdown(ext, parts))


@pytest.mark.parametrize("ext,build", [("xlsx", _workbook), ("pptx", _deck),
                                       ("docx", _document)])
def test_the_healthy_document_is_lossless(ext, build):
    """The baseline the injections are measured against. Both readers reach the same
    answer by different routes, which is the only reason their agreement means
    anything."""
    rep = _measure(ext, build())
    assert rep["recall"] == 1.0 and rep["valid"] is True
    assert rep["n_source"] > 0, "a vacuous denominator is not a passing gate"


# Each row bugs ONE reader the CONVERTER uses, converts a faithful document, and
# demands the gate notice. Before the ground truths moved out of `_ooxml_md`, every
# one of these read `recall == 1.0`.
_CONVERTER_READERS = [
    ("xlsx", _workbook, "_cell_value",
     lambda orig: lambda c, shared: (lambda v: v[:4] if len(v) > 4 else v)(orig(c, shared)),
     "the cell reader truncates every value"),
    ("xlsx", _workbook, "_shared_strings",
     lambda orig: lambda xml: orig(xml)[:-1],
     "the shared-string table loses its last entry"),
    ("pptx", _deck, "_sp_ph_type",
     lambda orig: lambda sp: "ftr",
     "every shape is mistaken for page furniture"),
    # A deck's body text stopped coming from `_text_of` in P9.8a: a paragraph now
    # goes through `_pptx_para_md`, which reads RUNS so it can emit the emphasis and
    # the hyperlinks the lane used to drop. The injection moved with it — the row
    # asserts a property of whatever reader the converter actually uses, and pointing
    # it at a function the deck no longer calls would have kept it green for good.
    ("pptx", _deck, "_pptx_para_md",
     lambda orig: lambda p, links: orig(p, links)[:6],
     "the shape text reader truncates"),
    # docx: `_text_of` renders the footnote/endnote/comment sections, and
    # `docx_source_text` used to call it for those same parts. One word going
    # unread therefore went unread on BOTH sides and the gate saw nothing.
    ("docx", _document, "_text_of",
     lambda orig: lambda el, skip=None, value_locals=("t",): orig(
         el, skip, value_locals).replace("silicon ", ""),
     "one word of a footnote is silently not read"),
]


@pytest.mark.parametrize("ext,build,helper,bug,what", _CONVERTER_READERS)
def test_a_bug_in_a_converter_reader_fails_a_faithful_document(
        ext, build, helper, bug, what, monkeypatch):
    parts = build()
    assert _measure(ext, parts)["recall"] == 1.0, "precondition: healthy"
    monkeypatch.setattr(_ooxml_md, helper, bug(getattr(_ooxml_md, helper)))
    rep = _measure(ext, parts)
    assert rep["recall"] < 1.0 and rep["valid"] is False, (
        "%s (%s) and the gate still passed -- the ground truth is sharing this "
        "reader, so the same error landed on both sides and cancelled" % (helper, what))


def test_recall_cannot_see_a_ground_truth_that_stops_reading(monkeypatch):
    """The asymmetry, pinned so it is never mistaken for a two-way check.

    Deleting from the DENOMINATOR shrinks the question rather than failing it: every
    word the truth still claims is present, so recall is a vacuous 1.0. Only
    `n_source` moves. This is why `evals/expectations.json` pins an
    `n_source_tokens_min` floor per corpus document -- without it, a ground truth
    that quietly stopped reading half a deck would pass every gate the project has."""
    from backend.ingest import _pptx_struct
    parts = _deck()
    before = _measure("pptx", parts)
    monkeypatch.setattr(_pptx_struct, "_chrome_shapes",
                        lambda root, pmap: set(id(e) for e in root.iter()
                                               if _pptx_struct._local(e.tag) == "sp"))
    after = _measure("pptx", parts)
    assert after["recall"] == 1.0, "recall genuinely cannot see this"
    assert after["n_source"] < before["n_source"], (
        "but the denominator must visibly collapse, because that is the only "
        "signal there is")


# ======================================================================
# The SECOND gate's version of the same property.
#
# `_struct_common` holds the mechanism the three STRUCTURAL ground truths share:
# parent maps, ancestry, the token notion, the heading record, the locator elision.
# Sharing it is admissible only because all three read the SOURCE — they are three
# readers on ONE side. The other side is the converter and
# `backend.validate._mdstructure`, which reads the emitted markdown back the way a
# CommonMark renderer would.
#
# `test_ingest_struct_common.py` enforces that boundary structurally, by reading the
# real import statements. These two tests enforce it BEHAVIOURALLY, which is the half
# an import check cannot reach: they show what the shared helper's one-sidedness is
# actually worth, and what it would cost to give it up.

def _structure(ext, parts):
    from backend.ingest import (docx_source_structure, pptx_source_structure,
                                xlsx_source_structure)
    from backend.validate import md_structure, structure_fidelity_report
    truth = (docx_source_structure(parts) if ext == "docx" else
             xlsx_source_structure(parts, True) if ext == "xlsx" else
             pptx_source_structure(parts))
    # `emit_images=True` because `scripts/build_bundle.py` — the only path that runs
    # this gate — converts with it on, and `ooxml_markdown` is the layer that adds the
    # trailing `## Figures` section. Measuring one layer lower reads a heading short
    # and fails a faithful deck for a reason that has nothing to do with the test.
    return structure_fidelity_report(
        md_structure(ooxml_markdown(ext, parts, True)), truth)


_TRUTH_MODULE = {"docx": "_ooxml_struct", "pptx": "_pptx_struct",
                 "xlsx": "_xlsx_struct"}


def _bug_the_shared_tokeniser(monkeypatch, ext):
    """Injects the fault EVERYWHERE the shared tokeniser is actually resolved.

    This is the part that decides whether the test measures anything. The truths do
    ``from ._struct_common import _words``, which binds the function object at IMPORT
    time, so patching the attribute on `_struct_common` alone does NOT reach their own
    direct calls — it reaches only the calls made from inside `_struct_common` itself,
    where `_words` is still a module global looked up per call. Measured on the
    adversarial deck:

        patch `_struct_common` only       fail, deltas ['heading_path']
        patch it AND the truth's binding  fail, deltas ['heading_path',
                                                        'list_item_words', 'tables']

    Both are red, so the narrow version passes — for a real reason, but a smaller one
    than the claim above it. That is the same trap P9.6 hit from the other side
    (patching an export where the caller resolves a global OVER-reaches, and reports
    independence a truth does not have); the rule either way is that the injection
    target has to match how the caller resolves the name."""
    from backend.ingest import _struct_common
    import importlib
    real = _struct_common._words
    bug = lambda t: real(t)[:-1]                                  # noqa: E731
    monkeypatch.setattr(_struct_common, "_words", bug)
    truth = importlib.import_module("backend.ingest." + _TRUTH_MODULE[ext])
    monkeypatch.setattr(truth, "_words", bug)


@pytest.mark.parametrize("ext,build", [("xlsx", _workbook), ("pptx", _deck),
                                       ("docx", _document)])
def test_a_bug_in_the_shared_source_mechanism_fails_a_faithful_document(
        ext, build, monkeypatch):
    """The property that makes `_struct_common` safe, stated as a measurement.

    Every truth tokenises a heading's title with the SAME function now. If that
    function is wrong, all three are wrong together — and that is fine, because being
    wrong together on ONE SIDE is exactly what a gate is built to notice. The markdown
    side keeps its own tokeniser, so the two disagree and the document fails loudly.

    Measured over the nine shipped corpus documents at the time this was written: all
    nine pass, and all nine fail on `heading_path` under this injection — plus
    `list_item_words` and `tables` wherever the document has them, once the injection
    reaches the truth's own import-time binding as well (see the helper)."""
    parts = build()
    assert _structure(ext, parts)["gate"] == "pass", "the baseline must be faithful"
    _bug_the_shared_tokeniser(monkeypatch, ext)
    hurt = _structure(ext, parts)
    assert hurt["gate"] == "fail", (
        "a bug in the shared SOURCE-side tokeniser left the structure gate green -- "
        "something on the markdown side is sharing it, so the error cancelled")
    assert "heading_path" in set(d["fact"] for d in hurt["deltas"])


def test_sharing_the_tokeniser_with_the_markdown_side_would_cancel_the_delta(
        monkeypatch):
    """What it would cost to "tidy up" the two tokenisers into one, priced.

    They look interchangeable — both are `[a-z0-9]+` runs over lowercased text — and
    the markdown one differs only by stripping a link's URL and a `<br>` first,
    because it is reading markup. Unify them and the SAME bug lands on both sides:
    `heading_path`, which the one-sided injection above reports on every corpus
    document, goes SILENT. Not "still fails for another reason" — the fact that
    exists to catch two exchanged section titles stops disagreeing at all."""
    from backend.validate import _mdstructure
    parts = _deck()
    md = _mdstructure._words
    _bug_the_shared_tokeniser(monkeypatch, "pptx")
    one_sided = set(d["fact"] for d in _structure("pptx", parts)["deltas"])
    monkeypatch.setattr(_mdstructure, "_words", lambda t: md(t)[:-1])
    two_sided = set(d["fact"] for d in _structure("pptx", parts)["deltas"])
    assert "heading_path" in one_sided
    assert "heading_path" not in two_sided, (
        "the whole argument for keeping the two tokenisers separate is that this "
        "delta cancels when they are shared; if it no longer cancels, re-derive the "
        "boundary rather than deleting this test")
