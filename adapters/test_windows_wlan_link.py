"""Rates, not raw counts. That distinction is the whole point of LinkWindow.

The counters are cumulative since boot and increment with traffic volume, so a raw delta
measures how busy the network was, not how hard the radio worked to get through. Only the
per-frame rate tracks the channel. These tests pin that, and the divide-by-zero guard that
stops an idle window from reporting a confident 0.0 it never measured.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="wlanapi.dll is Windows-only")

if sys.platform == "win32":
    from adapters.windows_wlan.link import LinkWindow


def _window(transmitted: int, retries: int, ack_failures: int = 0) -> LinkWindow:
    return LinkWindow(
        at=0.0,
        duration_s=1.0,
        transmitted=transmitted,
        received=transmitted,
        retries=retries,
        ack_failures=ack_failures,
    )


def test_rate_is_per_transmitted_frame_not_a_raw_count() -> None:
    """Twice the traffic at the same channel quality must give the same rate."""
    quiet = _window(transmitted=100, retries=12)
    busy = _window(transmitted=200, retries=24)
    assert quiet.retry_rate == busy.retry_rate == pytest.approx(0.12)


def test_a_degraded_channel_raises_the_rate_at_equal_traffic() -> None:
    baseline = _window(transmitted=100, retries=12)  # measured idle floor ~0.115
    obstructed = _window(transmitted=100, retries=40)
    assert obstructed.retry_rate > baseline.retry_rate * 3


def test_idle_window_reports_zero_rather_than_dividing_by_zero() -> None:
    idle = _window(transmitted=0, retries=0)
    assert idle.retry_rate == 0.0
    assert idle.ack_failure_rate == 0.0
    assert idle.frames_per_second == 0.0


def test_ack_failures_are_normalised_independently_of_retries() -> None:
    window = _window(transmitted=200, retries=20, ack_failures=50)
    assert window.retry_rate == pytest.approx(0.10)
    assert window.ack_failure_rate == pytest.approx(0.25)


def test_frames_per_second_uses_the_measured_duration() -> None:
    half = LinkWindow(
        at=0.0, duration_s=0.5, transmitted=50, received=50, retries=5, ack_failures=0
    )
    assert half.frames_per_second == pytest.approx(100.0)
