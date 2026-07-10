"""
title: Integration — the OOXML lane converts real zips losslessly, end to end
kind: tests
layer: backend
summary: office_convert reads real .docx/.pptx/.xlsx zips, writes gated markdown, is idempotent.
"""
# Integration (not unit): writes real OOXML zips + markdown to disk.
import importlib.util
import json
import os
import zipfile

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mod(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
CORE = ('<cp:coreProperties xmlns:cp="x" xmlns:dc="y"><dc:title>%s</dc:title>'
        '</cp:coreProperties>')


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _docx(path):
    doc = ('<w:document %s><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           '<w:r><w:t>Radar Overview</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>The transceiver runs at 77 GHz.</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Channel</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>Bandwidth</w:t></w:r></w:p></w:tc></w:tr>'
           '<w:tr><w:tc><w:p><w:r><w:t>TX0</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>4 GHz</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
           '</w:body></w:document>' % W)
    styles = ('<w:styles %s><w:style w:type="paragraph" w:styleId="Heading1">'
              '<w:name w:val="heading 1"/></w:style></w:styles>' % W)
    _zip(path, {"word/document.xml": doc, "word/styles.xml": styles,
                "docProps/core.xml": CORE % "Radar Spec"})


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


def test_office_lane_end_to_end(tmp_path):
    oc = _mod("office_convert")
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    _docx(str(src / "radar spec.docx"))
    _xlsx(str(src / "fmeda rates.xlsx"))

    rc = oc.main(["--src", str(src), "--out", str(out)])
    assert rc == 0
    mds = [f for f in os.listdir(str(out)) if f.endswith(".md")]
    assert len(mds) == 2

    recs = [json.loads(line) for line in open(str(out / "_coverage_ooxml.jsonl"))]
    assert len(recs) == 2 and all(r["valid"] and r["recall"] == 1.0 for r in recs)

    blob = "".join(open(str(out / f), encoding="utf-8").read() for f in mds)
    assert 'title: "Radar Spec"' in blob            # front matter
    assert "# Radar Overview" in blob               # heading style
    assert "| Channel | Bandwidth |" in blob        # GFM table w/ separator
    assert "| --- | --- |" in blob
    assert "## Rates" in blob                       # sheet section
    assert "| PLL | 3.2 |" in blob

    # Idempotent: second run skips everything (valid record + md present).
    rc2 = oc.main(["--src", str(src), "--out", str(out)])
    assert rc2 == 0
    recs2 = [json.loads(line) for line in open(str(out / "_coverage_ooxml.jsonl"))]
    assert len(recs2) == 2                          # no new records appended

    # validate-only re-gates existing markdown without rewriting it.
    before = dict((f, os.path.getmtime(str(out / f))) for f in mds)
    rc3 = oc.main(["--src", str(src), "--out", str(out), "--validate-only"])
    assert rc3 == 0
    assert before == dict((f, os.path.getmtime(str(out / f))) for f in mds)


def test_validate_markdown_tree_flags_lossy_office(tmp_path):
    oc = _mod("office_convert")
    vm = _mod("validate_markdown")
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    out.mkdir()
    _docx(str(src / "radar spec.docx"))
    rc = oc.main(["--src", str(src), "--out", str(out)])
    assert rc == 0
    assert vm.main(["--md-dir", str(out), "--src", str(src)]) == 0

    # Damage the markdown (drop the table) -> the validator must catch the loss.
    md_file = [f for f in os.listdir(str(out)) if f.endswith(".md")][0]
    p = str(out / md_file)
    body = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(body.split("| Channel")[0])
    assert vm.main(["--md-dir", str(out), "--src", str(src)]) == 1


def test_docling_convert_declines_office(tmp_path):
    """Office is owned by the OOXML lane — docling never plans a docx/pptx/xlsx.
    One format, one owner; there is no office-through-docling path to override."""
    import contextlib
    import io
    dc = _mod("docling_convert")
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    # dry-run needs no docling; the office doc is not even a docling source
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dc.main(["--src", str(src), "--out", str(out), "--dry-run"])
    assert rc == 0
    assert "spec.docx" not in buf.getvalue()


def test_accept_list_restricts_and_unsupported_is_reported(tmp_path, capsys):
    """--accept restricts which formats convert; unsupported/other-lane files are
    reported (not silently dropped) so the operator knows what was skipped."""
    oc = _mod("office_convert")
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    _docx(str(src / "spec.docx"))
    _xlsx(str(src / "rates.xlsx"))
    (src / "paper.pdf").write_bytes(b"%PDF-1.4 stub")     # docling lane
    (src / "blob.bin").write_bytes(b"\x00\x01\x02")       # unsupported

    # accept only docx -> xlsx AND pdf are declined by the accept-list; bin unsupported
    rc = oc.main(["--src", str(src), "--out", str(out), "--accept", "docx"])
    assert rc == 0
    mds = [f for f in os.listdir(str(out)) if f.endswith(".md")]
    assert len(mds) == 1                                  # only the docx converted
    err = capsys.readouterr().err
    assert "excluded by the accept-list" in err and "xlsx" in err and "pdf" in err
    assert "UNSUPPORTED" in err and "bin" in err

    # default accept (all supported): pdf is NOTED as belonging to the docling lane
    # (it is converted there, not "not converted"), and only unsupported bin warns.
    oc.main(["--src", str(src), "--out", str(out), "--force"])
    err2 = capsys.readouterr().err
    assert "docling lane" in err2 and "pdf" in err2
    assert "UNSUPPORTED" in err2 and "bin" in err2


def test_libreoffice_declines_cleanly_when_soffice_absent(tmp_path, monkeypatch):
    """An ODF doc with no soffice fails with a clear reason (never a silent skip,
    never a vacuous pass)."""
    oc = _mod("office_convert")
    monkeypatch.setattr(oc, "find_soffice", lambda: "")   # force "not available"
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    (src / "legacy.odt").write_bytes(b"PK\x03\x04 not really an odt")
    rc = oc.main(["--src", str(src), "--out", str(out)])
    assert rc == 1                                        # a doc failed
    recs = [json.loads(line) for line in open(str(out / "_coverage_ooxml.jsonl"))]
    assert recs[-1]["error"] == "libreoffice-unavailable"
    assert recs[-1]["valid"] is False


@pytest.mark.skipif(_mod("office_convert").find_soffice() == "",
                    reason="LibreOffice (soffice) not available")
def test_libreoffice_odf_routes_through_ooxml(tmp_path):
    """A real .odt is soffice-converted to its OOXML sibling and then travels the
    single OOXML lane to gated, lossless markdown."""
    oc = _mod("office_convert")
    soffice = oc.find_soffice()
    src = tmp_path / "srcdocs"
    out = tmp_path / "md"
    src.mkdir()
    out.mkdir()
    # seed a real .odt by converting a .txt with the same soffice
    seed = tmp_path / "seed.txt"
    seed.write_text("Mailbox controller overview. Eight channels at 200 MHz.\n")
    produced = oc.soffice_to_ooxml(soffice, str(seed), "odt")
    assert produced, "soffice failed to produce a seed .odt"
    import shutil
    shutil.copy(produced, str(src / "spec.odt"))
    shutil.rmtree(os.path.dirname(produced), ignore_errors=True)

    rc = oc.main(["--src", str(src), "--out", str(out)])
    assert rc == 0
    mds = [f for f in os.listdir(str(out)) if f.endswith(".md")]
    assert len(mds) == 1
    body = open(str(out / mds[0]), encoding="utf-8").read()
    assert "Mailbox controller overview" in body and "Eight channels" in body
    recs = [json.loads(line) for line in open(str(out / "_coverage_ooxml.jsonl"))]
    assert recs[-1]["valid"] is True and recs[-1]["recall"] == 1.0
    assert recs[-1]["ext"] == "odt"                       # record keeps the ORIGINAL format


# --- vendored LibreOffice discovery + packaging (setup_libreoffice.py) --------

def test_find_soffice_precedence_env_then_vendored_then_path(tmp_path, monkeypatch):
    oc = _mod("office_convert")
    def mkexe(name):
        p = tmp_path / name
        p.write_text("#!/bin/sh\n")
        os.chmod(str(p), 0o755)
        return str(p)
    env_exe, vend_exe = mkexe("env_soffice"), mkexe("vendored_soffice")
    monkeypatch.setattr(oc, "vendored_soffice", lambda: vend_exe)
    monkeypatch.setattr(oc.shutil, "which", lambda n: "/usr/bin/path_soffice")
    # 1) explicit env override wins over everything
    monkeypatch.setenv("DOC2MD_LIBREOFFICE", env_exe)
    assert oc.find_soffice() == env_exe
    # 2) no env -> the vendored-in-tree soffice wins over a PATH install
    monkeypatch.delenv("DOC2MD_LIBREOFFICE", raising=False)
    assert oc.find_soffice() == vend_exe
    # 3) no env, nothing vendored -> fall back to PATH
    monkeypatch.setattr(oc, "vendored_soffice", lambda: str(tmp_path / "absent"))
    assert oc.find_soffice() == "/usr/bin/path_soffice"


def test_vendored_soffice_path_is_derived_from_repo_root():
    oc = _mod("office_convert")
    p = oc.vendored_soffice()
    # Derived from __file__ (relocates with the tree), NOT a baked-in absolute path.
    assert p == os.path.join(REPO, "vendor", "libreoffice", "bin", "soffice")


def test_setup_wrapper_template_is_relocatable():
    su = _mod("setup_libreoffice")
    sh = su._WRAPPER_SH
    assert 'dirname "$0"' in sh                    # paths derive from the launcher's location
    assert "/scratch" not in sh
    assert su._REPO not in sh                      # no absolute repo path baked into the wrapper


def test_setup_is_installed_reflects_vendored_tree(tmp_path, monkeypatch):
    su = _mod("setup_libreoffice")
    monkeypatch.setattr(su, "VENDOR", str(tmp_path))
    monkeypatch.setattr(su, "WRAPPER", str(tmp_path / "bin" / "soffice"))
    assert su.is_installed() is False             # nothing there yet
    os.makedirs(str(tmp_path / "bin"))
    w = tmp_path / "bin" / "soffice"
    w.write_text("#!/bin/sh\n")
    os.chmod(str(w), 0o755)
    prog = tmp_path / su._PROG_REL
    os.makedirs(str(prog))
    (prog / "soffice").write_text("x")
    assert su.is_installed() is True              # wrapper + target binary present
