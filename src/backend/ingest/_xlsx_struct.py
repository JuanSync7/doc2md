"""
title: Converter-blind ground truth for xlsx
layer: backend
public_api: yes
summary: Every value a workbook holds, resolved by its own typed reader over every worksheet part — the second opinion the token gate compares against.
"""
# WHY THIS FILE EXISTS, AND WHY IT SHARES NOTHING WITH THE CONVERTER
#
# This is the module the measurement pointed at. `xlsx_source_text` used to live in
# the converter's own module and call `_cell_value` — the SAME function
# `xlsx_markdown` calls to decide what a cell says. Its docstring correctly boasted
# that it enumerates worksheet parts directly rather than through the workbook rels,
# "so a resolution bug on that side shows up as recall < 1.0 instead of zeroing both
# sides". That was true about GEOMETRY and false about VALUES: on the value itself it
# was the same reader twice, so a bug in it made both halves wrong in the same
# direction and the gate cancelled to a clean pass.
#
# Measured on the shipped `kestrel-registers.xlsx`, before this module existed:
#
#     healthy                                  recall=1.0  n_source=107  valid=True
#     bug _cell_value (both sides)             recall=1.0  n_source= 73  valid=True
#     bug _shared_strings (both sides)         recall=1.0  n_source=106  valid=True
#     control: bug _md_cell (converter only)   recall=0.40 n_source=107  valid=False
#
# A third of a workbook's source text vanishing at `token_recall: 1.0` is the
# NimbusH1 mechanism, live, inside the machinery built to prevent it. The control row
# is the contrast: a one-sided bug makes a FAITHFUL document fail loudly.
#
# THE RULE: a helper may be shared iff it is called on exactly one side of the
# comparison it feeds. From `_ooxml_leaf` this module takes only leaf primitives and
# `_SKIP_LOCALS` (a declaration of scope, not a reading of content). The typed cell
# resolver, the shared-string table, the column arithmetic and the sheet enumeration
# are all written out again here on purpose. Do not "deduplicate" them.
#
# WHAT THIS READER DOES DIFFERENTLY, concretely:
#   * VALUES come from every `xl/worksheets/*.xml` part directly, never through the
#     workbook's relationship graph — so a rels-resolution bug in the converter shows
#     up as a delta instead of zeroing both sides. (Kept from the previous
#     implementation, which got this half right.)
#   * The TYPE branch is re-derived from the schema's own vocabulary rather than
#     copied, including the `e` (error) and `str` (formula string) types the
#     converter's fall-through happens to handle by accident rather than by decision.
#   * Sheet NAMES are read by a flat scan filtered on the parent being `<sheets>`,
#     so a `<sheet>` element somewhere else in the workbook part cannot be mistaken
#     for a tab.
import re

from ._ooxml_leaf import _local, _root, _WS, _SKIP_LOCALS
# Mechanism shared with the OTHER TWO SOURCE-SIDE TRUTHS and with nothing else — see
# that module's header for why three readers on one side of a comparison may share
# and the converter may not.
from ._struct_common import (_add_heading as _add_heading_at, _parent_map,
                             _some, _words)


def _add_heading(out, text):
    # type: (dict, str) -> None
    """One level-2 heading — every section a workbook renders is an ``##``.

    The level is this FORMAT's claim, not shared machinery: a workbook has no
    construct that renders as anything else, so the 2 is stated here and the shared
    recorder is handed it. Keeping the claim local is the same discipline that keeps
    each truth's stated zeros out of `_struct_common`."""
    _add_heading_at(out, 2, text)


def _attr(el, local):
    # type: (object, str) -> str
    """Attribute value by LOCAL attribute name (``r:id``/``w:val`` -> ``id``/``val``).

    Written out in every reader rather than shared, and the six lines are the point.
    An attribute read is where a cell's ADDRESS comes from, so a shared one is
    called on both sides of the comparison: measured, a shared `_attr` that lost
    every `@r` shifted every value into a different column while `token_recall`,
    `n_source_tokens`, `compared` and the delta list all stayed exactly as they
    were. Nothing in the report moved. Four copies of five lines buys the one thing
    the second gate exists for."""
    for key, value in el.attrib.items():
        if key.rsplit("}", 1)[-1] == local:
            return value
    return ""

__all__ = ["xlsx_source_text", "xlsx_source_structure", "xlsx_policy_drops"]

_SHEET_PART = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_MEDIA_SVG = re.compile(r"^xl/media/[^/]+\.svg$")
# What makes a relationship a PICTURE: the declared type, or a raster/
# metafile extension on its target. Same test the converter applies.
_IMAGE_EXT = re.compile(r"\.(png|jpe?g|gif|bmp|tiff?|emf|wmf|webp)$", re.I)
# A cell reference: the LETTERS are the fact. A reader that took a row's width from
# how many `<c>` elements it holds publishes every value one column left of where
# the workbook puts it, and the docx `w:gridBefore` scar says the ground truth was
# blind in exactly the same place, so the gate agreed with the bug.
# A cell's OWN `r` attribute. The `$` absolute markers belong to formulas and
# defined names and Excel does not write them here, but a producer that does is
# still SAYING which column the value is in — so both readers honour it.
#
# An earlier revision of this slice went the other way and made this pattern strict
# to match the converter's. That was the anti-pattern this module exists to avoid:
# it encoded a CONVERTER LIMITATION as a document fact, and the measured cost was a
# green gate over misplaced data — `<c r="$C$2">` fell back to append order and
# published 182.5 under the `Notes` heading while the row below published the same
# quantity under `mW`, at `token_recall: 1.0` with no delta. The right fix was to
# teach the CONVERTER the ref, which is what `_ooxml_md._CELL_REF` now does.
_CELL_REF = re.compile(r"^\$?([A-Z]+)\$?\d+$")
_DRAWING = re.compile(r"^xl/drawings/drawing\d+\.xml$")
_COMMENTS = re.compile(r"^xl/comments\d*\.xml$")
_CHART = re.compile(r"^xl/charts/chart(?:Ex)?\d+\.xml$")

_TEXT_LOCALS = ("t", "text")
_BREAKS = ("br", "tab", "cr")


def _walk_text(root, value_locals=()):
    # type: (object, tuple) -> str
    """Every character under ``root``, flat pre-order, skipped subtrees excluded."""
    pmap = _parent_map(root)
    out = []
    for el in root.iter():
        loc = _local(el.tag)
        skip = False
        cur = pmap.get(id(el))
        while cur is not None:
            if _local(cur.tag) in _SKIP_LOCALS:
                skip = True
                break
            cur = pmap.get(id(cur))
        if skip or loc in _SKIP_LOCALS:
            continue
        if loc == "p" or loc in _BREAKS:
            # A PARAGRAPH BOUNDARY SEPARATES WORDS, and leaving it out was a
            # regression against the reader this module replaced. `_walk_text` is
            # called on a WHOLE comments / drawing / chart part, so with no `p`
            # boundary two comments welded into one token: "divider ratio" +
            # "signed off" became `ratiosigned`, a word in no document anywhere.
            # Measured cost: `recall` 1.0 -> 0.8, `valid: false`, `status: failed`
            # on a FAITHFUL conversion of a valid workbook.
            #
            # Inside one `<si>` rich-text run the opposite rule holds — Excel splits
            # a single word across runs at a formatting boundary, so those must
            # concatenate with nothing between. That is why `si` is read by
            # `_si_table` and not by this walker: one walker cannot serve both, and
            # the sibling `_pptx_struct._walk_text` has always had this boundary.
            out.append(" ")
        if loc in _TEXT_LOCALS and el.text:
            out.append(el.text)
        elif loc in value_locals and el.text:
            out.append(" %s " % el.text)
    return _WS.sub(" ", "".join(out)).strip()


def _si_table(xml):
    # type: (str) -> list
    """``sharedStrings.xml`` -> the list of strings, by index.

    A shared string is rich text: several ``<r><t>`` runs that concatenate with NO
    separator, because Excel splits one word across runs at a formatting boundary.
    ``rPh`` (furigana phonetic guides) is a duplicate reading of the base text and is
    excluded by `_SKIP_LOCALS`, so it cannot inflate the denominator."""
    root = _root(xml)
    if root is None:
        return []
    out = []
    for si in root:
        if _local(si.tag) == "si":
            out.append(_walk_text(si))
    return out


def _value_of(cell, shared):
    # type: (object, list) -> str
    """What this cell SHOWS, resolved from its declared type.

    Written out from the SpreadsheetML type vocabulary rather than copied from the
    converter, and it names every type rather than leaning on a fall-through:

      ``s``          index into the shared-string table
      ``inlineStr``  the string is inside the cell, in ``<is>``
      ``b``          0/1 that Excel displays as FALSE/TRUE
      ``e``          an error literal (``#REF!``) — real text a reader sees
      ``str``        a formula that evaluated to a string
      ``n`` / absent a number, as cached

    ``<f>`` is deliberately NOT read: the converter publishes the cached RESULT, so
    reading the EXPRESSION here would put tokens in the denominator that no faithful
    conversion could ever produce, and every workbook holding a formula would fail a
    gate it should pass. That the expression is lost is real, and it is a matter for
    a counted warning, not for this side of the comparison."""
    ctype = _attr(cell, "t") or "n"
    cached = None
    inline = None
    for ch in cell:
        loc = _local(ch.tag)
        if loc == "v":
            cached = ch.text or ""
        elif loc == "is":
            inline = _walk_text(ch)
    if ctype == "s":
        try:
            return shared[int((cached or "").strip())]
        except (ValueError, IndexError):
            return ""
    if ctype == "inlineStr":
        return inline or ""
    if ctype == "b":
        return "TRUE" if (cached or "").strip() == "1" else "FALSE"
    return cached or ""


def _tab_names(parts):
    # type: (dict) -> list
    """Every worksheet tab name, in workbook order.

    A tab name is body text: the converter renders it as the section heading for its
    grid, so it belongs in the denominator. Read by a flat scan filtered on the
    PARENT being ``<sheets>``, which is where the schema declares a tab — a
    ``<sheet>`` element anywhere else in the part cannot be mistaken for one."""
    root = _root(parts.get("xl/workbook.xml", ""))
    if root is None:
        return []
    pmap = _parent_map(root)
    out = []
    for el in root.iter():
        if _local(el.tag) != "sheet":
            continue
        owner = pmap.get(id(el))
        if owner is None or _local(owner.tag) != "sheets":
            continue
        name = _attr(el, "name")
        if name:
            out.append(name)
    return out


def xlsx_source_text(parts):
    # type: (dict) -> str
    """Exhaustive ground truth for the workbook conversion gate.

    Every tab name and every cell value, plus floating text boxes, cell comments and
    chart text. Cells are read from every worksheet part DIRECTLY rather than through
    the workbook's relationship graph, so a resolution bug on the converter's side
    surfaces as recall < 1.0 instead of removing the same content from both halves."""
    shared = _si_table(parts.get("xl/sharedStrings.xml", ""))
    chunks = list(_tab_names(parts))
    for name in sorted(parts):
        if not _SHEET_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        for cell in root.iter():
            if _local(cell.tag) != "c":
                continue
            value = _value_of(cell, shared)
            if value:
                chunks.append(value)
    for name in sorted(parts):
        root = None
        if _COMMENTS.match(name):
            # ONE CHUNK PER COMMENT. A whole-part read glues the last word of one
            # comment to the first word of the next even with the paragraph
            # boundary above, because adjacent comments are siblings rather than
            # paragraphs in some producers' output.
            chunks.extend(_comment_items(parts[name]))
        elif _DRAWING.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_walk_text(root))
        elif _CHART.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_walk_text(root, value_locals=("v",)))
    return _WS.sub(" ", " ".join(c for c in chunks if c)).strip()

# ------------------------------------------------------- the structural truth


def _ref_col(ref):
    # type: (str) -> int
    """0-based grid column from a cell reference (``B12`` -> 1), or -1 without one.

    Base-26 with no zero digit, re-derived here: ``AA`` is 26, not 0."""
    m = _CELL_REF.match((ref or "").strip().upper())
    if not m:
        return -1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _grid_of(sheet_xml, shared):
    # type: (str, list) -> list
    """The sheet's REGIONS — a list of grids, each positioned by ADDRESS and padded
    to the width of the last column that region uses.

    A BLANK ROW SEPARATES REGIONS. That is a statement about the source document,
    not about the converter: it is how a spreadsheet says "these are two different
    tables", and Excel's own current-region selection stops at one. Reading a sheet
    as ONE grid let a second region's header line be published as a data row of the
    first — and because this side dropped the same blank row, the two
    implementations agreed and the gate passed the fusion.

    This is where a spreadsheet's structure actually lives, and the two rules are
    not interchangeable. A value's column comes from its own ``r`` attribute, so a
    sparse row puts its value where the workbook puts it; and the table's width is
    the furthest column ANY row reaches, so a lone value at ``C1`` makes the whole
    table three wide rather than one. Trailing always-empty columns are trimmed
    because a styled-but-valueless cell is not content a reader can see.

    Read by a flat scan over the part with the owning ``row`` found through a parent
    map — the converter walks each ``row``'s direct children instead, so a wrapper
    element around a cell is a place the two can disagree."""
    root = _root(sheet_xml)
    if root is None:
        return []
    pmap = _parent_map(root)
    # ROWS are enumerated, not inferred from the cells they hold. An EMPTY row
    # (`<row r="5"/>`, no `<c>` at all) is exactly how a spreadsheet writes the
    # separator between two regions, and a reader that grouped cells by their owning
    # row could not see one: it contributed nothing to group. That blindness is what
    # let the region split land on the converter side only, so the two
    # implementations disagreed about a document they both read correctly.
    order = []  # type: list
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        owner = pmap.get(id(row))
        if owner is None or _local(owner.tag) != "sheetData":
            continue
        slot = []  # type: list
        for cell in row.iter():
            if _local(cell.tag) != "c":
                continue
            col = _ref_col(_attr(cell, "r"))
            if col < 0:
                col = len(slot)
            while len(slot) <= col:
                slot.append("")
            slot[col] = _value_of(cell, shared)
        order.append(slot)
    regions = []  # type: list
    current = []  # type: list
    for r in order:
        if any(c.strip() for c in r):
            current.append(r)
        elif current:
            regions.append(current)
            current = []
    if current:
        regions.append(current)
    out = []
    for region in regions:
        width = 1
        for r in region:
            for i in range(len(r) - 1, -1, -1):
                if r[i].strip():
                    width = max(width, i + 1)
                    break
        out.append([(list(r) + [""] * width)[:width] for r in region])
    return out


def _table_fact(grid):
    # type: (list) -> dict
    """``{rows, cols, cells}`` for one rendered grid.

    ``cells`` is what makes a TRANSPOSITION visible: swap two values between rows
    and the dimensions, the counts and the token multiset are all unchanged, while
    the register map now reports the wrong reset value."""
    return {"rows": len(grid), "cols": len(grid[0]),
            "has_header": True,
            "cells": [tuple(_words(c) for c in row) for row in grid]}


def _tab_parts(parts):
    # type: (dict) -> list
    """``[(tab_name, part_name_or_None)]`` in workbook order.

    Names and order come from ``workbook.xml``; each name's grid is found through
    the workbook's relationships. Both are read by this module's own reader — a
    flat scan filtered on the parent being ``<sheets>``, and its own rels parser —
    so a resolution bug in the converter surfaces as a delta rather than removing
    the same section from both sides."""
    root = _root(parts.get("xl/workbook.xml", ""))
    if root is None:
        return []
    targets = {}
    rels = _root(parts.get("xl/_rels/workbook.xml.rels", ""))
    if rels is not None:
        for rel in rels.iter():
            if _local(rel.tag) == "Relationship":
                targets[_attr(rel, "Id")] = _attr(rel, "Target")
    pmap = _parent_map(root)
    out = []
    for el in root.iter():
        if _local(el.tag) != "sheet":
            continue
        owner = pmap.get(id(el))
        if owner is None or _local(owner.tag) != "sheets":
            continue
        name = _attr(el, "name")
        target = targets.get(_attr(el, "id"), "")
        # Every spec-legal spelling of a relative target resolves to the same part.
        stripped = target
        for prefix in ("/xl/", "xl/", "../", "./"):
            while stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
        part = "xl/" + stripped if stripped.startswith("worksheets/") else None
        if part is not None and part not in parts:
            part = None
        out.append((name, part))
    return out


def _has_text(xml, value_locals=()):
    # type: (str, tuple) -> bool
    """Does this part show any text at all?

    Emptiness has to be decided the way the RENDERED document decides it: a chart
    whose only text is its cached numbers still renders a section, and a chart with
    the cache stripped renders nothing — counting a heading for that one invents a
    section the document does not have."""
    root = _root(xml)
    if root is None:
        return False
    for el in root.iter():
        loc = _local(el.tag)
        if loc in _TEXT_LOCALS or loc in value_locals:
            if (el.text or "").strip():
                return True
    return False


def _has_svg_labels(xml):
    # type: (str) -> bool
    """Does this embedded SVG carry label text? Its ``<text>`` elements can nest
    ``<tspan>``, so the whole subtree is read, not just the direct text."""
    root = _root(xml)
    if root is None:
        return False
    for el in root.iter():
        if _local(el.tag) != "text":
            continue
        if "".join(t for t in el.itertext() if t).strip():
            return True
    return False


def _comment_items(xml):
    # type: (str) -> list
    """One text per comment, for the bullets the ``## Comments`` section renders."""
    root = _root(xml)
    if root is None:
        return []
    pmap = _parent_map(root)
    items = []
    for cm in root.iter():
        if _local(cm.tag) not in ("comment", "cm"):
            continue
        nested = False
        cur = pmap.get(id(cm))
        while cur is not None:
            if _local(cur.tag) in ("comment", "cm"):
                nested = True
                break
            cur = pmap.get(id(cur))
        if nested:
            continue
        text = _walk_text(cm)
        if text:
            items.append(text)
    return items


def _has_pictures(parts):
    # type: (dict) -> bool
    """Does any drawing DECLARE a picture?

    Followed to the relationship and no further, which is where the document says
    "there is a picture here". Requiring the bytes to be present as well was
    over-clever and wrong: the converter resolves the rel and emits its sentinel
    whether or not the media part made it into the package, so a workbook whose
    image bytes are missing got a ``## Images`` heading in the markdown and none in
    the ground truth — a false FAIL over a packaging defect that the report already
    names separately as ``images_missing``. An SVG is excluded because it is
    extracted as TEXT, not pixels, and lands in ``## Figures`` instead."""
    for name in sorted(parts):
        if not _DRAWING.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        rids = set()
        for el in root.iter():
            if _local(el.tag) in ("blip", "imagedata"):
                for want in ("embed", "id", "link"):
                    rid = _attr(el, want)
                    if rid:
                        rids.add(rid)
        rels_name = name.replace("xl/drawings/", "xl/drawings/_rels/", 1) + ".xml.rels"
        rels_name = rels_name.replace(".xml.xml.rels", ".xml.rels")
        rels = _root(parts.get(rels_name, ""))
        if rels is None:
            continue
        for rel in rels.iter():
            if _local(rel.tag) != "Relationship" or _attr(rel, "Id") not in rids:
                continue
            if _attr(rel, "TargetMode") == "External":
                # A linked picture's bytes are not in the package, so no sentinel is
                # written and no `## Images` section opens. Counting it invented a
                # heading on this side alone and FAILED the whole bundle over a
                # workbook that had merely linked its screenshot instead of
                # embedding it.
                continue
            target = _attr(rel, "Target")
            kind = _attr(rel, "Type")
            if not (kind.endswith("/image") or _IMAGE_EXT.search(target or "")):
                continue
            if target and not target.lower().endswith(".svg"):
                return True
    return False


# Structure a workbook carries that NO name in the closed fact list can express, so
# a `pass` never claimed it. Declared rather than discovered: `unmeasured` is the
# weaker statement (a fact this vector HAS that this truth did not supply, closable
# by writing code); this list closes only by widening the vector, or never.
# Three entries were removed from this list after review, because each said
# something false and `blind_to` is read as a disclosure:
#   * `used_range` — MEASURED. `tables` is one of the compared facts and carries
#     rows and cols per region; widening either on the truth side reports a delta.
#     Declaring it blind told the reader the gate does not know how big their tables
#     are.
#   * `cell_emphasis` — CATEGORY ERROR. It is expressible in the vocabulary
#     (`strong`/`em`/`strike`), which is why those three sit in `unmeasured`, and it
#     closes by writing code — `unmeasured`'s definition, not this list's.
#   * `paragraph_position` — VACUOUS here. A workbook has no paragraphs, and the
#     format that does (docx) declared nothing, so the field was inverted across
#     formats.
_BLIND_TO = ("cell_addresses", "formulas", "number_formats",
             "hidden_state", "header_row")


_EXTERNAL_URL = ("http://", "https://", "mailto:")


def _sheet_rels(parts, part):
    # type: (dict, str) -> dict
    """``{Id: Target}`` from one worksheet's own rels part.

    A worksheet's rIds are numbered independently of the workbook's, so resolving a
    cell link against `xl/_rels/workbook.xml.rels` would follow whatever the WORKBOOK
    happens to call that id."""
    out = {}
    root = _root(parts.get(
        part.replace("xl/worksheets/", "xl/worksheets/_rels/", 1) + ".rels", ""))
    if root is None:
        return out
    for rel in root.iter():
        if _local(rel.tag) == "Relationship":
            out[_attr(rel, "Id")] = _attr(rel, "Target")
    return out


def _cell_spans(parts):
    # type: (dict) -> dict
    """{"strong"/"em"/"strike"/"links": count} over every cell the render publishes.

    THE UNIT IS THE SPAN A RENDERER SHOWS. A workbook states emphasis per CELL — `@s`
    into `cellXfs` into `fonts`, all positional — so a bold cell is exactly one bold
    span, and there is none of the deck's run-coalescing to model: a cell is the
    atom. An EMPTY cell is not a span, because it renders nothing to emphasise and
    `****` would be four literal asterisks the workbook never wrote.

    A hyperlink is attached to the CELL too, by `@ref` in the sheet's `<hyperlinks>`
    block. Only an external target counts: a `location`-only link jumps within the
    same workbook and has no address a reader outside it could follow, so the render
    keeps the text and `dropped_cell_links` reports the loss.

    Derived here rather than copied from the converter, which is the whole point of
    this module: both sides reach the same number by reading the same package
    differently, and if either is wrong the workbook fails loudly."""
    out = {"strong": 0, "em": 0, "strike": 0, "links": 0}
    styles = _emphasised_styles(parts.get("xl/styles.xml", ""))
    shared = _si_table(parts.get("xl/sharedStrings.xml", ""))
    # EVERY worksheet part, not only the ones a relationship reached. A tab whose
    # rels target is broken still RENDERS, under a synthesised
    # `## Sheet (unlinked): ...` heading — losing a tab's name never costs its
    # content — so its cells' emphasis is published and has to be stated. Reading
    # only `_tab_parts` made the truth blind to exactly the sheets the converter
    # takes most care to keep.
    seen_parts = set(part for _n, part in _tab_parts(parts) if part)
    seen_parts.update(n for n in parts if _SHEET_PART.match(n))
    for part in sorted(seen_parts):
        root = _root(parts.get(part, ""))
        if root is None:
            continue
        rels = _sheet_rels(parts, part)
        for el in root.iter():
            loc = _local(el.tag)
            if loc == "c":
                marks = styles.get(_attr(el, "s")) or ()
                if marks and _value_of(el, shared):
                    for mark in marks:
                        out[{"b": "strong", "i": "em", "strike": "strike"}[mark]] += 1
            elif loc == "hyperlink":
                url = rels.get(_attr(el, "id") or "", "")
                if _attr(el, "ref") and url.startswith(_EXTERNAL_URL):
                    out["links"] += 1
    return out


def xlsx_source_structure(parts, emit_images=False):
    # type: (dict, bool) -> dict
    """The structural facts a faithful conversion of this workbook must exhibit.

    Same keys as ``backend.validate.md_structure`` so the two compare directly, and
    reached by a different route: a flat scan with an ancestor predicate over a
    parent map, against the converter's recursive descent.

    ``emit_images`` matters and is not cosmetic. Under it the converter adds a real
    ``## Images`` heading, and ``scripts/build_bundle.py`` — the only path that runs
    this gate — converts with it ON, so a truth that ignored the flag would read one
    heading short on every bundled workbook holding a picture.

    WHAT IS STATED AS ZERO, and why a zero is worth stating. A workbook has no
    construct that renders as a fence, an ordered marker or a horizontal rule. Any
    of those appearing in the markdown is therefore a SUBSTITUTION — document text
    replaced by punctuation carrying none of it — and a substitution deletes the
    characters it replaces, so token recall reads a vacuous 1.0 over the damage. The
    stated zero is the only handle the gate can get on it.

    NOTHING IS OMITTED ANY MORE, and the rule that used to require omission is why.
    A workbook's bold header row, its strikethrough "cancelled" row and its cell
    hyperlinks are real formatting, and for as long as ``xlsx_markdown`` dropped them
    a stated ``strong: 0`` would have made this module inherit the converter's blind
    spot on the very axis it exists to police — it would have CERTIFIED the drop — so
    the keys were left out and the report named them in ``unmeasured``.

    P9.8c ended that the only way it could be ended: the converter emits all four, so
    the counts are real and a zero is a falsifiable claim about the SOURCE. That is
    strictly stronger than the omission it replaces, because a fact nobody states is
    a fact nobody can see FABRICATED — a ``**`` the converter invented over a plain
    cell was invisible for exactly as long as ``strong`` was unmeasured."""
    out = {"headings": {}, "heading_path": [], "block_sequence": [],
           "list_items": {}, "bullet_items": 0, "list_item_words": [],
           "tables": [],
           # Stated zeros — falsifiable, and that is the point (see the docstring).
           "ordered_items": 0, "ordered_numbers": [], "thematic_breaks": 0,
           "code_spans": 0, "code_blocks": 0,
           "_blind_to": list(_BLIND_TO)}
    # EMPHASIS AND HYPERLINKS, counted rather than omitted. These four were left out
    # of the vector for as long as `xlsx_markdown` dropped them — reported as
    # `unmeasured` by name — because a stated `strong: 0` over a workbook with a bold
    # header would have CERTIFIED the loss on the very axis this module exists to
    # police. The converter emits them now (P9.8c), so the omission ends the only way
    # it is allowed to: by counting. A zero is now a real claim about the SOURCE.
    out.update(_cell_spans(parts))
    shared = _si_table(parts.get("xl/sharedStrings.xml", ""))

    reached = set()
    for name, part in _tab_parts(parts):
        # The heading comes before the grid is looked for, so a broken relationship
        # costs the TABLE and never the tab's name.
        _add_heading(out, name)
        if part is None:
            continue
        reached.add(part)
        for grid in _grid_of(parts.get(part, ""), shared):
            out["tables"].append(_table_fact(grid))
            out["block_sequence"].append(("table", (len(grid), len(grid[0]))))

    # A worksheet part no relationship reached still renders, under a synthesised
    # heading: losing a tab's NAME must never cost its CONTENT.
    for name in sorted(parts):
        if not _SHEET_PART.match(name) or name in reached:
            continue
        grids = _grid_of(parts[name], shared)
        if not grids:
            continue
        _add_heading(out, "Sheet (unlinked): " + name.rsplit("/", 1)[-1][:-4])
        for grid in grids:
            out["tables"].append(_table_fact(grid))
            out["block_sequence"].append(("table", (len(grid), len(grid[0]))))

    # The satellite sections, in the order the document shows them. ONE heading per
    # KIND and only when some part of that kind carries text — a heading per PART
    # was wrong four ways for docx and the same trap is here.
    if any(_DRAWING.match(n) and _has_text(parts[n]) for n in sorted(parts)):
        _add_heading(out, "Text boxes")
    items = []
    for name in sorted(parts):
        if _COMMENTS.match(name):
            items.extend(_comment_items(parts[name]))
    if items:
        # Comments render as `- item` lines, which a renderer reads as a LIST.
        _add_heading(out, "Comments")
        out["list_items"][0] = out["list_items"].get(0, 0) + len(items)
        out["bullet_items"] += len(items)
        out["list_item_words"].extend(_words(t) for t in items)
        for _ in items:
            out["block_sequence"].append(("li", 0))
    if any(_CHART.match(n) and _has_text(parts[n], ("v",)) for n in sorted(parts)):
        _add_heading(out, "Charts")
    if emit_images and _has_pictures(parts):
        _add_heading(out, "Images")
    # `## Figures` is appended by `ooxml_markdown` for EVERY format, after the
    # format's own sections, whenever an embedded SVG carries label text.
    if any(_MEDIA_SVG.match(n) and _has_svg_labels(parts[n]) for n in sorted(parts)):
        _add_heading(out, "Figures")
    return out


# ------------------------------------------------- measured policy drops
#
# end-goal.md §1: the drop is always DELIBERATE AND VISIBLE, never an accident. A
# drop nobody counted reads exactly like a bug — and worse, it reads like nothing at
# all. `=SUM(C3:C5)` in the shipped corpus workbook was absent from the markdown,
# absent from the recall denominator and absent from the warnings: a reader of that
# bundle could not tell a computed total from a typed one, and had no hint to go
# looking. That is a different and weaker position than the header/footer drop the
# charter argues for, which at least leaves a receipt naming the part and the
# character count.
#
# Each warning names WHAT was lost, HOW MUCH, and WHAT WAS KEPT.

# Number formats whose rendering changes the CHARACTERS a reader sees, not merely
# the precision they are shown to. A date serial is the extreme case: 46027 and
# 2026-01-05 share not one character. Built-in ids per ECMA-376 §18.8.30.
_BUILTIN_DATE = tuple(range(14, 23)) + tuple(range(45, 48))
_BUILTIN_PERCENT = (9, 10)
# Tokens whose presence in a format code means the DISPLAYED CHARACTERS differ from
# the stored value: calendar and clock fields, a percent sign (0.815 -> 81.50%), a
# thousands separator (1234567 -> 1,234,567) and a currency symbol. Fixed-decimal
# precision alone (`0.00`, showing 1 as `1.00`) is deliberately NOT here and the
# reason is stated in the warning's doc row: the published number is numerically
# identical and a reader loses nothing they cannot recover, whereas 46027 and
# 2026-01-05 share not one character.
_DISPLAY_TOKENS = ("yy", "mm", "dd", "hh", "ss", "%", "am/pm", "a/p",
                   "#,#", "0,0", "$", "\u00a3", "\u20ac", "\u00a5")
# The built-in codes, spelled out. A warning that says "builtin 10" makes the reader
# go and look one up; "0.00%" tells them what the cell showed.
_BUILTIN_CODES = {
    9: "0%", 10: "0.00%", 14: "m/d/yyyy", 15: "d-mmm-yy", 16: "d-mmm",
    17: "mmm-yy", 18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm",
    21: "h:mm:ss", 22: "m/d/yy h:mm", 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0",
}
# The w:val spellings that mean "off" on a boolean font property.
_OFF_VALS = ("0", "false", "off")


def _number_formats(xml):
    # type: (str) -> dict
    """``{style_index: format_code}`` for every cell style that reformats its value.

    ``cellXfs`` is positional — a cell's ``s`` attribute is an INDEX into it — so the
    order is the meaning and a reader that collected the entries into a set would
    lose it."""
    root = _root(xml)
    if root is None:
        return {}
    codes = {}
    for el in root.iter():
        if _local(el.tag) == "numFmt":
            codes[_attr(el, "numFmtId")] = _attr(el, "formatCode")
    xfs = None
    for el in root.iter():
        if _local(el.tag) == "cellXfs":
            xfs = el
            break
    if xfs is None:
        return {}
    out = {}
    index = 0
    for xf in xfs:
        if _local(xf.tag) != "xf":
            continue
        fmt_id = _attr(xf, "numFmtId")
        try:
            as_int = int(fmt_id)
        except ValueError:
            as_int = 0
        code = codes.get(fmt_id, "")
        reformats = as_int in _BUILTIN_DATE or as_int in _BUILTIN_PERCENT
        if not reformats and code:
            low = code.lower()
            reformats = any(tok in low for tok in _DISPLAY_TOKENS)
        if reformats:
            # Excel escapes literal characters in a format code with a backslash
            # (``yyyy\\-mm\\-dd``). Those are markup, not something the reader ever
            # saw, so they are stripped before the code is quoted in a warning.
            shown = code.replace("\\", "") if code else _BUILTIN_CODES.get(as_int, "")
            out[str(index)] = shown or ("format %s" % fmt_id)
        index += 1
    return out


def _emphasised_styles(xml):
    # type: (str) -> dict
    """``{style_index: {marks}}`` for every style whose font carries emphasis.

    The MARKS are kept, not just the fact that there were some: reporting one lump
    count meant a workbook with a bold header and nothing else was told "a
    struck-through row means CANCELLED to the person who wrote it", a paragraph
    about a loss it had not suffered. A warning that describes the wrong loss is
    worse than a shorter one.

    ``cellXfs[i]`` -> ``fontId`` -> ``fonts[fontId]``, all positional."""
    root = _root(xml)
    if root is None:
        # A DICT, as the docstring says and as the other exit does. This read
        # `set()` until P9.8c: the only caller iterated the result, and iterating an
        # empty set and an empty dict look identical, so a workbook with no
        # `xl/styles.xml` handed back the wrong TYPE and nothing noticed until a
        # second caller did a lookup on it.
        return {}
    fonts = []
    for el in root.iter():
        if _local(el.tag) != "font":
            continue
        marks = set()
        for child in el:
            loc = _local(child.tag)
            if loc in ("b", "i", "strike") and _attr(child, "val") not in _OFF_VALS:
                marks.add(loc)
        fonts.append(marks)
    xfs = None
    for el in root.iter():
        if _local(el.tag) == "cellXfs":
            xfs = el
            break
    if xfs is None:
        return {}
    out = {}
    index = 0
    for xf in xfs:
        if _local(xf.tag) != "xf":
            continue
        try:
            font_id = int(_attr(xf, "fontId") or "0")
        except ValueError:
            font_id = 0
        if 0 <= font_id < len(fonts) and fonts[font_id]:
            out[str(index)] = fonts[font_id]
        index += 1
    return out



def _cells_of(parts):
    # type: (dict) -> list
    """Every ``(sheet_part, cell)`` in the workbook, for the drop counters."""
    out = []
    for name in sorted(parts):
        if not _SHEET_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        for cell in root.iter():
            if _local(cell.tag) == "c":
                out.append((name, cell))
    return out


def _hidden_content(parts):
    # type: (dict) -> dict
    """Concealed regions the conversion publishes anyway, counted WITHOUT
    double-counting and NAMED.

    Two corrections a first version got wrong, both of which made the number lie.
    A hidden sheet's own rows are not counted again — one concealed region holding
    forty hidden rows reported ``1 sheet and 40 rows``, reading as forty-one
    separate concealments. And a hidden row with no VALUE in it publishes nothing,
    so counting it made the claim "the text is kept in full" about rows that carry
    no text; only rows that actually reach the markdown are counted."""
    hidden_sheets = []
    hidden_parts = set()
    targets = {}
    rels = _root(parts.get("xl/_rels/workbook.xml.rels", ""))
    if rels is not None:
        for rel in rels.iter():
            if _local(rel.tag) == "Relationship":
                targets[_attr(rel, "Id")] = _attr(rel, "Target")
    root = _root(parts.get("xl/workbook.xml", ""))
    if root is not None:
        for el in root.iter():
            if _local(el.tag) != "sheet":
                continue
            if _attr(el, "state") not in ("hidden", "veryHidden"):
                continue
            hidden_sheets.append(_attr(el, "name") or "?")
            target = targets.get(_attr(el, "id"), "")
            stripped = target
            for prefix in ("/xl/", "xl/", "../", "./"):
                while stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
            if stripped.startswith("worksheets/"):
                hidden_parts.add("xl/" + stripped)
    rows = 0
    for name in sorted(parts):
        if not _SHEET_PART.match(name) or name in hidden_parts:
            continue
        part_root = _root(parts[name])
        if part_root is None:
            continue
        for el in part_root.iter():
            if _local(el.tag) != "row":
                continue
            if _attr(el, "hidden") in ("",) + _OFF_VALS:
                continue
            for cell in el.iter():
                if _local(cell.tag) == "c" and _value_of(cell, []).strip():
                    rows += 1
                    break
    return {"sheets": hidden_sheets, "rows": rows}


def xlsx_policy_drops(parts):
    # type: (dict) -> list
    """Every deliberate flattening or drop the workbook lane performs, as named
    warnings with the COUNTS behind them.

    None of these degrades ``status``: a policy drop is not a defect. What it must
    never be is silent, because a reader cannot go and look for something they were
    never told about."""
    found = []

    # TWO DIFFERENT LOSSES, and one sentence for both said the opposite of what
    # happened to half of them. A formula WITH a cached result publishes the number
    # and loses the expression. A formula with NO cached result publishes an EMPTY
    # CELL — the value is gone, not merely the derivation — and because both halves
    # of the token gate read that cell as empty, `token_recall` is a vacuous 1.0
    # over it and this warning is the only receipt there is. Telling a reader "the
    # cached RESULT is published" about a blank cell sends them looking for a number
    # that is not there.
    cached, blank = [], []
    for part, cell in _cells_of(parts):
        expr = ""
        has_value = False
        for child in cell:
            loc = _local(child.tag)
            if loc == "f" and (child.text or "").strip():
                expr = child.text.strip()
            elif loc == "v" and (child.text or "").strip():
                has_value = True
        if expr:
            sheet = part.rsplit("/", 1)[-1][:-4]
            (cached if has_value else blank).append(
                "%s!%s = %s" % (sheet, _attr(cell, "r"), expr))
    if cached:
        found.append({
            "code": "dropped_cell_formulas",
            "detail": "%d cell formula(s) dropped (%s): the cached RESULT is "
                      "published and the expression is not, so a reader cannot tell "
                      "a computed value from a typed one. The locators are cell "
                      "addresses in the package the converter walked, which on the "
                      "LibreOffice lane is the pre-converted copy rather than the "
                      "file you supplied"
                      % (len(cached), _some(cached)),
            "cells": len(cached),
            "first": cached[0]})
    if blank:
        found.append({
            "code": "empty_cell_formulas",
            "detail": "%d cell formula(s) carry no cached result (%s), so the cell "
                      "is published EMPTY: the value is gone, not just the "
                      "expression. Both halves of the token gate read the cell as "
                      "empty, so recall cannot see this one and this count is the "
                      "only record of it"
                      % (len(blank), _some(blank)),
            "cells": len(blank),
            "first": blank[0]})

    styles = parts.get("xl/styles.xml", "")
    reformatted = _number_formats(styles)
    if reformatted:
        seen = {}
        for _, cell in _cells_of(parts):
            style = _attr(cell, "s")
            if style not in reformatted:
                continue
            if (_attr(cell, "t") or "n") != "n":
                continue          # a string is already the text a reader sees
            for child in cell:
                if _local(child.tag) == "v" and (child.text or "").strip():
                    seen.setdefault(reformatted[style], 0)
                    seen[reformatted[style]] += 1
                    break
        total = sum(seen.values())
        if total:
            found.append({
                "code": "unformatted_cell_values",
                "detail": "%d numeric cell(s) are published as the value the "
                          "workbook STORES, not the text it displays (format%s %s): "
                          "a date is stored as a serial number and a percentage as a "
                          "fraction, so 46027 is published where a reader sees "
                          "2026-01-05. The number is exact; its presentation is not "
                          "carried, because GFM has no cell format"
                          % (total, "s" if len(seen) > 1 else "",
                             ", ".join(sorted(seen))),
                "cells": total,
                "formats": sorted(seen)})

    # EMPHASIS is no longer dropped, so there is no longer a receipt for it.
    # `dropped_cell_emphasis` counted the bold, italic and struck cells
    # `xlsx_markdown` did not emit; it emits all three now (P9.8c) and
    # `xlsx_source_structure` states real `strong`/`em`/`strike` counts, so the loss
    # this code reported has ENDED. `_emphasised_styles` is still read — by the fact
    # vector, which is the stronger statement: a warning says a loss happened, a fact
    # says how much and gets compared against the rendered markdown.

    links = []
    for name in sorted(parts):
        if not _SHEET_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        rels_name = name.replace("xl/worksheets/", "xl/worksheets/_rels/", 1) + ".rels"
        targets = {}
        rels = _root(parts.get(rels_name, ""))
        if rels is not None:
            for rel in rels.iter():
                if _local(rel.tag) == "Relationship":
                    targets[_attr(rel, "Id")] = _attr(rel, "Target")
        sheet = name.rsplit("/", 1)[-1][:-4]
        for el in root.iter():
            if _local(el.tag) != "hyperlink":
                continue
            url = targets.get(_attr(el, "id"), "") or _attr(el, "location")
            # NARROWED, not retired. An EXTERNAL url is emitted as `[text](url)` now,
            # so it is not lost and reporting it would be a receipt for nothing. A
            # `location` jump stays INSIDE the workbook: there is no address a reader
            # outside it could follow, so the render keeps the text and drops the
            # target, and this is the only place that loss can be seen.
            if url.startswith(_EXTERNAL_URL):
                continue
            if url:
                links.append("%s!%s -> %s" % (sheet, _attr(el, "ref") or "?", url))
    if links:
        found.append({
            "code": "dropped_cell_links",
            "detail": "%d cell hyperlink(s) lose their target (%s): the display text "
                      "is kept and the URL is not. A URL is on neither side of the "
                      "token gate — it is markup, never cell text — so nothing else "
                      "in the report can see this, which is why the destination is "
                      "quoted here rather than merely counted"
                      % (len(links), _some(links)),
            "cells": len(links),
            "first": links[0]})

    hidden = _hidden_content(parts)
    if hidden["sheets"] or hidden["rows"]:
        named = (" (%s)" % ", ".join(hidden["sheets"])) if hidden["sheets"] else ""
        found.append({
            "code": "hidden_content_published",
            "detail": "%d hidden sheet(s)%s and %d hidden row(s) elsewhere are "
                      "converted and published as ordinary content: the text is kept "
                      "in full, and the fact that the workbook does not SHOW it is "
                      "not — a scratch sheet reads as a peer of the real ones. The "
                      "sheets are NAMED so a reader can find them in the markdown"
                      % (len(hidden["sheets"]), named, hidden["rows"]),
            "sheets": len(hidden["sheets"]),
            "sheet_names": list(hidden["sheets"]),
            "rows": hidden["rows"]})
    return found
