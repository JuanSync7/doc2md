"""
title: Unit — content coverage metric (the lossless-ness instrument)
kind: tests
layer: backend
summary: tokenize + coverage() measure what fraction of source content survived into a target text.
"""
# Pure policy, no disk/network. This is the MEASUREMENT TOOL; it has its own tests
# (separately from the conversion features it validates).
from backend.ingest import (tokenize, coverage, CoverageReport, is_lossy,
                            is_lossy_explained, char_ngram_recall, html_to_text,
                            strip_running_lines, words_in_bbox, explain_gap)


def test_words_in_bbox_selects_by_fractional_center():
    # words + box in [0,1] page fractions; a word is "in" if its CENTER falls inside
    words = [("inside", 0.20, 0.20, 0.30, 0.24),      # center (0.25,0.22) -> in
             ("outside", 0.80, 0.80, 0.90, 0.84),     # center (0.85,0.82) -> out
             ("edge", 0.10, 0.10, 0.50, 0.50)]        # center (0.30,0.30) -> in
    box = (0.15, 0.15, 0.55, 0.55)
    assert words_in_bbox(words, box) == ["inside", "edge"]


def test_words_in_bbox_empty_and_none():
    assert words_in_bbox([], (0, 0, 1, 1)) == []
    assert words_in_bbox([("a", 0.5, 0.5, 0.6, 0.6)], (0.0, 0.0, 0.1, 0.1)) == []


def test_strip_running_lines_removes_repeated_headers():
    # 3 pages, each with the same header+footer and unique body; \f = page break
    hdr = "IEEE Std 802.1Q-2018"
    ftr = "Local and Metropolitan Area Networks"
    pages = ["%s\nbody one alpha\n%s" % (hdr, ftr),
             "%s\nbody two beta\n%s" % (hdr, ftr),
             "%s\nbody three gamma\n%s" % (hdr, ftr)]
    out = strip_running_lines("\f".join(pages), min_frac=0.5)
    assert hdr not in out and ftr not in out       # boilerplate gone
    assert "body one alpha" in out                 # unique body kept
    assert "body two beta" in out and "body three gamma" in out


def test_strip_running_lines_keeps_unique_and_single_page():
    # page numbers differ per page -> not repeated -> kept
    pages = ["header\ncontent A\n1", "header\ncontent B\n2", "header\ncontent C\n3"]
    out = strip_running_lines("\f".join(pages), min_frac=0.5)
    assert "header" not in out                      # repeated -> removed
    assert "1" in out and "2" in out and "3" in out  # page numbers unique -> kept
    # single page (no form-feed) -> nothing removed
    assert strip_running_lines("header\nbody\nheader", min_frac=0.5) == "header\nbody\nheader"
    assert strip_running_lines("", min_frac=0.5) == ""


def test_strip_running_lines_min_frac_threshold():
    # header on 2 of 4 pages (0.5); with min_frac 0.75 it stays, with 0.5 it goes
    pages = ["H\na", "H\nb", "x\nc", "y\nd"]
    keep = strip_running_lines("\f".join(pages), min_frac=0.75)
    assert "H" in keep                              # 0.5 < 0.75 -> not boilerplate
    drop = strip_running_lines("\f".join(pages), min_frac=0.5)
    assert "\nH" not in ("\n" + drop.replace("\f", "\n"))  # 0.5 >= 0.5 -> removed


def test_tokenize_normalizes_and_keeps_alnum():
    assert tokenize("Hello, World!") == ["hello", "world"]
    assert tokenize("Reg 0x1F set") == ["reg", "0x1f", "set"]
    assert tokenize("") == []
    assert tokenize("   \n\t  ") == []


def test_identical_text_is_full_coverage():
    r = coverage("the quick brown fox", "the quick brown fox")
    assert isinstance(r, CoverageReport)
    assert r.recall == 1.0
    assert r.n_source == 4
    assert r.n_covered == 4
    assert r.n_missing == 0
    assert r.missing_top == []


def test_target_superset_is_full_recall():
    # extra words in the target do not reduce recall (we measure source survival)
    r = coverage("alpha beta", "alpha beta gamma delta extra words")
    assert r.recall == 1.0
    assert r.n_missing == 0


def test_half_missing_is_half_recall():
    r = coverage("alpha beta gamma delta", "alpha beta")
    assert r.recall == 0.5
    assert r.n_source == 4
    assert r.n_covered == 2
    assert r.n_missing == 2


def test_coverage_is_multiset_aware():
    # source has "reg" x3, target only once -> the two dropped copies count as missing
    r = coverage("reg reg reg done", "reg done")
    assert r.n_source == 4
    assert r.n_covered == 2          # min(3,1)=1 for reg + 1 for done
    assert r.recall == 0.5


def test_empty_source_is_full_coverage():
    r = coverage("", "anything here")
    assert r.recall == 1.0
    assert r.n_source == 0
    assert r.n_missing == 0


def test_missing_top_reports_dropped_tokens_by_count():
    # the whole "notes" block (3x notes, 1x important) was dropped from the target
    src = "intro slide notes notes notes important point"
    tgt = "intro slide point"
    r = coverage(src, tgt)
    miss = dict(r.missing_top)
    assert miss.get("notes") == 3
    assert miss.get("important") == 1
    # ordered by missing count descending
    assert r.missing_top[0] == ("notes", 3)


def test_markdown_noise_is_caller_stripped_not_metric_concern():
    # the metric compares plain text; markdown stripping is the caller's job
    # (so a raw markdown target undercounts on purpose -> proves it is literal)
    r = coverage("title body", "# title\n\nbody")
    # "#" is not alnum so it drops out of tokenize; "title" and "body" still match
    assert r.recall == 1.0


# --- variation / robustness -------------------------------------------------

def test_tokenize_unicode_letters_and_cjk():
    # non-ASCII letters are NOT alnum under [a-z0-9]; they collapse away. Digits/latin stay.
    assert tokenize("café 温度 sensor 42") == ["caf", "sensor", "42"]


def test_coverage_case_insensitive():
    r = coverage("Vref VDD Clock", "vref vdd clock")
    assert r.recall == 1.0


def test_coverage_none_and_whitespace_inputs():
    assert coverage(None, "x").recall == 1.0        # empty source -> vacuously covered
    assert coverage("a b", None).n_covered == 0     # nothing in target
    assert coverage("   \n\t ", "x").recall == 1.0  # whitespace-only source == empty


def test_coverage_huge_repetition_is_counted():
    r = coverage("reg " * 1000, "reg " * 400)
    assert r.n_source == 1000
    assert r.n_covered == 400
    assert abs(r.recall - 0.4) < 1e-9


def test_coverage_exclude_subtracts_boilerplate_from_source():
    # 'header' appears twice in source (per-page boilerplate) but is excluded (docling
    # furniture) -> not counted against docling, which correctly dropped it
    src = "header alpha beta header gamma"     # 'header' on 2 pages (per-page boilerplate)
    tgt = "alpha beta gamma"                    # docling BODY (no header)
    assert coverage(src, tgt).recall < 1.0      # naive: header missing -> docked
    # docling furniture carries the header once per page, so exclude matches its count
    r = coverage(src, tgt, exclude="header header")
    assert r.recall == 1.0
    assert r.n_source == 3                       # both header occurrences subtracted


def test_coverage_exclude_is_multiset_not_overzealous():
    # excluding 'x' once removes only one occurrence; a real 'x' in body still counts
    src = "x x keep"                          # one x is boilerplate, one is content
    r = coverage(src, "keep", exclude="x")
    assert r.n_source == 2                    # x(1 left) + keep
    assert r.n_missing == 1                   # the remaining content x is genuinely missing


def test_is_lossy_predicate():
    # low recall + enough tokens -> lossy
    assert is_lossy(coverage("a b c d e f g h i j", ""), min_recall=0.8, min_tokens=5) is True
    # high recall -> not lossy
    assert is_lossy(coverage("a b c d", "a b c d"), min_recall=0.8, min_tokens=1) is False
    # too few source tokens -> never flagged (noise floor), even at recall 0
    assert is_lossy(coverage("a b", ""), min_recall=0.8, min_tokens=50) is False


def test_missing_top_is_bounded_and_ordered():
    # 50 distinct dropped tokens -> missing_top caps at 12, all count 1, alnum-sorted
    src = " ".join("tok%02d" % i for i in range(50))
    r = coverage(src, "")
    assert len(r.missing_top) == 12
    assert all(c == 1 for _, c in r.missing_top)
    toks = [t for t, _ in r.missing_top]
    assert toks == sorted(toks)                     # ties broken by token, stable


# --- html_to_text: script/style bodies must NOT inflate the baseline --------

def test_html_to_text_drops_script_and_style_bodies():
    html = ("<html><head><style>.a{color:red;font-size:12px}</style>"
            "<script>var secret_token = compute(1,2,3); doThing();</script></head>"
            "<body><h1>Register Map</h1><p>address offset width</p></body></html>")
    txt = html_to_text(html)
    toks = set(tokenize(txt))
    assert {"register", "map", "address", "offset", "width"} <= toks   # visible text kept
    assert "secret_token" not in txt and "compute" not in toks         # script body gone
    assert "color" not in toks and "font" not in toks                  # style body gone


def test_html_to_text_strips_comments_and_unescapes_entities():
    html = "<!-- hidden note --><p>Vref &amp; VDD &lt;=</p>"
    txt = html_to_text(html)
    assert "hidden" not in txt                     # comment gone
    assert "&amp;" not in txt and "&" in txt       # entity unescaped
    assert set(tokenize(txt)) == {"vref", "vdd"}


def test_html_to_text_empty_is_passthrough():
    assert html_to_text("") == ""
    assert html_to_text(None) is None


def test_html_baseline_no_longer_looks_lossy_after_fix():
    # a faithful conversion of an HTML doc whose source is >50% script/style
    src_html = ("<style>" + "x{margin:0}" * 40 + "</style>"
                "<body><p>alpha beta gamma delta epsilon</p></body>")
    md = "alpha beta gamma delta epsilon"          # docling emits only the visible text
    naive = coverage(src_html, md)                 # tag-strip only would keep CSS tokens
    fixed = coverage(html_to_text(src_html), md)
    assert naive.recall < 0.8                       # CSS inflates source -> looks lossy
    assert fixed.recall == 1.0                      # after removing style body -> faithful


# --- char_ngram_recall: content-presence, blind to tokenization -------------

def test_char_ngram_recall_ignores_hyphenation_and_spacing():
    # same letters, different tokenization/hyphenation/line-wrap -> full content recall
    assert char_ngram_recall("inter-face controller", "interface controller") == 1.0
    assert char_ngram_recall("power\nmanagement unit", "power management unit") == 1.0


def test_char_ngram_recall_falls_when_content_is_dropped():
    src = "the quick brown fox jumps over the lazy dog"
    assert char_ngram_recall(src, "the quick brown fox") < 0.7   # half the letters gone
    assert char_ngram_recall(src, "") == 0.0


def test_char_ngram_recall_short_source_is_vacuous():
    assert char_ngram_recall("ab", "zzz") == 1.0     # fewer than n(=3) alnum chars


# --- is_lossy_explained: BOTH signals must agree before flagging ------------

def test_is_lossy_explained_high_content_recall_is_not_lossy():
    # token recall low (re-tokenized) but content present -> explained gap, NOT lossy
    rep = coverage("inter face bus", "interfacebus", )   # token recall 0 (fused)
    assert rep.recall < 0.8
    content = char_ngram_recall("inter face bus", "interfacebus")
    assert content >= 0.95
    assert is_lossy_explained(rep, content, min_recall=0.8, min_tokens=1,
                              content_min=0.95) is False


def test_is_lossy_explained_low_both_is_lossy():
    src = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    rep = coverage(src, "alpha beta")                    # real content dropped
    content = char_ngram_recall(src, "alpha beta")
    assert rep.recall < 0.8 and content < 0.95
    assert is_lossy_explained(rep, content, min_recall=0.8, min_tokens=5,
                              content_min=0.95) is True


# --- explain_gap: decompose the missing mass into explained vs truly absent -

def test_explain_gap_full_coverage_has_no_gap():
    g = explain_gap("alpha beta gamma", "alpha beta gamma")
    assert g.n_source == 3 and g.covered == 3
    assert g.fused == g.numeric == g.residual_boiler == g.short == g.absent == 0
    assert g.absent_top == []


def test_explain_gap_classifies_fused_tokens_as_explained():
    # source "inter face" was emitted fused as "interface" -> chars present, not lost
    g = explain_gap("inter face controller", "interface controller")
    assert g.covered == 1                    # controller
    assert g.fused == 2                      # inter + face found inside 'interface'...
    # (inter/face are 5/4 chars -> substring check applies)
    assert g.absent == 0


def test_explain_gap_numeric_and_short_buckets():
    # dropped page number (42) and a stray unit letter (v) are explained-benign buckets
    g = explain_gap("42 v signal", "signal")
    assert g.numeric == 1
    assert g.short == 1
    assert g.absent == 0


def test_explain_gap_residual_boiler_subthreshold_lines():
    # 'draft copy' recurs on 2 of 5 pages (0.4 < 0.5 threshold) -> kept by strip, but
    # its loss in the target is residual boilerplate, not content loss
    pages = ["draft copy\nbody one", "draft copy\nbody two",
             "body three", "body four", "body five"]
    g = explain_gap("\f".join(pages), "body one body two body three body four body five")
    assert g.residual_boiler == 4            # draft x2 + copy x2
    assert g.absent == 0


def test_explain_gap_varying_page_footers_are_boiler_not_absent():
    # "Page N of M" differs per page (digits vary) so plain repetition misses it, but
    # the DIGIT-MASKED form recurs -> boilerplate-explained, not content loss
    pages = ["Page 1 of 3\nintro words", "Page 2 of 3\nmiddle words", "Page 3 of 3\nend words"]
    g = explain_gap("\f".join(pages), "intro words middle words end words")
    assert g.absent == 0
    assert g.residual_boiler == 6            # 'page' x3 + 'of' x3 (boiler claims short too)
    assert g.numeric == 6                    # the page digits
    assert (g.covered + g.fused + g.numeric + g.residual_boiler + g.short + g.absent
            == g.n_source)


def test_explain_gap_truly_absent_is_flagged_with_tokens():
    src = "the frobnicator subsystem handles quantum flux calibration"
    g = explain_gap(src, "the subsystem handles calibration")
    assert g.absent >= 3
    absent = dict(g.absent_top)
    assert "frobnicator" in absent and "quantum" in absent and "flux" in absent


def test_explain_gap_buckets_partition_the_source_exactly():
    # covered + all buckets == n_source, always (nothing double-counted or dropped)
    src = "42 inter face draft unique1 unique2 x " * 3
    g = explain_gap(src, "interface something else")
    assert (g.covered + g.fused + g.numeric + g.residual_boiler + g.short + g.absent
            == g.n_source)


def test_explain_gap_fused_is_multiset_bounded():
    # 'face' missing x3 but the stream only holds one extra fused copy -> only 1 fused,
    # the other 2 are genuinely absent
    g = explain_gap("face face face flag", "interface flag")
    assert g.fused == 1
    assert g.absent == 2


def test_explain_gap_empty_inputs():
    g = explain_gap("", "anything")
    assert g.n_source == 0 and g.absent == 0
    g2 = explain_gap("alpha beta gamma delta", "")
    assert g2.absent == 4                       # all >=4-char tokens, nothing explains them


def test_is_lossy_explained_respects_token_floor_and_min_tokens():
    # high token recall -> never lossy regardless of content signal
    rep = coverage("a b c d", "a b c d")
    assert is_lossy_explained(rep, 0.0, min_recall=0.8, min_tokens=1) is False
    # tiny source -> never flagged (noise floor)
    small = coverage("a b", "")
    assert is_lossy_explained(small, 0.0, min_recall=0.8, min_tokens=50) is False


def test_explain_gap_fused_evidence_only_from_surplus_tokens():
    # 'face' is missing; the only 'interface' in the target is COVERED source content,
    # so it must NOT double as fusion evidence for 'face' -> truly absent
    g = explain_gap("interface face", "interface")
    assert g.fused == 0
    assert g.absent == 1


def test_explain_gap_image_text_bucket_claims_figure_labels():
    from backend.ingest import merge_boxes
    # diagram labels docling rendered as <!-- image -->; the caller proved they sit
    # inside a figure region -> explained as image_text, not absent
    src = "body prose here gate1 clkdiv xtal"
    tgt = "body prose here"
    g = explain_gap(src, tgt, image_text="gate1 clkdiv xtal")
    assert g.image_text == 3
    assert g.absent == 0
    # image evidence is multiset-bounded: only one 'gate1' can be claimed
    g2 = explain_gap("gate1 gate1 body", "body", image_text="gate1")
    assert g2.image_text == 1
    assert g2.absent == 1
    # partition still exact with the new bucket
    assert (g2.covered + g2.fused + g2.numeric + g2.image_text + g2.residual_boiler
            + g2.short + g2.absent == g2.n_source)
    # merge_boxes: overlapping boxes chain into one cluster with a count
    clusters = merge_boxes([(0.1, 0.1, 0.2, 0.2), (0.19, 0.1, 0.3, 0.2),
                            (0.29, 0.1, 0.4, 0.2), (0.7, 0.7, 0.8, 0.8)], pad=0.01)
    assert len(clusters) == 2
    counts = sorted(n for _, n in clusters)
    assert counts == [1, 3]
    big = [c for c, n in clusters if n == 3][0]
    assert big == (0.1, 0.1, 0.4, 0.2)
