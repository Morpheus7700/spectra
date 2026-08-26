"""Horizontal position from a set of ranges. Pure -- no I/O, no clock, no logging.

Solves in **2D only**, deliberately. With ceiling-mounted APs the anchors are coplanar,
so z has no observable gradient in the range residuals and any z we produced would be a
floor prior dressed up in metres (ADR 0001). Floor is classified elsewhere.

Three decisions that matter more than they look:

  * **Robust loss.** RSSI range error is heavy-tailed -- one AP behind a lift shaft is not
    a 2-sigma event, it is a different distribution. Plain least squares lets that single
    anchor drag the fix several metres, so we use a soft-L1 loss.
  * **Weighting by distance.** Range error from RSSI grows roughly in proportion to
    distance, so a 40 m range is far less informative than a 4 m one and must not be
    allowed to count equally.
  * **Refusing to solve.** Fewer than three anchors, or anchors that are nearly collinear,
    leave the position under-determined along some direction. We detect that and say so
    rather than returning a confident-looking point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.optimize import least_squares

MIN_ANCHORS = 3
COLLINEARITY_RATIO = 0.06


class GeometryQuality(StrEnum):
    OK = "ok"
    TOO_FEW_ANCHORS = "too_few_anchors"
    COLLINEAR_ANCHORS = "collinear_anchors"


@dataclass(frozen=True, slots=True)
class RangeObservation:
    """One anchor, one estimated distance, and how much to trust it."""

    anchor_x: float
    anchor_y: float
    distance_m: float
    sigma_m: float

    def __post_init__(self) -> None:
        if self.sigma_m <= 0:
            raise ValueError("sigma_m must be positive")
        if self.distance_m < 0:
            raise ValueError("distance_m cannot be negative")


@dataclass(frozen=True, slots=True)
class Fix:
    x: float
    y: float
    covariance: tuple[tuple[float, float], tuple[float, float]]
    residual_rms_m: float
    anchors_used: int

    @property
    def sigma_m(self) -> float:
        (a, b), (_, d) = self.covariance
        trace, det = a + d, a * d - b * b
        return float(np.sqrt(max(trace / 2 + np.sqrt(max(trace**2 / 4 - det, 0.0)), 0.0)))


def assess_geometry(
    anchors: np.ndarray, ratio_threshold: float = COLLINEARITY_RATIO
) -> GeometryQuality:
    """Is this anchor arrangement capable of pinning down a 2D point?

    Collinearity is measured by the singular values of the centred anchor matrix: when the
    smaller one is negligible relative to the larger, every anchor lies essentially on one
    line and position along that line is unconstrained. A ratio test is used rather than an
    absolute area because it is scale-free -- three APs in a 2 m corridor are as collinear
    as three across a 200 m warehouse.
    """
    unique = np.unique(anchors, axis=0)
    if len(unique) < MIN_ANCHORS:
        return GeometryQuality.TOO_FEW_ANCHORS
    centred = unique - unique.mean(axis=0)
    singular = np.linalg.svd(centred, compute_uv=False)
    if singular[0] <= 0:
        return GeometryQuality.COLLINEAR_ANCHORS
    if singular[1] / singular[0] < ratio_threshold:
        return GeometryQuality.COLLINEAR_ANCHORS
    return GeometryQuality.OK


def _linear_seed(anchors: np.ndarray, distances: np.ndarray) -> np.ndarray:
    """Closed-form linear least squares, used to seed the nonlinear solve.

    Subtracting one anchor's equation from the rest cancels the quadratic term. It is
    biased in the presence of noise, which is why it is only a starting point -- but it is
    a good enough one to keep the nonlinear solve out of local minima.
    """
    ref = anchors[0]
    a = 2.0 * (anchors[1:] - ref)
    b = distances[0] ** 2 - distances[1:] ** 2 + np.sum(anchors[1:] ** 2, axis=1) - np.sum(ref**2)
    solution, *_ = np.linalg.lstsq(a, b, rcond=None)
    return np.asarray(solution, dtype=float)


def solve(
    observations: list[RangeObservation],
    ratio_threshold: float = COLLINEARITY_RATIO,
) -> tuple[Fix | None, GeometryQuality]:
    """Return a fix, or None with the reason the geometry did not support one."""
    if len(observations) < MIN_ANCHORS:
        return None, GeometryQuality.TOO_FEW_ANCHORS

    anchors = np.array([[o.anchor_x, o.anchor_y] for o in observations], dtype=float)
    distances = np.array([o.distance_m for o in observations], dtype=float)
    sigmas = np.array([o.sigma_m for o in observations], dtype=float)

    quality = assess_geometry(anchors, ratio_threshold)
    if quality is not GeometryQuality.OK:
        return None, quality

    def residuals(p: np.ndarray) -> np.ndarray:
        predicted = np.linalg.norm(anchors - p, axis=1)
        return np.asarray((predicted - distances) / sigmas, dtype=float)

    def jacobian(p: np.ndarray) -> np.ndarray:
        delta = p - anchors
        norms = np.linalg.norm(delta, axis=1)
        norms = np.where(norms < 1e-9, 1e-9, norms)
        return np.asarray((delta / norms[:, None]) / sigmas[:, None], dtype=float)

    seed = _linear_seed(anchors, distances)
    if not np.all(np.isfinite(seed)):
        seed = anchors.mean(axis=0)

    result = least_squares(residuals, seed, jac=jacobian, loss="soft_l1", f_scale=1.0, max_nfev=200)
    if not result.success and result.status <= 0:
        return None, GeometryQuality.COLLINEAR_ANCHORS

    covariance = _covariance_from_jacobian(
        np.asarray(result.jac, dtype=float), np.asarray(result.fun, dtype=float)
    )
    if covariance is None:
        return None, GeometryQuality.COLLINEAR_ANCHORS

    rms = float(np.sqrt(np.mean((result.fun * sigmas) ** 2)))
    (a, b), (c, d) = covariance
    fix = Fix(
        x=float(result.x[0]),
        y=float(result.x[1]),
        covariance=((float(a), float(b)), (float(c), float(d))),
        residual_rms_m=rms,
        anchors_used=len(observations),
    )
    return fix, GeometryQuality.OK


def _covariance_from_jacobian(
    jac: np.ndarray, residuals: np.ndarray
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Parameter covariance ``(JᵀJ)⁻¹``, scaled by the observed residual spread.

    Residuals are already divided by each range's sigma, so ``(JᵀJ)⁻¹`` is the covariance
    if those sigmas were right. They rarely are exactly, so it is rescaled by the reduced
    chi-square -- when the fit is worse than the assumed noise predicted, the reported
    uncertainty grows to match. An estimate that under-reports its own error is worse than
    no estimate at all.
    """
    try:
        hessian = jac.T @ jac
        cov = np.linalg.inv(hessian)
    except np.linalg.LinAlgError:
        return None

    dof = max(len(residuals) - 2, 1)
    scale = float(residuals @ residuals) / dof

    # Floor the rescale at 1.0, NOT at ~0.
    #
    # Residuals are already divided by each range's sigma, so (JᵀJ)⁻¹ is proportional to
    # sigma² while the reduced chi-square is proportional to 1/sigma². Left unfloored the
    # two cancel exactly: the declared measurement noise stops influencing the answer, and
    # a fit that happens to land on low residuals reports arbitrarily small uncertainty.
    # Measured before this floor: declaring 0.5 m per-range sigma gave 2.20 m, declaring
    # 20 m gave 0.82 m — worse input producing a more confident output — and exact ranges
    # reported 3.3 mm from RSSI multilateration.
    #
    # Flooring at 1.0 means: never claim better precision than the a-priori noise supports.
    # Residuals smaller than the declared noise are luck, not information. The estimator
    # may still inflate above 1.0 when the fit is worse than predicted, which is the
    # behaviour we actually wanted from the rescale.
    cov = cov * max(scale, 1.0)

    if not np.all(np.isfinite(cov)) or cov[0, 0] <= 0 or cov[1, 1] <= 0:
        return None
    if np.linalg.det(cov) <= 0:
        return None
    symmetric = (cov + cov.T) / 2.0
    return (
        (float(symmetric[0, 0]), float(symmetric[0, 1])),
        (float(symmetric[1, 0]), float(symmetric[1, 1])),
    )
