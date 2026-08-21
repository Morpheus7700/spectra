"""The accuracy regression gate: the tests that turn "is it accurate?" into a build failure.

Split out of test_pipeline.py because this suite is a different kind of thing. The tests
there are fast and assert behaviour; these are slow (~18 s), seeded, and every threshold in
them is a published claim about how wrong the engine is. They carry the `accuracy` marker so
CI can run them as their own step -- before this marker existed that step collected nothing,
exited 5, and printed "PASS (vacuous)" on every run while the gate quietly executed inside
the "not accuracy" step instead.

R13: every figure here describes how well the solver inverts OUR OWN propagation model.
None of it is a claim about a real building.

R14: distributions, never a mean, and always alongside coverage -- an excellent p50 achieved
by refusing to answer is not a result.
"""

from __future__ import annotations

import pytest
from spectra_engine.accuracy import AccuracyReport

from tools.accuracy_baseline import GATE_SEEDS, load_baseline, measure

pytestmark = pytest.mark.accuracy

# How far a pooled figure may drift from the committed baseline before the build fails.
#
# This is the gate's real regression detector, and it is sized against a measured mutant
# rather than chosen for roundness. Removing the slant-range correction at the call site
# (mutant M1') moves the pooled p50 by +16.0% while the worst per-seed p50 reaches only
# 3.09 m -- comfortably under the 3.5 m absolute ceiling below. So the absolute ceilings
# cannot see M1' at all, and 8% catches it with 2x headroom.
RATCHET = 0.08


@pytest.fixture(scope="module")
def gate() -> tuple[list[AccuracyReport], AccuracyReport]:
    """Per-seed reports and their pooled union. Module-scoped: this costs ~18 s."""
    return measure()


@pytest.fixture(scope="module")
def baseline() -> dict[str, float]:
    return load_baseline()


# --------------------------------------------------------------- absolute ceilings
#
# These answer "is the engine acceptable?" and are deliberately generous -- they are the
# floor of publishable behaviour, not a description of current performance. The ratchet
# below is what notices a regression. Both are needed: a ratchet alone would happily
# accept a slow slide re-baselined step by step, and a ceiling alone misses M1'.


def test_per_seed_p50_stays_under_the_ceiling(gate: tuple[list[AccuracyReport], object]) -> None:
    """Median horizontal error, every seed. Observed 1.54-2.61 m across the 20 seeds."""
    for seed, report in zip(GATE_SEEDS, gate[0], strict=True):
        assert report.p50 < 3.5, f"seed {seed}: {report.summary()}"


def test_per_seed_p95_stays_under_the_ceiling(gate: tuple[list[AccuracyReport], object]) -> None:
    """The tail is what a user actually feels, so it is gated separately. Observed 4.10-6.60 m."""
    for seed, report in zip(GATE_SEEDS, gate[0], strict=True):
        assert report.p95 < 8.0, f"seed {seed}: {report.summary()}"


def test_per_seed_coverage_stays_high(gate: tuple[list[AccuracyReport], object]) -> None:
    """Read with the error figures, never alone. Observed 99.07-100%."""
    for seed, report in zip(GATE_SEEDS, gate[0], strict=True):
        assert report.coverage >= 0.95, f"seed {seed}: {report.summary()}"


def test_per_seed_floor_classification_is_near_perfect(
    gate: tuple[list[AccuracyReport], object],
) -> None:
    """The BSSID-membership vote weighted by linear power. Observed 100% on every seed.

    A drop here means the vote broke, not that positioning got worse -- floor is classified
    by a separate mechanism entirely (ADR 0001).
    """
    for seed, report in zip(GATE_SEEDS, gate[0], strict=True):
        assert report.floor_accuracy >= 0.95, f"seed {seed}: {report.summary()}"
        assert report.floor_decision_rate >= 0.90, f"seed {seed}: {report.summary()}"


# --------------------------------------------------------------------- the ratchet
#
# Pooled over 2140 samples rather than per-seed. A per-seed gate asks "is any single run
# bad?", which a uniform degradation survives; the pooled p50 is the figure stable enough
# to compare against a committed number.


def test_pooled_p50_has_not_regressed(
    gate: tuple[list[AccuracyReport], AccuracyReport], baseline: dict[str, float]
) -> None:
    """The one assertion that catches M1'. See RATCHET above for why 8%."""
    limit = baseline["p50_m"] * (1 + RATCHET)
    assert gate[1].p50 <= limit, (
        f"pooled p50 {gate[1].p50:.3f} m exceeds baseline "
        f"{baseline['p50_m']:.3f} m by more than {RATCHET:.0%} (limit {limit:.3f} m). "
        f"If this is a deliberate trade, regenerate with "
        f"`uv run python -m tools.accuracy_baseline` and say so in the commit message."
    )


def test_pooled_coverage_has_not_regressed(
    gate: tuple[list[AccuracyReport], AccuracyReport], baseline: dict[str, float]
) -> None:
    """Coverage ratchets downward, so it is a floor rather than a ceiling.

    Paired with the p50 ratchet deliberately: the cheapest way to improve p50 is to refuse
    the hard fixes, and that shows up here rather than there.
    """
    floor = baseline["coverage"] * (1 - RATCHET)
    assert gate[1].coverage >= floor, (
        f"pooled coverage {gate[1].coverage:.4f} fell more than {RATCHET:.0%} below "
        f"baseline {baseline['coverage']:.4f} (floor {floor:.4f})"
    )


def test_the_committed_baseline_is_not_stale(
    gate: tuple[list[AccuracyReport], AccuracyReport], baseline: dict[str, float]
) -> None:
    """The baseline must describe the run the gate actually performs.

    Without this, editing GATE_SEEDS silently compares a 20-seed run against a 3-seed
    baseline and the ratchet becomes noise. Sample count is checked rather than the seed
    list alone because a scenario change alters the samples per seed too.
    """
    assert list(baseline["seeds"]) == list(GATE_SEEDS)  # type: ignore[call-overload]
    assert baseline["samples"] == gate[1].samples


# ------------------------------------------------------------ is the uncertainty honest?


def test_per_seed_uncertainty_is_not_overconfident(
    gate: tuple[list[AccuracyReport], object],
) -> None:
    """Reported sigma must track observed error (R10), gated at the R10 band 0.5-2.0.

    calibration_ratio is median(error) / median(reported 1-sigma). For an isotropic 2D
    Gaussian a perfectly calibrated engine lands near 1.18 (the Rayleigh median).

    Observed across these 20 seeds: 0.62-1.06, pooled 0.79. That is BELOW 1.18, meaning the
    engine currently draws volumes larger than the error distribution justifies -- it is
    under-confident, not over-confident. Under R10 that is the safe direction ("an
    overconfident display lies; a vague one merely underwhelms"), but it is still
    miscalibration and is recorded as an open finding rather than blessed by this band.

    The band is deliberately NOT tightened toward 1.18. Reintroducing mutant M1' moves the
    ratio from 0.79 to 0.95 -- a real accuracy regression that a tighter band would score as
    an improvement. A gate that rewards a known defect is worse than a loose one.
    """
    for seed, report in zip(GATE_SEEDS, gate[0], strict=True):
        assert 0.5 <= report.calibration_ratio <= 2.0, f"seed {seed}: {report.summary()}"


def test_fitting_path_loss_per_ap_earns_its_complexity() -> None:
    """R9 says fit A and n per AP rather than assuming them. This is that claim, tested.

    One seed, not the full sweep: this compares two configurations on identical input, so
    the seed cancels out of the comparison and 20 of them would only cost time.
    """
    from spectra_engine.accuracy import calibrate_from_truth, evaluate
    from spectra_engine.pipeline import PipelineConfig
    from spectra_sim.scenario import office_walk

    scenario = office_walk(seed=1)
    paired = scenario.paired()
    models = calibrate_from_truth(paired, scenario.site)
    naive = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig())
    fitted = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
    assert fitted.p95 < naive.p95, f"fitted {fitted.summary()} vs naive {naive.summary()}"
    assert fitted.coverage >= naive.coverage
