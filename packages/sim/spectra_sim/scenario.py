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
