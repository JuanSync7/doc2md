"""
title: Image byte-probe — magic sniff + dimensions (private)
layer: backend
public_api: no
summary: Stdlib, PIL-free format sniff + dimension read; shared by the caption tool and the figure validator's independent byte-magic ground truth.
"""
# 3.6-compatible. Stdlib only (struct). NO PIL (unavailable on the host) and NO disk. Pure
# byte inspection: given the RAW bytes of a file, say whether it is an image and, for the
# common raster formats, how big. Two consumers depend on it staying dependency-free:
#   * scripts/image_caption.py — decode-probe (undecodable) + too-large-by-pixels, before
#     the VLM sees anything.
#   * scripts/validate_figures.py — enumerate the ground-truth image set of a source by
#     BYTES not by path, so an image the extractor's */media/* glob missed is still counted
#     (the byte-level analogue of office_convert.py --audit-parts).
import struct

__all__ = ["sniff_image_format", "image_dimensions", "IMAGE_EXTS"]

# Every raster/metafile/vector image format the pipeline recognizes. Kept in sync with the
# ROUTE_IMAGE extensions + the office media the caption lane handles.
IMAGE_EXTS = frozenset([
    "png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif", "emf", "wmf", "webp", "svg",
])


def sniff_image_format(data):
    # type: (bytes) -> str
    """The image format of ``data`` from its leading bytes, or ``""`` if it is not a
    recognized image. Location-independent (keys on content, never a filename), so it finds
    images stored outside ``*/media/`` too. SVG is detected as XML that mentions ``<svg``."""
    if not data:
        return ""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\xd7\xcd\xc6\x9a" or data[:4] == b"\x01\x00\x09\x00":
        return "wmf"
    # EMF: EMR_HEADER record (iType==1) with the ' EMF' signature at byte offset 40.
    if len(data) >= 44 and data[40:44] == b" EMF" and struct.unpack_from("<I", data, 0)[0] == 1:
        return "emf"
    # SVG is text: an XML/`<svg` prolog within the first bytes (allow a BOM/whitespace).
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head[:5].lower() == b"<?xml" or head[:4].lower() == b"<svg":
        low = data[:2048].lower()
        if b"<svg" in low:
            return "svg"
    return ""


def _png_dims(data):
    if len(data) >= 24 and data[12:16] == b"IHDR":
        return struct.unpack_from(">II", data, 16)
    return None


def _gif_dims(data):
    if len(data) >= 10:
        return struct.unpack_from("<HH", data, 6)
    return None


def _bmp_dims(data):
    if len(data) >= 26:
        w, h = struct.unpack_from("<i", data, 18)[0], struct.unpack_from("<i", data, 22)[0]
        return (abs(w), abs(h))
    return None


def _jpeg_dims(data):
    # Walk JPEG markers to the first Start-Of-Frame (SOFn) and read its height/width.
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):   # SOF0..SOF15 (not DHT/JPG/DAC)
            h, w = struct.unpack_from(">HH", data, i + 5)
            return (w, h)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:             # SOI/EOI/RST: no length
            i += 2
            continue
        seg = struct.unpack_from(">H", data, i + 2)[0]                    # segment length
        i += 2 + seg
    return None


def image_dimensions(data):
    # type: (bytes) -> tuple
    """``(width, height)`` in pixels for the common RASTER formats (png/jpeg/gif/bmp), or
    ``None`` when unknown (metafiles, svg, malformed). Best-effort and PIL-free — used only
    to bound too-large images and for reporting, never to gate content."""
    fmt = sniff_image_format(data)
    try:
        if fmt == "png":
            return _png_dims(data)
        if fmt == "gif":
            return _gif_dims(data)
        if fmt == "bmp":
            return _bmp_dims(data)
        if fmt == "jpeg":
            return _jpeg_dims(data)
    except struct.error:
        return None
    return None
