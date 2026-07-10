"""
title: OOXML -> markdown deterministic converters (private)
layer: backend
public_api: no
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
import xml.etree.ElementTree as _ET

from ._figures import ooxml_image_sentinel

__all__ = ["docx_markdown", "pptx_markdown", "xlsx_markdown",
           "docx_source_text", "pptx_source_text", "xlsx_source_text",
           "ooxml_markdown", "ooxml_source_text", "svg_text", "OOXML_MAIN_PARTS"]

_WS = re.compile(r"\s+")

# The text-bearing parts each converter consumes, as regexes on part names.
# office_convert.py reads these; its --audit-parts mode uses the same list to
# report any OTHER part that still contains text runs (nothing silently dropped).
OOXML_MAIN_PARTS = {
    "docx": (r"^word/document\.xml$", r"^word/styles\.xml$", r"^word/numbering\.xml$",
             r"^word/footnotes\.xml$", r"^word/endnotes\.xml$", r"^word/comments\.xml$",
             r"^word/_rels/document\.xml\.rels$", r"^word/charts/chart\d+\.xml$",
             r"^word/diagrams/data\d+\.xml$", r"^word/media/[^/]+\.svg$",
             r"^docProps/(core|app)\.xml$"),
    "pptx": (r"^ppt/slides/slide\d+\.xml$", r"^ppt/slides/_rels/slide\d+\.xml\.rels$",
             r"^ppt/notesSlides/notesSlide\d+\.xml$", r"^ppt/diagrams/data\d+\.xml$",
             r"^ppt/charts/chart\d+\.xml$", r"^ppt/comments/[^/]+\.xml$",
             r"^ppt/media/[^/]+\.svg$", r"^docProps/(core|app)\.xml$"),
    "xlsx": (r"^xl/workbook\.xml$", r"^xl/_rels/workbook\.xml\.rels$",
             r"^xl/sharedStrings\.xml$", r"^xl/worksheets/[^/]+\.xml$",
             r"^xl/drawings/drawing\d+\.xml$", r"^xl/drawings/_rels/drawing\d+\.xml\.rels$",
             r"^xl/comments\d*\.xml$",
             r"^xl/charts/chart\d+\.xml$", r"^xl/media/[^/]+\.svg$",
             r"^docProps/(core|app)\.xml$"),
}

# Embedded SVG images (vector). Their <text> labels are real document text — unlike a
# raster screenshot, they are extractable deterministically (no VLM), so the OOXML lane
# captures them and the gate holds them to recall 1.0. Raster/metafile image text is a
# separate opt-in VLM pass (see docs/design/ooxml-lane.md).
_MEDIA_SVG = re.compile(r"^(word|ppt|xl)/media/[^/]+\.svg$")


def _local(tag):
    # type: (str) -> str
    """Local name of a namespaced ET tag (``{uri}p`` -> ``p``).

    Matching on local names keeps the walkers generic across the transitional and
    strict OOXML namespace URIs (and mixed w:/a: content inside drawings)."""
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""                       # comments/PIs have non-str tags


def _attr(el, local):
    # type: (object, str) -> str
    """Attribute value by LOCAL attribute name (``r:id``/``w:val`` -> ``id``/``val``)."""
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1] == local:
            return v
    return ""


def _root(xml):
    # type: (str) -> object
    """Parsed root or None — malformed parts degrade to empty output, never raise."""
    if not xml:
        return None
    try:
        return _ET.fromstring(xml)
    except _ET.ParseError:
        return None


# Elements that stand for whitespace but carry no text.
_BREAK_LOCALS = ("tab", "br", "cr")
# Subtrees skipped EVERYWHERE (converter and ground truth): Fallback duplicates
# Choice; rPh is furigana phonetic duplication of its base text; moveFrom is the
# tracked-changes "moved away" OLD copy of relocated content (the live copy is in
# moveTo) — keeping it resurrects stale text and duplicates whole sections.
_SKIP_LOCALS = ("Fallback", "rPh", "moveFrom")


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


def _emit_image_blocks(rids, rels, blocks):
    # type: (list, dict, list) -> None
    """Append an ``("img", sentinel)`` block for each rId that resolves to a media part."""
    for rid in rids:
        part = rels.get(rid)
        if part:
            blocks.append(("img", ooxml_image_sentinel(part)))


# Markdown metacharacters that would REINTERPRET literal source text when rendered:
# `__path__` turns bold (eating the underscores), `<prdata[31:0]>` parses as an HTML
# tag, `[x](y)` as a link, `~~x~~` as strikethrough. Silicon docs are full of these,
# so every literal text is escaped — the source must round-trip through a GFM
# renderer character-perfect.
_MD_SPECIAL = re.compile(r"([*_`<\[\]~])")
_LEAD_LIST_NUM = re.compile(r"^(\d+)([.)])(\s)")
_LEAD_MARK = re.compile(r"^([#+*-])(\s)")


def _esc(text):
    # type: (str) -> str
    """Backslash-escape inline markdown specials in literal source text."""
    if not text:
        return ""
    return _MD_SPECIAL.sub(r"\\\1", text.replace("\\", "\\\\"))


def _esc_lead(text):
    # type: (str) -> str
    """Escape LINE-LEADING constructs too (list/heading/blockquote markers), for
    text that opens a markdown line: ``15. foo`` would otherwise become an ordered
    list whose marker (the ``15``) renderers and strippers both swallow."""
    if not text:
        return ""
    if text.startswith(">"):
        return "\\" + text
    m = _LEAD_LIST_NUM.match(text)
    if m:
        return text[:m.end(1)] + "\\" + text[m.end(1):]
    if _LEAD_MARK.match(text):
        return "\\" + text
    return text


def _md_cell(text):
    # type: (str) -> str
    """Make a text safe as a single GFM table cell (escape pipes, no newlines)."""
    return _WS.sub(" ", (text or "")).replace("|", "\\|").strip()


def _gfm_table(rows):
    # type: (list) -> str
    """Rows of cell texts -> a well-formed GFM pipe table (first row = header).

    Every row is padded to the shared width so the column count is consistent —
    the validator's table-columns rule holds by construction. The width is the
    last column ANY row actually uses, so trailing always-empty columns (styled
    but valueless spreadsheet cells) never render as pipe noise."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = 1
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


def _join_blocks(blocks):
    # type: (list) -> str
    """Join (kind, text) blocks: consecutive list items stack with single newlines
    (one markdown list), everything else is blank-line separated."""
    parts = []  # type: list
    prev_kind = None
    for kind, text in blocks:
        if not text:
            continue
        if parts:
            parts.append("\n" if (kind == "li" and prev_kind == "li") else "\n\n")
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
    return "\n".join("- " + _esc(i) for i in _diagram_items(data_xml))


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
                # pre-formatted bullet lists pass through; prose gets lead-escaped
                blocks.append(("p", t if t.startswith("- ") else _esc_lead(t)))
    return blocks


# --------------------------------------------------------------------------- docx

_HEADING_NAME = re.compile(r"^heading\s+([1-9])$")
_DOCX_CHART = re.compile(r"^word/charts/chart\d+\.xml$")
_DOCX_DIAGRAM = re.compile(r"^word/diagrams/data\d+\.xml$")


def _docx_styles(styles_xml):
    # type: (str) -> dict
    """styleId -> heading level, from ``word/styles.xml``.

    A style is a heading when its w:name is ``heading N`` (Word's canonical names
    survive localization better than styleIds) or when it carries an explicit
    w:outlineLvl. ``Title`` maps to level 1."""
    root = _root(styles_xml)
    levels = {}
    if root is None:
        return levels
    for style in root:
        if _local(style.tag) != "style":
            continue
        sid = _attr(style, "styleId")
        if not sid:
            continue
        name = ""
        outline = None
        for ch in style.iter():
            loc = _local(ch.tag)
            if loc == "name":
                name = _attr(ch, "val")
            elif loc == "outlineLvl":
                try:
                    outline = int(_attr(ch, "val"))
                except ValueError:
                    outline = None
        m = _HEADING_NAME.match((name or "").strip().lower())
        if m:
            levels[sid] = int(m.group(1))
        elif (name or "").strip().lower() == "title":
            levels[sid] = 1
        elif outline is not None and 0 <= outline <= 8:
            levels[sid] = outline + 1
    return levels


def _docx_numbering(numbering_xml):
    # type: (str) -> dict
    """(numId, ilvl) -> numFmt (``bullet``/``decimal``/...), from numbering.xml."""
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
            for ch in lvl:
                if _local(ch.tag) == "numFmt":
                    abstract[(aid, ilvl)] = _attr(ch, "val")
    for num in root:
        if _local(num.tag) != "num":
            continue
        nid = _attr(num, "numId")
        for ch in num:
            if _local(ch.tag) == "abstractNumId":
                aid = _attr(ch, "val")
                for (a, ilvl), fmt in abstract.items():
                    if a == aid:
                        fmts[(nid, ilvl)] = fmt
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


def _docx_p_text(p, links, boxes, images=None):
    # type: (object, dict, list, object) -> str
    """Markdown text of one paragraph: verbatim run joins, hyperlinks rendered
    ``[text](url)`` when the rel target is external. Text boxes anchored inside
    the paragraph are collected into ``boxes`` for rendering as their own blocks.

    When ``images`` is a list, embedded-picture rIds (DrawingML ``<a:blip>`` / VML
    ``<v:imagedata>``) are appended to it in reading order; the caller emits the
    sentinels. ``None`` (the default) means the legacy text-only walk -- byte-identical."""
    parts = []  # type: list

    def walk(el):
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
            if loc == "hyperlink":
                mark = len(parts)
                walk(ch)
                inner = _WS.sub(" ", "".join(parts[mark:])).strip()
                del parts[mark:]
                url = _attr(ch, "id") and links.get(_attr(ch, "id"), "") or ""
                if inner and url.startswith(("http://", "https://")):
                    parts.append("[%s](%s)" % (inner, url))
                elif inner:
                    parts.append(inner)
                continue
            if loc == "t":
                if ch.text:
                    parts.append(_esc(ch.text))
                continue
            if loc in _BREAK_LOCALS:
                parts.append(" ")
                continue
            walk(ch)
    walk(p)
    return _WS.sub(" ", "".join(parts)).strip()


def _docx_cell_text(tc, links, boxes, images=None):
    # type: (object, dict, list, object) -> str
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
            t = _docx_p_text(el, links, boxes, images)
            if t:
                chunks.append(_md_cell(t))
        else:
            for row in _docx_table_rows_md(el, links, boxes, images):
                flat = " ".join(c for c in row if c)
                if flat:
                    chunks.append(flat)
    return "<br>".join(chunks)


def _docx_table_rows_md(tbl, links, boxes, images=None):
    # type: (object, dict, list, object) -> list
    """Rows of cell texts for one w:tbl, honoring gridSpan column geometry AND
    vMerge forward-fill. Rows/cells wrapped in w:sdt (repeating-section controls)
    are found through the wrappers; nested tables stay inside their owning cell.

    GFM tables have no rowspan, so a vertical merge (``w:vMerge``) is flattened by
    REPEATING the restart cell's value down its continuation rows — the
    continuation cells are empty in OOXML, so this adds no tokens the source lacks
    (recall stays 1.0) and makes each row self-contained for row-wise chunking.
    Only true merge-continuations are filled: an ordinary empty cell stays empty,
    and a (malformed) continuation carrying its own text keeps it, never dropped."""
    rows = []
    trs = []  # type: list
    _find_locals(tbl, ("tr",), trs, stop=("tc", "p", "tblPr", "tblGrid"))
    fill = {}  # type: dict  # grid-column index -> restart value for an active vMerge
    for tr in trs:
        cells = []  # type: list
        tcs = []  # type: list
        _find_locals(tr, ("tc",), tcs, stop=("p", "tbl", "trPr"))
        col = 0
        for tc in tcs:
            span = 1
            spans = []  # type: list
            _find_locals(tc, ("gridSpan",), spans, stop=("p", "tbl"))
            if spans:
                try:
                    span = max(1, int(_attr(spans[0], "val")))
                except ValueError:
                    span = 1
            vmerges = []  # type: list
            _find_locals(tc, ("vMerge",), vmerges, stop=("p", "tbl"))
            text = _docx_cell_text(tc, links, boxes, images)
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
        if cells:
            rows.append(cells)
    return rows


def _docx_blocks(el, styles, numbering, links, blocks, img=None):
    # type: (object, dict, dict, dict, list, object) -> None
    """Walk any element emitting (kind, markdown) blocks for each w:p / w:tbl.

    Recurses through wrappers (w:sdt content controls, bookmarks) so TOC fields
    and content-control bodies are never silently skipped. ``img`` is ``None``
    (legacy: no image emission, byte-identical) or ``{"rels": {rId: media_part}}``,
    in which case each embedded picture emits an ``("img", sentinel)`` block at its
    position (a paragraph's images right after its text; a table's after the table)."""
    rels = img["rels"] if img is not None else None
    for ch in el:
        loc = _local(ch.tag)
        if loc in _SKIP_LOCALS:
            continue
        if loc == "p":
            boxes = []  # type: list
            images = [] if rels is not None else None
            text = _docx_p_text(ch, links, boxes, images)
            if text:
                sid, num_id, ilvl, outline = _p_style_info(ch)
                level = styles.get(sid)
                if level is None and outline:
                    try:
                        level = int(outline) + 1
                    except ValueError:
                        level = None
                if level:
                    blocks.append(("h", "#" * min(level, 6) + " " + _esc_lead(text)))
                elif num_id and num_id != "0":
                    fmt = numbering.get((num_id, ilvl or "0"), "bullet")
                    marker = "-" if fmt == "bullet" else "1."
                    try:
                        indent = "  " * int(ilvl or "0")
                    except ValueError:
                        indent = ""
                    blocks.append(("li", indent + marker + " " + text))
                else:
                    blocks.append(("p", _esc_lead(text)))
            if images:
                _emit_image_blocks(images, rels, blocks)
            for box in boxes:
                _docx_blocks(box, styles, numbering, links, blocks, img)
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
                _docx_blocks(single, styles, numbering, links, blocks, img)
                continue
            boxes = []
            images = [] if rels is not None else None
            table = _gfm_table(_docx_table_rows_md(ch, links, boxes, images))
            if table:
                blocks.append(("table", table))
            if images:
                _emit_image_blocks(images, rels, blocks)
            for box in boxes:
                _docx_blocks(box, styles, numbering, links, blocks, img)
        else:
            _docx_blocks(ch, styles, numbering, links, blocks, img)


def _docx_notes_section(xml, title):
    # type: (str, str) -> str
    """foot/endnotes/comments part -> a ``## <title>`` section, one item per
    note/comment (separator stubs carry no text and drop out)."""
    root = _root(xml)
    if root is None:
        return ""
    items = []
    for note in root:
        t = _esc(_text_of(note, value_locals=("t", "text")))
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
    styles = _docx_styles(parts.get("word/styles.xml", ""))
    numbering = _docx_numbering(parts.get("word/numbering.xml", ""))
    links = _rels_targets(parts.get("word/_rels/document.xml.rels", ""))
    img = None
    if emit_images:
        img = {"rels": _image_rels(parts.get("word/_rels/document.xml.rels", ""),
                                   "word/document.xml")}
    blocks = []  # type: list
    _docx_blocks(root, styles, numbering, links, blocks, img)
    for part, title in (("word/footnotes.xml", "Footnotes"),
                        ("word/endnotes.xml", "Endnotes"),
                        ("word/comments.xml", "Comments")):
        section = _docx_notes_section(parts.get(part, ""), title)
        if section:
            blocks.append(("section", section))
    blocks.extend(_embedded_sections(parts, (
        (_DOCX_DIAGRAM, "Diagrams", _diagram_list_md),
        (_DOCX_CHART, "Charts", lambda x: _esc(_chart_text(x))))))
    md = _join_blocks(blocks)
    return md + "\n" if md else ""


def docx_source_text(parts):
    # type: (dict) -> str
    """Exhaustive ground truth for the docx conversion gate: EVERY text run in
    word/document.xml (+ foot/endnotes, embedded diagram/chart parts), regardless
    of structure, under the same shared content policy. Independent of the
    converter's structural walk."""
    chunks = []
    for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml",
                 "word/comments.xml"):
        root = _root(parts.get(name, ""))
        if root is not None:
            chunks.append(_text_of(root, value_locals=("t", "text")))
    for name in sorted(parts):
        if _DOCX_DIAGRAM.match(name):
            chunks.append(" ".join(_diagram_items(parts[name])))
        elif _DOCX_CHART.match(name):
            chunks.append(_chart_text(parts[name]))
    return _WS.sub(" ", " ".join(c for c in chunks if c)).strip()


# --------------------------------------------------------------------------- pptx

_SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES_PART = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_PPTX_DIAGRAM = re.compile(r"^ppt/diagrams/data\d+\.xml$")
_PPTX_CHART = re.compile(r"^ppt/charts/chart\d+\.xml$")
# Placeholder types that are page chrome, not content (shared with ground truth).
_CHROME_PH = ("sldNum", "dt", "ftr")


def _sp_ph_type(sp):
    # type: (object) -> str
    phs = []  # type: list
    _find_locals(sp, ("ph",), phs, stop=("txBody",))
    return _attr(phs[0], "type") if phs else ""   # _attr already matches the unprefixed attr


def _is_chrome_sp(el):
    # type: (object) -> bool
    return _local(el.tag) == "sp" and _sp_ph_type(el) in _CHROME_PH


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
    return _gfm_table(rows)


def _pptx_txbody_paras(container):
    # type: (object) -> list
    """(indent_level, text) per paragraph of every txBody under ``container``
    (or of ``container`` itself when it IS a txBody)."""
    out = []
    if _local(container.tag) == "txBody":
        bodies = [container]
    else:
        bodies = []  # type: list
        _find_locals(container, ("txBody",), bodies)
    for tx in bodies:
        for p in tx:
            if _local(p.tag) != "p":
                continue
            lvl = 0
            for pr in p:
                if _local(pr.tag) == "pPr":
                    try:
                        lvl = int(_attr(pr, "lvl") or "0")
                    except ValueError:
                        lvl = 0
            t = _text_of(p)
            if t:
                out.append((lvl, t))
    return out


def _pptx_shape_blocks(el, blocks, title_holder, img=None):
    # type: (object, list, list, object) -> None
    """Walk a slide's shape tree in order: title -> holder, body paragraphs ->
    bullets (PowerPoint's default rendering), a:tbl -> GFM, groups recurse.
    A txBody in any other container (connectors, exotic shapes) still renders,
    so no text-bearing shape type is silently dropped. With ``img`` (a
    ``{"rels": {rId: media_part}}`` dict) each ``p:pic`` emits an image sentinel
    where it sits; ``None`` (default) leaves pictures unrendered as before."""
    rels = img["rels"] if img is not None else None
    for ch in el:
        loc = _local(ch.tag)
        if loc in _SKIP_LOCALS:
            continue
        if loc == "sp":
            if _is_chrome_sp(ch):
                continue
            paras = _pptx_txbody_paras(ch)
            if _sp_ph_type(ch) in ("title", "ctrTitle") and not title_holder and paras:
                title_holder.append(" ".join(_esc(t) for _, t in paras))
            else:
                for lvl, t in paras:
                    blocks.append(("li", "  " * lvl + "- " + _esc(t)))
        elif loc == "graphicFrame":
            tbls = []  # type: list
            _find_locals(ch, ("tbl",), tbls)
            for tbl in tbls:
                table = _pptx_table_md(tbl)
                if table:
                    blocks.append(("table", table))
            # A graphicFrame can also carry an embedded-object / slide-zoom / OLE image
            # (its blip). Charts/tables reference a separate part and carry no inline
            # blip, so this is empty for them. Frequently the ONLY body copy of a picture:
            # modern PowerPoint puts it in the mc:Choice graphicFrame and leaves a <p:pic>
            # in the mc:Fallback we skip -- catching it here keeps that image from vanishing.
            if rels is not None:
                _emit_image_blocks(_blip_rids(ch), rels, blocks)
        elif loc == "txBody":
            for lvl, t in _pptx_txbody_paras(ch):
                blocks.append(("li", "  " * lvl + "- " + _esc(t)))
        elif loc == "pic":
            if rels is not None:
                _emit_image_blocks(_blip_rids(ch), rels, blocks)
        else:
            _pptx_shape_blocks(ch, blocks, title_holder, img)


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
    _pptx_shape_blocks(root, body, title_holder)
    if not body and not title_holder:
        return
    blocks.append(("h", "### Speaker notes"))
    if title_holder:
        blocks.append(("p", title_holder[0]))
    blocks.extend(body)


def pptx_markdown(parts, emit_images=False):
    # type: (dict, bool) -> str
    """Deterministic full markdown of a pptx: one ``## Slide N`` section per slide
    (numeric order) with title, bulleted body text, GFM tables, SmartArt/chart
    text, and that slide's speaker notes. Diagram/chart parts never referenced by
    any slide land in a trailing ``## Embedded objects`` section so nothing is
    orphaned. With ``emit_images`` each slide picture emits a positional
    ``<!-- ooxml-image:PART -->`` sentinel (default off = byte-identical legacy)."""
    slides = []
    for name in parts:
        m = _SLIDE_PART.match(name)
        if m:
            slides.append((int(m.group(1)), name))
    blocks = []  # type: list
    used = set()
    used_notes = set()
    for n, name in sorted(slides):
        root = _root(parts.get(name, ""))
        embedded, rel_notes = _slide_rel_parts(parts, n)
        if root is not None:
            title_holder = []  # type: list
            body = []  # type: list
            img = None
            if emit_images:
                img = {"rels": _image_rels(
                    parts.get("ppt/slides/_rels/slide%d.xml.rels" % n, ""), name)}
            _pptx_shape_blocks(root, body, title_holder, img)
            heading = "## Slide %d" % n
            if title_holder:
                heading += " — " + title_holder[0]
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
        blocks.append(("p", "\n".join("- " + _esc(i) for i in comment_items)))
    md = _join_blocks(blocks)
    return md + "\n" if md else ""


def pptx_source_text(parts):
    # type: (dict) -> str
    """Exhaustive pptx ground truth: every text run on every slide (chrome
    placeholders excluded, same policy as the converter), all diagram/chart
    parts, and the speaker notes."""
    chunks = []
    slides = sorted((int(_SLIDE_PART.match(n).group(1)), n)
                    for n in parts if _SLIDE_PART.match(n))
    for _, name in slides:
        root = _root(parts.get(name, ""))
        if root is not None:
            chunks.append(_text_of(root, skip=_is_chrome_sp))
    for name in sorted(parts):
        if _PPTX_DIAGRAM.match(name):
            chunks.append(" ".join(_diagram_items(parts[name])))
        elif _PPTX_CHART.match(name):
            chunks.append(_chart_text(parts[name]))
        elif _NOTES_PART.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_text_of(root, skip=_is_chrome_sp))
        elif _PPTX_COMMENTS.match(name):
            chunks.append(" ".join(_comments_items(parts[name])))
    return _WS.sub(" ", " ".join(c for c in chunks if c)).strip()


# --------------------------------------------------------------------------- xlsx

_CELL_REF = re.compile(r"^([A-Z]+)\d+$")
# Sheet part basenames are a CONVENTION, not normative — accept any name under
# worksheets/ and every spec-legal relative-target form (xl/, /xl/, ../, ./).
_SHEET_TARGET = re.compile(r"^(?:/xl/|xl/|\.\./|\./)*(worksheets/[^/]+\.xml)$")
_XLSX_SHEET_PART = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_XLSX_DRAWING = re.compile(r"^xl/drawings/drawing\d+\.xml$")
_XLSX_COMMENTS = re.compile(r"^xl/comments\d*\.xml$")
_XLSX_CHART = re.compile(r"^xl/charts/chart\d+\.xml$")


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


def _sheet_rows(sheet_xml, shared):
    # type: (str, list) -> list
    """All non-empty rows of a worksheet as positioned cell-text lists."""
    root = _root(sheet_xml)
    if root is None:
        return []
    rows = []
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
            cells[col] = _md_cell(_esc(_cell_value(c, shared)))
        if any(cells):
            rows.append(cells)
    return rows


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
    blocks = []  # type: list
    linked = set()
    for name, part in _workbook_sheets(parts):
        blocks.append(("h", "## " + _esc(name)))
        if part is None:
            continue
        linked.add(part)
        table = _gfm_table(_sheet_rows(parts.get(part, ""), shared))
        if table:
            blocks.append(("table", table))
    # Worksheet parts the workbook/rels resolution did NOT reach still render —
    # a bad rels target can cost naming, never cell content.
    for name in sorted(parts):
        if _XLSX_SHEET_PART.match(name) and name not in linked:
            table = _gfm_table(_sheet_rows(parts[name], shared))
            if table:
                blocks.append(("h", "## Sheet (unlinked): "
                               + _esc(name.rsplit("/", 1)[-1][:-4])))
                blocks.append(("table", table))
    blocks.extend(_embedded_sections(parts, (
        (_XLSX_DRAWING, "Text boxes",
         lambda x: _esc(_text_of(_root(x))) if _root(x) is not None else ""),
        (_XLSX_COMMENTS, "Comments",
         lambda x: "\n".join("- " + _esc(i) for i in _comments_items(x))),
        (_XLSX_CHART, "Charts", lambda x: _esc(_chart_text(x))))))
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


def xlsx_source_text(parts):
    # type: (dict) -> str
    """Exhaustive xlsx ground truth: every sheet name and every cell value (typed
    resolution, so shared-string INDICES are never counted as content), plus
    drawing text boxes, comments, and chart text."""
    shared = _shared_strings(parts.get("xl/sharedStrings.xml", ""))
    chunks = []
    for name, _ in _workbook_sheets(parts):
        chunks.append(name)
    # Cell values come from EVERY worksheet part directly — deliberately NOT via
    # the workbook/rels resolution the converter uses, so a resolution bug on
    # that side shows up as recall < 1.0 instead of zeroing both sides.
    for name in sorted(parts):
        if not _XLSX_SHEET_PART.match(name):
            continue
        root = _root(parts[name])
        if root is None:
            continue
        cells = []  # type: list
        _find_locals(root, ("c",), cells)
        for c in cells:
            v = _cell_value(c, shared)
            if v:
                chunks.append(v)
    for name in sorted(parts):
        if _XLSX_DRAWING.match(name):
            root = _root(parts[name])
            if root is not None:
                chunks.append(_text_of(root))
        elif _XLSX_COMMENTS.match(name):
            # per-comment items, matching the converter's bullet segmentation —
            # a raw whole-part read would glue adjacent comments verbatim
            chunks.append(" ".join(_comments_items(parts[name])))
        elif _XLSX_CHART.match(name):
            chunks.append(_chart_text(parts[name]))
    return _WS.sub(" ", " ".join(c for c in chunks if c)).strip()


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
    return "\n".join(_esc_lead(_esc(ln)) for ln in text.split("\n"))


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
