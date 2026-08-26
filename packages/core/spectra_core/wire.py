"""DTOs for crossing the network boundary.

These flatten the rich domain models into stable wire formats. The engine and API use
this to ensure what gets sent over the WebSocket or HTTP endpoints is typed and safe.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import HashedTargetId, PositionEstimate, SolutionKind


class PositionEstimateWire(BaseModel):
    """The JSON payload for a position estimate reaching the frontend."""

    tenant_id: str
    target_id: HashedTargetId
    site_id: str
    estimated_at: datetime
    kind: SolutionKind

    # Coordinates in the site frame.
    x: float | None = None
    y: float | None = None

    # Raw 2D covariance matrix: ((xx, xy), (yx, yy))
    covariance_xy: tuple[tuple[float, float], tuple[float, float]] | None = None

    floor_id: str | None = None
    floor_confidence: float = 0.0
    zone_id: str | None = None
    zone_confidence: float = 0.0

    downgrade_reason: str | None = None

    @classmethod
    def from_domain(cls, est: PositionEstimate) -> PositionEstimateWire:
        return cls(
            tenant_id=est.tenant_id,
            target_id=est.target_id,
            site_id=est.site_id,
            estimated_at=est.estimated_at,
            kind=est.kind,
            x=est.x,
            y=est.y,
            covariance_xy=est.covariance_xy,
            floor_id=est.floor_id,
            floor_confidence=est.floor_confidence,
            zone_id=est.zone_id,
            zone_confidence=est.zone_confidence,
            downgrade_reason=est.downgrade_reason,
        )
