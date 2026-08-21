---
title: backend.kb
kind: package
layer: backend
status: draft
owner: TBD
public_api: src/backend/kb/__init__.py
tags: [metadata, vocabulary, frontmatter, governance, doc2md, knowledge-base]
summary: Tiered document metadata — the controlled vocabulary, the field inventory, tier-1 derivations, the enrichment policy and the linter.
---

# backend.kb

The **document-metadata** layer: it decides what may be written into a converted
document's `meta` front-matter block, who is allowed to write each field, and
whether the corpus's vocabulary is still doing its job.

Everything here is pure policy. The one disk touch is reading `config/vocab.yaml`;
the bundle walk lives in [`scripts/kb_lint.py`](../../../scripts/kb_lint.py) and the
model call in [`scripts/enrich_metadata.py`](../../../scripts/enrich_metadata.py).

## The four questions it answers

| question | module | answer |
|---|---|---|
| Who may write this field? | `_schema` | tier 0/1/2, plus the `authored_only` boundary |
| Is this value a term? | `_vocab` | `closed` / `registry` / `ref` governance |
| Is this *document* sane? | `_lint` | cardinality, membership, hygiene, referential integrity |
| Is the *corpus* sane? | `_corpus` | identity, synonymy, entities, graph, skew, coverage |

`_derive` holds the fixed-rule tier-1 computations and `_enrich` holds the policy
for what a model is allowed to contribute.

`_lint` and `_corpus` are split by what they can **see**, not by subject matter.
Every check in `_corpus` is one a single document is structurally incapable of
answering — a duplicated `id`, one entity spelled two ways, a term nobody uses.
Those are not defects in any one file; each document is internally consistent, which
is exactly why they survive per-file linting indefinitely.

## Tiers

- **tier 0 — deterministic.** Read from the bytes or the filesystem. Same input,
  same value, forever.
- **tier 1 — derived.** Computed from tier-0 content by a fixed rule (a slug from a
  title, minutes from a word count). No judgement, no model.
- **tier 2 — model.** A model *proposes*, a human *corrects*: classification,
  summarisation, entity and relation extraction.

## Two files, one view

At schema v2 the **descriptors** (id, title, type, tags, status, owner) stay in
`document.md` front matter, where retrieval and markdown tooling read them, and the
**knowledge payload** (entities, relations, decisions, risks, open_questions, links)
goes to `knowledge.json`, where a graph loader can `json.load` it without parsing
markdown. The seam is mechanical — a field moves iff it is a `records`/`groups` field
and is not `authored_only` — and it is confined to `_schema.py` (`in_knowledge`,
`split_meta`, `merge_meta`, `knowledge_document`, `knowledge_payload`,
`meta_collisions`), so every other rule here still sees one mapping.

Orthogonal to the tier, `authored_only` marks the accountability and safety
boundaries — `owner`, `confidentiality`, `status`, the review dates. Their whole
purpose is that a person stood behind them, so no enrichment pass may write them
even though a model could certainly guess. **Authored always wins:** a generated
value fills a gap, it never overwrites one a person wrote, and `_provenance`
records which is which so the distinction survives in the file.

## Governance regimes

- **`closed`** — hard fail on an unknown value. Changing the list is a schema change.
- **`registry`** — an unknown value lands in `<field>_proposed` and is promoted once
  it appears on `promote_at` documents. Registries start **empty**: seeding one
  would guess the corpus shape at document zero, which is the failure the regime
  exists to avoid.
- **`ref`** — no vocabulary; validated by referential integrity instead.

## Public API

- `load_vocab(path=None, text=None)` → `Vocabulary`; `vocab_path(...)` resolves
  `$DOC2MD_VOCAB` → `config/vocab.local.yaml` → `config/vocab.yaml`.
- `Vocabulary.normalize(field, value)` → `(canonical, qualifiers)` — the single
  place alias resolution happens, so `threatens`/`targets` can never both exist.
- `field(name)`, `model_writable(name)`, `record_vocab(name)`, `proposed_key(name)`
  — the field inventory.
- `split_meta(meta)` → `(front_matter_block, knowledge_payload)` and
  `merge_meta(front, knowledge)` — everything else in this package works on the one
  merged mapping. `in_knowledge(name)` / `knowledge_field_names()` answer where a
  field lives; `knowledge_document(meta, doc_id, markdown_sha256, keep_empty=False)`
  builds the sidecar object (header + payload); `knowledge_payload(data)` strips the
  header back off a parsed one; `meta_collisions(front, knowledge)` names any field
  present in **both** files — two sources of truth, which `merge_meta` resolves and
  the next enrichment run would then overwrite, so it is reported rather than merely
  resolved. `DOCUMENT_FILE`, `KNOWLEDGE_FILE` and `KNOWLEDGE_HEADER` name the pair and
  its self-containment keys.
- `slugify`, `derive_uid`, `word_count`, `reading_time_minutes`,
  `keyword_candidates`, `heading_anchor`, `body_anchors` — tier-1 derivations.
- `request_spec(vocab, wanted=None)` — the enum-constrained field spec a model is
  asked to fill; `accept_model_meta(...)` decides what its answer may contribute;
  `revalidate_generated(...)` re-checks prior answers after a vocabulary bump;
  `meta_coverage(...)` produces the counts for the `doc_meta` report gate.
- `lint_document(meta, vocab, anchors=None, known_ids=None)` → findings + facets;
  `corpus_report(metas, vocab)` → registry health; `normalize_document(...)`,
  `facet_report(...)`.
- `corpus_findings(docs, vocab, partial=False)` → the corpus gates, where `docs` is
  `[{"path", "meta"}]`. `partial=True` skips the four checks a truncated walk would
  invert and names them in the result's `skipped`.
- `alias_suggestions(docs, vocab)` → `{field: {variant: canonical}}` for the spelling
  collisions found — proposals only; nothing writes to the vocabulary.
- `identity_report`, `synonym_report`, `entity_report`, `graph_report`,
  `skew_report`, `coverage_report`, `vocabulary_usage` — the individual gates;
  `vocabulary_hygiene(vocab)` grades the term list itself and needs no corpus.
- `norm_key(value)` — the identity key (case, separators and diacritics are not
  distinctions); `strip_polarity(value)` — `does_not_mitigate` → `mitigate`, for
  grouping candidates only, never for rewriting a stored value.

See [`docs/design/document-metadata.md`](../../../docs/design/document-metadata.md)
for the contract and the standards mapping, and
[`config/vocab.yaml`](../../../config/vocab.yaml) for the term lists themselves.
