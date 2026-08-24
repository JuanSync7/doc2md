"""
title: Code and host identity
layer: backend
public_api: yes
summary: Which converter, from which commit, on which interpreter — read from the checkout, never asserted.
"""
# A pipeline whose whole pitch is MEASURED losslessness must be able to say which
# instrument produced any given number. Before this, `converter` was a frozen string
# literal: every bundle ever built by every version of the converter carried the
# identical stamp, so the first real converter bug left nobody able to tell which
# bundles came from the broken code.
#
# Everything here is read from the checkout. The git commit comes from plain FILE
# reads (.git/HEAD -> the ref -> packed-refs), so it works with no git binary, in a
# worktree, and on a detached HEAD. Only `dirty` needs to ask git itself, and that
# answer is OPTIONAL: an unknown dirty flag is omitted, never guessed false, because
# "clean" is exactly the claim a reader would rely on.
import os
import platform
import re
import sys

from collections import OrderedDict

__all__ = ["code_identity", "host_identity", "git_commit", "package_version"]

_VERSION = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']', re.M)
_PROJECT = re.compile(r'^\[project\][ \t]*$', re.M)


def package_version(root):
    # type: (str) -> str
    """The distribution version from ``pyproject.toml``'s ``[project]`` table, or ``""``.

    Hand-parsed: this module has to import on the bare 3.6 host, which has no
    ``tomllib`` and no third-party TOML reader (same reason ``_config.py`` hand-parses).

    The table is LOCATED, not assumed to be first. The previous guard tested
    ``text.startswith("[project]")``, which is false for every pyproject that opens
    with a comment or ``[build-system]`` — including this repo's — so it never fired
    and the first ``version =`` anywhere in the file won. A ``[tool.commitizen]`` or
    ``[tool.poetry]`` table above ``[project]`` would have stamped ITS version into
    every bundle's ``run.code.version`` and into the ``converter`` string the rubric
    grades, which is exactly what "a dependency pin must never win" forbids.

    A ``[project]`` table that states no version (PEP 621 ``dynamic``) yields ``""``,
    not some other table's number: the whole-file fallback is for a project that has
    no ``[project]`` table at all (a poetry layout), where the only version there is
    is the one in ``[tool.poetry]``."""
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return ""
    m = _PROJECT.search(text)
    if m:
        table = text[m.end():].split("\n[", 1)[0]
        found = _VERSION.search(table)
        return found.group(1) if found else ""
    found = _VERSION.search(text)
    return found.group(1) if found else ""


def _resolve_ref(git_dir, ref):
    # type: (str, str) -> str
    """A ref name -> its sha, via the loose ref file then packed-refs."""
    loose = os.path.join(git_dir, ref)
    try:
        with open(loose, encoding="utf-8") as fh:
            return fh.read().strip()
    except (IOError, OSError):
        pass
    try:
        with open(os.path.join(git_dir, "packed-refs"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return sha.strip()
    except (IOError, OSError):
        pass
    return ""


def git_commit(root):
    # type: (str) -> str
    """The checked-out commit sha, or ``""`` when this is not a git checkout.

    Handles a worktree (``.git`` is a file pointing at the real gitdir) and a
    detached HEAD (``HEAD`` holds the sha directly)."""
    git_dir = os.path.join(root, ".git")
    if os.path.isfile(git_dir):                      # a linked worktree
        try:
            with open(git_dir, encoding="utf-8") as fh:
                line = fh.read().strip()
        except (IOError, OSError):
            return ""
        if not line.startswith("gitdir:"):
            return ""
        git_dir = line.split(":", 1)[1].strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(root, git_dir)
    if not os.path.isdir(git_dir):
        return ""
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except (IOError, OSError):
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        # A linked worktree keeps its refs in the COMMON dir, one level up.
        sha = _resolve_ref(git_dir, ref)
        if not sha and os.path.basename(os.path.dirname(git_dir)) == "worktrees":
            sha = _resolve_ref(os.path.dirname(os.path.dirname(git_dir)), ref)
        return sha
    return head if re.match(r"^[0-9a-f]{7,40}$", head) else ""


def code_identity(root, name="doc2md", dirty=None):
    # type: (str, str, object) -> OrderedDict
    """``{name, version, commit, dirty}`` for the code doing the converting.

    ``dirty`` is supplied by the caller (a script may ask git; this layer does not
    shell out). Pass ``None`` — the default — when it is not known, and the key is
    OMITTED rather than reported as ``false``: an unverified "clean" is the one
    claim a reader would actually act on."""
    out = OrderedDict()
    out["name"] = name
    version = package_version(root)
    if version:
        out["version"] = version
    commit = git_commit(root)
    if commit:
        out["commit"] = commit
    if dirty is not None:
        out["dirty"] = bool(dirty)
    return out


def host_identity():
    # type: () -> OrderedDict
    """The interpreter and platform, with nothing that identifies the machine.

    ``platform.node()`` is deliberately NOT recorded — a hostname is neither needed
    to repeat a run nor safe to publish alongside de-identified source paths."""
    out = OrderedDict()
    out["python"] = platform.python_version()
    out["implementation"] = platform.python_implementation()
    out["platform"] = platform.platform()
    out["executable_basename"] = os.path.basename(sys.executable or "")
    return out
