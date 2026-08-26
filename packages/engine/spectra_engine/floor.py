"""Which floor is the target on? Pure.

Floor is a **classification**, never a regression (ADR 0001). With ceiling-mounted APs the
anchors are coplanar, so there is no vertical baseline to solve against and any metric z
would be a floor prior wearing units.

The default classifier here is deliberately not machine learning. It is a weighted vote
over the floors of the APs that can hear the target -- the "BSSID membership" baseline the
council raised as the thing everyone forgets to try. In most enterprise deployments AP-to-
floor membership is *known* from the inventory, and the strongest APs are overwhelmingly on
the target's own floor, because a concrete slab costs far more attenuation than horizontal
distance across an open floorplate.

Two reasons it leads rather than a learned model:

  * it needs no training data, no site survey and no retraining when APs move;
  * it degrades **visibly**. When the vote is close it says so, and the pipeline can emit
    `unknown`. A learned classifier under distribution shift degrades silently, which is
    the failure mode that quietly poisons everything downstream.

It is also the control that any learned classifier must beat before it earns its place.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from spectra_core.models import ObservationEvent, Site

MIN_CONFIDENCE = 0.55


@dataclass(frozen=True, slots=True)
class FloorVerdict:
    """`floor_id is None` means the evidence did not support a decision."""

    floor_id: str | None
    confidence: float
    scores: tuple[tuple[str, float], ...]
    reason: str | None = None


def _linear_power(rssi_dbm: float) -> float:
    """Convert dBm to linear power so that voting is not distorted by a log scale.

    Summing dBm values is meaningless -- they are logarithms. In linear power a signal
    10 dB stronger counts ten times as much, which is the weighting actually wanted.
    """
    return float(10.0 ** (rssi_dbm / 10.0))


def classify_floor(
    observations: list[ObservationEvent],
    site: Site,
    min_confidence: float = MIN_CONFIDENCE,
) -> FloorVerdict:
    """Weighted membership vote over the floors of the observing APs."""
    if not observations:
        return FloorVerdict(None, 0.0, (), reason="no observations")

    weights: dict[str, float] = defaultdict(float)
    for obs in observations:
        if obs.kind not in ("rssi", "ble"):
            continue
        try:
            ap = site.access_point(obs.observer_id)
        except KeyError:
            continue  # an AP not in the inventory cannot vote on a floor
        weights[ap.floor_id] += _linear_power(obs.value)

    if not weights:
        return FloorVerdict(None, 0.0, (), reason="no observations from known access points")

    total = sum(weights.values())
    scores = tuple(sorted(((fid, w / total) for fid, w in weights.items()), key=lambda kv: -kv[1]))
    best_id, best_share = scores[0]

    if best_share < min_confidence:
        runner_up = scores[1][0] if len(scores) > 1 else "none"
        return FloorVerdict(
            None,
            best_share,
            scores,
            reason=(
                f"floor vote inconclusive: {best_id} at {best_share:.0%} versus "
                f"{runner_up}, below the {min_confidence:.0%} threshold"
            ),
        )
    return FloorVerdict(best_id, best_share, scores)


def observations_on_floor(
    observations: list[ObservationEvent], site: Site, floor_id: str
) -> list[ObservationEvent]:
    """Keep only observations from APs mounted on the given floor.

    Used to condition the horizontal solve once the floor is known. Cross-floor
    observations are heavily attenuated by the slab, so their implied ranges are far too
    long and would pull the fix outward.
    """
    keep = []
    for obs in observations:
        try:
            ap = site.access_point(obs.observer_id)
        except KeyError:
            continue
        if ap.floor_id == floor_id:
            keep.append(obs)
    return keep
