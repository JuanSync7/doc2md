#!/usr/bin/env python3
"""Caption enrichment for the doc2md output bundle — fill structure.json image captions.

The deterministic office lane extracts every body picture to ``<doc_id>/images/`` and
records a caption-less image node (``caption: null``) in ``structure.json``. This pass adds
the ONE remaining piece: a VLM-generated, search-indexable caption per image. It:

  * reads the pixels straight from the bundle's ``images/`` (already content-addressed +
    deduplicated by the writer — so an image reused across pages is captioned ONCE);
  * captions each image IN THE DOCUMENT'S CONTEXT — the prompt carries the figure's document
    title, its section heading-path, and the surrounding body text (both from the bundle we
    already produced), so the model grounds the figure ("in the DDR verification testbench,
    this block diagram shows …") instead of only naming the picture in isolation;
  * routes every image through the ONE shared formula-safe caption tool
    (``scripts/image_caption.py``), so a genuine figure/formula is kept and only the model's
    own "this is a logo" verdict drops furniture;
  * writes the caption back into ``structure.json`` in place, and records the coverage
    verdict in ``report.json``'s ``captions`` block (gate + counts + model) exactly as the
    office lane records losslessness — but touches ONLY that block: ``document.md``,
    ``status`` and the losslessness verdict stay untouched, so enrichment is still a
    detachable, re-runnable overlay;
  * is idempotent + cached: the cache key is (image bytes + the exact grounded prompt), so a
    re-run re-captions nothing while a prompt change (domain/context) correctly re-captions;
    a VLM outage leaves an image PENDING (caption stays null) for a later run.

The prompt is TUNABLE so its quality can be measured, not assumed: ``--domain`` prepends
domain grounding, ``--prompt-file`` replaces the base instruction, ``--no-context`` drops the
per-figure document context, ``--context-radius`` sizes the surrounding-text window. Every
run writes ``_caption_coverage.jsonl`` and prints a useful/furniture/pending pass-rate — the
feedback loop for iterating the prompt (add ``--no-cache`` to force fresh captions).

Runs on the plain host python (stdlib + urllib VLM client); PIL optional (downscale only),
soffice optional (emf/wmf render only).
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
sys.path.insert(0, _HERE)

from backend.ingest import (  # noqa: E402
    caption_is_useful, caption_type_is_furniture, load_ingest_config)
from backend.validate import caption_report  # noqa: E402  (report caption-gate policy)
import image_caption as ic  # noqa: E402  (the shared, formula-safe caption tool)

COV_NAME = "_caption_coverage.jsonl"
CACHE_NAME = "_captions.jsonl"
_TITLE_RE = re.compile(r'^source_title:\s*"(.*)"\s*$', re.M)


def build_base_prompt(base, domain):
    # type: (str, str) -> str
    """Base caption instruction with optional CORPUS-level ``domain`` grounding prepended
    (applied uniformly to every image — never per-document targeting)."""
    domain = (domain or "").strip()
    return (domain + "\n\n" + base) if domain else base


def context_block(title, path, nearby):
    # type: (str, str, str) -> str
    """The per-figure grounding appended to the base prompt. It gives the model the figure's
    place in the document (title + section path + surrounding text) and instructs it to ground
    the caption there while describing ONLY what the pixels show (no context-sourced invention)."""
    lines = ["\n\nGround the caption in this document context — use it to say what the figure "
             "IS FOR and to disambiguate its elements, but describe only what is actually "
             "visible in the image (never invent details from the context):"]
    if title:
        lines.append('- Document: "%s"' % title)
    if path:
        lines.append('- Section: %s' % path)
    if nearby:
        lines.append('- Surrounding text: "%s"' % nearby)
    return "\n".join(lines)


def _doc_title(doc_md, structure):
    # type: (str, dict) -> str
    """Document title for grounding: the frontmatter ``source_title`` if present, else the
    first level-1 heading that is not the synthetic preamble node."""
    m = _TITLE_RE.search(doc_md or "")
    if m and m.group(1).strip():
        return m.group(1).strip()
    for n in structure.get("outline", []):
        if n.get("level") == 1 and n.get("title") and n["title"] != "(preamble)":
            return n["title"]
    return ""


def _body_lines(doc_md):
    # type: (str) -> list
    """The markdown BODY lines (what the image ``line`` indices address): everything after the
    closing frontmatter fence and the single separator newline assemble_bundle inserts."""
    m = re.match(r"^---\n.*?\n---\n", doc_md or "", re.S)
    body = doc_md[m.end():] if m else (doc_md or "")
    if body.startswith("\n"):
        body = body[1:]
    return body.splitlines()


def _nearby_text(body_lines, line, lo, hi, radius, max_chars=700):
    # type: (list, int, int, int, int, int) -> str
    """Surrounding prose for one image: non-empty, non-image, non-heading body lines within
    ``radius`` of the image's line, clamped to its section span ``[lo, hi)`` and capped."""
    a = max(lo, line - radius)
    b = min(hi, line + radius + 1)
    picked = []
    for i in range(a, b):
        if i < 0 or i >= len(body_lines):
            continue
        s = body_lines[i].strip()
        if not s or s.startswith("![") or s.startswith("#") or set(s) <= set("|- "):
            continue                       # drop image links, headings, table rules
        picked.append(s)
    text = re.sub(r"\s+", " ", " ".join(picked)).strip()
    return text[:max_chars]


def _images_with_context(structure, body_lines, doc_title, radius, want_context):
    # type: (dict, list, str, int, bool) -> list
    """Walk the outline in order, returning ``(image_node, context_str)`` for every image.
    ``context_str`` is "" when ``want_context`` is off. The heading path is the chain of
    ancestor section titles down to the image's own section."""
    out = []

    def walk(nodes, path):
        for n in nodes:
            title = n.get("title", "")
            here = path + ([title] if title and title != "(preamble)" else [])
            for im in n.get("images", []):
                ctx = ""
                if want_context:
                    ls = n.get("line_span") or [0, len(body_lines)]
                    nearby = _nearby_text(body_lines, im.get("line", 0), ls[0], ls[1], radius)
                    ctx = context_block(doc_title, " > ".join(here), nearby)
                out.append((im, ctx))
            walk(n.get("children", []), here)
    walk(structure.get("outline", []), [])
    return out


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def _sha12(s):
    # type: (str) -> str
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _record(doc_id, im, oc, prompt_sha, grounded):
    # type: (str, dict, object, str, bool) -> dict
    """One QA row per image: the classifier verdict + whether the caption clears the useful
    gate, so prompt quality is measurable across runs/prompts."""
    txt = (oc.text if oc is not None else "") or ""
    return {
        "doc_id": doc_id, "image_id": im.get("image_id", ""), "ref": im.get("ref", ""),
        "kind": (oc.kind if oc is not None else "PENDING"),
        "useful": bool(caption_is_useful(txt)),
        "furniture": bool(caption_type_is_furniture(txt)),
        "truncated": bool(oc.truncated) if oc is not None else False,
        "n_chars": len(txt), "model": (oc.model if oc is not None else ""),
        "grounded": bool(grounded), "prompt_sha": prompt_sha, "caption": txt,
        "ts": int(time.time()),
    }


def _write_captions_to_report(doc_dir, records):
    # type: (str, list) -> None
    """Reflect this caption run in the bundle's ``report.json`` — the SAME way the office
    lane reflects its verdict. Rewrites ONLY the ``captions`` block (coverage counts +
    gate + model + prompt); losslessness, status, content and the markdown fingerprint are
    never touched, so the enrichment stays a detachable overlay. Per-image (dedup by
    ``image_id``) so a picture reused across pages counts once."""
    rp = os.path.join(doc_dir, "report.json")
    try:
        with open(rp, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return
    by_id = {}
    for r in records:
        by_id[r.get("image_id", "")] = r          # verdict is identical across an image's refs
    captioned = furniture = useless = pending = 0
    model = prompt_sha = ""
    for r in by_id.values():
        k = r.get("kind")
        if k == "OK":
            captioned += 1
        elif k == "FURNITURE":
            furniture += 1
        elif k == "USELESS":
            useless += 1
        else:
            pending += 1                          # PENDING / UNAVAILABLE / file gone
        model = model or r.get("model", "")
        prompt_sha = prompt_sha or r.get("prompt_sha", "")
    expected = int((report.get("images") or {}).get("unique_files", len(by_id)))
    # images the build expected but this run never reached (e.g. --only a subset) stay pending
    pending += max(0, expected - (captioned + furniture + useless + pending))
    report["captions"] = caption_report(True, expected, captioned, furniture, useless,
                                         pending, model, prompt_sha)
    _write_atomic(rp, json.dumps(report, indent=2, ensure_ascii=False) + "\n")


def caption_bundle(doc_dir, cache, client, cfg, render_metafile, base_prompt,
                   want_context, radius):
    # type: (str, object, object, object, object, str, bool, int) -> list
    """Caption every unique image of one bundle IN CONTEXT, fill ``structure.json`` captions
    in place, return the per-image QA records. A gated-out (furniture) or unusable caption
    leaves the node's ``caption`` null; a VLM outage leaves it PENDING (re-runnable)."""
    struct_path = os.path.join(doc_dir, "structure.json")
    try:
        with open(struct_path, encoding="utf-8") as f:
            structure = json.load(f)
    except (OSError, ValueError):
        return []
    try:
        with open(os.path.join(doc_dir, "document.md"), encoding="utf-8") as f:
            doc_md = f.read()
    except OSError:
        doc_md = ""
    doc_id = structure.get("doc_id", os.path.basename(doc_dir.rstrip("/")))
    body_lines = _body_lines(doc_md)
    doc_title = _doc_title(doc_md, structure)
    img_dir = os.path.join(doc_dir, "images")

    pairs = _images_with_context(structure, body_lines, doc_title, radius, want_context)
    # Caption each DISTINCT image once (image_id == content sha16); the FIRST occurrence's
    # context is used, then the verdict is applied to every node referencing that image.
    by_id = {}      # image_id -> (context, [nodes])
    for im, ctx in pairs:
        iid = im.get("image_id", "")
        if iid not in by_id:
            by_id[iid] = (ctx, [])
        by_id[iid][1].append(im)

    model = getattr(client, "model", "")
    records = []
    for iid, (ctx, ims) in by_id.items():
        fname = os.path.basename(ims[0].get("ref", ""))
        fmt = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        try:
            with open(os.path.join(img_dir, fname), "rb") as f:
                data = f.read()
        except OSError:
            for im in ims:
                records.append(_record(doc_id, im, None, "", want_context))   # file gone
            continue
        prompt = base_prompt + ctx
        # Cache tag = (model + prompt): the caption changes if EITHER changes, so a re-run with
        # the same model+prompt is a free cache hit, while swapping to a stronger model (or
        # editing the prompt) correctly re-captions instead of returning the stale caption.
        tag = _sha12(model + "\x00" + prompt)
        oc = ic.caption_image(data, fmt, cache, client, cfg, render_metafile,
                              prompt=prompt, key_suffix=tag)
        caption = oc.text if oc.kind == ic.OK else None      # only a USEFUL caption is stored
        for im in ims:
            im["caption"] = caption
            records.append(_record(doc_id, im, oc, tag, want_context))

    _write_atomic(struct_path, json.dumps(structure, indent=2, ensure_ascii=False) + "\n")
    _write_captions_to_report(doc_dir, records)
    return records


def _bundle_dirs(root):
    # type: (str) -> list
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if os.path.isfile(os.path.join(root, n, "structure.json"))]


def _summary(records):
    # type: (list) -> dict
    n = len(records)
    return {
        "images": n,
        "ok": sum(1 for r in records if r["kind"] == "OK"),
        "useful": sum(1 for r in records if r["useful"]),
        "furniture": sum(1 for r in records if r["kind"] == "FURNITURE"),
        "useless": sum(1 for r in records if r["kind"] == "USELESS"),
        "pending": sum(1 for r in records if r["kind"] in ("PENDING", "UNAVAILABLE")),
        "truncated": sum(1 for r in records if r["truncated"]),
    }


def main(argv=None, client=None):
    ap = argparse.ArgumentParser(
        description="Caption a bundle's extracted images IN CONTEXT and fill structure.json "
                    "captions (additive overlay; document.md + report.json untouched).")
    ap.add_argument("--bundles", default=os.path.join(_REPO, "data", "bundles"),
                    help="bundle root written by build_bundle.py (default data/bundles)")
    ap.add_argument("--only", action="append", default=[],
                    help="caption ONLY this doc_id; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N bundles with images")
    ap.add_argument("--domain", default="",
                    help="domain-grounding text prepended to the base caption prompt")
    ap.add_argument("--domain-file", default="", help="read domain grounding from a file")
    ap.add_argument("--prompt-file", default="",
                    help="replace the BASE caption instruction with this file's contents")
    ap.add_argument("--no-context", action="store_true",
                    help="do NOT add per-figure document context (image-only captions)")
    ap.add_argument("--context-radius", type=int, default=12,
                    help="body lines of surrounding text to include as context (default 12)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the caption cache (re-caption fresh — for prompt iteration)")
    ap.add_argument("--vlm-url", default="", help="override the VLM url (default from config)")
    ap.add_argument("--vlm-model", default="", help="override the VLM model (default from config)")
    args = ap.parse_args(argv)

    cfg = load_ingest_config()
    if not os.path.isdir(args.bundles):
        ap.error("bundle root not found: %r (run build_bundle.py first)" % (args.bundles,))

    from vlm_client import CAPTION_PROMPT
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as f:
            base = f.read().strip()
    else:
        domain = args.domain
        if args.domain_file:
            with open(args.domain_file, encoding="utf-8") as f:
                domain = f.read().strip()
        base = build_base_prompt(CAPTION_PROMPT, domain)
    want_context = not args.no_context

    if args.no_cache:
        cache_path = os.path.join(args.bundles, "_captions.nocache.%d.jsonl" % os.getpid())
    else:
        cache_path = os.path.join(args.bundles, CACHE_NAME)
    cache = ic.CaptionCache(cache_path)

    render_metafile = None
    soffice = _find_soffice()
    if soffice:
        render_metafile = ic.soffice_metafile_renderer(soffice)

    if client is None:
        import vlm_client
        client = vlm_client.VlmClient(args.vlm_url or cfg.vlm_url,
                                      model=args.vlm_model or cfg.vlm_model,
                                      max_tokens=cfg.vlm_max_tokens)
        if not client.healthy():
            print("  [vlm] server at %s not reachable -> images stay PENDING (re-run when up)"
                  % (args.vlm_url or cfg.vlm_url), file=sys.stderr)

    dirs = _bundle_dirs(args.bundles)
    if args.only:
        want = set(args.only)
        dirs = [d for d in dirs if d in want]
    if args.limit:
        dirs = [d for d in dirs if _has_images(os.path.join(args.bundles, d))][:args.limit]

    print("caption base-prompt %s (%d chars)%s%s -> %s" % (
        _sha12(base), len(base), " [domain-grounded]" if base != CAPTION_PROMPT else "",
        " [context-grounded]" if want_context else " [image-only]", args.bundles),
        file=sys.stderr)

    cov_path = os.path.join(args.bundles, COV_NAME)
    all_records = []
    for did in dirs:
        recs = caption_bundle(os.path.join(args.bundles, did), cache, client, cfg,
                              render_metafile, base, want_context, args.context_radius)
        if not recs:
            continue
        with open(cov_path, "a", encoding="utf-8") as cf:
            for r in recs:
                cf.write(json.dumps(r, ensure_ascii=False) + "\n")
        all_records.extend(recs)

    if args.no_cache:
        try:
            os.remove(cache_path)
        except OSError:
            pass

    s = _summary(all_records)
    rate = (100.0 * s["useful"] / s["images"]) if s["images"] else 0.0
    print("caption enrich: images=%d ok=%d useful=%d (%.0f%%) furniture=%d useless=%d "
          "pending=%d truncated=%d -> %s" % (
              s["images"], s["ok"], s["useful"], rate, s["furniture"], s["useless"],
              s["pending"], s["truncated"], cov_path), file=sys.stderr)
    return 0 if s["pending"] == 0 else 2      # pending => incomplete (re-run when VLM up)


def _has_images(doc_dir):
    # type: (str) -> bool
    try:
        return any(os.listdir(os.path.join(doc_dir, "images")))
    except OSError:
        return False


def _find_soffice():
    from office_convert import find_soffice
    return find_soffice()


if __name__ == "__main__":
    sys.exit(main())
