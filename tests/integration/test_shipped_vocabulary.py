"""
title: Integration — the shipped config/vocab.yaml still says what was decided
kind: tests
layer: backend
summary: Regression guard on the committed term list and the shipped docs — loadable, in-subset, MADR collapse, STIX rename, mapped to standards, registries empty, no YAML 1.1 retyping, no dangling binding, no stale schema_version in the docs.
"""
# Integration (not unit): reads the real config/vocab.yaml off disk. Everything
# here is a claim about THE FILE THAT SHIPS, not about the loader — the loader's
# own behaviour is covered by the unit tests, which feed it text.
import os

import pytest

from backend.ingest import parse_block
from backend.kb import (FIELDS, KNOWLEDGE_FILE, REF_FIELDS, SCHEMA_VERSION,
                        group_vocab, load_vocab, proposed_key,
                        record_vocab, vocab_path, VocabularyError)

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIPPED = os.path.join(REPO, "config", "vocab.yaml")
LOCAL_OVERRIDE = os.path.join(REPO, "config", "vocab.local.yaml")

# The registries that must ship empty, and the entity groups the schema expects.
REGISTRY_FIELDS = ("subtype", "audience", "topics", "tags", "keywords")
EXPECTED_GROUPS = ("organisations", "people", "software", "hosts", "paths",
                   "scripts", "hooks", "settings", "identifiers", "standards",
                   "metrics")

# Tokens a YAML 1.1 reader retypes as booleans. Our own parser is 1.2 (only
# true/false), so an unquoted `no` here would round-trip fine through this
# codec and silently become False the day anything else reads the file.
YAML_11_BOOLS = frozenset(["y", "yes", "n", "no", "true", "false", "on", "off"])


def _raw():
    with open(SHIPPED, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def raw():
    return _raw()


@pytest.fixture(scope="module")
def shipped():
    # Explicit path, never vocab_path(): a developer's $DOC2MD_VOCAB or a
    # config/vocab.local.yaml must not decide what these assertions grade.
    return load_vocab(SHIPPED)


def _prefix(term):
    return term.split(":")[0] if ":" in term else term


def test_the_shipped_vocabulary_loads_with_no_arguments_and_self_validates(monkeypatch):
    # vocab_path() resolves $DOC2MD_VOCAB > config/vocab.local.yaml > the committed
    # file. Both of the first two are deployment overrides; this test is about the
    # committed default, so clear the env one and stand down if a local file exists.
    monkeypatch.delenv("DOC2MD_VOCAB", raising=False)
    if os.path.isfile(LOCAL_OVERRIDE):
        pytest.skip("a local vocabulary override shadows the shipped file")

    assert vocab_path() == SHIPPED
    v = load_vocab()
    assert v.version == 2
    assert len(v.fields()) >= 15
    for name, _node in v.fields():
        assert v.governance(name) in ("closed", "registry", "ref"), name

    # The load above is only reassuring if the self-check has teeth, so prove it:
    # delete `accepted` from decision_status and the three aliases that resolve to
    # it become dangling — the exact way a term list rots after a hand edit.
    rotten = _raw().replace(
        "values: [proposed, rejected, accepted, deprecated, superseded]",
        "values: [proposed, rejected, deprecated, superseded]")
    assert rotten != _raw()
    with pytest.raises(VocabularyError) as exc:
        load_vocab(text=rotten)
    assert "accepted" in str(exc.value)


def test_the_file_stays_inside_the_strict_yaml_subset_every_consumer_parses_with(raw, shipped):
    # There is no PyYAML on the 3.6 host: backend.ingest.parse_block IS the reader.
    # A hand edit that reaches for an anchor, a flow map or a second document would
    # take the whole metadata layer down, so parse the raw bytes here directly.
    data = parse_block(raw)
    assert data["version"] == 2
    assert list(data.keys())[:3] == ["version", "standards", "lint"]
    # `demoted` is documentation (a deleted field and why), not a governed field.
    assert "demoted" in data
    assert not shipped.has("demoted")
    assert not shipped.has("standards")

    from_text = load_vocab(text=raw)
    assert [n for n, _ in from_text.fields()] == [n for n, _ in shipped.fields()]


def test_decision_status_collapsed_to_the_five_madr_values_with_the_rest_as_aliases(shipped):
    # File order is the order request_spec hands the model, so pin the list itself.
    assert shipped.values("decision_status") == [
        "proposed", "rejected", "accepted", "deprecated", "superseded"]
    assert shipped.maps_to("decision_status") == "madr:status"

    collapsed = ("recommended", "standing_rule", "assumed", "provisional")
    for term in collapsed:
        assert term not in shipped.values("decision_status")
        assert shipped.is_allowed("decision_status", term), term

    assert shipped.normalize("decision_status", "recommended") == ("proposed", {})
    assert shipped.normalize("decision_status", "standing_rule") == ("accepted", {})
    # The nuance the collapse removed from the enum survives as data on the alias:
    # "assumed" is an accepted decision nobody ratified, "provisional" one that owes
    # a review. Losing the qualifier would make the collapse lossy.
    assert shipped.normalize("decision_status", "assumed") == (
        "accepted", {"assumption": True})
    assert shipped.normalize("decision_status", "provisional") == (
        "accepted", {"review_required": True})

    # Same token, different vocabulary, different verdict — which is why membership
    # is asked per field and never against a bare list at a call site. `recommended`
    # is a decision that nobody has ratified; as a TAG it would be an ordinary
    # registry proposal, and the two answers must not come from one list.
    assert shipped.governance("tags") == "registry"
    assert "recommended" not in shipped.values("tags")


def test_the_stix_rename_landed_so_targets_is_the_predicate_and_threatens_an_alias(shipped):
    preds = shipped.values("relation_predicates")
    assert "targets" in preds
    assert "threatens" not in preds
    assert shipped.aliases("relation_predicates") == {"threatens": "targets"}
    assert shipped.normalize("relation_predicates", "threatens") == ("targets", {})
    assert shipped.is_allowed("relation_predicates", "threatens")

    assert "mitigates" in preds
    assert shipped.maps_to("relation_predicates", "targets") == "stix:targets"
    assert shipped.maps_to("relation_predicates", "mitigates") == "stix:mitigates"
    assert "oasis-open.org" in shipped.standards["stix"]


def test_every_governed_field_maps_to_a_declared_standard_or_to_local(shipped):
    # "Reuse before inventing" is only auditable if every field says what it reused;
    # an empty maps_to is an unreviewed field, and a prefix missing from `standards`
    # is a mapping that resolves to nothing on the RDF export path.
    declared = set(shipped.standards)
    assert "local" in declared
    for name, _node in shipped.fields():
        term = shipped.maps_to(name)
        assert term, "governed field %r declares no maps_to" % name
        assert _prefix(term) in declared, "%s -> %s" % (name, term)
        for value in shipped.values(name):
            vterm = shipped.maps_to(name, value)
            assert _prefix(vterm) in declared, "%s/%s -> %s" % (name, value, vterm)

    # Every prefix carried in the schema's own field table resolves here too.
    for f in FIELDS:
        assert _prefix(f.maps_to) in declared, "%s -> %s" % (f.name, f.maps_to)


def test_registry_fields_ship_empty_so_terms_arrive_by_promotion_not_by_guess(shipped):
    # A registry admits a term when >= promote_at documents use it. Seeding one here
    # would be guessing the corpus shape at document zero — the exact failure the
    # regime exists to avoid — and it would do so invisibly, because a seeded term
    # is accepted straight into the field instead of landing in <field>_proposed.
    for name in REGISTRY_FIELDS:
        assert shipped.governance(name) == "registry", name
        assert shipped.values(name) == [], name
        assert proposed_key(name) == name + "_proposed"
    assert int(shipped.threshold("promote_at")) >= 1

    # Closed fields are the contrast: they must be non-empty (the loader enforces it).
    closed = [n for n, _ in shipped.fields() if shipped.governance(n) == "closed"]
    assert closed
    for name in closed:
        assert shipped.values(name), name

    # tags ships alias + broader tables while its value list is empty. That is not a
    # contradiction: those are normalisation rules waiting for their term to be
    # promoted, and alias targets are only required to exist for CLOSED fields.
    assert shipped.aliases("tags")
    assert shipped.broader("rocky-linux-8") == ["rhel8", "linux"]


def test_enum_values_are_strings_that_no_yaml_reader_will_retype(shipped, raw):
    # `none` is the live instance: a cadence of "none" is a decision, and a reader
    # that retyped it to null would erase it rather than record it.
    assert "none" in shipped.values("review_cadence")
    for name, _node in shipped.fields():
        for value in shipped.values(name):
            # bool before int: a retyped `no` arrives as False and then compares
            # equal to nothing a document will ever contain.
            assert not isinstance(value, bool), "%s: %r" % (name, value)
            assert isinstance(value, str), "%s: %r" % (name, value)
            if value.lower() in YAML_11_BOOLS:
                quoted = '"%s"' % value in raw or "'%s'" % value in raw
                assert quoted, "%s: %r must stay quoted in the file" % (name, value)


def test_entity_group_types_cover_every_expected_group_and_name_legal_types(shipped):
    # A group missing from this table is not an error at lint time — it is a WARN
    # and then silence: every member that omits `type` goes unchecked. So the set is
    # pinned here rather than left to whoever next adds a group.
    groups = dict(shipped.fields())["entity_types"].get("group_types") or {}
    assert set(groups) == set(EXPECTED_GROUPS)
    for name, etype in groups.items():
        assert shipped.group_type(name) == etype
        assert shipped.is_allowed("entity_types", etype), "%s -> %s" % (name, etype)

    # The three types no group implies must be written out on the entity itself.
    assert set(groups.values()) | set(["Role", "OperatingSystem", "Package"]) == \
        set(shipped.values("entity_types"))
    assert shipped.group_type("no_such_group") == ""


def test_every_vocabulary_the_schema_binds_to_exists_in_the_shipped_file(shipped):
    # A field bound to a vocabulary that is not in the file is checked against
    # nothing at all — the binding fails open, so it has to be checked here.
    for f in FIELDS:
        if f.vocab:
            assert shipped.has(f.vocab), "%s -> %s" % (f.name, f.vocab)
        for sub, vname in record_vocab(f.name).items():
            assert shipped.has(vname), "%s[].%s -> %s" % (f.name, sub, vname)
        for slot, vname in group_vocab(f.name).items():
            assert shipped.has(vname), "%s(%s) -> %s" % (f.name, slot, vname)

    # The relation qualifier `mode` points at a vocabulary by name from inside the
    # file; it must agree with the schema's own binding for a relation record.
    mode = shipped.qualifiers("relation_predicates")["mode"]
    assert shipped.has(mode["ref"])
    assert record_vocab("relations")["mode"] == mode["ref"]

    # The ref-governed fields are listed in two places and must not drift apart.
    assert tuple(dict(shipped.fields())["refs"]["fields"]) == REF_FIELDS


def test_the_lint_thresholds_are_present_and_ordered(shipped):
    warn = float(shipped.threshold("facet_warn"))
    fail = float(shipped.threshold("facet_fail"))
    # warn < fail or the WARN band is empty and a drifting facet goes straight from
    # clean to ERROR with no chance to notice.
    assert 0.0 < warn < fail <= 1.0
    assert int(shipped.threshold("promote_at")) >= 1
    assert int(shipped.threshold("facet_min_uses")) >= 1
    assert 0.0 < float(shipped.threshold("singleton_rate_max")) <= 1.0
    assert shipped.threshold("no_such_threshold", "fallback") == "fallback"


def test_the_docs_do_not_claim_a_schema_version_the_code_no_longer_writes():
    # Doc drift after a schema bump is invisible until somebody copies a stale example
    # into a real bundle. Every `schema_version` a doc SHOWS must be the one the code
    # actually stamps — the reviewer found three stale ones after the v1 -> v2 split.
    import re
    stale = []
    for rel in ("docs/design/output-contract.md", "docs/design/document-metadata.md",
                "src/backend/kb/README.md", "README.md"):
        path = os.path.join(REPO, rel)
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                # Inline code spans are CITATIONS — "a v1 block keeping
                # `schema_version: 1` makes the migration undetectable" is prose about
                # history, not a template anybody would copy. A bare line in an
                # example block is the thing that gets copied, so only those are
                # graded.
                bare = re.sub(r"`[^`]*`", "", line)
                for m in re.finditer(r'schema_version"?:\s*(\d+)', bare):
                    if int(m.group(1)) != SCHEMA_VERSION:
                        stale.append("%s:%d %s" % (rel, n, line.strip()))
    assert stale == [], "docs show a schema_version the code does not write:\n" + \
        "\n".join(stale)


def test_the_bundle_contract_docs_name_the_knowledge_sidecar():
    # The split added a file to the bundle. A contract doc that does not list it
    # leaves a consumer discovering the sidecar by accident, or not at all.
    for rel in ("docs/design/output-contract.md", "docs/design/document-metadata.md",
                "src/backend/kb/README.md"):
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            assert KNOWLEDGE_FILE in fh.read(), rel
