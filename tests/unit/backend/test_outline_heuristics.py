"""
title: Unit — the heading heuristics are bounded
kind: tests
layer: backend
summary: A shouted body sentence cannot become a heading, and a real ALL-CAPS heading still is one.
"""
# quality-plan.md C6/P4.6. `is_heading` promotes an ALL-CAPS line, which is right for
# un-marked-up native text and catastrophic for a runbook callout: the callout becomes
# a LEVEL-1 heading, so every section printed after it is reparented under "DO NOT
# REBOOT THE PRIMARY NODE" — and both gates stay green, because every word is still
# present. The tests below pin both directions: the callout stays body text, the
# legitimate shouted headings keep working.
import pytest

from backend.sections import document_outline, is_heading

pytestmark = pytest.mark.unit

CALLOUT = "DO NOT REBOOT THE PRIMARY NODE"

# Shouted body text. Each is rejected by shape, not by a list of known callouts:
# a clause word (determiner/pronoun/auxiliary/modal/negation), terminal punctuation,
# or plain length.
NOT_HEADINGS = [
    CALLOUT,
    "THIS PROCEDURE MUST BE RUN AS ROOT",
    "NEVER POWER DOWN DURING A FLASH WRITE",
    "ALWAYS CHECK THE LOCK BIT FIRST",
    "ESCALATE TO THE ON-CALL LEAD IMMEDIATELY",
    "CHECK THE FANS.",                                  # terminal full stop
    "WARNING, THE HEATSINK IS HOT",                     # terminal-punctuation word
    "IF THE NODE IS UNRESPONSIVE, PULL IT FROM THE POOL",
    "PLEASE FILE A TICKET BEFORE TOUCHING PRODUCTION",
    "CONFIRM QUORUM ACROSS EVERY REMAINING REPLICA SET FIRST",   # 8 words
]

# P4.6 bounded the branch with FUNCTION words and stopped only one of these four:
# an IMPERATIVE has no determiner, no pronoun and no auxiliary to catch it on.
# Three of the four below were published as LEVEL-1 headings, each one reparenting
# every section printed after it. What they do carry is the adverbial furniture a
# clause needs to say WHEN or HOW MANY, which a noun-phrase label never has.
IMPERATIVE_CALLOUTS = [
    "POWER DOWN ALL NODES FIRST",                       # ALL
    "CONTACT SRE BEFORE FAILOVER",                      # BEFORE
    "DISABLE AUTOSCALING DURING MAINTENANCE",           # DURING
    "ESCALATE TO THE ON-CALL LEAD IMMEDIATELY",         # the one P4.6 already had
]

# The reason the heuristic exists. These are labels, not clauses, and must survive.
STILL_HEADINGS = [
    "SCOPE",
    "REVISION HISTORY",
    "TABLE OF CONTENTS",
    "THEORY OF OPERATION",
    "ELECTRICAL CHARACTERISTICS",
    "ABSOLUTE MAXIMUM RATINGS",
    "TERMS AND ABBREVIATIONS",
    "DMA ARBITER UNIT",
    "POWER SUPPLY REQUIREMENTS",
    "ESCALATION MATRIX",
]


@pytest.mark.parametrize("line", NOT_HEADINGS)
def test_a_shouted_body_sentence_is_not_a_heading(line):
    assert is_heading(line) == 0


@pytest.mark.parametrize("line", STILL_HEADINGS)
def test_a_shouted_label_is_still_a_heading(line):
    assert is_heading(line) == 1


def test_the_callout_does_not_reparent_the_document():
    """The whole point: the sections after the callout keep their real parent."""
    text = ("# Runbook\n"
            "intro prose\n"
            "## Failover\n"
            "%s\n"
            "pull the node from the pool instead\n"
            "## Rollback\n"
            "rollback prose\n" % CALLOUT)
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["Runbook"]
    assert [c["title"] for c in out["outline"][0]["children"]] == ["Failover",
                                                                   "Rollback"]
    # ...and the callout is still in the body, owned by the section it warns about.
    failover = out["outline"][0]["children"][0]
    assert failover["line_span"][0] <= text.split("\n").index(CALLOUT)
    assert failover["line_span"][1] > text.split("\n").index(CALLOUT)


def test_an_allcaps_heading_still_opens_a_section():
    # The counter-case, so the bound cannot be "reject every ALL-CAPS line".
    text = ("ESCALATION MATRIX\n"
            "who to call\n"
            "REVISION HISTORY\n"
            "what changed\n")
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["ESCALATION MATRIX",
                                                    "REVISION HISTORY"]


def test_other_heading_forms_are_untouched_by_the_bound():
    # The ALL-CAPS branch is the last one tried; ATX, keyword and numbered forms must
    # not be affected by a clause word appearing in their text.
    assert is_heading("# DO NOT REBOOT THE PRIMARY NODE") == 1
    assert is_heading("### THIS IS A SUBSECTION") == 3
    assert is_heading("Appendix A") == 1
    assert is_heading("2.1 The system context") == 2


@pytest.mark.parametrize("line", IMPERATIVE_CALLOUTS)
def test_a_shouted_imperative_is_not_a_heading(line):
    assert is_heading(line) == 0


def test_a_shouted_label_survives_the_tighter_bound():
    # The counter-pressure on the adverbial list: none of its words may appear in a
    # real section label, or the bound would trade one silent failure for another.
    for label in STILL_HEADINGS:
        assert is_heading(label) == 1, label
    assert is_heading("FIRST BOOT") == 1        # FIRST is deliberately NOT listed
    assert is_heading("ALL PROGRAMMABLE LOGIC") == 0   # ...and ALL is, honestly


def test_what_the_allcaps_bound_still_lets_through():
    # Stated rather than hidden: a bare imperative with a bare object carries no
    # function word and no adverbial, and nothing in its SHAPE separates it from a
    # noun-phrase label. Deciding "RESTART" is a verb and "RESET" is a noun needs a
    # part-of-speech model, and this layer is deliberately model-free.
    assert is_heading("RESTART NGINX") == 1
    assert is_heading("RESET SEQUENCE") == 1    # ...and this one SHOULD be a heading


# ------------------------------------------- the keyword branch (P4.7 / blocker 4a)
#
# `^(chapter|section|appendix|part)\s+[0-9IVXLA-Z]` under `re.I` — which makes
# `[A-Z]` match any letter at all — accepted the next ordinary word as a section
# designator, so each of these was published as a LEVEL-1 heading that reparented
# the rest of the document. Worse than the ALL-CAPS branch ever was, and unbounded
# because nobody had looked at it.
KEYWORD_NOT_HEADINGS = [
    "Section prose.",
    "Section 4 describes the reset sequence in detail.",
    "Part of the clock tree is duplicated for the display pipe.",
    "Chapter references appear at the end of the document.",
    # ...and the same sentences with the full stop taken away, so the bound cannot
    # be resting on terminal punctuation alone.
    "Section 4 describes the reset sequence in detail",
    "Part of the clock tree is duplicated for the display pipe",
    "Chapter references appear at the end of the document",
    "Appendix material is listed below",
    "Section 7 is the last one that matters",
]

# The reason the branch exists: a LABEL, in un-marked-up native text.
KEYWORD_HEADINGS = [
    "Chapter 4",
    "Chapter 4: Clocks and resets",
    "Section 2.3",
    "Section 2.3 Timing closure",
    "Appendix A",
    "Appendix A: Register map",
    "Part II",
    "Part II — Implementation",
    "CHAPTER 9 RESET SEQUENCE",
    "Chapter 7 Design of the Memory Subsystem Controller",
    "Chapter 3 The Reset Sequence",
]


@pytest.mark.parametrize("line", KEYWORD_NOT_HEADINGS)
def test_a_keyword_sentence_is_not_a_heading(line):
    assert is_heading(line) == 0


@pytest.mark.parametrize("line", KEYWORD_HEADINGS)
def test_a_keyword_label_is_still_a_heading(line):
    assert is_heading(line) == 1


def test_the_designator_is_matched_case_sensitively():
    # The actual mechanism of the bug: `re.I` turned `[0-9IVXLA-Z]` into "any
    # letter", so the word after the keyword was always accepted as a designator.
    assert is_heading("Section IV") == 1        # a roman numeral
    assert is_heading("Section iv") == 0        # ...but not a lower-case word
    assert is_heading("Part A") == 1
    assert is_heading("Part a") == 0
    assert is_heading("Appendix 2") == 1
    assert is_heading("Appendix and glossary") == 0


def test_what_the_keyword_bound_still_lets_through():
    # Honest about the residue in both directions. A title-cased sentence with a
    # capitalised verb still reads as a label by shape...
    assert is_heading("Section 4 Covers Reset") == 1
    # ...and a genuine heading written in lower case after its number is now missed.
    # The second error costs one node; the first costs the whole tree below it.
    assert is_heading("Section 4 reset sequence") == 0


def test_the_keyword_sentences_do_not_reparent_the_document():
    text = ("# Strap bus notes\n"
            "intro prose\n"
            "## Synchroniser\n"
            "Section 4 describes the reset sequence in detail.\n"
            "Part of the clock tree is duplicated for the display pipe.\n"
            "## Rollback\n"
            "rollback prose\n")
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["Strap bus notes"]
    assert [c["title"] for c in out["outline"][0]["children"]] == ["Synchroniser",
                                                                   "Rollback"]


# ---------------------------------- the ATX hash COUNT (blocker 4c)

def test_the_level_is_the_leading_hash_run_not_the_count_of_hashes():
    # `s.count("#")` counted every hash on the line, so a Word heading that talks
    # about a ticket number was published one level too deep and the next real H1
    # became its sibling.
    assert is_heading("# Issue #42 metastability on the strap bus") == 1
    assert is_heading("## Ticket #7 and #8") == 2
    # ...and an ATX-closed heading was demoted by its own closing run.
    assert is_heading("## Level Two ##") == 2
    assert is_heading("### Deep ###") == 3


def test_a_hash_in_the_title_does_not_reparent_the_next_heading():
    text = ("# Issue #42 metastability on the strap bus\n"
            "The synchroniser depth was raised to three flops.\n"
            "# Rollback\n"
            "rollback prose\n")
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == [
        "Issue #42 metastability on the strap bus", "Rollback"]
    assert [n["level"] for n in out["outline"]] == [1, 1]


# ---------------------------------- fenced code is not prose (blocker 4d)

FENCED = ("# Command transcript\n"
          "```sh\n"
          "# reset the board\n"
          "kestrelctl board reset --wait\n"
          "| not | a | table |\n"
          "| --- | --- | --- |\n"
          "| 1 | 2 | 3 |\n"
          "```\n"
          "# Rollback\n"
          "rollback prose\n")


def test_a_shell_comment_in_a_transcript_is_not_a_section():
    out = document_outline(FENCED)
    assert [n["title"] for n in out["outline"]] == ["Command transcript", "Rollback"]
    # ...and the transcript's section owns the WHOLE fence, closing delimiter and all.
    transcript = out["outline"][0]
    lines = FENCED.split("\n")
    assert lines[transcript["line_span"][1] - 1].strip() == "```"


def test_pipe_art_inside_a_fence_is_not_an_addressable_table():
    out = document_outline(FENCED)
    assert out["outline"][0]["tables"] == []


def test_a_heading_after_an_unclosed_fence_is_still_code():
    # CommonMark runs an unclosed fence to the end of the document, so the mask does
    # too: a "heading" inside one is code the renderer will never promote either.
    out = document_outline("# Intro\n```\n# still code\nmore code\n")
    assert [n["title"] for n in out["outline"]] == ["Intro"]


# ------------------------------------------- heading-level inference (P4.1/C1)

FLAT_DOCLING = ("## 1\nintro prose\n"
                "## 1.1\nscope prose\n"
                "## 1.1.1\ndetail prose\n"
                "## 1.1.1.1\ndeeper prose\n"
                "## 2\nnext section prose\n"
                "## 2.1\nnext scope prose\n")


def _titles(nodes):
    acc = []
    for n in nodes:
        acc.append(n["title"])
        acc.extend(_titles(n["children"]))
    return acc


def test_a_flat_extractor_gets_its_hierarchy_back_from_the_numbering():
    # THE eval-bundle case: every heading is `##`, and the document's real hierarchy
    # is written down in the titles. Nine flat siblings is not a tree.
    out = document_outline(FLAT_DOCLING)
    assert out["levels_inferred"] is True
    assert [n["title"] for n in out["outline"]] == ["1", "2"]
    one = out["outline"][0]
    assert [c["title"] for c in one["children"]] == ["1.1"]
    assert [g["title"] for g in one["children"][0]["children"]] == ["1.1.1"]
    assert [c["title"] for c in out["outline"][1]["children"]] == ["2.1"]
    assert sorted(_titles(out["outline"])) == ["1", "1.1", "1.1.1", "1.1.1.1",
                                               "2", "2.1"]


def test_a_bundle_with_real_levels_is_never_reshaped():
    # The guard that keeps every office bundle out of this branch: the extractor
    # varied its levels, so it HAS levels, so they are trusted verbatim — even though
    # the titles carry numbering the inference could have read instead.
    text = ("# 1 Introduction\nintro\n"
            "## 1.1 Scope\nscope\n"
            "#### 2.1.1.1 Timing budget\ndeep\n"
            "# 2 Verification\nplan\n")
    out = document_outline(text)
    assert out["levels_inferred"] is False
    assert [n["level"] for n in out["outline"]] == [1, 1]
    assert out["outline"][0]["children"][0]["level"] == 2
    # the H4 stays where the extractor put it: under 1.1, not renumbered
    assert out["outline"][0]["children"][0]["children"][0]["title"] == \
        "2.1.1.1 Timing budget"


def test_flat_and_unnumbered_headings_are_left_alone():
    # No numbering to read: the outline stays exactly as the extractor stated it.
    text = "## Alpha\na\n## Beta\nb\n## Gamma\nc\n"
    out = document_outline(text)
    assert out["levels_inferred"] is False
    assert len(out["outline"]) == 3


def test_flat_numbering_with_no_nesting_is_not_reshaped():
    # "1", "2", "3" describes a flat document. Inferring from it would be busywork,
    # so the flag stays False and the shape is untouched.
    text = "## 1 Alpha\na\n## 2 Beta\nb\n## 3 Gamma\nc\n"
    out = document_outline(text)
    assert out["levels_inferred"] is False
    assert len(out["outline"]) == 3


def test_inference_needs_the_numbering_to_be_the_documents_habit():
    # One numbered heading among four is a coincidence (a "2026 Roadmap" title), not
    # a scheme. Below the 60% floor nothing is inferred.
    text = ("## Overview\na\n## 2.1 Scope\nb\n## Background\nc\n"
            "## Appendix\nd\n")
    out = document_outline(text)
    assert out["levels_inferred"] is False
    assert len(out["outline"]) == 4


def test_an_unnumbered_heading_is_not_adopted_by_the_section_above_it():
    # When inference does fire, a heading with no number takes the shallowest
    # inferred level rather than falling under whatever preceded it.
    text = ("## 1\na\n## 1.1\nb\n## 1.1.1\nc\n## Appendix\nd\n## 2\ne\n")
    out = document_outline(text)
    assert out["levels_inferred"] is True
    assert [n["title"] for n in out["outline"]] == ["1", "Appendix", "2"]


# ------------------------- a docx with ONE heading style (P4.1 / blocker 4e)
#
# The guard's comment claimed "a document with real levels can never be touched"
# and the guard did not say that: `len(set(levels)) != 1` is ALSO what an ordinary
# docx looks like when its author used one heading style throughout. Whatever
# digits happened to open the titles were then read as a hierarchy.

ONE_STYLE_RAILS = ("# 5 V rail\nFeeds the fan controller and the board LEDs.\n"
                   "# 1.8 V rail\nFeeds the core logic of the main die.\n"
                   "# 3.3 V rail\nFeeds the general purpose IO ring.\n"
                   "# 12 V rail\nFeeds the barrel jack input stage.\n"
                   "# 2.5 V rail\nFeeds the DDR reference buffer.\n")


def test_measurements_in_titles_cannot_fabricate_a_hierarchy():
    # Five siblings came out as a two-level tree in which the 1.8 V and 3.3 V rails
    # are SUBSECTIONS of the 5 V rail — an assertion the source never made, with
    # both gates green because every word is still present.
    out = document_outline(ONE_STYLE_RAILS)
    assert out["levels_inferred"] is False
    assert [n["title"] for n in out["outline"]] == [
        "5 V rail", "1.8 V rail", "3.3 V rail", "12 V rail", "2.5 V rail"]
    assert all(not n["children"] for n in out["outline"])


def test_the_numbering_has_to_read_as_an_outline_to_be_believed():
    # The rule, directly: `1.2` under `1` is an outline, `1.8` under `5` is a
    # voltage. Every nested number must extend one the document already stated.
    from backend.sections._outline import _is_nested_outline
    assert _is_nested_outline(["1", "1.1", "1.1.1", "2", "2.1"]) is True
    assert _is_nested_outline(["5", "1.8", "3.3", "12", "2.5"]) is False
    assert _is_nested_outline(["2.4", "2.5", "3.3"]) is True   # all at one depth
    assert _is_nested_outline(["1", "1.1", "2.1"]) is False    # 2 was never stated


def test_a_flat_extractor_that_starts_below_the_top_level_is_still_inferred():
    # A document whose extractor skipped a chapter heading begins at `1.1`. Those
    # numbers are the document's own roots and answer to nothing above them, so
    # the outline guard must not refuse the whole document over it.
    text = "## 1.1\na\n## 1.1.1\nb\n## 1.2\nc\n"
    out = document_outline(text)
    assert out["levels_inferred"] is True
    assert [n["title"] for n in out["outline"]] == ["1.1", "1.2"]


# ------------------------- an ascending measurement series (blocker 4f / idx 29)
#
# Prefix existence alone is not the property a section numbering has. It rejects the
# rail list above only because `1` and `3` are never stated there — and an ASCENDING
# series states its own integer parts on the way up, so each of these came out as a
# two-level tree with fabricated `parent` pointers and rolled-up `subtree_tokens`,
# an assertion the source never made. document.md is untouched by the reshape and
# `structure_fidelity` grades document.md, so the second hard gate cannot see it.
MEASUREMENT_SERIES = [
    # capacity / price tiers: 1.5 is not a subsection of 1
    "## 1 GB tier\nfor hobby projects\n## 1.5 GB tier\nfor small teams\n"
    "## 2 GB tier\nfor growing teams\n## 2.5 GB tier\nfor large teams\n",
    # fastener sizes, on the office one-heading-style shape
    "# 2 mm screw\npan head\n# 2.5 mm screw\ncap head\n"
    "# 3 mm screw\nhex head\n# 3.5 mm screw\ncountersunk\n",
    # SLA response windows
    "## 1 hour response\ngold\n## 2 hour response\nsilver\n"
    "## 2.5 hour response\nbronze\n## 4 hour response\nbest effort\n",
    # a 3 V coin cell beside a 3.3 V logic rail
    "## 3 V rail\ncoin cell\n## 3.3 V rail\nlogic\n## 5 V rail\nfans\n",
]


@pytest.mark.parametrize("text", MEASUREMENT_SERIES)
def test_an_ascending_measurement_series_is_not_a_hierarchy(text):
    out = document_outline(text)
    assert out["levels_inferred"] is False
    assert all(not n["children"] for n in out["outline"])
    # every heading is still its own top-level node — nothing adopted, nothing lost
    assert len(out["outline"]) == text.count("\n#")+ 1


def test_a_numbering_is_believed_only_when_its_children_start_at_one():
    # The discriminator, directly. `1` then `1.1` is a numbering; `1` then only
    # `1.5` is an interval. Gaps stay believable (a spec whose 2.3 was deleted),
    # and `1.0 Introduction` is a real convention, so 0 counts as "starts at one".
    from backend.sections._outline import _is_nested_outline
    assert _is_nested_outline(["1", "1.1", "2", "2.1"]) is True
    assert _is_nested_outline(["2", "2.1", "2.2", "2.4"]) is True    # a deleted 2.3
    assert _is_nested_outline(["1", "1.0", "1.1"]) is True           # "1.0 Introduction"
    assert _is_nested_outline(["1", "1.5", "2", "2.5"]) is False     # GB tiers
    assert _is_nested_outline(["2", "2.5", "3", "3.5"]) is False     # mm fasteners
    assert _is_nested_outline(["1", "2", "2.5", "4"]) is False       # SLA hours
    assert _is_nested_outline(["3", "3.3", "5"]) is False            # V rails
    # ...and what the tighter rule costs, stated rather than hidden: a document whose
    # `1.1` heading the extractor missed leaves `1.2` as `1`'s only stated child and
    # is no longer reshaped. Staying as flat as the extractor said is the safe error.
    assert _is_nested_outline(["1", "1.2", "2"]) is False


# ------------------------- the numbered branch is bounded (blocker 4g / idx 5)
#
# The third of `is_heading`'s heuristic branches, and the one left unbounded: any
# short line opening with a digit run, a space and a letter became a heading that
# REPARENTED every section after it, publishing an anchor for a fragment no heading
# in document.md makes addressable — with recall, structure_fidelity and coverage all
# green, because every word is still present and only the shape is wrong.
NUMBERED_NOT_HEADINGS = [
    "2024 replaced the manual failover script with the supervisor.",
    "3.3 V is the nominal supply for the IO ring.",
    "100 MHz.",
    "10 GbE uplinks connect the top-of-rack switches to the spine.",
    "2 engineers reviewed the change before it merged.",
    "16 bytes are reserved at the head of every descriptor.",
    "5 minutes after boot the watchdog is armed.",
    "5 V tolerance is guaranteed.",
    "8 GB DDR4 is fitted on the module.",
    # ...and the same sentences with the full stop taken away, so the bound cannot
    # be resting on terminal punctuation alone.
    "2024 replaced the manual failover script with the supervisor",
    "10 GbE uplinks connect the top-of-rack switches to the spine",
    "5 V tolerance is guaranteed",
    "3.3 V is the nominal supply for the IO ring",
]

# The reason the branch exists: a numbered section LABEL in un-marked-up native text.
NUMBERED_HEADINGS = [
    ("1 Introduction", 1),
    ("1.2 Reference documents", 2),
    ("2.1 The system context", 2),          # THE is deliberately not a verbal word
    ("1.2.3 Something Here", 3),
    ("2.1.1.1 Timing budget", 4),
    ("4 Verification plan", 1),
    ("1 SCOPE", 1),
    ("3 Register map", 1),
]


@pytest.mark.parametrize("line", NUMBERED_NOT_HEADINGS)
def test_a_numbered_body_sentence_is_not_a_heading(line):
    assert is_heading(line) == 0


@pytest.mark.parametrize("line,level", NUMBERED_HEADINGS)
def test_a_numbered_label_is_still_a_heading(line, level):
    assert is_heading(line) == level


def test_the_numbered_sentence_does_not_reparent_the_document():
    """The harm, end to end: the sentence became a LEVEL-1 root and `## Rollback`
    was published as its child instead of a sibling of `## Failover`."""
    text = ("# Kestrel Runbook\n"
            "\n"
            "## Failover\n"
            "\n"
            "2024 replaced the manual failover script with the supervisor.\n"
            "Call the on-call lead before starting.\n"
            "\n"
            "## Rollback\n"
            "\n"
            "Restore the previous image and reboot.\n")
    out = document_outline(text)
    assert [n["title"] for n in out["outline"]] == ["Kestrel Runbook"]
    assert [c["title"] for c in out["outline"][0]["children"]] == ["Failover",
                                                                   "Rollback"]
    # ...and no anchor is published for a fragment the rendered document has no
    # heading for (quality-plan C3 grades exactly this).
    anchors = [n["anchor"] for n in out["outline"][0]["children"]]
    assert anchors == ["failover", "rollback"]


def test_what_the_numbered_bound_still_lets_through():
    # Honest about the residue in both directions, exactly as the keyword branch is.
    # A bare capitalised measurement has no verb and no stop, and nothing in its
    # SHAPE separates it from the real heading "5 V rail"...
    assert is_heading("100 MHz") == 1
    assert is_heading("8 GB DDR4") == 1
    # ...and a genuine heading written in lower case after its number is now missed,
    # as is one whose title runs past the keyword branch's eight-word title budget.
    assert is_heading("4.2 reset sequence") == 0
    assert is_heading("6.3 Requirements for the Detection of Open Faults") == 2
    wordy = "6.3 Detection of Open Circuit Faults in the Redundant Power Path"
    assert len(wordy) < 80                       # not the char bound: the word budget
    assert is_heading(wordy) == 0
    # That second loss is confined to UN-MARKED-UP text: once the converter marks the
    # same heading up, the ATX branch answers first and carries no bound at all.
    assert is_heading("## " + wordy) == 2


# ------------------- an explicitly marked-up heading has no length bound (idx 6)

LONG_TITLE = ("Reset and Initialisation Sequence for the Kestrel Fabric Bridge, "
              "Including the Optional Retry Path and the Timeout Handling Rules")


def test_an_atx_heading_is_recognised_at_any_length():
    # `len(s) > 120: return 0` sat ABOVE the ATX branch, so a heading the document
    # marked up ITSELF was silently deleted from structure.json. A `##` marker is
    # unambiguous evidence at any length; the bound belongs on the heuristics only.
    assert len("## " + LONG_TITLE) > 120
    assert is_heading("## " + LONG_TITLE) == 2
    assert is_heading("# " + LONG_TITLE) == 1
    # the bound still guards the un-marked-up branches
    assert is_heading(LONG_TITLE) == 0
    assert is_heading("4.2 " + LONG_TITLE) == 0


def test_a_long_heading_is_a_node_and_does_not_donate_its_body_to_its_sibling():
    # Not merely "the node vanished": the dropped heading's whole section was
    # re-attributed to the PRECEDING node, whose fingerprint, tables, images and
    # links then described content that was not its own.
    text = ("# Kestrel Databook\n"
            "intro prose\n"
            "## %s\n"
            "The bridge asserts nreset for eight cycles.\n"
            "## Clocking\n"
            "The reference clock runs at one hundred megahertz.\n" % LONG_TITLE)
    out = document_outline(text)
    top = out["outline"][0]
    assert [c["title"] for c in top["children"]] == [LONG_TITLE, "Clocking"]
    # the parent owns its own two lines and nothing after them
    assert top["line_span"][0] == 0
    assert top["self_tokens"] == document_outline(
        "# Kestrel Databook\nintro prose\n")["outline"][0]["self_tokens"]
    # ...and the title is published VERBATIM, so its anchor is a fragment a renderer
    # really emits (a `[:120]` clip would advertise the slug of a truncated title).
    assert top["children"][0]["title"] == LONG_TITLE
    assert top["children"][0]["anchor"].endswith("the-timeout-handling-rules")
