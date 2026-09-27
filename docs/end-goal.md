---
title: End goal — a document that loses nothing, an index whose every link is walkable
kind: doc
layer: backend
status: stable
owner: TBD
public_api: none
tags: [charter, losslessness, validation, knowledge-graph, north-star]
summary: The project charter: the two jobs (a document that loses nothing, an index whose every link is walkable), what the system must prove about itself, and what done looks like.
---

# End goal

**Convert documents to Markdown as close to lossless as possible — where
"lossless" means content, not pixels — and make the system itself prove how
lossless each conversion was.** That is the whole project, pure and simple.
Everything else (bundles, gates, evals, the SDK) exists to serve those two
sentences.

## Two jobs, two standards

The product does two things, and they are not the same job. Conflating them is
how a good conversion gets judged by an index's standards, or an index gets
excused because the conversion was perfect.

| | **Job 1 — the document** | **Job 2 — the knowledge** |
|---|---|---|
| Artifact | `document.md` (+ `structure.json`, `images/`) | `knowledge.json` + the `meta` block |
| Standard | **nothing may be LOST** | **nothing may be CLAIMED that cannot be followed** |
| Size | same information as the source | deliberately smaller — it is an index |
| Gate | `token_recall == 1.0`, hard, off-lane `best-effort` | every record cites a real section; every edge joins two declared nodes |
| Failure | markdown withheld | record refused, field left `pending` |
| Code | `backend.ingest`, `backend.validate` | `backend.kb` |

They share one direction of dependency (`kb` reads `ingest`'s primitives,
never the reverse) and nothing else, so the two can be worked on in parallel
and a change to one cannot quietly move the other's numbers. §1-§2 below are
Job 1's rules; §3 is Job 2's.

**The graph is never a summary of the document.** It sits on top of markdown
that kept every word, so compactness is not a virtue here and a smaller graph
is better only when what left it was not real. Job 2 may never be used as an
excuse to drop something from Job 1.

## 1. Lossless means content

Every token of **body content** in the source must survive into the Markdown:
prose, headings, list items, table cells, footnotes, figure labels, link
targets. Two things are deliberately *not* content:

- **Page furniture** — running headers, footers, page numbers, logos,
  watermarks, slide chrome. Furniture repeated onto every page would pollute
  the Markdown (and any RAG index built on it), so dropping it is correct
  behavior — but the drop is always *deliberate and visible*, never an
  accident: excluded by documented policy on both sides of the measurement
  (the office lane never walks header/footer parts; the PDF metric carries
  furniture buckets), with the output contract's named-warning vocabulary as
  the mechanism whenever a drop is conditional rather than structural.
- **Presentation** — fonts, colors, absolute positions. Markdown is a
  projection; we keep the structure (heading levels, table grids, list
  nesting, links), not the styling.

Structure **is** content. A document whose words all survive but whose
headings flattened, whose table rows scrambled, or whose diagram labels
vanished into an image placeholder is not lossless in any useful sense. So
structural fidelity is measured too (outline coverage, per-document eval
expectations), and text trapped in figures/diagrams is tracked as a named debt
(`figure_text_tokens`) until a transposition stage recovers it.

## 2. Self-validation: the converter never grades its own homework

A fidelity claim is only worth what measured it. The rules:

1. **Converter-blind ground truth.** The grader extracts source content
   through a path that shares no traversal logic with the converter. Office:
   an exhaustive structure-blind XML walk vs the structural converter walk.
   PDF: poppler `pdftotext` vs docling. A converter bug then shows up as
   `recall < 1.0` instead of zeroing both sides.
2. **Measured, not assumed.** Multiset token recall (count-aware, so a
   dropped table is charged even when its vocabulary survives elsewhere),
   backed by char-n-gram content recall where re-tokenization (hyphenation,
   ligatures) would lie. Every number lands in `report.json`.
3. **Honest verdicts, hard where provable.** Where a complete ground truth
   exists (Office XML), the gate is `token_recall == 1.0` exactly — a miss
   *fails* and the lossy Markdown is withheld. Where it cannot exist (PDF has
   no semantic source tree; a scan has no text layer at all), the gate is a
   measured floor with an explained gap, `gate: best-effort`, `status:
   degraded` when real loss is detected, and an explicit "unmeasured" note
   when nothing independent exists to measure against. Never an invented pass.
4. **Gates are ratchets.** They may be extended and hardened, never weakened.
   Changes to shared metric code (`tokenize`, `coverage`) require re-running
   the full office corpus before merge.
5. **Evals encode the truth.** The synthetic (fictional Nimbus/Kestrel)
   corpus pins the *measured current behavior* per document — shortfalls carry
   a `_note`/TODO, never a papered-over pass, and a missing tool is a SKIP
   row, never a silent one.

## 3. The knowledge layer: every link must be walkable

`document.md` answers "what does this document say?". `knowledge.json` answers
"which documents matter, and how do they connect?" — so that a reader, or an
LLM tracing the index, can get to the right document without reading all of
them. That makes it an **index onto content that is already complete**, never a
replacement for it.

An index earns its place only if following a link tells you something. So the
rules are about *walkability*, not volume:

1. **An edge must mean something when you follow it.** A relation's endpoints
   must each name an entity the document declares, so the edge joins two nodes
   rather than a node and a sentence. An endpoint naming a phrase is an edge to
   nowhere: it reads as a triple and is invisible as a graph, because nothing
   will ever link to it — and a vault built from it grows a junk note per
   phrase. Measured before the rule existed, on a real model answer over the
   real corpus: 10 of 54 endpoints (19%) resolved. Enforced at generation *and*
   on accept, because a rule stated only in a validator shows up as a silent
   81% drop.
2. **Every record cites the section that asserts it.** A claim that cannot be
   quoted cannot be checked or repaired, so a record whose `ref` is missing or
   names no published anchor is discarded. An unciteable claim is worse than no
   claim.
3. **Edges accumulate across documents, and that is the point.** Nodes are
   matched by one normalised identity rule (`norm_key`), corpus-wide, so a
   second document mentioning the same register joins the same node rather than
   creating a twin. A knowledge base that spells one concept three ways has
   three nodes where it needs one.
4. **Omission is not zero, here too.** A field the pipeline cannot fill is
   recorded `pending`, never guessed. `unknown` is always a legal answer,
   because a gap stays visibly outstanding while a wrong label silently becomes
   a new term.
5. **A model is the CEILING, never the floor.** Identity, provenance, links,
   title and abstract are filled with no model at all, from evidence the
   pipeline already measured. A model may improve a value the pipeline GUESSED
   and never one it READ, and may never write an authored-only field.
6. **The controlled vocabulary is an INPUT, not a constant.** See below.

### 3.1 The vocabulary is the deployment's own decision

Every value a model may store comes from a controlled vocabulary
(`config/vocab.yaml`): the document types, entity types, relation predicates,
link categories and impact scales. That list is **domain-specific and belongs
to whoever is running doc2md**, not to doc2md. A silicon specification and a
payroll policy do not share a notion of what an entity is, and a vocabulary
that tries to serve both serves neither: terms nothing uses are noise a model
must choose from, while a missing term forces a true statement to be dropped or
mislabelled.

So doc2md ships a **starting** vocabulary and owes its user three things:

- **Evidence for tuning it.** `kb_lint` reports, corpus-wide, which terms no
  document uses, which are used once, which pairs are near-duplicates, and what
  share of relation endpoints resolve. Measured on the current corpus: 9 of 14
  entity types and 11 of 17 predicates are used by nothing — and no entity type
  can express a *state*, which is why a true statement like "driving `rst_n`
  low forces the safe default" has no representable object. Both halves of that
  mismatch are findings the tooling must hand a user, not something they should
  have to discover by reading rejections.
- **A safe way to change it.** New terms arrive as *proposals*, are promoted
  only once enough documents use them, and the vocabulary carries a `version`
  that every stored value records — so a widening is a visible, dated event and
  never a silent reinterpretation of what is already stored.
- **Honesty about the cost of a narrow one.** A missing term must show up as a
  refused record with a named reason, never as a quietly relabelled one.

**Definition of done for the vocabulary is not "complete" — it is FITTED:**
every governed term is used by some document, every recurring proposal has been
promoted or deliberately rejected, and the endpoint-resolution rate is high
because the vocabulary can express the relations the corpus actually contains.

## 4. What done looks like

| Lane | Definition of done | State |
|------|--------------------|-------|
| Office (docx/xlsx/pptx + legacy via soffice) | Deterministic, provably content-lossless: recall == 1.0 hard gate against converter-blind XML walk | **Closed.** 544/544 corpus docs at exactly 1.0; regressions guarded by CI + evals |
| Text (md/txt/…) | Verbatim passthrough, fenced where needed | **Closed.** |
| PDF (digital) | Docling structure (real heading levels, tables, figures) with text completeness measured against the independent text layer; furniture excluded on evidence, not trust; explained gap for every missing token | **Active — see `docs/roadmap.md`** |
| PDF (scanned) | VLM/OCR transcription with figure extraction, hallucination-gated where any independent signal exists, honestly `degraded` otherwise | Active |
| Figures / vector diagrams / tables-as-images | Transposed to text by a VLM, precision-checked against the text layer's region words, burning down `figure_text_tokens` | Active |
| HTML | Needs an independent extractor before its numbers mean anything | Open |
| **Job 2 — knowledge** | Every record cited to a published anchor; every relation endpoint resolving to a declared node; the vocabulary FITTED to the corpus (no unused governed term, no recurring unpromoted proposal); tier 0/1 filled with no model, tier 2 filled or honestly `pending` | **Active — the machinery is built and guarded; the vocabulary is unfitted and `decisions`/`open_questions` are filled by nothing** |

The product shape at the end: the **bundle contract**'s four-artifact shape
(`document.md` + `structure.json` + `report.json` + `images/`,
cross-referenced by `doc_id` / `image_id` / `markdown_sha256`) is stable —
its content-level details evolve deliberately via
`docs/design/output-contract.md` — and **doc2md is an installable
package/SDK** (`pip install doc2md[pdf]`, `doc2md.convert(path)`), obeying the
project-keel template, so other tools integrate the pipeline instead of
reimplementing it.
