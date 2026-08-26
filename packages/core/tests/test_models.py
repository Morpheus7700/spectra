"""Contract tests for the domain models.

These are mostly about what the types *refuse* to represent. The whole point of putting
SolutionKind in the model is that a degenerate solve cannot be quietly dressed up as a
confident one, so most of these tests assert that the invalid state raises.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from spectra_core.models import (
    AccessPoint,
    Floor,
    ObservationEvent,
    PositionEstimate,
    Site,
    SolutionKind,
    Vec3,
    Zone,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def obs(**kw) -> ObservationEvent:
    return ObservationEvent(
        **{
            "tenant_id": "t-0",
            "ingested_at": NOW,
            "collection_policy": "ephemeral",
            "observer_id": "ap-1",
            "target_id": "dev-1",
            "observed_at": NOW,
            "kind": "rssi",
            "value": -60.0,
            **kw,
        }
    )


# --------------------------------------------------------------------------- observations


def test_accepts_plausible_rssi():
    assert obs(value=-72.5).value == -72.5


def test_rejects_ujiindoorloc_not_detected_sentinel():
    # UJIIndoorLoc encodes "WAP not detected" as 100. Feeding that straight into a
    # path-loss inversion silently produces a sub-millimetre distance. Catch it here.
    with pytest.raises(ValidationError, match="not detected"):
        obs(value=100.0)


@pytest.mark.parametrize("bad", [0.5, -121.0, 30.0])
def test_rejects_rssi_outside_physical_range(bad):
    with pytest.raises(ValidationError):
        obs(value=bad)


def test_rejects_negative_rtt():
    with pytest.raises(ValidationError):
        obs(kind="rtt", value=-1.0)


def test_rtt_accepts_large_millimetre_values():
    assert obs(kind="rtt", value=12_500.0).value == 12_500.0


def test_observation_is_immutable():
    with pytest.raises(ValidationError):
        obs().value = -50.0  # type: ignore[misc]


def test_rejects_unknown_field():
    with pytest.raises(ValidationError):
        obs(vendor="unifi")


# --------------------------------------------------------------------------- site model


def _site(**kw) -> Site:
    base = {
        "tenant_id": "t-0",
        "id": "site-1",
        "floors": (Floor(id="f1", level=1, elevation_m=0.0),),
        "access_points": (AccessPoint(id="ap-1", position=Vec3(x=0, y=0, z=2.8), floor_id="f1"),),
    }
    return Site(**{**base, **kw})


def test_site_rejects_access_point_on_unknown_floor():
    with pytest.raises(ValidationError, match="unknown floor"):
        _site(access_points=(AccessPoint(id="ap-9", position=Vec3(x=0, y=0), floor_id="nope"),))


def test_site_rejects_zone_on_unknown_floor():
    with pytest.raises(ValidationError, match="unknown floor"):
        _site(zones=(Zone(id="z1", floor_id="nope", polygon=((0, 0), (1, 0), (1, 1))),))


def test_site_rejects_duplicate_floor_ids():
    dupes = (Floor(id="f1", level=1, elevation_m=0.0), Floor(id="f1", level=2, elevation_m=3.0))
    with pytest.raises(ValidationError, match="duplicate floor"):
        _site(floors=dupes)


def test_zone_needs_at_least_three_vertices():
    with pytest.raises(ValidationError):
        Zone(id="z", floor_id="f1", polygon=((0, 0), (1, 1)))


def test_uncalibrated_access_point_reports_itself_as_such():
    ap = AccessPoint(id="ap-1", position=Vec3(x=0, y=0), floor_id="f1")
    assert ap.is_calibrated is False
    assert ap.model_copy(update={"path_loss_a": -40.0, "path_loss_n": 2.7}).is_calibrated


def test_access_point_rejects_non_positive_path_loss_exponent():
    with pytest.raises(ValidationError):
        AccessPoint(id="ap", position=Vec3(x=0, y=0), floor_id="f1", path_loss_n=0.0)


# --------------------------------------------------------------------------- estimates


def _point(**kw) -> PositionEstimate:
    base = {
        "tenant_id": "t-0",
        "collection_policy": "ephemeral",
        "target_id": "dev-1",
        "site_id": "site-1",
        "estimated_at": NOW,
        "kind": SolutionKind.POINT,
        "x": 3.0,
        "y": 4.0,
        "covariance_xy": ((4.0, 0.0), (0.0, 1.0)),
    }
    return PositionEstimate(**{**base, **kw})


def test_point_estimate_round_trips():
    assert (_point().x, _point().y) == (3.0, 4.0)


def test_point_estimate_requires_coordinates():
    with pytest.raises(ValidationError, match="requires x, y and covariance"):
        _point(x=None)


def test_point_estimate_requires_covariance():
    with pytest.raises(ValidationError, match="requires x, y and covariance"):
        _point(covariance_xy=None)


def test_covariance_must_be_symmetric():
    with pytest.raises(ValidationError, match="symmetric"):
        _point(covariance_xy=((4.0, 1.0), (0.0, 1.0)))


def test_covariance_must_be_positive_definite():
    with pytest.raises(ValidationError, match="positive-definite"):
        _point(covariance_xy=((1.0, 2.0), (2.0, 1.0)))


@pytest.mark.parametrize("cov", [((0.0, 0.0), (0.0, 1.0)), ((1.0, 0.0), (0.0, -1.0))])
def test_covariance_diagonal_must_be_positive(cov):
    with pytest.raises(ValidationError, match="diagonal must be positive"):
        _point(covariance_xy=cov)


def test_sigma_is_the_major_axis_of_the_uncertainty_ellipse():
    # var_x = 9, var_y = 4, uncorrelated -> 1-sigma major axis is 3 m, not 2 m.
    est = _point(covariance_xy=((9.0, 0.0), (0.0, 4.0)))
    assert est.horizontal_sigma_m == pytest.approx(3.0)


def test_sigma_is_none_without_a_covariance():
    est = PositionEstimate(
        tenant_id="t-0",
        collection_policy="ephemeral",
        target_id="d",
        site_id="s",
        estimated_at=NOW,
        kind=SolutionKind.ZONE_ONLY,
        floor_id="f1",
        downgrade_reason="only 2 observers",
    )
    assert est.horizontal_sigma_m is None


# --- the downgrade path is the part that must not be silent ------------------


def test_zone_only_estimate_must_not_carry_coordinates():
    with pytest.raises(ValidationError, match="must not carry coordinates"):
        PositionEstimate(
            tenant_id="t-0",
            collection_policy="ephemeral",
            target_id="d",
            site_id="s",
            estimated_at=NOW,
            kind=SolutionKind.ZONE_ONLY,
            x=1.0,
            y=2.0,
            floor_id="f1",
            downgrade_reason="collinear anchors",
        )


def test_downgraded_estimate_must_record_a_reason():
    with pytest.raises(ValidationError, match="silent downgrade"):
        PositionEstimate(
            tenant_id="t-0",
            collection_policy="ephemeral",
            target_id="d",
            site_id="s",
            estimated_at=NOW,
            kind=SolutionKind.ZONE_ONLY,
            floor_id="f1",
        )


def test_unknown_estimate_cannot_claim_a_floor():
    with pytest.raises(ValidationError, match="cannot claim a floor"):
        PositionEstimate(
            tenant_id="t-0",
            collection_policy="ephemeral",
            target_id="d",
            site_id="s",
            estimated_at=NOW,
            kind=SolutionKind.UNKNOWN,
            floor_id="f1",
            downgrade_reason="no observations",
        )


def test_valid_unknown_estimate():
    est = PositionEstimate(
        tenant_id="t-0",
        collection_policy="ephemeral",
        target_id="d",
        site_id="s",
        estimated_at=NOW,
        kind=SolutionKind.UNKNOWN,
        downgrade_reason="no observations in window",
    )
    assert est.floor_id is None and est.zone_confidence == 0.0


# --------------------------------------------------------------------------- geometry


def test_distance_includes_the_vertical_component():
    assert Vec3(x=0, y=0, z=0).distance_to(Vec3(x=3, y=4, z=12)) == pytest.approx(13.0)


def test_horizontal_distance_ignores_height():
    # An AP 2.8 m up a wall is 3 m away horizontally, not 4.1 m. Using the slant range
    # against a floor-plane solve is a real and easy mistake.
    assert Vec3(x=0, y=0, z=2.8).horizontal_distance_to(Vec3(x=3, y=0, z=0)) == pytest.approx(3.0)
