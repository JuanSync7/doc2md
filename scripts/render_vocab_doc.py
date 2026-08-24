#!/usr/bin/env python3
"""
title: Render the controlled-vocabulary reference
kind: script
layer: backend
summary: Generate docs/reference/vocabulary.md from config/vocab.yaml + the kb field inventory, or check the committed copy for drift.

The vocabulary is data, so its reference is generated, never transcribed — a
hand-written copy of a term list is wrong the first time anyone promotes a tag.
``--check`` is what the drift test calls: it re-renders and compares, so adding a
term without regenerating fails CI instead of silently rotting the docs.

    python3 scripts/render_vocab_doc.py            # write docs/reference/vocabulary.md
    python3 scripts/render_vocab_doc.py --check    # exit 1 if the committed copy differs
"""
from __future__ import print_function

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.ingest import parse_block                      # noqa: E402
from backend.kb import (FIELDS, REF_FIELDS, in_knowledge, record_vocab,  # noqa: E402
                        group_vocab, vocab_path, DOCUMENT_FILE, KNOWLEDGE_FILE)

DEFAULT_OUT = os.path.join(_REPO, "docs", "reference", "vocabulary.md")

# Keys of a vocabulary block that are rendered by name; anything else is surfaced
# generically so a new key in vocab.yaml cannot go undocumented by omission.
_KNOWN = ("governance", "maps_to", "rationale", "note", "values", "value_maps_to",
          "aliases", "alias_qualifiers", "broader", "qualifiers", "group_types",
          "rules", "health_metric", "fields")

_REGIME = {
    "closed": "**closed** — an unknown value is a hard failure. Changing this list "
              "is a schema change.",
    "registry": "**registry** — an unknown value lands in `<field>_proposed` and is "
                "promoted once it appears on `promote_at` documents.",
    "ref": "**ref** — no term list; validated by referential integrity instead.",
}


def _esc(text):
    # type: (object) -> str
    """Make a value safe inside a markdown table cell."""
    return ("%s" % (text,)).replace("|", "\\|").replace("\n", " ").strip()


def _scalar(value):
    # type: (object) -> str
    """Render a value the way the YAML/JSON it came from spells it."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "%s" % (value,)


def _code_list(values):
    # type: (list) -> str
    if not values:
        return "_(empty by design — terms arrive by promotion, never by seeding)_"
    return ", ".join("`%s`" % _scalar(v) for v in values)


def _para(text):
    # type: (object) -> str
    return " ".join(("%s" % (text,)).split())


def _field_rows(data):
    # type: (dict) -> list
    rows = []
    for f in FIELDS:
        where = KNOWLEDGE_FILE if in_knowledge(f.name) else DOCUMENT_FILE
        gov = ""
        if f.vocab:
            gov = (data.get(f.vocab) or {}).get("governance", "")
        rows.append((f.name, str(f.tier), f.kind,
                     "yes" if f.authored_only else "",
                     ("`%s`" % f.vocab) if f.vocab else "",
                     gov, "`%s`" % where, f.maps_to or "", _para(f.note)))
    return rows


def _table(header, rows):
    # type: (tuple, list) -> list
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_esc(c) for c in r) + " |")
    out.append("")
    return out


def _vocab_section(name, block, data):
    # type: (str, dict, dict) -> list
    out = ["### `%s`" % name, ""]
    gov = block.get("governance", "")
    out.append("Governance: %s" % _REGIME.get(gov, "`%s`" % gov))
    if block.get("maps_to"):
        out.append("")
        out.append("Maps to: `%s`" % block["maps_to"])
    for key in ("rationale", "note"):
        if block.get(key):
            out.append("")
            out.append("> %s" % _para(block[key]))
    used_by = [f.name for f in FIELDS if f.vocab == name]
    for owner, subs in (("relations", record_vocab("relations")),
                        ("decisions", record_vocab("decisions")),
                        ("risks", record_vocab("risks")),
                        ("entities", group_vocab("entities")),
                        ("links", group_vocab("links"))):
        for sub, voc in sorted((subs or {}).items()):
            if voc == name:
                used_by.append("%s[].%s" % (owner, sub))
    out.append("")
    out.append("Governs: %s" % (", ".join("`%s`" % u for u in used_by) or "_nothing "
               "yet — declared, unbound_"))
    if "values" in block:
        out.append("")
        out.append("Values: %s" % _code_list(block.get("values") or []))
    if block.get("fields"):
        out.append("")
        out.append("Fields: %s" % _code_list(block["fields"]))
    if block.get("value_maps_to"):
        out.append("")
        out.extend(_table(("value", "maps to"),
                          sorted(block["value_maps_to"].items())))
    if block.get("aliases"):
        out.append("")
        out.append("Aliases (normalised on the **write** path, so a variant can never "
                   "coexist with its canonical form):")
        out.append("")
        rows = []
        for variant, canon in sorted(block["aliases"].items()):
            quals = (block.get("alias_qualifiers") or {}).get(variant) or {}
            rows.append((variant, canon,
                         ", ".join("`%s: %s`" % (k, _scalar(v))
                                   for k, v in sorted(quals.items())) or ""))
        out.extend(_table(("variant", "canonical", "qualifiers added"), rows))
    if block.get("broader"):
        out.append("")
        out.append("Broader (`skos:broader` — a search for the broader term returns "
                   "the narrower ones):")
        out.append("")
        out.extend(_table(("narrower", "broader"),
                          sorted(block["broader"].items())))
    if block.get("qualifiers"):
        out.append("")
        out.append("Qualifiers (where the nuance lives, so the term list can stay "
                   "small):")
        out.append("")
        rows = []
        for q, spec in sorted(block["qualifiers"].items()):
            spec = spec or {}
            kind = spec.get("type") or ("ref -> `%s`" % spec["ref"]
                                        if spec.get("ref") else "")
            rows.append((q, kind, _para(spec.get("note", ""))))
        out.extend(_table(("qualifier", "type", "meaning"), rows))
    if block.get("group_types"):
        out.append("")
        out.append("Group defaults (a member with no explicit `type` takes its "
                   "group's — without this every host and script is untyped and "
                   "silently unchecked):")
        out.append("")
        out.extend(_table(("group", "member type"),
                          sorted(block["group_types"].items())))
    if block.get("rules"):
        out.append("")
        out.append("Rules:")
        out.append("")
        for r in block["rules"]:
            out.append("- %s" % _para(r))
    if block.get("health_metric"):
        out.append("")
        hm = block["health_metric"]
        out.append("Health metric: %s" % ", ".join(
            "**%s** %s" % (k, _para(v)) for k, v in sorted(hm.items())))
    extra = [k for k in sorted(block) if k not in _KNOWN]
    if extra:
        out.append("")
        out.append("Other keys: %s" % ", ".join("`%s`" % k for k in extra))
    out.append("")
    return out


def render(data):
    # type: (dict) -> str
    """The whole reference as markdown."""
    version = data.get("version", "?")
    vocab_names = [k for k in data
                   if isinstance(data.get(k), dict) and "governance" in data[k]]
    by_regime = {}
    for n in vocab_names:
        by_regime.setdefault(data[n].get("governance", "?"), []).append(n)

    out = [
        "---",
        "title: Controlled vocabulary reference",
        "kind: doc",
        "layer: backend",
        "status: draft",
        "owner: TBD",
        "public_api: none",
        "tags: [reference, vocabulary, metadata, governance, generated]",
        "summary: Every governed field and every term it may take — generated from "
        "config/vocab.yaml, so it cannot drift.",
        "---",
        "",
        "# Controlled vocabulary reference",
        "",
        "**Generated — do not edit by hand.** Regenerate with",
        "`python3 scripts/render_vocab_doc.py`; CI runs the same script with",
        "`--check`, so a term added to `config/vocab.yaml` without regenerating this",
        "file fails the build. The source of truth is",
        "[`config/vocab.yaml`](../../config/vocab.yaml); the contract that explains",
        "*why* each field is governed the way it is lives in",
        "[`document-metadata.md`](../design/document-metadata.md).",
        "",
        "Vocabulary revision: **v%s** (`vocab_version` in every written block)." % version,
        "",
        "## The three governance regimes",
        "",
    ]
    for regime in ("closed", "registry", "ref"):
        names = sorted(by_regime.get(regime, []))
        out.append("- %s" % _REGIME[regime])
        out.append("  Vocabularies: %s" % (", ".join("`%s`" % n for n in names)
                                           or "_none_"))
    out += [
        "",
        "Registry vocabularies ship **empty on purpose**: admitting terms by promotion",
        "is the whole point, so seeding one here would guess the corpus shape at",
        "document zero — the exact failure the regime exists to avoid.",
        "",
        "## Field inventory",
        "",
        "Every field the metadata layer knows about. **tier** 0 = deterministic,",
        "1 = derived by a fixed rule, 2 = a model proposes and a human corrects.",
        "**authored** marks the accountability and safety boundaries no enrichment",
        "pass may ever write. **file** is where the field is stored at schema v2 — a",
        "field lives in `%s` iff it is a `records`/`groups` field and is not"
        % KNOWLEDGE_FILE,
        "authored-only.",
        "",
    ]
    out += _table(("field", "tier", "kind", "authored", "vocabulary", "regime",
                   "file", "maps to", "note"), _field_rows(data))

    out += ["## Record and group sub-keys", "",
            "A record list carries its own governed sub-keys — without this a",
            "relation's `p` would be checked while its `mode` qualifier, which is",
            "where the nuance was deliberately pushed, would not.", ""]
    rows = []
    for owner in ("relations", "decisions", "risks"):
        for sub, voc in sorted((record_vocab(owner) or {}).items()):
            rows.append(("%s[].%s" % (owner, sub), "`%s`" % voc, "record"))
    for owner in ("entities", "links"):
        for sub, voc in sorted((group_vocab(owner) or {}).items()):
            rows.append(("%s (%s)" % (owner, sub), "`%s`" % voc, "group"))
    out += _table(("sub-key", "vocabulary", "shape"), rows)

    out += ["## Referential fields", "",
            "Pointers, graded by resolution rather than by membership: %s."
            % ", ".join("`%s`" % f for f in REF_FIELDS), ""]

    out += ["## Vocabularies", ""]
    for name in sorted(vocab_names):
        out += _vocab_section(name, data[name], data)

    lint = data.get("lint") or {}
    if lint:
        out += ["## Lint thresholds", "",
                "The numbers behind the per-document and corpus-wide gates. Every one",
                "of them is a judgement that had to be written down somewhere; here is",
                "where.", ""]
        rows = []
        for k in sorted(lint):
            v = lint[k]
            rows.append((k, ", ".join("`%s`" % _scalar(x) for x in v)
                         if isinstance(v, list) else "`%s`" % _scalar(v)))
        out += _table(("threshold", "value"), rows)

    std = data.get("standards") or {}
    if std:
        out += ["## Standards prefixes", "",
                "Every `maps_to` above resolves through one of these. Reuse before",
                "inventing: `local` means no standard models the concept, and that",
                "layer is deliberately kept small.", ""]
        out += _table(("prefix", "namespace"),
                      [(k, "`%s`" % v) for k, v in sorted(std.items())])

    dem = data.get("demoted") or {}
    if dem:
        out += ["## Demoted", "",
                "Fields deleted rather than governed, kept here so the decision is not",
                "re-litigated. A field whose values are all distinct is documentation,",
                "not a facet.", ""]
        for name in sorted(dem):
            b = dem[name] or {}
            out.append("- **`%s`** — %s" % (name, _para(b.get("action", ""))))
            if b.get("ratio_observed") is not None:
                out.append("  Observed distinct/used ratio: `%s`."
                           % b["ratio_observed"])
            if b.get("principle"):
                out.append("  _%s_" % _para(b["principle"]))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    # type: (list) -> int
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--vocab", default="",
                    help="vocabulary file (default $DOC2MD_VOCAB / config/vocab.yaml)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="where to write (default docs/reference/vocabulary.md)")
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the committed copy is stale")
    args = ap.parse_args(argv)

    # encoding= on EVERY open: both the vocabulary and the generated reference hold
    # non-ASCII prose (em dashes, the first at byte 9 of config/vocab.yaml), and a
    # bare open() decodes with the LOCALE's preferred encoding. On a bare 3.6 host
    # with no UTF-8 LANG — the exact environment the office lane is specified to run
    # on — that is ANSI_X3.4-1968 and the read dies with UnicodeDecodeError. Do NOT
    # "fix" this with errors="replace" or a binary read: --check compares the
    # rendered text against the committed copy as str, so any lossy decode or
    # newline translation would report STALE over a file that is current.
    path = args.vocab or vocab_path()
    with open(path, encoding="utf-8") as fh:
        data = parse_block(fh.read())
    text = render(data)

    if args.check:
        try:
            with open(args.out, encoding="utf-8") as fh:
                current = fh.read()
        except IOError:
            print("MISSING %s — run scripts/render_vocab_doc.py" % args.out,
                  file=sys.stderr)
            return 1
        if current != text:
            print("STALE %s — config/vocab.yaml changed; run "
                  "scripts/render_vocab_doc.py" % args.out, file=sys.stderr)
            return 1
        print("ok %s is current (vocab v%s)" % (args.out, data.get("version")))
        return 0

    d = os.path.dirname(args.out)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s (%d fields, %d vocabularies)"
          % (args.out, len(FIELDS),
             len([k for k in data if isinstance(data.get(k), dict)
                  and "governance" in data[k]])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
