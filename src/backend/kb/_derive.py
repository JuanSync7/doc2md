"""
title: Tier-1 metadata derivations (private)
layer: backend
public_api: no
summary: Fixed-rule values computed from the document — slug, uid, word count, reading time, keyword candidates.
"""
# 3.6-compatible. Stdlib only. Pure functions — same input, same output, no model.
#
# Everything here is tier 1: computed from tier-0 content by a rule with no
# judgement in it. That matters for backfill — a tier-1 field never needs a model
# to be re-derived, so a corpus can be brought up to a new schema revision offline.
import hashlib
import re
import unicodedata

from backend.ingest import identifier_vocab, markdown_to_text

__all__ = ["slugify", "derive_uid", "word_count", "reading_time_minutes",
           "keyword_candidates", "heading_anchor", "body_anchors",
           "WORDS_PER_MINUTE"]

# Technical prose, read for comprehension rather than skimmed. Named rather than
# inlined because it is a judgement call the number alone would hide.
WORDS_PER_MINUTE = 250

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_NON_SLUG_U = re.compile(r"[^^\w]+", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def slugify(text, max_len=80):
    # type: (str, int) -> str
    """A lowercase ``a-z0-9-`` slug.

    Unicode is folded to ASCII first (``café`` -> ``cafe``) so the same title
    always yields the same slug regardless of how the source encoded its accents —
    two spellings of one document must not become two ids.
    """
    raw = text or ""
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = _NON_SLUG.sub("-", s).strip("-")
    if not s and raw.strip():
        # ASCII folding empties an all-non-ASCII title, which would collapse every
        # such document onto the same (empty) slug and make `derive_uid` return the
        # bare namespace. Keep the unicode word characters instead, and fall back to
        # a content hash only when even those are absent — an id must be unique.
        s = _NON_SLUG_U.sub("-", raw.lower()).strip("-")
        if not s:
            s = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def derive_uid(source_relpath, namespace=""):
    # type: (str, str) -> str
    """A readable, path-shaped machine identity: ``<namespace>/<dirs>/<stem>``.

    Complements ``doc_id`` rather than replacing it — ``doc_id`` is a hash and is
    the collation key, while this is what a person reads in a citation. Both are
    path-derived, so both change if the source moves; a rename that must preserve
    identity is what the AUTHORED ``id`` field is for.
    """
    parts = [p for p in (source_relpath or "").replace("\\", "/").split("/") if p]
    if not parts:
        return slugify(namespace)
    parts[-1] = parts[-1].rsplit(".", 1)[0]
    segs = [slugify(p) for p in parts]
    if namespace:
        segs.insert(0, slugify(namespace))
    return "/".join(s for s in segs if s)


def word_count(body_md):
    # type: (str) -> int
    """Words in the markdown BODY, markdown stripped first.

    Counts letter-runs, so ``0644`` and ``|---|`` table rules do not inflate the
    figure that reading time is computed from.
    """
    return len(_WORD.findall(markdown_to_text(body_md or "")))


def reading_time_minutes(words, wpm=WORDS_PER_MINUTE):
    # type: (int, int) -> int
    """Whole minutes, never zero for a non-empty document."""
    if not words:
        return 0
    return max(1, int(round(float(words) / float(wpm))))


# GitHub/GFM, Python-Markdown and pandoc all KEEP underscores and unicode
# letters in an anchor. Dropping them reported live links as dead.
_ANCHOR_DROP = re.compile(r"[^\w \-]", re.UNICODE)
# CommonMark allows up to three leading spaces before the hashes.
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)


def heading_anchor(heading_text):
    # type: (str) -> str
    """The ``#fragment`` a heading is addressable by, WITHOUT the leading ``#``.

    The common markdown-renderer rule: lowercase, drop everything that is not a
    letter, digit, space or hyphen, then spaces to hyphens. Deliberately NOT
    ``slugify`` — that turns every punctuation mark into a separator, so
    ``7.3 Standing rule`` would become ``7-3-standing-rule`` where a renderer
    produces ``73-standing-rule``. A ``ref`` that does not match what the renderer
    emits is a dead link, so the rule has to be the renderer's, not ours.
    """
    s = (heading_text or "").strip().lower()
    s = _ANCHOR_DROP.sub("", s)
    return "-".join(s.split())


def body_anchors(body_md):
    # type: (str) -> set
    """Every anchor an ATX heading in the body makes addressable.

    Repeated headings get the renderer's disambiguating suffix — the second
    ``## Overview`` is addressable as ``overview-1``, the third as ``overview-2``.
    Returning only the bare anchor would report a perfectly valid ``#overview-1``
    reference as a dead link.
    """
    out = set()
    seen = {}
    for m in _HEADING.finditer(body_md or ""):
        base = heading_anchor(m.group(2))
        if not base:
            continue          # an anchor-less heading makes nothing addressable
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else "%s-%d" % (base, n))
    return out


def keyword_candidates(body_md, limit=0):
    # type: (str, int) -> list
    """Identifier-like tokens of the body — the tier-1 seed for the keyword registry.

    Reuses ``backend.ingest.identifier_vocab`` (underscore or camelCase, length
    >= 6) rather than inventing a second notion of "looks like a symbol". These
    are CANDIDATES: a settings key or a function name is a real keyword, but
    deciding which ones matter is tier-2 work.
    """
    out = sorted(identifier_vocab(body_md or ""))
    return out[:limit] if limit else out
