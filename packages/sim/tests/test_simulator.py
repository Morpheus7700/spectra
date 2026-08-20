"""Tests for the simulator.

The simulator is the measuring instrument for this whole project, so it gets tested like
one. Determinism and calibration of the noise terms matter most: if the field does not
actually have the sigma it claims, every accuracy number downstream is quietly wrong.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

import pytest
from spectra_sim.building import BuildingSpec, build_site, interior_walls, walls_by_floor
from spectra_sim.propagation import (
    PropagationParams,
    count_wall_crossings,
    expected_rssi,
    invert_path_loss,
    path_loss_rssi,
    segments_intersect,
)
from spectra_sim.scenario import office_walk
from spectra_sim.sensor import Sensor
from spectra_sim.shadowing import ShadowingField
from spectra_sim.trajectory import Trajectory, Waypoint

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
P = PropagationParams()


# --------------------------------------------------------------------------- propagation


def test_rssi_equals_reference_power_at_reference_distance():
    assert path_loss_rssi(P, 1.0) == pytest.approx(P.reference_power_dbm)


def test_rssi_falls_monotonically_with_distance():
    values = [path_loss_rssi(P, d) for d in (1, 2, 5, 10, 20, 40)]
    assert values == sorted(values, reverse=True)


def test_doubling_distance_costs_a_fixed_number_of_db():
    expected = 10 * P.path_loss_exponent * math.log10(2)
    assert path_loss_rssi(P, 5.0) - path_loss_rssi(P, 10.0) == pytest.approx(expected)


@pytest.mark.parametrize("distance", [1.0, 3.7, 12.0, 55.0])
def test_inversion_round_trips(distance):
    assert invert_path_loss(P, path_loss_rssi(P, distance)) == pytest.approx(distance)


def test_distance_is_clamped_below_the_reference_distance():
    # A target sitting on top of an AP must not produce a divergent RSSI.
    assert path_loss_rssi(P, 0.0) == path_loss_rssi(P, 1.0)


def test_slab_attenuation_applies_between_floors():
    same = expected_rssi(P, (0, 0), 0, (5, 0), 0)
    above = expected_rssi(P, (0, 0), 0, (5, 0), 1)
    assert same - above == pytest.approx(P.floor_attenuation_db)


def test_slab_attenuation_accumulates_across_floors():
    one = expected_rssi(P, (0, 0), 0, (5, 0), 1)
    two = expected_rssi(P, (0, 0), 0, (5, 0), 2)
    assert one - two == pytest.approx(P.floor_attenuation_db)


def test_walls_attenuate_only_within_a_floor():
    wall = (((2.5, -10.0), (2.5, 10.0)),)
    blocked = expected_rssi(P, (0, 0), 0, (5, 0), 0, walls=wall)
    clear = expected_rssi(P, (0, 0), 0, (5, 0), 0)
    assert clear - blocked == pytest.approx(P.wall_attenuation_db)


# --------------------------------------------------------------------------- geometry


def test_crossing_segments_intersect():
    assert segments_intersect((0, 0), (4, 0), ((2, -1), (2, 1)))


def test_parallel_segments_do_not_intersect():
    assert not segments_intersect((0, 0), (4, 0), ((0, 1), (4, 1)))


def test_touching_endpoint_is_not_a_crossing():
    # A path that grazes a wall end should not be charged full attenuation.
    assert not segments_intersect((0, 0), (4, 0), ((2, 0), (2, 3)))


def test_counts_every_wall_on_the_path():
    walls = (((1, -1), (1, 1)), ((2, -1), (2, 1)), ((3, -1), (3, 1)))
    assert count_wall_crossings((0, 0), (4, 0), walls) == 3


def test_interior_walls_leave_a_corridor_gap():
    spec = BuildingSpec(rooms_across=2, rooms_deep=1)
    walls = interior_walls(spec)
    # The partition is split into two runs with a doorway between them.
    assert len(walls) == 2
    assert not segments_intersect(
        (0.0, spec.depth_m * 0.5), (spec.width_m, spec.depth_m * 0.5), walls[0]
    )


# --------------------------------------------------------------------------- shadowing


def test_shadowing_is_deterministic_for_a_seed():
    a, b = ShadowingField(seed=7), ShadowingField(seed=7)
    assert a.value("ap-1", 3.0, 4.0) == b.value("ap-1", 3.0, 4.0)


def test_different_seeds_give_different_fields():
    a, b = ShadowingField(seed=1), ShadowingField(seed=2)
    assert a.value("ap-1", 3.0, 4.0) != b.value("ap-1", 3.0, 4.0)


def test_different_observers_see_independent_shadowing():
    f = ShadowingField(seed=3)
    assert f.value("ap-1", 10.0, 10.0) != f.value("ap-2", 10.0, 10.0)


def test_shadowing_is_spatially_correlated():
    # This is the property that distinguishes it from naive IID noise: standing still
    # must yield a stable bias, so nearby points must agree closely.
    f = ShadowingField(seed=11, correlation_length_m=5.0)
    near = [
        abs(f.value("ap-1", x, 0.0) - f.value("ap-1", x + 0.2, 0.0))
        for x in (0.0, 7.3, 21.9, 40.1)
    ]
    far = [
        abs(f.value("ap-1", x, 0.0) - f.value("ap-1", x + 25.0, 0.0))
        for x in (0.0, 7.3, 21.9, 40.1)
    ]
    assert statistics.mean(near) < statistics.mean(far) / 3


def test_shadowing_has_approximately_the_requested_sigma():
    # If the field is quietly narrower than sigma_db, every accuracy figure downstream is
    # optimistic. Sample on a lattice coarser than the correlation length.
    f = ShadowingField(seed=5, sigma_db=3.0, correlation_length_m=5.0)
    samples = [f.value("ap-1", 11.0 * i, 13.0 * j) for i in range(14) for j in range(14)]
    assert statistics.pstdev(samples) == pytest.approx(3.0, rel=0.30)
    assert statistics.mean(samples) == pytest.approx(0.0, abs=0.9)


def test_zero_sigma_disables_shadowing():
    assert ShadowingField(seed=1, sigma_db=0.0).value("ap-1", 1.0, 2.0) == 0.0


def test_rejects_non_positive_correlation_length():
    with pytest.raises(ValueError, match="correlation_length_m"):
        ShadowingField(correlation_length_m=0.0)


# --------------------------------------------------------------------------- trajectory


def test_walk_duration_follows_distance_over_speed():
    t = Trajectory(
        waypoints=(Waypoint(x=0, y=0, floor_id="f"), Waypoint(x=13.5, y=0, floor_id="f")),
        start=NOW, speed_ms=1.35,
    )
    assert t.duration_s() == pytest.approx(10.0)


def test_pauses_extend_the_duration():
    t = Trajectory(
        waypoints=(
            Waypoint(x=0, y=0, floor_id="f", pause_s=4.0),
            Waypoint(x=13.5, y=0, floor_id="f"),
        ),
        start=NOW, speed_ms=1.35,
    )
    assert t.duration_s() == pytest.approx(14.0)


def test_interpolates_position_along_a_leg():
    t = Trajectory(
        waypoints=(Waypoint(x=0, y=0, floor_id="f"), Waypoint(x=10, y=0, floor_id="f")),
        start=NOW, speed_ms=1.0, sample_interval_s=1.0,
    )
    samples = t.samples()
    assert samples[0].x == pytest.approx(0.0)
    assert samples[5].x == pytest.approx(5.0)


def test_stationary_samples_are_flagged():
    t = Trajectory(
        waypoints=(Waypoint(x=1, y=1, floor_id="f", pause_s=3.0),),
        start=NOW, sample_interval_s=1.0,
    )
    assert all(s.is_stationary for s in t.samples())


def test_floor_change_holds_horizontal_position_and_flips_floor():
    t = Trajectory(
        waypoints=(Waypoint(x=5, y=5, floor_id="f0"), Waypoint(x=5, y=5, floor_id="f1")),
        start=NOW, sample_interval_s=1.0, floor_change_s=8.0,
    )
    samples = t.samples()
    assert {(s.x, s.y) for s in samples} == {(5.0, 5.0)}
    assert samples[0].floor_id == "f0"
    assert samples[-1].floor_id == "f1"


def test_rejects_zero_speed():
    with pytest.raises(ValueError, match="speed"):
        Trajectory(waypoints=(Waypoint(x=0, y=0, floor_id="f"),), start=NOW, speed_ms=0.0)


# --------------------------------------------------------------------------- sensor


def _sensor(**kw) -> tuple[Sensor, BuildingSpec]:
    spec = BuildingSpec()
    site = build_site(spec)
    return Sensor(site=site, walls=walls_by_floor(site, spec), **kw), spec


def test_observations_use_the_shared_contract():
    sensor, spec = _sensor()
    events = sensor.observe("dev-1", (spec.width_m / 2, spec.depth_m / 2), "floor-0", NOW)
    assert events, "a target mid-building should be heard by at least one AP"
    assert all(e.kind == "rssi" and e.target_id == "dev-1" for e in events)
    assert all(-120.0 <= e.value <= 0.0 for e in events)


def test_sensor_is_deterministic():
    a, _ = _sensor(shadowing=ShadowingField(seed=4))
    b, _ = _sensor(shadowing=ShadowingField(seed=4))
    xy = (10.0, 10.0)
    assert [e.value for e in a.observe("d", xy, "floor-0", NOW)] == [
        e.value for e in b.observe("d", xy, "floor-0", NOW)
    ]


def test_distant_access_points_fall_silent():
    # Silence is informative and must actually occur, or the engine never learns to cope.
    sensor, _ = _sensor()
    events = sensor.observe("dev-1", (1.0, 1.0), "floor-0", NOW)
    assert len(events) < len(sensor.site.access_points)


def test_nearer_access_points_report_stronger_signal():
    sensor, _ = _sensor(shadowing=ShadowingField(sigma_db=0.0), fast_fading_sigma_db=0.0)
    ap = sensor.site.access_points[0]
    near = sensor.true_rssi(ap.id, (ap.position.x + 1.0, ap.position.y), ap.floor_id, "k")
    far = sensor.true_rssi(ap.id, (ap.position.x + 15.0, ap.position.y), ap.floor_id, "k")
    assert near is not None and far is not None and near > far


# --------------------------------------------------------------------------- scenario


def test_office_walk_produces_paired_truth_and_observations():
    scenario = office_walk(seed=0)
    assert len(scenario.truth) > 50
    assert scenario.observations
    paired = scenario.paired()
    assert len(paired) == len(scenario.truth)
    assert sum(1 for _, obs in paired if obs) / len(paired) > 0.9


def test_office_walk_visits_more_than_one_floor():
    floors = {t.floor_id for t in office_walk(seed=0).truth}
    assert len(floors) >= 2


def test_office_walk_is_reproducible():
    a, b = office_walk(seed=2), office_walk(seed=2)
    assert [o.value for o in a.observations] == [o.value for o in b.observations]


def test_different_seeds_produce_different_observations():
    a, b = office_walk(seed=1), office_walk(seed=2)
    assert [o.value for o in a.observations] != [o.value for o in b.observations]


def test_access_points_are_coplanar_per_floor_by_design():
    # The engine must survive this, so the default fixture must exhibit it.
    site = build_site(BuildingSpec())
    heights = {ap.position.z for ap in site.access_points if ap.floor_id == "floor-0"}
    assert len(heights) == 1
