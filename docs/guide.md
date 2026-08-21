---
title: doc2md guide — what it produces, how it runs, how it proves it
kind: doc
layer: backend
status: draft
owner: TBD
public_api: none
tags: [guide, overview, walkthrough, bundles, verification]
summary: End-to-end walkthrough of doc2md with a real worked example — the four artifacts, the four stages, with and without a model, and how each claim is measured.
---

# doc2md guide

The narrative one. Read this to understand how the pipeline works; use
[`reference/`](reference/) to look up a specific flag, key or term.

doc2md turns documents into Markdown **and measures how much survived**. That
second half is the product: a converter that claims losslessness is worth nothing;
one that computes it against a ground truth it does not control is worth
something.

---

## 1. What you get

One **bundle** per document, the same shape from every lane:

```
<out>/<doc_id>/
  document.md      # the converted body + front matter
  structure.json   # the heading tree, with token counts, images and links
  report.json      # what was measured (validator only — no model output)
  knowledge.json   # the extracted graph payload (only once enrichment has run)
  images/          # <sha16>.<ext>, byte-verified
<out>/manifest.jsonl   # a row per document per run
<out>/runs.jsonl       # a row per run: argv, code, resolved config, counts
```

The founding rule is that **a consumer never re-parses the markdown to learn a
fact**. Hierarchy, losslessness and image placement are outputs, not inferences.

`doc_id` = `sha1(source_relpath)[:16]`. `image_id` = `sha256(bytes)[:16]`, which
appears in exactly three places that re-collate without ambiguity — the body
reference, the `structure.json` node, and the filename — and deduplicates reused
figures for free.

### A worked example

A real `IT_runbook.docx` (title, five sections, a numbered procedure with a
sub-step, an on-call table, a bold warning, two figures, a hyperlink, a
`Confidential` header):

```console
$ python3 scripts/build_bundle.py --src ./docs-in --out ./bundles
tokenizer: char-estimate/4
office sources=1  already-built=0  to-build=1  -> ./bundles
bundles: ok=1 degraded=0 failed=0 in 0.0s
```

**`document.md`** — front matter, then the body:

```markdown
---
doc_id: "602f96677581c18b"
source_format: "docx"
lane: "office"
source_relpath: "IT_runbook.docx"
source_sha256: "c57c6f15…"
markdown_sha256: "09c1689b…"
converter: "doc2md-ooxml/0.1.0+81e94a7"   # version + commit, derived
lossless: "true"
structure: "structure.json"
report: "report.json"
images: "images/"
generated_run: "20260821T125543Z"
source_title: "Payments Gateway IT Runbook"
---

# Payments Gateway IT Runbook

## Procedure

1. Acknowledge the PagerDuty incident and open a bridge.
1. Check pod health: kubectl get pods -n payments -l app=payments-gateway.
   1. Inspect the last 500 lines of the gateway log for AuthSidecarTimeout entries.
1. Drain the unhealthy pod and let the deployment reschedule it.

| Role | Name | Phone | Slack channel |
| --- | --- | --- | --- |
| Primary on-call | Ravi Anand | +44 20 7946 0011 | #payments-oncall |
```

The heading tree, the GFM table, the hyperlink, both figures as content-addressed
references and the list nesting all survive. The `Confidential` header does not —
that is deliberate, and it is **named** in the report rather than silently
dropped.

**`report.json`** — the measurement:

```json
{
  "status": "ok",
  "losslessness": {"method": "ooxml-ground-truth", "token_recall": 1.0, "gate": "pass"},
  "content": {"chars": 2582, "tokens": 644, "headings": 6, "tables": 1, "images": 2, "links": 1},
  "savings": {"source_chars": 13833, "markdown_chars": 2582, "reduction_ratio": 5.36},
  "structure": {"coverage": {"content_lines": 36, "uncovered_lines": 0, "gate": "pass"}},
  "images": {"referenced": 2, "extracted": 2, "missing": 0, "orphans": 0, "verified": 2, "gate": "pass"},
  "warnings": [{"code": "dropped_headers_footers", "parts": 2, "chars": 78}],
  "decisions": [{"code": "lane_selected", "chose": "ooxml", "reason": "routed by source extension"}],
  "run": {"entrypoint": "build_bundle", "run_id": "20260821T125543Z",
          "argv": ["--src", "<src>", "--out", "<out>"],
          "code": {"name": "doc2md", "version": "0.1.0", "commit": "81e94a7…", "dirty": false},
          "host": {"python": "3.6.8"}, "config_ref": "runs.jsonl#20260821T125543Z"}
}
```

That `run{}` block is what makes the conversion **repeatable**, not just measured:

```console
$ python3 scripts/replay_run.py --report bundles/602f96677581c18b/report.json \
      --src ./docs-in --out /tmp/replay --execute --compare
no divergences: same code, same interpreter, same resolved settings
REPRODUCED 602f96677581c18b markdown_sha256 26aafd2740f8644d
```

Point it at a machine with a different commit, a dirty checkout or a changed
`DOC2MD_*` value and it names each difference **before** running anything. A replay
that quietly produced a different answer would be worse than no replay at all.

**`structure.json`** — each node carries `line_span`, `self_tokens`,
`subtree_tokens`, plus full nodes for images and links:

```json
{"id": "sec-0005", "level": 2, "title": "Procedure",
 "line_span": [34, 49], "self_tokens": 171, "subtree_tokens": 171,
 "images": [{"image_id": "b41b6fa3cb6b720f", "line": 45, "bytes": 275,
             "width": 140, "height": 64, "caption": null}]}
```

The token partition is exact: `49 + 83 + 102 + 161 + 171 + 78 = 644 = total`. A
chunker gets size-bounded windows with breadcrumb titles for free.

Every key of every artifact is catalogued in
[`reference/output-schema.md`](reference/output-schema.md).

---

## 2. How it runs — four stages

```
build_bundle.py      convert → validate → outline → write     (deterministic, py3.6, stdlib)
caption_bundles.py   figure captions                          (VLM overlay)
enrich_metadata.py   document metadata, tiers 0/1/2           (model optional)
kb_lint.py           per-document + corpus-wide vocabulary    (read-only)
```

Stages 2–4 are **detachable overlays**. They never touch the body, and
`markdown_sha256` covers the **body only** — so metadata can be added, rewritten
or reordered forever without invalidating a hash, a line span or an image index.
That is what makes a corpus backfillable without re-converting it.

Each stage is safe to run twice: re-runs skip completed work and produce
byte-identical files.

### Lanes

| Lane | Formats | How | Runtime |
|---|---|---|---|
| `ooxml` | docx, xlsx, pptx | Direct XML → Markdown, deterministic | 3.6, stdlib |
| `libreoffice` | doc, xls, ppt, odt, rtf | Vendored soffice → OOXML sibling → ooxml lane | 3.6 |
| passthrough / fence | md, txt, csv, json | Verbatim / fenced | 3.6, stdlib |
| `pdf` / `html` | pdf, html | docling (+ RapidOCR for scans), raw-vocab repair, text-layer fallback | 3.9+ |

---

## 3. Without a model, and with one

### Without (the default)

```console
$ python3 scripts/enrich_metadata.py --bundles ./bundles
kb-enrich documents=1  model=none  vocab=v1
kb-enrich: ok=0 incomplete=1 fields-pending=20
```

**Ten fields fill**, all tier 0 and 1: `schema_version`, `vocab_version`, `uid`,
`version`, `source{}`, `extraction{}`, `id`, `slug`, `word_count`,
`reading_time_minutes` — each with a `_provenance` entry recording its tier and
source.

**Twenty stay `pending`**: `title`, `abstract`, `type`, `subtype`, `lang`, `tags`,
`keywords`, `topics`, `audience`, `aliases`, `entities`, `relations`, `decisions`,
`risks`, `open_questions`, `links`, `see_also`, `prerequisites`, `out_of_scope`,
`short_title`. Plus the nine `authored_only` fields, which no run may ever fill.

No `knowledge.json` is written — the payload is empty, so there is no sidecar.
`doc_meta.gate` reads `disabled`, not `failed`: **a lossless document must not
look degraded because nobody has classified it yet.**

> Today the deterministic tier leaves more on the table than it should — `title`
> is sitting in `source_title` and never written, the lede paragraph is spanned in
> `structure.json` and never becomes `abstract`, and verified outbound links are
> harvested and then discarded. `quality-plan.md` P5.1–P5.3 fix all three.

### With

```console
$ python3 scripts/enrich_metadata.py --bundles ./bundles \
      --vlm-url http://127.0.0.1:8000/v1/chat/completions
```

Any OpenAI-compatible chat endpoint, over plain `urllib`, temperature 0. The model
is not asked an open question — it is handed an **enum-constrained spec**: for
`type`, the ten legal values plus `"unknown"`; for `relations`, the seventeen legal
predicates and the governed sub-keys.

Then governance decides what may be kept:

| Regime | Model answers an unknown term | Result |
|---|---|---|
| **closed** | rejected, reason `not-in-vocabulary` | never stored; logged verbatim so you can see what it keeps inventing |
| **registry** | proposed | lands in `<field>_proposed`, promoted into the field once 3 documents agree |
| **ref** | accepted verbatim | graded later by referential integrity |

`"unknown"` is a **distinct outcome from rejection** (`declined-unknown`), so an
honest decline does not pollute the "which term is it inventing" signal. A model
with no escape hatch is a model forced to guess.

**Authored always wins**, enforced three ways: nine accountability fields
(`owner`, `confidentiality`, `status`, the review dates) are refused outright; a
value with no provenance counts as authored; and a value stamped `generated` whose
hash no longer matches counts as a human edit and is protected. That last case is
the one tier 2 is *defined* by.

Answers are cached on `sha(body)` + `sha(model + prompt + vocab_version)`, so a
re-run costs nothing and a vocabulary bump re-asks exactly the affected documents.

The full field inventory and every term is in
[`reference/vocabulary.md`](reference/vocabulary.md).

---

## 4. How it proves it

The converter never grades its own homework.

### The hard gate — office lanes

A **second, independent** code path re-reads the OOXML and extracts every text
run, knowing nothing about the converter's structural walk. The markdown is
stripped back to prose and the two token **multisets** are compared — count-aware,
so a dropped table is charged even when its vocabulary survives elsewhere.

`token_recall == 1.0` exactly, or the gate fails and **the lossy markdown is
withheld**. 544/544 corpus documents.

A corollary worth stating plainly: this measures *tokens*. List indentation,
emphasis and code fences are not tokens, so `lossless: "true"` is a token claim,
not a formatting one. Widening the gate to cover structure is
[`quality-plan.md`](quality-plan.md) P3.

### The other gates

- **Outline coverage** measures *backwards* from the built tree: union every
  `line_span`, then classify every non-blank body line as covered, intentional
  TOC furniture, or **lost**. It grades the builder's product, not its intent.
- **Image integrity** re-hashes every written file and checks the hash matches its
  own content-addressed name, sweeps orphans, and counts referenced vs extracted.
  Body images are HTML-comment sentinels the text gate cannot see, so without this
  a dropped figure would be an invisible loss.
- **Named drops.** Every deliberate exclusion emits a warning code with a count —
  `dropped_headers_footers` carries the parts and the characters. Page furniture is
  correctly dropped; "dropped" must never read the same as "absent".

### The PDF lane cannot claim a pass, and does not

There is no ground-truth semantic tree for a PDF, so the report measures coverage
against poppler's own text layer (a tool sharing no code with docling),
de-boilerplated and with figure-region text excluded apple-to-apple. `gate` is
`best-effort`, and the report **structurally coerces** any non-office gate off
`pass` — a buggy writer cannot talk the report into a claim its lane cannot
support. A scan has no independent layer at all, and the report says so in words
rather than inventing a number.

### The linters

`kb_lint` grades one document (cardinality, vocabulary membership, hygiene,
referential integrity) and — separately — the **corpus**, for the questions a
single document is structurally incapable of answering: a duplicated `id`, one
entity spelled two ways, a term nobody uses, a field that quietly stopped being
populated. Those are not defects in any one file, which is exactly why they
survive per-file linting indefinitely.

---

## 5. Where this is going

The honest current state, per output dimension, and what is planned, lives in
[`quality-plan.md`](quality-plan.md) — including its rubric for what "done" means
checkably. The two headline items:

- **Structure is content** (`end-goal.md` §1), so the losslessness gate is being
  widened to grade list nesting, emphasis and code alongside tokens.
- **A run should be repeatable from its report** — the switches used, the resolved
  configuration, the code identity and every decision taken, recorded per run.

`roadmap.md` plans the orthogonal work: PDF-lane fidelity, the SDK, and
project-keel compliance.
