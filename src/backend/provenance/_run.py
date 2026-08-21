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

from collections import OrderedDict

__all__ = ["run_block", "decision", "config_provenance", "redact_argv",
           "path_id", "corpus_id", "compact_run", "DECISION_CODES"]

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


def redact_argv(argv, paths=None):
    # type: (list, dict) -> list
    """``argv`` with path VALUES replaced by placeholders, keeping every switch.

    A replay needs to know that ``--tokenizer tiktoken:cl100k_base`` was passed far
    more than it needs the operator's home directory, and the one thing that must
    never land in a published artifact is an absolute host path. ``paths`` maps a
    flag to the placeholder to use, e.g. ``{"--src": "<src>"}``; anything in the
    default set that is not named there becomes ``<path>``."""
    paths = paths or {}
    out = []
    expect = None
    for arg in list(argv or []):
        if expect is not None:
            out.append(expect)
            expect = None
            continue
        out.append(arg)
        flag = arg.split("=", 1)[0]
        if flag in _REDACT or flag in paths:
            placeholder = paths.get(flag, "<path>")
            if "=" in arg:
                out[-1] = "%s=%s" % (flag, placeholder)
            else:
                expect = placeholder
    return out


def _safe_value(value):
    # type: (object) -> object
    """A configuration value with any absolute host path replaced by its id.

    Resolved configuration is full of paths (``markdown_dir``, ``assets_dir``, a
    vendored soffice), and the root ``CLAUDE.md`` forbids an absolute host path in
    published output. Hashing keeps the value COMPARABLE — two runs that used the
    same directory still agree — without disclosing whose directory it was."""
    if isinstance(value, (list, tuple)):
        return [_safe_value(v) for v in value]
    if isinstance(value, str) and value.startswith("/") and len(value) > 1:
        return "<path:%s>" % path_id(value)
    return value


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
        out[key] = OrderedDict([("value", _safe_value(value)), ("from", source)])
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
    rec = OrderedDict([("code", code), ("chose", chose), ("reason", reason)])
    if evidence:
        rec["evidence"] = OrderedDict(sorted(evidence.items()))
    return rec


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
