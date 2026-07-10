"""
title: Unit — backend.ingest select_source
kind: tests
layer: backend
summary: Mirrors src/backend/ingest/_source.py. Docling-when-present, native fallback, correct origin.
"""
import pytest
from backend.ingest import select_source, SourceText, doc_id

pytestmark = pytest.mark.unit


def _native(rel):
    return "NATIVE:" + rel


def test_native_backend_always_uses_extractor():
    st = select_source("a.pdf", _native, markdown_dir="/md", backend="native",
                       read_md=lambda p: "# rich markdown body here")
    assert st == SourceText(text="NATIVE:a.pdf", origin="native")


def test_docling_backend_uses_markdown_when_present():
    md = "# Title\n\n| a | b |\nbody text long enough"
    st = select_source("a.pdf", _native, markdown_dir="/md", backend="docling",
                       read_md=lambda p: md)
    assert st.origin == "docling"
    assert st.text == md


def test_docling_reader_gets_id_based_path():
    seen = {}

    def reader(path):
        seen["path"] = path
        return "markdown content that is long enough"

    select_source("dir/a.pdf", _native, markdown_dir="/md", backend="docling",
                  read_md=reader)
    assert seen["path"].endswith(doc_id("dir/a.pdf") + ".md")


def test_docling_falls_back_when_markdown_missing():
    def missing(path):
        raise FileNotFoundError(path)

    st = select_source("a.pdf", _native, markdown_dir="/md", backend="docling",
                       read_md=missing)
    assert st == SourceText(text="NATIVE:a.pdf", origin="native")


def test_docling_falls_back_when_markdown_too_short():
    st = select_source("a.pdf", _native, markdown_dir="/md", backend="docling",
                       read_md=lambda p: "  x  ", min_chars=20)
    assert st.origin == "native"


def test_docling_without_markdown_dir_falls_back():
    st = select_source("a.pdf", _native, markdown_dir=None, backend="docling",
                       read_md=lambda p: "long enough markdown content here")
    assert st.origin == "native"
