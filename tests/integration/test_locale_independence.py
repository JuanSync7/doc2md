"""
title: Integration — the generated-docs lane does not depend on the host locale
kind: tests
layer: backend
summary: render_vocab_doc reads config/vocab.yaml and writes docs/reference/vocabulary.md as UTF-8 whatever LC_ALL says.
"""
# Integration: launches a real interpreter in a hostile locale.
#
# CLAUDE.md's premise is a "bare 3.6 host". A bare 3.6 host is exactly where a
# missing ``encoding=`` bites: ``open()`` with no encoding decodes with the LOCALE's
# preferred encoding, which under LC_ALL=C is ANSI_X3.4-1968, and both
# config/vocab.yaml and the reference it generates hold em dashes (vocab.yaml's
# first is at byte 9). The failure is loud — UnicodeDecodeError — so it never grades
# a damaged document as good; it just makes the drift check impossible to run at all
# on the host the project says it must run on. CI is safe only by accident
# (python:3.6-slim bakes in LANG=C.UTF-8) and this developer host only because RHEL8
# backports PEP 538 locale coercion, which an explicit LC_ALL=C defeats.
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "render_vocab_doc.py")
VOCAB_DOC = os.path.join(REPO, "docs", "reference", "vocabulary.md")

# LC_ALL/LANG force the C locale; PYTHONUTF8=0 defeats 3.7+'s UTF-8 mode and
# PYTHONCOERCECLOCALE=0 defeats PEP 538 coercion, so the child really does get an
# ASCII preferred encoding on every interpreter this suite runs on.
_C_LOCALE = {"LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C",
             "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}


def _env():
    env = dict(os.environ)
    env.update(_C_LOCALE)
    return env


def _run(args):
    proc = subprocess.Popen([sys.executable] + args, cwd=REPO, env=_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    return (proc.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


@pytest.fixture(scope="module")
def ascii_locale():
    """Skip honestly rather than pass vacuously if the locale cannot be forced."""
    rc, out, _err = _run(["-c", "import locale;print(locale.getpreferredencoding(False))"])
    enc = out.strip().lower()
    if rc != 0 or "utf" in enc:
        pytest.skip("this interpreter keeps a UTF-8 preferred encoding under "
                    "LC_ALL=C (%r): the defect cannot be reproduced here" % out.strip())
    return enc


def test_the_vocabulary_drift_check_runs_under_a_non_utf8_locale(ascii_locale):
    """``--check`` is what the drift test calls. Under LC_ALL=C it used to die with
    UnicodeDecodeError on config/vocab.yaml instead of printing ok/STALE, so the
    check could not report either verdict."""
    rc, out, err = _run([SCRIPT, "--check"])
    assert "UnicodeDecodeError" not in err, err
    assert rc == 0, err
    assert "is current" in out


def test_the_vocabulary_reference_renders_byte_identically_under_a_non_utf8_locale(
        ascii_locale, tmp_path):
    """The WRITE path is the same bug one direction over: encoding the em dashes into
    an ASCII stream raises UnicodeEncodeError. What it must produce is the committed
    file, byte for byte — the generated doc cannot be locale-dependent or `--check`
    would report STALE over a file that is current."""
    out_path = str(tmp_path / "vocabulary.md")
    rc, _out, err = _run([SCRIPT, "--out", out_path])
    assert "UnicodeEncodeError" not in err, err
    assert rc == 0, err
    with open(out_path, "rb") as fh:
        rendered = fh.read()
    with open(VOCAB_DOC, "rb") as fh:
        committed = fh.read()
    assert rendered == committed
