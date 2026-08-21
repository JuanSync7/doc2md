#!/usr/bin/env python3
"""Reconstruct — and optionally re-execute — the run that produced a bundle.

The point of recording a ``run{}`` block is that somebody can get the same output
again. This is the tool that cashes that in:

    python3 scripts/replay_run.py --report data/bundles/<id>/report.json \\
        --src /path/to/documents --out /tmp/replay

It prints the exact command line, then names **every divergence** between the
recorded run and this machine before running anything: a different commit, a dirty
checkout, a different interpreter, an external tool at a different version (or one
this machine cannot even be asked about), a `DOC2MD_*` variable that was set then
and is not now, a configuration value that resolves differently here, and the
source bytes themselves — this document's ``source_sha256`` and the run's
``corpus_sha256``, re-hashed from the tree you passed. A replay that quietly
produced a different answer would be worse than no replay at all, so divergences
are reported whether or not you asked to execute.

``--src`` (and ``--out``, for a stage that writes somewhere else) are supplied by
you: the recorded ``argv`` carries placeholders, because the root CLAUDE.md forbids
publishing an absolute host path and a bundle is published output. **The roots you
pass are always applied**, even when the recorded run took its default and so named
no switch at all — otherwise the replay would read whatever ``$DOC2MD_SRC``
resolves to now, which is a different corpus with no sign that it is one.
``source_root_id`` answers "the same directory?"; the two hashes answer "the same
bytes?", and only the second question is the one that matters.

A bundle is written by more than one stage. ``--stage enrich_metadata`` replays the
metadata run recorded in ``runs[]``; with no ``--stage`` it replays the writer.

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
sys.path.insert(0, _HERE)                        # import the sibling writer

import build_bundle as bb                         # noqa: E402  (hashing + file names)
from backend.provenance import (code_identity, config_provenance,  # noqa: E402
                                corpus_id, host_identity, path_id)

# Every replayable entrypoint, and the three things a replay has to know about it
# that differ per stage:
#   src_flag   which switch names the root it READS
#   out_flag   which switch names the root it WRITES — "" when it writes in place
#   root_kind  what that read root holds: source `documents`, or built `bundles`.
#              Only a documents root can be re-hashed against the recorded
#              `source_sha256` / `corpus_sha256`.
ENTRYPOINTS = {
    "build_bundle": ("build_bundle.py", "--src", "--out", "documents"),
    "build_pdf_bundle": ("build_pdf_bundle.py", "--src", "--out", "documents"),
    "enrich_metadata": ("enrich_metadata.py", "--bundles", "", "bundles"),
}


def _load(path):
    # type: (str) -> dict
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _run_root(report_path):
    # type: (str) -> str
    """The bundle root holding ``manifest.jsonl`` / ``runs.jsonl`` for this report."""
    return os.path.dirname(os.path.dirname(os.path.abspath(report_path)))


def _find_run_row(report, report_path, run=None):
    # type: (dict, str, dict) -> dict
    """The full ``runs.jsonl`` row for this run, or ``{}``.

    The per-document ``run{}`` names where its configuration went
    (``config_ref: runs.jsonl#<run_id>``); this follows that pointer so a replay can
    compare the resolved settings, not only the switches.

    Matched on run_id AND entrypoint. Two stages now write into one root and both
    take ``--run-id`` — the grader passes the SAME id to the writer and the
    enricher — so a lookup by id alone would hand an enrichment report the writer's
    resolved configuration and report "no divergences" about settings it never
    used."""
    run = run if run is not None else (report.get("run") or {})
    ref = run.get("config_ref") or ""
    if "#" not in ref:
        return {}
    fname, run_id = ref.split("#", 1)
    want = run.get("entrypoint") or ""
    try:
        with open(os.path.join(_run_root(report_path), fname), encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("run_id") != run_id:
                    continue
                if want and row.get("entrypoint") and row["entrypoint"] != want:
                    continue
                return row
    except (IOError, OSError, ValueError):
        return {}
    return {}


def _manifest_rows(report_path, run_id, entrypoint):
    # type: (str, str, str) -> list
    """This run's ``manifest.jsonl`` rows that name a source hash.

    The documents the run actually READ — skips and deferrals carry no hash and are
    excluded, exactly as ``corpus_id`` excludes them. That is what makes the corpus
    check work under ``--only`` and ``--limit`` instead of failing every narrowed
    run."""
    rows = []
    try:
        with open(os.path.join(_run_root(report_path), bb.MANIFEST),
                  encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("run_id") != run_id or not row.get("source_sha256"):
                    continue
                if entrypoint and row.get("stage") and row["stage"] != entrypoint:
                    continue
                rows.append(row)
    except (IOError, OSError, ValueError):
        return []
    return rows


def _soffice_version():
    # type: () -> str
    """This machine's soffice version, or ``""`` when there is no binary to ask."""
    try:
        found = bb.oc.find_soffice()
        return (bb.oc.soffice_version(found) or "") if found else ""
    except Exception:                                # noqa: BLE001
        return ""


# The external tools a replay can actually verify. A name with no probe is reported
# as UNVERIFIED rather than passed over: "I could not check the toolchain" and "the
# toolchain is the same" are different answers, and only one of them is evidence.
_TOOL_PROBES = {"soffice": _soffice_version}


def _git_dirty():
    # type: () -> object
    try:
        with open(os.devnull, "w") as null:
            return bool(subprocess.check_output(
                ["git", "-C", _REPO, "status", "--porcelain"], stderr=null).strip())
    except Exception:                                # noqa: BLE001
        return None


def divergences(run, full, src, report=None, report_path=""):
    # type: (dict, dict, str, dict, str) -> list
    """Every way this machine differs from the recorded run, worst first.

    Reported, never auto-corrected: the operator decides whether a divergence
    matters. A silent "close enough" is how a replay comes to mean nothing.

    Six classes, and each of the last three was once named in this docstring while
    the code walked straight past it: the external tool versions in ``run.tools``,
    the ``DOC2MD_*`` variables that were merely PRESENT, and the source bytes
    themselves — ``source_sha256`` for this document and ``corpus_sha256`` for the
    set the run read. Each is a way a replay produces a different answer while
    reporting "no divergences"."""
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

    # External binaries. A recorded soffice version that is not the one on this
    # machine changes what the legacy lane produces, and an ABSENT probe is not a
    # pass: an unverified toolchain must not read as an unchanged one.
    for name in sorted(run.get("tools") or {}):
        was = (run["tools"] or {}).get(name) or ""
        probe = _TOOL_PROBES.get(name)
        now = probe() if probe else ""
        if not probe:
            out.append(("tools", "%s recorded %r and this machine has no probe for "
                                 "it: UNVERIFIED, which is not the same as unchanged"
                        % (name, was)))
        elif not now:
            out.append(("tools", "%s recorded %r and is not installed here"
                        % (name, was)))
        elif now != was:
            out.append(("tools", "%s %s -> %s" % (name, was, now)))

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
                # Recorded precisely BECAUSE the resolution diff cannot see it: a
                # variable whose value equals the default moves nothing above and
                # still changes what a reader would set up elsewhere. Skipping it
                # here threw away the only fact it exists to carry.
                before = set((was_cfg[key] or {}).get("value") or [])
                after = set(env_present)
                gone, added = sorted(before - after), sorted(after - before)
                if gone:
                    out.append(("env", "set for the recorded run, unset here: %s"
                                % ", ".join(gone)))
                if added:
                    out.append(("env", "set here, unset for the recorded run: %s"
                                % ", ".join(added)))
                continue
            before, after = was_cfg[key], now_cfg.get(key)
            if key.startswith("cli."):
                # A stage's own switches. Their flag values are in `argv` and are
                # replayed literally, so the only one this machine can resolve
                # differently is the one that came from the ENVIRONMENT — and that
                # is exactly the one no command line would show you.
                env_name = before.get("env") or ""
                if before.get("from") == "env" and env_name:
                    now_v = os.environ.get(env_name, "")
                    if now_v != before.get("value"):
                        out.append(("env", "$%s = %r here, %r for the recorded run "
                                           "(it is where %s came from)"
                                    % (env_name, now_v, before.get("value"), key)))
                continue
            if after is None:
                out.append(("config", "%s no longer exists" % key))
            elif before.get("value") != after.get("value"):
                out.append(("config", "%s = %r (was %r, from %s)"
                            % (key, after.get("value"), before.get("value"),
                               before.get("from"))))
    # An `_env_present` block that was absent then and is present now is the same
    # divergence read from the other side, and the loop above cannot see it.
    if was_cfg and "_env_present" not in was_cfg:
        added = sorted(k for k in os.environ if k.startswith("DOC2MD_"))
        if added:
            out.append(("env", "set here, unset for the recorded run: %s"
                        % ", ".join(added)))

    out.extend(_source_divergences(run, full, src, report, report_path))
    return out


def _source_divergences(run, full, src, report, report_path):
    # type: (dict, dict, str, dict, str) -> list
    """The bytes: this document's ``source_sha256``, and the run's ``corpus_sha256``.

    Both were recorded and neither was ever read, so a replay over an EDITED source
    tree — same directory, same name, different content — reported "no divergences"
    and then produced different markdown. `source_root_id` only ever answered "same
    directory?", which is a different question.

    Only meaningful when the recorded ``--src`` names a source-document tree: for
    ``enrich_metadata`` it names a bundle root, where `source_relpath` does not
    resolve and re-hashing would compare a document against a directory that never
    held it."""
    out = []
    entry = ENTRYPOINTS.get(run.get("entrypoint", ""))
    if not src or not entry or entry[3] != "documents":
        return out

    rel = (report or {}).get("source_relpath") or ""
    was = (report or {}).get("source_sha256") or ""
    if rel and was:
        now = bb.sha256_file(os.path.join(src, rel))
        if not now:
            out.append(("source", "%s is not in the tree you passed as --src" % rel))
        elif now != was:
            out.append(("source", "%s has changed since the recorded run "
                                  "(source_sha256 %s -> %s)"
                        % (rel, was[:12], now[:12])))

    was_corpus = (full or {}).get("corpus_sha256") or ""
    rows = _manifest_rows(report_path, run.get("run_id", ""),
                          run.get("entrypoint", "")) if was_corpus else []
    if was_corpus and rows:
        rehashed = [{"doc_id": r.get("doc_id", ""),
                     "source_sha256": bb.sha256_file(
                         os.path.join(src, r.get("source_relpath") or ""))}
                    for r in rows]
        now_corpus = corpus_id(rehashed)
        if now_corpus != was_corpus:
            moved = [r["doc_id"][:8] for r, h in zip(rows, rehashed)
                     if h["source_sha256"] != r.get("source_sha256")]
            out.append(("corpus", "the %d document(s) this run read no longer hash "
                                  "to the recorded corpus (%s -> %s); changed or "
                                  "missing: %s"
                        % (len(rows), was_corpus[:12], now_corpus[:12],
                           ", ".join(moved[:6]) or "(a document set difference)")))
    elif was_corpus:
        out.append(("corpus", "corpus_sha256 %s was recorded and manifest.jsonl has "
                              "no rows for this run to check it against: UNVERIFIED"
                    % was_corpus[:12]))
    return out


def command_for(run, src, out_dir):
    # type: (dict, str, str) -> list
    """The recorded argv with the placeholders filled back in, and the roots FORCED.

    Forcing is the point. The recorded argv only carries ``--src`` when the run
    named one: a run that took the default recorded no switch at all, so filling
    placeholders left nothing to fill and the printed command read from whatever
    ``$DOC2MD_SRC`` resolves to at replay time — a different corpus, silently, while
    the divergence list above was busy saying "--src is a different directory".
    Whatever you hand this tool is what the replay reads and writes."""
    entry = ENTRYPOINTS.get(run.get("entrypoint", ""))
    if not entry:
        raise ValueError("report has no replayable entrypoint (%r)"
                         % (run.get("entrypoint"),))
    script, src_flag, out_flag, _kind = entry
    argv = []
    for arg in run.get("argv") or []:
        flag, eq, value = arg.partition("=")
        token = value if eq else arg
        if token == "<src>":
            token = src
        elif token == "<out>":
            token = out_dir
        elif "<path>" in token or "<path:" in token:
            raise ValueError("the recorded run used a path argument this tool cannot "
                             "reconstruct (%s); supply it by hand" % arg)
        argv.append("%s=%s" % (flag, token) if eq else token)

    named = set(a.partition("=")[0] for a in (run.get("argv") or [])
                if a.startswith("-"))
    for flag, value in ((src_flag, src), (out_flag, out_dir)):
        # argparse accepts prefixes, so the recorded switch may be `--sr`.
        if flag and value and not any(flag.startswith(f) for f in named):
            argv += [flag, value]
    return [sys.executable, os.path.join(_HERE, script)] + argv


def main(argv=None):
    # type: (list) -> int
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--report", required=True,
                    help="a bundle's report.json to replay the run of")
    ap.add_argument("--src", default="",
                    help="root the replay READS (source documents, or the bundle "
                         "root for a metadata stage). Always applied, whether or "
                         "not the recorded run named one")
    ap.add_argument("--out", default="",
                    help="output root to write the replay into (stages that rewrite "
                         "in place, like enrich_metadata, do not take one)")
    ap.add_argument("--stage", default="",
                    help="which recorded stage to replay: an entrypoint name from "
                         "the report's runs[] (default: the writer's run{})")
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
    if args.stage:
        # A bundle is written by more than one stage — the writer converts, the
        # enricher re-derives meta.id and the permalink — so which run is being
        # replayed has to be sayable.
        staged = [r for r in (report.get("runs") or [])
                  if r.get("entrypoint") == args.stage]
        if not staged:
            print("%s records no run for stage %r (has: %s)"
                  % (args.report, args.stage,
                     ", ".join(sorted(set(r.get("entrypoint", "?")
                                          for r in (report.get("runs") or []))))
                     or "none"), file=sys.stderr)
            return 1
        run = staged[-1]
    if not run:
        print("%s has no run{} block — it was written before run provenance existed, "
              "so this run cannot be reconstructed." % args.report, file=sys.stderr)
        return 1
    full = _find_run_row(report, args.report, run)

    print("run       %s  (%s)" % (run.get("run_id"), run.get("entrypoint")))
    print("code      %s" % json.dumps(run.get("code") or {}))
    print("host      %s" % json.dumps(run.get("host") or {}))
    if full.get("corpus_sha256"):
        print("corpus    %s" % full["corpus_sha256"])
    if not full:
        print("note      runs.jsonl row not found: resolved configuration cannot be "
              "compared (switches still can)")

    diffs = divergences(run, full, args.src, report, args.report)
    if diffs:
        print("\nDIVERGENCES (%d) — this machine is not the recorded one:" % len(diffs))
        for kind, text in diffs:
            print("  %-8s %s" % (kind, text))
    else:
        print("\nno divergences: same code, same interpreter, same external tools, "
              "same resolved settings, same source bytes")

    entry = ENTRYPOINTS.get(run.get("entrypoint", ""))
    needs_out = bool(entry and entry[2])
    if not args.src or (needs_out and not args.out):
        print("\npass --src%s to print the replay command "
              "(the recorded argv carries placeholders on purpose, and the roots "
              "are taken from you, never from the environment)"
              % (" and --out" if needs_out else ""))
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
