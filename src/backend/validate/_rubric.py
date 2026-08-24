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
#   * SUITE rows are answered by the SPECIFIC tests named in ``selector``, which
#     the runner executes; this module only records the verdict it was handed. A
#     suite that could not be run is "skip", and a skip is never an A: unknown is
#     not the same as passing.
#
# A suite row names tests, never just a file. Grading a row by "did this pytest
# FILE exit 0?" cannot tell "the condition I assert is checked" apart from "that
# file is green for unrelated reasons": the named demonstration could be deleted
# and the row would keep reporting pass, and an unrelated green test added to the
# file would earn the row on its behalf. Naming the tests makes the row fail when
# its own evidence disappears, which is the only reason the row is worth reading.
import re

from collections import namedtuple

from backend.provenance import DECISION_CODES

__all__ = ["DIMENSIONS", "ROWS", "gfm_anchor", "grade", "letter", "summarize",
           "Row", "Result", "PASS", "FAIL", "SKIP"]

# A single graded condition. ``check`` takes the corpus view and returns
# (status, evidence); ``target`` + ``selector`` name the pytest file and the
# tests inside it that demonstrate a suite row's condition.
Row = namedtuple("Row", "dim rid condition kind target selector check")
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
    check states its denominator.

    A corpus-wide denominator answers only the corpus-wide question. It cannot
    stand in for a PER-DOCUMENT one: a row whose only failure is "no bundle
    anywhere had any", and that quietly excuses the individual bundle that lost
    everything the row grades, is exactly the shape that once let all 32 rows pass
    over a document with every heading deleted. Dimension C therefore asks its
    question of each bundle and reports how many were asked."""
    return (FAIL, "nothing to grade: %s" % what)


# ---------------------------------------------------- markdown, read first-hand

# A fence opener/closer as CommonMark defines it: up to three leading spaces then
# three or more backticks or tildes.
_CODE_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,})")
# An unescaped cell divider, and a GFM delimiter-row cell (`---`, `:-:`, `--:`).
_TABLE_PIPE = re.compile(r"(?<!\\)\|")
_SEP_CELL = re.compile(r"^:?-+:?$")


def _prose_lines(markdown):
    # type: (str) -> list
    """``markdown``'s lines, with everything inside a fenced block blanked out.

    Spelled out here rather than imported from ``backend.sections`` on purpose,
    like every other fact this module grades: the rubric checks what that package
    produced, and a check that asked the producer where the code is would only be
    asking a module whether it agrees with itself. A shell transcript's ``# reset
    the board`` makes no fragment addressable and its pipe art is not a table. An
    unclosed fence runs to the end of the document, which is what CommonMark does
    with one, so the mask matches the renderer."""
    out = []
    in_fence = False
    for line in (markdown or "").splitlines():
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return out


def _gfm_tables(markdown):
    # type: (str) -> int
    """How many GFM tables the BODY renders — pipe art inside a fence is not one.

    The rubric counts them itself so that "this document has a table" is an
    observation about the published markdown rather than a claim copied from the
    structure.json being graded."""
    lines = _prose_lines(markdown)
    found = 0
    for i in range(1, len(lines)):
        if not _TABLE_PIPE.search(lines[i - 1]):
            continue
        cells = [c.strip() for c in _TABLE_PIPE.split(lines[i].strip()) if c.strip()]
        if cells and all(_SEP_CELL.match(c) for c in cells):
            found += 1
    return found


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
    outlined = 0
    for b in _all(view):
        outline = b.get("structure", {}).get("outline") or []
        if not outline:
            continue
        outlined += 1
        flattened = _flattened_children(outline)
        if len(outline) > 2 and len(flattened) >= _C1_FLATTENED_MIN:
            return (FAIL, "%s has %d top-level siblings whose section number makes "
                          "them a subsection of another top-level sibling (%s) — "
                          "the hierarchy was never inferred"
                    % (b.get("doc_id", "?")[:8], len(flattened),
                       ", ".join(repr(t[:12]) for t in flattened[:4])))
    if not outlined:
        return _nothing_to_grade("no bundle in the corpus publishes an outline, so "
                                 "no hierarchy was ever inspected")
    # "N bundle(s) inspected" used to count bundles with no outline at all as
    # inspected, which is how a corpus with one structure.json missing could read
    # as three bundles examined. Both numbers are reported now.
    return (PASS, "%d of %d bundle(s) publish an outline; none of them publishes a "
                  "numbered outline flattened into siblings"
            % (outlined, len(_all(view))))


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
    ``overview-1``); a heading that slugs to nothing makes nothing addressable;
    and a ``#`` inside a fenced block is a shell comment, not a heading, so it
    offers no fragment for a published anchor to hide behind.
    """
    out = set()
    seen = {}
    for m in _ATX_HEADING.finditer("\n".join(_prose_lines(markdown))):
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

    The second thing it got wrong was WHOSE denominator. A bundle whose body held
    no ATX heading was skipped with a bare ``continue`` while the compensating
    "nothing to grade" guard stayed corpus-wide, so one healthy sibling covered for
    every bundle without — and that shape needs no adversarial input: an unstyled
    .docx whose sections are ALL-CAPS or numbered paragraphs makes ``is_heading``
    publish anchors over a body that renders not one ``#``. A bundle that publishes
    anchors nothing in its OWN body can resolve now fails on its own account, and
    the pass evidence counts anchors against anchors instead of mixing nodes with
    bundles.
    """
    bundles = _all(view)
    graded = crossed = 0
    silent = 0
    for b in bundles:
        short = b.get("doc_id", "?")[:8]
        nodes = [n for n in _iter_nodes(b.get("structure", {}).get("outline"))
                 if (n.get("title") or "") not in _HEADLESS]
        for node in nodes:
            graded += 1
            anchor, title = node.get("anchor", ""), node.get("title", "")
            if not _anchor_ok(anchor, title):
                return (FAIL, "%s node %s anchor %r but GFM renders %r "
                              "(an ordinal suffix for a repeated title is fine)"
                        % (short, node.get("id"), anchor, gfm_anchor(title)))
        # A node whose title slugs empty carries the `section` placeholder, which
        # names no fragment in any body; it is outside the question, like the
        # preamble. Everything else must be a fragment the body really offers.
        published = set(n.get("anchor") for n in nodes
                        if gfm_anchor(n.get("title") or ""))
        if not published:
            silent += 1
            continue
        addressable = _rendered_anchors(b.get("markdown", ""))
        if not addressable:
            return (FAIL, "%s publishes %d anchor(s) but its own document.md renders "
                          "no heading at all, so every one of them is a fragment that "
                          "resolves nowhere: %s"
                    % (short, len(published), sorted(published)[:4]))
        missing = published - addressable
        if missing:
            return (FAIL, "%s publishes anchor(s) no heading in its own document.md "
                          "makes addressable: %s" % (short, sorted(missing)[:4]))
        crossed += len(published)
    if not graded:
        return _nothing_to_grade("no outline nodes in the corpus under grade")
    if not crossed:
        return _nothing_to_grade("no bundle publishes an anchor a renderer could "
                                 "resolve, so nothing was ever cross-checked against "
                                 "the headings it claims to address")
    return (PASS, "all %d outline node(s) across %d bundle(s) carry the GFM slug of "
                  "their title, and each of the %d anchor(s) that names a fragment "
                  "resolves in the body of its own bundle (%d bundle(s) publish none)"
            % (graded, len(bundles), crossed, silent))


def _c4_summary_numbers(view):
    # type: (dict) -> tuple
    """The published summary numbers describe the tree — of every document.

    A bundle with no outline used to be `continue`d, so a document whose whole
    outline was lost contributed nothing and any sibling that still had one
    carried the row. A lost outline is precisely the damage dimension C exists to
    catch, so a bundle that ships a body and no tree now fails. A bundle with no
    body has nothing to build a tree from and stays exempt."""
    bundles = _all(view)
    graded = 0
    for b in bundles:
        rep, st = b.get("report", {}), b.get("structure", {})
        outline = st.get("outline") or []
        short = b.get("doc_id", "?")[:8]
        if not outline:
            body = (b.get("markdown") or "").strip()
            if body:
                return (FAIL, "%s publishes a document.md of %d line(s) and no "
                              "outline at all — whatever tree it had is gone, and a "
                              "sibling bundle's outline does not stand in for it"
                        % (short, len(body.splitlines())))
            continue
        graded += 1
        summary = rep.get("structure") or {}
        want = _tree_depth(outline)
        if summary.get("max_depth") != want:
            return (FAIL, "%s max_depth %r but the tree is %d deep"
                    % (short, summary.get("max_depth"), want))
        if "largest_leaf_tokens" not in summary:
            return (FAIL, "%s publishes no largest_leaf_tokens, so "
                          "largest_section_tokens can still mean 'the whole document'"
                    % short)
    if not graded:
        return _nothing_to_grade("no bundle in the corpus has an outline")
    return (PASS, "%d of %d bundle(s) publish an outline; in every one max_depth is "
                  "the tree depth and a largest-leaf count is published"
            % (graded, len(bundles)))


def _c5_table_nodes(view):
    # type: (dict) -> tuple
    """Every table a document RENDERS is a node somebody can cite.

    The existence half of this row was one corpus-wide counter, so a bundle whose
    outline addressed none of its tables was covered by any sibling that still had
    one — the row could not fail for the document it was written to police. It is
    asked per bundle now, against the tables the rubric counts in that bundle's own
    ``document.md``; asking the producer how many tables it found would only be
    asking it whether it agrees with itself.

    Lane asymmetry is deliberate and matches ``structure_fidelity``: the office
    lane has a converter-blind ground truth and hard-fails, while a lane with no
    semantic tree of its own (PDF) is reported UNMEASURED rather than failed. The
    negative checks — a legacy integer ``tables``, a table dict missing its
    coordinates — apply to every lane, as they always did."""
    bundles = _all(view)
    saw = required = unmeasured = 0
    for b in bundles:
        short = b.get("doc_id", "?")[:8]
        here = 0
        for node in _iter_nodes(b.get("structure", {}).get("outline")):
            tables = node.get("tables")
            if isinstance(tables, int):
                if tables:
                    return (FAIL, "%s node %s reports tables as the integer %d — a "
                                  "table cannot be cited or linked"
                            % (short, node.get("id"), tables))
                continue
            for tbl in tables or []:
                miss = [k for k in ("table_id", "line", "rows", "cols") if k not in tbl]
                if miss:
                    return (FAIL, "table node missing %s" % miss)
                here += 1
        saw += here
        rendered = _gfm_tables(b.get("markdown", ""))
        if not rendered:
            continue
        if b.get("report", {}).get("lane") != "office":
            unmeasured += 1
            continue
        required += 1
        if not here:
            return (FAIL, "%s renders %d GFM table(s) in its own document.md and "
                          "publishes no table node at all, so not one of them can be "
                          "cited or linked" % (short, rendered))
    if not saw:
        return (FAIL, "no table nodes anywhere in the corpus under grade")
    return (PASS, "%d table(s) are addressable nodes; every one of the %d office "
                  "bundle(s) whose body renders a table addresses it (%d non-office "
                  "bundle(s) with a rendered table: unmeasured)"
            % (saw, required, unmeasured))


def _c6_allcaps(view):
    # type: (dict) -> tuple
    """The one shouted sentence in the graded corpus stayed body text.

    Named for exactly what it measures. This row greps the adversarial fixture for
    a single ALL-CAPS literal, so it is a REGRESSION guard over one sentence in one
    document — it cannot answer "a body sentence cannot become a heading", because
    the corpus under grade contains no numbered body sentence and no keyword one to
    ask it of, and the rubric cannot re-derive `is_heading`'s bound without
    reimplementing the code it is grading. The general claim, over all three
    branches and in both directions, is the C6s suite row's to make."""
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
    return Row(dim, rid, condition, "artifact", "", (), check)


def _suite(dim, rid, condition, target, selector):
    # type: (str, str, str, str, tuple) -> Row
    """A suite row and the specific tests that demonstrate its condition.

    ``selector`` is not decoration. It is the difference between "the condition
    this row asserts is checked" and "that file is green", and the runner turns it
    into pytest node ids so the row fails the moment its named demonstration stops
    existing."""
    return Row(dim, rid, condition, "suite", target, tuple(selector), None)


ROWS = [
    # A. document.md
    _artifact("A", "A1", "structure_fidelity is a second hard gate on the office lane",
              _a1_structure_gate),
    _suite("A", "A2", "body text round-trips as a sequence, not only a multiset",
           "tests/unit/backend/test_validate_roundtrip.py", (
               # The three documents that pass the MULTISET gate and must fail the
               # sequence one. Any other test in that file proves round-tripping,
               # not that order is graded.
               "test_swapping_two_body_paragraphs_passes_the_multiset_gate_but_fails_the_sequence",
               "test_reversing_the_whole_body_passes_the_multiset_gate_but_fails_the_sequence",
               "test_transposing_a_table_passes_the_multiset_gate_but_fails_the_sequence",
           )),
    _artifact("A", "A3", "identifiers survive verbatim in the stored bytes",
              _a3_verbatim),
    _suite("A", "A4", "every deliberate drop emits a named warning carrying a count",
           "tests/unit/backend/test_warning_vocabulary.py", (
               "test_every_documented_warning_code_has_an_emitter",
               "test_every_emitted_warning_code_is_documented",
               "test_a_deliberate_drop_carries_the_size_of_the_loss",
               "test_a_document_that_loses_nothing_reports_nothing",
           )),
    # The eval harness is not pytest, so the row names the FIXTURE it is graded
    # on. "The adversarial fixture is pinned" is a claim about one expectation:
    # eighteen other green fixtures do not make it true, and the row must fail if
    # that expectation is ever dropped from evals/expectations.json.
    _suite("A", "A5", "adversarial fixtures are pinned in the eval corpus",
           "evals/run_eval.py", ("office/kestrel-adversarial.docx",)),
    # B. report.json
    _artifact("B", "B1", "run{} records argv, resolved config with sources, code, host",
              _b1_run_block),
    _artifact("B", "B2", "converter is derived from package metadata + commit",
              _b2_converter),
    _artifact("B", "B3", "every branch taken is a structured decisions[] record",
              _b3_decisions),
    _artifact("B", "B4", "manifest.jsonl is a run log joined to runs.jsonl",
              _b4_runlog),
    # Both halves of the condition, named. "Reproduces the hash" is one test; "or
    # names every divergence" is the rest, one per kind of thing that can differ.
    # The row is deliberately silent about `replay --compare`, which the plan's own
    # P7.12 ledger says can print REPRODUCED for a bundle it did not reproduce:
    # no test here demonstrates that path, so no row may claim it.
    _suite("B", "B5", "replay_run reproduces markdown_sha256 or names divergences",
           "tests/integration/test_replay_run.py", (
               "test_replay_reproduces_the_exact_markdown_hash",
               "test_a_changed_setting_is_named_before_anything_runs",
               "test_an_edited_source_is_caught_although_the_directory_is_unchanged",
               "test_a_missing_source_file_is_named_rather_than_read_as_unchanged",
               "test_a_different_external_tool_version_is_a_divergence",
               "test_a_tool_this_machine_cannot_probe_is_unverified_not_unchanged",
           )),
    _artifact("B", "B6", "no vacuous pass: recall is always over a stated token count",
              _b6_no_vacuous_pass),
    _suite("B", "B7", "exit codes distinguish error from pending work",
           "tests/integration/test_enrich_metadata.py", (
               "test_the_exit_code_says_whether_the_corpus_needs_another_run",
               "test_a_model_outage_leaves_the_fields_pending_and_the_run_re_runnable",
           )),
    # C. structure.json
    _artifact("C", "C1", "heading hierarchy is inferred when the extractor is flat",
              _c1_hierarchy),
    _suite("C", "C1s", "heading levels are inferred from numbering on flat input",
           "tests/unit/backend/test_outline_heuristics.py", (
               "test_a_flat_extractor_gets_its_hierarchy_back_from_the_numbering",
               "test_a_flat_extractor_that_starts_below_the_top_level_is_still_inferred",
               "test_a_bundle_with_real_levels_is_never_reshaped",
               "test_flat_and_unnumbered_headings_are_left_alone",
               # The inference must also REFUSE: a measurement series is not a tree.
               "test_measurements_in_titles_cannot_fabricate_a_hierarchy",
               "test_an_ascending_measurement_series_is_not_a_hierarchy",
               "test_a_numbering_is_believed_only_when_its_children_start_at_one",
           )),
    _suite("C", "C2", "node ids are content-derived and stable across an insertion",
           "tests/unit/backend/test_outline_stability.py", (
               "test_positional_ids_move_but_content_ids_do_not",
               "test_the_fingerprint_tracks_the_sections_own_body_only",
               "test_renaming_a_section_is_the_one_thing_that_moves_its_id",
           )),
    _artifact("C", "C3", "one anchor scheme, GFM-correct, agreed corpus-wide",
              _c3_anchors),
    _artifact("C", "C4", "max_depth is tree depth; a largest-leaf count is published",
              _c4_summary_numbers),
    _artifact("C", "C5", "tables are first-class nodes, addressable like images",
              _c5_table_nodes),
    _artifact("C", "C6", "the shouted callout in the graded corpus stayed body text",
              _c6_allcaps),
    # C6 greps one ALL-CAPS literal in one fixture, so it reported PASS over a
    # numbered body sentence that DID become a heading. The plan's actual C6 —
    # "a body sentence cannot become a heading, the heuristic is bounded" — is a
    # claim about three branches in two directions, and this is where it is made:
    # each shape must stay body text, and its LABEL form must still open a section,
    # because a bound that never fires would satisfy the negative half by refusing
    # to recognise anything at all.
    _suite("C", "C6s", "the heading heuristics are bounded in both directions",
           "tests/unit/backend/test_outline_heuristics.py", (
               "test_a_shouted_body_sentence_is_not_a_heading",
               "test_a_shouted_label_is_still_a_heading",
               "test_a_numbered_body_sentence_is_not_a_heading",
               "test_a_numbered_label_is_still_a_heading",
               "test_a_keyword_sentence_is_not_a_heading",
               "test_a_keyword_label_is_still_a_heading",
           )),
    # D. metadata
    _artifact("D", "D1", "a no-model run yields a titled, summarised, linked page",
              _d1_no_model_page),
    _artifact("D", "D2", "every page can link back to its source", _d2_permalink),
    _artifact("D", "D3", "id is unique corpus-wide", _d3_ids_unique),
    _suite("D", "D3s", "id uniqueness holds by construction at corpus scale",
           "tests/unit/backend/test_id_uniqueness.py", (
               "test_a_thousand_colliding_titles_produce_a_thousand_distinct_ids",
               "test_two_paths_that_slugify_alike_still_get_different_ids",
               "test_documents_differing_only_in_extension_or_case_get_different_ids",
           )),
    _artifact("D", "D4", "one canonical identity", _d4_one_identity),
    _artifact("D", "D5", "every knowledge record present cites its section",
              _d5_records_cite),
    _suite("D", "D5s", "a record that cannot say which section asserts it is refused",
           "tests/unit/backend/test_kb_enrich.py", (
               "test_a_record_that_cannot_say_which_section_asserts_it_is_refused",
               "test_an_unverifiable_ref_is_skipped_rather_than_passed",
               "test_a_model_proposed_link_with_no_ref_is_still_dropped_on_revalidation",
           )),
    _suite("D", "D6", "no field ships that nothing reads and nothing fills",
           "tests/unit/backend/test_field_inventory.py", (
               "test_every_field_names_a_consumer_and_the_claim_is_true",
               "test_every_field_is_filled_by_somebody",
               "test_the_fields_the_inventory_shed_stay_shed",
           )),
    _suite("D", "D7", "corpus gates stay sound at scale; truncation is disclosed",
           "tests/integration/test_kb_lint_corpus.py", (
               "test_the_corpus_gates_stay_sound_and_affordable_at_a_thousand_documents",
               "test_a_narrowed_run_skips_the_gates_that_truncation_would_invert",
               "test_limit_announces_the_documents_it_deferred_rather_than_capping_silently",
               "test_a_narrowed_run_names_the_flag_that_narrowed_it",
           )),
    # E. documentation
    _artifact("E", "E1", "a product guide with a real worked example", _e1_guide),
    # E2, E3 and E4 all lived on tests/integration/test_docs_parity.py, so ONE
    # green file supplied three separate A-grades in a five-row dimension and no
    # row could tell which of the nine tests in it answered the question the row
    # asks. Each now names its own, and the nine are partitioned between them: no
    # test backs two rows, and none is left over to back a row by accident.
    _suite("E", "E2", "every switch and env var is documented and cannot drift",
           "tests/integration/test_docs_parity.py", (
               "test_every_cli_flag_is_documented",
               "test_every_documented_flag_exists",
               "test_every_environment_variable_is_documented",
           )),
    # Two halves, two targets. "Published and cannot go stale" is proven by the
    # drift test that regenerates vocabulary.md and byte-compares; "the vocabulary
    # itself is sound" is proven by the shipped-vocabulary suite. Naming one target
    # for both meant half the condition was never checked by the row asserting it.
    _suite("E", "E3", "the published vocabulary is generated and cannot go stale",
           "tests/integration/test_docs_parity.py", (
               "test_vocabulary_reference_is_not_stale",
               "test_every_vocabulary_term_appears_in_the_reference",
           )),
    _suite("E", "E3s", "the shipped vocabulary is internally valid and parses "
                       "under the restricted reader",
           "tests/integration/test_shipped_vocabulary.py", (
               "test_the_shipped_vocabulary_loads_with_no_arguments_and_self_validates",
               "test_the_file_stays_inside_the_strict_yaml_subset_every_consumer_parses_with",
               "test_every_vocabulary_the_schema_binds_to_exists_in_the_shipped_file",
           )),
    _suite("E", "E4", "every artifact key is documented and cannot drift",
           "tests/integration/test_docs_parity.py", (
               "test_every_report_and_structure_key_is_documented",
               "test_every_manifest_and_frontmatter_key_is_documented",
               "test_every_emitted_warning_code_is_documented",
               "test_the_decision_vocabulary_is_closed_in_both_directions",
           )),
]

DIMENSIONS = (
    ("A", "document.md — the markdown body"),
    ("B", "report.json — reporting and provenance"),
    ("C", "structure.json — the tree"),
    ("D", "metadata / knowledge — the KB substrate"),
    ("E", "documentation"),
)


def _suite_verdict(row, observed):
    # type: (Row, object) -> tuple
    """(status, evidence) for one suite row, from what the runner observed.

    ``observed`` is keyed by ROW ID, not by target, because two rows may name
    different tests in the same file and must be able to disagree. It is either
    ``None`` / absent ("not run"), a ``(status, evidence)`` pair from a runner
    that watched the individual tests, or a bare bool — the old shorthand, kept
    so a caller with nothing but an exit code can still be graded honestly.

    An exit code is the weakest of the three: a pytest target whose tests were
    ALL skipped exits 0, and this module's own rule is that a skip is never an A.
    A runner that hands over a bool is asserting it knows better."""
    if observed is None:
        return (SKIP, "%s: %s was not run"
                % (row.rid, " ".join(row.selector) or row.target))
    if isinstance(observed, tuple) or isinstance(observed, list):
        status, evidence = observed[0], observed[1]
        return (status, evidence)
    return (PASS if observed else FAIL,
            "%s %s" % (row.target, "passed" if observed else "FAILED"))


def grade(view):
    # type: (dict) -> list
    """Every rubric row, evaluated against ``view``.

    ``view`` carries ``bundles`` (each with report/structure/markdown/front/
    knowledge), ``manifest``, ``runs``, ``docs`` and ``suites`` — the last being
    {row id: (status, evidence)|bool|None} filled in by the runner, where None
    means "not run"."""
    results = []
    suites = view.get("suites") or {}
    for row in ROWS:
        if row.kind == "suite":
            status, evidence = _suite_verdict(row, suites.get(row.rid))
            results.append(Result(row.dim, row.rid, row.condition, status, evidence))
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
