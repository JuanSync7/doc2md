"""
title: Integration — work-stealing claims, fallback bodies, supervisor helpers
kind: tests
layer: backend
summary: The claim protocol is exclusive/reclaimable/stale-releasable; --fallback-only writes a validator-gated body; the supervisor's pure helpers decide correctly.
"""
# Integration: touches disk (tmp corpora, claim files) and loads the 3.12 scripts.
import importlib.util
import json
import os

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name):
    path = os.path.join(REPO, "scripts", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- claim protocol ----------------------------------------------------------

def test_claim_is_exclusive_and_reclaim_never_steals_live_owner(tmp_path):
    dc = _load("docling_convert")
    out = str(tmp_path)
    assert dc._try_claim(out, "doc1", "w0") is True
    assert dc._try_claim(out, "doc1", "w1") is False          # second claimant loses
    assert dc._try_claim(out, "doc2", "w1") is True           # other docs unaffected
    # doc1's owner (this pid) is ALIVE -> reclaim must refuse (stealing a live
    # worker's claim would put two converters on one doc)
    assert dc._try_claim(out, "doc1", "w9", reclaim=True) is False
    # a DEAD owner's claim is reclaimable (escalation of a dead worker's victim)
    with open(dc._claim_path(out, "doc1"), "w") as f:
        f.write("w0 999999999 0 %s\n" % dc._HOSTNAME)
    assert dc._try_claim(out, "doc1", "w9", reclaim=True) is True
    body = open(dc._claim_path(out, "doc1")).read().split()
    assert body[0] == "w9"                                     # owner recorded
    assert body[1] == str(os.getpid())                         # pid recorded (stale check)
    assert body[3] == dc._HOSTNAME                             # host recorded (NFS safety)
    # a foreign-host claim is conservatively treated as live -> never stolen
    with open(dc._claim_path(out, "doc3"), "w") as f:
        f.write("wx 1 0 some-other-host\n")
    assert dc._try_claim(out, "doc3", "w9", reclaim=True) is False


def test_release_stale_claims_only_frees_dead_invalid_ones(tmp_path):
    dc = _load("docling_convert")
    hs = _load("heal_supervisor")
    out = str(tmp_path)
    # live claim (this pid), dead claim (bogus pid), done claim (valid doc, dead pid)
    dc._try_claim(out, "live", "w0")
    dc._try_claim(out, "dead", "w1")
    dc._try_claim(out, "done", "w2")
    dead = dc._claim_path(out, "dead")
    donep = dc._claim_path(out, "done")
    open(dead, "w").write("w1 999999999 0\n")
    open(donep, "w").write("w2 999999999 0\n")
    released = hs._release_stale_claims(out, done_ids={"done"})
    assert released == ["dead"]                     # dead+invalid -> released
    assert os.path.exists(dc._claim_path(out, "live"))   # live claim kept
    assert os.path.exists(donep)                    # finished doc's claim kept


# --- fallback body + --fallback-only ------------------------------------------

def test_fallback_body_covers_the_formats(tmp_path):
    dc = _load("docling_convert")
    md = tmp_path / "spec.md"
    md.write_text("# Spec\n\nbody text here\n")
    assert dc._fallback_body(str(md)) == "# Spec\n\nbody text here\n"
    html = tmp_path / "r.html"
    html.write_text("<style>.x{color:red}</style><p>visible words</p>")
    out = dc._fallback_body(str(html))
    assert "visible words" in out and "color" not in out
    # office has no docling fallback — it is owned by the OOXML lane -> '' here
    assert dc._fallback_body(str(tmp_path / "d.pptx")) == ""
    # unknown/unreadable -> '' (caller blacklists), never raises
    assert dc._fallback_body(str(tmp_path / "mystery.bin")) == ""


def test_fallback_only_writes_validator_gated_md(tmp_path):
    dc = _load("docling_convert")
    src = tmp_path / "corpus"
    out = tmp_path / "md"
    src.mkdir()
    out.mkdir()
    # html has an independent extractor (html_to_text) but is NOT a verbatim passthrough,
    # so the fallback body is recorded as a real FALLBACK (md would be PASSTHROUGH).
    (src / "notes.html").write_text("<p>alpha beta gamma content words</p>")
    _sources, _ = dc.find_sources(str(src))
    rows = dc.plan(_sources, str(out), False)
    did = rows[0]["id"]
    rc = dc.main(["--src", str(src), "--out", str(out), "--only", did, "--fallback-only"])
    assert rc == 0
    body = open(str(out / (did + ".md"))).read()
    assert "alpha beta gamma content words" in body
    recs = [json.loads(line) for line in open(str(out / "_coverage.w0.jsonl"))]
    assert recs[-1]["id"] == did
    assert recs[-1]["docling_status"] == "FALLBACK"
    assert recs[-1]["valid"] is True                 # measured like any conversion
    # a claim now exists and a plain re-run without --reclaim skips it
    assert os.path.exists(dc._claim_path(str(out), did))


def test_fallback_only_unextractable_doc_fails_cleanly(tmp_path):
    dc = _load("docling_convert")
    src = tmp_path / "corpus"
    out = tmp_path / "md"
    src.mkdir()
    out.mkdir()
    (src / "empty.html").write_text("")               # nothing to extract (docling-lane, empty)
    _sources, _ = dc.find_sources(str(src))
    rows = dc.plan(_sources, str(out), False)
    did = rows[0]["id"]
    rc = dc.main(["--src", str(src), "--out", str(out), "--only", did, "--fallback-only"])
    assert rc == 1                                    # signalled: nothing extractable
    assert not os.path.exists(str(out / (did + ".md")))


# --- supervisor pure helpers ----------------------------------------------------

def test_watchdog_verdict_matrix():
    hs = _load("heal_supervisor")
    assert hs._watchdog_verdict(10, 0, stall_secs=600, busy_min=50) == "ok"
    assert hs._watchdog_verdict(700, 500, stall_secs=600, busy_min=50) == "spare"
    assert hs._watchdog_verdict(700, 3, stall_secs=600, busy_min=50) == "kill"


def test_log_tail_reads_last_bytes(tmp_path):
    hs = _load("heal_supervisor")
    p = tmp_path / "w.log"
    p.write_text("x" * 10000 + "\nTHE END MARKER")
    tail = hs._log_tail(str(p), nbytes=64)
    assert "THE END MARKER" in tail
    assert len(tail) <= 64
    assert hs._log_tail(str(tmp_path / "missing.log")) == ""
