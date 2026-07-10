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
# hierarchy. Both share the heading helpers (is_heading/normalize_title) so they agree
# on what a heading is. See docs/design/output-contract.md (structure.json).
#
# Per node we record two token counts, computed directly from a cumulative-size array
# so they partition cleanly (a parent's subtree == its self + its children's subtrees):
#   self_tokens    : the body directly under a heading, BEFORE its first subheading
#   subtree_tokens : the whole section including all descendants
# These make "which heading blows the embedding budget?" a lookup, not a re-tokenize.
import os
import re
from collections import Counter

from ._chunk import (is_heading, is_toc_line, content_start, normalize_title,
                     _is_table_row, _is_separator_row, _TOC_HEADER)

__all__ = ["document_outline", "outline_coverage"]

_IMG = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)')   # markdown image: ![alt](ref ...
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


def _count_tables(lines, a, b):
    # type: (list, int, int) -> int
    """Number of markdown tables that START within [a, b) (header row + separator)."""
    n = 0
    i = a
    while i < b - 1:
        if _is_table_row(lines[i]) and _is_separator_row(lines[i + 1]):
            n += 1
            i += 2
        else:
            i += 1
    return n


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

    Returns ``{"total_tokens": int, "has_toc": bool, "outline": [node, ...]}`` where
    each node is a JSON-serializable dict: ``id, level, title, anchor, line_span,
    self_tokens, subtree_tokens, tables, images, children``. ``has_toc`` records
    whether a leading table-of-contents block was detected and skipped. The caller
    wraps this with doc-level metadata (doc_id, source_format, lane, token_model).
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
    # all-caps rule) and list items (``1. Foo`` numbered items). The converter already
    # marks true headings as ATX ``#``, so this keeps the outline a faithful hierarchy
    # instead of one node per list bullet.
    heads = [(i, is_heading(lines[i])) for i in range(start, n)
             if is_heading(lines[i]) and not is_toc_line(lines[i])
             and not _is_table_row(lines[i]) and not _is_separator_row(lines[i])
             and not _LIST_ITEM.match(lines[i])]

    counts = Counter()
    seq = [0]                                   # running node-id counter (list = closure-writable)

    def _mk(level, title, anchor, a, self_end, subtree_end):
        seq[0] += 1
        return {
            "id": "sec-%04d" % seq[0],
            "level": level, "title": title, "anchor": anchor,
            "line_span": [a, subtree_end],
            "self_tokens": span(a, self_end),
            "subtree_tokens": span(a, subtree_end),
            "tables": _count_tables(lines, a, self_end),
            "images": _images_in(lines, a, self_end),
            "children": [],
        }

    outline = []
    # Preamble: content before the first heading is a top-level LEAF so counts are
    # complete (no tokens fall outside the tree). It is not on the nesting stack, so it
    # never adopts the real headings that follow it.
    first = heads[0][0] if heads else n
    if first > start and "".join(lines[start:first]).strip():
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
        base = normalize_title(title) or "section"
        counts[base] += 1
        anchor = base if counts[base] == 1 else "%s#%d" % (base, counts[base])
        node = _mk(level, title, anchor, hi, self_end, subtree_end)
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1]["children"] if stack else outline).append(node)
        stack.append((level, node))

    return {"total_tokens": span(0, n), "has_toc": start > 0, "outline": outline}
