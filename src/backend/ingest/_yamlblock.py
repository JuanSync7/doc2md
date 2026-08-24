"""
title: YAML block codec — strict subset, render and parse (private)
layer: backend
public_api: no
summary: Render/parse the nested YAML subset used by document front matter and config/vocab.yaml, stdlib only.
"""
# 3.6-compatible. Stdlib only. Pure string policy — never touches disk.
#
# WHY A HAND-ROLLED CODEC. The office lane runs on a bare 3.6 host and the CI 3.6
# ring (python:3.6-slim) installs pytest and nothing else, so PyYAML is not
# available where this must import. The repo already hand-rolls a flat TOML subset
# in ``_config.py`` for the same reason; this is the same trade, one level richer
# because document metadata is genuinely nested (lists, lists-of-maps).
#
# STRICT means strict: anything outside the documented subset raises
# ``YamlSubsetError`` rather than being guessed at. A metadata codec that silently
# misreads is worse than one that refuses — the whole point of the vocabulary layer
# is that what you read back is what was written.
#
# THE SUBSET
#   render + parse : block maps, block sequences, sequences of maps, scalars
#   parse only     : comments, flow sequences ``[a, b]``, block scalars ``|`` / ``>``
#   rejected       : anchors/aliases (& *), tags (!), flow maps with content,
#                    multi-document streams, tab indentation, duplicate keys
#
# TWO INVARIANTS THE REST OF THE PIPELINE RELIES ON
#   1. No rendered line is ever exactly ``---``. Every string is double-quoted, so
#      a value of "---" renders as ``"---"``. Three separate front-matter strippers
#      locate the closing fence by scanning for a ``---`` line; a bare one anywhere
#      inside the block would truncate every document.
#   2. No rendered line carries a raw control character. ``validate_markdown``
#      raises a hard ``bad-chars`` ERROR for those even inside front matter, so they
#      are escaped here instead of being emitted and then failing the gate.
import re
from collections import OrderedDict

__all__ = ["YamlSubsetError", "render_block", "render_front_matter",
           "parse_block", "split_front_matter"]


class YamlSubsetError(ValueError):
    """Input is outside the supported YAML subset (never a silent misparse)."""


INDENT = "  "

# Keys are rendered raw, so they are validated rather than escaped: a key holding a
# colon, a newline or a leading dash would corrupt the block.
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")

# Control characters that must never reach the output (see invariant 2).
_ESCAPE_ALSO = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")


def _escape(s):
    # type: (str) -> str
    """Escape a string so it stays one valid double-quoted YAML line.

    Backslash and quote first (order matters), then the line-breaking whitespace,
    then any remaining control character as a ``\\xNN``/``\\uFFFD`` escape.

    Carriage return is escaped as ``\\r``, NOT folded into ``\\n``. The legacy
    renderer folded it, which silently rewrote the value; a codec that claims to
    round-trip must not edit the data it stores.
    """
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    def _sub(m):
        ch = m.group(0)
        if ch == u"\ufffd":
            return "\\uFFFD"
        return "\\x%02x" % ord(ch)

    return _ESCAPE_ALSO.sub(_sub, s)


def _scalar(val):
    # type: (object) -> str
    """One scalar, rendered.

    Strings are ALWAYS double-quoted. That is what makes the output safe to read
    with a YAML 1.1 parser too: unquoted ``no``/``yes``/``on``/``off`` and
    ``0644`` retype themselves under 1.1, and an enum value that changes type
    between readers is exactly the drift this layer exists to prevent.
    """
    if val is None:
        return "null"
    if val is True:
        return "true"
    if val is False:
        return "false"
    if isinstance(val, int):
        return "%d" % val
    if isinstance(val, float):
        # repr drops the dot for exponent form ('1e-05') and yields 'inf'/'nan',
        # none of which a YAML reader resolves back to a float — the value would
        # silently return as a STRING. A codec that promises round-trip must refuse
        # what it cannot round-trip rather than quietly change the type.
        text = repr(val)
        if not _FLOAT.match(text):
            raise YamlSubsetError(
                "float %r has no round-trippable YAML form (exponent or non-finite); "
                "store it as a string if the exact text is what matters" % val)
        return text
    return '"%s"' % _escape(val if isinstance(val, str) else "%s" % (val,))


def _is_map(v):
    # type: (object) -> bool
    return isinstance(v, dict)


def _is_seq(v):
    # type: (object) -> bool
    return isinstance(v, (list, tuple))


def _check_key(key):
    # type: (object) -> str
    k = key if isinstance(key, str) else "%s" % (key,)
    if not _SAFE_KEY.match(k):
        raise YamlSubsetError("unsafe mapping key %r (must match %s)"
                              % (k, _SAFE_KEY.pattern))
    return k


def _render_map(mapping, depth, out):
    # type: (dict, int, list) -> None
    pad = INDENT * depth
    for key, val in mapping.items():
        k = _check_key(key)
        if _is_map(val):
            if not val:
                out.append("%s%s: {}" % (pad, k))
            else:
                out.append("%s%s:" % (pad, k))
                _render_map(val, depth + 1, out)
        elif _is_seq(val):
            if not val:
                out.append("%s%s: []" % (pad, k))
            else:
                out.append("%s%s:" % (pad, k))
                _render_seq(val, depth + 1, out)
        else:
            out.append("%s%s: %s" % (pad, k, _scalar(val)))


def _render_seq(seq, depth, out):
    # type: (list, int, list) -> None
    pad = INDENT * depth
    for item in seq:
        if _is_map(item):
            if not item:
                out.append("%s- {}" % pad)
                continue
            # First pair rides the dash; the rest align under it.
            keys = list(item.keys())
            first = keys[0]
            k = _check_key(first)
            v = item[first]
            if _is_map(v) or _is_seq(v):
                # A nested collection cannot share the dash line, so the whole
                # item body is indented one level and the dash stands alone.
                out.append("%s-" % pad)
                _render_map(item, depth + 1, out)
                continue
            out.append("%s- %s: %s" % (pad, k, _scalar(v)))
            rest = OrderedDict()
            for key in keys[1:]:
                rest[key] = item[key]
            if rest:
                _render_map(rest, depth + 1, out)
        elif _is_seq(item):
            if not item:
                out.append("%s- []" % pad)
            else:
                out.append("%s-" % pad)
                _render_seq(item, depth + 1, out)
        else:
            out.append("%s- %s" % (pad, _scalar(item)))


def render_block(mapping):
    # type: (dict) -> str
    """Render a mapping as block YAML (no ``---`` fences, trailing newline).

    Returns ``""`` for an empty/None mapping so callers can prepend unconditionally.
    Iterates in the mapping's own order — pass an OrderedDict for a stable layout.
    """
    if not mapping:
        return ""
    out = []  # type: list
    _render_map(mapping, 0, out)
    return "\n".join(out) + "\n"


def render_front_matter(mapping):
    # type: (dict) -> str
    """``render_block`` inside a ``---`` fence, or ``""`` when there is nothing."""
    body = render_block(mapping)
    if not body:
        return ""
    return "---\n" + body + "---\n"


# ---------------------------------------------------------------- parsing

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*):(?:[ \t]+(.*))?$")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_UNSUPPORTED_LEAD = ("&", "*", "!", "?", "|", ">", "{", "[")


def _quote_mask(s):
    # type: (str) -> tuple
    """``(mask, unterminated)`` — which characters of ``s`` sit inside a quote.

    ``mask[i]`` is True when ``s[i]`` belongs to a quoted scalar (its delimiters
    included). One scanner serves ``_strip_comment``, ``_flow_depth`` and
    ``_split_flow`` so they can no longer disagree with each other.

    A ``'`` or ``"`` opens a quoted scalar ONLY at the START OF A TOKEN — the
    start of the line, or just after ``[``, ``,``, a block-sequence ``- ``, or a
    ``key: `` — which is precisely the rule ``_scalar_value`` already applies
    (it checks ``s[0]``). Anywhere else a quote is an ordinary character, so the
    apostrophe in ``title: The Operator's Guide  # from OCR`` no longer opens a
    phantom quote that swallows the comment, hides a closing ``]``, or merges two
    flow items. The scanners used to say "a quote ANYWHERE opens", disagreeing
    with ``_scalar_value`` about the very same bytes.

    Inside a double quote ``\\x`` escapes the next character; inside a single
    quote ``''`` is an escaped apostrophe (YAML's own rule), so
    ``note: 'it''s # fine'`` keeps its ``#``.
    """
    mask = [False] * len(s)
    quote = ""
    start = True   # the next non-blank character begins a token
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if quote:
            mask[i] = True
            if ch == "\\" and quote == '"' and i + 1 < n:
                mask[i + 1] = True
                i += 2
                continue
            if ch == "'" and quote == "'" and i + 1 < n and s[i + 1] == "'":
                mask[i + 1] = True
                i += 2
                continue
            if ch == quote:
                quote = ""
                start = False
            i += 1
            continue
        if ch in (" ", "\t"):
            i += 1
            continue
        if start and ch in ("'", '"'):
            quote = ch
            mask[i] = True
        elif ch in ("[", ","):
            start = True
        elif ch == ":" and (i + 1 >= n or s[i + 1] in " \t"):
            start = True
        elif not (ch == "-" and start and (i + 1 >= n or s[i + 1] in " \t")):
            # A leading `- ` opens a block-sequence item, so the item's own first
            # token starts after it; every other character ends a token start.
            start = False
        i += 1
    return (mask, bool(quote))


def _strip_comment(s):
    # type: (str) -> str
    """Drop a trailing ``#`` comment that is not inside quotes."""
    mask = _quote_mask(s)[0]
    for i, ch in enumerate(s):
        if ch == "#" and not mask[i] and (i == 0 or s[i - 1] in " \t"):
            return s[:i]
    return s


def _unescape_double(s):
    # type: (str) -> str
    out = []  # type: list
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise YamlSubsetError("dangling backslash in %r" % s)
        nxt = s[i + 1]
        simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
                  "/": "/", "0": "\0"}
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt in ("x", "u", "U"):
            width = {"x": 2, "u": 4, "U": 8}[nxt]
            digits = s[i + 2:i + 2 + width]
            # A bare ValueError here would escape every `except YamlSubsetError`
            # in the repo (YamlSubsetError SUBCLASSES ValueError, not the reverse),
            # aborting a whole corpus scan on one hand-edited document. A Windows
            # path typed into a title — "C:\Users\file" — is enough to trigger it.
            if len(digits) < width:
                raise YamlSubsetError("truncated \\%s escape in %r" % (nxt, s))
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                raise YamlSubsetError("bad \\%s escape %r in %r" % (nxt, digits, s))
            i += 2 + width
        else:
            raise YamlSubsetError("unsupported escape \\%s" % nxt)
    return "".join(out)


def _scalar_value(raw):
    # type: (str) -> object
    """One scalar token -> a Python value.

    Plain scalars are typed conservatively and on YAML 1.2 rules: only
    ``true``/``false`` are booleans. ``yes``/``no``/``on``/``off`` stay STRINGS
    here, because retyping them is the bug this codec exists to avoid — but note
    a YAML 1.1 reader WOULD retype them, which is why the renderer quotes
    everything and why the linter flags a bool sitting in an enum field.
    """
    s = raw.strip()
    if not s:
        return None
    if s[0] == '"':
        if len(s) < 2 or not s.endswith('"'):
            raise YamlSubsetError("unterminated double-quoted scalar: %r" % raw)
        return _unescape_double(s[1:-1])
    if s[0] == "'":
        if len(s) < 2 or not s.endswith("'"):
            raise YamlSubsetError("unterminated single-quoted scalar: %r" % raw)
        return s[1:-1].replace("''", "'")
    if s[0] in _UNSUPPORTED_LEAD:
        raise YamlSubsetError("unsupported YAML construct: %r" % raw)
    if s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if _INT.match(s):
        return int(s)
    if _FLOAT.match(s):
        return float(s)
    return s


def _flow_depth(s):
    # type: (str) -> int
    """Net ``[`` minus ``]`` outside quotes — how many brackets are still open."""
    mask = _quote_mask(s)[0]
    depth = 0
    for i, ch in enumerate(s):
        if mask[i]:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth


def _gather_flow(lines, first, no):
    # type: (_Lines, str, int) -> str
    """A flow sequence, joined across continuation lines until its bracket closes.

    An authored enum list wraps: ``values: [a, b,`` / ``          c, d]``. Refusing
    that would push the vocabulary file into a shape nobody wants to hand-edit.
    """
    buf = first
    while _flow_depth(buf) > 0:
        row = lines.take()
        if row is None:
            raise YamlSubsetError("unterminated flow sequence opened at line %d" % no)
        buf = buf + " " + row[2].strip()
    if not buf.rstrip().endswith("]"):
        raise YamlSubsetError("trailing content after flow sequence at line %d" % no)
    return buf.rstrip()


def _split_flow(body):
    # type: (str) -> list
    """Split a flow sequence body on top-level commas, respecting quotes."""
    mask, unterminated = _quote_mask(body)
    items = []  # type: list
    start = 0
    for i, ch in enumerate(body):
        if mask[i]:
            continue
        if ch in "[]{}":
            raise YamlSubsetError("nested flow collections are not supported: %r"
                                  % body)
        if ch == ":" and (i + 1 == len(body) or body[i + 1] in " \t,"):
            # ``[a: b]`` is an implicit flow MAPPING — the brace-less spelling of
            # the ``{a: b}`` this subset already refuses two lines above. Reading
            # it as the plain string "a: b" is the one outcome the module forbids:
            # PyYAML returns {'a': 'b'} and nothing downstream would ever notice
            # the disagreement. A colon NOT followed by space stays an ordinary
            # character, so a bare URL — `prov: http://www.w3.org/ns/prov#` is
            # authored that way in config/vocab.yaml — is still a plain scalar.
            raise YamlSubsetError("flow mappings are not supported: %r" % body)
        if ch == ",":
            items.append(body[start:i])
            start = i + 1
    if unterminated:
        raise YamlSubsetError("unterminated quote in flow sequence: %r" % body)
    items.append(body[start:])
    return [_scalar_value(x) for x in items if x.strip() != ""]


class _Lines(object):
    """Cursor over significant lines, carrying (indent, text) and a raw view.

    The document-marker and tab-indentation checks run over EVERY raw line up
    front, before any structure is known — so a ``---`` inside a block scalar is
    rejected too. That is deliberate, not an oversight: three front-matter
    strippers elsewhere scan raw lines for ``\n---\n`` with no idea of YAML
    context, so a fence smuggled inside a folded rationale would truncate every
    document that carried it. The cost is that block-scalar prose cannot quote a
    YAML fence or a markdown horizontal rule.
    """

    def __init__(self, text):
        # type: (str) -> None
        self.rows = []  # type: list
        for no, raw in enumerate((text or "").split("\n"), start=1):
            if "\t" in raw[:len(raw) - len(raw.lstrip())]:
                raise YamlSubsetError("tab indentation at line %d" % no)
            stripped = raw.strip()
            if stripped in ("---", "..."):
                raise YamlSubsetError("document marker %r at line %d (multi-document "
                                      "streams are not supported)" % (stripped, no))
            self.rows.append((no, len(raw) - len(raw.lstrip(" ")), raw))
        self.i = 0

    def peek(self):
        # type: () -> tuple
        """Next significant row as (lineno, indent, text), or None at the end."""
        while self.i < len(self.rows):
            no, indent, raw = self.rows[self.i]
            body = _strip_comment(raw).rstrip()
            if not body.strip():
                self.i += 1
                continue
            return (no, indent, body[indent:])
        return None

    def take(self):
        # type: () -> tuple
        row = self.peek()
        self.i += 1
        return row

    def raw_from(self, indent):
        # type: (int) -> list
        """Consume raw lines for a block scalar: everything indented deeper.

        Returns ``(lineno, text)`` pairs, not bare text: ``_block_scalar`` has to
        be able to NAME the line when a continuation line is less indented than
        the block's own first line, and by then the cursor has already moved past
        it.
        """
        out = []  # type: list
        while self.i < len(self.rows):
            no, ind, raw = self.rows[self.i]
            if raw.strip() and ind < indent:
                break
            out.append((no, raw[indent:] if len(raw) >= indent else ""))
            self.i += 1
        while out and not out[-1][1].strip():
            out.pop()
        return out


def _block_scalar(lines, style, indent):
    # type: (_Lines, str, int) -> str
    """A ``|`` or ``>`` block scalar; ``-`` chomping strips the trailing newline.

    Only ``|``, ``>``, ``|-`` and ``>-`` are accepted. An explicit indentation
    indicator (``|2``) or keep-chomping (``|+``) is REFUSED rather than ignored:
    ignoring them changes the text that gets stored, and a codec that silently
    misreads is worse than one that refuses.
    """
    if style not in ("|", ">", "|-", ">-"):
        raise YamlSubsetError(
            "unsupported block-scalar header %r (only |, >, |- and >- are "
            "supported; an indentation or keep-chomping indicator changes the "
            "text and is refused rather than ignored)" % style)
    keep = not style.endswith("-")
    body = lines.raw_from(indent + 1)
    # Re-align: the block's own indentation is the first non-empty line's.
    base = 0
    for _no, row in body:
        if row.strip():
            base = len(row) - len(row.lstrip(" "))
            break
    # A non-blank continuation line LESS indented than that first line is outside
    # the subset, and slicing it blind is exactly the silent misread this module
    # exists to prevent: it shaves the first characters off the line, and a line
    # shorter than `base` disappears entirely — which inside a folded (>) block
    # becomes a manufactured paragraph break that splits the author's sentence in
    # two. PyYAML raises a ParserError on the same input; so do we, naming the
    # line, because a one-space indentation slip in a hand-maintained file is an
    # ordinary typo and the reader has no other way to learn about it.
    # Blank lines are exempt: they are legitimately less indented (or empty).
    for no, row in body:
        if row.strip() and len(row) - len(row.lstrip(" ")) < base:
            raise YamlSubsetError(
                "line %d is less indented than the block scalar's first line "
                "(%d spaces where at least %d are required): %r"
                % (no, len(row) - len(row.lstrip(" ")), base, row.strip()[:60]))
    rows = [row[base:] for _no, row in body]
    if style[0] == "|":
        text = "\n".join(rows)
    else:
        # Folded: blank lines become newlines, everything else joins with a space.
        # YAML's "more-indented lines keep their breaks" rule is NOT implemented;
        # folding such a line would silently reflow an author's sub-point or code
        # example into the paragraph, so it is refused instead.
        parts = []  # type: list
        buf = []  # type: list
        for r in rows:
            if r.strip():
                if r[:1] in (" ", "\t"):
                    raise YamlSubsetError(
                        "more-indented line inside a folded (>) block scalar is not "
                        "supported (it would be reflowed into the paragraph); use | "
                        "to keep the line breaks: %r" % r.strip()[:60])
                buf.append(r.strip())
            else:
                parts.append(" ".join(buf))
                buf = []
        parts.append(" ".join(buf))
        text = "\n".join(p for p in parts)
    return text + "\n" if keep else text


def _parse_map(lines, indent):
    # type: (_Lines, int) -> OrderedDict
    out = OrderedDict()
    while True:
        row = lines.peek()
        if row is None:
            return out
        no, ind, text = row
        if ind < indent:
            return out
        if ind > indent:
            raise YamlSubsetError("unexpected indent at line %d: %r" % (no, text))
        if text.startswith("-"):
            raise YamlSubsetError("sequence item where a mapping key was expected "
                                  "at line %d" % no)
        m = _KEY_LINE.match(text)
        if not m:
            raise YamlSubsetError("not a mapping entry at line %d: %r" % (no, text))
        lines.take()
        key, rest = m.group(1), (m.group(2) or "").strip()
        if key in out:
            raise YamlSubsetError("duplicate key %r at line %d" % (key, no))
        if rest and rest[0] in ("|", ">"):
            out[key] = _block_scalar(lines, rest, ind)
        elif rest == "[]":
            out[key] = []
        elif rest == "{}":
            out[key] = OrderedDict()
        elif rest.startswith("["):
            flow = _gather_flow(lines, rest, no)
            out[key] = _split_flow(flow[1:-1])
        elif rest.startswith("{"):
            raise YamlSubsetError("flow mappings are not supported at line %d" % no)
        elif rest:
            out[key] = _scalar_value(rest)
        else:
            out[key] = _parse_child(lines, ind)
    return out


def _parse_child(lines, parent_indent):
    # type: (_Lines, int) -> object
    """The value of a ``key:`` with nothing after it: a nested block, or None."""
    row = lines.peek()
    if row is None:
        return None
    no, ind, text = row
    if ind <= parent_indent:
        return None
    if text.startswith("-"):
        return _parse_seq(lines, ind)
    return _parse_map(lines, ind)


def _parse_seq(lines, indent):
    # type: (_Lines, int) -> list
    out = []  # type: list
    while True:
        row = lines.peek()
        if row is None:
            return out
        no, ind, text = row
        if ind < indent:
            return out
        if ind > indent:
            raise YamlSubsetError("unexpected indent at line %d: %r" % (no, text))
        if not text.startswith("-"):
            return out
        lines.take()
        rest = text[1:].strip()
        if not rest:
            # Dash alone: the item body is the deeper block that follows.
            child = _parse_child(lines, ind)
            out.append(child if child is not None else None)
            continue
        if rest == "[]":
            out.append([])
            continue
        if rest == "{}":
            out.append(OrderedDict())
            continue
        if rest[0] == "{":
            raise YamlSubsetError("flow mappings are not supported at line %d" % no)
        if rest[0] in ("|", ">"):
            # ``- >`` — a folded/literal item, so a rule or rationale in a list can
            # wrap instead of running off the right margin.
            out.append(_block_scalar(lines, rest, ind))
            continue
        m = _KEY_LINE.match(rest)
        if m:
            # ``- key: value`` — a map item whose remaining pairs align under the key.
            item = OrderedDict()
            key, val = m.group(1), (m.group(2) or "").strip()
            if val and val[0] in ("|", ">"):
                item[key] = _block_scalar(lines, val, ind)
            elif val == "[]":
                item[key] = []
            elif val == "{}":
                item[key] = OrderedDict()
            elif val.startswith("{"):
                raise YamlSubsetError(
                    "flow mappings are not supported at line %d" % no)
            elif val.startswith("["):
                flow = _gather_flow(lines, val, no)
                item[key] = _split_flow(flow[1:-1])
            elif val:
                item[key] = _scalar_value(val)
            else:
                item[key] = _parse_child(lines, ind)
            nxt = lines.peek()
            if nxt is not None and nxt[1] > ind:
                rest_map = _parse_map(lines, nxt[1])
                for k, v in rest_map.items():
                    if k in item:
                        raise YamlSubsetError("duplicate key %r at line %d" % (k, no))
                    item[k] = v
            out.append(item)
            continue
        out.append(_scalar_value(rest))
    return out


def parse_block(text):
    # type: (str) -> OrderedDict
    """Parse block YAML (no ``---`` fences) into an OrderedDict.

    Raises ``YamlSubsetError`` for anything outside the supported subset — this
    codec never guesses.
    """
    lines = _Lines(text or "")
    row = lines.peek()
    if row is None:
        return OrderedDict()
    result = _parse_map(lines, row[1])
    trailing = lines.peek()
    if trailing is not None:
        raise YamlSubsetError("trailing content at line %d: %r"
                              % (trailing[0], trailing[2]))
    return result


_FENCE = re.compile(r"^---\n((?:.*\n)*?)---\n")


def split_front_matter(md):
    # type: (str) -> tuple
    """``(meta, body)`` for a markdown document.

    ``meta`` is an OrderedDict (empty when there is no front matter). ``body`` is
    the markdown BODY — the exact bytes ``markdown_sha256`` covers — so the single
    separator newline the assembler inserts between the fence and the body is
    removed here too.
    """
    text = md or ""
    m = _FENCE.match(text)
    if not m:
        return (OrderedDict(), text)
    body = text[m.end():]
    if body.startswith("\n"):
        body = body[1:]
    return (parse_block(m.group(1)), body)
