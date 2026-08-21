"""A complete run: a building, a walk through it, and the observations it generated.

This is what the accuracy harness consumes. Everything is derived from a single seed so a
regression is reproducible from its scenario name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from spectra_core.models import ObservationEvent, Site

from .building import BuildingSpec, build_site, walls_by_floor
from .propagation import PropagationParams
from .sensor import Sensor
from .shadowing import ShadowingField
from .trajectory import GroundTruth, Trajectory, Waypoint

EPOCH = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    site: Site
    observations: tuple[ObservationEvent, ...]
    truth: tuple[GroundTruth, ...]
    target_id: str

    def observations_at(self, at: datetime) -> tuple[ObservationEvent, ...]:
        return tuple(o for o in self.observations if o.observed_at == at)

    def paired(self) -> list[tuple[GroundTruth, tuple[ObservationEvent, ...]]]:
        """Ground truth samples with the observations recorded at that instant."""
        by_time: dict[datetime, list[ObservationEvent]] = {}
        for o in self.observations:
            by_time.setdefault(o.observed_at, []).append(o)
        return [(t, tuple(by_time.get(t.at, ()))) for t in self.truth]


def run(
    name: str,
    trajectory: Trajectory,
    site: Site,
    sensor: Sensor,
    target_id: str = "device-1",
) -> Scenario:
    truth = trajectory.samples()
    observations: list[ObservationEvent] = []
    for sample in truth:
        observations.extend(
            sensor.observe(target_id, (sample.x, sample.y), sample.floor_id, sample.at)
        )
    return Scenario(
        name=name,
        site=site,
        observations=tuple(observations),
        truth=tuple(truth),
        target_id=target_id,
    )


def office_walk(seed: int = 0, spec: BuildingSpec | None = None) -> Scenario:
    """The default regression fixture: a loop on level 0, then a trip to level 1.

    Includes stationary pauses (where correlated shadowing bites) and a floor change
    (where multi-floor systems break), because a fixture that avoids both would report
    an accuracy figure that means nothing.
    """
    spec = spec or BuildingSpec()
    site = build_site(spec)
    sensor = Sensor(
        site=site,
        params=PropagationParams(),
        shadowing=ShadowingField(seed=seed),
        walls=walls_by_floor(site, spec),
    )
    w, d = spec.width_m, spec.depth_m
    waypoints = (
        Waypoint(x=w * 0.15, y=d * 0.20, floor_id="floor-0", pause_s=6.0),
        Waypoint(x=w * 0.85, y=d * 0.20, floor_id="floor-0"),
        Waypoint(x=w * 0.85, y=d * 0.80, floor_id="floor-0", pause_s=6.0),
        Waypoint(x=w * 0.15, y=d * 0.80, floor_id="floor-0"),
        Waypoint(x=w * 0.15, y=d * 0.20, floor_id="floor-0"),
        Waypoint(x=w * 0.15, y=d * 0.20, floor_id="floor-1"),
        Waypoint(x=w * 0.50, y=d * 0.50, floor_id="floor-1", pause_s=6.0),
    )
    return run(
        f"office-walk-seed{seed}",
        Trajectory(waypoints=waypoints, start=EPOCH),
        site,
        sensor,
    )


def corridor_walk(seed: int = 0) -> Scenario:
    """A narrow corridor with APs along the spine — triggers COLLINEAR_ANCHORS.

    The building is 30 m long and only 4 m wide, with 3 APs placed in a 3x1 grid. Because
    ap_grid_rows=1 all anchors share the same y-coordinate, making them collinear (the SVD
    ratio test in assess_geometry sees singular[1]/singular[0] < 0.06).

    This is realistic: a long hallway in a hospital or office wing with ceiling APs mounted
    along the centreline is exactly the geometry where the solver must refuse rather than
    produce a fix that is unconstrained perpendicular to the corridor.
    """
    spec = BuildingSpec(
        width_m=30.0,
        depth_m=4.0,
        floors=1,
        ap_grid_cols=3,
        ap_grid_rows=1,
        rooms_across=1,
        rooms_deep=1,
    )
    site = build_site(spec)
    sensor = Sensor(
        site=site,
        params=PropagationParams(),
        shadowing=ShadowingField(seed=seed),
        walls=walls_by_floor(site, spec),
    )
    waypoints = (
        Waypoint(x=2.0, y=2.0, floor_id="floor-0", pause_s=3.0),
        Waypoint(x=15.0, y=2.0, floor_id="floor-0"),
        Waypoint(x=28.0, y=2.0, floor_id="floor-0", pause_s=3.0),
    )
    return run(
        f"corridor-walk-seed{seed}",
        Trajectory(waypoints=waypoints, start=EPOCH),
        site,
        sensor,
    )


def sparse_grid(seed: int = 0) -> Scenario:
    """A wide floor with only 2 APs — triggers TOO_FEW_ANCHORS.

    A 50 m x 50 m warehouse floor with exactly 2 access points. The solver requires a
    minimum of 3 unique anchors (MIN_ANCHORS in multilateration.py), so every sample point
    here produces at most 2 ranges and is refused without attempting a solve.

    This is realistic: a large open warehouse with a partial AP rollout, where coverage
    exists but positioning geometry does not.
    """
    spec = BuildingSpec(
        width_m=50.0,
        depth_m=50.0,
        floors=1,
        ap_grid_cols=2,
        ap_grid_rows=1,
        rooms_across=1,
        rooms_deep=1,
    )
    site = build_site(spec)
    sensor = Sensor(
        site=site,
        params=PropagationParams(),
        shadowing=ShadowingField(seed=seed),
        walls=walls_by_floor(site, spec),
    )
    waypoints = (
        Waypoint(x=10.0, y=25.0, floor_id="floor-0", pause_s=3.0),
        Waypoint(x=25.0, y=25.0, floor_id="floor-0"),
        Waypoint(x=40.0, y=25.0, floor_id="floor-0", pause_s=3.0),
    )
    return run(
        f"sparse-grid-seed{seed}",
        Trajectory(waypoints=waypoints, start=EPOCH),
        site,
        sensor,
    )


def wide_open(seed: int = 0) -> Scenario:
    """A very large floor with APs only at the corners — triggers high uncertainty.

    100 m x 100 m floor with a 2x2 AP grid. Most trajectory points are far from all four
    APs, so the RSSI ranges have large sigma and the solved fix has uncertainty exceeding
    `max_sigma_for_point_m` (15 m default). The solver succeeds but the pipeline downgrades
    to ZONE_ONLY because the reported sigma is too large to claim a point.

    Also contains some points close enough to the APs that they get a POINT fix, so the
    scenario produces a mix of POINT and ZONE_ONLY outcomes.
    """
    spec = BuildingSpec(
        width_m=100.0,
        depth_m=100.0,
        floors=1,
        ap_grid_cols=2,
        ap_grid_rows=2,
        rooms_across=2,
        rooms_deep=2,
    )
    site = build_site(spec)
    sensor = Sensor(
        site=site,
        params=PropagationParams(),
        shadowing=ShadowingField(seed=seed),
        walls=walls_by_floor(site, spec),
    )
    # Trajectory crosses far from all APs, forcing large sigma estimates.
    waypoints = (
        Waypoint(x=50.0, y=50.0, floor_id="floor-0", pause_s=4.0),
        Waypoint(x=50.0, y=10.0, floor_id="floor-0"),
        Waypoint(x=10.0, y=50.0, floor_id="floor-0"),
        Waypoint(x=90.0, y=90.0, floor_id="floor-0", pause_s=4.0),
    )
    return run(
        f"wide-open-seed{seed}",
        Trajectory(waypoints=waypoints, start=EPOCH),
        site,
        sensor,
    )

