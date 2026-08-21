"""
title: Unit — backend.kb tiered field inventory and tier-1 derivations
kind: tests
layer: backend
summary: Who may write which field (tier + authored_only), which vocabulary governs it, and the fixed-rule derivations — slug, anchor, uid, counts, keyword seeds.
"""
import pytest
from collections import OrderedDict
from backend.kb import (FIELDS, KNOWLEDGE_FILE, PROVENANCE_KEY, REF_FIELDS,
                        SCHEMA_VERSION, SOURCE_AUTHORED,
                        SOURCE_DERIVED, SOURCE_EXTRACTED, SOURCE_GENERATED,
                        TIER_NAMES, VALUE_SOURCES, WORDS_PER_MINUTE,
                        body_anchors, derive_uid, field, field_names,
                        group_vocab, heading_anchor, in_knowledge,
                        keyword_candidates, knowledge_field_names,
                        load_vocab, merge_meta, model_writable, proposed_key,
                        record_vocab, reading_time_minutes, slugify, split_meta,
                        word_count)

pytestmark = pytest.mark.unit

# The five kinds the inventory documents. A sixth kind appearing without the
# linter learning about it is how a field silently stops being validated.
KINDS = frozenset(["scalar", "list", "map", "records", "groups"])


# ---------------------------------------------------------------- the inventory

def test_every_field_declares_a_known_tier_and_kind_and_a_unique_name():
    names = [f.name for f in FIELDS]
    assert len(names) == len(set(names))
    assert field_names() == names          # public order is the declaration order
    for f in FIELDS:
        assert f.tier in TIER_NAMES, f.name
        assert f.kind in KINDS, "%s has kind %r" % (f.name, f.kind)
    assert set(TIER_NAMES) == set([0, 1, 2])
    assert SCHEMA_VERSION >= 1


def test_field_lookup_raises_keyerror_for_an_unknown_name():
    assert field("tags").tier == 2
    assert field("uid").tier == 0
    with pytest.raises(KeyError):
        field("no_such_field")


def test_the_four_value_sources_are_distinct_and_authored_is_one_of_them():
    # `_provenance` records which of these wrote a value, and the whole
    # "authored always wins" rule is a comparison against SOURCE_AUTHORED — so
    # the constants must stay distinct strings, not aliases of each other.
    assert len(set(VALUE_SOURCES)) == 4
    assert SOURCE_AUTHORED in VALUE_SOURCES
    for s in (SOURCE_EXTRACTED, SOURCE_DERIVED, SOURCE_GENERATED):
        assert s in VALUE_SOURCES
        assert s != SOURCE_AUTHORED


def test_model_writable_is_true_only_for_tier_two_that_is_not_an_accountability_boundary():
    # WHY this is asserted field-by-field rather than only as an invariant: a
    # field whose entire purpose is a safety boundary cannot have a model as its
    # author. Retiering `owner` or `confidentiality` to something a model may
    # write would still satisfy the generic rule below while destroying the
    # guarantee, so the boundary list is pinned by name.
    for name in ("owner", "confidentiality", "status", "classification",
                 "accountable_roles", "review_cadence", "last_reviewed",
                 "next_review_due", "validated_against_version"):
        assert field(name).authored_only is True, name
        assert model_writable(name) is False, name

    for name in ("type", "tags", "abstract", "relations"):
        assert model_writable(name) is True, name

    # Deterministic and derived fields are nobody's guess either.
    for name in ("uid", "source", "slug", "word_count"):
        assert model_writable(name) is False, name

    # ...and the generic rule holds across the whole inventory.
    for f in FIELDS:
        assert model_writable(f.name) is (f.tier == 2 and not f.authored_only), f.name

    # An unknown name is not writable; it is not an error either, because the
    # enrichment pass asks this about whatever a model returned.
    assert model_writable("hallucinated_field") is False


def test_every_declared_vocab_names_a_vocabulary_that_ships_in_the_config():
    # This one test deliberately reads the shipped config/vocab.yaml through
    # load_vocab(): the claim under test is precisely that the inventory and the
    # SHIPPED term list agree, which a synthetic fixture cannot demonstrate. It
    # is the only disk touch in this file.
    vocab = load_vocab()
    nodes = dict(vocab.fields())
    shipped = set(nodes)
    for f in FIELDS:
        if f.vocab:
            assert vocab.has(f.vocab), "field %s names missing vocab %s" % (f.name, f.vocab)
    for table in (record_vocab, group_vocab):
        for f in FIELDS:
            for _sub, vname in table(f.name).items():
                assert vname in shipped, "%s qualifier names missing vocab %s" % (f.name, vname)
    # The ref-governed fields are listed in both places; a drift here means a
    # pointer field is validated by nothing at all.
    assert set(REF_FIELDS) == set(nodes["refs"]["fields"])


def test_record_vocab_binds_the_relation_qualifier_as_well_as_the_predicate():
    # Nuance was deliberately pushed OUT of the predicate enum and INTO the
    # qualifiers, so a table that binds `p` but not `mode` re-opens exactly the
    # hole the design closed: an unchecked qualifier is an uncontrolled vocabulary.
    rel = record_vocab("relations")
    assert rel["p"] == "relation_predicates"
    assert rel["mode"] == "failure_modes"
    assert record_vocab("risks")["mode"] == "failure_modes"
    assert record_vocab("decisions")["status"] == "decision_status"
    assert record_vocab("open_questions") == {}      # no qualifier vocabulary
    # A `groups` field governs its group NAMES and its members separately.
    assert group_vocab("entities")["member_type"] == "entity_types"
    assert group_vocab("links")["group_name"] == "link_categories"


def test_proposed_values_land_in_a_sidecar_key_and_never_in_the_field():
    # Registry promotion (>= 3 documents) only means something if a proposal is
    # NOT already sitting in the field it wants to join.
    assert proposed_key("tags") == "tags_proposed"
    assert proposed_key("keywords") == "keywords_proposed"
    declared = set(field_names())
    for name in declared:
        assert proposed_key(name) not in declared, name


# ------------------------------------------------------------ tier-1 derivation

def test_slugify_folds_unicode_so_two_spellings_of_one_title_share_one_id():
    # Two encodings of the same title must not become two documents.
    assert slugify(u"Caf\xe9 R\xe9sum\xe9 Notes") == slugify("Cafe Resume Notes")
    assert slugify(u"Caf\xe9 R\xe9sum\xe9 Notes") == "cafe-resume-notes"
    # Decomposed (e + combining acute) and precomposed spellings of the same
    # character agree too - the two byte sequences a source may carry.
    assert slugify(u"Cafe\u0301") == slugify(u"Caf\xe9") == "cafe"


def test_slugify_collapses_punctuation_strips_edges_and_truncates_cleanly():
    assert slugify("  ---Hello,,, World!!!  ") == "hello-world"
    assert slugify("MiXeD CaSe") == "mixed-case"
    assert slugify("") == ""
    # A truncation that lands on a separator must not leave a dangling hyphen,
    # which would make two near-identical titles slug to `x-` and `x`.
    truncated = slugify(("alpha " * 20).strip(), max_len=18)
    assert truncated == "alpha-alpha-alpha"
    assert not truncated.endswith("-")


def test_heading_anchor_follows_the_renderer_rule_not_the_slug_rule():
    # A `ref` is checked against what a markdown renderer actually emits, so the
    # rule has to be the renderer's: punctuation is DROPPED, not turned into a
    # separator, which is why `7.3` joins to `73` and `claude.ai` to `claudeai`.
    # If this ever collapsed back into slugify(), every numbered-heading ref in
    # the corpus would become a dead link.
    heading = "7.3 Standing rule: keep the claude.ai console empty"
    assert heading_anchor(heading) == "73-standing-rule-keep-the-claudeai-console-empty"
    assert slugify(heading) != heading_anchor(heading)
    assert slugify(heading) == "7-3-standing-rule-keep-the-claude-ai-console-empty"
    assert heading_anchor("  Multiple   spaces  ") == "multiple-spaces"
    assert heading_anchor("") == ""


def test_body_anchors_finds_every_atx_level_and_ignores_everything_else():
    body = (
        "# Overview\n"
        "\n"
        "Prose that mentions a # hash mid-line.\n"
        "\n"
        "## Level Two ##\n"
        "### Level Three\n"
        "#### Level Four\n"
        "##### Level Five\n"
        "###### Level Six\n"
        "####### Seven Is Not A Heading\n"
        "#NoSpaceIsNotAHeading\n"
    )
    assert body_anchors(body) == set([
        "overview", "level-two", "level-three", "level-four",
        "level-five", "level-six",
    ])
    assert body_anchors("") == set()


def test_derive_uid_is_path_shaped_slugified_per_segment_and_namespaced():
    assert derive_uid("docs/specs/My Doc.v2.docx", "kb") == "kb/docs/specs/my-doc-v2"
    # Only the final extension is dropped; the rest of the stem survives.
    assert derive_uid("docs/specs/My Doc.v2.docx") == "docs/specs/my-doc-v2"
    assert derive_uid("a\\b\\Report.md") == "a/b/report"
    assert derive_uid("/leading/and/trailing/") == "leading/and/trailing"
    # A missing path must degrade, not explode — uid is derived during ingest,
    # where a source without a relative path is a routing bug, not a crash.
    assert derive_uid("") == ""
    assert derive_uid("", "Kb Ns") == "kb-ns"


def test_word_count_counts_prose_words_with_markdown_stripped():
    body = (
        "# Release Notes\n"
        "\n"
        "The **installer** now writes `0644` files.\n"
        "\n"
        "| flag | default |\n"
        "|---|---|\n"
        "| fast | 1 |\n"
    )
    # Release Notes The installer now writes files flag default fast == 10.
    # Neither the bare numbers (0644, 1) nor the table rule row are words, so
    # they cannot inflate the reading time computed from this figure.
    assert word_count(body) == 10
    assert word_count("") == 0
    assert word_count(None) == 0


def test_reading_time_is_never_zero_for_a_non_empty_document():
    # Rounding a very short document to 0 minutes reads as "no content" in a
    # facet report, which is a different claim from "a one-minute read".
    assert reading_time_minutes(0) == 0
    assert reading_time_minutes(1) == 1
    assert reading_time_minutes(WORDS_PER_MINUTE) == 1
    assert reading_time_minutes(2 * WORDS_PER_MINUTE) == 2
    assert reading_time_minutes(100, wpm=10) == 10
    words = word_count("Alpha beta gamma.")
    assert words > 0 and reading_time_minutes(words) >= 1


def test_keyword_candidates_are_identifier_like_and_respect_the_limit():
    body = "Set the max_workers knob, then call getUserName from camelCase code."
    got = keyword_candidates(body)
    assert set(got) == set(["max_workers", "getUserName", "camelCase"])
    # Ordinary prose is not a keyword candidate — only symbols are.
    for word in ("Set", "knob", "code"):
        assert word not in got
    assert keyword_candidates(body, limit=2) == got[:2]
    assert keyword_candidates(body, limit=0) == got      # 0 means "no limit"
    assert keyword_candidates("") == []


# ------------------------------------------------ the descriptor/knowledge seam

def test_the_seam_is_mechanical_and_carves_out_the_accountability_block():
    # A field moves to knowledge.json iff it is a records/groups field and is not
    # authored_only. Mechanical so it can never drift into a matter of taste — and
    # the carve-out is the point: `accountable_roles` IS a record list, but it is a
    # statement about who stands behind the document, so it belongs with it.
    for f in FIELDS:
        expected = f.kind in ("records", "groups") and not f.authored_only
        assert in_knowledge(f.name) is expected, f.name
    assert set(knowledge_field_names()) == {
        "entities", "relations", "decisions", "risks", "open_questions", "links"}
    assert not in_knowledge("accountable_roles")     # authored: stays with the doc
    assert not in_knowledge("tags")                  # a list, and it routes retrieval
    assert not in_knowledge("see_also")


def test_a_proposals_slot_follows_its_field_across_the_seam():
    # Splitting a `<field>_proposed` list away from its field would put the promotion
    # EVIDENCE in a different file from the values it is evidence about.
    assert in_knowledge(proposed_key("entities")) is True
    assert in_knowledge(proposed_key("tags")) is False


def test_split_then_merge_is_lossless_including_provenance():
    meta = OrderedDict([
        ("id", "alpha"), ("title", "Alpha"), ("tags", ["t"]),
        ("accountable_roles", [{"role": "owner", "name": "platform"}]),
        ("entities", {"software": [{"name": "docker"}]}),
        ("relations", [{"s": "docker", "p": "runs_on", "o": "host"}]),
        ("some_future_key", 7),
        (PROVENANCE_KEY, OrderedDict([
            ("title", {"source": "generated"}),
            ("relations", {"source": "generated"}),
        ])),
    ])
    front, know = split_meta(meta)

    assert sorted(know) == ["_provenance", "entities", "relations"]
    assert "accountable_roles" in front
    # An unknown key stays in front matter: this function must never be the thing
    # that silently drops a field the schema has not heard of yet.
    assert front["some_future_key"] == 7
    # Each file records the origin of what IT holds, so neither is a fragment that
    # has to be joined to another file before it can be read.
    assert list(know[PROVENANCE_KEY]) == ["relations"]
    assert list(front[PROVENANCE_KEY]) == ["title"]

    # Round-tripping guarantees the CONTENT, not the key order — canonical order is
    # `order_meta`'s job and the write path applies it after merging.
    back = merge_meta(front, know)
    assert set(back) == set(meta)
    for key in meta:
        if key == PROVENANCE_KEY:
            assert dict(back[key]) == dict(meta[key])
        else:
            assert back[key] == meta[key]


def test_front_matter_wins_a_straight_collision():
    # document.md is the file a person edits, so when the two disagree the
    # hand-edited side is the one to believe.
    merged = merge_meta({"relations": "from-front"}, {"relations": "from-sidecar"})
    assert merged["relations"] == "from-front"


def test_the_split_is_total_on_the_malformed_shapes_the_linter_tolerates():
    # `_lint._check_authorship` REPORTS a non-mapping `_provenance` rather than
    # rejecting the document, so a corpus really can be in that state — and the split
    # must not be the thing that crashes the enricher on it. (Found by fuzzing the
    # round trip: `split_meta` called `.items()` on it and raised AttributeError.)
    front, know = split_meta({"_provenance": [1, 2], "relations": []})
    assert front[PROVENANCE_KEY] == [1, 2]        # kept where the linter will find it
    assert merge_meta(front, know)[PROVENANCE_KEY] == [1, 2]

    # A key literally named `_provenance` INSIDE a field value is data, not metadata.
    _f, k = split_meta({"relations": [{"_provenance": "decoy"}]})
    assert k["relations"] == [{"_provenance": "decoy"}]

    # An empty provenance normalises to absent — "no provenance recorded" and "an
    # empty provenance record" are the same statement, and `order_meta` already
    # drops it on the write path.
    assert PROVENANCE_KEY not in merge_meta(*split_meta({"id": "a",
                                                         PROVENANCE_KEY: {}}))


def test_merging_an_absent_sidecar_is_the_identity():
    front = OrderedDict([("id", "alpha"), ("tags", ["t"])])
    assert dict(merge_meta(front, {})) == dict(front)
    assert dict(merge_meta(front, None)) == dict(front)


def test_the_schema_version_records_that_the_seam_moved():
    # Moving fields between files is a schema change: a v1 bundle keeps everything in
    # front matter, so a reader has to be able to tell which layout it is looking at.
    assert SCHEMA_VERSION >= 2
    assert KNOWLEDGE_FILE == "knowledge.json"
