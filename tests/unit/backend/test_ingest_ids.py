"""
title: Unit — backend.ingest doc_id
kind: tests
layer: backend
summary: Mirrors src/backend/ingest/_ids.py. doc_id is stable, 16 hex chars, path-sensitive.
"""
import pytest
from backend.ingest import doc_id

pytestmark = pytest.mark.unit


def test_doc_id_is_16_hex_chars():
    d = doc_id("a/b/c.pdf")
    assert len(d) == 16
    assert all(c in "0123456789abcdef" for c in d)


def test_doc_id_is_deterministic():
    assert doc_id("specs/chi.pdf") == doc_id("specs/chi.pdf")


def test_doc_id_distinguishes_paths():
    assert doc_id("a.pdf") != doc_id("b.pdf")


def test_doc_id_matches_legacy_sha1_prefix():
    # Must equal the historic build_index hashing so existing ids stay valid.
    import hashlib
    rel = "Domain/Sub/Report.docx"
    assert doc_id(rel) == hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
