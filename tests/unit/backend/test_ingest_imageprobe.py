"""
title: Unit — backend.ingest image byte-probe (magic + dimensions)
kind: tests
layer: backend
summary: Stdlib format sniff + dimension read used by the caption tool AND the figure validator.
"""
import struct

import pytest

from backend.ingest import sniff_image_format, image_dimensions

pytestmark = pytest.mark.unit


def _png(w, h):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 4


def _gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 8


def _bmp(w, h):
    return b"BM" + b"\x00" * 16 + struct.pack("<i", w) + struct.pack("<i", h) + b"\x00" * 4


def _jpeg(w, h):
    # SOI + a SOF0 frame carrying dimensions
    return (b"\xff\xd8" + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")


def _emf():
    # EMR_HEADER: iType=1, then the ' EMF' signature at byte offset 40
    b = bytearray(88)
    struct.pack_into("<I", b, 0, 1)
    b[40:44] = b" EMF"
    return bytes(b)


def test_sniff_all_formats():
    assert sniff_image_format(_png(1, 1)) == "png"
    assert sniff_image_format(_gif(1, 1)) == "gif"
    assert sniff_image_format(_bmp(1, 1)) == "bmp"
    assert sniff_image_format(_jpeg(1, 1)) == "jpeg"
    assert sniff_image_format(_emf()) == "emf"
    assert sniff_image_format(b"\xd7\xcd\xc6\x9a" + b"\x00" * 20) == "wmf"
    assert sniff_image_format(b"II*\x00" + b"\x00" * 20) == "tiff"
    assert sniff_image_format(b"MM\x00*" + b"\x00" * 20) == "tiff"
    assert sniff_image_format(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8) == "webp"
    assert sniff_image_format(b'<?xml version="1.0"?><svg xmlns="...">x</svg>') == "svg"
    assert sniff_image_format(b"<svg viewBox='0 0 1 1'></svg>") == "svg"


def test_sniff_rejects_non_images():
    assert sniff_image_format(b"") == ""
    assert sniff_image_format(b"PK\x03\x04 zipzip") == ""      # a zip (docx), not an image
    assert sniff_image_format(b"just some text") == ""
    assert sniff_image_format(b"%PDF-1.7") == ""


def test_dimensions_raster():
    assert image_dimensions(_png(640, 480)) == (640, 480)
    assert image_dimensions(_gif(100, 200)) == (100, 200)
    assert image_dimensions(_bmp(320, 240)) == (320, 240)
    assert image_dimensions(_jpeg(1024, 768)) == (1024, 768)


def test_dimensions_unknown_is_none():
    assert image_dimensions(_emf()) is None          # metafile: dims not read here
    assert image_dimensions(b"not an image") is None
    assert image_dimensions(b"") is None
