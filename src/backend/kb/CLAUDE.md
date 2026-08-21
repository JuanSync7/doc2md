---
title: Agent rules — backend.kb
kind: rules
layer: backend
status: draft
owner: TBD
summary: Local rules for the document-metadata layer — tiers, controlled vocabulary, and the authored-wins boundary.
---
# Agent rules — `src/backend/kb/`

Inherits from the root, `src/`, and `src/backend/` rules; the more specific wins.

## Rules

- **Pure policy.** Mappings in, findings and decisions out. The only disk touch is
  `load_vocab()` reading `config/vocab.yaml`; the corpus walk lives in
  `scripts/kb_lint.py` and the model call in `scripts/enrich_metadata.py`. No
  network, no model, ever, in here.
- **Stays Python 3.6-compatible and stdlib-only** — the CI 3.6 ring is
  `python:3.6-slim` with pytest and nothing else, so **PyYAML is not available**.
  Parse and render YAML through `backend.ingest.parse_block` /
  `render_block`. No PEP 604 unions, no f-strings; use type comments.
- **One place decides membership.** Every "is this a legal value" question goes
  through `Vocabulary`, so alias normalisation happens exactly once. The moment a
  call site compares a value to a bare list, the corpus grows both spellings —
  which is the entire failure this package exists to prevent.
- **Authored always wins, and authored-only is absolute.** A generated value may
  fill a gap but never overwrite a value a person wrote, and `authored_only`
  fields (owner, confidentiality, status, review dates) may not be model-written at
  all. Record every field's origin in `_provenance`; an unlabelled existing value
  is treated as authored.
- **Never silently skip.** A value the linter cannot check must produce a finding,
  not an absence. "Not looked at" and "looked at and clean" must never render the
  same — that confusion is what let half the entities in the worked example escape
  validation.
- **Reuse before inventing.** A new vocabulary field or value carries `maps_to`
  naming its standard term (DCMI, PROV-O, SKOS, schema.org, STIX, MADR, ISO 31000)
  or the literal `local`. Borrow the standard's TERM as the value; keep YAML as the
  serialisation. Do not add RDF/OWL tooling.
- **The metadata block is a contract.** Keep the field inventory, the report gate
  and `docs/design/document-metadata.md` in sync, and bump `SCHEMA_VERSION` when
  the inventory moves or `version` in `config/vocab.yaml` when the terms do — they
  invalidate different work.
- Keep the public surface in `__init__.py`/`__all__`; implementation in `_*` modules.
