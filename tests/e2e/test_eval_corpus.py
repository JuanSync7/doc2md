"""
title: E2E — synthetic eval corpus is green
kind: tests
layer: backend
summary: Generate the synthetic corpus, run the office+text lanes (PDF lane opt-in), and require evals/run_eval.py to pass.
"""
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN_EVAL = os.path.join(REPO, "evals", "run_eval.py")


def _soffice_available():
    # type: () -> bool
    cand = os.environ.get("DOC2MD_LIBREOFFICE", "").strip()
    if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
        return True
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))


@pytest.mark.skipif(not _soffice_available(),
                    reason="LibreOffice not available (set DOC2MD_LIBREOFFICE "
                           "or put soffice on PATH)")
def test_eval_corpus_green(tmp_path):
    """The full eval must be green: corpus generation is deterministic, every
    office/text conversion hits its gates, and all content probes hold.

    The PDF lane (docling, minutes of model time) is opt-in: it runs only when
    DOC2MD_EVAL_PDF=1 AND DOC2MD_PDF_PYTHON is set; otherwise those
    expectations are SKIP rows and the eval must still exit 0."""
    cmd = [sys.executable, RUN_EVAL,
           "--corpus", str(tmp_path / "corpus"),
           "--bundles", str(tmp_path / "bundles"),
           "--text-out", str(tmp_path / "text")]
    if os.environ.get("DOC2MD_EVAL_PDF", "") != "1":
        cmd.append("--skip-pdf")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=1800)
    assert r.returncode == 0, "eval failed:\n%s\n%s" % (r.stdout, r.stderr)
    assert "0 fail" in r.stdout
