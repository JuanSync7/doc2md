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

The rubric lives in ``backend.validate`` as pure predicates; this file is the
transport that gives them something to look at.
"""
from __future__ import print_function

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

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

def run_suites(rows, quiet=True):
    # type: (list, bool) -> dict
    """Execute each distinct pytest target a suite row names.

    A target that does not exist yet is left as ``None`` — "not run" — rather than
    False. The distinction matters: an absent test is a missing check, and the
    rubric refuses to call that either a pass or a failure."""
    verdicts = {}
    for target in sorted(set(r.target for r in rows if r.kind == "suite")):
        path = os.path.join(_REPO, target)
        if not os.path.exists(path):
            verdicts[target] = None
            continue
        if target.endswith("run_eval.py"):
            # The eval harness is its own runner, not a pytest module. --skip-pdf
            # keeps it to the lanes that run without the PDF interpreter; the PDF
            # expectations report SKIP, which the harness prints as such.
            cmd = [sys.executable, path, "--skip-pdf"]
        else:
            cmd = [sys.executable, "-m", "pytest", target, "-q", "--no-header"]
        proc = subprocess.Popen(cmd, cwd=_REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        verdicts[target] = proc.returncode == 0
        if not quiet or proc.returncode != 0:
            print("  %-58s %s" % (target, "ok" if proc.returncode == 0 else "FAILED"))
            if proc.returncode != 0:
                print("    " + out.strip().splitlines()[-1][:110] if out.strip() else "")
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
        build_corpus(src)
        if convert(src, out) != 0:
            print("the graded run itself failed; nothing to grade", file=sys.stderr)
            return 2
        suites = {} if args.no_suites else run_suites(RUBRIC_ROWS,
                                                      quiet=args.as_json)
        view = load_view(out, _REPO, suites)
        results = grade(view)
        summary = summarize(results)
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
            print("\nartifacts kept in %s" % workdir)


if __name__ == "__main__":
    sys.exit(main())
