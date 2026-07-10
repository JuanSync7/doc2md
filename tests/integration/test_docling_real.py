"""
title: Integration — real docling conversion round-trip
kind: tests
layer: backend
summary: With docling installed, convert a real doc and feed it through the markdown strip.

Gated by importorskip: SKIPS under the 3.6 pipeline interpreter (no docling) and runs
under a docling-capable env, e.g.  .venv/bin/python -m pytest tests/integration/test_docling_real.py
"""
import pytest

docling_converter = pytest.importorskip("docling.document_converter")
from backend.ingest import markdown_to_text  # noqa: E402

pytestmark = pytest.mark.integration

HTML = """<html><body>
<h1>Team Roster</h1>
<table>
<tr><th>Name</th><th>Role</th></tr>
<tr><td>Silicon Operations</td><td>Owner</td></tr>
<tr><td>Owen Carter</td><td>Lead Engineer</td></tr>
</table>
<p>See the <a href="https://x/y">spec</a> for details.</p>
</body></html>
"""


def test_docling_emits_markdown_table_that_strips_clean(tmp_path):
    doc = tmp_path / "roster.html"
    doc.write_text(HTML, encoding="utf-8")

    conv = docling_converter.DocumentConverter()
    md = conv.convert(str(doc)).document.export_to_markdown()

    # docling preserves the table as GFM (the whole reason for this backend)
    assert "| Name" in md and "Silicon Operations" in md
    assert md.count("|") >= 6  # a real pipe table, not flattened text

    # and our shadow strip keeps it grep-clean
    shadow = markdown_to_text(md).lower()
    assert "|" not in shadow
    assert "silicon operations" in shadow          # bold/cell boundary didn't split it
    assert "owen carter lead engineer" in shadow   # cells space-joined, not fused
    assert "https://x/y" not in shadow             # link url dropped, text kept
