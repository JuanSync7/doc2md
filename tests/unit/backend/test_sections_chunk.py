"""
title: Unit — backend.sections chunk_sections
kind: tests
layer: backend
summary: Size-driven, heading-anchored chunking — bounded section counts, stable ids, content fingerprints.
"""
import pytest
from backend.sections import chunk_sections, normalize_title, is_heading

pytestmark = pytest.mark.unit

TARGET = 16000   # mirror SECTION_TARGET for sizing test inputs


def _blocks(n, heading_text="content tokens here", reps=900):
    """n heading-delimited blocks, each big enough (~25k chars) to be its own section."""
    return "\n".join("# Block %d\n" % i + (heading_text + " ") * reps for i in range(n))


def test_small_doc_is_a_single_section():
    # Size floor: a tiny doc is NOT fragmented even though it has two headings.
    secs = chunk_sections("d", "# A\nshort body here\n\n# B\nmore short body here\n")
    assert len(secs) == 1


def test_many_small_headings_do_not_explode_into_many_sections():
    # THE regression: 1000 heading-like lines must NOT yield ~1000 sections. With the
    # size floor they merge into a handful (~ total_chars / SECTION_TARGET).
    text = "\n".join("## H%d\nsome body text for section %d here" % (i, i) for i in range(1000))
    secs = chunk_sections("d", text)
    assert len(secs) <= (len(text) // TARGET) + 5     # bounded by SIZE, not heading count
    assert len(secs) < 50                              # nowhere near 1000


def test_large_doc_section_count_is_size_bounded():
    text = _blocks(5)                                  # ~125k chars
    secs = chunk_sections("d", text)
    assert len(secs) <= (len(text) // TARGET) + 6
    assert len(secs) >= 3


def test_section_ids_stable_across_identical_rebuild():
    text = _blocks(4)
    a = chunk_sections("d", text)
    b = chunk_sections("d", text)
    assert [s.section_id for s in a] == [s.section_id for s in b]
    assert all(s.fingerprint for s in a)


def test_fingerprint_changes_on_content_change_only():
    a = chunk_sections("d", "# H\n" + ("data alpha " * 700))   # < SUBSPLIT -> 1 section
    b = chunk_sections("d", "# H\n" + ("data gamma " * 700))    # same length, diff content
    assert len(a) == 1 and len(b) == 1
    assert a[0].section_id == b[0].section_id                   # same opening title -> same id
    assert a[0].fingerprint != b[0].fingerprint


def test_fingerprint_ignores_markdown_formatting():
    plain = chunk_sections("d", "# H\n" + ("the CHI protocol matters " * 200))
    bold = chunk_sections("d", "# H\n" + ("the **CHI** protocol matters " * 200))
    assert plain[0].fingerprint == bold[0].fingerprint          # strip is format-agnostic


def test_oversized_section_window_split_into_children():
    # ~15k chars: one size-driven span (under SECTION_TARGET) but over SUBSPLIT -> windowed.
    big = "# H\n" + ("x" * 100 + "\n") * 150
    secs = chunk_sections("d", big)
    assert len(secs) >= 2
    assert all(".w" in s.section_id and s.level == 2 for s in secs)


def _big_table(rows, header="| Bits | Name | Access | Description |"):
    sep = "|------|------|--------|-------------|"
    body = "\n".join("| %d | FIELD_%d | R/W | description of field %d here padded out |"
                     % (i, i, i) for i in range(rows))
    return "# Register Map\n" + header + "\n" + sep + "\n" + body + "\n"


def test_large_table_splits_and_every_chunk_keeps_the_header():
    # A table far larger than SECTION_TARGET must split into multiple chunks, and EVERY
    # chunk's materialized body must carry the header row (prefix) — not just the first.
    text = _big_table(4000)                      # ~ > 16k chars, no internal headings
    secs = chunk_sections("d", text)
    assert len(secs) >= 2                        # actually split
    bodies = [s.prefix + "\n".join(text.split("\n")[s.l0:s.l1]) for s in secs]
    for body in bodies:
        assert "| Bits | Name | Access | Description |" in body
    # the continuation chunks rely on prefix (their raw span does NOT contain the header)
    continuation = [s for s in secs if s.prefix]
    assert continuation, "expected at least one chunk to open mid-table and need a prefix"
    for s in continuation:
        raw = "\n".join(text.split("\n")[s.l0:s.l1])
        assert "| Bits | Name | Access | Description |" not in raw   # header only via prefix


def test_prose_chunks_have_no_table_prefix():
    secs = chunk_sections("d", _blocks(4))       # heading-delimited prose, no tables
    assert all(s.prefix == "" for s in secs)


# ── tokenizer-pluggable sizing ────────────────────────────────────────────────
# A fake tokenizer: ~1 token per 4 chars, so token budgets bite at ~1/4 the text a
# char budget would need. Deterministic and dependency-free (mirrors a real ~4:1 BPE).
def _toks(s):
    return max(1, len(s) // 4)


def test_token_count_omitted_is_char_identical():
    # The default path must be byte-for-byte unchanged: same spans with/without the
    # explicit None. (Guards the refactor — token plumbing must not perturb char mode.)
    text = _blocks(4)
    a = chunk_sections("d", text)
    b = chunk_sections("d", text, token_count=None)
    assert [s._asdict() for s in a] == [s._asdict() for s in b]


def test_token_mode_sections_respect_token_budget():
    # Build a doc whose TOKEN size forces multiple sections. ~4000 tok target; at 4:1
    # that's ~16k chars/section, so ~80k chars of prose -> several sections.
    text = "\n".join("# Block %d\n" % i + ("word " * 4000) for i in range(4))
    secs = chunk_sections("d", text, token_count=_toks)
    total_tok = sum(_toks(ln) for ln in text.split("\n"))
    assert len(secs) >= 3
    assert len(secs) <= (total_tok // 4000) + 6      # bounded by TOKEN size, not chars


def test_token_mode_windows_children_are_token_bounded():
    # A heading whose body far exceeds the section budget window-splits into several
    # level-2 children. Contract (asserted via the public API, no private constants):
    # the children tile the parent exactly — contiguous, non-overlapping, no loss — and
    # none is the whole document, i.e. the oversized section was genuinely bounded.
    big = "# H\n" + "\n".join(("token " * 40) for _ in range(400))   # ~16k tokens
    secs = chunk_sections("d", big, token_count=_toks)
    lines = big.split("\n")
    assert len(secs) >= 3
    assert all(s.level == 2 and ".w" in s.section_id for s in secs)
    child_toks = [sum(_toks(lines[i]) for i in range(s.l0, s.l1)) for s in secs]
    total = sum(_toks(lines[i]) for i in range(secs[0].l0, secs[-1].l1))
    assert max(child_toks) < total                    # split happened; no child is the whole
    assert sum(child_toks) == total                   # tiles the parent with no loss/overlap
    spans = [(s.l0, s.l1) for s in secs]
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        assert b1 == a2                               # contiguous: no gap, no overlap


def test_token_mode_ids_and_fingerprints_stable():
    text = _blocks(3)
    a = chunk_sections("d", text, token_count=_toks)
    b = chunk_sections("d", text, token_count=_toks)
    assert [s.section_id for s in a] == [s.section_id for s in b]
    assert all(s.fingerprint for s in a)


def test_normalize_title_canonicalizes_forms():
    assert normalize_title("## Background") == "background"
    assert normalize_title("BACKGROUND") == "background"
    assert normalize_title("1.2 Background") == "background"


def test_is_heading_levels():
    assert is_heading("# Title") == 1
    assert is_heading("### Deep") == 3
    assert is_heading("1.2.3 Something Here") == 3
    assert is_heading("just a normal sentence that goes on for a while") == 0
