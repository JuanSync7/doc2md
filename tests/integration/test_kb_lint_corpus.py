"""
title: Integration — kb_lint grades a corpus of bundles end to end
kind: tests
layer: backend
summary: The corpus linter end to end — closed-vocabulary failure, --strict, corpus-wide see_also and promotion, the corpus gates two clean documents cannot pass, --suggest-aliases, a malformed doc that never stops the scan, --json, --limit, read-only.
"""
# Integration (not unit): writes real bundles to disk and runs the script's main().
import importlib.util
import json
import os
import random
import time
from collections import OrderedDict

import pytest

from backend.ingest import parse_block, render_front_matter
from backend.kb import KNOWLEDGE_FILE, in_knowledge, load_vocab, split_meta

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Pin the committed vocabulary explicitly: the default resolution order prefers
# $DOC2MD_VOCAB and config/vocab.local.yaml, so a deployment override on the host
# would otherwise silently change the thresholds these tests assert against.
VOCAB = os.path.join(REPO, "config", "vocab.yaml")

BODY = "# Overview\n\nThe collector runs on the build host.\n"


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _argv(bundles, *extra):
    return ["--bundles", str(bundles), "--vocab", VOCAB] + list(extra)


def _meta(doc_id, extra=None):
    # A realistically populated, clean metadata block: every value a governed term.
    m = OrderedDict([
        ("id", doc_id),
        ("title", "Collector runbook"),
        ("type", "runbook"),
        ("lang", "en-GB"),
        ("status", "approved"),
        ("confidentiality", "internal"),
        ("owner", "platform-team"),
        ("relations", [OrderedDict([("s", "collector"),
                                    ("p", "runs_on"),
                                    ("o", "build-host")])]),
    ])
    for key, val in (extra or []):
        m[key] = val
    return m


def _write_doc(bundles, dirname, meta, body=BODY):
    """One fake bundle: ``<bundles>/<dirname>/document.md`` with ``meta`` front matter."""
    d = os.path.join(str(bundles), dirname)
    if not os.path.isdir(d):
        os.makedirs(d)
    # Written through the real seam, so every fixture exercises the two-file layout
    # a live bundle actually has rather than a v1 block the pipeline no longer emits.
    front, know = split_meta(OrderedDict(meta))
    fm = OrderedDict([("doc_id", dirname), ("lane", "office"),
                      ("markdown_sha256", "sha-%s" % dirname),
                      ("meta", OrderedDict(front))])
    path = os.path.join(d, "document.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)
    if know:
        payload = OrderedDict([("doc_id", dirname)])
        payload.update(know)
        with open(os.path.join(d, KNOWLEDGE_FILE), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
    return path


def _write_raw(bundles, dirname, text):
    d = os.path.join(str(bundles), dirname)
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, "document.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _snapshot(root):
    """path -> bytes for every file under ``root`` (a linter must not change these)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, str(root))] = fh.read()
    return out


def test_clean_corpus_passes_and_a_closed_vocabulary_violation_is_named_and_fails(
        tmp_path, capsys):
    kb = _mod("kb_lint")
    clean = tmp_path / "clean"
    _write_doc(clean, "b01", _meta("alpha-runbook"))
    _write_doc(clean, "b02", _meta("beta-runbook"))
    assert kb.main(_argv(clean)) == 0
    out = capsys.readouterr().out
    assert "errors=0" in out

    # `type` is governed by a CLOSED vocabulary: an unknown value is a hard error,
    # and the report must name the value so a human can fix it without re-running.
    dirty = tmp_path / "dirty"
    _write_doc(dirty, "b01", _meta("alpha-runbook"))
    _write_doc(dirty, "b02", _meta("beta-runbook", [("type", "cookbook")]))
    assert kb.main(_argv(dirty)) == 1
    out = capsys.readouterr().out
    assert "cookbook" in out
    assert "document_types" in out                   # names the vocabulary too
    assert "b02/document.md" in out                  # and which document
    assert "b01/document.md" not in out              # the clean one is not implicated


def test_strict_fails_on_warnings_while_the_default_run_does_not(tmp_path, capsys):
    # An ALIAS is a warning, never an error: the value is understood, it is just not
    # stored canonically. The default run reports it and still exits 0; --strict is
    # the CI knob that refuses to let warnings accumulate.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["rhel-8"])]))

    assert kb.main(_argv(bundles)) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "rhel-8" in out and "rhel8" in out
    assert "warnings=1" in out and "errors=0" in out

    assert kb.main(_argv(bundles, "--strict")) == 1
    assert "warnings=1" in capsys.readouterr().out   # the same finding, now fatal


def test_see_also_is_resolved_against_the_whole_corpus_not_one_document(
        tmp_path, capsys):
    # THE reason this walks a corpus: only the full id set can say whether a link is
    # dead. The same document graded alone must report the live link as dead too --
    # that contrast is what proves the resolution is corpus-wide rather than local.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook"))
    _write_doc(bundles, "b02", _meta("beta-runbook",
                                     [("see_also", ["alpha-runbook", "ghost-runbook"])]))

    assert kb.main(_argv(bundles)) == 0              # unresolved see_also is a WARN
    out = capsys.readouterr().out
    dead = [l for l in out.splitlines() if "does not resolve to a known page id" in l]
    assert len(dead) == 1                            # exactly one of the two links
    assert "ghost-runbook" in dead[0]
    assert "alpha-runbook" not in dead[0]

    # Narrowing the run takes the sibling id out of the corpus, so the question can
    # no longer be answered. The check is then SKIPPED and the run says so — it does
    # NOT report the live link as dead. An unverifiable pointer must never be
    # reported as verified, and it must never be reported as broken either: a --only
    # run that invented dead links would fail a build over its own truncation.
    assert kb.main(_argv(bundles, "--only", "b02")) == 0
    alone = capsys.readouterr().out
    assert [l for l in alone.splitlines()
            if "does not resolve to a known page id" in l] == []
    assert "PARTIAL" in alone


def test_registry_term_is_offered_for_promotion_only_at_the_document_threshold(
        tmp_path, capsys):
    # Promotion is document FREQUENCY, so it cannot be computed one document at a
    # time. A term on >= promote_at documents is a promotion candidate; a term on
    # fewer is just a proposal and must not be announced as ready.
    kb = _mod("kb_lint")
    promote_at = int(load_vocab(VOCAB).threshold("promote_at", 3))
    bundles = tmp_path / "bundles"
    for i in range(promote_at):
        _write_doc(bundles, "b%02d" % i,
                   _meta("doc-%d" % i, [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b90", _meta("doc-90", [("tags", ["one-off-topic"])]))

    assert kb.main(_argv(bundles)) == 0              # a proposal is never an error
    out = capsys.readouterr().out
    assert "REGISTRY HEALTH (corpus-wide)" in out
    promote = [l for l in out.splitlines() if l.strip().startswith("promote")]
    assert len(promote) == 1
    assert "fleet-upgrade" in promote[0]
    assert "one-off-topic" not in promote[0]         # below threshold: still waiting
    assert ("(>= %d docs)" % promote_at) in promote[0]


def test_one_malformed_document_neither_stops_the_scan_nor_vanishes_from_it(
        tmp_path, capsys):
    # A corpus scan that aborts on the first bad file is useless on a real corpus,
    # and one that skips it silently is worse: the document would read as clean.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook"))
    _write_raw(bundles, "b02", '---\nmeta:\n\tid: "beta"\n---\n\n' + BODY)  # tab indent
    _write_doc(bundles, "b03", _meta("gamma-runbook"))

    rc = kb.main(_argv(bundles))
    out, err = capsys.readouterr()
    assert rc != 0                                   # unreadable is a failure, not a skip
    assert "UNREADABLE b02/document.md" in out
    assert "unreadable=1" in out
    assert "documents=3" in err                      # all three were found ...
    assert "RESULT documents=2" in out               # ... and the two good ones graded
    assert "errors=0 warnings=0" in out              # the good pair is still clean


def test_json_report_carries_documents_and_corpus_and_is_written_atomically(
        tmp_path, capsys):
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("type", "cookbook")]))
    reports = tmp_path / "reports"
    os.makedirs(str(reports))
    target = os.path.join(str(reports), "kb_lint.json")

    assert kb.main(_argv(bundles, "--json", target)) == 1
    report = json.load(open(target, encoding="utf-8"))

    by_path = dict((d["path"], d) for d in report["documents"])
    assert sorted(by_path) == ["b01/document.md", "b02/document.md"]
    bad = by_path["b02/document.md"]
    assert bad["errors"] == 1
    assert any(f[0] == "vocab-unknown" and "cookbook" in f[3] for f in bad["findings"])
    assert by_path["b01/document.md"]["errors"] == 0
    assert by_path["b01/document.md"]["proposals"]["tags"] == ["fleet-upgrade"]
    assert report["unreadable"] == []
    # the corpus block is the part no per-document report can carry
    assert report["corpus"]["tags"]["documents"] == 2
    assert report["corpus"]["tags"]["proposed"]["fleet-upgrade"] == 1
    assert "singleton_rate" in report["corpus"]["tags"]

    # atomic write: the temp file is renamed into place, never left behind
    assert sorted(os.listdir(str(reports))) == ["kb_lint.json"]


def test_limit_announces_the_documents_it_deferred_rather_than_capping_silently(
        tmp_path, capsys):
    # A cap that does not say what it skipped turns a partial run into a false clean
    # bill of health, which is exactly how a bad document survives CI.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    for i in range(3):
        _write_doc(bundles, "b%02d" % i, _meta("doc-%d" % i))

    assert kb.main(_argv(bundles, "--limit", "1")) == 0
    out, err = capsys.readouterr()
    assert "deferred 2" in err                       # the count, not just a mention
    assert "documents=3" in err                      # what the walk actually found
    assert "RESULT documents=1" in out               # what was graded


def test_two_individually_perfect_documents_still_fail_the_corpus_gates(
        tmp_path, capsys):
    # THE case the corpus half exists for. Both documents lint clean on their own —
    # every value governed, every pointer resolved — and the corpus is broken anyway:
    # one id claimed twice, and one entity spelled two ways is two graph nodes. No
    # amount of per-file linting can ever see either.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("fleet-runbook", [
        ("entities", {"software": [OrderedDict([("name", "Docker"),
                                                ("type", "Software")])]})]))
    _write_doc(bundles, "b02", _meta("fleet-runbook", [
        ("entities", {"software": [OrderedDict([("name", "docker"),
                                                ("type", "Software")])]})]))

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    assert "errors=0 warnings=0" in out              # every DOCUMENT is clean ...
    assert "corpus_err=2" in out                  # ... and the CORPUS is not
    assert "2 documents claim the same `id`" in out
    assert "one entity, 2 spellings" in out
    assert "b01/document.md" in out and "b02/document.md" in out


def test_suggest_aliases_prints_paste_ready_entries_and_edits_nothing(
        tmp_path, capsys):
    # An alias is a permanent claim that two strings mean the same thing, so the
    # linter proposes and a human commits. A tool that edited the vocabulary it is
    # grading against could never be run twice with the same meaning.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("tags", ["Fleet_Upgrade"])]))
    with open(VOCAB, "rb") as fh:
        before = fh.read()

    assert kb.main(_argv(bundles, "--suggest-aliases")) == 1
    out = capsys.readouterr().out
    assert "# paste into config/vocab.yaml" in out
    # Values are QUOTED because the block is rendered by the same codec that will
    # parse it — an alias whose canonical term is `no`/`on`/`off` would otherwise be
    # retyped into a boolean by any YAML 1.1 reader the moment it was pasted.
    assert '    Fleet_Upgrade: "fleet-upgrade"' in out   # canonical: the lowercase slug
    with open(VOCAB, "rb") as fh:
        assert fh.read() == before                    # ... and nothing was written

    # And the block it prints must actually parse. A suggestion that fails on paste
    # is worse than none: it fails later, in someone's editor, with no clue why.
    block = out.split("# paste into config/vocab.yaml\n", 1)[1].split("\n\nRESULT")[0]
    assert parse_block(block)["tags"]["aliases"] == {"Fleet_Upgrade": "fleet-upgrade"}

    # Nothing to propose must say so rather than printing an empty heading, or an
    # operator cannot tell "no collisions" from "the flag did not work".
    clean = tmp_path / "clean"
    _write_doc(clean, "b01", _meta("alpha-runbook"))
    assert kb.main(_argv(clean, "--suggest-aliases")) == 0
    assert "no spelling collisions" in capsys.readouterr().out


def test_a_spelling_the_vocabulary_format_cannot_express_is_refused_not_emitted(
        tmp_path, capsys):
    # A tag containing a colon collides normally, but `a:b: canonical` is not a
    # mapping entry. Emitting it would hand the operator a block that breaks their
    # vocabulary file; the refusal is the useful answer, because a spelling the
    # format cannot hold has to be fixed at the source rather than aliased away.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["a:b"])]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("tags", ["A:B"])]))

    assert kb.main(_argv(bundles, "--suggest-aliases")) == 1
    out = capsys.readouterr().out
    assert "# paste into config/vocab.yaml" not in out
    assert "NOT aliasable" in out and "tags: A:B -> a:b" in out


def test_a_narrowed_run_skips_the_gates_that_truncation_would_invert(
        tmp_path, capsys):
    # A subset can only ever MISS a collision, so identity and synonymy stay honest.
    # The other four would manufacture findings out of the truncation itself — a
    # see_also whose target was excluded, a "current" schema version sitting in a
    # skipped document. Skipping is only safe because the run says it skipped.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook",
                                     [("see_also", ["beta-runbook"]),
                                      ("schema_version", 1)]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("schema_version", 2)]))

    assert kb.main(_argv(bundles, "--only", "b01")) == 0
    out = capsys.readouterr().out
    # Named ONE BY ONE, with what each stops answering — a joined list on one line
    # is the shape an operator reads past.
    skipped = [l for l in out.splitlines() if "SKIPPED" in l]
    assert len(skipped) == 4
    for name in ("graph", "skew", "coverage", "vocab_usage"):
        assert any(name in l for l in skipped), name
    assert "backfill" in " ".join(skipped)                      # skew, said in words
    assert "resolve to no document in this corpus" not in out   # would be an artefact
    assert "backfill work list" not in out
    assert "[PARTIAL]" in out

    # The whole walk answers both questions, and neither is invented.
    assert kb.main(_argv(bundles)) == 0
    whole = capsys.readouterr().out
    assert "SKIPPED" not in whole
    assert "backfill work list" in whole


def test_the_json_report_carries_the_corpus_gates_alongside_registry_health(
        tmp_path, capsys):
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("dup-id", [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b02", _meta("dup-id", [("tags", ["Fleet_Upgrade"])]))
    target = os.path.join(str(tmp_path), "kb_lint.json")

    assert kb.main(_argv(bundles, "--json", target)) == 1
    report = json.load(open(target, encoding="utf-8"))

    # Registry health keeps its own key — the two answer different questions and a
    # consumer of one must not have to learn the other.
    assert report["corpus"]["tags"]["documents"] == 2
    found = dict((f[0], f) for f in report["corpus_findings"])
    assert found["identity-collision"][1] == "error"
    assert sorted(found["identity-collision"][4]) == ["b01/document.md",
                                                      "b02/document.md"]
    assert found["synonym-collision"][1] == "error"
    assert report["alias_suggestions"]["tags"] == {"Fleet_Upgrade": "fleet-upgrade"}
    assert report["corpus_skipped"] == []
    assert report["corpus_metrics"]["identity"]["collisions"] == 1


def test_strict_promotes_a_corpus_warning_to_a_failure_too(tmp_path, capsys):
    # A corpus warning is counted separately from a document's, because it is not
    # attributable to any one document — but --strict must still refuse to let it
    # accumulate, or half the gates are exempt from the CI knob.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    # Both versions stamped everywhere, so exactly ONE corpus warning is in play:
    # b01 is behind on schema_version. Anything else would make the count ambiguous.
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("schema_version", 1),
                                                       ("vocab_version", 1)]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("schema_version", 2),
                                                      ("vocab_version", 1)]))

    assert kb.main(_argv(bundles)) == 0
    out = capsys.readouterr().out
    assert "errors=0 warnings=0" in out              # no DOCUMENT has a warning ...
    assert "corpus_warn=1" in out                # ... the corpus does
    assert "backfill work list" in out
    assert kb.main(_argv(bundles, "--strict")) == 1


def test_one_unreadable_document_does_not_disable_the_corpus_gates(tmp_path, capsys):
    # An unreadable document used to set `partial`, which set known_ids = None and
    # switched off see_also resolution, the graph, skew, coverage and
    # vocabulary-usage gates FOR THE WHOLE CORPUS — reported as one INFO line. At
    # 1000 bundles that is a clean-looking all-clear over a corpus nobody checked.
    # It is a finding about THAT document now, and every gate still runs.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook",
                                     [("see_also", ["beta-runbook"]),
                                      ("schema_version", 1)]))
    _write_doc(bundles, "b03", _meta("gamma-runbook", [("schema_version", 2)]))
    _write_raw(bundles, "b02", '---\nmeta:\n\tid: "beta-runbook"\n---\n\n' + BODY)

    assert kb.main(_argv(bundles)) == 1              # unreadable is still a failure
    out = capsys.readouterr().out
    assert "UNREADABLE b02/document.md" in out
    assert "[PARTIAL]" not in out                    # it is not a narrowed walk
    assert "SKIPPED" not in out                      # and nothing was skipped
    assert "backfill work list" in out               # skew still ran ...
    # ... and the one document that could not be read is an ERROR about itself.
    bad = [l for l in out.splitlines() if "contributes to NO corpus check" in l]
    assert len(bad) == 1 and "b02/document.md" in bad[0]
    # The denominator change is disclosed by name rather than by switching gates off.
    assert "every check below still ran" in out


def test_a_see_also_into_an_unreadable_document_is_reported_with_its_caveat(
        tmp_path, capsys):
    # The one thing an unreadable document genuinely can invert: b01's see_also
    # points at the document that failed to parse. Silence would be a false clean
    # bill; an unqualified "dead link" would be a defect the corpus does not have.
    # So it is reported WITH the reason it may be wrong, and the caveat is absent
    # when every document parsed.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook",
                                     [("see_also", ["beta-runbook"])]))
    _write_raw(bundles, "b02", '---\nmeta:\n\tid: "beta-runbook"\n---\n\n' + BODY)

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    dead = [l for l in out.splitlines() if "does not resolve to a known page id" in l]
    assert len(dead) == 1
    assert "could not be read" in dead[0]            # the caveat, with its count
    assert "1 document(s)" in dead[0]

    clean = tmp_path / "clean"
    _write_doc(clean, "b01", _meta("alpha-runbook", [("see_also", ["ghost"])]))
    _write_doc(clean, "b02", _meta("beta-runbook"))
    assert kb.main(_argv(clean)) == 0
    dead = [l for l in capsys.readouterr().out.splitlines()
            if "does not resolve to a known page id" in l]
    assert len(dead) == 1 and "could not be read" not in dead[0]


def test_quiet_never_hides_the_findings_that_decide_the_exit_code(tmp_path, capsys):
    # Under --strict a warning DECIDES the exit code. A CI log showing no findings
    # beside a non-zero exit is unactionable, so --quiet trims noise and never trims
    # the evidence for the verdict.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["rhel-8"])]))

    assert kb.main(_argv(bundles, "--quiet")) == 0
    assert "rhel-8" not in capsys.readouterr().out   # a warning, and the run passed

    assert kb.main(_argv(bundles, "--quiet", "--strict")) == 1
    out = capsys.readouterr().out
    assert "rhel-8" in out                           # now it decides the exit code


def test_the_result_line_counters_cannot_be_confused_by_a_grep(tmp_path, capsys):
    # `corpus_errors=0` contains `errors=0`, so a CI check grepping for the document
    # counter would match the corpus one and read a failing run as clean.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("dup"))
    _write_doc(bundles, "b02", _meta("dup"))

    assert kb.main(_argv(bundles)) == 1
    result = [l for l in capsys.readouterr().out.splitlines()
              if l.startswith("RESULT ")][0]
    assert " errors=0 " in result and " corpus_err=1 " in result
    assert result.count("errors=") == 1              # exactly one, unambiguously


def test_a_narrowed_run_names_the_flag_that_narrowed_it(tmp_path, capsys):
    # An operator told `--limit` deferred documents they never capped goes looking
    # for the bug in the wrong place.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    for i in range(3):
        _write_doc(bundles, "b%02d" % i, _meta("doc-%d" % i))

    kb.main(_argv(bundles, "--only", "b00"))
    assert "--only deferred 2 more" in capsys.readouterr().err
    kb.main(_argv(bundles, "--limit", "1"))
    assert "--limit deferred 2 more" in capsys.readouterr().err


def test_linting_twice_leaves_the_corpus_byte_identical(tmp_path, capsys):
    # kb_lint is a read-only walker; anything else makes it unsafe to run in CI or
    # against a corpus someone else owns.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b02", _meta("beta-runbook", [("type", "cookbook")]))

    rc1 = kb.main(_argv(bundles))
    before = _snapshot(bundles)
    first = capsys.readouterr().out
    rc2 = kb.main(_argv(bundles))
    after = _snapshot(bundles)
    second = capsys.readouterr().out

    assert rc1 == rc2 == 1
    assert before == after                           # same paths, same bytes
    assert first == second                           # and a deterministic report


def test_the_linter_grades_both_files_and_says_which_one_to_open(tmp_path, capsys):
    # The linter works on ONE merged mapping, which is what keeps every rule in
    # backend.kb unaware of the storage split — but a person still has to be told
    # which file the offending field is in, or the split trades a token cost for a
    # scavenger hunt through two files per document.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [
        ("type", "cookbook"),                                    # document.md
        ("relations", [OrderedDict([("s", "a"), ("p", "NOT_A_PREDICATE"),
                                    ("o", "b")])]),              # knowledge.json
    ]))

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    bad = [l for l in out.splitlines() if "not in the closed vocabulary" in l]
    assert len(bad) == 2
    doc_line = [l for l in bad if "cookbook" in l][0]
    kn_line = [l for l in bad if "NOT_A_PREDICATE" in l][0]
    assert KNOWLEDGE_FILE in kn_line
    assert KNOWLEDGE_FILE not in doc_line       # a descriptor is not mis-attributed


def test_the_json_report_names_the_file_each_finding_belongs_to(tmp_path, capsys):
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [
        ("type", "cookbook"),
        ("relations", [OrderedDict([("s", "a"), ("p", "NOPE"), ("o", "b")])]),
    ]))
    target = os.path.join(str(tmp_path), "kb_lint.json")
    assert kb.main(_argv(bundles, "--json", target)) == 1
    findings = json.load(open(target, encoding="utf-8"))["documents"][0]["findings"]

    by_file = dict((f[3].split("'")[1], f[4]) for f in findings
                   if f[0] == "vocab-unknown")
    assert by_file["cookbook"] == "document.md"
    assert by_file["NOPE"] == KNOWLEDGE_FILE
    # The first four positions are unchanged, so anything already reading the report
    # positionally keeps working.
    assert all(f[1] in ("error", "warn", "info") for f in findings)


def test_a_document_whose_knowledge_lives_only_in_the_sidecar_is_still_graded(
        tmp_path, capsys):
    # A bundle may carry a sidecar and no `meta` block at all — that is what a
    # knowledge-only enrichment leaves behind. Reporting it as "no metadata block"
    # would hide every entity and relation it holds.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    d = os.path.join(str(bundles), "b01")
    os.makedirs(d)
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(OrderedDict([("doc_id", "b01")])) + "\n" + BODY)
    with open(os.path.join(d, KNOWLEDGE_FILE), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": "b01",
                   "relations": [{"s": "a", "p": "NOT_A_PREDICATE", "o": "b"}]}, fh)

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    assert "no metadata block" not in out
    assert "NOT_A_PREDICATE" in out


def test_an_unreadable_sidecar_is_a_failure_not_a_document_that_looks_clean(
        tmp_path, capsys):
    # Grading a document on half its metadata and calling the result clean is the
    # silent skip this package exists to prevent.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook"))
    with open(os.path.join(str(bundles), "b01", KNOWLEDGE_FILE), "w",
              encoding="utf-8") as fh:
        fh.write("{ not json")

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    assert "UNREADABLE" in out and KNOWLEDGE_FILE in out
    assert "unreadable=1" in out


def test_a_provenance_record_for_a_pointer_field_is_not_read_as_a_pointer(
        tmp_path, capsys):
    # `see_also`, `control` and `protects` are pointer fields AND field names, so a
    # ref walk that descends into `_provenance` reports every document recording
    # provenance for one of them as carrying a malformed pointer.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [
        ("see_also", ["beta-runbook"]),
        ("_provenance", OrderedDict([
            ("see_also", OrderedDict([("tier", 2), ("source", "generated")])),
        ])),
    ]))
    _write_doc(bundles, "b02", _meta("beta-runbook"))

    assert kb.main(_argv(bundles)) == 0
    out = capsys.readouterr().out
    assert "expected a non-empty string pointer" not in out
    assert "errors=0" in out


def test_a_field_in_both_files_is_reported_rather_than_silently_resolved(
        tmp_path, capsys):
    # Two live copies of one field. merge_meta picks a winner so readers see one
    # coherent view, and the next enrichment run then REWRITES the loser away — so
    # a state that silently costs data has to be reported. The merged mapping the
    # linter grades cannot show that both ever existed, which is why this finding
    # cannot come from lint_document.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook"))       # relations -> sidecar
    path = os.path.join(str(bundles), "b01", "document.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace('  id: "alpha-runbook"',
                        '  id: "alpha-runbook"\n  relations:\n'
                        '    - s: "x"\n      p: "runs_on"\n      o: "y"')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)

    assert kb.main(_argv(bundles)) == 1
    out = capsys.readouterr().out
    assert "present in BOTH document.md and %s" % KNOWLEDGE_FILE in out
    assert "errors=1" in out


def test_a_sibling_markdown_file_does_not_inherit_the_bundles_sidecar(
        tmp_path, capsys):
    # The sidecar belongs to document.md, not to the DIRECTORY. Bound by directory,
    # a notes.md beside it inherited the same entities and relations — double-counting
    # every entity corpus-wide and inventing an id collision between a document and
    # its own neighbour.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook"))
    with open(os.path.join(str(bundles), "b01", "notes.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# Scratch\n\nNotes.\n")

    assert kb.main(_argv(bundles)) == 0
    out = capsys.readouterr().out
    assert "documents=2 graded=1 no-metadata=1" in out
    assert "notes.md  no metadata block" in out
    # ... and with the sidecar counted once, there is no phantom id collision.
    assert "claim the same" not in out


def test_every_findings_row_in_the_json_report_has_the_same_shape(tmp_path, capsys):
    # A consumer must not have to branch on document state to read a findings list.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _write_doc(bundles, "b01", _meta("alpha-runbook", [("type", "cookbook")]))
    with open(os.path.join(str(bundles), "b02.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(OrderedDict([("doc_id", "b02")])) + "\n" + BODY)
    target = os.path.join(str(tmp_path), "kb_lint.json")

    assert kb.main(_argv(bundles, "--json", target)) == 1
    report = json.load(open(target, encoding="utf-8"))
    rows = [f for d in report["documents"] for f in d["findings"]]
    assert rows                                   # both kinds of document present
    assert all(len(f) == 5 for f in rows)
    assert all(f[4] in ("document.md", KNOWLEDGE_FILE) for f in rows)


# ---------------------------------------------------------------- promotion

def _vocab_copy(tmp_path):
    """A writable copy of the shipped vocabulary — a test must never edit the repo's."""
    dst = os.path.join(str(tmp_path), "vocab.yaml")
    with open(VOCAB, encoding="utf-8") as fh:
        text = fh.read()
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dst, text


def test_promote_writes_the_earned_terms_and_is_safe_to_run_twice(tmp_path, capsys):
    # Promotion was frequency-counted, printed, and then left for somebody to hand
    # copy — so `<field>_proposed` grew forever and every classified document stayed
    # `pending` on terms the corpus had long since earned.
    kb = _mod("kb_lint")
    vocab_file, original = _vocab_copy(tmp_path)
    promote_at = int(load_vocab(vocab_file).threshold("promote_at", 3))
    bundles = tmp_path / "bundles"
    for i in range(promote_at):
        _write_doc(bundles, "b%02d" % i,
                   _meta("doc-%d" % i, [("tags", ["fleet-upgrade"])]))
    _write_doc(bundles, "b90", _meta("doc-90", [("tags", ["one-off-topic"])]))

    argv = ["--bundles", str(bundles), "--vocab", vocab_file, "--promote"]
    kb.main(argv)
    out = capsys.readouterr().out
    assert "promoted into" in out and "fleet-upgrade" in out
    assert "one-off-topic" not in out.split("promoted into")[1]   # below threshold

    after = load_vocab(vocab_file)
    assert "fleet-upgrade" in after.values("tags")
    assert "one-off-topic" not in after.values("tags")
    # Terms moved, so the vocabulary version moved — that is what lets skew_report
    # name the documents this promotion invalidates.
    assert int(after.version) == int(load_vocab(text=original).version) + 1
    # Everything a human wrote in that file is still there.
    with open(vocab_file, encoding="utf-8") as fh:
        text = fh.read()
    assert "# doc2md — controlled vocabularies for document metadata." in text
    assert "Hard-closing tags at document 1 means guessing the corpus shape." in text

    # SAFE TO RUN TWICE: the term is governed now, so it is no longer a candidate,
    # so there is nothing to write and not one byte changes.
    kb.main(argv)
    second = capsys.readouterr().out
    assert "nothing has reached the promotion threshold" in second
    with open(vocab_file, encoding="utf-8") as fh:
        assert fh.read() == text


def test_promote_is_refused_on_a_walk_the_operator_narrowed(tmp_path, capsys):
    # Promotion is a DOCUMENT-COUNT decision and --only/--limit chooses which
    # documents exist. Counting three uses out of a five-document slice of a
    # thousand promotes a term the corpus never voted for, permanently.
    kb = _mod("kb_lint")
    vocab_file, _original = _vocab_copy(tmp_path)
    bundles = tmp_path / "bundles"
    for i in range(6):
        _write_doc(bundles, "b%02d" % i,
                   _meta("doc-%d" % i, [("tags", ["fleet-upgrade"])]))
    with open(vocab_file, "rb") as fh:
        before = fh.read()

    rc = kb.main(["--bundles", str(bundles), "--vocab", vocab_file,
                  "--promote", "--limit", "3"])
    out = capsys.readouterr().out
    assert rc == 1                                   # a refused write is not a pass
    assert "[promote] REFUSED" in out and "--limit" in out
    with open(vocab_file, "rb") as fh:
        assert fh.read() == before                   # nothing was written


def test_a_promotion_list_too_long_to_read_is_counted_rather_than_dumped(
        tmp_path, capsys):
    # A 1000-document silicon corpus put 6,526 keyword candidates on this one line —
    # ~100KB of comma-separated text that hides every other field's candidates above
    # and below it.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    terms = ["candidate-term-%03d" % i for i in range(40)]
    for i in range(3):
        _write_doc(bundles, "b%02d" % i, _meta("doc-%d" % i, [("tags", terms)]))

    kb.main(_argv(bundles))
    line = [l for l in capsys.readouterr().out.splitlines()
            if l.strip().startswith("promote")][0]
    assert "40 candidate(s)" in line                  # the count is never dropped
    assert "and 28 more" in line                      # ... and so is what it hid
    assert len(line) < 600


# ------------------------------------------------------- D7: soundness at scale

def _scale_corpus(bundles, n_docs=1000, shared=("kubernetes", "ubernetes")):
    """``n_docs`` bundles that look like a real registry corpus: a small shared
    vocabulary plus one-off identifiers per document."""
    rng = random.Random(20260819)
    letters = "abcdefghijklmnopqrstuvwxyz"
    for i in range(n_docs):
        oneoff = ["".join(rng.choice(letters) for _ in range(12)) for _ in range(6)]
        tags = list(oneoff)
        # Entities are NOT scoped by document frequency — an entity named once is
        # still a node — so these five thousand names go through the blocked sweep
        # in full. That is the half of the run the shingle index has to carry.
        ents = [{"name": "".join(rng.choice(letters) for _ in range(14)),
                 "type": "Software"} for _ in range(5)]
        # The planted near-duplicate pair differs in its FIRST characters, which is
        # exactly what the old first-two-character bucketing could never compare.
        # Both spellings sit on enough documents to stay inside a scoped sweep.
        if i % 100 == 0:
            tags.append(shared[0])
        elif i % 100 == 1:
            tags.append(shared[1])
        _write_doc(bundles, "b%04d" % i,
                   _meta("scale-doc-%04d" % i,
                         [("tags_proposed", tags),
                          ("entities", {"software": ents})]))


def test_the_corpus_gates_stay_sound_and_affordable_at_a_thousand_documents(
        tmp_path, capsys):
    # Rubric D7. Two things have to hold at once, and the old code traded one for
    # the other: the gates must still ANSWER at scale (the sweep used to fall back
    # to unsound first-two-character bucketing above 4000 terms) and they must still
    # RUN in a time somebody will wait for.
    kb = _mod("kb_lint")
    bundles = tmp_path / "bundles"
    _scale_corpus(bundles, n_docs=1000)

    started = time.time()
    rc = kb.main(_argv(bundles))
    elapsed = time.time() - started
    out = capsys.readouterr().out

    assert rc == 0
    assert "RESULT documents=1000" in out
    # 5000 entity names went through the sweep with nothing skipped, and the run
    # says so — the old code reported "bucketed by leading characters" here.
    assert "no pair was skipped" in out
    # SOUND: the planted near-duplicate is found even though it differs in its first
    # two characters and is buried in ~6000 one-off terms.
    similar = [l for l in out.splitlines() if "% similar" in l]
    assert any("kubernetes" in l and "ubernetes" in l for l in similar), similar[:5]
    # DISCLOSED: the scope reduction is a warning carrying both counts, never silence.
    scoped = [l for l in out.splitlines() if "sweep cap" in l]
    assert len(scoped) == 1 and "not compared" in scoped[0]
    # ... and the shape that caused it is named for what it is.
    assert "registry growing faster than the corpus" in out
    # NOTHING was skipped: a big corpus is not a partial one.
    assert "[PARTIAL]" not in out and "SKIPPED" not in out
    # AFFORDABLE. The old sweep was O(n^2) inside first-two-character buckets and
    # took ~58s over 24,630 keyword terms; this budget is loose enough for a shared
    # CI box and tight enough that a return to quadratic sweeping fails here.
    assert elapsed < 60, "corpus gates took %.1fs over 1000 documents" % elapsed
