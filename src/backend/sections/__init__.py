"""
title: Sections public API
layer: backend
public_api: yes
summary: Deterministic markdown structure — the faithful heading outline and a tokenizer-pluggable chunker.
"""
# Callers import FROM HERE, never from the private submodules.
from ._chunk import Section, chunk_sections, is_heading, normalize_title
from ._outline import document_outline, outline_coverage

__all__ = [
    "Section",
    "chunk_sections",
    "document_outline",
    "is_heading",
    "normalize_title",
    "outline_coverage",
]
