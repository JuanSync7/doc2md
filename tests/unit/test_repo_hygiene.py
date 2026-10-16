"""
title: Unit — the repo is a publishable repo
kind: tests
layer: cross-cutting
summary: Licence, packaging metadata and the top-level files a public repository needs — checked, because each of these is invisible until someone else tries to use it.
"""
# A goal audit found `LICENSE` containing the literal text
# "TODO: choose a license (MIT/Apache-2.0/proprietary) and paste its text here."
# on a PUBLIC GitHub remote, with `pyproject.toml` declaring
# `license = { file = "LICENSE" }` — so `pip install doc2md` would have shipped that
# sentence as the licence.
#
# Nothing catches this class of defect. It is not a test failure, not a lint error,
# and not visible to anyone working inside the repo: it only shows up when a
# stranger tries to use the thing. So it is checked here.
import os
import re

import pytest

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Words that mean "nobody finished this". A licence file is the one place where a
# placeholder is worse than an empty file, because it LOOKS like an answer.
PLACEHOLDER = re.compile(r"\b(TODO|FIXME|TBD|XXX|choose a licen[cs]e|paste)\b", re.I)


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def test_the_licence_is_a_real_licence():
    """Not a placeholder, not empty, and long enough to be licence text rather than
    a name. `pyproject.toml` ships this file's CONTENT as the package licence."""
    text = _read("LICENSE")
    assert not PLACEHOLDER.search(text), (
        "LICENSE still reads as unfinished: %r" % PLACEHOLDER.search(text).group(0))
    assert len(text.strip()) > 400, "too short to be licence text"


def test_the_licence_names_a_copyright_holder_and_a_year():
    """An MIT licence with no holder grants nothing from nobody."""
    text = _read("LICENSE")
    m = re.search(r"Copyright \(c\) (\d{4})\s+(\S.*)", text)
    assert m, "no 'Copyright (c) YEAR Holder' line"
    year, holder = int(m.group(1)), m.group(2).strip()
    assert 2020 <= year <= 2100, year
    assert holder and not PLACEHOLDER.search(holder), holder


def test_the_packaging_declares_the_same_licence_the_file_grants():
    """Two places state the licence and they must not disagree — a wheel whose
    metadata says MIT over a file that says something else is worse than either."""
    text = _read("LICENSE")
    pyproject = _read("pyproject.toml")
    name = text.strip().splitlines()[0].strip()          # e.g. "MIT License"
    short = name.split()[0]                              # "MIT"
    assert 'license = { file = "LICENSE" }' in pyproject, (
        "pyproject must ship the LICENSE file, not a duplicated string")
    assert ("License :: OSI Approved :: %s License" % short) in pyproject, (
        "classifiers do not name %s, but LICENSE does" % short)


def test_the_packaging_names_an_author():
    """`pip show doc2md` with no author is a package nobody can be asked about."""
    pyproject = _read("pyproject.toml")
    assert re.search(r"^authors\s*=\s*\[", pyproject, re.M), "no authors in pyproject"


@pytest.mark.parametrize("rel", ["README.md", "LICENSE", "CONVENTIONS.md",
                                 "CONTRIBUTING.md", ".gitignore", "Makefile",
                                 "pyproject.toml"])
def test_the_files_a_stranger_looks_for_exist(rel):
    """The top-level set a newcomer checks before deciding whether to use or
    contribute. Each is cheap; the cost of a missing one is paid by someone who
    never tells you."""
    path = os.path.join(REPO, rel)
    assert os.path.isfile(path), "%s is missing" % rel
    assert os.path.getsize(path) > 0, "%s is empty" % rel


def test_no_top_level_document_still_reads_as_unfinished():
    """A TODO inside a design doc is a plan. A TODO in the files a stranger reads
    first is an unanswered question about whether this project is usable."""
    for rel in ("README.md", "LICENSE", "CONTRIBUTING.md"):
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            continue
        for n, line in enumerate(_read(rel).splitlines(), 1):
            assert not re.match(r"\s*(TODO|FIXME|TBD)\b", line, re.I), (
                "%s:%d still reads as unfinished: %s" % (rel, n, line.strip()))
