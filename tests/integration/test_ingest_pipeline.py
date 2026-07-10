"""
title: Integration — ingest two-layer invariant
kind: tests
layer: backend
summary: select_source + markdown_to_text together: canonical markdown in text/, clean shadow for grep.
"""
import pytest
from backend.ingest import select_source, markdown_to_text

pytestmark = pytest.mark.integration

MARKDOWN_DOC = """# Team

| Name | Role |
|------|------|
| **Silicon** Operations | Owner |
| Owen Carter | Lead |

See [the spec](https://x/y) for details.
"""


def _native(rel):
    return "native plain text for " + rel


def _two_layers(rel, md_store):
    """Mimic build_index's per-doc step: canonical text + lowercased grep shadow."""
    st = select_source(rel, _native, markdown_dir="/md", backend="docling",
                       read_md=lambda p: md_store)
    canonical = st.text
    shadow = (markdown_to_text(canonical) if st.origin == "docling" else canonical).lower()
    return st.origin, canonical, shadow


def test_docling_doc_keeps_markdown_canonical_but_clean_shadow():
    origin, canonical, shadow = _two_layers("team.pdf", MARKDOWN_DOC)
    assert origin == "docling"
    # canonical retains rich markdown (tables) for the LLM carders
    assert "|" in canonical and "**Silicon**" in canonical
    # shadow is clean for grep_link: no markdown noise, lowercased, phrases intact & unfused
    assert "|" not in shadow
    assert "**" not in shadow
    assert "silicon operations" in shadow      # bold did not split the phrase
    assert "owen carter lead" in shadow         # table cells space-joined, not fused
    assert "the spec" in shadow                 # link text kept, url dropped
    assert "https://x/y" not in shadow


def test_doc_without_markdown_falls_back_to_native_both_layers():
    st = select_source("only.pdf", _native, markdown_dir="/md", backend="docling",
                       read_md=lambda p: (_ for _ in ()).throw(FileNotFoundError()))
    assert st.origin == "native"
    shadow = st.text.lower()
    assert shadow == "native plain text for only.pdf"
