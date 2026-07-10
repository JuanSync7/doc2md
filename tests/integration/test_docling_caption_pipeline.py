"""
title: Integration — docling_convert routing (caption path vs RapidOCR vs VLM-OCR)
kind: tests
layer: backend
summary: _load_converter picks the right converter per doc and applies caption+inline, with docling/VLM mocked.
"""
import importlib.util
import os
import types

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "docling_convert.py")
PIL = pytest.importorskip("PIL.Image")   # caption path stores PNG crops


def _mod():
    spec = importlib.util.spec_from_file_location("docling_convert", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- minimal fake docling doc + converters (no real docling) ----------------

class _Pred:
    def __init__(self, name): self.class_name, self.confidence = name, 0.9


class _Ann:
    def __init__(self, name):
        self.kind = "classification"
        self.predicted_classes = [_Pred(name)]


class _BBox:
    def __init__(self, w, h):
        self.l, self.r, self.t, self.b = 0, w, h, 0


class _Prov:
    def __init__(self, w, h):
        self.page_no = 0
        self.bbox = _BBox(w, h)


class _Pic:
    def __init__(self, cls, img, w, h):
        self.annotations = [_Ann(cls)]
        self._img = img
        self.prov = [_Prov(w, h)]
        self.content_layer = types.SimpleNamespace(name="BODY")

    def get_image(self, doc):
        return self._img


class _Doc:
    def __init__(self, md, pics):
        self._md = md
        self.pictures = pics

        class _Sz:
            width = height = 1000
        self.pages = {0: types.SimpleNamespace(size=_Sz())}

    def export_to_markdown(self):
        return self._md


class _Conv:
    """Records the paths it was asked to convert and returns a fixed fake doc."""
    def __init__(self, doc, log, tag, status="SUCCESS"):
        self._doc = doc
        self._log = log
        self._tag = tag
        self._status = status

    def convert(self, path):
        self._log.append(self._tag)
        return types.SimpleNamespace(document=self._doc,
                                     status=types.SimpleNamespace(name=self._status))


class _Vlm:
    def caption(self, image_bytes, fmt="png", prompt=None):
        return "A detailed block diagram of the subsystem showing the data path and control."


def _wire(m, monkeypatch, digital_doc, scanned_md="SCANNED-TEXT"):
    """Patch the three converter factories; return a call-log list."""
    log = []
    main = _Conv(digital_doc, log, "main")
    rapid = _Conv(_Doc(scanned_md, []), log, "rapidocr")
    vlmocr = _Conv(_Doc(scanned_md, []), log, "vlmocr")
    monkeypatch.setattr(m, "_make_caption_converter", lambda threads: main)
    monkeypatch.setattr(m, "_make_converter", lambda do_ocr, threads: (rapid if do_ocr else main))
    monkeypatch.setattr(m, "_make_vlm_ocr_converter", lambda threads, url, model: vlmocr)
    return log


def test_digital_pdf_captions_and_inlines(tmp_path, monkeypatch):
    m = _mod()
    doc = _Doc("Body\n\n<!-- image -->\n\nEnd\n",
               [_Pic("engineering_drawing", PIL.new("RGB", (30, 30), (1, 2, 3)), 300, 300)])
    log = _wire(m, monkeypatch, doc)
    monkeypatch.setattr(m, "pdf_has_text_layer", lambda *a, **k: True)   # digital
    assets = str(tmp_path / "assets")
    to_md = m._load_converter(threads=0, ocr="auto", captions=True, vlm_ocr=True,
                              assets_dir=assets, vlm=_Vlm(),
                              vlm_url="http://x/v1/chat/completions", vlm_model="m")
    out, extras = to_md("/some/file.pdf", "docX")
    assert "main" in log and "vlmocr" not in log and "rapidocr" not in log
    assert "![A detailed block diagram" in out
    assert "assets/docX/0.png" in out
    assert os.path.isfile(os.path.join(assets, "docX", "0.png"))
    assert extras["figures"].lossless is True and extras["figures"].n_captured == 1
    assert extras["status"] == "SUCCESS"          # docling status is captured


def test_scanned_pdf_routes_to_vlm_ocr_when_enabled(tmp_path, monkeypatch):
    m = _mod()
    log = _wire(m, monkeypatch, _Doc("x", []))
    monkeypatch.setattr(m, "pdf_has_text_layer", lambda *a, **k: False)  # scanned
    to_md = m._load_converter(threads=0, ocr="auto", captions=True, vlm_ocr=True,
                              assets_dir=str(tmp_path), vlm=_Vlm(),
                              vlm_url="http://x/v1/chat/completions", vlm_model="m")
    out, extras = to_md("/scan.pdf", "d")
    assert log == ["vlmocr"]
    assert out == "SCANNED-TEXT"
    assert extras["figures"] is None      # OCR path has no figure accounting


def test_scanned_pdf_uses_rapidocr_when_vlm_ocr_off(tmp_path, monkeypatch):
    m = _mod()
    log = _wire(m, monkeypatch, _Doc("x", []))
    monkeypatch.setattr(m, "pdf_has_text_layer", lambda *a, **k: False)  # scanned
    to_md = m._load_converter(threads=0, ocr="auto", captions=False, vlm_ocr=False,
                              assets_dir=str(tmp_path), vlm=None,
                              vlm_url=None, vlm_model=None)
    out, extras = to_md("/scan.pdf", "d")
    assert log == ["rapidocr"]
    assert extras["figures"] is None


def test_nonpdf_goes_through_caption_path(tmp_path, monkeypatch):
    m = _mod()
    doc = _Doc("Slide\n\n<!-- image -->\n", [_Pic("flow_chart", PIL.new("RGB", (30, 30), (9, 9, 9)), None, None)])
    # non-PDF: prov present but we force area None by giving page geometry only; flow_chart kept
    log = _wire(m, monkeypatch, doc)
    to_md = m._load_converter(threads=0, ocr="auto", captions=True, vlm_ocr=False,
                              assets_dir=str(tmp_path / "a"), vlm=_Vlm(),
                              vlm_url="http://x/v1/chat/completions", vlm_model="m")
    out, extras = to_md("/deck.pptx", "deckid")
    assert log == ["main"]
    assert "assets/deckid/0.png" in out
    assert "<!-- image -->" not in out
    assert extras["figures"].n_captured == 1 and extras["figures"].lossless is True
