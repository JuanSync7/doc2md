"""
title: Unit — the canonical id is unique BY CONSTRUCTION, not by checking
kind: tests
layer: backend
summary: 1000 synthetic documents with deliberately colliding titles get 1000 distinct ids, and each id depends on nothing but its own source path.
"""
# quality-plan.md D3s. The old id was `slugify(title)`, which collides the instant
# two documents are both called "Overview" — and a collision was a `kb_lint` ERROR
# somebody had to hand-fix, on a corpus where the fix is "rename a document".
#
# The replacement has to survive three separate attacks, and a counter over
# directory order fails all three:
#
#   SAME DOCUMENT, ANOTHER RUN   the id must not move (a moved id orphans every
#                                see_also that pointed at it)
#   ANOTHER ORDER                a --only run, a re-import, a parallel walk
#   ANOTHER CORPUS               the same document imported alone must get the same
#                                id it gets among its thousand neighbours
#
# So the id is a function of THE SOURCE PATH AND NOTHING ELSE. These tests are the
# proof, and they are unit tests on purpose: no disk, no corpus, no ordering to be
# lucky about.
import random

import pytest

from backend.kb import slugify, unique_id

pytestmark = pytest.mark.unit

# The two titles the plan calls out. Every document below carries one of them, so
# title-derived ids would collapse the whole corpus onto two values.
COLLIDING_TITLES = ("Overview", "Release Notes")

N = 1000


def _corpus(n=N):
    """``n`` documents whose TITLES collide and whose PATHS do not — a real corpus.

    Filenames are deliberately mixed: slug-clean, spaced, capitalised, punctuated
    and accented, because the interesting collisions are the ones slugification
    creates rather than the ones the filesystem already prevents.
    """
    shapes = ("team-%d/overview.docx", "team-%d/Overview.docx",
              "team-%d/Overview v2.docx", "team %d/release notes.docx",
              "team_%d/Release Notes (final).docx", "équipe-%d/Aperçu.docx")
    docs = []
    for i in range(n):
        shape = shapes[i % len(shapes)]
        docs.append((shape % i, COLLIDING_TITLES[i % 2]))
    return docs


def test_a_thousand_colliding_titles_produce_a_thousand_distinct_ids():
    docs = _corpus()
    ids = [unique_id(path) for path, _title in docs]

    assert len(set(ids)) == len(docs) == N
    # ...and the thing that used to be the id would have produced exactly two.
    assert len(set(slugify(t) for _p, t in docs)) == 2


def test_the_same_document_gets_the_same_id_in_any_order_and_with_no_neighbours():
    docs = _corpus()

    in_order = dict((p, unique_id(p)) for p, _t in docs)

    shuffled = list(docs)
    random.Random(20260819).shuffle(shuffled)
    reversed_walk = dict((p, unique_id(p)) for p, _t in reversed(shuffled))
    assert reversed_walk == in_order

    # Imported ALONE — the case a counter over directory order gets wrong, and the
    # case that matters when a single document is re-enriched with --only.
    for path, _title in docs[:50]:
        assert unique_id(path) == in_order[path]

    # ...and a second call is not a second answer.
    assert [unique_id(p) for p, _t in docs] == [in_order[p] for p, _t in docs]


def test_two_paths_that_slugify_alike_still_get_different_ids():
    # The residual collision the pretty slug cannot prevent by itself: slugification
    # is lossy, so `Kestrel Spec.docx` and `kestrel-spec.docx` both reduce to
    # `kestrel-spec`. A path whose slug is not a faithful rendering of itself carries
    # a fingerprint of the EXACT path, which is what closes the last hole.
    clean = unique_id("specs/kestrel-spec.docx")
    spaced = unique_id("specs/Kestrel Spec.docx")
    punctuated = unique_id("specs/kestrel_spec!.docx")

    assert len(set([clean, spaced, punctuated])) == 3
    # The EXTENSION stays: spec.docx, spec.pptx and spec.xlsx are three
    # documents a real corpus really does hold side by side, and dropping it
    # gave all three one id with both writing scripts exiting 0.
    assert clean == "specs/kestrel-spec.docx"     # readable when the path is clean
    # A lossy path keeps its readable rendering and gains a fingerprint of the
    # EXACT path, so a rendering collision can never be a real one.
    assert spaced.startswith("specs/kestrel-spec.docx-")
    assert punctuated.startswith("specs/kestrel-spec.docx-")


def test_documents_differing_only_in_extension_or_case_get_different_ids():
    # Both were real collisions. `spec.docx` / `spec.pptx` / `spec.xlsx` is an
    # ordinary trio (a spec and the deck and the sheet that go with it), and on a
    # case-sensitive filesystem `spec.docx` and `Spec.docx` are two files. Before
    # this, all four shared one id and both writing scripts exited 0.
    paths = ["specs/spec.docx", "specs/spec.pptx", "specs/spec.xlsx",
             "specs/Spec.docx", "specs/spec.md", "specs/spec"]
    ids = [unique_id(p) for p in paths]
    assert len(set(ids)) == len(paths), list(zip(paths, ids))


def test_a_namespace_scopes_a_corpus_without_making_ids_collide_inside_it():
    # Two corpora imported into one index must not share ids, and namespacing must
    # not itself become a collision source.
    a = [unique_id(p, "acme") for p, _t in _corpus(200)]
    b = [unique_id(p, "globex") for p, _t in _corpus(200)]

    assert len(set(a)) == len(set(b)) == 200
    assert not set(a) & set(b)
    assert all(i.startswith("acme/") for i in a)


def test_a_degenerate_path_still_yields_something_rather_than_an_empty_id():
    # An id must exist for every document: an empty one collides with every other
    # empty one and takes the document out of the corpus graph entirely.
    assert unique_id("###/!!!.docx")
    assert unique_id("specs/文档.docx")
    assert unique_id("", "acme") == "acme"
    assert len(set([unique_id("###/!!!.docx"), unique_id("###/???.docx")])) == 2
