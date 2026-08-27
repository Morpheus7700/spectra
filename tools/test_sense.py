"""The separability maths has to be honest in both directions.

An effect-size function that flatters overlapping distributions, or a threshold sweep that
optimises accuracy on unbalanced classes, would let this project claim a detector it does
not have. These pin both.
"""

from __future__ import annotations

import pytest

from tools.sense import best_threshold, cohens_d


def test_identical_distributions_have_no_effect() -> None:
    sample = [0.10, 0.12, 0.11, 0.13, 0.09]
    assert cohens_d(sample, sample) == pytest.approx(0.0)


def test_effect_is_signed_towards_the_second_argument() -> None:
    quiet = [0.10, 0.11, 0.12, 0.10, 0.11]
    active = [0.30, 0.31, 0.32, 0.30, 0.31]
    assert cohens_d(quiet, active) > 0
    assert cohens_d(active, quiet) < 0


def test_wide_overlap_yields_a_small_effect() -> None:
    """The measured idle floor is 0.115 +/- 0.050. A shift of one SD must not read as large."""
    quiet = [0.07, 0.11, 0.15, 0.09, 0.13, 0.17, 0.05, 0.12]
    barely = [0.12, 0.16, 0.20, 0.14, 0.18, 0.22, 0.10, 0.17]
    assert 0.0 < cohens_d(quiet, barely) < 1.5


def test_degenerate_inputs_do_not_fabricate_an_effect() -> None:
    assert cohens_d([], []) == 0.0
    assert cohens_d([0.1], [0.9]) == 0.0  # n=1 has no variance to pool
    assert cohens_d([0.1, 0.1, 0.1], [0.1, 0.1, 0.1]) == 0.0  # zero pooled SD


def test_threshold_separates_cleanly_separable_classes() -> None:
    quiet = [0.10, 0.11, 0.12]
    active = [0.50, 0.51, 0.52]
    threshold, tpr, fpr = best_threshold(quiet, active)
    assert tpr == 1.0
    assert fpr == 0.0
    assert 0.12 < threshold <= 0.50


def test_threshold_reports_false_positives_it_cannot_avoid() -> None:
    """Overlapping classes must surface a non-zero FPR, never hide it."""
    quiet = [0.10, 0.20, 0.30, 0.40]
    active = [0.25, 0.35, 0.45, 0.55]
    _, tpr, fpr = best_threshold(quiet, active)
    assert tpr > 0.0
    assert fpr > 0.0


def test_threshold_on_empty_input_claims_nothing() -> None:
    assert best_threshold([], [0.1, 0.2]) == (0.0, 0.0, 0.0)
    assert best_threshold([0.1, 0.2], []) == (0.0, 0.0, 0.0)


def test_feature_windows_never_span_sessions_or_labels() -> None:
    """A window straddling the moment the person started walking belongs to neither label."""
    from tools.sense import SenseRow, extract_features

    def row(label: str, session: str, at: float) -> SenseRow:
        return SenseRow(
            label=label, session=session, at=at, duration_s=0.25,
            transmitted=100, received=100, retries=12, ack_failures=25,
        )

    rows = [row("still", "s1", t * 0.25) for t in range(160)]
    rows += [row("walking", "s1", 40.0 + t * 0.25) for t in range(160)]
    rows += [row("still", "s2", t * 0.25) for t in range(160)]

    features = extract_features(rows, window_s=20.0)
    assert {(f.session, f.label) for f in features} == {
        ("s1", "still"), ("s1", "walking"), ("s2", "still"),
    }
    for f in features:
        assert f.retry_mean == pytest.approx(0.12)


def test_a_single_sample_cannot_produce_a_variance() -> None:
    """One sample has no spread; emitting 0.0 would claim a stability it never measured."""
    from tools.sense import SenseRow, extract_features

    lonely = [SenseRow("still", "s1", 0.0, 0.25, 100, 100, 12, 25)]
    assert extract_features(lonely, window_s=20.0) == []
