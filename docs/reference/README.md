---
title: doc2md reference
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [reference, configuration, vocabulary, schema, generated]
summary: Look-up material — every switch, every env var, every artifact key, every controlled term — kept true by drift tests.
---

# Reference

Look-up material, not narrative. If you want to understand *how doc2md works*,
read [`../guide.md`](../guide.md); if you want to know *what this flag does* or
*what that key means*, you are in the right place.

| Document | Answers |
|---|---|
| [`configuration.md`](configuration.md) | Every CLI switch and `DOC2MD_*` environment variable — and, for each, **what changes in the output** |
| [`output-schema.md`](output-schema.md) | Every key of `document.md` front matter, `structure.json`, `report.json`, `knowledge.json`, `manifest.jsonl` |
| [`vocabulary.md`](vocabulary.md) | Every governed metadata field and every term it may take (**generated**) |

## These files cannot drift

A reference that lies is worse than no reference, so each of these is bound to
the code by a test in `tests/integration/test_docs_parity.py`:

- every `argparse` flag in `scripts/` appears in `configuration.md`, and every
  flag documented there exists;
- every `DOC2MD_*` name read anywhere in the tree is documented;
- every key a real build writes into `report.json` / `structure.json` /
  `manifest.jsonl` appears in `output-schema.md`;
- `vocabulary.md` matches a fresh `python3 scripts/render_vocab_doc.py --check`.

Adding a flag, an env var, a report key or a vocabulary term **fails CI until the
reference is updated**. That is the point: it makes documentation part of the
change rather than a follow-up nobody does.

`vocabulary.md` is **generated** — edit `config/vocab.yaml` and regenerate; never
edit it by hand.
