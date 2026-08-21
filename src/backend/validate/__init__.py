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
                       image_report, caption_report, doc_meta_report,
                       outline_report,
                       savings_report, structure_fidelity_report, MdIssue)
from ._covsummary import summarize as summarize_coverage
from ._mdstructure import md_structure
from ._rubric import (DIMENSIONS, ROWS as RUBRIC_ROWS, gfm_anchor, grade, letter,
                      summarize)

__all__ = [
    "validate_markdown",
    "conversion_report",
    "build_report",
    "image_report",
    "caption_report",
    "doc_meta_report",
    "outline_report",
    "savings_report",
    "structure_fidelity_report",
    "md_structure",
    "summarize_coverage",
    "MdIssue",
    # the executable rubric — docs/quality-plan.md's grade, as predicates
    "DIMENSIONS",
    "RUBRIC_ROWS",
    "gfm_anchor",
    "grade",
    "letter",
    "summarize",
]
