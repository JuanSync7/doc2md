#!/usr/bin/env python3
"""Emit the doc2md output BUNDLE for each PDF/HTML (docling-lane) document.

Runs UNDER PYTHON 3.12 with docling installed (the same interpreter as
scripts/docling_convert.py) — NOT the 3.6 pipeline. One document in -> one bundle
out, under ``<out>/<doc_id>/``, IDENTICAL in shape to the office lane's
(scripts/build_bundle.py):

    document.md      markdown body + YAML front matter (the source -> markdown map)
    structure.json   the faithful heading outline + per-section token counts
    report.json      the validator verdict (losslessness + gates + status), NO LLM
    images/          extracted figure pixels, content-addressed (<sha16>.png); each
                     referenced by an ![](images/..) link in the body. Captions are
                     added later by scripts/caption_bundles.py (same overlay stage
                     as the office lane)

The LANE ASYMMETRY is honest and structural (docs/design output-contract.md): a PDF
has no ground-truth semantic tree, so ``losslessness`` here is a MEASURED best-effort
coverage block (token recall + char-n-gram content recall against the PDF's own
pdftotext layer, furniture/image-region text excluded apple-to-apple) and its gate is
always ``best-effort`` — never ``pass``. Every other gate is the same hard machinery
as the office lane: image-extraction integrity (content-verified pixels, orphan GC),
outline coverage, caption coverage.

Losslessness improvements applied at convert time:
  * raw-vocab repair — identifiers a layout model split across a wrapped table cell
    ("SLV_AD DR4_EN") are rejoined iff the joined form exists verbatim in the PDF's
    own text layer (backend.ingest.repair_split_tokens; docling_test EXP-1: fixes
    100% of split artifacts, zero false joins).
  * text-layer fallback — when docling's markdown provably dropped body content the
    text layer holds (measured char-n-gram completeness), the de-boilerplated text
    layer is used instead and the fallback is recorded, never silent.

Scanned PDFs (no text layer) are OCR'd (RapidOCR pipeline); their reports say so
(``method: pdf-ocr-transcription``) — for VLM-quality OCR use docling_convert.py.

Idempotent: a doc whose bundle already exists with ``status`` ``ok``/``degraded`` is
skipped (``--force`` to rebuild; captions carry across rebuilds). Safe to run twice.
Office documents are NOT handled here — run scripts/build_bundle.py for those; both
writers share one ``--out`` root and one manifest.

Usage:
  .venv/bin/python scripts/build_pdf_bundle.py --src "$DOC2MD_SRC" --out data/bundles
  .venv/bin/python scripts/build_pdf_bundle.py --only "spec.pdf" --out data/bundles
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)                        # import the config package
sys.path.insert(0, _HERE)                        # import the sibling lane scripts

import docling_convert as dc                     # noqa: E402  (converters + measurement)
import build_bundle as bb                        # noqa: E402  (shared writer helpers)
from backend.bundle import assemble_bundle       # noqa: E402  (pure assembler)
from backend.ingest import (doc_id, tokenize,    # noqa: E402
                            image_markdown, inline_image_captions,
                            identifier_vocab, repair_split_tokens,
                            coverage, markdown_to_text, char_ngram_recall,
                            is_lossy_explained, strip_running_lines,
                            pdf_info_meta, load_ingest_config, load_source_root,
                            normalize_accept)
from backend.validate import image_report, caption_report   # noqa: E402  (report policy)

CONVERTER = "doc2md-docling/0.1.0"
_PLACEHOLDER = "<!-- image -->"

_TOOLCHAIN_VERSIONS = {}  # memoized probes: one process, one answer


def _dist_version(name):
    # type: (str) -> str
    """Installed version of a distribution, '' when absent (never a crash)."""
    if name not in _TOOLCHAIN_VERSIONS:
        ver = ""
        try:
            from importlib import metadata  # 3.8+; probe only, import stays 3.6-safe
            ver = metadata.version(name)
        except Exception:
            ver = ""
        _TOOLCHAIN_VERSIONS[name] = ver
    return _TOOLCHAIN_VERSIONS[name]


def _pdftotext_version():
    # type: () -> str
    """poppler's version from ``pdftotext -v`` (prints to stderr), '' if absent."""
    if "pdftotext" not in _TOOLCHAIN_VERSIONS:
        ver = ""
        try:
            out = subprocess.check_output(["pdftotext", "-v"],
                                          stderr=subprocess.STDOUT)
            first = out.decode("utf-8", "replace").strip().splitlines()[0]
            ver = first.split()[-1] if first else ""
        except Exception:
            ver = ""
        _TOOLCHAIN_VERSIONS["pdftotext"] = ver
    return _TOOLCHAIN_VERSIONS["pdftotext"]


def _toolchain_warning(lane):
    # type: (str) -> dict
    """The lane's provenance stamp (the ``libreoffice_preconvert`` version-naming
    analogue): NAME the external tools that produced/measured this document —
    docling (+ docling-core) always, poppler's pdftotext for the pdf lane where it
    supplies the ground-truth text layer. Versions are best-effort: an absent
    dist/binary keeps its name, drops its number — never a crash, never silent."""
    def named(tool, ver):
        return "%s %s" % (tool, ver) if ver else tool
    detail = "converted via %s + %s" % (
        named("docling", _dist_version("docling")),
        named("docling-core", _dist_version("docling-core")))
    if lane == "pdf":
        detail += "; text layer via %s" % named("pdftotext (poppler)",
                                                _pdftotext_version())
    return {"code": "pdf_toolchain", "detail": detail}


def plan(sources, out_root):
    # type: (list, str) -> list
    """Per-source rows {id, rel, src, ext} for the docling lane (pure, no docling)."""
    rows = []
    for full, rel in sources:
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        rows.append({"id": doc_id(rel), "rel": rel, "src": full, "ext": ext})
    return rows


def _extract_figures(doc):
    # type: (object) -> tuple
    """Content-addressed figure extraction from a converted docling document.

    Every BODY-layer picture is kept (maximal fidelity — the office lane extracts
    every embedded image too; the caption stage's own furniture gate handles noise,
    and content addressing deduplicates repeated pixels for free). Returns
    ``(renders, assets, n_missing)`` where ``renders[k]`` is the ![](images/..)
    substitution for the k-th body picture (None when its bytes could not be
    rendered), ``assets`` maps filename -> PNG bytes, and ``n_missing`` counts
    unrenderable pictures."""
    renders = []
    assets = OrderedDict()
    missing = 0
    for pic in (getattr(doc, "pictures", None) or []):
        if not dc._is_body_picture(pic):
            continue
        _cls, _area, _sha, img = dc._picture_meta(doc, pic)
        if img is None:
            renders.append(None)
            missing += 1
            continue
        data = dc._png_bytes(img)
        name = hashlib.sha256(data).hexdigest()[:16] + ".png"
        assets.setdefault(name, data)
        renders.append(image_markdown("", "images/" + name))
    return renders, assets, missing


def _pdf_losslessness(src_stripped, md, furniture, image_text, cfg):
    # type: (str, str, str, str, object) -> tuple
    """The measured best-effort losslessness block for a digital PDF/HTML doc.

    Same apple-to-apple model as docling_convert's coverage record: token recall of
    the de-boilerplated source vs the markdown, with docling's own furniture text and
    figure-region text excluded from the ground truth; char-n-gram content recall as
    the tokenization-blind second opinion. Returns ``(loss_block, is_real_loss)`` —
    ``is_real_loss`` is True only under the explained-gap model (low token recall AND
    low content recall), which is what should degrade the document status."""
    exclude = (furniture + " " + image_text).strip()
    md_text = markdown_to_text(md)
    rep = coverage(src_stripped, md_text, exclude=exclude)
    content = char_ngram_recall(
        src_stripped, (md_text + " " + exclude) if exclude else md_text)
    lossy = is_lossy_explained(rep, content, min_recall=cfg.min_recall,
                               min_tokens=cfg.min_tokens,
                               content_min=cfg.content_min_recall)
    loss = OrderedDict()
    loss["method"] = "pdf-text-coverage"
    loss["token_recall"] = round(rep.recall, 4)
    loss["content_recall"] = round(content, 4)
    loss["n_source_tokens"] = rep.n_source
    loss["missing_tokens"] = rep.missing_top if lossy else []
    # Text buried INSIDE figure regions (excluded from the body metric above — it is
    # figure content, not lost body text). Surfaced so the one loss class the text
    # gates cannot recover is VISIBLE per doc: this is exactly what the VLM caption
    # stage exists to bring back.
    loss["figure_text_tokens"] = len(tokenize(image_text)) if image_text else 0
    loss["ocr_used"] = False
    loss["gate"] = "best-effort"
    return loss, lossy


def _failure_report(row, lane, error, warnings):
    # type: (dict, str, str, list) -> dict
    """A report for a document that FAILED conversion — recorded, never silent."""
    rep = OrderedDict()
    rep["doc_id"] = row["id"]
    rep["lane"] = lane
    rep["source_format"] = row["ext"]
    rep["converter"] = CONVERTER
    rep["source_relpath"] = row["rel"]
    rep["status"] = "failed"
    rep["losslessness"] = {"method": "pdf-text-coverage", "gate": "best-effort",
                           "error": error}
    rep["warnings"] = list(warnings or [])
    return rep


def build_one(row, conv, ocr_conv, ocr_mode, out_root, run_id, cfg,
              token_count=None, token_model=None, captions_enabled=False):
    # type: (dict, object, object, str, str, str, object, object, str, bool) -> dict
    """Convert + measure + assemble + write one docling-lane document's bundle."""
    lane = "pdf" if row["ext"] == "pdf" else "html"
    doc_dir = os.path.join(out_root, row["id"])
    # Provenance first: the stamp rides every report, including failure reports.
    warnings = [_toolchain_warning(lane)]

    use_ocr = False
    if row["ext"] == "pdf":
        if ocr_mode == "on":
            use_ocr = True
        elif ocr_mode == "auto":
            use_ocr = not dc.pdf_has_text_layer(row["src"])

    t0 = time.time()
    try:
        res = (ocr_conv() if use_ocr else conv).convert(row["src"])
        status = dc._status_name(res)
        if status in dc._DOCLING_BAD_STATUS:
            raise RuntimeError("docling status %s" % status)
        doc = res.document
        md = doc.export_to_markdown()
    except Exception as e:
        os.makedirs(doc_dir, exist_ok=True)
        err = "%s: %s" % (type(e).__name__, e)
        bb._write_json(os.path.join(doc_dir, "report.json"),
                       _failure_report(row, lane, err, warnings))
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": lane,
                "status": "failed", "markdown_sha256": "", "error": err}
    t_convert = int((time.time() - t0) * 1000)
    if use_ocr:
        warnings.append({"code": "ocr_transcription",
                         "detail": "scanned PDF: no independent text layer to measure against"})

    # Figures -> content-addressed files + body links (office-lane scheme). A
    # placeholder/picture count mismatch means positional binding is unsafe: bail
    # (write no pixels, count every placeholder missing) — a DETECTED, gated loss.
    n_ph = md.count(_PLACEHOLDER)
    renders, assets, n_missing = ([], OrderedDict(), 0) if use_ocr else _extract_figures(doc)
    if n_ph != len(renders):
        if n_ph or renders:
            warnings.append({"code": "image_inline_bailed",
                             "detail": "%d placeholder(s) vs %d body picture(s): "
                                       "not inlined" % (n_ph, len(renders))})
        renders, assets, n_missing = [], OrderedDict(), n_ph
    else:
        md = inline_image_captions(md, renders)
    if n_missing:
        warnings.append({"code": "image_bytes_missing",
                         "detail": "%d figure(s) could not be rendered to pixels"
                                   % n_missing})

    # Losslessness measurement (+ the two convert-time improvements) — digital only.
    src_stripped = ""
    if not use_ocr:
        raw = dc._source_text(row["src"])
        src_stripped = strip_running_lines(raw, cfg.header_footer_min_frac)
        if lane == "pdf" and raw:
            md = repair_split_tokens(md, identifier_vocab(raw))
        # Text-layer fallback: docling provably dropped content the layer holds.
        lossy_fb, content_fb, n_fb = dc._alt_more_complete(src_stripped, md)
        if lossy_fb:
            warnings.append({"code": "pdf_text_layer_fallback",
                             "detail": "docling markdown held %.2f of the %d-token "
                                       "text layer; using the text layer body"
                                       % (content_fb, n_fb)})
            md = src_stripped
            renders, assets, n_missing = [], OrderedDict(), 0

    if use_ocr:
        loss = OrderedDict()
        loss["method"] = "pdf-ocr-transcription"
        loss["note"] = "scanned source: no independent text layer to measure against"
        loss["ocr_used"] = True
        loss["gate"] = "best-effort"
        real_loss = False
    else:
        furniture = dc._furniture_text(doc)
        image_text = ""
        if lane == "pdf":
            image_text = dc._image_region_text(row["src"], dc._picture_boxes(doc))
        loss, real_loss = _pdf_losslessness(src_stripped, md, furniture,
                                            image_text, cfg)
    if real_loss:
        warnings.append({"code": "pdf_content_loss",
                         "detail": "token recall %.3f / content recall %.3f below "
                                   "the explained-gap gate"
                                   % (loss["token_recall"], loss["content_recall"])})

    meta = OrderedDict()
    if lane == "pdf":
        try:
            meta = pdf_info_meta(dc._pdfinfo(row["src"]))
        except Exception:
            meta = OrderedDict()

    extras = {"images_extracted": sum(1 for r in renders if r),
              "image_files": len(assets), "images_missing": n_missing,
              "captions_enabled": captions_enabled,
              "image_meta": bb.image_meta_of(assets.items())}
    t1 = time.time()
    bundle = assemble_bundle(
        doc_id=row["id"], source_relpath=row["rel"], source_format=row["ext"],
        lane=lane, source_text=src_stripped, body_md=md, source_meta=meta,
        converter=CONVERTER, source_sha256=bb.sha256_file(row["src"]),
        warnings=warnings, extras=extras, timing_ms={"convert": t_convert},
        generated_run=run_id, token_count=token_count, token_model=token_model,
        losslessness=loss)
    bundle["report"]["timing_ms"]["validate"] = int((time.time() - t1) * 1000)
    rep = bundle["report"]
    # Measured real content loss degrades the document — same fold as the other
    # gates: recorded and triaged, never a silent "ok".
    if real_loss and rep["status"] == "ok":
        rep["status"] = "degraded"

    os.makedirs(doc_dir, exist_ok=True)
    if rep["status"] == "failed":
        bb._write_json(os.path.join(doc_dir, "report.json"), rep)
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": lane,
                "status": "failed", "markdown_sha256": rep["markdown_sha256"],
                "error": "structural-error"}

    # Pixels: write, content-verify on disk, GC stale files — the same measured
    # integrity feedback loop as the office writer.
    img_dir = os.path.join(doc_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    pairs = list(assets.items())
    for fname, data in pairs:
        bb._write_bytes(os.path.join(img_dir, fname), data)
    verified = bb._verify_images(img_dir, pairs)
    removed = bb._gc_orphans(img_dir, set(assets))
    if removed:
        rep["warnings"].append(
            {"code": "orphan_images_removed",
             "detail": "%d stale image file(s) removed on rebuild" % removed})
    im = rep["images"]
    rep["images"] = image_report(im["referenced"], im["extracted"],
                                 im["unique_files"], im["missing"], 0, verified)
    if rep["images"]["gate"] != "pass" and rep["status"] == "ok":
        rep["status"] = "degraded"

    attached = bb._count_outline_images(bundle["structure"])
    if attached < rep["images"]["referenced"]:
        rep["warnings"].append(
            {"code": "images_not_in_outline",
             "detail": "%d referenced image(s) fall outside the heading outline and "
                       "cannot be captioned" % (rep["images"]["referenced"] - attached)})

    bb._carry_captions(doc_dir, bundle["structure"])
    captioned = bb._count_captioned(bundle["structure"])
    rep["captions"] = caption_report(captions_enabled, im["unique_files"], captioned,
                                     0, 0, im["unique_files"] - captioned)

    bb._write_json(os.path.join(doc_dir, "report.json"), rep)
    bb._write_atomic(os.path.join(doc_dir, "document.md"), bundle["document_md"])
    bb._write_json(os.path.join(doc_dir, "structure.json"), bundle["structure"])
    return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": lane,
            "status": rep["status"], "markdown_sha256": rep["markdown_sha256"],
            "error": ""}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emit the doc2md output bundle (document.md + structure.json + "
                    "report.json + images/) for each PDF/HTML document. Python 3.12 "
                    "+ docling required.")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC / [paths].source_docs)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "bundles"),
                    help="output root for <doc_id>/ bundles (default data/bundles; "
                         "shared with the office writer)")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats to accept (default: all supported)")
    ap.add_argument("--only", action="append", default=[],
                    help="build ONLY this doc id or source basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when a non-failed bundle already exists")
    ap.add_argument("--run-id", default="",
                    help="stamp bundles with this run id (default: UTC timestamp)")
    ap.add_argument("--threads", type=int, default=0,
                    help="cap docling/torch CPU threads (0 = library defaults)")
    ap.add_argument("--ocr", choices=("auto", "on", "off"), default="auto",
                    help="OCR scanned PDFs: auto-detect (default), always, never")
    ap.add_argument("--tokenizer", default="",
                    help="tokenizer override 'backend[:model]' "
                         "(default from config/settings.py)")
    args = ap.parse_args(argv)

    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))
    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    token_count, token_model = bb._resolve_tokenizer(args.tokenizer)
    print("tokenizer: %s" % token_model, file=sys.stderr)
    cfg = load_ingest_config()
    captions_enabled = bool(getattr(cfg, "enable_captions", False))

    accept_spec = args.accept if args.accept.strip() else (cfg.accept_formats or None)
    accept = normalize_accept(accept_spec)
    sources, scan = dc.find_sources(args.src, accept)
    dc._warn_unrouted(scan)
    rows = plan(sources, args.out)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["id"] in want or os.path.basename(r["rel"]) in want]

    os.makedirs(args.out, exist_ok=True)
    done = set() if args.force else bb._done(args.out)
    todo_all = [r for r in rows if r["id"] not in done]
    todo = todo_all[:args.limit] if args.limit else todo_all
    capped = len(todo_all) - len(todo)
    msg = ("docling-lane sources=%d  already-built=%d  to-build=%d"
           % (len(rows), len(rows) - len(todo_all), len(todo)))
    if capped:
        msg += "  (--limit deferred %d more)" % capped     # never a silent cap
    print(msg + "  -> %s" % args.out, file=sys.stderr)
    if not todo:
        return 0

    conv = dc._make_caption_converter(args.threads)   # image export ON, captioning OFF
    state = {"ocr": None}

    def ocr_conv():
        if state["ocr"] is None:
            print("  [auto-ocr] scanned PDF -> loading OCR pipeline", file=sys.stderr)
            state["ocr"] = dc._make_converter(True, args.threads)
        return state["ocr"]

    ok = degraded = failed = 0
    t0 = time.time()
    manifest_path = os.path.join(args.out, bb.MANIFEST)
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for i, r in enumerate(todo):
            td = time.time()
            try:
                m = build_one(r, conv, ocr_conv, args.ocr, args.out, run_id, cfg,
                              token_count, token_model, captions_enabled)
            except Exception as e:                     # never lose the whole run
                err = "%s: %s" % (type(e).__name__, e)
                os.makedirs(os.path.join(args.out, r["id"]), exist_ok=True)
                bb._write_json(os.path.join(args.out, r["id"], "report.json"),
                               _failure_report(r, "pdf" if r["ext"] == "pdf" else "html",
                                               err, []))
                m = {"doc_id": r["id"], "source_relpath": r["rel"],
                     "lane": "pdf" if r["ext"] == "pdf" else "html",
                     "status": "failed", "markdown_sha256": "", "error": err}
            mf.write(json.dumps(m) + "\n")
            mf.flush()
            print("  [%d/%d] %s %s (%.1fs)" % (i + 1, len(todo), m["status"],
                                               r["rel"], time.time() - td),
                  file=sys.stderr)
            sys.stderr.flush()
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
