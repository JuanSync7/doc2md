"""
title: Integration — a recorded run can be repeated from its report
kind: tests
layer: backend
summary: replay_run reconstructs the command, reproduces the exact markdown hash, and names every divergence instead of hiding it.
"""
# The owner's stated goal for the reporting layer: "have all the reporting
# information needed to re-run, re-establish, or repeat a run based on the report."
# This is the test that decides whether that is true.
import importlib.util
import json
import os
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _docx(path, text="The transceiver runs at 77 GHz."):
    doc = ('<w:document %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Overview</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>%s</w:t></w:r></w:p>'
           '</w:body></w:document>' % (W, text))
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/styles.xml", styles)


def _build(tmp_path, name="o", extra=()):
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / name
    if not src.exists():
        src.mkdir()
        _docx(str(src / "spec.docx"))
    assert bb.main(["--src", str(src), "--out", str(out),
                    "--run-id", "R1"] + list(extra)) == 0
    d = [os.path.join(str(out), n) for n in os.listdir(str(out))
         if os.path.isdir(os.path.join(str(out), n))][0]
    return str(src), str(out), os.path.join(d, "report.json")


def _only_the_class_under_test(rr, report_path, monkeypatch):
    """Silence the two `code` divergences that depend on the checkout, not the report.

    A working copy is dirty while the suite runs from it, so every bundle built here
    records `dirty: true` and every replay of one reports two divergences about the
    checkout. They are correct — and they would mask the exit code of the class each
    test below is actually about, which is the number those tests exist to pin. This
    removes only that, and only from the recorded side; every other class is left to
    answer for itself.
    """
    monkeypatch.setattr(rr, "_git_dirty", lambda: False)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    for block in [rep.get("run") or {}] + list(rep.get("runs") or []):
        (block.get("code") or {}).pop("dirty", None)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)


def test_a_report_carries_everything_needed_to_repeat_the_run(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    run = rep["run"]
    assert run["entrypoint"] == "build_bundle" and run["run_id"] == "R1"
    assert run["argv"] == ["--src", "<src>", "--out", "<out>", "--run-id", "R1"]
    assert run["code"]["name"] == "doc2md"
    assert run["host"]["python"]
    assert run["config_ref"] == "runs.jsonl#R1"


def test_replay_reproduces_the_exact_markdown_hash(tmp_path):
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    rc = rr.main(["--report", report_path, "--src", src,
                  "--out", str(tmp_path / "replay"), "--execute", "--compare"])
    # 0 = reproduced with no divergence; 3 = reproduced but this checkout is dirty.
    assert rc in (0, 3)
    with open(report_path, encoding="utf-8") as fh:
        want = json.load(fh)["markdown_sha256"]
    replayed = [os.path.join(str(tmp_path / "replay"), n)
                for n in os.listdir(str(tmp_path / "replay"))
                if os.path.isdir(os.path.join(str(tmp_path / "replay"), n))][0]
    with open(os.path.join(replayed, "report.json"), encoding="utf-8") as fh:
        assert json.load(fh)["markdown_sha256"] == want


def test_a_changed_setting_is_named_before_anything_runs(tmp_path, monkeypatch, capsys):
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    monkeypatch.setenv("DOC2MD_MIN_RECALL", "0.5")
    rc = rr.main(["--report", report_path])
    out_text = capsys.readouterr().out
    assert rc == 3
    assert "DIVERGENCES" in out_text
    assert "min_recall" in out_text and "0.5" in out_text


def test_pointing_at_a_different_source_tree_is_caught(tmp_path, capsys):
    src, out, report_path = _build(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    rr = _mod("replay_run")
    rr.main(["--report", report_path, "--src", str(other),
             "--out", str(tmp_path / "r2")])
    text = capsys.readouterr().out
    assert "different directory" in text


def test_a_default_src_is_not_silently_replayed_from_the_environment(tmp_path,
                                                                     monkeypatch):
    """The recorded run took the default `--src`, so `argv` names no source root.

    Filling placeholders then had nothing to fill and the printed command carried
    no `--src` at all — so the replay read whatever `$DOC2MD_SRC` resolved to at
    replay time. Handing the tool a source root and having it replay a different
    corpus is worse than refusing, because the output looks like an answer.
    """
    src = tmp_path / "s"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    out = tmp_path / "o"
    monkeypatch.setenv("DOC2MD_SRC", str(src))
    bb = _mod("build_bundle")
    assert bb.main(["--out", str(out), "--run-id", "R1"]) == 0
    report_path = os.path.join(
        str(out), [n for n in os.listdir(str(out))
                   if os.path.isdir(os.path.join(str(out), n))][0], "report.json")
    with open(report_path, encoding="utf-8") as fh:
        run = json.load(fh)["run"]
    assert "--src" not in run["argv"]                  # the run really did default

    rr = _mod("replay_run")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cmd = rr.command_for(run, str(elsewhere), str(tmp_path / "r"))
    assert "--src" in cmd and cmd[cmd.index("--src") + 1] == str(elsewhere)
    assert "--out" in cmd and cmd[cmd.index("--out") + 1] == str(tmp_path / "r")
    # and pointing somewhere else is still called out, not just quietly obeyed
    assert any(n.kind == "source" and "different directory" in n.text
               for n in rr.divergences(run, {}, str(elsewhere)))


def test_an_edited_source_is_caught_although_the_directory_is_unchanged(tmp_path):
    """`source_root_id` only ever answered "same directory?".

    Both `source_sha256` and `corpus_sha256` were recorded and neither was ever
    read, so a replay over an edited tree reported "no divergences" and then
    produced different markdown.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    run = report["run"]
    full = rr._find_run_row(report, report_path, run)
    assert full.get("corpus_sha256")

    clean = rr.divergences(run, full, src, report, report_path)
    assert not [n for n in clean if n.kind in ("source", "corpus")]

    _docx(str(os.path.join(src, "spec.docx")), text="A completely different claim.")
    dirty = rr.divergences(run, full, src, report, report_path)
    kinds = dict((n.kind, n.text) for n in dirty if n.compared)
    assert "source" in kinds and "source_sha256" in kinds["source"]
    assert "corpus" in kinds and "no longer hash" in kinds["corpus"]


def test_a_missing_source_file_is_named_rather_than_read_as_unchanged(tmp_path):
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    os.remove(os.path.join(src, "spec.docx"))
    diffs = rr.divergences(report["run"],
                           rr._find_run_row(report, report_path), src, report,
                           report_path)
    assert any(n.kind == "source" and "not in the tree" in n.text
               for n in diffs if n.compared)


def test_a_different_external_tool_version_is_a_divergence(tmp_path, monkeypatch):
    """`run.tools` names the binaries that produced the document, and nothing read it.

    A legacy `.odt` is pre-converted by soffice; a different soffice produces
    different markdown from the same bytes, and the replay used to call that no
    divergence at all.
    """
    rr = _mod("replay_run")
    monkeypatch.setitem(rr._TOOL_PROBES, "soffice", lambda: "24.2.0.3")
    run = {"code": {}, "host": {}, "tools": {"soffice": "7.6.4.1"}}
    diffs = rr.divergences(run, {}, "")
    assert any(n.kind == "tools" and "7.6.4.1 -> 24.2.0.3" in n.text
               for n in diffs if n.compared)

    monkeypatch.setitem(rr._TOOL_PROBES, "soffice", lambda: "")
    diffs = rr.divergences(run, {}, "")
    assert any(n.kind == "tools" and "not installed here" in n.text
               for n in diffs if n.compared)


def test_a_tool_this_machine_cannot_probe_is_unverified_not_unchanged(tmp_path):
    # "I could not check the toolchain" and "the toolchain is the same" are
    # different answers, and only one of them is evidence.
    rr = _mod("replay_run")
    run = {"code": {}, "host": {}, "tools": {"docling": "docling 2.55.1"}}
    diffs = rr.divergences(run, {}, "")
    # ...and it is not counted as a divergence either: `compared` is False, so the
    # exit code says "could not compare" rather than "this machine differs".
    assert any(n.kind == "tools" and "UNVERIFIED" in n.text and not n.compared
               for n in diffs)


def test_an_environment_variable_that_was_merely_present_is_compared(tmp_path,
                                                                     monkeypatch):
    """`_env_present` exists precisely because the resolution diff cannot see it.

    A variable whose value equals the default moves no config value and still
    changes what a person re-establishing the run has to set up — and the
    comparison loop `continue`d straight past it.
    """
    monkeypatch.setenv("DOC2MD_MIN_RECALL", "0.80")     # equal to the default
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    rr = _mod("replay_run")
    full = rr._find_run_row(report, report_path)
    assert "DOC2MD_MIN_RECALL" in full["config"]["_env_present"]["value"]
    # It moved no value, so the config comparison stays silent about it...
    assert not [n for n in rr.divergences(report["run"], full, "")
                if n.kind == "config" and "min_recall" in n.text]

    monkeypatch.delenv("DOC2MD_MIN_RECALL")
    diffs = rr.divergences(report["run"], full, "")
    assert any(n.kind == "env" and "unset here" in n.text
               and "DOC2MD_MIN_RECALL" in n.text for n in diffs if n.compared)


def test_a_metadata_run_is_replayable_too(tmp_path):
    """Half the pipeline was unreplayable because it was in no ENTRYPOINTS table.

    `enrich_metadata` reads and writes the SAME root, so it takes no `--out` and
    the tool must not demand one; and its root flag is `--bundles`, which the
    replay has to fill back in the same way `--src` is filled for a writer.
    """
    src, out, report_path = _build(tmp_path)
    em = _mod("enrich_metadata")
    assert em.main(["--bundles", out, "--run-id", "E1",
                    "--namespace", "acme.internal",
                    "--source-base-url", "https://wiki.example.com/docs/"]) in (0, 3)

    rr = _mod("replay_run")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    staged = [r for r in report["runs"] if r["entrypoint"] == "enrich_metadata"][-1]
    cmd = rr.command_for(staged, out, "")
    assert cmd[1].endswith("enrich_metadata.py")
    assert cmd[cmd.index("--bundles") + 1] == out
    # the switches that decided meta.id and meta.source.url are IN the command
    assert cmd[cmd.index("--namespace") + 1] == "acme.internal"
    assert cmd[cmd.index("--source-base-url") + 1] == "https://wiki.example.com/docs/"
    # ...and no --out was invented for a stage that rewrites in place
    assert "--out" not in cmd

    # the run row it joins to is the ENRICHMENT's, not the writer's
    full = rr._find_run_row(report, report_path, staged)
    assert full["entrypoint"] == "enrich_metadata"
    assert full["config"]["cli.namespace"]["value"] == "acme.internal"


def test_the_same_run_id_on_two_stages_does_not_cross_the_run_rows(tmp_path):
    # Pinning both stages to one --run-id is how two runs are made comparable, and
    # `runs.jsonl` rows were looked up by run_id alone: an enrichment report got
    # handed the WRITER's resolved configuration and reported no divergence about
    # settings it never used.
    src, out, report_path = _build(tmp_path)          # --run-id R1
    em = _mod("enrich_metadata")
    assert em.main(["--bundles", out, "--run-id", "R1"]) in (0, 3)
    rr = _mod("replay_run")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    by_stage = dict((r["entrypoint"], r) for r in report["runs"])
    assert set(by_stage) == {"build_bundle", "enrich_metadata"}
    for name, run in by_stage.items():
        assert rr._find_run_row(report, report_path, run)["entrypoint"] == name


def test_a_run_row_that_cannot_be_joined_is_unverified_not_an_all_clear(
        tmp_path, monkeypatch, capsys):
    """Three of the six classes are read from the `runs.jsonl` row, and it can be gone.

    `build_bundle` appends that row only after the whole loop, so any interruption —
    Ctrl-C, a scheduler's SIGTERM, an OOM kill — leaves every finished bundle
    pointing at a row that is never written. Replaying one of those (the obvious move
    when investigating a run that died) skipped the resolved config, the `DOC2MD_*`
    presence and `corpus_sha256`, and then printed "no divergences: … same resolved
    settings, same source bytes" and exited 0 — asserting two comparisons it had not
    performed, with the threshold of the losslessness gate itself set to half.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    monkeypatch.setenv("DOC2MD_MIN_RECALL", "0.5")

    # control: with the row, the setting that halves the gate is named, and rc is 3
    assert rr.main(["--report", report_path, "--src", src,
                    "--out", str(tmp_path / "r1")]) == 3
    assert "min_recall" in capsys.readouterr().out

    os.remove(os.path.join(out, "runs.jsonl"))
    rc = rr.main(["--report", report_path, "--src", src, "--out", str(tmp_path / "r2")])
    text = capsys.readouterr().out
    assert rc == 4, "a comparison that did not happen is not an all-clear"
    assert "UNVERIFIED" in text
    # the verdict may not claim what it never checked...
    assert "same resolved settings" not in text
    assert "same corpus" not in text
    # ...and it still claims what it did check
    assert "same source bytes" in text

    notes = rr.divergences(json.load(open(report_path, encoding="utf-8"))["run"],
                           {}, src, None, report_path)
    unverified = set(n.kind for n in notes if not n.compared)
    assert {"config", "env", "corpus"} <= unverified


def test_the_inspect_only_call_never_claims_bytes_it_did_not_hash(
        tmp_path, monkeypatch, capsys):
    """`--report R` with no `--src` is the advertised "what drifted?" invocation.

    `_source_divergences` returns on its first line without opening a file, so the
    two byte checks never ran — and the verdict said "same source bytes" and exited
    0 over a corpus that had been rewritten underneath it.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    _docx(os.path.join(src, "spec.docx"), text="TOTALLY DIFFERENT CONTENT at 24 GHz.")

    rc = rr.main(["--report", report_path])
    text = capsys.readouterr().out
    assert rc == 4 and "UNVERIFIED" in text
    assert "same source bytes" not in text and "same corpus" not in text
    assert "same resolved settings" in text        # that half really was compared

    # ...and handing it the tree turns the same edit into a demonstrated divergence
    assert rr.main(["--report", report_path, "--src", src,
                    "--out", str(tmp_path / "r")]) == 3
    assert "source_sha256" in capsys.readouterr().out


def test_a_bundles_root_reports_the_bytes_unprobeable_rather_than_unchanged(
        tmp_path, monkeypatch, capsys):
    """The skip itself is correct and documented; claiming a comparison for it is not.

    `enrich_metadata` reads `document.md` from the bundle root, so the source `.docx`
    bytes genuinely cannot be re-hashed from what it was handed. The tool printed the
    recorded `corpus` hash as a header — which reads as evidence — and then "same
    source bytes", for a check that is structurally unreachable on this stage.
    """
    src, out, report_path = _build(tmp_path)
    em = _mod("enrich_metadata")
    assert em.main(["--bundles", out, "--run-id", "E1"]) in (0, 3)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)

    rc = rr.main(["--report", report_path, "--stage", "enrich_metadata", "--src", out])
    text = capsys.readouterr().out
    assert rc == 4
    assert "same source bytes" not in text and "same corpus" not in text
    assert "same resolved settings" in text
    assert "(recorded)" in text          # the corpus header is not evidence


def test_a_path_argument_that_cannot_be_reconstructed_names_its_switch(tmp_path):
    # `redact_argv` emits the switch and its placeholder as two argv elements, so
    # the refusal used to print a bare `<path:...>` and leave the operator guessing
    # which of the recorded switches it was being asked to supply.
    rr = _mod("replay_run")
    run = {"entrypoint": "build_bundle",
           "argv": ["--src", "<src>", "--vocab", "<path:deadbeef>", "--strict"]}
    with pytest.raises(ValueError) as exc:
        rr.command_for(run, str(tmp_path), str(tmp_path / "o"))
    assert "--vocab" in str(exc.value)


def test_compare_never_says_reproduced_over_two_documents_with_no_markdown(
        tmp_path, monkeypatch, capsys):
    """`REPRODUCED` was printed when both hashes were the empty string.

    A FAILED document publishes `report.json` alone, with no `markdown_sha256`, so a
    failure replayed as a failure compared `""` with `""` and announced that it had
    reproduced the markdown that neither run produced — the strongest claim this
    tool makes, over nothing at all — and exited 0.

    `subprocess.call` is stubbed because the verdict under test is the COMPARISON,
    not the conversion: what has to be pinned is what the tool says about two
    reports, and building a genuinely failing document twice would only be a slower
    way of putting the same two reports on disk.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    did = rep["doc_id"]
    rep.pop("markdown_sha256")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)

    replayed = tmp_path / "r" / did
    os.makedirs(str(replayed))
    with open(str(replayed / "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": did, "status": "failed"}, fh)
    monkeypatch.setattr(rr.subprocess, "call", lambda cmd: 0)

    rc = rr.main(["--report", report_path, "--src", src, "--out", str(tmp_path / "r"),
                  "--execute", "--compare"])
    captured = capsys.readouterr()
    assert "REPRODUCED" not in captured.out
    assert rc == 4
    assert "UNVERIFIED" in captured.err


def test_compare_says_unverified_rather_than_different_when_one_side_has_no_hash(
        tmp_path, monkeypatch, capsys):
    """The other half: `DIFFERENT` also claims a comparison that did not happen.

    "different" says two hashes were compared and disagreed. When the recorded run
    published none at all there was nothing to disagree with, and the number printed
    on the left of the arrow was an empty string.
    """
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    rep.pop("markdown_sha256")                       # as a failed document publishes
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)

    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    rc = rr.main(["--report", report_path, "--src", src, "--out", str(tmp_path / "r"),
                  "--execute", "--compare"])
    captured = capsys.readouterr()
    assert rc == 4
    assert "REPRODUCED" not in captured.out
    assert "UNVERIFIED" in captured.err


def test_compare_refuses_to_compare_a_bundle_with_itself(tmp_path, monkeypatch,
                                                          capsys):
    # `--out` pointed at the root being replayed makes the "replayed" report the
    # same file on disk as the original, so the hashes match by construction.
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    rc = rr.main(["--report", report_path, "--src", src, "--out", out,
                  "--execute", "--compare"])
    assert rc == 1
    assert "with itself" in capsys.readouterr().err


def test_a_failed_replay_never_borrows_the_replayed_tools_exit_code(
        tmp_path, monkeypatch, capsys):
    """0/1/3/4 are THIS tool's vocabulary; the command it runs has its own.

    The replayed command's code used to be returned verbatim, so a `build_bundle`
    that exited 1 (a document failed) came back as replay's "usage or I/O error", a
    3 came back as "a divergence was demonstrated" — a verdict about the RECORDED
    RUN that nothing here computed — and a 2 came back as a code replay does not
    define at all. What is actually known when the command dies is that the replay
    did not complete, so nothing downstream of it was compared: exit 4.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    for child_rc in (1, 2, 3, 4):
        monkeypatch.setattr(rr.subprocess, "call", lambda cmd, _c=child_rc: _c)
        rc = rr.main(["--report", report_path, "--src", src,
                      "--out", str(tmp_path / ("r%d" % child_rc)), "--execute"])
        err = capsys.readouterr().err
        assert rc == 4, "child exited %d, replay_run returned %r" % (child_rc, rc)
        # The child's code is a fact worth printing — as the child's.
        assert "exited %d" % child_rc in err and "not this tool's" in err


def test_a_failed_replay_does_not_compare_the_bundle_it_did_not_write(
        tmp_path, monkeypatch, capsys):
    """A bundle left at `--out` by an EARLIER replay is not this replay's output.

    Reaching `_compare` after the command died would read that stale bundle, match
    the recorded hash and print `REPRODUCED` for a run that never produced
    anything — the same vacuous claim `_compare` already refuses to make over two
    empty hashes.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    stale = tmp_path / "stale" / rep["doc_id"]
    os.makedirs(str(stale))
    with open(str(stale / "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": rep["doc_id"],
                   "markdown_sha256": rep["markdown_sha256"]}, fh)

    monkeypatch.setattr(rr.subprocess, "call", lambda cmd: 1)
    rc = rr.main(["--report", report_path, "--src", src,
                  "--out", str(tmp_path / "stale"), "--execute", "--compare"])
    captured = capsys.readouterr()
    assert rc == 4
    assert "REPRODUCED" not in captured.out
    assert "cannot answer" in captured.err


def test_a_replay_whose_command_succeeds_still_reports_the_divergence_verdict(
        tmp_path, monkeypatch, capsys):
    """The other direction: executing must not have become "always unverified".

    With every class compared and nothing moved, a successful command still exits
    0 — the fix above touches only the branch where the command failed.
    """
    src, out, report_path = _build(tmp_path)
    rr = _mod("replay_run")
    _only_the_class_under_test(rr, report_path, monkeypatch)
    monkeypatch.setattr(rr.subprocess, "call", lambda cmd: 0)
    rc = rr.main(["--report", report_path, "--src", src,
                  "--out", str(tmp_path / "ok"), "--execute"])
    assert rc == 0
    assert "no divergences" in capsys.readouterr().out


def test_a_report_without_run_provenance_says_so_rather_than_guessing(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(report_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    rep.pop("run")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)
    rr = _mod("replay_run")
    assert rr.main(["--report", report_path]) == 1


def test_the_run_log_joins_documents_to_runs(tmp_path):
    src, out, report_path = _build(tmp_path)
    with open(os.path.join(out, "runs.jsonl"), encoding="utf-8") as fh:
        runs = [json.loads(l) for l in fh]
    with open(os.path.join(out, "manifest.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh]
    assert len(runs) == 1
    assert set(r["run_id"] for r in rows) == set(r["run_id"] for r in runs)
    # the resolved configuration lives once, in the run row
    assert runs[0]["config"]["min_recall"]["from"] == "default"
    assert runs[0]["corpus_sha256"]


def _scan_for_paths(out, needles):
    """Every artifact under ``out`` that contains one of ``needles`` verbatim."""
    leaked = []
    for root, _dirs, files in os.walk(out):
        for fn in files:
            if not fn.endswith((".json", ".jsonl", ".md")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                text = fh.read()
            for needle in needles:
                if needle and len(needle) > 4 and needle in text:
                    leaked.append("%s -> %s" % (os.path.join(root, fn), needle))
    return leaked


def test_no_artifact_ever_contains_an_absolute_host_path(tmp_path):
    # The root CLAUDE.md forbids it and a bundle is published output. This is the
    # test that keeps the run block from quietly becoming the exception.
    #
    # IT PASSED WHILE THE GUARD WAS BROKEN, because it spelled every flag out in
    # full and only ever passed paths to the two flags that were on the redaction
    # list. So it now runs the shapes that actually got through: argparse PREFIX
    # abbreviations (`--sr`, `--ou` — unambiguous, and accepted), and path values
    # on flags nobody thought to list (`--only`, `--tokenizer`).
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    bb = _mod("build_bundle")
    tok_dir = tmp_path / "models" / "tok"
    os.makedirs(str(tok_dir))
    rc = bb.main(["--sr", str(src), "--ou", str(out),
                  "--only", str(src / "spec.docx"),
                  "--tokenizer", "char:%s" % tok_dir,
                  "--run-id", "R1"])
    assert rc == 0
    assert os.path.isfile(os.path.join(str(out), "runs.jsonl"))

    leaked = _scan_for_paths(str(out),
                             (str(tmp_path), os.path.expanduser("~"), REPO))
    assert not leaked, "absolute host paths leaked into artifacts: %s" % leaked

    # ...and the switches themselves are still there, redacted rather than dropped:
    # a record that forgot which flags were passed is not a record of the run.
    with open(os.path.join(str(out), "runs.jsonl"), encoding="utf-8") as fh:
        argv = json.loads(fh.readline())["argv"]
    assert argv[:4] == ["--sr", "<src>", "--ou", "<out>"]
    assert "--only" in argv and "--tokenizer" in argv
    assert argv[argv.index("--tokenizer") + 1].startswith("char:<path:")


def test_the_full_spelling_and_the_abbreviation_redact_the_same_way(tmp_path):
    # The property, stated directly: how the operator spelled the flag cannot
    # change what reaches the artifact.
    from backend.provenance import redact_argv
    paths = {"--src": "<src>", "--out": "<out>"}
    full = redact_argv(["--src", "/vols/x", "--out", "/vols/y"], paths)
    abbrev = redact_argv(["--sr", "/vols/x", "--ou", "/vols/y"], paths)
    assert full[1::2] == abbrev[1::2] == ["<src>", "<out>"]
