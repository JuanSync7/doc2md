"""
title: Document heading outline (private)
layer: backend
public_api: no
summary: Deterministic faithful heading tree of a document, with per-node token counts and image placement.
"""
# 3.6-compatible, stdlib only. NO LLM.
#
# The OUTLINE is not the CHUNKER. `chunk_sections` produces size-bounded RAG chunks;
# `document_outline` produces the document's faithful heading tree — EVERY heading,
# nested by level — so a consumer never has to re-parse the markdown to learn the
# hierarchy. Both share the heading helpers (is_heading/gfm_anchor) so they agree on
# what a heading is and what it is addressable by. See docs/design/output-contract.md
# (structure.json).
#
# Per node we record two token counts, computed directly from a cumulative-size array
# so they partition cleanly (a parent's subtree == its self + its children's subtrees):
#   self_tokens    : the body directly under a heading, BEFORE its first subheading
#   subtree_tokens : the whole section including all descendants
# These make "which heading blows the embedding budget?" a lookup, not a re-tokenize.
#
# Identity is CONTENT-derived, not positional: `section_id` hashes the anchor and
# `fingerprint` hashes the node's own body, so inserting a heading renumbers `id` and
# moves nothing else. Tables and images are addressable nodes for the same reason —
# a count says a thing exists, an id lets you cite it.
import hashlib
import os
import re
from collections import Counter

from ._chunk import (is_heading, is_toc_line, content_start, gfm_anchor,
                     fenced_lines, _fingerprint, _is_table_row, _is_separator_row,
                     _TOC_HEADER, _WS)

__all__ = ["document_outline", "outline_coverage"]

_IMG = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)')   # markdown image: ![alt](ref ...
# A markdown LINK: [text](url ...) that is NOT an image (no leading !) and whose
# opening bracket is not escaped literal text (the converter escapes source "[" as
# "\["; silicon docs are full of literal brackets that must not read as links).
_LINK = re.compile(r'(?<![!\\])\[([^\]]*)\]\(([^)\s]+)')
# A markdown LIST item: ``- x`` / ``* x`` / ``1. x`` / ``2) x``. It is NOT a heading,
# even though is_heading's numbered/all-caps heuristics (built for un-marked-up native
# text) match many list lines. Crucially the ``\d+[.)]\s`` form matches a FLAT numbered
# item (``1. Foo``) but NOT a hierarchical section number (``1.2.3 Foo`` — no space after
# the first dot), so genuine multi-level section headings survive the filter.
_LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+\S')


def _title_at(lines, i):
    # type: (list, int) -> str
    s = lines[i].strip().lstrip("#").strip()
    return (s[:120] or "section")


def _split_row(row):
    # type: (str) -> list
    """Cells of a GFM table row, dropping the leading/trailing pipe.

    Same rule as ``validate.md_structure`` uses on the same bytes (an escaped ``\\|``
    is a literal pipe, not a cell boundary) so ``cols`` here and ``cols`` in the
    fidelity gate are the same number."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def _section_id(anchor):
    # type: (str) -> str
    """A section's content-derived identity: ``sha1`` of its anchor.

    The anchor is derived from the heading TEXT (plus the renderer's disambiguating
    suffix when a title repeats), so this moves when the heading is renamed — which
    is when a section really has become a different section — and stays put when
    anything else in the document changes. It is deliberately the same input
    ``chunk_sections`` hashes for ``Section.section_id``, so the outline and any
    future chunk store address the same section by the same key.
    """
    return hashlib.sha1((anchor or "").encode("utf-8")).hexdigest()[:16]


def _table_id(anchor, header_line, ordinal):
    # type: (str, str, int) -> str
    """A stable, CONTENT-derived id for one table.

    Derived from three things and deliberately not from the line number: the
    containing section's anchor (itself derived from the heading text), the
    normalised header row, and the table's ordinal within that section. So

      * inserting a paragraph — or a whole earlier section — above the table does
        not move its id, which is the entire point of not using ``line``;
      * two tables sharing a header row in *different* sections stay distinct, so
        the id is unique document-wide rather than only within its node;
      * two tables sharing a header row in the *same* section are told apart by the
        ordinal — the one case where nothing about the content distinguishes them.

    A table whose header is edited gets a new id, which is correct: the citation
    "the escalation matrix, columns Role/Contact" no longer describes it.
    """
    cells = [_WS.sub(" ", c).strip().lower() for c in _split_row(header_line)]
    key = "%s\n%s\n%d" % (anchor or "", "|".join(cells), ordinal)
    return "tbl-%s" % hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def _tables_in(lines, a, b, anchor, fenced):
    # type: (list, int, int, str, list) -> list
    """Every markdown table that STARTS within [a, b), as structure.json table nodes.

    The image treatment applied to tabular content (quality-plan C5): a bare count
    told a consumer that an escalation matrix existed but gave it no way to retrieve,
    cite or link the thing. ``rows`` counts the header row plus its data rows — the
    same convention ``validate.md_structure`` reports, so the two agree.

    ``fenced`` is ``sections.fenced_lines(lines)``: ASCII pipe art drawn inside a
    command transcript is code, and publishing it as an addressable table node
    invited a consumer to cite a table the document does not have.
    """
    out = []
    i = a
    while i < b - 1:
        if (not fenced[i] and not fenced[i + 1]
                and _is_table_row(lines[i]) and _is_separator_row(lines[i + 1])):
            cols = len(_split_row(lines[i]))
            j = i + 2
            while j < b and not fenced[j] and _is_table_row(lines[j]):
                # A row that is itself followed by a delimiter row is the HEADER of
                # the next table, not a data row of this one — two tables written
                # back-to-back with no blank line between them are still two tables.
                if (j + 1 < b and not _is_separator_row(lines[j])
                        and _is_separator_row(lines[j + 1])):
                    break
                j += 1
            out.append({"table_id": _table_id(anchor, lines[i], len(out)),
                        "line": i, "rows": 1 + (j - (i + 2)), "cols": cols,
                        # True by construction: a GFM pipe table is defined by its
                        # header + delimiter. The key exists so a future HTML-island
                        # table (quality-plan P3.6) can report False without a
                        # schema change.
                        "has_header": True})
            i = j
        else:
            i += 1
    return out


def _images_in(lines, a, b):
    # type: (list, int, int) -> list
    """Every markdown image reference in [a, b), as structure.json image nodes.

    caption is left None — it is VLM (LLM) enrichment populated by the captioning
    stage, never by this deterministic layer. image_id is the ref's basename stem so
    it collates with the extracted file under images/ and the markdown reference.
    """
    out = []
    for i in range(a, b):
        for m in _IMG.finditer(lines[i]):
            alt, ref = m.group(1), m.group(2)
            image_id = os.path.splitext(os.path.basename(ref))[0]
            out.append({"image_id": image_id, "ref": ref, "line": i,
                        "alt": alt, "caption": None})
    return out


def _links_in(lines, a, b, fenced):
    # type: (list, int, int, list) -> list
    """Every markdown hyperlink in [a, b), as structure.json link nodes — the image
    pattern applied to connectivity.

    The converter already renders source hyperlinks as ``[text](url)``, so this is
    pure harvest (no sentinels, no bytes, no gate). ``url`` is kept verbatim;
    resolving it to another document (the doc->doc edge) is the consumer's job —
    only it knows the whole corpus. Image references never appear here (``![``),
    escaped literal brackets (``\\[``) are not links, and link-shaped text inside
    fenced code blocks is code, not connectivity (``fenced`` is tracked from the
    top of the body, so a fence opened before ``a`` still suppresses) — the same
    exclusion ``content.links`` applies, so the two counts agree.

    The fence state used to be recomputed here, and here only; it is now the shared
    ``sections.fenced_lines`` mask, so links, headings and tables cannot disagree
    about which lines of a document are code."""
    out = []
    for i in range(a, b):
        if fenced[i]:
            continue
        for m in _LINK.finditer(lines[i]):
            out.append({"text": m.group(1), "url": m.group(2), "line": i})
    return out


# --- heading-level inference for flat extractors (docs/quality-plan.md P4.1) ---
# ``is_heading`` returns the ATX hash count first, so its numbering branch is dead the
# moment an extractor emits ``##`` — which docling always does. The result is a real
# eval bundle with NINE top-level siblings titled ``1``, ``1.1``, ``1.1.1``,
# ``1.1.1.1``: a document whose hierarchy is written down in its own titles, published
# as a flat list. Where the extractor states no hierarchy, the numbering is the only
# evidence there is, so it is used.
#
# P4.1's comment claimed "a document with real levels can never be touched" and the
# guard did not say that: "all headings at one level" is ALSO what a perfectly
# ordinary docx looks like when its author used one heading style throughout. Such a
# document was reshaped by whatever digits happened to open its titles — five sibling
# rails (``5 V rail``, ``1.8 V rail``, ``3.3 V rail``, ``12 V rail``, ``2.5 V rail``)
# came out as a two-level tree in which the 1.8 V and 3.3 V rails are SUBSECTIONS of
# the 5 V rail, an assertion the source never made, published with both gates green.
#
# So the claim is now the guard: numbering is evidence of hierarchy only when it is
# ITSELF AN OUTLINE — every number deeper than the document's shallowest one extends
# a number the document has already stated. ``1.2`` under ``1`` is an outline; ``1.8``
# under ``5`` is a voltage. Part numbers, version strings and rail names cannot pass
# that test, and a genuinely flat extractor's ``1 / 1.1 / 1.1.1`` passes it trivially.
_SECNO = re.compile(r'^(?:(\d+(?:\.\d+){1,5})\.?(?=[\sA-Za-z]|$)'   # 1.2 / 1.2Scope
                    r'|(\d+)\.?(?=\s|$))')                          # 1 / 1. / "1"
_INFER_MIN_HEADS = 3          # two headings are not a pattern
_INFER_MIN_NUMBERED = 0.6     # the numbering has to be the document's habit
_MAX_LEVEL = 6


def _numbering_of(title):
    # type: (str) -> str
    """The leading section number of ``title`` (``1.2.3 Foo`` -> ``"1.2.3"``), or ``""``."""
    m = _SECNO.match((title or "").strip())
    if not m:
        return ""
    return m.group(1) or m.group(2)


def _numbering_level(title):
    # type: (str) -> int
    """Depth stated by a leading section number (``1.2.3 Foo`` -> 3), else 0."""
    num = _numbering_of(title)
    if not num:
        return 0
    return min(_MAX_LEVEL, 1 + num.count("."))


def _is_nested_outline(numbers):
    # type: (list) -> bool
    """True when ``numbers`` (in document order) read as a real section numbering.

    The test is prefix containment, which is the whole meaning of a nested number:
    ``1.1`` claims to be a subsection OF ``1``, so ``1`` has to be a section this
    document actually stated, and stated EARLIER. Numbers at the document's own
    shallowest depth are the roots and answer to nothing — a document may legitimately
    begin at ``1.1`` (the extractor skipped a TOC, or the source starts mid-chapter).

    Rejects the failure this guard exists for: ``5``, ``1.8``, ``3.3`` are three
    unrelated magnitudes, and ``1.8`` names no subsection of ``5``.
    """
    top = min(len(n.split(".")) for n in numbers)
    stated = set()
    for num in numbers:
        parts = num.split(".")
        if len(parts) > top and ".".join(parts[:-1]) not in stated:
            return False
        stated.add(num)
    return True


def _infer_levels(lines, heads):
    # type: (list, list) -> tuple
    """``(heads, inferred)`` — heading levels taken from the titles' numbering when
    the extractor gave none.

    Fires only when ALL of these hold:

      * every heading in the document carries the SAME level — one level is what an
        extractor with no levels to give emits. Any variation at all and the
        extractor's own levels are trusted verbatim;
      * there are at least three headings, and at least 60% of them open with a
        section number — the numbering must be the document's habit, not a stray
        ``2026 Roadmap``;
      * some number is actually nested (``1.1``). Numbering that is flat all the way
        down describes a flat document, and re-stating it changes nothing;
      * and the numbers form a NESTED OUTLINE (``_is_nested_outline``) — every nested
        number extends one the document already stated. This is the guard that keeps
        an ordinary one-heading-style docx out: digits that are measurements rather
        than section numbers do not nest, so nothing is reshaped.

    A heading with no number takes the shallowest inferred level, so an un-numbered
    ``Appendix`` is never adopted as a child of the section that happens to precede it.
    """
    if len(heads) < _INFER_MIN_HEADS:
        return heads, False
    if len(set(lv for _i, lv in heads)) != 1:
        return heads, False
    titles = [_title_at(lines, i) for i, _lv in heads]
    numbers = [_numbering_of(t) for t in titles]
    inferred = [(i, _numbering_level(t)) for (i, _lv), t in zip(heads, titles)]
    numbered = [lv for _i, lv in inferred if lv]
    if len(numbered) < _INFER_MIN_NUMBERED * len(heads) or max(numbered) < 2:
        return heads, False
    if not _is_nested_outline([n for n in numbers if n]):
        return heads, False
    top = min(numbered)
    return [(i, lv or top) for i, lv in inferred], True


def outline_coverage(text, outline_nodes):
    # type: (str, list) -> dict
    """Measure how completely ``outline_nodes`` cover ``text`` — the guardrail that
    makes outline loss VISIBLE.

    The recall gate proves every source token reached ``document.md``; nothing proved
    those lines then reached ``structure.json``. This closes that hole, deliberately
    measuring from the OUTPUT (the union of every node's ``line_span``) back against
    the body, independent of how the outline was built — so any builder bug that drops
    a region (a TOC misdetection, a span error) shows up here, not in a user report.

    Every NON-BLANK line is classified: ``covered`` (inside some node's span),
    ``toc`` (outside every span but table-of-contents furniture — dot-leader entries,
    bare page numbers, a ``Contents`` header — the one thing the outline skips on
    purpose), or ``uncovered`` (outside every span and NOT TOC-like: real content the
    outline lost). Returns ``{"content_lines", "covered_lines", "toc_lines",
    "uncovered_lines", "first_uncovered"}`` where ``first_uncovered`` holds up to the
    first 5 offending 0-based line numbers for triage.
    """
    lines = text.split("\n")
    n = len(lines)
    covered = [False] * n

    def mark(nodes):
        for nd in nodes:
            a, b = nd["line_span"]
            for i in range(max(0, a), min(n, b)):
                covered[i] = True
            mark(nd["children"])
    mark(outline_nodes or [])

    content = covered_ct = toc = 0
    uncovered = []
    for i in range(n):
        s = lines[i].strip()
        if not s:
            continue
        content += 1
        if covered[i]:
            covered_ct += 1
        elif is_toc_line(s) or _TOC_HEADER.match(s):
            toc += 1
        else:
            uncovered.append(i)
    return {"content_lines": content, "covered_lines": covered_ct,
            "toc_lines": toc, "uncovered_lines": len(uncovered),
            "first_uncovered": uncovered[:5]}


def document_outline(text, token_count=None):
    # type: (str, object) -> dict
    """Build the faithful heading tree of ``text`` with per-node token counts.

    ``token_count`` is an optional ``str -> int`` tokenizer (the same callable
    ``chunk_sections`` accepts). When supplied, counts are REAL TOKENS and
    ``token_model`` is left to the caller to record; when omitted, counts use a
    ~4-chars-per-token estimate so the shape is identical without a tokenizer.

    Returns ``{"total_tokens": int, "has_toc": bool, "levels_inferred": bool,
    "outline": [node, ...]}`` where each node is a JSON-serializable dict: ``id,
    section_id, parent, level, title, anchor, line_span, self_tokens,
    subtree_tokens, fingerprint, tables, images, links, children``. ``has_toc`` records whether a leading table-of-contents block
    was detected and skipped; ``levels_inferred`` records whether the nesting came
    from the titles' section numbering rather than from the extractor (see
    ``_infer_levels``) — a reshaped tree is never a silent one. The caller wraps this
    with doc-level metadata (doc_id, source_format, lane, token_model).
    """
    lines = text.split("\n")
    n = len(lines)
    if token_count is None:
        sizes = [(len(ln) + 3) // 4 for ln in lines]   # ~4 chars/token estimate
    else:
        sizes = [token_count(ln) for ln in lines]
    csum = [0]
    for sz in sizes:
        csum.append(csum[-1] + sz)
    span = lambda a, b: csum[b] - csum[a]  # noqa: E731

    start = content_start(lines)
    # Exclude lines that only LOOK like headings to is_heading's heuristics but are
    # really body markdown: table rows (a single-capital cell like ``| 1 | B |`` trips the
    # all-caps rule), list items (``1. Foo`` numbered items), and anything inside a
    # fenced block — a transcript's ``# reset the board`` is a shell comment, and
    # publishing it as a section put a heading in the tree the document never had,
    # over a span that ran past the closing fence. The converter already marks true
    # headings as ATX ``#``, so this keeps the outline a faithful hierarchy instead
    # of one node per list bullet.
    fenced = fenced_lines(lines)
    heads = [(i, is_heading(lines[i])) for i in range(start, n)
             if not fenced[i] and is_heading(lines[i]) and not is_toc_line(lines[i])
             and not _is_table_row(lines[i]) and not _is_separator_row(lines[i])
             and not _LIST_ITEM.match(lines[i])]
    heads, levels_inferred = _infer_levels(lines, heads)

    counts = Counter()
    seq = [0]                                   # running node-id counter (list = closure-writable)

    def _mk(level, title, anchor, a, self_end, subtree_end):
        seq[0] += 1
        return {
            # POSITIONAL id, kept for compatibility: it is what the published
            # examples and existing consumers of structure.json already index by.
            "id": "sec-%04d" % seq[0],
            # CONTENT-derived identity (quality-plan C2). Inserting a heading above
            # this one renumbers `id` and leaves `section_id` alone, so a permalink
            # or an incremental re-index can be built on it. Document-scoped, like
            # every other id in this file — the corpus key is (doc_id, section_id),
            # and doc_id is at the top of the same artifact.
            "section_id": _section_id(anchor),
            "parent": None,                     # filled by the tree assembly below
            "level": level, "title": title, "anchor": anchor,
            "line_span": [a, subtree_end],
            "self_tokens": span(a, self_end),
            "subtree_tokens": span(a, subtree_end),
            # Of the node's OWN body, not its subtree — so it partitions the document
            # exactly like self_tokens does, and a child's edit does not invalidate
            # every ancestor's card. Markdown is stripped first, so re-extracting the
            # same prose through a different lane yields the same fingerprint.
            "fingerprint": _fingerprint("\n".join(lines[a:self_end])),
            "tables": _tables_in(lines, a, self_end, anchor, fenced),
            "images": _images_in(lines, a, self_end),
            "links": _links_in(lines, a, self_end, fenced),
            "children": [],
        }

    outline = []
    # Preamble: content before the first heading is a top-level LEAF so counts are
    # complete (no tokens fall outside the tree). It is not on the nesting stack, so it
    # never adopts the real headings that follow it.
    first = heads[0][0] if heads else n
    if first > start and "".join(lines[start:first]).strip():
        # It occupies the "preamble" slug so a later ``# Preamble`` heading is
        # disambiguated against it rather than colliding with it.
        counts["preamble"] += 1
        outline.append(_mk(1, "(preamble)", "preamble", start, first, first))

    # Assemble the heading tree: a node attaches under the nearest preceding heading of
    # a strictly-lower level; equal-or-higher levels pop the stack first.
    stack = []      # (level, node)
    for k, (hi, level) in enumerate(heads):
        self_end = heads[k + 1][0] if k + 1 < len(heads) else n
        subtree_end = n
        for j in range(k + 1, len(heads)):
            if heads[j][1] <= level:
                subtree_end = heads[j][0]
                break
        title = _title_at(lines, hi)
        # ONE anchor scheme (quality-plan C3): the fragment a renderer emits for this
        # heading, number included. Repeats take the renderer's suffix — the second
        # ``## Overview`` is ``overview-1``, the third ``overview-2`` — which is
        # exactly what ``kb.body_anchors`` publishes, so a ``#fragment`` that resolves
        # in the KB is the one structure.json advertises.
        base = gfm_anchor(title) or "section"
        seen_before = counts[base]
        counts[base] += 1
        anchor = base if not seen_before else "%s-%d" % (base, seen_before)
        node = _mk(level, title, anchor, hi, self_end, subtree_end)
        while stack and stack[-1][0] >= level:
            stack.pop()
        node["parent"] = stack[-1][1]["section_id"] if stack else None
        (stack[-1][1]["children"] if stack else outline).append(node)
        stack.append((level, node))

    return {"total_tokens": span(0, n), "has_toc": start > 0,
            "levels_inferred": levels_inferred, "outline": outline}
