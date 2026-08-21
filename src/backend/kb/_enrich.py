"""
title: Metadata enrichment policy — what a model may propose and what is accepted (private)
layer: backend
public_api: no
summary: Build the enum-constrained request, validate a model's answer against the vocabulary, and merge without ever overwriting an authored value.
"""
# 3.6-compatible. Stdlib only. PURE — no disk, no network, no model. The HTTP call
# and the file writes live in scripts/enrich_metadata.py; everything about WHAT is
# allowed lives here so it can be tested without a server.
#
# THREE RULES, and they are the whole design:
#
#   1. CONSTRAIN AT GENERATION, NOT ONLY AT VALIDATION. The request carries the
#      allowed terms so the model SELECTS instead of writing. Free-generation into
#      a string field is the default failure mode and the fastest route to
#      mitigate / mitigates / helps_mitigate as three edges.
#   2. UNKNOWN IS A LEGAL ANSWER. Without an escape the model is forced to guess,
#      and a confident wrong label is worse than an absent one. An omitted field
#      stays PENDING and visibly backfillable; it never silently reads as done.
#   3. AUTHORED ALWAYS WINS. A generated value fills a gap. It never overwrites
#      what a person wrote, and it may never touch an authored-only field at all.
import hashlib
from collections import OrderedDict

from ._schema import (FIELDS, PROVENANCE_KEY, SCHEMA_VERSION, field, field_names,
                      group_vocab, model_writable, proposed_key, record_vocab,
                      SOURCE_AUTHORED, SOURCE_DERIVED, SOURCE_EXTRACTED,
                      SOURCE_GENERATED)

__all__ = ["request_spec", "accept_model_meta", "is_authored", "meta_coverage",
           "order_meta", "set_provenance", "revalidate_generated", "value_sha",
           "UNKNOWN"]

# The escape hatch. A model that cannot tell must be able to say so.
UNKNOWN = "unknown"


def _stable_repr(value):
    # type: (object) -> str
    """A deterministic text form of a value, for fingerprinting.

    Hand-rolled rather than json.dumps because this package is stdlib-only by
    rule and src/ deliberately imports no serializer; mappings are walked in key
    order so the fingerprint depends on content, not on dict ordering.
    """
    if isinstance(value, dict):
        return "{%s}" % ",".join(
            "%s:%s" % (k, _stable_repr(value[k])) for k in sorted(value, key=str))
    if isinstance(value, (list, tuple)):
        return "[%s]" % ",".join(_stable_repr(v) for v in value)
    return "%r" % (value,)


def value_sha(value):
    # type: (object) -> str
    """Short fingerprint of a stored value — how a later run recognises an edit."""
    return hashlib.sha256(_stable_repr(value).encode("utf-8")).hexdigest()[:12]


# Only these three sources mark a value the pipeline produced and may replace.
_MACHINE_SOURCES = (SOURCE_GENERATED, SOURCE_DERIVED, SOURCE_EXTRACTED)


def is_authored(entry, current):
    # type: (dict, object) -> bool
    """Must this existing value be protected from a generated one?

    Fail SAFE — anything that is not positively identifiable as machine-written
    counts as authored. A missing entry, a non-mapping entry, an entry with no
    ``source``, and an unrecognised source all mean "a person may have written
    this", and the cost of keeping a human value is nothing while the cost of
    overwriting one is trust.

    The load-bearing case is the one tier 2 is DEFINED by: a human CORRECTS a
    value a previous run generated. The provenance entry still says
    ``generated``, so source alone cannot tell the difference — which is why a
    generated value is fingerprinted when it is written. A current value whose
    fingerprint no longer matches has been edited by somebody, and is authored.
    """
    if _empty(current):
        return False                       # nothing to protect
    if not isinstance(entry, dict):
        return True                        # absent or degenerate -> assume authored
    source = entry.get("source")
    if source not in _MACHINE_SOURCES:
        return True
    if source == SOURCE_GENERATED:
        recorded = entry.get("value_sha")
        if recorded and recorded != value_sha(current):
            return True                    # edited since we wrote it
    return False


def request_spec(vocab, wanted=None):
    # type: (object, list) -> OrderedDict
    """The machine-readable field spec a model is asked to fill.

    One entry per model-writable field: its kind, and — where a vocabulary governs
    it — the exact terms it may choose from. ``closed`` fields list every allowed
    value; ``registry`` fields list the current terms and say that a new one is
    allowed but will be recorded as a proposal rather than used directly.

    This doubles as the JSON-schema source for a server that supports constrained
    decoding, and as the prose enum list for one that does not.
    """
    names = [f.name for f in FIELDS if model_writable(f.name)]
    if wanted is not None:
        want = set(wanted)
        names = [n for n in names if n in want]
    spec = OrderedDict()
    for name in names:
        f = field(name)
        entry = OrderedDict()
        entry["kind"] = f.kind
        entry["tier"] = f.tier
        if f.note:
            entry["note"] = f.note
        # The escape hatch is declared on EVERY field. It used to ride along inside
        # `values`, which meant an empty registry — the four fields most exposed to
        # free generation — offered no way to decline at all.
        entry["unknown_allowed"] = True
        if f.vocab and vocab.has(f.vocab):
            entry["governance"] = vocab.governance(f.vocab)
            values = vocab.values(f.vocab)
            if values:
                entry["values"] = list(values) + [UNKNOWN]
            if vocab.governance(f.vocab) == "registry":
                entry["new_values_allowed"] = True
                entry["new_values_note"] = (
                    "a term not listed is recorded as a proposal, not used directly")
        sub = record_vocab(name)
        if sub:
            entry["record_fields"] = OrderedDict(
                (k, list(vocab.values(v)) + [UNKNOWN]) for k, v in sub.items()
                if vocab.has(v))
        spec[name] = entry
    return spec


def _empty(value):
    # type: (object) -> bool
    return value is None or value == "" or value == [] or value == {}


def _kind_ok(kind, value):
    # type: (str, object) -> bool
    if kind == "list":
        return isinstance(value, list)
    if kind in ("map", "groups"):
        return isinstance(value, dict)
    if kind == "records":
        return isinstance(value, list) and all(isinstance(r, dict) for r in value)
    return not isinstance(value, (list, dict))


def _declined(value):
    # type: (object) -> bool
    """The model explicitly said it could not tell."""
    return isinstance(value, str) and value.strip().lower() == UNKNOWN


def _validate_scalar(vocab, vname, value):
    # type: (object, str, object) -> tuple
    """``(status, canonical, qualifiers)`` — status is ok/proposed/rejected/declined.

    ``declined`` is kept distinct from ``rejected`` on purpose: the point of
    recording reasons verbatim is to see which term a model keeps trying to
    invent, and legitimate "I don't know" answers would dilute that signal.
    """
    if _declined(value):
        return ("declined", None, {})
    canonical, quals = vocab.normalize(vname, value)
    regime = vocab.governance(vname)
    if regime == "closed":
        return ("ok", canonical, quals) if canonical in vocab.values(vname) \
            else ("rejected", canonical, quals)
    # Registry: a term is usable only once it is IN the registry. On a fresh corpus
    # that means everything starts as a proposal and the promotion rule (>= N
    # documents) is what admits terms — which is the whole point. Accepting unknown
    # terms whenever the registry happens to be empty would quietly bootstrap the
    # vocabulary from document one, guessing the corpus shape instead of measuring it.
    if canonical not in vocab.values(vname):
        return ("proposed", canonical, quals)
    return ("ok", canonical, quals)


_REASON = {"rejected": "not-in-vocabulary", "declined": "declined-unknown"}


def _accept_groups(name, groups, vocab, rejected):
    # type: (str, dict, object, list) -> OrderedDict
    """Validate a grouped field (``entities``, ``links``) on ACCEPT, not later.

    The linter re-checks these downstream, but by then the block has already been
    written and stamped ``generated``. A closed vocabulary that is only enforced
    after the value is stored is not a closed vocabulary.
    """
    gv = group_vocab(name)
    out = OrderedDict()
    for gname, members in (groups or {}).items():
        if gv.get("group_name") and vocab.has(gv["group_name"]):
            status, canonical, _q = _validate_scalar(vocab, gv["group_name"], gname)
            if status not in ("ok", "proposed"):
                rejected.append(("%s.%s" % (name, gname), gname, _REASON[status]))
                continue
            gname = canonical
        if gv.get("member_type") and isinstance(members, dict):
            # A group written as a MAPPING. Values that are themselves mappings are
            # entities in disguise and must be checked; a mapping of scalars is the
            # declared scalar-group shape (`identifiers: {...}`) and passes through.
            kept_map = OrderedDict()
            for mname, member in members.items():
                if isinstance(member, dict) and member.get("type"):
                    status, canonical, _q = _validate_scalar(
                        vocab, gv["member_type"], member["type"])
                    if status != "ok":
                        rejected.append(("%s.%s.%s.type" % (name, gname, mname),
                                         member["type"],
                                         _REASON.get(status, "not-in-vocabulary")))
                        continue
                    member = OrderedDict(member)
                    member["type"] = canonical
                kept_map[mname] = member
            if kept_map:
                out[gname] = kept_map
            continue
        if gv.get("member_type") and isinstance(members, list):
            implied = vocab.group_type(gname)
            keep = []
            for member in members:
                if not isinstance(member, dict):
                    rejected.append(("%s.%s" % (name, gname), member,
                                     "wrong-kind-expected-mapping"))
                    continue
                etype = member.get("type") or implied
                if etype:
                    status, canonical, _q = _validate_scalar(
                        vocab, gv["member_type"], etype)
                    if status != "ok":
                        rejected.append(("%s.%s.type" % (name, gname), etype,
                                         _REASON.get(status, "not-in-vocabulary")))
                        continue
                    if member.get("type"):
                        member = OrderedDict(member)
                        member["type"] = canonical
                keep.append(member)
            if keep:
                out[gname] = keep
            continue
        out[gname] = members
    return out


def accept_model_meta(proposed, vocab, existing=None, model="", prompt_sha=""):
    # type: (dict, object, dict, str, str) -> dict
    """Decide what a model's answer is allowed to contribute.

    Returns ``{"accepted": {...}, "proposals": {field: [value]}, "rejected":
    [(field, value, reason)], "provenance": {field: {...}}}``. Nothing here mutates
    ``existing`` — the caller merges, so a dry run costs nothing.

    Rejection reasons are kept verbatim rather than counted, because the useful
    question after a run is never "how many were wrong" but "which term did it keep
    trying to invent".
    """
    existing = existing or {}
    prov_existing = existing.get(PROVENANCE_KEY) or {}
    accepted = OrderedDict()
    proposals = OrderedDict()
    rejected = []  # type: list
    provenance = OrderedDict()

    for name, value in (proposed or {}).items():
        if name not in field_names():
            rejected.append((name, value, "not-in-schema"))
            continue
        if not model_writable(name):
            # Covers both tier-0/1 fields and the authored-only boundary fields.
            reason = ("authored-only" if field(name).authored_only
                      else "not-model-writable")
            rejected.append((name, value, reason))
            continue
        if _empty(value):
            continue                                    # silence is PENDING, not a value
        if _declined(value):
            # Rule 2 applies to EVERY field, not only the vocabulary-governed ones.
            # Free-text fields are where a model is likeliest to write the word
            # "unknown" as prose, and storing it would count as coverage.
            rejected.append((name, value, "declined-unknown"))
            continue
        f = field(name)
        if not _kind_ok(f.kind, value):
            rejected.append((name, value, "wrong-kind-expected-%s" % f.kind))
            continue
        if is_authored(prov_existing.get(name), existing.get(name)):
            rejected.append((name, value, "kept-authored"))
            continue

        if f.vocab and vocab.has(f.vocab) and f.kind in ("scalar", "list"):
            if f.kind == "scalar":
                status, canonical, _q = _validate_scalar(vocab, f.vocab, value)
                if status == "ok":
                    accepted[name] = canonical
                elif status == "proposed":
                    proposals.setdefault(name, []).append(canonical)
                else:
                    rejected.append((name, value, _REASON[status]))
                    continue
            else:
                keep = []
                for v in value:
                    status, canonical, _q = _validate_scalar(vocab, f.vocab, v)
                    if status == "ok":
                        keep.append(canonical)
                    elif status == "proposed":
                        proposals.setdefault(name, []).append(canonical)
                    else:
                        rejected.append((name, v, _REASON[status]))
                if keep:
                    accepted[name] = keep
        elif f.kind == "groups":
            kept = _accept_groups(name, value, vocab, rejected)
            if kept:
                accepted[name] = kept
        elif f.kind == "records":
            kept = []
            rv = record_vocab(name)
            for rec in value:
                bad = False
                new = OrderedDict(rec)
                for sub, vname in rv.items():
                    if sub not in new or not vocab.has(vname):
                        continue
                    status, canonical, quals = _validate_scalar(vocab, vname,
                                                                new[sub])
                    if status == "ok":
                        new[sub] = canonical
                        # The MADR collapse must hold on the WRITE path as well as
                        # in the report: `assumed` and `provisional` both become
                        # `accepted`, and without their qualifiers the two become
                        # indistinguishable the moment they are stored.
                        for qk, qv in quals.items():
                            new.setdefault(qk, qv)
                    else:
                        rejected.append(("%s.%s" % (name, sub), new[sub],
                                         _REASON[status]))
                        bad = True
                        break
                if not bad:
                    kept.append(new)
            if kept:
                accepted[name] = kept
        else:
            accepted[name] = value

        if name in accepted:
            provenance[name] = _prov(SOURCE_GENERATED, field(name).tier,
                                     model, prompt_sha, accepted[name])

    return {"accepted": accepted, "proposals": proposals, "rejected": rejected,
            "provenance": provenance}


def order_meta(meta):
    # type: (dict) -> OrderedDict
    """The metadata block in CANONICAL order: schema order, then extras, provenance last.

    Merging a prior block with a fresh one produces whatever key order the merge
    happened to take, so two runs that agree on every value can still write
    different bytes. Ordering by the schema makes the block a function of its
    CONTENT alone — which is what "safe to run twice" has to mean for a file under
    version control. Each ``<field>_proposed`` list sits directly after its field so
    the pair reads together.
    """
    meta = meta or {}
    out = OrderedDict()
    placed = set()
    for name in field_names():
        for key in (name, proposed_key(name)):
            if key in meta:
                out[key] = meta[key]
                placed.add(key)
    for key in sorted(meta):
        if key not in placed and key != PROVENANCE_KEY:
            out[key] = meta[key]
    prov = meta.get(PROVENANCE_KEY)
    if prov:
        ordered = OrderedDict()
        for name in field_names():
            if name in prov:
                ordered[name] = prov[name]
        for name in sorted(prov):
            if name not in ordered:
                ordered[name] = prov[name]
        out[PROVENANCE_KEY] = ordered
    return out


def revalidate_generated(meta, vocab):
    # type: (dict, object) -> tuple
    """Re-check previously GENERATED values against the current vocabulary.

    Returns ``(meta, moved)`` where ``moved`` is ``[(field, value, action)]``.

    This is what makes a vocabulary bump mean something. Enrichment carries prior
    answers forward so a re-run costs nothing, but carrying forward unconditionally
    would let a term that was legal under v1 survive forever under v2 — the stale
    classification the version number was supposed to invalidate.

    Only ``generated`` fields are touched. A value a person wrote is left exactly as
    written even when it is now off-vocabulary: demoting a human's decision behind
    their back is not this stage's call, and the linter reports it either way.

    "A person wrote it" is decided by ``is_authored``, NOT by the source label alone.
    The case tier 2 is defined by — a human CORRECTS a generated value — leaves the
    label saying ``generated``, so gating on the label lets a vocabulary bump delete
    the correction. That is the most expensive edit in the corpus to lose.

    Every mutation here RE-FINGERPRINTS the value it changed. Leaving the old
    ``value_sha`` in place makes the very next run see a value whose fingerprint does
    not match, conclude a person edited it, and freeze it as authored forever.
    """
    prov = meta.get(PROVENANCE_KEY) or {}
    moved = []  # type: list

    def refingerprint(name):
        """Keep provenance honest about a value this function just rewrote."""
        rec = prov.get(name)
        if isinstance(rec, dict) and "value_sha" in rec:
            rec["value_sha"] = value_sha(meta.get(name))

    for name in list(meta.keys()):
        if name == PROVENANCE_KEY or name not in field_names():
            continue
        if (prov.get(name) or {}).get("source") != SOURCE_GENERATED:
            continue
        if is_authored(prov.get(name), meta.get(name)):
            continue          # stamped generated, but edited since — a human's work
        f = field(name)
        value = meta.get(name)
        if _empty(value):
            continue
        if f.kind == "records" and record_vocab(name) and isinstance(value, list):
            # relations[].p, decisions[].status, risks[].impact/mode — four of the
            # five carriers of the closed vocabularies most likely to change. Left
            # unrevalidated, a vocabulary bump would silently leave them stale.
            kept = []
            for rec in value:
                if not isinstance(rec, dict):
                    continue
                bad = False
                new = OrderedDict(rec)
                for sub, vname in record_vocab(name).items():
                    if sub not in new or not vocab.has(vname):
                        continue
                    status, canonical, quals = _validate_scalar(vocab, vname, new[sub])
                    if status == "ok":
                        if canonical != new[sub]:
                            moved.append(("%s[].%s" % (name, sub), new[sub],
                                          "canonicalised"))
                        new[sub] = canonical
                        for qk, qv in quals.items():
                            new.setdefault(qk, qv)
                    else:
                        moved.append(("%s[].%s" % (name, sub), new[sub], status))
                        bad = True
                        break
                if not bad:
                    kept.append(new)
            if kept:
                meta[name] = kept
                refingerprint(name)
            else:
                del meta[name]
                prov.pop(name, None)
            continue
        if f.kind == "groups" and isinstance(value, dict):
            dropped = []  # type: list
            kept_groups = _accept_groups(name, value, vocab, dropped)
            for where, bad_value, reason in dropped:
                moved.append((where, bad_value, reason))
            if kept_groups:
                meta[name] = kept_groups
                refingerprint(name)
            else:
                del meta[name]
                prov.pop(name, None)
            continue
        if not f.vocab or not vocab.has(f.vocab) or f.kind not in ("scalar", "list"):
            continue
        if f.kind == "scalar":
            status, canonical, _q = _validate_scalar(vocab, f.vocab, value)
            if status == "ok":
                if canonical != value:
                    meta[name] = canonical
                    refingerprint(name)
                    moved.append((name, value, "canonicalised"))
            else:
                del meta[name]
                prov.pop(name, None)
                if status == "proposed":
                    slot = proposed_key(name)
                    meta[slot] = sorted(set(list(meta.get(slot) or []) + [canonical]))
                moved.append((name, value, status))
        else:
            keep, prop, drop = [], [], []
            for v in value:
                status, canonical, _q = _validate_scalar(vocab, f.vocab, v)
                if status == "ok":
                    keep.append(canonical)
                elif status == "proposed":
                    prop.append(canonical)
                else:
                    drop.append(v)
            if prop:
                slot = proposed_key(name)
                meta[slot] = sorted(set(list(meta.get(slot) or []) + prop))
                moved.extend((name, v, "proposed") for v in prop)
            for v in drop:
                moved.append((name, v, "rejected"))
            if keep:
                meta[name] = keep
                refingerprint(name)
            else:
                del meta[name]
                prov.pop(name, None)
    return (meta, moved)


def _prov(source, tier, model="", prompt_sha="", value=None):
    # type: (str, int, str, str, object) -> OrderedDict
    rec = OrderedDict()
    rec["tier"] = tier
    rec["source"] = source
    if source == SOURCE_GENERATED:
        rec["model"] = model or ""
        rec["prompt_sha"] = prompt_sha or ""
        # The fingerprint of what WE wrote. Without it a human correction to a
        # generated value is invisible to the next run, which reverts it.
        rec["value_sha"] = value_sha(value)
    return rec


def set_provenance(meta, name, source, model="", prompt_sha="", value=None):
    # type: (dict, str, str, str, str, object) -> None
    """Record how one field got its value, in place.

    PROV-O's per-field ``wasGeneratedBy``, kept as plain nested YAML. This is what
    makes ``authored wins`` enforceable later instead of being a convention nobody
    can check.
    """
    if name not in field_names():
        return
    prov = meta.setdefault(PROVENANCE_KEY, OrderedDict())
    prov[name] = _prov(source, field(name).tier, model, prompt_sha,
                       meta.get(name) if value is None else value)


def meta_coverage(meta, vocab):
    # type: (dict, object) -> dict
    """Counts for the ``doc_meta`` report gate.

    ``expected`` is every model-writable field — the denominator stays the SCHEMA,
    not whatever this run happened to attempt, so a run that skipped fields reports
    them as pending instead of quietly shrinking the target.
    """
    meta = meta or {}
    prov = meta.get(PROVENANCE_KEY) or {}
    expected = filled = authored = invalid = 0
    for f in FIELDS:
        if not model_writable(f.name):
            continue
        expected += 1
        value = meta.get(f.name)
        if _empty(value):
            continue
        ok = True
        if f.vocab and vocab.has(f.vocab) and vocab.governance(f.vocab) == "closed":
            vals = value if isinstance(value, list) else [value]
            ok = all(vocab.is_allowed(f.vocab, v) for v in vals
                     if not isinstance(v, (list, dict)))
        if not _kind_ok(f.kind, value):
            ok = False
        if ok:
            filled += 1
            if (prov.get(f.name) or {}).get("source") == SOURCE_AUTHORED:
                authored += 1
        else:
            invalid += 1
    return {"expected": expected, "filled": filled, "authored": authored,
            "invalid": invalid, "pending": max(0, expected - filled - invalid)}
