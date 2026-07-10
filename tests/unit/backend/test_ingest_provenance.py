"""
title: Unit — document provenance -> YAML front matter
kind: tests
layer: backend
summary: core_properties / pdf_info_meta / front_matter operate purely on XML strings + info dicts.
"""
# Pure policy — no disk. The provenance block both markdown lanes prepend: OOXML
# docProps for office docs, pdfinfo for PDFs, rendered identically by front_matter.
from backend.ingest import core_properties, pdf_info_meta, front_matter


def test_core_properties_takes_first_of_duplicate_and_ignores_attrs():
    core = ('<cp:coreProperties><cp:revision>3</cp:revision>'
            '<cp:revision>99</cp:revision>'
            '<dc:title xml:lang="en">Titled</dc:title></cp:coreProperties>')
    meta = core_properties(core)
    assert meta["version"] == "3"        # first wins
    assert meta["title"] == "Titled"     # attributes on the tag are tolerated


def test_core_properties_reads_title_author_version_dates():
    core = ('<cp:coreProperties xmlns:cp="x" xmlns:dc="y" xmlns:dcterms="z">'
            '<dc:title>InterCPU Comms TRM</dc:title>'
            '<dc:creator>Jane Engineer</dc:creator>'
            '<cp:lastModifiedBy>Bob Reviewer</cp:lastModifiedBy>'
            '<cp:revision>7</cp:revision>'
            '<dcterms:created>2024-01-02T10:00:00Z</dcterms:created>'
            '<dcterms:modified>2024-03-04T12:00:00Z</dcterms:modified>'
            '</cp:coreProperties>')
    meta = core_properties(core)
    assert meta["title"] == "InterCPU Comms TRM"
    assert meta["author"] == "Jane Engineer"
    assert meta["version"] == "7"
    assert meta["created"] == "2024-01-02T10:00:00Z"
    assert meta["modified"] == "2024-03-04T12:00:00Z"
    assert meta["last_modified_by"] == "Bob Reviewer"


def test_core_properties_merges_app_xml_and_omits_absent():
    core = '<cp:coreProperties><dc:title>T</dc:title></cp:coreProperties>'
    app = '<Properties><Company>Example Corp</Company><AppVersion>16.0300</AppVersion></Properties>'
    meta = core_properties(core, app)
    assert meta["title"] == "T"
    assert meta["company"] == "Example Corp"
    assert meta["app_version"] == "16.0300"
    # absent fields are simply not present (no empty keys)
    assert "author" not in meta
    assert "version" not in meta


def test_pdf_info_meta_extracts_and_maps():
    meta = pdf_info_meta({"Title": "SDHOST 3.0 Spec", "Author": "Owen",
                          "CreationDate": "Mon Jan 1 2024", "ModDate": "Tue Feb 2 2024",
                          "Pages": "42"})
    assert meta["title"] == "SDHOST 3.0 Spec"
    assert meta["author"] == "Owen"
    assert meta["created"] == "Mon Jan 1 2024"
    assert meta["modified"] == "Tue Feb 2 2024"
    assert "Pages" not in meta and "pages" not in meta   # only provenance fields


def test_pdf_info_meta_drops_junk_filename_titles():
    assert "title" not in pdf_info_meta({"Title": "Microsoft Word - Report.docx"})
    assert "title" not in pdf_info_meta({"Title": "spec.pdf"})
    assert "title" not in pdf_info_meta({"Title": "  "})
    # a real title with a dot but not a known extension survives
    assert pdf_info_meta({"Title": "IP-XACT 1.5 User Guide"})["title"] == "IP-XACT 1.5 User Guide"


def test_pdf_info_meta_empty():
    assert pdf_info_meta({}) == {}
    assert pdf_info_meta(None) == {}


def test_front_matter_escapes_newlines_and_unicode():
    fm = front_matter({"title": "Line1\nLine2", "author": "Zoé"})
    # a raw newline would break the YAML block -> must be escaped, block stays 4 lines
    assert "\n" in fm
    assert 'title: "Line1\\nLine2"' in fm
    assert 'author: "Zoé"' in fm
    body_lines = [ln for ln in fm.strip().splitlines()]
    assert body_lines[0] == "---" and body_lines[-1] == "---"
    assert len(body_lines) == 4          # ---, title, author, --- (no stray line from \n)


def test_front_matter_empty_is_blank():
    assert front_matter({}) == ""
    assert front_matter(None) == ""
