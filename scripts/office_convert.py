#!/usr/bin/env python3
"""The OOXML lane: convert office documents (docx/pptx/xlsx) straight to markdown.

Office files are ZIP+XML — every paragraph, heading style, table row and cell
value is explicitly tagged — so conversion is a deterministic walk of the parts
(``backend.ingest.ooxml_markdown``), not layout inference. Runs on the plain 3.6
host python, stdlib only: no docling, no models, no venv.

Every written markdown is GATED: ``conversion_report`` measures multiset token
recall against the independent exhaustive ground truth (every text run in the
zip) and validates the markdown structure; only ``recall == 1.0`` with zero
structural errors counts as valid. Records append to ``_coverage_ooxml.jsonl``
in the same shape as docling_convert.py's records, so the two lanes share one
skip/heal contract (a doc valid in either lane is done).

Routing: this script owns exactly the formats ``route_format`` maps to the
OOXML and LibreOffice lanes (LibreOffice/legacy-binary inputs are pre-converted
to their OOXML sibling by soffice first, so BOTH office suites travel this single
path); docling_convert.py declines all of them so a format is never double-
converted. Same ids (backend.ingest.doc_id), same <doc_id>.md layout, same out dir.

Idempotent: docs with markdown AND a valid record are skipped (--force to
rebuild). Safe to run twice.

Usage:
  python3 scripts/office_convert.py --src "$DOC2MD_SRC" --out data/markdown_ooxml
  python3 scripts/office_convert.py --validate-only     # re-gate existing .md
  python3 scripts/office_convert.py --audit-parts       # text-bearing parts we don't read
  python3 scripts/office_convert.py --report            # summarize the coverage records
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as _ET
import zipfile
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))

from backend.ingest import (  # noqa: E402
    doc_id, route_format, ext_of, summarize_routes, normalize_accept, unknown_formats,
    supported_formats,
    ROUTE_OOXML, ROUTE_LIBREOFFICE, ROUTE_DOCLING, ROUTE_FENCE, ROUTE_PASSTHROUGH,
    ooxml_markdown, ooxml_source_text, core_properties, front_matter,
    OOXML_MAIN_PARTS, load_source_root, load_ingest_config)
from backend.validate import conversion_report  # noqa: E402  (the validator layer)

COV_NAME = "_coverage_ooxml.jsonl"
# Parts DELIBERATELY not converted; --audit-parts separates these from genuinely
# unread text. Templates/themes/chrome are furniture; page headers/footers are the
# redundancy the pipeline drops by policy; diagrams/drawingN duplicates dataN
# character-for-character (verified on corpus); xl/externalLinks caches OTHER
# workbooks' cells (the referencing cells already carry their computed <v> here).
_FURNITURE_PARTS = re.compile(
    r"(slideLayout|slideMaster|notesMaster|handoutMaster|theme|"
    r"word/(header|footer)\d*\.xml|word/(styles|settings|fontTable|webSettings|numbering)\.xml|"
    r"(word|ppt)/diagrams/(drawing|layout|colors|quickStyle)\d*\.xml|"
    r"xl/externalLinks/|xl/styles\.xml|\.rels$|docProps/|customXml/|word/glossary/)")
_TEXT_RUN = re.compile(r"<(?:\w+:)?(?:t|v)\b[^>]*>\s*[^<\s]")


# LibreOffice/legacy-binary -> the OOXML sibling soffice converts it to. Both office
# suites then travel the SINGLE OOXML path (there is no separate ODF converter).
_LO_TARGET = {"odt": "docx", "odp": "pptx", "ods": "xlsx",
              "doc": "docx", "ppt": "pptx", "xls": "xlsx", "rtf": "docx"}


def vendored_soffice():
    # type: () -> str
    """Repo-relative path to the LibreOffice packaged INTO this tree by
    ``scripts/setup_libreoffice.py`` (``<repo>/vendor/libreoffice/bin/soffice``).

    Derived from ``__file__`` so the tree relocates without edits — no ``/scratch``
    hardcoded. ``""`` when it has not been materialized yet."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo, "vendor", "libreoffice", "bin", "soffice")


def _usable(path):
    # type: (str) -> bool
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def find_soffice():
    # type: () -> str
    """Resolve a LibreOffice ``soffice`` command, or ``""`` if none is available.

    Generic, no hardcoded path. Precedence: an explicit ``DOC2MD_LIBREOFFICE`` env
    override, then the LibreOffice VENDORED into this tree (so the office lane is
    self-contained and always has it once ``setup_libreoffice.py`` has run), then a
    system ``soffice``/``libreoffice`` on PATH."""
    cand = os.environ.get("DOC2MD_LIBREOFFICE", "").strip()
    if _usable(cand):
        return cand
    vendored = vendored_soffice()
    if _usable(vendored):
        return vendored
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return ""


_SOFFICE_VERSION = {}                     # path -> version string (memo, one probe per run)


def soffice_version(soffice, timeout=20):
    # type: (str, int) -> str
    """Version string of this ``soffice`` binary (e.g. ``LibreOffice 7.6.4.1``), or
    ``""`` if it cannot be probed. Memoized per path: soffice is the ONE external
    binary in the office lane, so its version is provenance every legacy/ODF
    conversion should carry — probed once per run, never once per document."""
    if soffice in _SOFFICE_VERSION:
        return _SOFFICE_VERSION[soffice]
    ver = ""
    try:
        out = subprocess.check_output([soffice, "--version"],
                                      stderr=subprocess.STDOUT, timeout=timeout)
        ver = out.decode("utf-8", "replace").strip().split("\n")[0].strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    _SOFFICE_VERSION[soffice] = ver
    return ver


def soffice_to_ooxml(soffice, src_path, target_ext, timeout=180):
    # type: (str, str, str, int) -> str
    """Convert ``src_path`` to ``target_ext`` (docx/pptx/xlsx) via soffice into a
    fresh temp dir; return the produced file's path, or ``""`` on any failure.

    Caller owns the returned file's temp dir and must remove it. Generic — keys only
    off the extension, never a per-document path."""
    tmp = tempfile.mkdtemp(prefix="doc2md_lo_")
    try:
        subprocess.check_output(
            [soffice, "--headless", "--convert-to", target_ext, "--outdir", tmp, src_path],
            stderr=subprocess.STDOUT, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        shutil.rmtree(tmp, ignore_errors=True)
        return ""
    expected = os.path.join(tmp, os.path.splitext(os.path.basename(src_path))[0] + "." + target_ext)
    if os.path.isfile(expected):
        return expected
    for fn in os.listdir(tmp):            # soffice occasionally renames; take the target
        if fn.lower().endswith("." + target_ext):
            return os.path.join(tmp, fn)
    shutil.rmtree(tmp, ignore_errors=True)
    return ""


def scan_tree(src_root, accept=None):
    # type: (str, object) -> tuple
    """Walk ``src_root`` once and classify every file through the router.

    Returns ``(office_sources, scan)`` where ``office_sources`` is the sorted
    ``[(abs, rel)]`` of ACCEPTED files this script owns (OOXML + LibreOffice) and
    ``scan`` is a ``RouteScan`` over the whole tree — its ``unsupported`` and
    ``declined`` buckets are exactly the files that will NOT be converted, which
    the caller warns about so nothing is dropped silently."""
    rel_of = {}
    for root, _, fns in os.walk(src_root):
        for fn in fns:
            full = os.path.join(root, fn)
            rel_of[os.path.relpath(full, src_root)] = full
    scan = summarize_routes(sorted(rel_of), accept)
    office = []
    for lane in (ROUTE_OOXML, ROUTE_LIBREOFFICE):
        for rel in scan.by_lane.get(lane, []):
            office.append((rel_of[rel], rel))
    office.sort(key=lambda t: t[1])
    return office, scan


def plan(sources, out_dir):
    # type: (list, str) -> list
    """Row shape docling_convert.plan shares: {id, rel, src, ext, lane, dest}."""
    rows = []
    for full, rel in sources:
        did = doc_id(rel)
        ext = ext_of(rel)          # single source of truth (matches the router's normalization)
        rows.append({"id": did, "rel": rel, "src": full, "ext": ext,
                     "lane": route_format(ext),
                     "dest": os.path.join(out_dir, did + ".md")})
    return rows


def load_parts(row, soffice="", want_media=False):
    # type: (dict, str, bool) -> tuple
    """``(parts, media, eff_ext, error)`` for one row — the single reader both the
    convert and validate paths use.

    A LibreOffice/legacy-binary row is soffice-converted to its OOXML sibling first
    (so BOTH office suites share the one OOXML path); its parts are read into memory
    and the temp file removed. ``error`` is ``""`` on success, else a short reason
    (``libreoffice-unavailable`` / ``libreoffice-convert-failed``). ``media`` is
    ``{media_part: bytes}`` when ``want_media`` (read from the SAME effective source,
    incl. a legacy temp before cleanup), else ``{}`` — so the bundle writer's image
    extraction reads the exact bytes the converter's sentinels point at."""
    if row.get("lane") == ROUTE_LIBREOFFICE:
        target = _LO_TARGET.get(row["ext"])
        if not target:
            return {}, {}, "", "libreoffice-unsupported-ext"
        if not soffice:
            return {}, {}, "", "libreoffice-unavailable"
        produced = soffice_to_ooxml(soffice, row["src"], target)
        if not produced:
            return {}, {}, "", "libreoffice-convert-failed"
        try:
            media = read_media(produced) if want_media else {}
            return read_parts(produced, target), media, target, ""
        finally:
            shutil.rmtree(os.path.dirname(produced), ignore_errors=True)
    media = read_media(row["src"]) if want_media else {}
    return read_parts(row["src"], row["ext"]), media, row["ext"], ""


def read_parts(path, ext):
    # type: (str, str) -> dict
    """The converter's input: {part_name: xml_text} for this format's main parts.

    A malformed/unreadable zip returns {} (the caller records the failure)."""
    pats = [re.compile(p) for p in OOXML_MAIN_PARTS.get(ext, ())]
    parts = {}
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if any(p.match(name) for p in pats):
                    parts[name] = zf.read(name).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError, KeyError):
        return {}
    return parts


# Raster/metafile media the deterministic image lane extracts (SVG is excluded —
# it is handled as text by the converter, not as pixels).
_MEDIA_RE = re.compile(
    r"^(word|ppt|xl)/media/[^/]+\.(png|jpe?g|gif|bmp|tiff?|emf|wmf|webp)$", re.I)


def read_media(path):
    # type: (str) -> dict
    """``{media_part: bytes}`` for every embedded raster/metafile image in the zip.

    The bytes the sentinel-resolving image extractor stores + content-addresses. A
    malformed/unreadable zip yields ``{}`` (the losslessness guards already fail such a
    document on the parts side)."""
    out = {}
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if _MEDIA_RE.match(name):
                    out[name] = zf.read(name)
    except (zipfile.BadZipFile, OSError, KeyError):
        return {}
    return out


# Text-BEARING parts per format. A part matching these that fails to parse is a real
# content loss the recall gate CANNOT see: both the converter and the independent ground
# truth parse each part through _root(), which degrades a malformed part to empty output
# SYMMETRICALLY — so a corrupt slide/worksheet/document (or the sharedStrings table, which
# backs every xlsx string cell) zeroes on both sides and sails through recall==1.0. We
# therefore parse-check these parts explicitly and FAIL the doc rather than silently
# converting only its healthy parts. (Structure/furniture parts — styles, _rels, docProps —
# are excluded: their loss is graceful, not silent text loss.)
_CONTENT_PARTS = {
    "docx": re.compile(r"^word/document\.xml$"
                       r"|^word/(footnotes|endnotes|comments)\.xml$"
                       r"|^word/charts/chart\d+\.xml$|^word/diagrams/data\d+\.xml$"),
    "pptx": re.compile(r"^ppt/slides/slide\d+\.xml$"
                       r"|^ppt/notesSlides/notesSlide\d+\.xml$"
                       r"|^ppt/diagrams/data\d+\.xml$|^ppt/charts/chart\d+\.xml$"
                       r"|^ppt/comments/[^/]+\.xml$"),
    "xlsx": re.compile(r"^xl/worksheets/[^/]+\.xml$|^xl/workbook\.xml$"
                       r"|^xl/sharedStrings\.xml$|^xl/comments\d*\.xml$"
                       r"|^xl/charts/chart\d+\.xml$"),
}


def _xml_parses(xml):
    # type: (str) -> bool
    try:
        _ET.fromstring(xml)
        return True
    except _ET.ParseError:
        return False


def malformed_content_part(eff_ext, parts):
    # type: (str, dict) -> str
    """Name of the first text-bearing part that does NOT parse (else "").

    The symmetry-hole guard: a present-but-corrupt content part is invisible to the
    recall gate (converter and ground truth both drop it to empty), so a non-empty
    result must still FAIL when one exists. Furniture/structure parts are ignored."""
    cp = _CONTENT_PARTS.get(eff_ext)
    if not cp:
        return ""
    for name in sorted(parts):
        if cp.match(name) and not _xml_parses(parts[name]):
            return name
    return ""


def bundle_inputs(row, soffice="", emit_images=False):
    # type: (dict, str, bool) -> dict
    """Read + guard + convert one office row into the pieces both the markdown lane
    and the bundle writer need — the SINGLE source of the losslessness guards, so the
    two paths can never drift.

    With ``emit_images`` the converter emits positional image sentinels and the media
    bytes are read (returned under ``media``) so the bundle writer can extract + link
    them; default off keeps the legacy markdown lane byte-identical (no sentinels, no
    media read).

    Returns a dict ``{error, body, source_text, meta, warnings, eff_ext, media,
    source_repr_chars}``. ``error`` is ``""`` on success; ``"empty-source-file"`` is a
    SUCCESS sentinel (a 0-byte upload is vacuously lossless — nothing to lose), while
    every other non-empty ``error`` is a genuine failure and ``body``/``source_text``
    are empty. ``source_repr_chars`` is the decompressed size (chars) of every XML part
    the converter parsed — the raw-representation side of the report's ``savings``
    block (0 on any failure path). The guards (unreadable zip, malformed content part,
    empty conversion) are exactly the invariants that keep the recall==1.0 gate honest.
    LibreOffice/legacy inputs are soffice-converted to their OOXML sibling first (via
    ``load_parts``) and noted with a ``libreoffice_preconvert`` warning."""
    warnings = []                                        # type: list
    try:
        if os.path.getsize(row["src"]) == 0:
            return {"error": "empty-source-file", "body": "<!-- empty source file -->\n",
                    "source_text": "", "meta": OrderedDict(), "warnings": warnings,
                    "eff_ext": row.get("ext", ""), "media": {}, "source_repr_chars": 0}
    except OSError:
        pass
    parts, media, eff_ext, err = load_parts(row, soffice, want_media=emit_images)
    if row.get("lane") == ROUTE_LIBREOFFICE and not err:
        # Provenance for the one external binary in the lane: name its version.
        ver = soffice_version(soffice)
        via = "soffice (%s)" % ver if ver else "soffice"
        warnings.append({"code": "libreoffice_preconvert",
                         "detail": "%s -> %s via %s" % (row.get("ext"), eff_ext, via)})
    if err:
        return {"error": err, "body": "", "source_text": "", "meta": OrderedDict(),
                "warnings": warnings, "eff_ext": eff_ext, "media": {},
                "source_repr_chars": 0}
    if not parts:
        return {"error": "unreadable-zip", "body": "", "source_text": "",
                "meta": OrderedDict(), "warnings": warnings, "eff_ext": eff_ext,
                "media": {}, "source_repr_chars": 0}
    # Close the symmetry hole BEFORE measuring: a corrupt content part is lost from both
    # sides and would pass recall==1.0 on the healthy remainder.
    bad_part = malformed_content_part(eff_ext, parts)
    if bad_part:
        return {"error": "malformed-content-part:%s" % bad_part, "body": "",
                "source_text": "", "meta": OrderedDict(), "warnings": warnings,
                "eff_ext": eff_ext, "media": {}, "source_repr_chars": 0}
    # The raw-representation size the markdown replaces: decompressed chars of every
    # XML part parsed (report ``savings`` block; measured, never estimated).
    repr_chars = sum(len(v) for v in parts.values())
    body = ooxml_markdown(eff_ext, parts, emit_images)
    src_text = ooxml_source_text(eff_ext, parts)
    if not body.strip() and src_text.strip():
        return {"error": "empty-conversion", "body": "", "source_text": src_text,
                "meta": OrderedDict(), "warnings": warnings, "eff_ext": eff_ext,
                "media": {}, "source_repr_chars": 0}
    meta = core_properties(parts.get("docProps/core.xml", ""),
                           parts.get("docProps/app.xml", ""))
    return {"error": "", "body": body, "source_text": src_text, "meta": meta,
            "warnings": warnings, "eff_ext": eff_ext, "media": media,
            "source_repr_chars": repr_chars}


def convert_one(row, soffice=""):
    # type: (dict, str) -> tuple
    """(markdown, report) for one source file; markdown is '' on failure.

    A source with NO text at all (0-byte upload, stub document shell) is
    VACUOUSLY lossless: a stub markdown is written and the record is valid, so
    the lane never loops on documents that have nothing to lose. Delegates the read +
    guard + convert to ``bundle_inputs`` (one source of truth)."""
    info = bundle_inputs(row, soffice)
    err = info["error"]
    if err == "empty-source-file":
        return (info["body"], {"valid": True, "recall": 1.0, "error": "empty-source-file"})
    if err:
        return "", {"valid": False, "error": err}
    body, src_text, meta = info["body"], info["source_text"], info["meta"]
    fm = front_matter(meta)
    # The gate scores the BODY only: front matter must never be able to supply
    # tokens the body lost.
    rep = conversion_report(src_text, body)
    md = (fm + "\n" + body) if fm else (body or "<!-- empty source document -->\n")
    return md, rep


def _record(row, rep):
    # type: (dict, dict) -> dict
    """A _coverage*.jsonl record docling_convert's _done_ids understands."""
    return {
        "id": row["id"], "rel": row["rel"], "ext": row["ext"],
        "recall": rep.get("recall", 0.0),
        "n_source": rep.get("n_source", 0),
        "n_covered": rep.get("n_covered", 0),
        "n_missing": rep.get("n_missing", 0),
        "missing_top": rep.get("missing_top", []),
        "structure_errors": rep.get("errors", 0),
        "structure_warnings": rep.get("warnings", 0),
        "docling_status": "OOXML",          # lane marker, same field the heal flow reads
        "backend": "ooxml",
        "valid": bool(rep.get("valid")),
        "error": rep.get("error", ""),
        "ts": int(time.time()),
    }


def _valid_ids(out_dir):
    # type: (str) -> set
    """Ids whose LATEST record across _coverage*.jsonl is valid (either lane).

    Last-record-wins, matching docling_convert._done_ids: a doc that regressed
    (newer valid:false) re-converts instead of being skipped forever on the
    strength of a stale pass."""
    verdict = {}
    for fp in sorted(glob.glob(os.path.join(out_dir, "_coverage*.jsonl"))):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if "id" in rec:
                        verdict[rec["id"]] = bool(rec.get("valid"))
        except OSError:
            pass
    return set(did for did, ok in verdict.items() if ok)


def strip_front_matter(md):
    # type: (str) -> str
    """Body of a markdown file, minus any leading YAML front matter — the gate
    scores the body only, so front matter can never mask a body loss."""
    if not md.startswith("---\n"):
        return md
    end = md.find("\n---\n", 3)
    return md[end + 5:] if end >= 0 else md


def _write_atomic(dest, text):
    # type: (str, str) -> None
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)


def audit_parts(rows, limit=0):
    # type: (list, int) -> int
    """Report zip parts that CONTAIN TEXT but are neither read by the converter
    nor known furniture — the empirical 'did we forget a part?' check."""
    from collections import Counter
    unread = Counter()
    examples = {}
    for i, row in enumerate(rows):
        if limit and i >= limit:
            break
        pats = [re.compile(p) for p in OOXML_MAIN_PARTS.get(row["ext"], ())]
        try:
            with zipfile.ZipFile(row["src"]) as zf:
                for name in zf.namelist():
                    if not name.endswith(".xml") or any(p.match(name) for p in pats):
                        continue
                    if _FURNITURE_PARTS.search(name):
                        continue
                    try:
                        blob = zf.read(name).decode("utf-8", "replace")
                    except (OSError, KeyError):
                        continue
                    if _TEXT_RUN.search(blob):
                        key = re.sub(r"\d+", "N", name)
                        unread[key] += 1
                        examples.setdefault(key, row["rel"])
        except (zipfile.BadZipFile, OSError):
            continue
    if not unread:
        print("audit-parts: no unread text-bearing parts — the converter reads "
              "everything that talks.")
        return 0
    print("audit-parts: text-bearing parts the converter does NOT read:")
    for key, n in unread.most_common():
        print("  %6d  %-45s e.g. %s" % (n, key, examples[key]))
    return 1


def _ext_counts(names):
    # type: (list) -> str
    """"docx(3), pdf(2)" — per-extension counts of a filename list, biggest first."""
    from collections import Counter
    c = Counter((os.path.splitext(n)[1].lstrip(".").lower() or "no-ext") for n in names)
    return ", ".join("%s(%d)" % (e, n) for e, n in c.most_common())


def _warn_unconverted(scan):
    # type: (object) -> None
    """Tell the operator, in ONE place, what will NOT be converted by this run:
    unsupported formats (no lane owns them), formats excluded by the accept-list,
    and — as an FYI, not a warning — files that belong to another lane's producer."""
    docling = scan.by_lane.get(ROUTE_DOCLING, [])
    if docling:
        print("  [note] %d file(s) belong to the docling lane (run scripts/docling_convert.py): %s"
              % (len(docling), _ext_counts(docling)), file=sys.stderr)
    other = scan.by_lane.get(ROUTE_FENCE, []) + scan.by_lane.get(ROUTE_PASSTHROUGH, [])
    if other:
        print("  [note] %d file(s) are markdown/data (passthrough/fence lane), not office: %s"
              % (len(other), _ext_counts(other)), file=sys.stderr)
    if scan.declined:
        print("  [skip] %d file(s) excluded by the accept-list -> NOT converted: %s"
              % (len(scan.declined), _ext_counts(scan.declined)), file=sys.stderr)
    if scan.unsupported:
        print("  [WARNING] %d file(s) in UNSUPPORTED formats will NOT be converted by any "
              "lane: %s" % (len(scan.unsupported), _ext_counts(scan.unsupported)), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic OOXML->markdown converter (the office lane): owns "
                    "docx/pptx/xlsx and, via soffice, the LibreOffice/legacy-binary formats.")
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC or [paths].source_docs)")
    ap.add_argument("--out", default=os.path.join(_REPO, "data", "markdown"),
                    help="output dir for <doc_id>.md (default data/markdown)")
    ap.add_argument("--accept", default="",
                    help="comma-separated formats the system accepts (default: [ingest] "
                         "accept_formats / $DOC2MD_ACCEPT_FORMATS = all supported). Files in "
                         "unsupported or non-accepted formats are reported, never converted.")
    ap.add_argument("--only", action="append", default=[],
                    help="convert ONLY this doc id or source basename; repeatable")
    ap.add_argument("--limit", type=int, default=0, help="stop after N docs")
    ap.add_argument("--force", action="store_true",
                    help="reconvert even when a valid markdown already exists")
    ap.add_argument("--validate-only", action="store_true",
                    help="no writes: re-run the lossless gate on existing markdown")
    ap.add_argument("--audit-parts", action="store_true",
                    help="report text-bearing zip parts the converter does not read")
    ap.add_argument("--report", action="store_true",
                    help="summarize %s and exit" % COV_NAME)
    args = ap.parse_args(argv)

    cov_file = os.path.join(args.out, COV_NAME)
    if args.report:
        return report(cov_file)
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src or set $DOC2MD_SRC" % (args.src,))

    # Accept-list: --accept wins, else the [ingest] config (empty => all supported).
    accept_spec = args.accept if args.accept.strip() else (load_ingest_config().accept_formats or None)
    unknowns = unknown_formats(accept_spec)
    if unknowns:
        print("  [WARNING] accept-list names %d format(s) that match NO lane (ignored; check "
              "for typos): %s  -- supported: %s"
              % (len(unknowns), ", ".join(unknowns), ", ".join(supported_formats())),
              file=sys.stderr)
    accept = normalize_accept(accept_spec)
    office_sources, scan = scan_tree(args.src, accept)
    _warn_unconverted(scan)
    rows = plan(office_sources, args.out)
    if args.only:
        want = set(args.only)
        rows = [r for r in rows
                if r["id"] in want or os.path.basename(r["rel"]) in want]
    if args.audit_parts:
        # audit is an OOXML-part-coverage diagnostic; LibreOffice rows are only OOXML
        # after soffice, so audit their converted sibling elsewhere, not the original.
        return audit_parts([r for r in rows if r["lane"] == ROUTE_OOXML], args.limit)

    os.makedirs(args.out, exist_ok=True)
    valid = set() if args.force else _valid_ids(args.out)
    todo = [r for r in rows
            if args.validate_only or args.force
            or not (r["id"] in valid and os.path.isfile(r["dest"]))]
    if args.limit:
        todo = todo[:args.limit]
    print("office sources=%d  already-valid=%d  to-%s=%d  -> %s"
          % (len(rows), len(rows) - len(todo),
             "validate" if args.validate_only else "convert", len(todo), args.out),
          file=sys.stderr)

    # soffice is only needed when this run actually processes LibreOffice/legacy inputs.
    n_lo = sum(1 for r in todo if r["lane"] == ROUTE_LIBREOFFICE)
    soffice = find_soffice() if n_lo else ""
    if n_lo:
        if soffice:
            print("  [libreoffice] %s -> pre-converting %d ODF/legacy doc(s) to OOXML"
                  % (soffice, n_lo), file=sys.stderr)
        else:
            print("  [WARNING] soffice NOT found -> %d ODF/legacy office doc(s) will FAIL "
                  "to convert (set DOC2MD_LIBREOFFICE=/path/to/soffice or install LibreOffice)"
                  % n_lo, file=sys.stderr)

    ok = bad = 0
    t0 = time.time()
    for r in todo:
        if args.validate_only:
            try:
                with open(r["dest"], encoding="utf-8") as f:
                    md = f.read()
            except OSError:
                bad += 1
                print("  MISSING %s (no markdown to validate)" % r["rel"], file=sys.stderr)
                continue
            parts, _media, eff_ext, err = load_parts(r, soffice)
            bad_part = malformed_content_part(eff_ext, parts) if parts else ""
            if err or (not parts and os.path.getsize(r["src"]) > 0):
                # an unreadable/unconvertible source must FAIL, not pass against an
                # empty ground truth (vacuous recall 1.0)
                rep = {"valid": False, "error": err or "unreadable-zip"}
            elif bad_part:
                # a present-but-corrupt content part is invisible to the recall gate
                rep = {"valid": False, "error": "malformed-content-part:%s" % bad_part}
            else:
                rep = conversion_report(ooxml_source_text(eff_ext, parts),
                                        strip_front_matter(md))
        else:
            md, rep = convert_one(r, soffice)
            if md:
                _write_atomic(r["dest"], md)
        with open(cov_file, "a", encoding="utf-8") as cf:
            cf.write(json.dumps(_record(r, rep)) + "\n")
        if rep.get("valid"):
            ok += 1
        else:
            bad += 1
            print("  FAIL %s recall=%s missing=%s errors=%s %s"
                  % (r["rel"], rep.get("recall"), rep.get("n_missing"),
                     rep.get("errors"), rep.get("error", "")), file=sys.stderr)
    print("office lane done: valid=%d failed=%d in %.1fs" % (ok, bad, time.time() - t0),
          file=sys.stderr)
    return 0 if bad == 0 else 1


def report(cov_file):
    # type: (str) -> int
    """Latest record per id -> per-extension validity/recall summary."""
    latest = {}
    try:
        with open(cov_file, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                latest[rec.get("id")] = rec
    except OSError:
        print("no %s yet" % cov_file, file=sys.stderr)
        return 1
    from collections import defaultdict
    by_ext = defaultdict(list)
    for rec in latest.values():
        by_ext[rec.get("ext", "?")].append(rec)
    total = sum(len(v) for v in by_ext.values())
    total_ok = sum(1 for v in by_ext.values() for r in v if r.get("valid"))
    print("docs=%d valid=%d (%.1f%%)" % (total, total_ok, 100.0 * total_ok / max(1, total)))
    for ext in sorted(by_ext):
        recs = by_ext[ext]
        n_ok = sum(1 for r in recs if r.get("valid"))
        rec_min = min((r.get("recall", 0.0) for r in recs), default=0.0)
        print("  %-5s n=%-4d valid=%-4d min_recall=%.4f warnings=%d"
              % (ext, len(recs), n_ok, rec_min,
                 sum(r.get("structure_warnings", 0) for r in recs)))
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
