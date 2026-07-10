"""
title: Unit — backend.ingest figure gating + caption inlining
kind: tests
layer: backend
summary: Mirrors src/backend/ingest/_figures.py — gate (deny/area/dedup), caption filter, inline.
"""
import pytest
from backend.ingest import (
    gate_figures, caption_is_useful, image_markdown, inline_image_captions,
    DENY_CLASSES,
    caption_type_is_furniture, caption_cache_key, cache_last_wins,
    figure_sentinel, inline_figures,
)

pytestmark = pytest.mark.unit


# --- gate_figures -----------------------------------------------------------

def _pic(cls=None, area=0.2, sha="h"):
    return {"cls": cls, "area": area, "sha": sha}


def test_gate_keeps_real_figure():
    d = gate_figures([_pic(cls="engineering_drawing", area=0.3, sha="a")])
    assert d[0].keep is True


def test_gate_drops_tiny_mark():
    d = gate_figures([_pic(cls="line_chart", area=0.005, sha="a")])
    assert d[0].keep is False
    assert d[0].reason == "tiny"


def test_gate_drops_deny_class():
    d = gate_figures([_pic(cls="logo", area=0.3, sha="a")])  # below readmit (0.05<area? 0.3>=0.05!)
    # 0.3 >= AREA_READMIT (0.05) so it is RE-ADMITTED; use a small-but-not-tiny logo instead
    d2 = gate_figures([_pic(cls="logo", area=0.03, sha="a")])
    assert d[0].keep is True            # big logo re-admitted (false-deny fix)
    assert d2[0].keep is False          # small logo denied
    assert d2[0].reason.startswith("deny")


def test_gate_dedups_by_sha_in_order():
    d = gate_figures([
        _pic(cls="flow_chart", area=0.3, sha="x"),
        _pic(cls="flow_chart", area=0.3, sha="x"),   # duplicate
        _pic(cls="flow_chart", area=0.3, sha="y"),
    ])
    assert [x.keep for x in d] == [True, False, True]
    assert d[1].reason == "dup"


def test_gate_nonpaginated_area_none_keeps_non_deny():
    # non-PDF (docx/pptx) has no page area -> area=None: tiny rule skipped, deny by class only
    d = gate_figures([
        _pic(cls="engineering_drawing", area=None, sha="a"),
        _pic(cls="icon", area=None, sha="b"),
    ])
    assert d[0].keep is True
    assert d[1].keep is False           # icon denied; can't be re-admitted without area


def test_gate_unclassified_none_class_kept():
    # NONE-class with a real image (has sha, decent area) should pass — let the VLM judge
    d = gate_figures([_pic(cls=None, area=0.2, sha="a")])
    assert d[0].keep is True


def test_deny_classes_membership():
    for c in ("logo", "icon", "stamp", "signature", "qr_code", "bar_code", "page_thumbnail"):
        assert c in DENY_CLASSES


# --- caption_is_useful ------------------------------------------------------

def test_caption_rejects_empty_and_short():
    assert caption_is_useful("") is False
    assert caption_is_useful("   ") is False
    assert caption_is_useful("a timing diagram") is False   # < 25 chars


def test_caption_rejects_degenerate_repetition():
    assert caption_is_useful("the the the the the the the the the the the the") is False


def test_caption_rejects_mostly_nonletters():
    assert caption_is_useful("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16") is False


def test_caption_accepts_real_paragraph():
    cap = ("This is a block diagram of the clock generator showing the PLL feeding "
           "three divider stages into the SoC fabric.")
    assert caption_is_useful(cap) is True


# --- image_markdown ---------------------------------------------------------

def test_image_markdown_builds_link():
    assert image_markdown("a diagram", "assets/doc1/3.png") == "![a diagram](assets/doc1/3.png)"


def test_image_markdown_sanitizes_caption():
    out = image_markdown("line one\nline two ] bracket", "assets/d/1.png")
    assert "\n" not in out
    assert out.startswith("![line one line two \\] bracket](")
    assert out.endswith("(assets/d/1.png)")


# --- inline_image_captions --------------------------------------------------

MD = "Intro.\n\n<!-- image -->\n\nMiddle.\n\n<!-- image -->\n\nEnd.\n"


def test_inline_replaces_in_order():
    out = inline_image_captions(MD, ["![cap A](a.png)", "![cap B](b.png)"])
    assert "![cap A](a.png)" in out
    assert "![cap B](b.png)" in out
    assert "<!-- image -->" not in out
    # order preserved
    assert out.index("cap A") < out.index("cap B")


def test_inline_drops_none_placeholder():
    out = inline_image_captions(MD, [None, "![cap B](b.png)"])
    assert "cap B" in out
    assert "<!-- image -->" not in out          # the dropped one removed too


def test_inline_leaves_extra_placeholders_intact():
    out = inline_image_captions(MD, ["![only one](a.png)"])  # 2 placeholders, 1 render
    assert "![only one](a.png)" in out
    assert out.count("<!-- image -->") == 1       # second placeholder untouched (lossless)


def test_inline_noop_without_placeholders():
    assert inline_image_captions("no images here", ["x"]) == "no images here"
    assert inline_image_captions("", ["x"]) == ""


# --- gate: new signals (ref/chrome, None-safety, formula-safety) ------------

def _rpic(cls=None, area=None, sha="h", ref=None, n_bytes=None, n_docs=None):
    return {"cls": cls, "area": area, "sha": sha, "ref": ref,
            "n_bytes": n_bytes, "n_docs": n_docs}


def test_formula_image_is_never_gated_out():
    # THE INVARIANT: a small (1.5KB), unique, body-placed image with NO page area and
    # recurring across 5 docs (a shared rendered formula) must be KEPT — recurrence and
    # size never drop, and None area must not crash (py3.6 None<0.02 raises).
    d = gate_figures([_rpic(cls=None, area=None, sha="f", ref="body",
                            n_bytes=1500, n_docs=5)])
    assert d[0].keep is True
    assert d[0].reason == "keep"


def test_gate_drops_chrome_placement():
    d = gate_figures([_rpic(cls=None, area=None, sha="logo", ref="chrome",
                            n_bytes=1500, n_docs=48)])
    assert d[0].keep is False
    assert d[0].reason == "chrome"


def test_gate_body_wins_is_caller_resolved_but_body_ref_kept():
    # body-wins is resolved by the extractor into ref='body'; the gate keeps body refs
    d = gate_figures([_rpic(sha="x", ref="body", n_bytes=1500, n_docs=10)])
    assert d[0].keep is True


def test_gate_none_safe_with_missing_new_keys():
    # a picture dict with ONLY the legacy keys still works (new keys absent -> None)
    d = gate_figures([{"cls": None, "area": None, "sha": "a"}])
    assert d[0].keep is True


def test_gate_recurrence_and_small_bytes_alone_never_drop():
    # high n_docs + tiny bytes but body placement + no deny-class -> KEEP (cache handles reuse)
    d = gate_figures([_rpic(sha="s", ref="body", n_bytes=800, n_docs=40)])
    assert d[0].keep is True


# --- caption_type_is_furniture (post-caption filter) ------------------------

def test_caption_type_furniture_detects_logo():
    assert caption_type_is_furniture("This is a company logo showing a stylized letter A.") is True
    assert caption_type_is_furniture("An icon of a gear used for settings.") is True


def test_caption_type_furniture_keeps_formula_and_diagram():
    assert caption_type_is_furniture(
        "This is a mathematical formula: E equals m c squared, the mass-energy relation.") is False
    assert caption_type_is_furniture(
        "A block diagram of the clock generator with a PLL feeding three dividers.") is False
    assert caption_type_is_furniture("") is False


def test_caption_type_furniture_rescues_formula_in_later_sentence_and_plurals():
    # informative type named only in a LATER sentence must still rescue (whole-caption scan)
    assert caption_type_is_furniture(
        "Watermark-style rendering. The Navier-Stokes equation is shown.") is False
    # inflected/plural informative types must rescue (prefix match)
    assert caption_type_is_furniture("Equations rendered beside the company icon.") is False
    assert caption_type_is_furniture("Several diagrams of the pipeline stages.") is False
    # a genuine logo with NO informative type anywhere is still dropped
    assert caption_type_is_furniture("A stylized company logo in blue and white.") is True


# --- caption cache core (pure) ----------------------------------------------

def test_caption_cache_key_deterministic_and_prefixed():
    k1 = caption_cache_key(b"\x89PNG\r\n\x1a\n abc")
    k2 = caption_cache_key(b"\x89PNG\r\n\x1a\n abc")
    assert k1 == k2 and k1.startswith("sha256:")
    assert caption_cache_key(b"different") != k1


def test_cache_last_wins_dedups_by_key():
    recs = [
        {"key": "sha256:a", "caption": "old", "kind": "OK"},
        {"key": "sha256:b", "caption": "b1", "kind": "OK"},
        {"key": "sha256:a", "caption": "new", "kind": "OK"},   # last wins
    ]
    merged = cache_last_wins(recs, "key")
    assert merged["sha256:a"]["caption"] == "new"
    assert set(merged) == {"sha256:a", "sha256:b"}


# --- id-sentinel inlining (idempotent) --------------------------------------

def test_figure_sentinel_and_inline_by_id():
    s0 = figure_sentinel("doc1", 0)
    s1 = figure_sentinel("doc1", 1)
    md = "A\n\n%s\n\nB\n\n%s\n\nC" % (s0, s1)
    fills = {"doc1:0": "![cap zero](assets/doc1/a.png)", "doc1:1": None}  # 1 kept, 1 dropped
    out = inline_figures(md, fills)
    assert "![cap zero](assets/doc1/a.png)" in out
    assert s0 not in out and s1 not in out          # both sentinels consumed
    # idempotent: a second pass with the SAME fills is a no-op (sentinels already gone)
    assert inline_figures(out, fills) == out


def test_inline_figures_leaves_unknown_sentinels_intact():
    md = "x %s y" % figure_sentinel("d", 5)
    assert inline_figures(md, {"d:9": "![z](z.png)"}) == md   # unmatched id untouched


# --- image_markdown extended escaping ---------------------------------------

def test_image_markdown_escapes_link_breakers():
    out = image_markdown("see f(x) | table `code` here", "assets/d/1.png")
    # ')' and '|' and backtick must not break the link / a surrounding table
    assert "](assets/d/1.png)" in out
    assert "\n" not in out
    for bad in ("|", "`"):
        assert bad not in out.split("](")[0]        # none survive in the alt text


# --- OOXML office image sentinels (deterministic extraction) ----------------
from backend.ingest import (                                       # noqa: E402
    ooxml_image_sentinel, ooxml_image_parts, inline_ooxml_images, plan_office_images,
)


def test_ooxml_sentinel_roundtrip_preserves_order_and_dups():
    s = ooxml_image_sentinel
    md = "a %s b %s c %s" % (s("word/media/i1.png"), s("word/media/i2.emf"),
                             s("word/media/i1.png"))
    assert ooxml_image_parts(md) == [
        "word/media/i1.png", "word/media/i2.emf", "word/media/i1.png"]
    assert ooxml_image_parts("no images here") == []


def test_plan_office_images_content_addresses_and_dedups():
    parts = ["ppt/media/i1.png", "ppt/media/i2.png", "ppt/media/i1.png"]  # i1 reused
    media = {"ppt/media/i1.png": b"AAA", "ppt/media/i2.png": b"BBB"}
    plan = plan_office_images(parts, media)
    assert plan.n_referenced == 3          # three occurrences
    assert plan.n_resolved == 3            # all backed by bytes
    assert plan.n_files == 2               # reused image stored ONCE
    assert plan.n_missing == 0
    # identical-byte images would collapse to one file; distinct bytes -> distinct files
    assert len({f for f, _ in plan.assets}) == 2
    # every occurrence of the reused part links to the SAME file
    body = inline_ooxml_images("x%sy%sz%s" % tuple(ooxml_image_sentinel(p) for p in parts),
                               plan.fills)
    links = [ln for ln in body.split("![](")[1:]]
    assert links[0].split(")")[0] == links[2].split(")")[0]   # i1 occurrences match


def test_plan_office_images_identical_bytes_collapse_to_one_file():
    parts = ["word/media/a.png", "word/media/b.png"]           # different parts...
    media = {"word/media/a.png": b"SAME", "word/media/b.png": b"SAME"}  # ...same bytes
    plan = plan_office_images(parts, media)
    assert plan.n_files == 1               # content-addressed: one physical file
    assert plan.n_resolved == 2


def test_plan_office_images_missing_bytes_are_dropped_not_silent():
    parts = ["word/media/here.png", "word/media/gone.png"]
    plan = plan_office_images(parts, {"word/media/here.png": b"X"})
    assert plan.n_missing == 1 and plan.n_resolved == 1
    assert plan.fills["word/media/gone.png"] is None
    md = "p%sq%sr" % (ooxml_image_sentinel(parts[0]), ooxml_image_sentinel(parts[1]))
    out = inline_ooxml_images(md, plan.fills)
    assert "gone.png" not in out and "here.png" not in out    # sentinel gone; link is sha-named
    assert out.count("![](") == 1                              # only the resolved one


def test_inline_ooxml_images_is_recall_safe_and_idempotent():
    from backend.ingest import markdown_to_text
    part = "word/media/i.png"
    md = "The core runs fast. %s More text." % ooxml_image_sentinel(part)
    # the sentinel is an HTML comment -> contributes NOTHING to extracted text
    assert markdown_to_text(md) == markdown_to_text("The core runs fast.  More text.")
    plan = plan_office_images([part], {part: b"IMG"})
    out = inline_ooxml_images(md, plan.fills)
    assert inline_ooxml_images(out, plan.fills) == out         # idempotent
    # an empty-alt image link also adds no text tokens (recall unaffected either way)
    assert markdown_to_text(out).replace("  ", " ") == markdown_to_text(md).replace("  ", " ")


def test_inline_ooxml_images_leaves_unknown_part_intact():
    md = "x %s y" % ooxml_image_sentinel("word/media/i.png")
    assert inline_ooxml_images(md, {"word/media/other.png": "![](images/z.png)"}) == md
