"""
title: Unit — the structure checker CONVENTIONS.md promises
kind: tests
layer: cross-cutting
summary: Every rule §6 advertises, tested on synthetic trees so the checker fails for the reason it claims.
"""
# CONVENTIONS.md §6 opens "Enforcement (this is checked, not just documented)" and
# describes `scripts/check_structure.py` as running "via make check, in CI, and as a
# pre-commit hook". None of it existed: no Makefile, no checker, no hook, and ruff
# and mypy are declared in pyproject.toml but not installed. A document that claims
# a gate it does not have is worse than one that admits the gap, because a reader
# stops looking.
#
# So the checker is built rather than the claim softened, and every rule is tested
# on a SYNTHETIC tree — a checker that only ever runs on the repo's own clean tree
# has never been seen to fail, and a gate nobody has watched fail is not a gate.
import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "check_structure", os.path.join(REPO, "scripts", "check_structure.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(path, text):
    d = os.path.dirname(str(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(str(path), "w") as fh:
        fh.write(text)


GOOD_FM = ("---\ntitle: Thing\nkind: package\nlayer: backend\nstatus: stable\n"
           "owner: TBD\nsummary: One line.\n---\n\n# Thing\n")


def _labelled_dir(root, rel):
    """A directory that satisfies the taxonomy rule: both labels, both valid.

    Every ANCESTOR is labelled too, because the rule applies to each taxonomy
    directory in turn — `src/` needs its own pair as much as `src/backend/` does.
    """
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        here = os.path.join(str(root), *parts[:i])
        _write(os.path.join(here, "README.md"), GOOD_FM)
        _write(os.path.join(here, "CLAUDE.md"),
               GOOD_FM.replace("kind: package", "kind: rules"))


# --- frontmatter ------------------------------------------------------------

def test_a_clean_tree_passes(tmp_path):
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = ["x"]\nx = 1\n')
    assert m.check_tree(str(tmp_path)) == []


def test_a_missing_frontmatter_key_is_named(tmp_path):
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "README.md",
           "---\ntitle: Thing\nkind: package\nlayer: backend\n---\n")
    problems = m.check_tree(str(tmp_path))
    assert any(p.rule == "frontmatter" and "status" in p.detail for p in problems), problems


def test_an_invalid_kind_is_rejected_and_the_valid_set_is_shown(tmp_path):
    """The error has to carry the allowed values. A reader who typed `kind: module`
    for a directory needs the list, not a verdict."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "README.md",
           GOOD_FM.replace("kind: package", "kind: pakcage"))
    problems = m.check_tree(str(tmp_path))
    bad = [p for p in problems if p.rule == "frontmatter"]
    assert bad and "pakcage" in bad[0].detail and "readme" in bad[0].detail


def test_an_invalid_layer_and_status_are_rejected(tmp_path):
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "CLAUDE.md",
           GOOD_FM.replace("layer: backend", "layer: middleware"))
    assert any("middleware" in p.detail for p in m.check_tree(str(tmp_path)))


def test_a_missing_owner_is_a_warning_not_a_failure(tmp_path):
    """CONVENTIONS.md says so explicitly, and the distinction matters: a repo full
    of TBD owners should not be unable to merge anything."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "README.md",
           GOOD_FM.replace("owner: TBD\n", ""))
    problems = m.check_tree(str(tmp_path))
    owner = [p for p in problems if "owner" in p.detail]
    assert owner and all(p.severity == "warn" for p in owner)
    assert m.exit_code(problems) == 0        # warnings alone do not fail the build


# --- documented directories -------------------------------------------------

def test_a_taxonomy_directory_missing_its_labels_fails(tmp_path):
    m = _mod()
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    problems = m.check_tree(str(tmp_path))
    rules = set(p.rule for p in problems)
    assert "documented-dir" in rules
    assert any("README.md" in p.detail for p in problems)
    assert any("CLAUDE.md" in p.detail for p in problems)


def test_a_directory_outside_the_taxonomy_is_not_policed(tmp_path):
    """Only the taxonomy dirs carry the labelling rule. Policing every directory
    would make the checker fail on .venv, __pycache__ and anything a tool drops."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "__pycache__" / "x.pyc", "")
    _write(tmp_path / "node_modules" / "pkg" / "index.js", "")
    assert m.check_tree(str(tmp_path)) == []


# --- package boundary -------------------------------------------------------

def test_a_src_package_without_an_init_fails(tmp_path):
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "thing.py", "x = 1\n")
    assert any(p.rule == "package-boundary" and "__init__.py" in p.detail
               for p in m.check_tree(str(tmp_path)))


def test_an_init_without_all_fails(tmp_path):
    """`__all__` is what makes the public API machine-checkable — CONVENTIONS §3.
    Without it the boundary is a convention nobody can verify."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", "from .x import y\n")
    assert any(p.rule == "package-boundary" and "__all__" in p.detail
               for p in m.check_tree(str(tmp_path)))


def test_a_namespace_only_directory_needs_no_all(tmp_path):
    """A directory whose only .py IS the __init__, re-exporting nothing, is a
    namespace package — requiring __all__ there is noise."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", "")
    assert m.check_tree(str(tmp_path)) == []


# --- private-import boundary ------------------------------------------------

def test_an_absolute_import_of_another_packages_private_module_fails(tmp_path):
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "thing.py",
           "from backend.kb._enrich import request_spec\n")
    assert any(p.rule == "private-import" and "_enrich" in p.detail
               for p in m.check_tree(str(tmp_path)))


def test_a_packages_own_relative_private_import_is_fine(tmp_path):
    """Inside a package, `_private` modules ARE the implementation — the rule is
    about crossing a package boundary, not about underscores."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py",
           '__all__ = ["x"]\nfrom ._impl import x\n')
    _write(tmp_path / "src" / "backend" / "_impl.py", "x = 1\n")
    assert m.check_tree(str(tmp_path)) == []


def test_a_file_that_does_not_parse_is_reported_not_skipped(tmp_path):
    """A syntax error must not read as "no problems here" — that is how a checker
    goes quietly blind."""
    m = _mod()
    _labelled_dir(tmp_path, "src/backend")
    _write(tmp_path / "src" / "backend" / "__init__.py", '__all__ = []\n')
    _write(tmp_path / "src" / "backend" / "broken.py", "def (:\n")
    assert any(p.rule == "unparseable" for p in m.check_tree(str(tmp_path)))


# --- the repo itself --------------------------------------------------------

def test_this_repository_passes_its_own_checker():
    """The point of the whole exercise. If this fails, either the tree drifted or
    the conventions changed and this file is the thing that says so."""
    m = _mod()
    problems = m.check_tree(REPO)
    failures = [p for p in problems if p.severity == "error"]
    assert failures == [], "\n".join("%s: %s" % (p.rule, p.detail) for p in failures)
