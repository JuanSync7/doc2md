"""
title: Unit — the leaf primitives, and the import boundary that keeps the readers independent
kind: tests
layer: backend
summary: The four shareable OOXML primitives behave, and no ground truth is allowed to import the converter it exists to disagree with.
"""
# WHY THIS EXISTS. This package holds several deliberately independent readers of
# the same bytes, and the whole value of the second one is that it can DISAGREE with
# the first. A helper called on both sides of that comparison destroys the property
# silently: the bug lands identically on each side, the delta cancels, and the gate
# reports a confident pass over real damage.
#
# That is not hypothetical here. `_cell_value` was called by both the xlsx converter
# and the xlsx token ground truth, and bugging it left `token_recall` at a clean 1.0
# while the source token count fell 107 -> 73. A one-sided control of the same bug
# read 0.40 and failed loudly, which is what a working gate looks like.
#
# The module docstrings state the rule; nothing enforced it. Every prior statement of
# the boundary was a comment, and a comment does not fail a build. These tests are the
# enforcement, and they are structural (an AST read of the real import statements)
# rather than a grep, so a re-spelled import cannot slip past.
import ast
import os

import pytest

from backend.ingest import _ooxml_leaf

pytestmark = pytest.mark.unit

_INGEST = os.path.dirname(_ooxml_leaf.__file__)


def _relative_imports(module):
    """``{module_name: {imported names}}`` for every ``from .x import y``."""
    with open(os.path.join(_INGEST, module), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            got = out.setdefault(node.module or "", set())
            for alias in node.names:
                got.add(alias.name)
    return out


# ------------------------------------------------- the boundary, enforced

# Each ground truth, and the modules it may NOT reach into. Kept as separate rows
# rather than one union of names so that a new converter helper fails loudly in the
# module that would have reached for it, naming that module in the failure.
_FORBIDDEN = [
    ("_ooxml_struct.py", ("_ooxml_md",),
     "the docx structural ground truth"),
    ("_pptx_struct.py", ("_ooxml_md", "_ooxml_struct", "_xlsx_struct"),
     "the pptx ground truth"),
    ("_xlsx_struct.py", ("_ooxml_md", "_ooxml_struct", "_pptx_struct"),
     "the xlsx ground truth"),
]


@pytest.mark.parametrize("module,forbidden,what", _FORBIDDEN)
def test_a_ground_truth_never_imports_the_converter(module, forbidden, what):
    """A truth that borrows the converter's reading of a document cannot disagree
    with it, and disagreeing is the only thing it is for. Leaf primitives come from
    ``_ooxml_leaf``, which is a reviewed contract; anything else must be written out
    again, deliberately, in the truth's own terms."""
    imported = _relative_imports(module)
    for bad in forbidden:
        assert bad not in imported, (
            "%s (%s) imports %r from .%s -- if those are leaf primitives they belong "
            "in _ooxml_leaf; if they read a document they must be re-implemented here"
            % (module, what, sorted(imported.get(bad, ())), bad))


def test_the_leaf_module_imports_nothing_from_the_package():
    """It is the bottom of the dependency order on purpose. `_ooxml_md` binds its
    dispatch tables at module scope, so once a per-format ground truth owns its
    format's token truth the converter must import from it -- and a leaf module that
    reached back into the package would close the loop and ImportError the whole
    office lane at first import."""
    assert _relative_imports("_ooxml_leaf.py") == {}


def test_the_leaf_module_declares_itself_private():
    """CONVENTIONS §1 makes ``__all__`` the machine-checkable public API. Nothing
    here is public: these are shared *implementation*, and the package re-exporting
    them would invite callers outside this boundary to depend on them."""
    assert _ooxml_leaf.__all__ == []


def test_every_name_the_leaf_module_offers_is_actually_a_leaf():
    """The admission test, executable. A helper qualifies only if it answers a
    question about XML SYNTAX -- what is this tag called, what does this attribute
    say, does this parse. A helper with an opinion about what a paragraph, a cell, a
    heading or a slide IS belongs to exactly one reader.

    This pins the roster so widening it is a deliberate edit with an argument behind
    it, which is exactly how `_SKIP_LOCALS` -- the single non-leaf entry, a
    declaration of SCOPE rather than a reading of content -- came to be justified in
    the module header instead of merely being present."""
    public = set(n for n in vars(_ooxml_leaf) if not n.startswith("__"))
    assert public == {"re", "_ET", "_WS", "_local", "_root",
                      "_BREAK_LOCALS", "_SKIP_LOCALS"}
    assert "_attr" not in public, (
        "an attribute read is where a cell's ADDRESS comes from, so sharing one puts "
        "it on both sides of the comparison -- see the per-reader copies below")


# ------------------------------------------------- the primitives behave

@pytest.mark.parametrize("tag,want", [
    ("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p", "p"),
    ("{http://schemas.openxmlformats.org/drawingml/2006/main}tc", "tc"),
    ("p", "p"),
    ("", ""),
])
def test_local_reads_the_name_past_any_namespace(tag, want):
    assert _ooxml_leaf._local(tag) == want


def test_local_survives_a_non_string_tag():
    """ET gives comments and processing instructions a callable tag, not a str. A
    walker that hit one used to raise mid-document; every one of these readers walks
    whole trees with ``.iter()``, so they all meet them."""
    import xml.etree.ElementTree as ET
    assert _ooxml_leaf._local(ET.Comment) == ""


def _readers():
    """Every module that reads OOXML attributes, and they must each own the read."""
    from backend.ingest import (_ooxml_md, _ooxml_struct, _pptx_struct, _xlsx_struct)
    return (_ooxml_md, _ooxml_struct, _pptx_struct, _xlsx_struct)


def test_every_reader_owns_its_own_attribute_read():
    """`_attr` is deliberately NOT shared, and four copies of five lines is the
    cheapest thing this project buys.

    An attribute read is where a cell's ADDRESS comes from, so one shared copy is
    called on both sides of the comparison. Measured: a shared `_attr` that lost
    every `@r` shifted every value into a different column while `token_recall`,
    `n_source_tokens`, `compared` and the delta list all stayed exactly as they
    were — nothing in the report moved at all."""
    readers = _readers()
    assert all(hasattr(m, "_attr") for m in readers)
    codes = set(id(m._attr.__code__) for m in readers)
    assert len(codes) == len(readers), (
        "two readers share one `_attr` code object, so a bug in it lands on both "
        "sides of the gate and the delta cancels")


@pytest.mark.parametrize("module", _readers(),
                         ids=[m.__name__.rsplit(".", 1)[-1] for m in _readers()])
def test_each_readers_attribute_read_behaves_identically(module):
    """Distinct implementations, one contract. They are separate so a BUG cannot
    land on both sides — not so they may disagree about what an attribute says."""
    el = _ooxml_leaf._root(
        '<c xmlns:r="urn:r" xmlns:w="urn:w" r:id="rId7" w:val="3" plain="x"/>')
    assert module._attr(el, "id") == "rId7"
    assert module._attr(el, "val") == "3"
    assert module._attr(el, "plain") == "x"
    # Callers test the result against tuples of "off" spellings and pass it to
    # int(); a None would become a TypeError deep inside a walk.
    assert module._attr(_ooxml_leaf._root("<c/>"), "val") == ""


def test_a_malformed_part_degrades_to_none_rather_than_raising():
    """A truncated or non-XML part must cost that part's facts, not the bundle. The
    caller's ``if root is None: continue`` is the whole error policy."""
    assert _ooxml_leaf._root("<w:document><unclosed>") is None
    assert _ooxml_leaf._root("") is None
    assert _ooxml_leaf._root("not xml at all") is None


def test_a_well_formed_part_parses():
    assert _ooxml_leaf._local(_ooxml_leaf._root("<w:p xmlns:w='urn:w'/>").tag) == "p"
