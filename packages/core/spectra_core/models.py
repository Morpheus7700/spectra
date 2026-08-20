"""The domain contract every part of Spectra agrees on.

Two types carry the whole system:

  ObservationEvent   what a sensor saw          (adapters -> engine)
  PositionEstimate   where the engine thinks it is (engine -> API -> viewer)

Adapters know vendors. The engine knows geometry. Neither knows the other. Everything
crosses that boundary as ObservationEvent, which is why the engine cannot tell a UniFi
controller from an ESP32 from the simulator.

Per ADR 0001, a PositionEstimate carries a **2D** covariance and a **discrete** floor.
There is deliberately no z field to regress: with ceiling-mounted APs on one plane the
anchors are coplanar, z has no observable gradient in the range residuals, and any z we
emitted would be a floor prior wearing metric units.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Metres = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Vec3(Frozen):
    """A point in the site coordinate frame. Metres, right-handed, +z up."""

    x: Metres
    y: Metres
    z: Metres = 0.0

    def distance_to(self, other: Vec3) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))

    def horizontal_distance_to(self, other: Vec3) -> float:
        return math.dist((self.x, self.y), (other.x, other.y))


# --------------------------------------------------------------------------- observations

ObservationKind = Literal["rssi", "rtt", "csi", "ble"]


class ObservationEvent(Frozen):
    """One measurement of one target by one observer.

    `value` units depend on `kind`: dBm for rssi/ble, millimetres for rtt. `variance` is in
    the square of those units and is optional only because some vendors do not report it;
    the ranging stage supplies a fitted default when it is absent.
    """

    observer_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    observed_at: datetime
    kind: ObservationKind
    value: float = Field(allow_inf_nan=False)
    variance: float | None = Field(default=None, ge=0.0)
    channel: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_plausible_range(self) -> Self:
        if self.kind in ("rssi", "ble") and not (-120.0 <= self.value <= 0.0):
            raise ValueError(
                f"{self.kind} value {self.value} dBm is outside the physically plausible "
                "range [-120, 0]; a positive value usually means an unremapped sentinel "
                "(UJIIndoorLoc encodes 'not detected' as 100)"
            )
        if self.kind == "rtt" and self.value < 0.0:
            raise ValueError(f"rtt distance {self.value} mm cannot be negative")
        return self


# --------------------------------------------------------------------------- site model


class Floor(Frozen):
    id: str = Field(min_length=1)
    level: int
    elevation_m: Metres
    height_m: Metres = Field(default=3.0, gt=0.0)
    name: str = ""


class AccessPoint(Frozen):
    """An observer with a known position.

    `path_loss_a` and `path_loss_n` are fitted per AP from calibration data. They default to
    None precisely so that an uncalibrated AP is a visible absence rather than a silent
    textbook constant -- an indoor system running on n=2.0 is a defect, not a default.
    """

    id: str = Field(min_length=1)
    position: Vec3
    floor_id: str = Field(min_length=1)
    path_loss_a: float | None = None
    path_loss_n: float | None = Field(default=None, gt=0.0)
    supports_ftm: bool = False

    @property
    def is_calibrated(self) -> bool:
        return self.path_loss_a is not None and self.path_loss_n is not None


class Zone(Frozen):
    """A named region on one floor, as a simple polygon in site coordinates."""

    id: str = Field(min_length=1)
    floor_id: str = Field(min_length=1)
    name: str = ""
    polygon: tuple[tuple[float, float], ...] = Field(min_length=3)


class Site(Frozen):
    id: str = Field(min_length=1)
    name: str = ""
    floors: tuple[Floor, ...] = Field(min_length=1)
    access_points: tuple[AccessPoint, ...] = ()
    zones: tuple[Zone, ...] = ()

    @model_validator(mode="after")
    def _check_referential_integrity(self) -> Self:
        floor_ids = {f.id for f in self.floors}
        if len(floor_ids) != len(self.floors):
            raise ValueError("duplicate floor id")
        for ap in self.access_points:
            if ap.floor_id not in floor_ids:
                raise ValueError(f"access point {ap.id!r} references unknown floor {ap.floor_id!r}")
        for zone in self.zones:
            if zone.floor_id not in floor_ids:
                raise ValueError(f"zone {zone.id!r} references unknown floor {zone.floor_id!r}")
        return self

    def floor(self, floor_id: str) -> Floor:
        for f in self.floors:
            if f.id == floor_id:
                return f
        raise KeyError(floor_id)

    def access_point(self, ap_id: str) -> AccessPoint:
        for ap in self.access_points:
            if ap.id == ap_id:
                return ap
        raise KeyError(ap_id)


# --------------------------------------------------------------------------- estimates


class SolutionKind(StrEnum):
    """How much the geometry actually supported.

    The downgrade path is explicit and typed so that a refusal to solve is legible to
    callers rather than disguised as a confident answer.
    """

    POINT = "point"
    ZONE_ONLY = "zone_only"
    UNKNOWN = "unknown"


class PositionEstimate(Frozen):
    target_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    estimated_at: datetime
    kind: SolutionKind

    x: Metres | None = None
    y: Metres | None = None
    covariance_xy: tuple[tuple[float, float], tuple[float, float]] | None = None

    floor_id: str | None = None
    floor_confidence: Probability = 0.0
    zone_id: str | None = None
    zone_confidence: Probability = 0.0

    observer_count: int = Field(default=0, ge=0)
    source_kinds: frozenset[ObservationKind] = frozenset()
    downgrade_reason: str | None = None

    @model_validator(mode="after")
    def _check_kind_matches_payload(self) -> Self:
        if self.kind is SolutionKind.POINT:
            if self.x is None or self.y is None or self.covariance_xy is None:
                raise ValueError("POINT estimate requires x, y and covariance_xy")
            _validate_covariance(self.covariance_xy)
        else:
            if self.x is not None or self.y is not None:
                raise ValueError(f"{self.kind} estimate must not carry coordinates")
            if self.covariance_xy is not None:
                raise ValueError(f"{self.kind} estimate must not carry a covariance")
            if self.downgrade_reason is None:
                raise ValueError(
                    f"{self.kind} estimate must record why it was downgraded -- a silent "
                    "downgrade is indistinguishable from a bug"
                )
        if self.kind is SolutionKind.UNKNOWN and self.floor_id is not None:
            raise ValueError("UNKNOWN estimate cannot claim a floor")
        return self

    @property
    def horizontal_sigma_m(self) -> float | None:
        """Radius of the 1-sigma uncertainty circle, for rendering and for reporting."""
        if self.covariance_xy is None:
            return None
        (a, b), (_, d) = self.covariance_xy
        trace, det = a + d, a * d - b * b
        larger = trace / 2 + math.sqrt(max(trace * trace / 4 - det, 0.0))
        return math.sqrt(max(larger, 0.0))


def _validate_covariance(cov: tuple[tuple[float, float], tuple[float, float]]) -> None:
    (a, b), (c, d) = cov
    if not math.isclose(b, c, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"covariance must be symmetric, got off-diagonals {b} and {c}")
    if a <= 0 or d <= 0:
        raise ValueError(f"covariance diagonal must be positive, got {a} and {d}")
    if a * d - b * c <= 0:
        raise ValueError("covariance must be positive-definite (non-positive determinant)")
