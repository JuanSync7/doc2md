#!/usr/bin/env python3
"""Corpus-global figure enrichment: recover the text inside embedded office images.

The deterministic lanes already produce lossless TEXT markdown; SVG vector labels are
captured too. This pass adds the one remaining piece — the text baked as PIXELS into
raster/metafile images (screenshots, block/timing diagrams, FORMULA images) — by routing
every image through the ONE shared caption tool (``scripts/image_caption.py``) behind the
formula-safe gate (``backend.ingest.gate_figures``). It is:

  * additive — it only APPENDS a "## Figures (captioned images)" section; the text lane and
    its recall-1.0 gate are untouched;
  * formula-safe — the gate never drops an informative image; only chrome-placed images
    (headers/masters) and the model's own "this is a logo" verdict are removed;
  * idempotent — the section is regenerated each run; the cache means every image is
    captioned at most once corpus-wide, and a VLM outage leaves a figure PENDING for a re-run.

Runs on the plain host python (stdlib + urllib VLM client); PIL is optional (only for
downscaling oversize images) and soffice optional (only to render emf/wmf).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _HERE)

from backend.ingest import (  # noqa: E402
    doc_id, route_format, ext_of, summarize_routes, normalize_accept,
    ROUTE_OOXML, sniff_image_format, resolve_media_refs, gate_figures, image_markdown,
    caption_cache_key, load_source_root, load_ingest_config)
import image_caption as ic  # noqa: E402  (the shared caption tool)

FIG_HEADING = "## Figures (captioned images)"
COV_NAME = "_figures.jsonl"
_MEDIA = re.compile(r"^(word|ppt|xl)/media/[^/]+$")


def _read_rels(zf):
    # type: (object) -> dict
    """All ``*/_rels/*.rels`` parts as {name: xml} (for media ref-location resolution)."""
    out = {}
    for n in zf.namelist():
        if n.endswith(".rels"):
            try:
                out[n] = zf.read(n).decode("utf-8", "replace")
            except (OSError, KeyError):
                pass
    return out


def extract_office(zip_path, eff_ext):
    # type: (str, str) -> list
    """Enumerate embedded raster/metafile images of one office zip as candidate dicts
    ``{part, sha, fmt, n_bytes, ref, bytes}``. SVG is skipped (its text is already captured
    deterministically by the OOXML lane). ``ref`` is body/chrome/unknown (body-wins)."""
    cands = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            refs = resolve_media_refs(_read_rels(zf))
            for n in zf.namelist():
                if not _MEDIA.match(n):
                    continue
                try:
                    data = zf.read(n)
                except (OSError, KeyError):
                    continue
                fmt = sniff_image_format(data)
                if not fmt or fmt == "svg":          # svg handled deterministically upstream
                    continue
                cands.append({
                    "part": n, "sha": caption_cache_key(data), "fmt": fmt,
                    "n_bytes": len(data), "ref": refs.get(n, "unknown"), "bytes": data,
                })
    except (zipfile.BadZipFile, OSError):
        return []
    return cands


def _corpus_index(all_cands):
    # type: (list) -> dict
    """sha -> {n_docs, occ}: distinct docs a content hash appears in, and its max within-doc
    repetition. Recorded for the validator/analytics; the gate does NOT drop on these."""
    docs = {}
    per_doc = {}
    for did, cands in all_cands:
        seen = {}
        for c in cands:
            seen[c["sha"]] = seen.get(c["sha"], 0) + 1
        for sha, occ in seen.items():
            docs.setdefault(sha, set()).add(did)
            per_doc[sha] = max(per_doc.get(sha, 0), occ)
    return dict((sha, {"n_docs": len(d), "occ": per_doc[sha]}) for sha, d in docs.items())


def _strip_section(md):
    # type: (str) -> str
    """Remove a previously-appended figures section (idempotent regeneration)."""
    i = md.find("\n" + FIG_HEADING)
    if i < 0 and md.startswith(FIG_HEADING):
        i = 0
    return md[:i].rstrip() + "\n" if i >= 0 else md


def _record(did, c, kept, reason, oc, rel_path, idx, captions_enabled):
    # type: (str, dict, bool, str, object, str, dict, bool) -> dict
    return {
        "doc_id": did, "fig_id": "%s:%s" % (did, c["part"].rsplit("/", 1)[-1]),
        "part": c["part"], "sha": c["sha"], "fmt": c["fmt"], "n_bytes": c["n_bytes"],
        "ref": c["ref"], "n_docs": idx.get(c["sha"], {}).get("n_docs", 1),
        "occ_in_doc": idx.get(c["sha"], {}).get("occ", 1),
        "rel_path": rel_path, "kept": bool(kept), "reason": reason,
        "outcome_kind": (oc.kind if oc is not None else ""),
        "caption": (oc.text if oc is not None else ""),
        "caption_sha": (caption_cache_key((oc.text or "").encode("utf-8")) if oc and oc.text else ""),
        "captions_enabled": bool(captions_enabled), "model": (oc.model if oc else ""),
        "truncated": bool(oc.truncated) if oc else False, "ts": int(time.time()),
    }


def enrich_doc(did, cands, index, md_path, assets_dir, cache, client, cfg, render_metafile):
    # type: (...) -> list
    """Gate -> caption (cached) -> dump asset -> regenerate the figures section. Returns the
    per-figure records. A furniture/chrome image is dropped (no model call); a transient VLM
    outage leaves the figure PENDING (recorded, not inlined)."""
    doc_assets = os.path.join(assets_dir, did)
    md_dir = os.path.dirname(md_path)
    decisions = gate_figures([{"cls": None, "area": None, "sha": c["sha"],
                               "ref": c["ref"], "n_bytes": c["n_bytes"],
                               "n_docs": index.get(c["sha"], {}).get("n_docs", 1)}
                              for c in cands])
    records = []
    lines = []
    for c, dec in zip(cands, decisions):
        if not dec.keep:
            records.append(_record(did, c, False, dec.reason, None, "", index, True))
            continue
        oc = ic.caption_image(c["bytes"], c["fmt"], cache, client, cfg, render_metafile)
        if oc.kind == ic.UNAVAILABLE:
            records.append(_record(did, c, True, "pending", oc, "", index, True))
            continue
        if oc.kind == ic.FURNITURE:
            records.append(_record(did, c, False, "furniture", oc, "", index, True))
            continue
        if oc.kind in (ic.UNDECODABLE, ic.TOO_LARGE):
            records.append(_record(did, c, False, oc.kind.lower(), oc, "", index, True))
            continue
        # OK / USELESS / RENDER_FAILED -> keep the image; dump the asset + inline
        try:
            os.makedirs(doc_assets, exist_ok=True)
            short = hashlib.sha256(c["bytes"]).hexdigest()[:16]
            asset = os.path.join(doc_assets, short + "." + c["fmt"])
            with open(asset, "wb") as f:
                f.write(c["bytes"])
            rel = os.path.relpath(asset, md_dir)
        except OSError:
            rel = ""
        cap = oc.text if oc.kind == ic.OK else "figure"     # neutral alt when caption unusable
        lines.append(image_markdown(cap, rel))
        records.append(_record(did, c, True, oc.kind.lower(), oc, rel, index, True))

    try:
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return records
    md = _strip_section(md)
    if lines:
        md = md.rstrip() + "\n\n" + FIG_HEADING + "\n\n" + "\n\n".join(lines) + "\n"
    _write_atomic(md_path, md)
    return records


def _write_atomic(dest, text):
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def _office_sources(src_root, accept):
    rel_of = {}
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            full = os.path.join(root, fn)
            rel_of[os.path.relpath(full, src_root)] = full
    scan = summarize_routes(sorted(rel_of), accept)
    return [(rel_of[rel], rel) for rel in scan.by_lane.get(ROUTE_OOXML, [])]


def main(argv=None, client=None):
    ap = argparse.ArgumentParser(description="Caption embedded office images through the shared "
                                             "formula-safe caption tool (additive; text lane untouched).")
    ap.add_argument("--src", default=load_source_root(), help="source documents root")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "markdown"),
                    help="markdown dir holding <doc_id>.md (default data/markdown)")
    ap.add_argument("--assets", default=os.path.join(_REPO, "data", "assets"),
                    help="asset + cache + records dir (default data/assets)")
    ap.add_argument("--accept", default="", help="accepted formats (default: config / all)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs with images")
    args = ap.parse_args(argv)

    cfg = load_ingest_config()
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))
    accept = (normalize_accept(args.accept) if args.accept.strip()
              else (cfg.accept_formats or None))
    os.makedirs(args.assets, exist_ok=True)
    cache = ic.CaptionCache(os.path.join(args.assets, "_captions.jsonl"))

    # A soffice-backed metafile renderer if one is available (else emf/wmf -> RENDER_FAILED).
    render_metafile = None
    soffice = _find_soffice()
    if soffice:
        render_metafile = ic.soffice_metafile_renderer(soffice)

    # Real VLM client unless the caller injected one (tests). Absent server -> UNAVAILABLE.
    if client is None:
        import vlm_client
        client = vlm_client.VlmClient(cfg.vlm_url, model=cfg.vlm_model,
                                      max_tokens=cfg.vlm_max_tokens)
        if not client.healthy():
            print("  [vlm] server at %s not reachable -> images will be PENDING "
                  "(re-run when up)" % cfg.vlm_url, file=sys.stderr)

    sources = _office_sources(args.src, accept)
    extracted = []
    for full, rel in sources:
        ext = ext_of(rel)
        if route_format(ext) != ROUTE_OOXML:
            continue                        # LibreOffice/legacy media: follow-up (needs soffice pre-convert)
        cands = extract_office(full, ext)
        if cands:
            extracted.append((doc_id(rel), rel, cands))
    index = _corpus_index([(did, cands) for did, _, cands in extracted])
    if args.limit:
        extracted = extracted[:args.limit]

    cov = os.path.join(args.assets, COV_NAME)
    n_docs = n_cap = n_drop = n_pending = 0
    for did, rel, cands in extracted:
        md_path = os.path.join(args.out, did + ".md")
        if not os.path.isfile(md_path):
            print("  [skip] no markdown for %s (run the text lane first)" % rel, file=sys.stderr)
            continue
        recs = enrich_doc(did, cands, index, md_path, args.assets, cache, client, cfg, render_metafile)
        with open(cov, "a", encoding="utf-8") as cf:
            for r in recs:
                cf.write(json.dumps(r) + "\n")
        n_docs += 1
        n_cap += sum(1 for r in recs if r["outcome_kind"] == "OK")
        n_drop += sum(1 for r in recs if not r["kept"])
        n_pending += sum(1 for r in recs if r["reason"] == "pending")
    print("figure enrich: docs=%d captioned=%d dropped=%d pending=%d -> %s"
          % (n_docs, n_cap, n_drop, n_pending, args.assets), file=sys.stderr)
    return 0 if n_pending == 0 else 2       # pending => incomplete (re-run when VLM is up)


def _find_soffice():
    # Same resolver as the office lane (env override -> vendored-in-tree -> PATH), so
    # emf/wmf figure rendering also uses the LibreOffice packaged by setup_libreoffice.py.
    from office_convert import find_soffice
    return find_soffice()


if __name__ == "__main__":
    sys.exit(main())
