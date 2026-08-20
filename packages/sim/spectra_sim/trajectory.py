"""Walking trajectories with exact ground truth.

Ground truth is the whole reason the simulator exists, so trajectories are generated
analytically rather than by integrating a noisy motion model -- there is no accumulated
error in the reference, and any error the engine reports is genuinely the engine's.

Trajectories can change floor, because floor transitions are where multi-floor systems
actually break: the fingerprint changes discontinuously while the horizontal position
barely moves.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

WALKING_SPEED_MS = 1.35


@dataclass(frozen=True, slots=True)
class Waypoint:
    x: float
    y: float
    floor_id: str
    pause_s: float = 0.0


@dataclass(frozen=True, slots=True)
class GroundTruth:
    at: datetime
    x: float
    y: float
    floor_id: str
    is_stationary: bool


@dataclass(frozen=True, slots=True)
class Trajectory:
    """A walk through a sequence of waypoints at constant speed.

    A change of `floor_id` between consecutive waypoints is treated as a vertical
    transition taking `floor_change_s`, during which the horizontal position is held.
    """

    waypoints: tuple[Waypoint, ...]
    start: datetime
    speed_ms: float = WALKING_SPEED_MS
    sample_interval_s: float = 1.0
    floor_change_s: float = 8.0

    def __post_init__(self) -> None:
        if len(self.waypoints) < 1:
            raise ValueError("a trajectory needs at least one waypoint")
        if self.speed_ms <= 0:
            raise ValueError("speed must be positive")
        if self.sample_interval_s <= 0:
            raise ValueError("sample interval must be positive")

    def _legs(self) -> Iterator[tuple[Waypoint, Waypoint, float, bool]]:
        for a, b in zip(self.waypoints, self.waypoints[1:], strict=False):
            if a.floor_id != b.floor_id:
                yield a, b, self.floor_change_s, True
            else:
                distance = math.dist((a.x, a.y), (b.x, b.y))
                yield a, b, distance / self.speed_ms, False

    def duration_s(self) -> float:
        total = sum(w.pause_s for w in self.waypoints)
        return total + sum(leg[2] for leg in self._legs())

    def samples(self) -> list[GroundTruth]:
        """Positions at fixed intervals, with exact interpolation between waypoints."""
        out: list[GroundTruth] = []
        t = 0.0
        next_sample = 0.0

        PositionAt = Callable[[float], tuple[float, float, str]]

        def emit_until(end_t: float, at: PositionAt, stationary: bool) -> None:
            nonlocal next_sample
            while next_sample <= end_t + 1e-9:
                x, y, floor = at(next_sample)
                out.append(
                    GroundTruth(
                        at=self.start + timedelta(seconds=next_sample),
                        x=x, y=y, floor_id=floor, is_stationary=stationary,
                    )
                )
                next_sample += self.sample_interval_s

        for index, wp in enumerate(self.waypoints):
            if wp.pause_s > 0:

                def held(_elapsed: float, w: Waypoint = wp) -> tuple[float, float, str]:
                    return w.x, w.y, w.floor_id

                emit_until(t + wp.pause_s, held, True)
                t += wp.pause_s
            if index >= len(self.waypoints) - 1:
                break
            a = wp
            b = self.waypoints[index + 1]
            leg_s = self.floor_change_s if a.floor_id != b.floor_id else (
                math.dist((a.x, a.y), (b.x, b.y)) / self.speed_ms
            )
            changing_floor = a.floor_id != b.floor_id
            leg_start = t

            def at(
                abs_s: float,
                a: Waypoint = a,
                b: Waypoint = b,
                leg_s: float = leg_s,
                leg_start: float = leg_start,
                changing: bool = changing_floor,
            ) -> tuple[float, float, str]:
                frac = 0.0 if leg_s <= 0 else min(max((abs_s - leg_start) / leg_s, 0.0), 1.0)
                if changing:
                    # Hold horizontal position; the floor flips at the midpoint.
                    return a.x, a.y, (a.floor_id if frac < 0.5 else b.floor_id)
                return a.x + (b.x - a.x) * frac, a.y + (b.y - a.y) * frac, a.floor_id

            emit_until(t + leg_s, at, changing_floor)
            t += leg_s

        # Guarantee the final waypoint is represented even if it fell between samples.
        last = self.waypoints[-1]
        if not out or (out[-1].x, out[-1].y) != (last.x, last.y):
            out.append(
                GroundTruth(
                    at=self.start + timedelta(seconds=t), x=last.x, y=last.y,
                    floor_id=last.floor_id, is_stationary=True,
                )
            )
        return out


def patrol(
    corners: tuple[tuple[float, float], ...],
    floor_id: str,
    start: datetime,
    pause_s: float = 5.0,
    **kwargs: object,
) -> Trajectory:
    """A closed loop with a pause at each corner -- stationary periods are where
    spatially-correlated shadowing does its damage, so they must be represented."""
    wps = tuple(Waypoint(x=x, y=y, floor_id=floor_id, pause_s=pause_s) for x, y in corners)
    return Trajectory(waypoints=(*wps, wps[0]), start=start, **kwargs)  # type: ignore[arg-type]
