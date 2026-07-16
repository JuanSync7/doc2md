#!/usr/bin/env python3
"""Run the doc2md eval: generate the synthetic corpus, run every lane, check
expectations.

The eval is the regression suite for the conversion pipeline. It:

  1. generates (or reuses) the synthetic corpus in ``--corpus``
     (evals/gen_corpus.py) and verifies the HAND-BUILT sources are byte-
     deterministic (regenerate -> same sha256);
  2. runs the office lane   (scripts/build_bundle.py, host python3),
     the text lane          (scripts/text_convert.py, host python3), and —
     iff $DOC2MD_PDF_PYTHON is set and --skip-pdf is not — the PDF lane
     (scripts/build_pdf_bundle.py under that interpreter);
  3. checks ``evals/expectations.json`` per document (keyed by corpus relpath):
     expected lane/status/gates plus targeted content probes for the known
     regressions (post-TOC first heading, merged-cell values, unsplit
     identifiers in the PDF lane, ...);
  4. prints a pass/fail table and exits nonzero on any failure.

Expectations for corpus files whose generation tools were unavailable (see the
corpus manifest) and for the PDF lane when it did not run are reported SKIP,
never silently passed. 3.6-compatible, stdlib only.

Usage:
  python3 evals/run_eval.py                       # full run (PDF lane if env set)
  python3 evals/run_eval.py --skip-pdf            # office + text lanes only
  python3 evals/run_eval.py --no-lanes            # re-check existing outputs
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _HERE)

import gen_corpus                                     # noqa: E402  (sibling module)
from backend.ingest import doc_id, classify_source, ROUTE_UNSUPPORTED  # noqa: E402
from backend.sections import document_outline         # noqa: E402

EXPECTATIONS = os.path.join(_HERE, "expectations.json")
RUN_ID = "eval"                                       # fixed: deterministic bundles


# --------------------------------------------------------------------- helpers

def strip_front_matter(md):
    # type: (str) -> str
    if not md.startswith("---\n"):
        return md
    end = md.find("\n---\n", 3)
    return md[end + 5:] if end >= 0 else md


def read_text(path):
    # type: (str) -> str
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_json(path):
    # type: (str) -> object
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_records(out_dir):
    # type: (str) -> dict
    """id -> latest coverage record across _coverage*.jsonl in ``out_dir``."""
    import glob as _glob
    latest = {}
    for fp in sorted(_glob.glob(os.path.join(out_dir, "_coverage*.jsonl"))):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if "id" in rec:
                        latest[rec["id"]] = rec
        except OSError:
            pass
    return latest


def outline_titles(nodes, out):
    # type: (list, list) -> None
    for n in nodes:
        out.append(n.get("title", ""))
        outline_titles(n.get("children", []), out)


def outline_links(nodes, out):
    # type: (list, list) -> None
    """Flatten every structure.json link node to (text, url)."""
    for n in nodes:
        for lk in n.get("links", []):
            out.append((lk.get("text", ""), lk.get("url", "")))
        outline_links(n.get("children", []), out)


class Checker(object):
    """Collects per-document check failures."""

    def __init__(self):
        self.fails = []  # type: list

    def check(self, ok, what):
        # type: (bool, str) -> None
        if not ok:
            self.fails.append(what)

    def eq(self, got, want, what):
        # type: (object, object, str) -> None
        if got != want:
            self.fails.append("%s: got %r, want %r" % (what, got, want))


# ----------------------------------------------------------- expectation kinds

def check_bundle(rel, exp, bundles_dir):
    # type: (str, dict, str) -> list
    """Checks for an office/pdf BUNDLE expectation; returns failure strings."""
    c = Checker()
    did = doc_id(rel)
    ddir = os.path.join(bundles_dir, did)
    rp = os.path.join(ddir, "report.json")
    if not os.path.isfile(rp):
        return ["no bundle: %s missing" % rp]
    rep = load_json(rp)

    c.eq(rep.get("lane"), exp["lane"], "lane")
    if "status" in exp:
        allowed = exp["status"] if isinstance(exp["status"], list) else [exp["status"]]
        c.check(rep.get("status") in allowed,
                "status: got %r, want one of %r" % (rep.get("status"), allowed))
    loss = rep.get("losslessness", {})
    if "losslessness_gate" in exp:
        c.eq(loss.get("gate"), exp["losslessness_gate"], "losslessness.gate")
    if "losslessness_method" in exp:
        c.eq(loss.get("method"), exp["losslessness_method"], "losslessness.method")
    if "token_recall_min" in exp:
        got = loss.get("token_recall")
        c.check(isinstance(got, (int, float)) and got >= exp["token_recall_min"],
                "token_recall: got %r, want >= %s" % (got, exp["token_recall_min"]))
    if "coverage_gate" in exp:
        cov = rep.get("structure", {}).get("coverage", {})
        c.eq(cov.get("gate"), exp["coverage_gate"], "structure.coverage.gate")
    if "toc_lines_min" in exp:
        cov = rep.get("structure", {}).get("coverage", {})
        c.check(cov.get("toc_lines", 0) >= exp["toc_lines_min"],
                "coverage.toc_lines: got %r, want >= %d"
                % (cov.get("toc_lines"), exp["toc_lines_min"]))
    if "has_toc" in exp:
        c.eq(rep.get("structure", {}).get("has_toc"), exp["has_toc"],
             "structure.has_toc")
    if "max_depth" in exp:
        c.eq(rep.get("structure", {}).get("max_depth"), exp["max_depth"],
             "structure.max_depth")
    if "savings_ratio_min" in exp:
        # The measured representation savings must exist AND clear the floor —
        # a missing block or a collapsed ratio is a regression in the exchange rate.
        sav = rep.get("savings") or {}
        got = sav.get("reduction_ratio")
        c.check(isinstance(got, (int, float)) and got >= exp["savings_ratio_min"],
                "savings.reduction_ratio: got %r, want >= %s"
                % (got, exp["savings_ratio_min"]))
    if "content_links_min" in exp:
        got = rep.get("content", {}).get("links")
        c.check(isinstance(got, int) and got >= exp["content_links_min"],
                "content.links: got %r, want >= %d"
                % (got, exp["content_links_min"]))
    if "images_gate" in exp:
        c.eq(rep.get("images", {}).get("gate"), exp["images_gate"], "images.gate")
    if "images_referenced" in exp:
        c.eq(rep.get("images", {}).get("referenced"), exp["images_referenced"],
             "images.referenced")
    codes = [w.get("code") for w in rep.get("warnings", [])]
    for code in exp.get("warning_codes", []):
        c.check(code in codes, "warnings: %r not present (got %r)" % (code, codes))
    for code in exp.get("warning_codes_absent", []):
        c.check(code not in codes, "warnings: %r unexpectedly present" % code)

    # Content probes (against the BODY, front matter stripped). A failed status
    # publishes no document.md — probe only when it exists / status not failed.
    mdp = os.path.join(ddir, "document.md")
    needs_md = (exp.get("md_contains") or exp.get("md_not_contains")
                or exp.get("md_min_count") or exp.get("outline_titles")
                or exp.get("structure_links_contains"))
    if needs_md:
        if not os.path.isfile(mdp):
            c.check(False, "document.md missing (status %r)" % rep.get("status"))
        else:
            body = strip_front_matter(read_text(mdp))
            for probe in exp.get("md_contains", []):
                c.check(probe in body, "md_contains: %r not found" % probe)
            for probe in exp.get("md_not_contains", []):
                c.check(probe not in body, "md_not_contains: %r found" % probe)
            for probe, n in sorted((exp.get("md_min_count") or {}).items()):
                got = body.count(probe)
                c.check(got >= n, "md_min_count: %r seen %d, want >= %d"
                        % (probe, got, n))
            if exp.get("outline_titles") or exp.get("structure_links_contains"):
                # One guarded load for both structure probes: a missing/corrupt
                # structure.json is a recorded per-doc failure, never a crash of
                # the whole eval run.
                try:
                    st = load_json(os.path.join(ddir, "structure.json"))
                except (OSError, ValueError) as exc:
                    st = None
                    c.check(False, "structure.json missing/unreadable: %s" % exc)
                if st is not None:
                    if exp.get("outline_titles"):
                        titles = []  # type: list
                        outline_titles(st.get("outline", []), titles)
                        for t in exp["outline_titles"]:
                            c.check(t in titles,
                                    "outline_titles: %r not in outline (got %r)"
                                    % (t, titles))
                    if exp.get("structure_links_contains"):
                        # The KG connectivity probe: these (text, url) pairs must
                        # be carried as structure.json link nodes, not just
                        # markdown text.
                        got_links = []  # type: list
                        outline_links(st.get("outline", []), got_links)
                        for want in exp["structure_links_contains"]:
                            pair = (want.get("text", ""), want.get("url", ""))
                            c.check(pair in got_links,
                                    "structure_links: %r not in outline links "
                                    "(got %r)" % (pair, got_links))
    return c.fails


def check_text(rel, exp, text_out):
    # type: (str, dict, str) -> list
    """Checks for a text-lane (passthrough/fence) expectation."""
    c = Checker()
    did = doc_id(rel)
    sc = classify_source(rel)
    c.eq(sc.lane, exp["lane"], "route lane")
    rec = latest_records(text_out).get(did)
    if rec is None:
        return c.fails + ["no coverage record for %s in %s" % (did, text_out)]
    c.check(bool(rec.get("valid")), "record.valid: got %r" % rec.get("valid"))
    c.eq(rec.get("recall"), 1.0, "record.recall")
    mdp = os.path.join(text_out, did + ".md")
    if not os.path.isfile(mdp):
        return c.fails + ["converted markdown missing: %s" % mdp]
    md = read_text(mdp)
    for probe in exp.get("md_contains", []):
        c.check(probe in md, "md_contains: %r not found" % probe)
    op = exp.get("outline_probe")
    if op:
        # Targeted regression probe: the outline built over this markdown must
        # keep the first post-TOC heading (the TOC skip once ate it).
        o = document_outline(md)
        if "has_toc" in op:
            c.eq(o.get("has_toc"), op["has_toc"], "outline.has_toc")
        if "first_title" in op:
            got = o["outline"][0]["title"] if o.get("outline") else None
            c.eq(got, op["first_title"], "outline first heading")
    return c.fails


def check_unsupported(rel, exp, bundles_dir, text_out):
    # type: (str, dict, str, str) -> list
    """An unsupported format must be routed to NO lane and never converted."""
    c = Checker()
    sc = classify_source(rel)
    c.eq(sc.lane, ROUTE_UNSUPPORTED, "route lane")
    c.check(not sc.accepted, "accepted: got %r, want False" % (sc.accepted,))
    did = doc_id(rel)
    c.check(not os.path.isdir(os.path.join(bundles_dir, did)),
            "a bundle was built for an unsupported format")
    c.check(not os.path.isfile(os.path.join(text_out, did + ".md")),
            "a markdown was written for an unsupported format")
    return c.fails


# ------------------------------------------------------------------ lane runs

def run_lane(name, cmd, env=None):
    # type: (str, list, dict) -> int
    print("\n== %s: %s" % (name, " ".join(cmd)), file=sys.stderr)
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        return subprocess.call(cmd, env=e)
    except OSError as exc:
        print("  lane failed to start: %s" % exc, file=sys.stderr)
        return 127


def determinism_check(corpus_dir, manifest):
    # type: (str, dict) -> list
    """Regenerate the hand-built sources into a temp dir and byte-compare with
    the corpus — the constraint is byte-identical output across runs."""
    fails = []
    tmp = tempfile.mkdtemp(prefix="doc2md_eval_det_")
    try:
        gen_corpus.generate(tmp, handbuilt_only=True)
        for rel, info in sorted(manifest.get("files", {}).items()):
            if info.get("kind") != "handbuilt":
                continue
            a = os.path.join(corpus_dir, rel)
            b = os.path.join(tmp, rel)
            try:
                sa, sb = gen_corpus.sha256_file(a), gen_corpus.sha256_file(b)
            except OSError as exc:
                fails.append("%s: unreadable (%s)" % (rel, exc))
                continue
            if sa != sb:
                fails.append("%s: regeneration changed bytes (%s.. != %s..)"
                             % (rel, sa[:12], sb[:12]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


# ------------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate the synthetic corpus, run all doc2md lanes, and "
                    "check evals/expectations.json.")
    ap.add_argument("--corpus", default=os.path.join(_REPO, "data", "eval_corpus"),
                    help="corpus root (default data/eval_corpus)")
    ap.add_argument("--bundles", default=os.path.join(_REPO, "data", "eval_bundles"),
                    help="bundle output root (default data/eval_bundles)")
    ap.add_argument("--text-out",
                    default=os.path.join(_REPO, "data", "eval_bundles_text"),
                    help="text-lane output dir (default data/eval_bundles_text)")
    ap.add_argument("--expectations", default=EXPECTATIONS)
    ap.add_argument("--regen", action="store_true",
                    help="regenerate the corpus even if it exists")
    ap.add_argument("--skip-pdf", action="store_true",
                    help="do not run the PDF lane even if DOC2MD_PDF_PYTHON is set")
    ap.add_argument("--no-lanes", action="store_true",
                    help="skip corpus generation and lane runs; only re-check "
                         "expectations against existing outputs")
    args = ap.parse_args(argv)

    failures = 0
    results = []  # type: list  # (verdict, rel, detail)

    manifest_path = args.corpus.rstrip("/\\") + ".manifest.json"
    if not args.no_lanes:
        if args.regen or not os.path.isfile(manifest_path):
            print("== generating corpus -> %s" % args.corpus, file=sys.stderr)
            os.makedirs(args.corpus, exist_ok=True)
            manifest = gen_corpus.generate(args.corpus)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)
                f.write("\n")
        else:
            print("== reusing corpus %s (use --regen to rebuild)" % args.corpus,
                  file=sys.stderr)
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError):
        print("FATAL: no corpus manifest at %s — run without --no-lanes first"
              % manifest_path, file=sys.stderr)
        return 2

    # Determinism gate on the hand-built sources.
    det = determinism_check(args.corpus, manifest)
    if det:
        for d in det:
            results.append(("FAIL", "(determinism)", d))
        failures += len(det)
    else:
        results.append(("PASS", "(determinism)",
                        "hand-built sources byte-identical on regeneration"))

    pdf_env = os.environ.get("DOC2MD_PDF_PYTHON", "").strip()
    pdf_ran = False
    if not args.no_lanes:
        rc = run_lane("office lane", [
            sys.executable, os.path.join(_REPO, "scripts", "build_bundle.py"),
            "--src", args.corpus, "--out", args.bundles, "--force",
            "--run-id", RUN_ID])
        if rc != 0:
            results.append(("FAIL", "(office lane)", "exit code %d" % rc))
            failures += 1
        rc = run_lane("text lane", [
            sys.executable, os.path.join(_REPO, "scripts", "text_convert.py"),
            "--src", args.corpus, "--out", args.text_out, "--force"])
        if rc != 0:
            results.append(("FAIL", "(text lane)", "exit code %d" % rc))
            failures += 1
        if pdf_env and not args.skip_pdf:
            # exit code is NOT gated here: per-document expectations judge the
            # PDF lane (a truthfully-encoded failed doc would flip the rc).
            run_lane("pdf lane", [
                pdf_env, os.path.join(_REPO, "scripts", "build_pdf_bundle.py"),
                "--src", args.corpus, "--out", args.bundles, "--accept", "pdf",
                "--force", "--run-id", RUN_ID])
            pdf_ran = True
        elif not pdf_env:
            print("\n== pdf lane SKIPPED (DOC2MD_PDF_PYTHON not set)",
                  file=sys.stderr)
    else:
        # --no-lanes: assume prior outputs; the PDF checks run if bundles exist.
        pdf_ran = True

    expectations = load_json(args.expectations)
    for rel in sorted(expectations):
        exp = expectations[rel]
        kind = exp.get("kind", "bundle")
        minfo = manifest.get("files", {}).get(rel, {})
        if minfo.get("kind") == "skipped":
            results.append(("SKIP", rel, "not generated: %s"
                            % minfo.get("reason", "unknown")))
            continue
        if exp.get("requires") == "pdf-lane" and not pdf_ran:
            results.append(("SKIP", rel, "pdf lane did not run"))
            continue
        if kind == "bundle":
            fails = check_bundle(rel, exp, args.bundles)
        elif kind == "text":
            fails = check_text(rel, exp, args.text_out)
        elif kind == "unsupported":
            fails = check_unsupported(rel, exp, args.bundles, args.text_out)
        else:
            fails = ["unknown expectation kind %r" % kind]
        if fails:
            failures += 1
            results.append(("FAIL", rel, "; ".join(fails)))
        else:
            results.append(("PASS", rel, "%d check(s)" % max(1, len(exp) - 1)))

    print("\n%-5s %-38s %s" % ("RES", "DOCUMENT", "DETAIL"))
    print("-" * 100)
    for verdict, rel, detail in results:
        print("%-5s %-38s %s" % (verdict, rel, detail))
    n_pass = sum(1 for v, _, _ in results if v == "PASS")
    n_skip = sum(1 for v, _, _ in results if v == "SKIP")
    n_fail = sum(1 for v, _, _ in results if v == "FAIL")
    print("-" * 100)
    print("eval: %d pass, %d fail, %d skip" % (n_pass, n_fail, n_skip))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
