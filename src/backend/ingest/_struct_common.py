"""
title: Mechanism shared by the structural ground truths (private)
kind: module
layer: backend
public_api: no
summary: One copy of the machinery every SOURCE-side structural truth needs — parent maps, ancestry, the token notion, the heading record, the locator elision.
"""
# THE ADMISSION RULE, which is the only reason this module is allowed to exist.
#
#     A helper may be shared iff it is called on exactly one side of the comparison
#     it feeds.
#
# A one-sided bug makes a faithful document fail loudly — that is a working gate. A
# two-sided bug lands identically on each side, the delta cancels, and the gate
# reports a confident pass over real damage. Measured in this package, twice: a
# `_cell_value` shared by the xlsx converter and the xlsx token truth held
# `token_recall` at a clean 1.0 while the source token count fell 107 -> 73; and a
# nesting rule the deck's structural truth had copied from the converter published
# 199 of 336 outline-level sequences misrepresented at `gate: pass, deltas: [],
# recall: 1.0`.
#
# `_ooxml_struct`, `_pptx_struct` and `_xlsx_struct` all read the SOURCE package.
# They are three readers on ONE side. The other side is `_ooxml_md`, which writes the
# markdown, and `backend.validate._mdstructure`, which reads it back the way a
# CommonMark renderer would. So the three truths may share mechanism with each other
# freely — and nothing here may ever be imported by those two. That is not a comment:
# `tests/unit/backend/test_ingest_struct_common.py` reads the real import statements
# with `ast` and fails the build.
#
# WHAT IS DELIBERATELY NOT HERE, because "it appears three times" is not the test.
#
# `_attr` — byte-identical in all three truths AND in the converter. An attribute
# read is where a cell's ADDRESS comes from, so one shared copy is called on both
# sides: measured, a shared `_attr` that lost every `@r` shifted every value into a
# different column while `token_recall`, `n_source_tokens`, `compared` and the delta
# list all stayed exactly as they were. Nothing in the report moved. Four copies of
# five lines buys the one thing the second gate exists for, and
# `test_every_reader_owns_its_own_attribute_read` pins that they stay four.
#
# `_rel_id` — byte-identical in `_ooxml_md` and `_pptx_struct`, and that pair is a
# converter and the truth that grades it. It is also the injection point the deck
# lane's independence proof uses.
#
# The FACT VECTOR (`_new_facts` and the two inline dict literals). This one the plan
# named as a candidate and the measurement refused, so the reasoning is written down
# rather than left as an omission. The empty containers are mechanism, but the
# STATED ZEROS are not: `"thematic_breaks": 0` is a falsifiable claim that this
# FORMAT has no construct rendering as a horizontal rule, and each of the three
# argues its own set in its own docstring. A shared seed would let one format's
# argued zero become another format's unargued certification — the exact move that
# makes a truth inherit the converter's blind spot on the axis it exists to police —
# and it would do so in six lines nobody would re-read. The containers stay with the
# claims they accumulate into.
import re

# Nothing here is public. It is shared implementation among three readers that answer
# to one contract; re-exporting it would invite a caller outside this boundary to
# depend on it.
__all__ = []  # type: list


def _parent_map(root):
    # type: (object) -> dict
    """``{id(child): parent}`` for the whole tree.

    ElementTree has no parent pointers, so "is this paragraph inside a table cell?"
    and "am I inside a footer placeholder?" both need one. Building it costs a single
    pass and is what lets every fact be a flat filter instead of a recursive descent
    — which is the entire independence claim, because a converter that forgets to
    recurse into a wrapper element still shows up in a flat stream.

    The ROOT is deliberately absent from the map: it has no parent, and `_ancestry`
    terminating on `pmap.get` returning None is the only stop condition."""
    pmap = {}
    stack = [root]
    while stack:
        el = stack.pop()
        for ch in el:
            pmap[id(ch)] = el
            stack.append(ch)
    return pmap


def _ancestry(el, pmap):
    # type: (object, dict) -> list
    """Every ancestor of ``el``, nearest first.

    An element the map never saw yields ``[]`` rather than raising. Readers build one
    map per PART, so a stray element from elsewhere must cost that element's facts,
    never the bundle."""
    out = []
    cur = pmap.get(id(el))
    while cur is not None:
        out.append(cur)
        cur = pmap.get(id(cur))
    return out


_WORDS = re.compile(r"[a-z0-9]+")


def _words(text):
    # type: (str) -> tuple
    """Heading, item and cell CONTENT as the token notion the whole gate uses.

    Shared among the three SOURCE-side truths and never with
    ``backend.validate._mdstructure``, which has its own: that one reads the EMITTED
    markdown and has to strip a link's URL and a ``<br>`` before tokenising, because
    it is reading markup. This one reads a source, which has none. They sit on
    opposite sides of the comparison, so sharing would make a tokenisation bug cancel
    instead of report — and the two are close enough to look interchangeable, which
    is exactly why a test pins them apart behaviourally rather than by import alone.

    Whitespace of any kind separates tokens, which is what a GFM cell's ``<br>`` and
    the converter's newline collapse do on the other side. Punctuation separates
    rather than joins, so escaping and emphasis markers — markup, not content —
    cannot make a faithful conversion differ."""
    return tuple(_WORDS.findall((text or "").lower()))


def _add_heading(out, level, text):
    # type: (dict, int, str) -> None
    """One heading the rendered document shows — in all THREE heading facts.

    ``headings`` is a level histogram, ``heading_path`` is the ordered, text-bearing
    view of the same events, and ``block_sequence`` is where it fell among the other
    graded blocks. They are written together, in one place, because they only mean
    anything side by side: a histogram cannot see two section titles exchanged (the
    levels are unchanged, the token multiset is unchanged, and prose ends up
    reattached to the wrong chapter with every gate green), and a path that drifted
    out of step with the histogram would be a second opinion about a different
    document.

    ``min(level, 6)`` because markdown stops at ``######``: a ``w:outlineLvl`` of 8
    is an h6 in the render, so it has to be an h6 here too or every deep outline
    would fail. That is justification (ii) — a statement about what MARKDOWN can
    hold — and never a statement about what this repo's converter happens to do. The
    deck and the workbook only ever pass 2 and 3, so the bound is a no-op for them
    and costs them nothing.

    The title is reduced to the same ``[a-z0-9]+`` token notion the cell and list
    facts use — escaping, emphasis markers and a hyperlink's URL are markup, not
    content, and comparing them would fail faithful conversions."""
    level = min(level, 6)
    out["headings"][level] = out["headings"].get(level, 0) + 1
    out["heading_path"].append((level, _words(text)))
    out["block_sequence"].append(("h", level))


def _some(items, limit=3):
    # type: (list, int) -> str
    """The first few locators, so a claim is checkable against the source rather
    than merely counted.

    The elision marker is load-bearing: without it a reader cannot tell "three" from
    "the first three of eleven", and a warning whose count and whose evidence
    disagree is worse than one that only counts."""
    head = "; ".join(items[:limit])
    return head + (", ..." if len(items) > limit else "")
