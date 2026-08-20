"""Turns a true position into the observations an infrastructure would actually report.

Emits `ObservationEvent` through the same interface every real adapter uses, so the engine
genuinely cannot tell simulated data from a UniFi controller. That property is what makes
the accuracy gate meaningful rather than circular.

Two error terms, deliberately separated:

  * shadowing -- spatially correlated, does not average out while you stand still
  * fast fading -- independent per sample, does average out

Conflating them is the mistake that makes a solver look better than it is.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime

from spectra_core.models import ObservationEvent, Site

from .propagation import PropagationParams, Segment, expected_rssi
from .shadowing import ShadowingField


def _iid_normal(*key: object) -> float:
    digest = hashlib.blake2b(repr(("fade", *key)).encode(), digest_size=16).digest()
    a, b = struct.unpack("<QQ", digest)
    u1 = (a / 2**64) or 1e-12
    u2 = b / 2**64
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


@dataclass(slots=True)
class Sensor:
    """Infrastructure-side observation source for one site."""

    site: Site
    params: PropagationParams = field(default_factory=PropagationParams)
    shadowing: ShadowingField = field(default_factory=ShadowingField)
    walls: dict[str, tuple[Segment, ...]] = field(default_factory=dict)
    fast_fading_sigma_db: float = 1.5
    rssi_quantisation_db: float = 1.0
    max_range_m: float = 60.0

    def true_rssi(
        self, ap_id: str, xy: tuple[float, float], floor_id: str, sample_key: object
    ) -> float | None:
        """RSSI including both error terms, or None if below the noise floor."""
        ap = self.site.access_point(ap_id)
        ap_level = self.site.floor(ap.floor_id).level
        target_level = self.site.floor(floor_id).level

        mean = expected_rssi(
            self.params,
            ap_xy=(ap.position.x, ap.position.y),
            ap_floor_level=ap_level,
            target_xy=xy,
            target_floor_level=target_level,
            walls=self.walls.get(ap.floor_id, ()) if ap_level == target_level else (),
        )
        mean += self.shadowing.value(ap_id, xy[0], xy[1], target_level)
        if self.fast_fading_sigma_db > 0:
            mean += self.fast_fading_sigma_db * _iid_normal(ap_id, sample_key)

        if self.rssi_quantisation_db > 0:
            mean = round(mean / self.rssi_quantisation_db) * self.rssi_quantisation_db
        if mean < self.params.noise_floor_dbm:
            return None
        return max(mean, -120.0)

    def observe(
        self,
        target_id: str,
        xy: tuple[float, float],
        floor_id: str,
        at: datetime,
    ) -> list[ObservationEvent]:
        """Every AP that can hear the target reports it. APs that cannot, say nothing.

        The silence is informative -- an AP two floors away failing to report is evidence
        about which floor the target is on, and the engine is entitled to use it.
        """
        events: list[ObservationEvent] = []
        for ap in self.site.access_points:
            if math.dist((ap.position.x, ap.position.y), xy) > self.max_range_m:
                continue
            rssi = self.true_rssi(ap.id, xy, floor_id, sample_key=(target_id, at.isoformat()))
            if rssi is None:
                continue
            events.append(
                ObservationEvent(
                    observer_id=ap.id,
                    target_id=target_id,
                    observed_at=at,
                    kind="rssi",
                    value=rssi,
                )
            )
        return events
