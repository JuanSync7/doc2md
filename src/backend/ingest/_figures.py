"""
title: Figure gating + caption inlining policy (private)
layer: backend
public_api: no
summary: Pure 3.6 policy — gate docling pictures (deny-class/area/dedup), filter captions, inline into markdown.
"""
# 3.6-compatible. Stdlib only. NO model/network/heavy deps — the VLM call lives in
# scripts/docling_convert.py (Python 3.12). This is the DECISION layer: given picture
# metadata it decides keep/drop; given markdown + per-picture renders it rewrites the
# docling `<!-- image -->` placeholders. Pure + deterministic => fully unit-testable.
#
# Generic, never path/doc-targeted (CONVENTIONS): rules key only on class/area/sha and
# on the caption OUTPUT, so they apply uniformly across the whole corpus.
import hashlib
import re
from collections import Counter, OrderedDict, namedtuple

__all__ = ["DENY_CLASSES", "gate_figures", "caption_is_useful",
           "caption_type_is_furniture", "caption_cache_key", "cache_last_wins",
           "figure_sentinel", "inline_figures",
           "image_markdown", "inline_image_captions", "FigureDecision",
           "ooxml_image_sentinel", "ooxml_image_parts", "inline_ooxml_images",
           "plan_office_images", "OfficeImagePlan",
           "figure_outcome", "figure_coverage", "FigureCoverage",
           "FIG_CAPTURED", "FIG_NO_CAPTION", "FIG_GATED_TINY", "FIG_GATED_DENY",
           "FIG_GATED_CHROME", "FIG_GATED_DUP", "FIG_GATED_OTHER",
           "FIG_LOST_BADCROP", "FIG_LOST_BAIL"]

# Classes docling's DocumentFigureClassifier assigns to non-content marks (template
# noise). We use the classifier ONLY as this junk gate; its positive type label is
# otherwise discarded (the VLM supplies the real type at caption time).
DENY_CLASSES = frozenset([
    "logo", "icon", "stamp", "signature", "qr_code", "bar_code", "page_thumbnail",
])
AREA_MIN = 0.02       # <2% of page area => decorative mark, drop
AREA_READMIT = 0.05   # a deny-CLASS pic >=5% of the page is re-admitted (false-deny fix)

FigureDecision = namedtuple("FigureDecision", ["keep", "reason"])

_PLACEHOLDER = "<!-- image -->"
_WS = re.compile(r"\s+")


def gate_figures(pictures):
    # type: (list) -> list
    """Decide keep/drop for each docling picture, IN INPUT ORDER (aligned to the
    ``<!-- image -->`` placeholders docling emits).

    ``pictures``: list of dicts with keys ``cls`` (class name or None), ``area`` (page
    area fraction 0..1, or None when the source is not paginated, e.g. docx/pptx), and
    ``sha`` (image hash or None). Rules:
      * tiny: ``area`` not None and ``area < AREA_MIN`` -> drop
      * deny-class (logo/icon/...): drop UNLESS ``area >= AREA_READMIT`` (re-admit big ones)
      * duplicate: same ``sha`` already seen in this doc -> drop (caption the figure once)
    Returns a list of ``FigureDecision(keep, reason)`` aligned to ``pictures``.
    """
    seen = set()
    out = []
    for p in pictures:
        cls = p.get("cls")
        area = p.get("area")
        sha = p.get("sha")
        ref = p.get("ref")
        # Formula-safe: only signals a formula CAN'T carry may drop -- deny-class, tiny page
        # area, CHROME placement (header/footer/slide-number-date-footer), within-doc dup.
        # Recurrence (n_docs) and byte-size are recorded but NEVER drop: a reused/rendered
        # formula is small and recurs, so the cache (one caption, reused) handles reuse and
        # the model's own verdict (caption_type_is_furniture) removes any furniture that slips
        # through. All comparisons are None-guarded (py3.6: None < x raises).
        if area is not None and area < AREA_MIN:
            out.append(FigureDecision(False, "tiny"))
            continue
        if cls in DENY_CLASSES and not (area is not None and area >= AREA_READMIT):
            out.append(FigureDecision(False, "deny:" + str(cls)))
            continue
        if ref == "chrome":
            # ref is 'body' if ANY occurrence is body (body-wins, resolved by the extractor);
            # 'chrome' only when EVERY placement is page furniture.
            out.append(FigureDecision(False, "chrome"))
            continue
        if sha is not None:
            if sha in seen:
                out.append(FigureDecision(False, "dup"))
                continue
            seen.add(sha)
        out.append(FigureDecision(True, "keep"))
    return out


def caption_is_useful(caption):
    # type: (str) -> bool
    """True if a VLM caption is worth indexing — rejects empty/short/degenerate output.

    Guards against the empty-output and runaway-repetition glitches CPU VLMs produce
    (hollow tables, degenerate loops). Generic: keyed only on the OUTPUT, not the doc.
    """
    if not caption:
        return False
    c = _WS.sub(" ", caption).strip()
    if len(c) < 25:                          # a real caption is a sentence+, not a word
        return False
    letters = sum(1 for ch in c if ch.isalpha())
    if letters < 0.5 * len(c):               # mostly digits/punctuation => junk
        return False
    words = c.split()
    if len(words) >= 8:
        uniq = len(set(w.lower() for w in words))
        if uniq <= 0.3 * len(words):         # heavy repetition => degenerate loop
            return False
    return True


# Informative visual TYPES the caption prompt asks the model to name first. If the leading
# sentence names one of these, the image is content -> KEEP even if a furniture word appears
# later. This is what keeps a "block diagram ... of the logo screen" and any FORMULA safe.
_INFORMATIVE_TYPES = frozenset([
    "diagram", "schematic", "chart", "graph", "plot", "waveform", "timing", "flowchart",
    "block", "table", "screenshot", "photograph", "photo", "map", "drawing", "formula",
    "equation", "circuit", "layout", "architecture", "figure", "illustration", "render",
    "plot", "histogram", "scatter", "sequence", "state", "tree", "network",
])
# Furniture TYPES: if the leading sentence names one of these and NO informative type, drop.
_FURNITURE_TYPES = frozenset([
    "logo", "icon", "watermark", "stamp", "signature", "thumbnail", "emblem", "badge",
    "letterhead", "avatar", "favicon", "bullet",
])
_FIRST_SENTENCE = re.compile(r"^.*?(?:\.|$)", re.S)
# Prefix/word-boundary matched so inflections count ("equations"/"rendered"/"diagrams" match
# "equation"/"render"/"diagram"). The informative scan is deliberately NON-narrower than the
# furniture trigger (formula-safety): it runs over the WHOLE caption, furniture over the lead.
_INFORMATIVE_RE = re.compile(r"\b(?:" + "|".join(sorted(_INFORMATIVE_TYPES)) + r")", re.I)
_FURNITURE_RE = re.compile(r"\b(?:" + "|".join(sorted(_FURNITURE_TYPES)) + r")", re.I)


def caption_type_is_furniture(caption):
    # type: (str) -> bool
    """True if the model's OWN verdict is that the image is furniture (logo/icon/watermark/...)
    and it names NO informative type anywhere.

    The CAPTION_PROMPT asks the model to name the visual type first, so this reads that verdict
    rather than guessing from pixels. FORMULA-SAFE by construction: if ANY informative type
    word (formula/equation/diagram/schematic/...) appears ANYWHERE in the caption, the image is
    kept -- the rescue is never narrower than the furniture trigger, and matches inflections.
    Only when no informative type is named AND a furniture type leads the caption is it dropped.
    Keyed only on the OUTPUT -> generic. Empty/short captions are NOT furniture here (that is
    ``caption_is_useful``'s job, which keeps the image with a neutral alt)."""
    if not caption:
        return False
    low = caption.strip().lower()
    if _INFORMATIVE_RE.search(low):            # any informative type ANYWHERE -> keep
        return False
    lead = _FIRST_SENTENCE.match(low).group(0)
    return bool(_FURNITURE_RE.search(lead))


def caption_cache_key(image_bytes):
    # type: (bytes) -> str
    """The ONE canonical content hash — ``"sha256:<hex>"`` over the EXACT bytes handed to
    the captioner (PNG after any soffice-render/downscale). Same value is the figure record
    ``sha`` and the caption-cache key, so the two always join."""
    return "sha256:" + hashlib.sha256(image_bytes or b"").hexdigest()


def cache_last_wins(records, key_field):
    # type: (object, str) -> dict
    """Merge an iterable of dict records into ``{key: last_record}`` (last-wins), skipping
    records without ``key_field``. The pure merge semantics the caption cache + the
    shard-suffixed record loaders share (mirrors docling ``_done_ids`` last-wins)."""
    out = {}
    for rec in records:
        k = rec.get(key_field)
        if k is not None:
            out[k] = rec
    return out


# Id-addressable placeholder both interpreters emit at an image's position. Idempotent by
# construction: only this exact token is ever replaced, so a re-run over already-inlined
# markdown is a no-op and a caption's text can never be mistaken for a placeholder.
_FIGURE_SENTINEL = re.compile(r"<!-- figure:([^>]+?) -->")


def figure_sentinel(doc_id, n):
    # type: (str, object) -> str
    """The stable placeholder ``<!-- figure:<doc_id>:<n> -->`` for the n-th figure of a doc."""
    return "<!-- figure:%s:%s -->" % (doc_id, n)


def inline_figures(markdown, fills):
    # type: (str, dict) -> str
    """Replace each ``<!-- figure:ID -->`` sentinel by ``fills[ID]``.

    ``fills[ID]`` is the substitution (e.g. an ``image_markdown`` link), or ``None`` to DROP
    the placeholder (gated furniture). An ID not in ``fills`` is left intact (lossless-safe;
    e.g. a still-pending caption). Idempotent: a second call replaces nothing new because the
    sentinels are gone. Returns ``markdown`` unchanged when it has no sentinels."""
    if not markdown or "<!-- figure:" not in markdown:
        return markdown
    def _sub(m):
        fid = m.group(1)
        if fid not in fills:
            return m.group(0)               # unknown/pending id -> leave intact
        val = fills[fid]
        return "" if val is None else val
    return _FIGURE_SENTINEL.sub(_sub, markdown)


def image_markdown(caption, rel_path):
    # type: (str, str) -> str
    """A GFM image link ``![caption](rel_path)`` with the caption made alt-text-safe.

    The caption becomes single-line alt text (indexed downstream by markdown_to_text and
    visible to the section carder); ``rel_path`` points at the stored asset for re-render.
    Escapes/normalizes every character that could break the link, a surrounding pipe table,
    or re-parse as markup: newline/backslash -> space, ``]`` -> ``\\]``, ``|`` -> ``/`` (table
    cell separator), backtick -> ``'`` (inline code)."""
    alt = (_WS.sub(" ", caption or "").strip()
           .replace("\\", " ").replace("]", "\\]")
           .replace("|", "/").replace("`", "'"))
    return "![%s](%s)" % (alt, rel_path)


# --- OOXML office image sentinels (deterministic, model-free extraction) ------
# The office converter emits ``<!-- ooxml-image:PART -->`` at each BODY picture's
# position, PART being the package media path (``word/media/image3.png``). It is an
# HTML COMMENT, so ``markdown_to_text`` strips it -> emitting one, or leaving one
# un-inlined, can NEVER move the recall gate (the losslessness invariant). A writer
# resolves each to an ``![](images/<file>)`` link (or drops it) via
# ``plan_office_images`` + ``inline_ooxml_images``.
_OOXML_IMG = re.compile(r"<!-- ooxml-image:([^>]+?) -->")
_MEDIA_EXT = re.compile(r"\.([A-Za-z0-9]+)$")

OfficeImagePlan = namedtuple(
    "OfficeImagePlan",
    ["fills", "assets", "n_referenced", "n_resolved", "n_files", "n_missing"])


def ooxml_image_sentinel(part):
    # type: (str) -> str
    """The stable positional placeholder for a body picture backed by package ``part``."""
    return "<!-- ooxml-image:%s -->" % part


def ooxml_image_parts(markdown):
    # type: (str) -> list
    """Media part names of every ooxml-image sentinel, in document order (dups kept)."""
    if not markdown or "<!-- ooxml-image:" not in markdown:
        return []
    return _OOXML_IMG.findall(markdown)


def inline_ooxml_images(markdown, fills):
    # type: (str, dict) -> str
    """Replace each ``<!-- ooxml-image:PART -->`` by ``fills[PART]``.

    ``fills[PART]`` is the substitution (an ``image_markdown`` link), or ``None`` to DROP
    the sentinel (bytes missing / gated). A PART absent from ``fills`` is left intact
    (lossless-safe). Idempotent: a second call finds no sentinels. Returns ``markdown``
    unchanged when it has none."""
    if not markdown or "<!-- ooxml-image:" not in markdown:
        return markdown

    def _sub(m):
        part = m.group(1)
        if part not in fills:
            return m.group(0)              # unknown part -> leave intact
        val = fills[part]
        return "" if val is None else val
    return _OOXML_IMG.sub(_sub, markdown)


def _img_ext_of(part):
    # type: (str) -> str
    """Lowercased file extension of a media part (``word/media/image3.PNG`` -> ``png``),
    or ``"bin"`` when the part carries none."""
    m = _MEDIA_EXT.search(part or "")
    return m.group(1).lower() if m else "bin"


def plan_office_images(ordered_parts, media, subdir="images"):
    # type: (list, dict, str) -> OfficeImagePlan
    """Plan the deterministic, model-free office-image extraction from body sentinels.

    ``ordered_parts``: media part names in body reading order (from
    :func:`ooxml_image_parts`; a repeat = the same image referenced more than once).
    ``media``: ``{media_part: bytes}`` available in the package.

    Content-addressed + deduplicated: identical bytes (same sha) share ONE file named
    ``<sha16>.<ext>`` under ``subdir``, so a diagram reused on every slide is stored once
    and every occurrence links to it. A sentinel whose bytes are ABSENT is dropped
    (``fills[part] = None``) and counted in ``n_missing`` -- a real loss the caller must
    surface, never silent. Alt text is empty here: the deterministic pass stores + links
    the pixels; the caption stage fills alt text + structure captions later.

    Returns an :class:`OfficeImagePlan`: ``fills`` {part -> link or None}, ``assets``
    ``[(filename, bytes)]`` (unique files to write), and the occurrence counts
    (``n_referenced`` sentinels, ``n_resolved`` that became links, ``n_files`` unique
    files, ``n_missing`` dropped)."""
    fills = {}                            # type: dict
    assets = OrderedDict()                # filename -> bytes (unique, content-addressed)
    seen_parts = set()                    # type: set
    for part in ordered_parts:
        if part in seen_parts:
            continue
        seen_parts.add(part)
        data = media.get(part)
        if not data:
            fills[part] = None            # referenced but bytes gone -> drop, count as loss
            continue
        fname = "%s.%s" % (hashlib.sha256(data).hexdigest()[:16], _img_ext_of(part))
        assets[fname] = data              # dedup: identical content collapses to one file
        fills[part] = image_markdown("", "%s/%s" % (subdir, fname))
    n_referenced = len(ordered_parts)
    n_resolved = sum(1 for p in ordered_parts if fills.get(p) is not None)
    return OfficeImagePlan(fills, list(assets.items()), n_referenced, n_resolved,
                           len(assets), n_referenced - n_resolved)


# --- Figure-loss accounting (the Tier-1 lossless-ness instrument) ------------
# Every BODY figure ends in exactly one of these states. OK = content preserved;
# GATED = dropped on purpose (policy); LOST = unintended loss to detect + report.
FIG_CAPTURED = "captured"          # stored + useful caption inlined
FIG_NO_CAPTION = "no_caption"      # stored + inlined, caption unusable (neutral alt) — image kept
FIG_GATED_TINY = "gated_tiny"      # decorative, below area floor
FIG_GATED_DENY = "gated_deny"      # deny-class mark (logo/icon/...) not re-admitted
FIG_GATED_CHROME = "gated_chrome"  # page furniture: referenced only from header/footer/master
FIG_GATED_DUP = "gated_dup"        # duplicate image already captioned once
FIG_GATED_OTHER = "gated_other"    # dropped by a gate reason we don't classify
FIG_LOST_BADCROP = "lost_bad_crop"  # kept but image could not be extracted/stored
FIG_LOST_BAIL = "lost_bail"        # discarded by the count-mismatch alignment guard

_FIG_OK = frozenset([FIG_CAPTURED, FIG_NO_CAPTION])
_FIG_GATED = frozenset([FIG_GATED_TINY, FIG_GATED_DENY, FIG_GATED_CHROME,
                        FIG_GATED_DUP, FIG_GATED_OTHER])
_FIG_LOST = frozenset([FIG_LOST_BADCROP, FIG_LOST_BAIL])

FigureCoverage = namedtuple(
    "FigureCoverage",
    ["n_body", "n_placeholders", "n_captured", "n_gated", "n_lost", "by_outcome",
     "bailed", "lossless"])


def figure_outcome(keep, reason, stored, caption_ok):
    # type: (bool, str, bool, bool) -> str
    """Classify one BODY figure's fate from its gate decision and capture result.

    ``keep``/``reason`` come from :func:`gate_figures`; ``stored`` is whether the
    crop was actually persisted; ``caption_ok`` is :func:`caption_is_useful` on the
    VLM output. (The bail case is applied by :func:`figure_coverage`, not here.)
    """
    if not keep:
        if reason == "tiny":
            return FIG_GATED_TINY
        if reason.startswith("deny"):
            return FIG_GATED_DENY
        if reason == "chrome":
            return FIG_GATED_CHROME
        if reason == "dup":
            return FIG_GATED_DUP
        return FIG_GATED_OTHER
    if not stored:
        return FIG_LOST_BADCROP
    return FIG_CAPTURED if caption_ok else FIG_NO_CAPTION


def figure_coverage(outcomes, n_placeholders, bailed=False):
    # type: (list, int, bool) -> FigureCoverage
    """Aggregate per-figure ``outcomes`` into a loss report for one document.

    When ``bailed`` (the alignment count-guard fired), every would-be-captured
    figure is reclassified as :data:`FIG_LOST_BAIL` — the inlining was discarded,
    so those captions/images never reached the markdown. ``lossless`` is True only
    when nothing was lost AND the BODY count matches the emitted placeholders.
    """
    outs = list(outcomes)
    if bailed:
        outs = [FIG_LOST_BAIL if o in _FIG_OK else o for o in outs]
    c = Counter(outs)
    n_body = len(outs)
    n_captured = sum(v for k, v in c.items() if k in _FIG_OK)
    n_gated = sum(v for k, v in c.items() if k in _FIG_GATED)
    n_lost = sum(v for k, v in c.items() if k in _FIG_LOST)
    lossless = (n_lost == 0) and (n_body == n_placeholders)
    return FigureCoverage(n_body, n_placeholders, n_captured, n_gated, n_lost,
                          dict(c), bool(bailed), lossless)


def inline_image_captions(markdown, renders):
    # type: (str, list) -> str
    """Replace each ``<!-- image -->`` placeholder, in order, with ``renders[k]``.

    ``renders[k]`` is the substitution string for the k-th picture (e.g. an
    ``image_markdown(...)`` link), or ``None`` to DROP that placeholder (gated-out junk).
    Placeholders beyond ``len(renders)`` are left intact (lossless-safe); extra renders
    are ignored. Returns ``markdown`` unchanged when there are no placeholders.
    """
    if not markdown or _PLACEHOLDER not in markdown:
        return markdown
    parts = markdown.split(_PLACEHOLDER)     # len == #placeholders + 1
    out = [parts[0]]
    for k in range(1, len(parts)):
        idx = k - 1
        if idx < len(renders):
            sub = renders[idx]
            out.append(parts[k] if sub is None else (sub + parts[k]))
        else:
            out.append(_PLACEHOLDER + parts[k])   # unmatched placeholder: leave intact
    return "".join(out)
