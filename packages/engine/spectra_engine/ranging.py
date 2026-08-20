"""RSSI to distance, with an honest uncertainty attached. Pure.

The path-loss model is trivial to write and easy to get subtly wrong in two ways this
module is built to prevent.

**Never assume the constants.** `A` (RSSI at 1 m) and `n` (path-loss exponent) vary per AP
with transmit power, antenna, mounting and what is in the way. A system running on a
textbook n=2.0 indoors is not approximating, it is guessing. `fit_path_loss` learns them
per AP, and an uncalibrated AP is reported as such rather than silently defaulted.

**Propagate the uncertainty properly.** Because distance is exponential in RSSI, a fixed
few-dB error becomes a *proportionally* larger distance error the further away you are.
Differentiating the inversion gives

    sigma_d = d * ln(10) / (10 n) * sigma_rssi

so a 4 dB error is worth about 0.33 m at 1 m, and 13 m at 40 m. That is the term that
justifies distance-dependent weighting in the solver -- without it, a far anchor with a
wild reading counts as much as a near one that is nearly right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

LN10 = math.log(10.0)
DEFAULT_RSSI_SIGMA_DB = 4.0


@dataclass(frozen=True, slots=True)
class PathLossModel:
    """Per-AP fitted model. `reference_power_dbm` is A, `exponent` is n."""

    reference_power_dbm: float
    exponent: float
    reference_distance_m: float = 1.0
    rssi_sigma_db: float = DEFAULT_RSSI_SIGMA_DB
    sample_count: int = 0

    def __post_init__(self) -> None:
        if self.exponent <= 0:
            raise ValueError("path-loss exponent must be positive")
        if self.reference_distance_m <= 0:
            raise ValueError("reference distance must be positive")
        if self.rssi_sigma_db <= 0:
            raise ValueError("rssi_sigma_db must be positive")

    def expected_rssi(self, distance_m: float) -> float:
        d = max(distance_m, self.reference_distance_m)
        return self.reference_power_dbm - 10.0 * self.exponent * math.log10(
            d / self.reference_distance_m
        )

    def distance(self, rssi_dbm: float) -> float:
        exponent = (self.reference_power_dbm - rssi_dbm) / (10.0 * self.exponent)
        return float(self.reference_distance_m * (10.0**exponent))

    def distance_sigma(self, distance_m: float) -> float:
        """First-order propagation of RSSI noise into distance error.

        Floored at 0.3 m: the linearisation understates the error at very short range, and
        claiming centimetre precision from RSSI would be absurd regardless of the algebra.
        """
        sigma = distance_m * LN10 / (10.0 * self.exponent) * self.rssi_sigma_db
        return max(sigma, 0.3)


class CalibrationError(ValueError):
    """Raised when a fit cannot be trusted. Never returns a fallback constant."""


def fit_path_loss(
    samples: list[tuple[float, float]],
    reference_distance_m: float = 1.0,
    min_samples: int = 6,
) -> PathLossModel:
    """Least-squares fit of A and n to (distance_m, rssi_dbm) calibration pairs.

    The model is linear in ``log10(d / d0)``, so this is an ordinary linear regression --
    no iteration, no starting guess, no local minima.

    Raises rather than degrading. A bad fit that silently returns plausible constants
    would poison every range this AP ever produces, and would be nearly impossible to
    trace back from the position error it caused.
    """
    usable = [(d, r) for d, r in samples if d > 0 and -120.0 <= r <= 0.0]
    if len(usable) < min_samples:
        raise CalibrationError(
            f"need at least {min_samples} usable calibration samples, got {len(usable)}"
        )

    log_d = np.array([math.log10(d / reference_distance_m) for d, _ in usable])
    rssi = np.array([r for _, r in usable])
    if np.ptp(log_d) < 0.15:
        raise CalibrationError(
            "calibration samples span too narrow a distance range to identify the "
            "exponent; measure across a wider spread of distances"
        )

    slope, intercept = np.polyfit(log_d, rssi, 1)
    exponent = -slope / 10.0
    if not math.isfinite(exponent) or exponent <= 0:
        raise CalibrationError(
            f"fitted path-loss exponent {exponent:.3f} is non-physical; signal should "
            "weaken with distance, so check for mislabelled calibration points"
        )

    predicted = intercept + slope * log_d
    residual_sigma = float(np.std(rssi - predicted, ddof=1)) if len(usable) > 2 else 0.0
    return PathLossModel(
        reference_power_dbm=float(intercept),
        exponent=float(exponent),
        reference_distance_m=reference_distance_m,
        rssi_sigma_db=max(residual_sigma, 1.0),
        sample_count=len(usable),
    )


def default_model(rssi_sigma_db: float = DEFAULT_RSSI_SIGMA_DB) -> PathLossModel:
    """A deliberately mediocre uncalibrated fallback.

    Use only when an AP has no calibration data and the alternative is no estimate at all.
    The wide sigma is the point: it tells the solver to distrust these ranges, so an
    uncalibrated AP degrades the fix gently instead of corrupting it confidently.
    """
    return PathLossModel(
        reference_power_dbm=-40.0, exponent=2.8, rssi_sigma_db=max(rssi_sigma_db, 6.0)
    )
