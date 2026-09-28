---
title: tests/unit — agent rules
kind: rules
layer: backend
status: stable
owner: TBD
summary: Local rules for unit tests.
---

# Agent rules — `tests/unit/`

Inherits from `tests/CLAUDE.md`; the more specific wins.

## Rules

- **Mirror the source file.** `src/<pkg>/<mod>.py` → `tests/unit/<pkg>/test_<mod>.py`.
- **No disk, no network, no subprocess.** Needing one means it is an integration test.
- **Through the package's `__init__`**, not a `_*` submodule — except the ast-based
  boundary tests, which exist to police that very rule.
- **Name the behaviour, not the function.** `test_a_ligature_and_its_letters_tokenize_the_same`
  survives a rename; `test_normalize_pdf_text_2` does not.
- **A test that cannot fail is not a test.** When you add a gate, prove the red
  direction — break the code deliberately, watch the test fail, put it back.
