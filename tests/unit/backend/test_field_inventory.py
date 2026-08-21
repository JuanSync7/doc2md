"""
title: Unit — no field ships that nothing reads and nothing fills
kind: tests
layer: backend
summary: Every metadata field must name a consumer that would break without it, and the model tier may not carry a field that nothing checks.
"""
# quality-plan.md D6. A schema grows by accretion: somebody imagines a consumer,
# adds a field, and the consumer never arrives. The field then costs a model call on
# every document forever, lands in every bundle, is published in the reference, and
# nothing anywhere would notice if it vanished. v2 shipped six of them —
# `requirement_level` (RESERVED, bound to nothing), `classification` (a second
# spelling of `confidentiality`), `aliases`, `prerequisites`, `out_of_scope` and
# `short_title`.
#
# This test is the gate that stops the seventh. It FAILS CLOSED: a new field with no
# entry in CONSUMERS below fails immediately, so adding one means saying who reads it
# — and the claim is machine-checked, not a comment.
#
# THE RULE, in one line: a MODEL-WRITABLE field must have a code consumer or a
# governing vocabulary, because that is the class where a useless field costs a model
# call and produces data nothing can check. A deterministic field is cheap, is a
# measurement of the source rather than a guess about it, and passes on a weaker
# justification: something fills it and the published contract names it.
import io
import os
import re

import pytest

from backend.kb import (FIELDS, PROVENANCE_KEY, REVIEW_INTERVAL_DAYS, field,
                        field_names, load_vocab, model_writable, request_spec,
                        set_provenance, vocab_path)

pytestmark = pytest.mark.unit

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Consumer kinds.
CODE = "code"                # a named module that would break without this field
VOCAB = "vocab"              # a term list governs it: the linter and the corpus
                             # gates check every value, by construction
DETERMINISTIC = "det"        # tier 0/1: measured from the source, published in the
                             # bundle contract, costs nothing to keep
AUTHORED = "authored"        # a person writes it and a person reads it; the linter
                             # enforces that no model ever wrote it

WRITER = os.path.join("scripts", "enrich_metadata.py")
LINTER = os.path.join("src", "backend", "kb", "_lint.py")
CORPUS = os.path.join("src", "backend", "kb", "_corpus.py")
RUBRIC = os.path.join("src", "backend", "validate", "_rubric.py")
GATE = os.path.join("src", "backend", "validate", "_mdcheck.py")

# field -> (kind, evidence). For CODE the evidence is (path, marker) and the marker
# must really be in that file; for the rest the kind itself is verified below.
CONSUMERS = {
    # ---- tier 0/1: measurements of the source ---------------------------------
    "schema_version": (CODE, (GATE, "schema_version")),
    "vocab_version": (CODE, (GATE, "vocab_version")),
    "id": (CODE, (CORPUS, '("id", "uid")')),
    "uid": (CODE, (CORPUS, '("id", "uid")')),
    "version": (DETERMINISTIC, None),
    "source": (DETERMINISTIC, None),
    "extraction": (DETERMINISTIC, None),
    "slug": (DETERMINISTIC, None),
    "word_count": (DETERMINISTIC, None),
    "reading_time_minutes": (DETERMINISTIC, None),
    # ---- tier 2: a model proposes, so something must be able to check it -------
    "title": (CODE, (RUBRIC, "_D1_DESCRIPTORS")),
    "abstract": (CODE, (RUBRIC, "_D1_DESCRIPTORS")),
    "type": (VOCAB, None),
    "subtype": (VOCAB, None),
    "lang": (VOCAB, None),
    "tags": (VOCAB, None),
    "keywords": (VOCAB, None),
    "topics": (VOCAB, None),
    "audience": (VOCAB, None),
    "entities": (VOCAB, None),
    "relations": (VOCAB, None),
    "decisions": (VOCAB, None),
    "risks": (VOCAB, None),
    "open_questions": (CODE, (LINTER, "open_questions")),
    "links": (VOCAB, None),
    "see_also": (CODE, (CORPUS, "see_also")),
    # ---- authored only: accountability ----------------------------------------
    "status": (VOCAB, None),
    "confidentiality": (VOCAB, None),
    "owner": (AUTHORED, None),
    "accountable_roles": (AUTHORED, None),
    "review_cadence": (VOCAB, None),
    "last_reviewed": (AUTHORED, None),
    "next_review_due": (CODE, (WRITER, "next_review_due")),
    "validated_against_version": (AUTHORED, None),
}

# Fields the inventory has SHED. Named so the deletion is a decision with a test
# behind it rather than something a merge can quietly undo.
DELETED = {
    "classification": "a second spelling of `confidentiality`; one concept, one field",
    "aliases": "no consumer anywhere — no resolver ever looked a document up by one",
    "prerequisites": "no consumer anywhere",
    "out_of_scope": "no consumer anywhere",
    "short_title": "nothing read it; `title` and `slug` already cover the need",
}

# `review_cadence` terms that name no interval, so no due date can be computed from
# them. Deliberately not in REVIEW_INTERVAL_DAYS: inventing a date for "on_change"
# would put a deadline in a document nobody agreed to.
NO_INTERVAL = ("on_change", "none")


@pytest.fixture(scope="module")
def vocab():
    return load_vocab(vocab_path())


def _read(rel):
    with io.open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _code_spans(text):
    return set(re.findall(r"`([^`\n]+)`", text))


def test_every_field_names_a_consumer_and_the_claim_is_true(vocab):
    # Fails CLOSED: a field with no entry is a field nobody has said who reads.
    missing = [f.name for f in FIELDS if f.name not in CONSUMERS]
    assert missing == [], (
        "these fields ship with no declared consumer — bind them (give them a "
        "reader) or delete them, and record which in CONSUMERS: %s" % missing)
    stale = [n for n in CONSUMERS if n not in field_names()]
    assert stale == [], "CONSUMERS names fields the schema no longer has: %s" % stale

    for f in FIELDS:
        kind, evidence = CONSUMERS[f.name]
        if kind == CODE:
            path, marker = evidence
            assert marker in _read(path), (
                "%s claims %s reads it, and it does not" % (f.name, path))
        elif kind == VOCAB:
            assert f.vocab and vocab.has(f.vocab), (
                "%s claims a vocabulary governs it; the shipped vocabulary does "
                "not declare %r" % (f.name, f.vocab))
        elif kind == DETERMINISTIC:
            assert f.tier < 2, "%s is not deterministic" % f.name
        else:
            assert f.authored_only, "%s is not an authored-only field" % f.name


def test_a_field_a_model_writes_must_have_something_that_can_check_it():
    # The teeth. A deterministic field is a measurement and costs nothing; a
    # model-writable field costs a request on every document and produces a claim.
    # If neither a vocabulary nor a named consumer can check that claim, the field is
    # producing unverifiable data at a price, which is strictly worse than not having
    # it — the exact shape of every field this row exists to catch.
    for f in FIELDS:
        if not model_writable(f.name):
            continue
        kind, _evidence = CONSUMERS[f.name]
        assert kind in (CODE, VOCAB), (
            "%s is model-writable but nothing reads or checks it" % f.name)


def test_every_field_is_filled_by_somebody(vocab):
    spec = request_spec(vocab)
    writer = _read(WRITER)
    for f in FIELDS:
        if model_writable(f.name):
            assert f.name in spec, "%s is never asked for" % f.name
        elif f.authored_only:
            # The boundary: a model is never even offered these.
            assert f.name not in spec, "%s is offered to a model" % f.name
        else:
            assert '"%s"' % f.name in writer, (
                "%s is tier %d but the deterministic writer never mentions it"
                % (f.name, f.tier))


def test_a_deterministic_field_is_published_in_the_contract():
    # The weaker justification a deterministic field passes on has to mean something:
    # its consumer is whoever reads the bundle contract, so the contract must name it.
    published = _code_spans(_read(os.path.join("docs", "reference",
                                               "output-schema.md")))
    published |= _code_spans(_read(os.path.join("docs", "reference",
                                                "vocabulary.md")))
    for name, (kind, _e) in sorted(CONSUMERS.items()):
        if kind == DETERMINISTIC:
            assert name in published, (
                "%s is justified as contract-published and the reference does not "
                "name it" % name)


def test_the_fields_the_inventory_shed_stay_shed(vocab):
    for name, why in sorted(DELETED.items()):
        assert name not in field_names(), "%s came back: %s" % (name, why)
    # ...and the vocabulary that bound to nothing went with them.
    assert not vocab.has("requirement_level")


def test_the_review_interval_table_cannot_drift_from_the_cadence_vocabulary(vocab):
    # `next_review_due` is arithmetic over `review_cadence`, so a term added to the
    # vocabulary and not to the table would silently stop producing a due date —
    # a field that quietly does nothing, which is what this whole test is about.
    terms = vocab.values("review_cadence")
    assert terms
    for term in terms:
        assert (term in REVIEW_INTERVAL_DAYS) != (term in NO_INTERVAL), (
            "review_cadence %r names neither an interval nor a documented reason "
            "not to have one" % term)
    for term in REVIEW_INTERVAL_DAYS:
        assert term in terms, "%r is not a review_cadence term" % term
        assert REVIEW_INTERVAL_DAYS[term] > 0


def test_provenance_stores_nothing_that_is_a_pure_function_of_the_field_name():
    # `tier` was recorded on every field of every document and read by nobody:
    # `field(name).tier` is the same number, for free. It was ~40% of the bytes of a
    # provenance block — a duplicated fact that could only ever go stale.
    meta = {"title": "T", "type": "runbook"}
    set_provenance(meta, "title", "extracted")
    set_provenance(meta, "type", "generated", model="m", prompt_sha="p")
    for name, rec in meta[PROVENANCE_KEY].items():
        assert "tier" not in rec, name
        assert field(name).tier in (0, 1, 2)          # ...and still recoverable
        assert rec["source"]
        assert rec["value_sha"]                       # every machine value is one
