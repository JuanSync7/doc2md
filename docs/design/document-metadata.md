---
title: Document metadata — tiers, controlled vocabulary, and the model boundary
kind: design
layer: backend
status: proposed
owner: TBD
summary: Which metadata field the pipeline fills, which a model may propose, which only a person may write, and how a closed vocabulary keeps the corpus from growing synonyms.
---

# Document metadata

**Status: proposed 2026-08-19.** Every converted document carries a `meta` block in
its front matter. This document says what may be in it, who is allowed to write each
field, and what stops the corpus from ending up with `mitigates`, `mitigate`,
`helps_mitigate` and `reduces_risk_of` as four different edges.

It complements [`output-contract.md`](output-contract.md), which owns the bundle
shape and the pipeline front-matter keys. Those keys do not change here.

## The problem this solves

A converted document is only useful downstream if you can route it — find every
runbook, every internal-only page, everything about RHEL 8. That routing runs on
metadata, and metadata has two failure modes that look nothing alike:

1. **Nobody wrote it.** The field is empty and the document is invisible to the
   query. Annoying, but honest and fixable.
2. **Something wrote it freely.** The field is full, and it holds a term nobody
   else uses. The document is *still* invisible to the query, but now the corpus
   also reports full coverage. This is the expensive one, and an unconstrained
   model produces it at scale.

So the contract is built around two ideas: a field's value must come from a known
set, and it must be obvious *who* put it there.

## Tiers — how a field is verified

Standards say what a field *means* (DCMI) and what *produced* it (PROV-O). Neither
encodes **how a field is verified**, which is the only question that decides
whether a model may write it. So the tier model is ours, and it is deliberately
small:

| tier | name | how a value is obtained |
|---|---|---|
| 0 | deterministic | read from the bytes or the filesystem — same input, same value, forever |
| 1 | derived | computed from tier-0 content by a fixed rule (a slug from a title, minutes from a word count) |
| 2 | model | a model **proposes**, a human **corrects** |

Orthogonal to the tier is **`authored_only`**. Some fields are an accountability or
safety boundary — `owner`, `confidentiality`, `status`, `classification`, the review
dates. Their entire purpose is that a person stood behind them. A model could
certainly guess them; that is exactly why it may not. `confidentiality` exists so a
restricted document is never surfaced to the wrong audience, and a field whose
purpose is a safety boundary cannot have a model as its author.

**Authored always wins.** A generated value fills a gap; it never overwrites one a
person wrote. Every field records its origin in `_provenance`, and the test is
deliberately fail-safe: a missing entry, a non-mapping entry, an entry with no
`source`, and an unrecognised source all count as authored. The cost of keeping a
human's value is nothing; the cost of overwriting one is trust.

The case that matters most is the one tier 2 is *defined* by — a human **corrects**
a value a previous run generated. The provenance still says `generated`, so source
alone cannot tell the difference. So a generated value is written together with
`value_sha`, a fingerprint of exactly what was stored; a current value whose
fingerprint no longer matches has been edited by somebody, and is treated as
authored from then on.

## Governance — where values are allowed to come from

Three regimes, chosen per field, all defined in [`config/vocab.yaml`](../../config/vocab.yaml):

- **`closed`** — a hard fail on an unknown value. Structural; changing the list is a
  schema change. Used where the field *routes* something: `document_types`,
  `impact`, `relation_predicates`, `confidentiality`.
- **`registry`** — an unknown value lands in `<field>_proposed` and is promoted into
  the vocabulary once it appears on `promote_at` (3) documents; promotion writes an
  alias for every variant seen. Used where the corpus, not the designer, should
  decide the term list: `tags`, `keywords`, `topics`, `audience`, `subtype`.
- **`ref`** — no vocabulary at all; validated by referential integrity. `ref` and
  `backs` must resolve to a heading anchor in the same document, `see_also` to a
  page id the corpus contains, `control`/`protects` to a register id or a named
  entity.

Registries ship **empty on purpose**. Hard-closing tags at document one means
guessing the corpus shape; seeding a registry does the same thing more quietly. An
empty registry means every term starts as a proposal and the promotion rule is what
admits it — which is the only version of this that actually measures anything.

Two health signals come from the corpus rather than any single document: the
**singleton rate** (share of registry terms used on exactly one document; above 0.40
the vocabulary has stopped grouping anything) and the **promotion queue**.

## Where each field is stored

The metadata a document carries is two different things wearing one name, and at
schema v2 they are stored separately:

| | lives in | why |
|---|---|---|
| **descriptors** — id, title, type, tags, lang, status, owner, abstract, see_also | `document.md` front matter | small, bounded, what retrieval filters on; markdown tooling reads them for free |
| **knowledge** — entities, relations, decisions, risks, open_questions, links | `knowledge.json` | unbounded, 100% structured, consumed by a graph loader that never wants the prose |

Measured on the worked example the knowledge payload was ~19k characters against
~3.5k of descriptors — roughly **5:1** — so leaving it in front matter makes every
consumer that only wants the body pay for a relation table it will discard.

The seam is **mechanical**, not a matter of taste: a field moves iff it is a
`records`/`groups` field and is **not** `authored_only`. That carve-out is the whole
subtlety — `accountable_roles` is a record list, but it is a statement about who
stands behind the document, so it stays with the document. A `<field>_proposed` slot
always follows its field.

Two properties make the split safe rather than merely tidy:

- **One view for everything that reads it.** The layout is confined to `_schema.py`
  (`in_knowledge`, `split_meta`, `merge_meta`, `knowledge_document`,
  `knowledge_payload`, `meta_collisions`); the scripts only do the disk touch. The
  linter and the enricher operate on one merged mapping, so no rule in `backend.kb`
  has to know the split exists — and `kb_lint` still tells a person which file a
  finding is in.
- **A field in BOTH files is an error, not a merge.** `merge_meta` picks front matter
  so readers see one view, but the next enrichment run then rewrites the loser away.
  `meta_collisions` reports it, because the merged mapping cannot show that two live
  copies existed.
- **Absent and unreadable are different states.** No sidecar means "not enriched
  yet". A sidecar that will not parse is an error that stops the document being
  written, because treating it as empty would let the next run regenerate over
  entities a person corrected by hand.

Full shape and rationale: [`output-contract.md`](output-contract.md).

## The corpus half of the linter

Per-document linting has a ceiling, and it is lower than it looks. Every defect
below leaves each individual file **internally perfect** — every value governed,
every pointer resolved, `errors=0` — while the corpus is broken:

| gate | what it catches | why one document cannot |
|---|---|---|
| **identity** | two documents claiming one `id`/`uid`; a document nothing can link to | neither document is wrong |
| **synonymy** | `rhel-8` here and `RHEL_8` there | each spelling is locally fine |
| **entities** | `Docker` and `docker` — one thing, two graph nodes | ditto, and this one is the expensive silent defect |
| **graph** | dead `see_also`, orphans, edges naming undeclared entities | needs the full id set |
| **schema skew** | which documents a version bump has to backfill | "current" is a corpus fact |
| **coverage** | a field on 3% of documents: conditional, or a broken extractor? | needs the denominator |
| **vocabulary** | terms nobody uses; synonyms *inside the term list itself* | needs no corpus at all — and runs on an empty one |

Two design decisions carry the weight.

**Normalise before grouping.** Values go through `Vocabulary.normalize` first, so a
variant the vocabulary already declares as an alias never surfaces as a corpus
finding — the per-document gate reports that as `vocab-alias`, and the corpus gate
would otherwise re-litigate every alias on every run. What survives normalisation is
drift nobody has decided about yet, and it arrives in two shapes: an **identity
collision** (same key, an error, with an alias proposed) and a **similarity** (a
warning, because only a person can tell `kubernetes`/`kubernets` from
`annual`/`biannual`). Both answers are recordable in `lint.similar_ok`, so a
question the tool cannot settle is asked exactly once.

**A truncated corpus answers fewer questions, not different ones.** `--only` and
`--limit` skip `graph`, `skew`, `coverage` and `vocab_usage` and say so. A subset can
only ever *miss* a collision, so identity, synonymy and entity checks stay honest;
the other four invert under truncation — a `see_also` whose target was excluded reads
as dead, the newest schema version may be sitting in a skipped document. Reporting a
defect the corpus does not have is worse than reporting nothing.

Cardinality is deliberately **not** re-run corpus-wide. Every facet the per-document
gate grades (`relations.p`, `entities.type`, `risks.impact`, `risks.mode`,
`decisions.status`) is governed by a closed vocabulary, so its value set is bounded by
the term list and membership is already the stricter test. The genuinely
unanswerable question is the mirror one, and that is what `vocabulary_usage`
measures: which governed terms no document ever draws on. A closed list nobody draws
from is as broken as an open one nobody reuses — and a model offered a fifteen-value
enum where four values are real will keep reaching for the other eleven.

## Cardinality — is this field a facet at all?

A field whose values are nearly all distinct is *documentation*, not a facet. It
will never group anything, and — worse — letting a model believe it is choosing from
a set invites it to invent one more value per document. The linter grades
`distinct / used` per field: above 0.75 the field is reported as not-a-facet, above
0.45 as thin.

Below `facet_min_uses` (8) the verdict is **`sparse`** and is neither a pass nor a
fail. One relation has a ratio of 1.00 and says nothing whatsoever about the field;
grading it would condemn every short document.

This is why `entities.role` was deleted rather than governed: every observed value
was unique (ratio 1.00). It became a free-text `note`, unindexed, so that no model
is told it is picking from a set that does not exist.

## Reuse before inventing

Every governed field and every value that has an established equivalent carries
`maps_to`. The rule is: **borrow the standard's term as the vocabulary value, keep
YAML as the serialisation.** That buys alignment and a mechanical RDF export path
later without adopting a triplestore or OWL tooling today — and adopting the tooling
first is how vocabulary projects die.

| layer | standard | how far we adopt it |
|---|---|---|
| identity, source, relations | DCMI Terms | wholesale |
| extraction provenance | PROV-O | wholesale — document/run/extractor maps onto Entity/Activity/Agent, and `_provenance.<field>` is per-field `wasGeneratedBy` |
| tags, keywords | SKOS | wholesale — the registry is a `ConceptScheme`, a tag a `Concept`, our aliases are `altLabel`, and `broader` gives free hierarchy |
| entity types | schema.org | where it maps (about half) |
| relation predicates | STIX 2.1 | the security subset only — `mitigates` exact, `threatens` → `targets` |
| decision status | MADR | wholesale, collapsing our eight values onto its five |
| risk severity | ISO 31000 / FMEA | already aligned |
| **tier model, failure mode, infra entity types** | **none** | **genuinely local — kept small** |

Roughly 70% of the vocabulary maps to something that already exists. What does not
is named `local` rather than left blank, so an unmapped field reads as a decision
rather than an oversight.

Two collapses are worth stating explicitly because they change stored values:

- **MADR.** `decision_status` is the five MADR values. `recommended` → `proposed`,
  `standing_rule` → `accepted`, `assumed` → `accepted` + `assumption: true`,
  `provisional` → `accepted` + `review_required: true`. Nuance moves into qualifier
  fields; the status itself stays a five-value enum.
- **STIX.** The predicate is `targets`; `threatens` is an alias. Canonical spelling
  stays snake_case and `maps_to` records the hyphenated STIX form.

Aliases are not documentation — `Vocabulary.normalize()` resolves them, so both
spellings can never coexist in stored data.

## The block

`document.md` carries the DESCRIPTORS; the graph payload is in `knowledge.json`
beside it (see [where each field is stored](#where-each-field-is-stored)).

```yaml
meta:
  schema_version: 3          # tier 0 — which revision of the field inventory wrote this
  id: "aion/it/runbooks/claude-code-rocky8"    # tier 0 — THE identity, from the path
  uid: "aion/it/runbooks/claude-code-rocky8"   # deprecated alias of `id`, always equal
  word_count: 14932          # tier 1
  reading_time_minutes: 60
  source: {...}              # tier 0 — dcterms:source
  extraction: {...}          # tier 0 — prov:Activity
  vocab_version: 1
  type: "runbook"            # tier 2 — closed vocabulary
  tags: []                   # tier 2 — registry
  tags_proposed: ["rhel8"]   #          not yet promoted
  confidentiality: "internal"  # authored only
  # entities / relations / decisions / risks / open_questions / links are NOT here —
  # they are the knowledge payload, and live in knowledge.json.
  _provenance:                 # ... and this block splits with its fields, so the
                               # sidecar carries the provenance of what IT holds.
    type:
      tier: 2
      source: "generated"
      model: "..."
      prompt_sha: "..."
      value_sha: "..."       # what we wrote — a later edit is detectable
```

Keys are written in a canonical order (schema order, each `<field>_proposed` beside
its field, `_provenance` last) so the block is a function of its content rather than
of the order a run happened to merge it in. That is what makes a re-run
byte-identical.

Everything sits under **one** top-level key. The pipeline keys (`doc_id`,
`markdown_sha256`, `lane`, `source_*`) stay exactly where they are and keep meaning
exactly what they meant; nesting is what lets the enrichment stage rewrite its own
block wholesale without touching a key it does not own, and what stops a `title`
from colliding with a `source_title`.

`_provenance` carries `tier` and `source` (`extracted` | `derived` | `generated` |
`authored`) per field, plus `model` and `prompt_sha` for generated values.

## Backfill

Adding metadata to an already-converted corpus must not require re-converting it,
and it does not:

- **`markdown_sha256` covers the body only**, and every `line_span` and image `line`
  index is body-relative. Front matter can be added, rewritten or reordered without
  invalidating a single hash or span. This was already true; the metadata block
  depends on it.
- **The deterministic tiers need no model at all**, so a corpus can be brought up to
  a new schema revision entirely offline.
- **Prior answers are carried forward and re-validated.** Carrying forward makes a
  re-run cheap; re-validating is what makes a vocabulary bump mean something. A
  value that was legal under v1 and is not under v2 is removed or relocated to
  `_proposed`, and the change is announced. Authored values are left alone.
- **Two version numbers, because they invalidate different work.**
  `schema_version` moves when the field inventory does; `version` in `vocab.yaml`
  moves when the allowed values do — and only classified fields need revisiting for
  the latter. The model-answer cache is keyed on body + model + prompt + vocabulary
  version, so a bump re-asks exactly the affected documents.

Note this versions the **metadata block**, not the bundle contract; a bundle-wide
`schema_version` remains a separate, still-open decision.

## The report gate

`report.json` gains a `doc_meta` block, mirroring `captions` and kept just as
deliberately separate from `status`: enrichment is a re-runnable, non-deterministic
pass that runs after a lossless build, and a lossless document must not read as
degraded merely because no model has classified it yet.

`expected` counts model-writable fields (the denominator is the schema, not what a
run attempted), against `filled`, `authored`, `invalid` and `pending`. The gate is
`disabled | pending | complete | incomplete`. One deliberate difference from
`captions`: a non-zero `invalid` keeps the gate off `complete`, because a value
outside a closed vocabulary is worse than an absent one — it silently becomes a new
term for everything that groups by that field.

`report.json` still carries **no model output**, only the gate over it. The values
live in `document.md`.

## Asking a model

`request_spec()` emits the field spec with the exact allowed terms, so the model
**selects** rather than writes. Free generation into a string field is the default
failure mode and the fastest route to a polluted vocabulary.

`unknown` is always a legal answer — declared on every field, including the
registry fields that ship with no term list — and an omitted field is simply left
pending. A model with no escape hatch is forced to guess, and a confident wrong
label is worse than a gap: the gap stays visibly outstanding and the label does not.
A decline is recorded as `declined-unknown` rather than `not-in-vocabulary`, so it
does not dilute the signal about which term a model keeps trying to invent.

Nothing a model returns is trusted. `accept_model_meta()` refuses values outside a
closed vocabulary, routes unknown registry values to `_proposed`, refuses
authored-only fields outright, refuses keys that are not in the schema at all (a
model cannot inject `doc_id`), and refuses to overwrite an authored value. Refusals
are reported verbatim, because the useful question after a run is never "how many
were wrong" but "which term did it keep trying to invent".

## Recorded deviations

- **`control`/`protects` resolve to a named entity as well as a register id.** The
  vocabulary's original rule said register ids only; measured on the worked example,
  11 of 12 controls named an entity (a hook script, a settings key, a sandbox) and
  exactly one named a decision id. A rule the data violates nine times in ten is a
  broken rule, not a broken corpus.
- **Entity type falls back to the group name.** `hosts` implies `Host`. Without it,
  every entry in a group that omits `type` is silently unchecked — that was 15 of 30
  entities in the worked example, all of which the naive linter reported as clean.
- **A scalar entity group is declared, not skipped.** `identifiers: {…}` is a
  mapping, not a list of entities; it produces an explicit finding so that "not
  checked" never renders the same as "checked and clean".
- **A pointer that cannot be checked is skipped, not passed.** `see_also` is only
  resolved when the caller supplies the corpus id set — and `kb_lint --only` or
  `--limit` therefore skips it and prints `[PARTIAL]`, because a narrowed run that
  invented dead links would fail a build over its own truncation.
- **Record sub-keys are split into required discriminators and optional
  qualifiers.** `relations[].p`, `decisions[].status` and `risks[].impact` must be
  present — nothing checks a record without them. `mode` and the boolean qualifiers
  are optional by design: nuance was deliberately pushed into them, so demanding one
  would make every ordinary relation an error.
- **Inflection is folded in the term list, never in an identity.** `_stem` collapses
  `mitigate`/`mitigates`/`does_not_mitigate` so a *curated closed list* holding two of
  them is an error. `norm_key` — the identity used for tags and entities — folds only
  case, separators and diacritics, because folding inflection there would merge proper
  nouns that differ by a trailing `s` (`Windows` is not `window`). Inflected tag
  variants are still caught, as a warning, by the similarity sweep.
- **An inherited hand-written value is recorded as `authored`, not as the source it
  would have had.** A prior value with no `_provenance` is treated as hand-written —
  that *is* the authored-wins rule. Stamping it `derived` made the next run read
  `source: derived`, decide the derived value won, and destroy it: "authored always
  wins" that survives exactly one run. It failed silently and expensively — a
  hand-picked `id` reverting to a slug orphans every `see_also` pointing at it.
- **Run stamps are never inherited.** `schema_version` and `extraction` describe the
  code that wrote the block, so the authored-wins rule must not apply to them; a v1
  block migrating to the v2 layout while keeping `schema_version: 1` makes the
  migration undetectable and the stamp a lie.
- **An entirely unstamped corpus gets one finding, not one per document.** With
  nothing carrying `schema_version` there is no current version to be behind, so
  naming every document would implicate each one for a property the corpus as a whole
  lacks.
- **An enumerated family is never a synonym family.** `node-01`…`node-40`,
  `us-east-1`/`us-west-2`, `ISO-27001`/`ISO-27002` are each ~95% similar to their
  siblings. Measured on a synthetic 400-document corpus naming 1200 hosts, the naive
  sweep produced **375,328 warnings in 10.6s** — enough to bury every real finding.
  Two strings identical apart from their digits are now skipped: a differing number
  is what makes them distinct, so it is evidence *against* synonymy. With that plus a
  length bound derived from `ratio = 2M/(la+lb)` (sound — it can only drop pairs that
  could never clear the threshold), the same corpus reports **0 warnings in 0.31s**
  while a planted `kubernets-controller`/`kubernetes-controller` typo still surfaces
  at 97%. This also suppresses `rhel`/`rhel8`, which is a hierarchy rather than a
  synonym and belongs in `broader`.

## Tooling

```
python3 scripts/enrich_metadata.py --bundles data/bundles          # tiers 0+1, no model
python3 scripts/enrich_metadata.py --bundles data/bundles --vlm-url <chat endpoint>
python3 scripts/kb_lint.py --bundles data/bundles --strict
python3 scripts/kb_lint.py --bundles data/bundles --suggest-aliases
```

`--suggest-aliases` prints paste-ready `aliases:` entries for the spelling collisions
it found. It never edits the vocabulary: an alias is a permanent claim that two
strings mean the same thing, so the linter proposes and a person commits.

The rules live in `backend.kb`; both scripts are transport. See
[`config/vocab.yaml`](../../config/vocab.yaml) for the term lists and
[`src/backend/kb/README.md`](../../src/backend/kb/README.md) for the API.
