"""
title: Unit — the run block, decisions, and configuration provenance
kind: tests
layer: backend
summary: A run records what was asked for and what it chose, without ever publishing a host path.
"""
import pytest

from backend.provenance import (config_provenance, corpus_id, decision, path_id,
                                redact_argv, run_block, DECISION_CODES)


# ------------------------------------------------------------------ redaction

def test_path_values_are_replaced_but_every_switch_survives():
    argv = ["--src", "/home/someone/private/docs", "--out", "/mnt/bundles",
            "--tokenizer", "tiktoken:cl100k_base", "--force"]
    out = redact_argv(argv, {"--src": "<src>", "--out": "<out>"})
    assert out == ["--src", "<src>", "--out", "<out>",
                   "--tokenizer", "tiktoken:cl100k_base", "--force"]


def test_equals_form_is_redacted_too():
    out = redact_argv(["--src=/home/someone/docs"], {"--src": "<src>"})
    assert out == ["--src=<src>"]


def test_an_unnamed_path_flag_still_never_leaks():
    # The default redaction set is what makes this safe by omission rather than by
    # the caller remembering every flag.
    out = redact_argv(["--vocab", "/etc/doc2md/vocab.yaml", "--strict"])
    assert out == ["--vocab", "<path>", "--strict"]


def test_no_absolute_path_survives_redaction():
    argv = ["--bundles", "/vols/x/y", "--json", "/tmp/r.json", "--limit", "5"]
    assert not [a for a in redact_argv(argv) if a.startswith("/")]


def test_path_id_identifies_without_disclosing():
    a, b = path_id("/vols/one"), path_id("/vols/two")
    assert a != b and len(a) == 16
    assert path_id("/vols/one") == a                  # stable across calls
    assert "/vols" not in a


# ----------------------------------------------------------------- corpus id

def test_corpus_id_is_order_independent_and_content_sensitive():
    rows = [{"doc_id": "a", "source_sha256": "1"},
            {"doc_id": "b", "source_sha256": "2"}]
    assert corpus_id(rows) == corpus_id(list(reversed(rows)))
    changed = [{"doc_id": "a", "source_sha256": "1"},
               {"doc_id": "b", "source_sha256": "3"}]
    assert corpus_id(rows) != corpus_id(changed)


def test_corpus_id_of_nothing_is_defined():
    assert len(corpus_id([])) == 64


# ------------------------------------------------------------------ decisions

def test_a_decision_records_what_was_chosen_and_the_evidence():
    d = decision("ocr_routed", "ocr", "no usable text layer",
                 {"pages": 9, "text_chars": 12})
    assert d["code"] == "ocr_routed" and d["chose"] == "ocr"
    assert d["evidence"]["text_chars"] == 12


def test_an_unknown_decision_code_is_refused_not_silently_recorded():
    # A typo would create a category of one that nothing ever aggregates again.
    with pytest.raises(ValueError):
        decision("ocr_routed_typo", "ocr", "because")


def test_every_decision_code_is_unique():
    assert len(set(DECISION_CODES)) == len(DECISION_CODES)


# ------------------------------------------------------- config provenance

def test_the_source_of_each_value_is_derived_by_difference():
    now = {"min_recall": 0.9, "backend": "docling", "min_tokens": 50}
    no_env = {"min_recall": 0.8, "backend": "docling", "min_tokens": 50}
    no_file = {"min_recall": 0.8, "backend": "native", "min_tokens": 50}
    prov = config_provenance(now, no_env, no_file)
    assert prov["min_recall"] == {"value": 0.9, "from": "env"}
    assert prov["backend"] == {"value": "docling", "from": "file"}
    assert prov["min_tokens"] == {"value": 50, "from": "default"}


def test_an_env_var_equal_to_the_default_is_invisible_to_the_diff_so_it_is_listed():
    # This is the case the difference method cannot see, and the case somebody
    # re-establishing the run on another machine most needs to know about.
    prov = config_provenance({"min_recall": 0.8}, {"min_recall": 0.8},
                             {"min_recall": 0.8}, env_names=["DOC2MD_MIN_RECALL"])
    assert prov["min_recall"]["from"] == "default"
    assert prov["_env_present"]["value"] == ["DOC2MD_MIN_RECALL"]


# ------------------------------------------------------------------ run block

def test_run_block_carries_what_a_replay_needs():
    run = run_block("build_bundle", "R1",
                    argv=["--src", "<src>"],
                    code={"name": "doc2md", "commit": "abc"},
                    host={"python": "3.6.8"},
                    config={"min_recall": {"value": 0.8, "from": "default"}},
                    tools={"soffice": "7.6.4.1"},
                    source_root_id="deadbeef")
    assert run["entrypoint"] == "build_bundle" and run["run_id"] == "R1"
    assert run["argv"] == ["--src", "<src>"]
    assert run["code"]["commit"] == "abc"
    assert run["tools"]["soffice"] == "7.6.4.1"
    assert run["source_root_id"] == "deadbeef"


def test_absent_optionals_are_omitted_not_emitted_empty():
    run = run_block("build_bundle", "R1")
    assert "tools" not in run and "started_at" not in run
    assert "source_root_id" not in run
