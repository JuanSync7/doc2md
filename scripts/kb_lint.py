#!/usr/bin/env python3
"""Lint document metadata against the controlled vocabulary, per document AND corpus-wide.

Reads every markdown file under a bundle root (``document.md`` in a normal bundle,
but any ``*.md`` is graded), merges its ``meta`` front-matter block with the
``knowledge.json`` sidecar beside it — the sidecar belongs to ``document.md`` alone —
and grades the merged view with ``backend.kb``:

PER DOCUMENT
    CARDINALITY      is each facet actually a facet, or is it documentation?
    MEMBERSHIP       is every value a governed term (closed), or a proposal (registry)?
    TYPE HYGIENE     did an unquoted YAML 1.1 keyword get retyped into an enum?
    INTEGRITY        do ``ref``/``backs``/``see_also``/``control`` pointers resolve?

CORPUS-WIDE (nothing below is answerable one document at a time)
    REGISTRY HEALTH  singleton rate, and proposals ready for promotion
    IDENTITY         two documents claiming one id; documents nothing can link to
    SYNONYMY         `rhel-8` here and `rhel_8` there — one concept, two index entries
    ENTITIES         one thing spelled two ways is two nodes in the graph
    GRAPH            dead see_also targets, orphans, edges to undeclared entities
    SCHEMA SKEW      which documents a version bump has to backfill
    COVERAGE         a field on 3% of documents: conditional, or quietly broken?
    VOCABULARY       terms the corpus never uses, and synonyms in the term list itself

The corpus half is the point. Every one of those defects leaves each individual
document internally consistent, so per-file linting can never surface them: a
duplicated id is not wrong in either document, and `Docker` in one file and `docker`
in another are each locally fine while the graph quietly grows two nodes.

This script is a thin WALKER: every rule lives in ``backend.kb``. It writes nothing
unless ``--json`` is given, so it is safe to run twice and safe to run in CI.

Exit 0 when no document and no corpus check has an ERROR finding and no facet is
worse than the vocabulary's fail threshold (``--strict`` fails on warnings too).

Usage:
  python3 scripts/kb_lint.py --bundles data/bundles
  python3 scripts/kb_lint.py --bundles data/bundles --json data/kb_lint.json --strict
  python3 scripts/kb_lint.py --bundles data/bundles --suggest-aliases
"""
import argparse
import json
import os
import sys
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.ingest import (render_block, split_front_matter,   # noqa: E402
                            YamlSubsetError)
from backend.kb import (body_anchors, check_schema_bindings,   # noqa: E402
                        corpus_findings, corpus_report, in_knowledge,
                        knowledge_payload, lint_document, load_vocab, merge_meta,
                        meta_collisions, Finding,
                        DOCUMENT_FILE, KNOWLEDGE_FILE, META_KEY, PROVENANCE_KEY,
                        ERROR, VERDICT_SPARSE)


def finding_file(where):
    # type: (str) -> str
    """Which of the two files an operator has to open to fix this finding.

    The linter grades one MERGED mapping, which is what keeps every rule in
    `backend.kb` unaware of the storage split — but a person still has to be told
    which file the offending field is actually in, or the split trades a token cost
    for a scavenger hunt.
    """
    parts = ("%s" % (where or "")).split(".")
    head = parts[0].split("[")[0]
    if head in (META_KEY, PROVENANCE_KEY) and len(parts) > 1:
        head = parts[1].split("[")[0]
    return KNOWLEDGE_FILE if in_knowledge(head) else DOCUMENT_FILE


def find_docs(root):
    # type: (str) -> list
    """Every markdown file under ``root``, sorted for a stable report order."""
    out = []  # type: list
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return out


def read_knowledge(doc_dir):
    # type: (str) -> tuple
    """``(payload, error)`` from a bundle's ``knowledge.json``, header keys stripped.

    Absent is not an error — a bundle that has never been enriched simply has no
    sidecar. Present-but-unreadable IS one, because the alternative is grading a
    document on half its metadata and calling the result clean.
    """
    path = os.path.join(doc_dir, KNOWLEDGE_FILE)
    if not os.path.isfile(path):
        return ({}, "")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=OrderedDict)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return (None, "%s not parseable: %s" % (KNOWLEDGE_FILE, e))
    if not isinstance(data, dict):
        return (None, "%s is %s, expected an object"
                % (KNOWLEDGE_FILE, type(data).__name__))
    return (knowledge_payload(data), "")


def read_doc(path):
    # type: (str) -> tuple
    """``(front_matter, meta, anchors, error, duplicated)`` for one markdown file.

    ``meta`` is the MERGED view — front-matter descriptors plus the ``knowledge.json``
    payload — because every rule in ``backend.kb`` was written against one mapping
    and the storage split must not leak into any of them.

    A file that cannot be read, or whose front matter is outside the supported YAML
    subset, comes back with an error STRING rather than raising: one malformed
    document must not stop the corpus scan, and it must not vanish from it either.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return (None, None, None, "unreadable (%s)" % e, [])
    except UnicodeDecodeError as e:
        # Not an OSError. Letting it escape would abort the whole corpus scan on one
        # mis-encoded file, which is exactly what this function promises not to do.
        return (None, None, None, "not valid UTF-8 (%s)" % e, [])
    try:
        fm, body = split_front_matter(text)
    except YamlSubsetError as e:
        return (None, None, None, "front matter not parseable: %s" % e, [])
    anchors = body_anchors(body)
    # The sidecar belongs to `document.md`, not to the DIRECTORY. A bundle may hold a
    # notes.md or a README beside its document.md, and binding by directory handed
    # each of them the same entities and relations — double-counting every entity and
    # inventing id collisions between a document and its own neighbour.
    know, know_err = ({}, "")
    if os.path.basename(path) == DOCUMENT_FILE:
        know, know_err = read_knowledge(os.path.dirname(path))
    if know_err:
        return (fm, None, anchors, know_err, [])
    meta = fm.get(META_KEY)
    if meta is not None and not isinstance(meta, dict):
        return (fm, None, anchors,
                "`%s` is %s, expected a mapping" % (META_KEY, type(meta).__name__), [])
    if meta is None and not know:
        return (fm, None, anchors, "", [])
    return (fm, merge_meta(meta or {}, know), anchors, "",
            meta_collisions(meta or {}, know))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Lint document metadata against the controlled vocabulary, "
                    "per document and corpus-wide (cardinality, membership, type "
                    "hygiene, referential integrity, registry health).")
    ap.add_argument("--bundles", default=os.path.join(_REPO, "data", "bundles"),
                    help="bundle root to walk for document.md (default data/bundles)")
    ap.add_argument("--vocab", default="",
                    help="vocabulary file (default $DOC2MD_VOCAB / config/vocab.yaml)")
    ap.add_argument("--only", action="append", default=[],
                    help="lint ONLY this doc id or file basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N documents")
    ap.add_argument("--json", default="",
                    help="also write the full machine-readable report to this path")
    ap.add_argument("--strict", action="store_true",
                    help="warnings fail the run too (default: only errors do)")
    ap.add_argument("--quiet", action="store_true",
                    help="summary and failures only; suppress per-document detail")
    ap.add_argument("--suggest-aliases", action="store_true",
                    help="print paste-ready `aliases:` entries for the spelling "
                         "collisions found (does not edit the vocabulary)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.bundles):
        ap.error("bundle root not found: %s" % args.bundles)
    vocab = load_vocab(args.vocab or None)
    missing_bindings = check_schema_bindings(vocab)
    if missing_bindings:
        # A closed field whose vocabulary is absent silently accepts anything, so a
        # lint run against such a vocabulary would report a clean corpus it never
        # actually checked. Refuse instead of failing open.
        ap.error("vocabulary does not declare: %s" % ", ".join(missing_bindings))

    paths = find_docs(args.bundles)
    total_found = len(paths)
    narrowed = False
    if args.only:
        want = set(args.only)
        paths = [p for p in paths
                 if os.path.basename(p) in want
                 or os.path.basename(os.path.dirname(p)) in want]
        if not paths:
            ap.error("--only matched no document: %s" % ", ".join(sorted(want)))
        narrowed = True                      # same effect as --limit on corpus scope
    if args.limit:
        paths = paths[:args.limit]
    deferred = total_found - len(paths)
    narrowed_by = "--only" if narrowed else ("--limit" if deferred else "")

    # Pass 1: read everything, so corpus-wide checks (see_also, registry health)
    # have the full id set before any document is graded.
    docs = []  # type: list
    unreadable = []  # type: list
    for path in paths:
        fm, meta, anchors, err, dup = read_doc(path)
        if err:
            unreadable.append((path, err))
            continue
        docs.append({"path": path, "fm": fm, "meta": meta, "anchors": anchors,
                     "duplicated": dup})
    # A document that could not be READ truncates the corpus exactly as --only does,
    # and the corpus gates cannot tell the difference: a see_also pointing at the
    # unreadable document reads as a dead link, and the newest schema version may be
    # sitting inside it. Treating only the flags as narrowing let a single tab-indented
    # front matter manufacture corpus findings the corpus does not have.
    partial = narrowed or bool(deferred) or bool(unreadable)
    known_ids = set()
    for d in docs:
        for key in ("id", "uid"):
            val = (d["meta"] or {}).get(key)
            if isinstance(val, str) and val:
                known_ids.add(val)
    if partial:
        # A partial corpus cannot answer a corpus-wide question. Resolving `see_also`
        # against a truncated id set would report live links as dead, and the
        # deferral notice goes to stderr while the findings go to stdout — so a log
        # that keeps only stdout would show confident nonsense. Skip, and say so.
        # `--only` narrows exactly as `--limit` does; it just does not "defer".
        known_ids = None

    msg = "kb-lint documents=%d" % total_found
    if deferred:
        # Name the flag that actually narrowed the run: an operator told `--limit`
        # deferred documents they never capped goes looking for a bug in the wrong
        # place.
        msg += "  (%s deferred %d more)" % (narrowed_by, deferred)
    msg += "  with-metadata=%d  unreadable=%d  vocab=v%s" % (
        sum(1 for d in docs if d["meta"] is not None), len(unreadable), vocab.version)
    print(msg, file=sys.stderr)

    # Pass 2: grade.
    # Relative, like every other path in the report — a consumer that keys on
    # `path` must not have to know which entries are absolute host paths.
    report = {"documents": [], "corpus": {}, "unreadable":
              [{"path": os.path.relpath(p, args.bundles), "error": e}
               for p, e in unreadable]}
    n_err = n_warn = 0
    worst_ratio = 0.0
    worst_where = ""
    n_nometa = 0
    for d in docs:
        rel = os.path.relpath(d["path"], args.bundles)
        if d["meta"] is None:
            # "Not looked at" must not render the same as "looked at and clean".
            n_nometa += 1
            report["documents"].append({
                "path": rel, "errors": 0, "warnings": 0, "facets": [],
                # Same 5-element shape as a graded row: a consumer must not
                # have to branch on document state to read a findings list.
                "findings": [["no-metadata", "info", rel,
                              "no `%s` block yet — run enrich_metadata.py"
                              % META_KEY, DOCUMENT_FILE]],
                "proposals": {}})
            if not args.quiet:
                print("%s  no metadata block (run enrich_metadata.py)" % rel)
            continue
        res = lint_document(d["meta"], vocab, anchors=d["anchors"],
                            known_ids=known_ids)
        for name in d.get("duplicated") or []:
            # Two live copies of one field. `merge_meta` picks a winner so readers
            # see one view, and the next enrichment run rewrites the loser away — so
            # a state that silently costs data has to be reported here, because the
            # merged mapping graded above cannot show that both ever existed.
            res["findings"].append(Finding(
                "field-duplicated", ERROR, name,
                "present in BOTH document.md and %s — two sources of truth; front "
                "matter wins the merge and the sidecar copy is overwritten by the "
                "next enrichment run" % KNOWLEDGE_FILE))
            res["errors"] += 1
        n_err += res["errors"]
        n_warn += res["warnings"]
        for row in res["facets"]:
            # Only a graded facet can move the run's worst ratio. A sparse row is
            # reported per document but must never fail a build on a sample of one.
            if row.verdict != VERDICT_SPARSE and row.ratio > worst_ratio:
                worst_ratio, worst_where = row.ratio, "%s %s" % (rel, row.name)
        report["documents"].append({
            "path": rel,
            "errors": res["errors"],
            "warnings": res["warnings"],
            "facets": [list(r) for r in res["facets"]],
            # A fifth element, appended: the file the field lives in. Positional
            # access to the first four is unchanged for anything already reading it.
            "findings": [list(f) + [finding_file(f.where)] for f in res["findings"]],
            "proposals": res["proposals"],
        })
        # Under --strict a warning DECIDES the exit code, so --quiet must not hide
        # it: a CI log showing no findings beside a non-zero exit is unactionable.
        # --quiet trims noise, it never trims the evidence for the verdict.
        loud = ERROR if not args.strict else ""
        if args.quiet and not res["errors"] and not (args.strict and res["warnings"]):
            continue
        if res["findings"]:
            print("%s  errors=%d warnings=%d" % (rel, res["errors"], res["warnings"]))
            for f in res["findings"]:
                if args.quiet and loud and f.severity != loud:
                    continue
                if args.quiet and not loud and f.severity == "info":
                    continue
                where = f.where
                if finding_file(where) == KNOWLEDGE_FILE:
                    # Name the file, or the split trades a token cost for a
                    # scavenger hunt through two files per document.
                    where = "%s:%s" % (KNOWLEDGE_FILE, where)
                print("  %-5s %-40s %s" % (f.severity.upper(), where, f.message))

    graded = [d for d in docs if d["meta"] is not None]
    corpus = corpus_report([d["meta"] for d in graded], vocab)
    report["corpus"] = corpus
    report["partial"] = partial
    print("\nREGISTRY HEALTH (corpus-wide)%s"
          % ("  [PARTIAL: --only/--limit is in effect, so see_also resolution is "
             "skipped and promotion counts are incomplete]" if partial else ""))
    failing = 0
    for name, row in corpus.items():
        flag = "FAIL" if row["failing"] else "ok"
        failing += 1 if row["failing"] else 0
        print("  %-10s terms=%3d singletons=%3d rate=%.2f (max %.2f) proposed=%3d  %s"
              % (name, row["terms_used"], row["singletons"],
                 row["singleton_rate"], row["singleton_rate_max"],
                 len(row["proposed"]), flag))
        if row["promote"]:
            print("      promote (>= %d docs): %s"
                  % (int(vocab.threshold("promote_at", 3)), ", ".join(row["promote"])))

    # The gates no single document can run. `partial` is passed through rather than
    # worked around: on a truncated walk the whole-corpus checks are SKIPPED and say
    # so, because a subset can only miss a collision but would actively invent dead
    # links, a wrong "current" schema version and dead vocabulary terms.
    cres = corpus_findings([{"path": os.path.relpath(d["path"], args.bundles),
                             "meta": d["meta"]} for d in graded],
                           vocab, partial=partial)
    report["corpus_findings"] = [list(f[:4]) + [list(f.detail)]
                                 for f in cres["findings"]]
    report["corpus_metrics"] = cres["metrics"]
    report["corpus_skipped"] = cres["skipped"]
    report["alias_suggestions"] = cres["aliases"]
    print("\nCORPUS GATES (identity, synonymy, entities, graph, skew, coverage)")
    if not cres["findings"]:
        print("  clean")
    for f in cres["findings"]:
        # Same rule as the per-document loop: whatever decides the exit code prints.
        if args.quiet and f.severity == "info":
            continue
        if args.quiet and not args.strict and f.severity != ERROR:
            continue
        print("  %-5s %-30s %s" % (f.severity.upper(), f.where, f.message))
        for d in f.detail:
            print("        %s" % (d,))

    for path, err in unreadable:
        print("  UNREADABLE %s %s" % (os.path.relpath(path, args.bundles), err))

    json_failed = False
    if args.json:
        tmp = "%s.tmp.%d" % (args.json, os.getpid())
        try:
            parent = os.path.dirname(os.path.abspath(args.json))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(report, indent=2, ensure_ascii=False,
                                    default=str) + "\n")
            os.replace(tmp, args.json)
            print("wrote %s" % args.json, file=sys.stderr)
        except OSError as e:
            # The lint results are already computed; losing the RESULT line to a bad
            # --json path would throw away the run's actual answer.
            json_failed = True
            try:
                os.unlink(tmp)
            except OSError:
                pass
            print("  [json] could not write %s: %s" % (args.json, e),
                  file=sys.stderr)

    if args.suggest_aliases:
        # Printed, never written. An alias is a permanent claim that two strings mean
        # the same thing, so a human pastes it in — the linter does not edit the
        # vocabulary it is grading against.
        #
        # Rendered through the codec that will PARSE it rather than hand-formatted:
        # a corpus can contain a tag with a colon in it, and `a:b: canonical` is not
        # a mapping entry. Hand-formatting emits that silently and the paste fails
        # later, in the user's editor, with no clue where it came from. render_block
        # refuses it here instead, and the refusal is the useful answer — a spelling
        # the vocabulary format cannot express has to be fixed at the source, not
        # aliased away.
        emit, reject = OrderedDict(), []
        for fname in sorted(cres["aliases"]):
            table = OrderedDict()
            for variant in sorted(cres["aliases"][fname]):
                canonical = cres["aliases"][fname][variant]
                try:
                    render_block(OrderedDict([(variant, canonical)]))
                except YamlSubsetError:
                    reject.append("%s: %s -> %s" % (fname, variant, canonical))
                    continue
                table[variant] = canonical
            if table:
                emit[fname] = OrderedDict([("aliases", table)])
        if emit:
            print("\n# paste into config/vocab.yaml")
            print(render_block(emit))
        elif not reject:
            print("\n# no spelling collisions — nothing to alias")
        for line in reject:
            print("# NOT aliasable (not a valid vocabulary key) — fix at the "
                  "source: %s" % line)

    fail_ratio = float(vocab.threshold("facet_fail", 0.75))
    over = worst_ratio > fail_ratio
    if json_failed:
        # The exit code turns on this, so it belongs on the same stream as the
        # verdict. Reported only on stderr, a log that keeps stdout showed a clean
        # RESULT line beside a non-zero exit and nothing explaining it.
        print("  [json] report could not be written to %s — see stderr" % args.json)
    # `corpus_err`/`corpus_warn`, deliberately not `corpus_errors`/`corpus_warnings`:
    # a CI check grepping for `errors=0` would otherwise match inside the corpus
    # counter and read a failing run as clean.
    print("\nRESULT documents=%d graded=%d no-metadata=%d errors=%d warnings=%d "
          "worst_ratio=%.2f%s registry_failing=%d corpus_err=%d "
          "corpus_warn=%d unreadable=%d%s"
          % (len(report["documents"]), len(report["documents"]) - n_nometa,
             n_nometa, n_err, n_warn, worst_ratio,
             (" (%s)" % worst_where) if worst_where else "", failing,
             cres["errors"], cres["warnings"],
             len(unreadable), "  [PARTIAL]" if partial else ""))
    # A failing registry prints FAIL above; --strict is what makes it bite. It is
    # corpus health rather than a defect, so it never fails a default run — but
    # printing FAIL and then exiting 0 unconditionally made the word meaningless.
    failed = (bool(n_err) or bool(cres["errors"]) or over or bool(unreadable)
              or json_failed
              or (args.strict and (n_warn > 0 or cres["warnings"] > 0
                                   or failing > 0)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
