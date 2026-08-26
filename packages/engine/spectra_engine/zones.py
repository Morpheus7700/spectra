"""Resolving an uncertain position to a named zone. Pure.

The zone label is what people actually act on -- "Room 4B" is operationally useful in a way
that "x=17.3, y=8.1" is not. But naming a zone from the centre point alone throws away the
uncertainty and produces a confident room name for an estimate that straddles three rooms.

So the ellipse is integrated over each zone polygon instead. The reported confidence is the
share of probability mass falling inside, which means a fix sitting exactly on a partition
honestly reports roughly 50/50 rather than picking a side.

Integration is a deterministic weighted grid in the ellipse's own eigenbasis. Monte Carlo
would need many more samples for the same accuracy, and would make the output irreproducible
run to run -- unacceptable when this feeds a regression gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from spectra_core.models import Site, Zone

GRID_STEPS = 13
GRID_EXTENT_SIGMA = 3.0


@dataclass(frozen=True, slots=True)
class ZoneVerdict:
    zone_id: str | None
    confidence: float
    ranked: tuple[tuple[str, float], ...]


def point_in_polygon(x: float, y: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting. Points exactly on an edge may fall either way, which is acceptable
    here -- an estimate that lands precisely on a wall has no meaningful zone anyway."""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def _sample_grid(
    x: float,
    y: float,
    covariance: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Points and normalised Gaussian weights covering the uncertainty ellipse."""
    cov = np.array(covariance, dtype=float)
    values, vectors = np.linalg.eigh(cov)
    values = np.maximum(values, 1e-9)
    axes = np.sqrt(values)

    steps = np.linspace(-GRID_EXTENT_SIGMA, GRID_EXTENT_SIGMA, GRID_STEPS)
    u, v = np.meshgrid(steps, steps, indexing="ij")
    weights = np.exp(-0.5 * (u**2 + v**2)).ravel()
    weights /= weights.sum()

    local = np.stack([u.ravel() * axes[0], v.ravel() * axes[1]], axis=1)
    points = local @ vectors.T + np.array([x, y])
    return points, weights


def resolve_zone(
    x: float,
    y: float,
    covariance: tuple[tuple[float, float], tuple[float, float]],
    site: Site,
    floor_id: str,
) -> ZoneVerdict:
    """Rank the zones on a floor by how much of the estimate's mass each contains."""
    zones: list[Zone] = [z for z in site.zones if z.floor_id == floor_id]
    if not zones:
        return ZoneVerdict(None, 0.0, ())

    points, weights = _sample_grid(x, y, covariance)
    mass: dict[str, float] = {}
    for zone in zones:
        total = sum(
            w
            for (px, py), w in zip(points, weights, strict=True)
            if point_in_polygon(px, py, zone.polygon)
        )
        if total > 0:
            mass[zone.id] = total

    if not mass:
        return ZoneVerdict(None, 0.0, ())
    ranked = tuple(sorted(mass.items(), key=lambda kv: -kv[1]))
    return ZoneVerdict(ranked[0][0], ranked[0][1], ranked)


def zone_containing(site: Site, floor_id: str, x: float, y: float) -> str | None:
    """The zone a known-exact point falls in. Used for ground truth, not for estimates."""
    for zone in site.zones:
        if zone.floor_id == floor_id and point_in_polygon(x, y, zone.polygon):
            return zone.id
    return None


def confidence_ellipse_axes(
    covariance: tuple[tuple[float, float], tuple[float, float]], sigma: float = 1.0
) -> tuple[float, float, float]:
    """Semi-major, semi-minor and rotation (radians) for rendering the volume."""
    cov = np.array(covariance, dtype=float)
    values, vectors = np.linalg.eigh(cov)
    values = np.maximum(values, 0.0)
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    major, minor = float(np.sqrt(values[0])) * sigma, float(np.sqrt(values[1])) * sigma
    angle = math.atan2(float(vectors[1, 0]), float(vectors[0, 0]))
    return major, minor, angle
