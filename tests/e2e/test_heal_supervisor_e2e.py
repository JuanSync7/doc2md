"""
title: E2E — elastic supervisor drives workers to a fully-healed corpus
kind: tests
layer: backend
summary: heal_supervisor spawns queue workers over a real tmp corpus (via an injected worker command), work-steals to completion, and walks the OOM->escalation recovery ladder.
"""
# E2E: real subprocesses (supervisor + injected fake workers), tmp corpora on disk.
import json
import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.e2e

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
SUP = os.path.join(REPO, "scripts", "heal_supervisor.py")

# A stand-in converter honoring the REAL worker contract (claims, --only/--reclaim,
# victim markers, valid coverage records) without needing docling. Mode file:
#   ''           -> convert everything claimable
#   'oom_first'  -> claim one doc, record it as in-flight, die with SIGKILL (kernel
#                   OOM shape); flips itself to '' so the escalation lane succeeds.
FAKE_WORKER = textwrap.dedent("""
    import argparse, json, os, sys
    sys.path.insert(0, r"{repo}/src"); sys.path.insert(0, r"{repo}/scripts")
    import docling_convert as dc

    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--worker-id", type=int, default=0)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--ocr", default="auto")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--reclaim", action="store_true")
    ap.add_argument("--fallback-only", action="store_true")
    ap.add_argument("--src")
    ap.add_argument("--out")
    a = ap.parse_args()

    _sources, _ = dc.find_sources(a.src)
    rows = dc.plan(_sources, a.out, False)
    valid = dc._done_ids(a.out)
    todo = [r for r in rows if r["id"] not in valid]
    if a.only:
        want = set(a.only)
        todo = [r for r in rows if r["id"] in want]
    # atomically CONSUME the failure-injection token (rename wins exactly once), so
    # precisely one worker simulates the OOM no matter how many race for it
    mode_file = os.path.join(a.out, "_fake_mode.txt")
    mode = ""
    if not a.only:
        try:
            os.rename(mode_file, mode_file + ".consumed.%d" % os.getpid())
            mode = open(mode_file + ".consumed.%d" % os.getpid()).read().strip()
        except OSError:
            pass
    cov = os.path.join(a.out, "_coverage.w%d.jsonl" % a.worker_id)
    cur = os.path.join(a.out, "_converting.w%d.txt" % a.worker_id)
    for r in todo:
        if not dc._try_claim(a.out, r["id"], "w%d" % a.worker_id, reclaim=a.reclaim):
            continue
        if mode == "oom_first":
            with open(cur, "w") as f:
                f.write(r["id"])
            print("simulating OOM kill on", r["rel"]); sys.stdout.flush()
            os.kill(os.getpid(), 9)
        with open(r["dest"], "w") as f:
            f.write(open(r["src"]).read())
        with open(cov, "a") as f:
            f.write(json.dumps({{"id": r["id"], "rel": r["rel"],
                                 "recall": 1.0, "valid": True}}) + "\\n")
        print("converted", r["rel"]); sys.stdout.flush()
    if os.path.isfile(cur):
        os.remove(cur)
    print("worker done")
""")


def _corpus(tmp_path, n=3):
    src = tmp_path / "corpus"
    out = tmp_path / "md"
    src.mkdir()
    out.mkdir()
    for i in range(n):
        (src / ("doc%d.html" % i)).write_text("<p>Doc %d body words %d</p>" % (i, i))
    fake = tmp_path / "fake_worker.py"
    fake.write_text(FAKE_WORKER.format(repo=REPO))
    return str(src), str(out), str(fake)


def _run_supervisor(src, out, fake, timeout=60):
    cmd = [PY, SUP, "--src", src, "--out", out,
           "--worker-cmd", "%s %s" % (PY, fake),
           "--tick", "0.3", "--max-workers", "2", "--stall-secs", "9999",
           "--max-respawns", "10"]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout)


def test_supervisor_heals_corpus_to_done(tmp_path):
    src, out, fake = _corpus(tmp_path, n=3)
    res = _run_supervisor(src, out, fake)
    assert res.returncode == 0, res.stderr[-2000:]
    # every doc converted + valid, status rollup says done with nothing remaining
    mds = [f for f in os.listdir(out) if f.endswith(".md")]
    assert len(mds) == 3
    status = json.load(open(os.path.join(out, "_heal_status.json")))
    assert status["phase"] == "done"
    assert status["totals"]["remaining"] == 0
    assert status["totals"]["blacklisted"] == 0


def test_supervisor_escalates_oom_victim_and_recovers(tmp_path):
    src, out, fake = _corpus(tmp_path, n=3)
    with open(os.path.join(out, "_fake_mode.txt"), "w") as f:
        f.write("oom_first")
    res = _run_supervisor(src, out, fake)
    assert res.returncode == 0, res.stderr[-2000:]
    mds = [f for f in os.listdir(out) if f.endswith(".md")]
    assert len(mds) == 3                              # incl. the OOM victim
    status = json.load(open(os.path.join(out, "_heal_status.json")))
    assert status["totals"]["remaining"] == 0
    # the ladder actually ran: one escalation attempt recorded for the victim
    assert len(status["attempts"]["escalations"]) == 1
    assert list(status["attempts"]["escalations"].values()) == [1]
    joined = "\n".join(status["events"]) + res.stderr
    assert "escalate" in joined                       # visible in the event log too


def test_supervisor_is_idempotent_when_nothing_to_heal(tmp_path):
    src, out, fake = _corpus(tmp_path, n=2)
    first = _run_supervisor(src, out, fake)
    assert first.returncode == 0
    again = _run_supervisor(src, out, fake, timeout=30)
    assert again.returncode == 0
    assert "nothing to heal" in again.stderr
