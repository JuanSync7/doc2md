"""
title: Unit — the corpus coverage summary
kind: tests
layer: backend
summary: The summary leads with the worst documents, never hides a small-document exclusion, and keeps figure loss separate from token loss.
"""
# The temptation with 544 documents is a mean. A mean recall of 0.997 over a corpus
# with two catastrophically broken documents reads as success, so this summary is
# built to refuse that shape — and these tests are what keep it refusing.
import pytest

from backend.validate import summarize_coverage
from backend.validate._covsummary import figure_losses, worst_documents

pytestmark = pytest.mark.unit


def _rec(doc_id, recall, n_source=200, missing_top=None, figures=None, rel=None):
    n_missing = int(round(n_source * (1.0 - recall)))
    rec = {"id": doc_id, "rel": rel or (doc_id + ".docx"), "recall": recall,
           "n_source": n_source, "n_covered": n_source - n_missing,
           "n_missing": n_missing, "missing_top": missing_top or []}
    if figures is not None:
        rec["figures"] = figures
    return rec


def test_the_worst_document_is_named_not_averaged():
    records = [_rec("a", 1.0) for _ in range(50)] + [_rec("bad", 0.4,
                                                          missing_top=[["table", 60]])]
    text = summarize_coverage(records)
    assert "bad.docx" in text
    assert "table x60" in text
    assert "lossless (recall == 1.0): 50 of 51" in text


def test_a_document_too_small_to_judge_is_excluded_and_the_exclusion_is_reported():
    # Two words out of four is a 0.5 recall and means nothing. Dropping it silently
    # would be the same sin as an unreported truncation.
    records = [_rec("tiny", 0.5, n_source=4), _rec("real", 1.0)]
    worst, too_small = worst_documents(records, min_tokens=50)
    assert worst == [] and too_small == 1
    text = summarize_coverage(records, min_tokens=50)
    assert "1 document(s) under 50 tokens" in text
    assert "tiny.docx" not in text


def test_same_ratio_more_tokens_ranks_worse():
    small = _rec("small", 0.9, n_source=60)
    large = _rec("large", 0.9, n_source=2000)
    worst, _ = worst_documents([small, large], min_tokens=50)
    assert [r["id"] for r in worst] == ["large", "small"]


def test_figure_loss_is_reported_separately_from_token_loss():
    # A deck can be a perfect 1.0 on text and have lost every diagram. Averaging
    # the two axes would let each hide the other.
    perfect_text_lost_figures = _rec(
        "deck", 1.0, figures={"n_body": 2, "n_lost": 2, "bailed": True,
                              "lossless": False}, rel="deck.pptx")
    text = summarize_coverage([perfect_text_lost_figures])
    assert "lossless (recall == 1.0): 1 of 1" in text
    assert "figures: 1 document(s) lost pixels" in text
    assert "deck.pptx" in text


def test_a_corpus_that_lost_nothing_says_so_plainly():
    text = summarize_coverage([_rec("a", 1.0), _rec("b", 1.0)])
    assert "no document below 1.0 recall" in text
    assert "figures: no document reports lost pixels" in text


def test_an_empty_corpus_is_not_a_clean_bill_of_health():
    text = summarize_coverage([])
    assert "coverage records: 0 total" in text
    assert "nothing to summarize" in text


def test_figure_losses_orders_by_how_much_was_lost():
    a = _rec("a", 1.0, figures={"n_body": 9, "n_lost": 1})
    b = _rec("b", 1.0, figures={"n_body": 9, "n_lost": 7})
    c = _rec("c", 1.0, figures={"n_body": 9, "n_lost": 0, "lossless": True})
    assert [r["id"] for r in figure_losses([a, b, c])] == ["b", "a"]
