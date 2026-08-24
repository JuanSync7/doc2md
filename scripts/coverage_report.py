#!/usr/bin/env python3
"""Summarise a corpus's coverage records — where is the loss, and is it worth chasing?

Each lane appends one JSON line per document to ``_coverage*.jsonl`` as it converts.
Per-document reports answer "did *this* conversion hold up?"; nobody reads 544 of
them. This answers the corpus question::

    python3 scripts/coverage_report.py --dir data/markdown
    python3 scripts/coverage_report.py --dir data/bundles --worst 20 --min-tokens 100

A document may appear more than once — a re-run appends rather than rewrites — so
records are **deduplicated by id, last line wins**: the newest attempt is the one
that describes what is on disk now.

Exit code is 0 unless ``--fail-under`` is given and the lossless fraction falls
below it, which makes this usable as a CI step rather than only as a report.
``--fail-under`` also fails when there are NO records at all: a gate that was asked
for a lossless corpus and measured nothing has not observed a pass, it has observed
nothing, and reporting that as success is the vacuous pass this project's reports
carry ``n_source`` denominators to prevent.
"""
from __future__ import print_function

import argparse
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.validate import summarize_coverage                     # noqa: E402

PATTERN = "_coverage*.jsonl"


def load(directory, pattern=PATTERN):
    # type: (str, str) -> list
    """Every coverage record under ``directory``, deduplicated by id, last wins.

    Files are read in sorted name order and lines in file order, so "last" is
    deterministic: ``_coverage.1.jsonl`` before ``_coverage.jsonl``, and within a
    file the newest append. A malformed line is skipped rather than fatal — a
    half-written record from an interrupted run must not cost you the report on
    the other 543 documents."""
    by_id = {}
    order = []
    for path in sorted(glob.glob(os.path.join(directory, pattern))):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (IOError, OSError):
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            key = rec.get("id") or rec.get("rel") or line
            if key not in by_id:
                order.append(key)
            by_id[key] = rec
    return [by_id[k] for k in order]


def summarize(records, worst_n=10, min_tokens=50):
    # type: (list, int, int) -> str
    """The report text. Policy lives in ``backend.validate``; this is transport."""
    return summarize_coverage(records, worst_n=worst_n, min_tokens=min_tokens)


def main(argv=None):
    # type: (list) -> int
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--dir", default=os.path.join(_REPO, "data", "bundles"),
                    help="directory holding the _coverage*.jsonl records")
    ap.add_argument("--worst", type=int, default=10,
                    help="how many worst documents to name (default 10)")
    ap.add_argument("--min-tokens", type=int, default=50,
                    help="ignore documents smaller than this when ranking: a "
                         "recall ratio over a handful of tokens is not a finding "
                         "(default 50). The count excluded is always reported.")
    ap.add_argument("--fail-under", type=float, default=0.0,
                    help="exit 1 when the lossless fraction is below this "
                         "(0.0 = never fail, the default)")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="emit the deduplicated records instead of the report")
    args = ap.parse_args(argv)

    records = load(args.dir)
    if args.as_json:
        print(json.dumps(records, indent=2, sort_keys=True))
    else:
        print(summarize(records, worst_n=args.worst, min_tokens=args.min_tokens))
    if args.fail_under:
        # Empty evidence is not a pass. A gate asked for a lossless corpus and handed
        # ZERO measured documents used to exit 0 — which is the vacuous pass the
        # rubric's own `_nothing_to_grade` refuses ("a grader that reports a cheerful
        # A because it was handed no documents"). It is reachable by the plainest
        # documented invocation: the default --dir is data/bundles, and data/* is
        # gitignored, so a fresh clone has nothing there at all.
        # NOTE the check lives INSIDE `if args.fail_under:` on purpose — --fail-under
        # 0.0 is the documented default and means "never fail", so a plain report run
        # over an empty directory must still exit 0 — and it returns BEFORE the
        # division, which is what `and records` was really guarding.
        if not records:
            print("--fail-under %.4f was requested but NO coverage records were found "
                  "under %s: nothing was measured, which is not a pass"
                  % (args.fail_under, args.dir), file=sys.stderr)
            return 1
        good = sum(1 for r in records if float(r.get("recall", 0.0) or 0.0) >= 1.0)
        frac = float(good) / len(records)
        if frac < args.fail_under:
            print("lossless fraction %.4f is below --fail-under %.4f"
                  % (frac, args.fail_under), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
