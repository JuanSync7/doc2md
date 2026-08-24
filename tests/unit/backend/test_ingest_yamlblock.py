"""
title: Unit — backend.ingest YAML subset codec
kind: tests
layer: backend
summary: render/parse round-trip the nested metadata block, hold the no-bare-fence and no-control-char invariants, and refuse everything outside the subset.
"""
# Pure string policy — no disk, no PyYAML (the 3.6 CI ring has pytest and nothing
# else, which is the whole reason this codec is hand-rolled).
import itertools
import re
from collections import OrderedDict

import pytest

from backend.ingest import (YamlSubsetError, front_matter, parse_block,
                            render_block, render_front_matter,
                            split_front_matter)

pytestmark = pytest.mark.unit


def od(*pairs):
    """An OrderedDict from pairs — equality against another OrderedDict is
    order-SENSITIVE, so round-trip assertions also assert key order."""
    return OrderedDict(pairs)


# Control characters validate_markdown rejects as a hard `bad-chars` ERROR.
_CONTROL = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")


def _metadata_block():
    """The shape the real document-metadata block has: every container the
    vocabulary layer emits, including records and the `links` map-of-lists-of-maps."""
    return od(
        ("doc_id", "d1"),
        ("title", "InterCPU Comms TRM"),
        ("word_count", 24),
        ("confidence", 0.75),
        ("lossless", True),
        ("draft", False),
        ("owner", None),
        ("tags", ["spec", "cpu"]),
        ("aliases", []),
        ("qualifiers", od()),
        ("provenance", od(("tier", 0), ("method", "deterministic"))),
        ("entities", [od(("name", "AXI"), ("tier", 2)),
                      od(("name", "SoC"), ("tier", 1))]),
        ("links", od(
            ("supersedes", [od(("doc_id", "d2"), ("confidence", 0.5))]),
            ("mitigates", [od(("doc_id", "d3"), ("negated", False))]),
        )),
    )


def test_the_nested_metadata_block_round_trips_through_render_and_parse():
    block = _metadata_block()
    assert parse_block(render_block(block)) == block


def test_render_never_emits_a_bare_document_marker_even_when_a_value_is_one():
    # INVARIANT 1. Three separate front-matter strippers find the closing fence by
    # scanning for a line that is exactly `---`. One of those anywhere inside the
    # block truncates every document, so every string is quoted.
    block = od(("sep", "---"), ("end", "..."), ("items", ["---", "...", "- x"]))
    rendered = render_block(block)
    lines = rendered.split("\n")
    assert [ln for ln in lines if ln.strip() in ("---", "...")] == []
    assert 'sep: "---"' in rendered
    assert parse_block(rendered) == block
    # The fence writer is the same emitter, so the only bare `---` are the fences.
    fenced = render_front_matter(block)
    assert fenced.startswith("---\n") and fenced.endswith("\n---\n")
    assert [ln for ln in fenced.split("\n") if ln == "---"] == ["---", "---"]


def test_unsafe_mapping_keys_are_refused_rather_than_escaped():
    # Keys are rendered RAW, so they are the one place a newline or a colon could
    # still smuggle a `---` line past invariant 1. They are validated, not escaped.
    for bad in ("bad key", "a:b", "a\n---\nb", "3leading", "-dash"):
        with pytest.raises(YamlSubsetError):
            render_block(od((bad, 1)))


def test_no_raw_control_character_ever_reaches_the_rendered_block():
    # INVARIANT 2. validate_markdown raises a hard `bad-chars` ERROR for these even
    # inside front matter, so they are escaped here instead of emitted and then
    # failing the gate.
    value = u"a\x00b\x0bc\x0cd\x1fe\ufffdf"
    block = od(("ctl", value), ("items", [value]))
    rendered = render_block(block)
    assert _CONTROL.search(rendered) is None
    assert "\\x00" in rendered and "\\uFFFD" in rendered
    assert parse_block(rendered) == block
    assert parse_block(rendered)["ctl"] == value


@pytest.mark.parametrize("text", ["no", "yes", "on", "off", "true", "false",
                                  "0644", "1.0", "null", "~", "12"])
def test_yaml_1_1_lookalike_strings_stay_strings_and_render_quoted(text):
    # The whole reason the renderer quotes every string: a YAML 1.1 reader retypes
    # bare no/yes/on/off and 0644, and an enum value whose type depends on the
    # reader is exactly the drift the vocabulary layer exists to prevent.
    rendered = render_block(od(("v", text)))
    assert rendered == 'v: "%s"\n' % text
    back = parse_block(rendered)["v"]
    assert back == text
    assert isinstance(back, str)


def test_front_matter_is_byte_identical_to_the_legacy_flat_renderer():
    # test_bundle_assemble.py asserts the literal bytes 'doc_id: "d1"'; the flat
    # str->str mapping is everything both converter lanes pass, and delegating to
    # the block codec must not have moved a single byte.
    assert front_matter(od(("doc_id", "d1"))) == '---\ndoc_id: "d1"\n---\n'
    assert front_matter(od(("doc_id", "d1"), ("title", "T"))) == (
        '---\ndoc_id: "d1"\ntitle: "T"\n---\n')
    assert front_matter(od(("title", 'He said "hi"\nbye'))) == (
        '---\ntitle: "He said \\"hi\\"\\nbye"\n---\n')
    assert front_matter({}) == ""
    assert front_matter(None) == ""
    assert render_block({}) == "" and render_front_matter({}) == ""


def test_native_types_render_natively_instead_of_being_stringified():
    # A deliberate change from the legacy renderer, which quoted everything: an int
    # that comes back as the string "24" cannot be compared, summed or gated on.
    rendered = render_block(od(("word_count", 24), ("ratio", 0.5),
                               ("lossless", True), ("draft", False),
                               ("owner", None), ("neg", -3)))
    assert rendered == ("word_count: 24\nratio: 0.5\nlossless: true\n"
                        "draft: false\nowner: null\nneg: -3\n")
    back = parse_block(rendered)
    assert back["word_count"] == 24 and isinstance(back["word_count"], int)
    assert back["lossless"] is True and back["draft"] is False
    assert back["owner"] is None


def test_split_front_matter_returns_exactly_the_bytes_the_body_hash_covers():
    # assemble_bundle builds `front_matter(fm) + "\n" + body_md` and hashes body_md
    # alone; splitting must drop that ONE separator newline and nothing more, or the
    # markdown_sha256 recorded on a re-written document stops matching.
    meta = od(("doc_id", "d1"), ("word_count", 3), ("tags", ["a", "b"]))
    body = "\n# Heading\n\nText.\n"
    document = render_front_matter(meta) + "\n" + body
    got_meta, got_body = split_front_matter(document)
    assert got_meta == meta
    assert got_body == body                       # leading blank line preserved
    assert render_front_matter(got_meta) + "\n" + got_body == document


def test_split_front_matter_leaves_a_document_without_front_matter_untouched():
    plain = "# Just a document\n\n---\n\nnot front matter\n"
    assert split_front_matter(plain) == (OrderedDict(), plain)
    assert split_front_matter("---\na: 1\nnever closed\n") == (
        OrderedDict(), "---\na: 1\nnever closed\n")
    assert split_front_matter("") == (OrderedDict(), "")
    assert split_front_matter(None) == (OrderedDict(), "")


@pytest.mark.parametrize("label,text", [
    ("tab indentation", "a: 1\n\tb: 2\n"),
    ("document marker ---", "a: 1\n---\nb: 2\n"),
    ("document marker ...", "a: 1\n...\n"),
    ("anchor", "a: &anchor 1\n"),
    ("alias", "a: *anchor\n"),
    ("tag", "a: !!str hi\n"),
    ("flow mapping with content", "a: {b: 1}\n"),
    ("unterminated double quote", 'a: "oops\n'),
    ("unterminated single quote", "a: 'oops\n"),
    ("duplicate key", "a: 1\na: 2\n"),
    ("duplicate key inside a record", "s:\n  - k: 1\n    k: 2\n"),
    ("unterminated flow sequence", "a: [1, 2\n"),
    ("trailing content after a flow sequence", "a: [1, 2] junk\n"),
    ("nested flow collection", "a: [1, [2]]\n"),
    # `[a: b]` is the brace-less spelling of the `{a: b}` two rows up. It used to
    # come back as the plain STRING "a: b" while PyYAML returns {'a': 'b'} — a
    # silent misparse of a construct this subset already says it rejects.
    ("implicit flow mapping inside a flow sequence", "a: [k: v, x]\n"),
    ("implicit flow mapping with an empty value", "a: [k:]\n"),
    ("implicit flow mapping with a quoted key", 'a: ["k": v]\n'),
    ("key with no space after the colon", "a:1\n"),
    ("sequence where a mapping was expected", "- 1\n"),
])
def test_parse_block_refuses_everything_outside_the_subset(label, text):
    # A metadata codec that silently misreads is worse than one that refuses: the
    # vocabulary layer's only promise is that what you read back is what was written.
    with pytest.raises(YamlSubsetError):
        parse_block(text)


def test_comments_are_dropped_but_a_hash_inside_a_url_or_a_quote_is_not_one():
    # config/vocab.yaml carries `prov: http://www.w3.org/ns/prov#` — a `#` that is
    # not preceded by whitespace is part of the value, not a comment.
    text = ("# whole-line comment\n"
            "doc_id: d1   # trailing comment\n"
            "prov: http://www.w3.org/ns/prov#\n"
            "quoted: \"a # b\"\n"
            "\n"
            "  # indented comment\n"
            "tail: 1\n")
    assert parse_block(text) == od(("doc_id", "d1"),
                                   ("prov", "http://www.w3.org/ns/prov#"),
                                   ("quoted", "a # b"),
                                   ("tail", 1))
    assert parse_block("") == OrderedDict()
    assert parse_block("\n\n# nothing but comments\n") == OrderedDict()


def test_flow_sequences_parse_including_ones_wrapped_across_several_lines():
    # An authored enum list wraps; refusing that would push config/vocab.yaml into a
    # shape nobody wants to hand-edit.
    text = ("tags: [spec, cpu]\n"
            "values: [runs_on, installed_via,\n"
            "         requires, governs,\n"
            "         mitigates]\n"
            "quoted: [\"a, b\", 'c']\n"
            "empty: []\n"
            "typed: [1, true, null, 0.5]\n")
    assert parse_block(text) == od(
        ("tags", ["spec", "cpu"]),
        ("values", ["runs_on", "installed_via", "requires", "governs", "mitigates"]),
        ("quoted", ["a, b", "c"]),
        ("empty", []),
        ("typed", [1, True, None, 0.5]),
    )


def test_block_scalars_parse_as_mapping_values_and_as_sequence_items():
    text = ("rationale: |\n"
            "  line one\n"
            "  line two\n"
            "folded: >\n"
            "  aaa\n"
            "  bbb\n"
            "\n"
            "  ccc\n"
            "clipped: |-\n"
            "  no trailing newline\n"
            "rules:\n"
            "  - |\n"
            "    literal item\n"
            "  - >\n"
            "    folded\n"
            "    item\n"
            "tail: 1\n")
    got = parse_block(text)
    assert got["rationale"] == "line one\nline two\n"
    assert got["folded"] == "aaa bbb\nccc\n"        # blank line -> a real newline
    assert got["clipped"] == "no trailing newline"  # `-` chomps
    assert got["rules"] == ["literal item\n", "folded item\n"]
    assert got["tail"] == 1                         # the block ends at the dedent


def test_escapes_round_trip_exactly_and_carriage_return_is_not_folded():
    # The legacy renderer folded \r into \n, which silently rewrote the stored value.
    # A codec that claims to round-trip must not edit the data it holds.
    value = 'quote " backslash \\ newline \n tab \t cr \r end'
    rendered = render_block(od(("v", value)))
    assert rendered.count("\n") == 1                # still exactly one line
    assert "\\r" in rendered and "\\n" in rendered
    assert parse_block(rendered)["v"] == value
    assert parse_block('v: "a\\r\\nb"')["v"] == "a\r\nb"


@pytest.mark.parametrize("label,text", [
    # What the shaved value USED to be, before the guard, is in the comment.
    ("literal block, one space short",
     "r: |\n    alpha\n   bravo\n   charlie\ntail: 1\n"),        # 'alpha\nravo\nharlie\n'
    ("literal block, a short line deleted outright",
     "r: |\n    alpha\n  hi\n  omega\n"),                        # 'alpha\n\nega\n'
    ("folded block, a manufactured paragraph break",
     "r: >\n    alpha\n   bravo\n"),                             # 'alpha ravo\n'
    ("a nested mapping key swallowed into the block",
     "a:\n  b: |\n      text\n    c: 1\n"),                      # b == 'text\n 1\n'
    ("a `- |` sequence item",
     "l:\n  - |\n      alpha\n     bravo\n"),                    # ['alpha\nravo\n']
])
def test_a_block_scalar_refuses_a_line_less_indented_than_its_own_first_line(label, text):
    # A one-space indentation slip in a hand-maintained file (config/vocab.yaml is
    # the only thing that reaches a block scalar — the renderer never emits `|`/`>`)
    # used to be sliced BLIND: `line[base:]` shaves the first characters off an
    # under-indented line, and `else ""` deletes a line shorter than `base`
    # outright — which inside a folded block manufactures a paragraph break that
    # splits the author's sentence in two. The corruption then republishes verbatim
    # into the generated docs/reference/vocabulary.md, and the staleness gate
    # cannot see it because it regenerates from the same corrupted parse.
    # PyYAML raises a ParserError on every one of these; so do we now.
    with pytest.raises(YamlSubsetError) as exc:
        parse_block(text)
    assert "less indented" in str(exc.value)
    assert re.search(r"line \d+", str(exc.value))     # the message names the line


def test_a_block_scalar_still_keeps_blank_and_more_indented_lines():
    # The other direction: the guard must not start rejecting correct blocks. Blank
    # lines are legitimately less indented (or empty), and a MORE indented line is
    # meaningful content in a literal block — only the folded style refuses it, and
    # for its own separate, already-documented reason.
    got = parse_block("r: |\n"
                      "    alpha\n"
                      "\n"                     # truly empty
                      "    beta\n"
                      "  \n"                   # whitespace-only, shallower than base
                      "      deeper\n"
                      "    gamma\n"
                      "tail: 1\n")
    assert got["r"] == "alpha\n\nbeta\n\n  deeper\ngamma\n"
    assert got["tail"] == 1
    assert parse_block("f: >\n  one two\n\n  three\n")["f"] == "one two\nthree\n"
    assert parse_block("l:\n  - |\n      a\n\n      b\n  - 2\n")["l"] == ["a\n\nb\n", 2]


def test_an_apostrophe_inside_a_plain_scalar_does_not_open_a_quote():
    # `_scalar_value` has always treated a quote as OPENING a quoted scalar only in
    # first position; `_strip_comment`, `_flow_depth` and `_split_flow` used to open
    # one at ANY offset, so they misread the same bytes the parser then read
    # correctly. An apostrophe is ordinary prose — "the operator's guide" — and
    # PyYAML reads every line below the way this asserts.
    #
    # 1. the line's own trailing comment was absorbed into the value
    assert parse_block("note: the writer's default # tuned 2026-05\n") == od(
        ("note", "the writer's default"))
    assert parse_block("rules:\n  - the doc's id must resolve  # added\n") == od(
        ("rules", ["the doc's id must resolve"]))
    # 2. an even number of apostrophes across a comma merged two flow items into one
    assert parse_block("values: [don't, isn't]\n") == od(
        ("values", ["don't", "isn't"]))
    # 3. an odd number hid the closing `]`, so `_gather_flow` ran to EOF and raised
    #    "unterminated flow sequence" on a sequence that is perfectly terminated
    assert parse_block("authors: [O'Neill, Smith]\n") == od(
        ("authors", ["O'Neill", "Smith"]))
    assert parse_block('q: [a, don"t]\n') == od(("q", ["a", 'don"t']))
    # and the control: the same lines without the apostrophe always worked
    assert parse_block("note: the writer default # tuned\n") == od(
        ("note", "the writer default"))


def test_a_quote_still_opens_a_quoted_scalar_at_every_token_start():
    # The dangerous half of the fix. `_strip_comment` sees whole raw lines with no
    # structural knowledge, so "only at column 0" would stop recognising the opening
    # quote of `title: "a # b"` and truncate every MACHINE-RENDERED value at its
    # interior `#` — silent data loss on the one path that works today. Token start
    # means: start of line, or after `[`, `,`, a block-sequence `- `, or a `key: `.
    assert parse_block('q: "a # b"\n') == od(("q", "a # b"))
    assert parse_block("q: 'a # b'\n") == od(("q", "a # b"))
    assert parse_block('l:\n  - "a # b"\n') == od(("l", ["a # b"]))
    assert parse_block('l:\n  - k: "a # b"\n') == od(("l", [od(("k", "a # b"))]))
    assert parse_block("q: ['a, b', \"c, d\"]  # note\n") == od(
        ("q", ["a, b", "c, d"]))
    assert parse_block('q: ["a]b", c]\n') == od(("q", ["a]b", "c"]))
    # a colon NOT followed by a space is an ordinary character, so a bare URL —
    # config/vocab.yaml authors `prov: http://www.w3.org/ns/prov#` — survives the
    # implicit-flow-mapping refusal, and so does a quoted one carrying a space.
    assert parse_block("q: [http://x/ns#, a]\n") == od(("q", ["http://x/ns#", "a"]))
    assert parse_block('q: ["a: b", c]\n') == od(("q", ["a: b", "c"]))
    # `''` is YAML's escaped apostrophe inside a single-quoted scalar, so the `#`
    # here is part of the value and the scalar does not end at the middle quote.
    assert parse_block("q: 'it''s # not a comment'\n") == od(("q", "it's # not a comment"))
    # a still-open quote in a flow sequence is refused, not guessed at
    with pytest.raises(YamlSubsetError):
        parse_block('q: ["a, b]\n')


_FUZZ_ALPHABET = ["a", "'", '"', "#", "[", "]", ",", ":", " ", "\\", "-", "{",
                  "}", "\n", "\t", "|", ">", "*", "&", "!"]


def test_render_then_parse_round_trips_every_adversarial_three_character_value():
    # The property the whole codec rests on, over the alphabet that breaks it:
    # quotes, comment markers, flow punctuation, block-scalar headers, anchors,
    # tags, and the line-breaking whitespace. 8000 values in three container
    # positions each — a scalar, a sequence item and a nested mapping value —
    # because the three scanners are reached by different call paths.
    for combo in itertools.product(_FUZZ_ALPHABET, repeat=3):
        value = "".join(combo)
        block = od(("v", value), ("l", [value, "x" + value]),
                   ("m", od(("k", value))))
        assert parse_block(render_block(block)) == block, repr(value)


def test_a_vocab_shaped_document_parses_as_authored():
    # Mirrors the constructs config/vocab.yaml actually uses (inline, because a unit
    # test may not touch disk): bare-URL scalars, a folded rationale, a wrapped flow
    # list, nested maps and a typed qualifier block.
    text = ("version: 1\n"
            "standards:\n"
            "  dcterms: http://purl.org/dc/terms/\n"
            "  local: doc2md\n"
            "lint:\n"
            "  facet_warn: 0.45\n"
            "  promote_at: 3\n"
            "relation_predicates:\n"
            "  governance: closed\n"
            "  rationale: >\n"
            "    Edge types are what queries traverse.\n"
            "    Keep this list small.\n"
            "  values: [runs_on, requires,\n"
            "           mitigates]\n"
            "  qualifiers:\n"
            "    negated:\n"
            "      type: bool\n"
            "      default: false\n")
    got = parse_block(text)
    assert got["version"] == 1
    assert got["standards"]["dcterms"] == "http://purl.org/dc/terms/"
    assert got["lint"] == od(("facet_warn", 0.45), ("promote_at", 3))
    assert got["relation_predicates"]["governance"] == "closed"
    assert got["relation_predicates"]["rationale"] == (
        "Edge types are what queries traverse. Keep this list small.\n")
    assert got["relation_predicates"]["values"] == ["runs_on", "requires", "mitigates"]
    assert got["relation_predicates"]["qualifiers"]["negated"]["default"] is False
