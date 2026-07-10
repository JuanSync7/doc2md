"""
title: Source-text backend selection (private)
layer: backend
public_api: no
summary: Pick docling markdown when present, else fall back to the native extractor (per doc).
"""
# 3.6-compatible. Stdlib only.
import os
from collections import namedtuple

from ._ids import doc_id

__all__ = ["SourceText", "select_source"]

# origin is "docling" (rich markdown) or "native" (deterministic extractor).
SourceText = namedtuple("SourceText", ["text", "origin"])


def _read_file(path):
    # type: (str) -> str
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def select_source(rel_path, native_extract, markdown_dir=None,
                  backend="native", read_md=None, min_chars=20):
    # type: (...) -> SourceText
    """Choose the source text for one document.

    ``backend="docling"``: if ``markdown_dir/<doc_id>.md`` exists and has at least
    ``min_chars`` non-whitespace characters, return it (origin ``"docling"``);
    otherwise fall back to ``native_extract`` for THIS doc (origin ``"native"``).
    A docling miss/failure therefore degrades to native, never to empty.

    ``backend="native"`` (default): always ``native_extract``.

    ``native_extract`` is a callable ``rel_path -> str`` (dependency-injected so
    the caller owns the per-type extractors). ``read_md`` is an optional
    ``path -> str`` reader, injected for tests; when given, the on-disk existence
    check is skipped and a raising reader signals "no markdown" (-> fallback).
    """
    if backend == "docling" and markdown_dir:
        md_path = os.path.join(markdown_dir, doc_id(rel_path) + ".md")
        reader = read_md or _read_file
        text = None
        if read_md is not None or os.path.isfile(md_path):
            try:
                text = reader(md_path)
            except Exception:
                text = None
        if text and len(text.strip()) >= min_chars:
            return SourceText(text=text, origin="docling")
    return SourceText(text=native_extract(rel_path) or "", origin="native")
