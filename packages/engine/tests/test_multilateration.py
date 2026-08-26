"""Tests for the multilateration solver.

The recovery tests matter, but the *refusal* tests matter more. A solver that returns a
confident point from three anchors in a corridor is the exact failure this project is
built to avoid, so the degenerate cases get as much attention as the happy path.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from spectra_engine.multilateration import (
    GeometryQuality,
    RangeObservation,
    assess_geometry,
    solve,
)

WELL_SPREAD = [(0.0, 0.0), (20.0, 0.0), (20.0, 15.0), (0.0, 15.0)]


def ranges_to(
    truth: tuple[float, float],
    anchors: list[tuple[float, float]] = WELL_SPREAD,
    sigma: float = 1.0,
    error: dict[int, float] | None = None,
) -> list[RangeObservation]:
    error = error or {}
    return [
        RangeObservation(
            anchor_x=ax,
            anchor_y=ay,
            distance_m=max(math.dist((ax, ay), truth) + error.get(i, 0.0), 0.0),
            sigma_m=sigma,
        )
        for i, (ax, ay) in enumerate(anchors)
    ]


# --------------------------------------------------------------------------- recovery


def test_recovers_position_from_exact_ranges():
    fix, quality = solve(ranges_to((7.0, 5.0)))
    assert quality is GeometryQuality.OK
    assert fix is not None
    assert (fix.x, fix.y) == pytest.approx((7.0, 5.0), abs=1e-3)


def test_recovers_a_target_sitting_on_an_anchor():
    # Zero range to one anchor is a singularity in the Jacobian if handled carelessly.
    fix, quality = solve(ranges_to((0.0, 0.0)))
    assert quality is GeometryQuality.OK
    assert fix is not None
    assert (fix.x, fix.y) == pytest.approx((0.0, 0.0), abs=1e-2)


def test_three_anchors_are_enough():
    fix, quality = solve(ranges_to((6.0, 4.0), anchors=WELL_SPREAD[:3]))
    assert quality is GeometryQuality.OK
    assert fix is not None and (fix.x, fix.y) == pytest.approx((6.0, 4.0), abs=1e-3)


def test_reports_how_many_anchors_it_used():
    fix, _ = solve(ranges_to((7.0, 5.0)))
    assert fix is not None and fix.anchors_used == 4


def test_exact_ranges_give_near_zero_residual():
    fix, _ = solve(ranges_to((7.0, 5.0)))
    assert fix is not None and fix.residual_rms_m < 1e-3


# --------------------------------------------------------------------------- refusal


def test_two_anchors_are_refused():
    fix, quality = solve(ranges_to((5.0, 5.0), anchors=WELL_SPREAD[:2]))
    assert fix is None and quality is GeometryQuality.TOO_FEW_ANCHORS


def test_no_anchors_are_refused():
    fix, quality = solve([])
    assert fix is None and quality is GeometryQuality.TOO_FEW_ANCHORS


def test_collinear_anchors_are_refused():
    # Three APs down a corridor. Position along the corridor is unconstrained, so a point
    # estimate here would be fiction.
    corridor = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    fix, quality = solve(ranges_to((10.0, 3.0), anchors=corridor))
    assert fix is None and quality is GeometryQuality.COLLINEAR_ANCHORS


def test_nearly_collinear_anchors_are_refused():
    almost = [(0.0, 0.0), (10.0, 0.05), (20.0, 0.0)]
    fix, quality = solve(ranges_to((10.0, 3.0), anchors=almost))
    assert fix is None and quality is GeometryQuality.COLLINEAR_ANCHORS


def test_duplicate_anchors_do_not_count_as_independent():
    # The same AP reporting three times is one vantage point, not three.
    doubled = [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (10.0, 0.0)]
    assert assess_geometry(np.array(doubled)) is GeometryQuality.TOO_FEW_ANCHORS


def test_well_spread_anchors_pass_the_geometry_check():
    assert assess_geometry(np.array(WELL_SPREAD)) is GeometryQuality.OK


def test_collinearity_test_is_scale_free():
    # Three APs in a 2 m corridor are as collinear as three across a 200 m warehouse.
    small = np.array([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    large = np.array([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)])
    assert assess_geometry(small) is assess_geometry(large) is GeometryQuality.COLLINEAR_ANCHORS


# --------------------------------------------------------------------------- robustness


def test_one_wild_anchor_does_not_drag_the_fix():
    # A single AP reading 25 m long -- a lift shaft, a body, a reflection. Soft-L1 should
    # largely reject it. Plain least squares would move the fix metres.
    truth = (7.0, 5.0)
    clean, _ = solve(ranges_to(truth))
    dirty, _ = solve(ranges_to(truth, error={2: 25.0}))
    assert clean is not None and dirty is not None
    assert math.dist((dirty.x, dirty.y), truth) < 2.5


def test_a_bad_fit_reports_a_larger_uncertainty():
    # Honest self-assessment: when the residuals exceed the assumed noise, sigma grows.
    good, _ = solve(ranges_to((7.0, 5.0)))
    bad, _ = solve(ranges_to((7.0, 5.0), error={0: 6.0, 1: -5.0, 2: 7.0}))
    assert good is not None and bad is not None
    assert bad.sigma_m > good.sigma_m


def test_covariance_is_symmetric_and_positive_definite():
    fix, _ = solve(ranges_to((7.0, 5.0), error={1: 2.0}))
    assert fix is not None
    (a, b), (c, d) = fix.covariance
    assert b == pytest.approx(c)
    assert a > 0 and d > 0 and a * d - b * c > 0


def test_larger_declared_sigma_yields_larger_reported_uncertainty():
    tight, _ = solve(ranges_to((7.0, 5.0), sigma=1.0, error={1: 2.0}))
    loose, _ = solve(ranges_to((7.0, 5.0), sigma=6.0, error={1: 12.0}))
    assert tight is not None and loose is not None
    assert loose.sigma_m > tight.sigma_m


def test_redundant_consistent_anchors_do_not_worsen_confidence():
    """Adding an anchor that agrees must never make the solver less certain."""
    truth = (7.0, 5.0)
    few, _ = solve(ranges_to(truth, anchors=WELL_SPREAD[:3], error={0: 1.0}))
    many, _ = solve(ranges_to(truth, anchors=[*WELL_SPREAD, (10.0, 7.5)], error={0: 1.0}))
    assert few is not None and many is not None
    assert many.sigma_m <= few.sigma_m * 1.05


# --------------------------------------------------------------------------- properties


@settings(max_examples=120, deadline=None)
@given(
    x=st.floats(min_value=1.0, max_value=19.0, allow_nan=False),
    y=st.floats(min_value=1.0, max_value=14.0, allow_nan=False),
)
def test_exact_ranges_always_recover_the_truth(x: float, y: float):
    fix, quality = solve(ranges_to((x, y)))
    assert quality is GeometryQuality.OK
    assert fix is not None
    assert math.dist((fix.x, fix.y), (x, y)) < 0.05


@settings(max_examples=60, deadline=None)
@given(
    x=st.floats(min_value=1.0, max_value=19.0),
    y=st.floats(min_value=1.0, max_value=14.0),
    shift=st.floats(min_value=-500.0, max_value=500.0),
)
def test_solution_is_translation_equivariant(x: float, y: float, shift: float):
    """Moving the whole site must move the answer by exactly the same amount."""
    base, _ = solve(ranges_to((x, y)))
    moved_anchors = [(ax + shift, ay + shift) for ax, ay in WELL_SPREAD]
    moved, _ = solve(ranges_to((x + shift, y + shift), anchors=moved_anchors))
    assert base is not None and moved is not None
    assert moved.x - base.x == pytest.approx(shift, abs=0.05)
    assert moved.y - base.y == pytest.approx(shift, abs=0.05)


# --------------------------------------------------------------------------- validation


def test_rejects_non_positive_sigma():
    with pytest.raises(ValueError, match="sigma_m"):
        RangeObservation(anchor_x=0, anchor_y=0, distance_m=5.0, sigma_m=0.0)


def test_rejects_negative_distance():
    with pytest.raises(ValueError, match="distance_m"):
        RangeObservation(anchor_x=0, anchor_y=0, distance_m=-1.0, sigma_m=1.0)
