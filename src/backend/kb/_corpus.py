"""
title: Corpus-level metadata gates — identity, synonymy, graph, skew (private)
layer: backend
public_api: no
summary: The checks that only a whole corpus can answer — id collisions, synonym pollution, entity identity, link integrity, schema skew and field coverage.
"""
# 3.6-compatible. Stdlib only. Pure policy — rows in, findings out. The file walk
# lives in scripts/kb_lint.py.
#
# WHY A SECOND MODULE. `_lint` grades ONE document: is this value a term, does this
# pointer resolve, is this field a facet. Every check here is one a single document
# is structurally incapable of answering, and they fail in a way per-document
# linting cannot even see:
#
#   IDENTITY    two documents claiming one id is not a defect in either document.
#   SYNONYMY    `rhel-8` in doc A and `rhel_8` in doc B are each locally fine; the
#               corpus has two nodes for one concept. This is THE failure the
#               controlled vocabulary exists to prevent, and it is invisible per file.
#   ENTITY      the same thing spelled two ways is two nodes in the graph. The
#               single most expensive silent defect in a knowledge base.
#   GRAPH       inbound link counts, orphans and edge endpoints are corpus facts.
#   SKEW        which documents are behind the current schema — i.e. exactly which
#               ones a version bump has to backfill.
#   COVERAGE    a field populated on 3% of documents is either conditional by
#               design or an extractor that quietly stopped working.
#
# A NOTE ON CARDINALITY, deliberately NOT re-implemented here. Grading a corpus-wide
# distinct/used ratio for `relations.p`, `entities.type`, `risks.impact`,
# `decisions.status` and `risks.mode` would measure nothing: all five are governed by
# a CLOSED vocabulary, so their value set is bounded by the term list and membership
# is already the correct and stricter test. Cardinality is a discovery heuristic for
# UNGOVERNED fields — "is this a note masquerading as a facet?" — and every facet the
# per-document gate grades is governed. What is genuinely unanswerable per document
# is the mirror question, so that is what `vocabulary_usage` measures instead: which
# governed terms no document ever uses, and which are used by exactly one. A closed
# list nobody draws from is as broken as an open one nobody reuses.
import re
import unicodedata
from collections import namedtuple, Counter, OrderedDict
from difflib import SequenceMatcher

from backend.ingest import parse_block, render_block

from ._lint import ERROR, INFO, WARN
from ._schema import (FIELDS, SCHEMA_VERSION, field, group_vocab, proposed_key,
                      record_vocab)

__all__ = ["CorpusFinding", "apply_promotions", "corpus_findings",
           "alias_suggestions", "norm_key",
           "strip_polarity", "identity_report", "synonym_report", "entity_report",
           "graph_report", "skew_report", "coverage_report", "vocabulary_hygiene",
           "vocabulary_usage"]

# ``detail`` is the evidence: which documents, which spellings. It is a tuple of
# strings rather than prose so the JSON report stays machine-readable.
CorpusFinding = namedtuple("CorpusFinding",
                           ["code", "severity", "where", "message", "detail"])

# Polarity and modality prefixes. `mitigates` and `does_not_mitigate` are the SAME
# edge type with `negated: true`; two predicates for one relation splits every query
# that traverses it. Longest first so `does_not_` wins over `not_`.
_POLARITY = ("does_not_", "did_not_", "do_not_", "cannot_", "can_not_", "not_",
             "is_not_", "was_not_", "would_", "should_", "must_", "may_",
             "can_", "is_", "has_", "was_")

_DETAIL_CAP = 12

# Above this many distinct terms in ONE registry field the similarity sweep narrows
# its SCOPE (not its method — see `_similar_pairs`, which is complete at every size)
# to terms the corpus has actually corroborated. Announced with its size, never
# silent. Overridable as `lint.similarity_max_terms`.
_SWEEP_MAX_TERMS = 4000

# Most similarity findings a single scope may report. Past this the list has stopped
# being a work queue and started being a wall, so the rest are COUNTED rather than
# printed — a cap that does not say what it dropped reads as "this is all of them".
_FINDING_CAP = 25


def _cap(items):
    # type: (object) -> tuple
    """Truncate an evidence list, SAYING how much was dropped.

    A detail list silently cut to twelve reads as "twelve documents affected",
    which is the same class of lie this package exists to stop.
    """
    items = list(items)
    if len(items) <= _DETAIL_CAP:
        return tuple(items)
    return tuple(items[:_DETAIL_CAP]
                 + ["... and %d more" % (len(items) - _DETAIL_CAP)])


def _where_line(name, paths):
    # type: (str, set) -> str
    """``name -> a.md, b.md (+3 more)`` — the count is never dropped silently."""
    ordered = sorted(paths)
    shown = ", ".join(ordered[:3])
    extra = len(ordered) - 3
    return "%s -> %s%s" % (name, shown, (" (+%d more)" % extra) if extra > 0 else "")


def _f(code, severity, where, message, detail=()):
    # type: (str, str, str, str, object) -> CorpusFinding
    return CorpusFinding(code, severity, where, message, _cap(detail))


# --------------------------------------------------------------- normalisation

def norm_key(value):
    # type: (object) -> str
    """Identity key: casing, separators and punctuation are not distinctions.

    ``RHEL-8``, ``rhel_8`` and ``rhel 8`` are one concept spelled three ways, and a
    knowledge base that keeps all three has three nodes where it needs one. NFKD
    folding means an accent is not an identity either (``Café`` == ``cafe``): a
    corpus that disagrees with itself about a diacritic is the same defect.
    """
    text = ("%s" % (value,)).strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in folded if ch.isalnum())


def strip_polarity(value):
    # type: (object) -> str
    """``does_not_mitigate`` -> ``mitigate``. Applied ONCE, never to a bare prefix.

    Stripping is only for GROUPING candidates in the synonym check — it never
    rewrites a stored value, because the negation is real information that belongs
    in the ``negated`` qualifier rather than in the predicate's name.
    """
    text = ("%s" % (value,)).strip().lower()
    for pre in _POLARITY:
        if text.startswith(pre) and len(text) > len(pre):
            return text[len(pre):]
    return text


# Verb inflection, folded ONLY for stem grouping inside a curated term list. Without
# it `mitigates` and `does_not_mitigate` reduce to `mitigates` and `mitigate`, which
# do not match — so the one collision the polarity strip exists to catch escapes it.
_INFLECTION = ("ing", "ed", "es", "s")


def _stem(value):
    # type: (object) -> str
    """Identity key for a TERM LIST entry: polarity, inflection and spelling folded.

    Deliberately NOT used by ``norm_key``. Identity for a tag or an entity must stay
    exact modulo case and punctuation, because folding inflection there merges proper
    nouns that differ only by a trailing ``s`` (``Windows`` is not ``window``). In a
    closed vocabulary the trade runs the other way: a curated list holding both
    ``mitigate`` and ``mitigates`` is unambiguously a bug, and inflected tag variants
    are still caught — as a warning — by the similarity sweep.
    """
    key = norm_key(strip_polarity(value))
    # Repeatedly, not once: `bypasses` sheds `es` to `bypass`, which still ends in
    # `s`. Stopping after one pass leaves `bypass` at `bypas` and `bypasses` at
    # `bypass`, so the one pair the fold exists to merge stays split.
    changed = True
    while changed:
        changed = False
        for suffix in _INFLECTION:
            if key.endswith(suffix) and len(key) - len(suffix) >= 3:
                key = key[:-len(suffix)]
                changed = True
                break
    # ... then a bare trailing `e`, so `mitigate` meets `mitigates` -> `mitigat`.
    if key.endswith("e") and len(key) > 3:
        key = key[:-1]
    return key


def _parse_similar_ok(raw):
    # type: (object) -> set
    """``["review_cadence:annual|biannual"]`` -> a lookup set of accepted pairs.

    A similarity finding is a QUESTION ("these two look alike — merge or justify?"),
    so the answer has to be recordable or the same question is asked forever. The
    same mechanism answers the harder case: two strings with ONE identity key that
    are genuinely different things (``/etc/a-b`` and ``/etc/a_b`` are two files).
    Without a way to record that, a false collision would fail every build forever
    with nothing a human could do about it.

    THREE forms are stored, so an entry works whether it names two spellings that
    merely resemble each other, two that differ only in case, or two that collapse to
    the same identity key. An entry may be scoped to a vocabulary/context or left bare.
    """
    out = set()
    for item in (raw or []):
        text = "%s" % (item,)
        scope = ""
        if ":" in text:
            scope, text = text.split(":", 1)
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) != 2:
            continue
        scope = scope.strip()
        # CASE-SENSITIVE, and stored IN ADDITION to the lowered form rather than
        # instead of it. `Docker` and `docker` are one identity key AND one lowered
        # pair, so both of the forms below degenerate to a single element and the
        # `len(pair) != 2` guard in `_accepted` skipped them — which made the ERROR
        # for a case-only collision unclearable by any configuration, while the
        # finding printed the exact entry that provably does nothing. The lowered
        # form stays because a punctuation-only pair binds through it whatever case
        # the operator typed.
        raw_pair = frozenset(parts)
        if len(raw_pair) == 2:
            out.add((scope, raw_pair))
        out.add((scope, frozenset(p.lower() for p in parts)))
        # The normalised pair is stored ONLY when it stays a pair. For the collision
        # case the two spellings share one identity key by definition, so the
        # frozenset degenerates to a single element — and a one-element entry would
        # then match ANY two spellings of that key, whitelisting a whole identity
        # instead of the one pair somebody actually cleared. The raw pair above is
        # what suppresses a collision; this one only serves similarity.
        normed = frozenset(norm_key(p) for p in parts)
        if len(normed) == 2:
            out.add((scope, normed))
    return out


def _accepted(similar_ok, scope, a, b):
    # type: (set, str, str, str) -> bool
    if not similar_ok:
        return False
    # Raw first, then lowered, then normalised. Each is tried only when it is still a
    # PAIR: a one-element candidate would match any two spellings sharing that form
    # and whitelist a whole identity instead of the one pair somebody cleared.
    for pair in (frozenset([("%s" % (a,)).strip(), ("%s" % (b,)).strip()]),
                 frozenset([("%s" % (a,)).strip().lower(),
                            ("%s" % (b,)).strip().lower()]),
                 frozenset([norm_key(a), norm_key(b)])):
        if len(pair) != 2:
            continue                 # a value compared against itself is not a pair
        if (scope, pair) in similar_ok or ("", pair) in similar_ok:
            return True
    return False


def _canonical(members, where, prefer_lower):
    # type: (list, dict, bool) -> str
    """Which spelling of a colliding group to propose as the canonical term.

    Document FREQUENCY decides first — the corpus has already voted, and no
    convention outranks that. Only a genuine tie falls through to convention, and
    the two contexts have opposite ones, both of them already visible in the shipped
    vocabulary: registry terms are lowercase slugs (``rhel-8 -> rhel8``), while
    entity names are proper nouns (``Docker``, not ``docker``). Guessing the wrong
    one produced ``RHEL_8_Fleet`` as a proposed tag, which is how a linter teaches a
    corpus a bad habit.
    """
    def rank(m):
        lowered = ("%s" % (m,)).islower()
        convention = (not lowered) if prefer_lower else lowered
        return (-len(where.get(m) or ()), convention, len(m), m)

    return sorted(members, key=rank)[0]


def _all_accepted(similar_ok, scope, members):
    # type: (set, str, list) -> bool
    """True when EVERY pair in a colliding group has been recorded as deliberate.

    All-or-nothing on purpose: a group of three where only one pair was cleared
    still holds an undeclared collision, and reporting the group minus that pair
    would hide it.
    """
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            if not _accepted(similar_ok, scope, a, b):
                return False
    return True


def _digit_skeleton(key):
    # type: (str) -> str
    return "".join(ch for ch in key if not ch.isdigit())


def _is_series(a, b, skeleton):
    # type: (str, str, dict) -> bool
    """Are these two members of an ENUMERATED FAMILY rather than a typo pair?

    ``node-01``/``node-02``, ``us-east-1``/``us-west-2``, ``rhel8``/``rhel9``,
    ``ISO-27001``/``ISO-27002`` — identical apart from their digits. They are the
    single largest source of false similarity findings, and not by a small margin:
    a corpus naming 1200 hosts produces ~375,000 mutually-similar pairs, which
    buries every real finding and takes a minute to compute.

    A number is what MAKES these distinct, so a differing number is evidence AGAINST
    synonymy, not for it. Note this also suppresses ``rhel``/``rhel8``: that pair is
    a hierarchy rather than a synonym, and the vocabulary models it with `broader`.
    """
    return a != b and skeleton[a] == skeleton[b]


def _shingles(key, q):
    # type: (str, int) -> list
    """MULTISET q-grams of ``key`` as ``(gram, occurrence-index)`` tokens.

    The occurrence index is what makes the token list a multiset rather than a set,
    and the bound in ``_shingle_plan`` is a multiset bound: a matching block of
    length L contributes L-q+1 gram OCCURRENCES to both strings, and for any gram
    value the number of such occurrences is at most ``min(count_a, count_b)``. Fold
    the duplicates away into a set and the bound is no longer valid — measured on a
    1000-document register corpus, a set-valued index dropped 616 of 101,044 true
    pairs while claiming to be complete.
    """
    seen = {}
    out = []  # type: list
    for i in range(len(key) - q + 1):
        g = key[i:i + q]
        n = seen.get(g, 0)
        seen[g] = n + 1
        out.append((g, n))
    return out


def _shingle_plan(threshold):
    # type: (float) -> tuple
    """``(q, min_len)`` — the widest SOUND shingle for ``threshold``, and from what
    length it is usable. ``(0, 0)`` means no shingle is sound at this threshold.

    THE BOUND, because a blocking key nobody can check is how the previous version
    (bucket by the first two characters) shipped a sweep with unknown recall.

    Write ``S = la + lb``. A reported pair has ``ratio = 2M/S >= t``, so
    ``M >= tS/2``. The matching blocks number ``k``; every extra block needs an
    unmatched character on at least one side, so ``k <= (S - 2M) + 1``. A block of
    length ``L`` contributes ``L - q + 1`` shared gram occurrences (never negative),
    so the multiset shingle intersection obeys::

        shared >= M - k(q-1) >= M(2q-1) - (q-1)(S+1) >= S[(2q-1)t/2 - (q-1)] - (q-1)

    That is positive for every S only when ``t > 2(q-1)/(2q-1)``: q=2 needs
    ``t > 0.667``, q=3 needs ``t > 0.8``, q=4 needs ``t > 0.857``. The shipped
    threshold is 0.78, so BIGRAMS are the widest sound width — trigrams look more
    selective and are simply wrong here, which is measurable rather than arguable.

    ``min_len``: the bound only guarantees a shared shingle once it reaches 1, i.e.
    ``S >= q / [(2q-1)t/2 - (q-1)]``. Combined with the length filter
    (``S >= l(1+bound)``) that is a floor on the key's own length. Keys under it are
    swept exhaustively — sound, and few: 11 of 6,497 on the register corpus.
    """
    bound = threshold / (2.0 - threshold) if threshold < 2.0 else 1.0
    for q in (2,):
        slope = (2 * q - 1) * threshold / 2.0 - (q - 1)
        if slope <= 0:
            continue
        min_s = q / slope
        min_len = int(min_s / (1.0 + bound))
        while min_len * (1.0 + bound) < min_s:
            min_len += 1
        return (q, max(q, min_len))
    return (0, 0)


def _alpha(la, lb, q, threshold):
    # type: (int, int, int, float) -> float
    """Shingles a pair MUST share to be able to reach ``threshold`` (see the bound)."""
    s = la + lb
    return threshold * s / 2.0 * (2 * q - 1) - (q - 1) * (s + 1)


def _similar_pairs(keys, threshold, similar_ok, scope, max_pairs=_FINDING_CAP):
    # type: (list, float, set, str, int) -> tuple
    """``([(a, b, ratio)], stats, dropped)`` for strings that look like each other.

    COMPLETE at every corpus size — no pair that clears ``threshold`` is skipped,
    which is the whole point of this rewrite. The previous version fell back to
    bucketing by the first two characters above 4000 distinct strings, and that
    bucketing is unsound in exactly the direction that matters: ``kubernetes`` and
    ``ubernetes`` (a dropped leading character — the commonest paste defect there
    is) land in different buckets and are never compared. Recall dropped silently to
    an unknown number at precisely the corpus size where near-duplicates start to
    matter.

    Four filters, cheapest first, and every one of them is SOUND — each can only
    discard pairs that could never have cleared the threshold:

      LENGTH    ``ratio = 2M/(la+lb)`` and ``M <= min(la, lb)``, so a pair can only
                reach ``t`` if the shorter is at least ``t/(2-t)`` of the longer.
      SHINGLE   an inverted index over multiset bigrams, with the per-pair floor
                ``_alpha``. This is the blocking: candidates come from the postings
                of the probe's own shingles instead of from the whole corpus.
      SERIES    identical apart from digits — an enumerated family, not a typo.
      QUICK     ``real_quick_ratio``/``quick_ratio`` are difflib's own upper bounds,
                so the O(n*m) comparison runs only for genuine candidates.

    COMPLEXITY. Exhaustive is O(n^2) pair tests; blocked is O(sum of posting lengths
    touched), which is data-dependent but bounded by the same worst case. Measured on
    the 1000-document register corpus below (6,497 distinct keys, 101,044 true
    pairs — a deliberately adversarial shape, since every key is drawn from one
    naming template): 58.3s exhaustive vs 16.2s blocked, same 101,044 pairs, zero
    missed and zero extra. The win is smaller than a textbook LSH would promise
    because at t=0.78 the neighbourhood genuinely is large; the point is that the
    result is now the SAME result, not a cheaper approximation of it.

    ``stats`` carries the sweep's shape (terms, how many were compared exhaustively
    because they are too short to block, the shingle width) so the caller can
    disclose it. Findings are still capped at ``max_pairs`` with the remainder
    counted, because a cap that does not say what it dropped reads as "this is all
    of them".
    """
    order = sorted(set(k for k in keys if k), key=lambda k: (len(k), k))
    skeleton = dict((k, _digit_skeleton(k)) for k in order)
    # Minimum shorter/longer length ratio that can still reach `threshold`.
    bound = threshold / (2.0 - threshold) if threshold < 2.0 else 1.0
    q, min_len = _shingle_plan(threshold)
    out = []  # type: list
    sm = SequenceMatcher(None)

    def keep(a, b, ratio):
        # `a` is always the earlier key in `order`, so the pair is oriented exactly
        # as the exhaustive sweep oriented it. That is not cosmetic: difflib's
        # ratio() is NOT symmetric (measured: `ddrmstraddrset` vs `ddrslvaddr5set`
        # is 0.786 one way and 0.429 the other), so swapping the operands would
        # change WHICH pairs a corpus reports.
        if ratio >= threshold and not _accepted(similar_ok, scope, a, b):
            out.append((a, b, ratio))

    # Phase 1 — keys too short for the shingle bound to bite. Every pair involving
    # one of them is swept here, in full. They sort first (they are the shortest),
    # so scanning forward from each covers every such pair exactly once.
    short = [i for i, k in enumerate(order) if q == 0 or len(k) < min_len]
    for i in short:
        a = order[i]
        la = len(a)
        sm.set_seq2(a)                           # seq2 is the cached side
        for b in order[i + 1:]:
            if la < bound * len(b):
                break                            # sorted by length: no later b can
            if _is_series(a, b, skeleton):
                continue
            sm.set_seq1(b)
            if sm.real_quick_ratio() < threshold:
                continue
            if sm.quick_ratio() < threshold:
                continue
            keep(a, b, sm.ratio())

    # Phase 2 — the blocked sweep over everything else. Walked in REVERSE order so
    # the probe is always the earlier key and the index holds the later ones; that
    # keeps the orientation above and lets seq2 stay cached across a whole probe.
    blocked = [k for k in order if not (q == 0 or len(k) < min_len)]
    index = OrderedDict()
    tokens = OrderedDict((k, _shingles(k, q)) for k in blocked)
    compared = 0
    for a in reversed(blocked):
        la = len(a)
        ta = tokens[a]
        cand = Counter()
        for g in ta:
            posting = index.get(g)
            if posting:
                cand.update(posting)
        sm.set_seq2(a)
        for b, shared in cand.items():
            lb = len(b)
            if la < bound * lb:
                continue
            need = _alpha(la, lb, q, threshold)
            if shared < need:
                continue
            if _is_series(a, b, skeleton):
                continue
            compared += 1
            sm.set_seq1(b)
            if sm.real_quick_ratio() < threshold:
                continue
            if sm.quick_ratio() < threshold:
                continue
            keep(a, b, sm.ratio())
        for g in ta:
            index.setdefault(g, []).append(a)

    out.sort(key=lambda t: (-t[2], t[0], t[1]))
    dropped = max(0, len(out) - max_pairs)
    stats = OrderedDict([
        ("terms", len(order)),
        ("shingle", q),
        ("blocked_terms", len(blocked)),
        ("exhaustive_terms", len(short)),
        ("compared", compared),
        ("complete", True),
    ])
    return (out[:max_pairs], stats, dropped)


# --------------------------------------------------------------- row plumbing

def _rows(docs):
    # type: (object) -> tuple
    """``([(path, meta)], findings)`` — a malformed row is REPORTED, never dropped.

    Accepts the walker's dicts (``{"path": ..., "meta": ...}``) or plain
    ``(path, meta)`` pairs, so a caller holding either shape can grade a corpus
    without reshaping it first.
    """
    rows = []  # type: list
    findings = []  # type: list
    for i, d in enumerate(docs or []):
        if isinstance(d, dict) and "meta" in d:
            path, meta = d.get("path") or "", d.get("meta")
        elif isinstance(d, (tuple, list)) and len(d) == 2:
            path, meta = d[0], d[1]
        else:
            findings.append(_f("corpus-row-malformed", ERROR, "docs[%d]" % i,
                               "expected {'path','meta'} or (path, meta), got %s"
                               % type(d).__name__))
            continue
        if not isinstance(meta, dict):
            findings.append(_f("corpus-row-malformed", ERROR,
                               "%s" % (path or "docs[%d]" % i),
                               "metadata block is %s, not a mapping — this document "
                               "contributes to no corpus check"
                               % type(meta).__name__))
            continue
        rows.append(("%s" % (path or "docs[%d]" % i), meta))
    return (rows, findings)


def _str_list(meta, key):
    # type: (dict, str) -> list
    val = meta.get(key)
    if isinstance(val, list):
        return [v for v in val if isinstance(v, str) and v.strip()]
    if isinstance(val, str) and val.strip():
        return [val]
    return []


def _records(meta, name):
    # type: (dict, str) -> list
    val = meta.get(name)
    if not isinstance(val, list):
        return []
    return [r for r in val if isinstance(r, dict)]


def _entity_mentions(meta, vocab, nameless=None):
    # type: (dict, object, list) -> list
    """``(name, type)`` for every entity named in one document.

    Mirrors ``_lint._entity_rows`` but corpus-side: it wants the NAME (the thing that
    becomes a graph node) rather than the type alone, and it accepts the scalar-group
    shape (``identifiers: {gpg_fingerprint: ...}``) because those keys are node names
    too — a mapping group is the shape a naive walk skips entirely.
    """
    out = []  # type: list
    groups = meta.get("entities")
    if not isinstance(groups, dict):
        return out
    typed = _has(vocab, "entity_types")
    for gname, members in groups.items():
        # No implied type is a real state, not an error: a group nobody registered
        # leaves its members untyped here, and the per-document gate is the one that
        # reports that. Corpus-side an untyped mention still counts as a NODE.
        implied = vocab.group_type(gname) if typed else ""
        if isinstance(members, dict):
            for key in members.keys():
                if isinstance(key, str) and key.strip():
                    out.append((key, implied))
            continue
        if not isinstance(members, list):
            continue
        for i, ent in enumerate(members):
            if not isinstance(ent, dict):
                continue
            name = ""
            for key in ("name", "fqdn", "path", "id"):
                if isinstance(ent.get(key), str) and ent[key].strip():
                    name = ent[key]
                    break
            if not name:
                # An entity with no name is a node with no identity: it can never
                # collide, never be reused, never be an endpoint. Dropping it makes
                # the corpus look cleaner for containing it, which is the exact
                # inversion this package forbids.
                if nameless is not None:
                    nameless.append("entities.%s[%d]" % (gname, i))
                continue
            etype = ent.get("type")
            if "type" in ent and not isinstance(etype, str):
                # `_lint._entity_rows` makes a retyped scalar an ERROR and explicitly
                # refuses the group fallback. Falling back here would have the two
                # walks disagree about the same document — the per-document gate
                # reporting a broken type while the corpus gate quietly invents one.
                etype = ""
            elif not etype:
                etype = implied
            out.append((name, etype))
    return out


# --------------------------------------------------------------- the checks

def identity_report(docs):
    # type: (object) -> dict
    """Two documents may not claim one identity, and a document with none is unlinkable.

    A collision is not a defect in either document — each is internally consistent —
    which is precisely why nothing but a corpus walk can find it. Downstream it is
    silent and destructive: whichever document is indexed second wins, and the first
    disappears from every lookup by id.
    """
    rows, findings = _rows(docs)
    seen = OrderedDict()   # (key, value) -> [path]
    for path, meta in rows:
        has_any = False
        for key in ("id", "uid"):
            val = meta.get(key)
            if val is None:
                continue
            if not isinstance(val, str):
                # An unquoted `id: 42` is parsed as an int by any YAML reader, and
                # `id` is an authored-wins hand-editable field, so a numeric document
                # number is ordinary authoring rather than corruption. Skipping it
                # here would take the document out of the collision check entirely —
                # the gate would go quiet on the one case it exists for. Compare on
                # the string form, and say that the value needs quoting.
                findings.append(_f(
                    "identity-coerced", WARN, path,
                    "`%s` is %s (%r), not a string — an unquoted scalar was retyped; "
                    "quote it so the identity cannot change shape between readers"
                    % (key, type(val).__name__, val)))
            text = ("%s" % (val,)).strip()
            if text:
                has_any = True
                seen.setdefault((key, text), []).append(path)
        if not has_any:
            findings.append(_f(
                "identity-missing", WARN, path,
                "no `id` and no `uid` — nothing in the corpus can link to this "
                "document, and `see_also` can never resolve to it"))
    collisions = 0
    for (key, val), paths in seen.items():
        if len(paths) > 1:
            collisions += 1
            findings.append(_f(
                "identity-collision", ERROR, "%s=%s" % (key, val),
                "%d documents claim the same `%s` — an index keyed by it keeps "
                "whichever it saw last" % (len(paths), key),
                sorted(paths)))
    return {"findings": findings,
            "metrics": OrderedDict([("documents", len(rows)),
                                    ("identities", len(seen)),
                                    ("collisions", collisions)])}


def synonym_report(docs, vocab):
    # type: (object, object) -> dict
    """Synonym pollution across the registry fields — the reason this package exists.

    Values are NORMALISED through the vocabulary first, so a variant already declared
    as an alias never shows up here (the per-document gate reports that as
    ``vocab-alias``). What survives normalisation is drift nobody has decided about
    yet, and it arrives in two shapes:

      COLLISION   two spellings with the same identity key. Not a judgement call —
                  ``fleet-upgrade`` and ``fleet_upgrade`` are one term, so this is an
                  error and an alias entry is proposed for it.
      SIMILARITY  two spellings that merely look alike. ``annual`` and ``biannual``
                  are a legitimate pair; ``kubernetes`` and ``kubernets`` are not.
                  Only a human can tell, so this is a warning and the answer is
                  recordable in ``lint.similar_ok``.

    Both count DOCUMENT frequency, not occurrences: one document repeating a tag
    thirty times is one document's opinion.
    """
    rows, findings = _rows(docs)
    threshold = float(_threshold(vocab, "synonym_similarity", 0.78))
    similar_ok = _parse_similar_ok(_threshold(vocab, "similar_ok", []) or [])
    max_terms = int(_threshold(vocab, "similarity_max_terms", _SWEEP_MAX_TERMS))
    corroborated_at = int(_threshold(vocab, "promote_at", 3))
    min_docs = int(_threshold(vocab, "corpus_min_docs", 5))

    registry_fields = [f.name for f in FIELDS
                       if f.vocab and _has(vocab, f.vocab)
                       and vocab.governance(f.vocab) == "registry"]

    metrics = OrderedDict()
    suggestions = OrderedDict()
    for name in registry_fields:
        vname = field(name).vocab
        # spelling -> set(documents). Both the field and its proposals slot: a term
        # waiting for promotion is exactly where drift is most likely, and dropping
        # proposals here would hide the collisions that matter most.
        where = OrderedDict()
        for path, meta in rows:
            seen = set(_str_list(meta, name)) | set(_str_list(meta, proposed_key(name)))
            for raw in seen:
                canonical, _q = vocab.normalize(vname, raw)
                if not isinstance(canonical, str) or not canonical.strip():
                    continue
                where.setdefault(canonical, set()).add(path)

        groups = OrderedDict()
        for spelling in where:
            key = norm_key(spelling)
            if key:
                groups.setdefault(key, []).append(spelling)

        collisions = 0
        # `sorted(groups)`, not `groups.items()`: `where` is filled by iterating a
        # per-document SET, so `groups` inherits an order that varies with the
        # process's hash seed — and `suggestions` is filled in this loop's order, so
        # two runs over byte-identical input wrote a different `alias_suggestions`
        # block into `kb_lint --json`. Sorting the members inside a group (below) only
        # ever ordered the variants WITHIN one collision; it is the order OF THE
        # GROUPS that was loose. Sorted HERE rather than at the traversal point on
        # purpose: `sweep_keys` below is handed to a truncating similarity sweep, and
        # reordering its input would trade one nondeterminism for a less visible one.
        for key in sorted(groups):
            members = groups[key]
            if len(members) < 2:
                continue
            if _all_accepted(similar_ok, vname, members):
                continue          # recorded as deliberately distinct spellings
            collisions += 1
            canonical = _canonical(members, where, prefer_lower=True)
            # `sorted`, not raw member order: `members` was built by iterating a set,
            # so its order varies between runs and the JSON report would not be
            # reproducible even though the findings themselves are stable.
            # Keyed by the VOCABULARY name, not the schema field name: the block is
            # pasted under `<vocabulary>.aliases:` in vocab.yaml. Today every
            # registry field happens to share its vocabulary's name, so the two are
            # indistinguishable — which is exactly how this would ship broken and
            # only fail the first time somebody adds a field that does not.
            for m in sorted(members):
                if m != canonical:
                    suggestions.setdefault(vname, OrderedDict())[m] = canonical
            findings.append(_f(
                "synonym-collision", ERROR, "%s/%s" % (name, key),
                "%d spellings of one term — they are separate values in every index; "
                "add aliases to `%s.aliases` (suggested canonical: %r)"
                % (len(members), vname, canonical),
                ["%s (%d doc%s)" % (m, len(where[m]), "" if len(where[m]) == 1 else "s")
                 for m in sorted(members, key=lambda m: (-len(where[m]), m))]))

        # DOCUMENT frequency per identity, so the scope floor below counts concepts
        # rather than spellings — the same rule the collision check groups by.
        key_docs = OrderedDict()
        for key, members in groups.items():
            seen_docs = set()
            for m in members:
                seen_docs |= where[m]
            key_docs[key] = len(seen_docs)

        # SCOPE, not method: `_similar_pairs` is complete at every size, so nothing
        # here is a recall trade in the algorithm. What a huge registry field costs
        # is ATTENTION — measured on 1000 synthetic silicon specs, `keywords` held
        # 24,630 distinct terms and produced 371,223 similar pairs, of which the
        # report can show 25. Past `max_terms` the sweep therefore asks its question
        # only about terms the corpus has corroborated on `promote_at` documents,
        # which is the same threshold that decides whether a term is real at all.
        # Every excluded term is still covered by the exact-collision check above,
        # and the count is reported — a narrowed sweep read as a clean one is the
        # false all-clear this package exists to prevent.
        sweep_keys = list(groups.keys())
        excluded = 0
        if len(sweep_keys) > max_terms:
            kept = [k for k in sweep_keys if key_docs[k] >= corroborated_at]
            excluded = len(sweep_keys) - len(kept)
            sweep_keys = kept
            findings.append(_f(
                "synonym-sweep-scoped", WARN, name,
                "%d distinct terms is past the %d-term sweep cap, so the SIMILARITY "
                "sweep was scoped to the %d term(s) on >= %d document(s); %d "
                "single-use term(s) were not compared to each other (the exact "
                "collision check above still covers all %d)"
                % (len(groups), max_terms, len(sweep_keys), corroborated_at,
                   excluded, len(groups))))

        # A registry that grows a term per document is not a vocabulary. This is the
        # `keywords` failure directly: seeded from every SCREAMING_SNAKE token in
        # every body, it reached 24,630 terms over 1000 documents (44.5% of them used
        # once, 6,526 at once "ready for promotion"). Neither the singleton rate nor
        # the similarity sweep can say that — the singleton rate is computed over
        # PROMOTED terms and reads 0.00 while the registry drowns.
        hapax = sum(1 for k, n in key_docs.items() if n <= 1)
        # More distinct terms than DOCUMENTS is the load-bearing half — a curated
        # registry converges, so passing one term per document means it never will.
        # The hapax share is the guard against firing on a large but real vocabulary
        # that every document draws from.
        if (len(rows) >= min_docs and len(groups) > len(rows)
                and hapax * 4 >= len(groups)):
            findings.append(_f(
                "registry-flood", WARN, name,
                "%d distinct terms across %d document(s), %d of them (%.0f%%) used "
                "by a single document — a registry growing faster than the corpus is "
                "an extractor writing free text into a governed field. Cap what seeds "
                "`%s`, or regovern it."
                % (len(groups), len(rows), hapax, 100.0 * hapax / len(groups), name),
                ["%s (1 doc)" % m for m in
                 sorted(k for k, n in key_docs.items() if n <= 1)]))

        pairs, stats, dropped = _similar_pairs(sweep_keys, threshold,
                                               similar_ok, vname)
        if stats["exhaustive_terms"] and stats["terms"] > max_terms:
            findings.append(_f(
                "synonym-sweep-scope", INFO, name,
                "similarity sweep compared %d term(s) in full: %d through the "
                "bigram index and %d too short to block" % (
                    stats["terms"], stats["blocked_terms"],
                    stats["exhaustive_terms"])))
        if dropped:
            findings.append(_f(
                "synonym-similar-truncated", WARN, name,
                "%d further similar pair(s) are not listed — this vocabulary needs a "
                "merge pass, not a longer report" % dropped))
        for a, b, ratio in pairs:
            sa = sorted(groups[a], key=lambda m: (-len(where[m]), m))[0]
            sb = sorted(groups[b], key=lambda m: (-len(where[m]), m))[0]
            findings.append(_f(
                "synonym-similar", WARN, "%s/%s~%s" % (name, a, b),
                "%r and %r are %.0f%% similar — merge them, or record the pair in "
                "`lint.similar_ok` as %r" % (sa, sb, ratio * 100,
                                             "%s:%s|%s" % (vname, sa, sb)),
                ["%s (%d doc%s)" % (s, len(where[s]), "" if len(where[s]) == 1 else "s")
                 for s in (sa, sb)]))

        metrics[name] = OrderedDict([
            ("vocab", vname),
            ("terms", len(where)),
            ("collisions", collisions),
            ("similar_pairs", len(pairs)),
            # The sweep's own shape, in the machine-readable report as well as in
            # the printed findings: a consumer must be able to see that a number
            # was computed over a scoped set without parsing prose.
            ("swept", stats["terms"]),
            ("not_swept", excluded),
            ("similar_pairs_dropped", dropped),
            ("single_document_terms", hapax),
        ])
    return {"findings": findings, "metrics": metrics, "aliases": suggestions}


def entity_report(docs, vocab):
    # type: (object, object) -> dict
    """Entity identity across documents — the defect that quietly destroys a graph.

    One thing spelled two ways is two nodes, and nothing downstream ever says so:
    both nodes look healthy, both have edges, and every query returns half the
    answer. The reuse rate at the end is the honest measure of whether this is a
    knowledge graph at all — an entity vocabulary with no sharing is N isolated
    documents that happen to be in one directory.
    """
    rows, findings = _rows(docs)
    threshold = float(_threshold(vocab, "synonym_similarity", 0.78))
    similar_ok = _parse_similar_ok(_threshold(vocab, "similar_ok", []) or [])

    where = OrderedDict()      # spelling -> set(documents)
    groups = OrderedDict()     # identity key -> [spelling]
    # Typing is keyed on the IDENTITY, never on the spelling. Keyed on the spelling,
    # `Docker` (Software) and `docker` (Package) are two untroubled entries and the
    # conflict is invisible — so the check would only ever fire on the case that
    # needs it least, and would be silently disarmed by the very collision the
    # check above exists to report.
    types = OrderedDict()      # identity key -> {type: set(documents)}
    reach = OrderedDict()      # identity key -> set(documents)
    nameless = []  # type: list
    for path, meta in rows:
        at = []  # type: list
        for name, etype in _entity_mentions(meta, vocab, nameless=at):
            key = norm_key(name)
            if not key:
                continue
            where.setdefault(name, set()).add(path)
            if name not in groups.setdefault(key, []):
                groups[key].append(name)
            reach.setdefault(key, set()).add(path)
            if etype:
                types.setdefault(key, OrderedDict()).setdefault(
                    etype, set()).add(path)
        nameless.extend("%s %s" % (path, w) for w in at)
    if nameless:
        findings.append(_f(
            "entity-nameless", WARN, "entities",
            "%d entity entr(ies) carry no name/fqdn/path/id — a node with no "
            "identity cannot collide, be reused, or be a relation endpoint, so it "
            "is invisible to every gate below" % len(nameless), sorted(nameless)))

    collisions = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        if _all_accepted(similar_ok, "entities", members):
            continue          # recorded as deliberately distinct spellings
        collisions += 1
        canonical = _canonical(members, where, prefer_lower=False)
        # EVERY unaccepted pair, not just the first two. `_all_accepted` demands all
        # of them, so a three-way group whose message named one pair printed a remedy
        # that, pasted back verbatim, reprinted the identical message.
        todo = ["entities:%s|%s" % (a, b)
                for i, a in enumerate(sorted(members))
                for b in sorted(members)[i + 1:]
                if not _accepted(similar_ok, "entities", a, b)]
        findings.append(_f(
            "entity-collision", ERROR, "entities/%s" % key,
            "one entity, %d spellings — these are %d nodes in the graph, not one "
            "(suggested canonical: %r). If they are genuinely different things, "
            "record the pair as `lint.similar_ok: [%s]`"
            % (len(members), len(members), canonical, ", ".join(todo)),
            [_where_line(m, where[m])
             for m in sorted(members, key=lambda m: (-len(where[m]), m))]))

    # One identity, two types. Sometimes two things genuinely share a label, so this
    # is a question rather than a verdict — but a graph cannot answer it, and
    # whichever type is loaded last silently wins.
    conflicts = 0
    for key, by_type in types.items():
        if len(by_type) > 1:
            conflicts += 1
            label = _canonical(groups.get(key) or [key], where, prefer_lower=False)
            findings.append(_f(
                "entity-type-conflict", WARN, "entities/%s" % label,
                "typed %d different ways across the corpus — either two entities "
                "share a name (rename one) or one entity is mistyped" % len(by_type),
                ["%s in %s" % (t, ", ".join(sorted(by_type[t])[:3]))
                 for t in sorted(by_type)]))

    pairs, sweep, dropped = _similar_pairs(list(groups.keys()), threshold,
                                           similar_ok, "entities")
    if sweep["terms"] > int(_threshold(vocab, "similarity_max_terms",
                                       _SWEEP_MAX_TERMS)):
        # Entities are NOT scoped by document frequency the way a registry field is:
        # an entity named by one document is still a node in the graph, and a
        # misspelling of it is still two nodes. The sweep is complete here at any
        # size; what the operator is told is what it cost.
        findings.append(_f(
            "entity-sweep-scope", INFO, "entities",
            "%d distinct entity names swept in full — %d through the bigram index, "
            "%d too short to block; no pair was skipped"
            % (sweep["terms"], sweep["blocked_terms"], sweep["exhaustive_terms"])))
    if dropped:
        findings.append(_f(
            "entity-similar-truncated", WARN, "entities",
            "%d further similar entity pair(s) are not listed — at this volume the "
            "entity vocabulary needs a naming convention, not a longer report"
            % dropped))
    for a, b, ratio in pairs:
        sa = sorted(groups[a], key=lambda m: (-len(where[m]), m))[0]
        sb = sorted(groups[b], key=lambda m: (-len(where[m]), m))[0]
        findings.append(_f(
            "entity-similar", WARN, "entities/%s~%s" % (a, b),
            "%r and %r are %.0f%% similar — one entity misspelled, or two that need "
            "distinguishable names" % (sa, sb, ratio * 100),
            [_where_line(s, where[s]) for s in (sa, sb)]))

    # Reuse is counted per IDENTITY too. Counted per spelling, a corpus where every
    # document writes the same entity a slightly different way scores 0% reuse — the
    # collision defect would depress the very number that measures whether this is a
    # graph, and the two findings would contradict each other.
    shared = sum(1 for _k, paths in reach.items() if len(paths) > 1)
    total = len(reach)
    if total and len(rows) > 1:
        findings.append(_f(
            "entity-reuse", INFO, "entities",
            "%d/%d entities appear in more than one document (%.0f%%)%s"
            % (shared, total, (100.0 * shared / total),
               "" if shared else " — no shared vocabulary yet, so the corpus is not "
                                 "yet a graph")))
    return {"findings": findings,
            "metrics": OrderedDict([("mentions", sum(len(p) for p in where.values())),
                                    ("distinct", total),
                                    ("spellings", len(where)),
                                    ("shared", shared),
                                    ("collisions", collisions),
                                    ("type_conflicts", conflicts),
                                    ("similar_pairs", len(pairs))])}


def graph_report(docs, vocab):
    # type: (object, object) -> dict
    """Link and edge integrity across the corpus.

    ``see_also`` resolution is corpus-scoped by definition, and so is the question
    behind it: which documents nothing points at. An orphan is not wrong — a
    reference page legitimately has no inbound links — so it is reported and never
    failed. Relation endpoints are measured the same way: an edge naming something
    no document declares as an entity is an edge to nowhere, and the RATE is the
    useful number, not a finding per edge.
    """
    rows, findings = _rows(docs)
    known = set()
    identities = OrderedDict()          # document path -> its identity strings
    for path, meta in rows:
        for key in ("id", "uid"):
            val = meta.get(key)
            if val is None:
                continue
            text = ("%s" % (val,)).strip()
            if text:
                known.add(text)
                identities.setdefault(path, set()).add(text)

    inbound = Counter()
    dangling = OrderedDict()
    for path, meta in rows:
        for raw in _str_list(meta, "see_also"):
            target = raw.strip()
            if target.startswith("[[") and target.endswith("]]"):
                target = target[2:-2]
            if target in known:
                inbound[target] += 1
            else:
                dangling.setdefault(target, []).append(path)

    if dangling:
        # Per-document linting already flags each dead pointer; the corpus adds the
        # roll-up a reviewer actually acts on — one list, deduplicated by target.
        findings.append(_f(
            "see-also-dangling", WARN, "see_also",
            "%d distinct see_also target(s) resolve to no document in this corpus"
            % len(dangling),
            ["%s <- %s" % (t, ", ".join(sorted(set(p))[:3]))
             for t, p in sorted(dangling.items())]))

    entity_keys = set()
    for _path, meta in rows:
        for name, _t in _entity_mentions(meta, vocab):
            key = norm_key(name)
            if key:
                entity_keys.add(key)

    endpoints = 0
    unresolved = Counter()
    incomplete = []  # type: list
    for path, meta in rows:
        for i, rec in enumerate(_records(meta, "relations")):
            for side in ("s", "o"):
                val = rec.get(side)
                if not isinstance(val, str) or not val.strip():
                    # An edge with only one end is not an edge. Dropping it from the
                    # count would let a corpus of half-written relations report a
                    # perfect endpoint rate over the ends that happen to exist.
                    incomplete.append("%s relations[%d].%s" % (path, i, side))
                    continue
                endpoints += 1
                # No identity key means the endpoint names nothing an entity could
                # ever match. Counting it as resolved credits the corpus for a link
                # it does not have.
                if norm_key(val) not in entity_keys:
                    unresolved[val] += 1
    if incomplete:
        findings.append(_f(
            "relation-incomplete", WARN, "relations",
            "%d relation endpoint(s) are missing or blank — an edge with one end "
            "cannot be traversed and is excluded from the endpoint rate below"
            % len(incomplete), sorted(incomplete)))
    if endpoints:
        resolved = endpoints - sum(unresolved.values())
        findings.append(_f(
            "relation-endpoints", INFO, "relations",
            "%d/%d relation endpoints (%.0f%%) name an entity declared somewhere in "
            "the corpus" % (resolved, endpoints, 100.0 * resolved / endpoints),
            ["%s x%d" % (v, n) for v, n in unresolved.most_common(_DETAIL_CAP)]))

    # Orphans are counted per DOCUMENT, not per identity string. A document carrying
    # both an `id` and a `uid` is normally linked by its `id` alone, so counting
    # identities makes every `uid` in the corpus a guaranteed orphan and the rate
    # reads ~50% on a perfectly connected corpus.
    orphans = sorted(p for p, ids in identities.items()
                     if not any(inbound[i] for i in ids))
    if orphans and len(rows) >= int(_threshold(vocab, "corpus_min_docs", 5)):
        findings.append(_f(
            "graph-orphans", INFO, "see_also",
            "%d/%d documents have no inbound see_also"
            % (len(orphans), len(identities)), orphans))
    return {"findings": findings,
            "metrics": OrderedDict([("identities", len(known)),
                                    # occurrences on both sides, so they add up
                                    ("links", int(sum(inbound.values())
                                                  + sum(len(p) for p
                                                        in dangling.values()))),
                                    ("dangling", len(dangling)),
                                    ("orphans", len(orphans)),
                                    ("relation_endpoints", endpoints),
                                    ("incomplete_endpoints", len(incomplete)),
                                    ("unresolved_endpoints",
                                     int(sum(unresolved.values())))])}


def skew_report(docs, schema_current="", vocab_current=""):
    # type: (object, str, str) -> dict
    """Which documents are behind the current schema — the backfill work list.

    This is the check that makes "bump the version and re-run" a bounded operation
    instead of a re-conversion of everything: the answer to *which documents does the
    bump invalidate* is a corpus fact, and the two versions answer different
    questions. ``schema_version`` moving means the field INVENTORY changed;
    ``vocab_version`` moving means the TERM LIST changed, which invalidates
    classifications while leaving every other field untouched.

    "CURRENT" IS THE VERSION THE RUNNING CODE EMITS, not the newest one the corpus
    happens to hold. Measured corpus-relative, a UNIFORMLY STALE corpus — every
    document at the old number, which is the exact state a bump leaves behind and the
    exact state this check exists to name — reported zero backfill work, zero
    warnings and exit 0 even under ``--strict``, and stamped the stale number into
    ``kb_lint.json`` as ``current`` while the same file's run header printed the real
    one. Both authoritative values are keyword arguments with empty defaults so an
    existing caller keeps working; passing neither restores the corpus-relative
    reading, which is honest only when the code's own version is genuinely unknown.

    A block with no version is a WARN, not an error: it is still backfillable — a
    selective pass must simply be written as "not the current version" rather than
    "less than the current version", or it skips these forever.
    """
    rows, findings = _rows(docs)
    metrics = OrderedDict()
    authoritative = {"schema_version": ("%s" % (schema_current,)).strip(),
                     "vocab_version": ("%s" % (vocab_current,)).strip()}
    for key in ("schema_version", "vocab_version"):
        counts = Counter()
        behind = []  # type: list
        unstamped = []  # type: list
        for path, meta in rows:
            if _unstamped(meta.get(key)):
                unstamped.append(path)
                counts["<unset>"] += 1
            else:
                counts["%s" % (meta.get(key),)] += 1
        stamped = [v for v in counts if v != "<unset>"]
        code_version = authoritative.get(key) or ""
        corpus_newest = _newest(stamped)
        # `_newest` over both, rather than the code's value outright: a corpus that
        # has run AHEAD of this checkout (a colleague's newer build) is a real state,
        # and calling every one of those documents "behind" would invert the work
        # list. What must never happen again is the corpus deciding what current is
        # while the code silently agrees.
        current = _newest(stamped + [code_version]) if code_version else corpus_newest
        if code_version and stamped and corpus_newest != current:
            findings.append(_f(
                "schema-corpus-behind", WARN, key,
                "the WHOLE corpus is behind: the newest `%s` any document carries is "
                "%s and the running code writes %s, so every stamped document (%d) "
                "needs a backfill — corpus-relative this check saw nothing to do"
                % (key, corpus_newest, current, len(rows) - len(unstamped))))
        for path, meta in rows:
            # The SAME predicate both loops. Using `is None` here while the loop
            # above also treats blank as unstamped puts a blank-stamped document in
            # both lists — reported once as missing a version and once as behind a
            # version, the second time with an empty parenthesis for evidence.
            if _unstamped(meta.get(key)):
                continue
            if "%s" % (meta.get(key),) != current:
                behind.append("%s (%s)" % (path, meta.get(key)))
        if behind:
            findings.append(_f(
                "schema-skew", WARN, key,
                "%d document(s) are not on %s=%s — this is the backfill work list"
                % (len(behind), key, current), sorted(behind)))
        if unstamped and not current:
            # NOTHING is stamped. "These documents are behind" is then meaningless —
            # there is no current version for them to be behind — and naming every
            # document implicates each one for a property the corpus as a whole
            # lacks. One finding about the corpus, with no per-document evidence,
            # is both the honest shape and the actionable one.
            findings.append(_f(
                "schema-unstamped", WARN, key,
                "no document carries `%s`, so this corpus cannot be backfilled "
                "selectively at all — a version bump means re-running everything"
                % key))
        elif unstamped:
            findings.append(_f(
                "schema-unstamped", WARN, key,
                "%d of %d document(s) carry no `%s`, so a selective backfill must "
                "select on `!= %s` rather than `< %s` or it will skip them "
                "permanently" % (len(unstamped), len(rows), key, current, current),
                sorted(unstamped)))
        metrics[key] = OrderedDict([
            ("current", current),
            ("behind", len(behind)),
            ("unstamped", len(unstamped)),
            ("distribution", OrderedDict(sorted(counts.items(), key=lambda kv: kv[0]))),
        ])
    return {"findings": findings, "metrics": metrics}


def _unstamped(val):
    # type: (object) -> bool
    """A missing version and a blank one are ONE state, so every loop must agree."""
    return val is None or (isinstance(val, str) and not val.strip())


def _newest(values):
    # type: (list) -> str
    """The highest version string present, comparing numerically when it can.

    A corpus mid-migration holds ``1`` and ``2``; string ordering would call ``10``
    older than ``9``, which is the classic way a backfill list comes out backwards.
    """
    if not values:
        return ""

    def key(v):
        parts = re.findall(r"\d+", "%s" % (v,))
        return (1, [int(p) for p in parts]) if parts else (0, [])

    numeric = [v for v in values if key(v)[0]]
    if numeric:
        return sorted(numeric, key=lambda v: key(v)[1])[-1]
    return sorted(values)[-1]


def coverage_report(docs, vocab):
    # type: (object, object) -> dict
    """Field presence across the corpus — conditional by design, or quietly broken?

    A field on 3% of documents is one of those two things and the difference matters,
    but only the shape of the whole corpus can raise the question. Reported for every
    field; warned about only for the model-written ones, because an authored-only
    field being rare is a fact about the organisation rather than about the pipeline.
    """
    rows, findings = _rows(docs)
    n = len(rows)
    min_docs = int(_threshold(vocab, "corpus_min_docs", 5))
    thin_at = float(_threshold(vocab, "coverage_thin", 0.25))

    def populated(meta, name):
        val = meta.get(name)
        if val is None:
            return False
        if isinstance(val, (list, dict, str)) and not val:
            return False                          # empty is absent, not populated
        return True

    present = OrderedDict()
    for f in FIELDS:
        count = 0
        for _path, meta in rows:
            # A registry field whose values are all still PROPOSALS is populated. It
            # is the normal state of a young corpus — every term starts in
            # `<field>_proposed` and only promotion moves it — so reading the field
            # alone reports the extractor as dead precisely while it is working.
            if populated(meta, f.name) or (
                    f.vocab and _has(vocab, f.vocab)
                    and vocab.governance(f.vocab) == "registry"
                    and populated(meta, proposed_key(f.name))):
                count += 1
        present[f.name] = count

    thin, never = [], []  # type: list, list
    for f in FIELDS:
        count = present[f.name]
        if f.tier != 2 or f.authored_only or n < min_docs:
            continue
        if count == 0:
            never.append(f.name)
        elif count < n * thin_at:
            thin.append("%s: %d/%d" % (f.name, count, n))
    if never:
        # The strongest extractor failure there is, and the one the thin test cannot
        # see: a field on NO document has no rate to fall under a threshold. Silence
        # here would make a dead extractor render exactly like a clean corpus, and
        # the mirror gate (`vocab-unused`) defers to this one — so without this
        # branch both stay quiet and the field vanishes from the report entirely.
        findings.append(_f(
            "coverage-absent", INFO, "coverage",
            "%d model-written field(s) are populated on no document at all — either "
            "the corpus does not use them, or nothing is emitting them"
            % len(never), sorted(never)))
    if thin:
        findings.append(_f(
            "coverage-thin", WARN, "coverage",
            "%d model-written field(s) are populated on under %.0f%% of documents — "
            "conditional by design, or is the extractor missing them?"
            % (len(thin), thin_at * 100), sorted(thin)))
    if n and n < min_docs:
        findings.append(_f(
            "coverage-sample", INFO, "coverage",
            "%d document(s) is below the %d-document floor, so coverage and orphan "
            "rates are reported but not graded" % (n, min_docs)))
    return {"findings": findings,
            "metrics": OrderedDict([("documents", n),
                                    ("present", present)])}


def vocabulary_hygiene(vocab):
    # type: (object) -> dict
    """Lint the TERM LIST itself, before any document is written against it.

    Synonym pollution in a closed vocabulary cannot be caught by grading documents:
    every value is legal by construction, so a list containing both ``mitigates`` and
    ``does_not_mitigate`` produces a perfectly clean corpus split across two edge
    types. This runs on an empty corpus, which is exactly when it is most useful.

    A same-stem collision is an ERROR — one concept must not have two terms. Mere
    similarity is INFO, because a curated list is a decision somebody already made
    and ``annual``/``biannual`` is a real distinction, not a typo.
    """
    findings = []  # type: list
    if vocab is None:
        return {"findings": findings, "metrics": OrderedDict()}
    threshold = float(_threshold(vocab, "synonym_similarity", 0.78))
    similar_ok = _parse_similar_ok(_threshold(vocab, "similar_ok", []) or [])
    metrics = OrderedDict()
    for name, node in vocab.fields():
        if node.get("governance") != "closed":
            continue
        values = [v for v in vocab.values(name) if isinstance(v, str)]
        stems = OrderedDict()
        for v in values:
            stems.setdefault(_stem(v), []).append(v)
        collisions = 0
        for stem, members in stems.items():
            if len(members) > 1 and not _all_accepted(similar_ok, name, members):
                collisions += 1
                findings.append(_f(
                    "vocab-synonym", ERROR, "%s/%s" % (name, stem),
                    "the closed vocabulary itself lists %d terms that reduce to one "
                    "stem — a document using either is legal, so the split is "
                    "invisible in every document" % len(members), members))
        # A curated term list is small by construction, so it is never scoped and
        # never truncated — both are corpus-scale concessions and neither applies to
        # a file somebody hand-maintains.
        pairs, _s, _d = _similar_pairs([norm_key(v) for v in values], threshold,
                                       similar_ok, name)
        by_key = OrderedDict((norm_key(v), v) for v in values)
        for a, b, ratio in pairs:
            findings.append(_f(
                "vocab-similar", INFO, "%s/%s~%s" % (name, a, b),
                "%r and %r are %.0f%% similar — if that is deliberate, record it as "
                "`lint.similar_ok: [%s]` to stop asking"
                % (by_key.get(a, a), by_key.get(b, b), ratio * 100,
                   "%s:%s|%s" % (name, by_key.get(a, a), by_key.get(b, b)))))
        metrics[name] = OrderedDict([("terms", len(values)),
                                     ("collisions", collisions),
                                     ("similar_pairs", len(pairs))])
    return {"findings": findings, "metrics": metrics}


def _binding_fields():
    # type: () -> dict
    """vocabulary name -> the metadata fields that can put a value in it.

    Resolved through the schema rather than hardcoded, and covering all three ways a
    field binds one: directly (``type`` -> ``document_types``), through a record
    sub-key (``relations.mode`` -> ``failure_modes``) and through a group role
    (``entities`` -> ``entity_types``).
    """
    out = OrderedDict()

    def bind(vname, fname):
        if not vname:
            return
        names = out.setdefault(vname, [])
        if fname not in names:
            names.append(fname)

    for f in FIELDS:
        bind(f.vocab, f.name)
        for _sub, vname in record_vocab(f.name).items():
            bind(vname, f.name)
        for _role, vname in group_vocab(f.name).items():
            bind(vname, f.name)
    return out


def vocabulary_usage(docs, vocab):
    # type: (object, object) -> dict
    """Which governed terms the corpus never draws on.

    The mirror of the registry singleton rate, and the honest replacement for a
    corpus-wide cardinality gate on a closed field (see the module note). A term no
    document has ever used is either premature — the list was designed before the
    corpus existed — or dead, and a model asked to choose from a fifteen-value enum
    where four values are real will keep reaching for the other eleven.
    """
    rows, findings = _rows(docs)
    if vocab is None:
        return {"findings": findings, "metrics": OrderedDict()}
    min_docs = int(_threshold(vocab, "corpus_min_docs", 5))
    used = OrderedDict()      # vocabulary name -> {value: set(documents)}

    def bump(vname, value, path):
        if not isinstance(value, str) or not value.strip():
            return
        canonical, _q = vocab.normalize(vname, value)
        used.setdefault(vname, OrderedDict()).setdefault(canonical, set()).add(path)

    for path, meta in rows:
        for f in FIELDS:
            if f.vocab and _has(vocab, f.vocab):
                if f.kind == "list":
                    for v in _str_list(meta, f.name):
                        bump(f.vocab, v, path)
                elif f.kind == "scalar":
                    bump(f.vocab, meta.get(f.name), path)
            for sub, vname in record_vocab(f.name).items():
                if not _has(vocab, vname):
                    continue
                for rec in _records(meta, f.name):
                    bump(vname, rec.get(sub), path)
        # Resolved through the schema rather than hardcoded: the vocabulary a group
        # field binds to is _schema's to decide, and a second copy of that mapping
        # here would drift the moment either side changed.
        ent_vocab = group_vocab("entities").get("member_type", "")
        if ent_vocab and _has(vocab, ent_vocab):
            for _name, etype in _entity_mentions(meta, vocab):
                bump(ent_vocab, etype, path)
        link_vocab = group_vocab("links").get("group_name", "")
        links = meta.get("links")
        if isinstance(links, dict) and link_vocab and _has(vocab, link_vocab):
            for cat in links.keys():
                bump(link_vocab, cat, path)

    # Which BINDING SITES the corpus populates. `coverage-absent` grades FIELD
    # presence, so the deferral below is only sound for a vocabulary whose binding
    # field is itself empty — see the comment at the deferral.
    binds = _binding_fields()
    populated = Counter()
    for _path, meta in rows:
        for f in FIELDS:
            val = meta.get(f.name)
            if val is None or (isinstance(val, (list, dict, str)) and not val):
                continue
            populated[f.name] += 1

    metrics = OrderedDict()
    for name, node in vocab.fields():
        if node.get("governance") != "closed":
            continue
        values = [v for v in vocab.values(name) if isinstance(v, str)]
        counts = used.get(name) or {}
        unused = sorted(v for v in values if not counts.get(v))
        singles = sorted(v for v in values if len(counts.get(v) or ()) == 1)
        metrics[name] = OrderedDict([
            ("terms", len(values)),
            ("used", len(values) - len(unused)),
            ("unused", unused),
            ("single_document", singles),
        ])
        if not unused or len(rows) < min_docs:
            continue
        if len(unused) < len(values):
            findings.append(_f(
                "vocab-unused", INFO, name,
                "%d/%d terms are used by no document — a model choosing from this "
                "enum is offered values the corpus has never needed"
                % (len(unused), len(values)), unused))
            continue
        # ALL of them dead. The deferral to `coverage-absent` is sound only when that
        # gate will actually name something: it grades tier-2, non-authored-only
        # FIELD presence, so it says nothing about a vocabulary bound through an
        # OPTIONAL record sub-key of a field that IS populated. `failure_modes` is
        # exactly that (`relations.mode` / `risks.mode`), and the result was a
        # monotonicity inversion — 3 of 4 dead terms reported, 4 of 4 silent, the
        # worse corpus being the quieter one. That is the inversion the zero-branch
        # in `coverage_report` exists to prevent, arriving through the other door.
        #
        # A vocabulary bound ONLY to `authored_only` fields stays silent on purpose:
        # `coverage_report` declines to warn about those ("an authored-only field
        # being rare is a fact about the organisation rather than about the
        # pipeline"), and contradicting that here would fire on every fresh corpus
        # for `document_status`, `confidentiality` and `review_cadence`.
        # EVERY binding field, not any: the deferral is only the whole story when
        # `coverage-absent` names them all. One populated binding field with not a
        # single term drawn from the vocabulary is the case nothing else can see, and
        # "risks is populated on no document" tells an operator nothing about a
        # qualifier that also hangs off `relations`.
        graded = [n for n in binds.get(name) or []
                  if field(n).tier == 2 and not field(n).authored_only]
        live = [n for n in graded if populated[n]]
        if not graded or not live:
            continue
        findings.append(_f(
            "vocab-dead", INFO, name,
            "0/%d terms are used by any document, yet %s populated — "
            "`coverage-absent` grades FIELD presence, so it cannot name this one and "
            "the whole vocabulary was invisible in both gates"
            % (len(values),
               "the field(s) that bind it are" if len(live) > 1
               else "the field that binds it is"),
            ["%s (populated on %d document(s))" % (n, populated[n]) for n in live]
            + ["unused: %s" % ", ".join(unused)]))
    return {"findings": findings, "metrics": metrics}


# --------------------------------------------------------------- entry point

def _has(vocab, name):
    # type: (object, str) -> bool
    try:
        return bool(vocab is not None and vocab.has(name))
    except Exception:
        return False


def _threshold(vocab, name, default):
    # type: (object, str, object) -> object
    if vocab is None:
        return default
    try:
        val = vocab.threshold(name, default)
    except Exception:
        return default
    return default if val is None else val


# Checks a TRUNCATED corpus cannot answer, each with what it stops answering. The
# split is not stylistic: a subset can only ever MISS a collision, never invent one,
# so identity/synonym/entity stay honest on a partial walk. The four below invert
# under truncation — a `see_also` whose target was excluded reads as dead, the newest
# schema version may be sitting in a document that was skipped, a coverage rate is a
# rate over the wrong denominator, and a term used only by an excluded document reads
# as dead. Each would report a defect the corpus does not have, which is worse than
# reporting nothing.
#
# The descriptions are not decoration. "graph, skew, coverage, vocab_usage were
# SKIPPED" is a line an operator reads past; naming what each one stops answering is
# what makes the cost of a narrowed run legible.
_NEEDS_WHOLE_CORPUS = OrderedDict([
    ("graph", "see_also resolution across the corpus, orphan counts, and the "
              "relation-endpoint resolution rate"),
    ("skew", "which documents a schema_version / vocab_version bump has to "
             "backfill, and which carry no version at all"),
    ("coverage", "per-field coverage rates, the thin-field warning, and the "
                 "dead-extractor check (a field populated on no document)"),
    ("vocab_usage", "which governed terms no document ever draws on, and which "
                    "are used by exactly one"),
])


def corpus_findings(docs, vocab, partial=False, unreadable=()):
    # type: (object, object, bool, object) -> dict
    """Grade a whole corpus. ``docs`` is ``[{"path", "meta"}]`` or ``[(path, meta)]``.

    Returns ``{"findings": [CorpusFinding], "metrics": {check: {...}}, "aliases":
    {field: {variant: canonical}}, "skipped": [str], "errors": int, "warnings":
    int}``. Never raises on bad data — a malformed row is a finding, for the same
    reason ``lint_document`` never raises: the linter reports the corpus it has.

    TWO DIFFERENT KINDS OF INCOMPLETE, and conflating them is what P5.12 fixes.

    ``partial=True`` says the OPERATOR asked for a subset (``--only``/``--limit``).
    The corpus under grade is then a deliberate selection, every rate would be a rate
    over the wrong denominator, and the four checks above are skipped — named one by
    one in ``skipped`` and in the findings, so a narrowed run cannot manufacture
    findings out of its own truncation.

    ``unreadable`` is a list of documents the corpus HAS and this run could not
    parse. That is a defect in those documents, not a licence to stop grading the
    other 999: every check still runs, each unreadable document gets its own ERROR,
    and one further finding names — individually — each check whose denominator now
    excludes them, with the count. Treating this as "partial" turned one
    tab-indented front matter into a clean-looking all-clear over a corpus nobody
    checked.

    ``vocabulary_hygiene`` runs even when ``docs`` is empty, and even when partial. A
    term list is wrong or right on its own, and the most valuable moment to hear
    about a polluted one is before it has been written into four hundred documents.
    """
    # Parsed ONCE, then handed to every gate as `(path, meta)` pairs — a shape
    # `_rows` passes through untouched. Letting each gate parse `docs` itself made a
    # single malformed row produce seven identical findings and inflate the error
    # count sevenfold, so one bad document read as seven broken ones.
    rows, findings = _rows(docs)
    metrics = OrderedDict()
    aliases = OrderedDict()
    skipped = []  # type: list

    for name, thunk in (("vocabulary", lambda: vocabulary_hygiene(vocab)),
                        ("identity", lambda: identity_report(rows)),
                        ("synonyms", lambda: synonym_report(rows, vocab)),
                        ("entities", lambda: entity_report(rows, vocab)),
                        ("graph", lambda: graph_report(rows, vocab)),
                        # The authoritative versions, FORWARDED. `corpus_findings`
                        # already holds the vocabulary and `SCHEMA_VERSION` is one
                        # import away; calling `skew_report(rows)` bare is what let a
                        # uniformly stale corpus grade itself against itself.
                        ("skew", lambda: skew_report(
                            rows, schema_current="%s" % (SCHEMA_VERSION,),
                            vocab_current=("%s" % (getattr(vocab, "version", ""),)
                                           if vocab is not None else ""))),
                        ("coverage", lambda: coverage_report(rows, vocab)),
                        ("vocab_usage", lambda: vocabulary_usage(rows, vocab))):
        if partial and name in _NEEDS_WHOLE_CORPUS:
            skipped.append(name)
            metrics[name] = OrderedDict([("skipped", "partial corpus")])
            continue
        result = thunk()
        findings.extend(result["findings"])
        metrics[name] = result.get("metrics") or OrderedDict()
        for fname, table in (result.get("aliases") or {}).items():
            aliases.setdefault(fname, OrderedDict()).update(table)
    # One finding PER SKIPPED CHECK, not one summarising line. A reader has to be
    # able to see which question went unanswered without knowing what the words
    # "graph" and "skew" cover, and a single joined list is exactly the shape that
    # reads as a formality.
    for name in skipped:
        findings.append(_f(
            "corpus-check-skipped", INFO, name,
            "SKIPPED — the operator narrowed the walk, so this check would report "
            "defects created by the truncation rather than by the corpus. Not "
            "answered by this run: %s" % _NEEDS_WHOLE_CORPUS[name]))

    # An unreadable document is a defect in THAT document. It gets its own error,
    # by name — never a corpus-wide amnesty.
    bad = ["%s" % (u,) for u in (unreadable or [])]
    for item in bad:
        findings.append(_f(
            "corpus-unreadable", ERROR, item.split(":", 1)[0],
            "could not be read, so it contributes to NO corpus check — it cannot "
            "collide, cannot be a see_also target, and is absent from every rate "
            "below: %s" % item))
    if bad and not skipped:
        # The checks still RAN. What changed is the denominator, so say which ones
        # by name and by how much, rather than switching them off.
        findings.append(_f(
            "corpus-denominator", WARN, "corpus",
            "%d of %d document(s) could not be read; every check below still ran, "
            "but these %d are missing from each of them"
            % (len(bad), len(rows) + len(bad), len(bad)),
            ["%s: %s" % (name, _NEEDS_WHOLE_CORPUS[name])
             for name in _NEEDS_WHOLE_CORPUS]
            + ["see_also resolution: a pointer into an unreadable document is "
               "reported unresolved, with that caveat named in the finding"]))

    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.code, f.where))
    return {
        "findings": findings,
        "metrics": metrics,
        "aliases": aliases,
        "skipped": skipped,
        "errors": sum(1 for f in findings if f.severity == ERROR),
        "warnings": sum(1 for f in findings if f.severity == WARN),
    }


def alias_suggestions(docs, vocab):
    # type: (object, object) -> dict
    """``{field: {variant: canonical}}`` — paste-ready entries for ``vocab.yaml``.

    Emitted only for identity collisions, never for similarity: a suggestion the tool
    is not certain about would get pasted in anyway, and an alias is a permanent
    claim that two strings mean the same thing.
    """
    return synonym_report(docs, vocab).get("aliases") or OrderedDict()


# --------------------------------------------------------------- promotion

def _flow_open(text):
    # type: (str) -> int
    """Net unclosed ``[`` in ``text``, ignoring brackets inside quotes."""
    depth = 0
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return depth


def _values_span(lines, start, end):
    # type: (list, int, int) -> tuple
    """``(first, last_exclusive)`` of the ``values:`` entry inside a field block.

    ``(-1, -1)`` when the field declares none. A flow sequence may wrap over several
    lines and a block sequence trails its items underneath, so the span is found by
    following the construct rather than by assuming one line.
    """
    for i in range(start, end):
        line = lines[i]
        if not line.startswith("  values:"):
            continue
        rest = line.split(":", 1)[1].strip()
        j = i + 1
        if rest.startswith("["):
            depth = _flow_open(rest)
            while depth > 0 and j < end:
                depth += _flow_open(lines[j])
                j += 1
        elif not rest:
            while j < end and (not lines[j].strip()
                               or lines[j].startswith("    ")):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1                       # do not swallow the blank separator
        return (i, j)
    return (-1, -1)


def _field_span(lines, name):
    # type: (list, str) -> tuple
    """``(first, last_exclusive)`` of a top-level ``name:`` block. ``(-1, -1)`` if absent."""
    head = "%s:" % name
    for i, line in enumerate(lines):
        if line.rstrip() != head and not line.startswith(head + " "):
            continue                         # a nested `  name:` cannot match
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() and not nxt[:1].isspace() and not nxt.lstrip().startswith("#"):
                break
            j += 1
        return (i, j)
    return (-1, -1)


def apply_promotions(vocab_text, promotions):
    # type: (str, dict) -> tuple
    """``(new_text, applied)`` — write promoted terms into a vocabulary file's TEXT.

    ``promotions`` is ``{vocabulary_name: [term]}``; ``applied`` reports what actually
    changed, which is what makes the operation safe to run twice: a term already in
    ``values`` is not re-added, and when nothing changes the text is returned byte
    for byte.

    WHY A SPLICE RATHER THAN A REPARSE-AND-RERENDER. ``config/vocab.yaml`` is a
    hand-maintained file whose comments, ``rationale`` blocks and ``rules`` lists are
    the reason anyone can review a governance decision. Round-tripping the whole file
    through ``parse_block``/``render_block`` would drop every comment and reflow every
    folded scalar — a writer that destroys the document it edits is not a writer
    anybody runs twice. Only the ``values:`` entry of a promoted field is replaced.

    The replacement is RENDERED BY ``render_block`` — the same codec that will parse
    it back — so a term the vocabulary format cannot express is refused here rather
    than corrupting the file (and a term like ``no`` or ``0644`` comes back as the
    string it went in as, instead of being retyped by a YAML 1.1 reader).

    A successful promotion also bumps the file's ``version``. That is the package
    rule — ``version`` moves when the TERMS move, ``SCHEMA_VERSION`` when the field
    inventory does — and it is what makes ``skew_report`` able to name the documents
    a promotion invalidates. Nothing applied means nothing bumped.
    """
    lines = (vocab_text or "").split("\n")
    applied = OrderedDict()
    for name in sorted(promotions or {}):
        terms = [t for t in (promotions[name] or []) if isinstance(t, str) and t.strip()]
        if not terms:
            continue
        fstart, fend = _field_span(lines, name)
        if fstart < 0:
            continue                        # a vocabulary the file does not declare
        vstart, vend = _values_span(lines, fstart + 1, fend)
        current = []  # type: list
        if vstart >= 0:
            parsed = parse_block("\n".join(
                l[2:] if l.startswith("  ") else l.strip()
                for l in lines[vstart:vend]))
            current = [v for v in (parsed.get("values") or [])
                       if isinstance(v, str)]
        added = [t for t in terms if t not in current]
        if not added:
            continue                        # already governed: run two writes nothing
        merged = sorted(set(current) | set(added))
        block = render_block(OrderedDict([("values", merged)]))
        rendered = ["  " + l if l else l for l in block.rstrip("\n").split("\n")]
        if vstart >= 0:
            lines[vstart:vend] = rendered
        else:
            lines[fstart + 1:fstart + 1] = rendered
        applied[name] = sorted(added)
    if applied:
        for i, line in enumerate(lines):
            if not line.startswith("version:"):
                continue
            raw = line.split(":", 1)[1].strip()
            try:
                lines[i] = "version: %d" % (int(raw) + 1)
            except ValueError:
                pass                        # a hand-written version we cannot count
            break
    return ("\n".join(lines), applied)
