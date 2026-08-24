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

from ._mdstructure import md_structure

__all__ = ["validate_markdown", "conversion_report", "build_report",
           "image_report", "caption_report", "outline_report", "savings_report",
           "structure_fidelity_report", "MdIssue"]

MdIssue = namedtuple("MdIssue", ["line", "code", "severity", "message"])

# A fenced code block, read as CommonMark §4.5 defines it. A single "is this a
# fence line" regex cannot answer the question, because the CLOSING rule depends
# on the OPENER: the closer must use the SAME character and a run AT LEAST AS
# LONG, with nothing but spaces after it. Toggling on "any fence line" is how a
# ``~~~`` line inside a ``` block closed it — the rest of the document was then
# read as code, the ``` that really closed it opened a phantom block, and the
# resulting `fence-unclosed` error made `build_report` report status="failed" on
# markdown a renderer is perfectly happy with. That verdict withdraws a good
# bundle, so this reader has to get the pair right rather than the line.
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
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


def _fence_opener(line):
    # type: (str) -> tuple
    """``(character, run length)`` if ``line`` OPENS a fenced block, else ``()``.

    CommonMark §4.5: an opener is three or more backticks or three or more
    tildes, indented at most three spaces, optionally followed by an info string
    — and a BACKTICK fence's info string may not itself contain a backtick
    (``` ```a`b ``` ``` is a paragraph, not a code block), while a tilde fence's
    may. Both facts are read off the spec here; ``_mdstructure`` reads the same
    spec separately, because two independent readings of the emitted markdown is
    the point of having two readers."""
    m = _FENCE_LINE.match(line)
    if not m:
        return ()
    run, info = m.group(1), m.group(2)
    if run[0] == "`" and "`" in info:
        return ()
    return (run[0], len(run))


def _fence_closes(line, char, length):
    # type: (str, str, int) -> bool
    """Does ``line`` CLOSE a fence opened by ``length`` x ``char``?

    Same character, a run at least as long as the opener, and nothing on the line
    after it but spaces. A shorter run, the other character, or any trailing text
    is CONTENT of the block, which is exactly what a ``~~~`` inside a ``` block
    is."""
    m = _FENCE_CLOSE.match(line)
    if not m:
        return False
    run = m.group(1)
    return run[0] == char and len(run) >= length


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

    fence_char = ""
    fence_len = 0
    fence_open_line = 0
    last_heading = 0
    block = []  # type: list
    for idx in range(body_start, len(lines)):
        text = lines[idx]
        no = idx + 1
        if _BAD_CHARS.search(text):
            issues.append(MdIssue(no, "bad-chars", "error",
                                  "control or replacement character in line"))
        if fence_char:
            # Inside a block: only a matching closer ends it. Everything else,
            # including a fence line of the OTHER character or a shorter run, is
            # code content and is exempt from the markdown rules.
            if _fence_closes(text, fence_char, fence_len):
                fence_char = ""
            continue
        opener = _fence_opener(text)
        if opener:
            if block:
                _check_table_block(block, issues)
                block = []
            fence_char, fence_len = opener
            fence_open_line = no
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
    if fence_char:
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
    headings = tables = lists = blocks = images = links = 0
    fence_char = ""
    fence_len = 0
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if fence_char:
            if _fence_closes(ln, fence_char, fence_len):
                fence_char = ""
            i += 1
            continue
        opener = _fence_opener(ln)
        if opener:
            # Counted on the OPENER, not as delimiters//2: an unclosed fence is
            # still one code block — it runs to the end of the document, which is
            # what a renderer does with it — and a ``~~~`` inside a ``` block is
            # not a delimiter at all.
            blocks += 1
            fence_char, fence_len = opener
            i += 1
            continue
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
        "code_blocks": blocks, "formulas": md.count("$$") // 2,
    }


# The facts the fidelity gate compares. Named explicitly rather than "every key
# both sides happen to produce", so widening the gate is a deliberate edit with a
# test behind it — never a silent consequence of adding a field.
_FIDELITY_FACTS = ("headings", "heading_path", "list_items", "ordered_items",
                   "bullet_items", "ordered_numbers",
                   "strong", "em", "strike", "code_spans", "code_blocks",
                   "links", "tables", "list_item_words", "thematic_breaks")


def _has_evidence(value):
    # type: (object) -> bool
    """Did this fact actually observe anything, on this document?

    ``compared`` used to count fact NAMES the source supplied, which is the schema,
    not the evidence: a one-paragraph memo with no list, no table and no emphasis
    reported ``compared: 13`` and read as thirteen things checked when twelve of them
    were 0 == 0. Counting only facts with something on at least one side stops the
    block overstating its own coverage."""
    if isinstance(value, dict):
        return any(value.values())
    if isinstance(value, (list, tuple)):
        return bool(value)
    return bool(value)


def structure_fidelity_report(emitted, source, lane="office"):
    # type: (dict, dict, str) -> dict
    """Grade the emitted markdown's STRUCTURE against the source's.

    ``emitted`` comes from ``md_structure`` (what a renderer sees), ``source`` from
    the converter-blind ``docx_source_structure``. Every disagreement is published
    as a delta, because a gate that reports only pass/fail teaches nobody anything.

    The lane asymmetry is the same one losslessness has, for the same reason: a PDF
    has no ground-truth semantic tree, so it cannot claim a provable pass. A caller
    that supplies no source facts gets ``unmeasured`` — never a free pass.

    ``ordered_numbers`` compares what the two sides say the reader SEES on each
    step, not the digits that were written. Counts and depths cannot see a list
    broken in two — a screenshot dropped between step 2 and step 3 leaves the item
    count, the depth histogram and the token multiset all untouched while the
    renderer prints 1, 2, 1, 2 — and they cannot see a declared start of 5 being
    ignored either. ``heading_path`` is the same argument for prose: a histogram of
    heading LEVELS cannot see two section titles exchanged.

    Two fields keep this block honest about its own reach:

      * ``compared`` counts facts that OBSERVED SOMETHING on this document, not
        facts the source happened to supply a key for. A memo with no lists and no
        tables is not thirteen things checked.
      * ``unmeasured`` names, when a ground truth IS present, every fact it did not
        supply — so a partial second opinion reads as partial instead of as a clean
        bill of health. ``gate`` still answers only for what was measured; read the
        two together."""
    out = OrderedDict()
    out["method"] = "ooxml-structure-ground-truth" if source else "unmeasured"
    deltas = []
    unmeasured = []
    compared = 0
    for fact in _FIDELITY_FACTS:
        if fact not in source:
            # The ground truth has no opinion on this one. Say so by NAME: silently
            # skipping it is how a gate ends up reporting a confident "pass" over a
            # fact vector nobody compared.
            unmeasured.append(fact)
            continue
        want, got = source.get(fact), emitted.get(fact)
        if fact == "tables":
            # Geometry AND placement. Rows x columns alone cannot see a
            # transposition: swap two values between rows and the dimensions, the
            # counts and the token multiset are all unchanged, while an escalation
            # table now pages the wrong rota.
            want = [(t.get("rows"), t.get("cols"), t.get("cells"))
                    for t in want or []]
            got = [(t.get("rows"), t.get("cols"), t.get("cells"))
                   for t in got or []]
        elif isinstance(want, dict):
            # Normalise away the difference between "absent" and "zero" so a
            # delta always means a real disagreement.
            keys = set(want) | set(got or {})
            want = dict((k, want.get(k, 0)) for k in keys if want.get(k, 0))
            got = dict((k, (got or {}).get(k, 0)) for k in keys if (got or {}).get(k, 0))
        if _has_evidence(want) or _has_evidence(got):
            compared += 1
        if want != got:
            deltas.append(OrderedDict([("fact", fact), ("source", want),
                                       ("markdown", got)]))
    out["compared"] = compared
    if source and unmeasured:
        out["unmeasured"] = unmeasured
    out["deltas"] = deltas
    if not source:
        out["gate"] = "unmeasured"
    elif lane != "office":
        out["gate"] = "best-effort"
    else:
        out["gate"] = "pass" if not deltas else "fail"
    return out


def build_report(source_text, md, lane="office", losslessness=None,
                 token_count=None, content_min=_CONTENT_GATE,
                 source_structure=None):
    # type: (str, str, str, dict, object, float, dict) -> dict
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
            # The DENOMINATOR, so a recall of 1.0 is never claimable over nothing.
            # The PDF block has carried n_source_tokens all along; without it here a
            # zero-byte upload reported `token_recall: 1.0, gate: pass, lossless:
            # true, warnings: []` in exactly the vocabulary of a real conversion, and
            # the only tell was recognising e3b0c442... as the sha of no bytes.
            "n_source_tokens": rep["n_source"],
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

    # The second hard gate. A ratchet beside token recall, never a replacement:
    # emphasis, list nesting and table geometry are not tokens, so recall == 1.0
    # can be — and was — true of a procedure whose steps had been renumbered.
    fidelity = structure_fidelity_report(md_structure(md), source_structure or {},
                                         lane=lane)

    if loss.get("gate") == "fail" or fidelity["gate"] == "fail" or n_err > 0:
        status = "failed"
    elif n_warn > 0:
        status = "degraded"
    else:
        status = "ok"

    return {
        "markdown_sha256": hashlib.sha256((md or "").encode("utf-8")).hexdigest(),
        "status": status,
        "losslessness": loss,
        "structure_fidelity": fidelity,
        "content": _content_metrics(md, token_count=token_count),
        "structural_errors": n_err,
        "structural_warnings": n_warn,
    }


def image_report(referenced, extracted, unique_files, missing, orphans, verified,
                 orphans_removed=0):
    # type: (int, int, int, int, int, int, int) -> dict
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
      * ``orphans``      — files on disk with no body reference REMAINING after the
        sweep, so a non-zero value means the GC itself failed and the gate must say so
      * ``orphans_removed`` — how many the sweep took out. Separate from ``orphans``
        on purpose: the gate needs "are there orphans now", a dashboard asking "how
        much churn is this corpus seeing" needs "how many were there", and folding
        both into one number loses whichever question you did not ask first. Before
        this the removed count existed only inside a prose ``detail`` string, so
        aggregating it corpus-wide was impossible.
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
    b["orphans_removed"] = orphans_removed
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
    verdict AND none of those verdicts is ``useless``; ``incomplete`` when a run
    left images uncaptioned or captioned uselessly (re-run when the VLM is back).

    NOTE ``useless`` keeps the gate off ``complete``, exactly as ``invalid`` does in
    ``doc_meta_report``, and for the same reason: it is a TERMINAL verdict, so it
    drives ``pending`` to zero while leaving the image with nothing a reader can
    use. Without that guard ``caption_report(True, 3, 0, 0, 3, 0)`` — three images,
    three captions the useful gate threw away, not one usable line — reported
    ``complete``, which is the block claiming coverage it does not have. Furniture
    is NOT in the guard: a caption the model deliberately declined to write for a
    spacer rule is a correct, finished outcome, not a failed one."""
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
    elif useless == 0 and (expected == 0 or pending == 0):
        b["gate"] = "complete"
    elif attempted == 0:
        b["gate"] = "pending"
    else:
        b["gate"] = "incomplete"
    return b


def doc_meta_report(enabled, expected, filled, authored, invalid, pending,
                    schema_version=0, vocab_version=0, model="", prompt_sha=""):
    # type: (bool, int, int, int, int, int, int, int, str, str) -> dict
    """The document-metadata block for ``report.json`` — the same shape, and the same
    deliberate separation from ``status``, as ``caption_report``.

    Metadata enrichment is the second re-runnable overlay on a lossless build, and it
    must not be able to make a lossless document read as degraded because no model has
    classified it yet. So it carries its own verdict:

      * ``expected`` — model-writable fields in the schema (tier 2, not authored-only)
      * ``filled``   — fields carrying a value the vocabulary accepts
      * ``authored`` — fields a PERSON wrote; counted separately because a generated
                       value must never overwrite one, so they are not "model coverage"
      * ``invalid``  — fields present but carrying a value the vocabulary rejects
      * ``pending``  — model-writable fields still empty (never run / model outage)

    ``gate``: ``disabled`` when enrichment is off; ``pending`` before the first run;
    ``complete`` when nothing is outstanding; ``incomplete`` when a run left fields
    unfilled or invalid. NOTE ``invalid`` keeps the gate off ``complete`` — a value
    outside the closed vocabulary is worse than an absent one, because it silently
    becomes a new term for every consumer that groups by that field.

    The two versions are recorded because they invalidate DIFFERENT work: a schema
    bump means the field inventory moved, a vocab bump means the allowed values did
    and only the classified fields need revisiting.
    """
    attempted = filled + invalid
    b = OrderedDict()
    b["enabled"] = bool(enabled)
    b["schema_version"] = int(schema_version or 0)
    b["vocab_version"] = int(vocab_version or 0)
    b["expected"] = expected
    b["filled"] = filled
    b["authored"] = authored
    b["invalid"] = invalid
    b["pending"] = pending
    b["model"] = model or ""
    b["prompt_sha"] = prompt_sha or ""
    if not enabled:
        b["gate"] = "disabled"
    elif invalid == 0 and (expected == 0 or pending == 0):
        # `invalid` gates independently of BOTH other counts. A run can fill every
        # model-writable field and still leave one holding a value the vocabulary
        # rejects: pending reaches 0 while invalid does not, and the earlier form
        # (`expected == 0 or pending == 0`) called that complete. Guarding on
        # `invalid` first also keeps the `expected == 0` short-circuit honest for any
        # caller that counts invalid fields outside the model-writable set.
        b["gate"] = "complete"
    elif attempted == 0:
        b["gate"] = "pending"
    else:
        b["gate"] = "incomplete"
    return b
