"""
title: Unit — backend.ingest office media ref-location resolution
kind: tests
layer: backend
summary: Resolve which office image is body content vs page chrome (header/footer/master), body-wins.
"""
import pytest

from backend.ingest import resolve_media_refs, is_body_part

pytestmark = pytest.mark.unit

IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _rels(*pairs):
    rels = "".join('<Relationship Id="rId%d" Type="%s" Target="%s"/>' % (i, t, tgt)
                   for i, (t, tgt) in enumerate(pairs, 1))
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels + "</Relationships>")


def test_body_image_is_body():
    rels = {"word/_rels/document.xml.rels": _rels((IMG, "media/image1.png"))}
    assert resolve_media_refs(rels) == {"word/media/image1.png": "body"}


def test_header_image_is_chrome():
    rels = {"word/_rels/header1.xml.rels": _rels((IMG, "media/logo.png"))}
    assert resolve_media_refs(rels) == {"word/media/logo.png": "chrome"}


def test_body_wins_when_referenced_by_both():
    # the SAME media referenced from the body AND a header -> body wins (never dropped)
    rels = {
        "word/_rels/document.xml.rels": _rels((IMG, "media/shared.png")),
        "word/_rels/header2.xml.rels": _rels((IMG, "media/shared.png")),
    }
    assert resolve_media_refs(rels)["word/media/shared.png"] == "body"


def test_relative_target_resolves():
    # pptx slide rels use ../media/.. ; must normalize to ppt/media/..
    rels = {"ppt/slides/_rels/slide3.xml.rels": _rels((IMG, "../media/image7.emf"))}
    assert resolve_media_refs(rels) == {"ppt/media/image7.emf": "body"}


def test_master_and_layout_are_not_chrome_formula_safe():
    # A formula/diagram authored on a layout/master is referenced ONLY from the layout's rels
    # (slides inherit it, never re-reference the media). It must NOT be classified chrome, or
    # it would be dropped before the model -> formula-safety violation. -> 'unknown' (kept).
    rels = {
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels((IMG, "../media/l.png")),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels((IMG, "../media/m.png")),
    }
    got = resolve_media_refs(rels)
    assert got["ppt/media/l.png"] == "unknown"     # kept (body-wins default), never dropped
    assert got["ppt/media/m.png"] == "unknown"


def test_layout_formula_is_gate_kept():
    from backend.ingest import gate_figures
    rels = {"ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels((IMG, "../media/eq.emf"))}
    ref = resolve_media_refs(rels)["ppt/media/eq.emf"]
    d = gate_figures([{"cls": None, "area": None, "sha": "s", "ref": ref, "n_bytes": 1400}])
    assert d[0].keep is True                       # a layout-only EMF equation survives to the model


def test_non_image_relationships_ignored():
    rels = {"word/_rels/document.xml.rels": _rels(
        ("http://.../hyperlink", "http://example.com"),
        (IMG, "media/real.png"),
    )}
    assert resolve_media_refs(rels) == {"word/media/real.png": "body"}


def test_is_body_part():
    assert is_body_part("word/document.xml") is True
    assert is_body_part("ppt/slides/slide1.xml") is True
    assert is_body_part("xl/worksheets/sheet1.xml") is True
    assert is_body_part("word/header1.xml") is False
    assert is_body_part("word/footer2.xml") is False
    assert is_body_part("ppt/slideMasters/slideMaster1.xml") is False
    assert is_body_part("ppt/slideLayouts/slideLayout3.xml") is False
