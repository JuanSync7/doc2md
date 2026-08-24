#!/usr/bin/env python3
"""Grade doc2md's output against the rubric in ``docs/quality-plan.md``.

The plan says a grade is "A only when EVERY condition is demonstrable by a command
that returns a verdict". This is that command::

    python3 scripts/grade_output.py                 # build a corpus, grade it
    python3 scripts/grade_output.py --no-suites     # artifacts only, fast
    python3 scripts/grade_output.py --json          # machine-readable

It builds a small corpus that deliberately includes an *adversarial* document —
one written to break a naive converter — converts it through the real office lane,
enriches it with **no model** (a no-model run is the honest floor: whatever the
page has without an LLM is what the pipeline itself knows), then evaluates every
rubric row and prints a per-dimension letter.

Exit codes: ``0`` every dimension is A, ``1`` not yet, ``2`` the run itself broke.

``--json`` owns stdout completely: exactly one document, either ``{summary, rows}``
on 0/1 or ``{error}`` on 2. Every progress note, suite result and housekeeping line
goes to stderr, so the machine-readable mode stays parseable on the runs a consumer
most needs it for — the failing ones.

The rubric lives in ``backend.validate`` as pure predicates; this file is the
transport that gives them something to look at.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "evals"))

from backend.ingest import split_front_matter                       # noqa: E402
from backend.validate import DIMENSIONS, grade, summarize           # noqa: E402

RUN_ID = "GRADE"


# ------------------------------------------------------------------ the corpus

def build_corpus(src):
    # type: (str) -> list
    """The documents under grade.

    ``gen_corpus`` is the eval corpus generator; reusing it means the graded
    documents and the pinned eval fixtures cannot drift apart. The adversarial one
    is what makes most of dimension A gradeable at all."""
    import gen_corpus as g

    if not os.path.isdir(src):
        os.makedirs(src)
    built = []
    for name, builder in (("kestrel-clock-spec.docx", g.build_spec_docx),
                          ("kestrel-readme.docx", g.build_minimal_docx),
                          ("kestrel-adversarial.docx",
                           getattr(g, "build_adversarial_docx", None))):
        if builder is None:
            print("note: gen_corpus has no build_adversarial_docx yet — the rows "
                  "that need it will report FAIL, which is the truth", file=sys.stderr)
            continue
        builder(os.path.join(src, name))
        built.append(name)
    return built


def _run(cmd, label):
    # type: (list, str) -> int
    proc = subprocess.Popen(cmd, cwd=_REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    out = proc.communicate()[0].decode("utf-8", "replace")
    if proc.returncode not in (0, 3):
        print("%s exited %d:\n%s" % (label, proc.returncode, out[-2000:]),
              file=sys.stderr)
    return proc.returncode


def convert(src, out):
    # type: (str, str) -> int
    rc = _run([sys.executable, os.path.join(_HERE, "build_bundle.py"),
               "--src", src, "--out", out, "--run-id", RUN_ID], "build_bundle")
    if rc != 0:
        return rc
    # A no-model enrichment: no endpoint, no key. Whatever the page carries after
    # this is what doc2md knows on its own, which is exactly what D1 grades.
    env_clean = dict(os.environ)
    for var in ("DOC2MD_VLM_URL", "DOC2MD_VLM_MODEL"):
        env_clean.pop(var, None)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_HERE, "enrich_metadata.py"),
         "--bundles", out, "--run-id", RUN_ID, "--vlm-url", ""],
        cwd=_REPO, env=env_clean, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.communicate()[0].decode("utf-8", "replace")
    # 0 ok, 3 pending — a no-model run leaves model-writable fields empty BY
    # DESIGN, so 3 is a complete success here. Anything else means the metadata
    # rows would be grading stale artifacts, and a grade over stale artifacts is
    # worse than no grade.
    if proc.returncode not in (0, 3):
        print("enrich_metadata exited %d; the metadata rows would be grading "
              "stale artifacts:\n%s" % (proc.returncode, text[-1500:]),
              file=sys.stderr)
        return proc.returncode
    return 0


# ------------------------------------------------------------- loading the view

def _jsonl(path):
    # type: (str) -> list
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _maybe_json(path):
    # type: (str) -> dict
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_view(out, docs_root, suites):
    # type: (str, str, dict) -> dict
    bundles = []
    for name in sorted(os.listdir(out)):
        doc_dir = os.path.join(out, name)
        if not os.path.isdir(doc_dir):
            continue
        md_path = os.path.join(doc_dir, "document.md")
        if not os.path.isfile(md_path):
            continue
        with open(md_path, encoding="utf-8") as fh:
            raw = fh.read()
        front, body = split_front_matter(raw)
        bundles.append({
            "doc_id": name,
            "report": _maybe_json(os.path.join(doc_dir, "report.json")),
            "structure": _maybe_json(os.path.join(doc_dir, "structure.json")),
            "knowledge": _maybe_json(os.path.join(doc_dir, "knowledge.json")),
            "markdown": body,
            "front": front,
        })
    docs = {}
    for rel in ("docs/guide.md", "docs/reference/configuration.md",
                "docs/reference/output-schema.md", "docs/reference/vocabulary.md"):
        path = os.path.join(docs_root, rel)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                docs[rel] = fh.read()
    return {
        "bundles": bundles,
        "manifest": _jsonl(os.path.join(out, "manifest.jsonl")),
        "runs": _jsonl(os.path.join(out, "runs.jsonl")),
        "docs": docs,
        "suites": suites,
    }


# ----------------------------------------------------------------- suite rows

PASS, FAIL, SKIP = "pass", "fail", "skip"     # backend.validate._rubric's statuses

# What a `pytest -v` line and a run_eval result row can say about one check.
_PYTEST_OUTCOMES = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")
_EVAL_OUTCOMES = ("PASS", "FAIL", "SKIP")
_BROKEN = ("FAILED", "ERROR", "FAIL")
# An xfail is a KNOWN failure and an xpass is a surprise; neither demonstrates the
# condition a row asserts, so both are unproven rather than passing.
_UNPROVEN = ("SKIPPED", "SKIP", "XFAIL", "XPASS")


# `path.py::test_name[a param with spaces] PASSED  [ 42%]`. Matched with a regex
# rather than split() because a parametrised id may contain whitespace, and the
# node the outcome belongs to is everything before the verdict word.
_NODE_LINE = re.compile(
    r"^(?P<node>\S.*?\.py::.+?)\s+(?P<outcome>%s)\b" % "|".join(_PYTEST_OUTCOMES))


def _pytest_outcomes(text):
    # type: (str) -> dict
    """{test function name: [outcome, ...]} from a ``pytest -v`` transcript.

    Parametrised cases collapse onto the function that owns them, so a row that
    names ``test_x`` is answered by every ``test_x[...]`` the run reported."""
    seen = {}
    for line in text.splitlines():
        if line.split(" ", 1)[0] in _PYTEST_OUTCOMES:
            continue          # the short summary prints "FAILED path::test - reason"
        m = _NODE_LINE.match(line)
        if not m:
            continue
        name = m.group("node").rsplit("::", 1)[-1].split("[", 1)[0]
        seen.setdefault(name, []).append(m.group("outcome"))
    return seen


def _eval_outcomes(text):
    # type: (str) -> dict
    """{fixture relpath: [PASS|FAIL|SKIP]} from run_eval's result table."""
    seen = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in _EVAL_OUTCOMES:
            continue
        seen.setdefault(parts[1], []).append(parts[0])
    return seen


def _row_verdict(row, observed, note):
    # type: (object, dict, str) -> tuple
    """What one suite row's own named evidence actually did.

    Four outcomes, and the last two are the point of this function. A named check
    that no longer EXISTS is a failure: the demonstration the row is graded on was
    deleted or renamed, and a rubric that keeps printing pass over evidence nobody
    can run is the exact thing this file is supposed to prevent. A named check
    that was SKIPPED is unproven, never passed — a wholly skipped pytest target
    exits 0, and an exit code cannot tell "it holds" from "nobody looked"."""
    names = list(row.selector) or [row.target]
    if observed is None:
        return (SKIP, "%s reported no per-check outcome (%s), so nothing was "
                      "demonstrated" % (row.target, note))
    missing = [n for n in names if n not in observed]
    if missing:
        return (FAIL, "%s no longer contains %s — the evidence this row is graded "
                      "on has disappeared" % (row.target, ", ".join(missing)))
    broke = [n for n in names if set(observed[n]) & set(_BROKEN)]
    if broke:
        return (FAIL, "%s FAILED in %s" % (", ".join(broke), row.target))
    unproven = [n for n in names if set(observed[n]) & set(_UNPROVEN)]
    if unproven:
        return (SKIP, "%s was skipped in %s, and a skip is not a demonstration"
                % (", ".join(unproven), row.target))
    shown = names if len(names) <= 4 else names[:4] + ["(+%d more)" % (len(names) - 4)]
    return (PASS, "%s: %s" % (row.target, ", ".join(shown)))


def _eval_census(observed):
    # type: (dict) -> str
    """The whole eval's pass/skip census, so a row's own green never hides it.

    ``grade_output`` hardcodes ``--skip-pdf``, so the PDF expectations are skipped
    on every ring but the nightly one. That is a DECLARED exclusion, and declaring
    it is the difference between a measurement and a rubber stamp — but the census
    also surfaces an UNdeclared skip (a fixture whose generator was missing), which
    no row grades today."""
    tally = {}
    for outcomes in observed.values():
        for outcome in outcomes:
            tally[outcome] = tally.get(outcome, 0) + 1
    skipped = sorted(rel for rel, o in observed.items() if "SKIP" in o)
    note = "eval census: %d pass, %d fail, %d skip" % (
        tally.get("PASS", 0), tally.get("FAIL", 0), tally.get("SKIP", 0))
    if skipped:
        note += " (not evaluated: %s)" % ", ".join(skipped)
    return note


def run_suites(rows, quiet=True):
    # type: (list, bool) -> dict
    """Answer each suite row from the specific checks it names.

    Each distinct target runs ONCE, verbosely, and every row that names it is then
    answered from the per-check outcomes that run reported. Running per file and
    filtering per row is deliberate twice over: it keeps three rows on one parity
    suite to one execution, and it means an unrelated test in the file can neither
    earn a row nor sink it.

    A target that does not exist yet is left as "not run" rather than False. The
    distinction matters: an absent test is a missing check, and the rubric refuses
    to call that either a pass or a failure.

    Every line this function prints goes to STDERR. Stdout belongs to the verdict,
    and ``--json`` puts a single document there; progress notes mixed into it made
    the machine-readable mode unparseable exactly on the runs a consumer cares
    about."""
    seen_by_target, note_by_target = {}, {}
    for target in sorted(set(r.target for r in rows if r.kind == "suite")):
        path = os.path.join(_REPO, target)
        if not os.path.exists(path):
            seen_by_target[target] = None
            note_by_target[target] = "no such file in the repo"
            continue
        if target.endswith("run_eval.py"):
            # The eval harness is its own runner, not a pytest module. --skip-pdf
            # keeps it to the lanes that run without the PDF interpreter; the PDF
            # expectations report SKIP, which the harness prints as such.
            cmd = [sys.executable, path, "--skip-pdf"]
            parse = _eval_outcomes
        else:
            # -v is what makes the row answerable: it prints one line per test, so
            # the runner can see WHICH check held rather than only whether the file
            # exited 0.
            cmd = [sys.executable, "-m", "pytest", target, "-v", "--no-header",
                   "--tb=line", "-p", "no:cacheprovider"]
            parse = _pytest_outcomes
        proc = subprocess.Popen(cmd, cwd=_REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        observed = parse(out)
        if not observed:
            # Nothing ran: a collection error, an import failure, a harness that
            # died before its table. Exit 0 would have read as a pass.
            seen_by_target[target] = None
            note_by_target[target] = "exit %d, no check reported an outcome" \
                                     % proc.returncode
        else:
            seen_by_target[target] = observed
            note_by_target[target] = "exit %d" % proc.returncode
        if not quiet:
            print("  %-58s %s" % (target, note_by_target[target]), file=sys.stderr)

    verdicts = {}
    for row in [r for r in rows if r.kind == "suite"]:
        observed = seen_by_target.get(row.target)
        status, evidence = _row_verdict(row, observed, note_by_target.get(row.target, ""))
        if row.target.endswith("run_eval.py") and observed:
            evidence = "%s; %s" % (evidence, _eval_census(observed))
        verdicts[row.rid] = (status, evidence)
        if not quiet or status != PASS:
            print("  %-4s %-4s %s" % (status.upper(), row.rid, evidence),
                  file=sys.stderr)
    return verdicts


# ---------------------------------------------------------------------- output

def report(results, summary, verbose=True):
    # type: (list, dict, bool) -> None
    labels = dict(DIMENSIONS)
    for dim, _ in DIMENSIONS:
        rows = [r for r in results if r.dim == dim]
        if not rows:
            continue
        print("\n%s. %s  —  %s" % (dim, labels[dim], summary["grades"][dim]))
        for r in rows:
            mark = {"pass": "ok  ", "fail": "FAIL", "skip": "skip"}[r.status]
            print("  %s %-4s %s" % (mark, r.rid, r.condition))
            if verbose and r.status != "pass":
                print("       %s" % r.evidence)
    print("\n%s" % ("-" * 72))
    print("OVERALL %s   (%d pass, %d fail, %d skip, of %d)"
          % (summary["overall"], summary["passed"], summary["failed"],
             summary["skipped"], summary["total"]))
    if summary["overall"] != "A":
        print("A requires every row to pass. A skipped row is an unproven row.")


def _broke(as_json, stage, message, detail=""):
    # type: (bool, str, str, str) -> int
    """Exit 2 — "the run itself broke" — and say so on whichever channel is in use.

    The JSON document is deliberately NOT ``{summary, rows}``: a consumer must not
    be able to read a break as a grade with no failing rows. It carries ``error``
    and nothing else, so a reader keyed on ``summary`` raises rather than
    concluding that nothing went wrong.

    Only UNEXPECTED breaks come here. ``return 1`` — one or more rows did not pass
    — is a real verdict about real output, and converting that into an error would
    turn a failing grade into a tooling problem."""
    print("%s: %s" % (stage, message), file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    if as_json:
        print(json.dumps({"error": {"stage": stage, "message": message,
                                    "detail": detail}}, indent=2, sort_keys=True))
    return 2


def main(argv=None):
    # type: (list) -> int
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--workdir", default="",
                    help="where to build the graded corpus (default: a temp dir)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the workdir so you can inspect the artifacts")
    ap.add_argument("--no-suites", action="store_true",
                    help="grade artifacts only; suite rows report as skipped")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the results as JSON instead of a table")
    args = ap.parse_args(argv)

    from backend.validate import RUBRIC_ROWS

    workdir = args.workdir or tempfile.mkdtemp(prefix="doc2md-grade-")
    src, out = os.path.join(workdir, "src"), os.path.join(workdir, "out")
    try:
        try:
            build_corpus(src)
            rc = convert(src, out)
            if rc != 0:
                return _broke(args.as_json, "convert",
                              "the graded run itself failed; nothing to grade",
                              "the build stage exited %d" % rc)
            suites = {} if args.no_suites else run_suites(RUBRIC_ROWS,
                                                          quiet=args.as_json)
            view = load_view(out, _REPO, suites)
            results = grade(view)
            summary = summarize(results)
        except Exception as exc:                                    # noqa: BLE001
            # A half-written report.json from a killed run, an unwritable workdir,
            # a corpus generator that raised: all of these used to escape as a
            # traceback and exit 1 — the code that means "not yet an A". They are
            # the run breaking, not a verdict about the output.
            traceback.print_exc()
            return _broke(args.as_json, "grade", "the grading run broke",
                          "%s: %s" % (type(exc).__name__, exc))
        # Outside the except on purpose: `return 1` is an honest verdict about real
        # output and must never be dressed up as a tooling failure.
        if args.as_json:
            print(json.dumps({
                "summary": summary,
                "rows": [dict(dim=r.dim, id=r.rid, condition=r.condition,
                              status=r.status, evidence=r.evidence)
                         for r in results]}, indent=2, sort_keys=True))
        else:
            report(results, summary)
        return 0 if summary["overall"] == "A" else 1
    finally:
        if not (args.keep or args.workdir):
            shutil.rmtree(workdir, ignore_errors=True)
        elif args.keep:
            # stderr: stdout is the verdict, and under --json it is one document.
            print("\nartifacts kept in %s" % workdir, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
