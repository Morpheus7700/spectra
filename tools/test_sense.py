"""The separability maths has to be honest in both directions.

An effect-size function that flatters overlapping distributions, a threshold sweep that
optimises accuracy on unbalanced classes, or a window that imputes a reading nobody took --
each would let this project claim a detector it does not have.
"""

from __future__ import annotations

import pytest

from tools.sense import Sample, best_threshold, cohens_d, extract_windows


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
    threshold, tpr, fpr = best_threshold([0.10, 0.11, 0.12], [0.50, 0.51, 0.52])
    assert (tpr, fpr) == (1.0, 0.0)
    assert 0.12 < threshold <= 0.50


def test_threshold_reports_false_positives_it_cannot_avoid() -> None:
    """Overlapping classes must surface a non-zero FPR, never hide it."""
    _, tpr, fpr = best_threshold([0.10, 0.20, 0.30, 0.40], [0.25, 0.35, 0.45, 0.55])
    assert tpr > 0.0
    assert fpr > 0.0


def test_threshold_on_empty_input_claims_nothing() -> None:
    assert best_threshold([], [0.1, 0.2]) == (0.0, 0.0, 0.0)
    assert best_threshold([0.1, 0.2], []) == (0.0, 0.0, 0.0)


def _samples(label: str, session: str, n: int, start: float = 0.0) -> list[Sample]:
    return [
        Sample(
            label=label,
            session=session,
            channel="rssi",
            at=start + i * 0.33,
            values={"aa:bb:cc:dd:ee:ff": -60.0 + (i % 3)},
        )
        for i in range(n)
    ]


def test_windows_never_span_sessions_or_labels() -> None:
    """A window straddling the moment the person started walking belongs to neither label."""
    rows = _samples("still", "s1", 120)
    rows += _samples("walking", "s1", 120, start=60.0)
    rows += _samples("still", "s2", 120)

    windows = extract_windows(rows, window_s=20.0)
    assert {(w.session, w.label) for w in windows} == {
        ("s1", "still"),
        ("s1", "walking"),
        ("s2", "still"),
    }
    for w in windows:
        assert "aa:bb:cc:dd:ee:ff.mean" in w.stats
        assert "aa:bb:cc:dd:ee:ff.sd" in w.stats


def test_a_sparse_metric_is_dropped_rather_than_imputed() -> None:
    """An AP heard twice in a window is a non-detection the rest of the time. Filling that in
    would invent readings the radio never took."""
    rows = _samples("still", "s1", 60)
    rows[0] = Sample(
        label="still",
        session="s1",
        channel="rssi",
        at=rows[0].at,
        values={"aa:bb:cc:dd:ee:ff": -60.0, "11:22:33:44:55:66": -80.0},
    )
    windows = extract_windows(rows, window_s=20.0)
    assert windows
    assert all("11:22:33:44:55:66.mean" not in w.stats for w in windows)


def test_a_window_with_no_usable_metric_is_omitted_entirely() -> None:
    lonely = [Sample("still", "s1", "rssi", 0.0, {"aa:bb:cc:dd:ee:ff": -60.0})]
    assert extract_windows(lonely, window_s=20.0) == []
