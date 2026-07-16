"""
title: Markdown structural validator + lossless conversion gate (private)
layer: backend
public_api: no
summary: Find broken tables/fences/leaked XML in markdown; gate conversions on 100% token recall.
"""
# 3.6-compatible. Stdlib only. Pure policy on markdown STRINGS — no disk. This is
# the validation system every converter lane's output is held to:
#   * validate_markdown  — is the markdown structurally well-formed? (pipe tables
#     consistent, fences/front matter closed, no leaked OOXML, no mojibake)
#   * conversion_report  — did EVERY source token survive into the markdown?
#     (multiset recall via coverage; the OOXML lane must score exactly 1.0)
# It sits ABOVE the ingest package: the measurement primitives (coverage,
# markdown_to_text) come from backend.ingest; nothing in ingest imports back.
# Scripts (office_convert.py, validate_markdown.py) do the file I/O and feed these.
import hashlib
import re
from collections import Counter, OrderedDict, namedtuple

from backend.ingest import coverage, markdown_to_text

__all__ = ["validate_markdown", "conversion_report", "build_report",
           "image_report", "caption_report", "outline_report", "savings_report",
           "MdIssue"]

MdIssue = namedtuple("MdIssue", ["line", "code", "severity", "message"])

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+\S")
_PIPE = re.compile(r"(?<!\\)\|")
_SEP_CELL = re.compile(r"^:?-+:?$")
# Leaked OOXML/DrawingML tags — a converter bug, never legitimate prose. The
# (?<!\\) exempts escaped mentions ("\<w:t>"): prose ABOUT markup is fine.
_XML_LEAK = re.compile(r"(?<!\\)</?(?:w|a|p|c|m|mc|v|o|wp|wps|wpg|pic|xdr|cp|dcterms|dc):"
                       r"[A-Za-z][A-Za-z0-9]*\b")
# NUL/other C0 control chars (tab/newline excluded) and U+FFFD replacement chars —
# both mean the text was damaged somewhere upstream.
_BAD_CHARS = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f�]")

# The blind-spot content gate: when the ASCII token metric is blind (CJK/Cyrillic
# text has alnum content but zero ASCII tokens), the unicode char-3gram recall must
# reach this to pass. Named + overridable (per-call ``content_min``) so it is not a
# magic literal buried in a boolean; mirrors _config.DEFAULT_CONTENT_MIN_RECALL, but
# this module stays config-free (3.6/stdlib-pure) so callers inject if they differ.
_CONTENT_GATE = 0.95


def _cells(line):
    # type: (str) -> list
    """Cell texts of a pipe-table line (backslash-escaped pipes stay literal)."""
    parts = _PIPE.split(line.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return parts


def _is_separator(line):
    # type: (str) -> bool
    cells = _cells(line)
    return bool(cells) and all(_SEP_CELL.match(c.strip()) for c in cells)


def _check_table_block(block, issues):
    # type: (list, list) -> None
    """``block`` is ``[(line_no, text), ...]`` of consecutive pipe-bearing lines."""
    if len(block) < 2:
        return                      # a lone pipe in prose is not a table
    header_no, header = block[0]
    if not _is_separator(block[1][1]):
        issues.append(MdIssue(header_no, "table-no-separator", "warning",
                              "pipe block has no |---| separator row; renders as plain text"))
        return
    want = len(_cells(header))
    for line_no, text in block[1:]:
        got = len(_cells(text))
        if got != want:
            issues.append(MdIssue(line_no, "table-columns", "error",
                                  "row has %d cells, header has %d" % (got, want)))


def validate_markdown(md):
    # type: (str) -> list
    """Structural issues in ``md``, as ``MdIssue`` tuples sorted by line.

    Errors (structure is broken): inconsistent pipe-table column counts, unclosed
    code fence, unclosed front matter, leaked OOXML tags, control/replacement
    characters. Warnings (renders, but degraded): pipe blocks with no separator
    row, heading levels that jump (``#`` -> ``###``). Table/heading rules are
    suspended inside fenced code blocks. An empty document is valid.
    """
    issues = []  # type: list
    lines = (md or "").split("\n")

    # Front matter: only when the FIRST line is exactly ---; find its closer.
    # The region is exempt from markdown rules but NOT from damage checks.
    body_start = 0
    if lines and lines[0].strip() == "---":
        closer = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closer = i
                break
        if closer is None:
            issues.append(MdIssue(1, "frontmatter-unclosed", "error",
                                  "front matter opened at line 1 is never closed"))
        body_start = (closer + 1) if closer is not None else len(lines)
        for i in range(0, body_start):
            if _BAD_CHARS.search(lines[i]):
                issues.append(MdIssue(i + 1, "bad-chars", "error",
                                      "control or replacement character in front matter"))

    in_fence = False
    fence_open_line = 0
    last_heading = 0
    block = []  # type: list
    for idx in range(body_start, len(lines)):
        text = lines[idx]
        no = idx + 1
        if _BAD_CHARS.search(text):
            issues.append(MdIssue(no, "bad-chars", "error",
                                  "control or replacement character in line"))
        if _FENCE.match(text):
            if block:
                _check_table_block(block, issues)
                block = []
            in_fence = not in_fence
            fence_open_line = no
            continue
        if in_fence:
            continue
        if _XML_LEAK.search(text):
            issues.append(MdIssue(no, "xml-leak", "error",
                                  "raw OOXML tag leaked into markdown"))
        h = _HEADING.match(text)
        if h:
            level = len(h.group(1))
            if last_heading and level > last_heading + 1:
                issues.append(MdIssue(no, "heading-jump", "warning",
                                      "heading level jumps from %d to %d"
                                      % (last_heading, level)))
            last_heading = level
        if _PIPE.search(text):
            block.append((no, text))
        elif block:
            _check_table_block(block, issues)
            block = []
    if block:
        _check_table_block(block, issues)
    if in_fence:
        issues.append(MdIssue(fence_open_line, "fence-unclosed", "error",
                              "code fence is never closed"))
    return sorted(issues, key=lambda i: (i.line, i.code))


_UNI_STRIP = re.compile(r"[\W_]+", re.U)


def _unicode_ngram_recall(source_text, target_text, n=3):
    # type: (str, str, int) -> float
    """Char n-gram recall over the UNICODE alphanumeric stream.

    The sibling ``coverage.char_ngram_recall`` strips to ``[a-z0-9]`` — blind to
    CJK/Cyrillic/Greek. This variant keeps every unicode letter/digit, so it
    still falls when non-ASCII text is dropped; ``1.0`` below ``n`` chars."""
    s = _UNI_STRIP.sub("", (source_text or "").lower())
    t = _UNI_STRIP.sub("", (target_text or "").lower())
    if len(s) < n:
        return 1.0
    src = Counter(s[i:i + n] for i in range(len(s) - n + 1))
    tgt = Counter(t[i:i + n] for i in range(len(t) - n + 1))
    total = sum(src.values())
    covered = 0
    for g, c in src.items():
        have = tgt.get(g, 0)
        covered += c if have >= c else have
    return covered / float(total)


def conversion_report(source_text, md, content_min=_CONTENT_GATE):
    # type: (str, str, float) -> dict
    """The conversion gate: is ``md`` a STRUCTURALLY SOUND, LOSSLESS rendering
    of ``source_text``?

    ``valid`` requires token recall of exactly 1.0 (every source token occurrence
    survives — the OOXML lane's contract; docling's lane uses the softer
    ``is_lossy_explained`` gate instead) AND zero structural errors. Warnings do
    not fail the gate. The token metric is ASCII-only, so it is BLIND to non-ASCII
    alphanumerics (CJK/Cyrillic/Greek). Whenever the source carries a meaningful
    amount of such text — whether it is the WHOLE source (pure CJK, zero ASCII
    tokens) OR MIXED in beside ASCII (``n_source > 0`` yet CJK present) — a token
    recall of 1.0 cannot certify those characters survived, so the unicode char-3gram
    content recall must ALSO reach ``content_min``. This closes the mixed-script hole
    where every ASCII token survives (recall reads a vacuous 1.0) while the CJK text is
    silently dropped. Safe for this gate's only caller (the order-preserving OOXML
    lane); the docling lane, which may reorder, uses ``is_lossy_explained`` instead.
    """
    md_text = markdown_to_text(md)
    rep = coverage(source_text, md_text)
    content = _unicode_ngram_recall(source_text, md_text)
    issues = validate_markdown(md)
    n_err = sum(1 for i in issues if i.severity == "error")
    n_warn = len(issues) - n_err
    # Count non-ASCII alphanumerics the ASCII token metric cannot see (3.6: no
    # str.isascii()). >= 3 so at least one 3-gram exists to score; short-circuit early.
    nonascii = 0
    for ch in (source_text or ""):
        if ord(ch) > 127 and ch.isalnum():
            nonascii += 1
            if nonascii >= 3:
                break
    content_gated = nonascii >= 3
    return {
        "valid": (rep.recall == 1.0 and n_err == 0
                  and (not content_gated or content >= content_min)),
        "recall": round(rep.recall, 6),
        "content_recall": round(content, 6),
        "n_source": rep.n_source,
        "n_covered": rep.n_covered,
        "n_missing": rep.n_missing,
        "missing_top": rep.missing_top,
        "errors": n_err,
        "warnings": n_warn,
        "issues": [list(i) for i in issues],
    }


_LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_IMG_MD = re.compile(r"!\[[^\]]*\]\([^)\s]+")
# A hyperlink: [text](url) that is neither an image (!) nor escaped literal text (\[).
_LINK_MD = re.compile(r"(?<![!\\])\[[^\]]*\]\([^)\s]+")


def _content_metrics(md, token_count=None):
    # type: (str, object) -> dict
    """Pure structural counts over ``md`` (fenced code excluded from prose rules).

    ``tokens`` uses ``token_count`` when supplied, else a ~4-chars/token estimate —
    the same convention as ``document_outline`` so the two agree under one tokenizer.
    """
    lines = md.split("\n")
    if token_count is None:
        tokens = sum((len(ln) + 3) // 4 for ln in lines)
    else:
        tokens = sum(token_count(ln) for ln in lines)
    headings = tables = lists = fences = images = links = 0
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if _FENCE.match(ln):
            fences += 1
            in_fence = not in_fence
            i += 1
            continue
        if not in_fence:
            if _HEADING.match(ln):
                headings += 1
            if _LIST.match(ln):
                lists += 1
            images += len(_IMG_MD.findall(ln))
            links += len(_LINK_MD.findall(ln))
            if (i + 1 < n and _PIPE.search(ln) and _is_separator(lines[i + 1])
                    and _PIPE.search(lines[i + 1])):
                tables += 1
        i += 1
    return {
        "chars": len(md), "tokens": tokens, "headings": headings,
        "tables": tables, "images": images, "links": links, "lists": lists,
        "code_blocks": fences // 2, "formulas": md.count("$$") // 2,
    }


def build_report(source_text, md, lane="office", losslessness=None,
                 token_count=None, content_min=_CONTENT_GATE):
    # type: (str, str, str, dict, object, float) -> dict
    """Assemble the validator's verdict for ``report.json`` — pure, no disk, no LLM.

    This is the machine-checkable core of the bundle report: the losslessness block,
    structural error/warning counts, content metrics, a triage ``status`` and the
    markdown fingerprint. The bundle-writer script merges this with the identity
    fields (``doc_id``), disk-derived hashes, the outline-derived ``structure`` block,
    ``warnings``, ``extras`` and ``timing_ms`` to produce the final report.

    Losslessness is lane-honest:
      * ``lane == "office"`` — computed here from the converter-blind ground truth via
        ``conversion_report``: ``method="ooxml-ground-truth"``, a hard ``recall == 1.0``
        gate (``gate`` is ``"pass"`` / ``"fail"``).
      * any other lane (e.g. ``"pdf"``) — there is no ground-truth semantic tree to grade
        against, so the caller passes an explicit ``losslessness`` dict (best-effort text
        coverage). ``gate`` should be ``"best-effort"``, never ``"pass"``.

    ``status``: ``failed`` if the gate failed or any structural error exists; else
    ``degraded`` if any warning; else ``ok``.
    """
    issues = validate_markdown(md)
    n_err = sum(1 for i in issues if i.severity == "error")
    n_warn = len(issues) - n_err

    if lane == "office":
        rep = conversion_report(source_text, md, content_min=content_min)
        loss = {
            "method": "ooxml-ground-truth",
            "token_recall": rep["recall"],
            "content_recall": rep["content_recall"],
            "missing_tokens": rep["missing_top"] if not rep["valid"] else [],
            "gate": "pass" if rep["valid"] else "fail",
        }
    else:
        # Non-office lanes have NO ground-truth semantic tree to grade against, so they
        # cannot claim a provable pass. Take the caller's best-effort coverage block but
        # coerce the gate away from "pass" (whether missing OR mistakenly supplied as
        # "pass") — the lane-asymmetry contract must hold structurally, not on trust.
        loss = dict(losslessness or {"method": "unmeasured"})
        if loss.get("gate") in (None, "pass"):
            loss["gate"] = "best-effort"

    if loss.get("gate") == "fail" or n_err > 0:
        status = "failed"
    elif n_warn > 0:
        status = "degraded"
    else:
        status = "ok"

    return {
        "markdown_sha256": hashlib.sha256((md or "").encode("utf-8")).hexdigest(),
        "status": status,
        "losslessness": loss,
        "content": _content_metrics(md, token_count=token_count),
        "structural_errors": n_err,
        "structural_warnings": n_warn,
    }


def image_report(referenced, extracted, unique_files, missing, orphans, verified):
    # type: (int, int, int, int, int, int) -> dict
    """The deterministic image-extraction integrity block for ``report.json``.

    This is the office text gate's twin, for pixels. Body images are HTML-comment
    sentinels the token-recall metric cannot see, so a dropped, un-extracted or
    corrupt picture is an INVISIBLE loss to the losslessness gate. This block makes
    that loss visible and gate-able:

      * ``referenced``   — ``![](images/..)`` links present in the markdown body
      * ``extracted``    — body sentinels that resolved to real bytes (== referenced
        unless bytes were missing)
      * ``unique_files`` — distinct content-addressed files expected under ``images/``
      * ``missing``      — referenced pictures whose bytes were ABSENT in the package
      * ``orphans``      — files on disk with no body reference (0 after GC)
      * ``verified``     — files whose on-disk ``sha256[:16]`` matches their filename
        (content-addressed integrity: the bytes actually landed intact)

    ``gate`` is ``pass`` iff nothing is missing, no orphan files remain, every body
    reference resolved, AND every expected file is present and content-verified;
    otherwise ``degraded``. A non-pass here DEGRADES the document status but never
    fails the losslessness gate — the text is still whole."""
    intact = (missing == 0 and orphans == 0 and extracted == referenced
              and verified == unique_files)
    b = OrderedDict()
    b["referenced"] = referenced
    b["unique_files"] = unique_files
    b["extracted"] = extracted
    b["missing"] = missing
    b["orphans"] = orphans
    b["verified"] = verified
    b["gate"] = "pass" if intact else "degraded"
    return b


def outline_report(content_lines, covered_lines, toc_lines, uncovered_lines,
                   first_uncovered=None):
    # type: (int, int, int, int, list) -> dict
    """The outline-coverage block for ``report.json`` — the guardrail that gates
    STRUCTURE loss the way ``token_recall`` gates text loss.

    The counts come from ``sections.outline_coverage`` (every non-blank body line
    classified as covered-by-a-node, intentional TOC furniture, or lost):

      * ``content_lines``   — non-blank lines in the markdown body
      * ``covered_lines``   — lines inside some outline node's ``line_span``
      * ``toc_lines``       — lines outside every span that are TOC furniture
        (dot-leader entries / bare page numbers / a ``Contents`` header) — the one
        region the outline skips ON PURPOSE
      * ``uncovered_lines`` — lines outside every span that are real content: loss
      * ``first_uncovered`` — up to 5 offending 0-based line numbers, for triage
      * ``ratio``           — (covered + toc) / content, 1.0 when content is empty

    ``gate`` is ``pass`` iff ``uncovered_lines == 0`` — every content line is either
    in the outline or an intentional TOC skip. Otherwise ``degraded``: the text is
    still whole in ``document.md`` (so never a losslessness fail), but navigation,
    carding and captioning walk the outline, so lost structure must degrade the
    document status, never hide."""
    accounted = covered_lines + toc_lines
    b = OrderedDict()
    b["content_lines"] = content_lines
    b["covered_lines"] = covered_lines
    b["toc_lines"] = toc_lines
    b["uncovered_lines"] = uncovered_lines
    b["ratio"] = round(float(accounted) / content_lines, 4) if content_lines else 1.0
    if first_uncovered:
        b["first_uncovered"] = list(first_uncovered)
    b["gate"] = "pass" if uncovered_lines == 0 else "degraded"
    return b


def savings_report(source_repr_chars, markdown_chars, source_repr="ooxml-xml"):
    # type: (int, int, str) -> dict
    """The representation-savings block for ``report.json`` — how much smaller the
    markdown is than the raw source representation it replaces.

    ``source_repr_chars`` is the decompressed size (chars) of the source parts the
    converter actually parsed (for the OOXML lane: every XML part read from the zip)
    — i.e. what a consumer would otherwise have to feed downstream. ``markdown_chars``
    is the converted body. Both sides are CHARS, measured identically, so the ratio is
    tokenizer-independent (token views are derivable — deliberately not stored).
    Purely informational: no gate, never touches ``status``."""
    src = int(source_repr_chars)
    md = int(markdown_chars)
    b = OrderedDict()
    b["source_repr"] = source_repr
    b["source_chars"] = src
    b["markdown_chars"] = md
    # A 0-char markdown from a 0-char source saved nothing (neutral 1.0); a 0-char
    # markdown from a real source reads 0.0 — the GATES fail such a doc, this block
    # just avoids the division.
    b["reduction_ratio"] = round(src / float(md), 2) if md else (1.0 if not src else 0.0)
    b["saved_pct"] = round(100.0 * (1.0 - md / float(src)), 2) if src else 0.0
    return b


def caption_report(enabled, expected, captioned, furniture, useless, pending,
                   model="", prompt_sha=""):
    # type: (bool, int, int, int, int, int, str, str) -> dict
    """The caption-coverage block for ``report.json`` — the enrichment overlay's own
    gate, kept DELIBERATELY SEPARATE from ``status``/losslessness.

    Captioning is a re-runnable, non-deterministic pass that runs AFTER the lossless
    build; folding it into ``status`` would make a perfectly lossless document read as
    "degraded" merely because the VLM has not run yet. So this block carries its own
    verdict instead:

      * ``expected``  — unique captionable images in the bundle
      * ``captioned`` — images with a stored, useful caption
      * ``furniture`` — images the model itself classified as furniture (intentional null)
      * ``useless``   — captions that failed the useful gate but were not furniture
      * ``pending``   — images with no terminal verdict yet (never run / VLM outage)

    ``gate``: ``disabled`` when captioning is off; ``pending`` before the first run
    (nothing attempted); ``complete`` when every expected image reached a terminal
    verdict (``pending == 0``); ``incomplete`` when a run left images uncaptioned
    (re-run when the VLM is back)."""
    attempted = captioned + furniture + useless
    b = OrderedDict()
    b["enabled"] = bool(enabled)
    b["expected"] = expected
    b["captioned"] = captioned
    b["furniture"] = furniture
    b["useless"] = useless
    b["pending"] = pending
    b["model"] = model or ""
    b["prompt_sha"] = prompt_sha or ""
    if not enabled:
        b["gate"] = "disabled"
    elif expected == 0 or pending == 0:
        b["gate"] = "complete"
    elif attempted == 0:
        b["gate"] = "pending"
    else:
        b["gate"] = "incomplete"
    return b
