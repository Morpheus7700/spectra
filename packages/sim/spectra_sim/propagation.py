"""Radio propagation for the synthetic building.

This is the model the engine will be asked to invert. Keeping it honest matters more than
keeping it sophisticated: if the simulator is too kind, the solver will look excellent here
and fail in a real building.

So it deliberately includes the three things that actually break indoor positioning:

  * log-normal shadowing, which is the dominant error term and is *correlated in space*,
    not independent per sample -- a target that is standing still gets a stable wrong
    reading, not a noisy one that averages out;
  * attenuation from walls and floor slabs, which is what makes the path-loss exponent
    vary per AP and per direction;
  * a noise floor, below which the AP simply does not report the target at all. Missing
    observations are informative and the engine must cope with them.

**Nothing here proves anything about a real building.** Passing the accuracy gate proves the
solver correctly inverts *this* model. That is a genuinely useful thing to know and it is
not the same claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Segment = tuple[tuple[float, float], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class PropagationParams:
    """Log-distance model parameters.

    Defaults are typical indoor office values, but the engine must never assume them --
    it fits `reference_power_dbm` and `path_loss_exponent` per AP from calibration data.
    They live here only because the simulator is entitled to know its own ground truth.
    """

    reference_power_dbm: float = -40.0
    path_loss_exponent: float = 2.8
    shadowing_sigma_db: float = 3.0
    wall_attenuation_db: float = 5.0
    floor_attenuation_db: float = 15.0
    reference_distance_m: float = 1.0
    noise_floor_dbm: float = -95.0

    def __post_init__(self) -> None:
        if self.path_loss_exponent <= 0:
            raise ValueError("path_loss_exponent must be positive")
        if self.reference_distance_m <= 0:
            raise ValueError("reference_distance_m must be positive")
        if self.shadowing_sigma_db < 0:
            raise ValueError("shadowing_sigma_db cannot be negative")


def path_loss_rssi(params: PropagationParams, distance_m: float) -> float:
    """Noiseless RSSI at a distance: ``A - 10 n log10(d / d0)``.

    Clamped at the reference distance so that a target standing on top of an AP does not
    produce a divergent value.
    """
    d = max(distance_m, params.reference_distance_m)
    return params.reference_power_dbm - 10.0 * params.path_loss_exponent * math.log10(
        d / params.reference_distance_m
    )


def invert_path_loss(params: PropagationParams, rssi_dbm: float) -> float:
    """Distance implied by an RSSI. The exact inverse of :func:`path_loss_rssi`."""
    exponent = (params.reference_power_dbm - rssi_dbm) / (10.0 * params.path_loss_exponent)
    return float(params.reference_distance_m * (10.0**exponent))


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float], seg: Segment, eps: float = 1e-9
) -> bool:
    """Strictly *proper* segment intersection.

    Collinear and endpoint-touching cases return False by design. A path that grazes the
    end of a partition is passing through the doorway beside it, not through the wall, and
    charging it full attenuation would put a phantom obstruction in every doorway.
    """
    q1, q2 = seg
    d1, d2 = _orientation(q1, q2, p1), _orientation(q1, q2, p2)
    d3, d4 = _orientation(p1, p2, q1), _orientation(p1, p2, q2)
    if min(abs(d1), abs(d2), abs(d3), abs(d4)) <= eps:
        return False
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def count_wall_crossings(
    a: tuple[float, float], b: tuple[float, float], walls: tuple[Segment, ...]
) -> int:
    return sum(1 for w in walls if segments_intersect(a, b, w))


def expected_rssi(
    params: PropagationParams,
    ap_xy: tuple[float, float],
    ap_floor_level: int,
    target_xy: tuple[float, float],
    target_floor_level: int,
    walls: tuple[Segment, ...] = (),
) -> float:
    """Mean RSSI before shadowing: path loss plus wall and slab attenuation.

    Wall crossings are only counted when the AP and target share a floor. Between floors
    the slab dominates and the horizontal wall geometry of a different storey is not the
    obstruction the signal actually met.
    """
    floors_between = abs(ap_floor_level - target_floor_level)
    distance = math.dist(ap_xy, target_xy)
    rssi = path_loss_rssi(params, distance)
    rssi -= floors_between * params.floor_attenuation_db
    if floors_between == 0 and walls:
        rssi -= count_wall_crossings(ap_xy, target_xy, walls) * params.wall_attenuation_db
    return rssi
