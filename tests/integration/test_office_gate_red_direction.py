"""
title: Integration — the second gate fails when the converter breaks, on real documents
kind: tests
layer: backend
summary: Converter-fault injection over the shipped corpus: each bug must drive structure_fidelity to fail, and the ones token recall cannot see are the whole point.
"""
# WHY THIS EXISTS, AND WHY IT IS SHAPED LIKE THIS.
#
# A gate that has only ever been observed to PASS is not known to be a gate. Every
# earlier demonstration in this project of "a deck/workbook ships green" edited the
# SOURCE document and then observed `gate: pass` — which proves nothing, because the
# pipeline is SELF-PAIRED: `bundle_inputs` derives the markdown, the source text and
# the source structure from the same file, so a faithful conversion of a damaged
# source SHOULD pass. Damaging the input and watching the gate stay green measures
# the wrong thing.
#
# The right experiment is CONVERTER-FAULT INJECTION: break one helper the converter
# uses, convert the PRISTINE document, and demand the gate notice. That is the idiom
# `tests/unit/backend/test_structure_fidelity.py` already uses for docx, and this
# file extends it to the formats P9 is bringing under the gate — over the REAL corpus
# documents, so the fixtures cannot quietly diverge from what ships.
#
# The rows that matter most are the ones where `token_recall` stays at a clean 1.0.
# Those are the damages the FIRST gate is structurally incapable of seeing: the words
# are all still present, in the same quantities, and only their arrangement changed.
# Before P9.4 every one of them shipped `status: ok`.
import os

import pytest

from backend.ingest import (docx_source_structure, ooxml_markdown,
                            ooxml_source_text, pptx_source_structure,
                            xlsx_source_structure)
import backend.ingest._ooxml_md as ooxml
from backend.validate import (conversion_report, md_structure,
                              structure_fidelity_report)

pytestmark = pytest.mark.integration

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKBOOK = os.path.join(_REPO, "data", "eval_corpus", "office",
                        "kestrel-registers.xlsx")


def _parts(path, ext):
    import sys
    sys.path.insert(0, os.path.join(_REPO, "scripts"))
    from office_convert import read_parts
    return read_parts(path, ext)


@pytest.fixture(scope="module")
def workbook():
    if not os.path.exists(WORKBOOK):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(WORKBOOK, "xlsx")


def _both_gates(parts):
    """(token verdict, structure verdict) over one conversion of ``parts``."""
    md = ooxml_markdown("xlsx", parts)
    token = conversion_report(ooxml_source_text("xlsx", parts), md)
    structure = structure_fidelity_report(md_structure(md),
                                          xlsx_source_structure(parts))
    return token, structure


def test_the_shipped_workbook_passes_both_gates(workbook):
    """The baseline every injection is measured against. Two implementations that
    share no traversal reach the same facts by different routes — which is the only
    reason their agreement is worth anything."""
    token, structure = _both_gates(workbook)
    assert token["recall"] == 1.0 and token["valid"] is True
    assert structure["gate"] == "pass" and structure["deltas"] == []
    assert structure["compared"] >= 4, "a gate that graded nothing is not a pass"


# Each row breaks ONE helper the xlsx converter uses. `blind` records whether the
# TOKEN gate can see the damage — every `True` is a class that shipped `status: ok`
# before this slice, and is the reason the second gate exists.
_INJECTIONS = [
    ("_col_index", lambda orig: (lambda ref: -1),
     ["tables"], True,
     "every cell loses its address, so a sparse row shifts left and values land "
     "under the wrong column heading"),
    ("_cell_value", lambda orig: (lambda c, shared: {
        "255": "3735928559", "3735928559": "255"}.get(orig(c, shared), orig(c, shared))),
     ["tables"], True,
     "two registers exchange their reset values"),
    ("_workbook_sheets", lambda orig: (lambda parts: list(reversed(orig(parts)))),
     ["heading_path", "tables", "block_sequence"], True,
     "the workbook's tabs are published in the wrong order"),
    ("_workbook_sheets", lambda orig: (lambda parts: [("Sheet?", part)
                                                      for _, part in orig(parts)]),
     ["heading_path"], False,
     "every tab loses its name"),
    ("_gfm_table", lambda orig: (lambda rows, min_width=0: orig(rows[:-1], min_width)),
     ["tables", "block_sequence"], False,
     "the last row of every sheet is dropped"),
]


@pytest.mark.parametrize("helper,bug,facts,token_blind,what",
                         _INJECTIONS,
                         ids=[("%s-%s" % (r[0], r[4][:28])) for r in _INJECTIONS])
def test_a_broken_converter_fails_the_structure_gate(
        workbook, helper, bug, facts, token_blind, what, monkeypatch):
    before, _ = _both_gates(workbook)
    assert before["recall"] == 1.0, "precondition: the pristine workbook is lossless"

    monkeypatch.setattr(ooxml, helper, bug(getattr(ooxml, helper)))
    token, structure = _both_gates(workbook)

    assert structure["gate"] == "fail", (
        "%s (%s) and the structure gate still passed" % (helper, what))
    assert [d["fact"] for d in structure["deltas"]] == facts, (
        "%s: expected %s to disagree, got %s"
        % (helper, facts, [d["fact"] for d in structure["deltas"]]))

    if token_blind:
        assert token["recall"] == 1.0 and token["valid"] is True, (
            "this row is recorded as invisible to token recall; if the token gate "
            "now catches it, the row's claim is stale and should be re-measured")


def test_the_token_gate_really_is_blind_to_arrangement(workbook):
    """Stated as its own claim rather than left implicit in the table above: three
    of the five injections leave `token_recall` at a perfect 1.0. Every word is
    still present, in the same quantity — only which column, which sheet and which
    order changed. That is precisely the class `structure_fidelity` was built for,
    and before this slice a workbook had no second implementation to notice it."""
    blind = [row for row in _INJECTIONS if row[3]]
    assert len(blind) == 3, "the count is part of the claim; re-measure before editing"


# ==========================================================================
# The deck: what protects it, and the honest limit of that (quality-plan P9.5)
# ==========================================================================
#
# This section was written in P9.5, when the deck had no structural ground truth and
# its injections could only be OBSERVED rather than graded — `structure_fidelity`
# reported `unmeasured`, so a wrong slide order was caught by nothing at all. P9.6
# supplied the second reader, and the section now grades the deck exactly as the
# workbook above is graded. What P9.5 pinned as the minimum a truth would have to
# supply — `block_sequence`, `heading_path`, `list_item_words` — is what the first
# row below now measures being supplied.

DECK = os.path.join(_REPO, "data", "eval_corpus", "office", "kestrel-overview.pptx")
REORDERED = os.path.join(_REPO, "data", "eval_corpus", "office",
                         "kestrel-reordered.pptx")


@pytest.fixture(scope="module")
def deck():
    if not os.path.exists(DECK):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(DECK, "pptx")


@pytest.fixture(scope="module")
def reordered():
    if not os.path.exists(REORDERED):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(REORDERED, "pptx")


def _deck_facts(parts):
    """(fact vector, token verdict) for one conversion of a real deck."""
    md = ooxml_markdown("pptx", parts)
    return md_structure(md), conversion_report(ooxml_source_text("pptx", parts), md)


def test_the_shipped_deck_passes_the_token_gate_and_has_the_facts_we_pin(deck):
    facts, token = _deck_facts(deck)
    assert token["valid"] and token["recall"] == 1.0
    assert facts["thematic_breaks"] == 0
    assert facts["bullet_items"] == 16
    assert facts["headings"] == {2: 5, 3: 1}


def test_removing_the_leading_escape_damages_a_pristine_deck(deck, monkeypatch):
    """CONVERTER-FAULT INJECTION, the only valid direction here: the DOCUMENT is the
    shipped one, and the converter is what breaks.

    `_esc_block_start` reduced to `_esc` is exactly the converter that shipped before
    this slice. Feed it a deck holding one bullet that opens a block and the bullet
    is gone from the rendered document while token recall stays at a clean 1.0 —
    which is why the escape, not a gate, is what closes this."""
    baseline, _tok = _deck_facts(deck)
    poisoned = dict(deck)
    name = "ppt/slides/slide2.xml"
    xml = poisoned[name]
    i = xml.index("<a:t>", xml.index('type="body"'))
    poisoned[name] = xml[:i + 5] + "- - -" + xml[xml.index("</a:t>", i):]

    fixed, fixed_token = _deck_facts(poisoned)
    assert fixed["thematic_breaks"] == 0, "a faithful conversion invents no break"
    assert fixed["bullet_items"] == baseline["bullet_items"]

    # The deck lane escapes INLINE per run now (that is what lets it emit real
    # emphasis), so the line-leading half lives in `_esc_block_start_md` and reducing
    # it to identity is the converter that shipped before the escape existed.
    monkeypatch.setattr(ooxml, "_esc_block_start_md", lambda md: md)
    broken, broken_token = _deck_facts(poisoned)
    assert broken["thematic_breaks"] == 1
    assert broken["bullet_items"] == baseline["bullet_items"] - 1
    # BOTH gates certify the damage. That is the whole finding.
    assert broken_token["valid"] and broken_token["recall"] == 1.0
    assert fixed_token["valid"] and fixed_token["recall"] == 1.0


def test_a_reordered_deck_publishes_in_the_decks_order(reordered):
    heads = [l for l in ooxml_markdown("pptx", reordered).split("\n")
             if l.startswith("## ")]
    assert heads == ["## Slide 1 — Kestrel bring-up sequence",
                     "## Slide 2 — Rollout plan",
                     "## Slide 3 — Silicon back",
                     "## Slide 4 — Board bring-up",
                     "## Slide 5 — Open questions"]


def test_breaking_the_order_reader_moves_a_document_no_gate_is_watching(
        reordered, monkeypatch):
    """The honest red-direction result for defect 4, as a measurement.

    With `pptx_slide_order` broken, the shipped deck publishes its slides in the
    order their FILES are named — a different document, saying the bring-up steps
    happen in a different sequence. Every number the TOKEN gate reports is
    identical, and that is structural rather than accidental: it compares
    multisets, and a permutation does not change one.

    The fact vector, though, is NOT blind — three of its sixteen facts move. That
    is the useful half of this result: slide order is already expressible in facts
    that exist, so P9.6 can grade it as soon as a pptx ground truth supplies them.
    What is missing today is a second reader, not an instrument. Pinning WHICH
    facts move is what tells P9.6 the minimum it has to supply, and pinning which
    do NOT is what stops a later change from quietly claiming more reach than it
    has (`headings` counts levels, so a permutation leaves it alone — a truth that
    supplied only `headings` would grade a reordered deck green)."""
    good_md = ooxml_markdown("pptx", reordered)
    good_facts, good_token = _deck_facts(reordered)

    monkeypatch.setattr(ooxml, "pptx_slide_order", lambda parts: [])
    bad_md = ooxml_markdown("pptx", reordered)
    bad_facts, bad_token = _deck_facts(reordered)

    assert bad_md != good_md, "the injection must actually change the document"
    assert "## Slide 2 — Open questions" in bad_md

    # The first gate: blind, and not by oversight.
    assert bad_token["recall"] == good_token["recall"] == 1.0
    assert bad_token["valid"] and good_token["valid"]
    assert bad_token["n_source"] == good_token["n_source"]

    moved = sorted(k for k in good_facts if good_facts[k] != bad_facts[k])
    assert moved == ["block_sequence", "heading_path", "list_item_words"], moved
    # And now a gate reads all three. In P9.5 this line read
    # `structure_fidelity_report(bad_facts, {})["gate"] == "unmeasured"` — the three
    # facts were computed, correct, and thrown away, because the format had no second
    # reader. The truth is graded against the PRISTINE deck's parts, which is the
    # whole experiment: the document is faithful and the converter is not.
    verdict = structure_fidelity_report(bad_facts, pptx_source_structure(reordered))
    assert verdict["gate"] == "fail"
    assert sorted(d["fact"] for d in verdict["deltas"]) == moved
    assert structure_fidelity_report(
        good_facts, pptx_source_structure(reordered))["gate"] == "pass"


def test_a_reordered_deck_keeps_each_slides_own_notes(reordered):
    """Position and part number are two different numbers. The notes hang off part
    five, which is position two; a lookup that builds `notesSlide<position>.xml`
    attaches them to the wrong slide with nothing in any report moving."""
    md = ooxml_markdown("pptx", reordered)
    here = md.index("## Slide 2 — Rollout plan")
    nxt = md.index("## Slide 3 — Silicon back")
    assert here < md.index("part five and position two") < nxt


def test_the_notes_title_escape_is_exercised_by_a_shipped_document(reordered):
    """Not by a unit fixture written from the same understanding as the code. The
    deck's notes title opens with `2. `, which unescaped is a list marker that
    swallows the line — measured `recall: 0.990, valid: False`, a FAITHFUL deck
    refusing to publish."""
    md = ooxml_markdown("pptx", reordered)
    assert "2\\. Timing for the rollout slide" in md
    assert conversion_report(ooxml_source_text("pptx", reordered), md)["valid"]


# ==========================================================================
# The deck, graded (quality-plan P9.6)
# ==========================================================================


def _deck_gates(parts):
    """(token verdict, structure verdict) over one conversion of a deck."""
    md = ooxml_markdown("pptx", parts)
    token = conversion_report(ooxml_source_text("pptx", parts), md)
    structure = structure_fidelity_report(md_structure(md),
                                          pptx_source_structure(parts))
    return token, structure


def test_the_shipped_decks_pass_both_gates(deck, reordered):
    for parts in (deck, reordered):
        token, structure = _deck_gates(parts)
        assert token["recall"] == 1.0 and token["valid"] is True
        assert structure["gate"] == "pass" and structure["deltas"] == []
        assert structure["compared"] >= 4, "a gate that graded nothing is not a pass"


# One helper per row, chosen so the injection reaches the converter as a GLOBAL
# lookup at call time. Patching an exported symbol instead is the trap that makes
# this whole experiment lie: a module that bound the name at import keeps the
# original function object, so both a shared and an independent reader would appear
# to fail and the test would report "independence proven" over a truth that has
# none.
_DECK_INJECTIONS = [
    ("_rel_id", "reordered",
     lambda orig: (lambda el: next((v for k, v in el.attrib.items()
                                    if k.rsplit("}", 1)[-1] == "id"), "")),
     ["block_sequence", "heading_path", "list_item_words"], True,
     "the deck's own order is read off the wrong attribute, so the slides publish "
     "in the order they were DRAFTED under headings claiming the presented one"),
    ("_sp_ph_type", "overview", lambda orig: (lambda sp: ""),
     ["block_sequence", "bullet_items", "heading_path", "list_item_words",
      "list_items"], True,
     "no shape is a title any more, so every slide heading loses its name and the "
     "title text reappears as a bullet"),
    ("_pptx_txbody_paras", "overview",
     lambda orig: (lambda c, links=None, inh=None: [
         (0, t, b) for _lvl, t, b in orig(c, links, inh)]),
     ["block_sequence", "list_items"], True,
     "the outline is flattened: every sub-bullet becomes a peer of its parent"),
    ("_pptx_txbody_paras", "overview",
     lambda orig: (lambda c, links=None, inh=None: (
         lambda got: [got[1], got[0]] + got[2:] if len(got) >= 2 else got)(
             orig(c, links, inh))),
     ["block_sequence", "list_item_words", "list_items"], True,
     "two bullets exchange places on every slide"),
    ("_pptx_table_md", "overview",
     lambda orig: (lambda tbl: "\n".join(orig(tbl).split("\n")[:-1])),
     ["block_sequence", "tables"], False,
     "the last row of the latency table is dropped"),
    ("_pptx_notes_blocks", "overview",
     lambda orig: (lambda parts, name, blocks: None),
     ["block_sequence", "bullet_items", "heading_path", "headings",
      "list_item_words", "list_items"], False,
     "the speaker-notes section is never opened"),
]


@pytest.mark.parametrize("helper,which,bug,facts,token_blind,what",
                         _DECK_INJECTIONS,
                         ids=[("%s-%s" % (r[0], r[5][:28]))
                              for r in _DECK_INJECTIONS])
def test_a_broken_deck_converter_fails_the_structure_gate(
        deck, reordered, helper, which, bug, facts, token_blind, what, monkeypatch):
    parts = {"overview": deck, "reordered": reordered}[which]
    before, _ = _deck_gates(parts)
    assert before["recall"] == 1.0, "precondition: the pristine deck is lossless"

    monkeypatch.setattr(ooxml, helper, bug(getattr(ooxml, helper)))
    token, structure = _deck_gates(parts)

    assert structure["gate"] == "fail", (
        "%s (%s) and the structure gate still passed" % (helper, what))
    assert sorted(d["fact"] for d in structure["deltas"]) == facts, (
        "%s: expected %s to disagree, got %s"
        % (helper, facts, sorted(d["fact"] for d in structure["deltas"])))

    if token_blind:
        assert token["recall"] == 1.0 and token["valid"] is True, (
            "this row is recorded as invisible to token recall; if the token gate "
            "now catches it, the row's claim is stale and should be re-measured")


def test_the_token_gate_is_blind_to_four_of_the_six_deck_damages():
    """Stated as its own claim. Four of the six injections above leave
    `token_recall` at a perfect 1.0 — every word present, in the same quantity, and
    only the arrangement changed. Every one of them shipped `status: ok` before
    P9.6, because the deck had no second reader at all."""
    blind = [row for row in _DECK_INJECTIONS if row[4]]
    assert len(blind) == 4, "the count is part of the claim; re-measure before editing"


def test_sharing_the_converters_order_reader_would_cancel_the_first_row(
        reordered, monkeypatch):
    """The measurement behind P9.5's rule, kept executable rather than asserted.

    A ground truth that imported `pptx_slide_order` instead of deriving the order
    again would sit on BOTH sides of the comparison it feeds: the converter's bug
    reorders the markdown, the same bug reorders the truth, the delta cancels and
    the gate reports `pass` over a deck nobody presented. The `_rel_id` row above
    is that exact bug, so the two verdicts side by side are the whole argument."""
    from backend.ingest import pptx_slide_order

    def _shared_truth(parts):
        """The lazy truth: same facts, but the ORDER comes from the converter."""
        import backend.ingest._pptx_struct as mod
        real = mod._slide_order
        try:
            mod._slide_order = pptx_slide_order
            return pptx_source_structure(parts)
        finally:
            mod._slide_order = real

    monkeypatch.setattr(ooxml, "_rel_id", lambda el: next(
        (v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == "id"), ""))
    facts = md_structure(ooxml_markdown("pptx", reordered))
    assert structure_fidelity_report(facts, _shared_truth(reordered))["gate"] == "pass"
    assert structure_fidelity_report(
        facts, pptx_source_structure(reordered))["gate"] == "fail"


# ==========================================================================
# The facts that stopped being `unmeasured` (quality-plan P9.8)
# ==========================================================================
#
# Emphasis, hyperlinks and a deck's ordinals were OMITTED from the two structural
# truths — never stated as zero — for as long as the converters dropped them. A
# stated `strong: 0` over a deck that draws bold would have certified the loss on
# the very axis the truth exists to police. They are real counts now, and a fact
# that is stated has to be a fact that can FAIL: the rows below bug the converter
# on the pristine ADVERSARIAL fixtures (the only corpus documents that carry these
# constructs) and demand the gate name the delta.
#
# Every one of them is invisible to token recall by construction. A marker is not a
# token the document stores — it is drawn — and a URL is markup on NEITHER side, so
# `recall` reads a clean 1.0 over all six.

ADV_DECK = os.path.join(_REPO, "data", "eval_corpus", "office",
                        "kestrel-adversarial.pptx")
ADV_BOOK = os.path.join(_REPO, "data", "eval_corpus", "office",
                        "kestrel-adversarial.xlsx")


@pytest.fixture(scope="module")
def adv_deck():
    if not os.path.exists(ADV_DECK):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(ADV_DECK, "pptx")


@pytest.fixture(scope="module")
def adv_book():
    if not os.path.exists(ADV_BOOK):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(ADV_BOOK, "xlsx")


def _book_gates(parts):
    md = ooxml_markdown("xlsx", parts)
    token = conversion_report(ooxml_source_text("xlsx", parts), md)
    structure = structure_fidelity_report(md_structure(md),
                                          xlsx_source_structure(parts))
    return token, structure


# Each row names a converter helper the deck path looks up as a GLOBAL at call
# time — the rule the whole file obeys, because patching an exported symbol a module
# bound at import would make an independent truth appear to fail too.
_P98_DECK = [
    ("_dml_marks", lambda orig: (lambda rpr: ()),
     ["em", "strike", "strong"],
     "the run-properties reader stops seeing emphasis, so every bold, italic and "
     "struck span in the deck is published as plain text"),
    ("_dml_link", lambda orig: (lambda rpr, links: ""),
     ["links"],
     "the hyperlink reader stops resolving, so a link is published as its bare "
     "display text and the destination is gone"),
    # NOT "make every marker read 1.", which was the first version of this row and
    # is not damage at all: CommonMark RENUMBERS a list from its first marker, so
    # `1. a / 1. b` and `1. a / 2. b` are the same document to a reader, the truth
    # says [1, 2] for both, and the gate is right to pass. `ordered_numbers` compares
    # what the reader SEES, so only a change to that is a change. The START is what
    # a reader sees, and it is what a wrong `startAt` would really move.
    ("_pptx_step", lambda orig: (lambda nums, lvl, start, reset=False:
                                 orig(nums, lvl, start, reset) + 10),
     ["ordered_numbers"],
     "the procedure begins at step 11, so a runbook's first instruction reads as "
     "its eleventh and an operator resuming one skips ten steps that do not exist"),
    ("_resolve_bullet", lambda orig: (lambda own, lvls, lvl, ph, inh: None),
     ["bullet_items", "ordered_items", "ordered_numbers"],
     "the bullet cascade resolves to nothing, so an auto-numbered procedure "
     "publishes as undifferentiated dashes"),
]


@pytest.mark.parametrize("helper,bug,facts,what", _P98_DECK,
                         ids=[r[0] for r in _P98_DECK])
def test_a_broken_deck_run_reader_fails_the_structure_gate(
        adv_deck, helper, bug, facts, what, monkeypatch):
    before, base = _deck_gates(adv_deck)
    assert before["recall"] == 1.0 and base["gate"] == "pass", "precondition"

    monkeypatch.setattr(ooxml, helper, bug(getattr(ooxml, helper)))
    token, structure = _deck_gates(adv_deck)

    assert structure["gate"] == "fail", "%s (%s) and the gate passed" % (helper, what)
    assert sorted(set(d["fact"] for d in structure["deltas"])) == facts, (
        "%s: expected %s, got %s"
        % (helper, facts, sorted(set(d["fact"] for d in structure["deltas"]))))
    assert token["recall"] == 1.0 and token["valid"] is True, (
        "a marker is drawn, not stored, and a URL is markup on neither side — if "
        "the token gate now catches this, the claim above is stale")


_P98_BOOK = [
    ("_cell_fonts", lambda orig: (lambda xml: {}),
     ["strike", "strong"],
     "the style chain stops resolving, so a bold header row and a struck-through "
     "cancelled row are published as ordinary text"),
]


@pytest.mark.parametrize("helper,bug,facts,what", _P98_BOOK,
                         ids=[r[0] for r in _P98_BOOK])
def test_a_broken_workbook_style_reader_fails_the_structure_gate(
        adv_book, helper, bug, facts, what, monkeypatch):
    before, base = _book_gates(adv_book)
    assert before["recall"] == 1.0 and base["gate"] == "pass", "precondition"

    monkeypatch.setattr(ooxml, helper, bug(getattr(ooxml, helper)))
    token, structure = _book_gates(adv_book)

    assert structure["gate"] == "fail", "%s (%s) and the gate passed" % (helper, what)
    assert sorted(set(d["fact"] for d in structure["deltas"])) == facts, (
        "%s: expected %s, got %s"
        % (helper, facts, sorted(set(d["fact"] for d in structure["deltas"]))))
    assert token["recall"] == 1.0 and token["valid"] is True


# ==========================================================================
# Two adjacencies markdown cannot write without help (quality-plan P9.9)
# ==========================================================================
#
# Both were PRE-EXISTING and both were publish-blockers on ordinary Word:
#
#   `**Dma**Arbiter`     emphasis ending mid-word. `_words` on the MARKDOWN side
#                        tokenises `[a-z0-9]+` runs, so the markers separated what
#                        the source correctly reads as one token `dmaarbiter`.
#                        `list_item_words` disagreed at `token_recall: 1.0`.
#   `***read***`+`*only*` two adjacent spans. Four asterisks is ONE em span to
#                        CommonMark, and the text layer mis-paired it too, so this
#                        one moved `em` AND took recall off 1.0.
#
# Neither construct existed anywhere in this corpus before P9.9, so deleting either
# fix left all fifteen documents byte-identical and the eval fully green. The
# adversarial .docx carries both now, in a LIST ITEM — `list_item_words` is the only
# fact that grades text word by word, and in a paragraph the mid-word case is
# invisible to the gate entirely (measured: it passed).

ADV_DOC = os.path.join(_REPO, "data", "eval_corpus", "office",
                       "kestrel-adversarial.docx")


@pytest.fixture(scope="module")
def adv_doc():
    if not os.path.exists(ADV_DOC):
        pytest.skip("eval corpus not generated: run python3 evals/gen_corpus.py")
    return _parts(ADV_DOC, "docx")


def _doc_gates(parts):
    md = ooxml_markdown("docx", parts)
    token = conversion_report(ooxml_source_text("docx", parts), md)
    structure = structure_fidelity_report(md_structure(md),
                                          docx_source_structure(parts))
    return token, structure


def test_the_adversarial_document_carries_both_adjacencies(adv_doc):
    md = ooxml_markdown("docx", adv_doc)
    assert "**Dma**Arbiter" in md, "the mid-word case is not in the fixture"
    assert "***read***<!---->*only*" in md, "the adjacent-span case is not in it"
    token, structure = _doc_gates(adv_doc)
    assert token["recall"] == 1.0 and structure["gate"] == "pass"


def test_without_the_span_separator_the_document_cannot_publish(adv_doc, monkeypatch):
    """`***read***` written straight against `*only*` is a delimiter run of four
    asterisks. Both gates move, which is unusual and is why this one was found: the
    structure gate names `em`, and the TEXT layer mis-pairs the same run, leaving a
    literal `*only*` that costs a token."""
    monkeypatch.setattr(ooxml, "_needs_separator", lambda left, right: False)
    token, structure = _doc_gates(adv_doc)
    assert structure["gate"] == "fail"
    assert "em" in set(d["fact"] for d in structure["deltas"])
    assert token["recall"] < 1.0


def test_without_the_marker_strip_a_mid_word_bold_cannot_publish(adv_doc, monkeypatch):
    """The classic shape: `token_recall` reads a perfect 1.0 — every character is
    present and in the same order — while the document is refused. A marker is
    markup, and markup does not split a word."""
    from backend.validate import _mdstructure
    monkeypatch.setattr(_mdstructure, "_strip_markers", lambda text: text)
    token, structure = _doc_gates(adv_doc)
    assert structure["gate"] == "fail"
    assert "list_item_words" in set(d["fact"] for d in structure["deltas"])
    assert token["recall"] == 1.0, "this one is invisible to the token gate"


def test_the_separator_must_vanish_from_the_text_layer(adv_doc, monkeypatch):
    """It stands between two halves of ONE word, so a space there splits a token the
    source holds whole. Only the token gate can see this, and it does."""
    import re as _re
    from backend.ingest import _markdown
    monkeypatch.setattr(_markdown, "_EMPTY_COMMENT", _re.compile(r"(?!x)x"))
    token, _structure = _doc_gates(adv_doc)
    assert token["recall"] < 1.0
