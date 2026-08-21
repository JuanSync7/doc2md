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

# One valid answer per model-writable field, against the seeded vocabulary.
FULL_REPLY = [
    ("title", "Memory Controller Design Spec"),
    ("short_title", "Mem Ctrl Spec"),
    ("abstract", "How the arbiter serves the read and write queues."),
    ("type", "design"),
    ("subtype", ["reference_architecture"]),
    ("lang", "en-GB"),
    ("tags", ["ddr", "memory-controller"]),
    ("keywords", ["ddr"]),
    ("topics", ["platform_engineering"]),
    ("audience", ["platform_engineering"]),
    ("aliases", ["mem ctrl spec"]),
    ("entities", {"hosts": [{"name": "ddr-node-1"}]}),
    ("relations", [{"s": "arbiter", "p": "requires", "o": "ddr-phy",
                    "mode": "silent"}]),
    ("decisions", [{"id": "d1", "status": "accepted", "text": "Round robin."}]),
    ("risks", [{"id": "r1", "impact": "high", "mode": "delayed",
                "text": "Refresh starvation."}]),
    ("open_questions", [{"id": "q1", "text": "What is the refresh budget?"}]),
    ("links", {"internal": [{"title": "Runbook", "url": "runbook.md"}]}),
    ("see_also", ["ddr-phy-guide"]),
    ("prerequisites", ["ddr-phy-guide"]),
    ("out_of_scope", ["power sequencing"]),
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
    assert meta["uid"] == "specs/aaa"                            # namespaced from the path
    assert meta["id"] == meta["slug"] == "memory-controller-design-spec"
    assert meta["word_count"] > 0 and meta["reading_time_minutes"] >= 1
    assert meta["source"]["uri"] == "specs/aaa.docx"
    assert meta["source"]["is_derivative"] is True               # every bundle is converted
    assert meta["extraction"]["run_at"] == "R1"
    assert meta[PROVENANCE_KEY]["uid"]["source"] == "derived"

    # not one tier-2 field invented, and the gate says the model tier never ran
    assert [n for n in WRITABLE if n in meta] == []
    gate = _report(d)["doc_meta"]
    assert gate["gate"] == "disabled" and gate["enabled"] is False
    assert gate["expected"] == len(WRITABLE) == gate["pending"]
    assert gate["filled"] == 0 and gate["invalid"] == 0


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
    assert prov["source"] == "generated" and prov["tier"] == 2
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

    assert rc == 0 and down.calls == 1   # an outage leaves PENDING, not a failure
    _fm, meta, body = _meta(d)
    assert [n for n in WRITABLE if n in meta] == []               # nothing invented
    assert meta["uid"] == "specs/aaa"                             # tiers 0/1 still written
    assert body == BODY
    gate = _report(d)["doc_meta"]
    assert gate["enabled"] is True and gate["gate"] == "pending"
    assert gate["filled"] == 0 and gate["pending"] == len(WRITABLE)
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
    # source_title, id/slug come from the title the model supplied.
    _fm, meta, _body = _meta(doc)
    assert meta["id"] == meta["slug"] == "memory-controller-design-spec"
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
    assert front["schema_version"] == 2
    assert front["extraction"]["schema"].endswith("/v2")
    assert front["extraction"]["extractor"].endswith("/2")

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
