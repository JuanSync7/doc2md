"""
title: Metadata linter — cardinality, vocabulary, hygiene, referential integrity (private)
layer: backend
public_api: no
summary: Grade one document's metadata block and a whole corpus's registry health against the controlled vocabulary.
"""
# 3.6-compatible. Stdlib only. Pure policy — mappings in, findings out. The file
# walk lives in scripts/kb_lint.py.
#
# FOUR GATES, and they answer different questions:
#
#   CARDINALITY   is this field a facet at all? A field whose values are nearly
#                 all distinct is documentation, not a facet: it will never group
#                 anything, and letting a model believe it is choosing from a set
#                 invites it to invent one more value per document.
#   MEMBERSHIP    is every value a term we actually govern? Closed fields fail on
#                 an unknown; registry fields divert it to <field>_proposed.
#   HYGIENE       did a value change TYPE in transit? Unquoted no/yes/on/off and
#                 0644 are retyped by any YAML 1.1 reader, so a bool sitting in an
#                 enum field is evidence the block was written by something that
#                 did not quote.
#   INTEGRITY     do the pointers resolve? refs are governed by what they point at
#                 rather than by a term list.
#
# WHY SILENT SKIPS ARE THE REAL ENEMY. The obvious way to write the membership
# check — collect the values, drop the Nones, compare to the allowed set — reports
# a clean bill of health for a document where half the entities never carried a
# type at all, and skips entirely any entity group that is a mapping rather than a
# list. Both cases are handled explicitly below and BOTH produce findings. A linter
# that cannot see a field is not the same as a field that is correct.
from collections import namedtuple, Counter, OrderedDict

from ._schema import (FIELDS, META_KEY, PROVENANCE_KEY, REF_FIELDS, field,
                      field_names, group_vocab, model_writable, proposed_key,
                      record_required, record_vocab, SOURCE_AUTHORED,
                      SOURCE_GENERATED)

__all__ = ["Finding", "FacetRow", "facet_report", "lint_document",
           "normalize_document", "corpus_report", "ERROR", "WARN", "INFO",
           "VERDICT_OK", "VERDICT_SPARSE", "VERDICT_THIN", "VERDICT_NOT_A_FACET"]

ERROR = "error"
WARN = "warn"
INFO = "info"

Finding = namedtuple("Finding", ["code", "severity", "where", "message"])
FacetRow = namedtuple("FacetRow", ["name", "uses", "distinct", "ratio", "verdict"])

VERDICT_OK = "ok"
VERDICT_THIN = "thin"
VERDICT_NOT_A_FACET = "note-not-facet"
VERDICT_SPARSE = "sparse"        # too few values to grade — not a pass, not a fail


# --------------------------------------------------------------- cardinality

def facet_report(pairs, warn=0.45, fail=0.75, min_uses=8):
    # type: (list, float, float, int) -> list
    """``[(name, values)]`` -> one FacetRow each, worst ratio first.

    ``ratio`` is distinct/used. Empty fields are reported with ratio 0 rather than
    dropped, so a field that stopped being populated is visible instead of absent.

    Below ``min_uses`` the verdict is ``sparse`` and NOT a judgement: a document
    with one relation has a distinct/used ratio of 1.00, which would condemn every
    short document as "not a facet" while saying nothing at all about the field.
    Sparse is deliberately neither a pass nor a fail — the ratio is reported so it
    can be aggregated across a corpus, where the sample does exist.
    """
    rows = []  # type: list
    for name, vals in pairs:
        vals = [v for v in vals if v is not None]
        if not vals:
            rows.append(FacetRow(name, 0, 0, 0.0, VERDICT_OK))
            continue
        counts = Counter("%s" % (v,) for v in vals)
        ratio = float(len(counts)) / float(len(vals))
        if len(vals) < min_uses:
            verdict = VERDICT_SPARSE
        elif ratio > fail:
            verdict = VERDICT_NOT_A_FACET
        elif ratio > warn:
            verdict = VERDICT_THIN
        else:
            verdict = VERDICT_OK
        rows.append(FacetRow(name, len(vals), len(counts), ratio, verdict))
    return sorted(rows, key=lambda r: -r.ratio)


# --------------------------------------------------------------- collection

def _records(meta, name, findings=None):
    # type: (dict, str, list) -> list
    """``(index, mapping)`` for each mapping member of a record list.

    The index is the position in the RAW list: once a malformed member is dropped,
    a re-derived index would point every later finding at the wrong record.

    A non-mapping member is REPORTED when a findings list is supplied, not just
    dropped — the entity walk already reports the identical shape, and a register
    full of junk must not read as an empty register.
    """
    v = meta.get(name)
    if not isinstance(v, list):
        return []
    out = []  # type: list
    for i, rec in enumerate(v):
        if isinstance(rec, dict):
            out.append((i, rec))
        elif findings is not None:
            findings.append(Finding("record-malformed", ERROR,
                                    "%s[%d]" % (name, i),
                                    "expected a mapping, got %s"
                                    % type(rec).__name__))
    return out


def _entity_rows(meta, vocab, findings):
    # type: (dict, object, list) -> list
    """``(where, type)`` for every entity, with the group's implied type filled in.

    Two shapes the naive walk gets wrong, both present in real data:
      * an entry with no ``type`` key — the group name supplies it, and if the
        group is unknown the entry is REPORTED, never skipped;
      * a group that is a mapping of scalars rather than a list of entities
        (``identifiers: {gpg_fingerprint: ...}``) — declared as a scalar group and
        recorded, so "not checked" never reads as "checked and clean".
    """
    out = []  # type: list
    groups = meta.get("entities")
    if not isinstance(groups, dict):
        return out
    for gname, members in groups.items():
        implied = vocab.group_type(gname)
        if isinstance(members, dict):
            if not implied:
                findings.append(Finding(
                    "entity-group-unknown", WARN, "entities.%s" % gname,
                    "scalar entity group %r is not in entity_types.group_types, so "
                    "nothing here is type-checked" % gname))
            else:
                findings.append(Finding(
                    "entity-group-scalar", INFO, "entities.%s" % gname,
                    "group is a mapping of scalars, not entities — %d key(s) not "
                    "type-checked" % len(members)))
            continue
        if not isinstance(members, list):
            findings.append(Finding(
                "entity-group-malformed", ERROR, "entities.%s" % gname,
                "expected a list of entities or a mapping, got %s"
                % type(members).__name__))
            continue
        for i, ent in enumerate(members):
            where = "entities.%s[%d]" % (gname, i)
            if not isinstance(ent, dict):
                findings.append(Finding("entity-malformed", ERROR, where,
                                        "entity is not a mapping"))
                continue
            if "type" in ent and not isinstance(ent.get("type"), str):
                findings.append(Finding(
                    "type-coerced", ERROR, "%s.type" % where,
                    "entity type is %s (%r), not a string — a retyped scalar must "
                    "not fall back to the group's implied type"
                    % (type(ent.get("type")).__name__, ent.get("type"))))
                continue
            etype = ent.get("type")
            if not etype:
                if not implied:
                    findings.append(Finding(
                        "entity-untyped", ERROR, where,
                        "entity has no `type` and group %r implies none — add a "
                        "type or register the group" % gname))
                    continue
                etype = implied
            out.append((where, etype))
    return out


def _facet_pairs(meta, vocab, findings):
    # type: (dict, object, list) -> list
    """The value lists the cardinality gate grades — the pasted linter's five, plus
    the entity types it could not see."""
    rels = _records(meta, "relations")
    return [
        ("relations.p", [r.get("p") for _i, r in rels]),
        ("entities.type", [t for _, t in _entity_rows(meta, vocab, findings)]),
        ("risks.mode", [r.get("mode") for _i, r in _records(meta, "risks")]),
        ("risks.impact", [r.get("impact") for _i, r in _records(meta, "risks")]),
        ("decisions.status", [r.get("status")
                              for _i, r in _records(meta, "decisions")]),
    ]


# --------------------------------------------------------------- membership

def _check_value(vocab, vname, value, where, findings, registry_proposals):
    # type: (object, str, object, str, list, dict) -> None
    """One value against one vocabulary, honouring its governance regime."""
    if value is None:
        return
    if not isinstance(value, str):
        # A non-string in an enum slot means a reader retyped it: an unquoted
        # no/yes/on/off becomes a bool under YAML 1.1 and an unquoted 0644 becomes
        # an int. Report the TYPE — comparing it to the term list would just say
        # "unknown value" and hide the actual cause.
        kind = {bool: "a boolean", int: "an integer", float: "a float",
                type(None): "null"}.get(type(value), "a %s" % type(value).__name__)
        findings.append(Finding(
            "type-coerced", ERROR, where,
            "value is %s (%r) in the %r enum — an unquoted scalar was retyped; "
            "quote enum values" % (kind, value, vname)))
        return
    if not vocab.has(vname):
        findings.append(Finding("vocab-missing", ERROR, where,
                                "no vocabulary named %r" % vname))
        return
    regime = vocab.governance(vname)
    canonical, quals = vocab.normalize(vname, value)
    if canonical != value:
        detail = " (carries %s)" % ", ".join(sorted(quals)) if quals else ""
        findings.append(Finding(
            "vocab-alias", WARN, where,
            "%r is an alias for %r%s — store the canonical term"
            % (value, canonical, detail)))
        value = canonical
    if regime == "closed":
        if value not in vocab.values(vname):
            findings.append(Finding(
                "vocab-unknown", ERROR, where,
                "%r is not in the closed vocabulary %r (allowed: %s)"
                % (value, vname, ", ".join(vocab.values(vname)))))
    elif regime == "registry":
        # Not in the registry yet -> a PROPOSAL, never a finding. Registry drift is
        # a corpus-level signal (promotion at >= N documents), not a per-document
        # error, so one document may not be told it is wrong for coining a term.
        if value not in vocab.values(vname):
            registry_proposals.setdefault(vname, []).append(value)


def _check_refs(meta, anchors, known_ids, findings, unreadable=0):
    # type: (dict, set, set, list, int) -> None
    """Referential integrity: refs are governed by what they point at.

    ``anchors`` is what the body actually makes addressable and ``known_ids`` the
    page ids the corpus contains. When a caller supplies neither, the check is
    SKIPPED rather than passed — an unverifiable pointer must not be reported as
    verified.

    ``unreadable`` is how many documents the corpus HAS but could not parse. It does
    not disable the check — one bad file must not buy the other 999 an amnesty — it
    QUALIFIES the one finding it can invert: a ``see_also`` whose target lives in the
    unreadable document reads as dead, so the finding says so and names the count.
    """
    # What a `control`/`protects` may point at. The vocabulary's rule says "an id in
    # this document's registers", but in real documents a control is far more often
    # a THING than a decision — a hook script, a settings key, a sandbox. Measured on
    # the worked example, 11 of 12 controls named an entity and exactly one named a
    # decision id. A rule that the data violates nine times in ten is a broken rule,
    # not a broken corpus, so entity identities resolve too (recorded in vocab.yaml).
    register_ids = set()
    for name in ("decisions", "risks", "open_questions"):
        for _i, rec in _records(meta, name):
            if rec.get("id"):
                register_ids.add(rec["id"])
    groups = meta.get("entities")
    if isinstance(groups, dict):
        for members in groups.values():
            if isinstance(members, dict):
                register_ids |= set(k for k in members.keys() if isinstance(k, str))
            elif isinstance(members, list):
                for ent in members:
                    if not isinstance(ent, dict):
                        continue
                    for key in ("name", "path", "fqdn", "id"):
                        if isinstance(ent.get(key), str):
                            register_ids.add(ent[key])

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == PROVENANCE_KEY:
                    # Provenance keys are FIELD NAMES, not pointers. `see_also`,
                    # `control` and `protects` are all both — so walking in here made
                    # every document that records provenance for one of them fail
                    # with "expected a non-empty string pointer, got {tier, source,
                    # ...}". A ref check must grade values, never the record of where
                    # a value came from.
                    continue
                if k in REF_FIELDS:
                    for val in (v if isinstance(v, list) else [v]):
                        _check_one_ref(k, val, "%s.%s" % (where, k))
                else:
                    walk(v, "%s.%s" % (where, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))

    def _check_one_ref(kind, val, where):
        if not isinstance(val, str) or not val.strip():
            findings.append(Finding(
                "ref-malformed", ERROR, where,
                "expected a non-empty string pointer, got %r" % (val,)))
            return
        if kind in ("ref", "backs"):
            if anchors is None:
                return
            if not val.startswith("#"):
                findings.append(Finding("ref-malformed", WARN, where,
                                        "%r is not a #fragment" % val))
                return
            if val[1:] not in anchors:
                findings.append(Finding(
                    "ref-unresolved", ERROR, where,
                    "%r does not match any heading anchor in this document" % val))
        elif kind == "see_also":
            if known_ids is None:
                return
            target = val.strip()
            if target.startswith("[[") and target.endswith("]]"):
                target = target[2:-2]
            if target not in known_ids:
                findings.append(Finding(
                    "see-also-unresolved", WARN, where,
                    "%r does not resolve to a known page id%s"
                    % (target,
                       "" if not unreadable else
                       " (%d document(s) in this corpus could not be read, so the "
                       "target may be one of them — fix those first)" % unreadable)))
        else:                                    # control / protects
            if val not in register_ids:
                findings.append(Finding(
                    "register-unresolved", WARN, where,
                    "%r is neither an id in this document's registers nor a named "
                    "entity in it" % val))

    walk(meta, META_KEY)


def _check_authorship(meta, findings):
    # type: (dict, list) -> None
    """A field that exists to be a safety or accountability boundary may not carry
    a generated value. This is the one check whose failure is never a style issue."""
    prov = meta.get(PROVENANCE_KEY)
    if prov is None:
        return
    if not isinstance(prov, dict):
        findings.append(Finding(
            "provenance-malformed", ERROR, PROVENANCE_KEY,
            "expected a mapping, got %s — nothing in this document's provenance "
            "can be checked" % type(prov).__name__))
        return
    for name, rec in prov.items():
        if name not in field_names():
            findings.append(Finding(
                "provenance-unknown-field", WARN,
                "%s.%s" % (PROVENANCE_KEY, name),
                "%r is not a metadata field" % name))
            continue
        if not isinstance(rec, dict):
            findings.append(Finding(
                "provenance-malformed", ERROR, "%s.%s" % (PROVENANCE_KEY, name),
                "expected a mapping, got %s — this field's origin is unverifiable, "
                "and an authored-only field could be model-written without any "
                "finding" % type(rec).__name__))
            continue
        if "source" not in rec:
            findings.append(Finding(
                "provenance-malformed", ERROR, "%s.%s" % (PROVENANCE_KEY, name),
                "no `source`, so it cannot be told apart from a generated value"))
            continue
        src = rec.get("source")
        if src == SOURCE_GENERATED and field(name).authored_only:
            findings.append(Finding(
                "authored-only-generated", ERROR,
                "%s.%s" % (PROVENANCE_KEY, name),
                "%r is authored-only but its provenance says %r — a model may not "
                "write an accountability or confidentiality field"
                % (name, SOURCE_GENERATED)))
        if src is not None and src not in (SOURCE_AUTHORED, SOURCE_GENERATED,
                                           "extracted", "derived"):
            findings.append(Finding(
                "provenance-unknown-source", WARN,
                "%s.%s" % (PROVENANCE_KEY, name),
                "unknown provenance source %r" % (src,)))


# --------------------------------------------------------------- entry points

def lint_document(meta, vocab, anchors=None, known_ids=None, unreadable=0):
    # type: (dict, object, set, set, int) -> dict
    """Grade one document's metadata block.

    Returns ``{"findings": [Finding], "facets": [FacetRow], "proposals": {field:
    [value]}, "errors": int, "warnings": int}``. Never raises on bad data — a
    malformed block is a FINDING, because the linter's job is to report the corpus
    it has rather than the corpus it wishes it had.

    ``unreadable`` — documents the corpus holds but could not parse — qualifies the
    ``see_also`` finding rather than switching the check off; see ``_check_refs``.
    """
    meta = meta or {}
    findings = []  # type: list
    proposals = {}  # type: dict

    known = set(field_names())
    for key in meta.keys():
        if key == PROVENANCE_KEY or key in known:
            continue
        if key.endswith("_proposed") and key[:-len("_proposed")] in known:
            continue
        findings.append(Finding("unknown-field", WARN, "%s.%s" % (META_KEY, key),
                                "%r is not in the metadata schema" % key))

    # Container SHAPE first. Without this a whole mis-typed field (relations as a
    # string, entities as a list) produces no finding at all — the collectors below
    # each return empty for the wrong type, so four broken fields read as a clean
    # bill of health. That is the silent skip this module exists to close.
    _EXPECT = {"records": list, "groups": dict, "map": dict, "list": list,
               "scalar": (str, int, float, bool)}
    for f in FIELDS:
        want = _EXPECT.get(f.kind)
        val = meta.get(f.name)
        if want is None or val is None:
            continue
        if not isinstance(val, want):
            names = (want.__name__ if isinstance(want, type)
                     else "/".join(t.__name__ for t in want))
            findings.append(Finding(
                "field-malformed", ERROR, "%s.%s" % (META_KEY, f.name),
                "expected %s for a %r field, got %s"
                % (names, f.kind, type(val).__name__)))

    # Scalar and list fields bound directly to a vocabulary.
    for f in FIELDS:
        if not f.vocab or f.kind in ("records", "groups", "map"):
            continue
        val = meta.get(f.name)
        if val is None:
            continue
        if f.kind == "list":
            if not isinstance(val, list):
                findings.append(Finding("field-malformed", ERROR,
                                        "%s.%s" % (META_KEY, f.name),
                                        "expected a list, got %s"
                                        % type(val).__name__))
                continue
            for i, v in enumerate(val):
                _check_value(vocab, f.vocab, v, "%s[%d]" % (f.name, i),
                             findings, proposals)
        else:
            _check_value(vocab, f.vocab, val, f.name, findings, proposals)

    # Record lists: every one is WALKED (so a junk member is reported even in a
    # list with no vocabulary, like open_questions), and where a vocabulary binds a
    # sub-key, a record missing it is reported rather than passing as clean.
    for f in FIELDS:
        if f.kind != "records":
            continue
        rv = record_vocab(f.name)
        required = record_required(f.name)
        for i, rec in _records(meta, f.name, findings):
            for sub, vname in rv.items():
                if sub in rec and rec.get(sub) is not None:
                    _check_value(vocab, vname, rec.get(sub),
                                 "%s[%d].%s" % (f.name, i, sub), findings, proposals)
                elif sub in required:
                    findings.append(Finding(
                        "record-untyped", ERROR, "%s[%d].%s" % (f.name, i, sub),
                        "record has no %r, so nothing checks it against %r"
                        % (sub, vname)))

    # Grouped fields: entity types (with the group fallback) and link categories.
    for where, etype in _entity_rows(meta, vocab, findings):
        _check_value(vocab, "entity_types", etype, "%s.type" % where,
                     findings, proposals)
    if isinstance(meta.get("links"), dict):
        gv = group_vocab("links")
        for cat in meta["links"].keys():
            _check_value(vocab, gv["group_name"], cat, "links.%s" % cat,
                         findings, proposals)

    _check_refs(meta, anchors, known_ids, findings, unreadable)
    _check_authorship(meta, findings)

    facets = facet_report(_facet_pairs(meta, vocab, []),
                          warn=float(vocab.threshold("facet_warn", 0.45)),
                          fail=float(vocab.threshold("facet_fail", 0.75)),
                          min_uses=int(vocab.threshold("facet_min_uses", 8)))
    for row in facets:
        if row.verdict == VERDICT_NOT_A_FACET:
            findings.append(Finding(
                "facet-not-a-facet", ERROR, row.name,
                "%d uses / %d distinct (ratio %.2f) — this is documentation, not a "
                "facet; move it to an unindexed note" % (row.uses, row.distinct,
                                                         row.ratio)))
        elif row.verdict == VERDICT_THIN:
            findings.append(Finding(
                "facet-thin", WARN, row.name,
                "%d uses / %d distinct (ratio %.2f) — thin; watch for drift"
                % (row.uses, row.distinct, row.ratio)))

    return {
        "findings": findings,
        "facets": facets,
        "proposals": proposals,
        "errors": sum(1 for f in findings if f.severity == ERROR),
        "warnings": sum(1 for f in findings if f.severity == WARN),
    }


def normalize_document(meta, vocab):
    # type: (dict, object) -> tuple
    """``(normalised_meta, changed)`` — aliases resolved, alias qualifiers merged.

    Registry values that are unknown move to ``<field>_proposed`` rather than being
    dropped or silently kept: an unknown tag is a signal about the corpus, and
    deleting it destroys the evidence the promotion rule runs on.
    """
    out = OrderedDict()
    changed = []  # type: list

    def norm_scalar(vname, value, where):
        canonical, quals = vocab.normalize(vname, value)
        if canonical != value:
            changed.append((where, value, canonical))
        return canonical, quals

    for key, val in (meta or {}).items():
        if key not in field_names():
            # A `<field>_proposed` list must MERGE, never replace. Overwriting it
            # loses whichever side arrived first depending purely on key order, and
            # what it loses is the promotion evidence this function promises to keep.
            base = key[:-len("_proposed")] if key.endswith("_proposed") else ""
            if base and base in field_names() and isinstance(val, list):
                out[key] = sorted(set(list(out.get(key) or []) + list(val)),
                                  key=str)
            else:
                out[key] = val
            continue
        f = field(key)
        if f.vocab and f.kind == "scalar" and isinstance(val, str):
            canonical, _ = norm_scalar(f.vocab, val, key)
            if vocab.governance(f.vocab) == "registry" \
                    and canonical not in vocab.values(f.vocab):
                slot = proposed_key(key)
                out[slot] = sorted(set(list(out.get(slot) or []) + [canonical]),
                                   key=str)
                continue
            out[key] = canonical
        elif f.vocab and f.kind == "list" and isinstance(val, list):
            keep, prop = [], []
            for i, v in enumerate(val):
                if not isinstance(v, str):
                    # Not a term. Route it to the proposals slot rather than leaving
                    # it sitting inside a governed field as if it had been checked.
                    prop.append(v)
                    continue
                canonical, _ = norm_scalar(f.vocab, v, "%s[%d]" % (key, i))
                if vocab.governance(f.vocab) == "registry" \
                        and canonical not in vocab.values(f.vocab):
                    prop.append(canonical)
                else:
                    keep.append(canonical)
            out[key] = keep
            if prop:
                slot = proposed_key(key)
                out[slot] = sorted(set(list(out.get(slot) or []) + prop), key=str)
        elif record_vocab(key) and isinstance(val, list):
            rv = record_vocab(key)
            recs = []
            for i, rec in enumerate(val):
                if not isinstance(rec, dict):
                    recs.append(rec)
                    continue
                new = OrderedDict(rec)
                for sub, vname in rv.items():
                    if isinstance(new.get(sub), str):
                        canonical, quals = norm_scalar(
                            vname, new[sub], "%s[%d].%s" % (key, i, sub))
                        new[sub] = canonical
                        for qk, qv in quals.items():
                            new.setdefault(qk, qv)
                recs.append(new)
            out[key] = recs
        else:
            out[key] = val
    return (out, changed)


def corpus_report(doc_metas, vocab):
    # type: (list, object) -> dict
    """Registry health across a whole corpus — the part one document cannot answer.

    Both governing rules here are corpus-scoped by definition: a proposal is
    promoted when it appears on >= ``promote_at`` DOCUMENTS, and the singleton rate
    (share of registry terms used on exactly one document) is the signal that the
    vocabulary has stopped grouping anything. Document frequency, not occurrence
    count — one document repeating a tag thirty times is still one document.
    """
    promote_at = int(vocab.threshold("promote_at", 3))
    max_singleton = float(vocab.threshold("singleton_rate_max", 0.40))
    registry_fields = [f.name for f in FIELDS
                       if f.vocab and vocab.has(f.vocab)
                       and vocab.governance(f.vocab) == "registry"]

    out = OrderedDict()
    for name in registry_fields:
        used = Counter()      # canonical value -> document frequency
        proposed = Counter()  # unknown value   -> document frequency
        for meta in doc_metas:
            vals = (meta or {}).get(name)
            seen = set()
            if isinstance(vals, list):
                seen |= set(v for v in vals if isinstance(v, str))
            elif isinstance(vals, str):
                seen.add(vals)
            prop = (meta or {}).get(proposed_key(name))
            if isinstance(prop, list):
                seen |= set(v for v in prop if isinstance(v, str))
            known = vocab.values(field(name).vocab)
            for v in seen:
                canonical, _ = vocab.normalize(field(name).vocab, v)
                if canonical not in known:
                    proposed[canonical] += 1
                else:
                    used[canonical] += 1
        total = len(used)
        singletons = sum(1 for _, n in used.items() if n == 1)
        rate = (float(singletons) / float(total)) if total else 0.0
        out[name] = OrderedDict([
            ("vocab", field(name).vocab),
            ("documents", len(doc_metas)),
            ("terms_used", total),
            ("singletons", singletons),
            ("singleton_rate", round(rate, 4)),
            ("singleton_rate_max", max_singleton),
            ("failing", bool(total) and rate > max_singleton),
            ("promote", sorted(v for v, n in proposed.items() if n >= promote_at)),
            ("proposed", OrderedDict(sorted(proposed.items(),
                                            key=lambda kv: (-kv[1], kv[0])))),
        ])
    return out
