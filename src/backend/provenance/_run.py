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

# A scheme, then `://`. Used to tell a network URL (a switch that decided the
# output, and safe to publish) from `file:///abs` — or any other scheme with an
# EMPTY authority — which is an absolute host path wearing a scheme.
_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")

# A long option name: everything a `--flag` may be spelled with, so the switch half
# of `--src/vols/x` can be told from the path half. A long option NEVER attaches its
# value without `=` (that form is split before this), so whatever follows the name is
# a value and nothing of the name is at stake.
_LONG_NAME = re.compile(r"^--[A-Za-z][A-Za-z0-9_\-]*")


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


def _split_switch(piece):
    # type: (str) -> tuple
    """``(switch, rest)`` for a piece that opens with a switch, else ``("", "")``.

    A short option carries its value ATTACHED, with no delimiter for the
    ``=``/``:``/``,`` tests below to find — ``-o/vols/secret/x`` is how an absolute
    path used to reach a published artifact verbatim. Splitting the switch off lets
    the same shape tests run on the value half; the switch half is copied through
    untouched, so no ordinary switch can be rewritten by this.

    A short option is EXACTLY ONE LETTER. Taking more would mistake the head of a
    relative value for part of the switch — ``-obuild/out`` is ``-o`` with the
    value ``build/out``, and hashing ``/out`` out of the middle of it would destroy
    a replayable value to protect a path that was never there. It must be a letter,
    so ``-1/2`` is left alone as the number it looks like. The cost is a BUNDLED
    short option that also carries a path (``-xzf/vols/x``, which nothing here
    emits): "flags x and z then f=/vols/x" and "flag x with value zf/vols/x" are the
    same characters, and guessing between them is what this module forbids. Every
    other spelling — ``-f /vols/x``, ``-f=/vols/x``, ``-f/vols/x`` — is redacted."""
    if piece.startswith("--"):
        m = _LONG_NAME.match(piece)
        # A long option never attaches a value without `=`, so its whole name is
        # the switch. `--` and `---x` match no name; two characters is still the
        # part that cannot be a path.
        cut = m.end() if m else 2
    elif len(piece) > 1 and piece[0] == "-" and piece[1].isalpha():
        cut = 2
    elif piece[:2] in ("-/", "-~"):
        cut = 1                     # a bare `-` in front of a path
    else:
        return ("", "")
    return (piece[:cut], piece[cut:]) if piece[cut:] else ("", "")


def _redact_piece(piece):
    # type: (str) -> str
    """One whitespace-free fragment with any absolute host path replaced by its id.

    THE TEST IS ON THE VALUE, never on the flag that carried it. Matching flag
    names was unsound twice over: argparse accepts unambiguous PREFIXES, so a set
    keyed on ``--src`` never fired for ``--sr /tmp/x``; and switches nobody thought
    to list take paths routinely (``--only /abs/spec.docx``,
    ``--tokenizer char:/home/me/models/tok``). Anything shaped like an absolute
    path is redacted whatever switch carried it.

    A leading ``//`` used to be waved through as "a scheme-relative URL". It is
    not: ``//vols/private/spec.docx`` is a working POSIX path that resolves exactly
    like ``/vols/private/spec.docx``, and ``file:///vols/...`` went through the same
    door. Only a piece that really opens with ``<scheme>://`` is a URL, and even
    then an empty authority is a host path, not a network location.

    A path is looked for ANYWHERE in the piece, not only at position 0: it can be
    glued straight onto a short option (``-o/vols/x``), or sit third in a
    comma-joined list (``-Wl,-rpath,/opt/lib``), or be the LEFT half of a pair
    (``char:/vols/x--out=1``). Each test still fires on the VALUE's shape — the
    switch half is copied through untouched — so no ordinary switch can be
    rewritten by one of them."""
    m = _URL.match(piece)
    if m:
        rest = piece[m.end():]
        if rest.startswith("/"):
            # An EMPTY AUTHORITY: `file:///vols/private/x`, and `char:///vols/x`
            # just the same. What decides this is the empty host, not the scheme
            # name — with nothing between the `//` and the `/`, what follows is a
            # local path and there is no network location to disclose.
            return "%s://<path:%s>" % (m.group(1), path_id(rest))
        # `--source-base-url https://wiki/docs` decided every permalink in the
        # bundle and discloses no host path, so it survives verbatim.
        return piece
    if piece.startswith("/") and len(piece) > 1:
        return "<path:%s>" % path_id(piece)
    if piece.startswith("~") and "/" in piece:
        # `~/models/tok`, `~someone/models/tok`. Not absolute by shape, still a home
        # directory — and the second form names the user, which this layer must
        # never record.
        return "<path:%s>" % path_id(piece)
    switch, rest = _split_switch(piece)
    # `-o/vols/private/x`, `-I/usr/include`. An absolute path is a path wherever it
    # sits in the argument, not only at position 0 or after a delimiter.
    if switch:
        return "%s%s" % (switch, _redact_piece(rest))
    # The delimiters a path can ride behind inside one compound value:
    #   `=`  `--worker-cmd "python3 w.py --out=/vols/private/x"` (the top-level
    #        form is split by `redact_argv`; this is the buried one)
    #   `:`  `--tokenizer char:/home/me/models/tok`
    #   `,`  `--exclude a,/vols/private/b`, and the linker's `-Wl,-rpath,/opt/lib`
    # BOTH halves go back through this function. The tail because the path is as
    # often the third element as the second, and because it can be `~/models/tok`
    # or carry its own switch; the head because it can hold a path too
    # (`char:/vols/x--out=1` used to publish its first path whole while carefully
    # redacting the second). Each half is strictly shorter, so this terminates, and
    # a half holding no path comes back byte-identical — `--out`, `char`, `-rpath`
    # and `a/b` are all returned unchanged by the tests above. A URL returned
    # already, so none of these can swallow one. `=` first, then `:`, then `,`, so
    # a `k=a,b` pair is still read as one pair.
    for delim in ("=", ":", ","):
        head, sep, tail = piece.partition(delim)
        if sep and "/" in piece:
            return "%s%s%s" % (_redact_piece(head), delim, _redact_piece(tail))
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
    if value.startswith("/") or value.startswith("~"):
        return _redact_piece(value)
    parts = re.split(r"(\s+)", value)
    return "".join(p if i % 2 else _redact_piece(p) for i, p in enumerate(parts))


def _placeholder_for(flag, paths):
    # type: (str, dict) -> str
    """The placeholder a REPLAYABLE root flag asks for, honouring argparse PREFIXES.

    ``--sr`` IS ``--src`` to argparse. A prefix that is ambiguous within ``paths``
    gets no placeholder and falls through to the value test above, which is the safe
    direction: it redacts rather than reconstructs.

    ``paths`` is the ONLY list consulted, and it holds roots a replay must be handed
    back (``--src``, ``--out``, ``--bundles``). There used to be a second, larger
    list of "path-ish" flags whose values were blanked to ``<path>``; it bought no
    safety — ``_redact_value`` already redacts every absolute path whatever switch
    carried it — and it broke the docstring above twice over. Prefix-expanded, it
    rewrote ordinary NON-path switches: ``--ex 4000`` (argparse's own abbreviation of
    ``--excerpt-chars``) recorded ``--ex <path>``, destroying the value in ``argv``
    and making ``replay_run`` refuse a run it could have reconstructed."""
    if flag in paths:
        return paths[flag]
    if not flag.startswith("--") or len(flag) < 4:
        return ""
    hits = sorted(set(v for name, v in paths.items() if name.startswith(flag)))
    return hits[0] if len(hits) == 1 else ""


def redact_argv(argv, paths=None):
    # type: (list, dict) -> list
    """``argv`` with path VALUES replaced by placeholders, keeping every switch.

    A replay needs to know that ``--tokenizer tiktoken:cl100k_base`` was passed far
    more than it needs the operator's home directory, and the one thing that must
    never land in a published artifact is an absolute host path. ``paths`` maps a
    flag to the placeholder a REPLAY can fill back in, e.g. ``{"--src": "<src>"}``;
    every other value is scrubbed BY SHAPE, so a path can never reach an artifact
    through a switch nobody listed — and a switch nobody listed can never lose its
    value to a placeholder it did not need."""
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
            # The left half of `--flag=value` is a SWITCH and is copied through, so
            # it never loses its name to a placeholder. But a left half holding a
            # `/` is not a switch at all — `/vols/private/x=1` is a positional
            # argument that happens to contain an `=`, and its path used to be
            # published whole while the right half was carefully redacted. It goes
            # through the same shape test as everything else. The right half is
            # redacted as ONE value, not word by word, so a path with a space in it
            # (`--out=/vols/spec drafts/x.docx`) still hashes as one path.
            left = _redact_value(flag) if "/" in flag else flag
            out.append("%s=%s" % (left, placeholder or _redact_value(value)))
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
    path looks like.

    Every container is walked, not only the flat ones: a caller handing this a
    mapping (``decision(..., evidence={"paths": {...}})``) is exactly the caller most
    likely to be handing it a path, and returning the dict untouched would have made
    the public helper the one place a path could get out."""
    if isinstance(value, (list, tuple)):
        return [safe_value(v) for v in value]
    if isinstance(value, dict):
        return OrderedDict((key, safe_value(val)) for key, val in value.items())
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
