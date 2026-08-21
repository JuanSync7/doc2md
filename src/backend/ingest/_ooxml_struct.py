"""
title: Converter-blind structural ground truth for OOXML
layer: backend
public_api: no
summary: Reads the same structural facts as backend.validate.md_structure, but out of the source XML by flat query rather than by the converter's recursive descent — the second opinion the fidelity gate compares against.
"""
# WHY A SEPARATE FILE
#
# The losslessness gate is trustworthy because two independent implementations
# extract the source's TEXT and must agree exactly. Emphasis, list nesting, code
# fencing and table geometry carry no tokens, so that gate cannot see them; this
# module is the second opinion for those, and it earns its keep the same way — by
# not being the converter.
#
# It lives in its own module so a helper cannot be shared by accident. The only
# things it borrows from _ooxml_md are the LEAF primitives (_local, _attr, _root)
# and the declared content POLICIES (_SKIP_LOCALS and friends) that must apply to
# both sides or the comparison is not apple-to-apple. Every traversal here is its
# own.
#
# The mechanism is deliberately different, not merely a retyped copy: the converter
# dispatches recursively child by child, while this walks a FLAT element.iter()
# stream and answers "where am I?" with an ancestor predicate over a parent map.
# That difference is what catches a converter that forgets to recurse through a
# w:sdt content control — the flat scan still finds the paragraph, the counts
# diverge, and the gate fires.
#
# POLICY MIRRORED FROM THE CONVERTER (deliberate, and the only coupling):
#   * a 1x1 table is Word layout scaffolding, so its content is body, not a table
#   * a paragraph with no text of its own emits no block
#   * consecutive code-styled paragraphs are ONE fenced block
#   * an all-blank table row is dropped, and trailing all-blank columns are trimmed
#   * headers/footers are not in ``parts`` at all, so neither side can see them
import re

from ._ooxml_md import _attr, _local, _root, _SKIP_LOCALS

# Word's canonical heading names. Written out again on purpose — a shared regex
# would mean a bug in it lands identically on both sides and cancels out.
_HEADING = re.compile(r"^\s*heading\s+([1-9])\s*$", re.I)
_CODE_NAMES = frozenset((
    "html preformatted", "source code", "code", "plain text", "macro text",
    "html code", "html typewriter", "verbatim char", "code char", "console",
    "codeblock", "code block", "preformatted text", "hljs",
))
# Text-bearing leaves the converter keeps. w:delText (a tracked deletion) and
# w:instrText (a field instruction) are excluded on BOTH sides — see the
# _ooxml_md module docstring — so they are excluded here too.
_TEXT_LOCAL = "t"


def _norm(name):
    # type: (str) -> str
    return (name or "").strip().lower().replace("-", " ")


def _parents(root):
    # type: (object) -> dict
    """``{id(child): parent}`` for the whole tree.

    ElementTree has no parent pointers, so "is this paragraph inside a table
    cell?" needs one. Building it costs a single pass and is what lets every fact
    below be a flat filter instead of a recursive descent."""
    pmap = {}
    stack = [root]
    while stack:
        el = stack.pop()
        for ch in el:
            pmap[id(ch)] = el
            stack.append(ch)
    return pmap


def _ancestors(el, pmap):
    # type: (object, dict) -> list
    out = []
    cur = pmap.get(id(el))
    while cur is not None:
        out.append(cur)
        cur = pmap.get(id(cur))
    return out


def _skipped(el, pmap):
    # type: (object, dict) -> bool
    """Inside an mc:Fallback / rPh / w:moveFrom subtree the converter never enters."""
    if _local(el.tag) in _SKIP_LOCALS:
        return True
    for anc in _ancestors(el, pmap):
        if _local(anc.tag) in _SKIP_LOCALS:
            return True
    return False


def _rows_of(tbl):
    # type: (object) -> list
    """Direct rows of a table — rows of a NESTED table belong to their own cell."""
    out = []
    stack = [(ch, 0) for ch in reversed(list(tbl))]
    while stack:
        el, depth = stack.pop()
        loc = _local(el.tag)
        if loc == "tbl":
            continue                       # a nested table: not our rows
        if loc == "tr":
            out.append(el)
            continue
        if loc in ("tc", "p", "tblPr", "tblGrid") or loc in _SKIP_LOCALS:
            continue
        for ch in reversed(list(el)):
            stack.append((ch, depth + 1))
    return out


def _int_attr(el, name, default):
    # type: (object, str, int) -> int
    try:
        return int(_attr(el, name))
    except (TypeError, ValueError):
        return default


def _grid_offsets(tr):
    # type: (object) -> tuple
    """``(before, after)`` — the grid columns this row leaves empty at each end.

    ``w:trPr/w:gridBefore`` means the row begins at grid column N rather than 0;
    ``w:gridAfter`` means it stops short of the right edge. A reader that counts
    only the ``w:tc`` elements puts every value one column too far LEFT, and — this
    being the failure this whole module exists to prevent — the ground truth was
    blind in exactly the same place as the converter, so the gate AGREED with the
    bug. A register named ``0x04`` published ``RO`` as its offset with every gate
    green. ``w:trPrChange`` carries the row properties a tracked change replaced and
    is never read."""
    before = after = 0
    for ch in tr:
        if _local(ch.tag) != "trPr":
            continue
        for pr in ch:
            loc = _local(pr.tag)
            if loc == "trPrChange":
                continue
            if loc == "gridBefore":
                before = max(before, _int_attr(pr, "val", 0))
            elif loc == "gridAfter":
                after = max(after, _int_attr(pr, "val", 0))
    return before, after


def _cells_of(tr):
    # type: (object) -> list
    out = []
    stack = [ch for ch in reversed(list(tr))]
    while stack:
        el = stack.pop()
        loc = _local(el.tag)
        if loc == "tc":
            out.append(el)
            continue
        if loc in ("p", "tbl", "trPr") or loc in _SKIP_LOCALS:
            continue
        for ch in reversed(list(el)):
            stack.append(ch)
    return out


def _is_layout_table(tbl):
    # type: (object) -> bool
    """A 1x1 table is a framed section, not data — the converter unwraps it."""
    rows = _rows_of(tbl)
    return len(rows) == 1 and len(_cells_of(rows[0])) == 1


def _own_text(el, skip_boxes=True):
    # type: (object, bool) -> str
    """Text belonging to this element, excluding subtrees the converter siphons.

    ``w:txbxContent`` is excluded because the converter lifts a text box out and
    renders it as its own blocks: an anchor paragraph holding nothing but a text
    box therefore emits no paragraph of its own.

    DOCUMENT ORDER IS THE POINT. An earlier draft walked this with an explicit
    LIFO stack, which returns the runs BACKWARDS — ``"Clock " + "domain A"`` came
    out as ``"domain AClock "``. It stayed invisible for as long as callers only
    asked "is there any text here?" and "how wide is this row?", and surfaced the
    moment cell CONTENT started being compared. Word splits a run at every rsid,
    spell-check and formatting boundary, so a multi-run cell is the common case,
    not an edge one."""
    chunks = []  # type: list

    def walk(node):
        for ch in node:
            loc = _local(ch.tag)
            if loc in _SKIP_LOCALS:
                continue
            if skip_boxes and loc == "txbxContent":
                continue
            if loc == _TEXT_LOCAL:
                if ch.text:
                    chunks.append(ch.text)
                continue
            walk(ch)

    walk(el)
    return "".join(chunks)


def _ppr_props(p):
    # type: (object) -> dict
    """A paragraph's own properties: style, numbering, outline level.

    w:pPrChange holds the properties a tracked change REPLACED; reading through it
    resurrects stale styles, so those subtrees are never entered."""
    props = {"style": "", "num": "", "ilvl": "", "outline": ""}
    ppr = [ch for ch in p if _local(ch.tag) == "pPr"]
    stack = list(ppr)
    while stack:
        el = stack.pop()
        for pr in el:
            loc = _local(pr.tag)
            if loc in ("pPrChange", "rPrChange"):
                continue
            if loc == "pStyle":
                props["style"] = _attr(pr, "val")
            elif loc == "numId":
                props["num"] = _attr(pr, "val")
            elif loc == "ilvl":
                props["ilvl"] = _attr(pr, "val")
            elif loc == "outlineLvl":
                props["outline"] = _attr(pr, "val")
            else:
                stack.append(pr)
    return props


def _style_map(styles_xml):
    # type: (str) -> dict
    """``{"heading": {sid: level}, "code_para": set, "code_char": set,
    "num": {sid: (numId, ilvl)}}``.

    Independently derived: this scans every ``w:style`` reachable from the root by
    ``iter()`` and reads the FIRST name it finds, where the converter iterates
    direct children and lets the last match win. Where those two disagree the
    style file is pathological and the gate should say so.

    ``w:basedOn`` is resolved by REPEATED PROPAGATION — every round hands each
    style whatever opinion its parent already holds, until a round changes
    nothing — rather than by walking each style's chain. Same answer for a
    well-formed styles part, and a ``w:basedOn`` cycle simply stops converging
    instead of needing a visited set to keep it from recursing forever. Doing this
    at all is not optional: a custom ``NimbusH1 basedOn="Heading1"`` is an ``<h1>``
    to LibreOffice, and a ground truth that called it body prose would have agreed
    with a converter that deleted every heading in the document."""
    out = {"heading": {}, "code_para": set(), "code_char": set(), "num": {}}
    root = _root(styles_xml)
    if root is None:
        return out
    raw = {}
    for style in root.iter():
        if _local(style.tag) != "style":
            continue
        sid = _attr(style, "styleId")
        if not sid:
            continue
        rec = {"name": "", "based": "", "outline": None, "num": "", "ilvl": "",
               "type": _attr(style, "type")}
        for ch in style.iter():
            loc = _local(ch.tag)
            if loc == "name" and not rec["name"]:
                rec["name"] = _attr(ch, "val")
            elif loc == "basedOn" and not rec["based"]:
                rec["based"] = _attr(ch, "val")
            elif loc == "outlineLvl" and rec["outline"] is None:
                rec["outline"] = _int_attr(ch, "val", None)
            elif loc == "numId" and not rec["num"]:
                rec["num"] = _attr(ch, "val")
            elif loc == "ilvl" and not rec["ilvl"]:
                rec["ilvl"] = _attr(ch, "val")
        raw[sid] = rec

    heading, code, numbered = {}, set(), {}
    for sid, rec in raw.items():
        m = _HEADING.match(rec["name"] or "")
        if m:
            heading[sid] = int(m.group(1))
        elif _norm(rec["name"]) == "title":
            heading[sid] = 1
        elif rec["outline"] is not None and 0 <= rec["outline"] <= 8:
            heading[sid] = rec["outline"] + 1
        if _norm(sid) in _CODE_NAMES or _norm(rec["name"]) in _CODE_NAMES:
            code.add(sid)
        if rec["num"]:
            numbered[sid] = (rec["num"], rec["ilvl"] or "0")
    for _round in range(len(raw)):
        moved = False
        for sid, rec in raw.items():
            parent = rec["based"]
            if not parent or parent not in raw:
                continue
            if sid not in heading and parent in heading:
                heading[sid] = heading[parent]
                moved = True
            if sid not in code and parent in code:
                code.add(sid)
                moved = True
            if sid not in numbered and parent in numbered:
                numbered[sid] = numbered[parent]
                moved = True
        if not moved:
            break

    out["heading"] = heading
    out["num"] = numbered
    for sid in code:
        # Filed under the DERIVED style's own w:type: a character style based on a
        # paragraph one is still an inline span, never a fenced block.
        key = "code_char" if raw[sid]["type"] == "character" else "code_para"
        out[key].add(sid)
    return out


def _num_formats(numbering_xml):
    # type: (str) -> dict
    """``{(numId, ilvl): (numFmt, start)}``, resolved through the abstractNum
    indirection and then through this instance's own ``w:lvlOverride``.

    ``start`` is the number the level's first item shows. Without it a list
    declared to begin at 5 reads as beginning at 1 on this side too, and the gate
    happily certifies a renumbered procedure — the count of items is the same
    either way."""
    root = _root(numbering_xml)
    fmts = {}
    if root is None:
        return fmts
    abstract, links, override = {}, {}, {}
    for el in root.iter():
        loc = _local(el.tag)
        if loc == "abstractNum":
            aid = _attr(el, "abstractNumId")
            for lvl in el.iter():
                if _local(lvl.tag) != "lvl":
                    continue
                ilvl = _attr(lvl, "ilvl")
                fmt, start = "", 1
                for ch in lvl.iter():
                    sub = _local(ch.tag)
                    if sub == "numFmt" and not fmt:
                        fmt = _attr(ch, "val")
                    elif sub == "start":
                        start = _int_attr(ch, "val", 1)
                if fmt:
                    abstract[(aid, ilvl)] = (fmt, start)
        elif loc == "num":
            nid = _attr(el, "numId")
            for ch in el:
                child = _local(ch.tag)
                if child == "abstractNumId" and nid not in links:
                    links[nid] = _attr(ch, "val")
                elif child == "lvlOverride":
                    fmt, start = "", None
                    for sub in ch.iter():
                        name = _local(sub.tag)
                        if name == "startOverride":
                            start = _int_attr(sub, "val", start)
                        elif name == "numFmt" and not fmt:
                            fmt = _attr(sub, "val")
                        elif name == "start" and start is None:
                            start = _int_attr(sub, "val", None)
                    override[(nid, _attr(ch, "ilvl"))] = (fmt, start)
    for nid, aid in links.items():
        for (a, ilvl), pair in abstract.items():
            if a == aid:
                fmts[(nid, ilvl)] = pair
    for key in override:
        fmt, start = override[key]
        base = fmts.get(key, ("bullet", 1))
        fmts[key] = (fmt or base[0], base[1] if start is None else start)
    return fmts


def _step(counters, num_id, depth, start):
    # type: (dict, str, int, int) -> int
    """The number this item shows, and the reset that makes the next one right.

    Each numbering INSTANCE owns a stack of running values, one per level. A level
    advances by one from its ``w:start``; the levels below it are dropped, so the
    next time one of them appears it begins at its own start again — which is why
    the first sub-step of step 2 reads 2.1 and not 2.4. ``w:lvlRestart`` is not
    read; the default is what is modelled, on both sides, and a document that
    overrides it is the one this can misnumber."""
    run = counters.setdefault(num_id, {})
    for level in [k for k in run if k > depth]:
        del run[level]
    run[depth] = run.get(depth, start - 1) + 1
    return run[depth]


def _external(rels_xml):
    # type: (str) -> dict
    root = _root(rels_xml)
    out = {}
    if root is None:
        return out
    for rel in root.iter():
        if _local(rel.tag) != "Relationship":
            continue
        if rel.attrib.get("TargetMode", "") == "External":
            rid, target = rel.attrib.get("Id", ""), rel.attrib.get("Target", "")
            if rid and target:
                out[rid] = target
    return out


_OFF = ("0", "false", "off")
_BOLD = ("b", "bCs")
_ITALIC = ("i", "iCs")
_STRIKE = ("strike", "dstrike")


def _run_marks(run, code_chars):
    # type: (object, object) -> tuple
    """The formatting a run carries, from its own w:rPr. Mirrors the converter's
    contract (direct formatting only, toggles tri-state, w:rPrChange ignored) but
    reads it with its own loop."""
    got = set()
    for pr in run:
        if _local(pr.tag) != "rPr":
            continue
        for prop in pr:
            loc = _local(prop.tag)
            if loc == "rPrChange":
                continue
            on = _attr(prop, "val") not in _OFF
            if loc in _BOLD and on:
                got.add("strong")
            elif loc in _ITALIC and on:
                got.add("em")
            elif loc in _STRIKE and on:
                got.add("strike")
            elif loc == "rStyle" and code_chars and _attr(prop, "val") in code_chars:
                got.add("code")
    return tuple(sorted(got))


def _count_marked_spans(container, code_chars, out, pmap):
    # type: (object, object, dict, dict) -> None
    """Count formatted SPANS, coalescing adjacent runs that carry the same marks.

    Spans, not runs: Word splits one word across several w:r at every property
    boundary, so counting runs would report three bold spans where a reader sees
    one — and the converter, which coalesces, would be graded as wrong for being
    right."""
    groups = []  # type: list  # [(marks, joined_text)], consecutive equal marks fused
    for el in container.iter():
        if _local(el.tag) != "r" or _skipped(el, pmap):
            continue
        text = _own_text(el)
        if not text:
            continue        # a run with no text emits no segment on the other side
        marks = _run_marks(el, code_chars)
        if groups and groups[-1][0] == marks:
            groups[-1] = (marks, groups[-1][1] + text)
        else:
            groups.append((marks, text))
    # A WHITESPACE-ONLY run is a break, not a skip. Skipping it silently fused
    # `**Never** **reboot**` into one span here while the converter emitted two,
    # and the gate then failed a perfectly correct document. The converter's rule
    # is: group consecutive segments by marks, emit markers only when the group
    # has non-whitespace text. Both halves have to be mirrored, not just the first.
    for marks, text in groups:
        if not text.strip():
            continue
        for mark in marks:
            key = "code_spans" if mark == "code" else mark
            out[key] = out.get(key, 0) + 1


# Cell and item CONTENT, reduced to the token notion the recall gate already uses
# (`[a-z0-9]+` after lowercasing). Reducing to tokens is what makes the two sides
# comparable at all: one has escapes, emphasis markers and pipes, the other has raw
# text, and neither difference is content.
_WORDS = re.compile(r"[a-z0-9]+")


def _words(text):
    # type: (str) -> tuple
    return tuple(_WORDS.findall((text or "").lower()))


def _table_shape(tbl, pmap):
    # type: (object, dict) -> dict
    """Rendered geometry of one table, under the converter's declared policies:
    all-blank rows dropped, trailing all-blank columns trimmed, gridSpan widening
    the grid without adding content."""
    grid = []
    fill = {}  # type: dict  # grid column -> the value an open vertical merge repeats
    for tr in _rows_of(tbl):
        before, after = _grid_offsets(tr)
        row, col = [""] * before, before
        for tc in _cells_of(tr):
            span, vmerge = 1, None
            for el in tc.iter():
                loc = _local(el.tag)
                if loc == "gridSpan":
                    try:
                        span = max(1, int(_attr(el, "val")))
                    except ValueError:
                        span = 1
                elif loc == "vMerge":
                    vmerge = _attr(el, "val") or "continue"
            text = _own_text(tc, skip_boxes=True).strip()
            # Mirror the converter's declared flattening: GFM has no rowspan, so a
            # vertical merge REPEATS its value down the continuation rows. Without
            # the same fill here the two sides would disagree on every merged
            # table for a reason that is policy, not defect.
            if vmerge is not None and vmerge != "restart":
                if not text:
                    text = fill.get(col, "")
            elif vmerge is not None:
                fill[col] = text
            else:
                fill.pop(col, None)
            row.append(text)
            row.extend([""] * (span - 1))
            col += span
        row.extend([""] * after)
        grid.append(row)
    # The DECLARED grid width, taken over every row before blank rows are dropped
    # — exactly the floor the converter passes to _gfm_table. Trimming trailing
    # empty columns here would disagree with a table whose last column is real but
    # unfilled, and fail a document the converter handled correctly.
    width = max([len(row) for row in grid] or [1])
    grid = [row for row in grid if any(c for c in row)]
    if not grid:
        return {}
    # `cells` is what makes a TRANSPOSITION detectable. Rows and columns alone
    # cannot see it: swap two values between rows and the geometry is identical,
    # the token multiset is identical, and an escalation table then pages the
    # wrong rota with every gate green.
    cells = [tuple(_words(c) for c in (list(row) + [""] * width)[:width])
             for row in grid]
    return {"rows": len(grid), "cols": width, "has_header": True, "cells": cells}


def _section_facts(xml, out):
    # type: (str, dict) -> None
    """A trailing ``## Footnotes`` / ``## Endnotes`` / ``## Comments`` section:
    one level-2 heading plus one top-level bullet per note that carries text."""
    root = _root(xml)
    if root is None:
        return
    items = []
    for note in root:
        text = _own_text(note).strip()
        if text:
            items.append(text)
    if not items:
        return
    out["headings"][2] = out["headings"].get(2, 0) + 1
    out["list_items"][0] = out["list_items"].get(0, 0) + len(items)
    out["bullet_items"] += len(items)
    out["list_item_words"].extend(_words(t) for t in items)


_DIAGRAM = re.compile(r"^word/diagrams/data\d+\.xml$")
_CHART = re.compile(r"^word/charts/chart(?:Ex)?\d+\.xml$")


# ------------------------------------------------------- measured policy drops
#
# end-goal.md §1: "the drop is always deliberate and visible, never an accident".
# A drop nobody counted is indistinguishable from a bug, and `dropped_headers_footers`
# spent months documented-but-unemitted precisely because nothing measured it. These
# functions measure, from the source, what the converter is known to flatten or
# discard — so every one of them can be NAMED with a count.

_SPAN_LOCALS = ("gridSpan", "vMerge", "hMerge", "rowSpan")
_BODY_PART = re.compile(
    r"^(word/document\.xml|ppt/slides/slide\d+\.xml|xl/worksheets/[^/]+\.xml)$")
_MERGE_CELL = re.compile(r"^xl/worksheets/[^/]+\.xml$")


def merged_cell_spans(parts):
    # type: (dict) -> dict
    """``{"horizontal": n, "vertical": n}`` merged cells across every body part.

    GFM has no colspan or rowspan, so every one of these is flattened. Counting
    them from the SOURCE means the number is right whether or not the converter
    for that format even looks at the attribute — pptx and xlsx currently do not."""
    out = {"horizontal": 0, "vertical": 0}
    for name in sorted(parts):
        if not _BODY_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        for el in root.iter():
            loc = _local(el.tag)
            if loc == "gridSpan":
                try:
                    out["horizontal"] += max(0, int(_attr(el, "val")) - 1)
                except ValueError:
                    pass
            elif loc == "hMerge" and _attr(el, "val") not in _OFF:
                out["horizontal"] += 1
            elif loc == "vMerge" and _attr(el, "val") != "restart":
                out["vertical"] += 1
            elif loc == "rowSpan":
                try:
                    out["vertical"] += max(0, int(_attr(el, "val")) - 1)
                except ValueError:
                    pass
        if _MERGE_CELL.match(name):
            for el in root.iter():
                if _local(el.tag) == "mergeCell":
                    out["horizontal"] += 1
    return out


# The two list formats markdown can spell. Everything else Word offers —
# upperLetter, lowerRoman, decimalZero, ordinalText, none — has no marker in
# CommonMark, whose ordered markers are decimal digits and nothing else.
_MD_NUM_FORMATS = ("bullet", "decimal")


def _numbered_paragraphs(parts):
    # type: (dict) -> tuple
    """``(root, [(numFmt, w:p)])`` for every numbered body paragraph that carries
    text, with the numbering resolved through the paragraph STYLE where the
    paragraph itself declares none.

    The parsed root comes back with the paragraphs because element identity is only
    meaningful within ONE parse: a caller that re-parsed the same XML to ask a
    second question would hold elements that compare equal to nothing here."""
    root = _root(parts.get("word/document.xml", ""))
    if root is None:
        return None, []
    styles = _style_map(parts.get("word/styles.xml", ""))
    numbering = _num_formats(parts.get("word/numbering.xml", ""))
    pmap = _parents(root)
    found = []
    for p in root.iter():
        if _local(p.tag) != "p" or _skipped(p, pmap):
            continue
        if not _own_text(p).strip():
            continue
        props = _ppr_props(p)
        num, ilvl = props["num"], props["ilvl"]
        if not num:
            inherited = styles["num"].get(props["style"])
            if inherited:
                num, ilvl = inherited[0], (ilvl or inherited[1])
        if not num or num == "0":
            continue
        found.append((numbering.get((num, ilvl or "0"), ("bullet", 1))[0], p))
    return root, found


def decimalised_numbering(parts):
    # type: (dict) -> dict
    """``{numFmt: n}`` for numbered paragraphs whose format markdown cannot write.

    CommonMark has exactly one ordered marker, the decimal digit, so a list Word
    labels ``A.`` or ``iii.`` can only be emitted as ``1.``, ``2.``, ``3.``. The
    POSITION is kept — "see step B" still points at the second item — and the label
    is not, so this is a real loss and it is counted rather than shrugged at."""
    out = {}
    for fmt, _p in _numbered_paragraphs(parts)[1]:
        if fmt and fmt not in _MD_NUM_FORMATS:
            out[fmt] = out.get(fmt, 0) + 1
    return out


def anchored_text_boxes(parts):
    # type: (dict) -> int
    """How many text boxes are anchored inside a NUMBERED paragraph.

    A text box is always lifted out of its anchor paragraph and rendered as its own
    blocks — a pipe table cannot live inside a sentence. When the anchor is a step
    of a procedure, that lift takes the callout out of the step it was written in;
    the converter re-indents it back into the step where the content allows, and
    emits it beside the list where it does not. Either way the reader is not looking
    at what Word drew, so the count is published."""
    root, paragraphs = _numbered_paragraphs(parts)
    numbered = set(id(p) for _fmt, p in paragraphs)
    if root is None or not numbered:
        return 0
    pmap = _parents(root)
    total = 0
    for el in root.iter():
        if _local(el.tag) != "txbxContent" or _skipped(el, pmap):
            continue
        for anc in _ancestors(el, pmap):
            if _local(anc.tag) == "p":
                if id(anc) in numbered:
                    total += 1
                break
    return total


_REVISION_LOCALS = {"ins": "insertions", "del": "deletions",
                    "moveFrom": "moves", "moveTo": "moves"}


def tracked_changes(parts):
    # type: (dict) -> dict
    """``{"insertions": n, "deletions": n, "moves": n}`` in the body parts.

    The converter takes the FINAL view: an insertion is live text, a deletion is
    dropped (``w:delText`` is not collected on either side, so recall never
    notices), and ``w:moveFrom`` is skipped as a stale copy. All three are correct
    and all three were silent — a reader had no way to know the document they were
    handed still had revision marks in it."""
    out = {"insertions": 0, "deletions": 0, "moves": 0}
    for name in sorted(parts):
        if not _BODY_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        for el in root.iter():
            key = _REVISION_LOCALS.get(_local(el.tag))
            if key:
                out[key] += 1
    return out


_EMBEDDING = re.compile(r"^(word|ppt|xl)/embeddings/")


def embedded_objects(names):
    # type: (object) -> list
    """Embedded OLE objects, by part name.

    Never read: an embedded spreadsheet inside a Word document is a whole second
    document, and converting it is a different feature. Dropping it is defensible;
    dropping it silently is not — and ``--audit-parts`` cannot see these either,
    because it only inspects members ending in ``.xml``."""
    return sorted(n for n in (names or ()) if _EMBEDDING.match(n))


def docx_source_structure(parts):
    # type: (dict) -> dict
    """The structural facts a faithful conversion of this docx must exhibit.

    Same keys as ``backend.validate.md_structure`` so the two can be compared
    directly. Keys the two sides cannot meaningfully compare (images, which are
    HTML-comment sentinels at gate time and are graded by the ``images{}`` block
    instead) are absent here on purpose."""
    out = {"headings": {}, "list_items": {}, "ordered_items": 0, "bullet_items": 0,
           "strong": 0, "em": 0, "strike": 0, "code_spans": 0, "code_blocks": 0,
           "links": 0, "tables": [], "list_item_words": [], "ordered_numbers": []}
    root = _root(parts.get("word/document.xml", ""))
    if root is None:
        return out
    styles = _style_map(parts.get("word/styles.xml", ""))
    numbering = _num_formats(parts.get("word/numbering.xml", ""))
    external = _external(parts.get("word/_rels/document.xml.rels", ""))
    pmap = _parents(root)

    # Tables first: a data table's cells are cell content, so its paragraphs must
    # not also be counted as body headings or list items.
    in_data_table = set()
    for tbl in root.iter():
        if _local(tbl.tag) != "tbl" or _skipped(tbl, pmap):
            continue
        if _is_layout_table(tbl):
            continue
        shape = _table_shape(tbl, pmap)
        if shape:
            out["tables"].append(shape)
        for el in tbl.iter():
            in_data_table.add(id(el))
    # A nested table inside a data cell was flattened into that cell, so it is not
    # a table of its own — the loop above already skipped its paragraphs.
    out["tables"] = [t for t in out["tables"] if t]

    code_run = False
    counters = {}  # type: dict  # numId -> {level: running number}
    for p in root.iter():
        if _local(p.tag) != "p" or _skipped(p, pmap):
            continue
        if id(p) in in_data_table:
            continue
        if not _own_text(p).strip():
            code_run = False
            continue
        props = _ppr_props(p)
        if not props["num"]:
            # Numbering the paragraph inherits from its STYLE. Both sides read only
            # the paragraph's own w:pPr until now, which made a "List Number"
            # procedure disappear symmetrically: no markers in the markdown, no list
            # items in the ground truth, and a gate that agreed the two matched.
            inherited = styles["num"].get(props["style"])
            if inherited:
                props["num"] = inherited[0]
                if not props["ilvl"]:
                    props["ilvl"] = inherited[1]
        if props["style"] in styles["code_para"]:
            if not code_run:
                out["code_blocks"] += 1       # a run of code paragraphs is ONE fence
                code_run = True
            continue
        code_run = False
        level = styles["heading"].get(props["style"])
        if level is None and props["outline"]:
            try:
                level = int(props["outline"]) + 1
            except ValueError:
                level = None
        if level:
            out["headings"][min(level, 6)] = out["headings"].get(min(level, 6), 0) + 1
        elif props["num"] and props["num"] != "0":
            try:
                depth = int(props["ilvl"] or "0")
            except ValueError:
                depth = 0
            out["list_items"][depth] = out["list_items"].get(depth, 0) + 1
            # Ordered item CONTENT: a procedure whose steps were swapped has the
            # same depth histogram and the same token multiset as the real one.
            out["list_item_words"].append(_words(_own_text(p)))
            fmt, start = numbering.get((props["num"], props["ilvl"] or "0"),
                                       ("bullet", 1))
            if fmt == "bullet":
                out["bullet_items"] += 1
            else:
                out["ordered_items"] += 1
                # The NUMBER the reader sees, which is what a split or a restart
                # moves and what nothing else here can see: a list broken in two by
                # a screenshot has the same item count, the same depths and the same
                # tokens as the whole one, and renders 1,2,1,2 instead of 1,2,3,4.
                out["ordered_numbers"].append(
                    _step(counters, props["num"], depth, start))

    _count_marked_spans(root, styles["code_char"], out, pmap)

    for el in root.iter():
        if _local(el.tag) != "hyperlink" or _skipped(el, pmap):
            continue
        url = external.get(_attr(el, "id"), "")
        if url.startswith(("http://", "https://")) and _own_text(el).strip():
            out["links"] += 1

    for part, _title in (("word/footnotes.xml", "Footnotes"),
                         ("word/endnotes.xml", "Endnotes"),
                         ("word/comments.xml", "Comments")):
        _section_facts(parts.get(part, ""), out)
    for name in sorted(parts):
        if _DIAGRAM.match(name) or _CHART.match(name):
            out["headings"][2] = out["headings"].get(2, 0) + 1
    return out


def policy_drops(parts, member_names=()):
    # type: (dict, object) -> list
    """Every deliberate flattening or drop this lane performs, as named warnings.

    The shape mirrors ``furniture_drops``: a ``code``, a human ``detail``, and the
    COUNTS behind it — because "some spans were flattened" is a shrug and "3
    horizontal, 1 vertical" is a fact somebody can act on."""
    found = []
    spans = merged_cell_spans(parts)
    if spans["horizontal"] or spans["vertical"]:
        found.append({
            "code": "flattened_table_spans",
            "detail": "GFM has no colspan/rowspan: %d horizontal and %d vertical "
                      "merge(s) were flattened into plain cells (a horizontal span "
                      "keeps its text in the left-most column; a vertical span is "
                      "repeated down its rows so each row stays self-contained)"
                      % (spans["horizontal"], spans["vertical"]),
            "horizontal": spans["horizontal"],
            "vertical": spans["vertical"]})
    revs = tracked_changes(parts)
    if any(revs.values()):
        found.append({
            "code": "tracked_changes_resolved",
            "detail": "the source still carries revision marks; the FINAL view was "
                      "taken: %d insertion(s) accepted as live text, %d deletion(s) "
                      "dropped, %d move mark(s) resolved to their destination"
                      % (revs["insertions"], revs["deletions"], revs["moves"]),
            "insertions": revs["insertions"],
            "deletions": revs["deletions"],
            "moves": revs["moves"]})
    formats = decimalised_numbering(parts)
    if formats:
        found.append({
            "code": "decimalised_list_numbering",
            "detail": "CommonMark has only the decimal ordered marker: %s "
                      "renumbered as 1., 2., 3. — the position of every item is "
                      "kept, its label is not"
                      % ", ".join("%d item(s) formatted %s" % (n, f)
                                  for f, n in sorted(formats.items())),
            "count": sum(formats.values()),
            "formats": sorted(formats)})
    boxes = anchored_text_boxes(parts)
    if boxes:
        found.append({
            "code": "lifted_text_boxes",
            "detail": "%d text box(es) anchored inside a numbered step were lifted "
                      "out of the anchor paragraph and rendered as their own "
                      "block(s); a callout drawn beside a step is not a block "
                      "markdown has" % boxes,
            "count": boxes})
    objects = embedded_objects(member_names)
    if objects:
        found.append({
            "code": "dropped_embedded_objects",
            "detail": "%d embedded object(s) not converted (%s): an embedded "
                      "document is a document, and this lane converts one file at "
                      "a time" % (len(objects), ", ".join(objects[:4])),
            "parts": len(objects)})
    return found
