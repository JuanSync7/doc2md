"""
title: CI ring simulation — block the optional deps CI does not install
kind: tests
layer: backend
summary: Put this directory on PYTHONPATH to reproduce CI's import environment locally.
"""
# CI installs neither PyYAML nor Pillow. A dev host that HAS them runs a different
# suite from the one CI runs, and the difference is invisible until CI goes red for
# a reason nobody can reproduce — or, worse, stays green over tests that only pass
# because a dependency was absent.
#
# Both directions have actually happened here. A farm node with Pillow 5.1.1 turned
# 34 tests red that were green on the dev host and in CI, because four test files
# built PNG bytes that were not decodable (see tests/pngsupport.py).
#
# `sitecustomize` is imported automatically by the interpreter at startup, so simply
# putting this directory on PYTHONPATH is enough:
#
#     PYTHONPATH=tests/ciring python3 -m pytest -q        # or: make test-ciring
import sys


class _Blocker(object):
    """A meta_path finder that refuses the modules CI does not have."""

    BLOCKED = ("yaml", "PIL")

    def find_module(self, name, path=None):
        return self if name.split(".")[0] in self.BLOCKED else None

    def load_module(self, name):
        raise ImportError("blocked by tests/ciring: not installed on the CI ring")

    # Python 3.4+ finder protocol, so this keeps working when find_module goes away.
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ImportError("blocked by tests/ciring: not installed on the CI ring")
        return None


sys.meta_path.insert(0, _Blocker())
