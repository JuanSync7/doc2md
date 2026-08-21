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

__all__ = ["Section", "chunk_sections", "gfm_anchor", "is_heading", "normalize_title"]

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
# A leading "1.2.3 " section number. Two alternatives, because real documents lose
# the space: a DOTTED number may be glued to the title ("1.2reference documents" —
# no ordinary word starts "<digit>.<digit>"), a BARE integer may not ("3D layout" is
# a title, not section 3 of "D layout"; "2026 budget" still loses its year only when
# a space follows, exactly as before).
_NUM = re.compile(r'^\s*(?:\d+(?:\.\d+){1,3}\.?\s*|\d+\.?\s+)')
_WS = re.compile(r"\s+")

# The URL fragment a rendered heading is addressable by — the SINGLE anchor scheme
# (docs/quality-plan.md C3). This is deliberately a re-implementation of
# ``backend.validate.gfm_anchor`` rather than an import of it: this package may only
# depend on ``backend.ingest`` (see its CLAUDE.md), and the rubric that GRADES anchors
# must not share code with the layer that PRODUCES them — the same converter-blind
# rule the token gate follows. ``tests/unit/backend/test_anchor_parity.py`` is what
# keeps the copies honest (here, ``backend.validate.gfm_anchor``,
# ``backend.kb.heading_anchor``).
#
# THE SECTION NUMBER STAYS IN THE ANCHOR: "1.2 Scope" -> "12-scope", not "scope".
# An anchor's only job is to be the fragment a renderer emits for that heading, and
# every common renderer (GitHub, Python-Markdown, pandoc) drops the dot and keeps the
# digits. ``kb._lint._check_refs`` grades every "#fragment" ref against
# ``kb.body_anchors``, which is derived from the rendered heading text — so an anchor
# with the number stripped would report every ref into a numbered section as a dead
# link. It also keeps two same-named sections ("2.1 Overview" / "3.1 Overview")
# distinct without needing a disambiguating suffix.
_ANCHOR_DROP = re.compile(r'[^\w\s-]', re.UNICODE)
_ANCHOR_SEP = re.compile(r'[-\s]+', re.UNICODE)


def gfm_anchor(title):
    # type: (str) -> str
    """The ``#fragment`` a rendered heading titled ``title`` is addressable by.

    Lowercase, drop everything that is not a word character / whitespace / hyphen
    (underscores and non-ASCII letters survive — dropping them reported live links
    as dead), then collapse whitespace-and-hyphen runs to a single hyphen and trim.
    """
    s = _ANCHOR_DROP.sub('', (title or "").strip().lower())
    return _ANCHOR_SEP.sub('-', s).strip('-')


# --- bounds on the ALL-CAPS heuristic (docs/quality-plan.md P4.6) -------------
# Un-marked-up native text shouts its headings, so an ALL-CAPS line is real evidence
# of one. It is ALSO how a runbook shouts an instruction — "DO NOT REBOOT THE PRIMARY
# NODE" — and promoting that to a heading does not merely add a node: it becomes a
# level-1 heading that REPARENTS every section after it, with both gates green (the
# text is all still there; only its shape is wrong). So the branch is bounded.
#
# The separating idea is grammatical, not lexical: a heading is a noun-phrase LABEL,
# a callout is a CLAUSE. Three shape bounds and one closed word list, no model:
_CAPS_MAX_CHARS = 60          # labels are short; 70 admitted whole sentences
_CAPS_MAX_WORDS = 6           # "ABSOLUTE MAXIMUM RATINGS AND LIMITS" is 5
_CAPS_TERMINAL = ".!?,;:"     # a label does not end in a full stop or a comma
# Determiners, pronouns, auxiliaries, modals and negations: a label almost never
# needs one, a sentence almost always carries one. OF / AND / FOR / IN / TO / ON are
# deliberately ABSENT — "THEORY OF OPERATION" and "TERMS AND ABBREVIATIONS" are
# headings, and a list that rejected them would trade one silent failure for another.
_CLAUSE_WORDS = frozenset((
    "THE", "A", "AN", "THIS", "THAT", "THESE", "THOSE", "ITS", "IT", "YOU",
    "YOUR", "WE", "OUR", "THEY", "THEIR",
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "AM",
    "HAS", "HAVE", "HAD", "DO", "DOES", "DID",
    "WILL", "WOULD", "SHALL", "SHOULD", "CAN", "COULD", "MAY", "MIGHT",
    "MUST", "CANNOT", "NOT", "NEVER", "ALWAYS", "PLEASE", "DONT", "DON'T",
))
# P4.6's list bounded the branch by FUNCTION words, and an imperative has none of
# them: no determiner, no pronoun, no auxiliary. Three of four realistic runbook
# callouts still walked into the tree — "POWER DOWN ALL NODES FIRST", "CONTACT SRE
# BEFORE FAILOVER", "DISABLE AUTOSCALING DURING MAINTENANCE" — while only
# "ESCALATE TO THE ON-CALL LEAD IMMEDIATELY" was stopped, and only by its THE.
#
# What an imperative DOES carry is the adverbial furniture a clause needs to say
# WHEN, or HOW MANY: a noun-phrase label names a thing, it does not schedule one.
# Same grammatical principle as above, second closed list. FIRST / LAST are absent
# on purpose ("FIRST BOOT" is a real heading) and are not needed: the callouts that
# end in FIRST reach for ALL or THE on the way.
_ADVERBIAL_WORDS = frozenset((
    "ALL", "ANY", "EVERY", "EACH", "BOTH", "ONLY", "JUST",
    "BEFORE", "AFTER", "DURING", "WHILE", "UNTIL", "UNLESS", "WHENEVER",
    "WHEN", "IF", "THEN", "ELSE", "AGAIN", "NOW", "SOON", "TWICE",
    "IMMEDIATELY", "ALREADY",
))
# HONEST ABOUT WHAT REMAINS: a bare imperative with a bare object and no adverbial
# — "RESTART NGINX", "RUN DIAGNOSTICS", "FLUSH CACHE" — is still promoted, because
# nothing in its SHAPE separates it from "RESET SEQUENCE" or "POWER SUPPLY". Shape
# alone cannot decide whether the first word is a verb or a noun; a part-of-speech
# model could, and this layer is deliberately model-free. The bound removes the
# common class — anything that says WHEN or HOW MANY — not every case.
_CAPS_WORD = re.compile(r"[^A-Z']")   # keep letters and the apostrophe of DON'T


def _looks_like_a_sentence(s):
    # type: (str) -> bool
    """True when an ALL-CAPS line reads as a clause rather than a section label."""
    if len(s) > _CAPS_MAX_CHARS or s[-1] in _CAPS_TERMINAL:
        return True
    words = s.split()
    if len(words) > _CAPS_MAX_WORDS:
        return True
    for w in words:
        bare = _CAPS_WORD.sub("", w.upper())
        if bare in _CLAUSE_WORDS or bare in _ADVERBIAL_WORDS:
            return True
    return False


# --- bounds on the KEYWORD heuristic (docs/quality-plan.md P4.7) --------------
# "Chapter 4", "Appendix A", "Section 2.3" open a section in un-marked-up native
# text, so the keyword is real evidence. The branch was UNBOUNDED, and worse than
# the ALL-CAPS one ever was: `[0-9IVXLA-Z]` under `re.I` matches ANY letter, so the
# designator slot accepted the next ordinary word and every one of these was
# published as a LEVEL-1 heading that reparented the rest of the document —
#     "Section prose."
#     "Section 4 describes the reset sequence in detail."
#     "Part of the clock tree is duplicated for the display pipe."
#     "Chapter references appear at the end of the document."
# Bounded on the same grammatical principle: a heading is a noun-phrase LABEL.
#
#   1. The DESIGNATOR is matched case-SENSITIVELY — a digit run, a roman numeral,
#      or a single capital letter — so `Part of`, `Chapter references` and
#      `Section prose` no longer have one, and the keyword alone can no longer
#      carry a sentence into the tree.
#   2. What follows it must READ as a label. The decisive test is capitalisation:
#      every convention for writing a title capitalises its first word (title case
#      and sentence case alike), while a sentence that runs on past "Section 4"
#      continues in lower case — `describes`, `covers`, `lists`. Plus the same
#      shape bounds the ALL-CAPS branch uses: no terminal punctuation, no run-on
#      length, no clause word.
#
# HONEST ABOUT WHAT REMAINS: a title-cased sentence with a capitalised verb and no
# clause word ("Section 4 Covers Reset") is still promoted, and a genuine heading
# written in lower case after its number ("Section 4 reset sequence") is now
# missed. The second error costs a node; the first costs the whole tree below it,
# which is why the bound leans this way.
_KEYWORDS = frozenset(("chapter", "section", "appendix", "part"))
_DESIGNATOR = re.compile(r'^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[A-Z])(?![0-9A-Za-z])')
_KEY_MAX_CHARS = 80           # a label plus a title, not a sentence
_KEY_MAX_WORDS = 10           # "Chapter 7 Design of the Memory Subsystem Controller"
_KEY_LEAD = re.compile(r'^[^0-9A-Za-z]*(.)')   # first alphanumeric of the remainder
# Auxiliaries, modals, negations and pronouns. Articles and prepositions are
# deliberately ABSENT here (unlike the ALL-CAPS list): a mixed-case chapter title
# carries them freely — "Chapter 7 Design of the Memory Subsystem Controller".
_VERBAL_WORDS = frozenset((
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "AM",
    "HAS", "HAVE", "HAD", "DOES", "DID",
    "WILL", "WOULD", "SHALL", "SHOULD", "CAN", "COULD", "MIGHT",
    "MUST", "CANNOT", "NOT", "NEVER",
    "IT", "ITS", "YOU", "YOUR", "WE", "OUR", "THEY", "THEIR",
))


def _is_keyword_heading(s):
    # type: (str) -> bool
    """True when ``s`` is a ``Chapter 4`` / ``Appendix A: Register map`` LABEL."""
    parts = s.split(None, 2)
    if len(parts) < 2 or parts[0].lower() not in _KEYWORDS:
        return False
    if not _DESIGNATOR.match(parts[1]):
        return False
    if len(s) > _KEY_MAX_CHARS or s[-1] in _CAPS_TERMINAL:
        return False
    if len(s.split()) > _KEY_MAX_WORDS:
        return False
    rest = parts[2] if len(parts) > 2 else ""
    lead = _KEY_LEAD.match(rest)
    if lead and lead.group(1).isalpha() and not lead.group(1).isupper():
        return False                      # a lower-case continuation is a clause
    for w in rest.split():
        if _CAPS_WORD.sub("", w.upper()) in _VERBAL_WORDS:
            return False
    return True


def is_heading(s):
    # type: (str) -> int
    """Heading level (1-6) or 0. Recognizes ATX (`#`), numbered, keyword, and ALL-CAPS forms."""
    s = s.strip()
    if not s or len(s) > 120:
        return 0
    # The LEADING RUN of hashes, never ``count("#")``: a Word heading reading
    # "Issue #42 metastability on the strap bus" is one `#` plus a body hash, and
    # counting them published it at level 2 — so the next real H1 became its
    # sibling and the whole tree below shifted. The same bug demoted an ATX-closed
    # "## Level Two ##" to level 4.
    m = re.match(r'^(#{1,6})\s+\S', s)
    if m:
        return len(m.group(1))
    if _is_keyword_heading(s):
        return 1
    m = re.match(r'^(\d+(?:\.\d+){0,3})\.?\s+[A-Za-z]', s)
    if m:
        return 1 + m.group(1).count(".")
    letters = [c for c in s if c.isalpha()]
    if (letters and sum(c.isupper() for c in letters) / len(letters) > 0.85
            and not _looks_like_a_sentence(s)):
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


def _table_headers(lines, fenced=None):
    # type: (list, list) -> dict
    """Map each table DATA-row line index -> its ``"header\\nseparator"`` block.

    A markdown table is a header row, a ``|---|`` separator, then data rows. When a
    size-driven section/window boundary lands on a data row (deep inside a big table),
    the resulting chunk would otherwise be headerless rows the carder can't interpret.
    This lets ``chunk_sections`` PREPEND the header block so every chunk is self-describing.
    Header/separator rows themselves are not mapped (their header is already in-body).

    ``fenced`` is ``fenced_lines(lines)``: pipe art inside a code transcript is code,
    not a table, so it never contributes a header to prepend.
    """
    hdr = {}
    n = len(lines)
    if fenced is None:
        fenced = fenced_lines(lines)
    i = 0
    while i < n - 1:
        if (not fenced[i] and not fenced[i + 1]
                and _is_table_row(lines[i]) and _is_separator_row(lines[i + 1])):
            block = lines[i] + "\n" + lines[i + 1]
            j = i + 2
            while j < n and not fenced[j] and _is_table_row(lines[j]):
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


def fenced_lines(lines):
    # type: (list) -> list
    """Per-line ``True`` for "this line is CODE, not prose" (docs/quality-plan.md P4.8).

    A fenced block is a verbatim transcript, and every heading/table heuristic in
    this package is a prose heuristic. Without this, a shell comment inside a
    command transcript —

        ```
        # reset the board
        kestrelctl board reset --wait
        ```

    — was published as a section, and because the section's span runs to the next
    heading it SWALLOWED the closing fence: the outline claimed a heading the
    document never had, over a body whose code block no longer terminates. The same
    hole let a ``|`` table drawn inside a fence be reported as a real GFM table.

    ``_links_in`` has tracked fences since links were harvested; this is that same
    state, computed once and shared, so links, headings and tables agree on where
    the code is. The delimiter lines themselves are marked too (they are the fence,
    not prose), and an UNCLOSED fence runs to the end of the document — which is
    what CommonMark does with one, so the mask matches the renderer.
    """
    mask = [False] * len(lines)
    in_fence = False
    for i, line in enumerate(lines):
        if _CODE_FENCE.match(line):
            mask[i] = True
            in_fence = not in_fence
            continue
        mask[i] = in_fence
    return mask


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
    # Fenced lines are code: a chunk must never break on a shell comment, which
    # would cut a transcript in half and leave the tail with no opening fence.
    fenced = fenced_lines(lines)
    heads = set(i for i in range(start, n)
                if not fenced[i] and is_heading(lines[i]) and not is_toc_line(lines[i]))

    spans = _size_driven_spans(lines, start, n, span_size, heads, target, min_sec)
    tbl = _table_headers(lines, fenced)  # data-row line -> repeated header block
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
