"""
title: The output-quality rubric, as executable checks
layer: backend
public_api: no
summary: Pure predicates that grade a built corpus against docs/quality-plan.md, so "grade A" is a command's verdict rather than an opinion.
"""
# docs/quality-plan.md defines four output dimensions and says a grade is "A only
# when EVERY condition is demonstrable by a command that returns a verdict". This
# module IS that command's brain.
#
# It is deliberately pure — dicts and strings in, verdicts out. Loading bundles
# from disk and running pytest suites belongs to scripts/grade_output.py, per this
# package's rule that file I/O lives in the runners. That split is what lets the
# whole rubric run inside a unit test with hand-built dicts.
#
# Two kinds of row:
#   * ARTIFACT rows are answered here, by inspecting what the pipeline wrote.
#   * SUITE rows are answered by a named pytest target the runner executes; this
#     module only records the verdict it was handed. A suite that could not be run
#     is "skip", and a skip is never an A: unknown is not the same as passing.
import re

from collections import namedtuple

from backend.provenance import DECISION_CODES

# A single graded condition. ``check`` takes the corpus view and returns
# (status, evidence); ``suite`` names a pytest target the runner must execute.
Row = namedtuple("Row", "dim rid condition kind target check")
Result = namedtuple("Result", "dim rid condition status evidence")

PASS = "pass"
FAIL = "fail"
SKIP = "skip"

# Identifiers an operator would copy-paste out of a converted runbook. Every one
# is a character sequence markdown would otherwise mangle; every one must survive
# byte-for-byte. Grading requires the adversarial fixture to be in the corpus, so
# that "none found" can never be mistaken for "nothing to check".
ADVERSARIAL_PROBES = (
    "DB_MAX_CONN_LIMIT",
    "--dry_run=true",
    "[payments]",
    "snake_case_helper",
    "kubectl get pods -n payments",
)
ADVERSARIAL_MARKER = "adversarial"

# docs/quality-plan.md P4.6: a shouted body sentence is not a section.
ALLCAPS_CALLOUT = "DO NOT REBOOT THE PRIMARY NODE"

# The per-record origin stamp written by backend.kb. Spelled out rather than
# imported: `validate` sits above `ingest` and must not grow an edge to `kb` for
# two string constants — and a rubric that imported the code it grades would be
# checking that a module agrees with itself.
_EVIDENCE_KEY = "source"
_EXTRACTED = "extracted"


# --------------------------------------------------------------- small helpers

def _office(view):
    # type: (dict) -> list
    return [b for b in view.get("bundles", []) if b.get("report", {}).get("lane") == "office"]


def _all(view):
    # type: (dict) -> list
    return list(view.get("bundles", []))


def _iter_nodes(outline):
    # type: (list) -> list
    """Every node of a structure.json outline, depth-first, parents before children."""
    out = []
    stack = list(reversed(outline or []))
    while stack:
        node = stack.pop()
        out.append(node)
        for kid in reversed(node.get("children") or []):
            stack.append(kid)
    return out


def _tree_depth(outline):
    # type: (list) -> int
    """Real depth of the node tree — not max(level), which is a different number."""
    best = 0
    for node in outline or []:
        best = max(best, 1 + _tree_depth(node.get("children") or []))
    return best


def gfm_anchor(title):
    # type: (str) -> str
    """GitHub's heading slug: lowercase, drop punctuation, spaces to hyphens.

    Published here because two places in the codebase need to agree on it and the
    rubric is what grades that agreement (quality-plan C3)."""
    text = (title or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.U)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _adversarial(view):
    # type: (dict) -> dict
    for bundle in view.get("bundles", []):
        relpath = bundle.get("report", {}).get("source_relpath", "")
        if ADVERSARIAL_MARKER in relpath:
            return bundle
    return {}


def _missing_fixture():
    # type: () -> tuple
    return (FAIL, "the corpus under grade contains no *%s* fixture, so this "
                  "condition cannot be demonstrated" % ADVERSARIAL_MARKER)


def _nothing_to_grade(what):
    # type: (str) -> tuple
    """The verdict when a check had nothing to look at.

    A loop over an empty corpus falls through to its `return PASS`, and a grader
    that reports a cheerful A because it was handed no documents is the same
    vacuous pass the report's `n_source_tokens` exists to prevent. Every artifact
    check states its denominator."""
    return (FAIL, "nothing to grade: %s" % what)


# ------------------------------------------------- A. document.md — the body

def _a1_structure_gate(view):
    # type: (dict) -> tuple
    """Every office bundle is either structurally proven or openly unmeasured.

    ``unmeasured`` is not a failure — pptx and xlsx have no second implementation
    yet — but it is not a pass either, so the count is always reported. A gap you
    can see is a gap somebody can close; a gap folded into a green tick is not."""
    bundles = _office(view)
    if not bundles:
        return (FAIL, "no office bundles in the corpus under grade")
    bad, unmeasured = [], 0
    for b in bundles:
        gate = (b.get("report", {}).get("structure_fidelity") or {}).get("gate")
        if gate == "unmeasured":
            unmeasured += 1
        elif gate != PASS:
            bad.append("%s=%s" % (b.get("doc_id", "?")[:8], gate or "<absent>"))
    if bad:
        return (FAIL, "structure_fidelity not passing on %d/%d office bundles: %s"
                % (len(bad), len(bundles), ", ".join(bad[:6])))
    if unmeasured == len(bundles):
        return (FAIL, "all %d office bundles report structure_fidelity "
                      "'unmeasured' — nothing was actually graded" % unmeasured)
    return (PASS, "structure_fidelity.gate == pass on %d of %d office bundles "
                  "(%d unmeasured: a format with no ground truth yet)"
            % (len(bundles) - unmeasured, len(bundles), unmeasured))


def _a3_verbatim(view):
    # type: (dict) -> tuple
    bundle = _adversarial(view)
    if not bundle:
        return _missing_fixture()
    body = bundle.get("markdown", "")
    missing = [p for p in ADVERSARIAL_PROBES if p not in body]
    if missing:
        return (FAIL, "mangled by escaping, not present verbatim: %s"
                % ", ".join(repr(m) for m in missing))
    return (PASS, "all %d identifiers survive byte-for-byte" % len(ADVERSARIAL_PROBES))


# ------------------------------------------- B. report.json — provenance

_RUN_REQUIRED = ("entrypoint", "run_id", "argv", "code", "host", "config_ref")


def _b1_run_block(view):
    # type: (dict) -> tuple
    bundles = _all(view)
    if not bundles:
        return (FAIL, "no bundles in the corpus under grade")
    for b in bundles:
        run = b.get("report", {}).get("run") or {}
        miss = [k for k in _RUN_REQUIRED if not run.get(k)]
        if miss:
            return (FAIL, "%s run{} missing %s" % (b.get("doc_id", "?")[:8], miss))
        code = run.get("code") or {}
        if not code.get("version") or "dirty" not in code:
            return (FAIL, "%s run.code lacks version/dirty" % b.get("doc_id", "?")[:8])
        if not (run.get("host") or {}).get("python"):
            return (FAIL, "%s run.host.python absent" % b.get("doc_id", "?")[:8])
    runs = view.get("runs", [])
    if not runs:
        return (FAIL, "runs.jsonl is empty, so the resolved config was never recorded")
    for row in runs:
        cfg = row.get("config") or {}
        if not cfg:
            return (FAIL, "run %s recorded no resolved config" % row.get("run_id"))
        untraced = [k for k, v in cfg.items()
                    if k != "_env_present" and not (isinstance(v, dict) and v.get("from"))]
        if untraced:
            return (FAIL, "config values with no recorded source: %s" % untraced[:5])
    return (PASS, "%d bundles carry a complete run{}; %d run row(s) trace every "
                  "config value to its source" % (len(bundles), len(runs)))


_CONVERTER = re.compile(r"^doc2md-[a-z]+/\d+\.\d+\.\d+\+[0-9a-f]{7,}$")


def _b2_converter(view):
    # type: (dict) -> tuple
    if not _all(view):
        return _nothing_to_grade("no bundles in the corpus under grade")
    for b in _all(view):
        rep = b.get("report", {})
        conv = rep.get("converter", "")
        if not _CONVERTER.match(conv):
            return (FAIL, "converter %r is not <name>/<version>+<commit>" % conv)
        commit = ((rep.get("run") or {}).get("code") or {}).get("commit", "")
        if commit and not commit.startswith(conv.rsplit("+", 1)[1]):
            return (FAIL, "converter commit %s does not match run.code.commit %s"
                    % (conv.rsplit("+", 1)[1], commit[:12]))
    return (PASS, "%d converter string(s) derived from package version + git "
                  "commit" % len(_all(view)))


def _b3_decisions(view):
    # type: (dict) -> tuple
    if not _all(view):
        return _nothing_to_grade("no bundles in the corpus under grade")
    seen = set()
    for b in _all(view):
        decisions = b.get("report", {}).get("decisions")
        if not decisions:
            return (FAIL, "%s recorded no decisions[]" % b.get("doc_id", "?")[:8])
        for d in decisions:
            if d.get("code") not in DECISION_CODES:
                return (FAIL, "decision code %r is not in the closed vocabulary"
                        % d.get("code"))
            if not d.get("reason"):
                return (FAIL, "decision %r carries no reason" % d.get("code"))
            seen.add(d["code"])
    return (PASS, "every decision is a structured record; codes seen: %s"
            % ", ".join(sorted(seen)))


def _b4_runlog(view):
    # type: (dict) -> tuple
    rows, runs = view.get("manifest", []), view.get("runs", [])
    if not rows:
        return (FAIL, "manifest.jsonl is empty")
    run_ids = set(r.get("run_id") for r in runs)
    orphan = set(r.get("run_id") for r in rows) - run_ids
    if orphan:
        return (FAIL, "manifest rows reference unknown runs: %s" % sorted(orphan)[:4])
    if not all(r.get("action") for r in rows):
        return (FAIL, "manifest rows without an action: not a run log")
    return (PASS, "%d manifest rows join to %d run(s) by run_id; actions: %s"
            % (len(rows), len(runs), sorted(set(r.get("action") for r in rows))))


def _b6_no_vacuous_pass(view):
    # type: (dict) -> tuple
    if not _all(view):
        return _nothing_to_grade("no bundles in the corpus under grade")
    for b in _all(view):
        loss = b.get("report", {}).get("losslessness") or {}
        if loss and "n_source_tokens" not in loss:
            return (FAIL, "%s claims a recall with no n_source_tokens"
                    % b.get("doc_id", "?")[:8])
        if loss.get("n_source_tokens") == 0 and loss.get("gate") == PASS:
            codes = [w.get("code") for w in b.get("report", {}).get("warnings") or []]
            if "empty_source" not in codes:
                return (FAIL, "%s passed the gate over zero tokens with no "
                              "empty_source code" % b.get("doc_id", "?")[:8])
    return (PASS, "%d recall(s) reported alongside the token count they are "
                  "over" % len(_all(view)))


# ------------------------------------------------- C. structure.json — the tree

# A leading section number, and nothing but: "1.1.1", "2.4 Scope". The negative
# lookahead stops "1.8 V" from being read as the number "1.8" of some deeper scheme.
_SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)(?![\d.])")
_C1_FLATTENED_MIN = 3


def _flattened_children(outline):
    # type: (list) -> list
    """Top-level titles whose number says they are a SUBSECTION of another top-level.

    "A numbered outline flattened into siblings" has a precise meaning and the row
    is graded on it: some sibling's number literally extends another sibling's
    number, so the document states a parent-child relation the tree does not.
    ``1.1`` beside ``1`` is that; ``1.1`` beside ``1.2`` is not (those really are
    siblings), and ``1.8 V rail`` beside ``5 V rail`` is not either — those are
    measurements, and reading them as a hierarchy is the bug on the other side of
    this row, the one that made five power rails into a two-level tree.
    """
    numbers = []
    for node in outline or []:
        m = _SECTION_NUMBER.match((node.get("title") or "").strip())
        if m:
            numbers.append((m.group(1), node.get("title") or ""))
    stated = set(num for num, _t in numbers)
    return [title for num, title in numbers
            if "." in num and num.rsplit(".", 1)[0] in stated]


def _c1_hierarchy(view):
    # type: (dict) -> tuple
    """No bundle publishes a numbered outline flattened into siblings.

    This is a REGRESSION guard over whatever is in the graded corpus. It cannot
    prove the inference works, because only a flat extractor (docling) produces
    the input that needs it and the office lane never does — that proof is the
    C1s suite row, over synthetic flat input. Reported honestly: the evidence line
    says how many bundles were actually looked at."""
    if not _all(view):
        return _nothing_to_grade("no bundles in the corpus under grade")
    for b in _all(view):
        outline = b.get("structure", {}).get("outline") or []
        flattened = _flattened_children(outline)
        if len(outline) > 2 and len(flattened) >= _C1_FLATTENED_MIN:
            return (FAIL, "%s has %d top-level siblings whose section number makes "
                          "them a subsection of another top-level sibling (%s) — "
                          "the hierarchy was never inferred"
                    % (b.get("doc_id", "?")[:8], len(flattened),
                       ", ".join(repr(t[:12]) for t in flattened[:4])))
    return (PASS, "%d bundle(s) inspected; none publishes a numbered outline "
                  "flattened into siblings" % len(_all(view)))


# A renderer disambiguates repeated headings by suffixing an ordinal: two
# `## Overview` sections become `overview` and `overview-1`. That IS the correct
# anchor — it is what the fragment actually resolves to — so the check accepts it.
_DISAMBIGUATED = re.compile(r"^(?P<base>.*?)-(?P<n>\d+)$")
# A node that names a region with no heading of its own. It has no rendered anchor
# to agree with, so it is outside this row's question.
_HEADLESS = ("(preamble)",)
# What the producer publishes when a title slugs to nothing ("***"): a key to
# address the NODE by, not a fragment any renderer will emit. Spelled out here
# rather than imported, like every other fact this module grades.
_FALLBACK_ANCHOR = "section"
# An ATX heading, as CommonMark defines it: up to three leading spaces, one to six
# hashes, whitespace, text, an optional closing run of hashes.
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)


def _anchor_ok(anchor, title):
    # type: (str, str) -> bool
    want = gfm_anchor(title) or _FALLBACK_ANCHOR
    if anchor == want:
        return True
    m = _DISAMBIGUATED.match(anchor or "")
    return bool(m) and m.group("base") == want


def _rendered_anchors(markdown):
    # type: (str) -> set
    """Every fragment the ATX headings of ``markdown`` make addressable.

    The rubric's OWN reading of the body — the point of the row is that the anchors
    structure.json advertises are the ones a reader can actually follow, and a
    grader that asked the producer would only be asking a module whether it agrees
    with itself. Repeats take the renderer's ordinal suffix (``overview``, then
    ``overview-1``); a heading that slugs to nothing makes nothing addressable.
    """
    out = set()
    seen = {}
    for m in _ATX_HEADING.finditer(markdown or ""):
        base = gfm_anchor(m.group(2))
        if not base:
            continue
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else "%s-%d" % (base, n))
    return out


def _c3_anchors(view):
    # type: (dict) -> tuple
    """Both halves of C3, and both of them able to fail.

    The cross-check used to read ``knowledge["body_anchors"]``, a key nothing has
    ever written to ``knowledge.json`` — it is computed transiently inside
    ``enrich_metadata.py`` and ``kb_lint.py`` and thrown away. ``if known:`` was
    therefore false for every bundle ever graded, and half this row had never run.
    It now derives the addressable set from the bundle's own ``document.md``, which
    the view always carries, and reports the denominator for both halves so a
    silent nothing-to-do can never read as a pass again.
    """
    graded = crossed = 0
    for b in _all(view):
        nodes = [n for n in _iter_nodes(b.get("structure", {}).get("outline"))
                 if (n.get("title") or "") not in _HEADLESS]
        for node in nodes:
            graded += 1
            anchor, title = node.get("anchor", ""), node.get("title", "")
            if not _anchor_ok(anchor, title):
                return (FAIL, "%s node %s anchor %r but GFM renders %r "
                              "(an ordinal suffix for a repeated title is fine)"
                        % (b.get("doc_id", "?")[:8], node.get("id"), anchor,
                           gfm_anchor(title)))
        addressable = _rendered_anchors(b.get("markdown", ""))
        if not addressable:
            continue
        crossed += 1
        # A node whose title slugs empty carries the `section` placeholder, which
        # names no fragment in any body; it is outside the question, like the
        # preamble. Everything else must be a fragment the body really offers.
        missing = set(n.get("anchor") for n in nodes
                      if gfm_anchor(n.get("title") or "")) - addressable
        if missing:
            return (FAIL, "%s publishes anchor(s) no heading in its own document.md "
                          "makes addressable: %s"
                    % (b.get("doc_id", "?")[:8], sorted(missing)[:4]))
    if not graded:
        return _nothing_to_grade("no outline nodes in the corpus under grade")
    if not crossed:
        return _nothing_to_grade("no bundle carries a markdown body, so the "
                                 "published anchors were never cross-checked "
                                 "against the headings they claim to address")
    return (PASS, "all %d anchors are the GFM slug of their title, and every one of "
                  "them resolves in the body of its own bundle (%d cross-checked)"
            % (graded, crossed))


def _c4_summary_numbers(view):
    # type: (dict) -> tuple
    graded = 0
    for b in _all(view):
        rep, st = b.get("report", {}), b.get("structure", {})
        outline = st.get("outline") or []
        if not outline:
            continue
        graded += 1
        summary = rep.get("structure") or {}
        want = _tree_depth(outline)
        if summary.get("max_depth") != want:
            return (FAIL, "%s max_depth %r but the tree is %d deep"
                    % (b.get("doc_id", "?")[:8], summary.get("max_depth"), want))
        if "largest_leaf_tokens" not in summary:
            return (FAIL, "%s publishes no largest_leaf_tokens, so "
                          "largest_section_tokens can still mean 'the whole document'"
                    % b.get("doc_id", "?")[:8])
    if not graded:
        return _nothing_to_grade("no bundle in the corpus has an outline")
    return (PASS, "%d outline(s): max_depth is tree depth and a largest-leaf "
                  "count is published" % graded)


def _c5_table_nodes(view):
    # type: (dict) -> tuple
    saw = 0
    for b in _all(view):
        for node in _iter_nodes(b.get("structure", {}).get("outline")):
            tables = node.get("tables")
            if isinstance(tables, int):
                if tables:
                    return (FAIL, "%s node %s reports tables as the integer %d — a "
                                  "table cannot be cited or linked"
                            % (b.get("doc_id", "?")[:8], node.get("id"), tables))
                continue
            for tbl in tables or []:
                miss = [k for k in ("table_id", "line", "rows", "cols") if k not in tbl]
                if miss:
                    return (FAIL, "table node missing %s" % miss)
                saw += 1
    if not saw:
        return (FAIL, "no table nodes anywhere in the corpus under grade")
    return (PASS, "%d tables are addressable nodes" % saw)


def _c6_allcaps(view):
    # type: (dict) -> tuple
    bundle = _adversarial(view)
    if not bundle:
        return _missing_fixture()
    for node in _iter_nodes(bundle.get("structure", {}).get("outline")):
        if ALLCAPS_CALLOUT.lower() in (node.get("title") or "").lower():
            return (FAIL, "the shouted body sentence %r became outline node %s"
                    % (ALLCAPS_CALLOUT, node.get("id")))
    if ALLCAPS_CALLOUT not in bundle.get("markdown", ""):
        return (FAIL, "the ALL-CAPS callout is missing from the body entirely")
    return (PASS, "the ALL-CAPS callout stays body text")


# ------------------------------------------- D. metadata — the KB substrate

# `title` and `abstract` describe the document and every document has one, so an
# empty value means nobody looked. `links` is different: a document may honestly
# have no outbound links, so what is graded is whether the extractor CONSIDERED
# it — a provenance record for the field — not whether it found anything. Grading
# a truthful empty as a failure would push the pipeline toward inventing edges.
_D1_DESCRIPTORS = ("title", "abstract")
_D1_HARVESTED = "links"


def _d1_no_model_page(view):
    # type: (dict) -> tuple
    graded = 0
    for b in _all(view):
        meta = (b.get("front") or {}).get("meta") or {}
        if not meta:
            continue
        graded += 1
        short = b.get("doc_id", "?")[:8]
        pending = set(meta.get("_pending") or [])
        bad = [k for k in _D1_DESCRIPTORS if k in pending or not meta.get(k)]
        if bad:
            return (FAIL, "%s has no deterministic %s after a no-model run"
                    % (short, "/".join(bad)))
        # `links` is a `groups` field, so the schema seam puts it in
        # knowledge.json rather than front matter. Look in both, because where it
        # lives is the schema's business and this row is about whether the
        # pipeline used the outbound URLs it already harvested at recall 1.0.
        know = b.get("knowledge") or {}
        prov = dict(meta.get("_provenance") or {})
        prov.update(know.get("_provenance") or {})
        if _D1_HARVESTED not in prov and _D1_HARVESTED not in know \
                and not meta.get(_D1_HARVESTED):
            return (FAIL, "%s never considered %s: structure.json already holds "
                          "the document's verified outbound URLs, harvested at "
                          "recall 1.0, and the extractor did not read them"
                    % (short, _D1_HARVESTED))
    if not graded:
        return (FAIL, "no enriched bundles in the corpus under grade")
    return (PASS, "%d no-model page(s) carry a deterministic title and abstract, "
                  "and each considered its harvested links" % graded)


def _d2_permalink(view):
    # type: (dict) -> tuple
    graded = 0
    for b in _all(view):
        meta = (b.get("front") or {}).get("meta") or {}
        if not meta:
            continue
        graded += 1
        if not (meta.get("source") or {}).get("url"):
            return (FAIL, "%s meta.source has no url, so a wiki page cannot link home"
                    % b.get("doc_id", "?")[:8])
    if not graded:
        return (FAIL, "no enriched bundles in the corpus under grade")
    return (PASS, "%d pages can link back to their source" % graded)


def _d3_ids_unique(view):
    # type: (dict) -> tuple
    seen = {}
    for b in _all(view):
        meta = (b.get("front") or {}).get("meta") or {}
        ident = meta.get("id")
        if not ident:
            continue
        if ident in seen:
            return (FAIL, "id %r is shared by %s and %s"
                    % (ident, seen[ident][:8], b.get("doc_id", "?")[:8]))
        seen[ident] = b.get("doc_id", "?")
    if not seen:
        return (FAIL, "no enriched bundles in the corpus under grade")
    return (PASS, "%d ids, all distinct" % len(seen))


def _d4_one_identity(view):
    # type: (dict) -> tuple
    graded = 0
    for b in _all(view):
        meta = (b.get("front") or {}).get("meta") or {}
        if not meta:
            continue
        graded += 1
        uid, ident = meta.get("uid"), meta.get("id")
        if uid and ident and uid != ident:
            return (FAIL, "%s carries two identity namespaces: uid=%r id=%r"
                    % (b.get("doc_id", "?")[:8], uid, ident))
    if not graded:
        return _nothing_to_grade("no enriched bundles in the corpus under grade")
    return (PASS, "%d document(s), one canonical identity each" % graded)


def _iter_records(know):
    # type: (dict) -> list
    """Every knowledge record, whether its field is a list or a group mapping."""
    out = []
    for key in ("relations", "entities", "requirements", "decisions", "risks",
                "open_questions", "links"):
        value = know.get(key)
        if isinstance(value, dict):
            for group, records in value.items():
                for rec in records or []:
                    out.append(("%s.%s" % (key, group), rec))
        else:
            for rec in value or []:
                out.append((key, rec))
    return out


def _d5_records_cite(view):
    # type: (dict) -> tuple
    """Every knowledge record present cites the section that asserts it.

    This is a REGRESSION guard over whatever the graded corpus produced. It cannot
    prove the rule, because the grader deliberately runs with NO model — that is
    the honest floor — and a no-model run proposes no records at all. The proof
    that a ref-less record is *refused* is the D5s suite row, which exercises the
    acceptance layer directly with a stub answer.

    "An enriched document produced no records" is a real observation and passes.
    "There were no documents" is not an observation at all, and fails — the
    distinction is the whole difference between a measurement and a rubber stamp."""
    enriched = [b for b in _all(view) if ((b.get("front") or {}).get("meta"))]
    if not enriched:
        return _nothing_to_grade("no enriched bundles in the corpus under grade")
    graded = 0
    for b in enriched:
        for key, rec in _iter_records(b.get("knowledge") or {}):
            graded += 1
            if not isinstance(rec, dict):
                return (FAIL, "a %s record is not a record" % key)
            if rec.get("ref"):
                continue
            # A HARVESTED record may have no anchor to point at and still be
            # perfectly answerable. A URL in the lede — the prose above the first
            # heading — sits in a region a renderer emits no fragment for, so a
            # `ref` there would be a DEAD link. What it does have is the exact
            # body line it came from, and that answers "which part of the document
            # says this?" better than a fragment that resolves nowhere.
            # A MODEL-PROPOSED record gets no such latitude: the whole point of
            # requiring a ref is that a claim nobody can locate cannot be checked.
            if (rec.get(_EVIDENCE_KEY) == _EXTRACTED
                    and isinstance(rec.get("line"), int)):
                continue
            return (FAIL, "a %s record carries neither a ref nor the line it was "
                          "extracted from, so \"which section says this?\" is "
                          "unanswerable" % key)
    if not graded:
        return (PASS, "%d enriched document(s), no knowledge records to regress — "
                      "the graded run uses no model by design; that a ref-less "
                      "record is REFUSED is proven by the D5s suite row"
                % len(enriched))
    return (PASS, "%d knowledge record(s) across %d document(s) cite a section"
            % (graded, len(enriched)))


# ---------------------------------------------------- E. documentation

def _e1_guide(view):
    # type: (dict) -> tuple
    guide = (view.get("docs") or {}).get("docs/guide.md", "")
    if not guide:
        return (FAIL, "docs/guide.md is absent")
    if len(guide) < 2000 or "```" not in guide:
        return (FAIL, "docs/guide.md carries no worked example")
    return (PASS, "docs/guide.md is %d bytes and shows a worked example" % len(guide))


# ------------------------------------------------------------------- the rubric

def _artifact(dim, rid, condition, check):
    # type: (str, str, str, object) -> Row
    return Row(dim, rid, condition, "artifact", "", check)


def _suite(dim, rid, condition, target):
    # type: (str, str, str, str) -> Row
    return Row(dim, rid, condition, "suite", target, None)


ROWS = [
    # A. document.md
    _artifact("A", "A1", "structure_fidelity is a second hard gate on the office lane",
              _a1_structure_gate),
    _suite("A", "A2", "body text round-trips as a sequence, not only a multiset",
           "tests/unit/backend/test_validate_roundtrip.py"),
    _artifact("A", "A3", "identifiers survive verbatim in the stored bytes",
              _a3_verbatim),
    _suite("A", "A4", "every deliberate drop emits a named warning carrying a count",
           "tests/unit/backend/test_warning_vocabulary.py"),
    _suite("A", "A5", "adversarial fixtures are pinned in the eval corpus",
           "evals/run_eval.py"),
    # B. report.json
    _artifact("B", "B1", "run{} records argv, resolved config with sources, code, host",
              _b1_run_block),
    _artifact("B", "B2", "converter is derived from package metadata + commit",
              _b2_converter),
    _artifact("B", "B3", "every branch taken is a structured decisions[] record",
              _b3_decisions),
    _artifact("B", "B4", "manifest.jsonl is a run log joined to runs.jsonl",
              _b4_runlog),
    _suite("B", "B5", "replay_run reproduces markdown_sha256 or names divergences",
           "tests/integration/test_replay_run.py"),
    _artifact("B", "B6", "no vacuous pass: recall is always over a stated token count",
              _b6_no_vacuous_pass),
    _suite("B", "B7", "exit codes distinguish error from pending work",
           "tests/integration/test_enrich_metadata.py"),
    # C. structure.json
    _artifact("C", "C1", "heading hierarchy is inferred when the extractor is flat",
              _c1_hierarchy),
    _suite("C", "C1s", "heading levels are inferred from numbering on flat input",
           "tests/unit/backend/test_outline_heuristics.py"),
    _suite("C", "C2", "node ids are content-derived and stable across an insertion",
           "tests/unit/backend/test_outline_stability.py"),
    _artifact("C", "C3", "one anchor scheme, GFM-correct, agreed corpus-wide",
              _c3_anchors),
    _artifact("C", "C4", "max_depth is tree depth; a largest-leaf count is published",
              _c4_summary_numbers),
    _artifact("C", "C5", "tables are first-class nodes, addressable like images",
              _c5_table_nodes),
    _artifact("C", "C6", "a body sentence cannot become a heading", _c6_allcaps),
    # D. metadata
    _artifact("D", "D1", "a no-model run yields a titled, summarised, linked page",
              _d1_no_model_page),
    _artifact("D", "D2", "every page can link back to its source", _d2_permalink),
    _artifact("D", "D3", "id is unique corpus-wide", _d3_ids_unique),
    _suite("D", "D3s", "id uniqueness holds by construction at corpus scale",
           "tests/unit/backend/test_id_uniqueness.py"),
    _artifact("D", "D4", "one canonical identity", _d4_one_identity),
    _artifact("D", "D5", "every knowledge record present cites its section",
              _d5_records_cite),
    _suite("D", "D5s", "a record that cannot say which section asserts it is refused",
           "tests/unit/backend/test_kb_enrich.py"),
    _suite("D", "D6", "no field ships that nothing reads and nothing fills",
           "tests/unit/backend/test_field_inventory.py"),
    _suite("D", "D7", "corpus gates stay sound at scale; truncation is disclosed",
           "tests/integration/test_kb_lint_corpus.py"),
    # E. documentation
    _artifact("E", "E1", "a product guide with a real worked example", _e1_guide),
    _suite("E", "E2", "every switch and env var is documented and cannot drift",
           "tests/integration/test_docs_parity.py"),
    # Two halves, two targets. "Published and cannot go stale" is proven by the
    # drift test that regenerates vocabulary.md and byte-compares; "the vocabulary
    # itself is sound" is proven by the shipped-vocabulary suite. Naming one target
    # for both meant half the condition was never checked by the row asserting it.
    _suite("E", "E3", "the published vocabulary is generated and cannot go stale",
           "tests/integration/test_docs_parity.py"),
    _suite("E", "E3s", "the shipped vocabulary is internally valid and parses "
                       "under the restricted reader",
           "tests/integration/test_shipped_vocabulary.py"),
    _suite("E", "E4", "every artifact key is documented and cannot drift",
           "tests/integration/test_docs_parity.py"),
]

DIMENSIONS = (
    ("A", "document.md — the markdown body"),
    ("B", "report.json — reporting and provenance"),
    ("C", "structure.json — the tree"),
    ("D", "metadata / knowledge — the KB substrate"),
    ("E", "documentation"),
)


def grade(view):
    # type: (dict) -> list
    """Every rubric row, evaluated against ``view``.

    ``view`` carries ``bundles`` (each with report/structure/markdown/front/
    knowledge), ``manifest``, ``runs``, ``docs`` and ``suites`` — the last being
    {target: bool|None} filled in by the runner, where None means "not run"."""
    results = []
    suites = view.get("suites") or {}
    for row in ROWS:
        if row.kind == "suite":
            verdict = suites.get(row.target)
            if verdict is None:
                results.append(Result(row.dim, row.rid, row.condition, SKIP,
                                      "%s was not run" % row.target))
            else:
                results.append(Result(
                    row.dim, row.rid, row.condition, PASS if verdict else FAIL,
                    "%s %s" % (row.target, "passed" if verdict else "FAILED")))
            continue
        try:
            status, evidence = row.check(view)
        except Exception as exc:                                    # noqa: BLE001
            status, evidence = FAIL, "check raised %s: %s" % (type(exc).__name__, exc)
        results.append(Result(row.dim, row.rid, row.condition, status, evidence))
    return results


def letter(results):
    # type: (list) -> str
    """The grade for one dimension's results.

    A is reserved for a clean sweep, because the plan says so: a skipped row is an
    unproven row, and an unproven row is not an A. Everything below A is a summary
    of how far off it is — the row table is the real answer."""
    if not results:
        return "n/a"
    if all(r.status == PASS for r in results):
        return "A"
    ratio = sum(1 for r in results if r.status == PASS) / float(len(results))
    for bound, mark in ((0.9, "B+"), (0.8, "B"), (0.7, "B-"), (0.6, "C+"),
                        (0.5, "C"), (0.4, "C-"), (0.25, "D")):
        if ratio >= bound:
            return mark
    return "F"


def summarize(results):
    # type: (list) -> dict
    """Per-dimension letters plus the overall verdict."""
    grades = {}
    for dim, _label in DIMENSIONS:
        grades[dim] = letter([r for r in results if r.dim == dim])
    return {
        "grades": grades,
        "passed": sum(1 for r in results if r.status == PASS),
        "failed": sum(1 for r in results if r.status == FAIL),
        "skipped": sum(1 for r in results if r.status == SKIP),
        "total": len(results),
        "overall": "A" if all(g == "A" for g in grades.values()) else
                   letter(results),
    }
