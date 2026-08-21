"""
title: The run block and the decision record
layer: backend
public_api: yes
summary: What a run was — argv, resolved configuration with the provenance of each value, and every branch the pipeline chose.
"""
# The goal this serves, stated by the owner: "have all the reporting information
# needed to re-run, re-establish, or repeat a run based on the report — the switches
# that were used and the decisions that were taken."
#
# Two halves, deliberately separate:
#   run{}        — the INPUTS. What was asked for, by which code, on what.
#   decisions[]  — the CHOICES. Which lane, which fallback, cache hit or miss.
# `warnings[]` already carries PROBLEMS. A choice is not a problem, and mixing the
# two means neither can be aggregated: "how many documents took the text-layer
# fallback" is unanswerable if the answer is buried in a prose detail string.
#
# Pure: strings and dicts in, an OrderedDict out. Every disk touch (reading .git,
# asking soffice its version, hashing a tree) belongs to the caller.
import hashlib
import re

from collections import OrderedDict

__all__ = ["run_block", "decision", "stamp_stage", "config_provenance",
           "redact_argv", "safe_value", "path_id", "corpus_id", "compact_run",
           "DECISION_CODES"]

# Every branch the pipeline is allowed to record. A closed list on purpose: an
# unnamed decision is one nobody can aggregate, and a typo would silently create a
# category of one. `tests/integration/test_docs_parity.py` checks these are documented.
DECISION_CODES = (
    "lane_selected",          # which lane converted this document, and why
    "preconvert",             # a legacy format went through soffice first
    "ocr_routed",             # a PDF page went to OCR, with the evidence that decided it
    "body_source",            # docling | text-layer | hybrid
    "tokenizer_selected",     # which tokenizer backed every token count
    "cache_hit",              # a stored answer was reused instead of re-asked
    "gate_coerced",           # a non-office gate was forced off "pass"
    "empty_source",           # a zero-byte source: vacuously lossless, nothing to lose
    "captions_carried",       # a --force rebuild reused prior captions by image_id
    "skipped_existing",       # an already-built bundle was left alone
    "metadata_tier",          # enrichment ran with a model, or deterministically
    "vocabulary_selected",    # which term list every value was graded against
    "identity_namespace",     # the namespace meta.id / meta.uid were derived under
    "permalink_base",         # whether meta.source.url came out absolute or relative
)

_REDACT = ("--src", "--out", "--bundles", "--assets", "--assets-dir", "--md-dir",
           "--dest", "--rpms", "--corpus", "--text-out", "--json", "--vocab",
           "--prompt-file", "--domain-file", "--expectations", "--status-file",
           "--worker-cmd")


def path_id(path):
    # type: (str) -> str
    """A stable id for a filesystem location that is not the location itself.

    The root ``CLAUDE.md`` forbids absolute host paths in any artifact, and a bundle
    is published output. Hashing the absolute path keeps "was this the same tree?"
    answerable without publishing where it is."""
    return hashlib.sha256((path or "").encode("utf-8")).hexdigest()[:16]


def corpus_id(rows):
    # type: (list) -> str
    """A content identity for the whole corpus, from the manifest rows.

    ``sha256`` over sorted ``doc_id:source_sha256`` pairs — so two runs over the same
    documents agree, and a single changed byte in a single source does not. Free:
    every input is already hashed per document."""
    pairs = sorted("%s:%s" % (r.get("doc_id", ""), r.get("source_sha256", ""))
                   for r in rows or [])
    return hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()


def _redact_piece(piece):
    # type: (str) -> str
    """One whitespace-free fragment with any absolute host path replaced by its id.

    THE TEST IS ON THE VALUE, never on the flag that carried it. Matching flag
    names was unsound twice over: argparse accepts unambiguous PREFIXES, so a set
    keyed on ``--src`` never fired for ``--sr /tmp/x``; and switches nobody thought
    to list take paths routinely (``--only /abs/spec.docx``,
    ``--tokenizer char:/home/me/models/tok``). Anything shaped like an absolute
    path is redacted whatever switch carried it."""
    if piece.startswith("//"):                  # a scheme-relative URL, not a path
        return piece
    if piece.startswith("/") and len(piece) > 1:
        return "<path:%s>" % path_id(piece)
    head, sep, tail = piece.partition(":")
    # A path riding inside a compound value: `char:/home/me/models/tok`. A URL is
    # excluded by the `//` test above, so `--source-base-url https://wiki/docs`
    # survives verbatim — it is a switch that decided the output, not a host path.
    if sep and tail.startswith("/") and not tail.startswith("//"):
        return "%s:<path:%s>" % (head, path_id(tail))
    return piece


def _redact_value(value):
    # type: (object) -> object
    """A whole value with every absolute host path inside it replaced by its id.

    A value that IS an absolute path is redacted whole, spaces and all — plenty of
    source documents live at ``/vols/spec drafts/radar spec.docx``. Only a value
    that is not itself a path is split on whitespace, because one argv element can
    be an entire command line (``--worker-cmd "python3 /repo/w.py --shard 1"``) and
    a path buried at word three leaks exactly as much as one at word one."""
    if not isinstance(value, str) or "/" not in value:
        return value
    if value.startswith("/"):
        return _redact_piece(value)
    parts = re.split(r"(\s+)", value)
    return "".join(p if i % 2 else _redact_piece(p) for i, p in enumerate(parts))


def _placeholder_for(flag, paths):
    # type: (str, dict) -> str
    """The placeholder a named path flag asks for, honouring argparse's PREFIXES.

    ``--sr`` IS ``--src`` to argparse. A prefix that is ambiguous within the known
    set gets no placeholder and falls through to the value test above, which is the
    safe direction: it redacts rather than reconstructs."""
    if flag in paths:
        return paths[flag]
    if flag in _REDACT:
        return "<path>"
    if not flag.startswith("--") or len(flag) < 4:
        return ""
    known = dict((name, "<path>") for name in _REDACT)
    known.update(paths)
    hits = sorted(set(v for name, v in known.items() if name.startswith(flag)))
    return hits[0] if len(hits) == 1 else ""


def redact_argv(argv, paths=None):
    # type: (list, dict) -> list
    """``argv`` with path VALUES replaced by placeholders, keeping every switch.

    A replay needs to know that ``--tokenizer tiktoken:cl100k_base`` was passed far
    more than it needs the operator's home directory, and the one thing that must
    never land in a published artifact is an absolute host path. ``paths`` maps a
    flag to the placeholder a REPLAY can fill back in, e.g. ``{"--src": "<src>"}``;
    the flags in the default set become ``<path>``; and every remaining value is
    scrubbed by shape, so a path can never reach an artifact through a switch
    nobody listed."""
    paths = paths or {}
    out = []
    expect = None
    for arg in list(argv or []):
        # A value never starts with `--`; a flag that swallowed the next SWITCH
        # would silently delete it from the record.
        if expect is not None and not arg.startswith("--"):
            out.append(expect)
            expect = None
            continue
        expect = None
        flag, eq, value = arg.partition("=")
        placeholder = _placeholder_for(flag, paths) if arg.startswith("-") else ""
        if eq:
            out.append("%s=%s" % (flag, placeholder or _redact_value(value)))
        elif placeholder:
            out.append(arg)
            expect = placeholder
        else:
            out.append(_redact_value(arg))
    return out


def safe_value(value):
    # type: (object) -> object
    """A published value with any absolute host path replaced by its id.

    Public, because every caller that writes a value into an artifact needs it
    and the alternative is each of them re-deciding what a path looks like.

    Resolved configuration is full of paths (``markdown_dir``, ``assets_dir``, a
    vendored soffice), and the root ``CLAUDE.md`` forbids an absolute host path in
    published output. Hashing keeps the value COMPARABLE — two runs that used the
    same directory still agree — without disclosing whose directory it was. Same
    shape test as ``argv``, so the two cannot drift into disagreeing about what a
    path looks like."""
    if isinstance(value, (list, tuple)):
        return [safe_value(v) for v in value]
    return _redact_value(value)


def config_provenance(now, without_env, without_file, env_names=()):
    # type: (dict, dict, dict, tuple) -> OrderedDict
    """Where each resolved configuration value came from.

    Derived by DIFFERENCE, using the real loader as its own oracle: resolve once
    normally, once with the environment removed, once with the config file removed
    too. A value that moves when the environment goes came from the environment; one
    that moves when the file goes came from the file; one that never moves is the
    built-in default. There is no second copy of the precedence rules to drift from
    the first — which is the whole reason it is done this way.

    ``env_names`` records which ``DOC2MD_*`` variables were merely PRESENT, since an
    environment value that happens to equal the default is invisible to the diff and
    is still something a person re-establishing the run needs to know about."""
    out = OrderedDict()
    for key in sorted(now or {}):
        value = now[key]
        if key in (without_env or {}) and without_env[key] != value:
            source = "env"
        elif key in (without_file or {}) and without_file[key] != value:
            source = "file"
        else:
            source = "default"
        out[key] = OrderedDict([("value", safe_value(value)), ("from", source)])
    if env_names:
        out["_env_present"] = OrderedDict(
            [("value", sorted(env_names)), ("from", "env")])
    return out


def decision(code, chose, reason, evidence=None):
    # type: (str, object, str, dict) -> OrderedDict
    """One branch the pipeline took, as a record rather than a sentence.

    ``code`` must be one of ``DECISION_CODES``; ``chose`` is what was chosen,
    ``reason`` why in one human phrase, and ``evidence`` the numbers that decided it
    — so "why did this page go to OCR" is answerable from the artifact."""
    if code not in DECISION_CODES:
        raise ValueError("unknown decision code %r (add it to DECISION_CODES and "
                         "docs/reference/output-schema.md)" % (code,))
    # `chose` and `evidence` are published output like everything else here, and a
    # caller reaching for "which vocabulary file" or "which base url" is exactly the
    # caller most likely to hand this an absolute path.
    rec = OrderedDict([("code", code), ("chose", safe_value(chose)),
                       ("reason", reason)])
    if evidence:
        rec["evidence"] = OrderedDict(
            (key, safe_value(val)) for key, val in sorted(evidence.items()))
    return rec


def stamp_stage(decisions, stage):
    # type: (list, str) -> list
    """Every record re-emitted carrying the stage that took it, right after ``code``.

    Two entrypoints now write into one ``decisions[]`` — the writer converts, the
    enricher re-derives ``meta.id`` and the permalink — and an unattributed record
    can answer neither "which run chose this?" nor "which records are mine to
    replace?". ``stage`` is that stage's ``run.entrypoint``, so there is one word
    for one thing. Pure: a new list of new records, the input untouched."""
    out = []
    for rec in decisions or []:
        stamped = OrderedDict()
        for key, value in rec.items():
            stamped[key] = value
            if key == "code":
                stamped["stage"] = stage
        stamped["stage"] = stage
        out.append(stamped)
    return out


def run_block(entrypoint, run_id, argv=(), code=None, host=None, config=None,
              tools=None, source_root_id="", started_at="", finished_at=""):
    # type: (...) -> OrderedDict
    """Everything needed to repeat this run, in the order a person reads it.

    Deliberately in ``report.json`` and not only in the front matter: a FAILED
    document publishes ``report.json`` alone, and the run that produced a failure is
    exactly the one somebody needs to reconstruct."""
    out = OrderedDict()
    out["entrypoint"] = entrypoint
    out["run_id"] = run_id
    if started_at:
        out["started_at"] = started_at
    if finished_at:
        out["finished_at"] = finished_at
    out["argv"] = list(argv or [])
    if source_root_id:
        out["source_root_id"] = source_root_id
    out["code"] = OrderedDict(code or {})
    out["host"] = OrderedDict(host or {})
    out["config"] = OrderedDict(config or {})
    if tools:
        out["tools"] = OrderedDict(sorted(tools.items()))
    return out


def compact_run(run, config_ref):
    # type: (dict, str) -> OrderedDict
    """The per-document copy of ``run{}``: everything except the resolved config.

    A report is meant to be self-contained, and it stays so for the question people
    actually ask of one bundle — which code, which switches, which run. What is
    dropped is the ~30-entry resolved-configuration table, which is identical for
    every document in the run and would otherwise be duplicated once per bundle. The
    omission is NAMED (``config_ref``) rather than silent, and the full block lives in
    ``runs.jsonl`` one join away on ``run_id``."""
    out = OrderedDict()
    for key, value in (run or {}).items():
        if key == "config":
            out["config_ref"] = config_ref
            continue
        out[key] = value
    return out
