"""The rules that would leak personal data or fake a solve if they broke.

No scanning here -- `build_snapshot` is pure, so every case is a hand-built sweep. The BSSIDs
are the real ones from this flat (the router's two BSSes are named in CLAUDE.md already); the
neighbour SSIDs are stand-ins for the surnames that are actually on the air.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime

from tools.live_rf import Reading, build_snapshot, radio_id, smoothed_rssi

SALT = b"\x01" * 32
AT = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

OWN_24 = "d8:aa:00:01:32:a0"
OWN_5 = "d8:aa:00:01:32:a1"
NEIGHBOUR = "c0:00:00:10:cf:85"


def _snapshot(readings: list[Reading], own: str | None = OWN_5) -> dict[str, object]:
    return build_snapshot(readings, own, SALT, AT)


def _reading(
    bssid: str, rssi: float, *, ssid: str = "HomeNet", band: float = 5.0, age: float = 0.5
) -> Reading:
    return Reading(bssid=bssid, ssid=ssid, rssi_dbm=rssi, band_ghz=band, age_s=age)


def test_weak_ap_is_refused_not_ranged() -> None:
    """-85 dBm sits 1-5 dB above the NIC's cliff, so it is only seen when fading helped."""
    snap = _snapshot([_reading(NEIGHBOUR, -85.0, ssid="Smith", band=2.4)], own=OWN_5)
    assert snap["shells"] == []
    (refusal,) = snap["refusals"]
    assert "-85 dBm" in refusal["reason"]


def test_one_db_above_the_cliff_still_gets_a_shell() -> None:
    snap = _snapshot([_reading(NEIGHBOUR, -84.0, ssid="Smith", band=2.4)])
    assert snap["refusals"] == []
    (shell,) = snap["shells"]
    assert shell["range_m"] > 0 and shell["sigma_m"] > 0
    assert shell["calibrated"] is False


def test_stale_reading_is_refused_with_its_own_reason() -> None:
    snap = _snapshot([_reading(OWN_5, -55.0, age=42.0)])
    (refusal,) = snap["refusals"]
    assert "cache" in refusal["reason"]


def test_both_bands_of_one_radio_collapse_to_one_shell() -> None:
    """Two BSSIDs, one antenna. Two shells would draw as two independent constraints."""
    snap = _snapshot(
        [
            _reading(OWN_24, -48.0, band=2.4),
            _reading(OWN_5, -57.0, band=5.0),
        ]
    )
    (shell,) = snap["shells"]
    assert shell["own"] is True
    assert shell["band_ghz"] == 5.0  # the quieter band wins the range
    assert "2.4 GHz" in shell["label"] and "same location" in shell["label"]


def test_separate_radios_stay_separate() -> None:
    snap = _snapshot(
        [_reading(OWN_5, -57.0), _reading(NEIGHBOUR, -70.0, ssid="Smith", band=2.4)]
    )
    assert len(snap["shells"]) == 2
    assert sum(1 for s in snap["shells"] if s["own"]) == 1


def test_id_is_stable_across_bands_and_changes_with_the_salt() -> None:
    assert radio_id(SALT, OWN_24) == radio_id(SALT, OWN_5)
    assert radio_id(SALT, OWN_5) != radio_id(b"\x02" * 32, OWN_5)
    assert radio_id(SALT, OWN_5) != radio_id(SALT, NEIGHBOUR)
    assert len(radio_id(SALT, OWN_5)) == 8


def test_no_raw_bssid_and_no_neighbour_ssid_reaches_the_wire() -> None:
    """live.json is served to a browser. BSSIDs are geolocatable; SSIDs here are surnames."""
    snap = _snapshot(
        [
            _reading(OWN_24, -48.0, band=2.4),
            _reading(OWN_5, -57.0),
            _reading(NEIGHBOUR, -70.0, ssid="Guest-2G", band=2.4),
            _reading("c2:00:00:11:cf:85", -88.0, ssid="Guest-2G", band=2.4),
        ]
    )
    wire = json.dumps(snap).lower()
    for bssid in (OWN_24, OWN_5, NEIGHBOUR, "c2:00:00:11:cf:85"):
        assert bssid not in wire
        assert bssid.replace(":", "") not in wire
    assert "guest-2g" not in wire  # a neighbour's network name must never reach the browser
    assert "homenet" in wire  # the user's own network is theirs to see


def test_median_smoothing_rejects_a_fading_dropout() -> None:
    history: dict[str, deque[tuple[float, float]]] = {}
    for rssi in (-57.0, -56.0, -57.0, -58.0):
        smoothed_rssi(history, OWN_5, rssi, 100.0)
    assert smoothed_rssi(history, OWN_5, -85.0, 100.0) == -57.0


def test_stale_samples_leave_the_smoothing_window() -> None:
    history: dict[str, deque[tuple[float, float]]] = {}
    smoothed_rssi(history, OWN_5, -80.0, 0.0)
    assert smoothed_rssi(history, OWN_5, -57.0, 60.0) == -57.0
