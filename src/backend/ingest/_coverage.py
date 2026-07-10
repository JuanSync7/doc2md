"""
title: Content coverage metric (private)
layer: backend
public_api: no
summary: Measure what fraction of a source document's content survived into a target text.
"""
# 3.6-compatible. Stdlib only. Pure policy — no disk, no network.
#
# This is the MEASUREMENT INSTRUMENT for the ingestion flow. Given the text we
# can independently pull out of a source document and the prose we actually
# produced (markdown -> text), it answers one question with a number:
#   "how much of the source content made it through?"  -> recall in [0, 1].
#
# It is deliberately count-aware (multiset). Set-based recall would say a word
# is "covered" if it appears once in the target, hiding the case where a whole
# table or a speaker-notes block was dropped but its vocabulary survived
# elsewhere. Multiset recall charges for every dropped occurrence, so a missing
# block actually moves the number — which is what lets it DRIVE the fixes.
import html as _htmllib
import re
from collections import Counter, namedtuple

__all__ = ["tokenize", "coverage", "CoverageReport", "is_lossy", "is_lossy_explained",
           "char_ngram_recall", "html_to_text", "strip_running_lines", "words_in_bbox",
           "explain_gap", "GapReport", "merge_boxes"]

# recall  : n_covered / n_source in [0, 1] (1.0 when the source has no tokens)
# n_source: total source tokens (multiset size)
# n_covered: sum over tokens of min(source_count, target_count)
# n_missing: n_source - n_covered
# missing_top: [(token, missing_count), ...] highest-loss first (what got dropped)
CoverageReport = namedtuple(
    "CoverageReport", ["recall", "n_source", "n_covered", "n_missing", "missing_top"])

_TOKEN = re.compile(r"[a-z0-9]+")
_MISSING_TOP = 12


def tokenize(text):
    # type: (str) -> list
    """Lowercase, then pull out maximal alphanumeric runs. Punctuation/markdown
    symbols fall away; words and hex/number tokens (``0x1f``, ``32``) survive."""
    if not text:
        return []
    return _TOKEN.findall(text.lower())


_HTML_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>")
_HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
_HTML_TAG = re.compile(r"(?s)<[^>]+>")


def html_to_text(source):
    # type: (str) -> str
    """Visible text of an HTML document, for the coverage BASELINE.

    Removes ``<script>``/``<style>`` BODIES (not just their tags) and comments before
    stripping the remaining tags, then unescapes entities. Without dropping the script/
    style bodies, embedded JS/CSS — which docling correctly never emits — inflates the
    source token count and sinks recall (generated report HTML can be >50% script/style),
    making a faithful conversion look lossy. Stdlib only (3.6-compatible)."""
    if not source:
        return source
    s = _HTML_COMMENT.sub(" ", source)
    s = _HTML_SCRIPT_STYLE.sub(" ", s)
    s = _HTML_TAG.sub(" ", s)
    return _htmllib.unescape(s)


def coverage(source_text, target_text, exclude=""):
    # type: (str, str, str) -> CoverageReport
    """Multiset token recall of ``source_text`` into ``target_text``.

    Counts how many source token occurrences are accounted for in the target.
    Extra tokens in the target do not change the score (recall, not F1) — the
    contract is "did the source survive", not "is the target minimal".

    ``exclude`` (optional) is boilerplate the target is EXPECTED not to contain —
    e.g. the running headers/footers docling removed as furniture. Its tokens are
    multiset-subtracted from the source first, so the target is not penalized for
    correctly dropping them (apple-to-apple). Subtraction is per-occurrence, so a
    token that is both boilerplate and genuine content still partly counts.
    """
    src = Counter(tokenize(source_text))
    if exclude:
        src = src - Counter(tokenize(exclude))   # multiset subtraction; drops non-positive
    tgt = Counter(tokenize(target_text))
    n_source = sum(src.values())
    if n_source == 0:
        return CoverageReport(1.0, 0, 0, 0, [])
    covered = 0
    missing = Counter()
    for tok, n in src.items():
        have = tgt.get(tok, 0)
        hit = n if have >= n else have
        covered += hit
        if hit < n:
            missing[tok] = n - hit
    n_missing = n_source - covered
    # highest-loss tokens first; ties broken by token for a stable, testable order
    missing_top = sorted(missing.items(), key=lambda kv: (-kv[1], kv[0]))[:_MISSING_TOP]
    return CoverageReport(covered / float(n_source), n_source, covered, n_missing, missing_top)


def words_in_bbox(words, box):
    # type: (list, tuple) -> list
    """Texts of the words whose CENTER falls inside ``box``.

    Both ``words`` (each ``(text, x0, y0, x1, y1)``) and ``box`` (``(x0, y0, x1, y1)``)
    are in [0,1] page fractions (top-left origin), so the caller normalizes docling's
    picture bbox and the pdftotext words to their OWN page dimensions and they compare
    directly — no absolute-scale matching needed. Used to recover the text docling
    buried inside a ``<!-- image -->`` region (image-text), for apple-to-apple scoring.
    """
    x0, y0, x1, y1 = box
    out = []
    for w in words:
        t, wx0, wy0, wx1, wy1 = w
        cx = (wx0 + wx1) / 2.0
        cy = (wy0 + wy1) / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(t)
    return out


def merge_boxes(boxes, pad=0.01):
    # type: (list, float) -> list
    """Merge overlapping/nearby boxes into clusters; returns ``[(box, n_merged)]``.

    Boxes are ``(x0, y0, x1, y1)`` in [0,1] page fractions; two boxes whose
    ``pad``-expanded rectangles intersect join one cluster (repeated to a fixed
    point, so chains merge transitively). ``n_merged`` counts the input boxes in
    each cluster — the caller uses it as EVIDENCE STRENGTH: a diagram is dozens of
    little stroke/rect objects, while a stray rule or underline is one, so a
    minimum count separates real figure regions from incidental drawing."""
    clusters = [(tuple(b), 1) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        for box, n in clusters:
            for i, (c, m) in enumerate(out):
                if not (box[2] + pad < c[0] or c[2] + pad < box[0]
                        or box[3] + pad < c[1] or c[3] + pad < box[1]):
                    out[i] = ((min(box[0], c[0]), min(box[1], c[1]),
                               max(box[2], c[2]), max(box[3], c[3])), m + n)
                    changed = True
                    break
            else:
                out.append((box, n))
        clusters = out
    return clusters


_PAGE_BREAK = "\f"


def strip_running_lines(text, min_frac=0.5):
    # type: (str, float) -> str
    """Remove running headers/footers so text→text comparison is apple-to-apple.

    ``text`` is page-delimited by form-feeds (as ``pdftotext`` emits). A line whose
    whitespace-normalized form recurs on at least ``min_frac`` of the pages (and on
    at least 2 pages — repetition is undefinable below that structural floor) is
    treated as running boilerplate and dropped from every page. Per-page-varying
    lines (page numbers, body text) are kept. Single-page / empty input is returned
    unchanged. This both (a) stops docling being penalized for correctly removing
    boilerplate and (b) yields clean text when used as a fallback body.
    """
    if not text:
        return text
    pages = text.split(_PAGE_BREAK)
    n = len(pages)
    if n < 2:
        return text
    seen = Counter()
    for pg in pages:
        for k in set(" ".join(ln.split()) for ln in pg.split("\n") if ln.strip()):
            seen[k] += 1
    thresh = min_frac * n
    boiler = set(k for k, c in seen.items() if c >= 2 and c >= thresh)
    if not boiler:
        return text
    out_pages = []
    for pg in pages:
        kept = [ln for ln in pg.split("\n") if " ".join(ln.split()) not in boiler]
        out_pages.append("\n".join(kept))
    return _PAGE_BREAK.join(out_pages)


def is_lossy(report, min_recall=0.8, min_tokens=50):
    # type: (CoverageReport, float, int) -> bool
    """True if a conversion dropped too much to trust: recall below ``min_recall``
    while the source had at least ``min_tokens`` tokens (so tiny/near-empty docs,
    where recall is noisy, are never flagged)."""
    return report.n_source >= min_tokens and report.recall < min_recall


_ALNUM = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"\d+")


def char_ngram_recall(source_text, target_text, n=3):
    # type: (str, str, int) -> float
    """Multiset recall of character ``n``-grams over the ALPHANUMERIC-ONLY stream.

    This is the CONTENT-PRESENCE check behind the "explained-gap" acceptance model.
    All non-alphanumerics (spaces, hyphens, punctuation, markdown/table framing) are
    removed before n-gramming, so ``inter-face`` / ``inter\\nface`` / ``interface`` all
    yield the same grams. It is therefore blind to the tokenization, hyphenation and
    layout differences that sink *token* recall without any content actually going
    missing — but it still falls when real letters are dropped (a lost paragraph or
    table lowers it). ``1.0`` when the source has fewer than ``n`` alphanumerics."""
    s = _ALNUM.sub("", source_text.lower()) if source_text else ""
    t = _ALNUM.sub("", target_text.lower()) if target_text else ""
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


# n_source     : total source tokens (multiset, after strip_running_lines)
# covered      : occurrences present in the target (min(src, tgt) per token)
# fused        : missing occurrences whose CHARACTERS are present in the target's
#                alphanumeric stream (re-tokenization/de-hyphenation, not lost content)
# numeric      : missing pure-digit occurrences (page numbers, TOC leaders, indices)
# image_text   : missing occurrences the caller PROVED live inside figure regions
#                (text drawn over dense vector art / raster images) — figure content
#                a text conversion legitimately renders as an image placeholder
# residual_boiler: missing occurrences from lines whose DIGIT-MASKED form repeats on
#                >=2 pages (running headers/footers incl. per-page-varying "Page 3 of 120")
# short        : missing tokens of 1-3 chars that none of the above claimed (single
#                letters/units from equations; too short to substring-match honestly)
# absent       : everything left — content genuinely unaccounted for in the target
# absent_top   : [(token, count), ...] largest truly-absent tokens, for inspection
GapReport = namedtuple("GapReport", [
    "n_source", "covered", "fused", "numeric", "image_text", "residual_boiler", "short",
    "absent", "absent_top"])

_ABSENT_TOP = 20


def explain_gap(raw_source_text, target_text, min_frac=0.5, image_text=""):
    # type: (str, str, float, str) -> GapReport
    """Decompose the source->target token gap into EXPLAINED buckets vs truly ABSENT.

    ``raw_source_text`` is the page-delimited (``\\f``) independent extraction BEFORE
    boilerplate stripping (this function applies ``strip_running_lines(min_frac)``
    itself so it can also see the sub-threshold repeated lines). ``image_text``
    (optional) is text the caller INDEPENDENTLY located inside figure regions —
    e.g. words over dense vector art found via the PDF's own drawing objects — so
    figure labels a converter renders as an image placeholder are explained, not
    counted as loss. Each missing token occurrence is claimed by the first bucket
    that can explain it:

      pure digits -> ``numeric`` (substring-matching digits is noise, so they get
      their own bucket); tokens of >=4 chars whose characters appear among the
      target's SURPLUS tokens (fusions/re-hyphenations) -> ``fused``; occurrences
      backed by ``image_text`` -> ``image_text``; occurrences from surviving lines
      whose digit-masked form repeats on >=2 pages -> ``residual_boiler`` (running
      furniture incl. varying page footers); remaining 1-3 char tokens -> ``short``;
      the rest -> ``absent`` — the only bucket that is real, unexplained content
      loss. ``absent_top`` lists its biggest tokens.
    """
    src_text = strip_running_lines(raw_source_text or "", min_frac)
    src = Counter(tokenize(src_text))
    tgt = Counter(tokenize(target_text))
    n_source = sum(src.values())
    if n_source == 0:
        return GapReport(0, 0, 0, 0, 0, 0, 0, 0, [])
    covered = 0
    missing = Counter()
    for tok, n in src.items():
        have = tgt.get(tok, 0)
        hit = n if have >= n else have
        covered += hit
        if hit < n:
            missing[tok] = n - hit
    # Fusion evidence comes from the target's SURPLUS tokens only (occurrences beyond
    # the source's count, in document order). Building the stream from the whole target
    # would credit substrings inside unrelated covered words ("face" in a covered
    # "interface") as "fused" and understate real absence.
    avail = Counter(src)
    surplus = []
    for t in tokenize(target_text):
        if avail.get(t, 0) > 0:
            avail[t] -= 1
        else:
            surplus.append(t)
    stream = "".join(surplus)
    # Residual running lines that survived the strip: count repetition on the
    # DIGIT-MASKED form so per-page-varying furniture ("Page 3 of 120", dated footers)
    # is recognized as the same recurring line. Any line whose masked form appears on
    # >= 2 pages contributes its tokens as boilerplate-explained, not content loss.
    boiler_avail = Counter()
    pages = src_text.split(_PAGE_BREAK)
    if len(pages) >= 2:
        seen = Counter()
        page_lines = []
        for pg in pages:
            lines = [" ".join(ln.split()) for ln in pg.split("\n") if ln.strip()]
            masked = set(_DIGITS.sub("#", ln) for ln in lines)
            page_lines.append(lines)
            for k in masked:
                seen[k] += 1
        for lines in page_lines:
            for ln in lines:
                if seen[_DIGITS.sub("#", ln)] >= 2:
                    for t in tokenize(ln):
                        boiler_avail[t] += 1
    image_avail = Counter(tokenize(image_text)) if image_text else Counter()
    fused = numeric = img = residual = short = absent = 0
    absent_toks = Counter()
    for tok, m in missing.items():
        left = m
        if tok.isdigit():
            numeric += left
            continue
        if len(tok) >= 4:
            # occurrences inside the surplus stream = fused/re-hyphenated copies
            extra = stream.count(tok)
            take = left if extra >= left else extra
            fused += take
            left -= take
            if left == 0:
                continue
        if image_avail.get(tok, 0) > 0:
            take = left if image_avail[tok] >= left else image_avail[tok]
            img += take
            image_avail[tok] -= take
            left -= take
            if left == 0:
                continue
        if boiler_avail.get(tok, 0) > 0:
            take = left if boiler_avail[tok] >= left else boiler_avail[tok]
            residual += take
            boiler_avail[tok] -= take
            left -= take
            if left == 0:
                continue
        if len(tok) < 4:
            short += left
            continue
        absent += left
        absent_toks[tok] += left
    absent_top = sorted(absent_toks.items(), key=lambda kv: (-kv[1], kv[0]))[:_ABSENT_TOP]
    return GapReport(n_source, covered, fused, numeric, img, residual, short,
                     absent, absent_top)


def is_lossy_explained(report, content_recall, min_recall=0.8, min_tokens=50,
                       content_min=0.95):
    # type: (CoverageReport, float, float, int, float) -> bool
    """Explained-gap losslessness: a doc is lossy ONLY when BOTH signals agree.

    Token recall alone over-reports loss because docling legitimately drops redundant
    text (furniture) and re-tokenizes the rest. So we flag a doc as lossy only if its
    token ``recall`` is below ``min_recall`` AND its character-n-gram ``content_recall``
    is below ``content_min`` — i.e. content is genuinely missing, not merely
    re-tokenized. Tiny sources (< ``min_tokens``) are never flagged (recall is noisy).
    The unexplained shortfall a caller records is ``max(0.0, 1.0 - content_recall)``."""
    if report.n_source < min_tokens:
        return False
    if report.recall >= min_recall:
        return False
    return content_recall < content_min
