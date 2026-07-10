"""
title: Heading-anchored section chunking (private)
layer: backend
public_api: no
summary: Deterministic doc->section split with STABLE heading-derived ids + content fingerprints.
"""
# 3.6-compatible, stdlib only (+ backend.ingest for markdown stripping).
#
# Why heading-anchored: the old chunker keyed sections by line offset, so any
# re-extraction (native -> docling, or a source edit) shifted every offset and
# invalidated every card. Here a section's identity is derived from its HEADING
# PATH, and a fingerprint is derived from its (markdown-stripped) BODY. So:
#   - same heading after re-extraction      -> same section_id (card addressable)
#   - same prose after re-extraction        -> same fingerprint (card reusable)
#   - only genuinely changed sections        -> new fingerprint (must re-card)
import hashlib
import re
from collections import namedtuple, Counter

from ..ingest import markdown_to_text

__all__ = ["Section", "chunk_sections", "is_heading", "normalize_title"]

# section_id is the stable key; fingerprint detects content change; l0/l1 are the
# CURRENT line span (addressing, always taken fresh — never trusted across builds).
# prefix is text PREPENDED to the materialized body (the repeated table header for a
# section/window that opens mid-table) so every chunk's table is self-describing; it is
# NOT part of the [l0,l1) line span, so consumers must prepend it explicitly.
Section = namedtuple("Section", ["section_id", "doc_id", "level", "parent",
                                 "title", "anchor", "l0", "l1", "fingerprint", "prefix"])

# Size-driven sectioning (the floor that prevents over-segmentation): accumulate
# ~SECTION_TARGET chars, then break AT the next heading within slack. A heading does
# NOT start a new section on its own — only the size budget does — so a databook with
# thousands of caps/numbered "heading-like" lines yields ~tens of sections, not thousands.
SECTION_TARGET = 16000   # aim ~16k chars (~4k tokens) per level-1 section
MIN_SEC = 2000           # a trailing remnant smaller than this merges into the previous
SUBSPLIT = 14000         # a single section larger than this is window-split into level-2
WIN = 9000               # window size (chars) for oversized sections

# Token-mode budgets: used when a ``token_count`` callable is supplied to
# chunk_sections. They are the char budgets above divided by ~4 chars/token, so
# section sizing tracks the RAG embedding-token budget DIRECTLY (real tokens, not a
# char proxy) while preserving the same shape. Bump these to match a specific
# embedding context window if needed — they are the only token-mode knobs.
SECTION_TARGET_TOK = 4000
MIN_SEC_TOK = 500
SUBSPLIT_TOK = 3500
WIN_TOK = 2200
_NUM = re.compile(r'^\s*\d+(?:\.\d+){0,3}\.?\s+')   # leading "1.2.3 " section number
_WS = re.compile(r"\s+")


def is_heading(s):
    # type: (str) -> int
    """Heading level (1-4) or 0. Recognizes ATX (`#`), numbered, keyword, and ALL-CAPS forms."""
    s = s.strip()
    if not s or len(s) > 120:
        return 0
    if re.match(r'^#{1,6}\s+\S', s):
        return s.count("#") if s.startswith("#") else 1
    if re.match(r'^(chapter|section|appendix|part)\s+[0-9IVXLA-Z]', s, re.I):
        return 1
    m = re.match(r'^(\d+(?:\.\d+){0,3})\.?\s+[A-Za-z]', s)
    if m:
        return 1 + m.group(1).count(".")
    letters = [c for c in s if c.isalpha()]
    if letters and len(s) <= 70 and len(s.split()) <= 10 and sum(c.isupper() for c in letters) / len(letters) > 0.85:
        return 1
    return 0


def is_toc_line(s):
    # type: (str) -> bool
    return bool(re.search(r'\.{4,}\s*\d+\s*$', s)) or bool(re.match(r'^\s*\d+\s*$', s))


def _is_table_row(s):
    # type: (str) -> bool
    """A markdown table row: starts with a pipe and has at least two cell delimiters."""
    s = s.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator_row(s):
    # type: (str) -> bool
    """A markdown header/body separator like ``|---|:--:|`` — pipes, dashes, colons only."""
    s = s.strip()
    if "-" not in s or "|" not in s:
        return False
    return all(c in "|:-" or c.isspace() for c in s)


def _table_headers(lines):
    # type: (list) -> dict
    """Map each table DATA-row line index -> its ``"header\\nseparator"`` block.

    A markdown table is a header row, a ``|---|`` separator, then data rows. When a
    size-driven section/window boundary lands on a data row (deep inside a big table),
    the resulting chunk would otherwise be headerless rows the carder can't interpret.
    This lets ``chunk_sections`` PREPEND the header block so every chunk is self-describing.
    Header/separator rows themselves are not mapped (their header is already in-body).
    """
    hdr = {}
    n = len(lines)
    i = 0
    while i < n - 1:
        if _is_table_row(lines[i]) and _is_separator_row(lines[i + 1]):
            block = lines[i] + "\n" + lines[i + 1]
            j = i + 2
            while j < n and _is_table_row(lines[j]):
                hdr[j] = block
                j += 1
            i = max(j, i + 1)
        else:
            i += 1
    return hdr


_DOT_LEADER_TOC = re.compile(r'\.{4,}\s*\d+\s*$')   # "1.2 Overview .......... 7"
_ATX_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+\S')
_IMG_LINK = re.compile(r'!\[[^\]]*\]\([^)]+\)')
_CODE_FENCE = re.compile(r'^\s{0,3}(```|~~~)')
_TOC_HEADER = re.compile(r'^\s*(table of contents|contents)\s*$', re.I)


def content_start(lines):
    # type: (list) -> int
    """Index of the first real content line, skipping a leading Table-of-Contents — but
    CONSERVATIVELY, because dropping real content from the outline is far worse than a
    slightly noisier one.

    A TOC is a CONTIGUOUS run at the very TOP of the document, made of dot-leader entries
    (``1.2 Overview .......... 7``), optionally under a ``Contents`` header, with blank
    lines allowed between entries. We skip only such a run, and only when it carries real
    dot-leader evidence. Two safeguards close the hole that let a lone ``1`` deep in the
    body swallow everything above it:

      * a bare page-number line (``5``) counts as TOC ONLY when already inside an open
        dot-leader run — never on its own (a stray number is not a table of contents); and
      * the candidate skip region is REJECTED (skip nothing) if it contains a heading,
        image, table or code fence — a genuine TOC has none of those, so their presence
        proves the detector misfired.

    When in doubt, return 0 (skip nothing): a TOC leaking a few nodes into the outline is
    recoverable; silently deleting a third of the document is not.
    """
    n = len(lines)
    limit = max(1, int(n * 0.45))
    i = 0
    while i < limit and not lines[i].strip():
        i += 1                                     # leading blanks
    if i < limit and _TOC_HEADER.match(lines[i]):
        i += 1                                     # an explicit "Contents" header
    last_toc = -1
    while i < limit:
        s = lines[i].strip()
        if not s:
            i += 1
            continue                               # blanks don't break the run
        if _DOT_LEADER_TOC.search(s) or (last_toc >= 0 and is_toc_line(s)):
            last_toc = i
            i += 1
            continue
        break                                      # first real content line ends the TOC
    if last_toc < 0:
        return 0                                   # no dot-leader evidence -> no TOC
    for j in range(last_toc + 1):                  # self-check: a TOC has no real content
        s = lines[j]
        if (_ATX_HEADING.match(s) or _IMG_LINK.search(s) or _CODE_FENCE.match(s)
                or _is_table_row(s)):
            return 0
    # Skip exactly the checked run [0, last_toc]. Never one line further: when real
    # content follows the last TOC entry with no blank between, a +2 would skip an
    # UNCHECKED line (e.g. the document's first heading). A leftover blank is harmless.
    return last_toc + 1


def normalize_title(s):
    # type: (str) -> str
    """Canonical heading text for anchoring: strip markers, numbering, case, whitespace.

    Makes a heading stable across extractors: ``## Background`` (docling),
    ``Background`` (native), and ``BACKGROUND`` all normalize to ``background``.
    """
    s = s.strip().lstrip("#").strip()
    s = re.sub(r'\s+#+\s*$', '', s)        # ATX close
    s = _NUM.sub('', s)                    # drop a leading "1.2 " section number
    return _WS.sub(' ', s).strip().lower()


def _fingerprint(body):
    # type: (str) -> str
    """Stable content hash of a section body, format-agnostic (markdown stripped)."""
    norm = _WS.sub(' ', markdown_to_text(body).lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _anchor_id(doc_id, anchor):
    # type: (str, str) -> str
    return "%s:%s" % (doc_id, hashlib.sha1(anchor.encode("utf-8")).hexdigest()[:10])


def _title_at(lines, a, b):
    for i in range(a, min(a + 4, b)):
        s = lines[i].strip().lstrip("#").strip()
        if s and not is_toc_line(s):
            return s[:120]
    return "section"


def _size_driven_spans(lines, start, n, span_size, heads, target, min_sec):
    """Greedily accumulate ~``target``-sized spans, breaking at the nearest heading.

    This is the size FLOOR: a section grows until it hits the size budget, then closes
    at the next heading within 50% slack (else cuts there). A trailing remnant < ``min_sec``
    folds into the previous section. ``span_size(a, b)`` measures a line range in the active
    unit (chars by default, tokens when a tokenizer is supplied). Returns [a, b) ranges.
    """
    spans, a, i = [], start, start
    while i < n:
        if span_size(a, i + 1) >= target:
            cut, j = i + 1, i + 1
            while j < n and span_size(a, j) < target * 1.5:
                if j in heads:
                    cut = j
                    break
                j += 1
            spans.append([a, cut])
            a = i = cut
        else:
            i += 1
    if a < n:
        if spans and span_size(a, n) < min_sec:
            spans[-1][1] = n
        else:
            spans.append([a, n])
    return spans or [[start, n]]


def chunk_sections(doc_id, text, token_count=None):
    # type: (str, str, object) -> list
    """Split ``text`` into size-bounded, heading-anchored ``Section``s with fingerprints.

    Size-driven (see ``_size_driven_spans``) so section COUNT stays sane — a heading does
    not start a new section by itself; the size budget does, breaking at headings. Each
    section's stable id is derived from its opening title (``<doc_id>:<hash(anchor)>``),
    so a same-extractor rebuild reuses unchanged cards; the fingerprint catches content
    changes. Sections larger than ``SUBSPLIT`` are window-split into level-2 children.

    ``token_count`` is an optional ``str -> int`` callable (e.g. an embedding model's
    tokenizer). When supplied, every size budget is measured in REAL TOKENS (the *_TOK
    constants) instead of chars, so the RAG window matches the embedding budget exactly.
    When omitted, sizing is char-based and byte-for-byte identical to before.
    """
    lines = text.split("\n")
    n = len(lines)
    if token_count is None:
        sizes = [len(ln) + 1 for ln in lines]     # +1 for the stripped newline
        target, min_sec, subsplit, win = SECTION_TARGET, MIN_SEC, SUBSPLIT, WIN
    else:
        sizes = [token_count(ln) for ln in lines]
        target, min_sec, subsplit, win = (SECTION_TARGET_TOK, MIN_SEC_TOK,
                                          SUBSPLIT_TOK, WIN_TOK)
    csum = [0]
    for sz in sizes:
        csum.append(csum[-1] + sz)
    span_size = lambda a, b: csum[b] - csum[a]  # noqa: E731
    start = content_start(lines)
    heads = set(i for i in range(start, n)
                if is_heading(lines[i]) and not is_toc_line(lines[i]))

    spans = _size_driven_spans(lines, start, n, span_size, heads, target, min_sec)
    tbl = _table_headers(lines)  # data-row line -> repeated header block (table-aware split)
    # prefix for a chunk opening at line ``a``: the table header to prepend, or "".
    prefix_for = lambda a: (tbl[a] + "\n") if a in tbl else ""  # noqa: E731
    counts = Counter()          # disambiguate repeated opening titles within a doc
    out = []
    for (a, b) in spans:
        title = _title_at(lines, a, b)
        base = normalize_title(title) or "section"
        counts[base] += 1
        anchor = base if counts[base] == 1 else "%s#%d" % (base, counts[base])
        sid = _anchor_id(doc_id, anchor)
        if span_size(a, b) > subsplit:
            # emit window children only (no overlapping parent) to keep counts bounded
            for j, (wa, wb) in enumerate(_windows(a, b, sizes, win)):
                pre = prefix_for(wa)
                out.append(Section(
                    section_id="%s.w%d" % (sid, j), doc_id=doc_id, level=2, parent=sid,
                    title=("%s (part %d)" % (title, j + 1)) if j else title,
                    anchor="%s.w%d" % (anchor, j), l0=wa, l1=wb,
                    fingerprint=_fingerprint(pre + "\n".join(lines[wa:wb])), prefix=pre))
        else:
            pre = prefix_for(a)
            out.append(Section(
                section_id=sid, doc_id=doc_id, level=1, parent=None,
                title=title, anchor=anchor, l0=a, l1=b,
                fingerprint=_fingerprint(pre + "\n".join(lines[a:b])), prefix=pre))
    return out


def _windows(l0, l1, sizes, win):
    out, a, cur = [], l0, 0
    for i in range(l0, l1):
        cur += sizes[i]
        if cur >= win and i + 1 < l1:
            out.append((a, i + 1))
            a = i + 1
            cur = 0
    out.append((a, l1))
    return out
