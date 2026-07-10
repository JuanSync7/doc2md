"""
title: Markdown -> clean text (private)
layer: backend
public_api: no
summary: Strip GFM markdown to plain prose for the grep entity-linker (text_lc shadow).
"""
# 3.6-compatible. Stdlib only.
#
# The job: turn docling/GFM markdown into clean prose that literal Aho-Corasick
# matching (grep_link.py) can scan, WITHOUT fusing or splitting entity phrases.
#   "**Silicon** Operations"      -> "Silicon Operations"
#   "[Owen Carter](mailto:o@x)"   -> "Owen Carter"
#   "| Owen Carter | Lead |"      -> "Owen Carter Lead"   (cells space-joined, never fused)
# This is intentionally NOT a full markdown parser; it is a lossy-but-safe strip
# whose only contract is "preserve the prose tokens, drop the syntax".
import re

__all__ = ["markdown_to_text", "collapse_table_padding"]

# (?<!\\): a converter-escaped literal "\<!-- ... -->" is prose, not a comment.
_COMMENT = re.compile(r"(?<!\\)<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```+|~~~+)")
_HR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_ATX = re.compile(r"^\s*#{1,6}\s+")
_ATX_CLOSE = re.compile(r"\s+#+\s*$")
_BLOCKQUOTE = re.compile(r"^\s*>+\s?")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|")

_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REF_LINK = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<((?:https?://|mailto:)[^>]+)>")
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_ITALIC = re.compile(r"(?<![\w*_])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*_])", re.S)
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
# CommonMark: a backslash before ASCII punctuation makes it LITERAL text. These
# must be hidden BEFORE the link/emphasis strips run — "\[SA,TD\](zero,SDF)" is
# prose, not a link — and restored as the bare character afterwards.
_ESCAPED_PUNCT = re.compile(r"\\([!-/:-@\[-`{-~])")
_PLACEHOLDER = re.compile(r"\x00([0-9a-f]{2})")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def _split_cells(line):
    # type: (str) -> str
    """A markdown table row -> its cell texts joined by single spaces."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # split on unescaped pipes
    cells = re.split(r"(?<!\\)\|", s)
    return " ".join(c.strip() for c in cells if c.strip())


def _inline(text):
    # type: (str) -> str
    text = _ESCAPED_PUNCT.sub(lambda m: "\x00%02x" % ord(m.group(1)), text)
    text = _IMG.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _REF_LINK.sub(lambda m: m.group(1), text)
    text = _AUTOLINK.sub(lambda m: m.group(1), text)
    text = _INLINE_CODE.sub(lambda m: m.group(1), text)
    text = _BOLD.sub(lambda m: m.group(2), text)
    text = _STRIKE.sub(lambda m: m.group(1), text)
    text = _ITALIC.sub(lambda m: m.group(2), text)
    return _PLACEHOLDER.sub(lambda m: chr(int(m.group(1), 16)), text)


_UNESC_PIPE = re.compile(r"(?<!\\)\|")


def _row_cells(line):
    # type: (str) -> list
    """Split a table row into raw cell strings on unescaped pipes (outer pipes dropped)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return _UNESC_PIPE.split(s)


def _norm_sep_cell(c):
    # type: (str) -> str
    """A separator cell (``----``, ``:--``, ``--:``, ``:-:``) -> minimal form, alignment kept."""
    c = c.strip()
    left = c.startswith(":")
    right = c.endswith(":")
    return ("%s---%s" % (":" if left else "", ":" if right else ""))


def collapse_table_padding(md):
    # type: (str) -> str
    """Remove docling's cosmetic per-cell ALIGNMENT padding from markdown tables, losslessly.

    docling pretty-prints tables by padding every cell with trailing spaces to its
    column's widest cell — so one 2,000-char cell inflates every row in that column.
    This rewrites each table row to single-space-padded cells (``| a | b |``) and each
    separator to ``| --- | --- |``. Cell TEXT is untouched (leading/trailing whitespace
    in a markdown cell is insignificant and trimmed by every renderer), so the table
    renders identically and the markdown stays valid — only the padding bytes go. Used
    on the CANONICAL text before chunking, so the per-doc text shrinks ~3x on
    table-heavy docs without dropping a single content token. Non-table lines pass
    through verbatim. Idempotent.
    """
    if not md:
        return md
    out = []
    for raw in md.split("\n"):
        s = raw.strip()
        if s.startswith("|") and s.count("|") >= 2:
            cells = _row_cells(raw)
            if _TABLE_SEP.match(raw):
                cells = [_norm_sep_cell(c) for c in cells]
            else:
                cells = [c.strip() for c in cells]
            out.append("| " + " | ".join(cells) + " |")
        else:
            out.append(raw)
    return "\n".join(out)


def markdown_to_text(md):
    # type: (str) -> str
    """Strip GFM markdown to plain prose suitable for literal grep matching.

    Drops headings/list/blockquote markers, horizontal rules, table separator
    rows, code fences and HTML comments; resolves links/images to their visible
    text; unwraps emphasis and inline code; and flattens table rows to
    space-separated cell text. Returns text with collapsed runs of blank lines.
    """
    if not md:
        return ""
    # NULs are never legitimate markdown; strip them up front so they cannot
    # collide with the internal \x00-escape placeholders below.
    md = md.replace("\x00", "")
    md = _COMMENT.sub(" ", md)
    out = []
    in_code = False
    for raw in md.split("\n"):
        if _FENCE.match(raw):
            in_code = not in_code
            continue  # drop the fence marker line itself
        if in_code:
            out.append(raw.rstrip())  # keep code content verbatim (tokens may be entities)
            continue
        line = raw
        if _HR.match(line) or _SETEXT.match(line) or _TABLE_SEP.match(line):
            out.append("")
            continue
        line = _BLOCKQUOTE.sub("", line)
        if _ATX.match(line):
            line = _ATX.sub("", line)
            line = _ATX_CLOSE.sub("", line)
        line = _LIST.sub("", line)
        if _TABLE_ROW.match(raw):
            line = _split_cells(line)
        line = _inline(line)
        line = _WS.sub(" ", line).strip()
        out.append(line)
    text = "\n".join(out)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()
