"""
title: Bundle public API
layer: backend
public_api: yes
summary: Assemble the doc2md output bundle (document.md + structure.json + report.json) from markdown + validator report.
"""
# Callers import FROM HERE, never from the private submodule. This package sits ABOVE
# sections + validate + ingest: it combines the deterministic heading outline
# (backend.sections.document_outline), the validator verdict
# (backend.validate.build_report) and the front-matter renderer
# (backend.ingest.front_matter) into one bundle dict. It is PURE (no disk, no LLM);
# the file writer is scripts/build_bundle.py. See docs/design (doc2md output-contract).
from ._assemble import assemble_bundle

__all__ = [
    "assemble_bundle",
]
