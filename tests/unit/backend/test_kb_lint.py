"""
title: Unit — backend.kb metadata linter
kind: tests
layer: backend
summary: The four gates — cardinality, membership, hygiene, integrity — plus the silent skips they exist to close, normalisation and corpus health.
"""
import pytest
from backend.kb import (ERROR, INFO, WARN, FacetRow, Finding, corpus_report,
                        facet_report, lint_document, load_vocab,
                        normalize_document)

pytestmark = pytest.mark.unit


# A whole vocabulary, inline — no disk, so this stays a unit test. The thresholds
# are parameters because several gates are only observable at a chosen threshold:
# `facet_min_uses` decides when a ratio is allowed to condemn a field at all, and
# `promote_at` decides when a proposal becomes a term.
_VOCAB = """
version: 1

lint:
  facet_warn: %(warn)s
  facet_fail: %(fail)s
  facet_min_uses: %(min_uses)s
  promote_at: %(promote_at)s
  singleton_rate_max: %(singleton)s

relation_predicates:
  governance: closed
  values: [runs_on, requires, mitigates, targets, governs]
  aliases:
    threatens: targets

failure_modes:
  governance: closed
  values: [silent, delayed, overt, adversarial]

impact:
  governance: closed
  values: [critical, high, medium, low]

decision_status:
  governance: closed
  values: [proposed, rejected, accepted, deprecated, superseded]
  aliases:
    assumed: accepted
  alias_qualifiers:
    assumed:
      assumption: true

entity_types:
  governance: closed
  values: [Host, Path, Script, Person, Identifier]
  group_types:
    hosts: Host
    scripts: Script
    identifiers: Identifier
    gadgets: Gadget

document_types:
  governance: closed
  values: [runbook, policy, design, guide]

document_status:
  governance: closed
  values: [draft, review, approved]

confidentiality:
  governance: closed
  values: [public, internal, confidential, restricted]

review_cadence:
  governance: closed
  values: [monthly, quarterly, annual]

lang:
  governance: closed
  values: [en-GB, en-US]

link_categories:
  governance: closed
  values: [product_docs, internal, standards]

tags:
  governance: registry
  values: [linux, storage]
  aliases:
    rhel-8: rhel8

subtype:
  governance: registry
  values: []

audience:
  governance: registry
  values: []

topics:
  governance: registry
  values: []

keywords:
  governance: registry
  values: []

refs:
  governance: ref
  fields: [ref, backs, see_also, control, protects]
"""

# `gadgets -> Gadget` above is deliberate: a registered entity GROUP whose implied
# type is not a value of the closed `entity_types` vocabulary. It is the only way
# to prove from the outside that an implied type reaches the membership gate
# instead of merely being collected.


def _vocab(warn=0.45, fail=0.75, min_uses=8, promote_at=3, singleton=0.40):
    return load_vocab(text=_VOCAB % {"warn": warn, "fail": fail,
                                     "min_uses": min_uses,
                                     "promote_at": promote_at,
                                     "singleton": singleton})


def _codes(result):
    return [f.code for f in result["findings"]]


def _of(result, code):
    return [f for f in result["findings"] if f.code == code]


def _facet(result, name):
    return [row for row in result["facets"] if row.name == name][0]


# --------------------------------------------------------- silent-skip defects

def test_an_entity_with_no_type_key_takes_it_from_the_group_and_is_still_checked():
    # The naive walk collects `ent["type"]`, drops the Nones and reports a clean
    # bill of health for a document where half the entities never carried a type.
    # A linter that cannot see a field is not the same as a field that is correct,
    # so the group name supplies the type and the type goes through the same gate.
    meta = {"entities": {"hosts": [{"name": "build01"}, {"name": "build02"}],
                         "gadgets": [{"name": "widget-press"}]}}
    result = lint_document(meta, _vocab())

    row = _facet(result, "entities.type")
    assert row.uses == 3 and row.distinct == 2  # 2 x Host + 1 x Gadget

    # And they are VALIDATED, not just counted: the group-implied `Gadget` is not
    # a member of the closed vocabulary and is reported as such.
    unknown = _of(result, "vocab-unknown")
    assert [f.where for f in unknown] == ["entities.gadgets[0].type"]
    assert unknown[0].severity == ERROR
    assert "Host" in unknown[0].message


def test_an_untyped_entity_in_an_unregistered_group_is_an_error_not_a_skip():
    meta = {"entities": {"widgets": [{"name": "sprocket"}]}}
    result = lint_document(meta, _vocab())

    untyped = _of(result, "entity-untyped")
    assert len(untyped) == 1
    assert untyped[0].severity == ERROR
    assert untyped[0].where == "entities.widgets[0]"
    # Unverifiable, so it is excluded from the facet sample rather than counted
    # as a value: a type nobody knows must not shape the cardinality verdict.
    assert _facet(result, "entities.type").uses == 0


def test_an_entity_group_that_is_a_mapping_of_scalars_is_reported_not_dropped():
    # `identifiers: {gpg_fingerprint: ...}` is a real shape in the corpus. It is
    # not a list of entities, so the obvious walk skips it entirely and the
    # document reads as clean. Both variants must leave a trace, and exactly one
    # each — the collector runs twice (once for findings, once for the facet
    # sample) and only one of those runs may report.
    meta = {"entities": {"identifiers": {"gpg_fingerprint": "0xDEADBEEF"},
                         "mystery": {"k": "v"},
                         "broken": "not a group at all"}}
    result = lint_document(meta, _vocab())

    scalar = _of(result, "entity-group-scalar")
    assert len(scalar) == 1 and scalar[0].severity == INFO
    assert scalar[0].where == "entities.identifiers"

    unknown_group = _of(result, "entity-group-unknown")
    assert len(unknown_group) == 1 and unknown_group[0].severity == WARN

    malformed = _of(result, "entity-group-malformed")
    assert len(malformed) == 1 and malformed[0].severity == ERROR
    assert malformed[0].where == "entities.broken"


def test_every_required_sub_key_is_enforced_whether_or_not_it_has_a_vocabulary():
    # THE REQUIRED-KEY CHECK USED TO BE A FILTER. It lived inside the loop over
    # vocabulary-bound sub-keys, so `record_required` could only ever narrow THAT
    # loop: of the thirteen required slots the schema declares, only the three that
    # also carry a vocabulary (relations.p, decisions.status, risks.impact) could
    # produce a finding. `open_questions` binds no vocabulary at all, so its loop
    # body never ran, and the members of a `groups` field were never walked for
    # required keys at all. A bundle where NOT ONE record cited a section anchor —
    # the exact state SCHEMA_VERSION 3 was bumped for — linted errors=0 warnings=0.
    meta = {"schema_version": 3,
            "relations": [{"p": "runs_on"}],
            "decisions": [{"id": "d1", "status": "accepted"}],
            "risks": [{"id": "r1", "impact": "high"}],
            "open_questions": [{"q": "?"}],
            "entities": {"hosts": [{"name": "build01"}]},
            "links": {"internal": [{"title": "x"}]}}
    result = lint_document(meta, _vocab(), anchors=set(["setup"]), known_ids=set())

    where = set(f.where for f in result["findings"]
                if f.code in ("record-missing-required", "record-uncited"))
    assert where == set([
        "relations[0].s", "relations[0].o", "relations[0].ref",
        "decisions[0].ref", "risks[0].ref", "open_questions[0].ref",
        "entities.hosts[0].ref", "links.internal[0].url",
        "links.internal[0].ref"])
    assert all(f.severity == ERROR for f in result["findings"]
               if f.code in ("record-missing-required", "record-uncited"))
    assert result["errors"] == 9

    # ... and a complete record produces none of them.
    clean = lint_document(
        {"schema_version": 3,
         "relations": [{"s": "a", "p": "runs_on", "o": "b", "ref": "#setup"}],
         "decisions": [{"id": "d1", "status": "accepted", "ref": "#setup"}],
         "risks": [{"id": "r1", "impact": "high", "ref": "#setup"}],
         "open_questions": [{"q": "?", "ref": "#setup"}],
         "entities": {"hosts": [{"name": "build01", "ref": "#setup"}]},
         "links": {"internal": [{"url": "https://x/", "ref": "#setup"}]}},
        _vocab(), anchors=set(["setup"]), known_ids=set())
    assert clean["errors"] == 0 and clean["warnings"] == 0


def test_a_harvested_record_that_names_its_line_has_cited_its_source():
    # A URL in the LEDE sits above the first heading, in a region a renderer emits
    # no fragment for, so a `ref` there would be a dead link. What it does have is
    # the body line it was lifted from. `harvested_links` writes exactly that, and
    # the fidelity rubric's D5 row grants exactly this latitude — two gates
    # disagreeing about what counts as a citation would make one of them wrong
    # about every harvested link in the corpus.
    meta = {"schema_version": 3,
            "links": {"internal": [{"url": "https://x/", "line": 3,
                                    "source": "extracted"}]}}
    result = lint_document(meta, _vocab(), anchors=set(["setup"]))
    assert _of(result, "record-uncited") == []

    # The latitude is HARVESTED-only. A model-proposed record gets none of it, and
    # neither does a harvested one that cannot say where it came from.
    for bad in ({"url": "https://x/", "line": 3},
                {"url": "https://x/", "source": "extracted"},
                {"url": "https://x/", "line": 3, "source": "generated"}):
        r = lint_document({"schema_version": 3, "links": {"internal": [bad]}},
                          _vocab(), anchors=set(["setup"]))
        assert [f.where for f in _of(r, "record-uncited")] == \
            ["links.internal[0].ref"]


def test_a_block_declaring_an_older_schema_gets_a_backfill_warning_not_an_error():
    # `ref` became required at SCHEMA_VERSION 3. Every knowledge.json written under
    # v2 has s/p/o and no ref, so grading those as ERRORs would turn every legacy
    # bundle red on the first run after the bump — the same run the skew gate hands
    # the operator as a work list. The finding is still MADE; only its severity
    # follows the revision the block itself declares.
    v2 = lint_document({"schema_version": 2,
                        "relations": [{"s": "a", "p": "runs_on", "o": "b"}]},
                       _vocab(), anchors=set(["setup"]))
    uncited = _of(v2, "record-uncited")
    assert len(uncited) == 1 and uncited[0].severity == WARN
    assert v2["errors"] == 0 and v2["warnings"] == 1

    # An UNSTAMPED block is being written now, against the current schema.
    now = lint_document({"relations": [{"s": "a", "p": "runs_on", "o": "b"}]},
                        _vocab(), anchors=set(["setup"]))
    assert _of(now, "record-uncited")[0].severity == ERROR


def test_a_link_group_that_is_not_a_list_of_records_is_reported_not_skipped():
    # The `links` group was only ever graded on its group NAMES, so a group holding
    # junk — or holding junk MEMBERS — read exactly like a group holding nothing.
    # "Not looked at" and "looked at and clean" must never render the same.
    result = lint_document({"links": {"internal": "https://x/",
                                      "product_docs": [42]}}, _vocab())
    codes = dict((f.code, f) for f in result["findings"])
    assert codes["group-malformed"].where == "links.internal"
    assert codes["record-malformed"].where == "links.product_docs[0]"
    assert all(f.severity == ERROR for f in
               (codes["group-malformed"], codes["record-malformed"]))


def test_one_mistyped_list_field_is_one_error_not_two():
    # The container shape gate already reports every mis-typed list field. The
    # governed-list loop repeated the same report, so `tags: urgent` cost two ERROR
    # rows in the JSON report and two in the corpus error total, while the identical
    # slip on `see_also` (a list field with no vocabulary) cost one — the count for
    # one mistake depended on whether the field happened to have a vocabulary.
    for name in ("tags", "keywords", "topics", "audience", "subtype", "see_also"):
        result = lint_document({name: "not a list"}, _vocab())
        malformed = _of(result, "field-malformed")
        assert [f.where for f in malformed] == ["meta.%s" % name]
        assert result["errors"] == 1
        # The `continue` has to stay: without it the per-item loop walks the string
        # one character at a time and invents a finding per character.
        assert _of(result, "vocab-unknown") == []


# --------------------------------------------------------------- membership

def test_a_container_that_lost_its_shape_is_an_error_for_every_kind_of_field():
    # The container-shape gate is the ONLY thing that notices a whole metadata
    # container arriving as the wrong type: normalize_document passes such a value
    # through verbatim (changed=[]), and every collector below the gate returns
    # empty for a shape it does not recognise — so a document that has lost every
    # relation, entity and link reads exactly like one that had none.
    #
    # Deleting the gate used to leave the whole suite green: the one test that
    # named `field-malformed` was answered by the governed-list emitter, and every
    # other kind (`records`, `groups`, `map`, `scalar`) was pinned nowhere. This
    # asserts the `where` of each finding for that reason — a bare
    # `"field-malformed" in codes` is satisfiable by any one of the five and would
    # leave the other four silently deletable.
    meta = {"relations": "see the architecture diagram",   # records -> str
            "entities": ["DmaArbiterUnit", "ClockTree"],   # groups  -> list
            "links": ["https://x/"],                       # groups  -> list
            "source": "kestrel.docx",                      # map     -> str
            "title": {"a": 1},                             # scalar  -> dict
            "tags": "urgent"}                              # list    -> str
    result = lint_document(meta, _vocab())

    malformed = _of(result, "field-malformed")
    assert sorted(f.where for f in malformed) == [
        "meta.entities", "meta.links", "meta.relations", "meta.source",
        "meta.tags", "meta.title"]
    assert all(f.severity == ERROR for f in malformed)
    # The message has to say what was expected AND what arrived, or the report
    # names a field and leaves the author to guess which half is wrong.
    by_where = dict((f.where, f.message) for f in malformed)
    assert "list" in by_where["meta.relations"] and "str" in by_where["meta.relations"]
    assert "dict" in by_where["meta.source"] and "str" in by_where["meta.source"]
    assert result["errors"] == 6


def test_a_record_nothing_can_check_is_an_error_because_the_exit_code_reads_it():
    # `record-untyped` fires when a record omits the ONE sub-key a vocabulary
    # governs — `relations.p`, `decisions.status`, `risks.impact` — i.e. when a
    # record is present and nothing about it is checkable. It is the sole finding
    # for that shape: no other rule notices a relation with no predicate.
    #
    # Its SEVERITY is what scripts/kb_lint.py's exit code keys off (`failed =
    # bool(n_err) or ...`; warnings only bind under --strict), so ERROR->WARN
    # silently turns a corpus gate from exit 1 into exit 0. Assert the (code,
    # severity) PAIR, not the aggregate count: an unrelated error would keep a
    # count assertion true after exactly that downgrade.
    meta = {"relations": [{"s": "hostA", "o": "hostB", "ref": "#intro"}],
            "decisions": [{"what": "use nfs", "ref": "#intro"}],
            "risks": [{"what": "disk", "ref": "#intro"}]}
    # No anchors and no known_ids: ref integrity is skipped, so nothing else can
    # keep the error count up on this document.
    result = lint_document(meta, _vocab())

    untyped = _of(result, "record-untyped")
    assert sorted(f.where for f in untyped) == [
        "decisions[0].status", "relations[0].p", "risks[0].impact"]
    assert [f.severity for f in untyped] == [ERROR, ERROR, ERROR]
    assert result["errors"] == 3 and result["warnings"] == 0
    # And the message says WHY it cannot be checked, naming the vocabulary that
    # would have graded it.
    assert "relation_predicates" in dict(
        (f.where, f.message) for f in untyped)["relations[0].p"]


def test_a_closed_vocabulary_rejects_an_unknown_value_and_names_the_allowed_set():
    result = lint_document({"type": "runbookk"}, _vocab())

    unknown = _of(result, "vocab-unknown")
    assert len(unknown) == 1 and unknown[0].severity == ERROR
    assert unknown[0].where == "type"
    # The allowed set travels with the finding: an enum error is only actionable
    # if it tells you what you were allowed to say.
    for allowed in ("runbook", "policy", "design", "guide"):
        assert allowed in unknown[0].message
    assert result["errors"] == 1


def test_an_alias_is_a_warning_naming_the_canonical_term_not_a_rejection():
    # Every record carries the citation the schema requires, so the only findings
    # this document can produce are the two alias warnings under test.
    meta = {"relations": [{"s": "a", "p": "threatens", "o": "b", "ref": "#setup"}],
            "decisions": [{"id": "d1", "status": "assumed", "ref": "#setup"}]}
    result = lint_document(meta, _vocab())

    aliases = _of(result, "vocab-alias")
    assert sorted(f.where for f in aliases) == ["decisions[0].status",
                                                "relations[0].p"]
    assert all(f.severity == WARN for f in aliases)
    assert result["errors"] == 0

    by_where = dict((f.where, f.message) for f in aliases)
    assert "'targets'" in by_where["relations[0].p"]
    # The MADR collapse carries a qualifier with it; the warning says so, because
    # rewriting `assumed` to `accepted` alone would lose the nuance.
    assert "'accepted'" in by_where["decisions[0].status"]
    assert "assumption" in by_where["decisions[0].status"]


def test_a_registry_value_outside_the_registry_is_a_proposal_not_a_finding():
    # Registry drift is a CORPUS-level signal — promotion happens at N documents —
    # so one document may not be told it is wrong for coining a term.
    result = lint_document({"tags": ["linux", "kafka", "nfs"]}, _vocab())

    assert result["findings"] == []
    assert result["errors"] == 0 and result["warnings"] == 0
    assert result["proposals"] == {"tags": ["kafka", "nfs"]}


# ----------------------------------------------------------------- hygiene

def test_a_boolean_in_an_enum_field_is_a_type_error_not_a_membership_error():
    # `status: no` unquoted is retyped to False by any YAML 1.1 reader. Reporting
    # it as "not in the vocabulary" would send the author looking for a missing
    # term; the defect is upstream, in whatever wrote the block without quoting.
    result = lint_document({"decisions": [{"id": "d1", "status": False}]}, _vocab())

    coerced = _of(result, "type-coerced")
    assert len(coerced) == 1
    assert coerced[0].severity == ERROR
    assert coerced[0].where == "decisions[0].status"
    assert "boolean" in coerced[0].message
    assert _of(result, "vocab-unknown") == []


# --------------------------------------------------------------- cardinality

def test_facet_verdicts_grade_a_field_by_distinct_over_used():
    rows = facet_report([
        ("wide", ["v%d" % i for i in range(9)] + ["v0"]),   # 10 used, 9 distinct
        ("mid", ["a", "a", "a", "a", "b", "c", "d", "e", "f", "g"]),
        ("narrow", ["a"] * 7 + ["b"] * 2 + ["c"]),
        ("empty", [None, None]),
    ], warn=0.45, fail=0.75, min_uses=8)
    verdicts = dict((r.name, r.verdict) for r in rows)

    assert verdicts["wide"] == "note-not-facet"   # 0.90 > fail
    assert verdicts["mid"] == "thin"              # 0.45 < 0.70 <= fail
    assert verdicts["narrow"] == "ok"             # 0.30 <= warn
    # A field that stopped being populated stays visible rather than vanishing.
    assert verdicts["empty"] == "ok"
    assert [r.name for r in rows][0] == "wide"    # worst ratio first
    assert isinstance(rows[0], FacetRow)


def _rel(p, i=0):
    """A COMPLETE relation — every required sub-key — so a cardinality assertion is
    not quietly reading a required-key finding instead."""
    return {"s": "s%d" % i, "p": p, "o": "o%d" % i, "ref": "#setup"}


def test_a_field_with_too_few_values_is_sparse_and_never_an_error():
    # One relation is 1.00 distinct/used and says nothing whatever about the
    # field. Without this guard every short document fails the cardinality gate,
    # which would make the gate mean "this document is short".
    row = facet_report([("relations.p", ["a", "b", "c"])], min_uses=8)[0]
    assert row.ratio == 1.0 and row.verdict == "sparse"

    result = lint_document(
        {"relations": [_rel("runs_on", 0), _rel("requires", 1)]}, _vocab())
    assert _facet(result, "relations.p").verdict == "sparse"
    assert _of(result, "facet-not-a-facet") == []
    assert _of(result, "facet-thin") == []
    assert result["errors"] == 0


def test_a_field_that_is_not_a_facet_is_an_error_once_the_sample_can_carry_it():
    # The ERROR path, on the regime the ratio is a heuristic FOR: a field nobody
    # governs, where "one more invented value per document" is the failure being
    # detected. `terms=0` is how a pair says "ungoverned, or unbounded".
    rows = facet_report([("notes.tag", ["v%d" % i for i in range(4)], 0)],
                        warn=0.45, fail=0.75, min_uses=3)
    assert rows[0].verdict == "note-not-facet" and rows[0].ratio == 1.0

    thin = facet_report([("notes.tag", ["a", "b", "b", "c"], 0)],
                        warn=0.45, fail=0.75, min_uses=3)
    assert thin[0].verdict == "thin"


def test_a_closed_vocabulary_is_never_condemned_for_using_the_terms_it_was_given():
    # THE POINT OF THE SAMPLE FLOOR. `distinct` can never exceed the number of legal
    # spellings, so with five governed predicates and four relations the ratio is
    # pinned at 1.00 by arithmetic, not by evidence: the document used four different
    # CURATED terms and invented nothing. Grading that as "documentation, not a
    # facet" condemned a maximally-correct document, and the remedy the message
    # prescribes ("move it to an unindexed note") is impossible — `p` is a required
    # discriminator, so the only edit that clears the gate is deleting relations.
    vocab = _vocab(min_uses=3)
    rels = [_rel(p, i) for i, p in
            enumerate(("runs_on", "requires", "mitigates", "targets"))]
    result = lint_document(rels and {"relations": rels}, vocab, anchors=set(["setup"]))

    assert _facet(result, "relations.p").ratio == 1.0      # still REPORTED ...
    assert _facet(result, "relations.p").verdict == "sparse"   # ... never graded
    assert _of(result, "facet-not-a-facet") == []
    assert _of(result, "facet-thin") == []
    assert result["errors"] == 0 and result["warnings"] == 0

    # The floor is the vocabulary's size, not a constant: an ungoverned facet of the
    # same shape and the same min_uses is still condemned (see the test above), so
    # this is a correction to the RULE and not the gate being switched off.
    assert facet_report([("relations.p", [r["p"] for r in rels], 5)],
                        warn=0.45, fail=0.75, min_uses=3)[0].verdict == "sparse"


# ------------------------------------------------------------ integrity

def test_a_ref_is_resolved_against_the_documents_own_heading_anchors():
    meta = {"decisions": [{"id": "d1", "ref": "#setup"},
                          {"id": "d2", "ref": "#ghost"},
                          {"id": "d3", "ref": "setup"}]}
    result = lint_document(meta, _vocab(), anchors=set(["setup"]),
                           known_ids=set())

    unresolved = _of(result, "ref-unresolved")
    assert len(unresolved) == 1                      # d1 resolved, d2 did not
    assert unresolved[0].severity == ERROR
    assert unresolved[0].where == "meta.decisions[1].ref"

    malformed = _of(result, "ref-malformed")
    assert len(malformed) == 1 and malformed[0].severity == WARN


def test_see_also_resolves_through_wikilink_brackets_against_the_known_ids():
    meta = {"see_also": ["[[other-page]]", "missing-page"]}
    result = lint_document(meta, _vocab(), anchors=set(),
                           known_ids=set(["other-page"]))

    unresolved = _of(result, "see-also-unresolved")
    assert len(unresolved) == 1                      # the wikilink resolved
    assert unresolved[0].severity == WARN
    assert "missing-page" in unresolved[0].message


def test_an_unverifiable_pointer_is_skipped_rather_than_reported_as_verified():
    # anchors/known_ids default to None, meaning "the caller cannot tell us".
    # Treating that as a pass would let the linter certify pointers it never saw;
    # treating it as a failure would make every caller that lacks a corpus index
    # produce noise. It is neither — the check does not run.
    # The decision carries a `status` so this stays a test about POINTERS: a record
    # missing its required discriminator is a separate (real) finding.
    meta = {"decisions": [{"id": "d1", "status": "accepted", "ref": "#nowhere"}],
            "see_also": ["no-such-page"]}
    result = lint_document(meta, _vocab())

    assert _of(result, "ref-unresolved") == []
    assert _of(result, "see-also-unresolved") == []
    assert result["errors"] == 0


def test_a_control_may_name_an_entity_and_only_a_dangling_one_is_reported():
    # Measured on the worked example, 11 of 12 controls named a THING (a script,
    # a settings key) and exactly one named a decision id, so entity identities
    # resolve too. A rule the data violates nine times in ten is a broken rule.
    meta = {"entities": {"scripts": [{"name": "verify.sh"}]},
            "decisions": [{"id": "d1"}],
            "risks": [{"id": "r1", "control": "verify.sh", "protects": "d1"},
                      {"id": "r2", "control": "ghost-control"}]}
    result = lint_document(meta, _vocab())

    dangling = _of(result, "register-unresolved")
    assert [f.where for f in dangling] == ["meta.risks[1].control"]
    assert dangling[0].severity == WARN


# ------------------------------------------------------------ accountability

def test_a_generated_value_on_an_authored_only_field_is_an_error():
    # The safety gate. `confidentiality` exists so a confidential document is
    # never surfaced to the wrong audience; a model guessing it defeats the whole
    # field, whatever the guess happens to be.
    meta = {"confidentiality": "internal", "tags": ["linux"],
            "_provenance": {"confidentiality": {"source": "generated"},
                            "tags": {"source": "generated"},
                            "title": {"source": "psychic"}}}
    result = lint_document(meta, _vocab())

    authored = _of(result, "authored-only-generated")
    assert len(authored) == 1
    assert authored[0].severity == ERROR
    assert authored[0].where == "_provenance.confidentiality"

    # A generated value on a model-writable field is exactly what tier 2 is for.
    assert "tags" not in [f.where for f in authored]
    assert [f.where for f in _of(result, "provenance-unknown-source")] \
        == ["_provenance.title"]


# ------------------------------------------------------------ normalisation

def test_normalize_resolves_an_alias_and_merges_its_qualifiers_into_the_record():
    meta = {"decisions": [{"id": "d1", "status": "assumed"}]}
    out, changed = normalize_document(meta, _vocab())

    rec = out["decisions"][0]
    assert rec["status"] == "accepted"
    # The nuance survives as data instead of inflating the enum with a sixth
    # status that means "accepted, but nobody checked".
    assert rec["assumption"] is True
    assert ("decisions[0].status", "assumed", "accepted") in changed


def test_normalize_moves_unknown_registry_values_aside_instead_of_dropping_them():
    # Deleting an unknown tag would destroy the evidence the promotion rule runs
    # on — the term would have to be re-invented on every document before anyone
    # could see it was invented three times.
    meta = {"tags": ["linux", "rhel-8", "kafka"], "custom_block": {"a": 1}}
    out, changed = normalize_document(meta, _vocab())

    assert out["tags"] == ["linux"]
    # Sorted, not insertion-ordered: a proposals list is merged from two sides (the
    # values being normalised and any `_proposed` already on the document), so a
    # stable order is what keeps a re-run byte-identical. Aliases resolve en route.
    assert out["tags_proposed"] == ["kafka", "rhel8"]
    assert ("tags[1]", "rhel-8", "rhel8") in changed
    # Keys the schema does not own are passed through untouched.
    assert out["custom_block"] == {"a": 1}


# ------------------------------------------------------------ corpus health

def test_corpus_report_counts_documents_not_occurrences():
    docs = [{"tags": ["kafka"] * 30},
            {"tags": ["kafka", "linux"]},
            {"tags": ["linux"], "tags_proposed": ["kafka"]},
            {"tags": ["storage", "rhel-8"]}]
    report = corpus_report(docs, _vocab())["tags"]

    # Thirty mentions in one document is one document. Occurrence counting would
    # promote a term the moment a single author fell in love with it.
    assert report["proposed"]["kafka"] == 3
    assert report["proposed"]["rhel8"] == 1            # alias normalised first
    assert report["documents"] == 4
    assert report["terms_used"] == 2                   # linux, storage
    assert report["singletons"] == 1                   # storage, on one document
    assert report["singleton_rate"] == 0.5
    assert report["failing"] is True                   # 0.50 > singleton_rate_max


def test_corpus_report_promotes_a_proposal_only_at_the_document_threshold():
    docs = [{"tags_proposed": ["kafka"]},
            {"tags_proposed": ["kafka", "nfs"]},
            {"tags_proposed": ["kafka"]}]

    assert corpus_report(docs, _vocab(promote_at=3))["tags"]["promote"] == ["kafka"]
    assert corpus_report(docs, _vocab(promote_at=4))["tags"]["promote"] == []
    # nfs stays a proposal at one document, and stays VISIBLE as one.
    assert corpus_report(docs, _vocab())["tags"]["proposed"]["nfs"] == 1


def test_a_low_singleton_rate_is_not_flagged_as_failing():
    docs = [{"tags": ["linux", "storage"]}, {"tags": ["linux", "storage"]}]
    report = corpus_report(docs, _vocab())["tags"]

    assert report["singletons"] == 0
    assert report["singleton_rate"] == 0.0
    assert report["failing"] is False


# ------------------------------------------------------------ robustness

def test_lint_document_reports_a_malformed_block_instead_of_raising():
    # The linter's job is to report the corpus it has, not the corpus it wishes
    # it had: an exception here would stop a whole corpus walk on one bad file.
    meta = {"entities": {"hosts": "not a list"},
            "tags": "not a list",
            "decisions": [42],
            "see_also": [None, 5, ""],
            "not_a_schema_field": 1}
    result = lint_document(meta, _vocab(), anchors=set(), known_ids=set())

    assert all(isinstance(f, Finding) for f in result["findings"])
    codes = _codes(result)
    assert "field-malformed" in codes            # tags is not a list
    assert "entity-group-malformed" in codes     # entities.hosts is not a group
    assert "unknown-field" in codes
    assert result["errors"] >= 2

    empty = lint_document(None, _vocab())
    assert empty["findings"] == [] and empty["errors"] == 0
    assert empty["proposals"] == {}
