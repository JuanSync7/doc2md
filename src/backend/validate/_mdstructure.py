"""
title: What a renderer sees in the emitted markdown
layer: backend
public_api: no
summary: Reads structural facts out of markdown under CommonMark's own rules — list nesting, emphasis, code, links, tables — so the fidelity gate grades the rendered document rather than the converter's intent.
"""
# WHY THIS EXISTS
#
# The losslessness gate compares TOKENS, and list indentation, emphasis markers and
# code fences are not tokens. That is how a nested numbered procedure came out
# renumbered with every gate green (docs/quality-plan.md, P0.1): the converter
# indented a child by 2 columns when `1. ` needs 3, so CommonMark read the child as
# a SIBLING and the renderer numbered it 4 instead of 3.1.
#
# The lesson is the design rule for this module: it must read the markdown the way
# a renderer would, never the way the converter meant it. A reader that trusted the
# emitter's intent would have agreed the procedure was nested and confirmed the bug.
#
# So this is a small, deliberate CommonMark block+inline scanner. It is stdlib and
# 3.6-safe because backend.validate runs on the bare office host. Its correctness is
# not asserted, it is DIFFERENTIALLY TESTED against a real CommonMark implementation
# (marko) in tests/unit/backend/test_validate_mdstructure.py on the modern ring — the same
# trick the losslessness gate uses, where a second independent implementation is
# what makes the first one trustworthy.
#
# DELIBERATE NON-GOALS (the differential test names these too, so the two stay in
# step). We are grading markdown this project emits, not arbitrary markdown:
#   * Inline scanning is LINE AT A TIME. A code span or emphasis run split across a
#     soft line break is not seen. Nothing here emits one.
#   * Block quotes are counted but not descended into for BLOCK structure: a list
#     inside a quote contributes no list items (its inline text is still scanned).
#   * No HTML blocks, no link reference definitions, no entity references.
#   * Tables and `~~strike~~` are GFM, not CommonMark, so a stock CommonMark parser
#     cannot corroborate them; they are pinned by hand-written cases instead.
#   * Emphasis implements flanking and the rule of three but NOT cmark's
#     `openers_bottom` bound, which stops a closer re-scanning past an opener an
#     earlier closer already failed on. Pure delimiter soup with no words in it
#     (`*_**__**_`) can therefore over-count by one; a delimiter run that encloses
#     actual text cannot. Measured at <0.1% over 80k random delimiter strings.
import re

__all__ = ["md_structure"]

TAB = 4

# Block openers. Ordered-list markers accept both `.` and `)` per CommonMark.
_BULLET = re.compile(r"^([-+*])([ \t]+|$)")
_ORDERED = re.compile(r"^(\d{1,9})([.)])([ \t]+|$)")
_ATX = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_SETEXT = re.compile(r"^(=+|-+)[ \t]*$")
_BLOCKQUOTE = re.compile(r"^>[ \t]?")
_THEMATIC = re.compile(r"^(?:\*[ \t]*){3,}$|^(?:-[ \t]*){3,}$|^(?:_[ \t]*){3,}$")
# A GFM table delimiter row: | --- | :-: | ---: |
_DELIM_ROW = re.compile(r"^\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?$")

# CommonMark autolinks: an absolute URI, or a bare email address, in angles. The
# `\x00` exclusion is what stops a blanked-out code span from becoming a link.
_AUTOLINK_URI = re.compile(r"<[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^<>\x00-\x20]*>")
_AUTOLINK_MAIL = re.compile(
    r"<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*>")

# `\x00` is the blanking placeholder the inline pass writes over code spans and
# backslash escapes. Both stand for something that WAS punctuation (a backtick, a
# backslash), so flanking must see punctuation there or `\*not\*` and `a`x`*b*`
# would flank as if the neighbour were a word character.
_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~\x00")


# Cell and item CONTENT, reduced to the token notion the recall gate already uses.
# Inline links resolve to their visible text first: the converter renders a
# hyperlink as `[text](url)`, and the URL is markup, not cell content — comparing
# it against a source that never held it would fail every table with a link in it.
_WORDS = re.compile(r"[a-z0-9]+")
# `(?<!\\)` for the same reason `_BR` below carries it, and it was missing here: the
# converter ESCAPES a bracket when the document's own text contains one, so a cell
# or list item whose literal text is `[t](u)` is emitted `\[t\](u)` and every
# renderer shows the brackets. Reducing that to its "link text" dropped the `u` —
# a token the document really holds — and failed a faithful conversion of any cell
# that merely LOOKS like a link.
_INLINE_LINK = re.compile(r"!?(?<!\\)\[([^\]]*)\]\([^)]*\)")
# THE ONE PROJECT-SPECIFIC RULE IN THIS OTHERWISE CONVERTER-BLIND READER, and it is
# a statement about RENDERING, not about the converter's intent: a GFM cell cannot
# hold a newline, so a multi-paragraph table cell is written `a<br>b`, and every
# renderer draws a line break there — the reader sees two lines, never the letters
# "br". Tokenising the raw tag invented a word `br` that no source document can
# contain, so EVERY multi-paragraph docx cell (and every list item holding a soft
# line break) failed the fidelity gate on a perfect conversion.
# `(?<!\\)` matters: the converter escapes `\<br>` when the document TALKS about the
# tag, and that IS prose the source side carries. Deliberately NOT generalised to
# `<[^>]*>` — an HTML comment sentinel or a `<stderr>` in prose is content.
_BR = re.compile(r"(?<!\\)<br\s*/?>", re.I)
# The SECOND project-specific rule, and deliberately the narrowest one that works: an
# EMPTY html comment only. `_render_runs` writes `<!---->` between two adjacent
# emphasis spans whose delimiter runs would otherwise merge, and it writes it exactly
# where the two are adjacent with NO whitespace — between two halves of one word. A
# source that holds the single run pair `alpha` + `beta` says `alphabeta`; leaving the
# separator in said `alpha`, `beta` and failed a faithful conversion on
# `list_item_words`.
#
# Not widened to every comment, for the reason the note above `_BR` already gives: a
# content-bearing comment is CONTENT to this reader. Removed to NOTHING rather than to
# a space, because it never stands where a space belongs.
_EMPTY_COMMENT = re.compile(r"(?<!\\)<!---*->")
# THE THIRD, and the one that was costing real documents. `_WORDS` is `[a-z0-9]+`, so
# every other character SEPARATES — right for punctuation the document wrote, wrong
# for a marker the CONVERTER wrote. Bolding part of a word (`**Dma**Arbiter`, the most
# ordinary thing in a datasheet) made this side read ("dma", "arbiter") where the
# source truth correctly reads ("dmaarbiter",), so `list_item_words` disagreed and the
# document refused to publish at `token_recall: 1.0`.
#
# Safe for exactly one reason, and it is a property of the converter's ESCAPING rather
# than of its intent: `*`, `~` and a backtick are escaped whenever the document's own
# text contains one, so an unescaped run of them in emitted markdown is always a
# marker. `_` is NOT here — this converter never emits it as a marker, and leaves it
# unescaped inside an identifier (`CLK_100M`), so stripping it would JOIN two tokens
# the source separates.
_MARKER_RUN = re.compile(r"(?<!\\)[*~`]+")
# A code span is emitted VERBATIM — that is what it is for — so an asterisk inside one
# is the document's own and must keep separating. Matched here so the marker strip can
# step around it rather than through it.
_CODE_SPAN = re.compile(r"(?<!\\)(`+)(.+?)\1", re.S)


def _strip_markers(text):
    # type: (str) -> str
    """Drop the converter's marker runs, leaving code-span CONTENT untouched."""
    out = []  # type: list
    pos = 0
    for m in _CODE_SPAN.finditer(text):
        out.append(_MARKER_RUN.sub("", text[pos:m.start()]))
        out.append(m.group(2))
        pos = m.end()
    out.append(_MARKER_RUN.sub("", text[pos:]))
    return "".join(out)


def _words(text):
    # type: (str) -> tuple
    text = _INLINE_LINK.sub(r"\1", text or "")
    text = _EMPTY_COMMENT.sub("", _BR.sub(" ", text))
    return tuple(_WORDS.findall(_strip_markers(text).lower()))


def _expand(line):
    # type: (str) -> str
    """Tabs to spaces on a 4-column grid, so indentation is comparable."""
    if "\t" not in line:
        return line
    out = []
    for ch in line:
        if ch == "\t":
            out.append(" " * (TAB - len(out) % TAB))
        else:
            out.append(ch)
    return "".join(out)


def _indent_of(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip(" "))


def _table_candidate(para):
    # type: (list) -> bool
    """Is the open paragraph a GFM table rather than prose?

    Header row, delimiter row, and — the part that is easy to forget — the SAME
    number of cells in both. GFM refuses the table outright when the counts differ,
    so ``a | b | c`` over ``---|---`` is a paragraph (and its ``---`` underline a
    setext h2), not a two-column table. Without that check, resolving the candidate
    earlier would invent tables where a renderer sees a heading."""
    if len(para) < 2 or "|" not in para[0]:
        return False
    if not _DELIM_ROW.match(para[1].strip()):
        return False
    return len(_split_row(para[0])) == len(_split_row(para[1]))


class _List(object):
    """One open list item: the column its content starts at is what children must
    reach to be children. This single number is the whole nesting rule."""

    __slots__ = ("col", "ordered")

    def __init__(self, col, ordered):
        # type: (int, bool) -> None
        self.col, self.ordered = col, ordered


# ---------------------------------------------------------------- inline pass

def _strip_code_spans(text, counter):
    # type: (str, dict) -> str
    """Blank out code spans, counting them.

    CommonMark matches a backtick string only against one of exactly equal length,
    and everything between is literal — so emphasis markers inside `code` are not
    emphasis. Doing this first is what keeps the emphasis scan honest."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append("\x00\x00")
            i += 2
            continue
        if ch != "`":
            out.append(ch)
            i += 1
            continue
        run = 1
        while i + run < n and text[i + run] == "`":
            run += 1
        close, j = None, i + run
        while j < n:
            if text[j] == "`":
                clen = 1
                while j + clen < n and text[j + clen] == "`":
                    clen += 1
                if clen == run:
                    close = j
                    break
                j += clen
            else:
                j += 1
        if close is None:                     # unmatched backticks are literal text
            out.append("\x00" * run)
            i += run
            continue
        counter["code_spans"] += 1
        out.append("\x00" * (close + run - i))
        i = close + run
    return "".join(out)


def _flanking(text, start, end):
    # type: (str, int, int) -> tuple
    """(can_open, can_close) for the delimiter run text[start:end].

    This is CommonMark's left/right-flanking definition, which is what decides
    whether `snake_case_name`'s underscores are emphasis (they are not) or the
    surrounding `*`s are (they are)."""
    before = text[start - 1] if start > 0 else "\n"
    after = text[end] if end < len(text) else "\n"
    before_ws, after_ws = before.isspace(), after.isspace()
    before_punct, after_punct = before in _PUNCT, after in _PUNCT
    left = (not after_ws) and (not after_punct or before_ws or before_punct)
    right = (not before_ws) and (not before_punct or after_ws or after_punct)
    if text[start] == "_":
        # Intraword `_` opens nothing: DB_MAX_CONN_LIMIT must stay one word.
        return (left and (not right or before_punct),
                right and (not left or after_punct))
    return left, right


def _count_links(text, counter):
    # type: (str, dict) -> None
    """Count inline links and images by their opening bracket.

    Every `[` is considered on its own, with a nesting-aware search for its `]`.
    One left-to-right regex sweep cannot do this: on `[![alt](img)](url)` it
    consumes the outer bracket and the image inside the link text disappears."""
    n = len(text)
    for i in range(n):
        if text[i] != "[":
            continue
        depth, j = 1, i + 1
        while j < n and depth:
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
            j += 1
        if depth or j >= n or text[j] != "(":
            continue                          # a bare `[label]`, not an inline link
        if i and text[i - 1] == "!":
            counter["images"] += 1
        else:
            counter["links"] += 1


def _scan_inline(text, counter):
    # type: (str, dict) -> None
    """Count links, images, emphasis and strikethrough in one line of inline text."""
    text = _strip_code_spans(text, counter)

    _count_links(text, counter)
    counter["links"] += len(_AUTOLINK_URI.findall(text))
    counter["links"] += len(_AUTOLINK_MAIL.findall(text))

    counter["strike"] += _count_pairs(text, "~~")

    # Emphasis: walk delimiter runs with an opener stack, closing the nearest
    # compatible opener. Strong (2) is matched before em (1), which is the
    # behaviour that makes `**bold**` one strong run rather than two ems.
    stack, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "*_":
            i += 1
            continue
        run = 1
        while i + run < n and text[i + run] == ch:
            run += 1
        can_open, can_close = _flanking(text, i, i + run)
        length = run
        # Keep closing while the run has delimiters left: `***a***` spends 2 on a
        # strong and the leftover 1 on an em nested inside it. Re-finding the
        # opener each time is required — matching MUTATES the stack, and the
        # leftovers are what the next iteration is judged on.
        while can_close and length > 0:
            found = -1
            for idx in range(len(stack) - 1, -1, -1):
                och, olen, oclose = stack[idx]
                if och != ch:
                    continue
                if _rule_of_three(olen, oclose, length, can_open):
                    continue
                found = idx
                break
            if found < 0:
                break
            och, olen, oclose = stack[found]
            use = 2 if (olen >= 2 and length >= 2) else 1
            counter["strong" if use == 2 else "em"] += 1
            olen -= use
            length -= use
            del stack[found:]                 # everything between is discarded
            if olen > 0:
                stack.append((och, olen, oclose))
        if length > 0 and can_open:
            stack.append((ch, length, can_close))
        i += run


def _rule_of_three(open_len, open_can_close, close_len, close_can_open):
    # type: (int, bool, bool, bool) -> bool
    """True when CommonMark forbids pairing this opener with this closer.

    When either delimiter can face both ways, the two runs' lengths may not sum
    to a multiple of three unless both are multiples of three. It is what makes
    `*a**b*` one em rather than two. The lengths are the UNSPENT ones: after
    `***cd****` has spent two on a strong, the 1 and 2 left over sum to 3 and the
    pair is refused, so nothing further nests."""
    if not (open_can_close or close_can_open):
        return False
    if (open_len + close_len) % 3:
        return False
    return bool(open_len % 3) or bool(close_len % 3)


def _count_pairs(text, token):
    # type: (str, str) -> int
    """Matched `~~ ... ~~` pairs; GFM has no flanking rule for strikethrough."""
    return text.count(token) // 2


# ------------------------------------------------------------------ block pass

def md_structure(markdown):
    # type: (str) -> dict
    """Structural facts about ``markdown``, as a renderer would read it.

    Pass the BODY (front matter excluded) — every line span in this project is
    body-relative, and front matter is not markdown.

    Returns a comparable "fact vector"::

        {"headings": {1: 2, 2: 3},        # level -> count
         "heading_path": [(1, ("intro",)), (2, ("scope",))],   # IN DOCUMENT ORDER
         "thematic_breaks": 0,
         "list_items": {0: 4, 1: 2},      # nesting depth -> count
         "ordered_items": 6, "bullet_items": 4,
         "ordered_numbers": [1, 2, 3, 1, 2, 3],   # what a renderer PRINTS
         "strong": 2, "em": 1, "strike": 0,
         "code_spans": 3, "code_blocks": 1,
         "links": 1, "images": 3,
         "tables": [{"rows": 5, "cols": 4, "has_header": True}],
         "block_quotes": 0, "max_list_depth": 2}

    Counts are of BLOCKS, not lines: three consecutive indented lines are one
    code block, and two `>` lines are one block quote.

    ``ordered_numbers`` is the number the reader SEES, not the digit that was
    written. CommonMark takes a list's start from its first marker and then counts
    up on its own, so `1. / 2. / <picture> / 1. / 2.` prints 1,2,1,2 whatever the
    source meant — and that is the whole of the renumbering defect. Recording the
    rendered value is what makes a list split, a list wrongly merged, and a start
    that was ignored all show up as the same kind of difference.

    ``heading_path`` is the same argument applied to prose. ``headings`` is a
    HISTOGRAM, so exchanging two section titles leaves it identical, leaves the token
    multiset identical, and every gate certified a document that no longer said what
    the source said. Recording ``(level, tokens)`` IN DOCUMENT ORDER is what turns a
    permuted, retitled or re-levelled outline into a difference. Tokens rather than
    raw text, for the reason the table cells use them: escaping and emphasis markers
    are markup, not content.

    ``thematic_breaks`` counts the one construct that DELETES ITS OWN CHARACTERS.
    A body paragraph of `-----` renders as an <hr> and carries no ASCII tokens, so
    recall reads a vacuous 1.0 over text that has left the document; a count is the
    only handle a gate can get on that substitution.
    """
    counter = {"strong": 0, "em": 0, "strike": 0, "code_spans": 0,
               "links": 0, "images": 0}
    # The ORDER the graded blocks appear in, one entry per block. Every other fact
    # here is a total or a per-kind list, and neither can see a block that moved
    # between sections: relocate a table from under one heading to under another and
    # the histogram, the geometry, the cell contents and the token multiset are all
    # untouched. `block_sequence` is the interleaving those facts are missing, and it
    # is built ONLY from blocks the fact vector already states, so it predicts
    # nothing new — it says where what is already counted actually sits.
    block_sequence = []  # type: list

    def add_block(kind, key):
        block_sequence.append((kind, key))

    headings = {}
    list_items = {}
    ordered_items = bullet_items = code_blocks = block_quotes = 0
    thematic_breaks = 0
    list_item_words = []  # type: list
    heading_path = []  # type: list
    ordered_numbers = []  # type: list
    tables = []
    # depth -> (ordered?, marker/delimiter, last rendered number) for the list open
    # at that depth. A list is what a renderer counts within; the item stack below
    # cannot stand in for it, because two items in a row at the same depth may
    # belong to two different lists.
    lists = {}  # type: dict

    def close_lists(from_depth):
        """Every list open at ``from_depth`` or deeper has ended."""
        for level in [k for k in lists if k >= from_depth]:
            del lists[level]

    lines = [_expand(l) for l in (markdown or "").split("\n")]
    stack = []                                    # open list items, outermost first
    fence = ""                                    # the open fence, "" when closed
    fence_base = 0                                # content column of its container
    para = []                                     # the open paragraph's lines
    para_depth = 0                                # len(stack) where it opened
    in_icode = False                              # inside an indented code block
    in_quote = False                              # inside a block quote
    i = 0
    # A list item's first line, re-queued to be read again at its content column
    # (see the splice below). It holds at most one line at a time — each is consumed
    # by the very next iteration — and it is a stack rather than an insert into
    # `lines` so the cost stays O(1): splicing into the list moved every remaining
    # line on every list item, which measured 30us/item against 18 at 80k items.
    pending = []  # type: list

    def flush_table():
        # Resolve the open paragraph: a header + delimiter row makes it a table.
        if _table_candidate(para):
            cols = len([c for c in _split_row(para[1])])
            body = [r for r in para[2:] if "|" in r]
            cells = []
            for row in [para[0]] + body:
                got = [_words(c) for c in _split_row(row)]
                cells.append(tuple((got + [()] * cols)[:cols]))
            # `cells` is what makes a TRANSPOSITION detectable: swap two values
            # between rows and the geometry, the counts and the token multiset are
            # all unchanged, while the table now says something else entirely.
            tables.append({"rows": 1 + len(body), "cols": cols,
                           "has_header": True, "cells": cells})
            add_block("table", (1 + len(body), cols))
            for row in [para[0]] + body:
                for cell in _split_row(row):
                    _scan_inline(cell, counter)
        else:
            for row in para:
                _scan_inline(row, counter)
        del para[:]

    while pending or i < len(lines):
        if pending:
            raw = pending.pop()
        else:
            raw = lines[i]
            i += 1
        stripped = raw.strip()
        indent = _indent_of(raw)
        content = raw[indent:]

        if fence:
            # A CLOSING fence has two conditions, and this reader used to check only
            # the first. (1) The run: at least as long as the opener, same character,
            # nothing else on the line. (2) The POSITION: a closing fence may be
            # indented at most three columns past its CONTAINER's content column —
            # four is code content, not a close. Getting (2) wrong ended a col-0 fence
            # early and swallowed the heading that followed it.
            if (stripped.startswith(fence) and set(stripped) <= set(fence[0] + " ")
                    and fence_base <= indent <= fence_base + 3):
                fence = ""
                continue
            if stripped and indent < fence_base:
                # The list item (or quote) that HELD this fence has ended, so the code
                # block ends with it — but it was never closed, so a renderer keeps
                # reading. This line is a block in its own right: fall through and
                # re-process it, which is how a stray col-0 ``` under an indented
                # fence OPENS a new one instead of tidily closing the old.
                fence = ""
            else:
                continue

        if not stripped:
            # A blank line ends the paragraph and the quote but NOT an indented
            # code block: blanks inside one are part of it, not a separator.
            flush_table()
            in_quote = False
            continue

        # Does this line START a block, or merely continue the open paragraph?
        # A lazy continuation must not close its list, or `1. a` / `text` /
        # `2. b` would renumber into two lists.
        bm = _BULLET.match(content)
        om = _ORDERED.match(content)
        starter = bool(_BLOCKQUOTE.match(content) or _FENCE.match(content)
                       or _ATX.match(content) or _THEMATIC.match(stripped)
                       or bm or om)
        # `* * *` matches the bullet pattern and is a thematic break, which ends a
        # list rather than continuing one.
        is_item = bool(bm or om) and not _THEMATIC.match(stripped)
        if starter or not para:
            # Close every list whose content column this line does not reach.
            # Doing this BEFORE anything else is what makes a dedent end a list,
            # and is the exact rule the renumbering bug fell foul of.
            while stack and indent < stack[-1].col:
                stack.pop()
            if not is_item:
                # Whatever this line opens, it is not a list item, so every list at
                # this level or deeper is over. A continuation indented INTO the
                # open item does not end that item's list: len(stack) still counts
                # it, so only the lists nested inside it close.
                close_lists(len(stack))
        if para and len(stack) != para_depth:
            flush_table()                         # the paragraph's container closed

        base = stack[-1].col if stack else 0

        if indent >= base + TAB:
            in_quote = False
            if para:
                para.append(content)              # continuation, NOT a code block
                continue
            if not in_icode:
                code_blocks += 1                  # an indented code block
                add_block("code", 0)
                in_icode = True
                close_lists(len(stack))
            continue
        in_icode = False

        if _BLOCKQUOTE.match(content):
            flush_table()
            if not in_quote:
                block_quotes += 1
                in_quote = True
                add_block("quote", 0)
            _scan_inline(_BLOCKQUOTE.sub("", content), counter)
            continue
        in_quote = False

        fm = _FENCE.match(content)
        if fm:
            flush_table()
            fence = fm.group(1)
            fence_base = base                     # the column its close is judged from
            code_blocks += 1
            add_block("code", 0)
            continue

        # A setext underline turns the open paragraph into a heading. It has to
        # be tested before the thematic break, because `---` under a paragraph is
        # an h2 and only a rule elsewhere. It may not be a LAZY continuation, so
        # it must reach the paragraph's own content column.
        if para and len(stack) == para_depth and indent >= base \
                and _SETEXT.match(content):
            if _table_candidate(para):
                # The open paragraph is a GFM TABLE, and a table has no setext
                # underline: `---` under its last row is a thematic break and the
                # table stands. Resolving the candidate first is what stops the
                # whole table being deleted and a heading nobody renders invented
                # in its place.
                flush_table()
            else:
                level = 1 if content[0] == "=" else 2
                headings[level] = headings.get(level, 0) + 1
                heading_path.append((level, _words(" ".join(para))))
                add_block("h", level)
                close_lists(len(stack))           # `-` alone reads as a bullet above
                for row in para:
                    _scan_inline(row, counter)
                del para[:]
                continue

        atx = _ATX.match(content)
        if atx:
            flush_table()
            level = len(atx.group(1))
            headings[level] = headings.get(level, 0) + 1
            heading_path.append((level, _words(atx.group(2) or "")))
            add_block("h", level)
            _scan_inline(atx.group(2) or "", counter)
            continue

        if _THEMATIC.match(stripped):
            flush_table()
            # A thematic break DELETES its own characters from the render: `-----`
            # typed as a body paragraph draws an <hr> and the hyphens are gone. It
            # carries no tokens, so recall is blind to it; counting it is the only
            # way the fidelity gate can see that substitution happen.
            thematic_breaks += 1
            add_block("hr", 0)
            continue

        if bm or om:
            marker = bm.group(1) if bm else (om.group(1) + om.group(2))
            spaces = (bm.group(2) if bm else om.group(3)) or " "
            rest = content[len(marker) + len(spaces):]
            # A list may interrupt a paragraph only if the item is non-empty, and
            # an ordered one only if it is numbered 1 — otherwise `see step 2.`
            # style prose would sprout a list.
            if para and (not rest.strip() or (om and om.group(1) != "1")):
                para.append(content)
                continue
            flush_table()
            # An item whose content is 5+ columns away starts an indented code
            # block instead; CommonMark then treats the marker width as 1 space.
            wide = len(spaces) > TAB
            width = 1 if wide else len(spaces)
            depth = len(stack)
            list_items[depth] = list_items.get(depth, 0) + 1
            add_block("li", depth)
            # Ordered item CONTENT: a procedure whose steps were swapped has the
            # same depth histogram and the same token multiset as the real one.
            list_item_words.append(_words(rest))
            close_lists(depth + 1)                # a dedent ended every deeper list
            if om:
                ordered_items += 1
                delim = om.group(2)
                # A list continues only while its type AND its delimiter hold:
                # `1.` after `1)` is a new list to CommonMark, which is the one way
                # markdown can show a restart with no separating block between.
                open_here = lists.get(depth)
                if open_here and open_here[0] and open_here[1] == delim:
                    number = open_here[2] + 1     # the renderer counts, not the digit
                else:
                    number = int(om.group(1))     # a new list starts at its marker
                lists[depth] = (True, delim, number)
                ordered_numbers.append(number)
            else:
                bullet_items += 1
                lists[depth] = (False, bm.group(1), 0)
            stack.append(_List(indent + len(marker) + width, bool(om)))
            if wide and rest.strip():
                code_blocks += 1                  # the item opens with code
                in_icode = True
            elif rest.strip():
                # An item's FIRST line is a LINE, at the item's content column —
                # not a paragraph by fiat. After `- `, CommonMark opens a heading, a
                # nested list, a quote, a fence or a thematic break exactly as it
                # would at column 0, and treating that text as prose made this
                # reader disagree with a real parser about five constructs at once:
                #
                #     markdown          marko            this reader (before)
                #     `- 15. step`      ul,li,ol,li      one bullet, no ordered item
                #     `- ## Rollout`    ul,li,h2         one bullet, no heading
                #     `- > quote`       ul,li,blockquote one bullet, no quote
                #     `- + item`        ul,li,ul,li      one bullet, not two
                #     `- [r]: dest`     ul,li            one bullet -- and the LINE
                #                                        is gone from the render
                #
                # It saw the same constructs perfectly on a CONTINUATION line
                # (`- item` / `  ## H2` counts the heading), so the gap was the
                # marker's own line and nothing else. That is precisely where a
                # converter puts source text, which is what made an unescaped
                # bullet reading `- - -` a loss BOTH gates certified.
                #
                # Re-dispatching the rest through this same loop, indented to the
                # column it really occupies, is what makes the item's first line
                # obey the same rules as every other line. `rest` is always shorter
                # than `content` by at least the marker, so this terminates — and
                # `pending` is a one-slot stack rather than a splice into `lines`,
                # so it terminates in LINEAR time.
                pending.append(" " * stack[-1].col + rest)
            continue

        if "|" not in content:
            flush_table()                         # can't extend a table candidate
        para.append(content)
        para_depth = len(stack)

    flush_table()
    result = {
        "headings": headings,
        "heading_path": heading_path,
        "thematic_breaks": thematic_breaks,
        "list_items": list_items,
        "ordered_items": ordered_items,
        "bullet_items": bullet_items,
        "ordered_numbers": ordered_numbers,
        "code_blocks": code_blocks,
        "block_quotes": block_quotes,
        "tables": tables,
        "list_item_words": list_item_words,
        "block_sequence": block_sequence,
        "max_list_depth": (max(list_items) + 1) if list_items else 0,
    }
    result.update(counter)
    return result


def _split_row(row):
    # type: (str) -> list
    """Cells of a GFM table row, dropping the leading/trailing pipe."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]
