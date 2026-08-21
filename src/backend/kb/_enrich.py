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
#   4. EVIDENCE OUTRANKS A GUESS. Anything the pipeline read out of the source
#      bytes — a title from the core properties, a URL harvested from the body at
#      recall 1.0 — is a fact, and a model does not get to overwrite a fact with a
#      proposal. This is what the FLOOR functions below exist for: they fill from
#      evidence the pipeline already has, so a no-model run produces a titled,
#      summarised, linked page instead of twenty PENDING fields.
import datetime
import hashlib
import re
from collections import OrderedDict
from urllib.parse import quote

from backend.ingest import markdown_to_text

from ._derive import derive_uid, heading_anchor, slugify
from ._schema import (FIELDS, PROVENANCE_KEY, SCHEMA_VERSION, field, field_names,
                      group_required, group_vocab, model_writable, proposed_key,
                      record_required, record_vocab,
                      SOURCE_AUTHORED, SOURCE_DERIVED, SOURCE_EXTRACTED,
                      SOURCE_GENERATED)

__all__ = ["request_spec", "accept_model_meta", "is_authored", "is_protected",
           "meta_coverage", "order_meta", "set_provenance", "revalidate_generated",
           "value_sha", "abstract_floor", "harvested_links", "link_category",
           "merge_group_evidence", "next_review_due", "record_source",
           "source_url", "title_floor", "unique_id", "value_source",
           "EVIDENCE_KEY", "REVIEW_INTERVAL_DAYS", "UNKNOWN"]

# The escape hatch. A model that cannot tell must be able to say so.
UNKNOWN = "unknown"

# Where a RECORD carries its own origin. `_provenance` is per FIELD, and per-field is
# the wrong grain for a list: once harvested links and model-proposed links live in
# one `links` block, a single `source: extracted` on the field would vouch for the
# guesses too. So each record says where it came from, using the same four terms
# `_provenance.source` uses — one vocabulary for one question.
EVIDENCE_KEY = "source"

# What `ref` means, told to the model in its own words. The anchors themselves are
# per-document, so the request carries the list separately; this is the rule.
REF_SPEC = ("the section that ASSERTS this record, as `#anchor` chosen from the "
            "SECTION ANCHORS list supplied with the document. A record whose ref "
            "is missing, invented, or points at a section that does not say this "
            "is discarded — an unciteable claim is worse than no claim.")


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
    ``generated``, so source alone cannot tell the difference — which is why
    every machine-written value is fingerprinted when it is written. A current
    value whose fingerprint no longer matches has been edited by somebody, and is
    authored. (The fingerprint is checked for EVERY machine source, not only
    ``generated``: a hand-curated link a run had harvested is exactly as much a
    person's work as a hand-corrected classification, and losing it costs the same
    trust. A record written before fingerprinting simply has none, and falls back
    to the source label.)
    """
    if _empty(current):
        return False                       # nothing to protect
    if not isinstance(entry, dict):
        return True                        # absent or degenerate -> assume authored
    source = entry.get("source")
    if source not in _MACHINE_SOURCES:
        return True
    recorded = entry.get("value_sha")
    if recorded and recorded != value_sha(current):
        return True                        # edited since we wrote it
    return False


def is_protected(entry, current):
    # type: (dict, object) -> bool
    """Must this existing value survive a model's proposal?

    Two different reasons, one predicate:

      * A PERSON wrote it (``is_authored``) — rule 3, absolute.
      * The PIPELINE READ IT out of the source (``extracted``) — rule 4. A title
        the docx core properties declare, or a URL harvested from the body, is
        evidence measured at recall 1.0 and zero cost. A model that proposes a
        different one is not improving the value, it is contradicting the document.

    ``derived`` is deliberately NOT protected: a value computed by a fixed rule (a
    title inferred from the filename, an abstract cut from the lede) is a floor, and
    the whole point of a floor is that something better may land on top of it.
    """
    if is_authored(entry, current):
        return True
    if _empty(current) or not isinstance(entry, dict):
        return False
    return entry.get("source") == SOURCE_EXTRACTED


def request_spec(vocab, wanted=None):
    # type: (object, list) -> OrderedDict
    """The machine-readable field spec a model is asked to fill.

    One entry per model-writable field: its kind, and — where a vocabulary governs
    it — the exact terms it may choose from. ``closed`` fields list every allowed
    value; ``registry`` fields list the current terms and say that a new one is
    allowed but will be recorded as a proposal rather than used directly.

    It also states the SHAPE of a record, which it used not to: with only
    ``kind: records`` and a ``p`` enum to go on, ``{"p": "runs_on"}`` was a
    schema-valid relation — no subject, no object, and no way to answer "which
    section says this?". Every required sub-key is now listed, ``ref`` among them,
    so the constraint is visible at GENERATION time and not only in the rejection
    log afterwards (rule 1: constrain at generation, not only at validation).

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
        required = record_required(name) or group_required(name)
        if required:
            entry["required_keys"] = list(required)
            if "ref" in required:
                entry["ref"] = REF_SPEC
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


def _missing_required(name, rec):
    # type: (str, dict) -> list
    """Required sub-keys this record does not carry (empty values count as absent)."""
    required = record_required(name) or group_required(name)
    return [k for k in required if _empty(rec.get(k))]


def _bad_ref(rec, anchors):
    # type: (dict, set) -> str
    """``""`` when the record's ``ref`` points at a real section, else the reason.

    Skipped entirely when the caller supplies no anchors: an unverifiable pointer
    must not be reported as verified, and this package never invents the body.
    """
    if anchors is None:
        return ""
    ref = rec.get("ref")
    if not isinstance(ref, str) or not ref.startswith("#"):
        return "ref-not-a-fragment"
    return "" if ref[1:] in anchors else "ref-not-an-anchor"


def _accept_records(name, records, rejected, anchors=None):
    # type: (str, list, list, set) -> list
    """Every member of a record LIST, validated: shape, then required keys, then ref."""
    kept = []  # type: list
    for rec in records or []:
        if not isinstance(rec, dict):
            rejected.append((name, rec, "wrong-kind-expected-mapping"))
            continue
        missing = _missing_required(name, rec)
        if missing:
            rejected.append((name, rec, "missing-required-%s" % "/".join(missing)))
            continue
        bad = _bad_ref(rec, anchors)
        if bad:
            rejected.append(("%s.ref" % name, rec.get("ref"), bad))
            continue
        kept.append(rec)
    return kept


def _accept_groups(name, groups, vocab, rejected, anchors=None):
    # type: (str, dict, object, list, set) -> OrderedDict
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
        if not isinstance(members, list):
            rejected.append(("%s.%s" % (name, gname), members,
                             "wrong-kind-expected-list"))
            continue
        # AN INVENTED GROUP IS NOT A LICENCE TO SKIP THE TYPE CHECK. `group_types`
        # maps a KNOWN group to the type its members take when they omit one
        # (`hosts` -> Host). For a group nobody declared, that fallback is "" — and
        # the old code read "" as "nothing to check", so `gadgets: [{name: x}]`
        # walked in untyped and got stamped `generated`. A new group is fine; a new
        # group whose members are also untyped is a whole ungoverned namespace, and
        # the type vocabulary is the only governance a group name has.
        implied = vocab.group_type(gname) if gv.get("member_type") else ""
        keep = []
        for member in members:
            if not isinstance(member, dict):
                rejected.append(("%s.%s" % (name, gname), member,
                                 "wrong-kind-expected-mapping"))
                continue
            missing = _missing_required(name, member)
            if missing:
                rejected.append(("%s.%s" % (name, gname), member,
                                 "missing-required-%s" % "/".join(missing)))
                continue
            bad = _bad_ref(member, anchors)
            if bad:
                rejected.append(("%s.%s.ref" % (name, gname), member.get("ref"), bad))
                continue
            if gv.get("member_type"):
                etype = member.get("type") or implied
                if not etype:
                    rejected.append(("%s.%s.type" % (name, gname), gname,
                                     "untyped-member-of-unknown-group"))
                    continue
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
    return out


def accept_model_meta(proposed, vocab, existing=None, model="", prompt_sha="",
                      anchors=None):
    # type: (dict, object, dict, str, str, set) -> dict
    """Decide what a model's answer is allowed to contribute.

    Returns ``{"accepted": {...}, "proposals": {field: [value]}, "rejected":
    [(field, value, reason)], "provenance": {field: {...}}}``. Nothing here mutates
    ``existing`` — the caller merges, so a dry run costs nothing.

    ``anchors`` is the set of section anchors the document publishes. Supply it and
    every knowledge record must cite one; omit it and the ref check is SKIPPED rather
    than passed, because this package never sees the body and must not pretend to
    have verified a pointer it could not resolve.

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
        prov_entry, current = prov_existing.get(name), existing.get(name)
        if is_authored(prov_entry, current):
            rejected.append((name, value, "kept-authored"))
            continue
        if is_protected(prov_entry, current) and f.kind != "groups":
            # Evidence the pipeline READ beats a proposal, and for a scalar or a list
            # "accept" means "replace", so the only way to keep the evidence is to
            # refuse. A `groups` field is the exception: its unit is the record, so
            # the model's records join the harvested ones instead of displacing them
            # (see merge_group_evidence below).
            rejected.append((name, value, "kept-extracted"))
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
            kept = merge_group_evidence(
                current, _accept_groups(name, value, vocab, rejected, anchors))
            if kept:
                accepted[name] = kept
        elif f.kind == "records":
            kept = []
            rv = record_vocab(name)
            for rec in _accept_records(name, value, rejected, anchors):
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
            # A merged grouped field is not wholly generated, and must not say it is:
            # `value_source` reports the weakest origin actually in the block.
            src = (value_source(accepted[name], SOURCE_GENERATED)
                   if f.kind == "groups" else SOURCE_GENERATED)
            provenance[name] = _prov(src, model, prompt_sha, accepted[name])

    return {"accepted": accepted, "proposals": proposals, "rejected": rejected,
            "provenance": provenance}


# =============================================================== THE FLOOR ====
#
# Everything below fills a field from evidence the pipeline ALREADY HAS. None of it
# needs a model, a network or a second parse of the source: the title is in the core
# properties the converter already read, the lede is in the body, and the links were
# harvested into `structure.json` at recall 1.0 while the outline was being built.
#
# Before this existed, a no-model run wrote 10 fields and left 20 PENDING — including
# `title`, on documents whose docx declared one, and `links`, on documents whose
# outbound URLs were sitting in a sibling file. The pipeline threw away its verified
# edges and kept only the ones a model imagined later. That is the failure these
# functions close.

_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
# Lines that are structure rather than prose: list bullets, ordered items, table
# rows and separators, block quotes, HTML/comment sentinels, link/image-only lines.
_NOT_PROSE = re.compile(r"^ {0,3}([-*+>|]|\d+[.)]\s|<!--|<[a-zA-Z/]|!\[)")
_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]?(\s|$)")

# How long an interval each `review_cadence` term names, in days. `on_change` and
# `none` are absent ON PURPOSE: they are cadences that do not imply a date, and
# inventing one for them would put a deadline in a document nobody agreed to.
# Must cover every term the vocabulary lists — tests/unit/backend/test_field_inventory.py
# binds this table to config/vocab.yaml so the two cannot drift.
REVIEW_INTERVAL_DAYS = OrderedDict([
    ("monthly", 30), ("quarterly", 91), ("biannual", 182), ("annual", 365)])


def unique_id(source_relpath, namespace=""):
    # type: (str, str) -> str
    """The canonical document identity: unique BY CONSTRUCTION, not by checking.

    The old ``slugify(title)`` collided the moment two documents were both called
    "Overview", and a collision was an ERROR a person had to hand-fix. Uniqueness is
    designed in instead:

      * The value is a function of THE SOURCE PATH ALONE. Two documents in one corpus
        cannot share a path, so they cannot share an id.
      * ...and of nothing else. Not of the corpus (so a document gets the same id
        whether or not its neighbours are present), not of iteration order (so a
        re-run, a `--only` run and a fresh import all agree), not of a counter (which
        would fail all three at once).
      * Slugification is lossy — ``Kestrel Spec.docx`` and ``kestrel-spec.docx`` both
        reduce to ``kestrel-spec`` — so a path whose slug is not a faithful lowercase
        rendering of itself carries an 8-hex fingerprint OF THE EXACT PATH. The two
        cases can then never meet, and a corpus whose filenames are already slug-clean
        (the common case) keeps ids a human can read.

    A rename changes the path and therefore the id. That is what the AUTHORED `id`
    field is for: write one by hand and it outranks this forever.
    """
    parts = [p for p in (source_relpath or "").replace("\\", "/").split("/") if p]
    if not parts:
        return derive_uid(source_relpath, namespace)
    # THE EXTENSION IS PART OF THE IDENTITY. Dropping it before comparing was how
    # spec.docx, spec.pptx and spec.xlsx — three different documents that a real
    # corpus really does contain side by side — all became `specs/spec`, with both
    # writing scripts exiting 0. It is kept with its dot rather than slugified into
    # the stem, so the readable form of an already-clean path IS the path.
    stem, dot, ext = parts[-1].rpartition(".")
    if not dot:
        stem, ext = parts[-1], ""
    segs = [slugify(p) for p in parts[:-1]] + [slugify(stem)]
    tail = "/".join(s for s in segs if s)
    if ext:
        tail = "%s.%s" % (tail, ext.lower())
    ns = slugify(namespace) if namespace else ""
    ident = ("%s/%s" % (ns, tail)) if ns else tail
    if not ident:
        return derive_uid(source_relpath, namespace)
    # Slugification is lossy, so it is only safe to stop here when the readable
    # form IS the path, CHARACTER FOR CHARACTER. Comparing against the lowercased
    # path is not enough: on a case-sensitive filesystem `spec.docx` and
    # `Spec.docx` are two documents, and both would have rendered to `spec.docx`
    # and both been called faithful. Exact equality means two faithful paths that
    # render the same ARE the same path; everything else carries an 8-hex
    # fingerprint of the exact path, so the two cases can never meet.
    if tail == "/".join(parts):
        return ident
    digest = hashlib.sha1(("/".join(parts)).encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (ident, digest)


def _outline_nodes(outline):
    # type: (list) -> list
    """Every outline node, depth-first, parents before children."""
    out = []  # type: list
    stack = list(reversed(outline or []))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append(node)
        for kid in reversed(node.get("children") or []):
            stack.append(kid)
    return out


def _first_heading(body_md, outline=None):
    # type: (str, list) -> str
    """The document's own opening heading — from the outline when there is one."""
    for node in _outline_nodes(outline):
        title = (node.get("title") or "").strip()
        if title:
            return title
    for line in (body_md or "").split("\n"):
        m = _ATX.match(line)
        if m and m.group(2).strip():
            return m.group(2).strip()
    return ""


def _from_filename(source_relpath):
    # type: (str) -> str
    """``specs/kestrel-clock-spec.docx`` -> ``Kestrel Clock Spec``."""
    base = (source_relpath or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    words = [w for w in re.split(r"[-_\s]+", stem) if w]
    # Capitalise only what is entirely lowercase, so IEEE and v2 survive as written.
    return " ".join(w.capitalize() if w.islower() else w for w in words)


def title_floor(source_title, body_md, source_relpath="", outline=None):
    # type: (str, str, str, list) -> tuple
    """``(title, provenance_source)`` — never empty for a document with a name.

    THE TIER QUESTION, decided deliberately. `title` stays a TIER-2 field: choosing
    what a document should be called is judgement, and a model reading the whole body
    can beat any rule. What changes is that the field is never left PENDING, because
    a floor and a ceiling are different things.

    Which floor value a model may replace is decided by WHERE THE FLOOR CAME FROM,
    not by the fact that a floor exists:

      * ``source_title`` is EXTRACTED — the author of the docx typed it into the
        document's own properties. That is evidence, and `is_protected` stops a model
        overwriting it. A proposal that contradicts the document is not an
        improvement.
      * The first heading and the filename are DERIVED — both are inferences that
        this text names the document, and either can be junk (`1. Introduction`,
        `Copy of report FINAL v3`). A model that has read the body should be allowed
        to do better, so they are left overwritable.

    A person outranks all of it either way: `is_authored` sees a hand-edited value
    whatever the source label says.
    """
    text = (source_title or "").strip()
    if text:
        return (text, SOURCE_EXTRACTED)
    head = _first_heading(body_md, outline)
    if head:
        return (head, SOURCE_DERIVED)
    stem = _from_filename(source_relpath)
    return (stem, SOURCE_DERIVED) if stem else ("", "")


def _lede_lines(body_md, outline=None):
    # type: (str, list) -> list
    """The first run of PROSE lines in the body — no heading, list, table or code.

    When the outline is available the scan starts at the first node's span, which is
    how table-of-contents furniture (already detected and excluded from the outline)
    stops being mistaken for the lede.
    """
    lines = (body_md or "").split("\n")
    start = 0
    for node in _outline_nodes(outline):
        span = node.get("line_span")
        if isinstance(span, (list, tuple)) and span and isinstance(span[0], int):
            start = max(0, min(span[0], len(lines)))
            break
    out = []  # type: list
    in_code = False
    for line in lines[start:]:
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        stripped = line.strip()
        if not stripped:
            if out:
                break                      # blank line ends the paragraph
            continue
        if _ATX.match(line) or _NOT_PROSE.match(line):
            if out:
                break
            continue
        out.append(stripped)
    return out


def _truncate_sentence(text, max_chars):
    # type: (str, int) -> str
    """At most ``max_chars``, cut at a sentence end when there is one."""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = -1
    for m in _SENTENCE_END.finditer(window):
        cut = m.end(0)
    if cut > 0:
        return window[:cut].strip()
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip() + "…"


def abstract_floor(body_md, outline=None, max_chars=320):
    # type: (str, list, int) -> str
    """A bounded summary of the document, with no model in the loop.

    The lede paragraph is not a summary a writer composed, so this is a FLOOR and
    tier 1: it is what the document opens with, truncated at a sentence boundary so
    it reads as prose rather than as a value that was cut off. A model may replace
    it (see `title_floor` for why a derived floor stays overwritable) and a person
    outranks both.

    Markdown is stripped through the shared `markdown_to_text`, so a lede full of
    links and emphasis reads as sentences instead of syntax.
    """
    lines = _lede_lines(body_md, outline)
    if not lines:
        return ""
    text = _WS.sub(" ", markdown_to_text(" ".join(lines))).strip()
    return _truncate_sentence(text, max_chars) if text else ""


def link_category(url):
    # type: (str) -> str
    """The `link_categories` term a URL can be assigned WITHOUT judgement.

    Two of the six are measurable from the URL itself: a link with an http(s)
    authority leaves the corpus, and everything else (a relative path, a `#fragment`,
    a `mailto:`) stays inside it. The other four — `product_docs`,
    `vendor_and_legal`, `platform`, `standards` — need to know what the target IS,
    which a URL does not say, so the harvester never claims one. That is a model's
    job, and its answer arrives as a separate record rather than as a rewrite of
    this one.
    """
    text = (url or "").strip().lower()
    return "ecosystem" if text.startswith(("http://", "https://")) else "internal"


def harvested_links(outline, anchors=None):
    # type: (list, set) -> OrderedDict
    """Every outbound URL the body carries, grouped by category — tier 0.

    ``structure.json`` publishes `{text, url, line}` per outline node, harvested from
    the source at recall 1.0 while the tree was built. This turns them into `links`
    records that carry the section that contains them, so a reader can go from an
    edge back to the sentence that asserts it.

    First occurrence wins: a URL cited in three sections is one edge with one home,
    not three. Deterministic, so a re-run reproduces it byte for byte.
    """
    out = OrderedDict()
    seen = set()
    for node in _outline_nodes(outline):
        anchor = heading_anchor(node.get("title") or "")
        if anchors is not None and anchor not in anchors:
            anchor = ""
        for link in node.get("links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            url = url.strip()
            if url.lower() in seen:
                continue
            seen.add(url.lower())
            rec = OrderedDict()
            text = (link.get("text") or "").strip()
            if text:
                rec["title"] = text
            rec["url"] = url
            if isinstance(link.get("line"), int):
                rec["line"] = link["line"]
            if anchor:
                rec["ref"] = "#%s" % anchor
            rec[EVIDENCE_KEY] = SOURCE_EXTRACTED
            out.setdefault(link_category(url), []).append(rec)
    return out


def record_source(rec):
    # type: (object) -> str
    """The per-record origin, or ``""`` when the record does not declare one."""
    if not isinstance(rec, dict):
        return ""
    src = rec.get(EVIDENCE_KEY)
    return src if src in (SOURCE_EXTRACTED, SOURCE_DERIVED, SOURCE_GENERATED,
                          SOURCE_AUTHORED) else ""


def _record_identity(rec):
    # type: (dict) -> str
    """What makes two records the same thing — a URL, a name, an id, or the bytes."""
    for key in ("url", "name", "id", "s"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return "%s=%s" % (key, val.strip().lower())
    return _stable_repr(rec)


def merge_group_evidence(existing, incoming):
    # type: (dict, dict) -> OrderedDict
    """Model records join the harvested ones; they never replace them.

    A `groups` field is the one place where "authored/evidence wins" cannot be a
    whole-field verdict: rejecting the model's answer outright would lose its
    categories, and accepting it outright would delete edges that were MEASURED.
    So the unit of the decision is the record — evidence first, in its own
    category, and a proposal naming a URL we already harvested is dropped as a
    duplicate of a better-sourced record.
    """
    evidence = OrderedDict()
    known = set()
    for gname, members in (existing or {}).items():
        if not isinstance(members, list):
            continue
        keep = [m for m in members
                if isinstance(m, dict) and record_source(m) == SOURCE_EXTRACTED]
        if keep:
            evidence[gname] = keep
            known |= set(_record_identity(m) for m in keep)
    if not evidence:
        return OrderedDict(incoming or {})
    out = OrderedDict((g, list(m)) for g, m in evidence.items())
    for gname, members in (incoming or {}).items():
        if isinstance(members, dict):
            out.setdefault(gname, members)
            continue
        for member in members or []:
            if isinstance(member, dict) and _record_identity(member) in known:
                continue
            out.setdefault(gname, [])
            if isinstance(out[gname], list):
                out[gname].append(member)
    return out


def value_source(value, default=SOURCE_GENERATED):
    # type: (object, str) -> str
    """The origin a MIXED grouped value may honestly claim: the weakest one in it.

    A block holding one harvested link and one proposed link is not "extracted". If
    the field-level record said it was, a reader would take the model's guess for a
    measurement — which is the exact confusion the per-record `source` exists to
    prevent, so the summary must not undo it.
    """
    sources = set()
    groups = value if isinstance(value, dict) else {}
    for members in groups.values():
        for member in (members if isinstance(members, list) else []):
            sources.add(record_source(member) or default)
    if not sources:
        return default
    for weakest in (SOURCE_GENERATED, SOURCE_DERIVED, SOURCE_EXTRACTED,
                    SOURCE_AUTHORED):
        if weakest in sources:
            return weakest
    return default


def source_url(base_url, source_relpath):
    # type: (str, str) -> str
    """A resolvable URI reference for the source document — the page's way home.

    ``source.uri`` is a filesystem RELPATH: it may hold spaces, ``#`` and ``?``, all
    of which mean something else in a URL, so it cannot be pasted into a link. This
    is always a valid URI reference — absolute when a base is configured
    (``--source-base-url``), and a relative one otherwise, which resolves correctly
    for any consumer that publishes the corpus alongside the sources it came from.
    A base is never invented: doc2md does not know where anybody serves their files.
    """
    rel = (source_relpath or "").replace("\\", "/").lstrip("/")
    if not rel:
        return ""
    ref = quote(rel)
    base = (base_url or "").strip()
    return (base.rstrip("/") + "/" + ref) if base else ref


def _as_date(value):
    # type: (object) -> object
    if isinstance(value, datetime.date):
        return value
    text = ("%s" % (value or "")).strip()[:10]
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def next_review_due(last_reviewed, review_cadence):
    # type: (object, str) -> str
    """``last_reviewed + review_cadence``, or ``""`` when that is not a date.

    WHY AN AUTHORED-ONLY FIELD MAY BE DERIVED. `authored_only` means no tier-2
    machinery may write the field: a model must not GUESS when a document was last
    reviewed or how secret it is, because the value's whole worth is that a person
    stood behind it. This is not a guess. Both inputs are authored, the rule is
    arithmetic, and the output makes no claim the person did not already make — it
    restates their commitment in the form a reminder can read. Nothing is written
    when either input is missing, and a value already present is never touched.
    """
    days = REVIEW_INTERVAL_DAYS.get(("%s" % (review_cadence or "")).strip().lower())
    if not days:
        return ""                        # `on_change` / `none` name no interval
    day = _as_date(last_reviewed)
    if day is None:
        return ""
    return (day + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


# ==============================================================================


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


def _prov(source, model="", prompt_sha="", value=None):
    # type: (str, str, str, object) -> OrderedDict
    """One field's origin record.

    NO ``tier``. It was a pure function of the field name (``field(name).tier``),
    duplicated into every record for every field of every document — about 40% of
    the metadata bytes — and nothing ever read it back. A number that can be
    recomputed for free is not a fact worth storing; it is a fact worth drifting.
    """
    rec = OrderedDict()
    rec["source"] = source
    if source == SOURCE_GENERATED:
        rec["model"] = model or ""
        rec["prompt_sha"] = prompt_sha or ""
    if source in _MACHINE_SOURCES:
        # The fingerprint of what WE wrote. Without it a human correction to a
        # machine value is invisible to the next run, which reverts it.
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
    prov[name] = _prov(source, model, prompt_sha,
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
