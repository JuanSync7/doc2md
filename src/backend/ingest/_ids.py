"""
title: Document id hashing (private)
layer: backend
public_api: no
summary: Stable doc id from a source-relative path; shared by build_index and docling_convert.
"""
# 3.6-compatible. No external deps.
import hashlib

__all__ = ["doc_id"]


def doc_id(rel_path):
    # type: (str) -> str
    """The stable id for a document, derived from its path relative to the source root.

    ``sha1(rel_path)[:16]`` — the single source of truth for the id used as the
    ``<id>.txt`` / ``<id>.md`` filename. ``build_index.py`` and
    ``docling_convert.py`` both call this so the text store and the markdown store
    agree on filenames without a side channel. Stable across runs and machines.
    """
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
