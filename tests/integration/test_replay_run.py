"""
title: Integration — a recorded run can be repeated from its report
kind: tests
layer: backend
summary: replay_run reconstructs the command, reproduces the exact markdown hash, and names every divergence instead of hiding it.
"""
# The owner's stated goal for the reporting layer: "have all the reporting
# information needed to re-run, re-establish, or repeat a run based on the report."
# This is the test that decides whether that is true.
import importlib.util
import json
import os
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _docx(path, text="The transceiver runs at 77 GHz."):
    doc = ('<w:document %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Overview</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>%s</w:t></w:r></w:p>'
           '</w:body></w:document>' % (W, text))
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/styles.xml", styles)


def _build(tmp_path, name="o", extra=()):
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / name
    if not src.exists():
        src.mkdir()
        _docx(str(src / "spec.docx"))
    assert bb.main(["--src", str(src), "--out", str(out),
                    "--run-id", "R1"] + list(extra)) == 0
    d = [os.path.join(str(out), n) for n in os.listdir(str(out))
         if os.path.isdir(os.path.join(str(out), n))][0]
    return str(src), str(out), os.path.join(d, "report.json")


def test_a_report_carries_everything_needed_to_repeat_the_run(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    run = rep["run"]
    assert run["entrypoint"] == "build_bundle" and run["run_id"] == "R1"
    assert run["argv"] == ["--src", "<src>", "--out", "<out>", "--run-id", "R1"]
    assert run["code"]["name"] == "doc2md"
    assert run["host"]["python"]
    assert run["config_ref"] == "runs.jsonl#R1"


def test_replay_reproduces_the_exact_markdown_hash(tmp_path):
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    rc = rr.main(["--report", report_path, "--src", src,
                  "--out", str(tmp_path / "replay"), "--execute", "--compare"])
    # 0 = reproduced with no divergence; 3 = reproduced but this checkout is dirty.
    assert rc in (0, 3)
    with open(report_path, encoding="utf-8") as fh:
        want = json.load(fh)["markdown_sha256"]
    replayed = [os.path.join(str(tmp_path / "replay"), n)
                for n in os.listdir(str(tmp_path / "replay"))
                if os.path.isdir(os.path.join(str(tmp_path / "replay"), n))][0]
    with open(os.path.join(replayed, "report.json"), encoding="utf-8") as fh:
        assert json.load(fh)["markdown_sha256"] == want


def test_a_changed_setting_is_named_before_anything_runs(tmp_path, monkeypatch, capsys):
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    monkeypatch.setenv("DOC2MD_MIN_RECALL", "0.5")
    rc = rr.main(["--report", report_path])
    out_text = capsys.readouterr().out
    assert rc == 3
    assert "DIVERGENCES" in out_text
    assert "min_recall" in out_text and "0.5" in out_text


def test_pointing_at_a_different_source_tree_is_caught(tmp_path, capsys):
    src, out, report_path = _build(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    rr = _mod("replay_run")
    rr.main(["--report", report_path, "--src", str(other),
             "--out", str(tmp_path / "r2")])
    text = capsys.readouterr().out
    assert "different directory" in text


def test_a_report_without_run_provenance_says_so_rather_than_guessing(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    rep.pop("run")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)
    rr = _mod("replay_run")
    assert rr.main(["--report", report_path]) == 1


def test_the_run_log_joins_documents_to_runs(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(os.path.join(out, "runs.jsonl"), encoding="utf-8") as fh:
        runs = [json.loads(l) for l in fh]
    with open(os.path.join(out, "manifest.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh]
    assert len(runs) == 1
    assert set(r["run_id"] for r in rows) == set(r["run_id"] for r in runs)
    # the resolved configuration lives once, in the run row
    assert runs[0]["config"]["min_recall"]["from"] == "default"
    assert runs[0]["corpus_sha256"]


def test_no_artifact_ever_contains_an_absolute_host_path(tmp_path):
    # The root CLAUDE.md forbids it and a bundle is published output. This is the
    # test that keeps the run block from quietly becoming the exception.
    src, out, _report = _build(tmp_path)
    leaked = []
    for root, _dirs, files in os.walk(out):
        for fn in files:
            if not fn.endswith((".json", ".jsonl", ".md")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                text = fh.read()
            for needle in (str(tmp_path), os.path.expanduser("~"), REPO):
                if needle and len(needle) > 4 and needle in text:
                    leaked.append("%s -> %s" % (os.path.join(root, fn), needle))
    assert not leaked, "absolute host paths leaked into artifacts: %s" % leaked
