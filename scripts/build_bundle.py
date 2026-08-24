#!/usr/bin/env python3
"""Emit the doc2md output BUNDLE for each office document.

One document in -> one bundle out, under ``<out>/<doc_id>/``:

    document.md      markdown body + YAML front matter (the source -> markdown map)
    structure.json   the faithful heading outline + per-section token counts
    report.json      the validator verdict (losslessness + metrics + status), NO LLM
    images/          extracted image pixels, content-addressed (<sha16>.<ext>); each
                     is referenced by an ![](images/..) link in the body. Captions are
                     added later by the enrichment stage (deterministic pass = empty alt)

This script is a thin WRITER: all the domain logic is in ``src/backend`` —
``office_convert.bundle_inputs`` (the shared read + losslessness guards) feeds
``backend.bundle.assemble_bundle`` (the pure bundle assembler). It adds no
conversion or validation logic of its own. See docs/design (doc2md
``output-contract.md``) for the schema.

Losslessness here is the office lane's hard gate (token recall == 1.0); a document
that fails the gate still gets a ``report.json`` with ``status: failed`` so the
failure is recorded, never silent.

Idempotent: a doc whose bundle already exists with ``status`` ``ok``/``degraded`` is
skipped (``--force`` to rebuild). Safe to run twice.

Usage:
  python3 scripts/build_bundle.py --src "$DOC2MD_SRC" --out data/bundles
  python3 scripts/build_bundle.py --only "radar spec.docx" --out data/bundles
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _REPO)                        # import the config package
sys.path.insert(0, _HERE)                        # import the sibling office lane script

import office_convert as oc                      # noqa: E402  (disk helpers + shared guards)
from backend.bundle import assemble_bundle       # noqa: E402  (pure assembler)
from backend.ingest import (load_source_root,    # noqa: E402
                            ooxml_image_parts, plan_office_images, inline_ooxml_images,
                            load_ingest_config, image_dimensions)
from backend.validate import image_report, caption_report   # noqa: E402  (report policy)
from backend.provenance import (code_identity, host_identity,   # noqa: E402
                                compact_run, config_provenance, corpus_id,
                                decision, path_id, redact_argv, run_block,
                                stamp_stage)

# This script's own name in `run.entrypoint`, in every `decisions[].stage` it
# writes and in every manifest row it appends — one string, so the three can never
# disagree about which stage produced what.
ENTRYPOINT = "build_bundle"

# Our extracted images are content-addressed: <sha16>.<ext>. Used to scope the orphan
# GC so it only ever removes files this pipeline wrote, never a stray hand-placed file.
_CA_NAME = re.compile(r"^[0-9a-f]{16}\.[A-Za-z0-9]+$")

RUNS = "runs.jsonl"

# Resolved once per process: reading .git per document would be wasteful and could
# even change mid-run. CONVERTER is derived (see _converter_id), never a literal.


def _git_dirty():
    # type: () -> object
    """True/False if git can tell us, else None (recorded as "unknown", never clean).

    The one shell-out in the provenance path, once per run rather than per document.
    3.6 has no ``capture_output``, so ``check_output`` it is."""
    import subprocess
    try:
        with open(os.devnull, "w") as null:
            out = subprocess.check_output(
                ["git", "-C", _REPO, "status", "--porcelain"], stderr=null)
        return bool(out.strip())
    except Exception:                                # noqa: BLE001 - git absent is fine
        return None


def _converter_id(lane="ooxml"):
    # type: (str) -> str
    """``doc2md-<lane>/<version>+<commit7>`` — DERIVED, so two builds from different
    code can never claim the same converter.

    The old frozen literal meant that when a real converter bug was found, nothing on
    disk said which bundles came from the broken code, and the only remedy was a
    --force rebuild of the entire corpus."""
    ident = code_identity(_REPO)
    stamp = "doc2md-%s/%s" % (lane, ident.get("version") or "0")
    commit = ident.get("commit")
    if commit:
        stamp += "+%s" % commit[:7]
    if ident.get("dirty"):
        stamp += ".dirty"
    return stamp


def _run_context(entrypoint, args, argv, run_id, tools=None, paths=None,
                 root_attr="src", extra_config=None):
    # type: (str, object, list, str, dict, dict, str, dict) -> OrderedDict
    """The ``run{}`` block: what was asked for, by which code, on what.

    Configuration provenance is resolved by DIFFERENCE against the real loader (see
    backend.provenance) so there is no second copy of the precedence rules. Path
    arguments are redacted — the root CLAUDE.md forbids absolute host paths in
    published output, and a bundle is published output.

    Shared by every entrypoint that writes into a bundle root, which is why the
    three things that differ between them are arguments: which flags a REPLAY must
    be able to fill back in (``paths``), which flag names the root whose identity is
    recorded (``root_attr``), and any settings the ingest loader does not own
    (``extra_config`` — enrichment's namespace and permalink base resolve through
    argparse, not through the toml)."""
    from backend.ingest import load_ingest_config as _cfg
    now = _cfg()._asdict()
    no_env = _cfg(env={})._asdict()
    no_file = _cfg(env={}, config_path=os.devnull)._asdict()
    env_present = sorted(k for k in os.environ if k.startswith("DOC2MD_"))
    ident = code_identity(_REPO, dirty=_git_dirty())
    config = config_provenance(now, no_env, no_file, env_present)
    for key, rec in sorted((extra_config or {}).items()):
        config[key] = rec
    return run_block(
        entrypoint, run_id,
        argv=redact_argv(argv, paths or {"--src": "<src>", "--out": "<out>"}),
        code=ident, host=host_identity(),
        config=config,
        tools=tools or {},
        source_root_id=path_id(os.path.abspath(getattr(args, root_attr, "") or "")))


def _resolve_tokenizer(cli_override):
    # type: (str) -> tuple
    """Resolve (token_count, token_model) from config.settings, honoring a --tokenizer
    override (``backend`` or ``backend:model``). Falls back to the char estimate if the
    config package is absent so the writer still runs in a stripped deployment."""
    override = None
    if cli_override:
        backend, _, model = cli_override.partition(":")
        override = {"backend": backend or None, "model": model or None}
    try:
        from config.settings import get_token_counter
    except ImportError:
        return None, "char-estimate/4"
    return get_token_counter(override)

MANIFEST = "manifest.jsonl"


def sha256_file(path):
    # type: (str) -> str
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def _write_json(dest, obj):
    # type: (str, object) -> None
    _write_atomic(dest, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _write_bytes(dest, data):
    # type: (str, bytes) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def image_meta_of(asset_pairs):
    # type: (object) -> dict
    """Measured per-image metadata for the structure.json image nodes, keyed by
    image_id (the content-addressed filename stem): byte size always, pixel
    dimensions when the header is a known raster (metafiles/malformed -> omitted,
    never guessed). Shared by both bundle writers via ``extras["image_meta"]``."""
    out = {}
    for fname, data in asset_pairs:
        meta = {"bytes": len(data)}
        dims = image_dimensions(data)
        if dims:
            meta["width"], meta["height"] = dims
        out[fname.split(".", 1)[0]] = meta
    return out


def _verify_images(img_dir, assets):
    # type: (str, list) -> int
    """Count files whose ON-DISK content ``sha256[:16]`` matches their content-addressed
    filename stem — proof the extracted bytes actually landed intact (not just that a
    file of that name exists). This is the image lane's integrity check, the pixel-side
    analogue of the office token-recall gate."""
    ok = 0
    for fname, _data in assets:
        stem = fname.split(".", 1)[0]
        h = hashlib.sha256()
        try:
            with open(os.path.join(img_dir, fname), "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            continue
        if h.hexdigest()[:16] == stem:
            ok += 1
    return ok


def _gc_orphans(img_dir, keep):
    # type: (str, set) -> int
    """Remove content-addressed image files no longer referenced by this build (e.g. a
    figure replaced in the source), returning the count removed. Keeps ``images/`` a
    faithful mirror of the body's references so stale pixels never accumulate across
    rebuilds. Only our ``<sha16>.<ext>`` files are eligible — anything else is left."""
    removed = 0
    try:
        names = os.listdir(img_dir)
    except OSError:
        return 0
    for name in names:
        if name in keep or not _CA_NAME.match(name):
            continue
        try:
            os.remove(os.path.join(img_dir, name))
            removed += 1
        except OSError:
            pass
    return removed


def _append_run(out_root, run, rows, counts):
    # type: (str, dict, list, dict) -> None
    """Append one ``runs.jsonl`` row describing the whole run.

    The per-document manifest rows join to this by ``run_id``. ``corpus_sha256`` is a
    content identity for the document set — sha256 over sorted
    ``doc_id:source_sha256`` pairs — so "did I point at the same corpus?" is
    answerable without recording where the corpus is."""
    from collections import OrderedDict
    row = OrderedDict(run)
    row["counts"] = OrderedDict(sorted(counts.items()))
    row["documents"] = len(rows)
    hashed = [r for r in rows if r.get("source_sha256")]
    if hashed:
        row["corpus_sha256"] = corpus_id(hashed)
    with open(os.path.join(out_root, RUNS), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _prior_captions(doc_dir):
    # type: (str) -> dict
    """The prior report's ``captions{}`` block, or ``{}``.

    Read for one reason: a ``--force`` rebuild carries the captions themselves forward
    by ``image_id``, and blanking ``model``/``prompt_sha`` while keeping the captions
    leaves text on disk that no artifact attributes to anything. The metadata overlay
    already inherits its stamps this way; the caption overlay did not."""
    try:
        with open(os.path.join(doc_dir, "report.json"), encoding="utf-8") as f:
            return json.load(f).get("captions") or {}
    except (OSError, ValueError):
        return {}


def _carry_captions(doc_dir, structure):
    # type: (str, dict) -> int
    """Preserve captions across a rebuild: map ``image_id -> caption`` from the PRIOR
    ``structure.json`` (if any) and re-attach to the freshly built nodes for the SAME
    content-addressed image. A changed image has a new ``image_id``, so a stale caption
    never carries over. Returns the number of captions carried. This is what makes a
    ``--force`` rebuild non-destructive: unchanged figures keep their captions, only
    new/changed images need the (cache-fast) caption pass re-run."""
    try:
        with open(os.path.join(doc_dir, "structure.json"), encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return 0
    caps = {}                                  # image_id -> prior caption

    def collect(nodes):
        for n in nodes:
            for im in n.get("images", []):
                c = im.get("caption")
                if c:
                    caps[im.get("image_id", "")] = c
            collect(n.get("children", []))
    collect(old.get("outline", []))
    if not caps:
        return 0

    def apply(nodes):
        k = 0
        for n in nodes:
            for im in n.get("images", []):
                c = caps.get(im.get("image_id", ""))
                if c and not im.get("caption"):
                    im["caption"] = c
                    k += 1
            k += apply(n.get("children", []))
        return k
    return apply(structure.get("outline", []))


def _count_captioned(structure):
    # type: (dict) -> int
    """Distinct content-addressed images that carry a non-null caption in ``structure``."""
    seen = set()

    def walk(nodes):
        for n in nodes:
            for im in n.get("images", []):
                if im.get("caption"):
                    seen.add(im.get("image_id", ""))
            walk(n.get("children", []))
    walk(structure.get("outline", []))
    return len(seen)


def _count_outline_images(structure):
    # type: (dict) -> int
    """Image-node occurrences attached to the heading outline — i.e. the images the
    caption stage can actually reach (it walks the outline). Less than the body's
    referenced count means some pictures fell outside every section and are
    uncaptionable — a structure gap the report must surface, not hide."""
    total = [0]

    def walk(nodes):
        for n in nodes:
            total[0] += len(n.get("images", []))
            walk(n.get("children", []))
    walk(structure.get("outline", []))
    return total[0]


# What a SUCCESSFUL build publishes, and therefore what a failed one must take out
# of publication. The suffix is not a decoration: every downstream selector tests
# for these names exactly (`enrich_metadata` and `kb_lint` both walk bundles by
# `document.md`), so a withdrawn artifact is invisible to them while still being
# on disk for a person.
_PUBLISHED_FILES = ("document.md", "structure.json", "knowledge.json")
_PUBLISHED_DIRS = ("images",)
STALE_SUFFIX = ".stale"


def _withdraw_published(doc_dir):
    # type: (str) -> list
    """Take a PREVIOUS run's artifacts out of publication when this run FAILED.

    ``docs/reference/output-schema.md`` says it plainly: *a failed document
    publishes ``report.json`` only*. The failure branches honoured that on a first
    build — where there is nothing to leave behind — and broke it on every rebuild
    of a document that used to convert: they rewrote ``report.json`` to
    ``status: failed`` and returned, leaving the last good ``document.md``,
    ``structure.json`` and ``images/`` standing. The bundle then asserted
    ``lossless: "true"`` over the OLD source under the OLD ``source_sha256`` beside
    a report saying the conversion failed, and every consumer keys off "does
    document.md exist": enrichment published a fresh ``knowledge.json`` describing
    the stale body and stamped a ``doc_meta`` gate into the failed report, and
    ``kb_lint`` graded the result clean. Nothing after the writer could see it.

    RENAMED, NOT DELETED. When the failure is environmental rather than about the
    document (soffice missing on the legacy lane, a truncated copy of the source),
    deleting would destroy the only good copy of a bundle to punish a transient
    fault. ``document.md.stale`` is unpublished — no selector matches it — and
    still there for a person. A later successful build clears it (see
    ``_clear_withdrawn``), because by then it is superseded rather than salvage.
    """
    moved = []  # type: list
    for name in _PUBLISHED_FILES:
        path = os.path.join(doc_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            os.replace(path, path + STALE_SUFFIX)      # atomic, overwrites an older one
            moved.append(name)
        except OSError:
            pass
    for name in _PUBLISHED_DIRS:
        path = os.path.join(doc_dir, name)
        if not os.path.isdir(path):
            continue
        dest = path + STALE_SUFFIX
        try:
            shutil.rmtree(dest, ignore_errors=True)
            os.rename(path, dest)
            moved.append(name + "/")
        except OSError:
            pass
    return moved


def _clear_withdrawn(doc_dir):
    # type: (str) -> int
    """Drop the withdrawn copies once a build has published a current bundle again."""
    removed = 0
    for name in _PUBLISHED_FILES:
        path = os.path.join(doc_dir, name + STALE_SUFFIX)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    for name in _PUBLISHED_DIRS:
        path = os.path.join(doc_dir, name + STALE_SUFFIX)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _announce_withdrawn(row, withdrawn):
    # type: (dict, list) -> None
    """Say what stopped being published, and where it went. Never silent: an
    artifact that quietly disappears from a bundle is indistinguishable from one
    that was never written, and the two mean opposite things about the corpus."""
    if withdrawn:
        print("  WITHDRAWN %s %s -> *%s (this run failed; the previous bundle is "
              "no longer published)" % (row.get("rel", row.get("id", "")),
                                        ", ".join(withdrawn), STALE_SUFFIX),
              file=sys.stderr)


def _failure_report(row, error, warnings, run_id="", converter="", run=None,
                    decisions=None):
    # type: (dict, str, list, str, str, dict, list) -> dict
    """A report for a document that FAILED conversion — recorded, never silent. It
    must NOT run through the assembler: an empty body would score a vacuous recall of
    1.0 and falsely 'pass', so failures carry an explicit failed gate instead. The
    run id and source hash are carried here because report.json is a failed doc's
    ONLY artifact (there is no markdown, so no markdown_sha256 — honestly absent)."""
    from collections import OrderedDict
    rep = OrderedDict()
    rep["doc_id"] = row["id"]
    rep["lane"] = "office"
    rep["source_format"] = row["ext"]
    rep["converter"] = converter or _converter_id()
    if run_id:
        rep["generated_run"] = run_id
    rep["source_relpath"] = row["rel"]
    rep["source_sha256"] = sha256_file(row["src"])
    rep["status"] = "failed"
    rep["losslessness"] = {"method": "ooxml-ground-truth", "gate": "fail",
                           "error": error}
    rep["warnings"] = list(warnings or [])
    rep["decisions"] = stamp_stage(decisions, ENTRYPOINT)
    if run:
        record_stage_run(rep, run, writer=True)
    return rep


def record_stage_run(rep, run, writer=False):
    # type: (dict, dict, bool) -> dict
    """Add ``run`` to a report's stage history; for the WRITER, also to ``run{}``.

    ``run{}`` stays exactly what it has always been — the run that produced the
    markdown — because replay_run and the rubric index by it, and a later stage
    overwriting it would destroy the conversion's provenance to record its own.
    ``runs[]`` is the history: one entry per entrypoint that has written into this
    report, oldest first, a re-run of a stage replacing its own entry so nothing
    accumulates. It exists because a bundle is not written once — enrichment
    rewrites ``meta.id``, ``meta.uid`` and ``meta.source.url`` afterwards, and
    before this the switches that decided those three were in no artifact at all.
    A rebuild starts the history over, which is correct: the earlier stages no
    longer describe these bytes."""
    if writer:
        rep["run"] = run
    runs = [r for r in (rep.get("runs") or [])
            if r.get("entrypoint") != run.get("entrypoint")]
    runs.append(run)
    rep["runs"] = runs
    return rep


def build_one(row, soffice, out_root, run_id, token_count=None, token_model=None,
              captions_enabled=False, converter="", run=None):
    # type: (dict, str, str, str, object, str, bool, str, dict) -> dict
    """Convert + validate + assemble + write one document's bundle.

    Returns a manifest row: ``{doc_id, source_relpath, lane, status, markdown_sha256,
    error}``. On hard failure only a ``report.json`` (status failed) is written."""
    doc_dir = os.path.join(out_root, row["id"])
    converter = converter or _converter_id()
    t0 = time.time()
    info = oc.bundle_inputs(row, soffice, emit_images=True)
    t_convert = int((time.time() - t0) * 1000)

    # Every branch this document took, as records rather than prose (see
    # backend.provenance): a choice is not a problem, so it does not go in warnings.
    decisions = [decision("lane_selected", row.get("lane") or "ooxml",
                          "routed by source extension", {"ext": row.get("ext", "")}),
                 decision("tokenizer_selected", token_model or "char-estimate/4",
                          "resolved from --tokenizer / config.settings")]
    if row.get("lane") == oc.ROUTE_LIBREOFFICE:
        decisions.append(decision("preconvert", "soffice", "legacy format converted "
                                  "to its OOXML sibling first",
                                  {"soffice": oc.soffice_version(soffice) or "unknown"}))
    if info["error"] == "empty-source-file":
        # Vacuously lossless -- nothing to lose -- but it must NOT read in the same
        # vocabulary as a real conversion, or a zero-byte upload looks like a success.
        decisions.append(decision("empty_source", "vacuous-pass",
                                  "source file is zero bytes", {"bytes": 0}))

    if info["error"] and info["error"] != "empty-source-file":
        os.makedirs(doc_dir, exist_ok=True)
        rep = _failure_report(row, info["error"], info["warnings"], run_id,
                              converter, run, decisions)
        # A conversion this run could not do must not leave the LAST one's bundle
        # standing as if it were current — see _withdraw_published.
        _announce_withdrawn(row, _withdraw_published(doc_dir))
        _write_json(os.path.join(doc_dir, "report.json"), rep)
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
                "status": "failed", "markdown_sha256": "",
                "source_sha256": sha256_file(row["src"]), "error": info["error"]}

    # Deterministic image extraction: resolve the converter's positional sentinels to
    # content-addressed files, inline the body BEFORE assembling (so structure.json and
    # the report see the final markdown), and surface any referenced-but-missing bytes as
    # a warning — never a silent drop.
    plan = plan_office_images(ooxml_image_parts(info["body"]), info.get("media", {}))
    body_md = inline_ooxml_images(info["body"], plan.fills)
    warnings = list(info["warnings"])
    if plan.n_missing:
        warnings.append({"code": "image_bytes_missing",
                         "detail": "%d referenced image(s) had no bytes in the package"
                                   % plan.n_missing})
    extras = {"images_extracted": plan.n_resolved, "image_files": plan.n_files,
              "images_missing": plan.n_missing, "captions_enabled": captions_enabled,
              # measured raw-XML size the markdown replaces -> report "savings" block
              "source_repr_chars": info.get("source_repr_chars", 0),
              "image_meta": image_meta_of(plan.assets)}

    src_sha = sha256_file(row["src"])
    t1 = time.time()
    bundle = assemble_bundle(
        doc_id=row["id"], source_relpath=row["rel"], source_format=row["ext"],
        lane="office", source_text=info["source_text"], body_md=body_md,
        source_meta=info["meta"], converter=converter, source_sha256=src_sha,
        warnings=warnings, extras=extras, timing_ms={"convert": t_convert},
        generated_run=run_id, token_count=token_count, token_model=token_model,
        source_structure=info.get("source_structure") or {})
    rep = bundle["report"]
    rep["decisions"] = decisions
    # An empty (or effectively empty) source is vacuously lossless -- there was
    # nothing to lose -- but it must SAY so rather than reporting in the same
    # vocabulary as a 337-word runbook.
    if rep["losslessness"].get("n_source_tokens", 0) == 0:
        warnings.append({"code": "empty_source",
                         "detail": "the source carried no gradeable text: recall is "
                                   "vacuous, not evidence of a faithful conversion",
                         "source_tokens": 0})
        rep["warnings"] = list(warnings)
        if not any(d["code"] == "empty_source" for d in decisions):
            decisions.append(decision("empty_source", "vacuous-pass",
                                      "no gradeable source text", {"tokens": 0}))

    os.makedirs(doc_dir, exist_ok=True)
    # A document that FAILS the gate (recall < 1.0 / structural error) must NOT publish
    # its lossy markdown or pixels — report.json only, so the failure is recorded, never
    # silent (and never leaves a half-written bundle behind).
    if rep["status"] == "failed" or rep["losslessness"].get("gate") == "fail":
        rep["timing_ms"]["validate"] = int((time.time() - t1) * 1000)
        rep["decisions"] = stamp_stage(decisions, ENTRYPOINT)
        if run:
            record_stage_run(rep, run, writer=True)
        # The likeliest route to this branch in this project is a converter or
        # ground-truth change that drops a document that passed last week — and the
        # bundle it passed with is exactly what must stop being published.
        _announce_withdrawn(row, _withdraw_published(doc_dir))
        _write_json(os.path.join(doc_dir, "report.json"), rep)
        return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
                "status": "failed", "markdown_sha256": rep["markdown_sha256"],
                "source_sha256": src_sha,
                "error": rep["losslessness"].get("error", "gate-fail")}

    # Gate passed: extract the pixels, then VERIFY them on disk and GC any files left
    # over from a prior build. Feed the MEASURED integrity (content-verified count,
    # orphans removed) back into the report so the image gate reflects the bytes that
    # actually landed — not just what the plan intended.
    img_dir = os.path.join(doc_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    for fname, data in plan.assets:
        _write_bytes(os.path.join(img_dir, fname), data)
    verified = _verify_images(img_dir, plan.assets)
    removed = _gc_orphans(img_dir, set(fname for fname, _ in plan.assets))
    if removed:
        rep["warnings"].append(
            {"code": "orphan_images_removed",
             "detail": "%d stale image file(s) removed on rebuild" % removed})
    im = rep["images"]
    # `orphans` is what REMAINS after the sweep (the gate's question); `orphans_removed`
    # is how many it took out (the dashboard's question). One number cannot answer both.
    rep["images"] = image_report(im["referenced"], im["extracted"], im["unique_files"],
                                 im["missing"], 0, verified, removed)
    if rep["images"]["gate"] != "pass" and rep["status"] == "ok":
        rep["status"] = "degraded"

    # Referenced pictures that never attached to a heading section cannot be captioned
    # (the caption stage walks the outline) — surface the gap instead of hiding it.
    attached = _count_outline_images(bundle["structure"])
    if attached < rep["images"]["referenced"]:
        rep["warnings"].append(
            {"code": "images_not_in_outline",
             "detail": "%d referenced image(s) fall outside the heading outline and "
                       "cannot be captioned" % (rep["images"]["referenced"] - attached)})

    # Non-destructive rebuild: carry unchanged images' captions forward, then make the
    # report's caption block reflect what the (possibly carried) structure now holds.
    carried = _carry_captions(doc_dir, bundle["structure"])
    captioned = _count_captioned(bundle["structure"])
    # A --force rebuild carries captions forward by image_id; carrying the pixels'
    # captions but not WHICH MODEL wrote them leaves captions with no attribution.
    prior = _prior_captions(doc_dir)
    rep["captions"] = caption_report(captions_enabled, im["unique_files"], captioned,
                                     0, 0, im["unique_files"] - captioned,
                                     prior.get("model", ""), prior.get("prompt_sha", ""))
    if carried:
        decisions.append(decision("captions_carried", carried,
                                  "unchanged images kept their prior caption",
                                  {"images": carried}))

    rep["timing_ms"]["validate"] = int((time.time() - t1) * 1000)
    rep["decisions"] = stamp_stage(decisions, ENTRYPOINT)
    if run:
        record_stage_run(rep, run, writer=True)
    _write_json(os.path.join(doc_dir, "report.json"), rep)
    _write_atomic(os.path.join(doc_dir, "document.md"), bundle["document_md"])
    _write_json(os.path.join(doc_dir, "structure.json"), bundle["structure"])
    # This bundle is current again, so anything a previous failure withdrew is
    # superseded rather than salvage — and leaving a `document.md.stale` beside a
    # fresh `document.md` invites somebody to read the wrong one.
    _clear_withdrawn(doc_dir)
    return {"doc_id": row["id"], "source_relpath": row["rel"], "lane": "office",
            "status": rep["status"], "markdown_sha256": rep["markdown_sha256"],
            "source_sha256": src_sha, "error": ""}


def _done(out_root):
    # type: (str) -> dict
    """``{doc_id: status}`` for bundles that already exist non-failed (skip unless
    --force). The STATUS is kept, not just the id, so a run that skips a document can
    still log what that document is — a run log with a hole where the skips were is
    not a run log."""
    done = {}
    try:
        ids = os.listdir(out_root)
    except OSError:
        return done
    for did in ids:
        rp = os.path.join(out_root, did, "report.json")
        try:
            with open(rp, encoding="utf-8") as f:
                status = json.load(f).get("status")
        except (OSError, ValueError):
            continue
        if status in ("ok", "degraded"):
            done[did] = status
    return done


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description="Emit the doc2md output bundle (document.md + structure.json + "
                    "report.json + images/) for each office document.")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC / [paths].source_docs)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "bundles"),
                    help="output root for <doc_id>/ bundles (default data/bundles)")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats to accept (default: all supported)")
    ap.add_argument("--only", action="append", default=[],
                    help="build ONLY this doc id or source basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when a non-failed bundle already exists")
    ap.add_argument("--run-id", default="",
                    help="stamp bundles with this run id (default: UTC timestamp)")
    ap.add_argument("--tokenizer", default="",
                    help="tokenizer override 'backend[:model]' (e.g. tiktoken:cl100k_base); "
                         "default from config/settings.py (char estimate if unset)")
    args = ap.parse_args(argv)

    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))
    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    token_count, token_model = _resolve_tokenizer(args.tokenizer)
    print("tokenizer: %s" % token_model, file=sys.stderr)
    # Whether captioning is enabled is recorded in each report's caption block so a
    # freshly built bundle already declares its caption debt (expected vs pending).
    captions_enabled = bool(getattr(load_ingest_config(), "enable_captions", False))

    from backend.ingest import normalize_accept
    accept_spec = args.accept if args.accept.strip() else (load_ingest_config().accept_formats or None)
    accept = normalize_accept(accept_spec)
    office_sources, scan = oc.scan_tree(args.src, accept)
    oc._warn_unconverted(scan)
    rows = oc.plan(office_sources, args.out)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["id"] in want or os.path.basename(r["rel"]) in want]

    os.makedirs(args.out, exist_ok=True)
    done = {} if args.force else _done(args.out)
    todo_all = [r for r in rows if r["id"] not in done]
    todo = todo_all[:args.limit] if args.limit else todo_all
    capped = len(todo_all) - len(todo)
    msg = ("office sources=%d  already-built=%d  to-build=%d"
           % (len(rows), len(rows) - len(todo_all), len(todo)))
    if capped:
        msg += "  (--limit deferred %d more)" % capped     # never a silent cap
    print(msg + "  -> %s" % args.out, file=sys.stderr)

    n_lo = sum(1 for r in todo if r["lane"] == oc.ROUTE_LIBREOFFICE)
    soffice = oc.find_soffice() if n_lo else ""
    if n_lo and not soffice:
        print("  [WARNING] soffice NOT found -> %d ODF/legacy doc(s) will FAIL "
              "(set DOC2MD_LIBREOFFICE or run scripts/setup_libreoffice.py)" % n_lo,
              file=sys.stderr)

    tools = {}
    if soffice:
        tools["soffice"] = oc.soffice_version(soffice) or "unknown"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = _run_context(ENTRYPOINT, args, raw_argv, run_id, tools)
    # Per document: everything except the ~30-entry resolved-config table, which is
    # identical for every bundle in the run. The omission is NAMED, and the full
    # block is one join away in runs.jsonl.
    run_doc = compact_run(run, "%s#%s" % (RUNS, run_id))
    converter = _converter_id()

    ok = degraded = failed = 0
    t0 = time.time()
    manifest_path = os.path.join(args.out, MANIFEST)
    rows_written = []
    todo_ids = set(r["id"] for r in todo)
    with open(manifest_path, "a", encoding="utf-8") as mf:
        def log(m, action):
            """One manifest row per document per RUN — skips and deferrals included.

            Without this the manifest is a "first non-failed build" log: a skipped
            rebuild wrote nothing, a retried failure appended another indistinguishable
            row, and the number of runs was unrecoverable from disk."""
            m["run_id"] = run_id
            m["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            m["action"] = action
            m["stage"] = ENTRYPOINT
            mf.write(json.dumps(m) + "\n")
            rows_written.append(m)

        for r in todo:
            m = build_one(r, soffice, args.out, run_id, token_count, token_model,
                          captions_enabled, converter, run_doc)
            log(m, "forced" if args.force else "built")
            if m["status"] == "ok":
                ok += 1
            elif m["status"] == "degraded":
                degraded += 1
            else:
                failed += 1
                print("  FAIL %s %s" % (r["rel"], m["error"]), file=sys.stderr)
        for r in rows:
            if r["id"] in todo_ids:
                continue
            action = "skipped" if r["id"] in done else "deferred"
            log({"doc_id": r["id"], "source_relpath": r["rel"], "lane": "office",
                 "status": done.get(r["id"], ""), "markdown_sha256": "",
                 "source_sha256": "", "error": ""}, action)

    run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run["started_at"] = started
    _append_run(args.out, run, rows_written,
                {"ok": ok, "degraded": degraded, "failed": failed,
                 "skipped": sum(1 for m in rows_written if m["action"] == "skipped"),
                 "deferred": sum(1 for m in rows_written if m["action"] == "deferred")})
    print("bundles: ok=%d degraded=%d failed=%d in %.1fs -> %s"
          % (ok, degraded, failed, time.time() - t0, args.out), file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
