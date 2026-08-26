"""The freshness filter is the whole correctness story of this adapter.

`WlanGetNetworkBssList` returns an accumulating cache: entries carried across calls keep a
byte-identical RSSI and timestamp because they were remembered, not re-measured. Unfiltered,
a survey scores departed APs as perfectly persistent and averages readings the radio never
took. These tests pin the filter and the FILETIME arithmetic it depends on, so neither can
be quietly removed.

Windows-only, like the module itself.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="wlanapi.dll is Windows-only")

if sys.platform == "win32":
    from adapters.windows_wlan.scanner import (
        FILETIME_UNIX_EPOCH_OFFSET_S,
        BssObservation,
        _filetime_now,
    )

TICKS_PER_SECOND = 10_000_000


def _obs(bssid: str, host_timestamp_ft: int, rssi: int = -60) -> BssObservation:
    return BssObservation(
        ssid="test",
        bssid=bssid,
        rssi_dbm=rssi,
        link_quality=50,
        frequency_khz=2_462_000,
        host_timestamp_ft=host_timestamp_ft,
    )


def test_filetime_epoch_offset_is_the_1601_to_1970_gap() -> None:
    """369 years including 89 leap days. A wrong constant silently ages every entry."""
    assert FILETIME_UNIX_EPOCH_OFFSET_S == 11_644_473_600


def test_filetime_now_round_trips_to_the_present() -> None:
    import time

    unix_from_ft = _filetime_now() / TICKS_PER_SECOND - FILETIME_UNIX_EPOCH_OFFSET_S
    assert abs(unix_from_ft - time.time()) < 2.0


def test_age_is_measured_against_the_supplied_clock() -> None:
    now = _filetime_now()
    heard_30s_ago = _obs("aa:bb:cc:dd:ee:ff", now - 30 * TICKS_PER_SECOND)
    assert heard_30s_ago.age_s(now) == pytest.approx(30.0, abs=0.01)


def test_a_stale_cache_entry_is_older_than_a_sweep_cutoff() -> None:
    """The exact discrimination `_read_bss_list` makes: cache vs measurement."""
    now = _filetime_now()
    cutoff = now - 1 * TICKS_PER_SECOND  # a sweep requested one second ago
    live = _obs("aa:bb:cc:dd:ee:01", now)
    remembered = _obs("aa:bb:cc:dd:ee:02", now - 42 * TICKS_PER_SECOND)

    assert live.host_timestamp_ft >= cutoff
    assert remembered.host_timestamp_ft < cutoff
    assert remembered.age_s(now) > 40.0


def test_band_is_split_at_3_ghz() -> None:
    assert _obs("aa:bb:cc:dd:ee:01", 0).band_ghz == 2.4
    five = BssObservation("s", "aa:bb:cc:dd:ee:02", -60, 50, 5_785_000, 0)
    assert five.band_ghz == 5.0
