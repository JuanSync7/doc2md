---
title: CI ring simulation
kind: tests
layer: backend
status: stable
owner: TBD
summary: Reproduce CI's import environment locally by blocking the optional dependencies CI does not install.
---

# `tests/ciring`

CI installs neither **PyYAML** nor **Pillow**. A dev host that has them runs a
different suite from the one CI runs.

Put this directory on `PYTHONPATH` and the interpreter auto-imports
`sitecustomize.py`, which blocks both imports:

```sh
PYTHONPATH=tests/ciring python3 -m pytest -q     # or: make test-ciring
```

## Why it exists

Both directions of the drift have bitten this repo:

- **Dependency present locally, absent in CI** — a test passes here and fails there.
- **Dependency absent locally, present elsewhere** — four test files built PNG bytes
  that were not actually decodable. They passed on the dev host and in CI (no
  Pillow) and failed on a compute-farm node with Pillow 5.1.1, where
  `image_caption.prepare_png` really decodes and correctly returned `UNDECODABLE`.
  34 tests, all green everywhere anyone had looked. See `tests/pngsupport.py`.

The fix for the second case was to make the test data real. The fix for the class
is this ring plus `make test-pillow`, so **both** environments are exercised
deliberately instead of whichever one the machine happens to provide.
