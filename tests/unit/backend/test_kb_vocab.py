"""
title: Unit — backend.kb Vocabulary loader
kind: tests
layer: backend
summary: Governance regimes, alias normalisation with qualifiers, load-time consistency gates, broader chains, group types, maps_to fallback and vocab_path precedence.
"""
import os

import pytest
from backend.kb import Vocabulary, VocabularyError, load_vocab, vocab_path

pytestmark = pytest.mark.unit


# One small vocabulary carrying all three governance regimes. Built as a STRING
# and handed to load_vocab(text=...) so this stays a unit test: the loader's only
# disk touch is the open() that `text` short-circuits.
VOCAB = """
version: 3

standards:
  stix: https://example.invalid/stix

lint:
  promote_at: 3

relation_predicates:
  governance: closed
  maps_to: local
  values: [runs_on, mitigates, targets]
  value_maps_to:
    targets: stix:targets
  aliases:
    threatens: targets

decision_status:
  governance: closed
  values: [proposed, accepted, rejected]
  aliases:
    assumed: accepted
  alias_qualifiers:
    assumed:
      assumption: true

entity_types:
  governance: closed
  values: [Host, Person]
  group_types:
    hosts: Host
    people: Person

tags:
  governance: registry
  maps_to: skos:Concept
  values: []
  aliases:
    rocky8: rocky-linux-8
  broader:
    rocky-linux-8: rhel8
    rhel8: linux

refs:
  governance: ref
  fields: [ref, backs]
"""


def _v():
    return load_vocab(text=VOCAB)


# ------------------------------------------------------------------ inventory

def test_each_governance_regime_reports_its_own_values_and_aliases():
    v = _v()
    assert v.governance("relation_predicates") == "closed"
    assert v.governance("tags") == "registry"
    assert v.governance("refs") == "ref"
    assert v.values("relation_predicates") == ["runs_on", "mitigates", "targets"]
    # A registry field starts empty on purpose — its terms arrive by promotion.
    assert v.values("tags") == []
    # A ref field carries no term list at all; referential integrity governs it.
    assert v.values("refs") == []
    assert v.aliases("relation_predicates") == {"threatens": "targets"}
    assert v.aliases("refs") == {}
    assert v.version == 3


def test_only_governed_blocks_are_fields_and_an_unknown_field_raises():
    # `version`/`standards`/`lint` describe the FILE, not a governed field. If they
    # answered has() the linter would try to validate values against them.
    v = _v()
    assert v.has("relation_predicates") and v.has("tags") and v.has("refs")
    assert not v.has("version")
    assert not v.has("standards")
    assert not v.has("nope")
    with pytest.raises(VocabularyError):
        v.governance("nope")
    with pytest.raises(VocabularyError):
        v.values("standards")


def test_values_and_aliases_hand_back_copies_a_caller_cannot_corrupt():
    # The vocabulary is a shared singleton in a lint run; one caller appending to
    # a returned list would silently widen a closed enum for every later document.
    v = _v()
    v.values("relation_predicates").append("smuggled")
    v.aliases("relation_predicates")["smuggled"] = "targets"
    assert v.values("relation_predicates") == ["runs_on", "mitigates", "targets"]
    assert v.aliases("relation_predicates") == {"threatens": "targets"}


# ------------------------------------------------------------------ normalize

def test_a_plain_value_normalises_to_itself_with_no_qualifiers():
    assert _v().normalize("relation_predicates", "mitigates") == ("mitigates", {})


def test_an_unknown_value_comes_back_unchanged_rather_than_being_dropped():
    # normalize() does not judge — reporting the unknown term is the linter's job,
    # and it can only report what it still has.
    assert _v().normalize("relation_predicates", "helps_mitigate") == \
        ("helps_mitigate", {})


def test_an_alias_resolves_to_its_canonical_term():
    assert _v().normalize("relation_predicates", "threatens") == ("targets", {})


def test_an_alias_may_carry_qualifiers_so_nuance_survives_without_inflating_the_enum():
    v = _v()
    canonical, quals = v.normalize("decision_status", "assumed")
    assert canonical == "accepted"
    assert quals == {"assumption": True}
    # The qualifiers are a fresh dict: a caller that annotates them further must
    # not write those annotations back into the vocabulary for every other document.
    quals["assumption"] = "edited"
    assert v.normalize("decision_status", "assumed")[1] == {"assumption": True}


def test_a_non_string_value_passes_through_normalize_untouched():
    # A YAML 1.1 reader turns a bare `no` into False; that bad value must survive
    # to the linter as-is instead of exploding in the alias lookup.
    v = _v()
    assert v.normalize("decision_status", False) == (False, {})
    assert v.normalize("decision_status", None) == (None, {})
    assert v.normalize("decision_status", 7) == (7, {})


# ------------------------------------------------------------------ is_allowed

def test_membership_is_enforced_only_for_closed_fields():
    v = _v()
    assert v.is_allowed("relation_predicates", "runs_on")
    assert not v.is_allowed("relation_predicates", "helps_mitigate")
    assert not v.is_allowed("relation_predicates", False)
    # Registry and ref fields allow anything HERE — the registry promotes terms and
    # referential integrity resolves refs; a fixed list would reject every new term.
    assert v.is_allowed("tags", "brand-new-tag")
    assert v.is_allowed("refs", "anything at all")


def test_an_alias_is_allowed_in_a_closed_field_because_membership_is_tested_after_normalisation():
    # This is the whole reason membership lives here: if a call site compared the
    # raw value to values(), "threatens" would be rejected instead of collapsed.
    assert _v().is_allowed("relation_predicates", "threatens")


# ------------------------------------------------------- load-time validation

ROTTEN = [
    # An unknown regime means nothing enforces the field at all.
    ("""
impact:
  governance: open
  values: [high, low]
""", "governance"),
    # A closed field with no values normalises every document's value to invalid.
    ("""
impact:
  governance: closed
  values: []
""", "declares no values"),
    ("""
impact:
  governance: closed
  values: [high, low, high]
""", "repeats value"),
    # The classic post-edit rot: the alias silently normalises to nothing.
    ("""
impact:
  governance: closed
  values: [high, low]
  aliases:
    severe: critical
""", "not a value"),
    # An alias that is also a value makes the term's meaning depend on lookup order.
    ("""
impact:
  governance: closed
  values: [high, low]
  aliases:
    low: high
""", "also a value"),
    ("""
impact:
  governance: closed
  values: [high, low]
  value_maps_to:
    critical: iso31000:critical
""", "maps unknown value"),
]


@pytest.mark.parametrize("text,fragment", ROTTEN,
                         ids=["bad-regime", "closed-empty", "repeated-value",
                              "alias-target-missing", "alias-is-a-value",
                              "map-of-unknown-value"])
def test_a_self_inconsistent_vocabulary_is_rejected_at_load_time_not_at_use_time(
        text, fragment):
    # Failing at load is the point: a rotten vocabulary does not raise when it is
    # used, it normalises to nothing and the corpus quietly grows two spellings.
    with pytest.raises(VocabularyError) as exc:
        load_vocab(text=text)
    assert fragment in str(exc.value)


def test_a_healthy_vocabulary_loads_and_a_bare_one_is_not_an_error():
    assert isinstance(_v(), Vocabulary)
    assert load_vocab(text="").fields() == []


# ------------------------------------------------------------------ hierarchy

def test_broader_returns_the_skos_chain_nearest_first():
    # A search for `linux` must reach the Rocky pages without anyone tagging them
    # three times, so the ORDER (nearest first) is the contract, not just the set.
    v = _v()
    assert v.broader("rocky-linux-8") == ["rhel8", "linux"]
    assert v.broader("rhel8") == ["linux"]
    assert v.broader("linux") == []
    assert v.broader("never-seen") == []


def test_broader_terminates_on_a_cycle_instead_of_hanging():
    # A mis-edited hierarchy must not wedge a whole corpus scan.
    v = load_vocab(text="""
tags:
  governance: registry
  values: []
  broader:
    a: b
    b: a
""")
    assert v.broader("a") == ["b"]
    assert v.broader("b") == ["a"]


# ---------------------------------------------------------------- group types

def test_group_type_maps_an_entity_group_to_its_implied_type():
    # Without this, every entry in a group that omits `type` is silently unchecked.
    v = _v()
    assert v.group_type("hosts") == "Host"
    assert v.group_type("people") == "Person"
    assert v.group_type("gadgets") == ""


# ------------------------------------------------------------------- maps_to

def test_maps_to_prefers_the_value_mapping_falls_back_to_the_field_and_never_guesses():
    v = _v()
    assert v.maps_to("relation_predicates", "targets") == "stix:targets"
    # A value with no mapping of its own inherits the field's standard.
    assert v.maps_to("relation_predicates", "runs_on") == "local"
    assert v.maps_to("relation_predicates") == "local"
    # Nothing maps: "" — a guessed RDF term is worse than no term.
    assert v.maps_to("decision_status") == ""
    assert v.maps_to("decision_status", "accepted") == ""


# ----------------------------------------------------------------- vocab_path

def test_the_env_var_beats_both_files_and_a_blank_one_is_ignored(monkeypatch):
    # $DOC2MD_VOCAB is how a deployment points at its own term list; it has to win
    # even when a local override file is sitting right there in config/.
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    assert vocab_path(repo_root="/repo",
                      env={"DOC2MD_VOCAB": "/elsewhere/terms.yaml"}) == \
        "/elsewhere/terms.yaml"
    # A blank/whitespace setting is not a path — fall through rather than return "".
    assert vocab_path(repo_root="/repo", env={"DOC2MD_VOCAB": "   "}) == \
        os.path.join("/repo", "config", "vocab.local.yaml")


def test_a_local_override_file_beats_the_committed_default(monkeypatch):
    local = os.path.join("/repo", "config", "vocab.local.yaml")
    monkeypatch.setattr(os.path, "isfile", lambda p: p == local)
    assert vocab_path(repo_root="/repo", env={}) == local
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    assert vocab_path(repo_root="/repo", env={}) == \
        os.path.join("/repo", "config", "vocab.yaml")
