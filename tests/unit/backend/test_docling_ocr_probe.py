"""
title: Unit — docling_convert.pdf_has_text_layer
kind: tests
layer: backend
summary: OCR routing probe: digital vs scanned classification, page-density, retry, safe fallback.

Mocks subprocess so it runs without pdftotext/PDFs (and under the 3.6 pipeline interpreter).
"""
import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(REPO, "scripts", "docling_convert.py")


def _mod():
    spec = importlib.util.spec_from_file_location("docling_convert", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _R:
    def __init__(self, out):
        self.stdout = out.encode("utf-8")
        self.stderr = b""


def _patch_pdftotext(m, monkeypatch, output=None, raises=None, fail_times=0, pages_info=""):
    """Mock subprocess.run for both probes: a ``pdfinfo`` call returns ``pages_info``
    (e.g. "Pages: 3"), any ``pdftotext`` call returns ``output``."""
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if raises is not None and calls["n"] <= fail_times:
            raise raises
        if cmd and cmd[0] == "pdfinfo":
            return _R(pages_info)
        return _R(output or "")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    return calls


def test_digital_pdf_detected(monkeypatch):
    m = _mod()
    # 3 pages (2 form-feeds + tail), dense text -> digital
    page = "lots of real selectable text here " * 20
    _patch_pdftotext(m, monkeypatch, output=page + "\f" + page + "\f" + page)
    assert m.pdf_has_text_layer("x.pdf") is True


def test_scanned_pdf_detected(monkeypatch):
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="\f\f")   # page breaks, ~no text
    assert m.pdf_has_text_layer("x.pdf") is False


def test_low_density_is_scanned(monkeypatch):
    m = _mod()
    # a few chars spread over 10 pages -> below 100 cpp
    _patch_pdftotext(m, monkeypatch, output="hi" + "\f" * 9)
    assert m.pdf_has_text_layer("x.pdf") is False


def test_transient_failure_then_success_retries(monkeypatch):
    m = _mod()
    # fail the first call, succeed the second (retries=1 default)
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return _R("plenty of text on one page " * 10)

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m.pdf_has_text_layer("x.pdf", retries=1) is True
    assert calls["n"] == 2


def test_persistent_failure_falls_back_to_scanned(monkeypatch):
    m = _mod()
    calls = _patch_pdftotext(m, monkeypatch, raises=OSError("boom"), fail_times=99)
    # never succeeds -> safe default False (OCR). The pdfinfo probe fails (swallowed ->
    # page count 0 -> head-only window), then retries+1 window attempts all fail.
    assert m.pdf_has_text_layer("x.pdf", retries=2) is False
    assert calls["n"] == 1 + 3          # 1 pdfinfo + (retries+1) window probes


def test_mixed_digital_head_scanned_tail_is_scanned(monkeypatch):
    m = _mod()
    # A 100-page PDF: digital up front, SCANNED in the tail. The head-only probe of the
    # old code would call this "digital" and silently lose the scanned tail; sampling the
    # tail window too must catch it and route to OCR.
    dense = "lots of real selectable text here " * 20

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "pdfinfo":
            return _R("Pages: 100\n")
        first_page = int(cmd[cmd.index("-f") + 1])
        return _R(dense if first_page == 1 else "\f\f\f\f")   # head dense, tail blank

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m.pdf_has_text_layer("x.pdf") is False


def test_fully_digital_multipage_uses_page_count(monkeypatch):
    m = _mod()
    dense = "plenty of selectable text " * 20
    calls = _patch_pdftotext(m, monkeypatch, output=dense, pages_info="Pages: 100\n")
    # head AND tail windows both dense -> digital; pdfinfo(1) + 2 window probes
    assert m.pdf_has_text_layer("x.pdf") is True
    assert calls["n"] == 1 + 2


# ================================ the exclusion set needs two witnesses (M1)

def test_a_body_region_docling_alone_calls_a_picture_buys_no_exclusion(monkeypatch):
    """Fault injection on the most dangerous input in the measurement. Text inside a
    figure region leaves the losslessness ground truth, so a converter that could
    nominate those regions by itself could excuse exactly the text it dropped — and
    the gate would read green over real damage.

    Here docling claims a whole page is a picture and the PDF's own drawing objects
    report nothing there. The claim buys nothing."""
    m = _mod()
    monkeypatch.setattr(m, "_pdf_drawn_boxes", lambda path: [])
    assert m._figure_regions("/x/spec.pdf", [(1, 0.0, 0.0, 1.0, 1.0)]) == []


def test_a_region_both_witnesses_claim_is_narrowed_to_the_overlap():
    """Agreement is necessary but does not let either side widen the region: what is
    excluded is the overlap, so an over-wide claim on either side is trimmed by the
    other rather than believed."""
    m = _mod()
    m._pdf_drawn_boxes = lambda path: [(1, 0.20, 0.20, 0.50, 0.50)]
    assert m._figure_regions("/x/spec.pdf", [(1, 0.0, 0.0, 0.40, 0.40)]) == \
        [(1, 0.20, 0.20, 0.40, 0.40)]


def test_a_non_pdf_has_no_drawing_objects_to_consult(monkeypatch):
    """HTML has no drawing objects, so docling's boxes are all there is and that
    lane's exclusion stays as circular as it was. Asserted rather than left implicit,
    because the honest statement is 'this lane is not yet covered', not 'this lane is
    fine' — the drawing detector must never be CALLED for it either, or a missing
    pypdfium2 would silently change an HTML document's ground truth."""
    m = _mod()

    def _boom(path):
        raise AssertionError("the drawing detector must not run for a non-PDF")

    monkeypatch.setattr(m, "_pdf_drawn_boxes", _boom)
    claimed = [(1, 0.1, 0.1, 0.4, 0.4)]
    assert m._figure_regions("/x/page.html", claimed) == claimed


# ================================ OCR routing by EVIDENCE, not volume (M1)
#
# The old probe asked one question — mean chars per page — and sent the whole
# document to OCR when any sampled window fell under 100. Measured on the corpus,
# that misroutes a legitimate class of document:
#
#   pdf/kestrel-dataflow.pdf     79 chars/page   7 text objects, 12 paths, no raster
#   pdf/kestrel-clock-spec.pdf  950 chars/page   44-48 text objects
#   pdf/kestrel-clock-spec-scan.pdf 0 chars/page  no text objects, one page-sized raster
#
# The dataflow PDF is a diagram-only DIGITAL page: it has a real text layer, just
# little text, because most of the page is vector drawing. OCR'ing it re-renders and
# re-transcribes content the PDF already holds exactly, and its measured
# losslessness collapses to "no independent text layer to measure against".
#
# What actually separates the two is not how MUCH text a page has but whether its
# content is a page-covering RASTER that the text layer does not account for. 79
# against 0 is a crisp separation; 79 against 100 is a coin toss.

def _patch_scan_evidence(m, monkeypatch, per_page):
    """`per_page` maps page number -> max raster area fraction."""
    monkeypatch.setattr(m, "_page_raster_fracs", lambda path: dict(per_page))


def test_a_diagram_only_digital_page_is_not_sent_to_ocr(monkeypatch):
    """The misroute this closes, at the measured numbers. Little text, no raster:
    the text layer is thin because the page is mostly vector art, and every
    character the document holds is already extractable."""
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="x" * 79, pages_info="Pages: 1")
    _patch_scan_evidence(m, monkeypatch, {1: 0.0})
    assert m.pdf_has_text_layer("/x/dataflow.pdf") is True


def test_a_scanned_page_is_still_sent_to_ocr(monkeypatch):
    """The case the probe exists for must not regress: no text, one page-sized
    raster."""
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="", pages_info="Pages: 3")
    _patch_scan_evidence(m, monkeypatch, {1: 1.0, 2: 1.0, 3: 1.0})
    assert m.pdf_has_text_layer("/x/scan.pdf") is False


def test_a_text_stamp_over_a_scan_does_not_buy_a_digital_verdict(monkeypatch):
    """The reason thin text alone cannot mean digital. A scanned page with a header
    stamp or a page number burnt in as real text has a few characters AND a
    page-covering raster — the raster is the content, and it needs OCR. This is the
    row that stops the fix from becoming "any text at all means digital"."""
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="Confidential  12", pages_info="Pages: 1")
    _patch_scan_evidence(m, monkeypatch, {1: 0.98})
    assert m.pdf_has_text_layer("/x/stamped-scan.pdf") is False


def test_a_page_with_plenty_of_text_never_consults_the_raster_probe(monkeypatch):
    """A document over the char threshold is digital on the cheap signal alone, so
    the expensive per-page object walk must not run at all — the probe is meant to
    stay instant on a 1000-page standard."""
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="y" * 5000, pages_info="Pages: 2")

    def _boom(path):
        raise AssertionError("the raster probe must not run on an obviously digital doc")

    monkeypatch.setattr(m, "_page_raster_fracs", _boom)
    assert m.pdf_has_text_layer("/x/spec.pdf") is True


def test_a_scanned_section_in_a_digital_document_still_forces_ocr(monkeypatch):
    """The mixed-PDF protection the old probe bought, kept. The tail window is a
    scanned section: no text, page-covering rasters. Any such window means OCR for
    the whole document, which is the lossless direction."""
    m = _mod()
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        if "pdfinfo" in cmd[0]:
            return _R("Pages: 40")
        calls["n"] += 1
        # head window first, then tail
        return _R(("z" * 900 + "\f") * 5 if calls["n"] == 1 else "\f" * 5)

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    _patch_scan_evidence(m, monkeypatch, dict((p, 1.0) for p in range(36, 41)))
    assert m.pdf_has_text_layer("/x/mixed.pdf") is False


def test_an_unavailable_raster_probe_falls_back_to_the_safe_direction(monkeypatch):
    """pypdfium2 is a PDF-lane dependency and may be missing. With no raster evidence
    the probe cannot tell a diagram page from a scan, and the asymmetry is unchanged:
    a wrong "scanned" costs OCR time, a wrong "digital" loses the content silently.
    So no evidence means OCR."""
    m = _mod()
    _patch_pdftotext(m, monkeypatch, output="x" * 79, pages_info="Pages: 1")
    _patch_scan_evidence(m, monkeypatch, {})
    assert m.pdf_has_text_layer("/x/dataflow.pdf") is False
