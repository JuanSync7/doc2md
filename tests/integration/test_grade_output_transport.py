"""
title: Integration — the grader's own transport
kind: tests
layer: backend
summary: A rubric row is answered by the checks it names, never by an exit code, and --json owns stdout on every path the tool documents.
"""
# scripts/grade_output.py is the command whose verdict docs/quality-plan.md cites
# as the project's evidence that "losslessness is measured, not assumed". Two ways
# it could lie were reproduced before these tests existed:
#
#   * it read a subprocess EXIT CODE as a verdict, and a pytest target whose tests
#     were all skipped exits 0 — so an unproven row printed `ok`;
#   * `--json`, the documented machine-readable mode, wrote pytest chatter and a
#     housekeeping line onto stdout and nothing at all on its documented exit-2
#     path, so the consumer broke on exactly the runs it exists for.
#
# Both are transport faults, invisible from any input document, which is why they
# get their own suite rather than a fixture.
import importlib.util
import json
import os

import pytest

from backend.validate import RUBRIC_ROWS, grade

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Build test rows off a real one so the Row shape is never duplicated here: if the
# rubric grows a field, this suite grows it too instead of quietly going stale.
_A_SUITE_ROW = [r for r in RUBRIC_ROWS if r.kind == "suite"][0]


@pytest.fixture(scope="module")
def go():
    spec = importlib.util.spec_from_file_location(
        "grade_output", os.path.join(REPO, "scripts", "grade_output.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(rid, target, selector):
    return _A_SUITE_ROW._replace(rid=rid, condition="the condition under test",
                                 target=str(target), selector=tuple(selector))


def _suite_file(tmp_path, body, name="test_probe.py"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------- the row is answered by its evidence

def test_a_check_that_was_skipped_is_never_recorded_as_a_pass(go, tmp_path):
    # The whole target exits 0. "A skip is never an A" is this project's own rule
    # and the rubric's runner was the thing breaking it.
    path = _suite_file(tmp_path, "import pytest\n\n\n"
                                 "def test_it_holds():\n"
                                 "    pytest.skip('no environment for this')\n")
    verdicts = go.run_suites([_row("A9", path, ["test_it_holds"])], quiet=True)
    assert verdicts["A9"][0] == "skip", verdicts
    assert "skip" in verdicts["A9"][1]


def test_a_row_whose_named_evidence_no_longer_exists_is_a_failure(go, tmp_path):
    # The file is green. The demonstration the row is graded on was renamed away,
    # so there is nothing left that shows the condition holds — and a rubric that
    # keeps printing pass over evidence nobody can run is the failure this whole
    # grader exists to stop.
    path = _suite_file(tmp_path, "def test_something_else_entirely():\n"
                                 "    assert True\n")
    verdicts = go.run_suites([_row("A9", path, ["test_the_thing_the_row_claims"])],
                             quiet=True)
    assert verdicts["A9"][0] == "fail", verdicts
    assert "test_the_thing_the_row_claims" in verdicts["A9"][1]


def test_an_unrelated_test_in_the_same_file_neither_earns_the_row_nor_sinks_it(
        go, tmp_path):
    # Both halves of "the row is graded on its own evidence": the named check
    # passing is enough, and an unrelated red test in the same file is somebody
    # else's row to fail.
    path = _suite_file(tmp_path, "def test_named():\n"
                                 "    assert True\n\n\n"
                                 "def test_unrelated():\n"
                                 "    assert False\n")
    verdicts = go.run_suites([_row("A9", path, ["test_named"])], quiet=True)
    assert verdicts["A9"][0] == "pass", verdicts

    both = go.run_suites([_row("A9", path, ["test_named"]),
                          _row("B9", path, ["test_unrelated"])], quiet=True)
    assert both["A9"][0] == "pass" and both["B9"][0] == "fail", both


def test_a_parametrised_case_answers_for_the_test_that_owns_it(go, tmp_path):
    path = _suite_file(tmp_path, "import pytest\n\n\n"
                                 "@pytest.mark.parametrize('n', [1, 2])\n"
                                 "def test_named(n):\n"
                                 "    assert n\n")
    verdicts = go.run_suites([_row("A9", path, ["test_named"])], quiet=True)
    assert verdicts["A9"][0] == "pass", verdicts


def test_a_target_that_could_not_run_at_all_is_unknown_not_passed(go, tmp_path):
    # A collection error exits 2 today, but the point is that NO check reported an
    # outcome: there is nothing to read a verdict off, so the row is unproven.
    path = _suite_file(tmp_path, "import a_module_that_does_not_exist\n\n\n"
                                 "def test_named():\n"
                                 "    assert True\n")
    verdicts = go.run_suites([_row("A9", path, ["test_named"])], quiet=True)
    assert verdicts["A9"][0] == "skip", verdicts


def test_a_target_that_does_not_exist_is_unknown_not_passed(go, tmp_path):
    verdicts = go.run_suites(
        [_row("A9", tmp_path / "test_absent.py", ["test_named"])], quiet=True)
    assert verdicts["A9"][0] == "skip", verdicts


def test_the_rubric_records_the_runners_verdict_verbatim(go, tmp_path):
    # End to end through the real rubric: what run_suites observed is what the row
    # reports, keyed by row id so two rows on one file can disagree.
    path = _suite_file(tmp_path, "def test_named():\n    assert True\n")
    rid = _A_SUITE_ROW.rid
    verdicts = go.run_suites([_row(rid, path, ["test_named"])], quiet=True)
    seen = {r.rid: r for r in grade({"bundles": [], "suites": verdicts})}
    assert seen[rid].status == "pass"
    assert "test_named" in seen[rid].evidence


# ----------------------------------------------------- --json owns stdout

def test_run_suites_prints_nothing_to_stdout_even_when_a_check_fails(
        go, tmp_path, capsys):
    # The leak that made the machine-readable mode unparseable: the FAILED lines
    # ignored `quiet` (which IS --json) and landed on stdout ahead of the document.
    path = _suite_file(tmp_path, "def test_named():\n    assert False\n")
    go.run_suites([_row("A9", path, ["test_named"])], quiet=True)
    captured = capsys.readouterr()
    assert captured.out == "", captured.out
    assert "A9" in captured.err


def test_the_documented_break_path_emits_one_json_document(go, tmp_path,
                                                           monkeypatch, capsys):
    # Exit 2 is "the run itself broke". It used to write zero bytes to stdout, so a
    # consumer got a JSONDecodeError instead of the reason.
    monkeypatch.setattr(go, "build_corpus", lambda src: [])
    monkeypatch.setattr(go, "convert", lambda src, out: 1)
    rc = go.main(["--no-suites", "--json", "--workdir", str(tmp_path / "wd")])
    assert rc == 2
    doc = json.loads(capsys.readouterr().out)
    # Structurally distinguishable from a grade: a reader keyed on `summary` must
    # raise rather than conclude that no row failed.
    assert list(doc) == ["error"]
    assert doc["error"]["stage"] == "convert"


def test_an_unexpected_break_is_exit_2_and_not_the_grade_that_means_not_yet(
        go, tmp_path, monkeypatch, capsys):
    # A truncated report.json from a killed run used to escape main() as a
    # traceback and exit 1 — the code documented as "quality is not yet an A".
    def _boom(src):
        raise ValueError("a half-written artifact")

    monkeypatch.setattr(go, "build_corpus", _boom)
    rc = go.main(["--no-suites", "--json", "--workdir", str(tmp_path / "wd")])
    assert rc == 2
    doc = json.loads(capsys.readouterr().out)
    assert list(doc) == ["error"]
    assert "ValueError" in doc["error"]["detail"]


def test_keep_does_not_append_a_human_line_after_the_json_document(
        go, tmp_path, monkeypatch, capsys):
    # `--keep --json` corrupted stdout on a completely SUCCESSFUL run: the finally
    # block printed "artifacts kept in ..." after json.dumps had already run.
    monkeypatch.setattr(go, "build_corpus", lambda src: [])
    monkeypatch.setattr(go, "convert", lambda src, out: 0)
    monkeypatch.setattr(go, "load_view",
                        lambda out, docs_root, suites: {"bundles": [],
                                                        "suites": suites})
    rc = go.main(["--no-suites", "--json", "--keep",
                  "--workdir", str(tmp_path / "wd")])
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert set(doc) == set(["summary", "rows"])
    assert rc == 1                      # an empty corpus is emphatically not an A
    assert "artifacts kept" in captured.err
    assert "artifacts kept" not in captured.out


def test_a_real_verdict_is_never_dressed_up_as_a_broken_run(
        go, tmp_path, monkeypatch, capsys):
    # The trap on the other side of the fix: `return 1` means one or more rows did
    # not pass, which is a true statement about real output. Converting it into
    # exit 2 would hide a failing grade behind a tooling complaint.
    monkeypatch.setattr(go, "build_corpus", lambda src: [])
    monkeypatch.setattr(go, "convert", lambda src, out: 0)
    monkeypatch.setattr(go, "load_view",
                        lambda out, docs_root, suites: {"bundles": [],
                                                        "suites": suites})
    rc = go.main(["--no-suites", "--workdir", str(tmp_path / "wd")])
    assert rc == 1
    assert "OVERALL" in capsys.readouterr().out


# --------------------------------------------------- what the eval harness said

def test_the_eval_row_is_graded_on_its_own_fixture_and_the_census_is_reported(go):
    # run_eval is not pytest, so the row names the FIXTURE it claims. A skipped or
    # deleted expectation cannot be covered by eighteen green siblings, and the
    # skips nothing grades are printed rather than swallowed.
    table = ("RES   DOCUMENT                               DETAIL\n"
             "PASS  office/kestrel-adversarial.docx        19 check(s)\n"
             "SKIP  pdf/kestrel-clock-spec.pdf             pdf lane did not run\n"
             "PASS  text/pin-map.csv                       4 check(s)\n"
             "eval: 2 pass, 0 fail, 1 skip\n")
    observed = go._eval_outcomes(table)
    row = _row("A5", "evals/run_eval.py", ["office/kestrel-adversarial.docx"])
    assert go._row_verdict(row, observed, "exit 0")[0] == "pass"
    assert "pdf/kestrel-clock-spec.pdf" in go._eval_census(observed)

    gone = _row("A5", "evals/run_eval.py", ["office/kestrel-was-deleted.docx"])
    assert go._row_verdict(gone, observed, "exit 0")[0] == "fail"

    skipped = _row("A5", "evals/run_eval.py", ["pdf/kestrel-clock-spec.pdf"])
    assert go._row_verdict(skipped, observed, "exit 0")[0] == "skip"
