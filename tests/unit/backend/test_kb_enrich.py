"""
title: Unit — backend.kb enrichment acceptance policy
kind: tests
layer: backend
summary: What a model may contribute — constrain at generation, unknown is legal, authored always wins; plus revalidation on a vocabulary bump and the coverage counts.
"""
import copy
from collections import OrderedDict

import pytest
from backend.kb import (FIELDS, PROVENANCE_KEY, SOURCE_AUTHORED, SOURCE_DERIVED,
                        SOURCE_EXTRACTED, SOURCE_GENERATED, UNKNOWN,
                        abstract_floor, accept_model_meta, field_names,
                        harvested_links, is_authored, load_vocab, meta_coverage,
                        next_review_due, order_meta, proposed_key, record_source,
                        request_spec, revalidate_generated, set_provenance,
                        source_url, title_floor, value_sha, value_source)

pytestmark = pytest.mark.unit


# A miniature of config/vocab.yaml carrying one example of each governance regime.
# Built inline so the policy is tested against a vocabulary the test controls, not
# against whatever the committed term list happens to say this week.
VOCAB_V1 = """
version: 1
lint:
  promote_at: 3

document_types:
  governance: closed
  values: [runbook, policy, design]
  aliases:
    playbook: runbook

lang:
  governance: closed
  values: [en-GB, en-US]

relation_predicates:
  governance: closed
  values: [runs_on, mitigates, targets]
  aliases:
    threatens: targets

failure_modes:
  governance: closed
  values: [silent, overt]

decision_status:
  governance: closed
  values: [proposed, rejected, accepted]
  aliases:
    assumed: accepted
  alias_qualifiers:
    assumed:
      assumption: true

confidentiality:
  governance: closed
  values: [public, internal]

tags:
  governance: registry
  values: [linux, aws]
  aliases:
    rhel-8: rhel8

topics:
  governance: registry
  values: []
"""

# The same vocabulary after an edit: `design` is gone from the closed document
# types, `en-GB` is gone from the closed languages, and `aws` was pulled back out
# of the tags registry.
VOCAB_V2 = """
version: 2
lint:
  promote_at: 3

document_types:
  governance: closed
  values: [runbook, policy]
  aliases:
    playbook: runbook

lang:
  governance: closed
  values: [en-US]

tags:
  governance: registry
  values: [linux]
"""


@pytest.fixture
def vocab():
    return load_vocab(text=VOCAB_V1)


@pytest.fixture
def bumped():
    return load_vocab(text=VOCAB_V2)


def _authored(*names):
    prov = {}
    for n in names:
        prov[n] = {"tier": 2, "source": SOURCE_AUTHORED}
    return {PROVENANCE_KEY: prov}


def _reasons(result):
    return dict((name, reason) for name, _value, reason in result["rejected"])


# --------------------------------------------------------------- request_spec


def test_request_spec_asks_for_every_model_writable_field_and_never_an_authored_only_one(vocab):
    spec = request_spec(vocab)
    authored_only = [f.name for f in FIELDS if f.authored_only]
    tier_0_1 = [f.name for f in FIELDS if f.tier < 2]

    assert list(spec.keys()) == [f.name for f in FIELDS
                                 if f.tier == 2 and not f.authored_only]
    # The accountability boundary is enforced by never ASKING. A field the model is
    # not shown cannot be answered wrongly, refused noisily, or argued about later.
    for name in authored_only + tier_0_1:
        assert name not in spec
    for name in ("confidentiality", "owner", "status", "review_cadence",
                 "last_reviewed", "next_review_due"):
        assert name in authored_only and name not in spec


def test_request_spec_carries_the_exact_closed_enum_plus_unknown_and_opens_registry_fields(vocab):
    spec = request_spec(vocab)

    # Constrain at generation: the model SELECTS from these terms rather than
    # writing prose into a string field. UNKNOWN rides along on every enum because
    # a model with no escape hatch is a model forced to guess.
    assert spec["type"]["governance"] == "closed"
    assert spec["type"]["values"] == ["runbook", "policy", "design", UNKNOWN]
    assert spec["type"]["kind"] == "scalar"
    assert spec["type"]["tier"] == 2
    assert "new_values_allowed" not in spec["type"]

    assert spec["tags"]["governance"] == "registry"
    assert spec["tags"]["values"] == ["linux", "aws", UNKNOWN]
    assert spec["tags"]["new_values_allowed"] is True
    assert "proposal" in spec["tags"]["new_values_note"]

    # A field no vocabulary governs is offered without an enum at all.
    assert "values" not in spec["abstract"]
    assert "governance" not in spec["abstract"]


def test_request_spec_binds_the_vocabulary_of_every_record_subfield(vocab):
    spec = request_spec(vocab)

    # A relation's predicate is checked everywhere; without this binding its `mode`
    # qualifier — where the nuance was deliberately pushed — would go unconstrained.
    assert spec["relations"]["record_fields"] == {
        "p": ["runs_on", "mitigates", "targets", UNKNOWN],
        "mode": ["silent", "overt", UNKNOWN],
    }
    assert spec["decisions"]["record_fields"] == {
        "status": ["proposed", "rejected", "accepted", UNKNOWN],
    }


# ------------------------------------------------- rule 1: constrain, then check


def test_a_value_outside_a_closed_vocabulary_is_rejected_and_never_stored(vocab):
    # Rule 1. A closed vocabulary is a structural promise: `type` is one of three
    # things. A fourth invented term is not a smaller mistake than a missing value,
    # it is a corpus that no longer partitions — so it is refused, not recorded.
    out = accept_model_meta({"type": "novel", "lang": "Klingon"}, vocab)

    assert out["accepted"] == {}
    assert out["proposals"] == {}
    assert out["provenance"] == {}
    assert _reasons(out) == {"type": "not-in-vocabulary", "lang": "not-in-vocabulary"}
    assert ("type", "novel", "not-in-vocabulary") in out["rejected"]


# --------------------------------------------------- rule 2: unknown is a value


def test_unknown_is_a_legal_answer_that_is_never_stored_and_silence_stays_pending(vocab):
    # Rule 2. "unknown" is how the model declines, in any casing or padding it
    # produces; it must not land in the block as if it were a classification. An
    # omitted or empty field is not even an answer — it is silence, and silence has
    # to read as PENDING later rather than as a completed field.
    out = accept_model_meta({
        "type": "UnKnown",
        "tags": ["linux", "  Unknown  "],
        "abstract": "",
        "keywords": None,
        "topics": [],
    }, vocab)

    assert "type" not in out["accepted"]
    assert out["accepted"]["tags"] == ["linux"]
    assert out["proposals"] == {}
    for silent in ("abstract", "keywords", "topics"):
        assert silent not in out["accepted"]
        assert silent not in out["proposals"]
        assert silent not in _reasons(out)

    counts = meta_coverage(out["accepted"], vocab)
    assert counts["filled"] == 1 and counts["invalid"] == 0
    assert counts["pending"] == counts["expected"] - 1


# ------------------------------------------------- rule 3: authored always wins


def test_an_authored_value_is_refused_rather_than_overwritten(vocab):
    # Rule 3. A generated value fills a gap; it never replaces a person's decision.
    # The refusal is reported as `kept-authored` and not as a vocabulary problem —
    # the model's answer here was perfectly legal, it simply arrived second.
    existing = {"type": "policy"}
    existing.update(_authored("type"))

    out = accept_model_meta({"type": "runbook"}, vocab, existing=existing)

    assert out["accepted"] == {}
    assert out["provenance"] == {}
    assert _reasons(out) == {"type": "kept-authored"}


def test_an_existing_value_with_no_provenance_at_all_is_treated_as_authored(vocab):
    # The safe default. An unlabelled value is more likely a hand-written one than a
    # forgotten machine one, and the asymmetry decides it: the cost of keeping a
    # human value is nothing, the cost of overwriting one is trust.
    out = accept_model_meta({"type": "runbook"}, vocab, existing={"type": "policy"})
    assert _reasons(out) == {"type": "kept-authored"}

    # A value the machine wrote last time carries no such claim, so a re-run may
    # improve on it. Otherwise enrichment could never correct its own mistakes.
    generated = {"type": "policy",
                 PROVENANCE_KEY: {"type": {"tier": 2, "source": SOURCE_GENERATED}}}
    out = accept_model_meta({"type": "runbook"}, vocab, existing=generated)
    assert out["accepted"]["type"] == "runbook"
    assert out["rejected"] == []


def test_accept_model_meta_leaves_the_existing_block_untouched(vocab):
    # The caller merges. A dry run must therefore cost nothing, or "what would this
    # model change?" is a question you can only answer by letting it change things.
    existing = {"type": "policy", "tags": ["linux"]}
    existing.update(_authored("type"))
    before = copy.deepcopy(existing)

    accept_model_meta({"type": "runbook", "tags": ["aws"]}, vocab, existing=existing)

    assert existing == before


# --------------------------------------------------- the boundary of the schema


def test_authored_only_fields_are_refused_outright_whatever_the_model_says(vocab):
    # `confidentiality` is refused even though "public" is a perfectly valid term:
    # a field whose entire purpose is that a person stood behind it cannot be
    # satisfied by a correct guess.
    out = accept_model_meta({"confidentiality": "public",
                             "owner": "platform-team",
                             "status": "approved"}, vocab)

    assert out["accepted"] == {}
    assert _reasons(out) == {"confidentiality": "authored-only",
                             "owner": "authored-only",
                             "status": "authored-only"}


def test_a_key_outside_the_schema_cannot_be_injected_into_the_metadata_block(vocab):
    # The enrichment stage rewrites its own block wholesale. If it accepted keys it
    # does not own, one hallucinated `doc_id` would overwrite the pipeline identity
    # every downstream artefact is keyed by.
    out = accept_model_meta({"doc_id": "forged-0001",
                             "markdown_sha256": "deadbeef",
                             "uid": "spoofed",
                             "type": "runbook"}, vocab)

    assert out["accepted"] == {"type": "runbook"}
    assert _reasons(out) == {"doc_id": "not-in-schema",
                             "markdown_sha256": "not-in-schema",
                             "uid": "not-model-writable"}


def test_a_value_of_the_wrong_kind_is_rejected_and_the_expected_kind_is_named(vocab):
    # The reason has to say what was expected, because this is the one rejection a
    # prompt fix can act on directly.
    out = accept_model_meta({"tags": "linux, aws", "type": ["runbook"]}, vocab)

    assert out["accepted"] == {}
    assert _reasons(out) == {"tags": "wrong-kind-expected-list",
                             "type": "wrong-kind-expected-scalar"}


# ------------------------------------------------------- canonicalisation


def test_aliases_are_canonicalised_on_the_way_in(vocab):
    # Normalising at acceptance is what stops synonym pollution: `threatens` and
    # `targets` must not become two edges, and MADR's status collapse has to happen
    # once, here, rather than at each call site that later reads the field.
    out = accept_model_meta({
        "type": "playbook",
        "relations": [{"s": "malware", "p": "threatens", "o": "host",
                       "ref": "#scope"}],
        "decisions": [{"id": "d-1", "status": "assumed", "ref": "#scope"}],
    }, vocab)

    assert out["rejected"] == []
    assert out["accepted"]["type"] == "runbook"
    assert out["accepted"]["relations"] == [{"s": "malware", "p": "targets",
                                             "o": "host", "ref": "#scope"}]
    # The MADR collapse carries its qualifier onto the WRITE path, not just into
    # the report: `assumed` and `provisional` both become `accepted`, so without
    # the qualifier the two are indistinguishable the moment they are stored.
    assert dict(out["accepted"]["decisions"][0]) == {
        "id": "d-1", "status": "accepted", "ref": "#scope", "assumption": True}


def test_one_invalid_record_is_dropped_while_the_valid_ones_survive(vocab):
    # A record list is not all-or-nothing. Discarding twelve good relations because
    # the thirteenth invented a predicate would make the whole extraction hostage to
    # its worst line — but the drop is reported, keyed by the offending subfield, so
    # the invented term is visible rather than merely absent.
    out = accept_model_meta({"relations": [
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#scope"},
        {"s": "c", "p": "helps_mitigate", "o": "d", "ref": "#scope"},
        {"s": "e", "p": "mitigates", "o": "f", "mode": "silent", "ref": "#scope"},
    ]}, vocab)

    assert out["accepted"]["relations"] == [
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#scope"},
        {"s": "e", "p": "mitigates", "o": "f", "mode": "silent", "ref": "#scope"},
    ]
    assert out["rejected"] == [("relations.p", "helps_mitigate", "not-in-vocabulary")]
    assert out["provenance"]["relations"]["source"] == SOURCE_GENERATED


def test_a_registry_term_becomes_a_proposal_rather_than_an_accepted_value(vocab):
    # The registry's promotion rule (>= N documents) is only meaningful if a new
    # term cannot enter the field directly. Accepting one on first sight would
    # bootstrap the vocabulary from document one — guessing the corpus shape instead
    # of measuring it.
    out = accept_model_meta({"tags": ["linux", "kubernetes", "rhel-8"]}, vocab,
                            model="m-1", prompt_sha="abc123")

    assert out["accepted"]["tags"] == ["linux"]
    assert out["proposals"]["tags"] == ["kubernetes", "rhel8"]   # alias applied first
    assert out["rejected"] == []
    prov_tags = dict(out["provenance"]["tags"])
    # `value_sha` fingerprints what we wrote, so a later run can tell a human's
    # correction of a generated value from the value we left there.
    assert prov_tags.pop("value_sha")
    assert prov_tags == {"source": SOURCE_GENERATED,
                         "model": "m-1", "prompt_sha": "abc123"}


# ----------------------------------------------------- revalidate_generated


def test_a_vocabulary_bump_evicts_a_stale_generated_value_and_relocates_a_registry_term(bumped):
    # This is what makes a version bump mean something. Enrichment carries prior
    # answers forward so a re-run is cheap; without revalidation a term that was
    # legal under v1 would survive forever under v2, which is exactly the stale
    # classification the version number was supposed to invalidate.
    meta = {
        "type": "design",                 # was closed-legal in v1, dropped in v2
        "tags": ["linux", "aws"],         # `aws` demoted out of the registry
        PROVENANCE_KEY: {
            "type": {"tier": 2, "source": SOURCE_GENERATED},
            "tags": {"tier": 2, "source": SOURCE_GENERATED},
        },
    }

    meta, moved = revalidate_generated(meta, bumped)

    assert "type" not in meta
    assert "type" not in meta[PROVENANCE_KEY]
    assert ("type", "design", "rejected") in moved

    # A registry term is demoted, not destroyed: it goes back to the proposal queue
    # where the promotion rule can readmit it.
    assert meta["tags"] == ["linux"]
    assert meta[proposed_key("tags")] == ["aws"]
    assert ("tags", "aws", "proposed") in moved


def test_revalidation_canonicalises_an_alias_in_place(bumped):
    meta = {"type": "playbook",
            PROVENANCE_KEY: {"type": {"tier": 2, "source": SOURCE_GENERATED}}}

    meta, moved = revalidate_generated(meta, bumped)

    assert meta["type"] == "runbook"
    assert meta[PROVENANCE_KEY]["type"]["source"] == SOURCE_GENERATED
    assert moved == [("type", "playbook", "canonicalised")]


def test_revalidation_leaves_an_authored_value_alone_even_when_it_is_now_off_vocabulary(bumped):
    # Demoting a human's decision behind their back is not this stage's call. The
    # value is now off-vocabulary and the linter will say so — loudly, to a person,
    # who can then decide whether the document or the vocabulary was wrong.
    meta = {"lang": "en-GB", "type": "design"}
    meta.update(_authored("lang", "type"))
    before = copy.deepcopy(meta)

    meta, moved = revalidate_generated(meta, bumped)

    assert meta == before
    assert moved == []


def test_revalidation_ignores_fields_it_does_not_own(bumped):
    # Only `generated` values are in scope. Extracted and derived fields are
    # recomputed by their own stage, and a key that is not in the schema at all
    # belongs to the pipeline, not to enrichment.
    meta = {"doc_id": "pipeline-owned",
            "lang": "en-GB",
            "type": "design",
            PROVENANCE_KEY: {
                "lang": {"tier": 2, "source": SOURCE_EXTRACTED},
                "type": {"tier": 2, "source": SOURCE_DERIVED},
            }}
    before = copy.deepcopy(meta)

    meta, moved = revalidate_generated(meta, bumped)

    assert meta == before
    assert moved == []


# ------------------------------------------------------------- meta_coverage


def test_coverage_measures_against_the_schema_not_against_what_the_run_attempted(vocab):
    # The denominator is the field inventory. If it shrank to "whatever this run
    # tried", a pass that quietly skipped fifteen fields would report itself
    # complete — the reporting failure that makes a coverage number worthless.
    expected = len([f.name for f in FIELDS if f.tier == 2 and not f.authored_only])

    empty = meta_coverage({}, vocab)
    assert empty["expected"] == expected
    assert empty["filled"] == 0 and empty["invalid"] == 0
    assert empty["pending"] == expected

    assert meta_coverage(None, vocab)["expected"] == expected


def test_coverage_separates_invalid_from_pending_and_never_reports_negative_pending(vocab):
    meta = {
        "type": "runbook",                 # valid + authored
        "abstract": "a summary",           # valid, generated
        "lang": "Klingon",                 # off a closed vocabulary -> invalid
        "tags": "linux",                   # wrong kind -> invalid
        "topics": [],                      # empty -> pending, not filled
        "confidentiality": "public",       # authored-only: outside the denominator
    }
    meta.update(_authored("type"))

    counts = meta_coverage(meta, vocab)

    assert counts["filled"] == 2
    assert counts["authored"] == 1
    assert counts["invalid"] == 2
    # An invalid value is not progress, but it is not silence either: it must not be
    # double-counted as pending, or the two numbers stop summing to the target.
    assert counts["pending"] >= 0
    assert counts["filled"] + counts["invalid"] + counts["pending"] == counts["expected"]


# ------------------------------------------------------------- set_provenance


def test_set_provenance_names_the_model_only_for_generated_values(vocab):
    # `authored wins` is only enforceable if the file says which values were
    # authored. The model/prompt pair is recorded for generated values alone —
    # attributing a human's edit to a model would be the same lie in reverse.
    meta = {}
    set_provenance(meta, "type", SOURCE_GENERATED, model="m-1", prompt_sha="abc123")
    set_provenance(meta, "title", SOURCE_AUTHORED, model="m-1", prompt_sha="abc123")
    set_provenance(meta, "slug", SOURCE_DERIVED)

    prov = meta[PROVENANCE_KEY]
    got = dict(prov["type"])
    assert got.pop("value_sha")          # fingerprint of the stored value
    assert got == {"source": SOURCE_GENERATED,
                   "model": "m-1", "prompt_sha": "abc123"}
    # An authored value is a person's, so nothing is fingerprinted; a machine value
    # is, whatever tier wrote it, or the next run cannot see a correction.
    assert prov["title"] == {"source": SOURCE_AUTHORED}
    assert dict(prov["slug"])["source"] == SOURCE_DERIVED
    assert dict(prov["slug"])["value_sha"] == value_sha(meta.get("slug"))
    # ...and no `tier`: it is field(name).tier, recomputable for free, and was ~40%
    # of the bytes of every provenance block in the corpus.
    assert "tier" not in prov["title"] and "tier" not in prov["slug"]

    # A key outside the schema gets no provenance record — and no block is conjured
    # for it either.
    fresh = {}
    set_provenance(fresh, "doc_id", SOURCE_GENERATED)
    assert fresh == {}


def test_order_meta_is_canonical_so_a_rerun_writes_the_same_bytes():
    # REGRESSION. Merging a prior block with a fresh one produced whichever key
    # order the merge happened to take, so two runs that agreed on every value
    # still wrote different bytes and `document.md` was not idempotent. Ordering by
    # the schema makes the block a function of its CONTENT alone.
    from collections import OrderedDict
    a = OrderedDict([("type", "runbook"), ("uid", "u"), ("word_count", 3),
                     ("tags_proposed", ["x"]), ("custom", 1),
                     (PROVENANCE_KEY, OrderedDict([("type", {"tier": 2}),
                                                   ("uid", {"tier": 0})]))])
    b = OrderedDict([("custom", 1), ("word_count", 3), ("uid", "u"),
                     ("tags_proposed", ["x"]), ("type", "runbook"),
                     (PROVENANCE_KEY, OrderedDict([("uid", {"tier": 0}),
                                                   ("type", {"tier": 2})]))])
    assert list(order_meta(a).items()) == list(order_meta(b).items())

    keys = list(order_meta(a).keys())
    # Schema order, unknown keys after the schema ones, provenance last.
    assert keys.index("uid") < keys.index("word_count") < keys.index("type")
    assert keys.index("type") < keys.index("custom")
    assert keys[-1] == PROVENANCE_KEY
    # A `<field>_proposed` list sits directly after the field it belongs to.
    assert keys.index("tags_proposed") == keys.index("tags") + 1 \
        if "tags" in keys else keys.index("tags_proposed") >= 0
    # Ordering never invents or drops a key.
    assert set(order_meta(a)) == set(a)


def test_order_meta_places_every_schema_field_before_any_extra():
    from collections import OrderedDict
    meta = OrderedDict([("zzz_extra", 1), ("aaa_extra", 2)])
    for name in field_names()[:5]:
        meta[name] = "v"
    out = list(order_meta(meta).keys())
    last_schema = max(out.index(n) for n in field_names()[:5])
    assert out.index("aaa_extra") > last_schema and out.index("zzz_extra") > last_schema
    assert out.index("aaa_extra") < out.index("zzz_extra")     # extras sorted


# ------------------------------------------- revalidation vs a human's correction

def _revalidate_vocab(extra=""):
    return load_vocab(text="""
version: 1
relation_predicates:
  governance: closed
  values: [runs_on, requires]
document_types:
  governance: closed
  values: [design]%s
""" % extra)


def test_a_vocabulary_bump_does_not_delete_a_humans_correction():
    # The case tier 2 is DEFINED by: a person corrects a value a model generated. The
    # provenance label still says `generated`, so gating on the label alone lets a
    # vocabulary bump delete the correction — the most expensive edit in the corpus
    # to lose. `is_authored` is what tells them apart, via the fingerprint.
    generated = [{"s": "a", "p": "runs_on", "o": "b"}]
    meta = OrderedDict([
        ("relations", [{"s": "a", "p": "HAND_EDITED", "o": "b"}]),
        (PROVENANCE_KEY, OrderedDict([
            ("relations", {"tier": 2, "source": "generated",
                           "value_sha": value_sha(generated)})])),
    ])
    out, moved = revalidate_generated(meta, _revalidate_vocab())
    assert out["relations"][0]["p"] == "HAND_EDITED"    # untouched
    assert moved == []                                  # and not even reported as moved


def test_a_value_this_stage_rewrites_is_re_fingerprinted():
    # Leaving the old `value_sha` after mutating the value makes the NEXT run see a
    # fingerprint that does not match, conclude a person edited it, and freeze the
    # field as authored forever — so the machine can never correct it again.
    meta = OrderedDict([
        ("type", "blueprint"),
        (PROVENANCE_KEY, OrderedDict([
            ("type", {"tier": 2, "source": "generated",
                      "value_sha": value_sha("blueprint")})])),
    ])
    vocab = _revalidate_vocab("\n  aliases:\n    blueprint: design")
    out, moved = revalidate_generated(meta, vocab)

    assert out["type"] == "design"
    assert ("type", "blueprint", "canonicalised") in moved
    rec = out[PROVENANCE_KEY]["type"]
    assert rec["value_sha"] == value_sha("design")
    assert is_authored(rec, out["type"]) is False       # still machine-owned


# --------------------------------------------------------- the floor (P5.1-P5.3)
#
# A model is the CEILING. Everything below is what the page carries WITHOUT one,
# taken from evidence the pipeline already measured — and the rules that stop a
# later model answer quietly deleting it.

# The two grouped vocabularies the floor and the group governance need. Kept apart
# from VOCAB_V1 so the acceptance tests above keep grading against the vocabulary
# they were written for.
VOCAB_GROUPS = VOCAB_V1 + """
entity_types:
  governance: closed
  values: [Host, Software]
  group_types:
    hosts: Host

link_categories:
  governance: closed
  values: [internal, ecosystem]
"""

BODY = ("# 1. Scope\n\n"
        "> quoted furniture\n\n"
        "- a list item\n\n"
        "The arbiter serves the read and write queues. It also refreshes the "
        "banks on a fixed schedule. A third sentence nobody needs.\n\n"
        "## Rollback\n\nRun the restore playbook.\n")

OUTLINE = [{"title": "1. Scope", "level": 1, "line_span": [0, 8],
            "links": [{"text": "wiki", "url": "https://x.example/w", "line": 3}],
            "children": [{"title": "Rollback", "level": 2, "line_span": [8, 11],
                          "links": [{"text": "runbook", "url": "run.md",
                                     "line": 9}]}]}]

ANCHORS = set(["1-scope", "rollback"])


@pytest.fixture
def gvocab():
    return load_vocab(text=VOCAB_GROUPS)


def test_a_title_from_a_document_property_is_evidence_and_one_from_a_filename_is_not():
    # The judgement the whole floor turns on. Both values are deterministic; only
    # one of them is something the document SAYS about itself, and that is what
    # decides whether a model may overwrite it later.
    assert title_floor("Memory Controller Spec", BODY, "specs/mem.docx") == (
        "Memory Controller Spec", SOURCE_EXTRACTED)
    assert title_floor("", BODY, "specs/mem.docx") == ("1. Scope", SOURCE_DERIVED)
    assert title_floor("", "", "specs/mem_ctrl-v2.docx") == (
        "Mem Ctrl V2", SOURCE_DERIVED)
    assert title_floor("", "", "") == ("", "")


def test_a_model_may_replace_a_guessed_title_and_may_not_replace_a_declared_one(vocab):
    # `is_protected` is the mechanism, and the two directions are the point: the
    # floor must not freeze a filename-derived title, and must not let a proposal
    # contradict the document's own property.
    guessed = {"title": "Mem Ctrl V2",
               PROVENANCE_KEY: {"title": {"source": SOURCE_DERIVED,
                                          "value_sha": value_sha("Mem Ctrl V2")}}}
    out = accept_model_meta({"title": "Memory Controller Design Spec"}, vocab,
                            guessed)
    assert out["accepted"]["title"] == "Memory Controller Design Spec"

    declared = {"title": "Memory Controller Spec",
                PROVENANCE_KEY: {"title": {"source": SOURCE_EXTRACTED,
                                           "value_sha": value_sha(
                                               "Memory Controller Spec")}}}
    out = accept_model_meta({"title": "Something Else"}, vocab, declared)
    assert "title" not in out["accepted"]
    assert ("title", "Something Else", "kept-extracted") in out["rejected"]


def test_the_abstract_floor_takes_prose_and_nothing_else():
    # Headings, quotes, lists and table rows are structure, not a summary. A floor
    # that picked one of them would read as a broken sentence on every page.
    text = abstract_floor(BODY, OUTLINE, max_chars=400)
    assert text.startswith("The arbiter serves the read and write queues.")
    assert "list item" not in text and "1. Scope" not in text

    # Bounded, and cut at a sentence rather than mid-word.
    short = abstract_floor(BODY, OUTLINE, max_chars=60)
    assert len(short) <= 60 and short.endswith(".")
    assert abstract_floor("", OUTLINE) == ""


def test_every_harvested_link_is_categorised_and_carries_the_section_it_sits_in():
    # The headline of P5: structure.json already holds these, measured at recall
    # 1.0, and the pipeline used to throw them away and keep only what a model
    # imagined.
    links = harvested_links(OUTLINE, ANCHORS)
    assert list(links) == ["ecosystem", "internal"]
    assert links["ecosystem"][0]["url"] == "https://x.example/w"
    assert links["ecosystem"][0]["ref"] == "#1-scope"
    assert links["internal"][0]["ref"] == "#rollback"
    # Each record says it is EVIDENCE, so a reader can tell it from a proposal.
    assert all(record_source(r) == SOURCE_EXTRACTED
               for recs in links.values() for r in recs)
    assert value_source(links) == SOURCE_EXTRACTED

    # An anchor the body does not publish is never cited: a ref that resolves to
    # nothing is a dead link, and the linter would report it as one.
    assert "ref" not in harvested_links(OUTLINE, set())["ecosystem"][0]


def test_a_model_adds_links_beside_the_harvested_ones_and_cannot_replace_them(gvocab):
    harvested = harvested_links(OUTLINE, ANCHORS)
    existing = {"links": harvested,
                PROVENANCE_KEY: {"links": {"source": SOURCE_EXTRACTED,
                                           "value_sha": value_sha(harvested)}}}
    proposed = {"links": {
        # the same URL the harvester already has, re-categorised: dropped, because
        # the measured record is the better-sourced one
        "internal": [{"url": "https://x.example/w", "ref": "#rollback"}],
        # ...and one the body does not carry, which is the model's to contribute
        "ecosystem": [{"url": "https://y.example/z", "ref": "#rollback"}]}}

    out = accept_model_meta(proposed, gvocab, existing, model="m", prompt_sha="p")
    kept = out["accepted"]["links"]
    urls = [r["url"] for recs in kept.values() for r in recs]
    assert urls.count("https://x.example/w") == 1
    assert "https://y.example/z" in urls
    assert "run.md" in urls                        # the other harvested one survives
    # The block is now mixed, and says so: the weakest origin in it is what the
    # field-level record may claim.
    assert out["provenance"]["links"]["source"] == SOURCE_GENERATED


def test_a_record_that_cannot_say_which_section_asserts_it_is_refused(gvocab):
    # "Which section says this?" has to be answerable or the claim cannot be
    # checked, quoted or repaired. `{"p": "runs_on"}` used to be schema-valid.
    out = accept_model_meta({"relations": [
        {"p": "runs_on"},                                   # no s, no o, no ref
        {"s": "a", "p": "runs_on", "o": "b"},               # no ref
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#nowhere"},
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#1-scope"},
    ]}, gvocab, anchors=ANCHORS)

    assert out["accepted"]["relations"] == [
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#1-scope"}]
    reasons = [r[2] for r in out["rejected"]]
    assert reasons == ["missing-required-s/o/ref", "missing-required-ref",
                       "ref-not-an-anchor"]


def test_an_unverifiable_ref_is_skipped_rather_than_passed(gvocab):
    # No anchors supplied means the caller could not resolve them. A pointer nobody
    # checked must not be reported as checked — but it must not be rejected either,
    # or a caller with no body would lose every record.
    out = accept_model_meta({"relations": [
        {"s": "a", "p": "runs_on", "o": "b", "ref": "#anything"}]}, gvocab)
    assert len(out["accepted"]["relations"]) == 1


def test_an_untyped_entity_in_a_group_nobody_declared_is_refused(gvocab):
    # P5.10. `group_types` says what an untyped member of a KNOWN group is
    # (`hosts` -> Host). For an invented group that fallback is "", and "" used to
    # read as "nothing to check" — so a whole ungoverned namespace walked in and was
    # written stamped `generated`.
    out = accept_model_meta({"entities": {
        "hosts": [{"name": "db-1", "ref": "#1-scope"}],          # type from the group
        "gadgets": [{"name": "thing", "ref": "#1-scope"}],       # untyped, unknown
        "widgets": [{"name": "w", "type": "Software", "ref": "#1-scope"}],
    }}, gvocab, anchors=ANCHORS)

    kept = out["accepted"]["entities"]
    assert sorted(kept) == ["hosts", "widgets"]        # a new group with TYPES is fine
    assert ("entities.gadgets.type", "gadgets",
            "untyped-member-of-unknown-group") in out["rejected"]


def test_the_permalink_is_a_uri_reference_with_or_without_a_base():
    # `source.uri` is a filesystem path and cannot be clicked: spaces, `#` and `?`
    # all mean something else in a URL.
    assert source_url("https://wiki/docs", "specs/a b#c.docx") == (
        "https://wiki/docs/specs/a%20b%23c.docx")
    assert source_url("https://wiki/docs/", "a.docx") == "https://wiki/docs/a.docx"
    assert source_url("", "specs/a b.docx") == "specs/a%20b.docx"
    assert source_url("https://wiki", "") == ""


def test_a_review_date_is_computed_only_when_both_authored_inputs_are_there():
    # An authored-only field MAY be derived when the derivation is arithmetic over
    # two authored values: it restates a commitment a person made rather than making
    # one for them. Everything else returns "" rather than inventing a deadline.
    assert next_review_due("2026-01-31", "quarterly") == "2026-05-02"
    assert next_review_due("2026-01-31", "annual") == "2027-01-31"
    assert next_review_due("2026-01-31", "on_change") == ""     # names no interval
    assert next_review_due("", "quarterly") == ""
    assert next_review_due("not a date", "quarterly") == ""


def test_the_spec_tells_the_model_the_shape_of_a_record_not_only_its_enums(vocab):
    spec = request_spec(vocab)
    assert spec["relations"]["required_keys"] == ["s", "p", "o", "ref"]
    assert "#anchor" in spec["relations"]["ref"]
    assert spec["links"]["required_keys"] == ["url", "ref"]
