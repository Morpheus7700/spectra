"""Measuring how wrong the engine is. Pure.

This is the instrument the whole project is steered by, so it reports a distribution rather
than a mean. A mean hides the tail, and the tail is what a user actually experiences: a
system with 3 m median error and 40 m p95 feels broken, while 4 m median and 7 m p95 feels
fine. Optimising the mean would prefer the first.

Coverage is reported alongside error, and the two must always be read together. A pipeline
that refuses to answer unless the geometry is perfect can post an excellent p50 while being
useless, so an accuracy figure without its coverage is not a result.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from spectra_core.models import ObservationEvent, Site, SolutionKind

from .multilateration import GeometryQuality
from .pipeline import PipelineConfig, estimate_position
from .ranging import PathLossModel, fit_path_loss
from .zones import zone_containing


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the engine decided for one sample, in machine-readable terms.

    `PositionEstimate.downgrade_reason` is prose built with f-strings, so it embeds
    distances and counts and has effectively unbounded cardinality -- useless to count.
    `SolutionKind` and `GeometryQuality` are the two enums that actually carry the
    decision, so the distribution is taken over those and the prose stays for humans.

    `geometry` is None when the solver was never reached (no observations, or the floor
    was undecidable), which is a genuinely different refusal from "the geometry was bad".
    """

    kind: SolutionKind
    geometry: GeometryQuality | None


@dataclass(frozen=True, slots=True)
class AccuracyReport:
    samples: int
    located: int
    horizontal_errors_m: tuple[float, ...]
    floor_correct: int
    floor_decided: int
    zone_correct: int
    zone_decided: int
    sigma_reported_m: tuple[float, ...]
    outcomes: tuple[Outcome, ...]

    @property
    def coverage(self) -> float:
        """Share of samples that produced a point estimate at all."""
        return self.located / self.samples if self.samples else 0.0

    @property
    def floor_accuracy(self) -> float:
        return self.floor_correct / self.floor_decided if self.floor_decided else 0.0

    @property
    def floor_decision_rate(self) -> float:
        return self.floor_decided / self.samples if self.samples else 0.0

    @property
    def zone_accuracy(self) -> float:
        return self.zone_correct / self.zone_decided if self.zone_decided else 0.0

    @property
    def refusal_rate(self) -> float:
        """Share of samples the engine declined to place (R8 refusals, not failures)."""
        return 1.0 - self.coverage

    def refusal_mix(self) -> dict[tuple[str, str | None], int]:
        """Counts of every (kind, geometry) pair that was NOT a point solution.

        Gated as a distribution rather than a total because the total is nearly
        conserved: a change that converts every COLLINEAR_ANCHORS refusal into a
        TOO_FEW_ANCHORS refusal leaves coverage, p50 and the refusal rate all identical.
        """
        counts: Counter[tuple[str, str | None]] = Counter()
        for outcome in self.outcomes:
            if outcome.kind is SolutionKind.POINT:
                continue
            counts[(outcome.kind.value, outcome.geometry.value if outcome.geometry else None)] += 1
        return dict(counts)

    def percentile(self, p: float) -> float:
        if not self.horizontal_errors_m:
            return math.inf
        ordered = sorted(self.horizontal_errors_m)
        k = (len(ordered) - 1) * (p / 100.0)
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return ordered[int(k)]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def worst(self) -> float:
        return max(self.horizontal_errors_m, default=math.inf)

    @property
    def calibration_ratio(self) -> float:
        """Median actual error divided by median claimed 1-sigma.

        For a well-calibrated 2D Gaussian this lands near 1.2 (the median of a Rayleigh
        distribution is ~1.177 sigma). Much above 1 means the engine is overconfident --
        it is drawing uncertainty volumes too small to contain the truth, which is worse
        than being inaccurate, because the display would be lying rather than vague.
        """
        if not self.sigma_reported_m or not self.horizontal_errors_m:
            return math.inf
        median_sigma = statistics.median(self.sigma_reported_m)
        if median_sigma <= 0:
            return math.inf
        return statistics.median(self.horizontal_errors_m) / median_sigma

    def summary(self) -> str:
        return (
            f"samples={self.samples} coverage={self.coverage:.0%} "
            f"p50={self.p50:.2f}m p95={self.p95:.2f}m worst={self.worst:.2f}m "
            f"floor={self.floor_accuracy:.0%} (decided {self.floor_decision_rate:.0%}) "
            f"zone={self.zone_accuracy:.0%} calib={self.calibration_ratio:.2f} "
            f"refused={self.refusal_mix() or 'none'}"
        )


def pooled(reports: Sequence[AccuracyReport]) -> AccuracyReport:
    """Combine per-seed reports into one report over the union of their samples.

    Every field of AccuracyReport is either a count or a raw sample tuple, so pooling is
    addition and concatenation and every derived property follows for free. That is why
    the dataclass stores samples rather than percentiles: a percentile of percentiles is
    not a percentile of anything.

    Pooling is not decoration. A per-seed gate asks "is any single run bad?", which a
    regression can survive by degrading every run a little. The pooled p50 over ~2000
    samples is stable to the third decimal across seeds, so it is the figure a relative
    ratchet can be hung on -- and the figure the M1' mutant (+18% pooled p50) moves.
    """
    if not reports:
        raise ValueError("cannot pool an empty sequence of reports")
    return AccuracyReport(
        samples=sum(r.samples for r in reports),
        located=sum(r.located for r in reports),
        horizontal_errors_m=tuple(e for r in reports for e in r.horizontal_errors_m),
        floor_correct=sum(r.floor_correct for r in reports),
        floor_decided=sum(r.floor_decided for r in reports),
        zone_correct=sum(r.zone_correct for r in reports),
        zone_decided=sum(r.zone_decided for r in reports),
        sigma_reported_m=tuple(s for r in reports for s in r.sigma_reported_m),
        outcomes=tuple(o for r in reports for o in r.outcomes),
    )


def calibrate_from_truth(
    paired: Sequence[tuple[object, Sequence[ObservationEvent]]],
    site: Site,
    device_height_m: float = 1.2,
    min_samples: int = 6,
) -> dict[str, PathLossModel]:
    """Fit a per-AP path-loss model, as a real site survey would.

    Uses known positions to recover the true AP-to-device distance for each observation,
    which is precisely what a surveyor produces by walking to marked points. APs with too
    few or too poorly-spread samples are left out rather than given invented constants --
    the pipeline's wide-sigma fallback then handles them.
    """
    per_ap: dict[str, list[tuple[float, float]]] = {}
    for truth, observations in paired:
        tx, ty = truth.x, truth.y  # type: ignore[attr-defined]
        floor_id = truth.floor_id  # type: ignore[attr-defined]
        elevation = site.floor(floor_id).elevation_m
        for obs in observations:
            if obs.kind not in ("rssi", "ble"):
                continue
            try:
                ap = site.access_point(obs.observer_id)
            except KeyError:
                continue
            if ap.floor_id != floor_id:
                continue  # cross-floor pairs would fit the slab into the exponent
            distance = math.dist(
                (ap.position.x, ap.position.y, ap.position.z),
                (tx, ty, elevation + device_height_m),
            )
            per_ap.setdefault(ap.id, []).append((distance, obs.value))

    models: dict[str, PathLossModel] = {}
    for ap_id, samples in per_ap.items():
        try:
            models[ap_id] = fit_path_loss(samples, min_samples=min_samples)
        except ValueError:
            continue
    return models


def evaluate(
    paired: Sequence[tuple[object, Sequence[ObservationEvent]]],
    site: Site,
    target_id: str,
    config: PipelineConfig | None = None,
) -> AccuracyReport:
    errors: list[float] = []
    sigmas: list[float] = []
    outcomes: list[Outcome] = []
    located = floor_correct = floor_decided = zone_correct = zone_decided = 0

    for truth, observations in paired:
        result = estimate_position(
            list(observations), site, truth.at, target_id, config  # type: ignore[attr-defined]
        )
        est = result.estimate
        outcomes.append(Outcome(kind=est.kind, geometry=result.geometry))

        if est.floor_id is not None:
            floor_decided += 1
            if est.floor_id == truth.floor_id:  # type: ignore[attr-defined]
                floor_correct += 1

        if est.kind is not SolutionKind.POINT:
            continue
        located += 1
        assert est.x is not None and est.y is not None
        errors.append(math.dist((est.x, est.y), (truth.x, truth.y)))  # type: ignore[attr-defined]
        sigma = est.horizontal_sigma_m
        if sigma is not None:
            sigmas.append(sigma)

        if est.zone_id is not None:
            zone_decided += 1
            true_zone = zone_containing(
                site, truth.floor_id, truth.x, truth.y  # type: ignore[attr-defined]
            )
            if true_zone is not None and est.zone_id == true_zone:
                zone_correct += 1

    return AccuracyReport(
        samples=len(paired),
        located=located,
        horizontal_errors_m=tuple(errors),
        floor_correct=floor_correct,
        floor_decided=floor_decided,
        zone_correct=zone_correct,
        zone_decided=zone_decided,
        sigma_reported_m=tuple(sigmas),
        outcomes=tuple(outcomes),
    )
