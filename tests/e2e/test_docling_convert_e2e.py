"""
title: E2E — docling_convert planning / --dry-run
kind: tests
layer: backend
summary: The converter's walk + id + skip logic runs without docling (dry-run); idempotent skip works.
"""
import os
import subprocess
import sys

import pytest

from backend.ingest import doc_id

pytestmark = pytest.mark.e2e

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONVERT = os.path.join(REPO, "scripts", "docling_convert.py")


def _run(src, out, *extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(REPO, "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, CONVERT, "--src", src, "--out", out, "--dry-run"] + list(extra),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def test_crash_recovery_blacklists_stuck_doc(tmp_path):
    # Simulate a prior process death mid-conversion: _converting.txt names a doc with
    # no .md. The next run must blacklist it (so it never loop-crashes) and exclude it.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.html").write_text("x", encoding="utf-8")
    (src / "b.html").write_text("y", encoding="utf-8")
    out = tmp_path / "md"
    out.mkdir()
    (out / "_converting.txt").write_text(doc_id("a.html"), encoding="utf-8")

    r = _run(str(src), str(out))
    assert r.returncode == 0, r.stderr
    assert "CONVERT a.html" not in r.stdout          # blacklisted -> not planned
    assert "CONVERT b.html" in r.stdout              # b still converts
    assert doc_id("a.html") in (out / "_crashed.txt").read_text(encoding="utf-8")
    assert not (out / "_converting.txt").exists()  # marker cleared


def test_dry_run_plans_all_and_writes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.html").write_text("x", encoding="utf-8")
    (src / "b.html").write_text("<p>y</p>", encoding="utf-8")
    (src / "ignore.bin").write_text("z", encoding="utf-8")  # unsupported -> not planned
    out = tmp_path / "md"
    out.mkdir()

    r = _run(str(src), str(out))
    assert r.returncode == 0, r.stderr
    assert "CONVERT a.html" in r.stdout
    assert "CONVERT b.html" in r.stdout
    assert "ignore.bin" not in r.stdout
    assert os.listdir(str(out)) == []  # dry-run wrote nothing


def test_trust_existing_md_skips_already_converted(tmp_path):
    # Legacy behaviour: --trust-existing-md skips any doc that already has a .md.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.html").write_text("x", encoding="utf-8")
    (src / "b.html").write_text("y", encoding="utf-8")
    out = tmp_path / "md"
    out.mkdir()
    (out / (doc_id("a.html") + ".md")).write_text("already", encoding="utf-8")

    r = _run(str(src), str(out), "--trust-existing-md")
    assert r.returncode == 0, r.stderr
    assert "CONVERT a.html" not in r.stdout   # existing md trusted -> skipped
    assert "CONVERT b.html" in r.stdout


def test_default_reconverts_md_without_valid_record(tmp_path):
    # New default: an existing .md with NO passing validation record is NOT skipped
    # (self-healing). A valid record makes it skip.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.html").write_text("x", encoding="utf-8")
    (src / "b.html").write_text("y", encoding="utf-8")
    out = tmp_path / "md"
    out.mkdir()
    (out / (doc_id("a.html") + ".md")).write_text("already", encoding="utf-8")

    # no coverage record yet -> a is re-converted (unvalidated)
    r = _run(str(src), str(out))
    assert r.returncode == 0, r.stderr
    assert "CONVERT a.html" in r.stdout

    # now bless a with a passing validation record -> a is skipped, b still converts
    import json
    (out / "_coverage.jsonl").write_text(
        json.dumps({"id": doc_id("a.html"), "rel": "a.html", "recall": 1.0,
                    "n_source": 100, "valid": True}) + "\n", encoding="utf-8")
    r2 = _run(str(src), str(out))
    assert r2.returncode == 0, r2.stderr
    assert "CONVERT a.html" not in r2.stdout
    assert "CONVERT b.html" in r2.stdout
