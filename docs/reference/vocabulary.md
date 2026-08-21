---
title: Controlled vocabulary reference
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [reference, vocabulary, metadata, governance, generated]
summary: Every governed field and every term it may take — generated from config/vocab.yaml, so it cannot drift.
---

# Controlled vocabulary reference

**Generated — do not edit by hand.** Regenerate with
`python3 scripts/render_vocab_doc.py`; CI runs the same script with
`--check`, so a term added to `config/vocab.yaml` without regenerating this
file fails the build. The source of truth is
[`config/vocab.yaml`](../../config/vocab.yaml); the contract that explains
*why* each field is governed the way it is lives in
[`document-metadata.md`](../design/document-metadata.md).

Vocabulary revision: **v1** (`vocab_version` in every written block).

## The three governance regimes

- **closed** — an unknown value is a hard failure. Changing this list is a schema change.
  Vocabularies: `confidentiality`, `decision_status`, `document_status`, `document_types`, `entity_types`, `failure_modes`, `impact`, `lang`, `link_categories`, `relation_predicates`, `requirement_level`, `review_cadence`
- **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.
  Vocabularies: `audience`, `keywords`, `subtype`, `tags`, `topics`
- **ref** — no term list; validated by referential integrity instead.
  Vocabularies: `refs`

Registry vocabularies ship **empty on purpose**: admitting terms by promotion
is the whole point, so seeding one here would guess the corpus shape at
document zero — the exact failure the regime exists to avoid.

## Field inventory

Every field the metadata layer knows about. **tier** 0 = deterministic,
1 = derived by a fixed rule, 2 = a model proposes and a human corrects.
**authored** marks the accountability and safety boundaries no enrichment
pass may ever write. **file** is where the field is stored at schema v2 — a
field lives in `knowledge.json` iff it is a `records`/`groups` field and is not
authored-only.

| field | tier | kind | authored | vocabulary | regime | file | maps to | note |
|---|---|---|---|---|---|---|---|---|
| schema_version | 0 | scalar |  |  |  | `document.md` | local | which revision of this inventory wrote the block |
| vocab_version | 0 | scalar |  |  |  | `document.md` | local | which revision of the term list the values were checked against |
| uid | 0 | scalar |  |  |  | `document.md` | dcterms:identifier | stable machine identity, namespaced from the source path |
| version | 0 | scalar |  |  |  | `document.md` | local | the source document's own revision, when it declares one |
| source | 0 | map |  |  |  | `document.md` | dcterms:source | uri/publisher/authored_by/supersedes/is_derivative |
| extraction | 0 | map |  |  |  | `document.md` | prov:Activity | run_at/schema/extractor — prov:generatedAtTime + prov:SoftwareAgent |
| id | 1 | scalar |  |  |  | `document.md` | dcterms:identifier | human-stable slug; authored wins, derived from title otherwise |
| slug | 1 | scalar |  |  |  | `document.md` | local | url form of the title |
| word_count | 1 | scalar |  |  |  | `document.md` | local |  |
| reading_time_minutes | 1 | scalar |  |  |  | `document.md` | local |  |
| title | 2 | scalar |  |  |  | `document.md` | dcterms:title | extracted when the source has a real one; the junk-title path is exactly where a model should propose from the first heading |
| short_title | 2 | scalar |  |  |  | `document.md` | local | compression judgement |
| abstract | 2 | scalar |  |  |  | `document.md` | dcterms:abstract |  |
| type | 2 | scalar |  | `document_types` | closed | `document.md` | dcterms:type |  |
| subtype | 2 | list |  | `subtype` | registry | `document.md` | dcterms:type |  |
| lang | 2 | scalar |  | `lang` | closed | `document.md` | dcterms:language |  |
| tags | 2 | list |  | `tags` | registry | `document.md` | skos:Concept |  |
| keywords | 2 | list |  | `keywords` | registry | `document.md` | skos:Concept |  |
| topics | 2 | list |  | `topics` | registry | `document.md` | dcterms:subject |  |
| audience | 2 | list |  | `audience` | registry | `document.md` | dcterms:audience |  |
| aliases | 2 | list |  |  |  | `document.md` | skos:altLabel |  |
| entities | 2 | groups |  | `entity_types` | closed | `knowledge.json` | schema:Thing |  |
| relations | 2 | records |  | `relation_predicates` | closed | `knowledge.json` | local |  |
| decisions | 2 | records |  | `decision_status` | closed | `knowledge.json` | madr:status |  |
| risks | 2 | records |  | `impact` | closed | `knowledge.json` | iso31000 |  |
| open_questions | 2 | records |  |  |  | `knowledge.json` | local |  |
| links | 2 | groups |  | `link_categories` | closed | `knowledge.json` | dcterms:references |  |
| see_also | 2 | list |  |  |  | `document.md` | dcterms:references |  |
| prerequisites | 2 | list |  |  |  | `document.md` | dcterms:requires |  |
| out_of_scope | 2 | list |  |  |  | `document.md` | local |  |
| status | 2 | scalar | yes | `document_status` | closed | `document.md` | local |  |
| confidentiality | 2 | scalar | yes | `confidentiality` | closed | `document.md` | dcterms:accessRights | a field whose entire purpose is a safety boundary cannot have a model as its author |
| classification | 2 | scalar | yes |  |  | `document.md` | local | the human-facing wording of confidentiality |
| owner | 2 | scalar | yes |  |  | `document.md` | prov:wasAttributedTo |  |
| accountable_roles | 2 | records | yes |  |  | `document.md` | prov:wasAttributedTo |  |
| review_cadence | 2 | scalar | yes | `review_cadence` | closed | `document.md` | local |  |
| last_reviewed | 2 | scalar | yes |  |  | `document.md` | dcterms:modified |  |
| next_review_due | 2 | scalar | yes |  |  | `document.md` | local |  |
| validated_against_version | 2 | scalar | yes |  |  | `document.md` | local | the product/tool version the document's claims were checked against |

## Record and group sub-keys

A record list carries its own governed sub-keys — without this a
relation's `p` would be checked while its `mode` qualifier, which is
where the nuance was deliberately pushed, would not.

| sub-key | vocabulary | shape |
|---|---|---|
| relations[].mode | `failure_modes` | record |
| relations[].p | `relation_predicates` | record |
| decisions[].status | `decision_status` | record |
| risks[].impact | `impact` | record |
| risks[].mode | `failure_modes` | record |
| entities (member_type) | `entity_types` | group |
| links (group_name) | `link_categories` | group |

## Referential fields

Pointers, graded by resolution rather than by membership: `ref`, `backs`, `see_also`, `control`, `protects`.

## Vocabularies

### `audience`

Governance: **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.

Maps to: `dcterms:audience`

> Who the document is written for. Registry — the role vocabulary is org-shaped.

Governs: `audience`

Values: _(empty by design — terms arrive by promotion, never by seeding)_

### `confidentiality`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `dcterms:accessRights`

> A safety boundary: it exists so a confidential document is never surfaced to the wrong audience. Authority-owned — a model may never author this field.

Governs: `confidentiality`

Values: `public`, `internal`, `confidential`, `restricted`

### `decision_status`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `madr:status`

> MADR's status vocabulary is the de-facto standard for decision records. Nuance moves into qualifier fields; the status itself stays a five-value enum.

Governs: `decisions`, `decisions[].status`

Values: `proposed`, `rejected`, `accepted`, `deprecated`, `superseded`

Aliases (normalised on the **write** path, so a variant can never coexist with its canonical form):

| variant | canonical | qualifiers added |
|---|---|---|
| assumed | accepted | `assumption: true` |
| provisional | accepted | `review_required: true` |
| recommended | proposed |  |
| standing_rule | accepted |  |


### `document_status`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `local`

> The document's own lifecycle, distinct from decision_status. No DCMI term exists for it, so it stays local — but closed, because it routes retrieval.

Governs: `status`

Values: `draft`, `review`, `approved`, `deprecated`, `superseded`

### `document_types`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `dcterms:type`

Governs: `type`

Values: `runbook`, `policy`, `design`, `report`, `guide`, `reference`, `meeting_note`, `contract`, `spec`

| value | maps to |
|---|---|
| contract | schema:DigitalDocument |
| guide | schema:TechArticle |
| policy | schema:DigitalDocument |
| reference | schema:TechArticle |
| report | schema:Report |
| runbook | schema:TechArticle |


### `entity_types`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `schema:Thing`

Governs: `entities`, `entities[].member_type`

Values: `Organization`, `Person`, `Role`, `Software`, `OperatingSystem`, `Package`, `Host`, `Path`, `Script`, `Hook`, `Setting`, `Identifier`, `Standard`, `Metric`

| value | maps to |
|---|---|
| OperatingSystem | schema:SoftwareApplication |
| Organization | schema:Organization |
| Package | schema:SoftwareSourceCode |
| Person | schema:Person |
| Role | schema:Role |
| Software | schema:SoftwareApplication |
| Standard | schema:CreativeWork |


Group defaults (a member with no explicit `type` takes its group's — without this every host and script is untyped and silently unchecked):

| group | member type |
|---|---|
| hooks | Hook |
| hosts | Host |
| identifiers | Identifier |
| metrics | Metric |
| organisations | Organization |
| paths | Path |
| people | Person |
| scripts | Script |
| settings | Setting |
| software | Software |
| standards | Standard |


### `failure_modes`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `local`

> Was 10 values at ratio 0.67, including one-offs like invisible_to_intune and presents_as_auth_failure. Those are descriptions, not categories — they belong in `note`. What is actually worth querying is HOW LATE you find out. This is FMEA's detectability dimension (Severity x Occurrence x Detection).

Governs: `relations[].mode`, `risks[].mode`

Values: `silent`, `delayed`, `overt`, `adversarial`

### `impact`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `iso31000`

> A standard qualitative severity scale (ISO 31000 / NIST SP 800-30 style).

Governs: `risks`, `risks[].impact`

Values: `critical`, `high`, `medium`, `low`

### `keywords`

Governance: **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.

Maps to: `skos:Concept`

> Seeded from tier-0 keyword candidates (code spans, camelCase, SCREAMING_SNAKE). A corpus-wide registry catches settings keys renamed between product versions.

Governs: `keywords`

Values: _(empty by design — terms arrive by promotion, never by seeding)_

### `lang`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `dcterms:language`

> BCP 47 tags, closed to the set this corpus actually uses so en-GB and en_GB and english cannot coexist.

Governs: `lang`

Values: `en-GB`, `en-US`, `fr-FR`, `de-DE`, `zh-CN`, `ja-JP`

### `link_categories`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `dcterms:references`

Governs: `links`, `links[].group_name`

Values: `product_docs`, `vendor_and_legal`, `platform`, `ecosystem`, `internal`, `standards`

### `refs`

Governance: **ref** — no term list; validated by referential integrity instead.

Maps to: `dcterms:references`

Governs: _nothing yet — declared, unbound_

Fields: `ref`, `backs`, `see_also`, `control`, `protects`

Rules:

- `ref`/`backs` must resolve to an anchor in the same document.
- `see_also` must resolve to an existing page id; unresolved -> see_also_unresolved.
- `control`/`protects` must resolve to an id in this document's registers OR to a named entity in it — a control is usually a thing (a hook script, a settings key, a sandbox), not a decision. Measured on the worked example: 11 of 12 controls named an entity and exactly one named a decision id.
- A pointer that cannot be checked is SKIPPED, never passed: `see_also` is only resolved when the caller supplies the corpus id set.

### `relation_predicates`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `local`

> Edge types are what queries traverse. An open predicate set produces mitigates / mitigate / helps_mitigate / reduces_risk_of as four distinct edges. Keep this list small and force qualifiers to carry nuance.

Governs: `relations`, `relations[].p`

Values: `runs_on`, `installed_via`, `requires`, `governs`, `controls`, `overrides`, `constrains`, `enforces`, `mitigates`, `targets`, `bypasses`, `produces`, `covers`, `discloses`, `designates`, `alternative_to`, `ordered_after`

| value | maps to |
|---|---|
| mitigates | stix:mitigates |
| requires | dcterms:requires |
| targets | stix:targets |


Aliases (normalised on the **write** path, so a variant can never coexist with its canonical form):

| variant | canonical | qualifiers added |
|---|---|---|
| threatens | targets |  |


Qualifiers (where the nuance lives, so the term list can stay small):

| qualifier | type | meaning |
|---|---|---|
| conditional | bool | holds only under a stated future change |
| exclusive | bool | is the ONLY source for |
| mode | ref -> `failure_modes` |  |
| negated | bool | collapses does_not_* / cannot_* variants |


### `requirement_level`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `local`

Governs: _nothing yet — declared, unbound_

Values: `mandatory`, `recommended`, `conditional`, `optional`, `excluded`

### `review_cadence`

Governance: **closed** — an unknown value is a hard failure. Changing this list is a schema change.

Maps to: `local`

Governs: `review_cadence`

Values: `monthly`, `quarterly`, `biannual`, `annual`, `on_change`, `none`

### `subtype`

Governance: **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.

Maps to: `dcterms:type`

> Secondary document facets; corpus-shaped, so proposed then promoted.

Governs: `subtype`

Values: _(empty by design — terms arrive by promotion, never by seeding)_

### `tags`

Governance: **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.

Maps to: `skos:Concept`

> Hard-closing tags at document 1 means guessing the corpus shape. Leaving them open means 35 tags x 400 docs of singletons, which is the same as no tags. So: propose, then promote.

Governs: `tags`

Values: _(empty by design — terms arrive by promotion, never by seeding)_

Aliases (normalised on the **write** path, so a variant can never coexist with its canonical form):

| variant | canonical | qualifiers added |
|---|---|---|
| ec2 | aws-ec2 |  |
| otel | opentelemetry |  |
| redhat-8 | rhel8 |  |
| rhel-8 | rhel8 |  |
| rocky-linux | rocky-linux-8 |  |
| rocky8 | rocky-linux-8 |  |


Broader (`skos:broader` — a search for the broader term returns the narrower ones):

| narrower | broader |
|---|---|
| aws-ec2 | aws |
| opentelemetry | observability |
| rhel8 | linux |
| rocky-linux-8 | rhel8 |


Rules:

- Extractor may only emit tags already in the registry.
- Unknown tags go to `tags_proposed`, never to `tags`.
- A proposal is promoted when it appears on >= 3 documents.
- Promotion writes an alias entry for every variant seen.

Health metric: **singleton_rate** share of registry tags used on exactly 1 document, **threshold** > 0.40 means the vocabulary is failing; run a merge pass

### `topics`

Governance: **registry** — an unknown value lands in `<field>_proposed` and is promoted once it appears on `promote_at` documents.

Maps to: `dcterms:subject`

> Broad subject areas. Registry so the corpus decides the granularity.

Governs: `topics`

Values: _(empty by design — terms arrive by promotion, never by seeding)_

## Lint thresholds

The numbers behind the per-document and corpus-wide gates. Every one
of them is a judgement that had to be written down somewhere; here is
where.

| threshold | value |
|---|---|
| corpus_min_docs | `5` |
| coverage_thin | `0.25` |
| facet_fail | `0.75` |
| facet_min_uses | `8` |
| facet_warn | `0.45` |
| promote_at | `3` |
| similar_ok | `review_cadence:annual\|biannual` |
| singleton_rate_max | `0.4` |
| synonym_similarity | `0.78` |

## Standards prefixes

Every `maps_to` above resolves through one of these. Reuse before
inventing: `local` means no standard models the concept, and that
layer is deliberately kept small.

| prefix | namespace |
|---|---|
| dcterms | `http://purl.org/dc/terms/` |
| iso31000 | `https://www.iso.org/standard/65694.html` |
| local | `doc2md` |
| madr | `https://adr.github.io/madr/` |
| prov | `http://www.w3.org/ns/prov#` |
| schema | `https://schema.org/` |
| skos | `http://www.w3.org/2004/02/skos/core#` |
| stix | `https://docs.oasis-open.org/cti/stix/v2.1/` |

## Demoted

Fields deleted rather than governed, kept here so the decision is not
re-litigated. A field whose values are all distinct is documentation,
not a facet.

- **`entities.role`** — delete; every value was unique. Replace with free-text `note` (unindexed) or a per-type closed subrole where one genuinely exists.
  Observed distinct/used ratio: `1.0`.
  _A field whose values are all distinct is documentation, not a facet. Do not index it, and do not let a model believe it is choosing from a set._
