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
