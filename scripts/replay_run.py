#!/usr/bin/env python3
"""Reconstruct — and optionally re-execute — the run that produced a bundle.

The point of recording a ``run{}`` block is that somebody can get the same output
again. This is the tool that cashes that in:

    python3 scripts/replay_run.py --report data/bundles/<id>/report.json \\
        --src /path/to/documents --out /tmp/replay

It prints the exact command line, then names **every divergence** between the
recorded run and this machine before running anything — a different commit, a dirty
checkout, a different interpreter, a configuration value that resolves differently
here. A replay that quietly produced a different answer would be worse than no
replay at all, so divergences are reported whether or not you asked to execute.

``--src`` and ``--out`` are supplied by you: the recorded ``argv`` carries
placeholders, because the root CLAUDE.md forbids publishing an absolute host path
and a bundle is published output. ``source_root_id`` and ``corpus_sha256`` are what
confirm you pointed at the same tree.

Exit codes: 0 all clear, 3 divergences found (or replay produced a different
markdown hash), 1 a usage/IO error.
"""
from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)

from backend.provenance import (code_identity, config_provenance,  # noqa: E402
                                host_identity, path_id)

ENTRYPOINTS = {"build_bundle": "build_bundle.py",
               "build_pdf_bundle": "build_pdf_bundle.py"}


def _load(path):
    # type: (str) -> dict
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _find_run_row(report, report_path):
    # type: (dict, str) -> dict
    """The full ``runs.jsonl`` row for this report's run, or ``{}``.

    The per-document ``run{}`` names where its configuration went
    (``config_ref: runs.jsonl#<run_id>``); this follows that pointer so a replay can
    compare the resolved settings, not only the switches."""
    run = report.get("run") or {}
    ref = run.get("config_ref") or ""
    if "#" not in ref:
        return {}
    fname, run_id = ref.split("#", 1)
    root = os.path.dirname(os.path.dirname(os.path.abspath(report_path)))
    try:
        with open(os.path.join(root, fname), encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("run_id") == run_id:
                    return row
    except (IOError, OSError, ValueError):
        return {}
    return {}


def _git_dirty():
    # type: () -> object
    try:
        with open(os.devnull, "w") as null:
            return bool(subprocess.check_output(
                ["git", "-C", _REPO, "status", "--porcelain"], stderr=null).strip())
    except Exception:                                # noqa: BLE001
        return None


def divergences(run, full, src):
    # type: (dict, dict, str) -> list
    """Every way this machine differs from the recorded run, worst first.

    Reported, never auto-corrected: the operator decides whether a divergence
    matters. A silent "close enough" is how a replay comes to mean nothing."""
    out = []
    was_code = run.get("code") or {}
    now_code = code_identity(_REPO, dirty=_git_dirty())
    if was_code.get("commit") and now_code.get("commit") != was_code.get("commit"):
        out.append(("code", "commit %s -> %s"
                    % (was_code.get("commit", "?")[:12], now_code.get("commit", "?")[:12])))
    if was_code.get("version") != now_code.get("version"):
        out.append(("code", "version %s -> %s"
                    % (was_code.get("version"), now_code.get("version"))))
    if was_code.get("dirty"):
        out.append(("code", "the RECORDED run had uncommitted changes: the code that "
                            "produced this report is not in any commit"))
    if now_code.get("dirty"):
        out.append(("code", "this checkout has uncommitted changes"))

    was_host = run.get("host") or {}
    now_host = host_identity()
    for key in ("python", "implementation"):
        if was_host.get(key) and was_host[key] != now_host.get(key):
            out.append(("host", "%s %s -> %s" % (key, was_host[key], now_host.get(key))))

    if src and run.get("source_root_id"):
        now_id = path_id(os.path.abspath(src))
        if now_id != run["source_root_id"]:
            out.append(("source", "--src is a different directory than the recorded "
                                  "run used (%s -> %s)"
                        % (run["source_root_id"], now_id)))

    was_cfg = (full.get("config") or {})
    if was_cfg:
        from backend.ingest import load_ingest_config as _cfg
        now = _cfg()._asdict()
        no_env = _cfg(env={})._asdict()
        no_file = _cfg(env={}, config_path=os.devnull)._asdict()
        env_present = sorted(k for k in os.environ if k.startswith("DOC2MD_"))
        now_cfg = config_provenance(now, no_env, no_file, env_present)
        for key in sorted(was_cfg):
            if key == "_env_present":
                continue
            before, after = was_cfg[key], now_cfg.get(key)
            if after is None:
                out.append(("config", "%s no longer exists" % key))
            elif before.get("value") != after.get("value"):
                out.append(("config", "%s = %r (was %r, from %s)"
                            % (key, after.get("value"), before.get("value"),
                               before.get("from"))))
    return out


def command_for(run, src, out_dir):
    # type: (dict, str, str) -> list
    """The recorded argv with the placeholders filled back in."""
    script = ENTRYPOINTS.get(run.get("entrypoint", ""), "")
    if not script:
        raise ValueError("report has no replayable entrypoint (%r)"
                         % (run.get("entrypoint"),))
    argv = []
    for arg in run.get("argv") or []:
        if arg == "<src>":
            argv.append(src)
        elif arg == "<out>":
            argv.append(out_dir)
        elif arg == "<path>" or arg.startswith("<path:"):
            raise ValueError("the recorded run used a path argument this tool cannot "
                             "reconstruct (%s); supply it by hand" % arg)
        else:
            argv.append(arg)
    return [sys.executable, os.path.join(_HERE, script)] + argv


def main(argv=None):
    # type: (list) -> int
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--report", required=True,
                    help="a bundle's report.json to replay the run of")
    ap.add_argument("--src", default="",
                    help="source root to use (the recorded argv carries a placeholder)")
    ap.add_argument("--out", default="",
                    help="output root to write the replay into")
    ap.add_argument("--execute", action="store_true",
                    help="actually run it (default: print the command and the "
                         "divergences, change nothing)")
    ap.add_argument("--compare", action="store_true",
                    help="after executing, compare markdown_sha256 with the original")
    args = ap.parse_args(argv)

    try:
        report = _load(args.report)
    except (IOError, OSError, ValueError) as exc:
        print("cannot read %s: %s" % (args.report, exc), file=sys.stderr)
        return 1
    run = report.get("run") or {}
    if not run:
        print("%s has no run{} block — it was written before run provenance existed, "
              "so this run cannot be reconstructed." % args.report, file=sys.stderr)
        return 1
    full = _find_run_row(report, args.report)

    print("run       %s  (%s)" % (run.get("run_id"), run.get("entrypoint")))
    print("code      %s" % json.dumps(run.get("code") or {}))
    print("host      %s" % json.dumps(run.get("host") or {}))
    if full.get("corpus_sha256"):
        print("corpus    %s" % full["corpus_sha256"])
    if not full:
        print("note      runs.jsonl row not found: resolved configuration cannot be "
              "compared (switches still can)")

    diffs = divergences(run, full, args.src)
    if diffs:
        print("\nDIVERGENCES (%d) — this machine is not the recorded one:" % len(diffs))
        for kind, text in diffs:
            print("  %-8s %s" % (kind, text))
    else:
        print("\nno divergences: same code, same interpreter, same resolved settings")

    if not (args.src and args.out):
        print("\npass --src and --out to print the replay command "
              "(the recorded argv carries placeholders on purpose)")
        return 3 if diffs else 0

    try:
        cmd = command_for(run, args.src, args.out)
    except ValueError as exc:
        print("\n%s" % exc, file=sys.stderr)
        return 1
    print("\ncommand   %s" % " ".join(cmd))
    if not args.execute:
        print("          (dry run — pass --execute to run it)")
        return 3 if diffs else 0

    rc = subprocess.call(cmd)
    if rc != 0:
        print("replay exited %d" % rc, file=sys.stderr)
        return rc
    if args.compare:
        did = report.get("doc_id", "")
        new = os.path.join(args.out, did, "report.json")
        try:
            got = _load(new).get("markdown_sha256", "")
        except (IOError, OSError, ValueError):
            print("replayed bundle not found at %s" % new, file=sys.stderr)
            return 3
        want = report.get("markdown_sha256", "")
        if got == want:
            print("REPRODUCED %s markdown_sha256 %s" % (did, want[:16]))
        else:
            print("DIFFERENT  %s markdown_sha256 %s -> %s"
                  % (did, want[:16], got[:16]), file=sys.stderr)
            return 3
    return 3 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
