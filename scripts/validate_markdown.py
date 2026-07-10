#!/usr/bin/env python3
"""Validate a markdown tree: structure for every file, losslessness for office docs.

Two layers, both from ``backend.ingest`` policy (this file is only I/O):
  * STRUCTURE (every .md): ``validate_markdown`` — consistent pipe-table columns,
    closed fences/front matter, no leaked OOXML tags, no control/replacement chars.
  * LOSSLESSNESS (office docs, when --src is given): ``conversion_report`` against
    the exhaustive OOXML ground truth — token recall must be exactly 1.0.

Prints a per-file line for every finding and a summary; --json writes a full
report. Exit 0 only when no file has structural ERRORS and every checked office
doc is lossless (warnings don't fail unless --strict). Safe to run twice.

Usage:
  python3 scripts/validate_markdown.py --md-dir data/markdown_ooxml --src "$DOC2MD_SRC"
  python3 scripts/validate_markdown.py --md-dir data/markdown          # structure only
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _HERE)   # sibling script import (office_convert.read_parts)

from backend.ingest import (  # noqa: E402
    doc_id, route_format, ROUTE_OOXML, ooxml_source_text, load_source_root)
from backend.validate import validate_markdown, conversion_report  # noqa: E402
from office_convert import read_parts, strip_front_matter  # noqa: E402  (sibling script)


def _md_files(md_dir):
    # type: (str) -> list
    return sorted(fn for fn in os.listdir(md_dir)
                  if fn.endswith(".md") and not fn.startswith("_"))


def _office_index(src_root):
    # type: (str) -> dict
    """doc_id -> (abs_path, ext) for every OOXML-lane source file."""
    out = {}
    if not src_root or not os.path.isdir(src_root):
        return out
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            ext = os.path.splitext(fn)[1].lstrip(".").lower()
            if route_format(ext) == ROUTE_OOXML:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, src_root)
                out[doc_id(rel)] = (full, ext)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a markdown tree "
                                             "(structure + office losslessness).")
    ap.add_argument("--md-dir", default=os.path.join(_REPO, "data", "markdown"),
                    help="markdown tree to validate (default data/markdown)")
    ap.add_argument("--src", default=load_source_root(),
                    help="source docs root; enables the lossless check for office docs")
    ap.add_argument("--json", default="",
                    help="also write the full per-file report to this JSONL path")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files")
    ap.add_argument("--strict", action="store_true",
                    help="warnings fail the run too (default: only errors do)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.md_dir):
        ap.error("markdown dir not found: %s" % args.md_dir)
    office = _office_index(args.src)
    files = _md_files(args.md_dir)
    if args.limit:
        files = files[:args.limit]

    n_err = n_warn = n_office = n_lossy = 0
    out = open(args.json, "w", encoding="utf-8") if args.json else None
    for fn in files:
        path = os.path.join(args.md_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                md = f.read()
        except OSError as e:
            n_err += 1
            print("ERROR  %s unreadable (%s)" % (fn, e))
            continue
        did = fn[:-3]
        rec = {"id": did, "file": fn}
        if did in office:
            n_office += 1
            src_path, ext = office[did]
            parts = read_parts(src_path, ext)
            if not parts and os.path.getsize(src_path) > 0:
                # unreadable source: FAIL loudly rather than pass vacuously
                # against an empty ground truth
                n_lossy += 1
                errs = warns = 0
                rec["error"] = "unreadable-source"
                print("UNREADABLE  %s (source zip cannot be read; cannot attest "
                      "losslessness)" % fn)
                if out:
                    out.write(json.dumps(rec) + "\n")
                continue
            rep = conversion_report(ooxml_source_text(ext, parts),
                                    strip_front_matter(md))
            rec.update(rep)
            errs = rep["errors"]
            warns = rep["warnings"]
            if not rep["valid"]:
                n_lossy += 1
                print("LOSSY  %s recall=%.4f missing=%d errors=%d top=%s"
                      % (fn, rep["recall"], rep["n_missing"], errs,
                         rep["missing_top"][:5]))
        else:
            issues = validate_markdown(md)
            errs = sum(1 for i in issues if i.severity == "error")
            warns = len(issues) - errs
            rec["issues"] = [list(i) for i in issues]
            for i in issues:
                if i.severity == "error":
                    print("ERROR  %s:%d %s (%s)" % (fn, i.line, i.code, i.message))
        n_err += errs
        n_warn += warns
        if out:
            out.write(json.dumps(rec) + "\n")
    if out:
        out.close()
    print("validated files=%d office-lossless-checked=%d lossy=%d "
          "structure-errors=%d warnings=%d"
          % (len(files), n_office, n_lossy, n_err, n_warn))
    failed = n_err > 0 or n_lossy > 0 or (args.strict and n_warn > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
