"""The screen has to reject exactly the sources that lie, and keep the ones that inform.

Fixtures are the real measured values from the dev laptop (8 stationary sweeps), because
the failure modes here were found by measurement, not by reasoning about them.
"""

from __future__ import annotations

from tools.survey import SweepRecord, candidate_radio_groups, screen


def _sweeps(series: dict[str, list[int]], count: int) -> list[SweepRecord]:
    """Build `count` sweeps; a BSSID with fewer readings than `count` was simply not seen."""
    return [
        SweepRecord(
            point="p1",
            room="living",
            x=0.0,
            y=0.0,
            orientation="N",
            sweep=i,
            at="2026-08-26T00:00:00+00:00",
            readings={b: vals[i] for b, vals in series.items() if i < len(vals)},
            ssids={b: b.replace(":", "") for b in series},
        )
        for i in range(count)
    ]


def test_strong_stable_source_is_a_continuous_feature() -> None:
    # The measured 5 GHz radio: mean -56.5, sigma 0.76 -- the best feature available.
    records = _sweeps({"d8:aa:00:01:32:a1": [-57, -56, -57, -56, -57, -56, -56, -57]}, 8)
    (quality,) = screen(records)
    assert quality.tier == "continuous"
    assert quality.persistence == 1.0
    assert quality.sigma_db < 1.0


def test_clamped_weak_source_is_binary_only_despite_zero_sigma() -> None:
    """sigma == 0.00 at -86 dBm is a driver clamp. Zero variance must not read as certainty."""
    records = _sweeps({"d8:dd:00:01:e2:0c": [-86] * 8}, 8)
    (quality,) = screen(records)
    assert quality.sigma_db == 0.0
    assert quality.tier == "binary-only"
    assert "clamp" in quality.note


def test_intermittent_source_is_excluded() -> None:
    # a weak neighbour was seen on 4 of 8 sweeps. A vanishing AP shifts the whole metric.
    records = _sweeps({"d8:ee:00:01:67:97": [-80, -84, -87, -83]}, 8)
    (quality,) = screen(records)
    assert quality.persistence == 0.5
    assert quality.tier == "excluded"


def test_strong_but_intermittent_is_still_excluded() -> None:
    """Persistence outranks strength -- a strong AP that disappears is worse than a weak one."""
    records = _sweeps({"d8:aa:00:01:32:a0": [-54, -55, -53]}, 8)
    (quality,) = screen(records)
    assert quality.tier == "excluded"


def test_empty_capture_screens_to_nothing() -> None:
    assert screen([]) == []


def test_same_radio_candidates_are_grouped() -> None:
    """Both real collapses: a 2.4/5 GHz pair, and a guest BSS on the same antenna."""
    groups = candidate_radio_groups(
        [
            "d8:aa:00:01:32:a0",  # own 2.4 GHz  \  one box
            "d8:aa:00:01:32:a1",  # own 5 GHz    /
            "c0:00:00:10:cf:85",  # box B 2.4    \
            "c0:00:00:10:cf:84",  # box B 5 GHz   > one box, three BSSIDs
            "c2:00:00:11:cf:85",  # guest BSS    /
            "d8:cc:00:01:f9:3e",  # unrelated, must stay alone
        ]
    )
    flattened = {b for g in groups for b in g}
    assert "d8:cc:00:01:f9:3e" not in flattened
    assert len(groups) == 2
    assert sorted(len(g) for g in groups) == [2, 3]
