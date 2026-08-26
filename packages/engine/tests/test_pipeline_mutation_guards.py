"""Tests that exist because a specific defect survived the whole suite.

Each test below was written against a mutant the existing tests did not kill, and each was
proved in both directions: it fails with the defect injected and passes without it. The
injection is done by monkeypatching at run time, never by editing source, so the proof is
reproducible from this file alone.

  M1'  ``estimate_position`` bypasses ``_slant_to_horizontal`` and hands the raw slant
       range to the solver as if it were a horizontal distance. The helper has its own unit
       test, but nothing exercised the *call site*, so the correction could be unwired while
       still passing the accuracy gate (pooled p50 degrades ~18%, well inside the 3.5 m
       threshold).
  M8   ``PipelineConfig.device_height_m`` defaults to 0.0 instead of 1.2 -- a device lying
       on the floor rather than carried. Nothing observed the default at all.
  M11  the ``max_sigma_for_point_m`` cap is never reached. The office_walk fixture never
       produces a sigma near 15 m, so deleting the check changed nothing observable and the
       one guarantee it provides -- refusing to publish a point the data cannot support
       (R8) -- was untested.

Caveat (R13): the observations here are synthesised from our own path-loss model, so these
tests prove the pipeline inverts *that* model correctly. They say nothing about whether the
model resembles a real building.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from spectra_core.models import (
    AccessPoint,
    Floor,
    ObservationEvent,
    Site,
    SolutionKind,
    Vec3,
)
from spectra_engine import pipeline as pipeline_module
from spectra_engine.multilateration import GeometryQuality, RangeObservation
from spectra_engine.pipeline import DEVICE_HEIGHT_M, PipelineConfig, estimate_position
from spectra_engine.ranging import PathLossModel
from spectra_sim.building import BuildingSpec, build_site

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
SITE = build_site(BuildingSpec())

# A noise-free model, so the only error left in the answer is error the pipeline created.
EXACT_MODEL = PathLossModel(reference_power_dbm=-40.0, exponent=2.8, rssi_sigma_db=2.0)
EXACT_MODELS = {ap.id: EXACT_MODEL for ap in SITE.access_points}

# With exact ranges the solve is over-determined and consistent, so it converges on the
# truth to machine precision. Anything above this is a bias the pipeline introduced.
EXACT_TOL_M = 1e-4


def _exact_slant_observations(
    x: float, y: float, floor_id: str = "floor-0", device_height_m: float = DEVICE_HEIGHT_M
) -> list[ObservationEvent]:
    """RSSI from every same-floor AP encoding the *exact* 3D slant range to (x, y).

    This is the inverse of what the pipeline has to do, so a pipeline that reverses it
    correctly must land back on (x, y).
    """
    elevation = SITE.floor(floor_id).elevation_m
    observations = []
    for ap in SITE.access_points:
        if ap.floor_id != floor_id:
            continue
        slant = math.dist(
            (ap.position.x, ap.position.y, ap.position.z),
            (x, y, elevation + device_height_m),
        )
        observations.append(
            ObservationEvent(
                tenant_id="t-0",
                ingested_at=datetime.now(),
                collection_policy="ephemeral",
                observer_id=ap.id,
                target_id="d1",
                observed_at=NOW,
                kind="rssi",
                value=EXACT_MODEL.expected_rssi(slant),
            )
        )
    return observations


def _located(observations: list[ObservationEvent], config: PipelineConfig) -> tuple[float, float]:
    estimate = estimate_position(observations, SITE, NOW, "d1", config).estimate
    assert estimate.kind is SolutionKind.POINT, estimate.downgrade_reason
    assert estimate.x is not None and estimate.y is not None
    return estimate.x, estimate.y


# ------------------------------------------------------------------- M1': the call site


def test_solver_receives_horizontal_ranges_not_slant_ranges(monkeypatch):
    """Spy on what estimate_position actually hands to solve().

    The helper's unit test proves the projection maths. This proves the projection is
    *wired in*: every distance reaching the solver must be the plan-view distance to its
    anchor, not the slant range that RSSI actually measures.
    """
    truth_x, truth_y = 8.0, 9.0
    seen: list[RangeObservation] = []
    real_solve = pipeline_module.solve

    def spy(observations, *args, **kwargs):
        seen.extend(observations)
        return real_solve(observations, *args, **kwargs)

    monkeypatch.setattr(pipeline_module, "solve", spy)
    estimate_position(
        _exact_slant_observations(truth_x, truth_y),
        SITE,
        NOW,
        "d1",
        PipelineConfig(models=EXACT_MODELS),
    )

    assert len(seen) == 6, "expected one range per same-floor AP"
    by_anchor = {(round(r.anchor_x, 6), round(r.anchor_y, 6)): r.distance_m for r in seen}
    for ap in SITE.access_points:
        if ap.floor_id != "floor-0":
            continue
        key = (round(ap.position.x, 6), round(ap.position.y, 6))
        horizontal = math.dist((ap.position.x, ap.position.y), (truth_x, truth_y))
        slant = math.hypot(horizontal, ap.position.z - DEVICE_HEIGHT_M)
        # The two must be distinguishable, or the assertion below proves nothing.
        assert slant - horizontal > 0.05
        assert by_anchor[key] == pytest.approx(horizontal, abs=1e-6)


@pytest.mark.parametrize("truth", [(6.6667, 7.5), (8.0, 9.0), (33.0, 22.0)])
def test_exact_slant_ranges_invert_to_the_exact_position(truth):
    """End-to-end consequence of the same defect.

    Un-projected ranges overstate every distance -- most severely near an anchor, where the
    1.7 m height difference is a large share of the range -- so the fix is pushed off truth
    by up to ~1.7 m here. The centroid of the AP grid is excluded deliberately: the bias is
    symmetric there and cancels, which is one reason a pooled accuracy figure hides this.
    """
    x, y = _located(_exact_slant_observations(*truth), PipelineConfig(models=EXACT_MODELS))
    assert math.dist((x, y), truth) < EXACT_TOL_M


# ------------------------------------------------------------------- M8: the device height


def test_default_device_height_is_a_carried_device_not_the_floor():
    """1.2 m is the modelled height of a device on a person. 0.0 m is a device on the slab.

    The difference is only ~0.5 m of dz against a 2.9 m ceiling mount, which is why it hides
    inside the accuracy gate's tolerance -- but it biases every near-anchor range.
    """
    assert math.isclose(DEVICE_HEIGHT_M, 1.2)
    assert PipelineConfig().device_height_m == pytest.approx(1.2)


def test_device_height_reaches_the_slant_correction_and_the_default_is_the_right_one():
    """Behavioural companion to the assertion above.

    Observations are synthesised for a device carried at 1.2 m. The default config must
    invert them exactly; a config claiming the device is on the floor must not. The second
    assertion is what stops this passing if device_height_m were accepted and then ignored.
    """
    truth = (8.0, 9.0)
    observations = _exact_slant_observations(*truth, device_height_m=1.2)

    default_error = math.dist(_located(observations, PipelineConfig(models=EXACT_MODELS)), truth)
    on_the_floor_error = math.dist(
        _located(observations, PipelineConfig(models=EXACT_MODELS, device_height_m=0.0)), truth
    )

    assert default_error < EXACT_TOL_M
    assert on_the_floor_error > 1.0


# ------------------------------------------------------------------- M11: the sigma cap


def _wide_site() -> Site:
    """Three uncalibrated APs strung along a 50 m run with a 8 m kink.

    Not collinear -- the singular-value ratio is ~0.19, comfortably above the 0.06
    threshold -- so the solver returns a fix rather than refusing on geometry. It is simply
    a fix nobody should publish.
    """
    return Site(
        tenant_id="t-0",
        id="wide-site",
        floors=(Floor(id="floor-0", level=0, elevation_m=0.0),),
        access_points=(
            AccessPoint(id="ap-a", position=Vec3(x=0.0, y=0.0, z=2.9), floor_id="floor-0"),
            AccessPoint(id="ap-b", position=Vec3(x=25.0, y=8.0, z=2.9), floor_id="floor-0"),
            AccessPoint(id="ap-c", position=Vec3(x=50.0, y=0.0, z=2.9), floor_id="floor-0"),
        ),
    )


def _weak_observations() -> list[ObservationEvent]:
    return [
        ObservationEvent(
            tenant_id="t-0",
            ingested_at=datetime.now(),
            collection_policy="ephemeral",
            observer_id=ap_id,
            target_id="d1",
            observed_at=NOW,
            kind="rssi",
            value=rssi,
        )
        for ap_id, rssi in (("ap-a", -85.0), ("ap-b", -88.0), ("ap-c", -86.0))
    ]


def test_a_solved_fix_past_the_sigma_cap_is_refused_not_published():
    """The cap is the last line of R8: a number the data cannot support is worse than none.

    Nothing in the office_walk fixture ever produced a sigma near 15 m, so this branch was
    dead as far as the suite was concerned. Here three uncalibrated APs at ~40 m report a
    26 m 1-sigma -- the solver succeeds, and the pipeline must still refuse the point while
    keeping the floor it did establish.
    """
    site, observations = _wide_site(), _weak_observations()
    result = estimate_position(observations, site, NOW, "d1")

    # The solve itself succeeded -- this is not a geometry refusal in disguise.
    assert result.geometry is GeometryQuality.OK
    assert result.estimate.kind is SolutionKind.ZONE_ONLY
    assert result.estimate.x is None and result.estimate.covariance_xy is None
    assert result.estimate.floor_id == "floor-0"
    assert "1-sigma uncertainty" in (result.estimate.downgrade_reason or "")


def test_the_refused_fix_really_did_exceed_the_cap():
    """Guards the test above from passing for the wrong reason.

    Lifting the cap must produce a point, and its sigma must genuinely be past the default
    limit -- otherwise the refusal above would be evidence of some unrelated downgrade.
    """
    site, observations = _wide_site(), _weak_observations()
    lifted = estimate_position(
        observations, site, NOW, "d1", PipelineConfig(max_sigma_for_point_m=math.inf)
    )

    assert lifted.estimate.kind is SolutionKind.POINT
    sigma = lifted.estimate.horizontal_sigma_m
    assert sigma is not None
    assert sigma > 15.0
