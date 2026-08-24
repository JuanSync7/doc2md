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
# The ONE inline HTML tag this project emits: _docx_cell_text joins a multi-paragraph
# table cell with <br>, because a GFM cell cannot hold a newline. Left alone it
# reaches the text layer as the literal token "br" — harmless to the recall gate,
# which is recall and forgives extra target tokens, but it lands in the body an
# embedder and a BM25 index actually read. The (?<!\\) exempts a converter-escaped
# "\<br>", which is prose ABOUT the tag rather than the tag.
_BR = re.compile(r"(?<!\\)<br\s*/?>", re.I)
# The run and its tail, because a CLOSER is not just "a fence-looking line": it
# repeats the opener's character with a run at least as long and carries nothing
# after it but spaces (CommonMark 4.5). Toggling on either character meant a `~~~`
# inside a ``` listing ended it, after which the rest of that listing was processed
# as PROSE — inline markup stripped out of text a renderer shows verbatim.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
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
# EMPHASIS IS ASYMMETRIC IN COMMONMARK, and this stripper has to be asymmetric with
# it. The intraword ban belongs to `_` ALONE: `DB_MAX_CONN_LIMIT` and
# `snake_case_helper` are one identifier each and must reach the KB and the BM25
# index unfused (docs/quality-plan.md P0.3). `*` has no such rule — `*n*th`,
# `two *Foo*s`, `re*start*` and even `2*3*4` all render as emphasis in marko and
# markdown-it — so applying the `_` guard to `*` left the markers in the text layer
# and reported a correct conversion as a 0.667-recall failure. `(?=\S)`/`(?<=\S)`
# are what keeps `2 * 3 * 4` literal, and `(?<!\*)`/`(?!\*)` keep `**bold**` for
# `_BOLD`, which must run first.
_ITALIC_STAR = re.compile(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)", re.S)
_ITALIC_US = re.compile(r"(?<![\w*_])_(?=\S)(.+?)(?<=\S)_(?![\w*_])", re.S)
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


_CODE_SLOT = re.compile("\x01([0-9]+)\x01")


def _inline(text):
    # type: (str) -> str
    """Resolve inline markdown to its visible text.

    ORDER IS LOAD-BEARING, and it follows CommonMark's precedence rather than
    convenience. A code span binds TIGHTER than a link or an emphasis run:
    ``\u0060[d](e)\u0060`` renders the literal characters ``[d](e)``, it is not a link
    to ``e``. So code-span contents are lifted out and protected FIRST, exactly as
    backslash-escaped punctuation is, and put back at the end.

    Stripping links first (as this did) silently deleted the target of any link
    pattern that happened to sit inside a code span — which is how a perfectly
    faithful ``\u0060kubectl apply -f [env](prod).yaml\u0060`` lost a token and failed the
    recall gate. The converter was right and the stripper was wrong."""
    text = _ESCAPED_PUNCT.sub(lambda m: "\x00%02x" % ord(m.group(1)), text)
    stash = []  # type: list

    def _protect(m):
        stash.append(m.group(1))
        return "\x01%d\x01" % (len(stash) - 1)

    text = _INLINE_CODE.sub(_protect, text)
    text = _IMG.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _REF_LINK.sub(lambda m: m.group(1), text)
    text = _AUTOLINK.sub(lambda m: m.group(1), text)
    text = _BOLD.sub(lambda m: m.group(2), text)
    text = _STRIKE.sub(lambda m: m.group(1), text)
    text = _ITALIC_STAR.sub(lambda m: m.group(1), text)
    text = _ITALIC_US.sub(lambda m: m.group(1), text)
    text = _CODE_SLOT.sub(lambda m: stash[int(m.group(1))], text)
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
    # C0 controls are never legitimate markdown; strip them up front so they cannot
    # collide with the internal \x00 / \x01 placeholders below.
    md = md.replace("\x00", "").replace("\x01", "")
    md = _COMMENT.sub(" ", md)
    md = _BR.sub(" ", md)
    out = []
    in_code = None      # (char, length) of the open fence, or None outside one
    para_open = False       # is there a paragraph a setext underline could attach to?
    for raw in md.split("\n"):
        fence = _FENCE.match(raw)
        if in_code is not None:
            if (fence and fence.group(1)[0] == in_code[0]
                    and len(fence.group(1)) >= in_code[1]
                    and not fence.group(2).strip()):
                in_code = None
                para_open = False
                continue  # drop the closing marker line itself
            out.append(raw.rstrip())  # keep code content verbatim (tokens may be entities)
            continue
        if fence:
            in_code = (fence.group(1)[0], len(fence.group(1)))
            para_open = False
            continue  # drop the opening marker line itself
        line = raw
        # WHAT A RENDERER REALLY DELETES. A thematic break, a setext UNDERLINE and a
        # GFM delimiter row draw furniture and contribute no text, so dropping them
        # keeps this shadow faithful. The two guards are what stop it dropping text
        # that a renderer DOES show:
        #   * `===` (or `--`) with no paragraph above it is not an underline, it is
        #     an ordinary paragraph — deleting it silently removed real characters
        #     from the text layer the KB and the BM25 index read, at recall 1.0,
        #     because a marker run carries no ASCII token to miss. A lone `-` is the
        #     one exception: that is an empty bullet, and it really does render blank.
        #   * a delimiter row needs a PIPE. `_TABLE_SEP` is deliberately loose (it
        #     also serves collapse_table_padding, which only ever shows it rows it
        #     already knows are table rows); without the pipe it swallows any short
        #     hyphen run standing on its own.
        setext = _SETEXT.match(line)
        if (_HR.match(line)
                or (setext and (para_open or line.strip() == "-"))
                or ("|" in line and _TABLE_SEP.match(line))):
            out.append("")
            para_open = False
            continue
        line = _BLOCKQUOTE.sub("", line)
        is_heading = bool(_ATX.match(line))
        if is_heading:
            line = _ATX.sub("", line)
            line = _ATX_CLOSE.sub("", line)
        line = _LIST.sub("", line)
        is_row = bool(_TABLE_ROW.match(raw))
        if is_row:
            line = _split_cells(line)
        line = _inline(line)
        line = _WS.sub(" ", line).strip()
        out.append(line)
        # An ATX heading and a table row are closed blocks: a `===` under either is a
        # paragraph, not an underline.
        para_open = bool(line) and not is_heading and not is_row
    text = "\n".join(out)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()
