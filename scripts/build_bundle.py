#!/usr/bin/env python3
"""Emit the doc2md output BUNDLE for each office document.

One document in -> one bundle out, under ``<out>/<doc_id>/``:

    document.md      markdown body + YAML front matter (the source -> markdown map)
    structure.json   the faithful heading outline + per-section token counts
    report.json      the validator verdict (losslessness + metrics + status), NO LLM
    images/          extracted image pixels, content-addressed (<sha16>.<ext>); each
                     is referenced by an ![](images/..) link in the body. Captions are
                     added later by the enrichment stage (deterministic pass = empty alt)

This script is a thin WRITER: all the domain logic is in ``src/backend`` —
``office_convert.bundle_inputs`` (the shared read + losslessness guards) feeds
``backend.bundle.assemble_bundle`` (the pure bundle assembler). It adds no
conversion or validation logic of its own. See docs/design (doc2md
``output-contract.md``) for the schema.

Losslessness here is the office lane's hard gate (token recall == 1.0); a document
that fails the gate still gets a ``report.json`` with ``status: failed`` so the
failure is recorded, never silent.

Idempotent: a doc whose bundle already exists with ``status`` ``ok``/``degraded`` is
skipped (``--force`` to rebuild). Safe to run twice.

Usage:
  python3 scripts/build_bundle.py --src "$DOC2MD_SRC" --out data/bundles
  python3 scripts/build_bundle.py --only "radar spec.docx" --out data/bundles
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)                        # import the config package
sys.path.insert(0, _HERE)                        # import the sibling office lane script

import office_convert as oc                      # noqa: E402  (disk helpers + shared guards)
from backend.bundle import assemble_bundle       # noqa: E402  (pure assembler)
from backend.ingest import (load_source_root,    # noqa: E402
                            ooxml_image_parts, plan_office_images, inline_ooxml_images,
                            load_ingest_config)
from backend.validate import image_report, caption_report   # noqa: E402  (report policy)

# Our extracted images are content-addressed: <sha16>.<ext>. Used to scope the orphan
# GC so it only ever removes files this pipeline wrote, never a stray hand-placed file.
_CA_NAME = re.compile(r"^[0-9a-f]{16}\.[A-Za-z0-9]+$")


def _resolve_tokenizer(cli_override):
    # type: (str) -> tuple
    """Resolve (token_count, token_model) from config.settings, honoring a --tokenizer
    override (``backend`` or ``backend:model``). Falls back to the char estimate if the
    config package is absent so the writer still runs in a stripped deployment."""
    override = None
    if cli_override:
        backend, _, model = cli_override.partition(":")
        override = {"backend": backend or None, "model": model or None}
    try:
        from config.settings import get_token_counter
    except ImportError:
        return None, "char-estimate/4"
    return get_token_counter(override)

CONVERTER = "doc2md-ooxml/0.1.0"
MANIFEST = "manifest.jsonl"


def sha256_file(path):
    # type: (str) -> str
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def _write_json(dest, obj):
    # type: (str, object) -> None
    _write_atomic(dest, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _write_bytes(dest, data):
    # type: (str, bytes) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def _verify_images(img_dir, assets):
    # type: (str, list) -> int
    """Count files whose ON-DISK content ``sha256[:16]`` matches their content-addressed
    filename stem — proof the extracted bytes actually landed intact (not just that a
    file of that name exists). This is the image lane's integrity check, the pixel-side
    analogue of the office token-recall gate."""
    ok = 0
    for fname, _data in assets:
        stem = fname.split(".", 1)[0]
        h = hashlib.sha256()
        try:
            with open(os.path.join(img_dir, fname), "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            continue
        if h.hexdigest()[:16] == stem:
            ok += 1
    return ok


def _gc_orphans(img_dir, keep):
    # type: (str, set) -> int
    """Remove content-addressed image files no longer referenced by this build (e.g. a
    figure replaced in the source), returning the count removed. Keeps ``images/`` a
    faithful mirror of the body's references so stale pixels never accumulate across
    rebuilds. Only our ``<sha16>.<ext>`` files are eligible — anything else is left."""
    removed = 0
    try:
        names = os.listdir(img_dir)
    except OSError:
        return 0
    for name in names:
        if name in keep or not _CA_NAME.match(name):
            continue
        try:
            os.remove(os.path.join(img_dir, name))
            removed += 1
        except OSError:
            pass
    return removed


def _carry_captions(doc_dir, structure):
    # type: (str, dict) -> int
    """Preserve captions across a rebuild: map ``image_id -> caption`` from the PRIOR
    ``structure.json`` (if any) and re-attach to the freshly built nodes for the SAME
    content-addressed image. A changed image has a new ``image_id``, so a stale caption
    never carries over. Returns the number of captions carried. This is what makes a
    ``--force`` rebuild non-destructive: unchanged figures keep their captions, only
    new/changed images need the (cache-fast) caption pass re-run."""
    try:
        with open(os.path.join(doc_dir, "structure.json"), encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return 0
    caps = {}                                  # image_id -> prior caption

    def collect(nodes):
        for n in nodes:
            for im in n.get("images", []):
                c = im.get("caption")
                if c:
                    caps[im.get("image_id", "")] = c
            collect(n.get("children", []))
    collect(old.get("outline", []))
    if not caps:
        return 0

    def apply(nodes):
        k = 0
        for n in nodes:
            for im in n.get("images", []):
                c = caps.get(im.get("image_id", ""))
                if c and not im.get("caption"):
                    im["caption"] = c
                    k += 1
            k += apply(n.get("children", []))
        return k
    return apply(structure.get("outline", []))


def _count_captioned(structure):
    # type: (dict) -> int
    """Distinct content-addressed images that carry a non-null caption in ``structure``."""
    seen = set()

    def walk(nodes):
        for n in nodes:
            for im in n.get("images", []):
                if im.get("caption"):
                    seen.add(im.get("image_id", ""))
            walk(n.get("children", []))
    walk(structure.get("outline", []))
    return len(seen)


def _count_outline_images(structure):
    # type: (dict) -> int
    """Image-node occurrences attached to the heading outline — i.e. the images the
    caption stage can actually reach (it walks the outline). Less than the body's
    referenced count means some pictures fell outside every section and are
    uncaptionable — a structure gap the report must surface, not hide."""
    total = [0]

    def walk(nodes):
        for n in nodes:
            total[0] += len(n.get("images", []))
            walk(n.get("children", []))
    walk(structure.get("outline", []))
    return total[0]


def _failure_report(row, error, warnings):
    # type: (dict, str, list) -> dict
    """A report for a document that FAILED conversion — recorded, never silent. It
    must NOT run through the assembler: an empty body would score a vacuous recall of
    1.0 and falsely 'pass', so failures carry an explicit failed gate instead."""
    from collections import OrderedDict
    rep = OrderedDict()
    rep["doc_id"] = row["id"]
    rep["lane"] = "office"
    rep["source_format"] = row["ext"]
    rep["converter"] = CONVERTER
    rep["source_relpath"] = row["rel"]
    rep["status"] = "failed"
    rep["losslessness"] = {"method": "ooxml-ground-truth", "gate": "fail",
                           "error": error}
    rep["warnings"] = list(warnings or [])
    return rep


def build_one(row, soffice, out_root, run_id, token_count=None, token_model=None,
              captions_enabled=False):
    # type: (dict, str, str, str, object, str, bool) -> dict
    """Convert + validate + assemble + write one document's bundle.

    Returns a manifest row: ``{doc_id, source_relpath, lane, status, markdown_sha256,
    error}``. On hard failure only a ``report.json`` (status failed) is written."""
    doc_dir = os.path.join(out_root, row["id"])
    t0 = time.time()
    info = oc.bundle_inputs(row, soffice, emit_images=True)
    t_convert = int((time.time() - t0) * 1000)

    if info["error"] and info["error"] != "empty-source-file":
        os.makedirs(doc_dir, exist_ok=True)
        rep = _failure_report(row, info["error"], info["warnings"])
        _write_json(os.path.join(doc_dir, "report.json"), rep)
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
                "status": "failed", "markdown_sha256": "", "error": info["error"]}

    # Deterministic image extraction: resolve the converter's positional sentinels to
    # content-addressed files, inline the body BEFORE assembling (so structure.json and
    # the report see the final markdown), and surface any referenced-but-missing bytes as
    # a warning — never a silent drop.
    plan = plan_office_images(ooxml_image_parts(info["body"]), info.get("media", {}))
    body_md = inline_ooxml_images(info["body"], plan.fills)
    warnings = list(info["warnings"])
    if plan.n_missing:
        warnings.append({"code": "image_bytes_missing",
                         "detail": "%d referenced image(s) had no bytes in the package"
                                   % plan.n_missing})
    extras = {"images_extracted": plan.n_resolved, "image_files": plan.n_files,
              "images_missing": plan.n_missing, "captions_enabled": captions_enabled}

    src_sha = sha256_file(row["src"])
    t1 = time.time()
    bundle = assemble_bundle(
        doc_id=row["id"], source_relpath=row["rel"], source_format=row["ext"],
        lane="office", source_text=info["source_text"], body_md=body_md,
        source_meta=info["meta"], converter=CONVERTER, source_sha256=src_sha,
        warnings=warnings, extras=extras, timing_ms={"convert": t_convert},
        generated_run=run_id, token_count=token_count, token_model=token_model)
    bundle["report"]["timing_ms"]["validate"] = int((time.time() - t1) * 1000)
    rep = bundle["report"]

    os.makedirs(doc_dir, exist_ok=True)
    # A document that FAILS the gate (recall < 1.0 / structural error) must NOT publish
    # its lossy markdown or pixels — report.json only, so the failure is recorded, never
    # silent (and never leaves a half-written bundle behind).
    if rep["status"] == "failed" or rep["losslessness"].get("gate") == "fail":
        _write_json(os.path.join(doc_dir, "report.json"), rep)
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
                "status": "failed", "markdown_sha256": rep["markdown_sha256"],
                "error": rep["losslessness"].get("error", "gate-fail")}

    # Gate passed: extract the pixels, then VERIFY them on disk and GC any files left
    # over from a prior build. Feed the MEASURED integrity (content-verified count,
    # orphans removed) back into the report so the image gate reflects the bytes that
    # actually landed — not just what the plan intended.
    img_dir = os.path.join(doc_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    for fname, data in plan.assets:
        _write_bytes(os.path.join(img_dir, fname), data)
    verified = _verify_images(img_dir, plan.assets)
    removed = _gc_orphans(img_dir, set(fname for fname, _ in plan.assets))
    if removed:
        rep["warnings"].append(
            {"code": "orphan_images_removed",
             "detail": "%d stale image file(s) removed on rebuild" % removed})
    im = rep["images"]
    rep["images"] = image_report(im["referenced"], im["extracted"], im["unique_files"],
                                 im["missing"], 0, verified)
    if rep["images"]["gate"] != "pass" and rep["status"] == "ok":
        rep["status"] = "degraded"

    # Referenced pictures that never attached to a heading section cannot be captioned
    # (the caption stage walks the outline) — surface the gap instead of hiding it.
    attached = _count_outline_images(bundle["structure"])
    if attached < rep["images"]["referenced"]:
        rep["warnings"].append(
            {"code": "images_not_in_outline",
             "detail": "%d referenced image(s) fall outside the heading outline and "
                       "cannot be captioned" % (rep["images"]["referenced"] - attached)})

    # Non-destructive rebuild: carry unchanged images' captions forward, then make the
    # report's caption block reflect what the (possibly carried) structure now holds.
    _carry_captions(doc_dir, bundle["structure"])
    captioned = _count_captioned(bundle["structure"])
    rep["captions"] = caption_report(captions_enabled, im["unique_files"], captioned,
                                     0, 0, im["unique_files"] - captioned)

    _write_json(os.path.join(doc_dir, "report.json"), rep)
    _write_atomic(os.path.join(doc_dir, "document.md"), bundle["document_md"])
    _write_json(os.path.join(doc_dir, "structure.json"), bundle["structure"])
    return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
            "status": rep["status"], "markdown_sha256": rep["markdown_sha256"], "error": ""}


def _done(out_root):
    # type: (str) -> set
    """Doc ids whose bundle already exists with a non-failed status (skip unless --force)."""
    done = set()
    try:
        ids = os.listdir(out_root)
    except OSError:
        return done
    for did in ids:
        rp = os.path.join(out_root, did, "report.json")
        try:
            with open(rp, encoding="utf-8") as f:
                if json.load(f).get("status") in ("ok", "degraded"):
                    done.add(did)
        except (OSError, ValueError):
            continue
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emit the doc2md output bundle (document.md + structure.json + "
                    "report.json + images/) for each office document.")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC / [paths].source_docs)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "bundles"),
                    help="output root for <doc_id>/ bundles (default data/bundles)")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats to accept (default: all supported)")
    ap.add_argument("--only", action="append", default=[],
                    help="build ONLY this doc id or source basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when a non-failed bundle already exists")
    ap.add_argument("--run-id", default="",
                    help="stamp bundles with this run id (default: UTC timestamp)")
    ap.add_argument("--tokenizer", default="",
                    help="tokenizer override 'backend[:model]' (e.g. tiktoken:cl100k_base); "
                         "default from config/settings.py (char estimate if unset)")
    args = ap.parse_args(argv)

    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))
    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    token_count, token_model = _resolve_tokenizer(args.tokenizer)
    print("tokenizer: %s" % token_model, file=sys.stderr)
    # Whether captioning is enabled is recorded in each report's caption block so a
    # freshly built bundle already declares its caption debt (expected vs pending).
    captions_enabled = bool(getattr(load_ingest_config(), "enable_captions", False))

    from backend.ingest import normalize_accept
    accept_spec = args.accept if args.accept.strip() else (load_ingest_config().accept_formats or None)
    accept = normalize_accept(accept_spec)
    office_sources, scan = oc.scan_tree(args.src, accept)
    oc._warn_unconverted(scan)
    rows = oc.plan(office_sources, args.out)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["id"] in want or os.path.basename(r["rel"]) in want]

    os.makedirs(args.out, exist_ok=True)
    done = set() if args.force else _done(args.out)
    todo_all = [r for r in rows if r["id"] not in done]
    todo = todo_all[:args.limit] if args.limit else todo_all
    capped = len(todo_all) - len(todo)
    msg = ("office sources=%d  already-built=%d  to-build=%d"
           % (len(rows), len(rows) - len(todo_all), len(todo)))
    if capped:
        msg += "  (--limit deferred %d more)" % capped     # never a silent cap
    print(msg + "  -> %s" % args.out, file=sys.stderr)

    n_lo = sum(1 for r in todo if r["lane"] == oc.ROUTE_LIBREOFFICE)
    soffice = oc.find_soffice() if n_lo else ""
    if n_lo and not soffice:
        print("  [WARNING] soffice NOT found -> %d ODF/legacy doc(s) will FAIL "
              "(set DOC2MD_LIBREOFFICE or run scripts/setup_libreoffice.py)" % n_lo,
              file=sys.stderr)

    ok = degraded = failed = 0
    t0 = time.time()
    manifest_path = os.path.join(args.out, MANIFEST)
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for r in todo:
            m = build_one(r, soffice, args.out, run_id, token_count, token_model,
                          captions_enabled)
            mf.write(json.dumps(m) + "\n")
            if m["status"] == "ok":
                ok += 1
            elif m["status"] == "degraded":
                degraded += 1
            else:
                failed += 1
                print("  FAIL %s %s" % (r["rel"], m["error"]), file=sys.stderr)
    print("bundles: ok=%d degraded=%d failed=%d in %.1fs -> %s"
          % (ok, degraded, failed, time.time() - t0, args.out), file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
