#!/usr/bin/env python3
"""Second pass — MEASURE that the figure/caption pipeline was lossless and correct.

This is NOT a unit test; it is a runtime gate that re-derives ground truth from the
ORIGINAL sources so it cannot grade its own homework. For every embedded image it proves
one of: captured (useful caption inlined) / kept-with-neutral-alt / dropped-with-a-reason /
pending — and FAILS (non-zero exit) on any image that is:

  * UNACCOUNTED — present in the source (found by BYTE MAGIC, scanning every zip entry, not
    just the extractor's ``*/media/*`` glob) but has no record -> silently lost;
  * ORPHAN — a kept/captured figure whose asset file is missing or whose link never made it
    into the markdown;
  * MIS-GATED — a drop whose reason cannot be re-derived from the source (e.g. reason=chrome
    on an image the rels graph shows is BODY -> a formula could be dropped this way);
  * PENDING — a VLM outage left it uncaptioned -> the doc is INCOMPLETE, not lossless.

Independent of the pass under test: it reads the sources + markdown + records, never trusts
the asset store as ground truth (that holds survivors only).
"""
import argparse
import glob
import json
import os
import sys
import zipfile
from collections import namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.ingest import (  # noqa: E402
    doc_id, route_format, ext_of, summarize_routes, normalize_accept, ROUTE_OOXML,
    sniff_image_format, caption_cache_key, resolve_media_refs, is_body_part,
    load_source_root, load_ingest_config)

FigureAudit = namedtuple("FigureAudit", [
    "doc_id", "n_source", "n_captured", "n_kept", "n_dropped", "n_pending",
    "n_unaccounted", "n_orphan", "n_misgate", "n_loss", "lossless"])


def _rank(rec):
    # type: (dict) -> tuple
    """Strength of a figure record for one sha: a KEPT record beats a dropped one, and an OK
    caption beats a neutral keep. So a within-doc 'dup' record never shadows the occurrence
    that was actually captioned + inlined."""
    return (1 if rec.get("kept") else 0, 1 if rec.get("outcome_kind") == "OK" else 0)


def source_images(zip_path):
    # type: (str) -> dict
    """INDEPENDENT ground truth: every embedded image in an office zip, found by BYTE MAGIC
    across ALL entries (so an image outside ``*/media/`` is still seen), deduped by content
    hash. ``{sha: {parts, fmt, n_bytes}}``. SVG excluded (its text is captured deterministically)."""
    gt = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for n in zf.namelist():
                try:
                    data = zf.read(n)
                except (OSError, KeyError):
                    continue
                fmt = sniff_image_format(data)
                if not fmt or fmt == "svg":
                    continue
                sha = caption_cache_key(data)
                e = gt.setdefault(sha, {"parts": set(), "fmt": fmt, "n_bytes": len(data)})
                e["parts"].add(n)
    except (zipfile.BadZipFile, OSError):
        return {}
    return gt


def _source_refs(zip_path):
    # type: (str) -> dict
    rels = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for n in zf.namelist():
                if n.endswith(".rels"):
                    try:
                        rels[n] = zf.read(n).decode("utf-8", "replace")
                    except (OSError, KeyError):
                        pass
    except (zipfile.BadZipFile, OSError):
        return {}
    return resolve_media_refs(rels)


def _asset_ok(rec, md_dir, md_text):
    # type: (dict, str, str) -> bool
    """A kept figure's asset must EXIST on disk AND its link must be present in the markdown."""
    rel = rec.get("rel_path", "")
    if not rel or rel not in md_text:
        return False
    return os.path.isfile(os.path.normpath(os.path.join(md_dir, rel)))


def audit_doc(did, zip_path, md_text, md_dir, recs):
    # type: (str, str, str, str, list) -> tuple
    """Return (FigureAudit, [failure strings]). ``recs`` are this doc's figure records."""
    gt = source_images(zip_path)
    refs = _source_refs(zip_path)
    by_sha = {}
    for r in recs:
        sha = r.get("sha")
        if sha not in by_sha or _rank(r) > _rank(by_sha[sha]):
            by_sha[sha] = r                       # strongest record per content hash (kept > dup)
    n_cap = n_kept = n_drop = n_pending = n_unacc = n_orphan = n_misgate = n_loss = 0
    fails = []
    for sha, meta in gt.items():
        rec = by_sha.get(sha)
        if rec is None:
            n_unacc += 1
            fails.append("UNACCOUNTED %s %s (no record)" % (did, sorted(meta["parts"])[0]))
            continue
        reason = rec.get("reason", "")
        if reason == "pending":
            n_pending += 1
            continue
        # A metafile that could not be rendered (or a referenced-but-missing media part) is a
        # LOSS -- its pixel-text (maybe a formula) was never recovered -> fail, not clean.
        if rec.get("outcome_kind") in ("RENDER_FAILED", "MEDIA_MISSING") \
                or reason in ("render_failed", "media_missing"):
            n_loss += 1
            fails.append("LOSS %s %s (%s: image text never recovered)"
                         % (did, rec.get("part"), reason or rec.get("outcome_kind")))
            continue
        if rec.get("kept"):
            if rec.get("outcome_kind") == "OK":
                n_cap += 1
            else:
                n_kept += 1
            if not _asset_ok(rec, md_dir, md_text):
                n_orphan += 1
                fails.append("ORPHAN %s %s (asset/link missing)" % (did, rec.get("part")))
        else:
            n_drop += 1
            # Re-derive a chrome drop from the source: chrome requires chrome placement.
            if reason == "chrome":
                part = rec.get("part", "")
                src_chrome = any((not is_body_part(p)) and refs.get(p) == "chrome"
                                 for p in meta["parts"]) or refs.get(part) == "chrome"
                if is_body_part(part) or refs.get(part) == "body" or not src_chrome:
                    n_misgate += 1
                    fails.append("MIS-GATED %s %s dropped=chrome but source ref is not chrome"
                                 % (did, part))
    lossless = (n_unacc == 0 and n_orphan == 0 and n_misgate == 0
                and n_pending == 0 and n_loss == 0)
    audit = FigureAudit(did, len(gt), n_cap, n_kept, n_drop, n_pending,
                        n_unacc, n_orphan, n_misgate, n_loss, lossless)
    return audit, fails


def _load_records(assets_dir):
    # type: (str) -> dict
    """All figure records, grouped by doc_id (glob shards; every record kept)."""
    by_doc = {}
    for fp in sorted(glob.glob(os.path.join(assets_dir, "_figures*.jsonl"))):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    by_doc.setdefault(r.get("doc_id"), []).append(r)
        except OSError:
            pass
    return by_doc


def _office_sources(src_root, accept):
    rel_of = {}
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            full = os.path.join(root, fn)
            rel_of[os.path.relpath(full, src_root)] = full
    scan = summarize_routes(sorted(rel_of), accept)
    return [(rel_of[rel], rel) for rel in scan.by_lane.get(ROUTE_OOXML, [])]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Second-pass figure validator: measure figure "
                                             "losslessness/correctness from the ORIGINAL sources.")
    ap.add_argument("--src", default=load_source_root(), help="source documents root (required)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "markdown"),
                    help="markdown dir holding <doc_id>.md")
    ap.add_argument("--assets", default=os.path.join(_REPO, "data", "assets"),
                    help="asset + records dir")
    ap.add_argument("--explain", action="store_true", help="list every offending figure")
    args = ap.parse_args(argv)
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))

    accept = (normalize_accept("") if False else (load_ingest_config().accept_formats or None))
    by_doc = _load_records(args.assets)
    audits = []
    all_fails = []
    for full, rel in _office_sources(args.src, accept):
        if route_format(ext_of(rel)) != ROUTE_OOXML:
            continue
        did = doc_id(rel)
        md_path = os.path.join(args.out, did + ".md")
        try:
            with open(md_path, encoding="utf-8") as f:
                md_text = f.read()
        except OSError:
            md_text = ""
        audit, fails = audit_doc(did, full, md_text, os.path.dirname(md_path),
                                 by_doc.get(did, []))
        if audit.n_source:
            audits.append(audit)
        all_fails.extend(fails)

    n_docs = len(audits)
    tot = lambda k: sum(getattr(a, k) for a in audits)   # noqa: E731
    n_bad = sum(1 for a in audits if not a.lossless)
    print("figure validate: docs_with_images=%d source_images=%d captured=%d kept=%d "
          "dropped=%d pending=%d unaccounted=%d orphan=%d mis-gated=%d loss=%d  -> %s"
          % (n_docs, tot("n_source"), tot("n_captured"), tot("n_kept"), tot("n_dropped"),
             tot("n_pending"), tot("n_unaccounted"), tot("n_orphan"), tot("n_misgate"),
             tot("n_loss"), "LOSSLESS" if n_bad == 0 else "%d DOC(S) FAILED" % n_bad),
          file=sys.stderr)
    if args.explain:
        for f in all_fails:
            print("  " + f, file=sys.stderr)
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
