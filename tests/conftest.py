"""Shared pytest fixtures live here.

Put `src/` on the path so tests import the backend through its public API
(`from backend import ...`) without requiring an editable install, and the repo
root so tests can import the `config` package (`from config.settings import ...`).
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)
