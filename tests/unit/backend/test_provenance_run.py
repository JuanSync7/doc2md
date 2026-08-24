"""
title: Unit — the run block, decisions, and configuration provenance
kind: tests
layer: backend
summary: A run records what was asked for and what it chose, without ever publishing a host path.
"""
import pytest

from backend.provenance import (config_provenance, corpus_id, decision, path_id,
                                redact_argv, run_block, safe_value, stamp_stage,
                                DECISION_CODES)


# ------------------------------------------------------------------ redaction

def test_path_values_are_replaced_but_every_switch_survives():
    argv = ["--src", "/home/someone/private/docs", "--out", "/mnt/bundles",
            "--tokenizer", "tiktoken:cl100k_base", "--force"]
    out = redact_argv(argv, {"--src": "<src>", "--out": "<out>"})
    assert out == ["--src", "<src>", "--out", "<out>",
                   "--tokenizer", "tiktoken:cl100k_base", "--force"]


def test_an_abbreviated_flag_is_redacted_because_argparse_accepts_prefixes():
    # `--sr` IS `--src` to argparse, so a redaction keyed on the exact flag name
    # never fired and the operator's directory sailed into every report.json.
    out = redact_argv(["--sr", "/home/someone/docs", "--ou", "/mnt/bundles"],
                      {"--src": "<src>", "--out": "<out>"})
    assert out == ["--sr", "<src>", "--ou", "<out>"]


def test_a_path_leaks_through_no_flag_because_the_value_is_what_is_tested():
    # `--only` and `--tokenizer` were in no redaction list and both take values
    # that are routinely paths. Nothing about the flag decides this any more.
    assert redact_argv(["--only", "/vols/corpus/radar spec.docx"]) == [
        "--only", "<path:%s>" % path_id("/vols/corpus/radar spec.docx")]
    assert redact_argv(["--tokenizer", "char:/home/me/models/tok"]) == [
        "--tokenizer", "char:<path:%s>" % path_id("/home/me/models/tok")]


def test_a_path_buried_inside_a_command_value_is_still_redacted():
    out = redact_argv(["--stage", "python3 /repo/scripts/worker.py --shard 1"])
    assert "/repo" not in out[1] and out[1].startswith("python3 <path:")
    assert out[1].endswith("--shard 1")


def test_a_url_is_not_a_host_path_and_survives_verbatim():
    # `--source-base-url` decides `meta.source.url` for the whole corpus. Redacting
    # it would delete the switch that produced every permalink in the bundle.
    argv = ["--source-base-url", "https://wiki.example.com/docs/",
            "--namespace", "acme.internal"]
    assert redact_argv(argv) == argv


def test_equals_form_is_redacted_too():
    out = redact_argv(["--src=/home/someone/docs"], {"--src": "<src>"})
    assert out == ["--src=<src>"]


def test_an_unnamed_path_flag_still_never_leaks():
    # Safe by the SHAPE of the value, not by anyone remembering to list the flag.
    # `--vocab` is on no list any more and the path is redacted all the same — to
    # the `<path:id>` form, which still answers "was it the same file?".
    out = redact_argv(["--vocab", "/etc/doc2md/vocab.yaml", "--strict"])
    assert out == ["--vocab", "<path:%s>" % path_id("/etc/doc2md/vocab.yaml"),
                   "--strict"]


def test_an_ordinary_switch_is_never_rewritten_into_a_path_placeholder():
    """A flag list expanded against argparse prefixes corrupted the record.

    `--ex` is argparse's own unambiguous abbreviation of `enrich_metadata`'s
    `--excerpt-chars`, and it is a 4-character prefix of the listed `--expectations`,
    so the NUMBER was overwritten with `<path>` — a wrong `run.argv` in every
    report.json, and `replay_run` then refusing to reconstruct a run it could have.
    Nothing about the flag decides this; only the value can.
    """
    assert redact_argv(["--ex", "4000"]) == ["--ex", "4000"]
    assert redact_argv(["--excerpt-chars", "4000"]) == ["--excerpt-chars", "4000"]
    # a relative path discloses nothing, so it is not blanked either
    assert redact_argv(["--vocab", "config/vocab.yaml"]) == ["--vocab",
                                                             "config/vocab.yaml"]
    # ...while the roots a replay must be handed back still get their placeholder
    assert redact_argv(["--sr", "/vols/x"], {"--src": "<src>"}) == ["--sr", "<src>"]


def test_a_path_glued_straight_onto_a_switch_is_still_a_path():
    """`-o/vols/secret/x` reached `report.json` and `runs.jsonl` verbatim.

    Redaction tested position 0, a `=` pair and a `:` pair, and a short option is
    the one place a value attaches with NO delimiter for any of those to find. The
    root `CLAUDE.md` forbids an absolute host path in published output outright, so
    "no CLI here happens to spell it that way today" is not a defence — `--stage`
    and `--worker-cmd` publish whole command lines somebody else wrote.
    """
    assert redact_argv(["-o/vols/private/x"]) == [
        "-o<path:%s>" % path_id("/vols/private/x")]
    assert redact_argv(["-I/vols/private/inc"]) == [
        "-I<path:%s>" % path_id("/vols/private/inc")]
    assert redact_argv(["-o~/private/x"]) == [
        "-o<path:%s>" % path_id("~/private/x")]
    # a long flag never attaches a value without `=`, but if one shows up the path
    # half is still a path
    assert redact_argv(["--src/vols/private/x"]) == [
        "--src<path:%s>" % path_id("/vols/private/x")]
    # third in a comma-joined list is as much a leak as first
    assert redact_argv(["--exclude=a,/vols/private/b"]) == [
        "--exclude=a,<path:%s>" % path_id("/vols/private/b")]
    assert redact_argv(["-Wl,-rpath,/vols/private/lib"]) == [
        "-Wl,-rpath,<path:%s>" % path_id("/vols/private/lib")]
    # and buried inside a command line, where a whole `cc` invocation can ride
    assert "/vols/private" not in " ".join(
        redact_argv(["--stage", "cc -o/vols/private/x -Ibuild"]))
    # a `:` pair whose tail is not a bare `/...` — a home directory, or its own
    # switch — was read as "no path here" and published whole
    assert redact_argv(["--tokenizer", "char:~/models/tok"]) == [
        "--tokenizer", "char:<path:%s>" % path_id("~/models/tok")]
    # the LEFT half of an `=` is a switch and is copied through, so a left half
    # that is really a path went out untouched while the right half was redacted
    assert redact_argv(["/vols/private/x=1"]) == [
        "<path:%s>=1" % path_id("/vols/private/x")]
    assert "/vols/private" not in " ".join(
        redact_argv(["-Wl,-rpath,/vols/private/lib--flag=1"]))
    # an EMPTY AUTHORITY is a local path whatever the scheme spells itself: with
    # nothing between the `//` and the `/` there is no host to disclose
    assert redact_argv(["--only", "char:///vols/private/x"]) == [
        "--only", "char://<path:%s>" % path_id("/vols/private/x")]


def test_a_switch_that_carries_a_relative_value_is_left_exactly_alone():
    """The other direction, which a previous review already caught once.

    `-obuild/out` is `-o` with the value `build/out`: a relative path discloses
    nothing, and hashing `/out` out of the middle of it would destroy a value
    `replay_run` needs while protecting a path that was never there. A short option
    is therefore read as EXACTLY ONE letter — the value starts right after it.
    """
    assert redact_argv(["-obuild/out"]) == ["-obuild/out"]
    assert redact_argv(["-o", "out.md"]) == ["-o", "out.md"]
    assert redact_argv(["-j4", "-v", "-n", "5"]) == ["-j4", "-v", "-n", "5"]
    assert redact_argv(["--exclude=a,b"]) == ["--exclude=a,b"]
    assert redact_argv(["--x", "s/a/b/"]) == ["--x", "s/a/b/"]
    assert redact_argv(["--only", "relative/path/spec.docx"]) == [
        "--only", "relative/path/spec.docx"]
    # `-1/2` is a number, not a switch carrying a path: a short option must be a
    # LETTER before its value is split off
    assert redact_argv(["-1/2"]) == ["-1/2"]
    # a network URL still survives whole, wherever the comma test might have bitten
    assert redact_argv(["--source-base-url", "https://wiki.example.com/a,b"]) == [
        "--source-base-url", "https://wiki.example.com/a,b"]
    # a `=` / `:` / `,` half that holds no path comes back byte-identical, so the
    # halves can be redacted without any of them being rewritten
    assert redact_argv(["--tokenizer", "char:models/tok", "a:b=c/d",
                        "-Wl,-rpath,build/lib", "--exclude=a,b"]) == [
        "--tokenizer", "char:models/tok", "a:b=c/d",
        "-Wl,-rpath,build/lib", "--exclude=a,b"]
    # a path with a space in it is still hashed as ONE path, not word by word
    assert redact_argv(["--out=/vols/spec drafts/radar spec.docx"]) == [
        "--out=<path:%s>" % path_id("/vols/spec drafts/radar spec.docx")]


def test_a_file_url_is_an_absolute_path_wearing_a_scheme():
    """`//` was waved through as "a scheme-relative URL, not a path". It is both.

    `file:///vols/private/corpus/` reached `runs.jsonl` and every `report.json`
    verbatim, and so did a bare `//vols/private/spec.docx`, which Linux resolves
    exactly like `/vols/private/spec.docx`. A real network URL still survives whole:
    `--source-base-url https://wiki/docs` decided every permalink in the bundle.
    """
    assert redact_argv(["--source-base-url", "file:///vols/private/corpus/"]) == [
        "--source-base-url", "file://<path:%s>" % path_id("/vols/private/corpus/")]
    assert redact_argv(["--only", "//vols/private/spec.docx"]) == [
        "--only", "<path:%s>" % path_id("//vols/private/spec.docx")]
    assert redact_argv(["--source-base-url", "https://wiki.example.com/docs/"]) == [
        "--source-base-url", "https://wiki.example.com/docs/"]


def test_a_home_directory_is_a_host_path_even_without_a_leading_slash():
    # `~someone/models/tok` names the user, which this layer must never record.
    assert safe_value("~/models/tok") == "<path:%s>" % path_id("~/models/tok")
    assert "someone" not in safe_value("~someone/models/tok")


def test_no_absolute_path_survives_redaction():
    argv = ["--bundles", "/vols/x/y", "--json", "/tmp/r.json", "--limit", "5"]
    assert not [a for a in redact_argv(argv) if a.startswith("/")]


@pytest.mark.parametrize("argv", [
    ["--sr", "/vols/private/docs"],                       # abbreviated
    ["--ou=/vols/private/out"],                           # abbreviated, = form
    ["--only", "/vols/private/docs/spec.docx"],           # unlisted flag
    ["--tokenizer", "char:/vols/private/models/tok"],     # path inside a value
    ["--expectations", "/vols/private/e.json"],           # once a listed flag
    ["--anything-at-all", "/vols/private/x"],             # a flag nobody listed
    ["--source-base-url", "file:///vols/private/corpus/"],  # a path wearing a scheme
    ["--only", "//vols/private/spec.docx"],               # `//abs` resolves as `/abs`
    ["--vocab", "~/vols/private/vocab.yaml"],             # a home directory
    ["--worker-cmd", "python3 w.py --out=/vols/private/x"],  # buried `k=/abs`
    ["-o/vols/private/x"],                                # glued to a short option
    ["-Wl,-rpath,/vols/private/lib"],                     # third in a comma list
    ["--stage", "cc -o/vols/private/x"],                  # glued, inside a command
    ["--tokenizer", "char:~/vols/private/models/tok"],    # a `:` tail that is a `~`
    ["/vols/private/x=1"],                                # the LEFT half of an `=`
    ["--only", "char:///vols/private/x"],                 # an empty authority
])
def test_no_flag_spelling_can_carry_an_absolute_path_out(argv):
    # The one invariant: whatever the switch, whatever its abbreviation, the VALUE
    # decides. Anything else is a list somebody has to remember to extend.
    out = " ".join(redact_argv(argv, {"--src": "<src>", "--out": "<out>"}))
    assert "/vols/private" not in out


# ----------------------------------------------------------------- safe_value

def test_safe_value_hides_a_path_but_keeps_it_comparable():
    a, b = safe_value("/vols/one"), safe_value("/vols/one")
    assert a == b and "/vols" not in a                    # two runs still agree
    assert safe_value("/vols/two") != a
    assert safe_value(["/vols/one", 7, "cl100k_base"])[1:] == [7, "cl100k_base"]
    assert safe_value("data/bundles") == "data/bundles"   # relative discloses nothing


def test_safe_value_walks_a_mapping_too():
    # The public helper every artifact writer is told to use returned a dict
    # untouched, so the one container a caller reaches for when it has several
    # paths to publish was the one container that published them.
    got = safe_value({"vocab": "/vols/private/vocab.yaml",
                      "roots": ["/vols/private/a", "rel/b"], "n": 7})
    assert "/vols/private" not in repr(got)
    assert got["roots"][1] == "rel/b" and got["n"] == 7
    assert list(got) == ["vocab", "roots", "n"]           # order is not disturbed


def test_a_path_buried_in_an_equals_pair_inside_a_compound_value_is_redacted():
    # One argv element can be a whole command line, and `--out=/abs/x` at word three
    # leaks exactly as much as `/abs/x` at word one.
    assert "/vols/private" not in safe_value("run --a=1 --out=/vols/private/x")
    assert safe_value("run --a=1 --out=/vols/private/x").startswith("run --a=1 ")


def test_decision_evidence_can_never_publish_a_host_path():
    d = decision("vocabulary_selected", "/etc/doc2md/vocab.yaml", "resolved",
                 {"path": "/etc/doc2md/vocab.yaml"})
    assert "/etc" not in d["chose"] and "/etc" not in d["evidence"]["path"]


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


def test_the_enrichment_stage_has_names_for_the_branches_that_move_graded_fields():
    # meta.id / meta.uid / meta.source.url are rubric rows D2-D4, and the switches
    # that decide them used to be recorded in no artifact at all.
    for code in ("metadata_tier", "vocabulary_selected", "identity_namespace",
                 "permalink_base"):
        assert code in DECISION_CODES


def test_a_stage_stamp_says_which_run_took_the_branch():
    records = [decision("lane_selected", "ooxml", "by extension", {"ext": "docx"}),
               decision("metadata_tier", "deterministic", "no model")]
    out = stamp_stage(records, "enrich_metadata")
    assert [d["stage"] for d in out] == ["enrich_metadata", "enrich_metadata"]
    # right after `code`, so the record reads "this branch, taken by this stage"
    assert list(out[0])[:2] == ["code", "stage"]
    assert out[0]["evidence"]["ext"] == "docx"        # nothing else is disturbed
    assert "stage" not in records[0]                  # pure: the input is untouched


def test_stamping_twice_does_not_grow_the_record():
    once = stamp_stage([decision("cache_hit", "stored", "reused")], "a")
    twice = stamp_stage(once, "b")
    assert twice[0]["stage"] == "b" and list(twice[0]) == list(once[0])


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
