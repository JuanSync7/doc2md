"""
title: Unit — backend.kb corpus gates
kind: tests
layer: backend
summary: The checks one document cannot answer — identity collisions, synonym pollution, entity identity, graph integrity, schema skew, coverage and term-list hygiene.
"""
import pytest
from backend.kb import (ERROR, INFO, WARN, alias_suggestions, corpus_findings,
                        coverage_report, entity_report, graph_report,
                        identity_report, load_vocab, norm_key, skew_report,
                        strip_polarity, synonym_report, vocabulary_hygiene,
                        vocabulary_usage)

pytestmark = pytest.mark.unit


# A whole vocabulary, inline — no disk, so this stays a unit test. `corpus_min_docs`
# is 2 rather than the shipped 5 so the gates that are deliberately ungraded on a
# tiny sample are still observable here without building a twenty-document fixture.
_VOCAB = """
version: 1

lint:
  synonym_similarity: 0.78
  corpus_min_docs: 2
  coverage_thin: 0.25
  promote_at: 3
  singleton_rate_max: 0.40
  similar_ok:
    - review_cadence:annual|biannual

relation_predicates:
  governance: closed
  values: [runs_on, requires, mitigates, targets]

failure_modes:
  governance: closed
  values: [silent, delayed, overt, adversarial]

impact:
  governance: closed
  values: [critical, high, medium, low]

decision_status:
  governance: closed
  values: [proposed, rejected, accepted, deprecated, superseded]

document_types:
  governance: closed
  values: [runbook, policy, design, report]

document_status:
  governance: closed
  values: [draft, review, approved]

confidentiality:
  governance: closed
  values: [public, internal, confidential]

review_cadence:
  governance: closed
  values: [monthly, quarterly, biannual, annual, none]

lang:
  governance: closed
  values: [en-GB, en-US]

link_categories:
  governance: closed
  values: [product_docs, internal]

entity_types:
  governance: closed
  values: [Software, Package, Host, Person, Path]
  group_types:
    software: Software
    hosts: Host
    people: Person
    paths: Path

tags:
  governance: registry
  values: []
  aliases:
    rhel-8: rhel8

topics:
  governance: registry
  values: []

keywords:
  governance: registry
  values: []

audience:
  governance: registry
  values: []

subtype:
  governance: registry
  values: []
"""


@pytest.fixture
def vocab():
    return load_vocab(text=_VOCAB)


def doc(path, **meta):
    return {"path": path, "meta": meta}


def codes(result):
    """``{code: [finding]}`` — assertions read better keyed by what fired."""
    out = {}
    for f in result["findings"]:
        out.setdefault(f.code, []).append(f)
    return out


# --------------------------------------------------------------- normalisation

def test_norm_key_collapses_the_distinctions_that_are_not_distinctions():
    # Casing, separators and diacritics are spelling, not identity. A knowledge base
    # that keeps all four of these keeps four nodes where it needs one.
    assert norm_key("RHEL-8") == norm_key("rhel_8") == norm_key("rhel 8") == "rhel8"
    assert norm_key("Café") == norm_key("cafe") == "cafe"
    assert norm_key("  Docker  ") == "docker"
    # ... but it must not collapse things that really are different terms.
    assert norm_key("rhel8") != norm_key("rhel9")
    # A value with no alphanumerics has no identity key at all, and must not become
    # the empty key that everything else without one collides with.
    assert norm_key("---") == ""


def test_strip_polarity_groups_a_negation_with_its_predicate_but_never_rewrites_it():
    # `does_not_mitigate` is `mitigates` with `negated: true`, not a second edge type.
    assert strip_polarity("does_not_mitigate") == "mitigate"
    assert strip_polarity("cannot_run") == "run"
    assert strip_polarity("is_governed_by") == "governed_by"
    # Longest prefix wins, or `does_not_x` would strip to `does_not_x` minus `not_`.
    assert strip_polarity("does_not_x") == "x"
    # A bare prefix is a whole word, not a prefix — stripping it would leave nothing.
    assert strip_polarity("not_") == "not_"
    assert strip_polarity("requires") == "requires"


# --------------------------------------------------------------- identity

def test_two_documents_claiming_one_id_is_an_error_neither_document_could_report(vocab):
    # The defining shape of a corpus check: both documents are internally perfect.
    rows = [doc("a.md", id="alpha"), doc("b.md", id="alpha"), doc("c.md", id="beta")]
    found = codes(identity_report(rows))
    assert len(found["identity-collision"]) == 1
    f = found["identity-collision"][0]
    assert f.severity == ERROR
    assert sorted(f.detail) == ["a.md", "b.md"]     # names both, implicates neither
    assert "c.md" not in "".join(f.detail)


def test_uid_collides_independently_of_id(vocab):
    # Distinct human ids, one machine identity: the index keyed by uid still loses a
    # document, so checking `id` alone would pass a corpus that is already broken.
    rows = [doc("a.md", id="alpha", uid="ns/one"), doc("b.md", id="beta", uid="ns/one")]
    found = codes(identity_report(rows))
    assert [f.where for f in found["identity-collision"]] == ["uid=ns/one"]


def test_a_document_with_no_identity_is_reported_as_unlinkable(vocab):
    found = codes(identity_report([doc("a.md", title="orphan")]))
    assert found["identity-missing"][0].severity == WARN
    assert found["identity-missing"][0].where == "a.md"


def test_an_unquoted_numeric_id_still_collides_instead_of_escaping_the_gate(vocab):
    # `id: 42` is parsed as an int by any YAML reader, and `id` is an authored-wins
    # hand-editable field — a numeric document number is ordinary authoring, not
    # corruption. Skipping non-strings took both documents out of the check entirely,
    # so the gate went silent on exactly the case it exists for.
    found = codes(identity_report([doc("a.md", id=42), doc("b.md", id=42)]))
    assert found["identity-collision"][0].severity == ERROR
    assert sorted(found["identity-collision"][0].detail) == ["a.md", "b.md"]
    # ... and the shape problem is still reported, because a value that changes type
    # between readers is not a stable identity.
    assert len(found["identity-coerced"]) == 2
    assert found["identity-coerced"][0].severity == WARN


# --------------------------------------------------------------- synonymy

def test_one_term_spelled_two_ways_across_documents_is_an_error_with_an_alias(vocab):
    # Neither document is wrong. The corpus has two index entries for one concept.
    rows = [doc("a.md", tags_proposed=["fleet-upgrade"]),
            doc("b.md", tags_proposed=["Fleet_Upgrade"])]
    found = codes(synonym_report(rows, vocab))
    f = found["synonym-collision"][0]
    assert f.severity == ERROR
    assert "fleet-upgrade" in f.message           # the lowercase slug, not the shouty
    assert alias_suggestions(rows, vocab)["tags"] == {"Fleet_Upgrade": "fleet-upgrade"}


def test_document_frequency_outranks_spelling_convention_when_choosing_a_canonical(
        vocab):
    # The corpus has already voted. Convention is only a tie-break, so a term three
    # documents actually use wins over a prettier one used once.
    rows = [doc("a.md", tags_proposed=["Fleet_Upgrade"]),
            doc("b.md", tags_proposed=["Fleet_Upgrade"]),
            doc("c.md", tags_proposed=["fleet-upgrade"])]
    assert alias_suggestions(rows, vocab)["tags"] == {"fleet-upgrade": "Fleet_Upgrade"}


def test_a_declared_alias_never_reports_as_a_collision(vocab):
    # `rhel-8 -> rhel8` is already decided. Normalising BEFORE grouping is what stops
    # the corpus gate from re-litigating every alias the vocabulary declares; the
    # per-document gate is the one that reports the non-canonical spelling.
    rows = [doc("a.md", tags_proposed=["rhel-8"]), doc("b.md", tags_proposed=["rhel8"])]
    assert "synonym-collision" not in codes(synonym_report(rows, vocab))


def test_near_misses_are_a_warning_and_a_recorded_answer_silences_them(vocab):
    rows = [doc("a.md", tags_proposed=["kubernetes"]),
            doc("b.md", tags_proposed=["kubernets"])]
    found = codes(synonym_report(rows, vocab))
    f = found["synonym-similar"][0]
    assert f.severity == WARN                     # only a human can call this one
    assert "similar_ok" in f.message

    # The message names the exact entry to paste, and pasting it must work.
    text = _VOCAB.replace("    - review_cadence:annual|biannual",
                          "    - review_cadence:annual|biannual\n"
                          "    - tags:kubernetes|kubernets")
    assert "synonym-similar" not in codes(synonym_report(rows, load_vocab(text=text)))


def test_proposals_are_graded_because_that_is_where_drift_actually_lives(vocab):
    # A term still waiting for promotion is the least governed value in the corpus.
    # Grading only the promoted field would look at exactly the wrong half.
    rows = [doc("a.md", tags=["shipped"], tags_proposed=["fleet-upgrade"]),
            doc("b.md", tags_proposed=["FLEET-UPGRADE"])]
    assert codes(synonym_report(rows, vocab))["synonym-collision"]


def test_one_document_repeating_a_term_is_still_one_document(vocab):
    # Document frequency, not occurrence count — otherwise a single chatty document
    # elects the canonical spelling for the whole corpus.
    rows = [doc("a.md", tags_proposed=["Wide_Term", "Wide_Term", "Wide_Term"]),
            doc("b.md", tags_proposed=["wide-term"]),
            doc("c.md", tags_proposed=["wide-term"])]
    assert alias_suggestions(rows, vocab)["tags"] == {"Wide_Term": "wide-term"}


def test_an_enumerated_family_is_not_a_synonym_family(vocab):
    # `node-01`..`node-40` are mutually ~95% similar and every pair would be a
    # warning. A corpus naming 1200 hosts produced 375,000 of them, which buries
    # every real finding. A differing NUMBER is what makes these distinct, so it is
    # evidence against synonymy rather than for it.
    rows = [doc("d%02d.md" % i, entities={"hosts": [{"fqdn": "node-%02d" % i}]})
            for i in range(40)]
    assert "entity-similar" not in codes(entity_report(rows, vocab))


def test_a_real_typo_survives_every_filter_that_kills_the_families(vocab):
    # The filters have to be precise, not merely aggressive: the same corpus that
    # produces the noise above is where the one real misspelling has to be found.
    rows = [doc("d%02d.md" % i, entities={"hosts": [{"fqdn": "node-%02d" % i}]})
            for i in range(40)]
    rows += [doc("x.md", entities={"software": [{"name": "kubernetes-controller"}]}),
             doc("y.md", entities={"software": [{"name": "kubernets-controller"}]})]
    f = codes(entity_report(rows, vocab))["entity-similar"][0]
    assert "kubernets-controller" in f.message and "kubernetes-controller" in f.message


def test_the_length_filter_never_discards_a_pair_that_would_have_cleared(vocab):
    # The length window is derived from ratio = 2M/(la+lb) with M <= min(la, lb), so
    # it is SOUND — unlike the bucketing, it can only drop pairs that could never
    # have reached the threshold. A filter that silently dropped real pairs would
    # make every clean report meaningless.
    from difflib import SequenceMatcher
    from backend.kb._corpus import _similar_pairs
    names = ["kubernetes", "kubernets", "kubernete", "docker", "dockr", "podman",
             "ab", "abcdefghijklmnop", "prometheus", "promethus", "grafana"]
    got = set(frozenset([a, b]) for a, b, _r in _similar_pairs(names, 0.78, set(), "x")[0])
    brute = set(frozenset([a, b]) for i, a in enumerate(names) for b in names[i + 1:]
                if SequenceMatcher(None, a, b).ratio() >= 0.78)
    assert got == brute


def test_a_wall_of_similar_pairs_is_counted_rather_than_printed(vocab):
    # Past a couple of dozen the list has stopped being a work queue. Truncating
    # without saying so would read as "this is all of them".
    # 26 terms differing by one letter: every pair clears the threshold, and none is
    # an enumerated family, so the filters above cannot help — only the cap can.
    rows = [doc("d%03d.md" % i, tags_proposed=["alpha" + chr(97 + i) + "-topic-name"])
            for i in range(26)]
    found = codes(synonym_report(rows, vocab))
    assert len(found["synonym-similar"]) == 25              # the cap, exactly
    assert len(found["synonym-similar-truncated"]) == 1     # and it says so
    assert "not listed" in found["synonym-similar-truncated"][0].message


# --------------------------------------------------------------- entities

def test_one_entity_spelled_two_ways_is_two_nodes_in_the_graph(vocab):
    rows = [doc("a.md", entities={"software": [{"name": "Docker", "type": "Software"}]}),
            doc("b.md", entities={"software": [{"name": "docker", "type": "Software"}]})]
    found = codes(entity_report(rows, vocab))
    f = found["entity-collision"][0]
    assert f.severity == ERROR
    assert "'Docker'" in f.message                # proper nouns keep their capital


def test_entity_type_conflicts_are_detected_on_the_identity_not_the_spelling(vocab):
    # Keyed on the spelling, `Docker`/`docker` are two untroubled entries and the
    # conflict is invisible — the check would be disarmed by the very collision it
    # sits next to.
    rows = [doc("a.md", entities={"software": [{"name": "Docker", "type": "Software"}]}),
            doc("b.md", entities={"software": [{"name": "docker", "type": "Package"}]})]
    found = codes(entity_report(rows, vocab))
    f = found["entity-type-conflict"][0]
    assert f.severity == WARN                     # two things may share a label
    assert sorted(f.detail) == ["Package in b.md", "Software in a.md"]


def test_reuse_is_measured_per_identity_so_a_collision_cannot_deflate_it(vocab):
    # Counted per spelling this corpus scores 0% reuse, and the "is this a graph?"
    # metric would be destroyed by the exact defect reported one line above it.
    rows = [doc("a.md", entities={"software": [{"name": "Docker"}]}),
            doc("b.md", entities={"software": [{"name": "docker"}]})]
    res = entity_report(rows, vocab)
    assert res["metrics"]["distinct"] == 1        # one entity ...
    assert res["metrics"]["spellings"] == 2       # ... written two ways
    assert res["metrics"]["shared"] == 1
    assert "1/1 entities appear in more than one document" in \
        codes(res)["entity-reuse"][0].message


def test_an_entity_group_takes_its_type_from_the_group_and_a_scalar_group_still_counts(
        vocab):
    # Both shapes appear in real data. A mapping group is the one a naive walk skips
    # entirely, and its keys are node names too.
    rows = [doc("a.md", entities={"hosts": [{"fqdn": "build01"}],
                                  "paths": {"/etc/doc2md": "config root"}})]
    res = entity_report(rows, vocab)
    assert res["metrics"]["distinct"] == 2


def test_a_recorded_pair_clears_a_collision_that_is_genuinely_two_things(vocab):
    # Two real paths differing only in punctuation collapse to one identity key. With
    # no way to record that, a false collision would fail every build forever with
    # nothing a human could do about it.
    rows = [doc("a.md", entities={"paths": [{"path": "/etc/a-b", "type": "Path"}]}),
            doc("b.md", entities={"paths": [{"path": "/etc/a_b", "type": "Path"}]})]
    assert codes(entity_report(rows, vocab))["entity-collision"]
    text = _VOCAB.replace("    - review_cadence:annual|biannual",
                          "    - review_cadence:annual|biannual\n"
                          "    - entities:/etc/a-b|/etc/a_b")
    cleared = load_vocab(text=text)
    assert "entity-collision" not in codes(entity_report(rows, cleared))

    # ... and it clears THAT PAIR, not the identity key. Both spellings normalise to
    # `etcab`, so storing the normalised pair would collapse it to a one-element set
    # that matches any two spellings of the key — silently whitelisting every future
    # collision on it, which is the opposite of recording one decision.
    other = [doc("c.md", entities={"paths": [{"path": "/etc/A.B", "type": "Path"}]}),
             doc("d.md", entities={"paths": [{"path": "/etc/a b", "type": "Path"}]})]
    assert codes(entity_report(other, cleared))["entity-collision"]


def test_an_entity_with_no_name_is_reported_rather_than_dropped(vocab):
    # A node with no identity can never collide, be reused, or be an endpoint.
    # Dropping it makes the corpus look CLEANER for containing it.
    rows = [doc("a.md", entities={"software": [{"vendor": "acme"}]})]
    f = codes(entity_report(rows, vocab))["entity-nameless"][0]
    assert f.severity == WARN
    assert f.detail == ("a.md entities.software[0]",)


def test_a_retyped_entity_type_does_not_fall_back_to_the_group(vocab):
    # `_lint` makes a retyped scalar an ERROR and explicitly refuses the group
    # fallback. Falling back here would have the two walks disagree about one
    # document: the per-document gate reporting a broken type while the corpus gate
    # quietly invented a working one.
    rows = [doc("a.md", entities={"hosts": [{"fqdn": "web01", "type": True}]})]
    assert entity_report(rows, vocab)["metrics"]["type_conflicts"] == 0
    assert "Host" in vocabulary_usage(rows, vocab)["metrics"]["entity_types"]["unused"]


# --------------------------------------------------------------- graph

def test_see_also_is_dead_only_against_the_whole_corpus(vocab):
    rows = [doc("a.md", id="alpha", see_also=["beta", "ghost"]), doc("b.md", id="beta")]
    f = codes(graph_report(rows, vocab))["see-also-dangling"][0]
    assert f.severity == WARN
    assert any("ghost" in d for d in f.detail)
    assert not any("beta <-" in d for d in f.detail)


def test_wikilink_brackets_are_stripped_before_resolution(vocab):
    rows = [doc("a.md", id="alpha", see_also=["[[beta]]"]), doc("b.md", id="beta")]
    assert "see-also-dangling" not in codes(graph_report(rows, vocab))


def test_relation_endpoints_are_measured_as_a_rate_not_a_finding_per_edge(vocab):
    # An edge naming nothing declared is an edge to nowhere, but one finding per edge
    # would bury every other result on a real corpus. The rate is the actionable form.
    rows = [doc("a.md", entities={"software": [{"name": "docker"}]},
                relations=[{"s": "docker", "p": "requires", "o": "containerd"}])]
    f = codes(graph_report(rows, vocab))["relation-endpoints"][0]
    assert f.severity == INFO
    assert "1/2 relation endpoints (50%)" in f.message
    assert "containerd x1" in f.detail


def test_an_endpoint_naming_nothing_is_unresolved_and_a_missing_one_is_reported(
        vocab):
    # Two ways to credit a corpus for a link it does not have. An endpoint with no
    # identity key (`---`) names nothing an entity could ever match, and a relation
    # with only one end is not an edge at all — dropping it from the count lets a
    # corpus of half-written relations report a perfect endpoint rate.
    rows = [doc("a.md", entities={"software": [{"name": "docker"}]},
                relations=[{"s": "docker", "p": "requires"},
                           {"s": "---", "p": "requires", "o": "docker"}])]
    res = graph_report(rows, vocab)
    found = codes(res)
    assert found["relation-incomplete"][0].severity == WARN
    assert found["relation-incomplete"][0].detail == ("a.md relations[0].o",)
    assert res["metrics"]["relation_endpoints"] == 3
    assert res["metrics"]["unresolved_endpoints"] == 1     # the `---`, not counted OK
    assert res["metrics"]["incomplete_endpoints"] == 1


def test_orphans_are_reported_never_failed(vocab):
    # A reference page with no inbound links is normal, not broken.
    rows = [doc("a.md", id="alpha"), doc("b.md", id="beta", see_also=["alpha"])]
    f = codes(graph_report(rows, vocab))["graph-orphans"][0]
    assert f.severity == INFO
    assert f.detail == ("b.md",)


def test_orphans_are_counted_per_document_not_per_identity(vocab):
    # A document is normally linked by its `id` alone, so counting identity STRINGS
    # makes every `uid` in the corpus a guaranteed orphan — this fully-connected pair
    # would report 50%, and a real corpus's orphan rate would never fall below it.
    rows = [doc("a.md", id="alpha", uid="ns/alpha", see_also=["beta"]),
            doc("b.md", id="beta", uid="ns/beta", see_also=["alpha"])]
    assert "graph-orphans" not in codes(graph_report(rows, vocab))


# --------------------------------------------------------------- schema skew

def test_skew_names_the_backfill_work_list_and_picks_the_version_numerically(vocab):
    # String ordering would call 10 older than 9, which is how a backfill list comes
    # out exactly backwards.
    rows = [doc("a.md", schema_version=9), doc("b.md", schema_version=10),
            doc("c.md", schema_version=10)]
    res = skew_report(rows)
    assert res["metrics"]["schema_version"]["current"] == "10"
    f = codes(res)["schema-skew"][0]
    assert f.severity == WARN
    assert f.detail == ("a.md (9)",)


def test_the_two_versions_are_tracked_separately_because_they_invalidate_different_work(
        vocab):
    # schema_version moving means the field INVENTORY changed; vocab_version moving
    # invalidates classifications and leaves every other field untouched.
    rows = [doc("a.md", schema_version=1, vocab_version=1),
            doc("b.md", schema_version=1, vocab_version=2)]
    m = skew_report(rows)["metrics"]
    assert m["schema_version"]["behind"] == 0
    assert m["vocab_version"]["behind"] == 1


def test_an_entirely_unstamped_corpus_gets_one_finding_that_implicates_no_document():
    # With nothing stamped there is no current version to be behind, so listing every
    # document would implicate each one for a property the corpus as a whole lacks.
    rows = [doc("a.md", id="alpha"), doc("b.md", id="beta")]
    f = codes(skew_report(rows))["schema-unstamped"][0]
    assert f.detail == ()
    assert "cannot be backfilled selectively at all" in f.message


def test_a_half_migrated_corpus_names_the_unstamped_documents():
    rows = [doc("a.md", schema_version=2), doc("b.md", id="beta")]
    f = [x for x in codes(skew_report(rows))["schema-unstamped"]
         if x.where == "schema_version"][0]
    assert f.detail == ("b.md",)
    assert "`!= 2`" in f.message


def test_a_blank_version_is_one_state_not_two():
    # Both loops must use one predicate. Treating blank as unstamped when counting
    # and as a version when comparing put the document in BOTH lists — reported once
    # as missing a version and once as behind one, the second time with an empty
    # parenthesis where the evidence should be.
    rows = [doc("a.md", schema_version=1), doc("b.md", schema_version="")]
    found = codes(skew_report(rows))
    assert "schema-skew" not in found
    assert [x for x in found["schema-unstamped"]
            if x.where == "schema_version"][0].detail == ("b.md",)


# --------------------------------------------------------------- coverage

def test_a_thinly_populated_model_field_is_a_question_about_the_extractor(vocab):
    rows = [doc("a.md", abstract="x", tags=["t"])] + [doc("%d.md" % i) for i in range(9)]
    f = codes(coverage_report(rows, vocab))["coverage-thin"][0]
    assert f.severity == WARN
    assert any(d.startswith("tags: 1/10") for d in f.detail)


def test_an_authored_only_field_being_rare_is_a_fact_about_the_organisation(vocab):
    # ... not about the pipeline, so it is counted and never warned about.
    rows = [doc("a.md", owner="platform")] + [doc("%d.md" % i) for i in range(9)]
    res = coverage_report(rows, vocab)
    assert res["metrics"]["present"]["owner"] == 1
    assert "coverage-thin" not in codes(res)


def test_an_empty_value_counts_as_absent_not_populated(vocab):
    rows = [doc("a.md", tags=[]), doc("b.md", tags=["real"])]
    assert coverage_report(rows, vocab)["metrics"]["present"]["tags"] == 1


def test_a_field_populated_on_no_document_is_not_silent(vocab):
    # The strongest extractor failure there is, and the one the thin test cannot see:
    # a field on NO document has no rate to fall under a threshold. The mirror gate
    # (`vocab-unused`) defers to this one, so without a zero branch both stay quiet
    # and the field disappears from the report — a dead extractor rendering exactly
    # like a clean corpus. A field on 1/10 warns; 0/10 must not be quieter.
    rows = [doc("d%d.md" % i, id="x%d" % i, type="runbook") for i in range(10)]
    f = codes(coverage_report(rows, vocab))["coverage-absent"][0]
    assert "relations" in f.detail
    assert "19 model-written field(s)" in f.message   # named, and counted in full

    # The contrast that makes it matter: one document populating `risks` warns, so a
    # corpus where NOTHING populates it must not be the quieter of the two.
    rows[0]["meta"]["risks"] = [{"impact": "high"}]
    found = codes(coverage_report(rows, vocab))
    assert any(d.startswith("risks: 1/10") for d in found["coverage-thin"][0].detail)
    assert "risks" not in found["coverage-absent"][0].detail


def test_a_registry_field_still_awaiting_promotion_counts_as_populated(vocab):
    # The normal state of a young corpus: every term starts in `<field>_proposed` and
    # only promotion moves it. Reading the field alone reports the extractor as dead
    # precisely while it is working.
    rows = [doc("d%d.md" % i, id="x%d" % i, tags_proposed=["fresh"]) for i in range(10)]
    res = coverage_report(rows, vocab)
    assert res["metrics"]["present"]["tags"] == 10
    absent = [d for f in res["findings"] if f.code == "coverage-absent"
              for d in f.detail]
    assert "tags" not in absent          # ... and so it is not reported as dead


# --------------------------------------------------------------- the term list

def test_a_closed_vocabulary_carrying_its_own_synonyms_is_an_error(vocab):
    # Unfindable by grading documents: every value is legal by construction, so the
    # corpus splits cleanly across two edge types and looks perfect doing it.
    text = _VOCAB.replace("values: [runs_on, requires, mitigates, targets]",
                          "values: [runs_on, requires, mitigates, "
                          "does_not_mitigate, targets]")
    f = codes(vocabulary_hygiene(load_vocab(text=text)))["vocab-synonym"][0]
    assert f.severity == ERROR
    assert sorted(f.detail) == ["does_not_mitigate", "mitigates"]


def test_similarity_in_a_curated_list_is_only_a_question(vocab):
    # A human put both terms there. `annual`/`biannual` is a real distinction.
    text = _VOCAB.replace("    - review_cadence:annual|biannual\n", "")
    found = codes(vocabulary_hygiene(load_vocab(text=text)))
    assert [f.severity for f in found["vocab-similar"]] == [INFO]
    # ... and recording the answer is what stops it being asked again.
    assert "vocab-similar" not in codes(vocabulary_hygiene(vocab))


def test_the_shipped_vocabulary_is_clean():
    # The gate is worthless if the term list it ships with cannot pass it.
    res = vocabulary_hygiene(load_vocab())
    assert [f for f in res["findings"] if f.severity == ERROR] == []
    assert [f for f in res["findings"] if f.severity == WARN] == []


def test_dead_terms_are_reported_but_an_unpopulated_field_is_not(vocab):
    # A term nobody uses invites a model to pick it. But ALL terms unused just means
    # the field is not populated yet, which `coverage-absent` reports directly and by
    # field name — reporting it here too would bury the case that matters. That
    # deferral is only sound because coverage_report HAS a zero branch; see
    # test_a_field_populated_on_no_document_is_not_silent.
    rows = [doc("a.md", type="runbook"), doc("b.md", type="runbook")]
    found = codes(vocabulary_usage(rows, vocab))
    dead = [f for f in found["vocab-unused"] if f.where == "document_types"]
    assert dead and dead[0].severity == INFO
    assert sorted(dead[0].detail) == ["design", "policy", "report"]
    assert [f for f in found["vocab-unused"] if f.where == "impact"] == []


def test_usage_counts_a_value_reached_through_a_record_or_an_entity_group(vocab):
    # A term used only inside `risks[].impact` or implied by an entity group is used.
    # Counting only top-level scalars would report most of the vocabulary as dead.
    rows = [doc("a.md", risks=[{"impact": "high"}],
                entities={"hosts": [{"fqdn": "build01"}]})]
    m = vocabulary_usage(rows, vocab)["metrics"]
    assert "high" not in m["impact"]["unused"]
    assert "Host" not in m["entity_types"]["unused"]


# --------------------------------------------------------------- the umbrella

def test_corpus_findings_never_raises_on_a_malformed_row(vocab):
    res = corpus_findings([doc("a.md", id="alpha"), {"path": "b.md", "meta": "junk"},
                           "not a row"], vocab)
    bad = [f for f in res["findings"] if f.code == "corpus-row-malformed"]
    # ONE finding per bad row, not one per gate. `docs` is parsed once and the rows
    # handed to every gate; letting each gate parse it again made a single malformed
    # document produce seven identical errors, so one bad file read as seven.
    assert len(bad) == 2                          # reported, never silently dropped
    assert res["errors"] == 2


def test_a_plain_path_meta_pair_is_accepted_exactly_like_the_dict_shape(vocab):
    # The public docstring promises both shapes, and the umbrella relies on it: it
    # parses `docs` once and passes the resulting tuples down to every gate.
    as_dicts = corpus_findings([doc("a.md", id="dup"), doc("b.md", id="dup")], vocab)
    as_pairs = corpus_findings([("a.md", {"id": "dup"}), ("b.md", {"id": "dup"})],
                               vocab)
    assert [tuple(f) for f in as_dicts["findings"]] == \
        [tuple(f) for f in as_pairs["findings"]]


def test_the_term_list_is_graded_even_with_no_corpus_at_all(vocab):
    # The most valuable moment to hear about a polluted vocabulary is before it has
    # been written into four hundred documents.
    text = _VOCAB.replace("values: [runs_on, requires, mitigates, targets]",
                          "values: [runs_on, requires, mitigates, "
                          "does_not_mitigate, targets]")
    res = corpus_findings([], load_vocab(text=text))
    assert [f.code for f in res["findings"]] == ["vocab-synonym"]


def test_a_partial_walk_skips_exactly_the_checks_that_would_invert(vocab):
    # A subset can only MISS a collision, so identity/synonym/entity stay honest. The
    # other four would report defects created by the truncation: a see_also whose
    # target was excluded, a "current" version sitting in a skipped document, a rate
    # over the wrong denominator, a term used only by a document nobody walked.
    rows = [doc("a.md", id="alpha", see_also=["beta"], schema_version=1),
            doc("b.md", id="alpha")]
    res = corpus_findings(rows, vocab, partial=True)
    assert res["skipped"] == ["graph", "skew", "coverage", "vocab_usage"]
    found = codes(res)
    assert found["identity-collision"]            # still reported: cannot be false
    assert "see-also-dangling" not in found       # would be an artefact of the cut
    assert "schema-unstamped" not in found
    assert found["corpus-partial"][0].severity == INFO


def test_findings_are_ordered_by_severity_so_the_first_line_is_the_worst_one(vocab):
    rows = [doc("a.md", id="alpha", tags_proposed=["Fleet_Upgrade"]),
            doc("b.md", id="alpha", tags_proposed=["fleet-upgrade"])]
    sev = [f.severity for f in corpus_findings(rows, vocab)["findings"]]
    assert sev == sorted(sev, key=lambda s: {ERROR: 0, WARN: 1, INFO: 2}[s])


def test_evidence_lists_say_how_much_they_truncated(vocab):
    # A detail list silently cut to twelve reads as "twelve documents affected".
    rows = [doc("d%02d.md" % i, id="same") for i in range(30)]
    f = [x for x in identity_report(rows)["findings"]
         if x.code == "identity-collision"][0]
    assert len(f.detail) == 13
    assert f.detail[-1] == "... and 18 more"
