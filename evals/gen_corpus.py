#!/usr/bin/env python3
"""Generate the deterministic synthetic eval corpus for doc2md.

The real evaluation corpus is confidential and lives outside the repo, so this
script builds an ARTIFICIAL one — plausible engineering documents for a fictional
company ("Nimbus Semiconductor", project "Kestrel") — that exercises every lane
and known failure mode of the pipeline:

  * OOXML sources (docx/xlsx/pptx) are HAND-BUILT: the XML parts are written
    directly with string templates + zipfile. This is deliberate — the office
    lane parses OOXML XML directly, so hand-built packages give precise control
    over exactly which constructs are exercised (merged cells, vMerge, TOC
    adjacency, split runs, furniture parts, ...).
  * Legacy/derived formats (doc/rtf/odt/xls/ppt and the digital PDFs) come from
    LibreOffice, resolved via $DOC2MD_LIBREOFFICE or PATH — never a hardcoded path.
  * The "scanned" PDF is a rasterized re-wrap of the digital PDF: poppler's
    pdftoppm renders page PNGs and the PDF-lane interpreter ($DOC2MD_PDF_PYTHON,
    which has Pillow) wraps them back into an image-only PDF. Skipped with a
    clear message when the tools are absent.

DETERMINISM: hand-built outputs are byte-identical across runs — fixed OOXML
core.xml dates (2026-01-01T00:00:00Z), fixed zip entry date_time, no randomness.
Derived outputs depend on the external tools and are NOT byte-stable (they embed
their own metadata); the eval never asserts on their bytes.

Everything is written under --out (default data/eval_corpus, git-ignored); a
sibling ``<out>.manifest.json`` records what was built vs skipped so the eval
runner can skip expectations for missing derived files honestly.

Usage:
  python3 evals/gen_corpus.py --out data/eval_corpus
  python3 evals/gen_corpus.py --handbuilt-only          # no soffice/poppler needed
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

# One fixed timestamp everywhere: OOXML core properties and zip entries.
STAMP = "2026-01-01T00:00:00Z"
ZIP_DT = (2026, 1, 1, 0, 0, 0)

COMPANY = "Nimbus Semiconductor"
PROJECT = "Kestrel"


# --------------------------------------------------------------------- helpers

def xesc(s):
    # type: (str) -> str
    """Escape a literal string for use as XML text/attribute content."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def write_zip(path, entries):
    # type: (str, list) -> None
    """Write an OOXML package with fully deterministic bytes: fixed entry order
    (as given), fixed date_time, fixed permissions, one deflate level."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zi = zipfile.ZipInfo(name, date_time=ZIP_DT)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o100644 << 16
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(zi, data)


def write_text(path, text):
    # type: (str, str) -> None
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def sha256_file(path):
    # type: (str) -> str
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ PNG writer

def png_bytes(width, height, pixel):
    # type: (int, int, object) -> bytes
    """A deterministic RGB PNG from a pixel(x, y) -> (r, g, b) function.

    Stdlib only (zlib + struct + hand-rolled chunks); no PIL on the 3.6 host."""
    def chunk(tag, data):
        raw = tag + data
        return (struct.pack(">I", len(data)) + raw
                + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF))
    rows = bytearray()
    for y in range(height):
        rows.append(0)                                   # filter: none
        for x in range(width):
            r, g, b = pixel(x, y)
            rows.append(r & 0xFF)
            rows.append(g & 0xFF)
            rows.append(b & 0xFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def _px_blocks(x, y):
    """Block-diagram-ish pattern: colored tiles with dark grid lines."""
    if x % 24 in (0, 23) or y % 16 in (0, 15):
        return (40, 44, 52)
    tile = (x // 24 + y // 16) % 3
    return [(96, 148, 210), (222, 178, 96), (128, 190, 128)][tile]


def _px_waves(x, y):
    """Waveform-ish pattern: horizontal bands with a square-wave trace."""
    hi = (x // 12) % 2 == 0
    trace = 8 if hi else 24
    band = y % 32
    if abs(band - trace) <= 1 or (x % 12 == 0 and 8 <= band <= 24):
        return (200, 60, 60)
    return (245, 245, 240) if (y // 32) % 2 == 0 else (230, 234, 240)


def _px_grid(x, y):
    """Plot-ish pattern: gradient with grid dots."""
    if x % 10 == 0 or y % 10 == 0:
        return (180, 180, 190)
    return ((x * 2) % 256, (y * 3) % 256, ((x + y) * 2) % 256)


PNGS = (
    ("clock-tree.png", 120, 80, _px_blocks),
    ("lock-wave.png", 120, 64, _px_waves),
    ("cov-plot.png", 100, 100, _px_grid),
)


# ------------------------------------------------------------- OOXML: docProps

def core_xml(title, subject):
    # type: (str, str) -> str
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties'
        ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>%s</dc:title><dc:subject>%s</dc:subject>'
        '<dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
        '</cp:coreProperties>'
        % (xesc(title), xesc(subject), xesc(COMPANY), xesc(COMPANY), STAMP, STAMP))


APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'extended-properties"><Application>doc2md-eval-generator</Application></Properties>')

PKG_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/officeDocument" Target="%s"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    '</Relationships>')

_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def content_types(overrides, defaults=()):
    # type: (list, tuple) -> str
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<Types xmlns="%s">' % _CT_NS,
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
             'package.relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>']
    for ext, ct in defaults:
        parts.append('<Default Extension="%s" ContentType="%s"/>' % (ext, ct))
    for name, ct in overrides:
        parts.append('<Override PartName="%s" ContentType="%s"/>' % (name, ct))
    parts.append('</Types>')
    return "".join(parts)


# ----------------------------------------------------------------- docx pieces

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_DRAW = ('xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
         'wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/'
         'drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/'
         'drawingml/2006/picture"')


def w_run(text, bold=False):
    # type: (str, bool) -> str
    pr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (pr, xesc(text))


def w_p(content, style=None, num=None):
    # type: (str, str, tuple) -> str
    """A w:p around pre-built run XML; ``num`` is (numId, ilvl)."""
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % style)
    if num:
        ppr.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>'
                   % (num[1], num[0]))
    pre = ("<w:pPr>%s</w:pPr>" % "".join(ppr)) if ppr else ""
    return "<w:p>%s%s</w:p>" % (pre, content)


def w_text_p(text, style=None, num=None):
    # type: (str, str, tuple) -> str
    return w_p(w_run(text), style=style, num=num)


def w_image_p(rid, name, cx, cy, docpr_id):
    # type: (str, str, int, int, int) -> str
    """A paragraph holding one inline picture (DrawingML)."""
    return w_p(
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="%d" cy="%d"/>'
        '<wp:docPr id="%d" name="%s"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
        'drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="%d" name="%s"/><pic:cNvPicPr/>'
        '</pic:nvPicPr><pic:blipFill><a:blip r:embed="%s"/><a:stretch>'
        '<a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm>'
        '<a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
        % (cx, cy, docpr_id, xesc(name), docpr_id, xesc(name), rid, cx, cy))


def w_tc(text, span=0, vmerge=""):
    # type: (str, int, str) -> str
    """A table cell. ``vmerge`` is "" | "restart" | "cont"."""
    pr = []
    if span > 1:
        pr.append('<w:gridSpan w:val="%d"/>' % span)
    if vmerge == "restart":
        pr.append('<w:vMerge w:val="restart"/>')
    elif vmerge == "cont":
        pr.append('<w:vMerge/>')
    tcpr = ("<w:tcPr>%s</w:tcPr>" % "".join(pr)) if pr else ""
    body = w_text_p(text) if text else "<w:p/>"
    return "<w:tc>%s%s</w:tc>" % (tcpr, body)


def w_table(rows, col_widths):
    # type: (list, list) -> str
    """A bordered w:tbl; ``rows`` is a list of lists of pre-built w:tc XML."""
    border = ('<w:tblBorders>'
              + "".join('<w:%s w:val="single" w:sz="4" w:color="404040"/>' % side
                        for side in ("top", "left", "bottom", "right",
                                     "insideH", "insideV"))
              + '</w:tblBorders>')
    grid = "".join('<w:gridCol w:w="%d"/>' % w for w in col_widths)
    trs = "".join("<w:tr>%s</w:tr>" % "".join(cells) for cells in rows)
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>%s</w:tblPr>'
            '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (border, grid, trs))


def docx_styles():
    # type: () -> str
    """Heading 1-4 + Normal, with real formatting so a LibreOffice PDF render
    shows a size/weight hierarchy docling's layout model can pick up."""
    heads = []
    sizes = {1: 34, 2: 30, 3: 26, 4: 24}          # half-points
    for lvl in (1, 2, 3, 4):
        heads.append(
            '<w:style w:type="paragraph" w:styleId="Heading%d">'
            '<w:name w:val="heading %d"/><w:basedOn w:val="Normal"/>'
            '<w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
            '<w:outlineLvl w:val="%d"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr></w:style>'
            % (lvl, lvl, lvl - 1, sizes[lvl], sizes[lvl]))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:styles %s><w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/>'
        '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style>%s</w:styles>'
        % (_W, "".join(heads)))


def docx_numbering():
    # type: () -> str
    """numId 1 = bullets (2 levels), numId 2 = decimal (2 levels)."""
    def lvl(ilvl, fmt, text):
        return ('<w:lvl w:ilvl="%d"><w:start w:val="1"/><w:numFmt w:val="%s"/>'
                '<w:lvlText w:val="%s"/><w:lvlJc w:val="left"/>'
                '<w:pPr><w:ind w:left="%d" w:hanging="360"/></w:pPr></w:lvl>'
                % (ilvl, fmt, text, 720 * (ilvl + 1)))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:numbering %s>'
        '<w:abstractNum w:abstractNumId="0">%s%s</w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1">%s%s</w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
        '</w:numbering>'
        % (_W,
           lvl(0, "bullet", "•"), lvl(1, "bullet", "◦"),
           lvl(0, "decimal", "%1."), lvl(1, "decimal", "%1.%2.")))


def docx_header():
    # type: () -> str
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:hdr %s>%s</w:hdr>'
            % (_W, w_text_p("%s Confidential — Project %s"
                            % (COMPANY, PROJECT))))


def docx_footer():
    # type: () -> str
    """Footer with a live PAGE field so the rendered PDF carries page numbers."""
    page_field = (
        '<w:r><w:t xml:space="preserve">%s clock spec — page </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>' % PROJECT)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:ftr %s>%s</w:ftr>' % (_W, w_p(page_field)))


# The post-TOC first heading. The eval asserts this survives everywhere; it once
# regressed when the TOC skip consumed one line too many.
SPEC_FIRST_HEADING = "1 Introduction"

# Long code-style identifiers with underscores + CamelCase — exercise markdown
# escaping in the office lane and split-token repair in the PDF lane (a layout
# model wraps them inside narrow table cells).
IDENT_ROWS = [
    ("ClkGateCtrl_enable_sync", "0x00", "RW",
     "Two-flop synchronised enable for the gate cell"),
    ("ClkGateCtrl_bypass_en", "0x04", None,          # None -> vMerge continuation
     "Bypass path used during scan shift"),
    ("DmaArbiterUnit_qos_cfg", "0x00", "RW",
     "Per-initiator quality-of-service weights"),
    ("PllLockMon_status_q", "0x08", "RO",
     "Sticky lock indicator, write one to clear"),
    ("XbarRouteCfg_prio_map", "0x0C", "RW",
     "Crossbar priority map for northbound traffic"),
]

SPEC_TOC = [
    "1 Introduction .......... 3",
    "1.1 Scope .......... 3",
    "1.2 Reference documents .......... 3",
    "2 Clock architecture .......... 4",
    "2.1 PLL configuration .......... 4",
    "2.1.1 Lock detection .......... 5",
    "2.1.1.1 Timing budget .......... 5",
    "3 Register map .......... 6",
    "4 Verification plan .......... 7",
]


def spec_docx_body():
    # type: () -> str
    """The body of the flagship spec docx (see evals/README.md feature matrix)."""
    b = []
    # -- Table of contents: plain paragraphs with dot leaders. Real content
    #    starts on the IMMEDIATELY following block (no filler paragraph): the
    #    outline's TOC skip must stop exactly at the last entry.
    b.append(w_text_p("Table of Contents"))
    for entry in SPEC_TOC:
        b.append(w_text_p(entry))
    # -- 1 Introduction (4-level heading hierarchy starts here)
    b.append(w_text_p(SPEC_FIRST_HEADING, style="Heading1"))
    b.append(w_text_p(
        "This specification describes the clock distribution subsystem of the "
        "Kestrel platform developed by Nimbus Semiconductor. The clk_ref_sel "
        "field selects between the crystal oscillator and the external "
        "reference; driving <rst_n> low forces the safe default. Gating "
        "decisions are *not* latched during scan | test modes, and the R&D "
        "bring-up board exposes every strap."))
    b.append(w_text_p("1.1 Scope", style="Heading2"))
    # A word split across two runs at a format boundary (Word does this
    # constantly): the converter must join them with no inserted space.
    b.append(w_p(w_run("The arbiter sub-block Dma") + w_run("ArbiterUnit", bold=True)
                 + w_run(" owns quality-of-service accounting for every "
                         "initiator port.")))
    b.append(w_text_p("1.2 Reference documents", style="Heading2"))
    b.append(w_text_p("Kestrel platform integration guide", num=(1, 0)))
    b.append(w_text_p("Chapter 4: clocks and resets", num=(1, 1)))
    b.append(w_text_p("Chapter 7: power domains", num=(1, 1)))
    b.append(w_text_p("Nimbus house rules for register naming", num=(1, 0)))
    # -- 2 Clock architecture
    b.append(w_text_p("2 Clock architecture", style="Heading1"))
    b.append(w_text_p(
        "Figure 1 shows the top level clock tree. Every leaf gate is owned by "
        "one ClkGateCtrl instance; the spine is balanced to under 40 ps of skew."))
    b.append(w_image_p("rId10", "clock-tree.png", 2286000, 1524000, 1))
    b.append(w_text_p("2.1 PLL configuration", style="Heading2"))
    b.append(w_text_p("Program the feedback divider.", num=(2, 0)))
    b.append(w_text_p("Wait for the lock counter to saturate.", num=(2, 0)))
    b.append(w_text_p("Poll PllLockMon_status_q until it reads one.", num=(2, 1)))
    b.append(w_text_p("Release the downstream gates.", num=(2, 0)))
    b.append(w_text_p("2.1.1 Lock detection", style="Heading3"))
    b.append(w_text_p(
        "The lock monitor counts reference cycles in a sliding window and "
        "compares the feedback edge position against the budget below."))
    b.append(w_image_p("rId11", "lock-wave.png", 2286000, 1219200, 2))
    b.append(w_text_p("2.1.1.1 Timing budget", style="Heading4"))
    b.append(w_text_p(
        "The budget allows 120 microseconds from cold start to a stable lock "
        "indication, including the two-flop synchroniser on the status output."))
    # -- 3 Register map (merged cells: gridSpan group rows + a vMerge column)
    b.append(w_text_p("3 Register map", style="Heading1"))
    b.append(w_text_p(
        "All registers are 32 bits wide and byte addressed. Group rows span "
        "the full table width; repeated access types merge vertically."))
    rows = [[w_tc("Field"), w_tc("Offset"), w_tc("Access"), w_tc("Description")],
            [w_tc("ClkGateCtrl block (base 0x4000)", span=4)]]
    for name, offset, access, desc in IDENT_ROWS[:2]:
        rows.append([w_tc(name), w_tc(offset),
                     w_tc(access or "", vmerge="restart" if access else "cont"),
                     w_tc(desc)])
    rows.append([w_tc("DmaArbiterUnit block (base 0x5000)", span=4)])
    for name, offset, access, desc in IDENT_ROWS[2:]:
        rows.append([w_tc(name), w_tc(offset), w_tc(access or ""), w_tc(desc)])
    b.append(w_table(rows, [2900, 1200, 1100, 4000]))
    # -- 4 Verification plan
    b.append(w_text_p("4 Verification plan", style="Heading1"))
    b.append(w_text_p("Directed tests for every register reset value.", num=(1, 0)))
    b.append(w_text_p("Constrained-random traffic across the crossbar.", num=(1, 0)))
    b.append(w_text_p("Skew sign-off against the extracted netlist.", num=(1, 0)))
    b.append(w_image_p("rId12", "cov-plot.png", 1828800, 1828800, 3))
    b.append(w_text_p(
        "Coverage closure is tracked per milestone; the plot above shows the "
        "trend for the last six weekly regressions."))
    return "".join(b)


def build_spec_docx(path):
    # type: (str) -> None
    sect = ('<w:sectPr>'
            '<w:headerReference w:type="default" r:id="rId3"/>'
            '<w:footerReference w:type="default" r:id="rId4"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
            ' w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:document %s %s %s><w:body>%s%s</w:body></w:document>'
                % (_W, _R, _DRAW, spec_docx_body(), sect))
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/header" Target="header1.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        + "".join('<Relationship Id="rId1%d" Type="http://schemas.openxmlformats'
                  '.org/officeDocument/2006/relationships/image" '
                  'Target="media/%s"/>' % (i, name)
                  for i, (name, _w, _h, _fn) in enumerate(PNGS))
        + '</Relationships>')
    wp = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    ct = content_types(
        [("/word/document.xml", wp + ".document.main+xml"),
         ("/word/styles.xml", wp + ".styles+xml"),
         ("/word/numbering.xml", wp + ".numbering+xml"),
         ("/word/header1.xml", wp + ".header+xml"),
         ("/word/footer1.xml", wp + ".footer+xml"),
         ("/docProps/core.xml",
          "application/vnd.openxmlformats-package.core-properties+xml"),
         ("/docProps/app.xml",
          "application/vnd.openxmlformats-officedocument.extended-properties+xml")],
        defaults=(("png", "image/png"),))
    entries = [
        ("[Content_Types].xml", ct),
        ("_rels/.rels", PKG_RELS % "word/document.xml"),
        ("word/document.xml", document),
        ("word/_rels/document.xml.rels", rels),
        ("word/styles.xml", docx_styles()),
        ("word/numbering.xml", docx_numbering()),
        ("word/header1.xml", docx_header()),
        ("word/footer1.xml", docx_footer()),
        ("docProps/core.xml", core_xml("Kestrel clock subsystem specification",
                                       "clocks")),
        ("docProps/app.xml", APP_XML),
    ]
    for name, w, h, fn in PNGS:
        entries.append(("word/media/" + name, png_bytes(w, h, fn)))
    write_zip(path, entries)


def build_minimal_docx(path):
    # type: (str) -> None
    """The trivial happy path: one heading, two paragraphs, nothing else."""
    body = (w_text_p("Kestrel bring-up quick notes", style="Heading1")
            + w_text_p("Power the board from the bench supply before "
                       "connecting the debugger pod.")
            + w_text_p("The default strap settings boot from the internal ROM."))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:document %s %s><w:body>%s</w:body></w:document>'
                % (_W, _R, body))
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>')
    wp = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    ct = content_types(
        [("/word/document.xml", wp + ".document.main+xml"),
         ("/word/styles.xml", wp + ".styles+xml"),
         ("/docProps/core.xml",
          "application/vnd.openxmlformats-package.core-properties+xml"),
         ("/docProps/app.xml",
          "application/vnd.openxmlformats-officedocument.extended-properties+xml")])
    write_zip(path, [
        ("[Content_Types].xml", ct),
        ("_rels/.rels", PKG_RELS % "word/document.xml"),
        ("word/document.xml", document),
        ("word/_rels/document.xml.rels", rels),
        ("word/styles.xml", docx_styles()),
        ("docProps/core.xml", core_xml("Kestrel bring-up quick notes", "bring-up")),
        ("docProps/app.xml", APP_XML),
    ])


# ----------------------------------------------------------------- xlsx pieces

_SS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _cell(ref, value, ctype="n"):
    # type: (str, str, str) -> str
    """One <c>: n(umber) | s(hared idx) | inline | b(ool) | f:formula,cached."""
    if ctype == "inline":
        return ('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                % (ref, xesc(value)))
    if ctype == "b":
        return '<c r="%s" t="b"><v>%s</v></c>' % (ref, value)
    if ctype == "s":
        return '<c r="%s" t="s"><v>%s</v></c>' % (ref, value)
    return '<c r="%s"><v>%s</v></c>' % (ref, xesc(value))


def _fcell(ref, formula, cached):
    # type: (str, str, str) -> str
    return '<c r="%s"><f>%s</f><v>%s</v></c>' % (ref, xesc(formula), cached)


def _sheet(rows_xml, merges=None):
    # type: (str, list) -> str
    m = ""
    if merges:
        m = ('<mergeCells count="%d">%s</mergeCells>'
             % (len(merges), "".join('<mergeCell ref="%s"/>' % r for r in merges)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet %s><sheetData>%s</sheetData>%s</worksheet>'
            % (_SS, rows_xml, m))


def build_registers_xlsx(path):
    # type: (str) -> None
    """3 sheets: a wide typed table (shared strings), a merged+formula sheet
    (inline strings), and a nearly-empty sheet."""
    shared = ["Register", "Offset", "Width", "Reset", "Access", "Gated",
              "Updated", "Scale", "RW", "RO"]
    sidx = dict((s, i) for i, s in enumerate(shared))

    def srow(n, cells):
        return '<row r="%d">%s</row>' % (n, "".join(cells))

    # Sheet 1: RegisterMap — header via shared strings, typed data columns
    # (hex-string offsets, integer widths, float resets, bool, ISO-date string).
    rows = [srow(1, [_cell("A1", str(sidx["Register"]), "s"),
                     _cell("B1", str(sidx["Offset"]), "s"),
                     _cell("C1", str(sidx["Width"]), "s"),
                     _cell("D1", str(sidx["Reset"]), "s"),
                     _cell("E1", str(sidx["Access"]), "s"),
                     _cell("F1", str(sidx["Gated"]), "s"),
                     _cell("G1", str(sidx["Updated"]), "s"),
                     _cell("H1", str(sidx["Scale"]), "s")])]
    data = [
        ("ClkGateCtrl_enable_sync", "0x4000", "32", "0", "RW", "1",
         "2026-01-05", "1.0"),
        ("ClkGateCtrl_bypass_en", "0x4004", "32", "0", "RW", "1",
         "2026-01-05", "1.0"),
        ("DmaArbiterUnit_qos_cfg", "0x5000", "32", "255", "RW", "0",
         "2026-01-12", "0.125"),
        ("PllLockMon_status_q", "0x5008", "32", "0", "RO", "0",
         "2026-01-19", "1.0"),
        ("XbarRouteCfg_prio_map", "0x500C", "32", "3735928559", "RW", "0",
         "2026-01-26", "0.5"),
    ]
    for i, (name, off, width, reset, acc, gated, upd, scale) in enumerate(data):
        r = i + 2
        rows.append(srow(r, [
            _cell("A%d" % r, name, "inline"),
            _cell("B%d" % r, off, "inline"),
            _cell("C%d" % r, width),
            _cell("D%d" % r, reset),
            _cell("E%d" % r, str(sidx[acc]), "s"),
            _cell("F%d" % r, gated, "b"),
            _cell("G%d" % r, upd, "inline"),
            _cell("H%d" % r, scale)]))
    sheet1 = _sheet("".join(rows))

    # Sheet 2: PowerBudget — merged title row + SUM formula with cached value.
    rows2 = [
        srow(1, [_cell("A1", "Kestrel power budget (mW)", "inline")]),
        srow(2, [_cell("A2", "Rail", "inline"), _cell("B2", "Block", "inline"),
                 _cell("C2", "Milliwatts", "inline")]),
        srow(3, [_cell("A3", "VDD_CORE", "inline"),
                 _cell("B3", "DmaArbiterUnit", "inline"), _cell("C3", "182.5")]),
        srow(4, [_cell("A4", "VDD_CORE", "inline"),
                 _cell("B4", "ClkGateCtrl", "inline"), _cell("C4", "96.25")]),
        srow(5, [_cell("A5", "VDD_IO", "inline"),
                 _cell("B5", "pad ring", "inline"), _cell("C5", "133.75")]),
        srow(6, [_cell("A6", "Total", "inline"),
                 _fcell("C6", "SUM(C3:C5)", "412.5")]),
    ]
    sheet2 = _sheet("".join(rows2), merges=["A1:C1", "A3:A4"])

    # Sheet 3: nearly empty.
    sheet3 = _sheet(srow(1, [_cell("A1", "Reserved for bring-up notes.", "inline")]))

    sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<sst %s count="%d" uniqueCount="%d">%s</sst>'
           % (_SS, len(shared), len(shared),
              "".join('<si><t xml:space="preserve">%s</t></si>' % xesc(s)
                      for s in shared)))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook %s %s><sheets>'
        '<sheet name="RegisterMap" sheetId="1" r:id="rId1"/>'
        '<sheet name="PowerBudget" sheetId="2" r:id="rId2"/>'
        '<sheet name="Notes" sheetId="3" r:id="rId3"/>'
        '</sheets></workbook>' % (_SS, _R))
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats'
                  '.org/officeDocument/2006/relationships/worksheet" '
                  'Target="worksheets/sheet%d.xml"/>' % (i, i)
                  for i in (1, 2, 3))
        + '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships/sharedStrings" '
          'Target="sharedStrings.xml"/>'
          '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
          '</Relationships>')
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet %s><fonts count="1"><font><sz val="10"/>'
        '<name val="Liberation Sans"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" xfId="0"/></cellXfs>'
        '</styleSheet>' % _SS)
    sp = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    ct = content_types(
        [("/xl/workbook.xml", sp + ".sheet.main+xml"),
         ("/xl/worksheets/sheet1.xml", sp + ".worksheet+xml"),
         ("/xl/worksheets/sheet2.xml", sp + ".worksheet+xml"),
         ("/xl/worksheets/sheet3.xml", sp + ".worksheet+xml"),
         ("/xl/sharedStrings.xml", sp + ".sharedStrings+xml"),
         ("/xl/styles.xml", sp + ".styles+xml"),
         ("/docProps/core.xml",
          "application/vnd.openxmlformats-package.core-properties+xml"),
         ("/docProps/app.xml",
          "application/vnd.openxmlformats-officedocument.extended-properties+xml")])
    write_zip(path, [
        ("[Content_Types].xml", ct),
        ("_rels/.rels", PKG_RELS % "xl/workbook.xml"),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", wb_rels),
        ("xl/worksheets/sheet1.xml", sheet1),
        ("xl/worksheets/sheet2.xml", sheet2),
        ("xl/worksheets/sheet3.xml", sheet3),
        ("xl/sharedStrings.xml", sst),
        ("xl/styles.xml", styles),
        ("docProps/core.xml", core_xml("Kestrel register workbook", "registers")),
        ("docProps/app.xml", APP_XML),
    ])


# ----------------------------------------------------------------- pptx pieces

_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _theme():
    # type: () -> str
    def clr(name, val):
        return '<a:%s><a:srgbClr val="%s"/></a:%s>' % (name, val, name)
    scheme = (
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        + clr("dk2", "1F3B4D") + clr("lt2", "EEECE1")
        + clr("accent1", "4F81BD") + clr("accent2", "C0504D")
        + clr("accent3", "9BBB59") + clr("accent4", "8064A2")
        + clr("accent5", "4BACC6") + clr("accent6", "F79646")
        + clr("hlink", "0000FF") + clr("folHlink", "800080"))
    font = ('<a:latin typeface="Liberation Sans"/><a:ea typeface=""/>'
            '<a:cs typeface=""/>')
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    ln = ('<a:ln w="9525" cap="flat"><a:solidFill><a:schemeClr val="phClr"/>'
          '</a:solidFill><a:prstDash val="solid"/></a:ln>')
    eff = '<a:effectStyle><a:effectLst/></a:effectStyle>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<a:theme %s name="doc2md-eval"><a:themeElements>'
        '<a:clrScheme name="doc2md-eval">%s</a:clrScheme>'
        '<a:fontScheme name="doc2md-eval"><a:majorFont>%s</a:majorFont>'
        '<a:minorFont>%s</a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="doc2md-eval">'
        '<a:fillStyleLst>%s%s%s</a:fillStyleLst>'
        '<a:lnStyleLst>%s%s%s</a:lnStyleLst>'
        '<a:effectStyleLst>%s%s%s</a:effectStyleLst>'
        '<a:bgFillStyleLst>%s%s%s</a:bgFillStyleLst>'
        '</a:fmtScheme></a:themeElements></a:theme>'
        % (_A, scheme, font, font, fill, fill, fill, ln, ln, ln,
           eff, eff, eff, fill, fill, fill))


_EMPTY_TREE = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
               '</p:nvGrpSpPr><p:grpSpPr/>')


def _sp(sid, name, text_paras, ph=None, pos=None, fill=None):
    # type: (int, str, list, str, tuple, str) -> str
    """A text shape. ``text_paras`` is [(level, text)]; ``ph`` a placeholder
    type; ``pos`` (x, y, cx, cy) EMU; ``fill`` an srgb hex for diagram boxes."""
    nvpr = "<p:nvPr>%s</p:nvPr>" % ('<p:ph type="%s"/>' % ph if ph else "")
    if ph == "body":
        nvpr = '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
    sppr = ""
    if pos:
        geom = ('<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
                if fill else '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>')
        f = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
             '<a:ln><a:solidFill><a:srgbClr val="404040"/></a:solidFill></a:ln>'
             % fill) if fill else ""
        sppr = ('<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>%s%s'
                % (pos[0], pos[1], pos[2], pos[3], geom, f))
    paras = []
    for lvl, text in text_paras:
        ppr = '<a:pPr lvl="%d"/>' % lvl if lvl else ""
        paras.append('<a:p>%s<a:r><a:t>%s</a:t></a:r></a:p>' % (ppr, xesc(text)))
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/>%s</p:nvSpPr>'
            '<p:spPr>%s</p:spPr><p:txBody><a:bodyPr/>%s</p:txBody></p:sp>'
            % (sid, xesc(name), nvpr, sppr, "".join(paras)))


def _slide(shapes_xml):
    # type: (str) -> str
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld %s %s %s><p:cSld><p:spTree>%s%s</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
            % (_P, _A, _R, _EMPTY_TREE, shapes_xml))


def _slide_rels(extra=""):
    # type: (str) -> str
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>%s</Relationships>' % extra)


def _pptx_table(sid, header, rows, pos):
    # type: (int, list, list, tuple) -> str
    ncols = len(header)
    colw = pos[2] // ncols

    def tr(cells, h):
        tcs = "".join('<a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:t>%s</a:t></a:r>'
                      '</a:p></a:txBody><a:tcPr/></a:tc>' % xesc(c) for c in cells)
        return '<a:tr h="%d">%s</a:tr>' % (h, tcs)
    grid = "".join('<a:gridCol w="%d"/>' % colw for _ in range(ncols))
    body = tr(header, 370840) + "".join(tr(r, 370840) for r in rows)
    return ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name="Table"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
            'drawingml/2006/table"><a:tbl><a:tblPr firstRow="1"/>'
            '<a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>'
            % (sid, pos[0], pos[1], pos[2], pos[3], grid, body))


def _dataflow_shapes():
    # type: () -> list
    """Diagram-only content: labelled boxes + tiny edge labels, no title, almost
    no prose — the known structural-validator edge case once rendered to PDF."""
    boxes = [
        ("sensor front end", 457200, 1600200, "4F81BD"),
        ("sample queue", 3200400, 1600200, "9BBB59"),
        ("dsp core", 5943600, 1600200, "C0504D"),
        ("result mailbox", 3200400, 4114800, "8064A2"),
        ("host bridge", 5943600, 4114800, "4BACC6"),
    ]
    shapes = []
    for i, (label, x, y, fill) in enumerate(boxes):
        shapes.append(_sp(10 + i, "box%d" % i, [(0, label)],
                          pos=(x, y, 2400300, 1000125), fill=fill))
    shapes.append(_sp(20, "edge0", [(0, "push")], pos=(2895600, 1233805, 762000, 400000)))
    shapes.append(_sp(21, "edge1", [(0, "pop")], pos=(5638800, 1233805, 762000, 400000)))
    return shapes


def _pptx_package(path, slides, notes, title, subject):
    # type: (str, list, dict, str, str) -> None
    """Assemble a pptx: ``slides`` is a list of slide XML strings (1-based
    order), ``notes`` maps slide number -> notes text."""
    n = len(slides)
    pp = "application/vnd.openxmlformats-officedocument.presentationml"
    overrides = [("/ppt/presentation.xml", pp + ".presentation.main+xml"),
                 ("/ppt/slideMasters/slideMaster1.xml", pp + ".slideMaster+xml"),
                 ("/ppt/slideLayouts/slideLayout1.xml", pp + ".slideLayout+xml"),
                 ("/ppt/theme/theme1.xml",
                  "application/vnd.openxmlformats-officedocument.theme+xml")]
    for i in range(1, n + 1):
        overrides.append(("/ppt/slides/slide%d.xml" % i, pp + ".slide+xml"))
    for i in sorted(notes):
        overrides.append(("/ppt/notesSlides/notesSlide%d.xml" % i,
                          pp + ".notesSlide+xml"))
    overrides += [("/docProps/core.xml",
                   "application/vnd.openxmlformats-package.core-properties+xml"),
                  ("/docProps/app.xml",
                   "application/vnd.openxmlformats-officedocument."
                   "extended-properties+xml")]

    pres = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:presentation %s %s %s>'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
        '</p:sldMasterIdLst><p:sldIdLst>%s</p:sldIdLst>'
        '<p:sldSz cx="9144000" cy="6858000"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
        % (_P, _A, _R,
           "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, 2 + i)
                   for i in range(n))))
    pres_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
        'Target="slideMasters/slideMaster1.xml"/>'
        + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats'
                  '.org/officeDocument/2006/relationships/slide" '
                  'Target="slides/slide%d.xml"/>' % (2 + i, 1 + i)
                  for i in range(n))
        + '</Relationships>')
    master = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sldMaster %s %s %s><p:cSld><p:bg><p:bgPr><a:solidFill>'
        '<a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree>%s</p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
        '</p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/>'
        '<p:otherStyle/></p:txStyles></p:sldMaster>'
        % (_P, _A, _R, _EMPTY_TREE))
    master_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
        'Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        '</Relationships>')
    layout = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sldLayout %s %s %s type="blank"><p:cSld><p:spTree>%s</p:spTree>'
        '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
        % (_P, _A, _R, _EMPTY_TREE))
    layout_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
        'Target="../slideMasters/slideMaster1.xml"/></Relationships>')

    entries = [
        ("[Content_Types].xml", content_types(overrides)),
        ("_rels/.rels", PKG_RELS % "ppt/presentation.xml"),
        ("ppt/presentation.xml", pres),
        ("ppt/_rels/presentation.xml.rels", pres_rels),
        ("ppt/slideMasters/slideMaster1.xml", master),
        ("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels),
        ("ppt/slideLayouts/slideLayout1.xml", layout),
        ("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels),
        ("ppt/theme/theme1.xml", _theme()),
    ]
    for i, slide_xml in enumerate(slides, start=1):
        extra = ""
        if i in notes:
            extra = ('<Relationship Id="rId2" Type="http://schemas.'
                     'openxmlformats.org/officeDocument/2006/relationships/'
                     'notesSlide" Target="../notesSlides/notesSlide%d.xml"/>' % i)
        entries.append(("ppt/slides/slide%d.xml" % i, slide_xml))
        entries.append(("ppt/slides/_rels/slide%d.xml.rels" % i, _slide_rels(extra)))
    for i in sorted(notes):
        notes_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:notes %s %s %s><p:cSld><p:spTree>%s%s</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
            % (_P, _A, _R, _EMPTY_TREE,
               _sp(2, "Notes", [(0, notes[i])], ph="body")))
        entries.append(("ppt/notesSlides/notesSlide%d.xml" % i, notes_xml))
    entries += [("docProps/core.xml", core_xml(title, subject)),
                ("docProps/app.xml", APP_XML)]
    write_zip(path, entries)


def build_overview_pptx(path):
    # type: (str) -> None
    slides = [
        # 1 title slide
        _slide(_sp(2, "Title", [(0, "Kestrel Platform Overview")], ph="ctrTitle",
                   pos=(457200, 1828800, 8229600, 1371600))
               + _sp(3, "Subtitle", [(0, "Nimbus Semiconductor architecture "
                                         "review, spring 2026")], ph="subTitle",
                     pos=(457200, 3429000, 8229600, 914400))),
        # 2 bullets (nested levels)
        _slide(_sp(2, "Title", [(0, "Why a new interconnect")], ph="title",
                   pos=(457200, 274638, 8229600, 1143000))
               + _sp(3, "Body", [(0, "The legacy bus saturates at four initiators"),
                                 (1, "arbitration stalls the display pipe"),
                                 (1, "no per-initiator quality of service"),
                                 (0, "Kestrel moves to a crossbar with QoS weights")],
                     ph="body", pos=(457200, 1600200, 8229600, 4525963))),
        # 3 table
        _slide(_sp(2, "Title", [(0, "Latency targets")], ph="title",
                   pos=(457200, 274638, 8229600, 1143000))
               + _pptx_table(4, ["Path", "Cycles", "Owner"],
                             [["display read", "40", "fabric team"],
                              ["dma write burst", "24", "dma team"],
                              ["cpu fetch miss", "60", "core team"]],
                             (457200, 1600200, 8229600, 2438400))),
        # 4 shapes / diagram-like (no title placeholder)
        _slide("".join(_dataflow_shapes())),
        # 5 bullets + speaker notes
        _slide(_sp(2, "Title", [(0, "Rollout plan")], ph="title",
                   pos=(457200, 274638, 8229600, 1143000))
               + _sp(3, "Body", [(0, "Tape-in freeze at milestone three"),
                                 (0, "Bring-up boards arrive two weeks later"),
                                 (0, "Software stack lands with the beta SDK")],
                     ph="body", pos=(457200, 1600200, 8229600, 4525963))),
    ]
    notes = {5: ("Remind the audience that the timeline assumes silicon back "
                 "in week nine.")}
    _pptx_package(path, slides, notes, "Kestrel platform overview", "overview")


def build_dataflow_pptx(path):
    # type: (str) -> None
    """Shapes-only single slide: source for the diagram-only PDF edge case."""
    _pptx_package(path, [_slide("".join(_dataflow_shapes()))], {},
                  "Kestrel dataflow diagram", "dataflow")


# ------------------------------------------------------------------ text files

TEXT_FILES = {
    # PASSTHROUGH .md — includes a dot-leader TOC whose last entry is IMMEDIATELY
    # followed by the first heading (no blank line): the exact adjacency that once
    # made the outline's TOC skip swallow the document's first heading.
    "text/design-notes.md": u"""Contents
1 Toolchain .......... 2
2 Floorplan checks .......... 3
# 1 Toolchain

The nightly flow pins every tool version in `flow.lock`; update it only from a
green baseline.

```sh
make synth TOP=kestrel_top
make lint SEVERITY=error
```

# 2 Floorplan checks

- verify macro keep-out halos
- verify the clock spine spacing rule
- rerun extraction after any bump map change
""",
    "text/build-log.txt": u"""kestrel nightly build 2026-01-07
synth: ok (warnings: 3)
lint: ok
sta: wns -0.012 ns on clk_main
regression: 412 passed, 0 failed, 2 skipped
""",
    "text/pin-map.csv": u"""pin,ball,bank,function
clk_ref,A4,north,reference clock input
rst_n,B2,north,active-low reset
dma_irq,C7,east,interrupt to host
spi_cs_n,D1,south,boot flash chip select
""",
    "text/timing-report.tsv": u"""path\tslack_ns\tclock\tendpoint
display read\t0.084\tclk_main\tdisp_fifo/wr_ptr_q
dma write burst\t0.121\tclk_main\tdma_arb/gnt_q
cpu fetch miss\t-0.012\tclk_main\ticache/tag_q
""",
    "text/ip-manifest.json": u"""{
  "project": "kestrel",
  "blocks": [
    {"name": "ClkGateCtrl", "version": "1.4.0", "owner": "clocks"},
    {"name": "DmaArbiterUnit", "version": "2.1.3", "owner": "fabric"},
    {"name": "PllLockMon", "version": "1.0.9", "owner": "clocks"}
  ]
}
""",
    "text/ci-pipeline.yaml": u"""stages:
  - lint
  - synth
  - regress

lint:
  stage: lint
  script: make lint SEVERITY=error

regress:
  stage: regress
  script: make regress SUITE=nightly
  timeout: 4h
""",
    # UNSUPPORTED format: no lane owns .tcl — must be reported, never converted.
    "text/synth-flow.tcl": u"""# kestrel synthesis flow
set top kestrel_top
read_verilog [glob rtl/*.v]
synth_design -top $top -flatten_hierarchy rebuilt
report_timing -max_paths 10
""",
}


# ------------------------------------------------------- derived (tool-driven)

def find_soffice():
    # type: () -> str
    """LibreOffice from $DOC2MD_LIBREOFFICE or PATH — never a hardcoded path."""
    cand = os.environ.get("DOC2MD_LIBREOFFICE", "").strip()
    if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def soffice_convert(soffice, src, target_ext, dest, profile_dir):
    # type: (str, str, str, str, str) -> bool
    """Convert ``src`` to ``target_ext`` with LibreOffice and move it to ``dest``."""
    outdir = tempfile.mkdtemp(prefix="doc2md_eval_lo_")
    try:
        cmd = [soffice, "--headless",
               "-env:UserInstallation=file://%s" % profile_dir,
               "--convert-to", target_ext, "--outdir", outdir, src]
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            print("  [derive] soffice FAILED for %s -> %s: %s"
                  % (os.path.basename(src), target_ext, e), file=sys.stderr)
            return False
        produced = os.path.join(
            outdir, os.path.splitext(os.path.basename(src))[0] + "." + target_ext)
        if not os.path.isfile(produced):
            hits = [f for f in os.listdir(outdir)
                    if f.lower().endswith("." + target_ext)]
            if not hits:
                print("  [derive] soffice produced nothing for %s -> %s"
                      % (os.path.basename(src), target_ext), file=sys.stderr)
                return False
            produced = os.path.join(outdir, hits[0])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(produced, dest)
        return True
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def make_scanned_pdf(digital_pdf, dest):
    # type: (str, str) -> bool
    """Rasterize a digital PDF (poppler pdftoppm) and re-wrap the page images
    into an image-only PDF via the PDF-lane interpreter (needs Pillow)."""
    pdf_python = os.environ.get("DOC2MD_PDF_PYTHON", "").strip()
    if not pdf_python:
        print("  [derive] DOC2MD_PDF_PYTHON unset -> skipping the scanned PDF "
              "(set it to a python with Pillow to enable)", file=sys.stderr)
        return False
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        print("  [derive] pdftoppm not on PATH -> skipping the scanned PDF",
              file=sys.stderr)
        return False
    tmp = tempfile.mkdtemp(prefix="doc2md_eval_scan_")
    try:
        try:
            subprocess.check_output(
                [pdftoppm, "-png", "-r", "110", digital_pdf,
                 os.path.join(tmp, "page")],
                stderr=subprocess.STDOUT, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            print("  [derive] pdftoppm FAILED: %s" % e, file=sys.stderr)
            return False
        pages = sorted(glob.glob(os.path.join(tmp, "page*.png")))
        if not pages:
            print("  [derive] pdftoppm produced no pages", file=sys.stderr)
            return False
        wrap = ("import sys\n"
                "from PIL import Image\n"
                "out = sys.argv[1]\n"
                "ims = [Image.open(p).convert('RGB') for p in sys.argv[2:]]\n"
                "ims[0].save(out, format='PDF', save_all=True, "
                "append_images=ims[1:], resolution=110.0)\n")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            subprocess.check_output([pdf_python, "-c", wrap, dest] + pages,
                                    stderr=subprocess.STDOUT, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            print("  [derive] PDF wrap FAILED: %s" % e, file=sys.stderr)
            return False
        return os.path.isfile(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- generator

HANDBUILT = [
    ("office/kestrel-clock-spec.docx", build_spec_docx),
    ("office/kestrel-readme.docx", build_minimal_docx),
    ("office/kestrel-registers.xlsx", build_registers_xlsx),
    ("office/kestrel-overview.pptx", build_overview_pptx),
    ("office/kestrel-dataflow.pptx", build_dataflow_pptx),
]

# (source relpath, soffice target ext, dest relpath)
DERIVED_OFFICE = [
    ("office/kestrel-clock-spec.docx", "doc", "legacy/kestrel-clock-spec.doc"),
    ("office/kestrel-clock-spec.docx", "rtf", "legacy/kestrel-clock-spec.rtf"),
    ("office/kestrel-clock-spec.docx", "odt", "legacy/kestrel-clock-spec.odt"),
    ("office/kestrel-registers.xlsx", "xls", "legacy/kestrel-registers.xls"),
    ("office/kestrel-overview.pptx", "ppt", "legacy/kestrel-overview.ppt"),
    ("office/kestrel-clock-spec.docx", "pdf", "pdf/kestrel-clock-spec.pdf"),
    ("office/kestrel-dataflow.pptx", "pdf", "pdf/kestrel-dataflow.pdf"),
]

SCANNED_PDF = ("pdf/kestrel-clock-spec.pdf", "pdf/kestrel-clock-spec-scan.pdf")


def generate(out_dir, handbuilt_only=False):
    # type: (str, bool) -> dict
    """Build the corpus under ``out_dir``. Returns the manifest dict."""
    manifest = {"generated_with": "evals/gen_corpus.py", "stamp": STAMP,
                "files": {}}

    for rel, builder in HANDBUILT:
        dest = os.path.join(out_dir, rel)
        builder(dest)
        manifest["files"][rel] = {"kind": "handbuilt", "sha256": sha256_file(dest)}
        print("  [handbuilt] %s" % rel, file=sys.stderr)
    for rel, text in sorted(TEXT_FILES.items()):
        dest = os.path.join(out_dir, rel)
        write_text(dest, text)
        manifest["files"][rel] = {"kind": "handbuilt", "sha256": sha256_file(dest)}
        print("  [handbuilt] %s" % rel, file=sys.stderr)

    if handbuilt_only:
        return manifest

    soffice = find_soffice()
    if not soffice:
        print("  [derive] LibreOffice not found (set DOC2MD_LIBREOFFICE or put "
              "soffice on PATH) -> skipping ALL derived formats", file=sys.stderr)
    profile = tempfile.mkdtemp(prefix="doc2md_eval_loprofile_")
    try:
        for src_rel, ext, dest_rel in DERIVED_OFFICE:
            if not soffice:
                manifest["files"][dest_rel] = {"kind": "skipped",
                                               "reason": "libreoffice-unavailable"}
                continue
            src = os.path.join(out_dir, src_rel)
            dest = os.path.join(out_dir, dest_rel)
            if soffice_convert(soffice, src, ext, dest, profile):
                manifest["files"][dest_rel] = {"kind": "derived", "tool": "soffice",
                                               "source": src_rel,
                                               "sha256": sha256_file(dest)}
                print("  [derived]   %s (from %s)" % (dest_rel, src_rel),
                      file=sys.stderr)
            else:
                manifest["files"][dest_rel] = {"kind": "skipped",
                                               "reason": "soffice-convert-failed"}
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    src_rel, dest_rel = SCANNED_PDF
    digital = os.path.join(out_dir, src_rel)
    if os.path.isfile(digital):
        if make_scanned_pdf(digital, os.path.join(out_dir, dest_rel)):
            manifest["files"][dest_rel] = {
                "kind": "derived", "tool": "pdftoppm+pillow", "source": src_rel,
                "sha256": sha256_file(os.path.join(out_dir, dest_rel))}
            print("  [derived]   %s (rasterized %s)" % (dest_rel, src_rel),
                  file=sys.stderr)
        else:
            manifest["files"][dest_rel] = {"kind": "skipped",
                                           "reason": "scan-tools-unavailable"}
    else:
        manifest["files"][dest_rel] = {"kind": "skipped",
                                       "reason": "no-digital-pdf"}
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate the deterministic synthetic eval corpus "
                    "(fictional Nimbus Semiconductor / Kestrel documents).")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "eval_corpus"),
                    help="corpus output root (default data/eval_corpus)")
    ap.add_argument("--handbuilt-only", action="store_true",
                    help="only the hand-built OOXML/text sources (no soffice/"
                         "poppler/Pillow needed)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    manifest = generate(args.out, handbuilt_only=args.handbuilt_only)
    mpath = args.out.rstrip("/\\") + ".manifest.json"
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    built = sum(1 for v in manifest["files"].values() if v["kind"] != "skipped")
    skipped = sum(1 for v in manifest["files"].values() if v["kind"] == "skipped")
    print("corpus: %d file(s) built, %d skipped -> %s (manifest %s)"
          % (built, skipped, args.out, mpath), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
