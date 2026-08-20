"""The positioning pipeline: observations in, one PositionEstimate out. Pure.

Order matters. Floor is decided *first*, because it determines which observations are even
admissible for the horizontal solve -- a slab costs ~15 dB, so an AP one floor away implies
a range far longer than the true horizontal distance and would drag the fix outward.

Every exit from this function is explicit. There is no path that returns a POINT without
having actually solved one, and no path that downgrades without recording why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from spectra_core.models import ObservationEvent, PositionEstimate, Site, SolutionKind

from .floor import FloorVerdict, classify_floor, observations_on_floor
from .multilateration import GeometryQuality, RangeObservation, solve
from .ranging import PathLossModel, default_model
from .zones import resolve_zone

DEVICE_HEIGHT_M = 1.2


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Calibrated models per AP id. Any AP without one falls back to a wide-sigma default,
    which lets it contribute without letting it dominate."""

    models: dict[str, PathLossModel] = field(default_factory=dict)
    device_height_m: float = DEVICE_HEIGHT_M
    min_floor_confidence: float = 0.55
    max_sigma_for_point_m: float = 15.0

    def model_for(self, ap_id: str) -> PathLossModel:
        return self.models.get(ap_id) or default_model()


@dataclass(frozen=True, slots=True)
class PipelineResult:
    estimate: PositionEstimate
    floor: FloorVerdict
    geometry: GeometryQuality | None


def _slant_to_horizontal(slant_m: float, height_difference_m: float) -> float:
    """Project a measured slant range onto the floor plane.

    RSSI measures the straight-line distance to an AP on the ceiling, but the solve happens
    in plan view. At 3 m horizontal with a 1.7 m height difference the slant range is 3.45 m
    -- a 15% overstatement that biases every nearby anchor outward if left uncorrected. The
    error vanishes at long range, which is exactly why it is easy to miss.
    """
    return math.sqrt(max(slant_m**2 - height_difference_m**2, 0.0))


def _unknown(
    target_id: str, site_id: str, at: datetime, reason: str
) -> PositionEstimate:
    return PositionEstimate(
        target_id=target_id, site_id=site_id, estimated_at=at,
        kind=SolutionKind.UNKNOWN, downgrade_reason=reason,
    )


def estimate_position(
    observations: list[ObservationEvent],
    site: Site,
    at: datetime,
    target_id: str,
    config: PipelineConfig | None = None,
) -> PipelineResult:
    config = config or PipelineConfig()

    if not observations:
        return PipelineResult(
            _unknown(target_id, site.id, at, "no observations in window"),
            FloorVerdict(None, 0.0, (), reason="no observations"),
            None,
        )

    # 1. Floor first -- it gates which observations are admissible below.
    verdict = classify_floor(observations, site, config.min_floor_confidence)
    if verdict.floor_id is None:
        return PipelineResult(
            _unknown(target_id, site.id, at, verdict.reason or "floor undetermined"),
            verdict,
            None,
        )

    def downgrade(reason: str) -> PipelineResult:
        return PipelineResult(
            PositionEstimate(
                target_id=target_id, site_id=site.id, estimated_at=at,
                kind=SolutionKind.ZONE_ONLY,
                floor_id=verdict.floor_id, floor_confidence=verdict.confidence,
                observer_count=len(observations),
                source_kinds=frozenset(o.kind for o in observations),
                downgrade_reason=reason,
            ),
            verdict,
            geometry,
        )

    geometry: GeometryQuality | None = None

    # 2. Range, using only same-floor APs.
    same_floor = observations_on_floor(observations, site, verdict.floor_id)
    ranges: list[RangeObservation] = []
    for obs in same_floor:
        if obs.kind not in ("rssi", "ble"):
            continue
        ap = site.access_point(obs.observer_id)
        model = config.model_for(ap.id)
        slant = model.distance(obs.value)
        floor_elevation = site.floor(verdict.floor_id).elevation_m
        dz = abs(ap.position.z - (floor_elevation + config.device_height_m))
        ranges.append(
            RangeObservation(
                anchor_x=ap.position.x,
                anchor_y=ap.position.y,
                distance_m=_slant_to_horizontal(slant, dz),
                sigma_m=model.distance_sigma(slant),
            )
        )

    if len(ranges) < 3:
        geometry = GeometryQuality.TOO_FEW_ANCHORS
        return downgrade(
            f"only {len(ranges)} usable same-floor range(s) on {verdict.floor_id}; "
            "at least 3 are needed for a horizontal fix"
        )

    # 3. Solve.
    fix, geometry = solve(ranges)
    if fix is None:
        return downgrade(f"geometry unusable: {geometry.value}")
    if fix.sigma_m > config.max_sigma_for_point_m:
        return downgrade(
            f"solved but 1-sigma uncertainty is {fix.sigma_m:.1f} m, beyond the "
            f"{config.max_sigma_for_point_m:.0f} m limit for reporting a point"
        )

    # 4. Zone from the mass of the ellipse, not from the centre point.
    zone = resolve_zone(fix.x, fix.y, fix.covariance, site, verdict.floor_id)

    return PipelineResult(
        PositionEstimate(
            target_id=target_id, site_id=site.id, estimated_at=at,
            kind=SolutionKind.POINT,
            x=fix.x, y=fix.y, covariance_xy=fix.covariance,
            floor_id=verdict.floor_id, floor_confidence=verdict.confidence,
            zone_id=zone.zone_id, zone_confidence=zone.confidence,
            observer_count=len(ranges),
            source_kinds=frozenset(o.kind for o in same_floor),
        ),
        verdict,
        geometry,
    )
