"""
title: Unit — the warning vocabulary is closed in both directions
kind: tests
layer: backend
summary: Every documented warning code has an emitter, every emitted code is documented, and every code that stands for a loss carries the count behind it.
"""
# This test exists because of a specific failure. `dropped_headers_footers` was
# documented in the output contract and REQUIRED by end-goal.md §1 ("the drop is
# always deliberate and visible, never an accident") — and had zero emitters. A live
# run discarded a "Confidential — Project Kestrel" banner and reported
# `warnings: []`. Prose promised something no code delivered, and nothing noticed.
#
# So the vocabulary is now closed from both ends: a documented code with no emitter
# fails here, and so does an emitted code nobody documented.
import os
import re

import pytest

pytestmark = pytest.mark.unit

_HERE = os.path.abspath(__file__)                          # tests/unit/backend/<f>.py
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
SCHEMA = os.path.join(REPO, "docs", "reference", "output-schema.md")
SEARCH_ROOTS = ("src", "scripts")

# Codes a caller supplies rather than the code inventing — they are emitted by the
# PDF lane's toolchain probe and by docling, which this repo does not own. Listed
# rather than pattern-matched, so adding one is a deliberate act.
CALLER_SUPPLIED = frozenset((
    "pdf_toolchain", "pdf_content_loss", "pdf_text_layer_fallback",
    "ocr_transcription", "image_inline_bailed", "figure_text_debt",
))

# Codes that stand for CONTENT the reader does not get. Each must publish the size
# of the loss: "some spans were flattened" is a shrug, "6 horizontal, 1 vertical"
# is something an operator can act on.
_COUNT_KEY = re.compile(r"^(parts|chars|horizontal|vertical|insertions|deletions|"
                        r"moves|source_tokens|removed|count|n_[a-z_]+)$")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _emitted_codes():
    """Every warning code any module can actually produce."""
    codes = set()
    for base in SEARCH_ROOTS:
        for root, _dirs, files in os.walk(os.path.join(REPO, base)):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                text = _read(os.path.join(root, fn))
                codes |= set(re.findall(r'"code":\s*"([a-z][a-z0-9_]+)"', text))
    return codes


def _documented_codes():
    """Codes named in the output schema's closed `warnings[]` vocabulary table.

    Anchored to that one section on purpose: matching every table in the document
    would sweep up `argv`, `anchor`, `alt` and every other first-column key, and a
    test that cannot fail is worse than no test."""
    doc = _read(SCHEMA)
    section = re.search(r"^### `warnings\[\]`$(.*?)(?=^#{1,3} |\Z)", doc,
                        re.S | re.M)
    assert section, "no `### `warnings[]`` section in %s" % SCHEMA
    return set(re.findall(r"^\|\s*`([a-z][a-z0-9_]+)`\s*\|", section.group(1), re.M))


def test_every_documented_warning_code_has_an_emitter():
    documented = _documented_codes()
    assert documented, "no warning-code table found in %s" % SCHEMA
    orphans = sorted(documented - _emitted_codes() - CALLER_SUPPLIED)
    assert not orphans, (
        "documented but never emitted — prose promising behaviour no code "
        "delivers, which is exactly how dropped_headers_footers hid: %s" % orphans)


def test_every_emitted_warning_code_is_documented():
    undocumented = sorted(_emitted_codes() - _documented_codes())
    assert not undocumented, (
        "emitted but undocumented — a reader meeting this code in a report has "
        "nowhere to look it up: %s" % undocumented)


def test_a_deliberate_drop_carries_the_size_of_the_loss():
    from backend.ingest import furniture_drops, policy_drops

    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    header = ('<w:hdr %s><w:p><w:r><w:t>Confidential — Project Kestrel</w:t>'
              '</w:r></w:p></w:hdr>' % W)
    table = ('<w:document %s><w:body><w:tbl>'
             '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="3"/></w:tcPr>'
             '<w:p><w:r><w:t>Group</w:t></w:r></w:p></w:tc></w:tr>'
             '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
             '<w:p><w:r><w:t>RW</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc></w:tr>'
             '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>'
             '<w:tc><w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>d</w:t></w:r></w:p></w:tc></w:tr>'
             '</w:tbl></w:body></w:document>' % W)

    records = furniture_drops({"word/header1.xml": header})
    records += policy_drops({"word/document.xml": table},
                            ["word/document.xml", "word/embeddings/sheet1.xlsx"])
    assert records, "the fixture drops things; nothing reported them"
    seen = set()
    for rec in records:
        seen.add(rec["code"])
        assert rec.get("detail"), "%s has no human detail" % rec["code"]
        counts = [k for k in rec if _COUNT_KEY.match(k)]
        assert counts, "%s names a loss but publishes no count" % rec["code"]
        assert any(rec[k] for k in counts), "%s reports a loss of nothing" % rec["code"]
    assert seen == {"dropped_headers_footers", "flattened_table_spans",
                    "dropped_embedded_objects"}, seen


def test_a_document_that_loses_nothing_reports_nothing():
    # The other half of the contract. A warning list that is never empty is a
    # warning list nobody reads.
    from backend.ingest import furniture_drops, policy_drops

    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    plain = ('<w:document %s><w:body><w:p><w:r><w:t>Just prose.</w:t></w:r></w:p>'
             '</w:body></w:document>' % W)
    assert furniture_drops({}) == []
    assert policy_drops({"word/document.xml": plain}, ["word/document.xml"]) == []
