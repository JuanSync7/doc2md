"""
title: Unit — format routing policy (extension → converter lane)
kind: tests
layer: backend
summary: route_format maps every corpus extension to exactly one markdown-producing lane.
"""
# Pure policy — no disk. The single source of truth for "which converter owns
# which format"; docling_convert.py and office_convert.py both consult it, so a
# format can never be double-converted or silently unowned.
from backend.ingest import (route_format, classify_source, summarize_routes,
                            supported_formats, normalize_accept, ext_of,
                            SUPPORTED_EXTS, ROUTE_OOXML, ROUTE_DOCLING,
                            ROUTE_PASSTHROUGH, ROUTE_FENCE, ROUTE_LIBREOFFICE,
                            ROUTE_UNSUPPORTED)


def test_office_formats_route_to_ooxml():
    for ext in ("docx", "pptx", "xlsx"):
        assert route_format(ext) == ROUTE_OOXML


def test_layout_formats_route_to_docling():
    # PDF is positioned glyphs — structure must be inferred; html docling already
    # renders well (structural markup, revisit as a deterministic lane later).
    for ext in ("pdf", "html", "htm"):
        assert route_format(ext) == ROUTE_DOCLING


def test_markdown_and_plain_text_pass_through():
    for ext in ("md", "markdown", "txt", "text"):
        assert route_format(ext) == ROUTE_PASSTHROUGH


def test_code_like_data_formats_are_fenced_verbatim():
    # Already machine-readable structure: embedding verbatim in a code fence is
    # lossless by construction; "converting" them to prose would only lose shape.
    for ext in ("json", "yaml", "yml", "toml", "xml", "csv", "tsv", "ini"):
        assert route_format(ext) == ROUTE_FENCE


def test_odf_and_legacy_binary_route_via_libreoffice():
    # ODF (LibreOffice zip+XML) and legacy binary Office: soffice converts them
    # to OOXML first, then the OOXML lane owns them.
    for ext in ("odt", "odp", "ods", "doc", "ppt", "xls", "rtf"):
        assert route_format(ext) == ROUTE_LIBREOFFICE


def test_unknown_extensions_are_unsupported():
    for ext in ("bin", "exe", "png", "", "tar.gz"):
        assert route_format(ext) == ROUTE_UNSUPPORTED


def test_route_is_case_and_dot_insensitive():
    assert route_format("DOCX") == ROUTE_OOXML
    assert route_format(".pdf") == ROUTE_DOCLING
    assert route_format(" Xlsx ") == ROUTE_OOXML


# --- ext_of: filename -> normalized extension --------------------------------

def test_ext_of_handles_paths_case_and_no_extension():
    assert ext_of("a/b/Spec.DOCX") == "docx"
    assert ext_of("report.tar.gz") == "gz"          # only the last dot segment
    assert ext_of("README") == ""                    # no extension
    assert ext_of("") == ""
    assert ext_of("dir.d/Makefile") == ""            # dot in a path component, not the name


# --- supported_formats / SUPPORTED_EXTS --------------------------------------

def test_supported_formats_covers_every_lane_and_excludes_unsupported():
    sup = set(supported_formats())
    assert sup == set(SUPPORTED_EXTS)
    for ext in ("docx", "pptx", "xlsx", "pdf", "html", "md", "odt", "json"):
        assert ext in sup                            # every real lane's formats
    assert "bin" not in sup and "exe" not in sup


# --- normalize_accept --------------------------------------------------------

def test_normalize_accept_none_and_all_mean_everything():
    assert normalize_accept(None) == SUPPORTED_EXTS
    assert normalize_accept("") == SUPPORTED_EXTS
    assert normalize_accept("all") == SUPPORTED_EXTS


def test_normalize_accept_parses_strings_and_iterables():
    assert normalize_accept("docx, pdf ;xlsx") == frozenset({"docx", "pdf", "xlsx"})
    assert normalize_accept([".DOCX", "PDF"]) == frozenset({"docx", "pdf"})


# --- classify_source: the router every producer consults ---------------------

def test_classify_source_accepts_known_format_by_default():
    sc = classify_source("a/b/Spec.docx")
    assert sc.ext == "docx" and sc.lane == ROUTE_OOXML
    assert sc.accepted is True and sc.reason == ""


def test_classify_source_flags_unsupported_format():
    sc = classify_source("archive.bin")
    assert sc.lane == ROUTE_UNSUPPORTED and sc.accepted is False
    assert "unsupported" in sc.reason


def test_classify_source_declines_format_outside_accept_list():
    sc = classify_source("paper.pdf", accept="docx,xlsx")
    assert sc.lane == ROUTE_DOCLING          # still knows the lane it WOULD use
    assert sc.accepted is False and "accept-list" in sc.reason
    # but an accepted format in the same run passes
    assert classify_source("sheet.xlsx", accept="docx,xlsx").accepted is True


def test_classify_source_libreoffice_is_a_known_accepted_lane():
    sc = classify_source("legacy.odt")
    assert sc.lane == ROUTE_LIBREOFFICE and sc.accepted is True


# --- summarize_routes: whole-tree buckets for the "not converted" warning ----

def test_summarize_routes_buckets_by_lane_unsupported_and_declined():
    names = ["a.docx", "b.pptx", "c.pdf", "d.odt", "e.bin", "f.json", "g.docx"]
    scan = summarize_routes(names, accept="docx,pdf,odt")
    assert sorted(scan.by_lane[ROUTE_OOXML]) == ["a.docx", "g.docx"]
    assert scan.by_lane[ROUTE_DOCLING] == ["c.pdf"]
    assert scan.by_lane[ROUTE_LIBREOFFICE] == ["d.odt"]
    assert scan.unsupported == ["e.bin"]             # no lane at all
    assert sorted(scan.declined) == ["b.pptx", "f.json"]   # known lane, not accepted
    # pptx is not in the accept-list, so it is NOT routed to a lane bucket
    assert "b.pptx" not in scan.by_lane.get(ROUTE_OOXML, [])
