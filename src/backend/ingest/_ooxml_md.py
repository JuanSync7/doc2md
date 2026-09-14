"""
title: OOXML -> markdown deterministic converters
layer: backend
public_api: yes
summary: Full docx/pptx/xlsx to markdown by walking the OOXML parts; no ML, no inference.
"""
# 3.6-compatible. Stdlib only. Pure policy — operates on a dict of OOXML *parts*
# ({part_name: xml_string}); the zip reading lives in scripts/office_convert.py.
#
# Why this exists: office files are ZIP+XML where every paragraph, heading style,
# table row/cell and spreadsheet value is EXPLICITLY tagged. Conversion is therefore
# deterministic file reading — unlike PDF (positioned glyphs) there is nothing to
# infer, so ~100% token fidelity is achievable by construction. Each converter has a
# sibling *_source_text ground truth that walks the same parts EXHAUSTIVELY (every
# text run, regardless of structure), so a traversal bug in the converter shows up
# as recall < 1.0 in conversion_report — the converter can't grade its own homework.
#
# Shared, documented content policy (applies to converter AND ground truth alike):
#   * mc:Fallback subtrees are skipped everywhere — they DUPLICATE mc:Choice for
#     legacy readers; keeping both would double text.
#   * page furniture is excluded: docx header/footer parts, pptx slide-number/date/
#     footer placeholders and layout/master parts, xlsx print headers. This matches
#     the corpus goal ("structure minus the redundancy text").
#   * tracked-change deletions (w:delText) and field instructions (w:instrText)
#     are source metadata, not visible text — excluded on both sides.
import re

from ._figures import ooxml_image_sentinel
# The leaf primitives — see `_ooxml_leaf`'s header for which helpers are allowed to
# be shared between readers of the same bytes and why these four qualify.
from ._ooxml_leaf import (_local, _root, _WS,               # noqa: F401
                          _BREAK_LOCALS, _SKIP_LOCALS)
# The deck and workbook TOKEN ground truths. They are imported rather than defined
# here precisely so they cannot reach this module's helpers: a ground truth that
# shares the converter's reading of a document cannot disagree with it, and
# disagreeing is the only thing it is for. This import is why `_ooxml_leaf` exists —
# `_SOURCES` below binds at module scope, so those modules must not import back.
from ._ooxml_struct import docx_source_text
from ._pptx_struct import pptx_source_text
from ._xlsx_struct import xlsx_source_text


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

__all__ = ["docx_markdown", "pptx_markdown", "xlsx_markdown", "pptx_slide_order",
           "docx_source_text", "pptx_source_text", "xlsx_source_text",
           "ooxml_markdown", "ooxml_source_text", "svg_text", "OOXML_MAIN_PARTS"]

# The text-bearing parts each converter consumes, as regexes on part names.
# office_convert.py reads these; its --audit-parts mode uses the same list to
# report any OTHER part that still contains text runs (nothing silently dropped).
OOXML_MAIN_PARTS = {
    "docx": (r"^word/document\.xml$", r"^word/styles\.xml$", r"^word/numbering\.xml$",
             r"^word/footnotes\.xml$", r"^word/endnotes\.xml$", r"^word/comments\.xml$",
             r"^word/_rels/document\.xml\.rels$", r"^word/charts/chart(?:Ex)?\d+\.xml$",
             r"^word/diagrams/data\d+\.xml$", r"^word/media/[^/]+\.svg$",
             r"^docProps/(core|app)\.xml$"),
    # `ppt/presentation.xml` and its rels are read but never RENDERED: they carry no
    # text at all. They are here because the deck's ORDER lives in `p:sldIdLst`, and
    # while the part was absent from this list the converter could not read it, so
    # `## Slide N` numbered by filename — the order the slides were created in.
    "pptx": (r"^ppt/presentation\.xml$", r"^ppt/_rels/presentation\.xml\.rels$",
             r"^ppt/slides/slide\d+\.xml$", r"^ppt/slides/_rels/slide\d+\.xml\.rels$",
             r"^ppt/notesSlides/notesSlide\d+\.xml$", r"^ppt/diagrams/data\d+\.xml$",
             r"^ppt/charts/chart(?:Ex)?\d+\.xml$", r"^ppt/comments/[^/]+\.xml$",
             r"^ppt/media/[^/]+\.svg$", r"^docProps/(core|app)\.xml$"),
    # `xl/styles.xml` is read but never RENDERED: the converter emits no cell
    # emphasis and no number formatting. It is loaded so the drop can be COUNTED —
    # a workbook's bold header, its struck-through cancelled row and its date
    # serials are real losses, and while the part was absent from this list the two
    # warnings that name them could not fire at all, which is a stronger kind of
    # silence than merely not emitting the formatting. `word/styles.xml` was here
    # from the start; the workbook lane simply never gained it.
    "xlsx": (r"^xl/workbook\.xml$", r"^xl/_rels/workbook\.xml\.rels$",
             r"^xl/styles\.xml$",
             r"^xl/sharedStrings\.xml$", r"^xl/worksheets/[^/]+\.xml$",
             r"^xl/drawings/drawing\d+\.xml$", r"^xl/drawings/_rels/drawing\d+\.xml\.rels$",
             r"^xl/comments\d*\.xml$",
             r"^xl/charts/chart(?:Ex)?\d+\.xml$", r"^xl/media/[^/]+\.svg$",
             r"^docProps/(core|app)\.xml$"),
}

# Embedded SVG images (vector). Their <text> labels are real document text — unlike a
# raster screenshot, they are extractable deterministically (no VLM), so the OOXML lane
# captures them and the gate holds them to recall 1.0. Raster/metafile image text is a
# separate opt-in VLM pass (see docs/design/ooxml-lane.md).
_MEDIA_SVG = re.compile(r"^(word|ppt|xl)/media/[^/]+\.svg$")


def _collect_text(el, parts, skip=None, value_locals=("t",)):
    # type: (object, list, object, tuple) -> None
    """Exhaustive VERBATIM text collection under ``el`` into ``parts``.

    Adjacent text runs concatenate with NO inserted space (Word/PowerPoint split
    single words across runs at format boundaries); tab/br/cr and paragraph
    boundaries contribute one space. ``skip`` is an optional callable(el) -> bool
    pruning whole subtrees (chrome shapes)."""
    loc = _local(el.tag)
    if loc in _SKIP_LOCALS or (skip is not None and skip(el)):
        return
    if loc == "p" and parts:
        parts.append(" ")
    if loc in value_locals:
        if loc == "v":
            # A discrete value (chart c:v): never run-split, so pad with spaces
            # to keep adjacent cached values/titles from gluing together.
            if el.text:
                parts.append(" %s " % el.text)
            return
        # A text run/container: dgm:t (SmartArt) and legacy p:text (comments)
        # can HOLD runs as children, so append direct text and keep walking.
        if el.text:
            parts.append(el.text)
    if loc in _BREAK_LOCALS:
        parts.append(" ")
    for ch in el:
        _collect_text(ch, parts, skip, value_locals)


def _text_of(el, skip=None, value_locals=("t",)):
    # type: (object, object, tuple) -> str
    parts = []  # type: list
    _collect_text(el, parts, skip, value_locals)
    return _WS.sub(" ", "".join(parts)).strip()


def _find_locals(el, want, out, stop=()):
    # type: (object, tuple, list, tuple) -> None
    """Collect descendant elements whose local name is in ``want``, in document
    order, WITHOUT descending into found elements, ``stop`` locals, or skipped
    subtrees (Fallback). The traversal backbone for tables/rows/cells/paragraphs —
    wrapper elements (w:sdt content controls, smartTags, AlternateContent Choice)
    are transparent, so wrapped rows/cells/paragraphs are never missed."""
    for ch in el:
        loc = _local(ch.tag)
        if loc in _SKIP_LOCALS or loc in stop:
            continue
        if loc in want:
            out.append(ch)
        else:
            _find_locals(ch, want, out, stop)


# --- embedded raster/metafile image extraction (opt-in body sentinels) -------
# A picture references its bytes by relationship id: DrawingML ``<a:blip r:embed>``
# (docx drawings, pptx/xlsx pics) or legacy VML ``<v:imagedata r:id>``. We resolve the
# rId -> package media part via the OWNING part's .rels and emit a positional
# ``<!-- ooxml-image:PART -->`` sentinel where the picture sits in reading order.
# The sentinel is an HTML COMMENT, so the recall gate (which reads markdown_to_text,
# comment-stripped) can NEVER move -- image support is losslessness-safe by construction.
# Only BODY parts are walked here (headers/footers/masters are not), so page-chrome images
# never get a sentinel: placement furniture-gating for free. SVG is intentionally excluded
# -- its <text> labels are already extracted as real content by svg_text(); only raster/
# metafile pixels (which carry no extractable text) need a sentinel + a later caption.
_MEDIA_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|bmp|tiff?|emf|wmf|webp)$", re.I)


def _norm_part(base_dir, target):
    # type: (str, str) -> str
    """Resolve a rels Target against its owner part's directory into a package path:
    ``../media/x.png`` with owner-dir ``ppt/slides`` -> ``ppt/media/x.png``. Absolute
    targets (leading ``/``) are package-root-relative."""
    t = (target or "").replace("\\", "/").strip()
    if not t:
        return ""
    if t.startswith("/"):
        return t.lstrip("/")
    out = []  # type: list
    for seg in (base_dir.split("/") if base_dir else []) + t.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
        else:
            out.append(seg)
    return "/".join(out)


def _image_rels(rels_xml, owner):
    # type: (str, str) -> dict
    """``{rId: media_part}`` for the INTERNAL image relationships declared by ``owner``'s
    .rels part (external and non-image relationships ignored), each Target resolved to a
    package path. SVG targets are dropped -- they are handled as text, not pixels."""
    root = _root(rels_xml)
    out = {}  # type: dict
    if root is None:
        return out
    base_dir = owner.rsplit("/", 1)[0] if "/" in owner else ""
    for rel in root:
        if _local(rel.tag) != "Relationship":
            continue
        if rel.attrib.get("TargetMode", "") == "External":
            continue
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        typ = rel.attrib.get("Type", "")
        if not (rid and target):
            continue
        if not (typ.endswith("/image") or _MEDIA_IMG_EXT.search(target)):
            continue
        part = _norm_part(base_dir, target)
        if part and not part.lower().endswith(".svg"):
            out[rid] = part
    return out


def _blip_rids(el):
    # type: (object) -> list
    """Embedded image rIds under a drawing/pic subtree, in document order: DrawingML
    ``<a:blip r:embed|r:link>`` and legacy VML ``<v:imagedata r:id>``. Skips Fallback
    (the VML duplicate of a Choice drawing) so one picture is counted exactly once."""
    rids = []  # type: list

    def walk(e):
        for ch in e:
            loc = _local(ch.tag)
            if loc in _SKIP_LOCALS:
                continue
            if loc == "blip":
                rid = _attr(ch, "embed") or _attr(ch, "link")
                if rid:
                    rids.append(rid)
            elif loc == "imagedata":
                rid = _attr(ch, "id")
                if rid:
                    rids.append(rid)
            else:
                walk(ch)
    walk(el)
    return rids


def _emit_image_blocks(rids, rels, blocks, pad=""):
    # type: (list, dict, list, str) -> None
    """Append an ``("img", sentinel)`` block for each rId that resolves to a media part.

    ``pad`` is the content column of the list item the picture falls inside, and it
    is load-bearing rather than cosmetic. ``_join_blocks`` separates any non-``li``
    block with a blank line, so a sentinel at COLUMN 0 closes the list it interrupts
    and every item after it restarts at depth 0 — the source's nesting silently gone
    at ``recall: 1.0``. Indented to the open item's content column it is a
    continuation of that item instead, and the items below keep their depth. The
    blank lines stay: they close the item's PARAGRAPH without closing the ITEM,
    which is what lets an ordered sub-list whose marker is not `1` survive (a list
    may interrupt a paragraph only at `1`, so gluing the picture on with a single
    newline would swallow the next step into its prose)."""
    for rid in rids:
        part = rels.get(rid)
        if part:
            blocks.append(("img", pad + ooxml_image_sentinel(part)))


# Markdown metacharacters that would REINTERPRET literal source text when rendered:
# `__path__` turns bold (eating the underscores), `<prdata[31:0]>` parses as an HTML
# tag, `[x](y)` as a link, `~~x~~` as strikethrough. Silicon docs are full of these,
# so every literal text is escaped — the source must round-trip through a GFM
# renderer character-perfect.
_MD_SPECIAL = re.compile(r"([*`<\[\]~])|(_+)")
# CommonMark caps an ordered marker at NINE digits, which is also what
# `_mdstructure._ORDERED` reads. Without the cap a ten-digit part number or
# order reference (`1234567890. `) was escaped for a danger it cannot pose,
# and a backslash a renderer hides is still a byte an index and a grep see.
# The `|$` is not a nicety. A bullet whose whole text is `15.` emits `- 15.`,
# which CommonMark reads as an EMPTY nested ordered list — the characters are
# deleted from the render, and because `markdown_to_text` still reports them the
# token gate sees `recall: 1.0`. Measured on a three-bullet deck, marko 2.2.3:
#     - alpha / - 15. / - beta   renders   "alpha beta"   recall 1.0 valid True
# The same holds for `10)`, `1.` and `1)`.
_LEAD_LIST_NUM = re.compile(r"^(\d{1,9})([.)])(\s|$)")
# LINE-LEADING constructs a body paragraph must never be mistaken for.
#   * an ATX heading opens on ONE TO SIX hashes followed by whitespace or nothing
#     at all; seven hashes is not a heading and neither is `#1 priority`, so
#     escaping either would add a visible backslash to text never at risk.
#   * `+ `/`- `/`* ` is a bullet marker (a lone `*` is already escaped by `_esc`).
#   * a bare `+` is a bullet with an empty item and vanishes exactly as `15.`
#     does; `-` alone is caught by `_LEAD_RULE` and `*` alone by `_esc`, so `+`
#     was the one marker nothing covered.
_LEAD_MARK = re.compile(r"^(?:#{1,6}(?:\s|$)|[+*-](?:\s|$))")
# A line made ONLY of dashes or ONLY of equals signs (spaces allowed between) is a
# thematic break or a setext heading underline: it renders as a rule, or as nothing,
# and either way the paragraph's own characters are gone. `_` and `*` rules cannot
# arise — `_esc` already escapes both.
_LEAD_RULE = re.compile(r"^(?:-[- \t]*|=[= \t]*)$")
# `[label]: destination` at the start of a line is a link reference DEFINITION.
# CommonMark consumes it whole: the paragraph disappears from the render entirely,
# and because `markdown_to_text` does not model definitions, both gates see a
# document that is still intact.
_LEAD_REFDEF = re.compile(r"^\[[^\]]*\]:")
# A GFM table's delimiter row. Made only of dashes, colons, pipes and spaces, so it
# carries no token — which is exactly why the gates cannot see the table it builds.
_LEAD_DELIM_ROW = re.compile(r"^\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?$")
_WORD_CH = re.compile(r"[0-9A-Za-z]")


# The two character sequences that let a bracket mean something: an inline link
# `[text](url)` and a reference link `[text][label]`. Nothing else can.
_BRACKET_MEANS = ("](", "][")


def _esc_special(m, dangerous):
    # type: (object, bool) -> str
    """Escape one markdown special — except one that cannot mean anything.

    Two exemptions, both the same argument. Escaping a character that could never
    have been syntax buys no safety and corrupts the stored bytes — a renderer
    hides the backslash, but a BM25 index, an embedder and a human grep do not.

    A SINGLE underscore flanked by word characters can neither open nor close
    emphasis under CommonMark's flanking rule (and ``markdown_to_text``'s ``_ITALIC``
    already mirrors that rule): ``DB_MAX_CONN_LIMIT`` became
    ``DB\\_MAX\\_CONN\\_LIMIT``. A run of two or more stays escaped — ``__x__`` IS
    strong emphasis to ``markdown_to_text``'s ``_BOLD``, which carries no flanking
    guard, so unescaping there would move the recall gate.

    A BRACKET is only syntax next to ``](`` or ``][``. On its own, ``[payments]``
    is a *shortcut reference link* — and it resolves only against a link reference
    definition, which this converter never emits, so there is nothing for it to
    resolve to and every renderer prints it literally. An ini section name, a bus
    slice ``[31:0]``, a ticket id: all of them were being stored as ``\\[…\\]`` for
    a danger that cannot arise. The test is on the whole run text rather than the
    single character, because it takes two characters to make the danger.

    ``dangerous`` is that two-character test, and it is the CALLER's to make: the
    danger is a property of the assembled LINE, not of the ``w:t`` this call is
    escaping. Word splits a run at every rsid, spell-check, bookmark, field and
    internal-hyperlink boundary, so ``[3]`` and ``(page 12)`` routinely arrive as
    two runs; deciding per run left both brackets bare and let the join fabricate
    a link whose destination text a renderer then eats."""
    run = m.group(2)
    if run is None:
        ch = m.group(1)
        if ch in "[]" and not dangerous:
            return ch
        return "\\" + ch
    s, i, j = m.string, m.start(2), m.end(2)
    if (len(run) == 1 and i > 0 and j < len(s)
            and _WORD_CH.match(s[i - 1]) and _WORD_CH.match(s[j])):
        return run
    return "\\" + "\\".join(run)


def _esc(text, line=None):
    # type: (str, object) -> str
    """Backslash-escape inline markdown specials in literal source text.

    ``line`` is the whole literal text of the markdown line ``text`` will end up
    in, when the caller assembles a line out of several pieces; it is used only to
    decide whether a bracket in this piece could combine with a neighbouring piece
    into link syntax. Callers that already hand over a whole line (a footnote, a
    chart caption, a spreadsheet cell) pass nothing and the text speaks for itself."""
    if not text:
        return ""
    ctx = text if line is None else line
    dangerous = any(seq in ctx for seq in _BRACKET_MEANS)
    return _MD_SPECIAL.sub(lambda m: _esc_special(m, dangerous),
                           text.replace("\\", "\\\\"))


def _num_val(value, default):
    # type: (str, int) -> int
    """An OOXML integer attribute, or ``default`` when it is absent or junk."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list_indent(cols, depth, marker):
    # type: (list, int, str) -> str
    """Indentation for a list item at source level ``depth``, by CONTAINMENT.

    CommonMark nests a child item only when it is indented to at least its parent's
    **content column** — the column after the parent's marker and the space following
    it. That is 2 for ``- `` but **3** for ``1. ``, so a fixed two-space indent
    silently flattens every nested ORDERED list: the parent item closes and the child
    renders as its sibling, RENUMBERING the procedure from that point on. A converted
    runbook then instructs a different action for every step after the first sub-step,
    and no gate can object — indentation is not a token.

    ``cols`` is the stack of ancestors still open, as ``(source level, content
    column)``. An item closes every ancestor at its own level or deeper and then sits
    one step in from what remains, so **two items at the same source level are always
    siblings** and an item deeper than anything open costs one step of depth rather
    than the level it names. That is what markdown can hold: a child of a parent that
    is not there cannot be written, and an invented indent of four or more columns
    past the open item stops being a list item at all.

    THE CLAMP HAS TO BE STICKY, and this one was not. Clamping to the HEIGHT of the
    stack — "one deeper than the deepest thing open" — is right for the FIRST item at
    a skipped level and wrong for every one after it, because each successive item
    finds the stack one entry taller. Three PEER bullets at outline level 2 came out
    as a three-deep chain, so a bring-up slide read as though its steps nested; a
    sweep of every level sequence of length 2-4 over levels 0-3 found 199 of 336
    misrepresented. Recall could not see it (indentation is not a token) and neither
    could the deck's structure gate, because the ground truth had been written to
    mirror this function instead of deriving containment for itself."""
    depth = max(0, depth)
    while cols and cols[-1][0] >= depth:
        cols.pop()                                # a peer or shallower item closes it
    indent = cols[-1][1] if cols else 0
    cols.append((depth, indent + len(marker) + 1))
    return " " * indent


def _list_number(counters, num_id, depth, start):
    # type: (dict, str, int, int) -> int
    """The number Word shows on this item, plus the bookkeeping the next one needs.

    One counter per (numbering instance, level). A level counts up from its
    ``w:start``; every DEEPER level returns to its own start the moment a shallower
    one advances, which is why the first sub-step of step 2 is 2.1 and not 2.4.
    Counters are per INSTANCE, so two procedures Word gave their own ``w:numId`` do
    not share a sequence. ``w:lvlRestart`` is not read — the default restart is what
    is modelled, and a document that overrides it is the one case this can misnumber."""
    for key in [k for k in counters if k[0] == num_id and k[1] > depth]:
        del counters[key]
    key = (num_id, depth)
    counters[key] = counters.get(key, start - 1) + 1
    return counters[key]


def _ordered_marker(open_lists, depth, number):
    # type: (dict, int, int) -> str
    """``"3."`` — an ordered marker whose delimiter keeps the RENDERED number equal
    to the one Word wrote.

    CommonMark takes a list's start from its first marker and then counts up on its
    own, ignoring every later number. So an item that must show a restart cannot just
    write its number: glued to the items above it, a renderer prints it as that list's
    next one instead. Switching the delimiter (``.`` <-> ``)``) is CommonMark's own way
    of beginning a new list with no separating block between, and inside a run of
    adjacent items it is the only way there is."""
    for k in [k for k in open_lists if k > depth]:
        del open_lists[k]
    cur = open_lists.get(depth)
    if cur is None:
        delim = "."
    elif number == cur[1] + 1:
        delim = cur[0]
    else:
        delim = ")" if cur[0] == "." else "."
    open_lists[depth] = (delim, number)
    return "%d%s" % (number, delim)


def _close_ordered(open_lists, depth):
    # type: (dict, int) -> None
    """A bullet at ``depth`` ends any ordered list open there or deeper — a change of
    list type is a new list to CommonMark, so the next number must be a start again."""
    for k in [k for k in open_lists if k >= depth]:
        del open_lists[k]


def _close_list(lst):
    # type: (dict) -> None
    """Any non-list block ends the markdown list, exactly as the blank line the
    joiner puts before it does when rendered.

    The NUMBER counters deliberately survive: Word does not restart a procedure
    because a paragraph got in the way, so neither may we. That asymmetry — columns
    reset, counters kept — is the whole of defect 1b."""
    del lst["cols"][:]
    lst["open"].clear()


def _esc_lead(text):
    # type: (str) -> str
    """Escape LINE-LEADING constructs too, for text that opens a markdown line.

    Inline escaping (``_esc``) is not enough: a body paragraph must never come out
    as a BLOCK the source never had. ``15. foo`` becomes an ordered list whose
    marker renderers and strippers both swallow; ``## Build steps`` becomes a real
    H2, which the structure-fidelity gate then (correctly) rejects as a fabricated
    heading, so the whole document fails to publish; ``-----`` becomes a thematic
    break and ``===`` a setext underline, and both of those are worse than a
    failure — they carry no tokens and no heading fact, so BOTH gates report a
    clean pass over a paragraph that has been deleted from the rendered document.
    ``[label]: dest`` is the same shape again: CommonMark eats the whole line as a
    link reference definition.

    One backslash on the front is enough for every one of them: it makes the first
    character literal, and no block construct can open on a literal character.
    ``markdown_to_text`` strips it back off (``_ESCAPED_PUNCT``), so the text layer
    and the recall gate see the paragraph exactly as the document wrote it."""
    if not text:
        return ""
    if text.startswith(">"):
        return "\\" + text
    m = _LEAD_LIST_NUM.match(text)
    if m:
        return text[:m.end(1)] + "\\" + text[m.end(1):]
    if _LEAD_MARK.match(text) or _LEAD_RULE.match(text) or _LEAD_REFDEF.match(text):
        return "\\" + text
    return text


# CommonMark lets an ATX heading end with an optional CLOSING SEQUENCE of `#`s —
# a run preceded by whitespace, optionally followed by whitespace — and DELETES it
# from the rendered heading. So a heading whose own text ends in a hash loses that
# character: `# Drain procedure #` renders as "Drain procedure", and a revision
# marker (`Rev #`), an issue reference or a C preprocessor line ends up one
# character short. Both gates certify it — `#` carries no token, so recall stays
# 1.0, and the heading is still a heading of the same level, so the fact vector
# reports nothing. Measured on a real docx: `# Drain procedure #` -> rendered words
# ['Drain', 'procedure'], recall 1.0, valid True.
_TRAIL_HASH = re.compile(r"(?:^|[ \t])(#+)[ \t]*$")


def _esc_heading(text):
    # type: (str) -> str
    """Heading text that must survive its own trailing `#` run.

    One backslash on the first `#` of that run is enough: a closing sequence is a
    run of literal hashes, and an escaped one is not a hash. `markdown_to_text`
    strips the backslash again, so no token moves. Inline escaping is the caller's
    (the text arrives already `_esc`-ed); nothing here needs `_esc_lead`, because a
    block cannot open inside a heading line."""
    m = _TRAIL_HASH.search(text)
    if not m:
        return text
    return text[:m.start(1)] + "\\" + text[m.start(1):]


def _esc_block_start(text):
    # type: (str) -> str
    """Source text that will sit where a markdown BLOCK can open — a line of its
    own, or a list item's content column right after ``- ``.

    A list item's content column is a block start exactly as column 0 is. After
    ``- ``, CommonMark opens a heading, a nested list, a block quote, a fence or a
    thematic break just as it would at the left margin, and every one of those is a
    block the source document never wrote. Measured on the shipped corpus deck with
    one bullet's text replaced by ``- - -``::

        pristine   bullet_items=16  thematic_breaks=0  recall=1.0  valid=True
        poisoned   bullet_items=15  thematic_breaks=1  recall=1.0  valid=True

    The bullet came out as ``- - - -``, which is a thematic break, and DELETED
    ITSELF: a break carries no tokens for recall to miss, so both gates certified
    it. ``15. step`` fails the other way and is no better — it becomes a nested
    ordered list whose marker ``markdown_to_text`` swallows, so a FAITHFUL deck
    refuses to publish at ``recall: 0.990``.

    Both halves are needed and in this order: ``_esc`` neutralises inline syntax
    (including the backtick and tilde runs that would open a fence), ``_esc_lead``
    the block openers. Every construct that survived that pipeline was checked
    against marko 2.2.3 rather than against this project's own reader — a
    converter's author does not get to rule on what its markdown means.

    A GFM DELIMITER ROW is escaped unconditionally, and that is the one rule here
    that does not ask what the line says on its own. A table needs a delimiter row
    directly under a line holding a pipe, so the danger is a property of a PAIR of
    lines — and no emitter can see its own neighbour. `svg_text` joins a figure's
    labels with newlines, `_join_blocks` stacks consecutive list items with a single
    newline, and both put two pieces of SOURCE TEXT on adjacent lines: three labels
    reading `| Path | Cycles |`, `| --- | --- |`, `| display read | 40 |` published a
    real two-column TABLE the drawing never had (`tables: [{rows: 2, cols: 2,
    has_header: True}]` at token recall 1.0), and the same three as bullets did it
    again inside a list item, where the fact vector does not even see it.

    Escaping it whatever precedes it costs nothing worth having: a line that matches
    is made only of dashes, colons, pipes and spaces, so it carries no token at all,
    and `markdown_to_text` strips the backslash. Tracking the previous line instead
    would mean threading that state through six emitters and the block joiner, and
    would still miss whichever emitter came next.

    No ``line`` context parameter, unlike ``_esc``: every caller here hands over a
    WHOLE line — a list item's content, a comment, a note, a caption — so the text
    speaks for itself and there is no neighbouring piece a bracket could combine
    with. A parameter nothing fills reads as an option somebody chose not to use."""
    esc = _esc_lead(_esc(text))
    return "\\" + esc if _LEAD_DELIM_ROW.match(esc) else esc


def _esc_block_start_md(md):
    # type: (str) -> str
    """The same guard for text that has ALREADY been inline-escaped and marked.

    `_esc_block_start` does both halves — `_esc` then `_esc_lead` — and running the
    first half over rendered markdown would escape the converter's own `**`, `*`,
    `~~` and `[](...)` back into literal punctuation. The deck lane escapes inline
    per RUN, because that is the only place that knows which characters are the
    document's and which are the converter's, so only the line-leading half is left
    to do here.

    Safe on every marker this converter emits, checked rather than assumed: `_LEAD_MARK`
    wants `[+*-]` followed by whitespace, and `**bold**` puts an asterisk there;
    `_LEAD_REFDEF` wants `]:`, and a rendered link puts `](` there."""
    esc = _esc_lead(md)
    return "\\" + esc if _LEAD_DELIM_ROW.match(esc) else esc


def _md_cell(text):
    # type: (str) -> str
    """Make a text safe as a single GFM table cell (escape pipes, no newlines)."""
    return _WS.sub(" ", (text or "")).replace("|", "\\|").strip()


# --------------------------------------------------------------- run formatting
#
# Bold, italic, strikethrough and monospace are CONTENT, not decoration:
# end-goal.md §1 says "structure IS content". They carry no tokens, though, so the
# losslessness gate is blind to losing them — which is exactly why they were lost
# for so long. The structure_fidelity gate is what grades them now.
#
# Marks are applied outermost-first in this fixed order so the output is
# deterministic and a comparison against the source is meaningful.
_MARK_ORDER = ("code", "strong", "em", "strike")
_MARKERS = {"strong": "**", "em": "*", "strike": "~~"}
_OFF = ("0", "false", "off")

# Word's toggle properties. bCs/iCs are the complex-script twins; a document that
# sets only those (common in mixed-script text) still means bold/italic.
_BOLD_LOCALS = ("b", "bCs")
_ITALIC_LOCALS = ("i", "iCs")
_STRIKE_LOCALS = ("strike", "dstrike")

# Styles that mean "this is code". Matched on BOTH the styleId and the canonical
# w:name, lowercased: Word localizes styleIds but the name is stabler, the same
# argument _docx_styles makes for headings.
_CODE_STYLE_NAMES = frozenset((
    "html preformatted", "source code", "code", "plain text", "macro text",
    "html code", "html typewriter", "verbatim char", "code char", "console",
    "codeblock", "code block", "preformatted text", "hljs",
))


def _docx_style_records(styles_xml):
    # type: (str) -> dict
    """styleId -> the RAW facts of one ``w:style``, nothing resolved.

    ``{"name", "based", "type", "outline", "num", "ilvl"}``. Kept unresolved and in
    one place because THREE questions are asked of the same file — is this a
    heading, is it monospace, does it carry numbering — and all three must follow
    ``w:basedOn`` or they lie in the same way. A custom ``NimbusH1`` derived from
    ``Heading1`` is a heading to Word and to LibreOffice; reading only its own
    ``w:name`` made a two-section document one structureless blob with
    ``status: ok``."""
    root = _root(styles_xml)
    records = {}
    if root is None:
        return records
    for style in root:
        if _local(style.tag) != "style":
            continue
        sid = _attr(style, "styleId")
        if not sid:
            continue
        rec = {"name": "", "based": "", "type": _attr(style, "type"),
               "outline": None, "num": "", "ilvl": ""}
        for ch in style.iter():
            loc = _local(ch.tag)
            if loc == "name":
                rec["name"] = _attr(ch, "val")
            elif loc == "basedOn":
                rec["based"] = _attr(ch, "val")
            elif loc == "outlineLvl":
                rec["outline"] = _num_val(_attr(ch, "val"), None)
            elif loc == "numId":
                rec["num"] = _attr(ch, "val")
            elif loc == "ilvl":
                rec["ilvl"] = _attr(ch, "val")
        records[sid] = rec
    return records


def _style_chain(sid, records):
    # type: (str, dict) -> list
    """``[sid, its basedOn, that one's basedOn, ...]`` — nearest first.

    Stops on a missing style and on a CYCLE: ``w:basedOn`` pointing back into the
    chain is malformed but real (a template edited by two tools), and a converter
    that recursed on it would hang on the document instead of converting it."""
    chain, seen = [], set()
    cur = sid
    while cur and cur in records and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = records[cur].get("based", "")
    return chain


def _docx_code_styles(styles_xml):
    # type: (str) -> dict
    """``{"para": {styleId}, "char": {styleId}}`` — the styles that mean monospace.

    Separate from ``_docx_styles`` on purpose: that one maps styleId to an *int*
    heading level and two call sites do arithmetic on the result, so a non-int
    marker cannot be smuggled into it. A style DERIVED from a code style is code
    too, and is filed under its own ``w:type``: a character style based on a
    paragraph one is still an inline span."""
    records = _docx_style_records(styles_xml)
    found = {"para": set(), "char": set()}
    for sid in records:
        for anc in _style_chain(sid, records):
            keys = set(k for k in (anc, records[anc]["name"]) if k)
            if any(k.strip().lower().replace("-", " ") in _CODE_STYLE_NAMES
                   for k in keys):
                kind = "char" if records[sid]["type"] == "character" else "para"
                found[kind].add(sid)
                break
    return found


def _docx_style_numbering(styles_xml):
    # type: (str) -> dict
    """styleId -> ``(numId, ilvl)`` carried by the style's own ``w:pPr/w:numPr``.

    Word's built-in ``List Number``/``List Bullet``, and any template style with
    numbering baked into it, put ``w:numPr`` in the STYLE rather than in each
    paragraph. Reading only the paragraph's ``w:pPr`` made such a list vanish —
    three plain paragraphs with no markers at all — and the structural ground truth
    was blind in exactly the same place, so no gate could object. Inherited through
    ``w:basedOn``, nearest definition wins."""
    records = _docx_style_records(styles_xml)
    out = {}
    for sid in records:
        for anc in _style_chain(sid, records):
            if records[anc]["num"]:
                out[sid] = (records[anc]["num"], records[anc]["ilvl"] or "0")
                break
    return out


def _toggle_on(pr):
    # type: (object) -> bool
    """A Word toggle property is tri-state: present means on UNLESS w:val turns it
    off, which is how a run inside a bold style un-bolds itself."""
    return _attr(pr, "val") not in _OFF


def _mark_of(loc):
    # type: (str) -> str
    """The mark one ``w:rPr`` child names, or ``""`` for a property that is not
    one of the three this converter can render."""
    if loc in _BOLD_LOCALS:
        return "strong"
    if loc in _ITALIC_LOCALS:
        return "em"
    if loc in _STRIKE_LOCALS:
        return "strike"
    return ""


def _rpr_marks(el):
    # type: (object) -> tuple
    """``(on, off)`` — the marks the DIRECT ``w:rPr`` children of ``el`` turn on
    and off. Tri-state, because ``<w:b w:val="0"/>`` is how a run (or a derived
    style) takes back the bold it would otherwise inherit."""
    on, off = set(), set()
    for pr in el:
        if _local(pr.tag) != "rPr":
            continue
        for prop in pr:
            loc = _local(prop.tag)
            if loc == "rPrChange":            # the PREVIOUS formatting of a tracked
                continue                      # change — never the live one
            mark = _mark_of(loc)
            if mark:
                (on if _toggle_on(prop) else off).add(mark)
    return frozenset(on), frozenset(off)


def _docx_style_marks(styles_xml):
    # type: (str) -> dict
    """styleId -> the emphasis marks that style CARRIES, resolved through basedOn.

    Word puts bold on the STYLE at least as often as on the run: the built-in
    ``Strong``/``Emphasis`` character styles, a ``Quote`` or ``Caption`` paragraph
    style, and anything that came out of pandoc or an HTML->Word round trip. Reading
    only a run's own ``w:rPr`` deleted every one of them — and deleted them
    SILENTLY, because the structural ground truth used to read the document the same
    wrong way and agree that there was nothing there.

    Resolved by VALUE down the chain (``(inherited | own_on) - (own_off - own_on)``),
    so a style based on a bold one that carries ``<w:b w:val="0"/>`` is not bold.
    ``w:docDefaults`` and the ``w:default="1"`` style are deliberately NOT resolved:
    a document that declares bold document-wide is not emphasising anything, and
    marking every run in the file would be the mirror-image lie."""
    root = _root(styles_xml)
    if root is None:
        return {}
    records = _docx_style_records(styles_xml)
    own = {}
    for style in root:
        if _local(style.tag) == "style" and _attr(style, "styleId"):
            own[_attr(style, "styleId")] = _rpr_marks(style)
    out = {}
    for sid in records:
        marks = frozenset()
        for anc in reversed(_style_chain(sid, records)):   # farthest ancestor first
            on, off = own.get(anc, (frozenset(), frozenset()))
            marks = (marks | on) - (off - on)
        if marks:
            out[sid] = marks
    return out


def _run_marks(run, char_styles=None, style_marks=None, inherited=()):
    # type: (object, object, object, tuple) -> tuple
    """Formatting marks carried by one w:r: its own ``w:rPr``, the character style
    its ``w:rStyle`` names, and whatever the owning PARAGRAPH's style supplies.

    The order of resolution is load-bearing. An inherited mark is turned back off
    by the run's OWN ``<w:b w:val="0"/>`` and by nothing else, which is how a run
    inside a bold style un-bolds itself; an ``on`` in the same run wins over its own
    ``off``, so a contradictory ``w:rPr`` still emphasises rather than dropping the
    fact on the floor."""
    style_on = set(inherited)
    on, off = set(), set()
    code = False
    for pr in run:
        if _local(pr.tag) != "rPr":
            continue
        for prop in pr:
            loc = _local(prop.tag)
            if loc == "rPrChange":            # the PREVIOUS formatting of a tracked
                continue                      # change — never the live one
            if loc == "rStyle":
                val = _attr(prop, "val")
                if char_styles and val in char_styles:
                    code = True
                if style_marks:
                    style_on |= style_marks.get(val, frozenset())
                continue
            mark = _mark_of(loc)
            if mark:
                (on if _toggle_on(prop) else off).add(mark)
    marks = (style_on | on) - (off - on)
    if code:
        marks.add("code")
    return tuple(m for m in _MARK_ORDER if m in marks)


def _code_span(text):
    # type: (str) -> str
    """Wrap text as an inline code span, widening the fence past any backticks.

    CommonMark closes a code span only on a backtick string of exactly equal
    length, and pads with one space when the content itself starts or ends with a
    backtick — so ``kubectl get pods`` survives whatever it contains."""
    longest = 0
    for m in re.finditer(r"`+", text):
        longest = max(longest, len(m.group(0)))
    fence = "`" * (longest + 1)
    pad = " " if (text.startswith("`") or text.endswith("`")) else ""
    return fence + pad + text + pad + fence


def _punct(ch):
    # type: (str) -> bool
    """CommonMark's "punctuation" for the flanking rule.

    Anything that is neither whitespace nor alphanumeric. Deliberately WIDER than
    the 0.30 spec (which leaves non-ASCII symbols out of the class): over-reporting
    punctuation can only make the converter shift a delimiter it did not have to,
    while under-reporting it emits one that cannot open."""
    return bool(ch) and not ch.isspace() and not ch.isalnum()


def _flank_blocked(inner, outer):
    # type: (str, str) -> bool
    """True when a delimiter run sitting between ``outer`` and ``inner`` can
    neither open nor close.

    A run is left-flanking when it is not followed by whitespace AND either it is
    not followed by punctuation, or it is preceded by whitespace or punctuation
    (start of line counts as whitespace); right-flanking is the mirror image. So
    exactly ONE configuration kills a delimiter: punctuation on the inside and a
    word character on the outside. ``Field MODE**(2:0)**`` is that configuration —
    a renderer prints the four asterisks literally and the document's bold is gone
    from the render, which the fidelity gate then reports as ``strong 1 vs 0``."""
    return _punct(inner) and bool(outer) and not outer.isspace() and not _punct(outer)


def _lead_unit(s):
    # type: (str) -> int
    """Length of the first RENDERING unit of an escaped string — a backslash escape
    is two characters that render as one, and splitting it would leave the
    backslash escaping a delimiter instead of the character it was written for."""
    return 2 if (s[:1] == "\\" and len(s) > 1) else 1


def _trail_unit(s):
    # type: (str) -> int
    """Length of the last rendering unit, found by scanning from the LEFT: ``\\\\.``
    is an escaped backslash followed by a period, not an escaped period."""
    i, last = 0, 0
    while i < len(s):
        last = i
        i += 2 if (s[i] == "\\" and i + 1 < len(s)) else 1
    return len(s) - last


def _wrap_marks(text, marks, prev="", nxt=""):
    # type: (str, tuple, str, str) -> str
    """Apply formatting marks to one already-escaped segment.

    Leading and trailing whitespace is moved OUTSIDE the markers: CommonMark's
    right-flanking rule refuses to close ``**bold **``, which would leave the
    asterisks as literal text in the stored bytes.

    ``prev``/``nxt`` are the characters that will sit either side of the emitted
    markers, and they are what makes the rest of that rule checkable. When the
    delimiter would be blocked — punctuation just inside it, a word character just
    outside — one rendering unit of the segment's own text is moved out of the
    span. That unit is punctuation by construction, so it becomes the delimiter's
    new outer neighbour and the run flanks. The cost is that the emphasis covers
    ``2:0`` instead of ``(2:0)``; the alternative is asterisks the reader sees and
    an emphasis the render does not have. See DEVIATIONS in docs/quality-plan.md.

    The shift is only available when the delimiter's inner neighbour is a character
    of the TEXT. ``**`` and ``*`` together are ONE contiguous asterisk run, so bold
    italic is still shiftable; a ``~~`` or a backtick between the asterisks and the
    text (``**~~x~~**``, ``**`x`**``) is a delimiter that cannot move, and those stay
    as they are with the fidelity gate reporting the loss."""
    if not marks or not text.strip():
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    if lead:
        prev = lead[-1]
    if trail:
        nxt = trail[0]
    pre = post = ""
    emph = [m for m in ("strong", "em", "strike") if m in marks]
    if "code" not in marks and emph and ("strike" not in emph or emph == ["strike"]):
        n = _lead_unit(core)
        if len(core) > n and _flank_blocked(core[n - 1], prev):
            pre, core = core[:n], core[n:]
        n = _trail_unit(core)
        if len(core) > n and _flank_blocked(core[-1], nxt):
            core, post = core[:-n], core[-n:]
    if "code" in marks:
        core = _code_span(core)
    for mark in ("strike", "em", "strong"):       # innermost first
        if mark in marks:
            core = _MARKERS[mark] + core + _MARKERS[mark]
    return lead + pre + core + post + trail


def _stars(marks):
    # type: (tuple) -> bool
    """True when this segment's OUTERMOST marker is an asterisk run."""
    return "strong" in marks or "em" in marks


def _edge_char(group, first, merges):
    # type: (tuple, bool, bool) -> str
    """The character that will actually sit against this segment's delimiter run.

    Usually the neighbour's own marker, and every marker this converter emits
    (``*``, ``~``, `````) is punctuation, so which one it is does not matter.

    The exception is what made ``**MODE***(2:0)*`` unreadable: two asterisk markers
    written against each other are ONE delimiter run to CommonMark, so the
    neighbour's marker does not shield us — what abuts the run is whatever is inside
    the neighbour's asterisks, its own inner ``~~``/backtick or its text. ``merges``
    is whether THIS segment emits asterisks too; a ``~~`` against a ``*`` is two
    runs, and each is punctuation to the other."""
    marks, text = group
    if not text:
        return ""
    edge = text[0] if first else text[-1]
    if not marks or not text.strip() or edge.isspace():
        return edge
    if merges and _stars(marks):
        return "*" if ("code" in marks or "strike" in marks) else edge
    return "*"


# The separator, and why it is an html comment. Two rendered spans written against
# each other can be UNREADABLE to CommonMark, in two distinct ways:
#
#   MERGE      `***alpha***` + `*beta*` is a delimiter run of FOUR asterisks, and the
#              parser pairs them as one em span, not two. Measured at HEAD on an
#              ordinary Word paragraph of that shape: `structure_fidelity` fails on
#              `em`, `token_recall` reads 0.0, and the document refuses to publish.
#   FLANK      `**~~beta~~**` after a letter cannot OPEN: the `**` is preceded by a
#              word character and followed by punctuation, which is not left-flanking.
#              `_wrap_marks` shifts a text unit out to fix exactly this, and cannot
#              when the inner neighbour is another delimiter — its docstring says so.
#
# An empty html comment renders as nothing, carries no token, and breaks a delimiter
# run. Verified against marko 2.2.3 over every sequence of one to three spans drawn
# from an eight-mark alphabet — 576 documents, 0 misreads with this rule, 326 without
# it. Both of this project's readers remove it to NOTHING (`markdown_to_text`,
# `_mdstructure._words`), which they must: it only ever stands where the two spans are
# adjacent with NO whitespace, which is to say between two halves of one word.
#
# It is NOT written at every boundary. Emphasis with spaces around it — almost all
# emphasis — needs nothing, and every shipped document stays byte-identical.
_SPAN_SEP = "<!---->"
_DELIMS = "*~`"


def _delim_run(text, at_end):
    # type: (str, bool) -> str
    """The run of delimiter characters at one end of a rendered span, or ``""``."""
    if not text:
        return ""
    ch = text[-1] if at_end else text[0]
    if ch not in _DELIMS:
        return ""
    i = 0
    while i < len(text) and (text[-1 - i] if at_end else text[i]) == ch:
        i += 1
    return ch * i


def _needs_separator(left, right):
    # type: (str, str) -> bool
    """Would writing ``right`` straight after ``left`` be misread?

    Three questions, each a property of the BOUNDARY rather than of either span:
    whether two runs of the same delimiter would fuse into one, and whether either
    span's outermost delimiter would be left unable to flank by what lands beside it.
    ``left`` empty means this is the start of the line, where the separator must never
    go: a line beginning `<!--` is an HTML BLOCK and nothing in it is parsed as inline
    markdown at all."""
    if not left or not right:
        return False
    if left[-1] in _DELIMS and left[-1] == right[0]:
        return True                       # the two runs fuse into one
    open_run = _delim_run(right, False)
    if open_run and left[-1].isalnum():
        inner = right[len(open_run):len(open_run) + 1]
        if inner and not inner.isalnum():
            return True                   # cannot LEFT-flank: word outside, punct in
    close_run = _delim_run(left, True)
    if close_run and right[0].isalnum():
        inner = left[-len(close_run) - 1:-len(close_run)]
        if inner and not inner.isalnum():
            return True                   # cannot RIGHT-flank, the mirror of above
    return False


def _render_runs(segments):
    # type: (list) -> str
    """Coalesce adjacent identically-marked segments, then apply the markers.

    Coalescing is mandatory, not tidiness. Word splits one word across several
    runs at every property boundary — a spell-check mark is enough — so wrapping
    each run on its own emits ``**Dma****Arbiter**``, which ``markdown_to_text``'s
    non-greedy _BOLD mis-pairs into a stray literal ``**`` in the text layer.

    Coalescing FIRST is also what makes the flanking check meaningful: a segment's
    neighbours are only known once the groups are final."""
    groups = []  # type: list
    i = 0
    while i < len(segments):
        marks, text = segments[i]
        j = i + 1
        while j < len(segments) and segments[j][0] == marks:
            text += segments[j][1]
            j += 1
        groups.append((marks, text))
        i = j
    out = []
    for k, group in enumerate(groups):
        star = _stars(group[0])
        prev = _edge_char(groups[k - 1], False, star) if k else ""
        nxt = _edge_char(groups[k + 1], True, star) if k + 1 < len(groups) else ""
        rendered = _wrap_marks(group[1], group[0], prev, nxt)
        if out and _needs_separator(out[-1], rendered):
            out.append(_SPAN_SEP)
        out.append(rendered)
    return "".join(out)


def _gfm_table(rows, min_width=0):
    # type: (list, int) -> str
    """Rows of cell texts -> a well-formed GFM pipe table (first row = header).

    Every row is padded to the shared width so the column count is consistent —
    the validator's table-columns rule holds by construction. The width is the
    last column ANY row actually uses, so trailing always-empty columns (styled
    but valueless spreadsheet cells) never render as pipe noise.

    ``min_width`` is a floor for callers that KNOW the real grid width. A docx
    does: ``w:tblGrid`` plus ``w:gridSpan`` give it exactly. Without the floor a
    one-row table whose only cell spans both columns trims back to a single
    column — the span padding looks exactly like a styled-empty column — and the
    grid width is silently lost. A spreadsheet passes no floor, because a ragged
    sheet has no declared grid to defend."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(1, int(min_width or 0))
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if r[i].strip():
                width = max(width, i + 1)
                break
    out = []
    for i, r in enumerate(rows):
        cells = (list(r) + [""] * width)[:width]
        out.append("| " + " | ".join(cells) + " |")
        if i == 0:
            out.append("| " + " | ".join(["---"] * width) + " |")
    return "\n".join(out)


def _fenced(lines):
    # type: (list) -> str
    """One fenced code block, with a fence long enough to survive its contents.

    CommonMark closes a fence only on a run of at least the opening length, so a
    body line that itself starts with ``` would truncate a three-backtick block."""
    longest = 0
    for line in lines:
        for m in re.finditer(r"`+", line):
            longest = max(longest, len(m.group(0)))
    fence = "`" * max(3, longest + 1)
    return fence + "\n" + "\n".join(lines) + "\n" + fence


def _join_blocks(blocks):
    # type: (list) -> str
    """Join (kind, text) blocks: consecutive list items stack with single newlines
    (one markdown list), consecutive code paragraphs fuse into ONE fenced block,
    everything else is blank-line separated.

    Code has to fuse: one w:p is one line, so a five-line shell transcript arrives
    as five blocks, and five separate fences would be five separate programs.

    ``lib`` is a list item that must NOT be glued to the item above it. CommonMark
    lets a list interrupt a paragraph only when an ordered marker reads ``1``, so a
    sub-list that starts at 5 written directly under its parent's text is swallowed
    as that parent's prose. One blank line closes the parent's paragraph and the
    sub-list survives; the items after it stack normally again.

    An EMPTY code block is a blank line inside a listing (in Word, pressing Enter
    inside a shell transcript), and it is kept when it falls between two code lines:
    a blank line is part of the program. Leading and trailing ones are dropped —
    they emit no block, so they neither open a fence nor close one, which is exactly
    what the structural ground truth counts."""
    # An image sentinel indented to an open list item's content column is a
    # CONTINUATION of that item, and that is what keeps the items AFTER it at their
    # depth (`_emit_image_blocks` says why). When no item follows, there is no depth
    # left to keep and the indent would claim only that the picture sits inside the
    # last bullet — a position the source does not state. So it is settled back to
    # column 0, which is also what keeps this fix from moving a single byte of any
    # document whose pictures merely follow a list rather than interrupting one.
    flat = set()
    for n, (kind, text) in enumerate(blocks):
        if kind != "img" or text == text.lstrip():
            continue
        after = n + 1
        while after < len(blocks) and blocks[after][0] == "img":
            after += 1
        if after >= len(blocks) or blocks[after][0] not in ("li", "lib"):
            flat.add(n)
    parts = []  # type: list
    prev_kind = None
    i = 0
    while i < len(blocks):
        kind, text = blocks[i]
        if i in flat:
            text = text.lstrip()
        if kind == "code":
            run = []  # type: list
            while i < len(blocks) and blocks[i][0] == "code":
                run.append(blocks[i][1])
                i += 1
            while run and not run[0]:
                del run[0]
            while run and not run[-1]:
                del run[-1]
            if not run:
                continue
            text = _fenced(run)
        elif not text:
            i += 1
            continue
        else:
            i += 1
        if parts:
            parts.append("\n" if (kind == "li" and prev_kind in ("li", "lib"))
                         else "\n\n")
        parts.append(text)
        prev_kind = kind
    return "".join(parts)


def _chart_text(chart_xml):
    # type: (str) -> str
    """Everything a chart shows: title/axis runs (a:t) plus cached series names,
    categories and values (c:v)."""
    root = _root(chart_xml)
    if root is None:
        return ""
    return _text_of(root, value_locals=("t", "v"))


def _diagram_items(data_xml):
    # type: (str) -> list
    """SmartArt node texts, one item per diagram point that carries text."""
    root = _root(data_xml)
    if root is None:
        return []
    items = []
    pts = []  # type: list
    _find_locals(root, ("pt",), pts)
    for pt in pts:
        t = _text_of(pt)
        if t:
            items.append(t)
    return items


def _diagram_list_md(data_xml):
    # type: (str) -> str
    return "\n".join("- " + _esc_block_start(i) for i in _diagram_items(data_xml))


def _embedded_sections(parts, pattern_titles):
    # type: (dict, tuple) -> list
    """(kind, text) blocks for embedded chart/diagram parts matching each
    ``(regex, title, renderer)`` — shared by the docx/pptx/xlsx assemblers."""
    blocks = []  # type: list
    for pat, title, render in pattern_titles:
        items = []
        for name in sorted(parts):
            if pat.match(name):
                t = render(parts[name])
                if t:
                    items.append(t)
        if items:
            blocks.append(("h", "## " + title))
            for t in items:
                # Each renderer escapes its OWN text, because only it knows
                # whether it built a bullet list or rendered free prose. This
                # used to be guessed here, from whether the value started with
                # "- " — so a text box whose content was the three characters
                # `- - -` was taken for a pre-formatted list, passed through
                # unescaped, and published as a THEMATIC BREAK: the box's text
                # gone from the document at recall 1.0, gate pass.
                blocks.append(("p", t))
    return blocks


# --------------------------------------------------------------------------- docx

_HEADING_NAME = re.compile(r"^heading\s+([1-9])$")
_DOCX_CHART = re.compile(r"^word/charts/chart(?:Ex)?\d+\.xml$")
_DOCX_DIAGRAM = re.compile(r"^word/diagrams/data\d+\.xml$")


def _docx_styles(styles_xml):
    # type: (str) -> dict
    """styleId -> heading level, from ``word/styles.xml``.

    A style is a heading when its w:name is ``heading N`` (Word's canonical names
    survive localization better than styleIds) or when it carries an explicit
    w:outlineLvl. ``Title`` maps to level 1. Both questions are asked of the whole
    ``w:basedOn`` chain, nearest first: a custom ``NimbusH1 basedOn="Heading1"`` is
    an ``<h1>`` to every real reader, and treating it as body prose deleted every
    heading in the document while the report still said ``status: ok``."""
    records = _docx_style_records(styles_xml)
    levels = {}
    for sid in records:
        for anc in _style_chain(sid, records):
            name = (records[anc]["name"] or "").strip().lower()
            outline = records[anc]["outline"]
            m = _HEADING_NAME.match(name)
            if m:
                levels[sid] = int(m.group(1))
            elif name == "title":
                levels[sid] = 1
            elif outline is not None and 0 <= outline <= 8:
                levels[sid] = outline + 1
            else:
                continue
            break
    return levels


# Everything the paragraph and run renderers need to know about word/styles.xml,
# in one bundle so the four walkers that thread it cannot drift apart on which
# question they answer from which map.
_EMPTY_STYLE_CTX = {"para": (), "char": (), "levels": {}, "marks": {}}


def _docx_style_ctx(styles_xml):
    # type: (str) -> dict
    """``{"para", "char", "levels", "marks"}`` — the four style questions the docx
    renderers ask: is this paragraph code, is this run code, is this paragraph a
    heading, and what emphasis does this style carry."""
    code = _docx_code_styles(styles_xml)
    return {"para": code["para"], "char": code["char"],
            "levels": _docx_styles(styles_xml),
            "marks": _docx_style_marks(styles_xml)}


def _docx_numbering(numbering_xml):
    # type: (str) -> dict
    """(numId, ilvl) -> ``(numFmt, start)``, from numbering.xml.

    ``start`` is the number the level's FIRST item carries: ``w:start`` on the
    abstract level, overridden by whatever this numbering instance says in
    ``w:lvlOverride`` (``w:startOverride``, or a replacement ``w:lvl``). Neither was
    read anywhere until now, so a procedure declared to begin at 5 shipped beginning
    at 1 and the prose saying "see step 5" pointed at nothing. A level with no
    ``w:numFmt`` stays absent, so the caller's ``bullet`` default is unchanged."""
    root = _root(numbering_xml)
    fmts = {}
    if root is None:
        return fmts
    abstract = {}
    for an in root:
        if _local(an.tag) != "abstractNum":
            continue
        aid = _attr(an, "abstractNumId")
        for lvl in an:
            if _local(lvl.tag) != "lvl":
                continue
            ilvl = _attr(lvl, "ilvl")
            fmt, start = "", 1
            for ch in lvl:
                loc = _local(ch.tag)
                if loc == "numFmt":
                    fmt = _attr(ch, "val")
                elif loc == "start":
                    start = _num_val(_attr(ch, "val"), 1)
            if fmt:
                abstract[(aid, ilvl)] = (fmt, start)
    for num in root:
        if _local(num.tag) != "num":
            continue
        nid = _attr(num, "numId")
        for ch in num:
            if _local(ch.tag) == "abstractNumId":
                aid = _attr(ch, "val")
                for (a, ilvl), pair in abstract.items():
                    if a == aid:
                        fmts[(nid, ilvl)] = pair
        # The instance's own overrides come SECOND, so they win over the abstract
        # definition they are overriding. This is where "restart this list at 1"
        # lives when the same abstract numbering is used twice in one document.
        for ch in num:
            if _local(ch.tag) != "lvlOverride":
                continue
            ilvl = _attr(ch, "ilvl")
            fmt, start = fmts.get((nid, ilvl), ("bullet", 1))
            for sub in ch:
                loc = _local(sub.tag)
                if loc == "startOverride":
                    start = _num_val(_attr(sub, "val"), start)
                elif loc == "lvl":
                    for lv in sub:
                        lloc = _local(lv.tag)
                        if lloc == "numFmt":
                            fmt = _attr(lv, "val") or fmt
                        elif lloc == "start":
                            start = _num_val(_attr(lv, "val"), start)
            fmts[(nid, ilvl)] = (fmt, start)
    return fmts


def _rels_targets(rels_xml):
    # type: (str) -> dict
    """rId -> Target for EXTERNAL relationships (hyperlinks) in a ``.rels`` part."""
    root = _root(rels_xml)
    out = {}
    if root is None:
        return out
    for rel in root:
        if _local(rel.tag) != "Relationship":
            continue
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rel.attrib.get("TargetMode", "") != "External":
            continue
        if rid and target:
            out[rid] = target
    return out


def _p_style_info(p):
    # type: (object) -> tuple
    """(styleId, numId, ilvl, outlineLvl) from a paragraph's pPr, any missing -> ''.

    w:pPrChange/w:rPrChange hold the PREVIOUS properties of a tracked change —
    reading through them would resurrect stale styles (a Caption-turned-Heading
    ghost), so those subtrees are never descended."""
    sid = num_id = ilvl = outline = ""
    stack = [ch for ch in p if _local(ch.tag) == "pPr"]
    while stack:
        el = stack.pop()
        for pr in el:
            loc = _local(pr.tag)
            if loc in ("pPrChange", "rPrChange"):
                continue
            if loc == "pStyle":
                sid = _attr(pr, "val")
            elif loc == "numId":
                num_id = _attr(pr, "val")
            elif loc == "ilvl":
                ilvl = _attr(pr, "val")
            elif loc == "outlineLvl":
                outline = _attr(pr, "val")
            else:
                stack.append(pr)
    return sid, num_id, ilvl, outline


# ECMA-376 Part 1 §17.3.1.20 (w:outlineLvl) restricts the value to 0..9, and the
# two ends of that range do not mean the same KIND of thing. 0..8 are the nine
# outline levels a heading can sit at — "Level 1".."Level 9", rendered h1..h9 —
# while 9 is the value Word writes for **Body Text**: the paragraph's explicit
# statement that it is NOT in the outline at all. So the range is not a bound to
# clamp, it is a range with a sentinel on the end, and 9 has to fall out of the
# heading branch entirely rather than be treated as the deepest heading.
_OUTLINE_BODY_TEXT = 9


def _heading_level(sid, outline, levels):
    # type: (str, str, dict) -> object
    """The markdown heading level a paragraph renders at, or ``None``: its style's
    level, else an explicit ``w:outlineLvl`` on the paragraph itself.

    An unbounded ``int(outline) + 1`` turned ``w:outlineLvl w:val="9"`` — "this is
    body text" — into a level 10, which ``_add_heading``'s ``min(level, 6)`` then
    rendered as ``###### Ordinary body prose.``. Every paragraph in a document
    whose author had once ticked "Body Text" became a heading, the outline in
    ``structure.json`` reparented the sections under it, and (because the ground
    truth read the same value the same wrong way) both sides agreed and the gate
    stayed green. The style path has always bounded this correctly; the paragraph
    path had the bound missing, not different."""
    level = levels.get(sid)
    if level is None and outline:
        try:
            declared = int(outline)
        except ValueError:
            return None
        if 0 <= declared < _OUTLINE_BODY_TEXT:
            level = declared + 1
    return level


def _p_marks(p, sty):
    # type: (object, object) -> tuple
    """The emphasis a paragraph's OWN style hands to every run inside it.

    Two exclusions, and both are statements about MARKDOWN rather than about Word.
    A CODE paragraph carries none: inside a fence ``**`` is two asterisks, so there
    is no span there either to emit or to lose. A HEADING carries none either —
    every stock ``Heading1..9`` carries ``<w:b/>``, and ``# Title`` already renders
    bold, so honouring it would demand ``# **Title**`` of every heading in every
    document. A run's OWN ``w:rStyle`` marks inside either still count."""
    if not sty:
        return ()
    sid, _num, _ilvl, outline = _p_style_info(p)
    if not sid and not outline:
        return ()
    if sid in (sty.get("para") or ()):
        return ()
    if _heading_level(sid, outline, sty.get("levels") or {}):
        return ()
    return tuple((sty.get("marks") or {}).get(sid, ()))


def _p_literal(p):
    # type: (object) -> str
    """The paragraph's literal source text, joined the way the markdown line will
    join it — the string a LINE-SCOPED escaping decision has to be made against.

    Only the characters the document itself supplies: a hyperlink's display text is
    in, the ``](url)`` the converter synthesises around it is not. That asymmetry is
    the point. A genuine link must not freeze every unrelated ``[31:0]`` in the same
    paragraph into ``\\[31:0\\]``, and a fabricated one must not be allowed to form."""
    out = []  # type: list

    def walk(el):
        for ch in el:
            loc = _local(ch.tag)
            if loc in _SKIP_LOCALS or loc in ("pPr", "txbxContent"):
                continue
            if loc == "t":
                if ch.text:
                    out.append(ch.text)
            elif loc in _BREAK_LOCALS:
                out.append(" ")
            else:
                walk(ch)
    walk(p)
    return "".join(out)


def _collapse_prose(segments):
    # type: (list) -> list
    """Collapse runs of whitespace in PROSE segments, leaving code spans verbatim.

    Whitespace is not content in prose — Word's own renderer collapses it — but
    inside a code span it IS content: ``cmd   --flag`` and a column-aligned
    ``NAME      OFFSET`` mean what their columns say, and one shared normalisation
    over the finished line silently retyped both.

    The collapse spans segment boundaries, because Word splits one word and the
    space after it across runs, so collapsing each segment on its own would leave
    ``foo `` + `` bar`` doubled where the old whole-line pass did not."""
    out = []  # type: list
    prev_space = True                 # the start of the line behaves like a space
    for marks, text in segments:
        if "code" in marks:
            core = text.strip()
            if core:
                # Only the OUTER whitespace of a code segment is prose spacing;
                # _wrap_marks moves it outside the backticks anyway.
                chunk = ((" " if (text[:1].isspace() and not prev_space) else "")
                         + core + (" " if text[-1:].isspace() else ""))
            else:
                chunk = "" if prev_space else " "
        else:
            chunk = _WS.sub(" ", text)
            if prev_space and chunk[:1] == " ":
                chunk = chunk[1:]
        if chunk:
            prev_space = chunk[-1:] == " "
        out.append((marks, chunk))
    return out


def _docx_p_text(p, links, boxes, images=None, sty=None, raw=False, pmarks=()):
    # type: (object, dict, list, object, object, bool, tuple) -> str
    """Markdown text of one paragraph: verbatim run joins, hyperlinks rendered
    ``[text](url)`` when the rel target is external. Text boxes anchored inside
    the paragraph are collected into ``boxes`` for rendering as their own blocks.

    When ``images`` is a list, embedded-picture rIds (DrawingML ``<a:blip>`` / VML
    ``<v:imagedata>``) are appended to it in reading order; the caller emits the
    sentinels. ``None`` (the default) means the legacy text-only walk -- byte-identical.

    ``sty`` is the style context from ``_docx_style_ctx``: ``char`` (the character
    styleIds that mean monospace, whose runs become inline code spans) and ``marks``
    (the emphasis each style carries). ``pmarks`` is what the paragraph's OWN style
    hands to every run in it — see ``_p_marks``.

    ``raw`` is for a paragraph that is ITSELF code. Its text is emitted unescaped,
    unmarked and — this is the part that was missing — UNTOUCHED: no whitespace
    normalisation, ``w:tab`` as a tab and ``w:br`` as a newline, because the caller
    puts it inside a fence where indentation and column alignment are the content.
    Escaping is skipped for the same reason (``markdown_to_text`` keeps fenced lines
    verbatim and never unescapes them, so a backslash there would destroy recall)."""
    segments = []  # type: list
    char_styles = sty.get("char") if sty else None
    style_marks = sty.get("marks") if sty else None
    line = None if raw else _p_literal(p)

    def walk(el, marks):
        for ch in el:
            loc = _local(ch.tag)
            if loc in _SKIP_LOCALS or loc == "pPr":
                continue
            if loc == "txbxContent":
                boxes.append(ch)
                continue
            if images is not None and loc == "blip":
                rid = _attr(ch, "embed") or _attr(ch, "link")
                if rid:
                    images.append(rid)
                continue
            if images is not None and loc == "imagedata":
                rid = _attr(ch, "id")
                if rid:
                    images.append(rid)
                continue
            if loc == "r" and not raw:
                walk(ch, _run_marks(ch, char_styles, style_marks, pmarks) or marks)
                continue
            if loc == "hyperlink":
                mark = len(segments)
                walk(ch, marks)
                if raw:
                    inner = "".join(t for _m, t in segments[mark:])
                else:
                    inner = _render_runs(_collapse_prose(segments[mark:])).strip()
                del segments[mark:]
                url = _attr(ch, "id") and links.get(_attr(ch, "id"), "") or ""
                if raw:
                    if inner:
                        segments.append(((), inner))
                elif inner and url.startswith(("http://", "https://")):
                    segments.append(((), "[%s](%s)" % (inner, url)))
                elif inner:
                    segments.append(((), inner))
                continue
            if loc == "t":
                if ch.text:
                    segments.append((marks, ch.text if (raw or "code" in marks)
                                     else _esc(ch.text, line)))
                continue
            if loc in _BREAK_LOCALS:
                # Inside a fence these are the program's own layout: a tab is an
                # indent and a soft break is the next LINE, not a space that welds
                # two statements into one.
                segments.append((marks, ("\t" if loc == "tab" else "\n") if raw
                                 else " "))
                continue
            walk(ch, marks)
    walk(p, ())
    if raw:
        # rstrip only: leading indentation is the content, trailing blanks are not
        # (and markdown_to_text rstrips fenced lines anyway, so keeping them would
        # only move the stored bytes away from the text layer).
        return "".join(t for _m, t in segments).rstrip()
    return _render_runs(_collapse_prose(segments)).strip()


def _docx_cell_text(tc, links, boxes, images=None, sty=None):
    # type: (object, dict, list, object, object) -> str
    """One table cell as a single GFM-safe cell text; inner paragraphs join with
    ``<br>``; NESTED table rows flatten into the cell (escaped pipes) so their
    tokens are never lost. Paragraphs wrapped in content controls (w:sdt) are
    found through the wrappers. Picture rIds inside the cell go to ``images`` (when
    given) for a sentinel emitted after the table -- never inside a pipe row."""
    chunks = []  # type: list
    content = []  # type: list
    _find_locals(tc, ("p", "tbl"), content, stop=("tcPr",))
    for el in content:
        if _local(el.tag) == "p":
            t = _docx_p_text(el, links, boxes, images, sty, pmarks=_p_marks(el, sty))
            if t:
                chunks.append(_md_cell(t))
        else:
            for row in _docx_table_rows_md(el, links, boxes, images, sty):
                flat = " ".join(c for c in row if c)
                if flat:
                    chunks.append(flat)
    return "<br>".join(chunks)


def _row_grid_pad(tr):
    # type: (object) -> tuple
    """``(before, after)`` — grid columns this row does not occupy.

    ``w:trPr/w:gridBefore`` says the row starts at grid column N, not 0 (an indented
    continuation row in a register map); ``w:gridAfter`` says it stops short of the
    right edge. Reading the cells and ignoring these shifts every value one column
    LEFT, which is how a register named ``0x04`` published ``RO`` as its offset —
    a plausible table, wrong in every row, with nothing in the report to say so.

    ``w:trPrChange`` holds the row properties a tracked change REPLACED and is never
    entered, for the same reason ``w:pPrChange`` is not."""
    before = after = 0
    for ch in tr:
        if _local(ch.tag) != "trPr":
            continue
        for pr in ch:
            loc = _local(pr.tag)
            if loc == "trPrChange":
                continue
            if loc == "gridBefore":
                before = max(before, _num_val(_attr(pr, "val"), 0))
            elif loc == "gridAfter":
                after = max(after, _num_val(_attr(pr, "val"), 0))
    return before, after


def _docx_table_rows_md(tbl, links, boxes, images=None, sty=None):
    # type: (object, dict, list, object, object) -> list
    """Rows of cell texts for one w:tbl, honoring gridSpan column geometry,
    gridBefore/gridAfter row offsets AND vMerge forward-fill. Rows/cells wrapped in
    w:sdt (repeating-section controls) are found through the wrappers; nested tables
    stay inside their owning cell.

    GFM tables have no rowspan, so a vertical merge (``w:vMerge``) is flattened by
    REPEATING the restart cell's value down its continuation rows — the
    continuation cells are empty in OOXML, so this adds no tokens the source lacks
    (recall stays 1.0) and makes each row self-contained for row-wise chunking.
    Only true merge-continuations are filled: an ordinary empty cell stays empty,
    and a (malformed) continuation carrying its own text keeps it, never dropped.

    ``w:tcPrChange`` is a stop for the geometry scans for the same reason
    ``w:trPrChange`` is skipped above: it holds the cell properties a tracked change
    REPLACED, so reading through it resurrects the grid the author already edited
    away and shifts every value in the row into the wrong column."""
    rows = []
    trs = []  # type: list
    _find_locals(tbl, ("tr",), trs, stop=("tc", "p", "tblPr", "tblGrid"))
    fill = {}  # type: dict  # grid-column index -> restart value for an active vMerge
    for tr in trs:
        tcs = []  # type: list
        _find_locals(tr, ("tc",), tcs, stop=("p", "tbl", "trPr"))
        before, after = _row_grid_pad(tr)
        cells = [""] * before                  # type: list  # skipped grid columns
        col = before
        for tc in tcs:
            span = 1
            spans = []  # type: list
            _find_locals(tc, ("gridSpan",), spans, stop=("p", "tbl", "tcPrChange"))
            if spans:
                try:
                    span = max(1, int(_attr(spans[0], "val")))
                except ValueError:
                    span = 1
            vmerges = []  # type: list
            _find_locals(tc, ("vMerge",), vmerges, stop=("p", "tbl", "tcPrChange"))
            text = _docx_cell_text(tc, links, boxes, images, sty)
            if vmerges and _attr(vmerges[0], "val") != "restart":
                if not text:                       # continuation: repeat the value above
                    text = fill.get(col, "")
            elif vmerges:
                fill[col] = text                   # restart: source of the fill below
            else:
                fill.pop(col, None)                # plain cell: no active vertical merge
            cells.append(text)
            cells.extend([""] * (span - 1))
            col += span
        cells.extend([""] * after)
        if cells:
            rows.append(cells)
    return rows


def _lift_box(box, styles, numbering, links, blocks, img, lst, sty, snum, pad):
    # type: (object, dict, dict, dict, list, object, dict, object, dict, str) -> None
    """Render one text box's content as its own blocks, indented into the list item
    that anchors it whenever that is expressible.

    A text box HAS to be lifted out of its anchor paragraph — a pipe table and a
    fenced block cannot live inside a sentence. Emitted at column 0 it also closed
    the enclosing list, splitting a procedure in two around a callout. Indenting it
    to the item's content column makes it a list-item continuation and the steps
    keep counting.

    Two block kinds refuse the indent and are still emitted at column 0. A LIST ITEM,
    because its nesting depth is one of the facts the structural ground truth
    compares, and indenting it would report a level the source never had. A CODE
    paragraph, because ``_join_blocks`` wraps the fused run in a fence written at
    column 0, so indenting only the contents would break the block. Either way the
    anchor is counted in ``lifted_text_boxes``, and the numbering counters survive
    the split, so the item after the box carries its true number."""
    sub = []  # type: list
    # A fresh column stack and a fresh set of open lists: the box's own structure is
    # its own. The COUNTERS are shared, because Word does not restart a procedure
    # because a callout was anchored inside it.
    inner = {"cols": [], "counts": lst["counts"], "open": {}}
    _docx_blocks(box, styles, numbering, links, sub, img, inner, sty, snum)
    if pad and not any(k in ("li", "lib", "code") for k, _t in sub):
        for kind, text in sub:
            blocks.append((kind, "\n".join(pad + ln for ln in text.split("\n"))))
        return
    _close_list(lst)
    blocks.extend(sub)


def _docx_blocks(el, styles, numbering, links, blocks, img=None, lst=None, sty=None,
                 snum=None):
    # type: (object, dict, dict, dict, list, object, dict, object, dict) -> None
    """Walk any element emitting (kind, markdown) blocks for each w:p / w:tbl.

    Recurses through wrappers (w:sdt content controls, bookmarks) so TOC fields
    and content-control bodies are never silently skipped. ``img`` is ``None``
    (legacy: no image emission, byte-identical) or ``{"rels": {rId: media_part}}``,
    in which case each embedded picture emits an ``("img", sentinel)`` block at its
    position (a paragraph's images right after its text; a table's after the table).

    ``lst`` carries the open list's ancestor content columns across the recursion (see
    ``_list_indent``); any non-list block closes the list, exactly as a blank line does
    in the rendered markdown, so the next item restarts at column 0. The columns are
    keyed on w:ilvl ALONE, never on the numbering instance: Word gives a bullet
    sub-list inside a numbered procedure its own w:numId, and discarding the ancestor
    columns on that change unparents the sub-list — the notes stop belonging to the
    step they were written under. An item deeper than anything open is already
    CONTAINED by ``_list_indent`` — it costs one step of depth, not the level it
    names — which is the real defence against a list that starts at ilvl 2 with no
    parent.

    ``sty`` is the style context from ``_docx_style_ctx``. Its ``para``/``char``
    code-style sets make a paragraph in a code style a ``("code", ...)`` block —
    which ``_join_blocks`` fuses with its neighbours into one fenced block — and a
    run in one an inline code span; ``marks`` and ``levels`` resolve the emphasis a
    style carries (see ``_p_marks``).

    ``snum`` is ``{styleId: (numId, ilvl)}`` from ``_docx_style_numbering`` — the
    numbering a paragraph inherits from its STYLE when its own w:pPr carries none.
    An explicit ``w:numId 0`` still means "not a list" and never inherits."""
    rels = img["rels"] if img is not None else None
    if lst is None:
        lst = {"cols": [], "counts": {}, "open": {}}
    if sty is None:
        sty = _EMPTY_STYLE_CTX
    if snum is None:
        snum = {}
    cols = lst["cols"]
    for ch in el:
        loc = _local(ch.tag)
        if loc in _SKIP_LOCALS:
            continue
        if loc == "p":
            boxes = []  # type: list
            images = [] if rels is not None else None
            sid, num_id, ilvl, outline = _p_style_info(ch)
            if not num_id:
                inherited = snum.get(sid)
                if inherited:
                    num_id = inherited[0]
                    if not ilvl:
                        ilvl = inherited[1]
            is_code = sid in sty["para"]
            level = None if is_code else _heading_level(sid, outline, styles)
            text = _docx_p_text(ch, links, boxes, images, sty, raw=is_code,
                                pmarks=_p_marks(ch, sty))
            item_pad = ""            # the content column an anchored figure continues
            if is_code:
                # Emitted even when EMPTY: a blank line inside a shell transcript is
                # part of the transcript, and _join_blocks keeps the ones that fall
                # between two code lines (and drops the ones that do not, which is
                # what the structural ground truth counts).
                _close_list(lst)
                blocks.append(("code", text))
            elif text:
                if level:
                    _close_list(lst)
                    blocks.append(("h", "#" * min(level, 6) + " "
                                    + _esc_heading(_esc_lead(text))))
                elif num_id and num_id != "0":
                    fmt, start = numbering.get((num_id, ilvl or "0"), ("bullet", 1))
                    depth = _num_val(ilvl, 0)
                    if fmt == "bullet":
                        marker = "-"
                        restart = False
                        _close_ordered(lst["open"], depth)
                    else:
                        number = _list_number(lst["counts"], num_id, depth, start)
                        marker = _ordered_marker(lst["open"], depth, number)
                        restart = number != 1
                    pad = _list_indent(cols, depth, marker)
                    # A NESTED item whose number is not 1 may not be glued to its
                    # parent's text: CommonMark lets a list interrupt a paragraph only
                    # when an ordered marker reads 1, so "5." written straight under
                    # step 4 is swallowed as step 4's prose. One blank line closes that
                    # paragraph and the sub-list survives.
                    blocks.append((("lib" if (pad and restart) else "li"),
                                   pad + marker + " " + _esc_lead(text)))
                    item_pad = " " * cols[-1][1]
                else:
                    _close_list(lst)
                    blocks.append(("p", _esc_lead(text)))
            if images:
                img_blocks = []  # type: list
                _emit_image_blocks(images, rels, img_blocks)
                # The picture sits INSIDE this step. Indented to the step's
                # content column it is a list-item continuation, so the steps below
                # keep counting; emitted at column 0 it closes the list, which is
                # how a screenshot in a runbook renumbered every step after it back
                # to 1.
                #
                # `item_pad` is set when the picture shares the step's own paragraph.
                # A picture in a paragraph of its OWN — which is how a Word document
                # actually carries a screenshot between a step and its sub-step — has
                # no `item_pad`, and went out at column 0: measured, `list_items`
                # {0: 1, 1: 1} became {0: 2} with every token still present, so the
                # structure gate FAILED a faithful conversion of an ordinary runbook
                # and the document refused to publish. An open list is an open list
                # however the picture is anchored.
                pad = item_pad or (" " * cols[-1][1] if cols else "")
                if pad:
                    blocks.extend([(k, pad + t) for k, t in img_blocks])
                else:
                    blocks.extend(img_blocks)
                    _close_list(lst)          # a column-0 sentinel closes the list
            for box in boxes:
                _lift_box(box, styles, numbering, links, blocks, img, lst, sty,
                          snum, item_pad)
        elif loc == "tbl":
            # A 1x1 table is Word LAYOUT scaffolding (a framed section), not data:
            # unwrap the lone cell into normal body blocks instead of emitting a
            # giant one-row pipe table.
            trs = []  # type: list
            _find_locals(ch, ("tr",), trs, stop=("tc", "p", "tblPr", "tblGrid"))
            single = None
            if len(trs) == 1:
                tcs = []  # type: list
                _find_locals(trs[0], ("tc",), tcs, stop=("p", "tbl", "trPr"))
                if len(tcs) == 1:
                    single = tcs[0]
            if single is not None:
                _docx_blocks(single, styles, numbering, links, blocks, img, lst, sty,
                             snum)
                continue
            boxes = []
            images = [] if rels is not None else None
            rows_md = _docx_table_rows_md(ch, links, boxes, images, sty)
            # The row lists are already span-padded, so their length IS the
            # declared grid width; passing it stops a lone spanning cell from
            # trimming the table back to one column.
            table = _gfm_table(rows_md,
                               min_width=max([len(r) for r in rows_md] or [0]))
            if table:
                _close_list(lst)
                blocks.append(("table", table))
            if images:
                _emit_image_blocks(images, rels, blocks)
                _close_list(lst)
            for box in boxes:
                _lift_box(box, styles, numbering, links, blocks, img, lst, sty,
                          snum, "")
        else:
            _docx_blocks(ch, styles, numbering, links, blocks, img, lst, sty, snum)


def _docx_notes_section(xml, title):
    # type: (str, str) -> str
    """foot/endnotes/comments part -> a ``## <title>`` section, one item per
    note/comment (separator stubs carry no text and drop out)."""
    root = _root(xml)
    if root is None:
        return ""
    items = []
    for note in root:
        t = _esc_block_start(_text_of(note, value_locals=("t", "text")))
        if t:
            items.append("- " + t)
    if not items:
        return ""
    return "## " + title + "\n\n" + "\n".join(items)


_PPTX_COMMENTS = re.compile(r"^ppt/comments/[^/]+\.xml$")


def _comments_items(xml):
    # type: (str) -> list
    """Comment texts from a comments part (docx w:comment, pptx legacy p:cm with
    p:text, pptx modern one-comment-per-part), one item per comment."""
    root = _root(xml)
    if root is None:
        return []
    cms = []  # type: list
    _find_locals(root, ("comment", "cm"), cms)
    items = []
    for cm in (cms or [root]):
        t = _text_of(cm, value_locals=("t", "text"))
        if t:
            items.append(t)
    return items


def docx_markdown(parts, emit_images=False):
    # type: (dict, bool) -> str
    """Deterministic full markdown of a docx from its OOXML parts.

    Structure comes straight from the tags: heading styles -> ``#``, numbering ->
    lists, w:tbl -> GFM pipe tables (gridSpan-aware), text boxes as their own
    blocks, hyperlinks as links, foot/endnotes and embedded chart/SmartArt text
    as trailing sections. Returns ``""`` when word/document.xml is missing or
    malformed. With ``emit_images`` each body picture emits a positional
    ``<!-- ooxml-image:PART -->`` sentinel (default off = byte-identical legacy)."""
    root = _root(parts.get("word/document.xml", ""))
    if root is None:
        return ""
    sty = _docx_style_ctx(parts.get("word/styles.xml", ""))
    styles = sty["levels"]
    numbering = _docx_numbering(parts.get("word/numbering.xml", ""))
    links = _rels_targets(parts.get("word/_rels/document.xml.rels", ""))
    img = None
    if emit_images:
        img = {"rels": _image_rels(parts.get("word/_rels/document.xml.rels", ""),
                                   "word/document.xml")}
    snum = _docx_style_numbering(parts.get("word/styles.xml", ""))
    blocks = []  # type: list
    _docx_blocks(root, styles, numbering, links, blocks, img, None, sty, snum)
    for part, title in (("word/footnotes.xml", "Footnotes"),
                        ("word/endnotes.xml", "Endnotes"),
                        ("word/comments.xml", "Comments")):
        section = _docx_notes_section(parts.get(part, ""), title)
        if section:
            blocks.append(("section", section))
    blocks.extend(_embedded_sections(parts, (
        (_DOCX_DIAGRAM, "Diagrams", _diagram_list_md),
        (_DOCX_CHART, "Charts", lambda x: _esc_block_start(_chart_text(x))))))
    md = _join_blocks(blocks)
    return md + "\n" if md else ""


# `docx_source_text` used to live here, and its docstring claimed to be
# "independent of the converter's structural walk". It was not independent of the
# converter's TEXT reader: it called `_text_of`, which the converter also reaches
# through `_docx_p_text`, so bugging `_collect_text` under it drove the source token
# count from 400 to 0 while the gate held at `recall: 1.0, valid: true`. It now lives
# in `_ooxml_struct` beside the structural facts, built from that module's own
# walkers — see its docstring for the measurement.


# Page furniture at PART granularity: word header/footer parts are the one
# unambiguous case (see _media.py, which uses the same rule to classify chrome
# images). pptx footer/slide-number placeholders are placeholder-SCOPED — they live
# inside `ppt/slides/slideN.xml`, a fully converted part — so this reader could
# never see them, and claiming them here would have been a guess. They are counted
# instead by `pptx_policy_drops`'s `dropped_slide_chrome`, which reads the role off
# `p:ph/@type` where the schema puts it. That the drop needed a warning at all is
# the measured point: both halves of the token gate exclude the same shapes, so 59
# characters of banner left `n_source` and `recall` exactly where they were.
_HEADER_FOOTER_PART = re.compile(r"^word/(header|footer)\d*\.xml$")


def furniture_drops(parts):
    # type: (dict) -> list
    """Named, MEASURED warnings for text-bearing page furniture dropped by policy.

    ``docs/end-goal.md`` §1 permits dropping running headers/footers — repeated onto
    every page, they would pollute the markdown and any RAG index built on it — but
    only when the drop is "deliberate and visible, never an accident". The converter
    simply never walks these parts, so without this a dropped ``Confidential`` banner
    is indistinguishable from a document that never had one: the report said
    ``warnings: []``. ``output-contract.md`` has named the ``dropped_headers_footers``
    code all along; nothing emitted it.

    ``parts`` is ``{part_name: xml}`` holding furniture parts only — the converter's
    own ``parts`` mapping never contains them. Text is measured with the same
    ``_text_of`` walk the converter-blind ground truth uses, so ``chars`` is
    comparable to ``content.chars``. A header with no text dropped nothing and is not
    reported; the warning never degrades ``status``, because a policy drop is not a
    defect."""
    names, chars = [], 0
    for name in sorted(parts):
        if not _HEADER_FOOTER_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        text = _text_of(root, value_locals=("t", "text"))
        if text:
            names.append(name.split("/")[-1])
            chars += len(text)
    if not names:
        return []
    return [{"code": "dropped_headers_footers",
             "detail": "%d text-bearing part(s) dropped as page furniture (%s); "
                       "%d char(s)" % (len(names), ", ".join(names), chars),
             "parts": len(names), "chars": chars}]


# --------------------------------------------------------------------------- pptx

_SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES_PART = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_PPTX_DIAGRAM = re.compile(r"^ppt/diagrams/data\d+\.xml$")
_PPTX_CHART = re.compile(r"^ppt/charts/chart(?:Ex)?\d+\.xml$")
# Placeholder types that are page chrome, not content (shared with ground truth).
_CHROME_PH = ("sldNum", "dt", "ftr")


# A relationship Target is a URI reference relative to the part that HOLDS it, and
# real producers write every one of these forms. Same shape as `_SHEET_TARGET` on
# the workbook side, and same reason: a form this misses reads as a dangling id, so
# the deck silently drops back to filename order.
_REL_PREFIX = re.compile(r"^(?:/ppt/|ppt/|\.\./|\./)+")


def _rel_id(el):
    # type: (object) -> str
    """The RELATIONSHIP id of an element that also carries a plain ``id``.

    ``p:sldId`` has both, and they mean different things: ``id`` is the slide's own
    identifier within the deck, ``r:id`` points at the relationship that names its
    part. ``_attr`` matches on LOCAL name alone, so it returns whichever comes first
    in document order — which is the slide id, every time. Reading that one resolved
    no relationship at all, and the deck fell back to filename order with nothing
    anywhere reporting a problem: the whole of this fix, silently doing nothing.

    A relationship reference is namespace-qualified by definition (the `r:` prefix
    binds the relationships namespace), so requiring the qualification is what tells
    the two attributes apart. The workbook side never met this because ``xl:sheet``
    spells its other attributes ``name`` and ``sheetId``."""
    for key, value in el.attrib.items():
        if key.startswith("{") and key.rsplit("}", 1)[-1] == "id":
            return value
    return ""


def pptx_slide_order(parts):
    # type: (dict) -> list
    """Slide part names in the DECK's own order, from ``ppt/presentation.xml``.

    Returns ``[]`` when the presentation cannot say — no part, an unparseable one,
    no ``p:sldIdLst``, or nothing in it that resolves — and an empty answer is the
    honest one: the caller falls back to numeric part order, which is what this lane
    did for every deck before this existed.

    WHY THIS PART IS READ AT ALL. PowerPoint does not renumber slide parts when a
    user drags a slide; it rewrites ``p:sldIdLst`` and leaves ``slideN.xml`` where it
    was. Sorting part names therefore reads the order the slides were CREATED in, not
    the order they are shown in, and the two disagree for any deck anyone has
    reordered. Measured on the shipped corpus deck: swapping two entries in
    ``sldIdLst`` left the markdown byte-identical, because this part was not even in
    ``OOXML_MAIN_PARTS`` and ``p:sldIdLst`` had no reader anywhere in this module.

    Slide-ness is decided in ONE place — ``_SLIDE_PART``, the same predicate the
    notes binding and the rels lookup use. A target resolving to anything else is
    ignored rather than half-supported: ordering a part the rest of the lane does not
    recognise as a slide would put it in the sequence and nowhere else.

    POSTCONDITION, because a caller depends on it: every name returned matches
    ``_SLIDE_PART`` and is a key of ``parts``. ``pptx_markdown`` reads the slide's
    PART number straight back out with ``int(_SLIDE_PART.match(name).group(1))``, and
    a name that did not match would be an ``AttributeError`` on ``None`` in the middle
    of a batch rather than a graceful degradation."""
    root = _root(parts.get("ppt/presentation.xml", ""))
    if root is None:
        return []
    rels = {}
    rels_root = _root(parts.get("ppt/_rels/presentation.xml.rels", ""))
    if rels_root is not None:
        for rel in rels_root:
            if _local(rel.tag) == "Relationship":
                rels[rel.attrib.get("Id", "")] = rel.attrib.get("Target", "")
    order = []
    seen = set()
    ids = []  # type: list
    _find_locals(root, ("sldId",), ids)
    for el in ids:
        rid = _rel_id(el)
        if not rid:
            continue          # a sldId with no r:id names no part; it cannot be placed
        name = "ppt/" + _REL_PREFIX.sub("", rels.get(rid, ""))
        # A part listed twice is a malformed deck; publishing the slide twice would
        # double every one of its tokens against a ground truth that read it once.
        if _SLIDE_PART.match(name) and name in parts and name not in seen:
            seen.add(name)
            order.append(name)
    return order


def _sp_ph_type(sp):
    # type: (object) -> str
    phs = []  # type: list
    _find_locals(sp, ("ph",), phs, stop=("txBody",))
    return _attr(phs[0], "type") if phs else ""   # _attr already matches the unprefixed attr


def _is_chrome_sp(el):
    # type: (object) -> bool
    return _local(el.tag) == "sp" and _sp_ph_type(el) in _CHROME_PH


def _pptx_grid_width(tbl):
    # type: (object) -> int
    """How many columns this table DECLARES, from its own ``a:tblGrid``.

    DrawingML spells a merge as ATTRIBUTES on ``a:tc``: the origin carries
    ``gridSpan``/``rowSpan`` and every position it covers is still present, marked
    ``hMerge``/``vMerge`` and EMPTY. GFM has no colspan, so rendering the covered
    cell empty is right — but an always-empty TRAILING column is exactly what
    ``_gfm_table`` trims as styled-but-valueless, and trimming it loses a column the
    deck states it has. Measured: a two-column table whose header spanned both
    published as ONE column, at `recall: 1.0` with well-formed GFM and no warning,
    because the covered cell carries no token for anything else to miss.

    Only this table's OWN grid counts. A table nested in a cell declares a grid too,
    and its rows are flattened into the owning cell rather than becoming columns."""
    n = 0
    for grid in tbl:
        if _local(grid.tag) != "tblGrid":
            continue
        for col in grid:
            if _local(col.tag) == "gridCol":
                n += 1
    return n


def _pptx_table_md(tbl):
    # type: (object) -> str
    rows = []
    trs = []  # type: list
    _find_locals(tbl, ("tr",), trs, stop=("tc",))
    for tr in trs:
        cells = []
        tcs = []  # type: list
        _find_locals(tr, ("tc",), tcs)
        for tc in tcs:
            cells.append(_md_cell(_esc(_text_of(tc))))
        if cells:
            rows.append(cells)
    return _gfm_table(rows, min_width=_pptx_grid_width(tbl))


# DrawingML's run properties. A deck spells these as ATTRIBUTES of `a:rPr` — not as
# child elements the way WordprocessingML does — which is why the deck lane needs its
# own reader rather than the docx walk with different tag names.
_DML_OFF = ("", "0", "false", "off", "none", "nostrike")


def _dml_marks(rpr):
    # type: (object) -> tuple
    """The emphasis one ``a:rPr`` declares, in `_MARK_ORDER`.

    Every OFF spelling a deck really writes is honoured: `b="0"`, `i="false"` and
    `strike="noStrike"` are NOT emphasis, and emitting markers for them would invent
    emphasis the slide does not draw — which fails a faithful conversion exactly as
    surely as dropping the emphasis it does."""
    if rpr is None:
        return ()
    marks = []
    for attr, mark in (("b", "strong"), ("i", "em"), ("strike", "strike")):
        if _attr(rpr, attr).lower() not in _DML_OFF:
            marks.append(mark)
    return tuple(m for m in _MARK_ORDER if m in marks)


def _dml_link(rpr, links):
    # type: (object, dict) -> str
    """The external URL one run's ``a:hlinkClick`` points at, or ``""``.

    An INTERNAL jump — `action="ppaction://hlinksldjump"`, or a relationship whose
    target is another slide part rather than a URL — resolves to nothing here on
    purpose. `[text]()` is a dead link in the stored bytes, and a slide-to-slide
    jump has no address a reader outside the deck could follow. The docx lane makes
    the same call for an internal `w:hyperlink`: keep the text, drop the address."""
    if rpr is None or not links:
        return ""
    for ch in rpr:
        if _local(ch.tag) not in ("hlinkClick", "hlinkHover"):
            continue
        url = links.get(_attr(ch, "id") or "", "")
        if url.startswith(("http://", "https://", "mailto:")):
            return url
    return ""


def _collapse_segments(segments):
    # type: (list) -> list
    """`_WS.sub(" ", ...)` + `.strip()` over the JOIN, applied per segment.

    The whitespace collapse has to run across segment boundaries or the new reader
    stops being byte-identical to the flat `_text_of` walk it replaces: two runs
    reading ``"clock "`` and ``" tree"`` are one space in the render, not two, and a
    deck splits text at every property boundary. Collapsing each segment on its own
    would leave the pair as ``"clock  tree"`` and move eight shipped documents."""
    out = []  # type: list
    prev_space = True                       # leading whitespace is stripped
    for marks, url, text in segments:
        buf = []  # type: list
        for ch in text:
            if ch.isspace():
                if not prev_space:
                    buf.append(" ")
                prev_space = True
            else:
                buf.append(ch)
                prev_space = False
        out.append((marks, url, "".join(buf)))
    # Trailing: strip the last space anywhere at the end of the segment list.
    for i in range(len(out) - 1, -1, -1):
        marks, url, text = out[i]
        stripped = text.rstrip()
        if stripped != text:
            out[i] = (marks, url, stripped)
        if stripped:
            break
    return [seg for seg in out if seg[2]]


def _pptx_para_md(p, links):
    # type: (object, dict) -> str
    """One DrawingML paragraph as inline markdown: emphasis, links, escaping.

    The RENDERING is the docx lane's — `_render_runs` already owns CommonMark's
    flanking rules, the coalescing that stops a word split across runs from emitting
    ``**Dma****Arbiter**``, and the delimiter cases like ``**~~x~~**``. Only the
    READER is new, because DrawingML states a run's properties as attributes of
    `a:rPr` and puts a hyperlink INSIDE them rather than around a span of runs.

    A linked run is rendered to its own markdown first and then emitted as one
    unmarked segment, so emphasis inside a link survives and the link's brackets are
    never themselves escaped or re-marked."""
    segments = []  # type: list

    def walk(el, marks, url):
        for ch in el:
            loc = _local(ch.tag)
            if loc in _SKIP_LOCALS or loc == "pPr":
                continue
            if loc in ("r", "fld"):
                rpr = None
                for sub in ch:
                    if _local(sub.tag) == "rPr":
                        rpr = sub
                        break
                walk(ch, _dml_marks(rpr) or marks, _dml_link(rpr, links) or url)
                continue
            if loc == "rPr":
                continue
            if loc == "t":
                if ch.text:
                    segments.append((marks, url, ch.text))
                continue
            if loc in _BREAK_LOCALS:
                segments.append((marks, url, " "))
                continue
            walk(ch, marks, url)

    walk(p, (), "")
    segments = _collapse_segments(segments)
    if not segments:
        return ""
    out = []  # type: list
    i = 0
    while i < len(segments):
        url = segments[i][1]
        j = i
        while j < len(segments) and segments[j][1] == url:
            j += 1
        run = [(m, _esc(t)) for m, _u, t in segments[i:j]]
        text = _render_runs(run)
        out.append("[%s](%s)" % (text.strip(), url) if url else text)
        i = j
    return "".join(out).strip()


# THE BULLET CASCADE. A DrawingML paragraph inherits its bullet from, nearest first:
# its own `a:pPr`, the shape's `a:txBody/a:lstStyle/a:lvlNpPr`, the slide LAYOUT's
# matching placeholder `a:lstStyle`, and the slide MASTER's `p:txStyles/p:bodyStyle`.
# Reading only the first says "no numbering" over a deck whose entire body list is
# numbered from the master — which is exactly why `ordered_items` was `unmeasured`
# rather than zero until this resolved the whole chain.
_BU_LOCALS = ("buAutoNum", "buChar", "buNone")
_LEVEL_PR = re.compile(r"^lvl([1-9])pPr$")


def _bu_of(ppr):
    # type: (object) -> object
    """The bullet declaration a `a:pPr`-shaped element makes, or ``None``.

    Returns the element itself so the caller can read `@type`/`@startAt`; `buNone`
    is returned like any other, because "explicitly no bullet" is a DECISION that
    must beat an inherited number rather than read as "said nothing"."""
    if ppr is None:
        return None
    for ch in ppr:
        if _local(ch.tag) in _BU_LOCALS:
            return ch
    return None


def _lst_style_levels(el):
    # type: (object) -> dict
    """``{level: bullet element}`` from an `a:lstStyle`'s `a:lvlNpPr` children.

    `a:lvl1pPr` is outline level 0 — the file numbers levels from one and every
    other reader here numbers them from zero, and getting that off by one silently
    applies level 2's bullet to level 1."""
    out = {}
    if el is None:
        return out
    for ch in el:
        m = _LEVEL_PR.match(_local(ch.tag))
        if not m:
            continue
        bu = _bu_of(ch)
        if bu is not None:
            out[int(m.group(1)) - 1] = bu
    return out


def _first_local(el, want):
    # type: (object, str) -> object
    for sub in el.iter():
        if _local(sub.tag) == want:
            return sub
    return None


def _pptx_inherited_bullets(parts, slide_part):
    # type: (dict, str) -> dict
    """``{(ph type, ph idx): {level: bullet}}`` for one slide's layout and master.

    Walked slide -> layout -> master by RELATIONSHIP, never by filename: a deck with
    two masters resolves `slideLayout3` to whichever master ITS rels name, and
    guessing `slideMaster1` would apply another theme's numbering. The master's
    `p:bodyStyle` is stored under the key `(None, None)` because it applies to every
    body placeholder whatever its index."""
    out = {}
    layout_rels = _rels_all(parts, _rels_of(slide_part))
    for target in layout_rels.values():
        name = _pptx_part(target)
        if not name.startswith("ppt/slideLayouts/"):
            continue
        root = _root(parts.get(name, ""))
        if root is not None:
            for sp in root.iter():
                if _local(sp.tag) != "sp":
                    continue
                ph = _first_local(sp, "ph")
                if ph is None:
                    continue
                levels = _lst_style_levels(_first_local(sp, "lstStyle"))
                if levels:
                    out[(_attr(ph, "type") or "body", _attr(ph, "idx"))] = levels
        for mtarget in _rels_all(parts, _rels_of(name)).values():
            mname = _pptx_part(mtarget)
            if not mname.startswith("ppt/slideMasters/"):
                continue
            mroot = _root(parts.get(mname, ""))
            if mroot is None:
                continue
            for styles in mroot.iter():
                if _local(styles.tag) != "bodyStyle":
                    continue
                levels = _lst_style_levels(styles)
                if levels:
                    out.setdefault((None, None), levels)
    return out


def _rels_of(part):
    # type: (str) -> str
    """The `.rels` part carrying one part's relationships."""
    head, _sep, tail = part.rpartition("/")
    return "%s/_rels/%s.rels" % (head, tail)


def _rels_all(parts, rels_part):
    # type: (dict, str) -> dict
    """``{Id: Target}`` for EVERY relationship, external or not.

    `_rels_targets` keeps only `TargetMode="External"` because its caller wants
    hyperlinks; a layout and a master are internal parts, so they need the other
    half of the same file."""
    root = _root(parts.get(rels_part, ""))
    out = {}
    if root is None:
        return out
    for rel in root:
        if _local(rel.tag) != "Relationship":
            continue
        rid, target = rel.attrib.get("Id", ""), rel.attrib.get("Target", "")
        if rid and target:
            out[rid] = target
    return out


def _pptx_part(target):
    # type: (str) -> str
    """A relationship target as a package part name (`../slideLayouts/x.xml` ->
    `ppt/slideLayouts/x.xml`); "" for an external one."""
    if not target or "://" in target:
        return ""
    return "ppt/" + _REL_HOPS.sub("", target)


_REL_HOPS = re.compile(r"^(?:/?ppt/|\.\./|\./)+")


def _pptx_txbody_paras(container, links=None, inherited=None):
    # type: (object, dict, dict) -> list
    """(indent_level, inline markdown) per paragraph of every txBody under
    ``container`` (or of ``container`` itself when it IS a txBody).

    The text arrives already inline-escaped and marked; callers apply only the
    LINE-LEADING escape, because escaping again would eat the markers this reader
    just emitted."""
    out = []
    if _local(container.tag) == "txBody":
        bodies = [container]
    else:
        bodies = []  # type: list
        _find_locals(container, ("txBody",), bodies)
    for tx in bodies:
        levels = _lst_style_levels(_first_local(tx, "lstStyle"))
        ph = _ph_of(tx, container)
        for p in tx:
            if _local(p.tag) != "p":
                continue
            lvl = 0
            own = None
            for pr in p:
                if _local(pr.tag) == "pPr":
                    lvl = _num_val(_attr(pr, "lvl"), 0)
                    own = _bu_of(pr)
            t = _pptx_para_md(p, links or {})
            if t:
                out.append((lvl, t, _resolve_bullet(own, levels, lvl, ph, inherited)))
    return out


def _ph_of(tx, container):
    # type: (object, object) -> tuple
    """The placeholder a txBody belongs to, as ``(type, idx)``.

    Read from the SHAPE, not the txBody: `p:ph` lives in `p:nvSpPr/p:nvPr`, a
    sibling of `p:txBody`, which is why the shape has to be passed in."""
    el = container if _local(container.tag) != "txBody" else None
    if el is None:
        return ("body", None)
    ph = _first_local(el, "ph")
    if ph is None:
        return ("body", None)
    return (_attr(ph, "type") or "body", _attr(ph, "idx"))


def _resolve_bullet(own, shape_levels, lvl, ph, inherited):
    # type: (object, dict, int, tuple, dict) -> object
    """This paragraph's effective bullet, nearest declaration first.

    Own `a:pPr` beats the shape's `a:lstStyle`, which beats the layout placeholder's,
    which beats the master's `p:bodyStyle`. `a:buNone` is a DECLARATION and wins at
    whatever level states it: a paragraph the slide draws plain must not inherit a
    number from the master."""
    if own is not None:
        return own
    if lvl in shape_levels:
        return shape_levels[lvl]
    if inherited:
        for key in (ph, (ph[0], None), (None, None)):
            levels = inherited.get(key)
            if levels and lvl in levels:
                return levels[lvl]
    return None


def _list_pad(cols):
    # type: (list) -> str
    """The column an open list item's continuation lines sit at, "" when none is."""
    return " " * cols[-1][1] if cols else ""


def _pptx_bullet(blocks, cols, lvl, text, bu=None, nums=None):
    # type: (list, list, int, str, object, list) -> tuple
    """One body paragraph as a list-item block, indented to a depth that EXISTS.

    A deck's outline level is a free integer, so ``"  " * lvl`` can indent an item
    for a nesting nothing opened — and CommonMark nests a child only under a parent
    that is really there. ``_list_indent`` is the containment rule docx grew in P0.1
    (as a stack-height clamp, which P9.6r had to replace — see its docstring); the
    deck path never had it at all, and the three things that cost were all measured:

        levels 0 then 2   an item at markdown depth 1, not 2  (harmless, but the
                          level is gone and nothing said so)
        levels 0 then 3   six columns under a content column of two is a LAZY
                          CONTINUATION: the text is absorbed into the item above,
                          the item stops existing, every token is still present, so
                          `recall` reads 1.0 and the bullet is simply gone
        a slide OPENING at level 2  four columns with no ancestor at all is an
                          indented CODE BLOCK: the `- ` becomes literal text and
                          the deck has grown a listing it never wrote

    ``cols`` is the ancestor content-column stack. Any other block ends the list, so
    it is reset from ``blocks`` itself rather than by every call site remembering
    to — a call site that forgot would indent onto a list that had already closed."""
    if not blocks or blocks[-1][0] not in ("li", "img"):
        del cols[:]
        if nums is not None:
            del nums[:]
    marker = "-"
    if bu is not None and _local(bu.tag) == "buAutoNum":
        marker = "%d." % _pptx_step(nums, lvl, _num_val(_attr(bu, "startAt"), 1))
    elif nums is not None:
        # An UNORDERED item at this level ends the ordered run it interrupts, so the
        # next numbered item starts again — which is what a renderer shows and what
        # `ordered_numbers` exists to make visible.
        _pptx_step(nums, lvl, 1, reset=True)
    return ("li", _list_indent(cols, lvl, marker) + marker + " "
            + _esc_block_start_md(text))


def _pptx_step(nums, lvl, start, reset=False):
    # type: (list, int, int, bool) -> int
    """The ordinal this item shows, and the bookkeeping the next one needs.

    ``nums`` is one counter per outline LEVEL, held as a list so a deeper level
    restarts whenever a shallower one advances — the reason the first sub-step of
    step 2 is 1 and not 3. ``reset`` records that an unordered item interrupted the
    run at this level without consuming an ordinal."""
    if nums is None:
        return start
    # A deck's outline level is a free integer and a negative one is a level 0 item
    # to every renderer. Without this, `nums[lvl]` indexes from the END of the list
    # and a `lvl="-1"` paragraph crashes the whole conversion.
    lvl = max(0, lvl)
    while len(nums) <= lvl:
        nums.append(None)
    del nums[lvl + 1:]
    if reset:
        nums[lvl] = None
        return start
    nums[lvl] = start if nums[lvl] is None else nums[lvl] + 1
    return nums[lvl]


def _pptx_shape_blocks(el, blocks, title_holder, img=None, cols=None, links=None,
                       inherited=None, nums=None):
    # type: (object, list, list, object, list, dict, dict, list) -> None
    """Walk a slide's shape tree in order: title -> holder, body paragraphs ->
    bullets (PowerPoint's default rendering), a:tbl -> GFM, groups recurse.
    A txBody in any other container (connectors, exotic shapes) still renders,
    so no text-bearing shape type is silently dropped. With ``img`` (a
    ``{"rels": {rId: media_part}}`` dict) each ``p:pic`` emits an image sentinel
    where it sits; ``None`` (default) leaves pictures unrendered as before."""
    rels = img["rels"] if img is not None else None
    if cols is None:
        cols = []
    for ch in el:
        loc = _local(ch.tag)
        if loc in _SKIP_LOCALS:
            continue
        if loc == "sp":
            if _is_chrome_sp(ch):
                continue
            paras = _pptx_txbody_paras(ch, links, inherited)
            if _sp_ph_type(ch) in ("title", "ctrTitle") and not title_holder and paras:
                title_holder.append(" ".join(t for _, t, _b in paras))
            else:
                for lvl, t, bu in paras:
                    blocks.append(_pptx_bullet(blocks, cols, lvl, t, bu, nums))
        elif loc == "graphicFrame":
            tbls = []  # type: list
            _find_locals(ch, ("tbl",), tbls)
            for tbl in tbls:
                table = _pptx_table_md(tbl)
                if table:
                    del cols[:]           # a table really does end the list
                    blocks.append(("table", table))
            # A graphicFrame can also carry an embedded-object / slide-zoom / OLE image
            # (its blip). Charts/tables reference a separate part and carry no inline
            # blip, so this is empty for them. Frequently the ONLY body copy of a picture:
            # modern PowerPoint puts it in the mc:Choice graphicFrame and leaves a <p:pic>
            # in the mc:Fallback we skip -- catching it here keeps that image from vanishing.
            if rels is not None:
                _emit_image_blocks(_blip_rids(ch), rels, blocks, _list_pad(cols))
        elif loc == "txBody":
            for lvl, t, bu in _pptx_txbody_paras(ch, links, inherited):
                blocks.append(_pptx_bullet(blocks, cols, lvl, t, bu, nums))
        elif loc == "pic":
            if rels is not None:
                _emit_image_blocks(_blip_rids(ch), rels, blocks, _list_pad(cols))
        else:
            _pptx_shape_blocks(ch, blocks, title_holder, img, cols, links,
                               inherited, nums)


def _slide_rel_parts(parts, n):
    # type: (dict, int) -> tuple
    """(embedded, notes) part names referenced by slide ``n``'s rels, resolved to
    full part names (``../charts/chart1.xml`` -> ``ppt/charts/chart1.xml``).
    The RELATIONSHIP is the normative slide->notes binding — part numbering is a
    convention that spec-legal packages (and python-pptx) are free to break."""
    rels = _root(parts.get("ppt/slides/_rels/slide%d.xml.rels" % n, ""))
    embedded = []
    notes = []
    if rels is None:
        return embedded, notes
    for rel in rels:
        if _local(rel.tag) != "Relationship":
            continue
        target = rel.attrib.get("Target", "")
        name = target.replace("../", "ppt/", 1).lstrip("./")
        if name in parts:
            if (_PPTX_DIAGRAM.match(name) or _PPTX_CHART.match(name)) \
                    and name not in embedded:
                embedded.append(name)
            elif _NOTES_PART.match(name) and name not in notes:
                notes.append(name)
    return embedded, notes


def _pptx_notes_blocks(parts, name, blocks):
    # type: (dict, str, list) -> None
    """Append a ``### Speaker notes`` section for one notes part — the full
    shape walk (paragraph-per-line, tables, groups), chrome placeholders
    skipped, so the notes render segmented instead of as one flattened wall."""
    root = _root(parts.get(name, ""))
    if root is None:
        return
    body = []  # type: list
    title_holder = []  # type: list
    # A note's links live in the NOTE's own rels part, not the slide's: the two parts
    # number their rIds independently, so reading the slide's here would resolve a
    # note's rId2 to whatever the slide happens to call rId2.
    rels = _rels_targets(parts.get(
        name.replace("ppt/notesSlides/", "ppt/notesSlides/_rels/") + ".rels", ""))
    # Notes inherit from the notesMaster, not the slideMaster, and this lane does
    # not read it: a numbered speaker note is rare and getting it from the wrong
    # master would be worse than not numbering it. The paragraph's own `a:pPr` still
    # counts, which is where a note that really is numbered declares it.
    _pptx_shape_blocks(root, body, title_holder, None, None, rels, None, [])
    if not body and not title_holder:
        return
    blocks.append(("h", "### Speaker notes"))
    if title_holder:
        blocks.append(("p", _esc_lead(title_holder[0])))
    blocks.extend(body)


def pptx_markdown(parts, emit_images=False):
    # type: (dict, bool) -> str
    """Deterministic full markdown of a pptx: one ``## Slide N`` section per slide,
    with title, bulleted body text, GFM tables, SmartArt/chart text, and that
    slide's speaker notes.

    ``N`` is the slide's POSITION IN THE DECK, read from ``p:sldIdLst`` via
    ``pptx_slide_order`` — not the number in its part name, which PowerPoint leaves
    alone when a slide is dragged. A deck that cannot say (no presentation part, or
    nothing in it that resolves) falls back to numeric part order, and ``N`` still
    counts position so the heading means one thing either way.

    A slide part the deck never lists has no position and may not claim one: it
    publishes after the ordered slides under ``## Slide (unlisted): slideN``,
    following the ``## Sheet (unlinked): NAME`` precedent on the workbook side.
    Dropping it instead would take its words out of the markdown while the ground
    truth still counted them, which fails token recall outright.

    Diagram/chart parts never referenced by any slide land in a trailing
    ``## Embedded objects`` section so nothing is orphaned. With ``emit_images``
    each slide picture emits a positional ``<!-- ooxml-image:PART -->`` sentinel
    (default off = byte-identical legacy)."""
    numbered = sorted((int(m.group(1)), name) for name, m
                      in ((x, _SLIDE_PART.match(x)) for x in parts) if m)
    ordered = pptx_slide_order(parts)
    if ordered:
        listed = set(ordered)
        # (position in the deck, part number, part name). The two numbers are
        # deliberately separate: the heading counts POSITION, while every satellite
        # part — this slide's rels, its notes fallback — is addressed by PART number.
        # Conflating them attaches slide 5's speaker notes to whatever sits fifth.
        sequence = [(pos, int(_SLIDE_PART.match(nm).group(1)), nm)
                    for pos, nm in enumerate(ordered, start=1)]
        # A part the deck never lists: a deleted-but-not-purged slide. It has no
        # position, so it may not claim one — but its words are in the package and
        # recall counts them, so dropping it would fail the whole document.
        sequence += [(None, pn, nm) for pn, nm in numbered if nm not in listed]
    else:
        sequence = [(pos, pn, nm)
                    for pos, (pn, nm) in enumerate(numbered, start=1)]
    blocks = []  # type: list
    used = set()
    used_notes = set()
    for pos, n, name in sequence:
        root = _root(parts.get(name, ""))
        embedded, rel_notes = _slide_rel_parts(parts, n)
        if root is not None:
            title_holder = []  # type: list
            body = []  # type: list
            img = None
            if emit_images:
                img = {"rels": _image_rels(
                    parts.get("ppt/slides/_rels/slide%d.xml.rels" % n, ""), name)}
            _pptx_shape_blocks(
                root, body, title_holder, img, None,
                _rels_targets(parts.get("ppt/slides/_rels/slide%d.xml.rels" % n, "")),
                _pptx_inherited_bullets(parts, name), [])
            heading = ("## Slide %d" % pos if pos is not None
                       else "## Slide (unlisted): slide%d" % n)
            if title_holder:
                heading += " — " + _esc_heading(title_holder[0])
            blocks.append(("h", heading))
            blocks.extend(body)
            for ref in embedded:
                used.add(ref)
                if _PPTX_DIAGRAM.match(ref):
                    items = _diagram_list_md(parts[ref])
                    if items:
                        blocks.append(("h", "### Diagram"))
                        blocks.append(("p", items))
                else:
                    t = _chart_text(parts[ref])
                    if t:
                        blocks.append(("h", "### Chart"))
                        blocks.append(("p", _esc_lead(_esc(t))))
        # Notes bind via the slide's RELATIONSHIP; the same-number filename is
        # only the fallback. Anything left over is rescued below, so a notes
        # part can never silently vanish.
        note_names = rel_notes or \
            [x for x in ("ppt/notesSlides/notesSlide%d.xml" % n,) if x in parts]
        for nn in note_names:
            if nn not in used_notes and root is not None:
                used_notes.add(nn)
                _pptx_notes_blocks(parts, nn, blocks)
    orphan_blocks = []  # type: list
    for name in sorted(parts):
        if (_PPTX_DIAGRAM.match(name) or _PPTX_CHART.match(name)) and name not in used:
            if _PPTX_DIAGRAM.match(name):
                items = _diagram_list_md(parts[name])
                if items:
                    orphan_blocks.append(("p", items))
            else:
                t = _chart_text(parts[name])
                if t:
                    orphan_blocks.append(("p", _esc_lead(_esc(t))))
    if orphan_blocks:
        blocks.append(("h", "## Embedded objects"))
        blocks.extend(orphan_blocks)
    for name in sorted(parts):
        if _NOTES_PART.match(name) and name not in used_notes:
            _pptx_notes_blocks(parts, name, blocks)
    comment_items = []
    for name in sorted(parts):
        if _PPTX_COMMENTS.match(name):
            comment_items.extend(_comments_items(parts[name]))
    if comment_items:
        blocks.append(("h", "## Comments"))
        blocks.append(("p", "\n".join("- " + _esc_block_start(i)
                                      for i in comment_items)))
    md = _join_blocks(blocks)
    return md + "\n" if md else ""


# `pptx_source_text` used to live here, and calling this module's helpers is exactly
# what made it a second opinion in name only: bugging `_sp_ph_type` took a deck's
# source token count from 108 to 20 while the gate held at a clean `recall: 1.0`,
# because the converter and the "independent" ground truth were the same reader
# twice. It now lives in `_pptx_struct` with its own traversal — see that module's
# header for the measurement.


# --------------------------------------------------------------------------- xlsx

# `\$?` because a producer may write `$C$2` into a cell's own `r`, and the ref
# still says which column the value is in. Without it the ref was unparseable,
# `_sheet_rows` fell back to append order, and the value published under the
# WRONG column heading — silently, at token recall 1.0.
_CELL_REF = re.compile(r"^\$?([A-Z]+)\$?\d+$")
# Sheet part basenames are a CONVENTION, not normative — accept any name under
# worksheets/ and every spec-legal relative-target form (xl/, /xl/, ../, ./).
_SHEET_TARGET = re.compile(r"^(?:/xl/|xl/|\.\./|\./)*(worksheets/[^/]+\.xml)$")
_XLSX_SHEET_PART = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_XLSX_DRAWING = re.compile(r"^xl/drawings/drawing\d+\.xml$")
_XLSX_COMMENTS = re.compile(r"^xl/comments\d*\.xml$")
_XLSX_CHART = re.compile(r"^xl/charts/chart(?:Ex)?\d+\.xml$")


def _shared_strings(xml):
    # type: (str) -> list
    """sharedStrings.xml -> list of si texts (rich-text runs concatenated verbatim,
    phonetic rPh subtrees excluded)."""
    root = _root(xml)
    if root is None:
        return []
    out = []
    for si in root:
        if _local(si.tag) == "si":
            parts = []  # type: list
            _collect_text(si, parts)
            out.append("".join(parts))
    return out


def _col_index(ref):
    # type: (str) -> int
    """0-based column from a cell ref (``B12`` -> 1); -1 when there is no ref."""
    m = _CELL_REF.match(ref or "")
    if not m:
        return -1
    n = 0
    for c in m.group(1):
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _cell_value(c, shared):
    # type: (object, list) -> str
    """The displayed value of one cell: shared/inline strings resolved, booleans
    spelled out, numbers and cached formula results verbatim."""
    ctype = _attr(c, "t") or "n"
    v = None
    inline = None
    for ch in c:
        loc = _local(ch.tag)
        if loc == "v":
            v = ch.text or ""
        elif loc == "is":
            parts = []  # type: list
            _collect_text(ch, parts)
            inline = "".join(parts)
    if ctype == "s":
        try:
            return shared[int((v or "").strip())]
        except (ValueError, IndexError):
            return ""
    if ctype == "inlineStr":
        return inline or ""
    if ctype == "b":
        return "TRUE" if (v or "").strip() == "1" else "FALSE"
    return v or ""


def _workbook_sheets(parts):
    # type: (dict) -> list
    """Ordered ``(sheet_name, part_name_or_None)`` from workbook.xml + its rels
    (chartsheets and unresolvable targets keep the name with part None)."""
    wb = _root(parts.get("xl/workbook.xml", ""))
    if wb is None:
        return []
    rels = {}
    rels_root = _root(parts.get("xl/_rels/workbook.xml.rels", ""))
    if rels_root is not None:
        for rel in rels_root:
            if _local(rel.tag) == "Relationship":
                rels[rel.attrib.get("Id", "")] = rel.attrib.get("Target", "")
    out = []
    sheets = []  # type: list
    _find_locals(wb, ("sheet",), sheets)
    for el in sheets:
        name = _attr(el, "name")
        rid = _attr(el, "id")
        target = rels.get(rid, "")
        m = _SHEET_TARGET.match(target)
        part = ("xl/" + m.group(1)) if m else None
        if part is not None and part not in parts:
            part = None
        out.append((name, part))
    return out


def _cell_fonts(styles_xml):
    # type: (str) -> dict
    """``{style index: marks tuple}`` for every cell style whose font is emphasised.

    A workbook states emphasis per CELL, not per run: `@s` indexes `cellXfs`, whose
    `@fontId` indexes `fonts`. All three are POSITIONAL, which is why this resolves
    the chain rather than reading the font element nearest the cell.

    The marks are kept in `_MARK_ORDER` so `_render_runs` sees the same canonical
    shape it gets from the other two formats and the output is deterministic."""
    root = _root(styles_xml)
    if root is None:
        return {}
    fonts = []  # type: list
    for el in root.iter():
        if _local(el.tag) != "font":
            continue
        marks = set()
        for ch in el:
            loc = _local(ch.tag)
            if loc in ("b", "i", "strike") and _attr(ch, "val") not in _OFF:
                marks.add({"b": "strong", "i": "em", "strike": "strike"}[loc])
        fonts.append(tuple(m for m in _MARK_ORDER if m in marks))
    xfs = None
    for el in root.iter():
        if _local(el.tag) == "cellXfs":
            xfs = el
            break
    out = {}
    if xfs is None:
        return out
    index = 0
    for xf in xfs:
        if _local(xf.tag) != "xf":
            continue
        font_id = _num_val(_attr(xf, "fontId"), 0)
        if 0 <= font_id < len(fonts) and fonts[font_id]:
            out[str(index)] = fonts[font_id]
        index += 1
    return out


def _sheet_links(sheet_xml, rels):
    # type: (str, dict) -> dict
    """``{cell ref: external url}`` from the worksheet's ``<hyperlinks>`` block.

    A workbook attaches a link to the CELL, keyed by `@ref`, rather than to a run.
    Only an external target resolves: an internal `location` is a jump within the
    same workbook, which has no address a reader outside it could follow, so the
    render keeps the text and `dropped_cell_links` reports the loss."""
    root = _root(sheet_xml)
    out = {}
    if root is None:
        return out
    for el in root.iter():
        if _local(el.tag) != "hyperlink":
            continue
        ref = _attr(el, "ref")
        url = rels.get(_attr(el, "id") or "", "")
        if ref and url.startswith(("http://", "https://", "mailto:")):
            out[ref.split(":")[0]] = url
    return out


def _cell_md(text, marks, url):
    # type: (str, tuple, str) -> str
    """One cell's text as inline markdown: escaped, marked, linked.

    An EMPTY cell gains nothing. `****` would be four literal asterisks the workbook
    never wrote, and a row of them is a GFM delimiter row waiting to happen — the
    exact shape `_esc_block_start`'s unconditional delimiter guard exists for."""
    body = _md_cell(_esc(text))
    if not body:
        return body
    if marks:
        body = _render_runs([(marks, body)])
    return "[%s](%s)" % (body, url) if url else body


def _sheet_rows(sheet_xml, shared, fonts=None, links=None):
    # type: (str, list, dict, dict) -> list
    """The worksheet's REGIONS: a list of row-blocks, each a list of positioned
    cell-text lists.

    A BLANK ROW SEPARATES REGIONS, which is how a spreadsheet says "these are two
    different tables" and how Excel itself reads a sheet — its own current-region
    selection stops at one. Dropping the blank row and running the rows together
    produced a single fused table in which the SECOND region's header line was
    published as a data row of the first: a power budget whose rows read
    ``| Corner | Margin |`` and ``| SSG 0.72V 125C | 0.94 |``, so a reader saw a
    rail drawing 0.94 mW. Both gates passed it — the words were all present and the
    ground truth dropped the same blank row, so the error cancelled.

    Leading and trailing blanks open no region, and a run of several blank rows is
    one separator rather than several."""
    root = _root(sheet_xml)
    if root is None:
        return []
    blocks = []  # type: list
    current = []  # type: list
    row_els = []  # type: list
    _find_locals(root, ("row",), row_els)
    for row in row_els:
        cells = []  # type: list
        for c in row:
            if _local(c.tag) != "c":
                continue
            col = _col_index(_attr(c, "r"))
            if col < 0:
                col = len(cells)
            while len(cells) <= col:
                cells.append("")
            ref = _attr(c, "r")
            cells[col] = _cell_md(_cell_value(c, shared),
                                  (fonts or {}).get(_attr(c, "s"), ()),
                                  (links or {}).get(ref, ""))
        if any(cells):
            current.append(cells)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def xlsx_markdown(parts, emit_images=False):
    # type: (dict, bool) -> str
    """Deterministic full markdown of an xlsx: one ``## <sheet name>`` section per
    workbook tab (workbook order) rendering the sheet as a GFM table (first data
    row as header), plus sheet text boxes, cell comments, and chart text. With
    ``emit_images`` each drawing's pictures emit ``<!-- ooxml-image:PART -->``
    sentinels in a trailing ``## Images`` group (default off = byte-identical legacy).
    Spreadsheet pictures float over the grid rather than sitting in a cell, so they
    are grouped after the tables rather than wedged into a pipe row."""
    shared = _shared_strings(parts.get("xl/sharedStrings.xml", ""))
    fonts = _cell_fonts(parts.get("xl/styles.xml", ""))
    blocks = []  # type: list
    linked = set()

    def sheet_links(part):
        """A sheet's links come from the SHEET's own rels part: worksheets number
        their rIds independently, so the workbook's rels would resolve rId1 to
        whatever the workbook happens to call rId1."""
        return _sheet_links(parts.get(part, ""), _rels_targets(parts.get(
            part.replace("xl/worksheets/", "xl/worksheets/_rels/") + ".rels", "")))

    for name, part in _workbook_sheets(parts):
        blocks.append(("h", "## " + _esc_heading(_esc(name))))
        if part is None:
            continue
        linked.add(part)
        for region in _sheet_rows(parts.get(part, ""), shared, fonts,
                                  sheet_links(part)):
            table = _gfm_table(region)
            if table:
                blocks.append(("table", table))
    # Worksheet parts the workbook/rels resolution did NOT reach still render —
    # a bad rels target can cost naming, never cell content.
    for name in sorted(parts):
        if _XLSX_SHEET_PART.match(name) and name not in linked:
            tables = [t for t in (_gfm_table(r) for r in _sheet_rows(
                parts[name], shared, fonts, sheet_links(name))) if t]
            if tables:
                blocks.append(("h", "## Sheet (unlinked): "
                               + _esc(name.rsplit("/", 1)[-1][:-4])))
                for table in tables:
                    blocks.append(("table", table))
    blocks.extend(_embedded_sections(parts, (
        (_XLSX_DRAWING, "Text boxes",
         lambda x: (_esc_block_start(_text_of(_root(x)))
                    if _root(x) is not None else "")),
        (_XLSX_COMMENTS, "Comments",
         lambda x: "\n".join("- " + _esc_block_start(i)
                             for i in _comments_items(x))),
        (_XLSX_CHART, "Charts", lambda x: _esc_block_start(_chart_text(x))))))
    if emit_images:
        img_blocks = []  # type: list
        for name in sorted(parts):
            if not _XLSX_DRAWING.match(name):
                continue
            root = _root(parts[name])
            if root is None:
                continue
            rels_name = name.replace("xl/drawings/", "xl/drawings/_rels/", 1) + ".rels"
            rels = _image_rels(parts.get(rels_name, ""), name)
            _emit_image_blocks(_blip_rids(root), rels, img_blocks)
        if img_blocks:
            blocks.append(("h", "## Images"))
            blocks.extend(img_blocks)
    md = _join_blocks(blocks)
    return md + "\n" if md else ""


# `xlsx_source_text` used to live here and call `_cell_value` — the SAME function
# `xlsx_markdown` asks what a cell says. Its own comment above the sheet loop boasted
# that reading worksheet parts directly kept a rels bug from "zeroing both sides",
# which was true about GEOMETRY and false about VALUES: bugging `_cell_value` took
# the workbook's source token count from 107 to 73 with the gate still reporting
# `recall: 1.0`. It now lives in `_xlsx_struct` with its own typed resolver — see
# that module's header for the measurement.


# ----------------------------------------------------------------------- dispatch

_CONVERTERS = {"docx": docx_markdown, "pptx": pptx_markdown, "xlsx": xlsx_markdown}
_SOURCES = {"docx": docx_source_text, "pptx": pptx_source_text, "xlsx": xlsx_source_text}


def svg_text(svg_xml):
    # type: (str) -> str
    """Visible label text of an embedded SVG image — the content of every ``<text>``
    element (nested ``<tspan>`` included), one label per line. ``""`` on a missing/
    malformed SVG. Deterministic (no model): SVG is XML, so its diagram labels are real
    extractable document text, unlike a raster screenshot."""
    root = _root(svg_xml)
    if root is None:
        return ""
    out = []
    for el in root.iter():
        if _local(el.tag) == "text":
            s = _WS.sub(" ", " ".join(t for t in el.itertext() if t)).strip()
            if s:
                out.append(s)
    return "\n".join(out)


def _esc_fig(text):
    # type: (str) -> str
    """Escape a figure label block line by line: inline specials AND line-leading
    list/heading/quote markers, exactly like body paragraphs. A diagram callout such
    as ``1. Configure`` would otherwise render as an ordered-list item whose ``1``
    marker a GFM stripper swallows — dropping a real token and breaking recall."""
    return "\n".join(_esc_block_start(ln) for ln in text.split("\n"))


def _svg_parts_text(parts):
    # type: (dict) -> list
    """[(part_name, label_text)] for every embedded SVG that carries text, name-sorted —
    the SHARED source both the converter and the ground truth walk (so SVG text is added
    to both symmetrically and recall stays exactly 1.0)."""
    out = []
    for name in sorted(parts):
        if _MEDIA_SVG.match(name):
            t = svg_text(parts[name])
            if t:
                out.append((name, t))
    return out


def ooxml_markdown(ext, parts, emit_images=False):
    # type: (str, dict, bool) -> str
    """Convert any OOXML format's parts to markdown; ``""`` for unknown formats.

    With ``emit_images`` each body picture emits a positional ``<!-- ooxml-image:PART -->``
    sentinel (an HTML comment: recall-gate invisible), resolved to an image link by the
    bundle writer. Default off keeps the legacy markdown lane byte-identical."""
    fn = _CONVERTERS.get((ext or "").lower().lstrip("."))
    body = fn(parts, emit_images) if fn else ""
    figs = _svg_parts_text(parts)
    if figs:
        blocks = "\n\n".join(_esc_fig(t) for _, t in figs)
        body = (body + "\n\n" if body.strip() else "") + "## Figures\n\n" + blocks + "\n"
    return body


def ooxml_source_text(ext, parts):
    # type: (str, dict) -> str
    """The matching exhaustive ground truth for ``ooxml_markdown``'s output."""
    fn = _SOURCES.get((ext or "").lower().lstrip("."))
    base = fn(parts) if fn else ""
    figs = _svg_parts_text(parts)
    if figs:
        base = base + "\n" + "\n".join(t for _, t in figs)
    return base
