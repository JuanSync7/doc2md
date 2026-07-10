"""
title: Integration — docling_convert figure caption+store helper
kind: tests
layer: backend
summary: _caption_and_inline gates/stores/inlines, filters non-BODY pictures, and survives a bad image crop.
"""
# Integration (not unit): the caption helper writes PNG crops to disk.
import importlib.util
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "docling_convert.py")
PIL = pytest.importorskip("PIL.Image")


def _mod():
    spec = importlib.util.spec_from_file_location("docling_convert", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- fake docling document --------------------------------------------------

class _Layer:
    def __init__(self, name): self.name = name


class _BBox:
    def __init__(self, w, h): self.l, self.r, self.t, self.b = 0, w, h, 0


class _Prov:
    def __init__(self, w, h):
        self.page_no = 0
        self.bbox = _BBox(w, h)


class _Pred:
    def __init__(self, name): self.class_name, self.confidence = name, 0.9


class _Ann:
    def __init__(self, name):
        self.kind = "classification"
        self.predicted_classes = [_Pred(name)]


class _Pic:
    def __init__(self, cls, img, w, h, layer="BODY", raise_image=False):
        self.annotations = [_Ann(cls)] if cls else []
        self._img = img
        self.prov = [_Prov(w, h)]
        self.content_layer = _Layer(layer)
        self._raise = raise_image

    def get_image(self, doc):
        if self._raise:
            raise ValueError("bad crop bbox")   # mimic PIL crop on inverted bbox
        return self._img


class _Doc:
    def __init__(self, md, pics):
        self._md = md
        self.pictures = pics

        class _Sz:
            width = height = 1000
        import types as _t
        self.pages = {0: _t.SimpleNamespace(size=_Sz())}

    def export_to_markdown(self):
        return self._md


def _img(c):
    return PIL.new("RGB", (40, 40), c)


def test_gates_stores_and_inlines(tmp_path):
    m = _mod()
    shared = _img((10, 20, 30))
    doc = _Doc("a\n\n<!-- image -->\n\nb\n\n<!-- image -->\n\nc\n\n<!-- image -->\n\nd\n", [
        _Pic("engineering_drawing", shared, 300, 300),   # KEEP
        _Pic("logo", _img((200, 0, 0)), 170, 170),       # deny + small -> DROP
        _Pic("engineering_drawing", shared, 300, 300),   # dup sha -> DROP
    ])

    class V:
        calls = 0
        def caption(self, b, fmt="png", prompt=None):
            V.calls += 1
            return "A detailed block diagram of the clock subsystem with PLL and divider stages."
    out, cov = m._caption_and_inline(doc, doc._md, "docid7", str(tmp_path / "assets"), V())
    assert V.calls == 1
    assert os.path.isfile(os.path.join(str(tmp_path / "assets"), "docid7", "0.png"))
    assert "![A detailed block diagram" in out
    assert "assets/docid7/0.png" in out
    assert "<!-- image -->" not in out
    # figure accounting: 1 captured, logo+dup gated (intentional), nothing lost
    assert cov.n_body == 3
    assert cov.n_captured == 1
    assert cov.n_gated == 2
    assert cov.n_lost == 0
    assert cov.lossless is True


def test_non_body_picture_is_excluded_to_keep_alignment(tmp_path):
    """A FURNITURE picture (header logo) is in doc.pictures but emits NO markdown
    placeholder. It must be filtered so the body figure binds to the right placeholder."""
    m = _mod()
    md = "Body text\n\n<!-- image -->\n\nEnd\n"   # ONE placeholder (the body figure)
    doc = _Doc(md, [
        _Pic("logo", _img((1, 1, 1)), 50, 50, layer="FURNITURE"),         # header logo: no placeholder
        _Pic("engineering_drawing", _img((9, 9, 9)), 400, 400, layer="BODY"),  # the real figure
    ])

    class V:
        def caption(self, b, fmt="png", prompt=None):
            return "A schematic of the body figure showing the signal path and registers."
    out, cov = m._caption_and_inline(doc, md, "d", str(tmp_path / "a"), V())
    # the body figure's caption is inlined (NOT mis-bound to the furniture logo)
    assert "![A schematic of the body figure" in out
    assert "assets/d/0.png" in out          # furniture filtered out -> body pic is index 0
    assert "<!-- image -->" not in out
    assert cov.n_body == 1 and cov.n_captured == 1 and cov.lossless is True


def test_bad_image_crop_skips_figure_not_whole_doc(tmp_path):
    m = _mod()
    md = "x\n\n<!-- image -->\n\ny\n\n<!-- image -->\n\nz\n"
    doc = _Doc(md, [
        _Pic("flow_chart", None, 300, 300, raise_image=True),            # get_image raises
        _Pic("engineering_drawing", _img((5, 5, 5)), 300, 300),          # fine
    ])

    class V:
        def caption(self, b, fmt="png", prompt=None):
            return "A flowchart describing the boot sequence with several decision branches."
    # must NOT raise; bad figure dropped, good figure captioned
    out, cov = m._caption_and_inline(doc, md, "d", str(tmp_path / "a"), V())
    assert "![A flowchart" in out
    assert "assets/d/1.png" in out
    assert "<!-- image -->" not in out
    # the un-extractable figure is DETECTED as a loss (not silently swallowed)
    assert cov.n_lost == 1
    assert cov.lossless is False
    assert cov.by_outcome.get("lost_bad_crop") == 1


def test_count_mismatch_bails_to_uninlined(tmp_path):
    """If placeholder count != render count (residual ordering edge), do NOT mis-bind —
    return the markdown un-inlined."""
    m = _mod()
    md = "only text, no placeholders but a body picture exists somehow"
    doc = _Doc(md, [_Pic("engineering_drawing", _img((2, 2, 2)), 300, 300)])

    class V:
        def caption(self, b, fmt="png", prompt=None):
            return "A diagram."
    out, cov = m._caption_and_inline(doc, md, "d", str(tmp_path / "a"), V())
    assert out == md          # bailed safely
    # the bail is reported as total figure loss for the doc (not a silent skip)
    assert cov.bailed is True
    assert cov.n_lost == 1
    assert cov.lossless is False
