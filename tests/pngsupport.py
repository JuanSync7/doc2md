"""
title: Test support — real PNG bytes
kind: tests
layer: backend
summary: One builder for genuinely decodable PNG bytes, so an optional Pillow install cannot decide whether the suite passes.
"""
# WHY THIS EXISTS.
#
# Four test files each carried their own `_png()` that emitted the PNG magic bytes,
# an IHDR with a bogus CRC, and nothing else — no IDAT, no IEND. Those bytes are not
# a PNG. They passed anywhere Pillow was ABSENT, because `image_caption.prepare_png`
# can then only sniff the magic number, and failed anywhere Pillow was PRESENT,
# because `_decodes` really decodes and correctly returned UNDECODABLE.
#
# Measured: 20 failures in `test_image_caption.py` and 14 more across
# `test_caption_bundles.py`, `test_image_enrich.py` and `test_validate_figures.py`
# on a compute-farm node with Pillow 5.1.1 — all of them green on the dev host and
# on CI, neither of which has Pillow. A suite whose verdict depends on whether an
# OPTIONAL dependency happens to be installed is not testing the code, and the shape
# of the failure (UNDECODABLE everywhere) reads exactly like a product bug.
#
# Built from zlib + struct so constructing one needs no Pillow of its own.
import struct
import zlib


def _chunk(tag, data):
    # type: (bytes, bytes) -> bytes
    body = tag + data
    return (struct.pack(">I", len(data)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xffffffff))


def real_png(w=8, h=8, tag=b""):
    # type: (int, int, bytes) -> bytes
    """A valid, decodable RGBA PNG of ``w`` x ``h``.

    ``tag`` varies the PIXELS rather than appending trailing bytes, so two images
    with different tags are genuinely different images AND both still decode. The
    old helpers appended a tag byte after the end of the file, which changed the
    content hash without changing the image — fine for a content-address test,
    invalid as a PNG.
    """
    fill = (tag[:1] or b"\xff")
    row = b"\x00" + (fill + b"\x00\x00\xff") * w          # filter byte + RGBA pixels
    raw = row * h
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


def have_pil():
    # type: () -> bool
    """Is Pillow importable here? Used to skip the branches that require it — never
    to decide what the OTHER branches should assert."""
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False
