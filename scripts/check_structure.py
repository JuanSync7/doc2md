#!/usr/bin/env python3
"""
title: Structure checker — the enforcement CONVENTIONS.md §6 promises
kind: script
layer: cross-cutting
public_api: no
summary: Fails the build when labelling, directory taxonomy or package boundaries drift from CONVENTIONS.md.
"""
# CONVENTIONS.md §6 opens "Enforcement (this is checked, not just documented)" and
# describes this file as running "via make check, in CI, and as a pre-commit hook".
# It did not exist. Neither did the Makefile, the hook, or the ruff/mypy that
# pyproject.toml declares. A document that claims a gate it does not have is worse
# than one that admits the gap, because a reader stops looking.
#
# So this is the claim made true rather than softened. Four rules, all statically
# checkable, all of them things this repo has actually got wrong before:
#
#   frontmatter      every README.md / CLAUDE.md / docs md carries the required keys
#                    with a valid kind, layer and status
#   documented-dir   every taxonomy directory that exists has BOTH labels
#   package-boundary every src/ package has an __init__.py that declares __all__
#   private-import   no absolute import of another package's _private module
#
# Deliberately stdlib-only and 3.6-safe: it runs on the bare ring, before anything
# is installed, which is the only place a structure check is worth having.
from __future__ import print_function

import argparse
import ast
import os
import re
import sys
from collections import namedtuple

# Kept in step with CONVENTIONS.md §1 BY HAND, and the file says so: "If you change
# the scheme (KINDS / LAYERS / STATUSES), update both this file and the constants at
# the top of scripts/check_structure.py."
KINDS = frozenset((
    "readme", "rules", "package", "module", "tests", "test-doc", "doc", "spec",
    "design", "adr", "config", "script", "agent", "mcp", "api", "wiki", "demo",
    "model", "eval", "container", "ops"))
LAYERS = frozenset(("frontend", "backend", "shared", "app", "cross-cutting", "n/a"))
STATUSES = frozenset(("draft", "stable", "deprecated", "template",
                      "proposed", "accepted", "superseded"))

REQUIRED = ("title", "kind", "layer", "status", "summary")
# CONVENTIONS.md: "Missing `owner` is a warning, not a failure." A repo full of TBD
# owners should still be able to merge.
ADVISORY = ("owner",)

# Directories that carry the labelling rule. Everything else is left alone: policing
# every directory would fail on .venv, __pycache__, and whatever a tool drops next.
TAXONOMY_ROOTS = ("src", "scripts", "tests", "docs", "config", "evals")

# Never walked. `data/` is generated and gitignored; the rest are caches and venvs.
SKIP_DIRS = frozenset((
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "data", "vendor", ".make", ".github"))

# Build output, matched by suffix rather than by name: `pip install -e .` drops
# `src/<name>.egg-info`, which is generated and gitignored and carries no decisions.
SKIP_SUFFIXES = (".egg-info", ".dist-info")

Problem = namedtuple("Problem", ["rule", "severity", "path", "detail"])

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*?\r?\n", re.S)
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def _frontmatter(text):
    # type: (str) -> dict
    """The leading YAML block as a flat {key: value}, or {} when there is none.

    Deliberately NOT a YAML parser: this must run before anything is installed, and
    PyYAML is not on the bare ring. Only top-level scalar keys matter here.
    """
    m = _FM.match(text or "")
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if line[:1] in (" ", "\t", "#") or not line.strip():
            continue                       # nested value or comment: not a top key
        km = _KEY.match(line)
        if km:
            out[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    return out


def _check_frontmatter(path, rel, problems):
    try:
        with open(path) as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(Problem("unreadable", "error", rel, "%s" % exc))
        return
    fm = _frontmatter(text)
    if not fm:
        problems.append(Problem(
            "frontmatter", "error", rel,
            "no YAML frontmatter block — CONVENTIONS.md section 1 requires one on "
            "every README.md, CLAUDE.md and docs/ markdown file"))
        return
    for key in REQUIRED:
        if not fm.get(key):
            problems.append(Problem("frontmatter", "error", rel,
                                    "missing required key %r" % key))
    for key in ADVISORY:
        if not fm.get(key):
            problems.append(Problem("frontmatter", "warn", rel,
                                    "missing key %r (advisory)" % key))
    for key, allowed in (("kind", KINDS), ("layer", LAYERS), ("status", STATUSES)):
        val = fm.get(key)
        if val and val not in allowed:
            problems.append(Problem(
                "frontmatter", "error", rel,
                "%s: %r is not one of %s" % (key, val, " ".join(sorted(allowed)))))


def _is_taxonomy_dir(rel):
    # type: (str) -> bool
    head = rel.replace(os.sep, "/").split("/")[0]
    return head in TAXONOMY_ROOTS


def _check_package(dirpath, rel, names, problems):
    """CONVENTIONS section 3: a src/ package declares its API in __init__.__all__."""
    if not rel.replace(os.sep, "/").startswith("src/"):
        return
    pys = [n for n in names if n.endswith(".py")]
    if not pys:
        return
    if "__init__.py" not in pys:
        problems.append(Problem(
            "package-boundary", "error", rel,
            "%d .py file(s) but no __init__.py — a directory of modules with no "
            "package boundary has no public API to respect" % len(pys)))
        return
    # A directory whose ONLY .py is an empty __init__ is a namespace package;
    # requiring __all__ there is noise.
    if pys == ["__init__.py"]:
        try:
            with open(os.path.join(dirpath, "__init__.py")) as fh:
                if not fh.read().strip():
                    return
        except OSError:
            pass
    init = os.path.join(dirpath, "__init__.py")
    try:
        with open(init) as fh:
            tree = ast.parse(fh.read(), init)
    except (OSError, SyntaxError) as exc:
        problems.append(Problem("unparseable", "error",
                                rel + "/__init__.py", "%s" % exc))
        return
    has_all = any(isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "__all__" for t in n.targets)
                  for n in tree.body)
    if not has_all:
        problems.append(Problem(
            "package-boundary", "error", rel + "/__init__.py",
            "no __all__ — it is the machine-checkable public API (CONVENTIONS "
            "section 3), and without it the boundary is a convention nobody can verify"))


def _check_private_imports(path, rel, problems):
    """No absolute import of ANOTHER package's private module.

    The rule is about crossing a package boundary, not about underscores: inside a
    package, `_private` modules are the implementation and a relative import of one
    is exactly right. `from backend.kb._enrich import x` is not.

    SEVERITY DEPENDS ON WHO IS IMPORTING. In `src/` and `scripts/` this is an error:
    the root CLAUDE.md rule is "never reach into another package's internals to save
    an import". In `tests/` it is a WARNING, because `tests/CLAUDE.md` names a
    documented exception — the ast-based boundary tests have to read the private
    modules they police, and a checker that forbade that would forbid the tests that
    enforce the very same rule. Reported rather than ignored, so the exception stays
    visible and countable instead of becoming the norm.
    """
    try:
        with open(path) as fh:
            tree = ast.parse(fh.read(), path)
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(Problem("unreadable", "error", rel, "%s" % exc))
        return
    except SyntaxError as exc:
        problems.append(Problem("unparseable", "error", rel, "%s" % exc))
        return
    is_test = rel.replace(os.sep, "/").startswith("tests/")
    severity = "warn" if is_test else "error"
    seen = set()
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            mods = [node.module or ""]
        for mod in mods:
            parts = mod.split(".")
            if len(parts) > 1 and any(p.startswith("_") for p in parts[1:]):
                if mod in seen:
                    continue          # one finding per file per module, not per line
                seen.add(mod)
                problems.append(Problem(
                    "private-import", severity, rel,
                    "absolute import of a private module: %r — import the package's "
                    "public symbol from its __init__ instead%s"
                    % (mod, " (tests/CLAUDE.md permits this for the boundary tests; "
                            "advisory here)" if is_test else "")))


def check_tree(root):
    # type: (str) -> list
    """Every problem in the tree, worst first. An empty list means clean."""
    problems = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith(".")
                             and not d.endswith(SKIP_SUFFIXES))
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            rel = ""
        names = sorted(filenames)
        if rel and _is_taxonomy_dir(rel):
            for label in ("README.md", "CLAUDE.md"):
                if label not in names:
                    problems.append(Problem(
                        "documented-dir", "error", rel,
                        "missing %s — a taxonomy directory is not done until it "
                        "carries both labels (CONVENTIONS section 2)" % label))
        for name in names:
            path = os.path.join(dirpath, name)
            sub = os.path.join(rel, name) if rel else name
            if name in ("README.md", "CLAUDE.md") and (not rel or _is_taxonomy_dir(rel)):
                _check_frontmatter(path, sub, problems)
            elif name.endswith(".md") and rel.replace(os.sep, "/").startswith("docs"):
                _check_frontmatter(path, sub, problems)
            elif name.endswith(".py"):
                _check_private_imports(path, sub, problems)
        if rel:
            _check_package(dirpath, rel, names, problems)
    order = {"error": 0, "warn": 1}
    return sorted(problems, key=lambda p: (order.get(p.severity, 9), p.rule, p.path))


def exit_code(problems):
    # type: (list) -> int
    """Errors fail the build; warnings are printed and do not."""
    return 1 if any(p.severity == "error" for p in problems) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check the repo against CONVENTIONS.md (labelling, taxonomy, "
                    "package boundaries). Errors fail; warnings are advisory.")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), help="tree to check (default: the repo root)")
    ap.add_argument("--quiet", action="store_true",
                    help="summary only; suppress the per-problem list")
    args = ap.parse_args(argv)

    problems = check_tree(args.root)
    n_err = sum(1 for p in problems if p.severity == "error")
    n_warn = len(problems) - n_err
    if not args.quiet:
        for p in problems:
            print("  %-5s %-16s %s: %s" % (p.severity.upper(), p.rule, p.path,
                                           p.detail))
    print("check_structure: %d error(s), %d warning(s) -> %s"
          % (n_err, n_warn, args.root))
    return exit_code(problems)


if __name__ == "__main__":
    sys.exit(main())
