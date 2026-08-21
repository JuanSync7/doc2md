"""
title: Integration — the reference docs cannot drift from the code
kind: tests
layer: backend
summary: Every switch, env var, artifact key and vocabulary term is documented, and every documented one exists.
"""
# A reference that lies is worse than no reference. These tests make documentation
# part of the change rather than a follow-up nobody does: adding a flag, an env
# var, a report key or a vocabulary term fails CI until docs/reference/ is updated.
#
# Integration (not unit): reads the real scripts, the real config, and builds a
# real bundle to learn which keys a writer actually emits.
import ast
import importlib.util
import io
import json
import os
import re
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REF = os.path.join(REPO, "docs", "reference")
CONFIG_DOC = os.path.join(REF, "configuration.md")
SCHEMA_DOC = os.path.join(REF, "output-schema.md")
VOCAB_DOC = os.path.join(REF, "vocabulary.md")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

# Flags every argparse-based tool inherits from convention and which the reference
# documents once, in the pipeline tables, rather than repeating per script.
_UNIVERSAL = {"--help", "-h"}


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _script_paths():
    out = []
    for base in ("scripts", "evals"):
        d = os.path.join(REPO, base)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                out.append(os.path.join(d, fn))
    return out


def _flags_of(path):
    """Every long option string the file registers with argparse."""
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:                                    # pragma: no cover
        return set()
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_argument"):
            for a in node.args:
                if isinstance(a, ast.Str) and a.s.startswith("--"):
                    flags.add(a.s)
    return flags - _UNIVERSAL


def test_every_cli_flag_is_documented():
    doc = _read(CONFIG_DOC)
    missing = []
    for path in _script_paths():
        rel = os.path.relpath(path, REPO)
        for flag in sorted(_flags_of(path)):
            if ("`%s`" % flag) not in doc:
                missing.append("%s %s" % (rel, flag))
    assert not missing, (
        "undocumented CLI flags — add them to docs/reference/configuration.md "
        "with what they change in the OUTPUT:\n  " + "\n  ".join(missing))


def test_every_documented_flag_exists():
    doc = _read(CONFIG_DOC)
    real = set()
    for path in _script_paths():
        real |= _flags_of(path)
    # Only claim flags the doc presents as code spans starting with "--".
    documented = set(re.findall(r"`(--[a-z0-9][a-z0-9-]*)`", doc)) - _UNIVERSAL
    stale = sorted(documented - real)
    assert not stale, (
        "docs/reference/configuration.md documents flags that no script has: %s"
        % ", ".join(stale))


def test_every_environment_variable_is_documented():
    doc = _read(CONFIG_DOC)
    names = set()
    for base in ("src", "scripts", "config", "evals", "tests"):
        d = os.path.join(REPO, base)
        for root, _dirs, files in os.walk(d):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                names |= set(re.findall(r'"(DOC2MD_[A-Z0-9_]+)"',
                                        _read(os.path.join(root, fn))))
    missing = sorted(n for n in names if ("`%s`" % n) not in doc)
    assert not missing, (
        "undocumented environment variables — add them to "
        "docs/reference/configuration.md:\n  " + "\n  ".join(missing))


def test_vocabulary_reference_is_not_stale():
    spec = importlib.util.spec_from_file_location(
        "render_vocab_doc", os.path.join(REPO, "scripts", "render_vocab_doc.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main(["--check", "--out", VOCAB_DOC])
    assert rc == 0, ("docs/reference/vocabulary.md is stale — "
                     "run `python3 scripts/render_vocab_doc.py`")


def test_every_vocabulary_term_appears_in_the_reference():
    """Belt and braces: the generator could render a block and omit its values."""
    from backend.ingest import parse_block
    from backend.kb import vocab_path
    data = parse_block(_read(vocab_path()))
    doc = _read(VOCAB_DOC)
    missing = []
    for name, block in sorted(data.items()):
        if not isinstance(block, dict) or "governance" not in block:
            continue
        for value in block.get("values") or []:
            if ("`%s`" % value) not in doc:
                missing.append("%s: %s" % (name, value))
    assert not missing, "terms missing from the reference: %s" % ", ".join(missing)


# --------------------------------------------------------------- artifact keys

def _key_paths(obj, prefix, out):
    """Every dotted key path, with repeated `children[]` levels collapsed so an
    outline of any depth compares against one documented node shape."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = (prefix + "." + k) if prefix else k
            out.add(p)
            _key_paths(v, p, out)
    elif isinstance(obj, list):
        for v in obj:
            _key_paths(v, prefix + "[]", out)


def _leaf(path):
    return path.replace("[]", "").split(".")[-1]


def _documented_tokens(doc):
    """Every identifier appearing inside a code span. A block documented as
    ``captions{}`` or a pair documented as ``{code, detail}`` counts, so the
    reference can read like prose instead of a flat key dump."""
    tokens = set()
    # Drop fenced-block markers first: a ``` line makes every backtick pair after
    # it line up one span out, which silently turns this whole check into noise.
    doc = re.sub(r"^\s*```.*$", "", doc, flags=re.M)
    for span in re.findall(r"`([^`\n]+)`", doc):
        for word in re.split(r"[^A-Za-z0-9_]+", span):
            if word:
                tokens.add(word)
    return tokens


def _build_a_real_bundle(tmp_path):
    """Convert a docx exercising headings, a list, a table, a link and a header,
    then enrich it offline — so the key set is what a writer really emits."""
    spec = importlib.util.spec_from_file_location(
        "build_bundle", os.path.join(REPO, "scripts", "build_bundle.py"))
    bb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bb)
    spec2 = importlib.util.spec_from_file_location(
        "enrich_metadata", os.path.join(REPO, "scripts", "enrich_metadata.py"))
    em = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(em)

    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    doc = ('<w:document %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Runbook</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Body prose with a '
           '</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc></w:tr>'
           '<w:tr><w:tc><w:p><w:r><w:t>Lead</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>Ravi</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
           '</w:body></w:document>' % W)
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    hdr = '<w:hdr %s><w:p><w:r><w:t>Confidential</w:t></w:r></w:p></w:hdr>' % W
    with zipfile.ZipFile(str(src / "runbook.docx"), "w") as zf:
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/header1.xml", hdr)
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    em.main(["--bundles", str(out), "--run-id", "R"])       # offline: exit 2 = pending
    d = [os.path.join(str(out), n) for n in os.listdir(str(out))
         if os.path.isdir(os.path.join(str(out), n))][0]
    return str(out), d


def test_every_report_and_structure_key_is_documented(tmp_path):
    out, d = _build_a_real_bundle(tmp_path)
    tokens = _documented_tokens(_read(SCHEMA_DOC))
    missing = []
    for name in ("report.json", "structure.json"):
        keys = set()
        with io.open(os.path.join(d, name), encoding="utf-8") as fh:
            _key_paths(json.load(fh), "", keys)
        for path in sorted(keys):
            if _leaf(path) not in tokens:
                missing.append("%s: %s" % (name, path))
    assert not missing, (
        "keys missing from docs/reference/output-schema.md:\n  "
        + "\n  ".join(missing))


def test_every_manifest_and_frontmatter_key_is_documented(tmp_path):
    from backend.ingest import split_front_matter
    out, d = _build_a_real_bundle(tmp_path)
    doc = _read(SCHEMA_DOC)
    tokens = _documented_tokens(doc)
    missing = []

    with io.open(os.path.join(out, "manifest.jsonl"), encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    for k in sorted(row):
        if k not in tokens:
            missing.append("manifest.jsonl: %s" % k)

    meta, _body = split_front_matter(_read(os.path.join(d, "document.md")))
    for k in sorted(meta):
        # source_* properties are documented as one family, by prefix.
        if k.startswith("source_") and "`source_*`" in doc:
            continue
        if k not in tokens:
            missing.append("front matter: %s" % k)
    assert not missing, (
        "keys missing from docs/reference/output-schema.md:\n  "
        + "\n  ".join(missing))


def test_every_emitted_warning_code_is_documented():
    """A named drop is only visible if the name is written down somewhere."""
    doc = _read(SCHEMA_DOC)
    codes = set()
    for base in ("src", "scripts"):
        for root, _dirs, files in os.walk(os.path.join(REPO, base)):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    codes |= set(re.findall(r'"code":\s*"([a-z_]+)"',
                                            _read(os.path.join(root, fn))))
    codes.discard("x")                                     # test placeholder
    tokens = _documented_tokens(doc)
    missing = sorted(c for c in codes if c not in tokens)
    assert not missing, (
        "warning codes missing from docs/reference/output-schema.md: %s"
        % ", ".join(missing))
