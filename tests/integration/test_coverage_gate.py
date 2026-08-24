"""
title: Integration — the corpus coverage gate cannot pass on empty evidence
kind: tests
layer: backend
summary: coverage_report --fail-under fails a run that measured nothing, still fails a lossy corpus, and still passes a lossless one.
"""
# Integration: coverage_report.py reads _coverage*.jsonl off disk.
#
# The one rule this file exists to keep: --fail-under is a GATE, and a gate that was
# handed no documents has not observed a pass, it has observed nothing. Reporting
# that as success is the vacuous pass the rest of this project spends its reports'
# n_source denominators preventing — and it was reachable by the plainest documented
# invocation, because the default --dir is data/bundles and data/* is gitignored.
import importlib.util
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


def _records(directory, *lines):
    with open(os.path.join(directory, "_coverage.jsonl"), "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


_LOSSLESS = ('{"id":"a","rel":"a.docx","recall":1.0,"n_source":100,'
             '"n_covered":100,"n_missing":0,"missing_top":[]}')
_LOSSY = ('{"id":"b","rel":"b.docx","recall":0.5,"n_source":100,'
          '"n_covered":50,"n_missing":50,"missing_top":[["table",50]]}')


def test_a_gate_that_measured_nothing_is_not_a_pass(tmp_path, capsys):
    """An empty record set under --fail-under must FAIL, loudly and by name."""
    rep = _mod("coverage_report")
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = rep.main(["--dir", str(empty), "--fail-under", "1.0"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "NO coverage records were found" in err
    assert str(empty) in err                     # names the denominator it was given


def test_a_directory_that_does_not_exist_is_not_a_pass_either(tmp_path, capsys):
    """The typo'd path and the wiped corpus are the same finding: nothing measured."""
    rep = _mod("coverage_report")
    missing = str(tmp_path / "no-such-dir")
    assert rep.main(["--dir", missing, "--fail-under", "1.0"]) == 1
    assert "NO coverage records were found" in capsys.readouterr().err


def test_an_empty_corpus_without_the_gate_is_still_only_a_report(tmp_path):
    """The other direction, and the reason the check lives INSIDE `if fail_under`:
    --fail-under 0.0 is the documented default and means "never fail", so a plain
    report run over an empty directory must still exit 0."""
    rep = _mod("coverage_report")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rep.main(["--dir", str(empty)]) == 0
    assert rep.main(["--dir", str(empty), "--fail-under", "0.0"]) == 0


def test_an_ordinary_lossless_corpus_still_passes_the_gate(tmp_path):
    """A gate fix that starts rejecting valid input is worse than the bug it fixed."""
    rep = _mod("coverage_report")
    d = tmp_path / "good"
    d.mkdir()
    _records(str(d), _LOSSLESS)
    assert rep.main(["--dir", str(d), "--fail-under", "1.0"]) == 0


def test_a_lossy_corpus_still_fails_the_gate(tmp_path, capsys):
    """The gate that already worked must keep working: real evidence of loss."""
    rep = _mod("coverage_report")
    d = tmp_path / "lossy"
    d.mkdir()
    _records(str(d), _LOSSLESS, _LOSSY)
    assert rep.main(["--dir", str(d), "--fail-under", "1.0"]) == 1
    assert "is below --fail-under" in capsys.readouterr().err


def test_json_output_survives_the_empty_gate(tmp_path, capsys):
    """--json owns the machine-readable side; a consumer must still get its (empty)
    array on the failing path, or the exit code is the only thing it can read."""
    rep = _mod("coverage_report")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rep.main(["--dir", str(empty), "--json", "--fail-under", "1.0"]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"
    assert "NO coverage records were found" in captured.err
