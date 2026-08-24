"""
title: Integration — enrich_metadata fills the document metadata block (stub model)
kind: tests
layer: backend
summary: Deterministic tiers offline, tier-2 PENDING without a model; body + pipeline front matter untouched; closed-vocabulary / authored-only / not-in-schema refusals; outage leaves gaps not guesses; cached, idempotent, exit codes 0/2/1.
"""
import hashlib
import importlib.util
import json
import os
import sys
from collections import OrderedDict

import pytest

from backend.ingest import render_front_matter, split_front_matter
from backend.kb import (FIELDS, KNOWLEDGE_FILE, KNOWLEDGE_HEADER, META_KEY,
                        PROVENANCE_KEY, in_knowledge, merge_meta, model_writable)

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BODY = ("# Memory Controller Design Spec\n\n"
        "The arbiter serves the read and write queues. See `DDR_PHY_TIMEOUT` and\n"
        "`refresh_interval_ns` for the tunables.\n\n"
        "## Rollback\n\nRun the restore playbook.\n")

# Every model-writable field in the schema. The denominator this whole stage is
# graded against, so it is read from the schema rather than written down here.
WRITABLE = [f.name for f in FIELDS if model_writable(f.name)]

# Terms seeded into the five registry vocabularies (see _seeded_vocab). A registry
# starts empty on purpose, so with the committed vocabulary a registry field can
# never leave PENDING — which is exactly why "nothing outstanding" needs a seeded one.
REG = ["reference_architecture", "platform_engineering", "ddr", "memory-controller"]

# One valid answer per model-writable field, against the seeded vocabulary. Every
# knowledge record cites a section of BODY: "which section says this?" is now part of
# the contract, and a record that cannot answer it is discarded on arrival.
REF = "#rollback"

FULL_REPLY = [
    ("title", "Memory Controller Design Spec"),
    ("abstract", "How the arbiter serves the read and write queues."),
    ("type", "design"),
    ("subtype", ["reference_architecture"]),
    ("lang", "en-GB"),
    ("tags", ["ddr", "memory-controller"]),
    ("keywords", ["ddr"]),
    ("topics", ["platform_engineering"]),
    ("audience", ["platform_engineering"]),
    ("entities", {"hosts": [{"name": "ddr-node-1", "ref": REF}]}),
    ("relations", [{"s": "arbiter", "p": "requires", "o": "ddr-phy",
                    "mode": "silent", "ref": REF}]),
    ("decisions", [{"id": "d1", "status": "accepted", "text": "Round robin.",
                    "ref": REF}]),
    ("risks", [{"id": "r1", "impact": "high", "mode": "delayed",
                "text": "Refresh starvation.", "ref": REF}]),
    ("open_questions", [{"id": "q1", "text": "What is the refresh budget?",
                         "ref": REF}]),
    ("links", {"internal": [{"title": "Runbook", "url": "runbook.md",
                             "ref": REF}]}),
    ("see_also", ["ddr-phy-guide"]),
]


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _StubClient(object):
    """Counts calls and records prompts; returns a canned reply. No network."""

    def __init__(self, reply, ok=True, model="stub-lm"):
        self.calls = 0
        self.prompts = []
        self.model = model
        self._reply = reply
        self._ok = ok

    def text_result(self, prompt, max_tokens=0, response_format=None):
        self.calls += 1
        self.prompts.append(prompt)
        text = self._reply if isinstance(self._reply, str) \
            else json.dumps(dict(self._reply))
        return {"text": text, "finish_reason": "stop", "ok": self._ok}

    def healthy(self, timeout=5):
        return True


def _bundle(root, doc_id="aaa", body=BODY, title="Memory Controller Design Spec"):
    """A build_bundle-shaped bundle: document.md (front matter + body) + report.json."""
    d = os.path.join(str(root), doc_id)
    os.makedirs(d)
    fm = OrderedDict([
        ("doc_id", doc_id),
        ("source_format", "docx"),
        ("lane", "office"),
        ("source_relpath", "specs/%s.docx" % doc_id),
        ("markdown_sha256", hashlib.sha256(body.encode("utf-8")).hexdigest()),
        ("converter", "doc2md.ooxml/1"),
        ("lossless", "true"),
        ("source_title", title),
    ])
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)   # the assembler's layout
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"doc_id": doc_id, "status": "ok",
                   "losslessness": {"gate": "pass"}}, fh)
    return d


def _bundles(tmp_path, **kw):
    root = os.path.join(str(tmp_path), "bundles")
    os.makedirs(root)
    return root, _bundle(root, **kw)


def _read(doc_dir):
    with open(os.path.join(doc_dir, "document.md"), encoding="utf-8") as fh:
        return fh.read()


def _knowledge(doc_dir):
    """The raw ``knowledge.json`` object, or ``None`` when the sidecar is absent."""
    path = os.path.join(doc_dir, KNOWLEDGE_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=OrderedDict)


def _meta(doc_dir):
    """``(front_matter, MERGED metadata, body)``.

    Merged, because the metadata a document carries now lives in two files: the
    descriptors in front matter, the graph payload in ``knowledge.json``. Every
    assertion about "what this document says" should be about the whole view; the
    tests that care WHERE a field landed read the two sides explicitly.
    """
    fm, body = split_front_matter(_read(doc_dir))
    kn = _knowledge(doc_dir) or {}
    payload = dict((k, v) for k, v in kn.items() if k not in KNOWLEDGE_HEADER)
    return fm, merge_meta(fm.get(META_KEY) or {}, payload), body


def _report(doc_dir):
    with open(os.path.join(doc_dir, "report.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _coverage(root):
    return _jsonl(os.path.join(root, "_kb_meta_coverage.jsonl"))


def _reasons(root):
    """{field: refusal reason} for the LAST run recorded in the coverage log."""
    return dict((name, reason) for name, _value, reason in _coverage(root)[-1]["rejected"])


def _seeded_vocab(tmp_path):
    """The committed vocabulary with its five registries pre-populated.

    A registry admits a term only by promotion, so on the shipped vocabulary the
    five registry fields can never be filled and a document can never reach "nothing
    outstanding". Seeding them is the only way to exercise the complete/exit-0 path,
    and the count assert keeps this coupled to the real file rather than guessing.
    """
    with open(os.path.join(REPO, "config", "vocab.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("values: []") == 5, "config/vocab.yaml registry shape moved"
    text = text.replace("values: []", "values: [%s]" % ", ".join(REG))
    path = os.path.join(str(tmp_path), "vocab.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- #


def test_the_deterministic_tiers_are_written_with_no_model_and_tier2_stays_pending(tmp_path):
    # The property that makes backfill cheap: a corpus can be brought up to a new
    # schema revision entirely offline, with the model tier visibly outstanding
    # rather than silently absent.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)

    rc = em.main(["--bundles", root, "--run-id", "R1"])          # no client at all
    assert rc == 0                       # pending is reported, not a failure

    _fm, meta, _body = _meta(d)
    for name in ("uid", "id", "slug", "word_count", "reading_time_minutes",
                 "source", "extraction"):
        assert name in meta, name
    # ONE identity, from the path, so it cannot collide with another document's and
    # cannot move when somebody retitles this one. `uid` is now its alias, not a peer.
    assert meta["id"] == meta["uid"] == "specs/aaa.docx"
    assert meta["slug"] == "memory-controller-design-spec"        # the title's URL form
    assert meta["word_count"] > 0 and meta["reading_time_minutes"] >= 1
    assert meta["source"]["uri"] == "specs/aaa.docx"
    assert meta["source"]["url"] == "specs/aaa.docx"             # a URI reference home
    assert meta["source"]["is_derivative"] is True               # every bundle is converted
    assert meta["extraction"]["run_at"] == "R1"
    assert meta[PROVENANCE_KEY]["uid"]["source"] == "derived"

    # THE FLOOR: a no-model run is not a blank page. `title` comes off the docx core
    # property, so it is EXTRACTED evidence; `abstract` is cut from the lede, so it is
    # DERIVED and a model may still improve it. Nothing else is invented.
    assert meta["title"] == "Memory Controller Design Spec"
    assert meta[PROVENANCE_KEY]["title"]["source"] == "extracted"
    assert meta["abstract"].startswith("The arbiter serves the read and write queues")
    assert meta[PROVENANCE_KEY]["abstract"]["source"] == "derived"
    assert sorted(n for n in WRITABLE if n in meta) == ["abstract", "title"]

    gate = _report(d)["doc_meta"]
    assert gate["gate"] == "disabled" and gate["enabled"] is False
    assert gate["expected"] == len(WRITABLE)
    assert gate["filled"] == 2 and gate["invalid"] == 0
    assert gate["pending"] == len(WRITABLE) - 2


def test_the_body_survives_byte_for_byte_and_the_pipeline_front_matter_is_untouched(tmp_path):
    # markdown_sha256 covers the BODY only. If rewriting front matter could
    # invalidate it, every line span, image index and losslessness verdict computed
    # against that hash would silently go stale.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    before, _ = split_front_matter(_read(d))

    assert em.main(["--bundles", root, "--run-id", "R1"],
                   client=_StubClient(FULL_REPLY)) == 0

    fm, meta, body = _meta(d)
    assert body == BODY
    assert fm["markdown_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    for key in before:
        assert fm[key] == before[key], key                       # value AND type kept
    # the block is appended; it never reorders or displaces a key it does not own
    assert list(fm.keys()) == list(before.keys()) + [META_KEY]
    assert meta                                                   # ...and it did write one


def test_accepted_values_carry_generated_provenance_naming_the_model_and_the_prompt(tmp_path):
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    stub = _StubClient(FULL_REPLY, model="stub-lm")

    em.main(["--bundles", root, "--run-id", "R1"], client=stub)

    _fm, meta, _body = _meta(d)
    assert meta["type"] == "design" and meta["lang"] == "en-GB"
    assert meta["abstract"] == "How the arbiter serves the read and write queues."
    assert meta["relations"][0]["p"] == "requires"
    assert meta["risks"][0]["impact"] == "high"

    prov = meta[PROVENANCE_KEY]["type"]
    assert prov["source"] == "generated" and "tier" not in prov
    assert prov["model"] == "stub-lm"
    assert len(prov["prompt_sha"]) == 12                          # the request's identity
    # one request, so every generated field cites the same prompt; the report agrees
    shas = set(prov["prompt_sha"] for name, prov in meta[PROVENANCE_KEY].items()
               if prov["source"] == "generated")
    assert shas == set([_report(d)["doc_meta"]["prompt_sha"]])
    assert stub.calls == 1


def test_a_value_outside_a_closed_vocabulary_is_rejected_and_never_stored(tmp_path):
    # A wrong label is worse than a gap: the gap stays visibly pending, while the
    # label silently becomes a new term for everyone who groups by that field.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    stub = _StubClient([("type", "blueprint"), ("lang", "klingon"),
                        ("abstract", "A real abstract.")])

    em.main(["--bundles", root, "--run-id", "R1"], client=stub)

    _fm, meta, _body = _meta(d)
    assert "type" not in meta and "lang" not in meta
    assert meta["abstract"] == "A real abstract."                 # the good value still lands
    assert _reasons(root) == {"type": "not-in-vocabulary",
                              "lang": "not-in-vocabulary"}
    # rejected, not counted as filled — so the gate cannot read complete
    assert _report(d)["doc_meta"]["gate"] != "complete"


def test_an_authored_only_field_offered_by_the_model_is_refused_and_never_requested(tmp_path):
    # owner/confidentiality/status exist so that a PERSON stood behind them. They are
    # not "hard tier-2 fields", they are not tier-2 fields — so the model is neither
    # asked for them nor believed when it volunteers one.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    stub = _StubClient([("confidentiality", "public"), ("owner", "Alice Ng"),
                        ("status", "approved"), ("type", "design")])

    em.main(["--bundles", root, "--run-id", "R1"], client=stub)

    _fm, meta, _body = _meta(d)
    assert "confidentiality" not in meta and "owner" not in meta
    assert "status" not in meta
    assert meta["type"] == "design"                               # the writable one is kept
    assert _reasons(root) == {"confidentiality": "authored-only",
                              "owner": "authored-only",
                              "status": "authored-only"}
    # and the request itself never put the boundary fields in front of the model
    prompt = stub.prompts[0]
    assert "confidentiality" not in prompt and "owner" not in prompt


def test_an_authored_value_is_never_overwritten_by_a_generated_one(tmp_path):
    # The cost of keeping a human's value is nothing; the cost of overwriting one is
    # trust. Provenance is what makes the rule checkable instead of a convention.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    fm, body = split_front_matter(_read(d))
    fm[META_KEY] = OrderedDict([
        ("type", "policy"),
        (PROVENANCE_KEY, OrderedDict([("type", OrderedDict([("tier", 2),
                                                            ("source", "authored")]))])),
    ])
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)

    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(FULL_REPLY))

    _fm, meta, _body = _meta(d)
    assert meta["type"] == "policy"                               # the model said "design"
    assert meta[PROVENANCE_KEY]["type"]["source"] == "authored"
    assert _reasons(root)["type"] == "kept-authored"
    assert _report(d)["doc_meta"]["authored"] == 1                # counted apart from model work


def test_a_key_that_is_not_in_the_schema_cannot_be_injected_into_the_block(tmp_path):
    # The reply is untrusted input. A model that answers `doc_id` or `markdown_sha256`
    # must not be able to reach the pipeline keys the converter owns.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    original, _ = split_front_matter(_read(d))
    stub = _StubClient([("doc_id", "hijacked"), ("markdown_sha256", "0" * 64),
                        ("nonesuch", 1), ("type", "design")])

    em.main(["--bundles", root, "--run-id", "R1"], client=stub)

    fm, meta, _body = _meta(d)
    assert "doc_id" not in meta and "markdown_sha256" not in meta
    assert "nonesuch" not in meta
    assert fm["doc_id"] == original["doc_id"] == "aaa"
    assert fm["markdown_sha256"] == original["markdown_sha256"]
    assert set(_reasons(root)) == set(["doc_id", "markdown_sha256", "nonesuch"])
    assert set(_reasons(root).values()) == set(["not-in-schema"])


def test_a_model_outage_leaves_the_fields_pending_and_the_run_re_runnable(tmp_path):
    # A transport failure must produce a gap, never an empty value: an empty value
    # reads as done and is never backfilled.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    down = _StubClient(FULL_REPLY, ok=False)

    rc = em.main(["--bundles", root, "--run-id", "R1"], client=down)

    # The ARTIFACT of an outage is a gap, never a guess and never an empty value —
    # everything below this line pins that, and none of it changed. The VERDICT is
    # the part that was wrong: exiting 0 said "the tiers you asked for ran", which
    # is the one thing that did not happen, and a nightly backfill against a model
    # that had been down for a week reported success every night. 4 = asked for,
    # never answered; re-run when it is up.
    assert rc == 4 and down.calls == 1
    _fm, meta, body = _meta(d)
    # Nothing INVENTED — but the deterministic floor is not the model's work and does
    # not go missing when the model does. What is absent is every field that needs
    # judgement, and it is absent rather than empty.
    assert sorted(n for n in WRITABLE if n in meta) == ["abstract", "title"]
    assert meta["uid"] == "specs/aaa.docx"                             # tiers 0/1 still written
    assert body == BODY
    gate = _report(d)["doc_meta"]
    # `incomplete`, not `pending`: the gate reads `pending` only while NOTHING has
    # been filled, and the floor has filled two fields without a model. That is the
    # same way an authored title has always read, and the number that decides whether
    # to re-run is `pending`, which is still the whole model tier.
    assert gate["enabled"] is True and gate["gate"] == "incomplete"
    assert gate["filled"] == 2 and gate["pending"] == len(WRITABLE) - 2
    assert _coverage(root)[-1]["status"] == "model-unavailable"
    # a failed answer is never cached, so the re-run really re-asks
    assert not os.path.exists(os.path.join(root, "_kb_meta.jsonl"))

    up = _StubClient(FULL_REPLY)
    em.main(["--bundles", root, "--run-id", "R2"], client=up)
    _fm2, meta2, _b2 = _meta(d)
    assert up.calls == 1 and meta2["type"] == "design"


def test_a_second_run_is_byte_identical_and_asks_the_model_nothing(tmp_path):
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    vocab = _seeded_vocab(tmp_path)
    argv = ["--bundles", root, "--vocab", vocab, "--run-id", "R1"]

    first = _StubClient(FULL_REPLY)
    assert em.main(list(argv), client=first) == 0                 # nothing outstanding
    text = _read(d)
    assert first.calls == 1
    cache = _jsonl(os.path.join(root, "_kb_meta.jsonl"))
    assert len(cache) == 1 and cache[0]["reply"]["type"] == "design"

    second = _StubClient(FULL_REPLY)
    assert em.main(list(argv), client=second) == 0
    assert second.calls == 0                                      # nothing left to ask
    assert _read(d) == text                                       # byte-identical

    # --force re-asks for EVERY field, so this proves the answer is cached on
    # (body, model, prompt, vocabulary version) rather than merely skipped.
    forced = _StubClient(FULL_REPLY)
    assert em.main(list(argv) + ["--force"], client=forced) == 0
    assert forced.calls == 0
    assert _read(d) == text

    # ...and with the cache ignored the same run does reach the model again, which is
    # what makes the zero above evidence of a cache hit and not of a no-op --force.
    uncached = _StubClient(FULL_REPLY)
    assert em.main(list(argv) + ["--force", "--no-cache"], client=uncached) == 0
    assert uncached.calls == 1
    assert _read(d) == text


@pytest.mark.parametrize("reply,expected", [
    ("```json\n{\"type\": \"runbook\", \"lang\": \"en-US\"}\n```", "runbook"),
    ("Sure! Here is the catalogue entry:\n\n"
     "{\"type\": \"policy\", \"lang\": \"en-US\"}\n\nHope that helps.", "policy"),
])
def test_a_reply_wrapped_in_a_fence_or_in_prose_is_still_parsed(tmp_path, reply, expected):
    # A server without constrained decoding wraps JSON in a fence or in chat often
    # enough that refusing those replies would throw away good answers.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)

    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(reply))

    _fm, meta, _body = _meta(d)
    assert meta["type"] == expected and meta["lang"] == "en-US"
    assert _coverage(root)[-1]["rejected"] == []


def test_the_exit_code_says_whether_the_corpus_needs_another_run(tmp_path):
    em = _mod("enrich_metadata")

    # 0 — every model-writable field carries a value the vocabulary accepts
    done = os.path.join(str(tmp_path), "done")
    os.makedirs(done)
    d_done = _bundle(done, doc_id="complete")
    vocab = _seeded_vocab(tmp_path)
    assert em.main(["--bundles", done, "--vocab", vocab, "--run-id", "R1"],
                   client=_StubClient(FULL_REPLY)) == 0
    assert _report(d_done)["doc_meta"]["gate"] == "complete"

    # 0 — something is outstanding, but pending is not a FAILURE. The exit code says
    # what happened, not what is left to do; report.json already carries the gate, and
    # returning non-zero here broke every `set -e` chain on a healthy run.
    open_ = os.path.join(str(tmp_path), "open")
    os.makedirs(open_)
    d_open = _bundle(open_, doc_id="partial")
    assert em.main(["--bundles", open_, "--run-id", "R1"],
                   client=_StubClient([("type", "design")])) == 0
    assert _report(d_open)["doc_meta"]["gate"] != "complete"
    assert _report(d_open)["doc_meta"]["pending"] > 0

    # 3 — the same run, when the caller OPTS IN to failing on outstanding work
    assert em.main(["--bundles", open_, "--run-id", "R1", "--fail-on-pending"],
                   client=_StubClient([("type", "design")])) == 3

    # 1 — a document could not be read at all. A failure outranks a pending field,
    # because "re-run later" is the wrong instruction for a file that will not parse.
    broken = os.path.join(str(tmp_path), "broken")
    os.makedirs(os.path.join(broken, "bad"))
    with open(os.path.join(broken, "bad", "document.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ndoc_id: \"bad\"\nweird: {a: 1}\n---\n\nbody\n")
    assert em.main(["--bundles", broken, "--run-id", "R1"],
                   client=_StubClient(FULL_REPLY)) == 1
    assert _coverage(broken)[-1]["status"] == "unparseable"

    # 4 — a model was ASKED FOR and answered for nothing. Deliberately NOT 0: the
    # artifacts are indistinguishable from a deterministic run's, and a deterministic
    # run is a choice while this is an outage. Ahead of --fail-on-pending's 3,
    # because the outage is the CAUSE of the pending fields.
    down = os.path.join(str(tmp_path), "outage")
    os.makedirs(down)
    _bundle(down, doc_id="unanswered")
    assert em.main(["--bundles", down, "--run-id", "R1"],
                   client=_StubClient(FULL_REPLY, ok=False)) == 4
    assert em.main(["--bundles", down, "--run-id", "R1", "--fail-on-pending"],
                   client=_StubClient(FULL_REPLY, ok=False)) == 4

    # 0 — the SAME artifacts when nobody asked for a model. This is the line that
    # keeps the code above from being "pending is a failure" in disguise.
    quiet = os.path.join(str(tmp_path), "quiet")
    os.makedirs(quiet)
    d_quiet = _bundle(quiet, doc_id="offline")
    assert em.main(["--bundles", quiet, "--run-id", "R1"]) == 0
    assert _report(d_quiet)["doc_meta"]["pending"] > 0


def test_an_endpoint_that_was_named_and_never_answered_is_not_a_deterministic_run(
        tmp_path, monkeypatch, capsys):
    """`--vlm-url` at an address nothing is listening on.

    The health check fails, the client is dropped and the run writes exactly the
    tiers a no-model run writes — which is right. What was wrong is that it then
    exited 0, so the two runs a person has to be able to tell apart ("I asked for
    deterministic" and "I asked for a model and the box was down") were the same
    number, and no `set -e` chain could see the difference.
    """
    import types

    stub = types.ModuleType("vlm_client")

    class _Unreachable(object):
        model = "stub-lm"

        def __init__(self, *args, **kwargs):
            pass

        def healthy(self, timeout=5):
            return False

    stub.VlmClient = _Unreachable
    monkeypatch.setitem(sys.modules, "vlm_client", stub)

    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    rc = em.main(["--bundles", root, "--run-id", "R1",
                  "--vlm-url", "http://127.0.0.1:1/v1/chat/completions"])
    assert rc == 4
    assert "not reachable" in capsys.readouterr().err
    # The artifacts are the deterministic ones, unharmed and re-runnable.
    _fm, meta, body = _meta(d)
    assert body == BODY and meta["uid"] == "specs/aaa.docx"
    assert _report(d)["doc_meta"]["pending"] > 0

    # ...and the other edge of the same rule: a corpus that needed NOTHING from the
    # model loses nothing when the model is down. 4 reports a gap, not an opinion
    # about the endpoint, so with no gap it must not fire.
    full = os.path.join(str(tmp_path), "full")
    os.makedirs(full)
    vocab = _seeded_vocab(tmp_path)
    d_full = _bundle(full, doc_id="complete")
    assert em.main(["--bundles", full, "--vocab", vocab, "--run-id", "R1"],
                   client=_StubClient(FULL_REPLY)) == 0
    assert _report(d_full)["doc_meta"]["gate"] == "complete"
    assert em.main(["--bundles", full, "--vocab", vocab, "--run-id", "R2",
                    "--vlm-url", "http://127.0.0.1:1/v1/chat/completions"]) == 0


def test_a_hand_edited_provenance_block_is_enriched_rather_than_crashed_on(tmp_path):
    """`_provenance: generated` — a block a person flattened by hand.

    `backend.kb` carries a non-mapping `_provenance` through verbatim on purpose and
    `kb_lint` reports it as `provenance-malformed`; enrichment used to die on it with
    `AttributeError: 'str' object has no attribute 'get'` at five separate reads, so
    the one document the linter merely described was the one document nothing could
    enrich — and the failure was recorded against the DOCUMENT, as if its content
    were at fault.

    Fail-safe, not repaired-in-place: a block that is not a mapping records no
    field's origin, so every value already there is treated as a person's work and
    kept, and what this run writes is stamped properly so the NEXT run reads real
    records instead of inheriting an unreadable one forever.
    """
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    fm, body = split_front_matter(_read(d))
    fm[META_KEY] = OrderedDict([("id", "aaa"), ("title", "Hand Written Title"),
                                (PROVENANCE_KEY, "generated")])
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)

    assert em.main(["--bundles", root, "--run-id", "R1"]) == 0
    assert _coverage(root)[-1]["status"] != "error"

    _fm2, meta, body2 = _meta(d)
    assert body2 == BODY                                   # the body is never touched
    assert meta["title"] == "Hand Written Title"           # authored value protected
    block = meta[PROVENANCE_KEY]
    assert isinstance(block, dict) and block.get("slug", {}).get("source") == "derived"


def test_a_provenance_record_that_is_not_a_mapping_is_survived_too(tmp_path):
    """One level down: `_provenance: {title: generated}`, a record written as a bare
    source string. `kb_lint` calls this "this field's origin is unverifiable"; here
    it crashed on the same read."""
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    fm, body = split_front_matter(_read(d))
    fm[META_KEY] = OrderedDict([
        ("id", "aaa"), ("title", "Hand Written Title"),
        (PROVENANCE_KEY, OrderedDict([("title", "generated")]))])
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)

    assert em.main(["--bundles", root, "--run-id", "R1"]) == 0
    assert _coverage(root)[-1]["status"] != "error"
    _fm2, meta, _b = _meta(d)
    # An unreadable record cannot say "machine wrote this", so the value is kept.
    assert meta["title"] == "Hand Written Title"


def test_a_source_with_no_title_stays_idempotent_once_the_model_supplies_one(tmp_path):
    # REGRESSION, three bugs at once, all of which only appear when the source has
    # NO title — the junk-title case a model is meant to rescue:
    #   * the prompt was built from the model's OWN previous title, so the request
    #     changed after run 1, its sha changed, and the cache never hit again;
    #   * the request was narrowed to the still-missing fields, so it changed every
    #     run for the same reason;
    #   * report.json's prompt_sha was blanked by a run that asked nothing.
    # Together they made a re-run re-ask the model forever and rewrite both files.
    root, doc = _bundles(tmp_path, title="")
    em = _mod("enrich_metadata")
    reply = dict(FULL_REPLY)

    snaps = []
    calls = []
    for _ in range(3):
        stub = _StubClient(reply)
        em.main(["--bundles", root, "--run-id", "FIXED"], client=stub)
        with open(os.path.join(doc, "report.json"), encoding="utf-8") as fh:
            gate = json.load(fh)["doc_meta"]
        snaps.append((_read(doc), gate))
        calls.append(stub.calls)

    assert snaps[0][0] == snaps[1][0] == snaps[2][0], "document.md is not idempotent"
    assert snaps[0][1] == snaps[1][1] == snaps[2][1], "report.json doc_meta is not idempotent"
    assert calls == [1, 0, 0], "a re-run must hit the cache, not re-ask the model"
    assert snaps[0][1]["prompt_sha"], "the request stamp must survive a no-op re-run"

    # And the tier-1 derivation that depends on a tier-2 parent still lands: with no
    # source_title, `slug` comes from the title the floor or the model supplied. `id`
    # does not — an identity that moved when somebody retitled a page would orphan
    # every see_also pointing at it, so it comes from the path and stays put.
    _fm, meta, _body = _meta(doc)
    assert meta["slug"] == "memory-controller-design-spec"
    assert meta["id"] == meta["uid"] == "specs/aaa.docx"
    assert meta[PROVENANCE_KEY]["id"]["source"] == "derived"


def test_the_request_does_not_depend_on_what_a_previous_run_stored(tmp_path):
    # The prompt must be a function of tier-0/1 inputs only. Feeding generated
    # output back into the request is what made the cache key move.
    root, doc = _bundles(tmp_path, title="")
    em = _mod("enrich_metadata")
    first = _StubClient(dict(FULL_REPLY))
    em.main(["--bundles", root, "--run-id", "FIXED"], client=first)
    second = _StubClient(dict(FULL_REPLY))
    em.main(["--bundles", root, "--run-id", "FIXED", "--no-cache"], client=second)
    assert second.calls == 1
    assert first.prompts[0] == second.prompts[0]


# --------------------------------------------- the descriptor / knowledge split

def test_the_graph_payload_lands_in_the_sidecar_and_the_descriptors_stay_in_front_matter(
        tmp_path):
    # The whole point of the split: a retrieval consumer reading document.md gets the
    # descriptors it filters on and none of the relation table it would discard, and
    # a graph loader reads structured JSON without parsing markdown at all.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(FULL_REPLY))

    fm, _merged, _body = _meta(d)
    front = fm[META_KEY]
    kn = _knowledge(d)
    # The payload, with the self-containment header set aside — those keys are
    # deliberately duplicated and are not part of the split.
    payload = dict((k, v) for k, v in kn.items() if k not in KNOWLEDGE_HEADER)

    for name in ("entities", "relations", "decisions", "risks", "open_questions",
                 "links"):
        assert name in payload, name
        assert name not in front, "%s leaked into front matter" % name
    for name in ("title", "type", "abstract", "see_also"):
        assert name in front, name
        assert name not in payload, "%s leaked into the sidecar" % name
    # A proposals slot follows its field, so the promotion evidence never ends up in
    # a different file from the values it is evidence about. (`tags` itself is an
    # empty registry on the shipped vocabulary, so these land in `tags_proposed`.)
    assert "tags_proposed" in front and "tags_proposed" not in payload

    # The sidecar is self-contained: a graph loader never has to open document.md.
    # This is the property that makes the split worth doing rather than merely tidy.
    assert kn["doc_id"] == os.path.basename(d)
    assert kn["id"] == front["id"]
    assert kn["markdown_sha256"] == fm["markdown_sha256"]
    assert kn["schema_version"] == front["schema_version"]
    # ... and it carries the provenance of what IT holds, nothing else, so neither
    # file is a fragment that has to be joined before it can be read.
    assert set(kn[PROVENANCE_KEY]) <= set(payload)
    assert all(in_knowledge(n) for n in kn[PROVENANCE_KEY])
    assert not any(in_knowledge(n) for n in front[PROVENANCE_KEY])


def test_a_run_that_changes_only_the_sidecar_is_still_detected_as_a_change(tmp_path):
    # Idempotence now spans TWO files. Comparing document.md alone would call a run
    # unchanged while knowledge.json went stale beside a document claiming otherwise.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(FULL_REPLY))
    md_before = _read(d)
    kn_path = os.path.join(d, KNOWLEDGE_FILE)

    with open(kn_path, encoding="utf-8") as fh:
        kn = json.load(fh)
    del kn["relations"]                       # something else emptied the sidecar
    with open(kn_path, "w", encoding="utf-8") as fh:
        json.dump(kn, fh, indent=2)

    em.main(["--bundles", root, "--run-id", "R2"], client=_StubClient(FULL_REPLY))
    with open(kn_path, encoding="utf-8") as fh:
        assert "relations" in json.load(fh)    # refilled, not reported unchanged
    assert _read(d) != md_before               # ... and the run_at stamp moved


def test_an_unreadable_sidecar_refuses_rather_than_regenerating_over_it(tmp_path):
    # Treating a corrupt knowledge.json as "no knowledge yet" would let the next run
    # overwrite entities and relations a person corrected by hand — the exact thing
    # "authored always wins" exists to prevent. Refuse, and change nothing.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(FULL_REPLY))
    kn_path = os.path.join(d, KNOWLEDGE_FILE)
    with open(kn_path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    md_before = _read(d)

    assert em.main(["--bundles", root, "--run-id", "R2"],
                   client=_StubClient(FULL_REPLY)) == 1
    with open(kn_path, encoding="utf-8") as fh:
        assert fh.read() == "{ not json"       # untouched
    assert _read(d) == md_before


def test_a_hand_edited_sidecar_value_survives_a_forced_re_ask(tmp_path):
    # "Authored always wins" has to hold ACROSS the seam, or the split quietly
    # becomes the place hand corrections go to die.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    em.main(["--bundles", root, "--run-id", "R1"], client=_StubClient(FULL_REPLY))
    kn_path = os.path.join(d, KNOWLEDGE_FILE)

    with open(kn_path, encoding="utf-8") as fh:
        kn = json.load(fh, object_pairs_hook=OrderedDict)
    kn["relations"][0]["o"] = "ddr-phy-CORRECTED"
    kn[PROVENANCE_KEY]["relations"] = {"tier": 2, "source": "authored"}
    with open(kn_path, "w", encoding="utf-8") as fh:
        json.dump(kn, fh, indent=2)

    em.main(["--bundles", root, "--run-id", "R2", "--force"],
            client=_StubClient(FULL_REPLY))
    with open(kn_path, encoding="utf-8") as fh:
        assert json.load(fh)["relations"][0]["o"] == "ddr-phy-CORRECTED"


def test_a_v1_bundle_migrates_to_the_two_file_layout_with_no_model_and_no_loss(
        tmp_path):
    # v1 kept everything in one front-matter block. Migration has to be a purely
    # DETERMINISTIC operation — no model, nothing re-asked — or bringing a corpus to
    # a new layout costs a full re-extraction, which is the thing the tier model
    # exists to avoid.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    v1 = OrderedDict([
        ("schema_version", 1), ("vocab_version", 1),
        ("id", "old-doc"), ("title", "Old Doc"), ("type", "runbook"),
        ("entities", {"software": [{"name": "legacy-agent", "type": "Software"}]}),
        ("relations", [{"s": "legacy-agent", "p": "runs_on", "o": "host-1"}]),
        ("decisions", [{"id": "d1", "status": "accepted", "text": "Keep it."}]),
        ("extraction", OrderedDict([("run_at", "OLD"),
                                    ("schema", "doc2md.kb.document/v1"),
                                    ("extractor", "doc2md.kb/1")])),
    ])
    fm, body = split_front_matter(_read(d))
    fm[META_KEY] = v1
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)

    assert em.main(["--bundles", root, "--run-id", "M1"]) == 0   # no model: pending

    front = split_front_matter(_read(d))[0][META_KEY]
    kn = _knowledge(d)
    # Moved, not copied — a duplicated payload is two sources of truth that drift.
    for name in ("entities", "relations", "decisions"):
        assert name in kn and name not in front, name
    assert kn["relations"] == v1["relations"]
    assert kn["entities"] == v1["entities"]

    # The stamps say which layout this now IS. A migrated block keeping
    # `schema_version: 1` makes the migration undetectable and the stamp a lie —
    # and a block with no provenance is otherwise treated as authored, so the prior
    # value would win.
    assert front["schema_version"] == 3
    assert front["extraction"]["schema"].endswith("/v3")
    assert front["extraction"]["extractor"].endswith("/3")

    # ... and migrating an already-migrated bundle changes nothing.
    before = (_read(d), json.dumps(_knowledge(d), sort_keys=True))
    em.main(["--bundles", root, "--run-id", "M2"])
    assert (_read(d), json.dumps(_knowledge(d), sort_keys=True)) == before


def test_an_authored_descriptor_with_no_provenance_still_wins(tmp_path):
    # The run-stamp carve-out must not become a general licence to overwrite. A block
    # with no `_provenance` is treated as hand-written, and that has to keep holding
    # for everything that is not a stamp about the writer.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    fm, body = split_front_matter(_read(d))
    fm[META_KEY] = OrderedDict([("id", "hand-picked-id"), ("title", "Hand Title")])
    with open(os.path.join(d, "document.md"), "w", encoding="utf-8") as fh:
        fh.write(render_front_matter(fm) + "\n" + body)

    em.main(["--bundles", root, "--run-id", "R1"])
    front = split_front_matter(_read(d))[0][META_KEY]
    assert front["id"] == "hand-picked-id"      # not re-slugged from source_title
    assert front["title"] == "Hand Title"
    # ... and it must be RECORDED as authored, or the next run reads `derived`,
    # decides the derived value wins, and destroys it. "Authored always wins" that
    # only survives one run is not the rule, and it fails silently: a hand-picked
    # `id` reverting to a slug orphans every see_also that pointed at it.
    assert front[PROVENANCE_KEY]["id"]["source"] == "authored"

    em.main(["--bundles", root, "--run-id", "R2"])
    front = split_front_matter(_read(d))[0][META_KEY]
    assert front["id"] == "hand-picked-id"
    assert front["title"] == "Hand Title"


# --------------------------------------------------- run provenance (blocker 6)

def _manifest(root):
    return _jsonl(os.path.join(root, "manifest.jsonl"))


def _runs(root):
    return _jsonl(os.path.join(root, "runs.jsonl"))


def test_the_switches_that_decided_the_identity_and_the_permalink_are_recorded(
        tmp_path):
    """Enrichment used to record NOTHING, and it is not a read-only stage.

    Running it with `--namespace` and `--source-base-url` changes `meta.id`,
    `meta.uid` and `meta.source.url` — rubric rows D2/D3/D4 — and `report.json`,
    `runs.jsonl` and `manifest.jsonl` came out byte-identical. The switches that
    produced the corpus you are holding were recoverable from no artifact at all.
    """
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    argv = ["--bundles", root, "--run-id", "E1", "--namespace", "acme.internal",
            "--source-base-url", "https://wiki.example.com/docs/"]
    assert em.main(list(argv)) == 0

    # the output really did move
    _fm, meta, _body = _meta(d)
    assert meta["id"] == meta["uid"] == "acme-internal/specs/aaa.docx"
    assert meta["source"]["url"] == "https://wiki.example.com/docs/specs/aaa.docx"

    # ...and every one of the four artifacts now says why
    row = [r for r in _runs(root) if r["entrypoint"] == "enrich_metadata"][-1]
    assert row["run_id"] == "E1"
    assert row["argv"] == ["--bundles", "<src>", "--run-id", "E1",
                           "--namespace", "acme.internal",
                           "--source-base-url", "https://wiki.example.com/docs/"]
    assert row["config"]["cli.namespace"] == {"value": "acme.internal",
                                              "from": "flag"}
    assert row["config"]["cli.source_base_url"]["from"] == "flag"
    assert row["counts"]["deferred"] == 0 and row["documents"] == 1
    assert row["code"]["name"] == "doc2md" and row["host"]["python"]
    assert row["started_at"] and row["finished_at"]

    rows = [m for m in _manifest(root) if m["stage"] == "enrich_metadata"]
    assert len(rows) == 1
    assert rows[0]["run_id"] == "E1" and rows[0]["action"] == "enriched"
    assert rows[0]["doc_id"] == "aaa" and rows[0]["source_relpath"] == "specs/aaa.docx"

    rep = _report(d)
    mine = [x for x in rep["decisions"] if x["stage"] == "enrich_metadata"]
    chose = dict((x["code"], x["chose"]) for x in mine)
    assert chose["identity_namespace"] == "acme.internal"
    assert chose["permalink_base"] == "absolute"
    assert chose["metadata_tier"] == "deterministic"
    assert chose["vocabulary_selected"].startswith("v")
    assert all(x["reason"] for x in mine)

    staged = [r for r in rep["runs"] if r["entrypoint"] == "enrich_metadata"]
    assert len(staged) == 1
    assert staged[0]["config_ref"] == "runs.jsonl#E1"


def test_a_run_with_no_switches_is_distinguishable_from_one_with_them(tmp_path):
    # The point of recording is that two runs read differently. Same corpus, one
    # switch apart, and the artifacts have to say so.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0
    plain = json.dumps(_report(d), sort_keys=True)
    assert em.main(["--bundles", root, "--run-id", "E2",
                    "--namespace", "acme.internal", "--force"]) == 0
    assert json.dumps(_report(d), sort_keys=True) != plain

    rows = [r for r in _runs(root) if r["entrypoint"] == "enrich_metadata"]
    assert [r["config"]["cli.namespace"]["value"] for r in rows] == \
        ["", "acme.internal"]


def test_a_permalink_base_taken_from_the_environment_is_still_recorded(tmp_path,
                                                                      monkeypatch):
    # The case `argv` structurally cannot answer: the base never appears on the
    # command line, and a run re-established without it publishes a corpus of
    # different links with nothing saying why.
    monkeypatch.setenv("DOC2MD_SOURCE_BASE_URL", "https://intranet/docs")
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0

    _fm, meta, _body = _meta(d)
    assert meta["source"]["url"] == "https://intranet/docs/specs/aaa.docx"
    row = [r for r in _runs(root) if r["entrypoint"] == "enrich_metadata"][-1]
    rec = row["config"]["cli.source_base_url"]
    assert rec == {"value": "https://intranet/docs", "from": "env",
                   "env": "DOC2MD_SOURCE_BASE_URL"}


def test_the_writers_provenance_is_never_overwritten_by_the_enrichers(tmp_path):
    # `run{}` is the run that produced the MARKDOWN. A later stage recording its own
    # there would trade the conversion's provenance for its own, and the report is a
    # failed document's only artifact.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    rp = os.path.join(d, "report.json")
    report = json.load(open(rp, encoding="utf-8"))
    report["run"] = {"entrypoint": "build_bundle", "run_id": "B1",
                     "config_ref": "runs.jsonl#B1"}
    report["runs"] = [dict(report["run"])]
    report["decisions"] = [{"code": "lane_selected", "stage": "build_bundle",
                            "chose": "ooxml", "reason": "by extension"}]
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(report, fh)

    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0
    after = _report(d)
    assert after["run"] == {"entrypoint": "build_bundle", "run_id": "B1",
                            "config_ref": "runs.jsonl#B1"}
    assert [r["entrypoint"] for r in after["runs"]] == ["build_bundle",
                                                        "enrich_metadata"]
    assert [x["code"] for x in after["decisions"]][0] == "lane_selected"


def test_a_re_run_replaces_its_own_records_instead_of_piling_them_up(tmp_path):
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    for run_id in ("E1", "E2", "E3"):
        assert em.main(["--bundles", root, "--run-id", run_id]) == 0
    rep = _report(d)
    mine = [x for x in rep["decisions"] if x["stage"] == "enrich_metadata"]
    assert len(mine) == len(set(x["code"] for x in mine))     # no accumulation
    assert [r["entrypoint"] for r in rep["runs"]] == ["enrich_metadata"]
    assert rep["runs"][0]["run_id"] == "E3"                   # the latest, once

    # the run LOG, however, keeps every run: that is what makes it a log
    rows = [m for m in _manifest(root) if m["stage"] == "enrich_metadata"]
    assert [m["run_id"] for m in rows] == ["E1", "E2", "E3"]
    # ...and a converged re-run says it changed nothing rather than claiming a write
    assert [m["action"] for m in rows] == ["enriched", "unchanged", "unchanged"]


def test_a_deferred_document_still_gets_a_row_so_the_log_has_no_holes(tmp_path):
    em = _mod("enrich_metadata")
    root, _d = _bundles(tmp_path)
    _bundle(root, doc_id="bbb")
    assert em.main(["--bundles", root, "--run-id", "E1", "--limit", "1"]) == 0
    rows = dict((m["doc_id"], m["action"])
                for m in _manifest(root) if m["stage"] == "enrich_metadata")
    assert rows == {"aaa": "enriched", "bbb": "deferred"}
    row = [r for r in _runs(root) if r["entrypoint"] == "enrich_metadata"][-1]
    assert row["counts"]["deferred"] == 1


def test_an_unreadable_bundle_is_logged_as_failed_rather_than_omitted(tmp_path):
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    with open(os.path.join(d, KNOWLEDGE_FILE), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert em.main(["--bundles", root, "--run-id", "E1"]) == 1
    rows = [m for m in _manifest(root) if m["stage"] == "enrich_metadata"]
    assert len(rows) == 1 and rows[0]["action"] == "failed" and rows[0]["error"]


def test_no_enrichment_artifact_carries_an_absolute_host_path(tmp_path):
    # Same rule as the writers, and this stage's `--bundles` default IS an absolute
    # path under the repo root, so its config table is a live leak vector.
    em = _mod("enrich_metadata")
    root, _d = _bundles(tmp_path)
    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0
    leaked = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or not name.endswith((".json", ".jsonl")):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for needle in (str(tmp_path), REPO):
            if needle in text:
                leaked.append("%s -> %s" % (name, needle))
    assert not leaked, "absolute host paths leaked: %s" % leaked


def test_a_hand_edited_value_survives_even_under_a_machine_stamp(tmp_path):
    """`unique_id`'s docstring promises a hand-written `id` "outranks this forever".

    It did not. The deterministic merge decided inheritance on the provenance
    LABEL alone, so a correction made to a field the pipeline had already written —
    which is every field, in every bundle it has ever produced — was reverted on
    the next run, with exit 0 and no warning. That silently orphaned every
    `see_also` pointing at the corrected id, which is the one thing the documented
    remedy for an id collision was supposed to prevent.
    """
    import re

    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    assert em.main(["--bundles", root, "--run-id", "R1"]) == 0

    md_path = os.path.join(d, "document.md")
    with open(md_path, encoding="utf-8") as fh:
        before = fh.read()
    assert '\n  id: "' in before
    picked = "kestrel-hand-picked-identity"
    edited = re.sub(r'\n  id: "[^"]*"', '\n  id: "%s"' % picked, before, count=1)
    assert edited != before
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(edited)

    assert em.main(["--bundles", root, "--run-id", "R2"]) == 0
    with open(md_path, encoding="utf-8") as fh:
        after = fh.read()
    assert '\n  id: "%s"' % picked in after, (
        "the hand-picked id was reverted; the documented remedy for a collision "
        "is only a remedy if it survives the next run")


def test_the_second_change_to_a_derived_field_is_not_silently_discarded(tmp_path):
    """Adopt a namespace, then correct it — and the correction has to land.

    A deterministic field used to be provenance-stamped only when it had NO prior
    entry, so the first legitimate change left `value_sha` describing the OLD
    value. From the next run on, `is_authored` read that mismatch as a human edit
    and inherited the machine's own stale value: the field froze forever while
    still claiming `source: derived`, `report.json` recorded the NEW namespace in
    its decisions, and the manifest row said `unchanged`. Exit 0 throughout.
    """
    from backend.kb import value_sha

    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)

    def enrich(run_id, ns, base):
        argv = ["--bundles", root, "--run-id", run_id]
        if ns:
            argv += ["--namespace", ns]
        if base:
            argv += ["--source-base-url", base]
        assert em.main(argv) == 0
        front = split_front_matter(_read(d))[0][META_KEY]
        # the fingerprint must describe what is STORED, on every run — that is the
        # whole point of recording one
        assert front[PROVENANCE_KEY]["id"]["value_sha"] == value_sha(front["id"])
        assert front[PROVENANCE_KEY]["id"]["source"] == "derived"
        return front

    enrich("R1", "", "")
    front = enrich("R2", "acme", "https://wiki.example.com/docs")
    assert front["id"] == "acme/specs/aaa.docx"

    front = enrich("R3", "beta", "https://intranet.example.com/d")
    assert front["id"] == front["uid"] == "beta/specs/aaa.docx", (
        "the corrected namespace was discarded and the machine's own earlier value "
        "was inherited as if a person had written it")
    assert front["source"]["url"] == \
        "https://intranet.example.com/d/specs/aaa.docx"

    # ...and a run that asks for the same thing again still converges: the
    # fingerprint is a function of the value, so re-stamping costs nothing.
    before = _read(d)
    front = enrich("R4", "beta", "https://intranet.example.com/d")
    assert front["id"] == "beta/specs/aaa.docx"
    assert _read(d) == before
    assert [r["action"] for r in _manifest(root) if r["stage"] == "enrich_metadata"] \
        == ["enriched", "enriched", "enriched", "unchanged"]


def test_a_hand_corrected_derived_field_survives_every_later_run(tmp_path):
    """The opposite pull, and both have to hold at once.

    The re-fingerprinting above must not become a licence to overwrite: a value a
    person corrected under a machine stamp is still a person's value, and the run
    after it must not read its own fresh fingerprint as permission to revert.
    """
    import re

    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    assert em.main(["--bundles", root, "--run-id", "R1"]) == 0

    md_path = os.path.join(d, "document.md")
    with open(md_path, encoding="utf-8") as fh:
        before = fh.read()
    picked = "kestrel-hand-picked-identity"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(re.sub(r'\n  id: "[^"]*"', '\n  id: "%s"' % picked, before, count=1))

    # three more runs, one of them changing the very switch that derives `id`
    for run_id, extra in (("R2", []), ("R3", ["--namespace", "acme"]),
                          ("R4", ["--namespace", "acme"])):
        assert em.main(["--bundles", root, "--run-id", run_id] + extra) == 0
        front = split_front_matter(_read(d))[0][META_KEY]
        assert front["id"] == picked, "run %s reverted a hand-corrected id" % run_id
        # and the block now says what it is, rather than crediting a person's value
        # to a rule that no longer produces it
        assert front[PROVENANCE_KEY]["id"]["source"] == "authored"


def test_a_bundle_whose_conversion_failed_is_refused_rather_than_published(tmp_path):
    """Enrichment selected bundles on `document.md` existing and nothing else.

    So a bundle a failed rebuild had left standing — stale body, `lossless: "true"`,
    a report reading `status: failed` — was enriched: a fresh `knowledge.json`
    minted for the stale body and a `doc_meta` gate stamped into the failed report,
    exit 0. It read that very `status` out of the same file to fill a manifest
    column, so the verdict was read, recorded, and not used as a gate.
    """
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    with open(os.path.join(d, "report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    rep["status"] = "failed"
    rep["losslessness"] = {"gate": "fail", "error": "unreadable-zip"}
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh)

    before = _read(d)
    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0

    assert _read(d) == before, "a failed bundle's document.md was rewritten"
    assert _knowledge(d) is None, (
        "a knowledge.json was minted for a body the pipeline could not convert")
    after = _report(d)
    assert "doc_meta" not in after, (
        "a metadata gate was stamped into a report that says the conversion failed")
    assert after["status"] == "failed"
    assert not [r for r in _manifest(root) if r["stage"] == "enrich_metadata"]


def test_a_degraded_or_unreported_bundle_is_still_enriched(tmp_path):
    # The refusal has to be exactly as wide as the verdict. `degraded` is a
    # published bundle, and a root with no report.json at all is not a failure —
    # "I don't know" must not read as "it failed", or every hand-made bundle root
    # stops being enrichable.
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path)
    with open(os.path.join(d, "report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    rep["status"] = "degraded"
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh)
    other = _bundle(root, doc_id="bbb")
    os.remove(os.path.join(other, "report.json"))

    assert em.main(["--bundles", root, "--run-id", "E1"]) == 0
    assert split_front_matter(_read(d))[0][META_KEY]["id"] == "specs/aaa.docx"
    assert split_front_matter(_read(other))[0][META_KEY]["id"] == "specs/bbb.docx"
    assert sorted(r["doc_id"] for r in _manifest(root)
                  if r["stage"] == "enrich_metadata") == ["aaa", "bbb"]


def test_a_harvested_edge_survives_the_sidecar_going_away(tmp_path):
    """The end-to-end shape of the revalidation asymmetry.

    A URL in the prose above the first heading is harvested with no `ref` — there
    is no fragment up there to point at — and once the model contributes one link
    the field's provenance reads `generated`, so `revalidate_generated` re-judged
    the WHOLE block by the model-answer rules and dropped the measured record every
    run, logging `missing-required-ref` against the schema's own intent.
    `apply_floor` re-harvested it from `structure.json`, so the corpus never
    converged — and the moment the sidecar was unreadable the edge was gone for
    good, with the run still exiting 0.
    """
    from backend.sections import document_outline

    body = ("The vendor [timing note](https://vendor.invalid/timing) applies.\n\n"
            "# Clock Spec\n\nThe clock tree is described here.\n\n"
            "## Rollback\n\nRun the restore playbook.\n")
    em = _mod("enrich_metadata")
    root, d = _bundles(tmp_path, body=body)
    with open(os.path.join(d, "structure.json"), "w", encoding="utf-8") as fh:
        json.dump({"outline": document_outline(body)["outline"]}, fh)
    client = _StubClient([("links", {"internal": [{"title": "Runbook",
                                                   "url": "runbook.md",
                                                   "ref": REF}]})])

    def urls():
        block = _knowledge(d)["links"]
        return sorted(r["url"] for recs in block.values() for r in recs)

    assert em.main(["--bundles", root, "--run-id", "E1"], client=client) == 0
    assert urls() == ["https://vendor.invalid/timing", "runbook.md"]
    assert "missing-required" not in json.dumps(_coverage(root)[-1]["revalidated"])

    os.remove(os.path.join(d, "structure.json"))       # a pruned or partial copy
    assert em.main(["--bundles", root, "--run-id", "E2"], client=client) == 0
    assert urls() == ["https://vendor.invalid/timing", "runbook.md"], (
        "a recall-1.0 edge was deleted by the rules written for a model's guess")
