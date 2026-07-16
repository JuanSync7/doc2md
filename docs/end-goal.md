---
title: End goal — content-lossless conversion, self-validated
kind: doc
layer: backend
status: stable
owner: TBD
public_api: none
tags: [charter, losslessness, validation, north-star]
summary: The project charter: what lossless means here, what the system must prove about itself, and what done looks like.
---

# End goal

**Convert documents to Markdown as close to lossless as possible — where
"lossless" means content, not pixels — and make the system itself prove how
lossless each conversion was.** That is the whole project, pure and simple.
Everything else (bundles, gates, evals, the SDK) exists to serve those two
sentences.

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

## 3. What done looks like

| Lane | Definition of done | State |
|------|--------------------|-------|
| Office (docx/xlsx/pptx + legacy via soffice) | Deterministic, provably content-lossless: recall == 1.0 hard gate against converter-blind XML walk | **Closed.** 544/544 corpus docs at exactly 1.0; regressions guarded by CI + evals |
| Text (md/txt/…) | Verbatim passthrough, fenced where needed | **Closed.** |
| PDF (digital) | Docling structure (real heading levels, tables, figures) with text completeness measured against the independent text layer; furniture excluded on evidence, not trust; explained gap for every missing token | **Active — see `docs/roadmap.md`** |
| PDF (scanned) | VLM/OCR transcription with figure extraction, hallucination-gated where any independent signal exists, honestly `degraded` otherwise | Active |
| Figures / vector diagrams / tables-as-images | Transposed to text by a VLM, precision-checked against the text layer's region words, burning down `figure_text_tokens` | Active |
| HTML | Needs an independent extractor before its numbers mean anything | Open |

The product shape at the end: the **bundle contract**'s four-artifact shape
(`document.md` + `structure.json` + `report.json` + `images/`,
cross-referenced by `doc_id` / `image_id` / `markdown_sha256`) is stable —
its content-level details evolve deliberately via
`docs/design/output-contract.md` — and **doc2md is an installable
package/SDK** (`pip install doc2md[pdf]`, `doc2md.convert(path)`), obeying the
project-keel template, so other tools integrate the pipeline instead of
reimplementing it.
