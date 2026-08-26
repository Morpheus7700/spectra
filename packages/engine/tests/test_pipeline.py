"""Tests for ranging, floor classification, zone resolution, and the pipeline.

Fast behavioural tests only. The accuracy regression gate -- the seeded, slow, marked
suite whose thresholds are published claims -- lives in test_accuracy_gate.py.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from spectra_core.models import ObservationEvent, SolutionKind
from spectra_engine.floor import classify_floor
from spectra_engine.pipeline import _slant_to_horizontal, estimate_position
from spectra_engine.ranging import CalibrationError, PathLossModel, default_model, fit_path_loss
from spectra_engine.zones import confidence_ellipse_axes, point_in_polygon, resolve_zone
from spectra_sim.building import BuildingSpec, build_site

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
SITE = build_site(BuildingSpec())


def obs(ap_id: str, rssi: float) -> ObservationEvent:
    return ObservationEvent(
        tenant_id="t-0",
        ingested_at=datetime.now(),
        collection_policy="ephemeral",
        observer_id=ap_id,
        target_id="d1",
        observed_at=NOW,
        kind="rssi",
        value=rssi,
    )


# --------------------------------------------------------------------------- ranging


def test_fit_recovers_known_parameters():
    truth = PathLossModel(reference_power_dbm=-42.0, exponent=3.1)
    samples = [(d, truth.expected_rssi(d)) for d in (1, 2, 3, 5, 8, 13, 21, 34)]
    fitted = fit_path_loss(samples)
    assert fitted.reference_power_dbm == pytest.approx(-42.0, abs=0.3)
    assert fitted.exponent == pytest.approx(3.1, abs=0.05)


def test_fit_refuses_too_few_samples():
    with pytest.raises(CalibrationError, match="at least"):
        fit_path_loss([(1.0, -40.0), (2.0, -49.0)])


def test_fit_refuses_a_narrow_distance_spread():
    # All measurements taken from one spot cannot identify the exponent.
    with pytest.raises(CalibrationError, match="narrow"):
        fit_path_loss([(5.0 + i * 0.01, -60.0) for i in range(10)])


def test_fit_refuses_a_non_physical_exponent():
    # Signal getting *stronger* with distance means the data is mislabelled.
    rising = [(d, -80.0 + 3.0 * d) for d in (1, 3, 5, 8, 12, 20)]
    with pytest.raises(CalibrationError, match="non-physical"):
        fit_path_loss(rising)


def test_distance_inversion_round_trips():
    m = PathLossModel(reference_power_dbm=-40.0, exponent=2.8)
    assert m.distance(m.expected_rssi(12.0)) == pytest.approx(12.0)


def test_distance_uncertainty_grows_with_range():
    # The term that justifies distance-dependent weighting in the solver.
    m = PathLossModel(reference_power_dbm=-40.0, exponent=2.8)
    assert m.distance_sigma(40.0) > 10 * m.distance_sigma(2.0)


def test_distance_uncertainty_has_a_floor():
    m = PathLossModel(reference_power_dbm=-40.0, exponent=2.8)
    assert m.distance_sigma(0.01) >= 0.3


def test_uncalibrated_fallback_is_deliberately_distrusted():
    # A wide sigma is the point: it degrades the fix gently instead of corrupting it.
    assert default_model().rssi_sigma_db >= 6.0


def test_rejects_non_positive_exponent():
    with pytest.raises(ValueError, match="exponent"):
        PathLossModel(reference_power_dbm=-40.0, exponent=0.0)


# --------------------------------------------------------------------------- floor


def _floor0_aps() -> list[str]:
    return [ap.id for ap in SITE.access_points if ap.floor_id == "floor-0"]


def test_strong_same_floor_signals_decide_the_floor():
    verdict = classify_floor([obs(ap, -50.0) for ap in _floor0_aps()], SITE)
    assert verdict.floor_id == "floor-0" and verdict.confidence > 0.9


def test_weak_cross_floor_signals_do_not_override():
    observations = [obs(ap, -50.0) for ap in _floor0_aps()]
    observations += [obs(ap.id, -85.0) for ap in SITE.access_points if ap.floor_id == "floor-1"]
    assert classify_floor(observations, SITE).floor_id == "floor-0"


def test_voting_uses_linear_power_not_decibels():
    # Summing dBm is meaningless. One AP at -50 dBm outweighs three at -70 dBm, because
    # it is 100x the power; naive dB summing would get this backwards.
    f0, f1 = _floor0_aps()[0], [ap.id for ap in SITE.access_points if ap.floor_id == "floor-1"][:3]
    verdict = classify_floor([obs(f0, -50.0)] + [obs(a, -70.0) for a in f1], SITE)
    assert verdict.floor_id == "floor-0"


def test_ambiguous_evidence_yields_no_decision():
    # Mid-stairwell. Refusing is correct; guessing would poison the horizontal solve.
    f0 = _floor0_aps()[:2]
    f1 = [ap.id for ap in SITE.access_points if ap.floor_id == "floor-1"][:2]
    verdict = classify_floor([obs(a, -65.0) for a in f0 + f1], SITE)
    assert verdict.floor_id is None
    assert verdict.reason is not None and "inconclusive" in verdict.reason


def test_unknown_access_points_cannot_vote():
    verdict = classify_floor([obs("rogue-ap", -40.0)], SITE)
    assert verdict.floor_id is None and "known access points" in (verdict.reason or "")


def test_no_observations_yields_no_decision():
    assert classify_floor([], SITE).floor_id is None


# --------------------------------------------------------------------------- zones


SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


def test_point_inside_polygon():
    assert point_in_polygon(5.0, 5.0, SQUARE)


def test_point_outside_polygon():
    assert not point_in_polygon(15.0, 5.0, SQUARE)


def test_concave_polygon_notch_is_outside():
    l_shape = ((0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10))
    assert not point_in_polygon(8.0, 8.0, l_shape)
    assert point_in_polygon(2.0, 8.0, l_shape)


def test_tight_estimate_commits_to_one_zone():
    site = build_site(BuildingSpec())
    zone = next(z for z in site.zones if z.floor_id == "floor-0")
    cx = sum(p[0] for p in zone.polygon) / 4
    cy = sum(p[1] for p in zone.polygon) / 4
    verdict = resolve_zone(cx, cy, ((0.04, 0.0), (0.0, 0.04)), site, "floor-0")
    assert verdict.zone_id == zone.id and verdict.confidence > 0.9


def test_uncertain_estimate_spreads_confidence_across_zones():
    # An estimate straddling a partition must not report a confident room name.
    site = build_site(BuildingSpec())
    verdict = resolve_zone(20.0, 15.0, ((36.0, 0.0), (0.0, 36.0)), site, "floor-0")
    assert len(verdict.ranked) > 1
    assert verdict.confidence < 0.75


def test_zone_confidences_never_exceed_total_probability():
    site = build_site(BuildingSpec())
    verdict = resolve_zone(20.0, 15.0, ((9.0, 0.0), (0.0, 9.0)), site, "floor-0")
    assert sum(c for _, c in verdict.ranked) <= 1.0 + 1e-9


def test_ellipse_axes_order_major_before_minor():
    major, minor, _ = confidence_ellipse_axes(((16.0, 0.0), (0.0, 4.0)))
    assert major == pytest.approx(4.0) and minor == pytest.approx(2.0)


# --------------------------------------------------------------------------- pipeline


def test_slant_range_is_projected_onto_the_floor_plane():
    # 3 m horizontal, 1.7 m up: slant is 3.45 m. Failing to correct biases anchors outward.
    assert _slant_to_horizontal(math.hypot(3.0, 1.7), 1.7) == pytest.approx(3.0)


def test_slant_shorter_than_height_difference_clamps_to_zero():
    assert _slant_to_horizontal(1.0, 2.0) == 0.0


def test_no_observations_gives_unknown_with_a_reason():
    result = estimate_position([], SITE, NOW, "d1")
    assert result.estimate.kind is SolutionKind.UNKNOWN
    assert result.estimate.downgrade_reason


def test_too_few_anchors_downgrades_to_zone_only_and_keeps_the_floor():
    observations = [obs(ap, -55.0) for ap in _floor0_aps()[:2]]
    result = estimate_position(observations, SITE, NOW, "d1")
    assert result.estimate.kind is SolutionKind.ZONE_ONLY
    assert result.estimate.floor_id == "floor-0"
    assert "at least 3" in (result.estimate.downgrade_reason or "")


def test_downgraded_estimate_never_carries_coordinates():
    observations = [obs(ap, -55.0) for ap in _floor0_aps()[:2]]
    est = estimate_position(observations, SITE, NOW, "d1").estimate
    assert est.x is None and est.y is None and est.covariance_xy is None


def test_undecidable_floor_yields_unknown_not_a_guess():
    f0 = _floor0_aps()[:3]
    f1 = [ap.id for ap in SITE.access_points if ap.floor_id == "floor-1"][:3]
    est = estimate_position([obs(a, -65.0) for a in f0 + f1], SITE, NOW, "d1").estimate
    assert est.kind is SolutionKind.UNKNOWN and est.floor_id is None
