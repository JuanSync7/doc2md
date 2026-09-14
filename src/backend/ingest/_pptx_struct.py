"""
title: Converter-blind ground truth for pptx
layer: backend
public_api: yes
summary: Every word a deck holds AND every structural fact a faithful conversion must exhibit, read by a flat scan with an ancestor predicate rather than by the converter's recursive shape-tree walk — the second opinion both hard gates compare against — plus the deck's deliberate drops, named and counted.
"""
# WHY THIS FILE EXISTS, AND WHY IT SHARES NOTHING WITH THE CONVERTER
#
# The losslessness gate is only as trustworthy as the independence of its two
# halves. `pptx_markdown` walks a slide's shape tree recursively, dispatching child
# by child; this module answers the same question — "which words does this deck
# hold?" — by a FLAT `root.iter()` stream plus an ancestor predicate over a parent
# map. When the two disagree, the gate fires. When they agree, that agreement means
# something, because it was reached twice by different routes.
#
# It used to mean much less than it looked. `pptx_source_text` lived in the
# converter's own module and called the converter's own helpers, so a bug in one of
# them landed identically on both sides of the comparison and CANCELLED. Measured on
# `kestrel-overview.pptx`, before this module existed:
#
#     healthy                                 recall=1.0  n_source=108  valid=True
#     bug _sp_ph_type (both sides)            recall=1.0  n_source= 20  valid=True
#     bug _collect_text (both sides)          recall=1.0  n_source=  0  valid=True
#     control: bug _md_cell (converter only)  recall=0.89  n_source=108 valid=False
#
# Eighty-two per cent of a deck's source text disappearing while the gate reports a
# clean 1.0 is exactly the NimbusH1 failure the gate was built to end, and the
# control row is what a working gate looks like: a one-sided bug makes a FAITHFUL
# document fail loudly, so somebody fixes it.
#
# THE RULE, stated so the next edit obeys it: a helper may be shared iff it is called
# on exactly one side of the comparison it feeds. From `_ooxml_leaf` this module
# takes only leaf primitives (what is this tag called, what does this attribute say,
# does this parse) and `_SKIP_LOCALS`, which declares SCOPE rather than reading
# content. Everything that reads a document is written out again here, on purpose,
# and must not be "tidied up" by importing the converter's version.
#
# THE POLICY MIRRORED, and why mirroring it is not the same as sharing it. Chrome
# placeholders — slide numbers, dates, footers — are excluded on both sides, because
# a banner repeated on every slide is furniture rather than body text. That is a
# POLICY, and both sides must apply the same one or they are not comparing the same
# document. What must NOT be shared is the READING of it: whether a given shape is
# chrome is a fact this module derives itself, from `p:ph/@type` reached through
# `p:nvPr` — the location the schema actually puts it — rather than by asking the
# converter's predicate. The values are re-spelled below; the derivation is ours.
import re

from ._ooxml_leaf import _local, _root, _WS, _SKIP_LOCALS
# Mechanism shared with the OTHER TWO SOURCE-SIDE TRUTHS and with nothing else — see
# that module's header for why three readers on one side of a comparison may share
# and the converter may not.
from ._struct_common import (_add_heading, _ancestry, _parent_map, _some,
                             _words)


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

__all__ = ["pptx_source_text", "pptx_source_structure", "pptx_policy_drops"]

# Slide and satellite parts, spelled out again rather than imported: a shared regex
# is a shared blind spot, and "which parts hold text" is precisely the question the
# two sides must answer separately.
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES = re.compile(r"^ppt/notesSlides/notesSlide\d+\.xml$")
_DIAGRAM = re.compile(r"^ppt/diagrams/data\d+\.xml$")
_CHART = re.compile(r"^ppt/charts/chart(?:Ex)?\d+\.xml$")
_COMMENTS = re.compile(r"^ppt/comments/[^/]+\.xml$")

# Placeholder roles that are page furniture. Re-spelled from the converter's list on
# purpose (see the header): the two sides must agree on SCOPE, and writing the
# agreement out twice is what makes a silent divergence in it visible as a delta
# rather than invisible as a shared assumption.
_CHROME_ROLES = ("sldNum", "dt", "ftr")

# Elements that stand for whitespace but carry none of their own.
_BREAKS = ("br", "tab", "cr")

# Where a run's characters live. `a:t` in DrawingML; `p:text` is the legacy comment
# spelling and can itself hold runs, so it is collected AND descended into.
_TEXT_LOCALS = ("t", "text")

# Cached chart values: a category or series number is text a reader sees on the
# slide, and it is never a run, so it has to be named separately.
_VALUE_LOCALS = ("v",)


def _chrome_shapes(root, pmap):
    # type: (object, dict) -> set
    """``{id(sp)}`` for every shape whose placeholder role is page furniture.

    Derived from where the schema PUTS the declaration: a ``p:ph`` is a child of
    ``p:nvPr``, inside the shape's non-visual properties. Reading it positively —
    "find the placeholders that are really placeholder declarations" — rather than
    by descending a shape and stopping before its text body means a stray ``ph``
    somewhere unexpected cannot be mistaken for a shape's role, and it does not
    depend on the converter's choice of where to stop."""
    chrome = set()
    for ph in root.iter():
        if _local(ph.tag) != "ph":
            continue
        line = _ancestry(ph, pmap)
        if not any(_local(a.tag) == "nvPr" for a in line):
            continue                      # not a placeholder DECLARATION
        if _attr(ph, "type") not in _CHROME_ROLES:
            continue
        for anc in line:
            if _local(anc.tag) == "sp":
                chrome.add(id(anc))
                break
    return chrome


def _walk_text(root, value_locals=()):
    # type: (object, tuple) -> str
    """Every character the deck shows, in document order, chrome excluded.

    A FLAT pre-order stream, with membership decided per node by looking UP through
    the parent map — the opposite of the converter, which decides by pruning on the
    way DOWN. Adjacent runs concatenate with no inserted space (PowerPoint splits
    single words across runs at formatting boundaries, so a space here would invent
    a word break); paragraph and explicit-break boundaries contribute one space."""
    pmap = _parent_map(root)
    chrome = _chrome_shapes(root, pmap)
    out = []
    for el in root.iter():
        loc = _local(el.tag)
        skip = False
        for anc in _ancestry(el, pmap):
            if id(anc) in chrome or _local(anc.tag) in _SKIP_LOCALS:
                skip = True
                break
        if skip or loc in _SKIP_LOCALS:
            continue
        if loc == "p" or loc in _BREAKS:
            out.append(" ")
        if loc in _TEXT_LOCALS and el.text:
            out.append(el.text)
        elif loc in value_locals and el.text:
            # A discrete value, never run-split, so pad it: two adjacent cached
            # numbers must not glue into one token.
            out.append(" %s " % el.text)
    return _WS.sub(" ", "".join(out)).strip()


def _diagram_points(xml):
    # type: (str) -> str
    """SmartArt node text. Each ``dgm:pt`` is one node, and nodes are separated so
    two adjacent labels cannot fuse into a token neither of them contains."""
    root = _root(xml)
    if root is None:
        return ""
    pmap = _parent_map(root)
    items = []
    for pt in root.iter():
        if _local(pt.tag) != "pt":
            continue
        if any(_local(a.tag) == "pt" for a in _ancestry(pt, pmap)):
            continue                      # a nested point is part of its owner
        text = _walk_text(pt)
        if text:
            items.append(text)
    return " ".join(items)


def _comment_items(xml):
    # type: (str) -> list
    """One item per comment, across all three spellings a deck may use: the legacy
    ``p:cm`` list, the modern one-comment-per-part file, and a bare root."""
    root = _root(xml)
    if root is None:
        return []
    pmap = _parent_map(root)
    found = []
    for cm in root.iter():
        if _local(cm.tag) not in ("cm", "comment"):
            continue
        if any(_local(a.tag) in ("cm", "comment") for a in _ancestry(cm, pmap)):
            continue
        found.append(cm)
    return [t for t in (_walk_text(el) for el in (found or [root])) if t]


def _comment_texts(xml):
    # type: (str) -> str
    """The same comments as one blob, for the token denominator."""
    return " ".join(_comment_items(xml))


def pptx_source_text(parts):
    # type: (dict) -> str
    """Exhaustive ground truth for the deck conversion gate.

    Every text run on every slide, plus speaker notes, SmartArt, chart text and
    comments — regardless of structure, and with page-furniture placeholders
    excluded under the same declared policy the converter applies (derived here
    independently; see the module header).

    Slides are read in PART-NAME order, which is deliberately NOT the deck's
    presentation order. Order does not affect a token multiset, so the gate cannot
    care; and reading `ppt/presentation.xml` here would make this module depend on
    the same part the converter's slide ordering is being fixed to read, which is
    the coupling this file exists to avoid."""
    chunks = []
    slides = sorted((int(_SLIDE.match(n).group(1)), n)
                    for n in parts if _SLIDE.match(n))
    for _, name in slides:
        root = _root(parts.get(name, ""))
        if root is not None:
            chunks.append(_walk_text(root))
    for name in sorted(parts):
        if _NOTES.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_walk_text(root))
        elif _DIAGRAM.match(name):
            chunks.append(_diagram_points(parts[name]))
        elif _CHART.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_walk_text(root, value_locals=_VALUE_LOCALS))
        elif _COMMENTS.match(name):
            chunks.append(_comment_texts(parts[name]))
    return _WS.sub(" ", " ".join(c for c in chunks if c)).strip()


# ============================================================ the STRUCTURAL truth
#
# Everything above answers "which words does this deck hold?" — a MULTISET question,
# and a multiset is blind to arrangement by construction. Measured on the shipped
# `kestrel-reordered.pptx`, whose `p:sldIdLst` says 1, 5, 3, 4, 2 while its slide
# parts are numbered in the order they were drafted:
#
#     published in the deck's order   recall 1.0  n_source 135  valid True
#     published in part-name order    recall 1.0  n_source 135  valid True
#
# Five slides in the wrong order, every word present, nothing to see. Everything
# below answers the second question — does the markdown still MEAN what the deck
# meant? — and the three facts that move on that deck are `block_sequence`,
# `heading_path` and `list_item_words`.
#
# THE TWO JUSTIFICATIONS every fact here must satisfy, and there is no third: a fact
# is stated only if it is (i) a statement about the SOURCE DOCUMENT, or (ii) a
# statement about what MARKDOWN can hold. It may never be a statement about what
# this repo's converter happens to do. Making a ground truth agree with the
# converter is how a gate certifies its own bugs, and the report then says `pass`
# and means nothing.

# An embedded SVG is extracted as TEXT (its labels are real XML), so it lands in a
# `## Figures` section rather than among the pictures.
_MEDIA_SVG = re.compile(r"^ppt/media/[^/]+\.svg$")

# Everything before the part path in a relationship target. A slide's rels are
# written relative to `ppt/slides/` (`../notesSlides/notesSlide1.xml`) and the
# presentation's relative to `ppt/` (`slides/slide1.xml`), so one normaliser that
# strips every leading hop and re-roots at `ppt/` reads both.
_REL_HOP = re.compile(r"^(?:/?ppt/|\.\./|\./)+")

def _rel_id(el):
    # type: (object) -> str
    """The value of the NAMESPACE-QUALIFIED ``id`` attribute (``r:id``), or "".

    Not ``_attr(el, "id")``, and the difference is the whole of a slide's identity.
    A ``p:sldId`` carries TWO attributes whose local name is ``id``: its own
    unqualified ``id="256"``, which is a private serial number, and the qualified
    ``r:id="rId2"``, which is the relationship that says WHICH SLIDE. A reader that
    matches on the local name returns whichever comes first in document order —
    ``256`` — which resolves to no relationship at all, so every entry is dropped
    and the whole ordering silently falls back to the filenames it was written to
    replace. Requiring the brace is what makes it read the right one."""
    for key, value in el.attrib.items():
        if key.startswith("{") and key.rsplit("}", 1)[-1] == "id":
            return value
    return ""


def _rel_pairs(xml):
    # type: (str) -> list
    """``[(Id, Target)]`` for one ``.rels`` part, IN DOCUMENT ORDER.

    The order is load-bearing and a dict threw it away. A slide's ``### Diagram``
    and ``### Chart`` sections publish in the order the slide's relationships are
    written, so sorting by relationship id — a string, where ``rId10`` precedes
    ``rId9`` — put them in a different order than the markdown and FAILED a faithful
    conversion of any slide carrying two embedded parts."""
    root = _root(xml)
    out = []
    if root is None:
        return out
    for rel in root.iter():
        if _local(rel.tag) == "Relationship":
            out.append((_attr(rel, "Id"), _attr(rel, "Target")))
    return out


def _rel_targets(xml):
    # type: (str) -> dict
    """``{Id: Target}`` — for the callers that resolve BY id."""
    return dict(_rel_pairs(xml))


def _rels_name(part):
    # type: (str) -> str
    """The ``.rels`` part that carries one page part's relationships.

    The OPC rule, stated once: ``<dir>/_rels/<file>.rels``. It was spelled as two
    string replacements for `ppt/slides/` and `ppt/notesSlides/`, which silently
    returned `ppt/slideLayouts/slideLayout1.xml.rels` — a part no package contains —
    the moment the bullet cascade needed to walk a LAYOUT's relationships, so the
    whole layout->master chain resolved to nothing and a numbered deck read as
    unnumbered.

    A slide's and its notes page's rIds are numbered INDEPENDENTLY, which is why the
    part's OWN rels is the only correct place to resolve one: a note's ``rId2``
    against the slide's rels follows whatever the slide happens to call ``rId2``."""
    head, _sep, tail = part.rpartition("/")
    return "%s/_rels/%s.rels" % (head, tail) if head else "_rels/%s.rels" % tail


def _part_of(target):
    # type: (str) -> str
    """A relationship target as a package part name (``../charts/c1.xml`` ->
    ``ppt/charts/c1.xml``); "" for an absent or external one."""
    if not target or "://" in target:
        return ""
    return "ppt/" + _REL_HOP.sub("", target)


def _slide_order(parts):
    # type: (dict) -> list
    """The slide parts in the order the deck SHOWS them, from ``p:sldIdLst``.

    DERIVED HERE, and that is not tidiness. The converter has its own reader
    (`pptx_slide_order`), and importing it would put one reader on both sides of
    the comparison this function feeds: a bug in it would reorder the markdown and
    the ground truth identically, the delta would cancel, and the gate would report
    `pass` over a deck nobody presented. Measured in P9.5 — two plausible faults in
    that reader go GREEN under a shared one and RED under this one.

    The differences are deliberate. This reader requires ``p:sldId`` to be a child
    of ``p:sldIdLst`` (found by looking UP through a parent map, so a stray
    ``sldId`` elsewhere in the part cannot claim a position); the converter matches
    the element name anywhere. Only entries whose relationship resolves to a slide
    part that is really in the package count — an unresolvable one is not a
    position, and counting it would shift every slide after it."""
    root = _root(parts.get("ppt/presentation.xml", ""))
    if root is None:
        return []
    pmap = _parent_map(root)
    targets = _rel_targets(parts.get("ppt/_rels/presentation.xml.rels", ""))
    order = []
    seen = set()
    for el in root.iter():
        if _local(el.tag) != "sldId":
            continue
        owner = pmap.get(id(el))
        if owner is None or _local(owner.tag) != "sldIdLst":
            continue
        name = _part_of(targets.get(_rel_id(el), ""))
        if _SLIDE.match(name) and name in parts and name not in seen:
            seen.add(name)
            order.append(name)
    return order


def _slide_sequence(parts):
    # type: (dict) -> list
    """``[(position_or_None, part_number, part_name)]`` for the whole deck.

    Position and part number are two different numbers and the deck says so: a drag
    in PowerPoint rewrites ``p:sldIdLst`` and leaves ``slideN.xml`` where it was. A
    slide part the deck never lists has NO position — it is a deleted-but-not-purged
    slide — so it may not claim one; but its words are in the package and token
    recall counts them, so it still has to publish, after the ordered ones."""
    numbered = sorted((int(_SLIDE.match(n).group(1)), n)
                      for n in parts if _SLIDE.match(n))
    order = _slide_order(parts)
    if not order:
        return [(pos, num, name)
                for pos, (num, name) in enumerate(numbered, start=1)]
    listed = set(order)
    out = [(pos, int(_SLIDE.match(name).group(1)), name)
           for pos, name in enumerate(order, start=1)]
    out += [(None, num, name) for num, name in numbered if name not in listed]
    return out


def _new_facts():
    # type: () -> dict
    """The fact vector this module fills in, and the argument for each stated zero.

    WHAT IS STATED AS ZERO, and why a zero is worth stating. A deck holds no
    construct that renders as a horizontal rule or a code fence. Any of those
    appearing in the markdown is therefore a SUBSTITUTION — document text replaced
    by punctuation carrying none of it — and a substitution deletes the characters
    it replaces, so token recall reads a vacuous 1.0 over the damage. Measured, and
    this is the defect the zero was written for: a slide bullet whose text is
    ``- - -`` was emitted ``- - - -``, which CommonMark reads as a thematic break.
    `bullet_items` 16 -> 15, `thematic_breaks` 0 -> 1, `recall` 1.0, no warning.

    WHAT IS OMITTED, and why omission is not the same as zero. Bold, italic,
    strikethrough and hyperlinks are real DrawingML that ``pptx_markdown`` does not
    emit. Stating ``strong: 0`` would make this module inherit the converter's blind
    spot on the very axis it exists to police: it would CERTIFY the drop. Leaving
    the key out puts it in the report's ``unmeasured`` list by name, and the loss
    gets a counted warning from ``pptx_policy_drops`` instead.

    ``ordered_items``/``ordered_numbers`` were that middle case until P9.8b and are
    now stated unconditionally, because the converter stopped dropping the ordinal.
    Nothing in this vector is conditional any more, which is what emptied a deck
    report's ``unmeasured`` list."""
    return {"headings": {}, "heading_path": [], "block_sequence": [],
            "list_items": {}, "bullet_items": 0, "list_item_words": [],
            "tables": [],
            # ORDERED ITEMS, stated rather than conditional. `a:buAutoNum` is real
            # ordering markdown holds perfectly well, and for as long as the deck
            # converter dropped the ordinal these two were OMITTED — never zeroed —
            # because a zero would have certified the drop. It emits `1.` `2.` `3.`
            # now (P9.8b), resolving the whole layout -> master cascade, so a zero
            # here is a falsifiable claim about the SOURCE and an ordered marker
            # appearing in the markdown anyway is a fabrication the gate can see.
            "ordered_items": 0, "ordered_numbers": [],
            "thematic_breaks": 0, "code_spans": 0, "code_blocks": 0,
            # Not a graded fact and not compared — the leading underscore keeps it
            # out of `_FIDELITY_FACTS` — but the OUTLINE LEVELS the render could not
            # hold have to be counted somewhere, and this is the only walk that
            # knows. `pptx_policy_drops` reads it back and publishes the receipt.
            "_flattened_levels": 0}


def _add_bullet(out, open_levels, level, text, bullet=None, nums=None):
    # type: (dict, list, int, str, object, list) -> None
    """One list item, at the depth a RENDERER will really show it at.

    THE DEPTH IS CONTAINMENT, and it is a fact about markdown rather than a
    concession to the converter. CommonMark nests a child item only under a parent
    that exists, so an item's depth is how many STRICTLY SHALLOWER ancestors are
    still open above it: an item at outline level 2 with nothing at level 1 above it
    is a child of the level-0 item, and the NEXT item at level 2 is that item's
    SIBLING, not its child. A truth that read ``a:pPr/@lvl`` straight off the source
    would demand a depth no renderer on earth produces, and would fail every
    faithful conversion of a deck whose author skipped a level, permanently.

    WHAT THIS RULE IS NOT, because the first version of it got this exactly wrong:
    it is not "one deeper than the deepest item currently open". That is right for
    the FIRST item at a skipped level and wrong for every one after it, since each
    finds the stack one entry taller — three peer bullets at level 2 came out as a
    three-deep chain, so a bring-up slide read as though its steps nested. And
    because the converter's own clamp had the same shape and this function had been
    written to mirror it, the delta CANCELLED: 199 of 336 level sequences of length
    2-4 published misrepresented at `gate: pass, deltas: [], recall: 1.0`. The rule
    is derived here from containment, not copied from `_list_indent`, and the two
    now disagree the moment either is wrong.

    The outline level a deck states is not thrown away silently: what it costs is
    counted as ``flattened_list_levels`` (`pptx_policy_drops`), the same shape the
    lane already uses for a table span GFM cannot hold. That claim was prose only
    for one review cycle — the code did not exist — which is `dropped_headers_footers`
    all over again, so the count is now read back out of this very walk.

    ``open_levels`` is the stack of ancestor items still open, by SOURCE level. Any
    other block ends the list, and that is read back off ``block_sequence`` rather
    than maintained by hand, so no call site can forget to say so."""
    if not out["block_sequence"] or out["block_sequence"][-1][0] != "li":
        del open_levels[:]
        if nums is not None:
            del nums[:]
    # `ST_TextIndentLevelType` is 0-8; a negative level is malformed, and reporting a
    # depth of -1 would fail a document no renderer can disagree about.
    level = max(0, level)
    while open_levels and open_levels[-1] >= level:
        open_levels.pop()
    depth = len(open_levels)
    open_levels.append(level)
    out["list_items"][depth] = out["list_items"].get(depth, 0) + 1
    ordered = bullet is not None and _local(bullet.tag) == "buAutoNum"
    if ordered:
        out["ordered_items"] += 1
        if nums is not None:
            out["ordered_numbers"].append(
                _ordinal(nums, level, _num_of(_attr(bullet, "startAt"), 1)))
    else:
        out["bullet_items"] += 1
        if nums is not None:
            _ordinal(nums, level, 1, reset=True)
    out["list_item_words"].append(_words(text))
    out["block_sequence"].append(("li", depth))
    if depth != level:
        out["_flattened_levels"] = out.get("_flattened_levels", 0) + 1


# THE BULLET CASCADE, derived here and NOT shared with `_ooxml_md`'s.
#
# A DrawingML paragraph inherits its bullet from, nearest first: its own `a:pPr`, the
# shape's `a:txBody/a:lstStyle/a:lvlNpPr`, the slide LAYOUT's matching placeholder
# `a:lstStyle`, and the slide MASTER's `p:txStyles/p:bodyStyle`. Reading only the
# first says "no numbering" over a deck whose whole body list is numbered from its
# theme, which is why `ordered_items` was `unmeasured` rather than zero until this.
#
# The converter resolves the same chain with its own code. That duplication IS the
# gate: two readers agree only when both are right, and one shared resolver would
# make a bug in it land on both sides and cancel.
_BU_LOCALS = ("buAutoNum", "buChar", "buNone")
_LVL_PR = re.compile(r"^lvl([1-9])pPr$")


def _bullet_decl(ppr):
    # type: (object) -> object
    """The bullet an `a:pPr`-shaped element declares, or ``None``.

    `a:buNone` comes back like any other: "explicitly no bullet" is a DECISION that
    must beat an inherited number, not an absence that lets one through."""
    if ppr is None:
        return None
    for ch in ppr:
        if _local(ch.tag) in _BU_LOCALS:
            return ch
    return None


def _levels_of(lst):
    # type: (object) -> dict
    """``{outline level: bullet}`` from an `a:lstStyle`.

    `a:lvl1pPr` is level 0. The file counts from one and every other reader here
    counts from zero; getting that off by one applies the wrong level's bullet."""
    out = {}
    if lst is None:
        return out
    for ch in lst:
        m = _LVL_PR.match(_local(ch.tag))
        if not m:
            continue
        decl = _bullet_decl(ch)
        if decl is not None:
            out[int(m.group(1)) - 1] = decl
    return out


def _find_one(el, local):
    # type: (object, str) -> object
    for sub in el.iter():
        if _local(sub.tag) == local:
            return sub
    return None


def _theme_bullets(parts, slide_part):
    # type: (dict, str) -> dict
    """``{(ph type, ph idx): {level: bullet}}`` from one slide's layout and master.

    Followed by RELATIONSHIP, never by filename: a deck with two masters resolves
    each layout to the master ITS rels name, and guessing `slideMaster1` would apply
    another theme's numbering. The master's `p:bodyStyle` is filed under
    ``(None, None)`` because it applies to every body placeholder."""
    out = {}
    for target in _rel_targets(parts.get(_rels_name(slide_part), "")).values():
        layout = _part_of(target)
        if not layout.startswith("ppt/slideLayouts/"):
            continue
        root = _root(parts.get(layout, ""))
        if root is not None:
            for sp in root.iter():
                if _local(sp.tag) != "sp":
                    continue
                ph = _find_one(sp, "ph")
                levels = _levels_of(_find_one(sp, "lstStyle"))
                if ph is not None and levels:
                    out[(_attr(ph, "type") or "body", _attr(ph, "idx"))] = levels
        for mt in _rel_targets(parts.get(_rels_name(layout), "")).values():
            master = _part_of(mt)
            if not master.startswith("ppt/slideMasters/"):
                continue
            mroot = _root(parts.get(master, ""))
            if mroot is None:
                continue
            for styles in mroot.iter():
                if _local(styles.tag) == "bodyStyle":
                    levels = _levels_of(styles)
                    if levels:
                        out.setdefault((None, None), levels)
    return out


def _effective_bullet(p, pmap, theme):
    # type: (object, dict, dict) -> object
    """This paragraph's bullet after the whole cascade, or ``None``."""
    own = None
    for pr in p:
        if _local(pr.tag) == "pPr":
            own = _bullet_decl(pr)
    if own is not None:
        return own
    level = _para_level(p)
    shape = None
    body = None
    for anc in _ancestry(p, pmap):
        loc = _local(anc.tag)
        if loc == "txBody" and body is None:
            body = anc
        elif loc == "sp":
            shape = anc
            break
    if body is not None:
        levels = _levels_of(_find_one(body, "lstStyle"))
        if level in levels:
            return levels[level]
    ph = ("body", None)
    if shape is not None:
        el = _find_one(shape, "ph")
        if el is not None:
            ph = (_attr(el, "type") or "body", _attr(el, "idx"))
    for key in (ph, (ph[0], None), (None, None)):
        levels = theme.get(key)
        if levels and level in levels:
            return levels[level]
    return None


def _num_of(value, default):
    # type: (str, int) -> int
    """An integer attribute, or ``default`` when absent or junk."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ordinal(nums, level, start, reset=False):
    # type: (list, int, int, bool) -> int
    """The number a reader SEES on this item, and the state the next one needs.

    One counter per outline LEVEL, so a deeper level restarts whenever a shallower
    one advances. ``reset`` records an unordered item interrupting the run without
    consuming an ordinal — the `1, 2, 1, 2` shape `ordered_numbers` exists to show."""
    level = max(0, level)
    while len(nums) <= level:
        nums.append(None)
    del nums[level + 1:]
    if reset:
        nums[level] = None
        return start
    nums[level] = start if nums[level] is None else nums[level] + 1
    return nums[level]


def _para_level(p):
    # type: (object) -> int
    """The outline level a paragraph declares, from ``a:pPr/@lvl``."""
    for pr in p:
        if _local(pr.tag) == "pPr":
            try:
                return int(_attr(pr, "lvl") or "0")
            except ValueError:
                return 0
    return 0


def _para_text(p, pmap):
    # type: (object, dict) -> str
    """One paragraph's characters, by the same flat run scan the token side uses."""
    out = []
    for el in p.iter():
        loc = _local(el.tag)
        if any(_local(a.tag) in _SKIP_LOCALS for a in _ancestry(el, pmap)):
            continue
        if loc in _SKIP_LOCALS:
            continue
        if loc in _BREAKS:
            out.append(" ")
        elif loc in _TEXT_LOCALS and el.text:
            out.append(el.text)
    return _WS.sub(" ", "".join(out)).strip()


def _cell_text(tc, pmap):
    # type: (object, dict) -> str
    """One table cell's characters, its paragraphs separated by a space.

    A cell's paragraph boundary is real content: two paragraphs reading `divider`
    and `ratio` must not weld into `dividerratio`, a token no document contains.
    The deck converter collapses a cell's whitespace to single spaces on ONE line,
    so a space is what it renders too — this is not the docx path, which writes a
    multi-paragraph cell as ``a<br>b`` because that one preserves the newline."""
    return " ".join(t for t in
                    (_para_text(p, pmap) for p in tc.iter()
                     if _local(p.tag) == "p") if t)


def _grid_cols(tbl):
    # type: (object) -> int
    """How many columns the table DECLARES, from ``a:tblGrid``.

    A deck states its grid, so the width is read rather than inferred. It has to be:
    a horizontally merged cell is written as an origin ``a:tc`` carrying
    ``gridSpan`` followed by the covered ones carrying ``hMerge``, and a covered
    cell is empty — so a span in the LAST column looks exactly like a styled-but-
    valueless trailing column, and inferring the width would silently narrow the
    table.

    Only THIS table's own ``a:tblGrid`` counts. A table nested in a cell declares a
    grid too, and a flat count of every ``gridCol`` in the subtree made the owner as
    wide as both of them put together."""
    n = 0
    for grid in tbl:
        if _local(grid.tag) != "tblGrid":
            continue
        for col in grid:
            if _local(col.tag) == "gridCol":
                n += 1
    return n


def _table_of(tbl, pmap):
    # type: (object, dict) -> dict
    """``{rows, cols, has_header, cells}`` for one DrawingML table, or ``{}``.

    Every ``a:tc`` is a grid position, including the ones a merge covers: DrawingML
    keeps them and marks them, so a merged rectangle's continuation cells are
    present and empty, which is exactly what a GFM table without colspan shows.

    ``cells`` is what makes a TRANSPOSITION visible: exchange two values between
    rows and the dimensions, the counts and the token multiset are all unchanged,
    while the latency table now names the wrong owner."""
    rows = []
    for tr in tbl.iter():
        if _local(tr.tag) != "tr":
            continue
        if any(a is not tbl and _local(a.tag) == "tbl"
               for a in _ancestry(tr, pmap)):
            continue                      # belongs to a table nested in a cell
        cells = []
        for tc in tr.iter():
            if _local(tc.tag) != "tc":
                continue
            if any(a is not tr and _local(a.tag) == "tr"
                   for a in _ancestry(tc, pmap)):
                continue
            cells.append(_cell_text(tc, pmap))
        if cells:
            rows.append(cells)
    # A row with nothing in it renders nothing: the converter drops it and so does
    # every reader, because a blank pipe row is not a row of the table a reader sees.
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return {}
    width = max(1, _grid_cols(tbl))
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if r[i].strip():
                width = max(width, i + 1)
                break
    return {"rows": len(rows), "cols": width, "has_header": True,
            "cells": [tuple(_words(c) for c in (list(r) + [""] * width)[:width])
                      for r in rows]}


def _shape_facts(root, out, want_title, theme=None):
    # type: (object, dict, bool, dict) -> str
    """Fill ``out`` from one slide's (or notes part's) shape tree; return the title.

    A FLAT pre-order stream over the part, with membership decided per node by
    looking UP through a parent map — the opposite of the converter, which walks the
    shape tree recursively and dispatches child by child. A converter that forgets
    to recurse into a wrapper element still shows up here, which is the whole of the
    independence claim.

    Document order is preserved because ``iter()`` is pre-order, so a table and the
    bullets around it land in the sequence the slide really has them in."""
    pmap = _parent_map(root)
    chrome = _chrome_shapes(root, pmap)
    title_sp = None
    title = ""
    if want_title:
        # The heading takes the FIRST title placeholder that carries text; a second
        # one is body copy, because only one title can be in the heading and its
        # words are in the package either way.
        for ph in root.iter():
            if _local(ph.tag) != "ph" or _attr(ph, "type") not in ("title", "ctrTitle"):
                continue
            line = _ancestry(ph, pmap)
            if not any(_local(a.tag) == "nvPr" for a in line):
                continue                  # not a placeholder DECLARATION
            sp = None
            for anc in line:
                if _local(anc.tag) == "sp":
                    sp = anc
                    break
            if sp is None or id(sp) in chrome:
                continue
            paras = [_para_text(p, pmap) for p in sp.iter() if _local(p.tag) == "p"]
            paras = [t for t in paras if t]
            if paras:
                title_sp, title = sp, " ".join(paras)
                break
    open_levels = []
    nums = []  # type: list  # type: list
    for el in root.iter():
        loc = _local(el.tag)
        if loc not in ("p", "tbl"):
            continue
        line = _ancestry(el, pmap)
        if any(id(a) in chrome or _local(a.tag) in _SKIP_LOCALS for a in line):
            continue
        if title_sp is not None and any(a is title_sp for a in line):
            continue                      # already in the heading
        if loc == "tbl":
            if any(_local(a.tag) == "tbl" for a in line):
                continue                  # a table in a cell is flattened into it
            fact = _table_of(el, pmap)
            if fact:
                out["tables"].append(fact)
                out["block_sequence"].append(("table", (fact["rows"], fact["cols"])))
            continue
        if any(_local(a.tag) == "tbl" for a in line):
            continue                      # a table cell's own paragraphs
        text = _para_text(el, pmap)
        if text:
            _add_bullet(out, open_levels, _para_level(el), text,
                        _effective_bullet(el, pmap, theme or {}), nums)
    return title


def _diagram_texts(xml):
    # type: (str) -> list
    """One item per SmartArt point that carries text — the deck renders them as a
    bullet list, so each is a list item."""
    root = _root(xml)
    if root is None:
        return []
    pmap = _parent_map(root)
    items = []
    for pt in root.iter():
        if _local(pt.tag) != "pt":
            continue
        line = _ancestry(pt, pmap)
        if any(_local(a.tag) == "pt" for a in line):
            continue                      # a nested point is part of its owner
        # The scope test has to be rooted at the PART. `_walk_text(pt)` builds its
        # parent map from the point down, so it cannot see an `mc:Fallback` ABOVE
        # it — and a Fallback is a duplicate rendering of its Choice sibling, so its
        # points were counted a second time.
        if any(_local(a.tag) in _SKIP_LOCALS for a in line):
            continue
        text = _walk_text(pt)
        if text:
            items.append(text)
    return items


def _has_text(xml, value_locals=()):
    # type: (str, tuple) -> bool
    """Does this part carry any text at all? A textless chart opens no section."""
    root = _root(xml)
    return root is not None and bool(_walk_text(root, value_locals))


def _svg_labels(xml):
    # type: (str) -> bool
    """Does this SVG carry label text? Its ``<text>`` elements are real document
    text, unlike a raster screenshot, so they publish under ``## Figures``."""
    root = _root(xml)
    if root is None:
        return False
    for el in root.iter():
        if _local(el.tag) == "text" and "".join(el.itertext()).strip():
            return True
    return False


_HLINK_LOCALS = ("hlinkClick", "hlinkHover")
_EXTERNAL_URL = ("http://", "https://", "mailto:")


def _rpr_marks(r):
    # type: (object) -> tuple
    """The emphasis one ``a:r`` declares, as a canonical tuple.

    DrawingML spells these as ATTRIBUTES of ``a:rPr``, and every OFF spelling a deck
    really writes — ``b="0"``, ``i="false"``, ``strike="noStrike"`` — is not
    emphasis. Counting one would demand a span the render is right not to draw, and
    fail a faithful conversion of an entirely ordinary deck."""
    marks = []
    for ch in r:
        if _local(ch.tag) != "rPr":
            continue
        for attr, name in (("b", "strong"), ("i", "em"), ("strike", "strike")):
            if _attr(ch, attr).lower() not in ("",) + _OFF_VALS:
                marks.append(name)
        break
    return tuple(marks)


def _rpr_link(r, rels):
    # type: (object, dict) -> str
    """The EXTERNAL url this run links to, or ``""``.

    An internal slide jump resolves to nothing on purpose: it has no address a
    reader outside the deck could follow, so the render keeps the text and drops the
    link, and a truth that counted it would fail a faithful conversion."""
    for ch in r:
        if _local(ch.tag) != "rPr":
            continue
        for sub in ch:
            if _local(sub.tag) in _HLINK_LOCALS:
                url = rels.get(_rel_id(sub) or "", "")
                if url.startswith(_EXTERNAL_URL):
                    return url
        break
    return ""


def _para_spans(p, rels):
    # type: (object, dict) -> dict
    """{"strong"/"em"/"strike"/"links": count} for one ``a:p``.

    THE UNIT IS THE SPAN A RENDERER SHOWS, not the run the deck stores, and the
    difference is not cosmetic: PowerPoint splits a word across runs at any property
    boundary, so a bold ``DmaArbiter`` is routinely two runs. Emitted one marker pair
    per run that is ``**Dma****Arbiter**``, which is ONE bold span to every reader of
    markdown — and to the markdown-side fact this is compared against. Adjacent runs
    whose mark set is IDENTICAL are therefore one span; anything between them that
    renders — including a run of plain spaces — separates them into two.

    Runs with no characters at all are skipped, because they contribute no segment to
    render; whitespace-only runs at either END are skipped too, because the paragraph
    is stripped. Both rules are here to predict the RENDER, which is the only thing
    justification (ii) allows this module to state.

    Derived from the source, never from the converter's own grouping: the two agree
    because both are right about what markdown means, and if one is wrong the deck
    fails loudly instead of the delta cancelling."""
    runs = []  # type: list
    for el in p.iter():
        if _local(el.tag) not in ("r", "fld"):
            continue
        text = "".join(t.text or "" for t in el.iter()
                       if _local(t.tag) in _TEXT_LOCALS)
        if not text:
            continue
        runs.append((_rpr_marks(el), _rpr_link(el, rels), text))
    while runs and not runs[0][2].strip():
        del runs[0]
    while runs and not runs[-1][2].strip():
        del runs[-1]
    out = {"strong": 0, "em": 0, "strike": 0, "links": 0}
    prev_marks = None  # type: object
    prev_url = None  # type: object
    for marks, url, _text in runs:
        if marks != prev_marks:
            for mark in marks:
                out[mark] += 1
        if url and url != prev_url:
            out["links"] += 1
        prev_marks, prev_url = marks, url
    return out


def _emphasis_and_links(parts):
    # type: (dict) -> dict
    """Every in-scope paragraph's spans, summed over the deck.

    Chrome is excluded on both counts, the same scope every other warning and fact
    here obeys: a bold footer publishes nothing, so it can neither lose emphasis nor
    demand any."""
    out = {"strong": 0, "em": 0, "strike": 0, "links": 0}
    for name, root in _text_parts(parts):
        rels = _rel_targets(parts.get(_rels_name(name), ""))
        pmap = _parent_map(root)
        chrome = _chrome_shapes(root, pmap)
        for el in root.iter():
            if _local(el.tag) != "p" or not _in_scope(el, pmap, chrome):
                continue
            for fact, count in _para_spans(el, rels).items():
                out[fact] += count
    return out


# Structure a deck carries that NO name in the closed fact list can express, so a
# `pass` never claimed it. Declared rather than discovered: `unmeasured` is the
# weaker statement (a fact this VECTOR has that this truth did not supply, closable
# by writing code); this list closes only by widening the vector, or never.
#
#   slide_geometry  where a shape sits on the canvas: two columns of bullets side by
#                   side and the same bullets stacked flatten to one identical run.
#   shape_grouping  a `p:grpSp`'s membership — its children render as peer bullets.
#   shape_role      a floating callout, a body placeholder and a connector's label
#                   all render as `- `; markdown has no word for which one it was.
#   header_row      `a:tblPr/@firstRow`: GFM's first row is ALWAYS the header, so a
#                   table that declares it has none gets one fabricated.
# Every entry has to be TRUE and non-vacuous: three were deleted from the workbook's
# list in review for saying something false, and `blind_to` is read as a disclosure.
# Deliberately NOT here: transitions and animations, which were measured to leave
# both the markdown and the source text byte-identical — they carry no reader-visible
# text, so they are not structure this gate is failing to grade; and the bullet
# glyph, which markdown CAN hold (`ordered_items`) and so belongs to `unmeasured` by
# that field's own definition.
_BLIND_TO = ("slide_geometry", "shape_grouping", "shape_role", "header_row")


def pptx_source_structure(parts):
    # type: (dict) -> dict
    """The structural facts a faithful conversion of this deck must exhibit.

    Same keys as ``backend.validate.md_structure`` so the two compare directly, and
    reached by a different route: a flat scan with an ancestor predicate over a
    parent map, against the converter's recursive shape-tree descent.

    There is no ``emit_images`` flag, and its absence is measured rather than
    assumed. A workbook's pictures are grouped into a trailing ``## Images``
    heading, so the workbook truth has to know whether images are being emitted; a
    deck's picture renders in place as an HTML-comment sentinel that the bundle
    writer resolves to an inline image link. An inline image is not a block kind in
    the fact vector, so nothing here moves either way."""
    out = _new_facts()
    out["_blind_to"] = list(_BLIND_TO)
    # CONDITIONAL ZEROS. The rule "omit rather than certify" protects against the
    # converter DROPPING a mark; on its own it leaves the opposite direction wide
    # open, because a fact nobody states is a fact nobody can see FABRICATED either.
    # Measured with `_esc` reduced to the identity, on decks whose bullets read
    # `set *ready* high` and `the ~~week six~~ date`: the markdown renders real
    # emphasis and a real strikethrough over words the deck wrote literally, and
    # EMPHASIS AND HYPERLINKS, counted rather than conditionally zeroed.
    #
    # These four were OMITTED from the vector for as long as `pptx_markdown` dropped
    # them — reported as `unmeasured` by name — because a stated `strong: 0` over a
    # deck that draws bold would have CERTIFIED the loss on the very axis this module
    # exists to police. The converter emits them now, so the omission ends the only
    # way it is allowed to: by counting. A zero here is a real, falsifiable claim
    # about the SOURCE, which is what makes a `**` appearing in the markdown anyway a
    # fabrication the gate can see.
    out.update(_emphasis_and_links(parts))

    used_embedded = set()
    used_notes = set()
    for pos, num, name in _slide_sequence(parts):
        root = _root(parts.get(name, ""))
        if root is None:
            # A slide part that does not parse is no section. It cannot be silently
            # lost either: `office_convert.malformed_content_part` fails the whole
            # document before this is ever reached.
            continue
        slide = _new_facts()
        title = _shape_facts(root, slide, want_title=True,
                             theme=_theme_bullets(parts, name))
        heading = ("Slide %d" % pos if pos is not None
                   else "Slide (unlisted): slide%d" % num)
        _add_heading(out, 2, heading + (" " + title if title else ""))
        _merge(out, slide)

        rels = _rel_pairs(parts.get("ppt/slides/_rels/slide%d.xml.rels" % num, ""))
        notes = []
        embedded = []  # type: list
        for _rid, target in rels:
            part = _part_of(target)
            if part not in parts:
                continue
            if _DIAGRAM.match(part) or _CHART.match(part):
                # Deduped WITHIN a slide and never across the deck: a diagram two
                # slides both reference publishes a section under EACH of them,
                # because each slide really shows it. Suppressing the second cost a
                # heading, its bullets and their place in the sequence, and failed a
                # faithful conversion with six deltas at recall 1.0.
                if part not in embedded:
                    embedded.append(part)
            elif _NOTES.match(part) and part not in notes:
                notes.append(part)
        for part in embedded:
            used_embedded.add(part)       # for the orphan pass only
            _embedded_section(out, part, parts[part])
        # The RELATIONSHIP is the normative slide->notes binding; the same-numbered
        # filename is only the fallback, and part numbering is a convention that
        # spec-legal packages are free to break.
        for note in (notes or [n for n in ("ppt/notesSlides/notesSlide%d.xml" % num,)
                               if n in parts]):
            if note not in used_notes:
                used_notes.add(note)
                _notes_section(out, parts[note])

    orphans = [n for n in sorted(parts)
               if (_DIAGRAM.match(n) or _CHART.match(n)) and n not in used_embedded]
    orphan_facts = _new_facts()
    if any([_embedded_body(orphan_facts, n, parts[n]) for n in orphans]):
        # One section for the lot of them, after the slides: a diagram or chart no
        # slide references is still text in the package, and the token gate counts
        # it, so it can never simply be dropped.
        _add_heading(out, 2, "Embedded objects")
        _merge(out, orphan_facts)

    # A notes part no slide reached still publishes: losing the BINDING must never
    # cost the words.
    for name in sorted(parts):
        if _NOTES.match(name) and name not in used_notes:
            _notes_section(out, parts[name])

    items = []
    for name in sorted(parts):
        if _COMMENTS.match(name):
            items.extend(_comment_items(parts[name]))
    if items:
        # Comments render as `- item` lines, which a renderer reads as a LIST.
        _add_heading(out, 2, "Comments")
        levels = []  # type: list
        for text in items:
            _add_bullet(out, levels, 0, text)

    # `## Figures` is appended by `ooxml_markdown` for EVERY format, after the
    # format's own sections, whenever an embedded SVG carries label text.
    if any(_MEDIA_SVG.match(n) and _svg_labels(parts[n]) for n in sorted(parts)):
        _add_heading(out, 2, "Figures")
    return out


def _merge(dst, src):
    # type: (dict, dict) -> None
    """Fold one section's facts into the document's, keeping document order."""
    for depth, n in src["list_items"].items():
        dst["list_items"][depth] = dst["list_items"].get(depth, 0) + n
    dst["bullet_items"] += src["bullet_items"]
    # The ordered facts fold the same way, and they have to be listed HERE or a
    # per-slide count is computed and then thrown away — which is exactly what
    # happened for one run of this slice: `bullet_items` fell (the item was
    # correctly not a bullet) while `ordered_items` stayed 0, so a numbered deck
    # reported as having no list items at all.
    dst["ordered_items"] += src["ordered_items"]
    dst["ordered_numbers"].extend(src["ordered_numbers"])
    dst["list_item_words"].extend(src["list_item_words"])
    dst["tables"].extend(src["tables"])
    dst["block_sequence"].extend(src["block_sequence"])
    dst["_flattened_levels"] = (dst.get("_flattened_levels", 0)
                                + src.get("_flattened_levels", 0))


def _embedded_section(out, name, xml):
    # type: (dict, str, str) -> None
    """A diagram or chart a slide references: its own level-3 heading, then its
    body. A part carrying no text opens no section at all."""
    body = _new_facts()
    if not _embedded_body(body, name, xml):
        return
    _add_heading(out, 3, "Diagram" if _DIAGRAM.match(name) else "Chart")
    _merge(out, body)


def _embedded_body(out, name, xml):
    # type: (dict, str, str) -> bool
    """What an embedded part contributes, and whether it is a section at all.

    SmartArt renders as one bullet per node; a chart renders as free prose, which is
    deliberately NOT a block kind — neither side of this gate has an opinion about
    where a paragraph lands. So a chart contributes nothing countable and the
    return value is the only thing that knows its heading has to exist."""
    if _DIAGRAM.match(name):
        texts = _diagram_texts(xml)
        levels = []  # type: list
        for text in texts:
            _add_bullet(out, levels, 0, text)
        return bool(texts)
    return _has_text(xml, _VALUE_LOCALS)


def _notes_section(out, xml):
    # type: (dict, str) -> None
    """One ``### Speaker notes`` section. The notes' own title shape renders as the
    section's opening paragraph rather than as an item, so it contributes no block —
    and a notes part with nothing in it opens no section."""
    root = _root(xml)
    if root is None:
        return
    body = _new_facts()
    title = _shape_facts(root, body, want_title=True)
    if not title and not body["block_sequence"]:
        return
    _add_heading(out, 3, "Speaker notes")
    _merge(out, body)


# ==================================================== measured policy drops
#
# end-goal.md §1: the drop is always DELIBERATE AND VISIBLE, never an accident. A
# drop nobody counted reads exactly like a bug — and worse, it reads like nothing at
# all.
#
# What makes the deck's list different from the workbook's is that most of these are
# invisible to token recall for a REASON no other format has: the text is excluded
# from the DENOMINATOR too. A page-furniture placeholder is dropped by the converter
# and by `pptx_source_text` under the same declared policy, so `n_source` is the same
# number with the banner and without it. A symmetric exclusion cannot move a recall
# metric however much it removes, and a shape excluded from both sides contributes to
# none of the sixteen compared structural facts either. Measured: a footer, a date
# and a slide number carrying 59 characters between them leave the markdown
# byte-identical, `n_source` at 8, `recall` at 1.0 and `warnings` empty. These counts
# are the only record there can be.
#
# Each warning names WHAT was lost, HOW MUCH, and WHAT WAS KEPT.

_OFF_VALS = ("0", "false", "off", "none", "nostrike")

# Where a bullet declaration really belongs: a child of the paragraph's own `a:pPr`.
# The same element names appear inside `a:lstStyle` as per-level DEFAULTS, and
# counting those would report a loss on a deck that has none — and, worse, would make
# `ordered_items` unmeasurable on a deck whose paragraphs are all plain.
_BULLET_LOCALS = {"buAutoNum": "auto_numbered", "buChar": "custom_char",
                  "buNone": "suppressed"}


def _text_parts(parts):
    # type: (dict) -> list
    """``[(part_name, root)]`` for every part whose shapes a reader sees."""
    out = []
    for name in sorted(parts):
        if _SLIDE.match(name) or _NOTES.match(name):
            root = _root(parts[name])
            if root is not None:
                out.append((name, root))
    return out


def _positions(parts):
    # type: (dict) -> dict
    """``{part_name: "Slide N"}`` — a locator a reader can count to.

    The POSITION, not the part number: a drag rewrites `p:sldIdLst` and leaves
    `slideN.xml` where it was, so quoting the filename would send a reader to a
    slide that is somewhere else entirely."""
    where = {}
    for pos, num, name in _slide_sequence(parts):
        label = ("Slide %d" % pos if pos is not None
                 else "Slide (unlisted): slide%d" % num)
        where[name] = label
        # A notes part is a page too, and it was getting its raw package path: on a
        # reordered deck that reads `ppt/notesSlides/notesSlide1.xml`, which is the
        # notes of the slide published FIFTH. A locator a reader cannot count to is
        # not a locator.
        rels = _rel_pairs(parts.get("ppt/slides/_rels/slide%d.xml.rels" % num, ""))
        for _rid, target in rels:
            note = _part_of(target)
            if _NOTES.match(note) and note not in where:
                where[note] = "Speaker notes of " + label
    for name in sorted(parts):
        if _NOTES.match(name) and name not in where:
            where[name] = "Speaker notes (%s)" % name.rsplit("/", 1)[-1]
    return where


def _in_scope(el, pmap, chrome):
    # type: (object, dict, set) -> bool
    """Is this node part of the document the reader gets?

    Three of the warnings below scan a whole part with a bare ``root.iter()`` and
    then assert that the TEXT survived — "every character is kept and the emphasis
    is not". Two classes of node make that sentence false. A CHROME shape's text is
    not kept: it is excluded from the markdown and from the denominator, and
    `dropped_slide_chrome` already reports it, so counting its bold runs a second
    time told a reader their struck-through footer was live copy that had merely
    lost its marks. An ``mc:Fallback`` subtree is a duplicate rendering of its
    sibling ``mc:Choice``, so counting it reported one picture's alt text twice."""
    if any(_local(a.tag) in _SKIP_LOCALS for a in _ancestry(el, pmap)):
        return False
    if id(el) in chrome:
        return False
    return not any(id(a) in chrome for a in _ancestry(el, pmap))


def _bullet_paragraphs(root, pmap=None, chrome=()):
    # type: (object) -> dict
    """``{kind: n}`` for paragraphs whose OWN ``a:pPr`` declares a bullet style."""
    if pmap is None:
        pmap = _parent_map(root)
    found = {}
    for p in root.iter():
        if _local(p.tag) != "p" or not _in_scope(p, pmap, chrome):
            continue
        for pr in p:
            if _local(pr.tag) != "pPr":
                continue
            for decl in pr:
                kind = _BULLET_LOCALS.get(_local(decl.tag))
                if kind:
                    found[kind] = found.get(kind, 0) + 1
    return found


def pptx_policy_drops(parts):
    # type: (dict) -> list
    """Every deliberate flattening or drop the deck lane performs, as named
    warnings with the COUNTS behind them.

    None of these degrades ``status``: a policy drop is not a defect. What it must
    never be is silent, because a reader cannot go and look for something they were
    never told about."""
    found = []
    where = _positions(parts)
    pages = _text_parts(parts)

    # ---------------------------------------------------------------- chrome
    roles = {"ftr": 0, "dt": 0, "sldNum": 0}
    chrome_shapes = 0
    chars = 0
    authored = []
    for name, root in pages:
        pmap = _parent_map(root)
        # Counted per SHAPE, from the same set that decides the exclusion, rather
        # than per `p:ph` element. The shape is what is really dropped: reading the
        # declarations instead double-counted a shape carrying two of them (which
        # is malformed, but a warning that miscounts malformed input is a warning
        # that lies) and let the role tallies stop summing to the total, so the one
        # sentence they build would have said two different things.
        excluded = _chrome_shapes(root, pmap)
        for sp in root.iter():
            if id(sp) not in excluded:
                continue
            role = ""
            for ph in sp.iter():
                if _local(ph.tag) != "ph":
                    continue
                if not any(_local(a.tag) == "nvPr" for a in _ancestry(ph, pmap)):
                    continue
                if _attr(ph, "type") in _CHROME_ROLES:
                    role = _attr(ph, "type")
                    break
            if not role:
                continue
            roles[role] += 1
            chrome_shapes += 1
            text = _walk_text_of_shape(sp)
            chars += len(text)
            if role == "ftr" and text:
                authored.append('%s "%s"' % (where.get(name, name), text))
    if chrome_shapes:
        detail = ("%d page-furniture placeholder(s) excluded — %d footer(s), "
                  "%d date(s), %d slide number(s), %d char(s) in all. A banner "
                  "repeated on every slide is furniture rather than body text, so "
                  "BOTH halves of the token gate exclude it and recall is a vacuous "
                  "1.0 over the drop: this count is the only record there is"
                  % (chrome_shapes, roles["ftr"], roles["dt"], roles["sldNum"],
                     chars))
        if authored:
            # Said only when it is true. A date and a slide number are values
            # PowerPoint regenerates, and losing those loses nothing; a footer is
            # something a person typed, so it is quoted.
            detail += (". The footer text was AUTHORED and is quoted here: %s"
                       % _some(authored))
        found.append({"code": "dropped_slide_chrome", "detail": detail,
                      "shapes": chrome_shapes, "chars": chars,
                      "footers": roles["ftr"], "dates": roles["dt"],
                      "slide_numbers": roles["sldNum"],
                      "first": authored[0] if authored else ""})

    # EMPHASIS is no longer dropped, so there is no longer a receipt for it.
    # `dropped_shape_emphasis` counted the bold, italic and strikethrough runs
    # `pptx_markdown` did not emit; it emits all three now (P9.8a) and
    # `pptx_source_structure` states real `strong`/`em`/`strike` counts, so the loss
    # this code reported has ENDED. A drop code that keeps firing over a faithful
    # conversion is worse than no code: it teaches a reader to discount the
    # vocabulary, which is the one thing making the honest codes worth reading.

    # ------------------------------------------------------------ hyperlinks
    links = []
    for name, root in pages:
        targets = _rel_targets(parts.get(_rels_name(name), ""))
        pmap = _parent_map(root)
        chrome = _chrome_shapes(root, pmap)
        for el in root.iter():
            if _local(el.tag) != "hlinkClick" or not _in_scope(el, pmap, chrome):
                continue
            url = targets.get(_rel_id(el), "") or _attr(el, "action")
            if not url:
                continue
            # NARROWED, not retired. An EXTERNAL url is now emitted as
            # `[text](url)`, so it is not lost and reporting it would be a receipt
            # for nothing. A slide-to-slide jump still is lost: it has no address a
            # reader outside the deck could follow, `[text]()` is a dead link in the
            # stored bytes, and a URL is on NEITHER side of the token gate — markup,
            # never slide text — so this is the only place the loss can be seen.
            if url.startswith(_EXTERNAL_URL):
                continue
            label = ""
            for anc in _ancestry(el, pmap):
                if _local(anc.tag) in ("r", "p"):
                    label = _walk_text_of_shape(anc)
                    if label:
                        break
            links.append('%s "%s" -> %s' % (where.get(name, name), label, url))
    if links:
        found.append({
            "code": "dropped_shape_links",
            "detail": "%d internal jump(s) lose their target (%s): the display "
                      "text is kept and the destination is not, because a "
                      "slide-to-slide jump has no address a reader outside the deck "
                      "could follow. An EXTERNAL url is emitted as a real link and "
                      "is not counted here. A destination is on NEITHER side of the "
                      "token gate — it is markup, never slide text — so nothing "
                      "else in the report can see this, which is why it is quoted "
                      "here rather than merely counted"
                      % (len(links), _some(links)),
            "links": len(links), "first": links[0]})

    # -------------------------------------------------------------- alt text
    alts = []
    alt_chars = 0
    for name, root in pages:
        pmap = _parent_map(root)
        chrome = _chrome_shapes(root, pmap)
        for el in root.iter():
            if _local(el.tag) != "cNvPr" or not _in_scope(el, pmap, chrome):
                continue
            text = (_attr(el, "descr") or "").strip()
            if not text:
                continue
            alt_chars += len(text)
            alts.append('%s "%s"' % (where.get(name, name), text))
    if alts:
        found.append({
            "code": "dropped_shape_alt_text",
            "detail": "%d shape(s) carry alternative text that is not emitted "
                      "(%d char(s); %s): a picture's description is prose the deck "
                      "holds and the markdown does not. Neither half of the token "
                      "gate reads it, so recall is 1.0 over the whole of it and "
                      "this count is the only record"
                      % (len(alts), alt_chars, _some(alts)),
            "shapes": len(alts), "chars": alt_chars, "first": alts[0]})

    # -------------------------------------------------------- the bullet cascade
    cascade = {}
    for _name, root in pages:
        pmap = _parent_map(root)
        for kind, n in _bullet_paragraphs(
                root, pmap, _chrome_shapes(root, pmap)).items():
            cascade[kind] = cascade.get(kind, 0) + n
    # The ordinal is emitted now, so it is not a loss and must not be counted as
    # one. A drop code that keeps reporting a loss nobody suffered teaches a reader
    # to discount the whole vocabulary, which is what makes the honest codes worth
    # reading. The DECLARATION is still read — by the fact vector, where it decides
    # whether a paragraph is an ordered item.
    cascade.pop("auto_numbered", None)
    total = sum(cascade.values())
    if total:
        found.append({
            "code": "flattened_bullet_formatting",
            "detail": "%d paragraph(s) publish as a plain `-` bullet whatever the "
                      "deck draws: %d custom bullet character(s) are dropped, and "
                      "%d paragraph(s) the deck shows with NO bullet at all gain "
                      "one. Markdown has exactly one bullet glyph and no way to "
                      "write an item without a marker, so neither is a converter "
                      "choice — every character is kept and the rendered marker is "
                      "not, and no fact in the closed list can express either. "
                      "AUTO-NUMBERED steps are no longer counted here: the ordinal "
                      "is emitted (P9.8b) and graded as `ordered_items` and "
                      "`ordered_numbers`, which is the stronger statement"
                      % (total, cascade.get("custom_char", 0),
                         cascade.get("suppressed", 0)),
            "paragraphs": total,
            "custom_char": cascade.get("custom_char", 0),
            "suppressed": cascade.get("suppressed", 0)})

    # -------------------------------------------------- outline levels markdown
    # -------------------------------------------------- could not hold
    flattened = pptx_source_structure(parts).get("_flattened_levels", 0)
    if flattened:
        found.append({
            "code": "flattened_list_levels",
            "detail": "%d bullet(s) render one level in from where the deck puts "
                      "them: CommonMark nests a child item only under a parent that "
                      "EXISTS, so an outline level with nothing above it at the "
                      "level between cannot be written at all. The text of every "
                      "bullet is kept and its declared depth is not, and the reader "
                      "sees a shallower outline than the author drew. Nothing else "
                      "can see this — indentation is not a token, so recall is 1.0 "
                      "over it, and the structure gate grades the depth a RENDERER "
                      "really shows, which is the only depth markdown has"
                      % flattened,
            "count": flattened})

    # ---------------------------------------------------------- hidden slides
    hidden = []       # positions the deck really states
    unlisted = []     # hidden AND never listed: it has no position to state
    for pos, num, name in _slide_sequence(parts):
        root = _root(parts.get(name, ""))
        if root is None:
            continue
        if _attr(root, "show").lower() not in ("0", "false"):
            continue
        # A part number is NOT a position, and publishing one as though it were sent
        # a reader to a slide the deck fully shows: on parts 1..3 with
        # `sldIdLst = [3, 1]` and part 2 hidden-and-unlisted, "position 2" named
        # `## Slide 2 — Alpha`, which is part 1.
        if pos is None:
            unlisted.append("slide%d" % num)
        else:
            hidden.append(pos)
    if hidden or unlisted:
        # The SAME disclosure a hidden worksheet gets, so it is deliberately the
        # same code rather than a second one: content the document does not SHOW,
        # published as a peer of the content it does.
        named = []
        if hidden:
            named.append("position%s %s" % ("s" if len(hidden) > 1 else "",
                                            ", ".join(str(p) for p in hidden)))
        if unlisted:
            named.append("%s, which the deck never lists at all and so has no "
                         "position to give" % ", ".join(unlisted))
        found.append({
            "code": "hidden_content_published",
            "detail": "%d slide(s) the deck does not show (%s) are converted and "
                      "published as ordinary content: the text is kept in full, and "
                      "the fact that the deck skips them is not — a backup slide "
                      "reads as a peer of the ones presented. They are NAMED so a "
                      "reader can find them in the markdown"
                      % (len(hidden) + len(unlisted), "; ".join(named)),
            "slides": len(hidden) + len(unlisted),
            "slide_positions": list(hidden)})
    return found


def _walk_text_of_shape(el):
    # type: (object) -> str
    """Every character under one element, chrome policy NOT applied.

    ``_walk_text`` exists to answer "what is in the denominator", so it excludes the
    furniture. These warnings exist to say what the furniture WAS, which is the one
    question that reader cannot answer."""
    out = []
    for node in el.iter():
        loc = _local(node.tag)
        if loc in _SKIP_LOCALS:
            continue
        if loc in _BREAKS:
            out.append(" ")
        elif loc in _TEXT_LOCALS and node.text:
            out.append(node.text)
    return _WS.sub(" ", "".join(out)).strip()
