#!/usr/bin/env python3
"""Materialize the PINNED docling model artifacts dir (roadmap M0).

Runs UNDER THE PDF-LANE INTERPRETER (Python 3.9+ with the ``docling`` extra
installed — huggingface_hub / requests / yaml all arrive with it). Downloads
every model the PDF lane loads, at a FIXED revision, into one directory that
``DOCLING_ARTIFACTS_PATH`` then points at:

    $DOC2MD_PDF_PYTHON scripts/prefetch_docling_models.py --dest vendor/docling-artifacts
    export DOCLING_ARTIFACTS_PATH="$PWD/vendor/docling-artifacts"

Why this exists: docling 2.113 loads its layout model and picture classifier at
HF revision "main" — floating; only TableFormer rides a tag (v2.3.0). And with
``artifacts_path`` set docling is LOCAL-OR-FAIL (nothing downloads at convert
time), which is exactly the hermeticity we want — provided the bundle is
complete. So this script pins:

  * the three HF model repos at exact COMMITS (recorded 2026-07-17, the
    revisions both CI and the local baseline resolved), and
  * the RapidOCR torch-backend chinese model set (what docling's OCR "auto"
    selects in this venv: no onnxruntime, no easyocr) at the sha256-verified
    v3.9.1 release files, laid out under ``RapidOcr/`` exactly as docling
    2.113's ``rapid_ocr_model.py`` expects.

Everything is fetched IN-PROCESS (huggingface_hub / requests) — no curl/wget.
Idempotent: existing files with a matching sha256 (or an HF dir with a matching
``.pin`` marker) are skipped; safe to run twice. A failed font download is
reported and tolerated (the font serves visualization, not inference); any
other missing artifact is a hard error — better to fail here than at convert
time on a runner with no network.
"""
import argparse
import hashlib
import os
import sys

# One pin table, one place to bump. Folder names follow docling's
# resolve_model_artifacts_path scheme: repo_id with "/" -> "--".
HF_PINS = [
    ("docling-project/docling-layout-heron",
     "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"),          # branch main, 2026-07-17
    ("docling-project/docling-models",
     "fc0f2d45e2218ea24bce5045f58a389aed16dc23"),          # == tag v2.3.0 (tableformer)
    ("docling-project/DocumentFigureClassifier-v2.5",
     "f859dfbff5c9916cd996942d4b0db7fa25808220"),          # branch main, 2026-07-17
]

# RapidOCR torch-backend chinese set — the relpaths docling 2.113 constructs
# under <artifacts>/RapidOcr/ (models/stages/ocr/rapid_ocr_model.py). sha256s
# come from the installed rapidocr's own default_models.yaml at run time so the
# pins cannot drift from what the resolved rapidocr release verifies against.
RAPIDOCR_RELEASE = "v3.9.1"
RAPIDOCR_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve"
RAPIDOCR_FILES = [
    # (relpath under RapidOcr/, manifest key path for the sha256, required)
    ("torch/PP-OCRv4/det/ch_PP-OCRv4_det_mobile.pth",
     ("torch", "PP-OCRv4", "det", "ch_PP-OCRv4_det_mobile"), True),
    ("torch/PP-OCRv4/cls/ch_ptocr_mobile_v2.0_cls_mobile.pth",
     ("torch", "PP-OCRv4", "cls", "ch_ptocr_mobile_v2.0_cls_mobile"), True),
    ("torch/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.pth",
     ("torch", "PP-OCRv4", "rec", "ch_PP-OCRv4_rec_mobile"), True),
    ("paddle/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile/ppocr_keys_v1.txt",
     None, True),                                          # keys: no sha in manifest
    ("resources/fonts/FZYTK.TTF", None, False),            # vis-only; tolerated if 404
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rapidocr_manifest_sha(key_path):
    """sha256 for a model from the INSTALLED rapidocr's default_models.yaml."""
    if key_path is None:
        return None
    import yaml
    import rapidocr
    manifest_path = os.path.join(os.path.dirname(rapidocr.__file__),
                                 "default_models.yaml")
    with open(manifest_path, encoding="utf-8") as f:
        node = yaml.safe_load(f)
    for k in key_path:
        node = node[k]
    return node["SHA256"]


def fetch_hf(dest_root, repo_id, commit):
    """Snapshot one HF repo at an exact commit; skip when the .pin matches.

    The .pin marker is written only AFTER a successful snapshot, so a run that
    died mid-download resumes on rerun. A matching .pin is additionally sanity-
    checked to guard non-empty content (a wiped folder with a surviving marker
    must refetch, not report ok)."""
    folder = os.path.join(dest_root, repo_id.replace("/", "--"))
    pin_file = os.path.join(folder, ".pin")
    if os.path.isfile(pin_file):
        with open(pin_file, encoding="utf-8") as f:
            pinned = f.read().strip() == commit
        has_content = any(n != ".pin" and not n.startswith(".")
                          for n in os.listdir(folder))
        if pinned and has_content:
            print("  [ok] %s @ %s (pinned, present)" % (repo_id, commit[:12]))
            return
        os.unlink(pin_file)
    from huggingface_hub import snapshot_download
    print("  [fetch] %s @ %s" % (repo_id, commit[:12]))
    last_err = None
    for attempt in range(2):     # one retry for transient hub errors
        try:
            snapshot_download(repo_id=repo_id, revision=commit, local_dir=folder)
            last_err = None
            break
        except Exception as e:
            last_err = e
    if last_err is not None:
        raise last_err
    with open(pin_file, "w", encoding="utf-8") as f:
        f.write(commit + "\n")


def fetch_rapidocr(dest_root):
    """The torch-chinese RapidOCR set, sha256-verified where the manifest can."""
    import requests
    failures = []
    for rel, sha_key, required in RAPIDOCR_FILES:
        try:
            # Inside the loop's accounting on purpose: a future rapidocr whose
            # manifest moved/renamed keys must surface as a recorded FAIL for
            # this file, not a raw traceback that hides the rest of the run.
            want_sha = rapidocr_manifest_sha(sha_key)
        except Exception as e:
            failures.append((rel, "manifest sha lookup failed: %s: %s"
                             % (type(e).__name__, e), required))
            continue
        dest = os.path.join(dest_root, "RapidOcr", rel)
        if os.path.isfile(dest) and (want_sha is None
                                     or sha256_file(dest) == want_sha):
            print("  [ok] RapidOcr/%s (present%s)"
                  % (rel, ", sha256 verified" if want_sha else ""))
            continue
        url = "%s/%s/%s" % (RAPIDOCR_BASE, RAPIDOCR_RELEASE, rel)
        # modelscope.cn resets connections intermittently (observed repeatedly
        # from this network) — retry a few times before recording a failure.
        last_err = None
        for attempt in range(1, 4):
            print("  [fetch%s] %s"
                  % ("" if attempt == 1 else " retry %d" % attempt, url))
            try:
                r = requests.get(url, stream=True, timeout=120)
                r.raise_for_status()
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                if want_sha is not None and sha256_file(tmp) != want_sha:
                    os.unlink(tmp)
                    last_err = "sha256 mismatch vs rapidocr manifest"
                    continue
                os.replace(tmp, dest)
                last_err = None
                break
            except requests.HTTPError as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                status = getattr(getattr(e, "response", None), "status_code", 0)
                if 400 <= status < 500:
                    break            # a 404 is not transient — do not re-fetch
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
        if last_err is not None:
            failures.append((rel, last_err, required))
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Download the PDF lane's pinned model artifacts into one "
                    "directory for DOCLING_ARTIFACTS_PATH (idempotent).")
    ap.add_argument("--dest", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vendor", "docling-artifacts"),
        help="artifacts dir (default vendor/docling-artifacts, git-ignored)")
    args = ap.parse_args(argv)

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    print("artifacts -> %s" % dest)

    for repo_id, commit in HF_PINS:
        fetch_hf(dest, repo_id, commit)

    failures = fetch_rapidocr(dest)
    hard = [f for f in failures if f[2]]
    for rel, why, required in failures:
        print("  [%s] RapidOcr/%s: %s"
              % ("FAIL" if required else "warn", rel, why))

    if hard:
        print("prefetch FAILED: %d required artifact(s) missing" % len(hard))
        return 1
    print("prefetch complete. Use it with:\n"
          "  export DOCLING_ARTIFACTS_PATH=\"%s\"" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
