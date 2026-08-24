"""
title: Unit — backend.validate doc_meta_report
kind: tests
layer: backend
summary: The metadata overlay's own gate — published block shape, gate precedence, invalid never completes, versions recorded.
"""
# Pure policy over counts — no disk, no model, no vocabulary file. This block is the
# metadata twin of caption_report: a re-runnable enrichment overlay that carries its
# OWN verdict so a lossless document never reads as degraded merely because no model
# has classified it yet.
from collections import OrderedDict

import pytest
from backend.validate import doc_meta_report

pytestmark = pytest.mark.unit


# report.json is consumed by everything downstream, so the block's key set AND their
# order are contract, not an implementation detail — a reordered block is a diff in
# every stored report.
def test_block_keys_and_their_order_are_the_published_contract():
    b = doc_meta_report(True, 6, 4, 2, 0, 2, schema_version=3, vocab_version=7,
                        model="claude-x", prompt_sha="abc123")
    assert isinstance(b, OrderedDict)
    assert list(b.keys()) == [
        "enabled", "schema_version", "vocab_version", "expected", "filled",
        "authored", "invalid", "pending", "model", "prompt_sha", "gate",
    ]
    assert b["expected"] == 6 and b["filled"] == 4 and b["authored"] == 2
    assert b["invalid"] == 0 and b["pending"] == 2
    assert b["model"] == "claude-x" and b["prompt_sha"] == "abc123"


def test_absent_model_identity_is_empty_string_not_none():
    # The block is serialised straight into report.json; a real bool and real strings
    # keep the schema stable whether or not the overlay ever ran.
    b = doc_meta_report(1, 0, 0, 0, 0, 0)
    assert b["enabled"] is True
    assert b["model"] == "" and b["prompt_sha"] == ""
    b = doc_meta_report(True, 0, 0, 0, 0, 0, model=None, prompt_sha=None)
    assert b["model"] == "" and b["prompt_sha"] == ""


def test_gate_states_mirror_caption_report():
    # Same four-state ladder as caption_report, deliberately: off / never ran /
    # ran-and-left-work / nothing outstanding.
    assert doc_meta_report(True, 0, 0, 0, 0, 0)["gate"] == "complete"    # nothing expected
    assert doc_meta_report(True, 6, 0, 0, 0, 6)["gate"] == "pending"     # nothing attempted
    assert doc_meta_report(True, 6, 4, 0, 0, 2)["gate"] == "incomplete"  # ran, two left
    assert doc_meta_report(True, 6, 6, 0, 0, 0)["gate"] == "complete"    # every field resolved


def test_disabled_wins_over_every_count():
    # Precedence is checked first: with enrichment off, outstanding and even invalid
    # fields describe work nobody asked for, so the block must not read as a failure.
    for kw in ({"expected": 6, "filled": 0, "invalid": 0, "pending": 6},
               {"expected": 6, "filled": 2, "invalid": 3, "pending": 1},
               {"expected": 0, "filled": 0, "invalid": 0, "pending": 0}):
        b = doc_meta_report(False, kw["expected"], kw["filled"], 0,
                            kw["invalid"], kw["pending"])
        assert b["gate"] == "disabled"
        assert b["pending"] == kw["pending"]       # counts still reported, not zeroed


# The ONE place this deliberately diverges from caption_report: captions only need a
# terminal verdict, but a metadata value outside the closed vocabulary is WORSE than
# an absent one — it silently becomes a new term for every consumer that groups or
# filters by that field. So a non-zero `invalid` holds the gate off "complete" even
# when nothing is pending, and only a human correction can clear it.
def test_an_invalid_value_keeps_the_gate_off_complete_even_with_nothing_pending():
    b = doc_meta_report(True, 6, 5, 0, 1, 0)
    assert b["invalid"] == 1
    assert b["gate"] == "incomplete"
    # Correct that one value and the same run completes — invalid was the only reason.
    assert doc_meta_report(True, 6, 6, 0, 0, 0)["gate"] == "complete"


def test_authored_fields_are_reported_but_are_never_model_coverage():
    # Authored (tier-0/authored-only) values are a person's accountability record: a
    # generated value must never overwrite one, so they cannot count as the model
    # having done its work. A doc full of authored fields whose model-writable fields
    # are untouched is still "pending", not "complete".
    b = doc_meta_report(True, 4, 0, 5, 0, 4)
    assert b["authored"] == 5
    assert b["gate"] == "pending"
    # authored moves nothing in the verdict — only filled/invalid/pending do.
    assert (doc_meta_report(True, 4, 2, 0, 0, 2)["gate"]
            == doc_meta_report(True, 4, 2, 9, 0, 2)["gate"] == "incomplete")


def test_both_versions_are_recorded_as_ints_because_they_invalidate_different_work():
    # A schema bump means the field INVENTORY moved (re-derive everything); a vocab
    # bump means only the ALLOWED VALUES moved (revisit the classified fields). They
    # are stamped separately so a consumer can tell which kind of staleness it has,
    # and coerced to int so a version read out of YAML/argv still compares numerically.
    b = doc_meta_report(True, 1, 1, 0, 0, 0, schema_version="3", vocab_version="7")
    assert b["schema_version"] == 3 and b["vocab_version"] == 7
    assert isinstance(b["schema_version"], int) and isinstance(b["vocab_version"], int)
    b = doc_meta_report(True, 1, 1, 0, 0, 0)
    assert b["schema_version"] == 0 and b["vocab_version"] == 0   # unstamped, not None


def test_report_is_a_pure_function_of_its_arguments():
    # No disk, no clock, no module state: identical arguments give an identical block,
    # and a caller mutating one result cannot poison the next.
    args = (True, 6, 4, 2, 1, 1)
    first = doc_meta_report(*args, schema_version=3, vocab_version=7, model="m")
    second = doc_meta_report(*args, schema_version=3, vocab_version=7, model="m")
    assert first == second and first is not second
    first["gate"] = "tampered"
    first["expected"] = 999
    assert doc_meta_report(*args, schema_version=3, vocab_version=7,
                           model="m") == second
