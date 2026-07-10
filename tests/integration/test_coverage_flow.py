"""
title: Integration — coverage measurement is wired into the conversion flow
kind: tests
layer: backend
summary: _source_text/_coverage_record cross-check markdown vs an independent source read; report summarizes.
"""
# Integration: builds real OOXML zips on disk and loads the 3.12 scripts.
import importlib.util
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name):
    path = os.path.join(REPO, "scripts", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _md_src(path, text):
    """Write ``text`` as a docling-lane .md source; _source_text reads it verbatim,
    so the test controls the coverage ground truth exactly."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_validator_obeys_docling_exit_status(tmp_path):
    """We trust docling's status (its 'exit code'): a PARTIAL_SUCCESS/FAILURE is invalid
    (must re-convert) EVEN when text recall is perfect. SUCCESS/PASSTHROUGH pass."""
    dc = _load("docling_convert")
    p = str(tmp_path / "d.md")
    _md_src(p, "alpha beta gamma delta content here extra note words present")
    md = "alpha beta gamma delta content here extra note words present"   # full recall
    base = {"figures": None}

    ok = dc._coverage_record("i", "d.md", p, md, extras=dict(base, status="SUCCESS"))
    assert ok["recall"] >= 0.99 and ok["valid"] is True

    for bad in ("PARTIAL_SUCCESS", "FAILURE"):
        rec = dc._coverage_record("i", "d.md", p, md, extras=dict(base, status=bad))
        assert rec["recall"] >= 0.99          # content is all there ...
        assert rec["valid"] is False          # ... but docling said it didn't finish -> re-convert
        assert rec["docling_status"] == bad

    passt = dc._coverage_record("i", "d.md", p, md, extras=dict(base, status="PASSTHROUGH"))
    assert passt["valid"] is True             # .md passthrough is not a docling failure


def test_validator_fails_lossy_even_when_status_success(tmp_path):
    """Second gate: SUCCESS status but low recall (content genuinely lost) is invalid."""
    dc = _load("docling_convert")
    p = str(tmp_path / "d.md")
    _md_src(p, " ".join("word%d" % i for i in range(200)))
    thin = "word0 word1 word2"                 # dropped almost everything
    rec = dc._coverage_record("i", "d.md", p, thin, extras={"figures": None, "status": "SUCCESS"})
    assert rec["recall"] < 0.5 and rec["valid"] is False


def test_coverage_record_embeds_figure_coverage(tmp_path):
    """The per-doc record carries figure loss beside text loss when the caption path ran."""
    dc = _load("docling_convert")
    from backend.ingest import figure_coverage, FIG_CAPTURED, FIG_LOST_BADCROP
    p = str(tmp_path / "d.md")
    _md_src(p, "body content words here note words")
    fc = figure_coverage([FIG_CAPTURED, FIG_LOST_BADCROP], n_placeholders=2)
    rec = dc._coverage_record("id1", "d.md", p, "body content words here note words",
                              extras={"figures": fc})
    assert "figures" in rec
    assert rec["figures"]["n_lost"] == 1
    assert rec["figures"]["lossless"] is False
    # no figures key when the caption path did not run (extras None / figures None)
    rec2 = dc._coverage_record("id1", "d.md", p, "body content words here", extras={"figures": None})
    assert "figures" not in rec2


def test_md_source_passes_through_verbatim(tmp_path):
    """An already-markdown source must be copied verbatim (docling's MD backend drops
    content); coverage of a .md source against itself is then ~1.0."""
    pytest.importorskip("docling.document_converter")   # _load_converter builds a docling converter
    dc = _load("docling_convert")
    src = ("# Spec\n\nThe system must do X.\n\n- item one\n- item two\n\n"
           "```python\ncode_block_kept = True\n```\n")
    p = tmp_path / "spec.md"
    p.write_text(src)
    to_md = dc._load_converter(threads=0, ocr="auto", captions=False, vlm_ocr=False,
                               assets_dir=str(tmp_path), vlm=None, vlm_url=None, vlm_model=None)
    out, extras = to_md(str(p), "d")
    assert out == src                    # verbatim, nothing dropped (the real guarantee)
    assert extras["figures"] is None
    # the only token not covered is the ```python fence language (stripped by
    # markdown_to_text) — a tokenization artifact, not content loss
    from backend.ingest import coverage, markdown_to_text
    rep = coverage(dc._source_text(str(p)), markdown_to_text(out))
    assert rep.n_missing <= 1
    assert not rep.missing_top or rep.missing_top[0][0] == "python"


def test_pdf_coverage_fallback_rescues_diagram_pdf():
    """When docling's PDF markdown covers almost none of the text layer, fall back
    to the text layer; a well-covered PDF is left untouched."""
    dc = _load("docling_convert")
    text_layer = " ".join(["clk divider gate channel sys radar"] * 40)   # >100 tokens
    dc._pdftotext = lambda p: text_layer
    # docling produced near-empty markdown -> lossy -> fallback to text layer
    out = dc._pdf_coverage_fallback("/x/diagram.pdf", "<!-- image -->")
    assert out == text_layer
    # docling markdown already covers the text layer -> unchanged
    good = "clk divider gate channel sys radar " * 40
    assert dc._pdf_coverage_fallback("/x/diagram.pdf", good) == good

    # pdftotext failure or empty -> body unchanged (never breaks conversion)
    def boom(p):
        raise OSError("no pdftotext")
    dc._pdftotext = boom
    assert dc._pdf_coverage_fallback("/x/diagram.pdf", "sparse") == "sparse"
    dc._pdftotext = lambda p: "   "
    assert dc._pdf_coverage_fallback("/x/diagram.pdf", "sparse") == "sparse"


def test_pdf_fallback_triggers_on_partial_content_not_just_near_empty():
    """The content-completeness trigger catches PARTIAL loss (docling covers 60-80% of the
    text layer) that the old token-recall>0.5 gate missed — the text layer is more complete."""
    dc = _load("docling_convert")
    # 100 distinct content words in the text layer
    words = ["term%03d" % i for i in range(100)]
    dc._pdftotext = lambda p: " ".join(words)
    partial = " ".join(words[:65])          # docling kept ~65% of the text content
    out = dc._pdf_coverage_fallback("/x/spec.pdf", partial)
    assert out == " ".join(words)           # fell back to the more-complete text layer
    # docling that preserves ~90% of the content keeps its (structured) markdown
    good = " ".join(words[:92])
    assert dc._pdf_coverage_fallback("/x/spec.pdf", good) == good


def test_pdf_frontmatter_prepended_junk_and_failure_safe():
    dc = _load("docling_convert")
    dc._pdfinfo = lambda p: {"Title": "Real Spec", "Author": "Jane"}
    out = dc._with_pdf_frontmatter("/x/a.pdf", "# Body\n\ntext")
    assert out.startswith('---\ntitle: "Real Spec"')
    assert '"Jane"' in out and "# Body" in out
    # junk filename title -> no front matter added
    dc._pdfinfo = lambda p: {"Title": "a.pdf"}
    assert dc._with_pdf_frontmatter("/x/a.pdf", "# Body") == "# Body"
    # already fronted -> unchanged (idempotent)
    dc._pdfinfo = lambda p: {"Title": "Real"}
    assert dc._with_pdf_frontmatter("/x/a.pdf", "---\ntitle: x\n---\nbody") == "---\ntitle: x\n---\nbody"

    # pdfinfo failure -> body unchanged (never breaks a conversion)
    def boom(p):
        raise OSError("no pdfinfo")
    dc._pdfinfo = boom
    assert dc._with_pdf_frontmatter("/x/a.pdf", "body") == "body"


def test_source_text_html_drops_script_and_style_bodies(tmp_path):
    """_source_text uses html_to_text so embedded JS/CSS doesn't inflate the baseline."""
    dc = _load("docling_convert")
    p = tmp_path / "r.html"
    p.write_text("<style>.x{color:red;font-size:9px}</style>"
                 "<script>var secret_token = compute();</script>"
                 "<body><h1>Register Map</h1><p>offset width reset</p></body>")
    src = dc._source_text(str(p))
    assert "Register Map" in src and "offset" in src and "reset" in src
    assert "secret_token" not in src and "compute" not in src   # script body gone
    assert "color" not in src and "font" not in src             # style body gone


def test_explained_gap_accepts_retokenized_content(tmp_path):
    """Low TOKEN recall but high CONTENT recall (same letters, re-tokenized/de-hyphenated)
    is the 'explained gap' — NOT flagged as lossy."""
    dc = _load("docling_convert")
    p = str(tmp_path / "d.md")
    slide = " ".join("alpha%d-beta%d" % (i, i) for i in range(60))   # 120 hyphenated tokens
    _md_src(p, slide + " closing note words")
    md = " ".join("alpha%dbeta%d" % (i, i) for i in range(60)) + " closing note words"  # fused
    rec = dc._coverage_record("i", "d.md", p, md, extras={"figures": None, "status": "SUCCESS"})
    assert rec["n_source"] >= 50            # above the noise floor (exercise the content gate)
    assert rec["recall"] < 0.8              # token recall dinged by the fusion
    assert rec["content_recall"] >= 0.95    # ... but the content is all there
    assert rec["valid"] is True             # explained gap -> accepted, no needless re-convert


def test_coverage_report_summarize_and_dedup(tmp_path):
    rep = _load("coverage_report")
    # two records for the same id -> last wins; one low-recall doc surfaces as worst
    d = str(tmp_path)
    with open(os.path.join(d, "_coverage.jsonl"), "w") as f:
        f.write('{"id":"a","rel":"a.pptx","recall":0.50,"n_source":100,"n_covered":50,'
                '"n_missing":50,"missing_top":[["notes",30],["skew",5]]}\n')
        f.write('{"id":"a","rel":"a.pptx","recall":0.99,"n_source":100,"n_covered":99,'
                '"n_missing":1,"missing_top":[]}\n')        # newer record for a
    with open(os.path.join(d, "_coverage.1.jsonl"), "w") as f:
        f.write('{"id":"b","rel":"b.pdf","recall":0.40,"n_source":200,"n_covered":80,'
                '"n_missing":120,"missing_top":[["table",60]]}\n')
    recs = rep.load(d)
    assert len(recs) == 2                                  # a deduped to one
    by_id = {r["id"]: r for r in recs}
    assert by_id["a"]["recall"] == 0.99                    # last line won
    text = rep.summarize(recs, worst_n=5, min_tokens=50)
    assert "coverage records: 2 total" in text
    assert "b.pdf" in text                                 # worst doc listed
    assert "table x60" in text                             # its missing tokens shown


def test_report_surfaces_figure_loss(tmp_path):
    rep = _load("coverage_report")
    d = str(tmp_path)
    with open(os.path.join(d, "_coverage.jsonl"), "w") as f:
        # doc x: figures all captured (lossless); doc y: 2 lost + bail
        f.write('{"id":"x","rel":"x.pdf","recall":1.0,"n_source":100,"n_covered":100,'
                '"n_missing":0,"missing_top":[],"figures":{"n_body":3,"n_placeholders":3,'
                '"n_captured":3,"n_gated":0,"n_lost":0,"by_outcome":{"captured":3},'
                '"bailed":false,"lossless":true}}\n')
        f.write('{"id":"y","rel":"y.pptx","recall":1.0,"n_source":100,"n_covered":100,'
                '"n_missing":0,"missing_top":[],"figures":{"n_body":2,"n_placeholders":0,'
                '"n_captured":0,"n_gated":0,"n_lost":2,"by_outcome":{"lost_bail":2},'
                '"bailed":true,"lossless":false}}\n')
    text = rep.summarize(rep.load(d), worst_n=5, min_tokens=10)
    assert "figures:" in text.lower()
    assert "y.pptx" in text                # the figure-lossy doc is surfaced
    assert "lost" in text.lower()
