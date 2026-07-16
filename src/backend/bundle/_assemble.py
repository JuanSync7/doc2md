"""
title: Output-bundle assembler (private)
layer: backend
public_api: no
summary: Combine body markdown + heading outline + validator report into the doc2md bundle dict — pure, no disk, no LLM.
"""
# 3.6-compatible, stdlib only. PURE: strings/dicts in, a bundle dict out — the file
# I/O is the caller's (scripts/build_bundle.py). This is the single place that knows
# the bundle shape (docs/design/output-contract.md):
#   * structure.json  — doc metadata + the faithful heading outline (+ token counts)
#   * report.json      — the validator verdict (losslessness + metrics + status), NO LLM
#   * document.md      — the body with a YAML front-matter block mapping source -> markdown
# It combines document_outline (sections) + build_report (validate) + front_matter
# (ingest); each is imported through its package's public API, never a private module.
import re
from collections import OrderedDict

from backend.ingest import front_matter
from backend.sections import document_outline, outline_coverage
from backend.validate import (build_report, image_report, caption_report,
                              outline_report, savings_report)

__all__ = ["assemble_bundle"]

# A body image link into the extracted images/ dir. Counting these off the BODY (the
# exact bytes markdown_sha256 covers) — not the outline — makes the integrity gate
# ground-truth: the outline may drop an image into a skipped region, but document.md is
# authoritative for what was referenced.
_BODY_IMG = re.compile(r"!\[[^\]]*\]\(images/[^)\s]+\)")


def _walk(nodes):
    """Yield every node in an outline tree, depth-first."""
    for nd in nodes:
        yield nd
        for c in _walk(nd["children"]):
            yield c


def _structure_summary(outline_nodes, has_toc, body_md):
    # type: (list, bool, str) -> dict
    """Report-side rollup of the outline: deepest heading level, the largest section
    by tokens (the fast 'does any section blow the budget?' triage), and the
    outline-COVERAGE gate — measured from the built nodes back against the body, so
    any outline-builder bug that drops a region degrades the report instead of
    passing silently (see ``validate.outline_report``)."""
    max_depth = 0
    largest = 0
    for nd in _walk(outline_nodes):
        if nd["level"] > max_depth:
            max_depth = nd["level"]
        if nd["subtree_tokens"] > largest:
            largest = nd["subtree_tokens"]
    cov = outline_coverage(body_md, outline_nodes)
    return {"max_depth": max_depth, "largest_section_tokens": largest,
            "has_toc": bool(has_toc),
            "coverage": outline_report(cov["content_lines"], cov["covered_lines"],
                                       cov["toc_lines"], cov["uncovered_lines"],
                                       cov["first_uncovered"])}


def _images_block(body_md, extras):
    # type: (str, dict) -> dict
    """The deterministic image-extraction integrity block (see ``validate.image_report``).

    ``referenced`` is measured here from the BODY markdown (the ``![](images/..)`` links
    ``document.md`` actually carries — authoritative even when the outline drops an image
    into a skipped region); the rest are supplied by the writer via ``extras``
    (``images_extracted``/``image_files``/``images_missing`` from the extraction plan,
    plus the on-disk ``image_verified``/``image_orphans`` it measures after writing).
    Defaults are the clean case (everything verified, no orphans) so a pure caller that
    only knows the plan still gets a coherent block."""
    ex = extras or {}
    referenced = len(_BODY_IMG.findall(body_md or ""))
    unique_files = int(ex.get("image_files", ex.get("images_extracted", referenced)))
    extracted = int(ex.get("images_extracted", referenced))
    missing = int(ex.get("images_missing", 0))
    orphans = int(ex.get("image_orphans", 0))
    verified = int(ex.get("image_verified", unique_files))
    return image_report(referenced, extracted, unique_files, missing, orphans, verified)


def _captions_block(images_block, extras):
    # type: (dict, dict) -> dict
    """The build-time caption-coverage block (see ``validate.caption_report``): every
    unique image starts PENDING. The caption enrichment pass rewrites this block in the
    persisted ``report.json`` once it has run; here we only record what is EXPECTED so a
    freshly built (un-captioned) bundle already reports its caption debt."""
    ex = extras or {}
    expected = images_block["unique_files"]
    enabled = bool(ex.get("captions_enabled", False))
    return caption_report(enabled, expected, 0, 0, 0, expected)


def _frontmatter(doc_id, source_format, lane, source_relpath, source_sha256,
                 markdown_sha256, converter, lossless, generated_run, source_meta):
    # type: (...) -> OrderedDict
    """The document.md front matter — this IS the source->markdown map, self-contained
    per file. Source core properties (title/author/...) are flattened under a
    ``source_`` prefix so they survive for citation without colliding with pipeline keys.
    """
    fm = OrderedDict()
    fm["doc_id"] = doc_id
    fm["source_format"] = source_format
    fm["lane"] = lane
    fm["source_relpath"] = source_relpath or ""
    if source_sha256:
        fm["source_sha256"] = source_sha256
    fm["markdown_sha256"] = markdown_sha256
    fm["converter"] = converter
    fm["lossless"] = "true" if lossless else "false"
    fm["structure"] = "structure.json"
    fm["report"] = "report.json"
    fm["images"] = "images/"
    if generated_run:
        fm["generated_run"] = generated_run
    for k, v in (source_meta or OrderedDict()).items():
        fm["source_%s" % k] = v
    return fm


def assemble_bundle(doc_id, source_relpath, source_format, lane,
                    source_text, body_md, source_meta=None, token_count=None,
                    token_model=None, converter=None, source_sha256=None,
                    warnings=None, extras=None, timing_ms=None,
                    losslessness=None, generated_run=None):
    # type: (str, str, str, str, str, str, dict, object, str, str, str, list, dict, dict, dict, str) -> dict
    """Assemble the doc2md bundle for one document — PURE (no disk, no LLM).

    ``body_md`` is the markdown BODY only (front matter stripped): the validator gate
    scores the body, and the assembler prepends its own front matter, so a
    front-matter block must never reach the outline or the recall gate. ``source_text``
    is the converter-blind ground truth (office lane) or the reference text (other
    lanes). ``token_count`` is an optional ``str -> int`` tokenizer threaded into both
    the outline and the report so section/token counts agree; ``token_model`` names it.

    Returns ``{"document_md": str, "structure": dict, "report": dict}``. The lane
    determines how losslessness is graded (see ``build_report``): office is a hard
    ``recall == 1.0`` gate; other lanes pass an explicit best-effort ``losslessness``.

    NOTE ON LINE INDICES: every ``line_span`` / image ``line`` in the structure is
    relative to the markdown BODY (``body_md`` — the exact bytes ``markdown_sha256``
    covers), NOT to ``document_md``. The body is frontmatter-independent, so these
    indices stay stable across runs even though the front matter (which carries a
    per-run ``generated_run``) changes; a consumer that indexes ``document.md`` directly
    must first strip its leading front-matter block.
    """
    outline = document_outline(body_md, token_count=token_count)
    tmodel = token_model or ("char-estimate/4" if token_count is None else "custom")

    verdict = build_report(source_text, body_md, lane=lane,
                           losslessness=losslessness, token_count=token_count)

    # Measured image metadata (extras["image_meta"]: {image_id: {width,height,bytes}},
    # probed by the writer from the actual extracted bytes) annotates the outline's
    # image nodes so consumers can budget/lay out without re-reading the files.
    img_meta = (extras or {}).get("image_meta") or {}
    if img_meta:
        for nd in _walk(outline["outline"]):
            for im in nd["images"]:
                meta = img_meta.get(im.get("image_id", ""))
                if meta:
                    # Whitelist merge: only the measured keys. A blind update()
                    # would let a writer-supplied dict clobber contract fields
                    # (caption/alt/ref) the enrichment stages own.
                    for k in ("bytes", "width", "height"):
                        if k in meta:
                            im[k] = meta[k]

    structure = OrderedDict()
    structure["doc_id"] = doc_id
    structure["source_format"] = source_format
    structure["lane"] = lane
    # The exact body bytes every line_span below indexes into — lets a consumer verify
    # structure.json still matches document.md/report.json before trusting a span
    # (the three files are written sequentially, not transactionally).
    structure["markdown_sha256"] = verdict["markdown_sha256"]
    structure["token_model"] = tmodel
    structure["total_tokens"] = outline["total_tokens"]
    structure["has_toc"] = outline.get("has_toc", False)
    structure["outline"] = outline["outline"]

    report = OrderedDict()
    report["doc_id"] = doc_id
    report["lane"] = lane
    report["source_format"] = source_format
    report["converter"] = converter or "doc2md/0.1.0"
    # Run provenance: a FAILED doc publishes report.json only (no document.md), so the
    # report must carry the run id itself or the failure has no provenance at all.
    if generated_run:
        report["generated_run"] = generated_run
    report["source_relpath"] = source_relpath or ""
    report["source_sha256"] = source_sha256 or ""
    report["markdown_sha256"] = verdict["markdown_sha256"]
    report["status"] = verdict["status"]
    report["losslessness"] = verdict["losslessness"]
    # Names the tokenizer behind content.tokens (mirrors structure.json) so the
    # number is self-describing without opening the sibling file.
    report["token_model"] = tmodel
    report["content"] = verdict["content"]
    # Representation savings: emitted only when the writer MEASURED the source side
    # (extras["source_repr_chars"] — for the office lane, the decompressed chars of
    # every XML part parsed). Lane-honest: a writer with no meaningful raw-text
    # representation (e.g. PDF: binary glyphs) simply omits the key and no block is
    # emitted — never an invented number. Informational only; no gate, no status.
    src_repr = int((extras or {}).get("source_repr_chars", 0) or 0)
    if src_repr:
        report["savings"] = savings_report(
            src_repr, len(body_md or ""),
            source_repr=(extras or {}).get("source_repr", "ooxml-xml"))
    report["structure"] = _structure_summary(outline["outline"],
                                              outline.get("has_toc", False), body_md)
    report["structural_errors"] = verdict["structural_errors"]
    report["structural_warnings"] = verdict["structural_warnings"]
    report["warnings"] = list(warnings or [])
    images = _images_block(body_md, extras)
    report["images"] = images
    report["captions"] = _captions_block(images, extras)
    report["timing_ms"] = dict(timing_ms or {})

    # A degraded image gate (missing/corrupt/orphaned pixels) is a real loss the
    # token-recall gate cannot see, so it DEGRADES a document that would otherwise be
    # ``ok`` — but it never promotes a ``failed`` doc, and never touches losslessness.
    if images["gate"] != "pass" and report["status"] == "ok":
        report["status"] = "degraded"

    # Same for outline coverage: content lines outside every outline node are a
    # structure loss the text gate cannot see (document.md still has them; navigation,
    # carding and captioning do not). Degrade and record — never silent.
    cov = report["structure"]["coverage"]
    if cov["gate"] != "pass":
        report["warnings"].append(
            {"code": "outline_uncovered_content",
             "detail": "%d content line(s) fall outside every outline node "
                       "(first at body line(s) %s)"
                       % (cov["uncovered_lines"],
                          ", ".join(str(i) for i in cov.get("first_uncovered", [])))})
        if report["status"] == "ok":
            report["status"] = "degraded"

    lossless = verdict["losslessness"].get("gate") == "pass"
    fm = _frontmatter(doc_id, source_format, lane, source_relpath, source_sha256,
                      verdict["markdown_sha256"], report["converter"], lossless,
                      generated_run, source_meta)
    document_md = front_matter(fm) + "\n" + body_md
    return {"document_md": document_md, "structure": structure, "report": report}
