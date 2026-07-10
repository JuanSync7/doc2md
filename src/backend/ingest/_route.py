"""
title: Format routing policy (private)
layer: backend
public_api: no
summary: One extension -> one markdown-producing lane; the converters all consult this.
"""
# 3.6-compatible. Stdlib only. Pure policy — the single source of truth for which
# converter OWNS which source format. Every producer consults it so a format is
# never double-converted and never silently unowned:
#   * scripts/office_convert.py  owns ROUTE_OOXML + ROUTE_LIBREOFFICE (deterministic
#     XML walk; LibreOffice/legacy-binary are pre-converted to OOXML by soffice first,
#     so BOTH office suites travel the single OOXML path)
#   * scripts/docling_convert.py owns ROUTE_DOCLING (layout inference for PDF/HTML)
# Every lane's output contract is identical: markdown at data/markdown/<doc_id>.md.
#
# ``classify_source`` layers a caller-supplied ACCEPT-LIST on top of the lane map so
# the operator can restrict which formats the system ingests; anything unsupported or
# not accepted comes back flagged (never silently dropped) for the caller to warn on.
from collections import namedtuple

__all__ = ["route_format", "classify_source", "summarize_routes",
           "supported_formats", "normalize_accept", "unknown_formats", "ext_of",
           "SourceClass", "RouteScan", "SUPPORTED_EXTS",
           "ROUTE_OOXML", "ROUTE_DOCLING", "ROUTE_PASSTHROUGH",
           "ROUTE_FENCE", "ROUTE_LIBREOFFICE", "ROUTE_UNSUPPORTED"]

# Structured XML containers: every character of content is explicitly tagged in the
# file, so a deterministic walk is lossless — no ML, no inference, no drops.
ROUTE_OOXML = "ooxml"
# Layout formats: structure must be INFERRED (PDF is positioned glyphs). docling
# earns its complexity here; losslessness is measured, not guaranteed.
ROUTE_DOCLING = "docling"
# Already markdown/plain text: copy through verbatim.
ROUTE_PASSTHROUGH = "passthrough"
# Code-like data (config/data interchange): embed verbatim in a fenced block —
# lossless by construction; prose conversion would only destroy the shape.
ROUTE_FENCE = "fence"
# OpenDocument (LibreOffice) and legacy binary Office: soffice --convert-to the
# OOXML sibling first, then the OOXML lane owns the result.
ROUTE_LIBREOFFICE = "libreoffice"
# No lane owns this extension — leave the file alone (never a fallback lane).
ROUTE_UNSUPPORTED = "unsupported"

_LANES = {
    ROUTE_OOXML: ("docx", "pptx", "xlsx"),
    ROUTE_DOCLING: ("pdf", "html", "htm"),
    ROUTE_PASSTHROUGH: ("md", "markdown", "txt", "text"),
    ROUTE_FENCE: ("json", "yaml", "yml", "toml", "xml", "csv", "tsv", "ini"),
    ROUTE_LIBREOFFICE: ("odt", "odp", "ods", "doc", "ppt", "xls", "rtf"),
}
_EXT_TO_LANE = {}
for _lane, _exts in _LANES.items():
    for _e in _exts:
        _EXT_TO_LANE[_e] = _lane

# Every extension any lane owns — the system's full ingest surface. Callers use this
# to validate an accept-list and to tell "unsupported" apart from "declined".
SUPPORTED_EXTS = frozenset(_EXT_TO_LANE)


def ext_of(name):
    # type: (str) -> str
    """Normalized bare extension of a filename OR a bare extension.

    Accepts ``"a/b/Spec.DOCX"``, ``"docx"``, ``".PDF"``, ``" xlsx "`` alike and
    returns the lowercase extension with no leading dot (``""`` when there is none).
    Only the LAST dot segment is taken, so ``"archive.tar.gz"`` -> ``"gz"``.
    """
    base = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base.lstrip("."):
        # no real extension (covers "", "README", ".hidden")
        return ""
    return base.rsplit(".", 1)[-1].strip().lower()


def route_format(ext):
    # type: (str) -> str
    """Which converter lane owns files with this extension?

    Accepts the bare extension in any case, with or without a leading dot
    (``"docx"``, ``".PDF"``). Unknown formats come back ``ROUTE_UNSUPPORTED`` —
    callers must treat that as "leave the file alone", never as a fallback lane.
    """
    e = (ext or "").strip().lower().lstrip(".")
    return _EXT_TO_LANE.get(e, ROUTE_UNSUPPORTED)


def supported_formats():
    # type: () -> tuple
    """Every extension the system can ingest, sorted — for --help text and to
    validate an operator-supplied accept-list."""
    return tuple(sorted(SUPPORTED_EXTS))


def normalize_accept(spec):
    # type: (object) -> frozenset
    """Parse an accept-list into a frozenset of normalized extensions.

    ``spec`` may be a comma/space/semicolon-separated string, a list/tuple/set of
    extensions, or ``None``/``""``/``"all"`` meaning "accept every supported
    format" (returned as ``SUPPORTED_EXTS``). Extensions are lowercased and de-
    dotted; unknown tokens are kept (so the caller can report them), except the
    ``"all"`` sentinel. Never raises.
    """
    if spec is None:
        return SUPPORTED_EXTS
    if isinstance(spec, str):
        tokens = spec.replace(",", " ").replace(";", " ").split()
    else:
        tokens = list(spec)
    out = set()
    for tok in tokens:
        e = ("%s" % tok).strip().lower().lstrip(".")
        if not e or e == "all":
            if e == "all":
                return SUPPORTED_EXTS
            continue
        out.add(e)
    return SUPPORTED_EXTS if not out else frozenset(out)


def unknown_formats(spec):
    # type: (object) -> tuple
    """Accept-list tokens that NO lane supports, sorted — for a typo warning.

    ``normalize_accept`` keeps unknown tokens so they can be surfaced here; a caller
    warns on a non-empty result (e.g. ``--accept "docx,pfd"`` -> ``("pfd",)``) instead
    of silently declining every real file. Empty when ``spec`` means "accept all"
    (None/""/"all"), since that resolves to exactly ``SUPPORTED_EXTS``.
    """
    return tuple(sorted(normalize_accept(spec) - SUPPORTED_EXTS))


# One file's routing verdict. ``accepted`` is the final gate: a producer converts
# the file only when accepted is True; otherwise ``reason`` explains (to the user)
# why it was left alone.
SourceClass = namedtuple("SourceClass", ["ext", "lane", "accepted", "reason"])


def classify_source(name, accept=None):
    # type: (str, object) -> SourceClass
    """Route one source path/filename, honoring an optional accept-list.

    The single entry every producer uses to decide fate + build user warnings:
      * unknown extension           -> accepted=False, lane=ROUTE_UNSUPPORTED
      * known but not in ``accept`` -> accepted=False, lane=<its lane>
      * known and accepted          -> accepted=True,  lane=<its lane>
    ``accept`` follows ``normalize_accept`` (None/"all" = every supported format).
    ``reason`` is empty when accepted, else a short human phrase.
    """
    ext = ext_of(name)
    lane = route_format(ext)
    shown = ("." + ext) if ext else "(no extension)"
    if lane == ROUTE_UNSUPPORTED:
        return SourceClass(ext, lane, False,
                           "unsupported format %s" % shown)
    allowed = normalize_accept(accept)
    if ext not in allowed:
        return SourceClass(ext, lane, False,
                           "format %s excluded by accept-list" % shown)
    return SourceClass(ext, lane, True, "")


# A whole-tree routing summary: which lane each accepted file belongs to, plus the
# files that will NOT be converted (unsupported extension, or excluded by the
# accept-list) so a producer can warn about them in one place.
RouteScan = namedtuple("RouteScan", ["by_lane", "unsupported", "declined"])


def summarize_routes(names, accept=None):
    # type: (list, object) -> RouteScan
    """Classify many filenames at once (pure — no disk).

    Returns ``RouteScan(by_lane, unsupported, declined)`` where ``by_lane`` maps a
    lane to the accepted names routed there, ``unsupported`` lists names with an
    extension no lane owns, and ``declined`` lists names excluded by the accept-
    list. The two latter buckets are exactly what "was not converted" — a caller
    warns from them so nothing is dropped silently.
    """
    by_lane = {}
    unsupported = []
    declined = []
    for name in names:
        sc = classify_source(name, accept)
        if sc.accepted:
            by_lane.setdefault(sc.lane, []).append(name)
        elif sc.lane == ROUTE_UNSUPPORTED:
            unsupported.append(name)
        else:
            declined.append(name)
    return RouteScan(by_lane, unsupported, declined)
