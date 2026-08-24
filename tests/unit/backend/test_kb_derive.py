"""
title: Unit — backend.kb tier-1 derivations
kind: tests
layer: backend
summary: The fixed-rule values computed from a document — which headings are addressable, and what a title slugs to when ASCII folding empties it.
"""
import pytest

from backend.kb import body_anchors, derive_uid, heading_anchor, slugify
from backend.sections import document_outline

pytestmark = pytest.mark.unit


# --------------------------------------------------------------- fenced code

def _published(body):
    """The anchors the layer that PUBLISHES structure.json actually emits.

    The outline is a TREE, so this walks it: a flat read of the top level would
    compare a subset and the parity assertion would pass over a real disagreement.
    """
    out = []

    def walk(nodes):
        for n in nodes or []:
            out.append(n["anchor"])
            walk(n.get("children"))

    walk(document_outline(body)["outline"])
    return sorted(out)


def test_an_atx_line_inside_a_fenced_block_is_not_an_addressable_anchor():
    # `# Reset the fleet` in a shell transcript is a comment. No renderer emits a
    # fragment for it, and the publishing layer masks fences — so scanning the raw
    # body made this the LAXER of the two implementations, and a `ref` to a fragment
    # nothing publishes resolved cleanly through `_check_refs`. Worse, this set is
    # what `enrich_metadata` hands the model as "the only legal `ref` values", so the
    # phantom was advertised before it was ever accepted.
    body = ("## Install\n\n"
            "```bash\n"
            "# Reset the fleet\n"
            "systemctl restart nodes\n"
            "```\n\n"
            "## Rollback\n\ntext\n")

    assert sorted(body_anchors(body)) == ["install", "rollback"]
    assert sorted(body_anchors(body)) == _published(body)


def test_a_tilde_fence_and_an_indented_fence_mask_the_same_way():
    body = ("# Top\n\n"
            "   ~~~\n"
            "## Not a heading\n"
            "   ~~~\n\n"
            "## Real\n")

    assert sorted(body_anchors(body)) == ["real", "top"]
    assert sorted(body_anchors(body)) == _published(body)


def test_an_unclosed_fence_runs_to_the_end_of_the_document():
    # CommonMark's rule, and the publishing layer's. A mask that instead treated an
    # unclosed fence as closed would resurrect the disagreement in the OTHER
    # direction, turning live refs into hard ERRORs.
    body = "# Top\n\n```\n# Still code\n\n## Also still code\n"

    assert sorted(body_anchors(body)) == ["top"]
    assert sorted(body_anchors(body)) == _published(body)


def test_ordinary_headings_around_a_fence_are_untouched():
    body = ("# Alpha\n\ntext\n\n"
            "```\ncode\n```\n\n"
            "## Beta\n\n### Gamma\n\n## Beta\n")

    # `beta-1` is the renderer's disambiguating suffix for the repeat.
    assert sorted(body_anchors(body)) == ["alpha", "beta", "beta-1", "gamma"]
    assert sorted(body_anchors(body)) == _published(body)


def test_a_hash_with_no_space_is_still_not_a_heading():
    # `#include`, `#!/bin/bash` and `#define` were never anchors and must not become
    # ones: the fence mask is the only thing that changed.
    body = "#include <stdio.h>\n\n#!/bin/bash\n\n# Real Heading\n"
    assert sorted(body_anchors(body)) == ["real-heading"]


# ------------------------------------------------------------------- slugify

def test_a_unicode_title_slugs_without_a_caret():
    # `[^^\w]+` reads "not a caret and not a word character": the second `^` is a
    # LITERAL member of the class, so the caret survived into `slug` — declared the
    # "url form of the title" — and into the readable uid.
    assert slugify(u"设计^规范") == u"设计-规范"
    assert "^" not in derive_uid(u"docs/设计^规范.docx")


def test_a_title_with_no_word_characters_at_all_falls_through_to_the_hash():
    # The fallback's own comment says a content hash is what happens when even
    # unicode word characters are absent. A caret is not a word character, so
    # keeping it bypassed the hash and made `slugify('  ^^^  ') == slugify('^^^')`.
    hashed = slugify("^^^")
    assert hashed != "^^^"
    assert len(hashed) == 12 and hashed.isalnum()
    assert slugify(u"★^★") not in ("^", "")


def test_the_ascii_path_is_unchanged():
    # Any surviving ASCII alphanumeric suppresses the fallback entirely, so the
    # ordinary case must not move.
    assert slugify("Rev^2 Clock Tree") == "rev-2-clock-tree"
    assert slugify(u"Café Runbook") == "cafe-runbook"
    assert heading_anchor("7.3 Standing rule") == "73-standing-rule"
