"""
title: Unit — the mechanism the structural ground truths share, and the boundary that keeps it safe
kind: tests
layer: backend
summary: One copy of the machinery every source-side truth needs, and proof that neither the converter nor the markdown reader may touch it.
"""
# WHY A SHARED MODULE IS SAFE HERE AND IS NOT SAFE ANYWHERE ELSE.
#
# The admission rule this package is built on: *a helper may be shared iff it is
# called on exactly one side of the comparison it feeds.* A one-sided bug makes a
# faithful document fail loudly, which is a working gate; a two-sided bug cancels
# and the gate reports a confident pass over real damage.
#
# The three structural ground truths all sit on the SAME side — they read the SOURCE
# package. The other side of their comparison is `backend.validate._mdstructure`,
# which reads the EMITTED markdown, and the converter `_ooxml_md`, which writes it.
# So `_ooxml_struct`, `_pptx_struct` and `_xlsx_struct` may share mechanism with each
# other freely, and may share NOTHING with those two.
#
# That is the whole contract, and it is the part a future edit gets wrong: importing
# `_words` into `_mdstructure` "because it is the same tokenisation" would put one
# function on both sides of every comparison in the project. The two really are
# different — the markdown side strips a link's URL and a `<br>` first, because it
# is reading markup — and these tests pin that they stay different.
import ast
import os

import pytest

from backend.ingest import _struct_common

pytestmark = pytest.mark.unit

_INGEST = os.path.dirname(_struct_common.__file__)
_VALIDATE = os.path.join(os.path.dirname(_INGEST), "validate")


def _relative_imports(path):
    """``{module_name: {imported names}}`` for every ``from .x import y``."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            out.setdefault(node.module or "", set()).update(
                a.name for a in node.names)
    return out


def _every_module_referenced(path):
    """Every module this file imports, relative OR absolute, as flat dotted names.

    Relative-only was the first version of this and it had a hole big enough to drive
    the whole boundary through: `_mdstructure` lives in a DIFFERENT package, so the
    import that would actually break the gate is
    ``from backend.ingest._struct_common import _words`` — absolute, ``node.level``
    zero, invisible to a relative-import reader. Verified by writing exactly that
    import and watching the guard stay green.

    Absolute form also defeats the behavioural half of this file on its own: a
    ``from X import _words`` binds the function at import time, so monkeypatching
    ``_struct_common._words`` never reaches the copy the other side actually calls.
    That is the one shape a fault injection cannot see, which is precisely why it has
    to be caught here, structurally, instead."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out.add("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


# ------------------------------------------------------------- the boundary

def test_the_shared_module_imports_nothing_from_the_package():
    """Bottom of the dependency order, for the same reason `_ooxml_leaf` is: the
    converter imports the per-format token truths at module scope, so anything those
    truths import must not reach back into the package or the office lane
    ImportErrors at first import."""
    assert _relative_imports(os.path.join(_INGEST, "_struct_common.py")) == {}


def test_the_shared_module_declares_itself_private():
    """CONVENTIONS §1 makes ``__all__`` the machine-checkable public API. Nothing
    here is public: it is shared *implementation* among three readers that answer to
    one contract, and re-exporting it would invite a caller outside this boundary —
    the converter, a script, the markdown reader — to depend on it."""
    assert _struct_common.__all__ == []


@pytest.mark.parametrize("path,what", [
    (os.path.join(_INGEST, "_ooxml_md.py"), "the converter"),
    (os.path.join(_VALIDATE, "_mdstructure.py"), "the markdown-side reader"),
])
def test_the_other_side_of_the_gate_may_not_import_the_shared_mechanism(path, what):
    """THE test in this file. Everything here is called on the SOURCE side; these
    two are the other side. `_words` is the one that will be reached for — the
    tokenisations look interchangeable and are not — and a single shared tokeniser
    would make every heading, cell and list-item comparison in the project cancel on
    a bug rather than report it."""
    reached = [m for m in _every_module_referenced(path)
               if m.split(".")[-1] == "_struct_common"]
    assert not reached, (
        "%s (%s) imports the source-side shared mechanism (%s), which puts it on "
        "BOTH sides of the comparison it feeds -- and an absolute import binds the "
        "name at import time, so no fault injection would ever show you the "
        "cancellation" % (os.path.basename(path), what, reached))


# What the converter may take from each SOURCE-side truth. A closed whitelist,
# because the direct-import check above has a blind spot a review found: `_ooxml_md`
# already imports all three truth modules at module scope — it has to, since each owns
# its format's TOKEN truth — so `_struct_common` is transitively reachable from the
# converter's import graph. Import is not a call, and today no execution path gets
# there: `*_source_text` never touches `_add_heading`, `_words`, `_parent_map` or
# `_ancestry`, which only the `*_source_structure` walks use. But "today it does not
# call it" is the kind of fact that stops being true in a refactor nobody reviewed as
# a gate change, so the reachable SURFACE is pinned instead of the current behaviour.
_CONVERTER_MAY_IMPORT = {
    "_ooxml_struct": {"docx_source_text"},
    "_pptx_struct": {"pptx_source_text"},
    "_xlsx_struct": {"xlsx_source_text"},
}


def test_the_converter_takes_only_the_token_truths_from_the_structural_modules():
    """The converter imports each truth module for ONE thing: the exhaustive text
    the token gate uses as its denominator. Reaching further — for a
    `*_source_structure`, a `*_policy_drops`, or any helper — would put the SECOND
    gate's source-side machinery inside the thing that second gate grades, and the
    shared mechanism would be on both sides of the comparison by a route no
    direct-import check can see.

    A new symbol here is not automatically wrong; it is automatically a GATE CHANGE,
    and this list is where the argument for it goes."""
    got = _relative_imports(os.path.join(_INGEST, "_ooxml_md.py"))
    for module, allowed in _CONVERTER_MAY_IMPORT.items():
        extra = sorted(got.get(module, set()) - allowed)
        assert not extra, (
            "_ooxml_md imports %s from .%s. Only the token truth may cross that "
            "line; anything reading STRUCTURE belongs on the other side of the gate"
            % (extra, module))


def test_the_two_tokenisers_disagree_where_markup_is_involved():
    """NOT a sharing detector, and it is named so it cannot be mistaken for one.
    Verified: with `_mdstructure._words` rewritten to delegate to the shared one, this
    still passes, because the markdown side strips the link BEFORE delegating and the
    observable behaviour is unchanged. The import guard above is the detector.

    What this does prove is the reason the two must stay separate: they are not
    interchangeable. The markdown reader must see through `[text](url)` and `<br>`
    because it is reading markup; the source reader must not, because a source has
    none. A future edit that made them agree here would have made one of them wrong
    about its own side."""
    from backend.validate import _mdstructure
    assert _mdstructure._words("see [the spec](http://x/y#z)") == ("see", "the", "spec")
    assert _struct_common._words("see [the spec](http://x/y#z)") != ("see", "the", "spec")


# ---------------------------------------------------------- the mechanism behaves

def _root(xml):
    import xml.etree.ElementTree as ET
    return ET.fromstring(xml)


def test_the_parent_map_reaches_every_descendant():
    root = _root("<a><b><c/></b><d/></a>")
    pmap = _struct_common._parent_map(root)
    b, d = list(root)
    c = list(b)[0]
    assert pmap[id(c)] is b and pmap[id(b)] is root and pmap[id(d)] is root
    assert id(root) not in pmap, "the root has no parent, and must not claim one"


def test_ancestry_is_nearest_first_and_stops_at_the_root():
    root = _root("<a><b><c/></b></a>")
    pmap = _struct_common._parent_map(root)
    c = list(list(root)[0])[0]
    assert _struct_common._ancestry(c, pmap) == [list(root)[0], root]
    assert _struct_common._ancestry(root, pmap) == []


def test_ancestry_of_an_element_the_map_never_saw_is_empty_not_an_error():
    """Readers build one map per PART and then walk elements from it. An element
    from another part must cost that element's facts, never the bundle."""
    other = _root("<z/>")
    assert _struct_common._ancestry(other, {}) == []


@pytest.mark.parametrize("text,want", [
    ("Reset the clock tree", ("reset", "the", "clock", "tree")),
    # An underscore is a SEPARATOR, not a word character, so an identifier compares
    # as its parts. That is deliberate and it is what makes the fact robust: the
    # identifier itself is graded verbatim by rubric row A3 over the stored bytes,
    # while this notion only has to survive markdown's escaping of `_` as emphasis.
    ("CLK_100M / rst_n", ("clk", "100m", "rst", "n")),
    ("  spaced\tout\nlines ", ("spaced", "out", "lines")),
    ("", ()),
    (None, ()),
    ("--- 1.2.3 ---", ("1", "2", "3")),
])
def test_words_is_the_token_notion_the_whole_gate_uses(text, want):
    """Lowercased ``[a-z0-9]+`` runs. Punctuation separates rather than joins, so
    escaping and emphasis markers — markup, not content — cannot make a faithful
    conversion differ."""
    assert _struct_common._words(text) == want


def test_add_heading_writes_the_histogram_the_path_and_the_order_together():
    """The three only mean anything side by side: a histogram of LEVELS cannot see
    two section titles exchanged, because the levels and the token multiset are
    identical either way."""
    out = {"headings": {}, "heading_path": [], "block_sequence": []}
    _struct_common._add_heading(out, 2, "Bring-up sequence")
    _struct_common._add_heading(out, 2, "Clock tree")
    _struct_common._add_heading(out, 3, "Notes")
    assert out["headings"] == {2: 2, 3: 1}
    assert out["heading_path"] == [(2, ("bring", "up", "sequence")),
                                   (2, ("clock", "tree")), (3, ("notes",))]
    assert out["block_sequence"] == [("h", 2), ("h", 2), ("h", 3)]


def test_a_heading_deeper_than_markdown_can_hold_is_recorded_as_the_h6_it_renders():
    """``######`` is the deepest heading markdown has, so a w:outlineLvl of 8 IS an
    h6 in the render. A truth that demanded level 8 would fail every faithful
    conversion of a deep outline, permanently — a statement about what MARKDOWN can
    hold, which is the only other justification a fact is allowed to have."""
    out = {"headings": {}, "heading_path": [], "block_sequence": []}
    _struct_common._add_heading(out, 9, "Deep")
    assert out["headings"] == {6: 1}
    assert out["block_sequence"] == [("h", 6)]


@pytest.mark.parametrize("items,want", [
    ([], ""),
    (["Slide 1"], "Slide 1"),
    (["Slide 1", "Slide 2", "Slide 3"], "Slide 1; Slide 2; Slide 3"),
    (["Slide 1", "Slide 2", "Slide 3", "Slide 4"],
     "Slide 1; Slide 2; Slide 3, ..."),
])
def test_some_names_the_first_few_locators_and_says_when_it_elided(items, want):
    """A warning that only COUNTS is not checkable against the source. The elision
    marker is load-bearing: without it a reader cannot tell "three" from "the first
    three of eleven"."""
    assert _struct_common._some(items) == want
