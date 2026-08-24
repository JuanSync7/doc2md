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
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

# A 1x1 PNG, so the fixture really emits an image node (and so `images{}` counts a
# verified file) instead of only claiming to in a docstring.
PNG = (b"\x89PNG\r\n\x1a\n"
       b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89"
       b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")

DRAW = ('xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
        'wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/'
        'drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/'
        'drawingml/2006/picture"')

_PICTURE = (
    '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
    '<wp:extent cx="914400" cy="914400"/>'
    '<wp:docPr id="1" name="shot"/>'
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
    'drawingml/2006/picture"><pic:pic><pic:nvPicPr>'
    '<pic:cNvPr id="1" name="shot"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="rId11"/><a:stretch><a:fillRect/>'
    '</a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
    '<a:ext cx="914400" cy="914400"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
    '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')

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


# ------------------------------------------------- where a key must be documented
#
# `_documented_tokens` over the WHOLE reference is a 440-word bag, and matching a
# key's leaf name against it accepted a key because its name happened to occur in
# some OTHER block's table. `structure_fidelity.ratio`, `.note` and `.reason` all
# passed while the `structure_fidelity{}` section named none of them: `note` is
# documented for `losslessness{}` on the pdf-ocr lane, `reason` for `decisions[]`,
# `ratio` as `coverage.ratio` under `structure{}`. A reader who follows the doc to
# the block they just met a key in finds nothing — which is the exact failure this
# module says it prevents. So the bag is now taken per SECTION and a key is looked
# up in the section that documents ITS block.
#
# The mapping has to be written out. The reference's headings do NOT name JSON
# blocks one-to-one: the top-level `report.json` identity keys live under
# `### Identity and provenance`, and the outline node shape under `### Outline
# node` / `### Image node` / `### Table node`, none of which is a heading that
# names `outline[]`. A regex over heading text would guess wrong and redden keys
# that are correctly documented today.
_IDENTITY = "### Identity and provenance"
_RUN = "### `run{}` — what this run was"

# (artifact, `[]`-stripped path prefix) -> the section(s) that document that block.
_BLOCK_SECTIONS = {
    ("structure.json", ""): ("## `structure.json` — the outline",),
    ("structure.json", "outline"): ("### Outline node",),
    ("structure.json", "outline.images"): ("### Image node",),
    ("structure.json", "outline.tables"): ("### Table node",),
    # Link nodes are documented inline, in the outline node's own `links` row.
    ("structure.json", "outline.links"): ("### Outline node",),
    ("report.json", ""): (_IDENTITY,),
    ("report.json", "timing_ms"): (_IDENTITY,),
    ("report.json", "losslessness"): ("### `losslessness{}` — the gate",),
    ("report.json", "structure_fidelity"):
        ("### `structure_fidelity{}` — the second gate",),
    ("report.json", "run"): (_RUN,),
    # `runs[]` says in prose that each entry "has exactly the shape of run{}
    # above" rather than repeating the table, so both sections answer for it.
    ("report.json", "runs"):
        ("### `runs[]` — every stage that wrote into this report", _RUN),
    ("report.json", "decisions"): ("### `decisions[]` — what the pipeline chose",),
    ("report.json", "content"): ("### `content{}` — what the markdown contains",),
    ("report.json", "savings"): ("### `savings{}` — the exchange rate",),
    ("report.json", "structure"):
        ("### `structure{}` — outline summary and the coverage gate",),
    ("report.json", "images"): ("### `images{}` — the pixel-side gate",),
    ("report.json", "captions"): ("### `captions{}` — overlay coverage",),
    ("report.json", "doc_meta"): ("### `doc_meta{}` — metadata overlay coverage",),
    ("report.json", "structural_errors"):
        ("### `structural_errors` / `structural_warnings`",),
    ("report.json", "structural_warnings"):
        ("### `structural_errors` / `structural_warnings`",),
    ("report.json", "warnings"): ("### `warnings[]`",),
    ("manifest.jsonl", ""): ("## `manifest.jsonl` — the corpus index",),
    ("front matter", ""): ("## `document.md` — front matter",
                           "#### `meta` — identity, the floor, and the permalink"),
}

# Keys the pipeline publishes today that the reference does not document in the
# block they appear in. Written down rather than waved through, and the assertion
# below is an EQUALITY: a NEW gap fails the test, and CLOSING one fails it too
# until the entry is deleted, so this ledger cannot quietly become the norm.
_UNDOCUMENTED_TODAY = {}      # empty, and the equality below keeps it that way


def _sections(doc):
    """The reference split into its `##`/`###`/`####` sections, heading included
    (so `### \\`losslessness{}\\`` documents the name of the block it opens)."""
    out = {}
    parts = re.split(r"(?m)^(#{2,4} .*)$", doc)
    for i in range(1, len(parts), 2):
        out[parts[i].strip()] = parts[i] + "\n" + parts[i + 1]
    return out


def _collapse(path):
    """`[]` dropped and every repeated `children[]` level removed, so an outline
    of any depth is compared against the one documented node shape."""
    while ".children[]." in path:
        path = path.replace(".children[].", ".", 1)
    return path.replace("[]", "")


def _sections_for(artifact, prefix):
    """The longest declared block prefix that covers `prefix`."""
    segments = prefix.split(".") if prefix else []
    for n in range(len(segments), -1, -1):
        key = (artifact, ".".join(segments[:n]))
        if key in _BLOCK_SECTIONS:
            return _BLOCK_SECTIONS[key]
    return _BLOCK_SECTIONS[(artifact, "")]


def _tokens_in(sections, heads):
    tokens = set()
    for head in heads:
        assert head in sections, "no `%s` section in output-schema.md" % head
        tokens |= _documented_tokens(sections[head])
    return tokens


def _undocumented(sections, artifact, keys):
    """Every key path whose leaf is absent from its own block's section.

    A key that OPENS a documented block (`losslessness`, `outline[].tables`) is
    documented either by a row in its parent's table or by the heading of the
    section it opens — both count, and nothing else does."""
    missing = []
    for path in sorted(keys):
        collapsed = _collapse(path)
        segments = collapsed.split(".")
        leaf, parent = segments[-1], ".".join(segments[:-1])
        ok = leaf in _tokens_in(sections, _sections_for(artifact, parent))
        if not ok and (artifact, collapsed) in _BLOCK_SECTIONS:
            ok = leaf in _tokens_in(
                sections, _BLOCK_SECTIONS[(artifact, collapsed)])
        if not ok:
            missing.append("%s: %s" % (artifact, path))
    return missing


def _assert_documented(missing, artifacts):
    """Fail on anything not in the ledger — and on a ledger entry now documented.

    `artifacts` names which artifacts this caller actually inspected, so the
    second assertion only speaks for ledger entries this run could have seen."""
    unexpected = [m for m in missing if m not in _UNDOCUMENTED_TODAY]
    assert not unexpected, (
        "keys missing from the block that documents them in "
        "docs/reference/output-schema.md — a reader who follows the doc to that "
        "block has nowhere to look them up:\n  " + "\n  ".join(unexpected))
    stale = sorted(k for k in _UNDOCUMENTED_TODAY
                   if k.split(":")[0] in artifacts and k not in missing)
    assert not stale, (
        "these are documented now — delete them from _UNDOCUMENTED_TODAY so the "
        "ledger keeps meaning what it says: %s" % ", ".join(stale))


def _build_a_real_bundle(tmp_path):
    """Convert a docx exercising headings, a list, a table, a link, an inline
    image and a header, then enrich it offline — so the key set is what a writer
    really emits.

    The list, the hyperlink and the picture are here because the docstring used to
    claim them and the XML did not carry them: `outline[].links` and
    `outline[].images` came out empty, so the documented link-node and image-node
    keys (`text`/`url`/`line`, `image_id`/`ref`/`alt`/`caption`/`bytes`/`width`/
    `height`) were never in the compared set at all. A key the fixture cannot emit
    is a key this test cannot police."""
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
    doc = ('<w:document %s %s %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Runbook</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Body prose with a '
           '</w:t></w:r></w:p>'
           # A numbered step, an external hyperlink and an inline picture.
           '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
           '</w:numPr></w:pPr><w:r><w:t>Drain the queue</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>See the </w:t></w:r>'
           '<w:hyperlink r:id="rId20"><w:r><w:t>escalation policy</w:t></w:r>'
           '</w:hyperlink><w:r><w:t> before paging.</w:t></w:r></w:p>'
           '<w:p>%s</w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc></w:tr>'
           '<w:tr><w:tc><w:p><w:r><w:t>Lead</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>Ravi</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
           '</w:body></w:document>' % (W, R, DRAW, _PICTURE))
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    hdr = '<w:hdr %s><w:p><w:r><w:t>Confidential</w:t></w:r></w:p></w:hdr>' % W
    numbering = (
        '<w:numbering %s><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%%1."/><w:pPr><w:ind w:left="720" w:hanging="360"/>'
        '</w:pPr></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '</w:numbering>' % W)
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rId11" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/image" Target="media/shot.png"/>'
        '<Relationship Id="rId20" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/hyperlink" '
        'Target="https://runbooks.example/escalation" TargetMode="External"/>'
        '</Relationships>')
    with zipfile.ZipFile(str(src / "runbook.docx"), "w") as zf:
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/header1.xml", hdr)
        zf.writestr("word/numbering.xml", numbering)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/media/shot.png", PNG)
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    em.main(["--bundles", str(out), "--run-id", "R"])       # offline: exit 2 = pending
    d = [os.path.join(str(out), n) for n in os.listdir(str(out))
         if os.path.isdir(os.path.join(str(out), n))][0]
    return str(out), d


def test_every_report_and_structure_key_is_documented(tmp_path):
    out, d = _build_a_real_bundle(tmp_path)
    sections = _sections(_read(SCHEMA_DOC))
    missing = []
    for name in ("report.json", "structure.json"):
        keys = set()
        with io.open(os.path.join(d, name), encoding="utf-8") as fh:
            _key_paths(json.load(fh), "", keys)
        missing += _undocumented(sections, name, keys)
    _assert_documented(missing, ("report.json", "structure.json"))


def test_a_key_is_not_documented_by_another_blocks_table(tmp_path):
    """The leakage itself, pinned: three names that ARE in the reference, none of
    them in `structure_fidelity{}`, must not pass as that block's keys.

    `ratio` is `coverage.ratio` under `structure{}`, `note` is a pdf-ocr key of
    `losslessness{}`, `reason` belongs to `decisions[]`. Under the old flat bag all
    three were accepted, and a fidelity `ratio` beside `compared` is exactly the
    field somebody adds next."""
    out, d = _build_a_real_bundle(tmp_path)
    sections = _sections(_read(SCHEMA_DOC))
    with io.open(os.path.join(d, "report.json"), encoding="utf-8") as fh:
        report = json.load(fh)
    report["structure_fidelity"]["ratio"] = 0.5
    report["structure_fidelity"]["note"] = "undocumented"
    report["structure_fidelity"]["reason"] = "also undocumented"
    keys = set()
    _key_paths(report, "", keys)
    missing = _undocumented(sections, "report.json", keys)
    assert sorted(m for m in missing if m not in _UNDOCUMENTED_TODAY) == [
        "report.json: structure_fidelity.note",
        "report.json: structure_fidelity.ratio",
        "report.json: structure_fidelity.reason",
    ]


def test_every_manifest_and_frontmatter_key_is_documented(tmp_path):
    from backend.ingest import split_front_matter
    out, d = _build_a_real_bundle(tmp_path)
    doc = _read(SCHEMA_DOC)
    sections = _sections(doc)
    missing = []

    with io.open(os.path.join(out, "manifest.jsonl"), encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    missing += _undocumented(sections, "manifest.jsonl", set(row))

    meta, _body = split_front_matter(_read(os.path.join(d, "document.md")))
    # source_* properties are documented as one family, by prefix.
    keys = set(k for k in meta
               if not (k.startswith("source_") and "`source_*`" in doc))
    missing += _undocumented(sections, "front matter", keys)
    _assert_documented(missing, ("manifest.jsonl", "front matter"))


def test_every_emitted_warning_code_is_documented():
    """A named drop is only visible if the name is written down IN THE WARNING
    TABLE. Against the whole-file bag a code called `note`, `count` or `reason`
    counted as documented because some unrelated block used the word."""
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
    tokens = _tokens_in(_sections(doc), _BLOCK_SECTIONS[("report.json", "warnings")])
    missing = sorted(c for c in codes if c not in tokens)
    assert not missing, (
        "warning codes missing from the `warnings[]` table in "
        "docs/reference/output-schema.md: %s" % ", ".join(missing))


def test_the_decision_vocabulary_is_closed_in_both_directions():
    """`src/backend/provenance/CLAUDE.md` says a code cannot be added without
    documenting it, "the parity test enforces it".

    It said that while no such test existed — the same shape of claim as
    `dropped_headers_footers`, which was documented and required and had zero
    emitters for months. A rule nobody enforces is a rule nobody follows, so this
    is the enforcement rather than a softened claim.
    """
    import sys
    sys.path.insert(0, os.path.join(REPO, "src"))
    from backend.provenance import DECISION_CODES

    doc = _read(os.path.join(REPO, "docs", "reference", "output-schema.md"))
    section = re.search(r"^### `decisions\[\]`(.*?)(?=^#{1,3} |\Z)", doc, re.S | re.M)
    assert section, "no `decisions[]` section in output-schema.md"
    documented = set(re.findall(r"`([a-z][a-z0-9_]+)`", section.group(1)))

    undocumented = sorted(set(DECISION_CODES) - documented)
    assert not undocumented, (
        "decision codes the pipeline can emit but the contract does not name — a "
        "reader meeting one in a report has nowhere to look it up: %s" % undocumented)

    # And the other direction: the codes listed after "Codes:" must all be real.
    listed = re.search(r"^Codes: (.*?)\.$", section.group(1), re.S | re.M)
    assert listed, "the decisions[] section lists no codes"
    claimed = set(re.findall(r"`([a-z][a-z0-9_]+)`", listed.group(1)))
    phantom = sorted(claimed - set(DECISION_CODES))
    assert not phantom, (
        "documented decision codes nothing can emit: %s" % phantom)


def _without_elisions(text):
    """A design doc's JSON sample, made parseable without changing its shape.

    `…` is deliberate in these samples — it says "and more of the same", which is
    the right thing for a doc to say. It is not JSON, so it has to come out before
    the sample can be validated, and it has to come out in a way that leaves the
    CONCRETE records exactly as written: a substitution that turned an elided
    record into an empty one would let the guard grade a record the doc never made
    a claim about."""
    text = re.sub(r"//[^\n]*", "", text)             # jsonc-style asides
    text = text.replace('"…"', '"..."')              # an elided VALUE is still a value
    text = re.sub(r",\s*…", "", text)                # "and more members here"
    text = re.sub(r"\[\s*…\s*\]", '["..."]', text)   # "and more items here"
    text = re.sub(r"\{\s*…\s*\}", '{"...": "..."}', text)
    return text


def test_the_documented_knowledge_payload_is_one_the_writer_would_accept():
    """The drift guard that would have caught idx 34.

    Until now the only thing checked about the documented `knowledge.json` sample
    was its `schema_version` LITERAL. So when v3 began requiring `ref` on every
    record, the sample kept printing a v2-shaped relation under a `"schema_version":
    3` header, and the guard saw a matching `3` and said nothing — while the payload
    it advertises is one `accept_model_meta` rejects outright, storing nothing. A
    reader who copies the documented shape loses every relation *silently*.

    So: run the sample's own records through the real acceptor. Elisions (`…`) are
    deliberate in a design doc, so records that carry one are skipped — but a record
    written out in full is a claim about the contract and has to hold.
    """
    import sys
    sys.path.insert(0, os.path.join(REPO, "src"))
    from backend.kb import accept_model_meta, load_vocab

    doc = _read(os.path.join(REPO, "docs", "design", "output-contract.md"))
    block = re.search(r"## `knowledge\.json`.*?```json\n(.*?)```", doc, re.S)
    assert block, "no knowledge.json sample in output-contract.md"

    payload = json.loads(_without_elisions(block.group(1)))

    assert payload.get("schema_version") == 3, (
        "the sample must show the CURRENT schema version, or the shape below is "
        "being validated against the wrong rules")

    def _elided(rec):
        return "..." in json.dumps(rec)

    concrete = dict((f, [r for r in v if not _elided(r)])
                    for f, v in payload.items() if isinstance(v, list))
    concrete = dict((f, v) for f, v in concrete.items() if v)
    assert concrete, (
        "every record in the documented sample is elided, so the sample makes no "
        "checkable claim about the record shape at all")

    anchors = set()
    for recs in concrete.values():
        for rec in recs:
            ref = rec.get("ref") if isinstance(rec, dict) else None
            if isinstance(ref, str) and ref.startswith("#"):
                anchors.add(ref[1:])

    verdict = accept_model_meta(concrete, load_vocab(), anchors=anchors)
    assert not verdict["rejected"], (
        "the documented knowledge.json sample is a payload the writer REJECTS, so "
        "a producer written against the doc loses these records silently: %s"
        % verdict["rejected"])
    for field, recs in concrete.items():
        assert verdict["accepted"].get(field), (
            "`%s` is written out in full in the doc but nothing was stored for it"
            % field)
