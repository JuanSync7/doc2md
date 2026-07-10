"""
title: Integration — build_bundle emits the doc2md output bundle end to end
kind: tests
layer: backend
summary: build_bundle reads real office zips and writes document.md + structure.json + report.json per doc.
"""
# Integration (not unit): writes real OOXML zips + bundle files to disk.
import importlib.util
import json
import os
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
CORE = ('<cp:coreProperties xmlns:cp="x" xmlns:dc="y"><dc:title>%s</dc:title>'
        '<dc:creator>%s</dc:creator></cp:coreProperties>')


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _docx(path):
    doc = ('<w:document %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Radar Overview</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>The transceiver runs at 77 GHz.</w:t></w:r></w:p>'
           '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
           '<w:r><w:t>Channels</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Four transmit channels are available.</w:t></w:r></w:p>'
           '</w:body></w:document>' % W)
    styles = ('<w:styles %s>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
              '</w:styles>' % W)
    _zip(path, {"word/document.xml": doc, "word/styles.xml": styles,
                "docProps/core.xml": CORE % ("Radar Spec", "A. Engineer")})


def _xlsx(path):
    wb = ('<workbook %s %s><sheets><sheet name="Rates" sheetId="1" r:id="rId1"/>'
          '</sheets></workbook>' % (S, R))
    rels = ('<Relationships %s><Relationship Id="rId1" '
            'Target="worksheets/sheet1.xml"/></Relationships>' % RELS)
    sst = '<sst %s><si><t>Component</t></si><si><t>FIT</t></si></sst>' % S
    sheet = ('<worksheet %s><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>PLL</t></is></c>'
             '<c r="B2"><v>3.2</v></c></row></sheetData></worksheet>' % S)
    _zip(path, {"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": rels,
                "xl/sharedStrings.xml": sst, "xl/worksheets/sheet1.xml": sheet})


def _bundle_dirs(out):
    return sorted(d for d in os.listdir(out)
                  if os.path.isdir(os.path.join(out, d)))


def test_build_bundle_end_to_end(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "srcdocs"
    out = tmp_path / "bundles"
    src.mkdir()
    _docx(str(src / "radar spec.docx"))
    _xlsx(str(src / "fmeda rates.xlsx"))

    rc = bb.main(["--src", str(src), "--out", str(out), "--run-id", "RUN1"])
    assert rc == 0

    dirs = _bundle_dirs(str(out))
    assert len(dirs) == 2
    for did in dirs:
        d = os.path.join(str(out), did)
        assert os.path.isfile(os.path.join(d, "document.md"))
        assert os.path.isdir(os.path.join(d, "images"))
        rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
        st = json.load(open(os.path.join(d, "structure.json"), encoding="utf-8"))
        # lossless office gate
        assert rep["status"] == "ok"
        assert rep["losslessness"]["method"] == "ooxml-ground-truth"
        assert rep["losslessness"]["token_recall"] == 1.0
        assert rep["losslessness"]["gate"] == "pass"
        assert rep["doc_id"] == did == st["doc_id"]
        assert rep["source_sha256"] and len(rep["markdown_sha256"]) == 64
        assert rep["timing_ms"].get("convert") is not None
        # structure has a real outline with token counts
        assert st["outline"] and "total_tokens" in st
        assert all("subtree_tokens" in n for n in st["outline"])
        # document.md carries the mapping front matter, then the body
        md = open(os.path.join(d, "document.md"), encoding="utf-8").read()
        assert md.startswith("---\n")
        assert ('doc_id: "%s"' % did) in md
        assert 'lossless: "true"' in md
        assert 'generated_run: "RUN1"' in md

    # the docx bundle proves headings nest and source metadata survives
    blob = "".join(open(os.path.join(str(out), d, "document.md"), encoding="utf-8").read()
                   for d in dirs)
    assert "# Radar Overview" in blob and "## Channels" in blob
    assert 'source_title: "Radar Spec"' in blob
    assert 'source_author: "A. Engineer"' in blob

    # manifest has a row per doc
    manifest = [json.loads(l) for l in open(os.path.join(str(out), "manifest.jsonl"))]
    assert len(manifest) == 2 and all(m["status"] == "ok" for m in manifest)

    # idempotent: a second run rebuilds nothing (both bundles already ok)
    rc2 = bb.main(["--src", str(src), "--out", str(out)])
    assert rc2 == 0
    manifest2 = [json.loads(l) for l in open(os.path.join(str(out), "manifest.jsonl"))]
    assert len(manifest2) == 2                              # no new manifest rows appended


def test_docx_structure_outline_nests_by_level(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    assert bb.main(["--src", str(src), "--out", str(out)]) == 0
    did = _bundle_dirs(str(out))[0]
    st = json.load(open(os.path.join(str(out), did, "structure.json"), encoding="utf-8"))
    top = st["outline"][0]
    assert top["title"] == "Radar Overview" and top["level"] == 1
    assert [c["title"] for c in top["children"]] == ["Channels"]
    assert top["subtree_tokens"] >= top["self_tokens"]


def test_failed_conversion_records_failed_report_only(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    # a .docx that is not a valid zip -> unreadable, must FAIL (never a vacuous pass)
    (src / "broken.docx").write_bytes(b"PK\x03\x04 not a real zip")
    rc = bb.main(["--src", str(src), "--out", str(out)])
    assert rc == 1
    did = _bundle_dirs(str(out))[0]
    d = os.path.join(str(out), did)
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["status"] == "failed"
    assert rep["losslessness"]["gate"] == "fail"
    assert rep["losslessness"]["error"] == "unreadable-zip"
    # a failed doc writes NO document.md (there is no lossless markdown to publish)
    assert not os.path.isfile(os.path.join(d, "document.md"))


def test_malformed_content_part_fails_and_writes_no_document(tmp_path):
    # A valid zip whose word/document.xml is present but NOT well-formed XML: the symmetry
    # hole guard must FAIL it (both converter and ground truth would drop it to empty and
    # sail through recall==1.0 otherwise), and no document.md may be published.
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    _zip(str(src / "corrupt.docx"),
         {"word/document.xml": "<w:document><w:body><w:p>unterminated",   # invalid XML
          "word/styles.xml": ("<w:styles %s/>" % W)})
    rc = bb.main(["--src", str(src), "--out", str(out)])
    assert rc == 1
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["status"] == "failed"
    assert rep["losslessness"]["error"].startswith("malformed-content-part")
    assert not os.path.isfile(os.path.join(d, "document.md"))


def test_empty_source_is_vacuously_lossless(tmp_path):
    # A 0-byte upload has nothing to lose -> a stub, vacuously-lossless OK bundle (so the
    # lane never loops on empty shells). This pins that intended behavior.
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    (src / "empty.docx").write_bytes(b"")
    rc = bb.main(["--src", str(src), "--out", str(out)])
    assert rc == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["status"] == "ok"
    assert os.path.isfile(os.path.join(d, "document.md"))
    assert "empty source" in open(os.path.join(d, "document.md"), encoding="utf-8").read()


def test_force_rebuild_is_deterministic(tmp_path):
    # Same input -> byte-identical output. markdown_sha256 and document.md must not drift
    # across a --force rebuild (the sha is the cache key; drift is a regression).
    bb = _mod("build_bundle")
    src = tmp_path / "s"
    out = tmp_path / "o"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R1"]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    sha1 = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))["markdown_sha256"]
    doc1 = open(os.path.join(d, "document.md"), encoding="utf-8").read()

    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R1", "--force"]) == 0
    sha2 = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))["markdown_sha256"]
    doc2 = open(os.path.join(d, "document.md"), encoding="utf-8").read()
    assert sha1 == sha2 and doc1 == doc2


# --- deterministic image extraction end to end ------------------------------
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PIC = ('xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"')
IMG_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
# a real 1x1 PNG and a real 1x1 GIF (distinct bytes -> distinct content hashes)
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da6360000002000001e221bc330000000049454e44ae426082")
GIF_1x1 = bytes.fromhex("4749463839610100010080000000000000ffffff21f90401000000002c000000000100010000020144003b")


def _draw(rid):
    return ('<w:p><w:r><w:drawing %s %s><wp:inline><a:graphic><a:graphicData>'
            '<pic:pic><pic:blipFill><a:blip r:embed="%s"/></pic:blipFill></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
            % (A, PIC, rid))


def _docx_images(path, rels_triples, media):
    # rels_triples: [(rId, target, type)]; media: {part: bytes}
    body = "".join(_draw(rid) for rid, _, _ in rels_triples)
    doc = ('<w:document %s %s><w:body>'
           '<w:p><w:r><w:t>Figure follows.</w:t></w:r></w:p>%s'
           '</w:body></w:document>'
           % (W, R, body))
    rels = ('<Relationships %s>%s</Relationships>'
            % (RELS, "".join('<Relationship Id="%s" Target="%s" Type="%s"/>' % t
                             for t in rels_triples)))
    members = {"word/document.xml": doc,
               "word/_rels/document.xml.rels": rels,
               "docProps/core.xml": CORE % ("Imgs", "Author")}
    members.update(media)
    _zip(path, members)


def test_images_extracted_linked_and_join_is_bijective(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "figs.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE),
                  ("rId2", "media/image2.gif", IMG_TYPE)],
                 {"word/media/image1.png": PNG_1x1, "word/media/image2.gif": GIF_1x1})
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["losslessness"]["token_recall"] == 1.0        # images never move the gate
    im = rep["images"]
    assert im["referenced"] == 2
    assert im["extracted"] == 2
    assert im["unique_files"] == 2
    assert im["orphans"] == 0
    assert im["verified"] == 2                                # both files content-verified on disk
    assert im["gate"] == "pass" and rep["status"] == "ok"
    # every ![](images/X) resolves to a real file, and every file is referenced
    import re
    md = open(os.path.join(d, "document.md"), encoding="utf-8").read()
    refs = set(re.findall(r"!\[[^\]]*\]\(images/([^)]+)\)", md))
    files = set(os.listdir(os.path.join(d, "images")))
    assert refs == files and len(files) == 2
    # the stored bytes are the source bytes (content-addressed, decodable)
    for f in files:
        b = open(os.path.join(d, "images", f), "rb").read()
        assert b in (PNG_1x1, GIF_1x1)
    # structure.json carries the image on a section node, caption pending
    st = json.load(open(os.path.join(d, "structure.json"), encoding="utf-8"))
    imgs = [im for n in st["outline"] for im in n["images"]]
    assert imgs and all(im["caption"] is None and im["alt"] == "" for im in imgs)
    assert all(im["ref"].startswith("images/") for im in imgs)


def test_reused_image_is_stored_once(tmp_path):
    # same picture referenced twice -> two links, ONE content-addressed file (dedup)
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "dup.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE),
                  ("rId2", "media/image1.png", IMG_TYPE)],   # same target, twice
                 {"word/media/image1.png": PNG_1x1})
    assert bb.main(["--src", str(src), "--out", str(out)]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["images"]["referenced"] == 2
    assert rep["images"]["unique_files"] == 1                # deduped to one file
    assert rep["images"]["orphans"] == 0
    assert rep["images"]["verified"] == 1 and rep["images"]["gate"] == "pass"
    assert len(os.listdir(os.path.join(d, "images"))) == 1


def test_missing_image_bytes_are_flagged_not_silent(tmp_path):
    # a picture whose media part is absent from the package: no broken link, no silent
    # drop -- the sentinel is removed and an image_bytes_missing warning is recorded.
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "gap.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE),
                  ("rId2", "media/ghost.png", IMG_TYPE)],    # ghost.png not written
                 {"word/media/image1.png": PNG_1x1})
    assert bb.main(["--src", str(src), "--out", str(out)]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["images"]["extracted"] == 1
    assert rep["images"]["missing"] == 1
    # a missing picture is a real loss the text gate cannot see -> image gate degrades,
    # status degrades, but losslessness stays a clean pass (the text is whole)
    assert rep["images"]["gate"] == "degraded"
    assert rep["status"] == "degraded" and rep["losslessness"]["gate"] == "pass"
    assert any(w.get("code") == "image_bytes_missing" for w in rep["warnings"])
    md = open(os.path.join(d, "document.md"), encoding="utf-8").read()
    assert "ghost" not in md and "ooxml-image" not in md     # no broken ref, no leaked sentinel
    assert len(os.listdir(os.path.join(d, "images"))) == 1


def test_orphan_image_files_are_gc_removed_on_rebuild(tmp_path):
    # a stale content-addressed file left by an earlier build must be swept on rebuild,
    # so images/ stays a faithful mirror of the body's references (recorded, never silent)
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "figs.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE)],
                 {"word/media/image1.png": PNG_1x1})
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    orphan = os.path.join(d, "images", "0123456789abcdef.png")   # unreferenced, CA-named
    with open(orphan, "wb") as f:
        f.write(PNG_1x1)
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R", "--force"]) == 0
    assert not os.path.exists(orphan)
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["images"]["orphans"] == 0
    assert any(w.get("code") == "orphan_images_removed" for w in rep["warnings"])


def test_captions_are_preserved_across_force_rebuild(tmp_path):
    # a --force rebuild must NOT destroy captions already written for UNCHANGED images
    # (they carry over by image_id); the report's caption block reflects the carry.
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "figs.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE)],
                 {"word/media/image1.png": PNG_1x1})
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    sp = os.path.join(d, "structure.json")
    st = json.load(open(sp, encoding="utf-8"))
    imgs = [im for n in st["outline"] for im in n["images"]]
    imgs[0]["caption"] = "A detailed block diagram of the pipeline."   # as the caption pass would
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R", "--force"]) == 0
    st2 = json.load(open(sp, encoding="utf-8"))
    imgs2 = [im for n in st2["outline"] for im in n["images"]]
    assert imgs2[0]["caption"] == "A detailed block diagram of the pipeline."
    rep = json.load(open(os.path.join(d, "report.json"), encoding="utf-8"))
    assert rep["captions"]["captioned"] == 1        # report reflects the carried caption


def test_image_filenames_are_deterministic_across_force_rebuild(tmp_path):
    bb = _mod("build_bundle")
    src = tmp_path / "s"; out = tmp_path / "o"; src.mkdir()
    _docx_images(str(src / "figs.docx"),
                 [("rId1", "media/image1.png", IMG_TYPE)],
                 {"word/media/image1.png": PNG_1x1})
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R"]) == 0
    d = os.path.join(str(out), _bundle_dirs(str(out))[0])
    files1 = sorted(os.listdir(os.path.join(d, "images")))
    assert bb.main(["--src", str(src), "--out", str(out), "--run-id", "R", "--force"]) == 0
    files2 = sorted(os.listdir(os.path.join(d, "images")))
    assert files1 == files2 and len(files1) == 1             # content-addressed = stable
