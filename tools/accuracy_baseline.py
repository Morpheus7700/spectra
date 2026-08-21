"""The accuracy gate's seed set, its measurement, and its committed baseline.

Why this lives in `tools/` and not in `packages/engine/`: the measurement drives the
simulator, and R12 forbids the engine package from importing it (enforced by
`packages/engine/tests/test_engine_is_pure.py`). Why it is not simply inlined in the test:
the baseline generator and the gate must compute the *same* number, or the ratchet compares
two different quantities and means nothing. One function, two callers.

R13 applies to every figure here. These describe how well the solver inverts our own
propagation model. They are not a claim about a building.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("core", "engine", "sim"):
    sys.path.insert(0, str(ROOT / "packages" / _pkg))

from spectra_engine.accuracy import (  # noqa: E402
    AccuracyReport,
    calibrate_from_truth,
    evaluate,
    pooled,
)
from spectra_engine.pipeline import PipelineConfig  # noqa: E402
from spectra_sim.scenario import office_walk  # noqa: E402

# 20 seeds, ~0.9 s each. Three seeds -- what the gate used before -- is enough to state a
# range and not enough to ratchet against: the spread of per-seed p50 across these 20 runs
# is 1.54-2.61 m, so any three of them can be a lucky or unlucky window. Pooled over 2140
# samples the p50 is stable, which is what makes a relative ratchet meaningful.
GATE_SEEDS: tuple[int, ...] = tuple(range(20))

BASELINE_PATH = ROOT / "packages" / "engine" / "tests" / "accuracy_baseline.json"


def measure(seeds: tuple[int, ...] = GATE_SEEDS) -> tuple[list[AccuracyReport], AccuracyReport]:
    """Run the calibrated pipeline over every seed. Returns (per-seed, pooled)."""
    reports = []
    for seed in seeds:
        scenario = office_walk(seed=seed)
        paired = scenario.paired()
        models = calibrate_from_truth(paired, scenario.site)
        reports.append(
            evaluate(paired, scenario.site, scenario.target_id, PipelineConfig(models=models))
        )
    return reports, pooled(reports)


def as_baseline(report: AccuracyReport, seeds: tuple[int, ...] = GATE_SEEDS) -> dict[str, Any]:
    return {
        "_comment": (
            "Simulator figures (R13) -- how well the solver inverts our own propagation "
            "model, NOT real-world accuracy. Regenerate with: "
            "uv run python -m tools.accuracy_baseline"
        ),
        "seeds": list(seeds),
        "samples": report.samples,
        "coverage": round(report.coverage, 6),
        "p50_m": round(report.p50, 6),
        "p95_m": round(report.p95, 6),
        "worst_m": round(report.worst, 6),
        "floor_accuracy": round(report.floor_accuracy, 6),
        "floor_decision_rate": round(report.floor_decision_rate, 6),
        "zone_accuracy": round(report.zone_accuracy, 6),
        "calibration_ratio": round(report.calibration_ratio, 6),
    }


def load_baseline() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def main() -> int:
    reports, total = measure()
    BASELINE_PATH.write_text(
        json.dumps(as_baseline(total), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {BASELINE_PATH.relative_to(ROOT)}")
    print(f"pooled: {total.summary()}")
    zones = [r.zone_accuracy for r in reports]
    calibs = [r.calibration_ratio for r in reports]
    p50s = [r.p50 for r in reports]
    print(
        f"per-seed p50 {min(p50s):.2f}-{max(p50s):.2f} m, "
        f"zone {min(zones):.0%}-{max(zones):.0%}, "
        f"calib {min(calibs):.2f}-{max(calibs):.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
