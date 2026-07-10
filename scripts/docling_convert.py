#!/usr/bin/env python3
"""Offline source -> markdown converter (the docling backend's producer).

Runs UNDER PYTHON 3.12 with docling installed — NOT the 3.6 pipeline. It walks the
source corpus and writes data/markdown/<doc_id>.md, using the SAME id hashing as
build_index.py (backend.ingest.doc_id) so the two halves agree on filenames. The
3.6 pipeline (build_index.py --backend docling) then consumes these markdown files.

Idempotent: skips docs whose .md already exists (use --force to rebuild). Per-doc
failures are logged and skipped — build_index.py falls back to native extraction
for any doc without markdown, so a failure never loses content.

See docs/design/docling-ingestion.md.

Usage:
  python3.12 scripts/docling_convert.py --src "$DOC2MD_SRC" --out data/markdown
  python3.12 scripts/docling_convert.py --dry-run            # plan only, no docling needed
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)   # so this script can import its private _vlm helper
from backend.ingest import (doc_id, gate_figures, caption_is_useful,
                            image_markdown, inline_image_captions, load_ingest_config,
                            coverage, markdown_to_text, figure_outcome, figure_coverage,
                            front_matter, pdf_info_meta, is_lossy_explained,
                            char_ngram_recall, html_to_text, strip_running_lines,
                            words_in_bbox, tokenize, recommend_shards, order_todo,
                            load_source_root, merge_boxes,
                            summarize_routes, normalize_accept, unknown_formats,
                            supported_formats, ROUTE_DOCLING, ROUTE_OOXML,
                            ROUTE_LIBREOFFICE, ROUTE_PASSTHROUGH, ROUTE_FENCE)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    import socket as _socket
    _HOSTNAME = _socket.gethostname()
except Exception:
    _HOSTNAME = ""
# Formats docling owns are NOT hardcoded here — the router (backend.ingest.route_format)
# is the single source of truth. This lane converts exactly ROUTE_DOCLING (pdf/html/htm):
# layout formats whose structure must be INFERRED. Office (docx/pptx/xlsx + ODF/legacy)
# travels the deterministic OOXML lane (office_convert.py); md/txt + json/yaml/csv travel
# the passthrough/fence lane (text_convert.py). One format, one owner. docling retains a
# verbatim md branch ONLY for the routing-exempt --only escalation, never normal ingest.

# docling statuses that mean the conversion did NOT fully succeed -> re-convert.
_DOCLING_BAD_STATUS = ("FAILURE", "PARTIAL_SUCCESS")

# Resolved config holder. main() sets it from load_ingest_config() (honoring env/toml);
# helpers fall back to a lazily-loaded default config so NO threshold is hardcoded here
# and direct helper calls (tests) still get proper defaults.
_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = load_ingest_config()
    return _CFG


def _probe_resources():
    """(cpu_cores, mem_available_gb, gpu_desc) from the actual machine at run time."""
    cpu = os.cpu_count() or 1
    mem_gb = 0.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_gb = int(line.split()[1]) / (1024.0 * 1024.0)   # kB -> GiB
                    break
    except Exception:
        pass
    gpu = "none"
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL, timeout=5).decode("utf-8", "replace").strip()
        if out:
            gpu = "; ".join(out.splitlines())
    except Exception:
        pass
    return cpu, mem_gb, gpu


def find_sources(src_root, accept=None):
    """Docling-lane sources under src_root, plus a whole-tree RouteScan.

    Returns ``(sources, scan)`` where ``sources`` is the sorted ``[(abs, rel)]`` of files
    the router assigns to the docling lane (pdf/html/htm) AND allowed by ``accept``; the
    ``scan`` buckets everything else (office -> OOXML lane, passthrough/fence -> text lane,
    plus unsupported / accept-declined) so ``main`` can warn. One owner per format — md/txt
    and json/yaml are deliberately NOT claimed here."""
    rel_of = {}
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            full = os.path.join(root, fn)
            rel_of[os.path.relpath(full, src_root)] = full
    scan = summarize_routes(sorted(rel_of), accept)
    out = [(rel_of[rel], rel) for rel in scan.by_lane.get(ROUTE_DOCLING, [])]
    out.sort(key=lambda t: t[1])
    return out, scan


def _ext_counts(names):
    """"pdf(3), html(2)" — per-extension counts of a filename list, biggest first."""
    from collections import Counter
    c = Counter((os.path.splitext(n)[1].lstrip(".").lower() or "no-ext") for n in names)
    return ", ".join("%s(%d)" % (e, n) for e, n in c.most_common())


def _warn_unrouted(scan):
    """Report, in ONE place, every file this run will NOT convert: office files (owned by
    office_convert.py), passthrough/fence files (owned by text_convert.py), accept-declined
    files, and genuinely unsupported formats — so nothing is silently dropped."""
    office = scan.by_lane.get(ROUTE_OOXML, []) + scan.by_lane.get(ROUTE_LIBREOFFICE, [])
    if office:
        print("  [note] %d office file(s) belong to the OOXML lane (run scripts/office_convert.py): %s"
              % (len(office), _ext_counts(office)), file=sys.stderr)
    textlane = scan.by_lane.get(ROUTE_PASSTHROUGH, []) + scan.by_lane.get(ROUTE_FENCE, [])
    if textlane:
        print("  [note] %d markdown/text/data file(s) belong to the passthrough/fence lane "
              "(run scripts/text_convert.py): %s" % (len(textlane), _ext_counts(textlane)),
              file=sys.stderr)
    if scan.declined:
        print("  [skip] %d file(s) excluded by the accept-list -> NOT converted: %s"
              % (len(scan.declined), _ext_counts(scan.declined)), file=sys.stderr)
    if scan.unsupported:
        print("  [WARNING] %d file(s) in UNSUPPORTED formats will NOT be converted by any "
              "lane: %s" % (len(scan.unsupported), _ext_counts(scan.unsupported)), file=sys.stderr)


def plan(sources, out_dir, force):
    """Decide, per source, the target .md path and whether to (re)convert.

    Returns a list of dicts: {rel, src, dest, skip}. Pure (no docling, no writes)
    so it is unit/e2e testable and drives --dry-run.
    """
    rows = []
    for full, rel in sources:
        did = doc_id(rel)
        dest = os.path.join(out_dir, did + ".md")
        skip = (not force) and os.path.isfile(dest)
        rows.append({"id": did, "rel": rel, "src": full, "dest": dest, "skip": skip})
    return rows


def _pdf_page_count(path):
    """Total page count via ``pdfinfo``, or 0 if unavailable/unparseable (cheap, ms)."""
    try:
        r = subprocess.run(["pdfinfo", path], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[1])
    except Exception:
        pass
    return 0


def _window_cpp(path, first, last, timeout=60):
    """Mean chars-per-page over pages [first, last] via one pdftotext call, or None on
    failure. Pages counted by form-feeds; whitespace stripped."""
    r = subprocess.run(
        ["pdftotext", "-q", "-f", str(first), "-l", str(last), path, "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    out = r.stdout.decode("utf-8", "replace")
    pages_read = max(1, out.count("\f"))   # form-feed separates pages
    return len(out.replace("\f", "").strip()) / float(pages_read)


def pdf_has_text_layer(path, sample_pages=10, min_chars_per_page=100, retries=1):
    """True if a PDF carries a real embedded text layer (digital) -> OCR not needed.

    Samples the document at BOTH ends — a head window and a tail window (sized from the
    ``pdfinfo`` page count) — and calls it digital only when EVERY sampled window clears
    ``min_chars_per_page``. This closes the mixed-PDF blind spot: a file that is digital up
    front but SCANNED in a later section would pass a head-only probe as "digital" and its
    scanned text would be silently lost (the coverage metric, also pdftotext-based, can't
    see it either). Any textless window -> OCR the whole doc, the safe/lossless direction.

    Bounded + fast (1-2 pdftotext calls, instant even on 1000+ page PDFs). Falls back to a
    contiguous head probe when ``pdfinfo`` is unavailable. Asymmetric-safe: a wrong
    "scanned" only costs OCR time, so on ANY probe failure (after a retry) we return False
    (-> OCR). 100 cpp cleanly separates the two (digital pages measured 700-2700 cpp;
    scanned ~0).
    """
    n = _pdf_page_count(path)
    half = max(1, sample_pages // 2)
    if n <= 0:
        windows = [(1, sample_pages)]                 # no page count -> head-only probe
    elif n <= sample_pages:
        windows = [(1, n)]                            # small doc -> probe all of it
    else:
        windows = [(1, half), (n - half + 1, n)]      # head + tail
    last = None
    for attempt in range(retries + 1):
        try:
            return all(_window_cpp(path, f, l) >= min_chars_per_page for (f, l) in windows)
        except Exception as e:
            last = e   # transient (e.g. timeout) -> retry once before giving up
    print("  [auto-ocr] probe failed for %s (%s) -> assuming scanned (OCR)"
          % (os.path.basename(path), last), file=sys.stderr)
    return False


# --- figure capture (Tier 1): store crops + inline VLM captions -------------
# The DECISION layer (gate/filter/inline) is pure policy in backend.ingest; here we do
# the docling/PIL/network side. Generic: every doc is treated the same.

def _png_bytes(img):
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _picture_meta(doc, pic):
    """Extract (class, area_fraction, sha16, PIL image) for one docling picture.

    ``area`` is the page-area fraction for paginated sources (PDF), or None when the
    source has no page geometry (docx/pptx) — gate_figures handles None by class+dedup.
    """
    cls = None
    for ann in getattr(pic, "annotations", []) or []:
        if getattr(ann, "kind", "") == "classification" and getattr(ann, "predicted_classes", None):
            cls = max(ann.predicted_classes, key=lambda c: c.confidence).class_name
    # A malformed/inverted/zero-area provenance bbox makes PIL crop or save raise. Treat
    # that as a non-extractable figure (placeholder later dropped) rather than letting the
    # exception bubble to the per-doc handler and lose ALL of this doc's docling markdown.
    try:
        img = pic.get_image(doc) if hasattr(pic, "get_image") else None
        sha = hashlib.sha1(_png_bytes(img)).hexdigest()[:16] if img is not None else None
    except Exception as e:
        print("  [caption] figure image extract failed (%s) -> skip figure" % e, file=sys.stderr)
        img, sha = None, None
    area = None
    prov = getattr(pic, "prov", None)
    if prov:
        try:
            pr = prov[0]
            bb = pr.bbox
            pg = doc.pages[pr.page_no]
            area = abs((bb.r - bb.l) * (bb.t - bb.b)) / (pg.size.width * pg.size.height)
        except Exception:
            area = None
    return cls, area, sha, img


def _caption_and_inline(doc, md, did, assets_dir, vlm):
    """Gate docling pictures, store surviving crops, caption them, inline into ``md``.

    Survivors (gate_figures: not deny-class / not tiny / unique-by-sha) are written to
    ``<assets_dir>/<did>/<k>.png`` and their VLM caption inlined as
    ``![caption](assets/<did>/<k>.png)`` in place of docling's k-th ``<!-- image -->``;
    gated-out placeholders are dropped. A useless caption still keeps the (re-renderable)
    image with a neutral alt. Returns the rewritten markdown.
    """
    # CRITICAL alignment: export_to_markdown emits one `<!-- image -->` per BODY-layer
    # picture, but doc.pictures is a FLAT list that ALSO holds FURNITURE/BACKGROUND/etc.
    # pictures (header/footer logos, watermarks) which produce NO placeholder. A naive
    # positional zip against doc.pictures would mis-bind every figure after the first
    # non-body picture. So we keep only non-(explicitly-non-BODY) pictures, preserving
    # doc.pictures order (reading order == placeholder order), and guard the count below.
    n_ph = md.count("<!-- image -->")
    pics = [p for p in (getattr(doc, "pictures", None) or []) if _is_body_picture(p)]
    if not pics:
        return md, figure_coverage([], n_ph, bailed=False)
    metas, imgs = [], []
    for pic in pics:
        cls, area, sha, img = _picture_meta(doc, pic)
        metas.append({"cls": cls, "area": area, "sha": sha})
        imgs.append(img)
    decisions = gate_figures(metas)
    renders, outcomes = [], []
    for k, (dec, img) in enumerate(zip(decisions, imgs)):
        if not dec.keep or img is None:
            renders.append(None)
            outcomes.append(figure_outcome(dec.keep, dec.reason, False, False))
            continue
        rel = "assets/%s/%d.png" % (did, k)
        dest = os.path.join(assets_dir, did, "%d.png" % k)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            img.save(dest, "PNG")
        except Exception as e:
            print("  [caption] save failed %s (%s) -> skip figure" % (rel, e), file=sys.stderr)
            renders.append(None)
            outcomes.append(figure_outcome(True, dec.reason, False, False))  # lost_bad_crop
            continue
        cap = ""
        try:
            cap = vlm.caption(_png_bytes(img))
        except Exception as e:
            print("  [caption] VLM error (%s)" % e, file=sys.stderr)
        ok = caption_is_useful(cap)
        # Keep the stored image re-renderable even if the caption is junk (neutral alt).
        alt = cap if ok else "figure"
        renders.append(image_markdown(alt, rel))
        outcomes.append(figure_outcome(True, dec.reason, True, ok))
    # Safety net: if our render count doesn't match the emitted placeholders (a residual
    # ordering/exclusion edge), DON'T mis-bind — return the markdown un-inlined. This is a
    # DETECTED LOSS (figure_coverage marks every survivor lost_bail so it gets reported).
    bailed = (n_ph != len(renders))
    if bailed:
        print("  [caption] placeholder/picture mismatch (%d vs %d) for %s -> skip inlining"
              % (n_ph, len(renders), did), file=sys.stderr)
        return md, figure_coverage(outcomes, n_ph, bailed=True)
    return inline_image_captions(md, renders), figure_coverage(outcomes, n_ph, bailed=False)


_NONBODY_LAYERS = ("FURNITURE", "BACKGROUND", "INVISIBLE", "NOTES")


def _pdftotext(path):
    """Extract a PDF's embedded text layer via ``pdftotext`` (raw UTF-8 text)."""
    out = subprocess.check_output(["pdftotext", "-q", path, "-"], stderr=subprocess.DEVNULL)
    return out.decode("utf-8", "replace")


_BBOX_PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
_BBOX_WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)


def _xml_unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&#39;", "'").replace("&apos;", "'").replace("&amp;", "&"))


def _picture_boxes(doc):
    """Fractional top-left bboxes of docling BODY pictures: ``[(page_no, x0, y0, x1, y1)]``.
    Cheap (reads the doc only); the pdftotext crop is deferred to the metric step."""
    boxes = []
    for pic in getattr(doc, "pictures", None) or []:
        if not _is_body_picture(pic):
            continue
        prov = getattr(pic, "prov", None)
        if not prov:
            continue
        pr = prov[0]
        pg = getattr(pr, "page_no", None)
        bb = getattr(pr, "bbox", None)
        if pg is None or bb is None:
            continue
        try:
            page = doc.pages[pg]
            wd, hd = page.size.width, page.size.height
            tb = bb.to_top_left_origin(hd)
            xs = sorted((tb.l / wd, tb.r / wd))
            ys = sorted((tb.t / hd, tb.b / hd))
            boxes.append((pg, xs[0], ys[0], xs[1], ys[1]))
        except Exception:
            continue
    return boxes


def _pdf_all_page_words(path):
    """Every page's fractional words in ONE ``pdftotext -bbox`` run:
    ``{page_no: [(text, x0, y0, x1, y1), ...]}`` — a per-page subprocess would cost
    thousands of invocations on a big standard. Empty dict on failure."""
    try:
        out = subprocess.check_output(["pdftotext", "-bbox", path, "-"],
                                      stderr=subprocess.DEVNULL).decode("utf-8", "replace")
    except Exception:
        return {}
    pages = {}
    for pg_no, m in enumerate(_BBOX_PAGE.finditer(out), 1):
        w, h = float(m.group(1)), float(m.group(2))
        if w <= 0 or h <= 0:
            continue
        words = []
        for wm in _BBOX_WORD.finditer(m.group(3)):
            x0, y0, x1, y1 = (float(wm.group(i)) for i in range(1, 5))
            txt = _xml_unescape(wm.group(5)).strip()
            if txt:
                words.append((txt, x0 / w, y0 / h, x1 / w, y1 / h))
        pages[pg_no] = words
    return pages


def _pdf_drawn_boxes(path):
    """INDEPENDENT figure regions from the PDF's own drawing objects (pypdfium2):
    ``[(page_no, x0, y0, x1, y1), ...]`` in fractional top-left coords.

    Rationale: docling's picture bboxes explaining docling's own gap is circular
    (a misclassified body block would excuse exactly the text it dropped). The
    PDF itself records where drawings are — raster image objects and clusters of
    vector path objects (a state machine is dozens of strokes/boxes). Text over
    such a cluster is figure content by EVIDENCE. Individual objects covering
    more than ``image_region_max_frac`` of the page are frames/backgrounds and
    ignored; a cluster qualifies with >= ``image_region_min_paths`` objects (a
    raster image qualifies alone). All thresholds from config. [] on failure."""
    c = _cfg()
    try:
        import ctypes
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except Exception:
        return []
    out = []
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception:
        return []
    try:
        for pg_no in range(len(pdf)):
            try:
                page = pdf[pg_no]
                w, h = page.get_size()
                if w <= 0 or h <= 0:
                    continue
                paths, rasters = [], []
                for obj in page.get_objects(max_depth=4):
                    if obj.type not in (pdfium_c.FPDF_PAGEOBJ_PATH,
                                        pdfium_c.FPDF_PAGEOBJ_IMAGE):
                        continue
                    l = ctypes.c_float()
                    b = ctypes.c_float()
                    r = ctypes.c_float()
                    t = ctypes.c_float()
                    if not pdfium_c.FPDFPageObj_GetBounds(obj.raw, l, b, r, t):
                        continue
                    box = (max(0.0, l.value / w), max(0.0, (h - t.value) / h),
                           min(1.0, r.value / w), min(1.0, (h - b.value) / h))
                    if (box[2] - box[0]) * (box[3] - box[1]) >= c.image_region_max_frac:
                        continue              # page frame / background rect
                    (rasters if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE else paths).append(box)
                for box, n in merge_boxes(paths + rasters, pad=c.image_region_pad):
                    # any raster inside the cluster is figure evidence by itself
                    has_raster = any(not (rb[2] < box[0] or box[2] < rb[0]
                                          or rb[3] < box[1] or box[3] < rb[1])
                                     for rb in rasters)
                    if n >= c.image_region_min_paths or has_raster:
                        out.append((pg_no + 1, box[0], box[1], box[2], box[3]))
            except Exception:
                continue
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return out


def _image_region_text(path, boxes):
    """Text living inside figure regions (the ``<!-- image -->`` text), recovered
    from ``pdftotext -bbox`` and matched by fractional bbox. This is the
    apple-to-apple image-text: what the conversion represented as an image but the
    PDF holds as text. One whole-doc pdftotext run regardless of page count."""
    if not boxes:
        return ""
    by_page = {}
    for pg, x0, y0, x1, y1 in boxes:
        by_page.setdefault(pg, []).append((x0, y0, x1, y1))
    all_words = _pdf_all_page_words(path)
    out = []
    for pg, bs in by_page.items():
        words = all_words.get(pg)
        if not words:
            continue
        for b in bs:
            out.extend(words_in_bbox(words, b))
    return " ".join(out)


def _alt_more_complete(alt_text, md):
    """Is the independent ``alt_text`` body materially more complete than docling's ``md``?

    Keep-best decision shared by the PDF and office fallbacks. Keys on CHARACTER content
    (``char_ngram_recall``), so it is blind to tokenization/hyphenation and only fires on
    a real content shortfall: True when docling's md preserves less than
    ``fallback_content_min`` of ``alt_text``'s characters. Returns ``(is_lossy, content,
    n_tokens)``. Never fires when the baseline is empty or below the token floor (recall
    there is noise). This is what lets a structurally-good docling md be kept while a
    genuinely lossy one falls back to plain-but-complete text."""
    c = _cfg()
    if not alt_text or not alt_text.strip():
        return False, 1.0, 0
    n = len(tokenize(alt_text))
    if n < c.fallback_min_tokens:
        return False, 1.0, n
    content = char_ngram_recall(alt_text, markdown_to_text(md))
    return content < c.fallback_content_min, content, n


def _pdf_coverage_fallback(path, md):
    """If docling's PDF markdown dropped real content the text layer holds, use the text
    layer. Generic + self-targeting: keys on the measured CONTENT completeness of ``md``
    against ``pdftotext`` (threshold from config), so normal PDFs (docling covers the
    text) are untouched and diagram/vector/partial PDFs (content short) are rescued. The
    text layer is first de-boilerplated so the fallback body is clean and apple-to-apple."""
    c = _cfg()
    try:
        ptxt = strip_running_lines(_pdftotext(path), c.header_footer_min_frac)
    except Exception:
        return md
    lossy, content, n = _alt_more_complete(ptxt, md)
    if lossy:
        print("  [fallback] %s: docling content %.2f of %d-token text layer -> using text layer"
              % (os.path.basename(path), content, n), file=sys.stderr)
        return ptxt
    return md


def _pdfinfo(path):
    """Parse ``pdfinfo`` key: value output into a dict (empty on any failure)."""
    out = subprocess.check_output(["pdfinfo", path], stderr=subprocess.DEVNULL)
    d = {}
    for line in out.decode("utf-8", "replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def _with_pdf_frontmatter(path, md):
    """Prepend provenance front matter (title/author/dates) to a PDF's markdown, so
    PDFs carry the same provenance as office docs. No-op if metadata is junk/absent
    or the markdown already starts with a front-matter block (idempotent)."""
    if not md or md.startswith("---\n"):
        return md
    try:
        meta = pdf_info_meta(_pdfinfo(path))
    except Exception:
        return md
    fm = front_matter(meta)
    return (fm.rstrip("\n") + "\n\n" + md) if fm else md


def _is_body_picture(pic):
    """True unless the picture is on an explicitly non-BODY content layer (which would
    emit no markdown placeholder). Missing/unknown layer is treated as BODY (kept)."""
    name = getattr(getattr(pic, "content_layer", None), "name", None)
    return name not in _NONBODY_LAYERS


def _make_caption_converter(threads):
    """Converter for digital PDFs + HTML with picture classification + image export ON
    (so we can gate + caption figures). Office formats are not handled here — they go
    through the deterministic OOXML lane (scripts/office_convert.py)."""
    from docling.document_converter import (  # type: ignore
        DocumentConverter, PdfFormatOption, HTMLFormatOption)
    from docling.datamodel.base_models import InputFormat  # type: ignore
    from docling.datamodel.pipeline_options import (  # type: ignore
        PdfPipelineOptions, ConvertPipelineOptions)

    def _accel(opts):
        if threads and threads > 0:
            try:
                from docling.datamodel.pipeline_options import AcceleratorOptions  # type: ignore
                opts.accelerator_options = AcceleratorOptions(num_threads=threads, device="cpu")
            except Exception:
                pass
        return opts

    pdf = PdfPipelineOptions()
    pdf.do_ocr = False
    pdf.do_table_structure = True
    pdf.do_picture_classification = True
    pdf.generate_picture_images = True
    pdf.images_scale = 2.0
    _accel(pdf)

    def simple():
        o = ConvertPipelineOptions()
        o.do_picture_classification = True
        return _accel(o)

    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pdf),
        InputFormat.HTML: HTMLFormatOption(pipeline_options=simple()),
    })


_VLM_OCR_PROMPT = (
    "Transcribe this document page to GitHub-flavored Markdown. Reproduce ALL text "
    "verbatim, render tables as markdown tables, and keep headings and lists. Do not "
    "summarize, translate, or omit anything; output only the page content as markdown."
)


def _make_vlm_ocr_converter(threads, vlm_url, vlm_model):
    """Converter that transcribes scanned PDFs full-page via the VLM (docling VlmPipeline
    + ApiVlmOptions pointed at our llama-server) instead of RapidOCR."""
    from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore
    from docling.datamodel.base_models import InputFormat  # type: ignore
    from docling.datamodel.pipeline_options import VlmPipelineOptions, ApiVlmOptions  # type: ignore
    from docling.datamodel.pipeline_options_vlm_model import ResponseFormat  # type: ignore
    from docling.pipeline.vlm_pipeline import VlmPipeline  # type: ignore

    # max_tokens bounds a full-page transcription. A scanned page has no text-layer
    # ground truth, so a too-low cap would silently cut the tail and the gate could not
    # see it — keep it generous and CONFIGURABLE (DOC2MD_VLM_MAX_TOKENS / [ingest]) rather
    # than a hardcoded literal.
    api = ApiVlmOptions(
        url=vlm_url,
        params={"model": vlm_model, "max_tokens": _cfg().vlm_max_tokens},
        prompt=_VLM_OCR_PROMPT,
        response_format=ResponseFormat.MARKDOWN,
        scale=2.0, temperature=0.0, timeout=600,
    )
    opts = VlmPipelineOptions(vlm_options=api, enable_remote_services=True)
    if threads and threads > 0:
        try:
            from docling.datamodel.pipeline_options import AcceleratorOptions  # type: ignore
            opts.accelerator_options = AcceleratorOptions(num_threads=threads, device="cpu")
        except Exception:
            pass
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=opts)})


def _make_converter(do_ocr, threads):
    if threads and threads > 0:
        try:
            import torch  # type: ignore
            torch.set_num_threads(threads)
        except Exception:
            pass
    from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore
    from docling.datamodel.base_models import InputFormat  # type: ignore
    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    # Cap docling's OWN worker threads (defaults to 4, independent of torch) to bound
    # peak memory — page rasterization at high concurrency is what triggers bad_alloc.
    if threads and threads > 0:
        try:
            from docling.datamodel.pipeline_options import AcceleratorOptions  # type: ignore
            opts.accelerator_options = AcceleratorOptions(num_threads=threads, device="cpu")
        except Exception:
            pass
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _load_converter(threads=0, ocr="auto", captions=False, vlm_ocr=False,
                    assets_dir=None, vlm=None, vlm_url=None, vlm_model=None):
    """Return a ``(path, doc_id) -> markdown`` callable that routes per document.

    Routing (generic, per-doc; no path targeting):
      * digital PDF + every non-PDF  -> the "main" converter. With ``captions`` it has
        picture classification + image export ON, and surviving figures are stored +
        VLM-captioned + inlined; otherwise it's the plain text/table converter (today's
        behaviour, unchanged).
      * scanned PDF (auto: no text layer) -> ``vlm_ocr`` ? docling VlmPipeline against the
        VLM (Qwen2.5-VL) : RapidOCR (today's behaviour).
    ``ocr="on"`` forces the OCR path for all; ``"off"`` never OCRs. Heavy converters and
    their model loads are built LAZILY, so a corpus that never hits a branch never pays it.
    """
    main_conv = _make_caption_converter(threads) if captions else _make_converter(False, threads)
    state = {"ocr": None, "vlmocr": None}

    def ocr_conv():
        if state["ocr"] is None:
            print("  [auto-ocr] scanned PDF -> loading RapidOCR pipeline", file=sys.stderr)
            state["ocr"] = _make_converter(True, threads)
        return state["ocr"]

    def vlm_ocr_conv():
        if state["vlmocr"] is None:
            print("  [auto-ocr] scanned PDF -> loading VLM-OCR pipeline (%s)" % vlm_model, file=sys.stderr)
            state["vlmocr"] = _make_vlm_ocr_converter(threads, vlm_url, vlm_model)
        return state["vlmocr"]

    def to_markdown(path, did=None):
        """Return ``(markdown, extras)`` where extras carries per-doc figure loss
        accounting (``extras["figures"]`` = FigureCoverage or None on the OCR paths)."""
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        # Already-markdown sources pass through VERBATIM. docling's markdown backend
        # re-parses and re-emits, dropping fenced code / nested structure / body text
        # (the corpus sweep caught .md specs at ~0.6 recall). Copying is lossless.
        if ext == "md":
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read(), {"figures": None, "status": "PASSTHROUGH"}
        # OCR only ever applies to PDFs; non-PDFs always go through the main/caption path
        # (so `--ocr on` never starves docx/pptx of figure captioning).
        if ext != "pdf":
            use_ocr = False
        elif ocr == "on":
            use_ocr = True
        elif ocr == "off":
            use_ocr = False
        else:  # auto
            use_ocr = not pdf_has_text_layer(path)
        if use_ocr:
            res = (vlm_ocr_conv() if (vlm_ocr and ext == "pdf") else ocr_conv()).convert(path)
            raw = res.document.export_to_markdown()
            return (_with_pdf_frontmatter(path, raw),
                    {"figures": None, "status": _status_name(res),
                     "furniture": _furniture_text(res.document)})
        # digital PDF or non-PDF
        res = main_conv.convert(path)
        doc = res.document
        md = doc.export_to_markdown()
        figcov = None
        if captions:
            md, figcov = _caption_and_inline(doc, md, did or "doc", assets_dir, vlm)
        extras = {"figures": figcov, "status": _status_name(res),
                  "furniture": _furniture_text(doc)}
        if ext == "pdf":
            extras["pic_boxes"] = _picture_boxes(doc)   # for image-text apple-to-apple
            md = _pdf_coverage_fallback(path, md)        # rescue diagram/vector PDFs
            md = _with_pdf_frontmatter(path, md)
        return md, extras

    return to_markdown


def _status_name(result):
    """docling ConversionResult.status -> its enum name (e.g. 'SUCCESS'); '' if absent."""
    st = getattr(result, "status", None)
    return getattr(st, "name", str(st)) if st is not None else ""


def _furniture_text(doc):
    """Text docling placed OUTSIDE the body — page headers/footers/numbers, i.e. the
    running boilerplate its trained layout model identified. Used as the accurate
    boilerplate oracle: excluded from the coverage ground truth so docling BODY is
    scored against source-minus-redundancy (apple-to-apple)."""
    out = []
    for t in getattr(doc, "texts", None) or []:
        layer = getattr(getattr(t, "content_layer", None), "name", None)
        if layer and layer != "BODY":
            txt = getattr(t, "text", None)
            if txt:
                out.append(txt)
    return " ".join(out)


def _source_text(path):
    """Independent (non-docling) plain text of the SOURCE, for the coverage metric.

    PDF via ``pdftotext``, HTML tag-stripped, md/txt read raw. Shares no code with the
    converter, so the metric is a genuine cross-check. Returns ``""`` when no
    independent extraction exists (e.g. a scanned PDF) -> coverage is then vacuously
    1.0 and simply not informative. Office formats never reach this lane (they are
    owned by the OOXML lane, which has its own exhaustive ground truth)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    try:
        if ext == "pdf":
            return _pdftotext(path)
        if ext in ("html", "htm"):
            with open(path, encoding="utf-8", errors="replace") as fh:
                # html_to_text drops <script>/<style> BODIES too — else embedded JS/CSS
                # (often >50% of a generated report) inflates the baseline and a faithful
                # conversion looks lossy.
                return html_to_text(fh.read())
        if ext == "md":
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        return ""
    except Exception:
        return ""


def _claims_dir(out_dir):
    d = os.path.join(out_dir, "_claims")
    os.makedirs(d, exist_ok=True)
    return d


def _claim_path(out_dir, did):
    return os.path.join(_claims_dir(out_dir), did + ".claim")


def _claim_owner_alive(claim):
    """Is the claim's recorded owner still a live process ON THIS HOST?

    Claims record ``owner pid epoch host``. A pid can only be probed locally, so a
    claim written from another host is conservatively treated as alive (never
    stolen automatically). Unreadable/malformed claims count as dead."""
    try:
        parts = open(claim).read().split()
        pid = int(parts[1])
        host = parts[3] if len(parts) > 3 else ""
    except (OSError, IndexError, ValueError):
        return False
    if host and host != _HOSTNAME:
        return True                       # foreign host: cannot probe -> assume live
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _try_claim(out_dir, did, owner, reclaim=False):
    """Atomically claim a doc for one worker (work-stealing queue mode).

    NFS-safe: writes a unique temp file then hard-links it to the claim path —
    ``link(2)`` is atomic on NFS where ``O_EXCL`` historically is not — and treats
    a failed ``link`` as success when the temp's link count is 2 (a lost-reply
    retransmit returns EEXIST for our OWN successful link). The claim file holds
    ``owner pid epoch host`` so stale claims (owner dead, doc still invalid) can
    be detected and released. ``reclaim=True`` (escalation / fallback lanes
    re-running a dead worker's victim) takes over an existing claim ONLY if its
    owner is dead — a live owner's claim is never stolen (two converters on one
    doc would race on the output). Returns True iff this caller owns the claim."""
    claim = _claim_path(out_dir, did)
    if reclaim and os.path.exists(claim):
        if _claim_owner_alive(claim):
            return False
        try:
            os.remove(claim)
        except OSError:
            pass
    tmp = os.path.join(_claims_dir(out_dir), ".tmp.%s.%d" % (did, os.getpid()))
    try:
        with open(tmp, "w") as f:
            f.write("%s %d %d %s\n" % (owner, os.getpid(), int(time.time()), _HOSTNAME))
        try:
            os.link(tmp, claim)
            return True
        except OSError:
            # NFS lost-reply: our link may have LANDED even though we got EEXIST.
            # nlink==2 on our unique tmp proves tmp and claim are the same inode.
            try:
                return os.stat(tmp).st_nlink == 2
            except OSError:
                return False
    except OSError:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _fallback_body(path):
    """Independent (non-docling) full-content body for a doc docling cannot convert.

    The terminal remedy of the recovery ladder (retry -> escalate -> FALLBACK): plain
    but complete text from the same independent extractors the metric trusts. The
    result still goes through the validator, so a fallback body is measured like any
    conversion. Returns '' when no independent extraction exists (caller blacklists)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    try:
        if ext == "pdf":
            c = _cfg()
            txt = strip_running_lines(_pdftotext(path), c.header_footer_min_frac)
            return _with_pdf_frontmatter(path, txt) if txt.strip() else ""
        if ext in ("html", "htm"):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return html_to_text(fh.read())
        if ext == "md":
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    except Exception:
        return ""
    return ""


def _done_ids(out_dir):
    """Doc ids with a PASSING validation record, merged across all _coverage*.jsonl
    shards (last record per id wins). These are the only docs safe to skip."""
    verdict = {}
    for fp in sorted(glob.glob(os.path.join(out_dir, "_coverage*.jsonl"))):
        try:
            with open(fp, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if "id" in rec and "valid" in rec:
                        verdict[rec["id"]] = bool(rec["valid"])
        except OSError:
            continue
    return set(i for i, ok in verdict.items() if ok)


def _measure_only(rows, cov_file):
    """Measure coverage of ALREADY-CONVERTED markdown against source (no docling).

    Reuses the same metric the live flow records; writes a fresh ``cov_file`` so
    ``coverage_report.py`` can summarize the whole corpus's losslessness. Figure
    accounting is absent here (no re-conversion) — this is the text-loss sweep.
    """
    try:
        open(cov_file, "w").close()               # fresh sweep
    except OSError:
        pass
    c = _cfg()
    n = low = skipped = 0
    with open(cov_file, "a", encoding="utf-8") as cf:
        for r in rows:
            dest = r["dest"]
            if not os.path.isfile(dest):
                skipped += 1
                continue
            try:
                with open(dest, encoding="utf-8", errors="replace") as fh:
                    md = fh.read()
                rec = _coverage_record(r["id"], r["rel"], r["src"], md)
            except Exception as e:
                print("  [measure] skip %s (%s)" % (r["rel"], e), file=sys.stderr)
                skipped += 1
                continue
            cf.write(json.dumps(rec) + "\n")
            n += 1
            if not rec["valid"]:
                low += 1
            if n % 200 == 0:
                print("  measured %d (invalid=%d) ..." % (n, low), file=sys.stderr)
                sys.stderr.flush()
    print("measured=%d invalid(recall<%.0f%%)=%d no-md=%d -> %s"
          % (n, c.min_recall * 100, low, skipped, cov_file), file=sys.stderr)
    return 0


def _validate(rep, status, figcov, c, content_recall=0.0):
    """Validator verdict for one doc: True only if the conversion is trustworthy.

    Three independent gates (all parameterized): docling did not report a bad
    status (its 'exit code'); the text is not lossy under the EXPLAINED-GAP model
    (a low token recall is only a real loss when char-n-gram content recall is ALSO
    low — else the gap is benign tokenization/furniture, not dropped content); and
    figures, if captioned, are lossless. ``content_recall`` defaults to 0.0 so a
    caller that omits it reproduces the strict token-recall gate.
    """
    status_ok = status not in _DOCLING_BAD_STATUS
    # A PASSTHROUGH (.md copied verbatim) is lossless BY CONSTRUCTION; any recall < 1 is
    # only markdown_to_text stripping fences/tables from the target side, not lost content.
    recall_ok = status == "PASSTHROUGH" or not is_lossy_explained(
        rep, content_recall, min_recall=c.min_recall,
        min_tokens=c.min_tokens, content_min=c.content_min_recall)
    fig_ok = figcov is None or bool(figcov.get("lossless"))
    return bool(status_ok and recall_ok and fig_ok)


def _coverage_record(did, rel, path, md, extras=None):
    """Measure + VALIDATE one doc; returns a JSON-able record.

    Coverage is computed against the DE-BOILERPLATED source (running headers/footers
    removed) so docling isn't penalized for correctly dropping them — apple-to-apple.
    ``extras["figures"]`` (FigureCoverage) and ``extras["status"]`` (docling status)
    feed the validator; ``valid`` is the gate the skip-logic trusts.
    """
    c = _cfg()
    # Apple-to-apple cleaning of the ground truth: strip repeated headers/footers
    # (independent position/repetition heuristic) AND subtract docling's own furniture
    # text (its trained header/footer/page-number detector) when available. Both, so
    # the metric never docks docling for correctly removing redundancy.
    src = strip_running_lines(_source_text(path), c.header_footer_min_frac)
    furniture = (extras or {}).get("furniture") or ""
    # Image-text: words buried inside <!-- image --> regions. Excluded from the
    # BODY-text ground truth (it is figure content, not lost body text) and reported
    # separately so it is visible how much figure text VLM captioning would recover.
    # Boxes come from docling's layout (convert time) or, when absent (measure-only
    # sweeps), from the INDEPENDENT drawing-object detector — so the apple-to-apple
    # holds without a conversion, and the evidence isn't docling judging itself.
    boxes = (extras or {}).get("pic_boxes")
    if not boxes and path.rsplit(".", 1)[-1].lower() == "pdf":
        boxes = _pdf_drawn_boxes(path)
    image_text = _image_region_text(path, boxes) if boxes else ""
    exclude = (furniture + " " + image_text).strip()
    md_text = markdown_to_text(md)
    rep = coverage(src, md_text, exclude=exclude)
    # Content-presence signal for the explained-gap model: char-n-gram recall, blind to
    # tokenization/hyphenation. Excluded text (furniture + image-text) is added to the
    # TARGET pool so the source is not penalized for the target correctly omitting it —
    # the char-level mirror of the multiset `exclude` above.
    content = char_ngram_recall(src, (md_text + " " + exclude) if exclude else md_text)
    figcov = (extras or {}).get("figures")
    fig_d = (figcov._asdict() if hasattr(figcov, "_asdict")
             else dict(figcov)) if figcov is not None else None
    status = (extras or {}).get("status") or ""
    out = {
        "id": did, "rel": rel,
        "recall": round(rep.recall, 4),
        "content_recall": round(content, 4),
        "unexplained": round(max(0.0, 1.0 - content), 4),
        "n_source": rep.n_source, "n_covered": rep.n_covered, "n_missing": rep.n_missing,
        "missing_top": rep.missing_top,
        "docling_status": status,
        "valid": _validate(rep, status, fig_d, c, content),
    }
    if image_text:
        out["image_text_tokens"] = len(tokenize(image_text))
    if fig_d is not None:
        out["figures"] = fig_d
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert corpus documents to markdown via docling.")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC or [paths].source_docs)")
    ap.add_argument("--out", default=os.environ.get("DOC2MD_MARKDOWN_DIR", os.path.join(REPO, "data", "markdown")),
                    help="markdown output dir (default $DOC2MD_MARKDOWN_DIR or data/markdown)")
    ap.add_argument("--force", action="store_true", help="re-convert even if the .md already exists")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats the system accepts (default: [ingest] "
                         "accept_formats / $DOC2MD_ACCEPT_FORMATS = all supported). Only docling-lane "
                         "formats (pdf/html/htm) are converted here; other accepted formats are "
                         "reported for their own lane, and unsupported/declined files are warned about.")
    ap.add_argument("--limit", type=int, default=0, help="convert at most N docs (0 = all)")
    ap.add_argument("--threads", type=int, default=1,
                    help="CPU threads for the ML models (default 1; raise to go faster on idle cores)")
    ap.add_argument("--ocr", choices=["auto", "on", "off"], default="auto",
                    help="OCR policy for PDFs: auto = only scanned PDFs (default); on = always; off = never")
    ap.add_argument("--captions", action="store_true", default=False,
                    help="store figure crops + inline VLM captions (overrides [ingest].enable_captions). "
                         "Needs the VLM server up at --vlm-url.")
    ap.add_argument("--vlm-ocr", action="store_true", default=False,
                    help="transcribe scanned PDFs with the VLM instead of RapidOCR "
                         "(overrides [ingest].enable_vlm_ocr). Needs the VLM server up.")
    ap.add_argument("--assets-dir", default=None, help="where figure crops are stored (default [ingest].assets_dir)")
    ap.add_argument("--vlm-url", default=None, help="VLM chat-completions URL (default [ingest].vlm_url)")
    ap.add_argument("--vlm-model", default=None, help="VLM model name (default [ingest].vlm_model)")
    ap.add_argument("--no-coverage", action="store_true", default=False,
                    help="disable the per-doc source->markdown coverage measurement "
                         "(on by default; writes data/markdown/_coverage*.jsonl)")
    ap.add_argument("--measure-only", action="store_true", default=False,
                    help="do not convert; measure coverage of existing markdown in --out "
                         "against source and write _coverage*.jsonl (no docling needed)")
    ap.add_argument("--trust-existing-md", action="store_true", default=False,
                    help="skip any doc that already has a .md WITHOUT requiring a passing "
                         "validation record (legacy behaviour). Default: a doc is only "
                         "skipped if its _coverage record says valid=true, so partial/lossy "
                         "conversions self-heal. Run --measure-only first to bless good docs.")
    ap.add_argument("--auto-shards", action="store_true", default=False,
                    help="probe CPU/RAM/GPU and print recommended '<shards> <threads>' to stdout "
                         "(sized from the actual machine + [ingest] threads_per_shard/"
                         "mem_per_shard_gb/max_shards), then exit. Used by convert_sharded.sh.")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit (no docling needed)")
    ap.add_argument("--shard", default="",
                    help="parallel sharding as I/N (0-based shard I of N processes); this shard "
                         "takes todo[I::N] and uses its own crash markers so N shards run safely "
                         "in parallel over disjoint docs")
    ap.add_argument("--queue", action="store_true", default=False,
                    help="work-stealing mode: claim docs one at a time from a shared claim dir "
                         "(--out/_claims) instead of a fixed stripe, biggest-first, so no worker "
                         "idles while another drags a long tail. Run N of these under "
                         "heal_supervisor.py; mutually exclusive with --shard")
    ap.add_argument("--worker-id", type=int, default=0,
                    help="worker number in --queue mode (names this worker's markers/claims)")
    ap.add_argument("--only", action="append", default=[],
                    help="convert ONLY this doc id (or source basename); repeatable. Bypasses "
                         "skip/blacklist — used by the supervisor's escalation lane")
    ap.add_argument("--reclaim", action="store_true", default=False,
                    help="with --only: take over an existing claim (re-running a dead worker's doc)")
    ap.add_argument("--fallback-only", action="store_true", default=False,
                    help="with --only: skip docling entirely and write the independent fallback "
                         "body (pdftotext text layer / native office text), validator-gated. "
                         "The terminal remedy when escalation is exhausted")
    args = ap.parse_args(argv)
    if args.queue and args.shard:
        ap.error("--queue and --shard are mutually exclusive")
    if args.fallback_only and not args.only:
        ap.error("--fallback-only requires --only <id>")
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src, set $DOC2MD_SRC, or set "
                 "[paths].source_docs in config/default.local.toml" % (args.src,))

    # Parse --shard into (index, count); default is the single-process case 0/1.
    shard_i, shard_n = 0, 1
    if args.shard:
        parts = args.shard.split("/")
        try:
            if len(parts) != 2:
                raise ValueError
            shard_i, shard_n = int(parts[0]), int(parts[1])
        except ValueError:
            ap.error("--shard must be I/N with 0 <= I < N")
        if not (shard_n >= 1 and 0 <= shard_i < shard_n):
            ap.error("--shard must be I/N with 0 <= I < N")
    if args.queue or args.only:
        suffix = ".w%d" % args.worker_id      # per-worker markers in the shared out dir
    else:
        suffix = "" if shard_n == 1 else ".%d" % shard_i

    # Cap CPU use BEFORE torch imports (OMP/MKL read these at load); no GPU needed.
    if args.threads and args.threads > 0:
        os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))
        os.environ.setdefault("MKL_NUM_THREADS", str(args.threads))

    os.makedirs(args.out, exist_ok=True)
    # Per-shard markers so parallel shards never clobber each other's crash recovery.
    crashed_file = os.path.join(args.out, "_crashed%s.txt" % suffix)
    cur_file = os.path.join(args.out, "_converting%s.txt" % suffix)
    cov_file = os.path.join(args.out, "_coverage%s.jsonl" % suffix)
    measure_cov = not args.no_coverage

    # Crash recovery: if a previous run died mid-document (e.g. OS OOM-kill on a huge
    # page that docling can't catch), the marker names the victim. If it still has no
    # .md, blacklist it so we don't loop-crash on it forever -> build_index uses native.
    crashed = set()
    if args.queue or args.only:
        # Queue workers share the out dir: any worker's crash knowledge applies to all.
        for cf in glob.glob(os.path.join(args.out, "_crashed*.txt")):
            try:
                crashed |= set(l.strip() for l in open(cf) if l.strip())
            except OSError:
                pass
    elif os.path.isfile(crashed_file):
        crashed = set(l.strip() for l in open(crashed_file) if l.strip())
    # In queue/only mode this legacy self-blacklist MUST NOT run: worker ids restart
    # at 0 every supervisor run, so a fresh worker would misread the PREVIOUS run's
    # in-flight marker as its own crash and blacklist a healable doc. Crash
    # classification there belongs to the supervisor's recovery ladder.
    if os.path.isfile(cur_file) and not (args.queue or args.only):
        stuck = open(cur_file).read().strip()
        if stuck and not os.path.isfile(os.path.join(args.out, stuck + ".md")):
            crashed.add(stuck)
            with open(crashed_file, "a") as f:
                f.write(stuck + "\n")
            print("  [recover] %s crashed the converter last run -> blacklisted "
                  "(native fallback at build_index)" % stuck, file=sys.stderr)
        try: os.remove(cur_file)
        except OSError: pass

    # Resolve config ONCE and publish it so every helper reads the same (parameterized)
    # thresholds — nothing hardcoded downstream.
    global _CFG
    _CFG = cfg = load_ingest_config()

    if args.auto_shards:
        cpu, mem_gb, gpu = _probe_resources()
        shards, threads = recommend_shards(cpu, mem_gb, cfg.threads_per_shard,
                                           cfg.mem_per_shard_gb, cfg.max_shards)
        print("  [auto-shards] cpu=%d mem_avail=%.0fGB gpu=%s -> SHARDS=%d THREADS=%d "
              "(threads/shard=%d mem/shard=%.0fGB cap=%s)"
              % (cpu, mem_gb, gpu, shards, threads, cfg.threads_per_shard,
                 cfg.mem_per_shard_gb, cfg.max_shards or "none"), file=sys.stderr)
        print("%d %d" % (shards, threads))     # stdout: consumed by convert_sharded.sh
        return 0

    # Accept-list: --accept wins, else [ingest] accept_formats (empty => all supported).
    accept_spec = args.accept if args.accept.strip() else (cfg.accept_formats or None)
    unknowns = unknown_formats(accept_spec)
    if unknowns:
        print("  [WARNING] accept-list names %d format(s) that match NO lane (ignored; check "
              "for typos): %s  -- supported: %s"
              % (len(unknowns), ", ".join(unknowns), ", ".join(supported_formats())),
              file=sys.stderr)
    accept = normalize_accept(accept_spec)
    sources, scan = find_sources(args.src, accept)
    _warn_unrouted(scan)
    rows = plan(sources, args.out, args.force)
    # A doc is "done" (skippable) only if it has a .md AND — unless --trust-existing-md —
    # a prior validation record marks it valid. Unvalidated/partial/lossy docs re-convert.
    valid_ids = set() if args.trust_existing_md else _done_ids(args.out)
    def _done(r):
        return r["skip"] and (args.trust_existing_md or r["id"] in valid_ids)
    if args.only:
        # Escalation/fallback lane: exactly these docs, bypassing skip AND blacklist
        # (the whole point is re-running a doc that crashed a worker).
        want = set(args.only)
        todo = [r for r in rows
                if r["id"] in want or os.path.basename(r["rel"]) in want]
        missing = want - set(r["id"] for r in todo) - set(os.path.basename(r["rel"]) for r in todo)
        if missing:
            print("  [only] no such doc(s): %s" % ", ".join(sorted(missing)), file=sys.stderr)
    else:
        todo = [r for r in rows if not _done(r) and r["id"] not in crashed]
    if args.queue:
        # Biggest-first so a huge doc starts early instead of stalling the tail alone.
        by_id = dict((r["id"], r) for r in todo)
        sizes = []
        for r in todo:
            try:
                sizes.append((r["id"], os.path.getsize(r["src"])))
            except OSError:
                sizes.append((r["id"], 0))
        todo = [by_id[i] for i in order_todo(sizes)]
    if shard_n > 1:
        todo = todo[shard_i::shard_n]     # disjoint stripe; deterministic since rows are sorted
    if args.limit:
        todo = todo[:args.limit]
    have_md = len([r for r in rows if r["skip"]])
    reconv = len([r for r in rows if r["skip"] and not _done(r) and r["id"] not in crashed])
    print("sources=%d  have-md=%d  validated-skip=%d  reconvert(unvalidated/lossy)=%d  "
          "blacklisted=%d  to-convert=%d  -> %s"
          % (len(rows), have_md, have_md - reconv, reconv, len(crashed), len(todo), args.out),
          file=sys.stderr)

    if args.dry_run:
        for r in todo:
            print("CONVERT %s -> %s" % (r["rel"], os.path.basename(r["dest"])))
        return 0

    if args.measure_only:
        # Measure ALL converted docs (a shard measures its stripe), not just `todo`
        # (which excludes already-converted ones — the whole point of the sweep).
        rows_m = rows[shard_i::shard_n] if shard_n > 1 else rows
        if args.limit:
            rows_m = rows_m[:args.limit]
        return _measure_only(rows_m, cov_file)

    if args.fallback_only:
        # Terminal remedy: no docling at all — write the independent full-content body
        # and put it through the SAME validator/record path as a real conversion.
        okf = badf = 0
        for r in todo:
            if not _try_claim(args.out, r["id"], "w%d" % args.worker_id, reclaim=args.reclaim):
                print("  [fallback-only] %s: claim held elsewhere, skipping" % r["rel"],
                      file=sys.stderr)
                continue
            md = _fallback_body(r["src"])
            if not md.strip():
                badf += 1
                print("  [fallback-only] FAIL %s: no independent extraction -> blacklist candidate"
                      % r["rel"], file=sys.stderr)
                continue
            tmp = "%s.tmp.%d" % (r["dest"], os.getpid())   # per-process: no cross-lane clobber
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(md)
                os.replace(tmp, r["dest"])
            except Exception as e:
                badf += 1
                print("  [fallback-only] WRITE-FAIL %s (%s)" % (r["rel"], e), file=sys.stderr)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue
            # a .md source copied verbatim is a PASSTHROUGH (lossless by construction),
            # so it keeps the validator's passthrough exemption even via this lane
            ext_fb = r["src"].rsplit(".", 1)[-1].lower() if "." in r["src"] else ""
            rec = _coverage_record(r["id"], r["rel"], r["src"], md,
                                   extras={"figures": None,
                                           "status": "PASSTHROUGH" if ext_fb == "md" else "FALLBACK"})
            with open(cov_file, "a", encoding="utf-8") as cf:
                cf.write(json.dumps(rec) + "\n")
            okf += 1 if rec["valid"] else 0
            badf += 0 if rec["valid"] else 1
            print("  [fallback-only] %s recall=%.0f%% valid=%s" %
                  (r["rel"], rec["recall"] * 100, rec["valid"]), file=sys.stderr)
        print("fallback-only done: valid=%d failed=%d" % (okf, badf), file=sys.stderr)
        return 0 if badf == 0 else 1

    # Tier-1 figure-caption / VLM-OCR settings: CLI flag OR [ingest] config/env.
    captions = bool(args.captions or cfg.enable_captions)
    vlm_ocr = bool(args.vlm_ocr or cfg.enable_vlm_ocr)
    assets_dir = args.assets_dir or cfg.assets_dir
    vlm_url = args.vlm_url or cfg.vlm_url
    vlm_model = args.vlm_model or cfg.vlm_model

    # The VLM server backs BOTH captioning and VLM-OCR. Probe once; if it's down, degrade
    # gracefully (no captions / RapidOCR) rather than failing the whole run.
    vlm = None
    if captions or vlm_ocr:
        import vlm_client as _vlm  # sibling helper (scripts/ on sys.path); needs no docling
        # cfg.vlm_max_tokens governs BOTH caption and OCR calls (see config comment); wire it
        # through so a long caption's tail is never silently cut at the client's 384 default.
        vlm = _vlm.VlmClient(vlm_url, model=vlm_model, max_tokens=cfg.vlm_max_tokens)
        if not vlm.healthy():
            print("  [vlm] server at %s not reachable -> captions/VLM-OCR DISABLED for this run "
                  "(digital text + RapidOCR still work)" % vlm_url, file=sys.stderr)
            captions = vlm_ocr = False
            vlm = None
        else:
            print("  [vlm] using %s (captions=%s vlm_ocr=%s)" % (vlm_url, captions, vlm_ocr), file=sys.stderr)
        if captions:
            os.makedirs(assets_dir, exist_ok=True)

    try:
        to_markdown = _load_converter(args.threads, args.ocr, captions=captions, vlm_ocr=vlm_ocr,
                                      assets_dir=assets_dir, vlm=vlm, vlm_url=vlm_url, vlm_model=vlm_model)
    except Exception as e:
        print("ERROR: docling not available (%s). Install it under Python 3.9+ "
              "(`pip install docling`) and run with that interpreter, or use --dry-run."
              % e, file=sys.stderr)
        return 2

    ok = bad = empty = 0
    stop_file = os.path.join(args.out, "_stop%s.txt" % suffix)
    owner = "w%d" % args.worker_id
    # same-lane attempts for transient docling/model hiccups; from config, not hardcoded
    # (default retry_attempts=2 -> 2 tries = 1 retry). At least one attempt always.
    ATTEMPTS = max(1, cfg.retry_attempts)
    for n, r in enumerate(todo, 1):
        if args.queue or args.only:
            if os.path.exists(stop_file):
                # Graceful drain: the supervisor asked this worker to yield (admission
                # shrink). Finish nothing new; already-converted docs are safely done.
                print("  [yield] stop requested -> draining after %d docs" % (ok + bad + empty),
                      file=sys.stderr)
                break
            if not _try_claim(args.out, r["id"], owner, reclaim=args.reclaim):
                continue                     # another worker owns it — steal the next one
        # Record the in-flight doc so a hard process death (OOM-kill) is recoverable.
        with open(cur_file, "w") as f:
            f.write(r["id"])
        # Heartbeat: a guaranteed log line per doc. A watchdog watches log freshness to
        # tell a slow-but-progressing large file (log keeps ticking) from a real hang
        # (log goes silent). Flush so the timestamp is immediate.
        print("  [hb %d/%d] %s" % (n, len(todo), os.path.basename(r["rel"])[:60]),
              file=sys.stderr)
        sys.stderr.flush()
        md = None
        extras = {"figures": None}
        for attempt in range(ATTEMPTS):
            try:
                md, extras = to_markdown(r["src"], r["id"])
                md = md or ""
                break
            except Exception as e:
                if attempt + 1 < ATTEMPTS:
                    print("  retry %s (%s)" % (r["rel"], e), file=sys.stderr)
                else:
                    bad += 1
                    # No .md written -> build_index step 2 falls back to native
                    # extraction for this doc, so content is never lost.
                    print("  FAIL %s (%s) -> native fallback at build_index"
                          % (r["rel"], e), file=sys.stderr)
        if md is None:
            pass                          # failed after retries (counted above)
        elif md.strip():
            # Atomic write: a kill mid-write must never leave a partial .md that the
            # skip-check would treat as complete. Write to a PER-PROCESS tmp then
            # rename (a shared name would let two lanes truncate each other's write).
            tmp = "%s.tmp.%d" % (r["dest"], os.getpid())
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(md)
                os.replace(tmp, r["dest"])
                ok += 1
                # In-flow validation: measure how much source content survived into
                # the markdown and record it. Never let measurement break a conversion.
                if measure_cov:
                    try:
                        rec = _coverage_record(r["id"], r["rel"], r["src"], md, extras)
                        with open(cov_file, "a", encoding="utf-8") as cf:
                            cf.write(json.dumps(rec) + "\n")
                        if not rec["valid"]:
                            top = ", ".join("%s x%d" % (t, c) for t, c in rec["missing_top"][:5])
                            print("  [validate] FAIL %s recall=%.0f%% status=%s (missing: %s)"
                                  % (r["rel"], rec["recall"] * 100, rec["docling_status"], top),
                                  file=sys.stderr)
                        fig = rec.get("figures")
                        if fig and not fig["lossless"]:
                            print("  [figures] LOSS %s: lost=%d bailed=%s (%s)"
                                  % (r["rel"], fig["n_lost"], fig["bailed"], fig["by_outcome"]),
                                  file=sys.stderr)
                    except Exception as e:
                        print("  [coverage] skip %s (%s)" % (r["rel"], e), file=sys.stderr)
            except Exception as e:       # disk full / perms -> native fallback, don't crash
                bad += 1
                print("  WRITE-FAIL %s (%s) -> native fallback" % (r["rel"], e), file=sys.stderr)
                try: os.remove(tmp)
                except OSError: pass
        else:
            empty += 1                    # converted but empty -> also native fallback
        if n % 20 == 0 or n == len(todo):
            print("  %d/%d (ok=%d bad=%d empty=%d)" % (n, len(todo), ok, bad, empty),
                  file=sys.stderr)
    try: os.remove(cur_file)              # clean exit -> no in-flight victim
    except OSError: pass
    print("done: converted=%d failed=%d empty=%d -> %s"
          % (ok, bad, empty, args.out), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
