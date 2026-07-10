"""
title: Integration — the passthrough+fence lane converts text/data losslessly
kind: tests
layer: backend
summary: text_convert copies md/txt verbatim, fences json/yaml/csv, gates at recall 1.0, is idempotent.
"""
# Integration (not unit): writes real source files + markdown to disk.
import importlib.util
import json
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _records(out):
    recs = {}
    with open(os.path.join(out, "_coverage_text.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            recs[r["id"]] = r          # last-wins
    return recs


def test_passthrough_md_is_byte_identical(tmp_path):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    body = "# Spec\n\nThe core runs at 2 GHz with __wide__ buses.\n"
    (src / "note.md").write_text(body, encoding="utf-8")
    (src / "plain.txt").write_text("just some words here\n", encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    from backend.ingest import doc_id
    got = (out / (doc_id("note.md") + ".md")).read_text(encoding="utf-8")
    assert got == body                                  # VERBATIM, no re-rendering
    recs = _records(str(out))
    assert all(r["valid"] and r["recall"] == 1.0 for r in recs.values())
    assert recs[doc_id("note.md")]["docling_status"] == "TEXT"


def test_passthrough_with_links_is_not_falsely_lossy(tmp_path):
    # Regression: the gate must compare a verbatim copy against the text the pipeline
    # actually extracts (markdown_to_text), not the RAW markdown — else a link/URL that
    # markdown_to_text strips by design counts as 'lost' and a lossless copy fails.
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    body = "See [the datasheet](http://example.com/specs/widget-v2.pdf) for pinout.\n"
    (src / "ref.md").write_text(body, encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    from backend.ingest import doc_id
    assert (out / (doc_id("ref.md") + ".md")).read_text(encoding="utf-8") == body   # verbatim
    rec = _records(str(out))[doc_id("ref.md")]
    assert rec["valid"] is True and rec["recall"] == 1.0


def test_passthrough_structure_issue_is_advisory_not_gating(tmp_path):
    # A user-authored .md/.txt with a broken table (or stray fence) is copied verbatim;
    # structure issues are recorded as warnings, never a hard fail (no re-conversion could
    # fix a verbatim copy, so failing it would only wedge the self-heal loop).
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    (src / "notes.txt").write_text("col a | col b\n1 | 2 | 3\n", encoding="utf-8")  # ragged table
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    from backend.ingest import doc_id
    rec = _records(str(out))[doc_id("notes.txt")]
    assert rec["valid"] is True                     # still valid (lossless copy)
    assert rec["structure_errors"] == 0             # downgraded


def test_fence_json_is_lossless(tmp_path):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    raw = '{"chip": "widget", "freq_ghz": 77, "lanes": [0, 1, 2]}\n'
    (src / "cfg.json").write_text(raw, encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    from backend.ingest import doc_id, markdown_to_text
    md = (out / (doc_id("cfg.json") + ".md")).read_text(encoding="utf-8")
    assert md.startswith("```json")                     # fenced with the format label
    assert "widget" in markdown_to_text(md)               # content recoverable
    assert _records(str(out))[doc_id("cfg.json")]["recall"] == 1.0


def test_fence_delimiter_grows_past_inner_backticks(tmp_path):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    # content already contains a ``` run — a naive 3-backtick fence would break out
    raw = 'value with ```triple``` and ````quad```` runs\n'
    (src / "d.csv").write_text(raw, encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    from backend.ingest import doc_id
    md = (out / (doc_id("d.csv") + ".md")).read_text(encoding="utf-8")
    assert md.startswith("`````")                       # 5 backticks (quad + 1)
    assert _records(str(out))[doc_id("d.csv")]["valid"] is True


def test_accept_list_declines_and_warns(tmp_path, capsys):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    (src / "keep.md").write_text("keep me\n", encoding="utf-8")
    (src / "drop.json").write_text("{}\n", encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out), "--accept", "md"]) == 0
    from backend.ingest import doc_id
    assert (out / (doc_id("keep.md") + ".md")).exists()
    assert not (out / (doc_id("drop.json") + ".md")).exists()   # declined -> not converted
    err = capsys.readouterr().err
    assert "excluded by the accept-list" in err and "json" in err


def test_reports_other_lanes_and_unsupported(tmp_path, capsys):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    (src / "a.md").write_text("x\n", encoding="utf-8")
    (src / "b.docx").write_text("x\n", encoding="utf-8")
    (src / "c.pdf").write_text("x\n", encoding="utf-8")
    (src / "d.xyz").write_text("x\n", encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert "OOXML lane" in err                          # docx noted
    assert "docling lane" in err                        # pdf noted
    assert "UNSUPPORTED" in err and "xyz" in err        # xyz warned, never silent


def test_typo_in_accept_list_warns(tmp_path, capsys):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    (src / "a.md").write_text("x\n", encoding="utf-8")
    tc.main(["--src", str(src), "--out", str(out), "--accept", "md,jsonn"])
    assert "match NO lane" in capsys.readouterr().err   # 'jsonn' typo surfaced


def test_idempotent_skip_and_validate_only(tmp_path):
    tc = _mod("text_convert")
    src = tmp_path / "src"; out = tmp_path / "md"
    src.mkdir(); out.mkdir()
    (src / "a.md").write_text("alpha beta\n", encoding="utf-8")
    (src / "b.yaml").write_text("k: v\n", encoding="utf-8")
    assert tc.main(["--src", str(src), "--out", str(out)]) == 0
    # second run: both already valid -> nothing to convert
    srcs, _ = tc.scan_tree(str(src), None)
    rows = tc.plan(srcs, str(out))
    valid = tc._valid_ids(str(out))
    todo = [r for r in rows if not (r["id"] in valid and os.path.isfile(r["dest"]))]
    assert todo == []
    # validate-only re-gates without writing
    assert tc.main(["--src", str(src), "--out", str(out), "--validate-only"]) == 0
