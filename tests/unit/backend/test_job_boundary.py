"""
title: Unit — the two jobs stay separable in code
kind: tests
layer: backend
summary: Job 1 (a document that loses nothing) must not depend on Job 2 (an index whose every link is walkable), so the two can be worked on in parallel and neither can move the other's numbers.
"""
# `end-goal.md` names two jobs with two different standards of success:
#
#   Job 1  the document   nothing may be LOST      token_recall == 1.0, hard
#   Job 2  the knowledge   nothing may be CLAIMED
#                          that cannot be followed  cited records, joined edges
#
# They already live in different packages. What was missing is anything that KEEPS
# them there: the separation was a fact about today's imports, not a contract, and
# the first shortcut ("the enricher already computed that, just read it here") would
# have coupled a hard gate to an index.
#
# The direction matters more than the distance. `kb` reading `ingest`'s measurement
# primitives is fine and intended; the reverse would mean a conversion's verdict
# could change because a vocabulary changed — and the vocabulary is a DEPLOYMENT's
# input, so a document that converted cleanly yesterday could fail today for a
# reason that has nothing to do with the document.
import ast
import os

import pytest

pytestmark = pytest.mark.unit

SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "src")

# Job 1: the run path and the gates. Job 2 is `backend.kb`.
JOB1 = ("ingest", "validate")
JOB2 = "kb"


def _imports(pkg):
    """Every module path imported anywhere in `src/backend/<pkg>`, absolute or not."""
    root = os.path.join(SRC, "backend", pkg)
    out = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(root, name)
        with open(path) as fh:
            tree = ast.parse(fh.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    out.setdefault(alias.name, []).append(name)
            elif isinstance(node, ast.ImportFrom):
                # `level` covers the relative form: `from . import x` inside
                # backend/<pkg> resolves to backend.<pkg>, never to a sibling
                # package, so only absolute imports can cross this boundary.
                if not node.level:
                    out.setdefault(node.module or "", []).append(name)
    return out


@pytest.mark.parametrize("pkg", JOB1)
def test_job_one_never_depends_on_job_two(pkg):
    """The contract that lets the two jobs proceed in parallel.

    A gate that imported the knowledge layer would make a LOSSLESSNESS verdict a
    function of a controlled vocabulary — and that vocabulary is the deployment's
    input, tuned per domain. A document that converted cleanly would then be able
    to fail because somebody promoted a term."""
    offenders = dict((mod, where) for mod, where in _imports(pkg).items()
                     if mod.split(".")[:2] == ["backend", JOB2])
    assert not offenders, (
        "backend.%s imports the knowledge layer: %s — Job 1's verdict must not "
        "depend on Job 2's vocabulary" % (pkg, offenders))


def test_job_two_may_read_job_ones_primitives_and_does():
    """The permitted direction, asserted rather than assumed — if this ever stops
    being true the dependency has been inverted or duplicated, and a second
    tokenizer is exactly the defect the shared one exists to prevent."""
    imported = _imports(JOB2)
    assert any(m == "backend.ingest" or m.startswith("backend.ingest.")
               for m in imported), sorted(imported)


def test_job_two_never_reaches_into_the_validator():
    """`validate` is Job 1's judge. The knowledge layer has its own judge
    (`kb_lint`) with its own standard, and a shared one would apply a hard
    losslessness gate to an index that is SUPPOSED to be smaller than the source."""
    offenders = dict((mod, where) for mod, where in _imports(JOB2).items()
                     if mod.split(".")[:2] == ["backend", "validate"])
    assert not offenders, offenders


@pytest.mark.parametrize("pkg", JOB1 + (JOB2,))
def test_neither_job_crosses_a_package_private_boundary(pkg):
    """CONVENTIONS: a package's public symbols come from its `__init__`, never from
    a `_*` submodule. Stated here too because a private import is how a boundary
    stops being a boundary without anyone editing a rule."""
    bad = dict((mod, where) for mod, where in _imports(pkg).items()
               if mod.startswith("backend.") and "._" in mod)
    assert not bad, bad
