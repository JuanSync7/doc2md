"""
title: Unit — figure-loss accounting (Tier-1 lossless-ness instrument)
kind: tests
layer: backend
summary: figure_outcome classifies each figure's fate; figure_coverage aggregates + flags loss.
"""
# Pure policy. The SAME detect-and-report idea as text coverage, applied to figures:
# every BODY figure is captured, intentionally gated, or LOST — and loss is flagged.
from backend.ingest import (
    figure_outcome, figure_coverage, FigureCoverage,
    FIG_CAPTURED, FIG_NO_CAPTION, FIG_GATED_TINY, FIG_GATED_DENY, FIG_GATED_DUP,
    FIG_LOST_BADCROP, FIG_LOST_BAIL)


def test_figure_outcome_maps_gate_reasons():
    assert figure_outcome(False, "tiny", False, False) == FIG_GATED_TINY
    assert figure_outcome(False, "deny:logo", False, False) == FIG_GATED_DENY
    assert figure_outcome(False, "dup", False, False) == FIG_GATED_DUP


def test_figure_outcome_kept_paths():
    # kept but not stored (bad crop / save fail) -> loss
    assert figure_outcome(True, "keep", False, False) == FIG_LOST_BADCROP
    # kept + stored + useful caption -> captured
    assert figure_outcome(True, "keep", True, True) == FIG_CAPTURED
    # kept + stored + unusable caption -> image kept, caption absent (not loss)
    assert figure_outcome(True, "keep", True, False) == FIG_NO_CAPTION


def test_coverage_all_captured_is_lossless():
    cov = figure_coverage([FIG_CAPTURED, FIG_CAPTURED, FIG_NO_CAPTION], n_placeholders=3)
    assert isinstance(cov, FigureCoverage)
    assert cov.n_body == 3
    assert cov.n_captured == 3          # no_caption still counts as captured (image kept)
    assert cov.n_gated == 0
    assert cov.n_lost == 0
    assert cov.lossless is True


def test_coverage_gated_is_intentional_not_loss():
    cov = figure_coverage([FIG_CAPTURED, FIG_GATED_TINY, FIG_GATED_DENY, FIG_GATED_DUP],
                          n_placeholders=4)
    assert cov.n_gated == 3
    assert cov.n_lost == 0
    assert cov.lossless is True          # gating is policy, not loss


def test_coverage_bad_crop_is_loss():
    cov = figure_coverage([FIG_CAPTURED, FIG_LOST_BADCROP], n_placeholders=2)
    assert cov.n_lost == 1
    assert cov.lossless is False
    assert cov.by_outcome[FIG_LOST_BADCROP] == 1


def test_coverage_bail_flags_total_loss():
    cov = figure_coverage([FIG_LOST_BAIL, FIG_LOST_BAIL, FIG_GATED_TINY],
                          n_placeholders=3, bailed=True)
    assert cov.bailed is True
    assert cov.n_lost == 2
    assert cov.n_captured == 0
    assert cov.lossless is False


def test_coverage_alignment_mismatch_is_not_lossless():
    # even with zero drops, body count != placeholder count is an anomaly to surface
    cov = figure_coverage([FIG_CAPTURED, FIG_CAPTURED], n_placeholders=3)
    assert cov.n_lost == 0
    assert cov.lossless is False


def test_coverage_empty_is_lossless():
    cov = figure_coverage([], n_placeholders=0)
    assert cov.n_body == 0
    assert cov.lossless is True


# --- variation --------------------------------------------------------------

def test_figure_outcome_unknown_gate_reason_is_gated_other():
    from backend.ingest import FIG_GATED_OTHER
    assert figure_outcome(False, "some_new_reason", False, False) == FIG_GATED_OTHER


def test_coverage_mixed_bag_aggregates_correctly():
    outcomes = [FIG_CAPTURED, FIG_NO_CAPTION, FIG_GATED_TINY, FIG_GATED_DENY,
                FIG_GATED_DUP, FIG_LOST_BADCROP]
    cov = figure_coverage(outcomes, n_placeholders=6)
    assert cov.n_body == 6
    assert cov.n_captured == 2          # captured + no_caption
    assert cov.n_gated == 3
    assert cov.n_lost == 1
    assert cov.lossless is False
    assert cov.by_outcome[FIG_CAPTURED] == 1
    assert cov.by_outcome[FIG_NO_CAPTION] == 1


def test_coverage_bail_leaves_gated_untouched():
    # on bail only OK figures become lost; already-gated stay gated
    cov = figure_coverage([FIG_CAPTURED, FIG_GATED_DENY], n_placeholders=2, bailed=True)
    assert cov.by_outcome[FIG_LOST_BAIL] == 1
    assert cov.by_outcome[FIG_GATED_DENY] == 1
    assert cov.n_lost == 1
