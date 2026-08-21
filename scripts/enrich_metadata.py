#!/usr/bin/env python3
"""Fill each bundle's document metadata — deterministic tiers always, model tier when one is reachable.

Walks ``<bundles>/<doc_id>/`` and writes the DESCRIPTORS into ``document.md``'s
``meta`` front-matter block, the KNOWLEDGE payload into ``knowledge.json``, and a
``doc_meta`` gate into ``report.json``:

    tier 0  deterministic  id, version, source{}, extraction{} — from the bundle
    tier 1  derived        slug, word_count, reading_time_minutes — by rule
    tier 2  model          type, tags, entities, relations, ... — proposed by a
                           model against the CLOSED vocabulary, then validated

A model is the CEILING, never the floor. ``title``, ``abstract`` and ``links`` are
tier-2 fields that a no-model run still fills, from evidence the pipeline already
has: the docx core properties, the lede paragraph, and the outbound URLs harvested
into ``structure.json`` at recall 1.0. Whether a model may then improve a floor value
depends on where the floor came from — a title the document DECLARES is evidence and
is protected, a title inferred from a filename is a guess and is not.

Everything about what is allowed lives in ``backend.kb``; this file is transport.

TWO FILES, ONE VIEW. Which field lands where is ``backend.kb.split_meta``'s decision,
not this script's: descriptors (id/title/type/tags/status/owner) stay in front matter
where retrieval and markdown tooling read them, and the graph-shaped payload
(entities/relations/decisions/risks/open_questions/links) goes to ``knowledge.json``
where a graph loader can ``json.load`` it without parsing markdown. Everything in
between — merging, validating, filling — happens on ONE merged mapping, so no rule in
``backend.kb`` has to know the storage split exists.

The model is asked to SELECT from the vocabulary rather than write free text, and
``unknown`` is always a legal answer — a forced guess is worse than a gap, because
a gap stays visibly pending and a wrong label silently becomes a new term. A value
the vocabulary rejects is never stored. A value a PERSON wrote is never overwritten.
Authored-only fields (owner, confidentiality, status, review dates) are refused
outright, whatever the model says.

Safe without a model: with no reachable client the deterministic tiers are still
written and every tier-2 field is recorded PENDING, so a later run backfills them
without re-converting anything. ``markdown_sha256`` covers the BODY only, so
rewriting front matter never invalidates a hash, a line span or an image index.

Idempotent: a re-run whose metadata block would be unchanged writes nothing at all
— ``extraction.run_at`` is re-stamped only when something actually moved, so
``document.md`` stays byte-identical. Model answers are cached on (body, model,
prompt, vocabulary version), so a second run makes no request; ``--force`` asks
again even when nothing is missing, and ``--no-cache`` ignores the stored answer.
The body is always written back byte-for-byte. Safe to run twice.

Usage:
  python3 scripts/enrich_metadata.py --bundles data/bundles              # no model: tiers 0+1
  python3 scripts/enrich_metadata.py --bundles data/bundles --vlm-url http://127.0.0.1:21717/v1/chat/completions
"""
import argparse
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _HERE)

import build_bundle as bb                        # noqa: E402  (shared writer helpers)
from backend.ingest import (cache_last_wins, load_ingest_config,   # noqa: E402
                            render_front_matter, split_front_matter, YamlSubsetError)
from backend.provenance import (compact_run, decision,   # noqa: E402
                                safe_value, stamp_stage)
from backend.kb import (abstract_floor, accept_model_meta,        # noqa: E402
                        body_anchors, check_schema_bindings,
                        harvested_links, is_authored, keyword_candidates,
                        knowledge_document, knowledge_payload,
                        load_vocab, merge_group_evidence, merge_meta,
                        meta_collisions, meta_coverage,
                        next_review_due, order_meta, reading_time_minutes,
                        request_spec, revalidate_generated, set_provenance,
                        slugify, source_url, split_meta, title_floor, unique_id,
                        value_source, word_count,
                        ALIAS_FIELDS, KNOWLEDGE_FILE, META_KEY, PROVENANCE_KEY,
                        SCHEMA_VERSION, SOURCE_AUTHORED, SOURCE_DERIVED,
                        SOURCE_EXTRACTED, SOURCE_GENERATED)
from backend.validate import doc_meta_report              # noqa: E402  (gate policy)

from collections import OrderedDict                        # noqa: E402

CACHE_NAME = "_kb_meta.jsonl"
COV_NAME = "_kb_meta_coverage.jsonl"
EXTRACTOR = "doc2md.kb/%d" % SCHEMA_VERSION

# This stage's name in `run.entrypoint`, in every `decisions[].stage` it writes and
# in every manifest row it appends. One string, so the three can never disagree.
ENTRYPOINT = "enrich_metadata"

# THIS STAGE IS A WRITER TOO, and until now it was the only one that recorded
# nothing. Running it with `--namespace acme.internal --source-base-url
# https://wiki/docs/` rewrites `meta.id`, `meta.uid` and `meta.source.url` — the
# three fields the rubric grades as D2/D3/D4 — while `report.json`, `runs.jsonl`
# and `manifest.jsonl` stayed byte-identical. The switches that decided the output
# were recorded nowhere, so the run was neither repeatable nor even detectable.
#
# HOW IT COEXISTS WITH THE WRITER'S ROWS, since enrichment rewrites bundles
# `build_bundle` wrote into the SAME root:
#   runs.jsonl      one more row, `entrypoint: enrich_metadata`. Rows were already
#                   keyed by run_id alone, and both stages accept `--run-id` — the
#                   grader passes the SAME id to both — so a lookup by run_id could
#                   silently return the writer's config for an enrichment report.
#                   `replay_run` now matches on (run_id, entrypoint).
#   manifest.jsonl  one row per document per run, in the existing shape. `status`
#                   keeps meaning the CONVERSION verdict (read from report.json, not
#                   invented here) so the column is not two vocabularies in a
#                   trenchcoat; what enrichment did is the `action`, which is what
#                   that column has always been for.
#   report.json     `run{}` is left alone — it is the run that produced the markdown
#                   and overwriting it would trade one stage's provenance for
#                   another's. The stage history is `runs[]`; see
#                   `build_bundle.record_stage_run`.

# What this stage can have DONE to a document, as a closed list for the same reason
# `DECISION_CODES` is closed: an unnamed action is one nobody can aggregate, and a
# typo would silently create a category of one.
_ACTIONS = (
    "enriched",       # metadata was written
    "unchanged",      # the run converged: nothing moved, nothing was written
    "failed",         # unreadable / unparseable / a raised error, that document only
    "deferred",       # --limit cut it before this run reached it
)

# Stamps about THIS run's writer, re-derived every time and never inherited — not
# even from a block with no provenance, which the merge below otherwise treats as
# authored. `schema_version` is the field a reader consults to know which LAYOUT it
# is looking at, so a v1 block migrating to the v2 two-file layout while keeping
# `schema_version: 1` makes the migration undetectable and the stamp a lie.
# `extraction` names the extractor that ran; inheriting it would credit this run's
# output to the previous version. (`extraction.run_at` is preserved separately, by
# the idempotence check below, which reads it from `existing`.)
_ALWAYS_DERIVED = ("schema_version", "extraction")

BASE_PROMPT = (
    "You are cataloguing a technical document for a knowledge base. Return ONE JSON "
    "object and nothing else — no prose, no code fence.\n\n"
    "RULES, in order of importance:\n"
    "1. For any field with a `values` list you MUST choose from that list verbatim. "
    "Never invent a value, never reword one, never return a synonym.\n"
    "2. If you cannot tell from the document, use \"unknown\" (or omit the field). "
    "An omitted field is recorded as outstanding and is harmless; a guess becomes a "
    "permanent wrong label.\n"
    "3. For a field marked `new_values_allowed`, prefer a listed term; propose a new "
    "one only when nothing listed fits. It is recorded as a proposal, not used.\n"
    "4. Describe only what the document actually says. Do not infer facts it does "
    "not state, and quote names, identifiers and numbers exactly as written.\n"
)


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


# Which of this script's own switches read an environment variable when the flag is
# absent. `config_provenance` derives the INGEST settings by difference against the
# real loader, which is the right trick when there is a loader; these resolve
# through argparse, and argparse is the only loader they have.
_ENV_BACKED = {"source_base_url": "DOC2MD_SOURCE_BASE_URL"}


def _cli_provenance(parser, args):
    # type: (object, object) -> dict
    """Where each of THIS script's own switches got its value, as ``{value, from}``.

    ``--namespace`` and ``--source-base-url`` decide ``meta.id``, ``meta.uid`` and
    ``meta.source.url``, and `argv` alone cannot answer for them: a value that came
    from ``$DOC2MD_SOURCE_BASE_URL`` never appears on the command line at all, so a
    reader re-establishing the run elsewhere would rebuild it with a different
    permalink and no artifact would say why.

    Three cases, written out rather than modelled, because three is all there is: a
    value argparse did not default to came from the FLAG; a value equal to an
    env-backed default with that variable set came from the ENV; anything else is
    the built-in DEFAULT."""
    out = OrderedDict()
    for action in parser._actions:                # the parser IS the spec here
        name = action.dest
        if name == "help" or not hasattr(args, name):
            continue
        value = getattr(args, name)
        env_name = _ENV_BACKED.get(name)
        if value != action.default:
            source = "flag"
        elif env_name and os.environ.get(env_name) not in (None, ""):
            source = "env"
        else:
            source = "default"
        # `--bundles` defaults to an absolute path under the repo root, so this
        # table is a leak vector unless every value goes through the same shape
        # test `argv` does. It does — one implementation, in backend.provenance.
        rec = OrderedDict([("value", safe_value(value)), ("from", source)])
        if source == "env":
            # Name the variable in the record rather than leaving a replay to
            # rebuild the mapping: it is the one fact that lets another machine
            # check "is this still set, and still to that?" with no coupling.
            rec["env"] = env_name
        out["cli.%s" % name] = rec
    return out


def _sha12(s):
    # type: (str) -> str
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def build_prompt(spec, title, body, excerpt_chars=12000, anchors=()):
    # type: (dict, str, str, int, object) -> str
    """The full request: the rules, the field spec with its enums, then the document.

    The spec is embedded as JSON so the enums are unambiguous, and the document is
    truncated rather than dropped — a long runbook still classifies correctly from
    its opening sections, and announcing the truncation is better than silently
    changing what the model saw.

    The SECTION ANCHORS block is what makes ``ref`` answerable. The spec says every
    knowledge record must cite the section that asserts it; without the list of legal
    anchors in front of the model that instruction can only be guessed at, and a
    guessed anchor is rejected on arrival — so the constraint would cost a whole
    answer instead of shaping one.
    """
    head = body[:excerpt_chars]
    truncated = len(body) > excerpt_chars
    parts = [BASE_PROMPT,
             "\nFIELDS TO FILL (JSON schema-ish; `values` is a closed list):\n",
             json.dumps(spec, indent=1, ensure_ascii=False),
             "\n\nDOCUMENT TITLE: %s\n" % (title or "(none)")]
    if anchors:
        parts.append("\nSECTION ANCHORS (the only legal `ref` values, verbatim):\n"
                     + ", ".join("#%s" % a for a in sorted(anchors)) + "\n")
    parts.append("\nDOCUMENT (markdown%s):\n" % (", truncated" if truncated else ""))
    parts.append(head)
    return "".join(parts)


def parse_json_reply(text):
    # type: (str) -> dict
    """The first JSON object in a model reply, code fences tolerated.

    A server without constrained decoding wraps JSON in prose or a fence often
    enough that refusing those replies would throw away good answers; anything that
    is not a JSON object still returns ``{}`` rather than raising.
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start = s.find("{")
    if start < 0:
        return {}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                except ValueError:
                    return {}
                return obj if isinstance(obj, dict) else {}
    return {}


def deterministic_meta(fm, body, run_id, namespace="", source_base_url=""):
    # type: (dict, str, str, str, str) -> tuple
    """The tier-0 and tier-1 block, computed from the bundle alone.

    Never needs a model, so a corpus can be brought up to a new schema revision
    entirely offline — which is the property that makes backfill cheap.
    """
    meta = OrderedDict()
    prov = OrderedDict()

    def put(name, value, source):
        if value is None or value == "":
            return
        meta[name] = value
        prov[name] = source

    relpath = fm.get("source_relpath") or ""
    words = word_count(body)
    title = fm.get("source_title") or ""

    put("schema_version", SCHEMA_VERSION, SOURCE_DERIVED)
    # ONE IDENTITY, derived from the path and unique by construction. `uid` is
    # written as the alias it now is — the same value, never a second namespace.
    ident = unique_id(relpath, namespace)
    put("id", ident, SOURCE_DERIVED)
    put("uid", ident, SOURCE_DERIVED)
    put("version", fm.get("source_version"), SOURCE_EXTRACTED)
    if title:
        put("slug", slugify(title), SOURCE_DERIVED)
    put("word_count", words, SOURCE_DERIVED)
    put("reading_time_minutes", reading_time_minutes(words), SOURCE_DERIVED)

    source = OrderedDict()
    for key, fmkey in (("uri", "source_relpath"), ("publisher", "source_company"),
                       ("authored_by", "source_author"),
                       ("created", "source_created"),
                       ("modified", "source_modified"),
                       ("last_modified_by", "source_last_modified_by")):
        if fm.get(fmkey):
            source[key] = fm[fmkey]
    if source.get("uri"):
        # The PERMALINK. `uri` is a filesystem path and cannot be clicked; this is a
        # URI reference that can — absolute when --source-base-url says where the
        # sources live, relative otherwise. Never invented, always resolvable.
        source["url"] = source_url(source_base_url, source["uri"])
    source["is_derivative"] = True            # every bundle is converted, never original
    put("source", source, SOURCE_EXTRACTED)

    extraction = OrderedDict()
    extraction["run_at"] = run_id
    extraction["schema"] = "doc2md.kb.document/v%d" % SCHEMA_VERSION
    extraction["extractor"] = EXTRACTOR
    if fm.get("converter"):
        extraction["converter"] = fm["converter"]
    put("extraction", extraction, SOURCE_DERIVED)
    return (meta, prov, title)


def read_structure(doc_dir):
    # type: (str) -> list
    """The outline ``structure.json`` publishes, or ``None`` when there is none.

    ``None`` and ``[]`` are different answers and the floor treats them differently:
    an empty outline is a document with no links to harvest, while a missing or
    unreadable sidecar is NO INFORMATION, and no information must never be read as
    "this document has no links" — that would delete harvested edges from a bundle
    whose structure file merely failed to open.
    """
    path = os.path.join(doc_dir, "structure.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    outline = data.get("outline") if isinstance(data, dict) else None
    return outline if isinstance(outline, list) else None


def _blank(value):
    # type: (object) -> bool
    return value is None or value == "" or value == [] or value == {}


def apply_floor(meta, fm, body, outline, anchors):
    # type: (dict, dict, str, list, set) -> list
    """Fill from evidence the pipeline already has. Returns the field names filled.

    THE POLICY IS ``backend.kb``'s (the ``*_floor`` functions); this is the wiring
    that hands each one its inputs and stamps the provenance. Every fill is a GAP
    fill — a value already in the block, whoever wrote it, is left exactly as it is —
    except ``links``, where the unit is the record rather than the field, and ``uid``,
    which is an alias and therefore never has a value of its own.
    """
    filled = []  # type: list

    def fill(name, value, source):
        if _blank(value) or not _blank(meta.get(name)):
            return
        meta[name] = value
        set_provenance(meta, name, source)
        filled.append(name)

    title, title_source = title_floor(fm.get("source_title"), body,
                                      fm.get("source_relpath") or "", outline)
    fill("title", title, title_source)
    fill("abstract", abstract_floor(body, outline), SOURCE_DERIVED)
    # Arithmetic over two AUTHORED values is not a model guessing a review date; it
    # restates a commitment a person already made (see kb.next_review_due).
    fill("next_review_due",
         next_review_due(meta.get("last_reviewed"), meta.get("review_cadence")),
         SOURCE_DERIVED)

    if outline is not None:
        harvested = harvested_links(outline, anchors)
        current = meta.get("links")
        prov = (meta.get(PROVENANCE_KEY) or {}).get("links")
        if not is_authored(prov, current):
            merged = merge_group_evidence(harvested, current or {})
            if merged != current:
                filled.append("links")
            if merged:
                meta["links"] = merged
                set_provenance(meta, "links",
                               value_source(merged, SOURCE_GENERATED))
            else:
                # A document may honestly have no outbound links. The provenance
                # record is still written, because "looked, found none" and "never
                # looked" are different facts and only one of them means the
                # harvested edges were thrown away. Never silently skip.
                meta.pop("links", None)
                set_provenance(meta, "links", SOURCE_EXTRACTED,
                               value=OrderedDict())

    # An alias is not a peer: it is recomputed from the canonical field every run, so
    # the two can never drift apart and no corpus can grow a second link graph.
    for alias, canonical in ALIAS_FIELDS.items():
        if meta.get(canonical):
            meta[alias] = meta[canonical]
            set_provenance(meta, alias, SOURCE_DERIVED)
    return filled


def _prior_doc_meta(doc_dir):
    # type: (str) -> dict
    """The `doc_meta` block a previous run left in report.json, or {}."""
    try:
        with open(os.path.join(doc_dir, "report.json"), encoding="utf-8") as fh:
            return json.load(fh).get("doc_meta") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def read_knowledge(doc_dir):
    # type: (str) -> tuple
    """``(payload, error)`` from ``knowledge.json`` — the header keys stripped.

    A file that exists but cannot be read is an ERROR, never an empty payload: the
    difference between "this document has no entities" and "this document's entities
    could not be loaded" decides whether a re-run is allowed to overwrite them, and
    guessing wrong destroys hand-corrected work.

    The SHAPE of the file is ``backend.kb``'s to decide (``knowledge_payload``); this
    is only the disk touch.
    """
    path = os.path.join(doc_dir, KNOWLEDGE_FILE)
    if not os.path.isfile(path):
        return (OrderedDict(), "")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=OrderedDict)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return (None, "%s: %s" % (KNOWLEDGE_FILE, e))
    if not isinstance(data, dict):
        return (None, "%s is %s, expected an object"
                % (KNOWLEDGE_FILE, type(data).__name__))
    return (knowledge_payload(data), "")


def render_knowledge(fm, meta, keep_empty=False):
    # type: (dict, dict, bool) -> str
    """``knowledge.json`` text for ``meta``, or ``""`` when there is nothing to write.

    ``keep_empty`` renders the header alone — how a sidecar whose contents were all
    removed gets explicitly emptied rather than left serving relations the document
    no longer claims. The layout itself is ``backend.kb.knowledge_document``'s.
    """
    doc = knowledge_document(order_meta(meta), doc_id=fm.get("doc_id") or "",
                             markdown_sha256=fm.get("markdown_sha256") or "",
                             keep_empty=keep_empty)
    if not doc:
        return ""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _manifest_row(res, run_id, action):
    # type: (dict, str, str) -> OrderedDict
    """One ``manifest.jsonl`` row for one document in this enrichment run.

    Deliberately the SAME shape the writers append, and `status` deliberately keeps
    the writers' meaning — the CONVERSION verdict, read out of `report.json`. Two
    vocabularies in one column would make `status == "ok"` mean "converted" on some
    rows and "nothing outstanding" on others, and no consumer could tell which. What
    THIS stage did is the `action`, which is what that column has always been for,
    and `stage` says who did it."""
    if action not in _ACTIONS:
        raise ValueError("unknown manifest action %r (add it to _ACTIONS and "
                         "docs/reference/output-schema.md)" % (action,))
    row = OrderedDict()
    row["doc_id"] = res.get("doc_id", "")
    row["source_relpath"] = res.get("source_relpath", "")
    row["lane"] = res.get("lane", "")
    row["status"] = res.get("bundle_status", "")
    row["markdown_sha256"] = res.get("markdown_sha256", "")
    # Carried from the report so `corpus_sha256` over these rows names the same
    # corpus the build named — that identity is what makes "did enrichment run over
    # the documents I think it did?" answerable.
    row["source_sha256"] = res.get("source_sha256", "")
    row["error"] = res.get("error", "") if action == "failed" else ""
    row["run_id"] = run_id
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row["action"] = action
    row["stage"] = ENTRYPOINT
    return row


def _log_manifest(bundles, run_id, results, deferred_ids):
    # type: (str, str, list, list) -> list
    """Append this run's manifest rows and return them.

    A row for every document the run touched AND for every one `--limit` cut, for
    the same reason the writers log their skips: a log with a hole where the
    deferrals were cannot say how many runs there have been."""
    rows = []
    for res in results:
        if res["status"] in ("unreadable", "unparseable", "error"):
            action = "failed"
        elif res.get("unchanged"):
            action = "unchanged"
        else:
            action = "enriched"
        rows.append(_manifest_row(res, run_id, action))
    for did in deferred_ids:
        rows.append(_manifest_row({"doc_id": did}, run_id, "deferred"))
    with open(os.path.join(bundles, bb.MANIFEST), "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def enrich_one(doc_dir, vocab, client, run_id, args, cache, run_doc=None,
               run_decisions=()):
    # type: (str, object, object, str, object, dict, dict, object) -> dict
    """One bundle: read, compute, optionally ask a model, write back. Never raises.

    ``run_doc`` is this stage's compact ``run{}`` block and ``run_decisions`` the
    choices that were made once for the whole run (which tier, which vocabulary,
    which namespace, which permalink base). Both are recorded into every bundle's
    ``report.json``, because a bundle has to be able to answer for itself: a reader
    holding one document should not have to find the run log to learn which
    switches produced the ``id`` in front of them."""
    doc_id = os.path.basename(doc_dir.rstrip(os.sep))
    md_path = os.path.join(doc_dir, "document.md")
    out = {"doc_id": doc_id, "status": "skipped", "rejected": [], "proposals": {}}
    decisions = list(run_decisions or [])
    try:
        with open(md_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        out["status"] = "unreadable"
        out["error"] = "%s" % e
        return out
    try:
        fm, body = split_front_matter(text)
    except YamlSubsetError as e:
        out["status"] = "unparseable"
        out["error"] = "%s" % e
        return out

    front = fm.get(META_KEY)
    front = front if isinstance(front, dict) else OrderedDict()
    prior_know, know_err = read_knowledge(doc_dir)
    if know_err:
        # Refuse rather than proceed on a partial view. Treating an unreadable
        # knowledge.json as "no knowledge yet" would let this run regenerate and
        # overwrite entities and relations a person may have corrected by hand — the
        # one thing "authored always wins" exists to prevent.
        out["status"] = "unreadable"
        out["error"] = know_err
        return out
    # A field living in BOTH files is two sources of truth, and this run is about to
    # rewrite the sidecar from the merged view — destroying whichever copy loses.
    # Say so: the merged mapping below cannot show that two live copies existed, so
    # if this is silent the loss is invisible everywhere.
    out["duplicated"] = meta_collisions(front, prior_know)
    # ONE merged view from here on. Every rule in backend.kb was written against a
    # single mapping and stays that way: the split is storage, not policy.
    existing = merge_meta(front, prior_know)
    det, det_prov, title = deterministic_meta(fm, body, run_id, args.namespace,
                                              args.source_base_url)

    meta = OrderedDict()
    meta.update(det)
    # A prior value with NO provenance is treated as hand-written (that is the whole
    # "authored always wins" rule). Which means it has to be RECORDED as authored —
    # see below.
    inherited_authored = set()
    for key, val in existing.items():          # authored/prior values win over derived
        if key == PROVENANCE_KEY:
            continue
        prior_src = ((existing.get(PROVENANCE_KEY) or {}).get(key) or {}).get("source")
        if key in _ALWAYS_DERIVED:
            continue                           # a run stamp is never inherited
        prov_entry = ((existing.get(PROVENANCE_KEY) or {}).get(key) or {})
        # A HAND EDIT UNDER A MACHINE STAMP IS STILL A HAND EDIT. Deciding on the
        # provenance LABEL alone reverted every correction an operator made to a
        # field this pipeline had already written — which is every field, in every
        # bundle it has ever produced. `unique_id`'s own docstring tells a reader
        # to write an `id` by hand after a collision and promises it "outranks this
        # forever"; without consulting `is_authored`, the next run silently put the
        # colliding value back and orphaned every `see_also` pointing at it.
        # `is_authored` is what carries the value_sha edit detection, so it is what
        # can tell a stamp that still describes its value from one that does not.
        if key in det and (prior_src in (None, "authored")
                           or is_authored(prov_entry, val)):
            meta[key] = val
            if prior_src is None:
                inherited_authored.add(key)
        elif key not in det:
            meta[key] = val
    meta[PROVENANCE_KEY] = OrderedDict(existing.get(PROVENANCE_KEY) or {})
    for name, source in det_prov.items():
        if name not in (existing.get(PROVENANCE_KEY) or {}):
            # AUTHORED WINS HAS TO SURVIVE THE NEXT RUN. Stamping an inherited
            # hand-written value with the DERIVED source it would have had makes the
            # following run see `source: derived`, which is not in (None, "authored"),
            # so the derived value wins and the hand-written one is destroyed. That
            # turns "authored always wins" into "authored wins once" — and silently:
            # a hand-picked `id` reverts to a slug on run two, orphaning every
            # `see_also` that pointed at it.
            set_provenance(meta, name,
                           SOURCE_AUTHORED if name in inherited_authored else source)

    meta, moved = revalidate_generated(meta, vocab)
    out["revalidated"] = moved

    # THE FLOOR, before the model is asked. Order matters twice over: a title read
    # from the document's own properties has to be in the block for `is_protected` to
    # stop the model contradicting it, and the harvested links have to be there for
    # the model's links to MERGE with rather than replace.
    outline = read_structure(doc_dir)
    anchors = body_anchors(body)
    out["floor"] = apply_floor(meta, fm, body, outline, anchors)

    # The prompt is built from TIER-0/1 INPUTS ONLY — deliberately not from a title a
    # previous run's model produced. Feeding generated output back into the request
    # makes the prompt depend on enrichment state, which changes its sha, which misses
    # the cache and re-asks forever. The deterministic floor title is safe on both
    # counts: it is a function of the source bytes, so it is the same every run.
    floor_title = title_floor(fm.get("source_title"), body,
                              fm.get("source_relpath") or "", outline)[0] or title
    keyword_seed = keyword_candidates(body, limit=args.keyword_limit)

    model = getattr(client, "model", "") if client else ""
    prompt_sha = ""
    # A run that asks nothing (everything already filled) must not blank the stamps
    # the previous run recorded — report.json would then disagree with the per-field
    # `_provenance` in document.md about which request produced the values.
    prior = _prior_doc_meta(doc_dir)
    if not model:
        model = prior.get("model", "")
    prior_sha = prior.get("prompt_sha", "")
    if client is not None:
        missing = [n for n in request_spec(vocab).keys()
                   if n not in meta or meta.get(n) in (None, "", [], {})]
        if missing or args.force:
            # The spec is ALWAYS the full field set, never narrowed to what is still
            # missing. Narrowing looks like an optimisation and is a trap: the prompt
            # then changes every run, so its sha changes, so the cache never hits and
            # the same document is re-asked forever while `report.json` disagrees with
            # itself between runs. A stable request is what makes "safe to run twice"
            # true. Whether to ask at all is still decided by `missing`.
            spec = request_spec(vocab)
            prompt = build_prompt(spec, floor_title, body, args.excerpt_chars,
                                  anchors)
            if keyword_seed:
                prompt += ("\n\nIDENTIFIERS FOUND IN THE BODY (candidates for "
                           "`keywords`; include only the ones that matter):\n"
                           + ", ".join(keyword_seed))
            prompt_sha = _sha12("%s\x00%s\x00v%s" % (model, prompt, vocab.version))
            key = "%s:%s" % (_sha12(body), prompt_sha)
            hit = cache.get(key)
            if hit is not None and not args.no_cache:
                reply = hit
                # A choice, not a problem: "how many documents were answered from
                # the cache" is the question that says whether a re-run cost
                # anything, and it is unanswerable from prose.
                decisions.append(decision(
                    "cache_hit", "stored-reply",
                    "a stored answer for this body, model, prompt and vocabulary "
                    "version was reused instead of re-asked",
                    {"key": key}))
            else:
                res = client.text_result(
                    prompt, response_format=None if args.no_json_mode
                    else {"type": "json_object"})
                if not res.get("ok"):
                    out["status"] = "model-unavailable"
                    reply = {}
                else:
                    reply = parse_json_reply(res.get("text", ""))
                    if reply:
                        cache[key] = reply
                        out["cache_write"] = key
                    else:
                        # The model answered but the answer did not parse (truncated
                        # at max_tokens, a prose refusal, a mangled fence). Caching
                        # {} would make the document permanently unaskable while
                        # reporting nothing. Say so and leave it retryable.
                        out["status"] = "reply-unparsed"
                        out["reply_chars"] = len(res.get("text", "") or "")
            verdict = accept_model_meta(reply, vocab, meta, model, prompt_sha,
                                        anchors)
            for name, value in verdict["accepted"].items():
                meta[name] = value
                # The verdict decided the SOURCE too: a grouped field that still
                # carries harvested records is not wholly generated, and stamping it
                # `generated` would vouch for the evidence with the model's name.
                rec = verdict["provenance"].get(name) or {}
                set_provenance(meta, name, rec.get("source") or SOURCE_GENERATED,
                               model, prompt_sha)
            for name, values in verdict["proposals"].items():
                slot = name + "_proposed"
                meta[slot] = sorted(set(list(meta.get(slot) or []) + list(values)))
            out["rejected"] = verdict["rejected"]
            out["proposals"] = verdict["proposals"]

    # Tier 1 depends on a tier-2 parent here: `slug` is the URL form of whatever the
    # title finally turned out to be, so it is derived once a title exists — on this
    # run or a later one — rather than only from `source_title` at build time. It is
    # NOT an identity: nothing resolves against it, so two documents called "Overview"
    # sharing a slug costs nothing. `id` is the identity, and it comes from the path.
    have_title = meta.get("title") or title
    if have_title and not meta.get("slug"):
        meta["slug"] = slugify(have_title)
        set_provenance(meta, "slug", SOURCE_DERIVED)

    cov = meta_coverage(meta, vocab)
    meta["vocab_version"] = vocab.version
    set_provenance(meta, "vocab_version", SOURCE_DERIVED)

    kn_path = os.path.join(doc_dir, KNOWLEDGE_FILE)
    prior_kn_text = ""
    if os.path.isfile(kn_path):
        try:
            with open(kn_path, encoding="utf-8") as fh:
                prior_kn_text = fh.read()
        except OSError:
            prior_kn_text = ""

    def render_both(stamp):
        """The exact bytes both files would be written with, at ``run_at = stamp``.

        EXACT is the whole contract: the unchanged check below compares these against
        what is on disk, so anything the writer would do differently means the run
        never converges. That was a real bug — an empty payload rendered as "" here
        while the writer wrote a header-only object, so the two never matched and
        `document.md` earned a fresh `run_at` on every single run, forever.
        """
        if isinstance(meta.get("extraction"), dict):
            meta["extraction"]["run_at"] = stamp
        front_block, _know = split_meta(order_meta(meta))
        fm[META_KEY] = order_meta(front_block)
        return (render_front_matter(fm) + "\n" + body,
                render_knowledge(fm, meta, keep_empty=bool(prior_kn_text)))

    # IDEMPOTENCE, now across TWO files. `extraction.run_at` is a wall-clock stamp, so
    # writing it every run would churn document.md even when nothing moved — not what
    # "safe to run twice" means for a file under version control. So: render with the
    # PRIOR stamp first and compare BOTH files; only a bundle that actually moved
    # earns a fresh stamp. Comparing document.md alone would miss a run that changed
    # nothing but the entity list, leaving knowledge.json stale beside a document that
    # claims it is current.
    prior_run_at = ((existing.get("extraction") or {}).get("run_at")
                    if isinstance(existing.get("extraction"), dict) else None)
    md_text, kn_text = render_both(prior_run_at or run_id)
    if prior_run_at and md_text == text and kn_text == prior_kn_text:
        out["unchanged"] = True
    else:
        md_text, kn_text = render_both(run_id)
        # ORDER MATTERS, and this way round. `document.md` is written with the
        # knowledge fields ALREADY STRIPPED, so writing it first opens a window where
        # a failure leaves the payload in neither file — unrecoverable, and worst on
        # exactly the v1 -> v2 migration, where the knowledge is authored and lives
        # only in front matter. Every unmigrated bundle passes through this window on
        # its first v2 run. Writing the sidecar first makes a crash harmless: the
        # payload is then in BOTH files, and `merge_meta` resolves that in front
        # matter's favour on the next run, which completes the migration.
        if kn_text:
            _write_atomic(kn_path, kn_text)
            out["knowledge"] = KNOWLEDGE_FILE
        _write_atomic(md_path, md_text)

    rp = os.path.join(doc_dir, "report.json")
    try:
        with open(rp, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        report = None
    if report is not None:
        report["doc_meta"] = doc_meta_report(
            client is not None, cov["expected"], cov["filled"], cov["authored"],
            cov["invalid"], cov["pending"], SCHEMA_VERSION, vocab.version,
            model, prompt_sha or prior_sha)
        # Replace exactly THIS stage's records and leave every other stage's alone.
        # Appending would grow the list without bound across re-runs; rewriting the
        # list would delete the conversion's choices to record the enricher's.
        report["decisions"] = (
            [d for d in (report.get("decisions") or [])
             if d.get("stage") != ENTRYPOINT]
            + stamp_stage(decisions, ENTRYPOINT))
        if run_doc:
            bb.record_stage_run(report, run_doc)
        _write_atomic(rp, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        # What the manifest row needs about the CONVERSION, read rather than
        # invented: enrichment never opens the source document, so a status or a
        # source hash it made up here would be a second, weaker source of truth.
        out["bundle_status"] = report.get("status", "")
        out["source_sha256"] = report.get("source_sha256", "")

    if out["status"] == "skipped":
        out["status"] = "ok" if cov["pending"] == 0 and cov["invalid"] == 0 \
            else "incomplete"
    out["source_relpath"] = fm.get("source_relpath") or ""
    out["lane"] = fm.get("lane") or ""
    out["markdown_sha256"] = fm.get("markdown_sha256") or ""
    out.update(cov)
    return out


def main(argv=None, client=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description="Fill each bundle's document metadata block: the deterministic "
                    "tiers always, the model tier when a model is reachable.")
    ap.add_argument("--bundles", default=os.path.join(_REPO, "data", "bundles"),
                    help="bundle root holding <doc_id>/document.md (default data/bundles)")
    ap.add_argument("--vocab", default="",
                    help="vocabulary file (default $DOC2MD_VOCAB / config/vocab.yaml)")
    ap.add_argument("--namespace", default="",
                    help="prefix for the derived id (e.g. an org or corpus name)")
    ap.add_argument("--source-base-url",
                    default=os.environ.get("DOC2MD_SOURCE_BASE_URL", ""),
                    help="where the source documents are served, e.g. "
                         "https://intranet/docs — makes meta.source.url absolute "
                         "(default $DOC2MD_SOURCE_BASE_URL; relative otherwise)")
    ap.add_argument("--only", action="append", default=[],
                    help="enrich ONLY this doc id; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N documents")
    ap.add_argument("--force", action="store_true",
                    help="re-ask the model for every field, not just the empty ones")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore cached model answers (still writes new ones)")
    ap.add_argument("--no-json-mode", action="store_true",
                    help="do not send response_format (for a server that rejects it)")
    ap.add_argument("--excerpt-chars", type=int, default=12000,
                    help="how much document body the model sees (default 12000)")
    ap.add_argument("--keyword-limit", type=int, default=120,
                    help="how many tier-1 identifier candidates to offer (default 120)")
    ap.add_argument("--run-id", default="",
                    help="stamp extraction.run_at with this id (default: UTC timestamp)")
    ap.add_argument("--fail-on-pending", action="store_true",
                    help="exit 3 when any model-writable field is still empty "
                         "(default: pending is reported, not a failure)")
    ap.add_argument("--vlm-url", default="",
                    help="OpenAI-compatible chat endpoint; omit to run without a model")
    ap.add_argument("--vlm-model", default="", help="model name to request")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.bundles):
        ap.error("bundle root not found: %s" % args.bundles)
    vocab = load_vocab(args.vocab or None)
    missing_bindings = check_schema_bindings(vocab)
    if missing_bindings:
        # A closed field whose vocabulary is absent would accept ANYTHING. Refuse
        # rather than fail open on a vocabulary override that is missing a term list.
        ap.error("vocabulary does not declare: %s" % ", ".join(missing_bindings))
    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    if client is None and args.vlm_url:
        import vlm_client                       # noqa: E402  (deferred: keeps --help cheap)
        cfg = load_ingest_config()
        client = vlm_client.VlmClient(args.vlm_url,
                                      model=args.vlm_model or cfg.vlm_model,
                                      max_tokens=cfg.vlm_max_tokens,
                                      temperature=0.0)
        if not client.healthy():
            print("  [model] %s not reachable -> tier-2 fields stay PENDING "
                  "(re-run when up)" % args.vlm_url, file=sys.stderr)
            client = None

    dirs = sorted(d for d in os.listdir(args.bundles)
                  if os.path.isfile(os.path.join(args.bundles, d, "document.md")))
    if args.only:
        want = set(args.only)
        dirs = [d for d in dirs if d in want]
        if not dirs:
            ap.error("--only matched no bundle: %s" % ", ".join(sorted(want)))
    total = len(dirs)
    selected = list(dirs)                    # after --only, before --limit
    if args.limit:
        dirs = dirs[:args.limit]
    deferred_ids = selected[len(dirs):]
    deferred = len(deferred_ids)

    cache_path = os.path.join(args.bundles, CACHE_NAME)
    cache = {}
    if os.path.isfile(cache_path):
        recs = []
        with open(cache_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        continue
        # cache_last_wins returns {key: record}; the records carry the reply.
        for key, rec in cache_last_wins(recs, "key").items():
            cache[key] = rec.get("reply")

    msg = "kb-enrich documents=%d" % total
    if deferred:
        msg += "  (--limit deferred %d more)" % deferred     # never a silent cap
    msg += "  model=%s  vocab=v%s -> %s" % (
        getattr(client, "model", "") or "none", vocab.version, args.bundles)
    print(msg, file=sys.stderr)

    # THE RUN, recorded before anything is written. `--bundles` is redacted to
    # `<src>` rather than `<path>` because that is the root a replay must be handed
    # back — see replay_run.ENTRYPOINTS, which knows this stage reads and writes the
    # same root and therefore needs no `--out`.
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = bb._run_context(ENTRYPOINT, args, raw_argv, run_id, tools={},
                          paths={"--bundles": "<src>"}, root_attr="bundles",
                          extra_config=_cli_provenance(ap, args))
    run_doc = compact_run(run, "%s#%s" % (bb.RUNS, run_id))
    # The choices this run made ONCE, for every document. Each of them moves a field
    # the rubric grades: the tier decides whether tier-2 is filled or PENDING, the
    # vocabulary decides which values survive, the namespace decides `meta.id` and
    # its `uid` alias, and the base decides whether `meta.source.url` is a permalink
    # or a relative reference.
    run_decisions = [
        decision("metadata_tier", "model" if client is not None else "deterministic",
                 "a model was reachable" if client is not None
                 else "no --vlm-url or the endpoint was unreachable: tier-2 fields "
                      "stay pending rather than guessed",
                 {"model": getattr(client, "model", "") or ""}),
        decision("vocabulary_selected", "v%s" % vocab.version,
                 "every closed-vocabulary value was graded against this term list",
                 {"schema_version": SCHEMA_VERSION}),
        decision("identity_namespace", args.namespace or "(none)",
                 "meta.id and its uid alias are derived from the source path under "
                 "this namespace",
                 {"namespace": args.namespace or ""}),
        decision("permalink_base",
                 "absolute" if args.source_base_url else "relative",
                 "meta.source.url is resolved against --source-base-url"
                 if args.source_base_url else
                 "no base is configured, so meta.source.url is a relative URI "
                 "reference — a base is never invented",
                 {"base": args.source_base_url or ""}),
    ]

    t0 = time.time()
    results = []  # type: list
    new_cache = []  # type: list
    for did in dirs:
        try:
            res = enrich_one(os.path.join(args.bundles, did), vocab, client, run_id,
                             args, cache, run_doc, run_decisions)
        except Exception as e:                      # never lose the whole run
            # enrich_one guards the conditions it knows about, but a corrupt bundle,
            # a client that raises, or a disk error must cost one document, not the
            # whole corpus — and certainly not the model answers already paid for.
            res = {"doc_id": did, "status": "error",
                   "error": "%s: %s" % (type(e).__name__, e)}
        results.append(res)
        if res.get("cache_write"):
            new_cache.append({"key": res["cache_write"], "doc_id": did,
                              "reply": cache.get(res["cache_write"], {}),
                              "ts": run_id})
        if res["status"] in ("unreadable", "unparseable", "error"):
            print("  FAIL %s %s" % (did, res.get("error", "")), file=sys.stderr)
        for name, value, reason in res.get("rejected", []):
            print("  [rejected] %s %s=%r (%s)" % (did, name, value, reason))
        for name in res.get("duplicated", []):
            # Two live copies of one field, one of which this run just overwrote.
            print("  [duplicated] %s %s was in BOTH document.md and %s — front "
                  "matter won, the sidecar copy is gone" % (did, name, KNOWLEDGE_FILE))
        for name, value, action in res.get("revalidated", []):
            # A prior answer that the CURRENT vocabulary no longer accepts. Announced,
            # never silent — this is what a vocabulary bump is supposed to look like.
            print("  [revalidated] %s %s=%r -> %s" % (did, name, value, action))

    if new_cache:
        with open(cache_path, "a", encoding="utf-8") as fh:
            for rec in new_cache:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(os.path.join(args.bundles, COV_NAME), "a", encoding="utf-8") as fh:
        for res in results:
            fh.write(json.dumps(dict(res, ts=run_id), ensure_ascii=False,
                                default=str) + "\n")

    ok = sum(1 for r in results if r["status"] == "ok")
    incomplete = sum(1 for r in results if r["status"] == "incomplete")
    failed = sum(1 for r in results
                 if r["status"] in ("unreadable", "unparseable", "error"))
    unparsed = sum(1 for r in results if r["status"] == "reply-unparsed")
    unavailable = sum(1 for r in results if r["status"] == "model-unavailable")
    pending = sum(r.get("pending", 0) for r in results)

    # THE RUN LOG. One row per document per run, the deferred ones included, in the
    # same shape and the same file the writers append to — a stage that rewrote
    # `meta.id` for 500 documents and left no trace in the corpus log is exactly the
    # hole this closes. Then one runs.jsonl row, which is what the rows join to.
    rows_written = _log_manifest(args.bundles, run_id, results, deferred_ids)
    run["started_at"] = started
    run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bb._append_run(args.bundles, run, rows_written,
                   {"ok": ok, "incomplete": incomplete, "failed": failed,
                    "unchanged": sum(1 for r in results if r.get("unchanged")),
                    "deferred": deferred})

    print("kb-enrich: ok=%d incomplete=%d failed=%d model-unavailable=%d "
          "reply-unparsed=%d fields-pending=%d in %.1fs"
          % (ok, incomplete, failed, unavailable, unparsed, pending,
             time.time() - t0), file=sys.stderr)
    # Exit codes say what HAPPENED, not what is left to do. A deterministic
    # (no-model) run leaves 20 fields pending BY DESIGN and is a complete success —
    # report.json agrees, recording doc_meta.gate "disabled" rather than a failure.
    # Returning non-zero for that made every `set -e` chain break on a healthy run,
    # and made the exit code disagree with the artifact. Pending is now a fact you
    # can opt into failing on, not a failure by default.
    if failed:
        return 1
    if pending and args.fail_on_pending:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
