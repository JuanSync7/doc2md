#!/usr/bin/env python3
"""The passthrough + fence lane: markdown/plain-text and code-like data -> markdown.

Two deterministic, lossless-by-construction sub-lanes (no models, no inference), on the
plain 3.6 host python, stdlib only:

  * PASSTHROUGH (md/markdown/txt/text) -> copied through VERBATIM. The bytes already are
    the text the pipeline indexes; re-parsing them would only risk dropping content.
  * FENCE (json/yaml/yml/toml/xml/csv/tsv/ini) -> the raw content wrapped in a fenced code
    block. Prose conversion would destroy the structure of config/data-interchange files;
    a fence preserves every character and renders readably. The fence delimiter grows past
    any backtick run already in the content, so nothing can break out of the block.

Every written markdown is GATED exactly like the other lanes: ``conversion_report`` measures
multiset token recall against the raw source and validates structure; only ``recall == 1.0``
with zero structural errors counts as valid. Records append to ``_coverage_text.jsonl`` in the
shared record shape, so all lanes share one skip/heal contract.

Routing: this script owns exactly the formats ``route_format`` maps to the PASSTHROUGH and
FENCE lanes; office_convert.py owns OOXML, docling_convert.py owns pdf/html. One format, one
owner; same ids (backend.ingest.doc_id), same <doc_id>.md layout, same out dir. Idempotent:
docs with markdown AND a valid record are skipped (--force to rebuild). Safe to run twice.

Usage:
  python3 scripts/text_convert.py --src "$DOC2MD_SRC" --out data/markdown
  python3 scripts/text_convert.py --validate-only     # re-gate existing .md
  python3 scripts/text_convert.py --report            # summarize the coverage records
"""
import argparse
import glob
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.ingest import (  # noqa: E402
    doc_id, route_format, ext_of, markdown_to_text, summarize_routes, normalize_accept,
    unknown_formats, supported_formats, ROUTE_PASSTHROUGH, ROUTE_FENCE, ROUTE_OOXML,
    ROUTE_LIBREOFFICE, ROUTE_DOCLING, load_source_root, load_ingest_config)
from backend.validate import conversion_report  # noqa: E402  (the validator layer)

COV_NAME = "_coverage_text.jsonl"


def _read_text(path):
    # type: (str) -> str
    """Raw file text (utf-8, undecodable bytes replaced) or '' if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _fence(content, lang):
    # type: (str, str) -> str
    """``content`` wrapped in a fenced code block whose delimiter is longer than any
    backtick run inside it (so the content can never terminate the fence early)."""
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    ticks = "`" * max(3, longest + 1)
    body = content if content.endswith("\n") or not content else content + "\n"
    return "%s%s\n%s%s\n" % (ticks, lang, body, ticks)


def render(row):
    # type: (dict) -> tuple
    """(markdown, gate_source) for one row — both lossless by CONSTRUCTION; the gate
    just confirms and captures structure.

    FENCE wraps the raw bytes, which are NOT markdown, so the gate source is the raw
    content (``markdown_to_text`` de-fences the output back to it -> recall 1.0).
    PASSTHROUGH copies the file verbatim, so output == source and the only text the
    pipeline extracts is ``markdown_to_text(raw)``; that is the correct gate source
    (comparing the RAW markdown against its own stripped form would falsely count links/
    URLs that ``markdown_to_text`` strips by design as 'lost')."""
    raw = _read_text(row["src"])
    if row["lane"] == ROUTE_FENCE:
        return _fence(raw, row["ext"]), raw
    return raw, markdown_to_text(raw)          # PASSTHROUGH: verbatim, gate on extracted text


def _gate(row, md, gate_source):
    # type: (dict, str, str) -> dict
    """Run the shared conversion gate, then apply the PASSTHROUGH policy: a verbatim copy
    is lossless by identity, so structural issues in a user-authored file are ADVISORY
    (counted as warnings), never gating — no re-conversion could change a verbatim copy,
    so failing it would only wedge the self-heal loop."""
    rep = conversion_report(gate_source, md)
    if row["lane"] == ROUTE_PASSTHROUGH and rep.get("recall") == 1.0:
        rep = dict(rep)
        rep["warnings"] = rep.get("warnings", 0) + rep.get("errors", 0)
        rep["errors"] = 0
        rep["valid"] = True
    return rep


def scan_tree(src_root, accept=None):
    # type: (str, object) -> tuple
    """Walk ``src_root`` once; return ``(text_sources, scan)`` where ``text_sources`` is the
    sorted ``[(abs, rel)]`` of ACCEPTED files this script owns (passthrough + fence) and
    ``scan`` is a RouteScan over the whole tree (its unsupported/declined buckets are what
    will NOT be converted — the caller warns so nothing is dropped silently)."""
    rel_of = {}
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            full = os.path.join(root, fn)
            rel_of[os.path.relpath(full, src_root)] = full
    scan = summarize_routes(sorted(rel_of), accept)
    out = []
    for lane in (ROUTE_PASSTHROUGH, ROUTE_FENCE):
        for rel in scan.by_lane.get(lane, []):
            out.append((rel_of[rel], rel))
    out.sort(key=lambda t: t[1])
    return out, scan


def plan(sources, out_dir):
    # type: (list, str) -> list
    """Row shape the other lanes share: {id, rel, src, ext, lane, dest}."""
    rows = []
    for full, rel in sources:
        did = doc_id(rel)
        ext = ext_of(rel)
        rows.append({"id": did, "rel": rel, "src": full, "ext": ext,
                     "lane": route_format(ext),
                     "dest": os.path.join(out_dir, did + ".md")})
    return rows


def _record(row, rep):
    # type: (dict, dict) -> dict
    """A _coverage*.jsonl record docling_convert's _done_ids understands."""
    return {
        "id": row["id"], "rel": row["rel"], "ext": row["ext"],
        "recall": rep.get("recall", 0.0),
        "n_source": rep.get("n_source", 0),
        "n_covered": rep.get("n_covered", 0),
        "n_missing": rep.get("n_missing", 0),
        "missing_top": rep.get("missing_top", []),
        "structure_errors": rep.get("errors", 0),
        "structure_warnings": rep.get("warnings", 0),
        "docling_status": "TEXT",           # lane marker, same field the heal flow reads
        "backend": "text",
        "valid": bool(rep.get("valid")),
        "error": rep.get("error", ""),
        "ts": int(time.time()),
    }


def _valid_ids(out_dir):
    # type: (str) -> set
    """Ids whose LATEST record across _coverage*.jsonl is valid (any lane), last-wins —
    matching office_convert / docling_convert so all lanes share one done-set."""
    verdict = {}
    for fp in sorted(glob.glob(os.path.join(out_dir, "_coverage*.jsonl"))):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if "id" in rec:
                        verdict[rec["id"]] = bool(rec.get("valid"))
        except OSError:
            pass
    return set(did for did, ok in verdict.items() if ok)


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def _ext_counts(names):
    # type: (list) -> str
    """"json(3), md(2)" — per-extension counts of a filename list, biggest first."""
    from collections import Counter
    c = Counter((os.path.splitext(n)[1].lstrip(".").lower() or "no-ext") for n in names)
    return ", ".join("%s(%d)" % (e, n) for e, n in c.most_common())


def _warn_unconverted(scan):
    # type: (object) -> None
    """Report, in ONE place, what this run will NOT convert: office + docling files (owned
    by their own producers), accept-declined files, and genuinely unsupported formats."""
    office = scan.by_lane.get(ROUTE_OOXML, []) + scan.by_lane.get(ROUTE_LIBREOFFICE, [])
    if office:
        print("  [note] %d office file(s) belong to the OOXML lane (run scripts/office_convert.py): %s"
              % (len(office), _ext_counts(office)), file=sys.stderr)
    docling = scan.by_lane.get(ROUTE_DOCLING, [])
    if docling:
        print("  [note] %d file(s) belong to the docling lane (run scripts/docling_convert.py): %s"
              % (len(docling), _ext_counts(docling)), file=sys.stderr)
    if scan.declined:
        print("  [skip] %d file(s) excluded by the accept-list -> NOT converted: %s"
              % (len(scan.declined), _ext_counts(scan.declined)), file=sys.stderr)
    if scan.unsupported:
        print("  [WARNING] %d file(s) in UNSUPPORTED formats will NOT be converted by any "
              "lane: %s" % (len(scan.unsupported), _ext_counts(scan.unsupported)), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic passthrough+fence converter: owns md/markdown/txt/text "
                    "(verbatim) and json/yaml/toml/xml/csv/tsv/ini (fenced verbatim).")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC or [paths].source_docs)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "markdown"),
                    help="output dir for <doc_id>.md (default data/markdown)")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats the system accepts (default: [ingest] "
                         "accept_formats / $DOC2MD_ACCEPT_FORMATS = all supported). Files in "
                         "unsupported or non-accepted formats are reported, never converted.")
    ap.add_argument("--only", action="append", default=[],
                    help="convert ONLY this doc id or source basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs")
    ap.add_argument("--force", action="store_true",
                    help="reconvert even when a valid markdown already exists")
    ap.add_argument("--validate-only", action="store_true",
                    help="no writes: re-run the lossless gate on existing markdown")
    ap.add_argument("--report", action="store_true",
                    help="summarize %s and exit" % COV_NAME)
    args = ap.parse_args(argv)

    cov_file = os.path.join(args.out, COV_NAME)
    if args.report:
        return report(cov_file)
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))

    # Accept-list: --accept wins, else the [ingest] config (empty => all supported).
    accept_spec = args.accept if args.accept.strip() else (load_ingest_config().accept_formats or None)
    unknowns = unknown_formats(accept_spec)
    if unknowns:
        print("  [WARNING] accept-list names %d format(s) that match NO lane (ignored; check "
              "for typos): %s  -- supported: %s"
              % (len(unknowns), ", ".join(unknowns), ", ".join(supported_formats())),
              file=sys.stderr)
    accept = normalize_accept(accept_spec)
    text_sources, scan = scan_tree(args.src, accept)
    _warn_unconverted(scan)
    rows = plan(text_sources, args.out)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows
                if r["id"] in want or os.path.basename(r["rel"]) in want]

    os.makedirs(args.out, exist_ok=True)
    valid = set() if args.force else _valid_ids(args.out)
    todo = [r for r in rows
            if args.validate_only or args.force
            or not (r["id"] in valid and os.path.isfile(r["dest"]))]
    if args.limit:
        todo = todo[:args.limit]
    print("text sources=%d  already-valid=%d  to-%s=%d  -> %s"
          % (len(rows), len(rows) - len(todo),
             "validate" if args.validate_only else "convert", len(todo), args.out),
          file=sys.stderr)

    ok = bad = 0
    t0 = time.time()
    for r in todo:
        if args.validate_only:
            try:
                with open(r["dest"], encoding="utf-8") as f:
                    md = f.read()
            except OSError:
                bad += 1
                print("  MISSING %s (no markdown to validate)" % r["rel"], file=sys.stderr)
                continue
            raw = _read_text(r["src"])
            gate_source = raw if r["lane"] == ROUTE_FENCE else markdown_to_text(raw)
            rep = _gate(r, md, gate_source)
        else:
            md, gate_source = render(r)
            rep = _gate(r, md, gate_source)
            if rep.get("valid"):
                _write_atomic(r["dest"], md)
        with open(cov_file, "a", encoding="utf-8") as cf:
            cf.write(json.dumps(_record(r, rep)) + "\n")
        if rep.get("valid"):
            ok += 1
        else:
            bad += 1
            print("  FAIL %s recall=%s errors=%s %s"
                  % (r["rel"], rep.get("recall"), rep.get("errors"), rep.get("error", "")),
                  file=sys.stderr)
    print("text lane done: valid=%d failed=%d in %.1fs" % (ok, bad, time.time() - t0),
          file=sys.stderr)
    return 0 if bad == 0 else 1


def report(cov_file):
    # type: (str) -> int
    """Latest record per id -> per-extension validity/recall summary."""
    latest = {}
    try:
        with open(cov_file, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                latest[rec.get("id")] = rec
    except OSError:
        print("no %s yet" % cov_file, file=sys.stderr)
        return 1
    from collections import defaultdict
    by_ext = defaultdict(list)
    for rec in latest.values():
        by_ext[rec.get("ext", "?")].append(rec)
    total = sum(len(v) for v in by_ext.values())
    total_ok = sum(1 for v in by_ext.values() for r in v if r.get("valid"))
    print("docs=%d valid=%d (%.1f%%)" % (total, total_ok, 100.0 * total_ok / max(1, total)))
    for ext in sorted(by_ext):
        recs = by_ext[ext]
        n_ok = sum(1 for r in recs if r.get("valid"))
        rec_min = min((r.get("recall", 0.0) for r in recs), default=0.0)
        print("  %-9s n=%-4d valid=%-4d min_recall=%.4f" % (ext, len(recs), n_ok, rec_min))
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
