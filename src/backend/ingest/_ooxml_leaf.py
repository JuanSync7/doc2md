"""
title: OOXML leaf primitives
layer: backend
public_api: no
summary: The three irreducible namespace-blind primitives every OOXML reader may share, the one skip policy they all obey, and why attribute reads are deliberately NOT here.
"""
# WHY THIS MODULE EXISTS — two reasons, and the second is the load-bearing one.
#
# 1. A DECLARED SHARING CONTRACT. This package holds several deliberately
#    independent readers of the same bytes: the converter (`_ooxml_md`), the
#    converter-blind structural ground truth (`_ooxml_struct` and its per-format
#    siblings), and the exhaustive text ground truths the token gate compares
#    against. They are separate on purpose — a helper shared between two sides of
#    one comparison means a bug lands identically on both, the delta cancels, and
#    the gate reports a confident pass over real damage. That is the NimbusH1
#    mechanism, and this repo has since measured it happening again: bugging
#    `_cell_value`, which the xlsx converter and the xlsx token truth both called,
#    left `token_recall` at a clean 1.0 while the source token count silently fell
#    from 107 to 73.
#
#    The rule that survives that lesson is not "share nothing" — it is:
#
#        A helper may be shared IFF it is called on exactly one side of the
#        comparison it feeds.
#
#    A bug in a one-sided helper makes the ground truth wrong, so a FAITHFUL
#    document produces a delta: a loud failure somebody fixes. A bug in a two-sided
#    helper makes both sides wrong in the same direction, and the gate goes green.
#
#    Everything in this module is a LEAF: it answers a question about XML syntax
#    (what is this tag called, what does this attribute say, does this parse) and
#    knows nothing about documents, structure or meaning. Putting them here makes
#    that judgement explicit and reviewable, instead of a habit of reaching into
#    `_ooxml_md` for whatever happens to be there.
#
#    THE OTHER SHARED MODULE, so nobody has to guess which one a helper belongs in.
#    `_struct_common` holds mechanism with real opinions about documents — parent
#    maps, ancestry, the token notion, the heading record — and is therefore shared
#    on a NARROWER licence: only among the three SOURCE-side structural truths, and
#    never with `_ooxml_md` or `backend.validate._mdstructure`, which are the other
#    side of the comparison those truths feed. This module's licence is wider (the
#    converter may share these four) precisely because these four are irreducible
#    and those are not. A helper that knows what a paragraph IS goes there, or
#    nowhere; a helper that only knows what a tag is called goes here.
#
#    WHAT SHARING THESE COSTS, stated exactly, because an earlier version of this
#    header claimed "there is no structural claim for such a function to be wrong
#    about in a way that could cancel" and that is FALSE. It was measured: make
#    `_local` answer the wrong name for `c`, and a whole workbook's content leaves
#    the markdown at `token_recall: 1.0` with `gate: pass`, because both readers
#    stop seeing cells together. So the honest claim is narrower —
#
#      these four are shared because they are IRREDUCIBLE (every reader of XML needs
#      them, and writing four copies of `_local` buys independence about nothing),
#      and the residual risk is covered by SEPARATE one-sided guards rather than by
#      the sharing being harmless:
#
#        * `_root` returning None on a parse error is a two-sided claim, and a
#          corrupt part is caught instead by `office_convert._CONTENT_PARTS`, which
#          parse-checks every text-bearing part and FAILS the document. That guard
#          is one-sided by construction — it uses `_ET.fromstring` directly, not
#          `_root`.
#        * `_local`/`_SKIP_LOCALS` collapsing a whole class of element is caught by
#          the `n_source_tokens_min` floor pinned per document in
#          `evals/expectations.json`: it is a corpus-level regression pin, not a
#          per-document gate, and that is the limit of the protection.
#
#    A helper that is NOT irreducible does not belong here, however leaf-shaped it
#    looks. `_attr` was moved out for exactly that reason: reading a cell's `r`
#    attribute is six lines, and sharing it let every value shift column with
#    nothing at all moving in the report — recall, `compared`, `n_source_tokens` and
#    the delta list all identical.
#
# 2. IT BREAKS AN IMPORT CYCLE. `_ooxml_md` binds its dispatch tables at module
#    scope (`_SOURCES = {"pptx": pptx_source_text, ...}`). Once a per-format ground
#    truth owns its format's token truth, `_ooxml_md` must import from it — so a
#    ground truth that reached back into `_ooxml_md` for `_local`/`_root` would
#    close the loop and `ImportError` the entire office lane at first import. This
#    module imports nothing from the package, so every reader can depend on it.
#
# WHAT MAY BE ADDED HERE: nothing that reads a document. If a helper has an opinion
# about what a paragraph, a cell, a heading or a slide IS, it belongs to exactly one
# reader and must be written out again in the other. `_SKIP_LOCALS` is the single
# deliberate exception and is argued for at its definition.
import re
import xml.etree.ElementTree as _ET

__all__ = []  # private module: the package re-exports nothing from here

_WS = re.compile(r"\s+")


def _local(tag):
    # type: (str) -> str
    """Local name of a namespaced ET tag (``{uri}p`` -> ``p``).

    Matching on local names keeps the walkers generic across the transitional and
    strict OOXML namespace URIs (and mixed w:/a: content inside drawings)."""
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""                       # comments/PIs have non-str tags


def _root(xml):
    # type: (str) -> object
    """Parsed root or None — malformed parts degrade to empty output, never raise."""
    if not xml:
        return None
    try:
        return _ET.fromstring(xml)
    except _ET.ParseError:
        return None


# Elements that stand for whitespace but carry no text.
_BREAK_LOCALS = ("tab", "br", "cr")
# Subtrees skipped EVERYWHERE (converter and ground truth): Fallback duplicates
# Choice; rPh is furigana phonetic duplication of its base text; moveFrom is the
# tracked-changes "moved away" OLD copy of relocated content (the live copy is in
# moveTo) — keeping it resurrects stale text and duplicates whole sections.
#
# This is the one non-leaf thing in the module, and it is here deliberately. It is a
# declaration of SCOPE, not a reading of content: it says which subtrees are outside
# the comparison altogether. Both sides must agree on scope or they are not comparing
# the same document — the same argument that keeps header/footer parts out of `parts`
# entirely. Sharing it cannot hide damage, because nothing inside a skipped subtree
# is on either side of the gate; disagreeing about it would instead produce deltas
# over text neither side was ever meant to grade.
_SKIP_LOCALS = ("Fallback", "rPh", "moveFrom")
