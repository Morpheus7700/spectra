"""Tests for the degenerate-geometry simulator scenarios.

These scenarios exist to make the R8 refusal paths visible to the accuracy instrument.
Before them, evaluate() ran 321 samples and saw zero refusals -- every path through
multilateration.py's assess_geometry that leads to a refusal was invisible.

Each test asserts that the scenario produces the expected refusal type, and that the
count is non-zero.  This is the anti-vacuity property: a test that verifies "refusals
are handled" without producing any is testing nothing.
"""

from __future__ import annotations

from spectra_core.models import SolutionKind
from spectra_engine.accuracy import calibrate_from_truth, evaluate
from spectra_engine.multilateration import GeometryQuality
from spectra_engine.pipeline import PipelineConfig
from spectra_sim.scenario import corridor_walk, sparse_grid, wide_open


def _refusal_summary(scenario_fn: object, seed: int = 0) -> dict[tuple[str, str | None], int]:
    """Run the calibrated pipeline over a scenario and return the refusal mix."""
    from spectra_sim.scenario import Scenario

    assert callable(scenario_fn)
    scenario: Scenario = scenario_fn(seed=seed)
    paired = scenario.paired()
    models = calibrate_from_truth(paired, scenario.site)
    report = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
    return report.refusal_mix()


class TestCorridorWalk:
    """APs along the spine of a 30 m x 4 m corridor -- all collinear."""

    def test_produces_collinear_refusals(self) -> None:
        mix = _refusal_summary(corridor_walk)
        collinear_key = (SolutionKind.ZONE_ONLY.value, GeometryQuality.COLLINEAR_ANCHORS.value)
        assert collinear_key in mix, f"expected COLLINEAR_ANCHORS in {mix}"
        assert mix[collinear_key] > 0

    def test_refusal_rate_is_nonzero(self) -> None:
        from spectra_engine.accuracy import calibrate_from_truth, evaluate
        from spectra_engine.pipeline import PipelineConfig

        scenario = corridor_walk(seed=0)
        paired = scenario.paired()
        models = calibrate_from_truth(paired, scenario.site)
        report = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
        assert report.refusal_rate > 0, f"expected non-zero refusal rate: {report.summary()}"

    def test_is_deterministic(self) -> None:
        """Same seed, same result -- the scenario must be reproducible."""
        a = _refusal_summary(corridor_walk, seed=42)
        b = _refusal_summary(corridor_walk, seed=42)
        assert a == b


class TestSparseGrid:
    """Only 2 APs on a 50 m x 50 m floor -- always too few anchors."""

    def test_produces_too_few_anchors_refusals(self) -> None:
        mix = _refusal_summary(sparse_grid)
        too_few_key = (SolutionKind.ZONE_ONLY.value, GeometryQuality.TOO_FEW_ANCHORS.value)
        assert too_few_key in mix, f"expected TOO_FEW_ANCHORS in {mix}"
        assert mix[too_few_key] > 0

    def test_zero_point_estimates(self) -> None:
        """With only 2 APs, no sample should ever produce a POINT fix."""
        scenario = sparse_grid(seed=0)
        paired = scenario.paired()
        models = calibrate_from_truth(paired, scenario.site)
        report = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
        assert report.located == 0, f"expected zero POINT fixes: {report.summary()}"

    def test_is_deterministic(self) -> None:
        a = _refusal_summary(sparse_grid, seed=7)
        b = _refusal_summary(sparse_grid, seed=7)
        assert a == b


class TestWideOpen:
    """APs at corners of a 100 m x 100 m floor -- many points too uncertain."""

    def test_produces_some_refusals_or_mixed_outcomes(self) -> None:
        """At 100 m scale, points far from all APs should exceed the sigma threshold."""
        scenario = wide_open(seed=0)
        paired = scenario.paired()
        models = calibrate_from_truth(paired, scenario.site)
        report = evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
        # Either there are refusals, or coverage is below 100%.
        # Both indicate the pipeline is correctly refusing uncertain estimates.
        has_refusals = len(report.refusal_mix()) > 0
        has_imperfect_coverage = report.coverage < 1.0
        assert has_refusals or has_imperfect_coverage, (
            f"expected some non-POINT outcomes at 100 m scale: {report.summary()}"
        )

    def test_is_deterministic(self) -> None:
        a = _refusal_summary(wide_open, seed=3)
        b = _refusal_summary(wide_open, seed=3)
        assert a == b
