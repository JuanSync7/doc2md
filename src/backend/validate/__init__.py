"""
title: Validate public API
layer: backend
public_api: yes
summary: Output validators — markdown structural checks + the lossless conversion gate.
"""
# Callers import FROM HERE, never from the private submodules. This package is the
# VALIDATOR layer, deliberately separate from the run path (backend.ingest): the
# converters/router/config produce markdown, and these validators check + gate it.
# It depends on backend.ingest (coverage, markdown_to_text); nothing in ingest
# imports back, so the dependency is one-way (validate sits above ingest).
from ._mdcheck import (validate_markdown, conversion_report, build_report,
                       image_report, caption_report, outline_report,
                       savings_report, MdIssue)

__all__ = [
    "validate_markdown",
    "conversion_report",
    "build_report",
    "image_report",
    "caption_report",
    "outline_report",
    "savings_report",
    "MdIssue",
]
