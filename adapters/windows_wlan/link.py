"""The fast sensing channel: per-frame link statistics at ~20 Hz.

The receiver cannot move and the transmitters cannot move, so the only thing that varies is
the propagation environment -- a person walking through it. That makes sampling rate the
binding constraint, and on Windows almost every obvious channel is dead. Measured on an
Intel AX211, Windows 11:

| source                                          | update rate      |
|-------------------------------------------------|------------------|
| `WlanGetNetworkBssList` (needs a scan)           | 0.25 Hz          |
| `wlan_intf_opcode_rssi` (0x10000102)             | **0 Hz**         |
| `ulRxRate` / `ulTxRate` / `wlanSignalQuality`    | ~0.1 Hz          |
| `wlan_intf_opcode_statistics` (0x10000101)       | **19.5 Hz**      |

`wlan_intf_opcode_rssi` returns real dBm but is a smoothed roaming-decision value: it did not
move once across 1787 polls over 20 s, nor across 2801 injected packets. It is not a
measurement and must not be treated as one.

The statistics counters are. `ullRetryCount` and `ullACKFailureCount` increment per frame, so
they track how hard the radio is working to get packets through -- which is exactly what a
body in the path changes. They are counters, so what matters is the *rate*, normalised by
frames transmitted; raw deltas track traffic volume, not the channel.

**This needs traffic to exist.** An idle desktop transmits almost nothing, and a counter that
never increments senses nothing, so the monitor generates its own probe traffic. Measured
noise floor with the environment undisturbed: retry rate 0.115 +/- 0.050 over 1 s windows
(coefficient of variation 0.43). Any claimed detection has to clear that.

# ponytail: UDP to a closed port on the gateway, ~100 pps, because the ICMP unreachable it
# provokes is a received frame and costs the router nothing measurable. Rude at higher rates,
# so the rate is capped. If ambient traffic is ever sufficient, drop the prober entirely.
"""

from __future__ import annotations

import ctypes
import socket
import sys
import threading
import time
from collections.abc import Iterator
from ctypes import POINTER, Structure, byref, c_void_p
from ctypes.wintypes import DWORD, HANDLE
from dataclasses import dataclass
from types import TracebackType

if sys.platform != "win32":  # pragma: no cover - the adapter is Windows-only by nature
    raise ImportError("adapters.windows_wlan.link requires Windows (wlanapi.dll)")

from adapters.windows_wlan.scanner import (
    GUID,
    WLAN_INTERFACE_INFO_LIST,
    _check,
    _wlanapi,
)

ULONGLONG = ctypes.c_ulonglong

WLAN_INTF_OPCODE_STATISTICS = 0x10000101
"""MSM opcode. Lightly documented, but the only channel on Windows that updates fast."""

_MAC_FIELDS = (
    "transmitted_frames",
    "received_frames",
    "wep_excluded",
    "tkip_local_mic_failures",
    "tkip_replays",
    "tkip_icv_errors",
    "ccmp_replays",
    "ccmp_decrypt_errors",
    "wep_undecryptable",
    "wep_icv_errors",
    "decrypt_success",
    "decrypt_failure",
)

_PHY_FIELDS = (
    "transmitted_frames",
    "multicast_transmitted",
    "failed",
    "retry",
    "multiple_retry",
    "max_tx_lifetime_exceeded",
    "transmitted_fragments",
    "rts_success",
    "rts_failure",
    "ack_failure",
    "received_frames",
    "multicast_received",
    "promiscuous_received",
    "max_rx_lifetime_exceeded",
    "frame_duplicates",
    "received_fragments",
    "promiscuous_fragments",
    "fcs_errors",
)


class WLAN_MAC_FRAME_STATISTICS(Structure):
    _fields_ = tuple((name, ULONGLONG) for name in _MAC_FIELDS)


class WLAN_PHY_FRAME_STATISTICS(Structure):
    _fields_ = tuple((name, ULONGLONG) for name in _PHY_FIELDS)


class WLAN_STATISTICS(Structure):
    _fields_ = (
        ("four_way_handshake_failures", ULONGLONG),
        ("tkip_counter_measures_invoked", ULONGLONG),
        ("reserved", ULONGLONG),
        ("mac_ucast", WLAN_MAC_FRAME_STATISTICS),
        ("mac_mcast", WLAN_MAC_FRAME_STATISTICS),
        ("phy_type_count", DWORD),
        ("phy", WLAN_PHY_FRAME_STATISTICS * 1),
    )


_wlanapi.WlanQueryInterface.argtypes = (
    HANDLE,
    POINTER(GUID),
    ctypes.c_int,
    c_void_p,
    POINTER(DWORD),
    POINTER(c_void_p),
    POINTER(ctypes.c_int),
)
_wlanapi.WlanQueryInterface.restype = DWORD


@dataclass(frozen=True, slots=True)
class LinkWindow:
    """Channel effort over one window, normalised by traffic so it measures the link.

    Rates are per transmitted frame. `retry_rate` above the idle baseline means the radio is
    retransmitting more to reach the same AP -- the signature of something in the path.
    """

    at: float
    duration_s: float
    transmitted: int
    received: int
    retries: int
    ack_failures: int

    @property
    def retry_rate(self) -> float:
        return self.retries / self.transmitted if self.transmitted else 0.0

    @property
    def ack_failure_rate(self) -> float:
        return self.ack_failures / self.transmitted if self.transmitted else 0.0

    @property
    def frames_per_second(self) -> float:
        return self.transmitted / self.duration_s if self.duration_s else 0.0


class _Prober(threading.Thread):
    """Keeps frames flowing so the counters have something to count."""

    def __init__(self, gateway: str, rate_hz: float = 100.0) -> None:
        super().__init__(daemon=True)
        self._gateway = gateway
        self._interval = 1.0 / rate_hz
        self._stop = threading.Event()
        self.sent = 0

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.005)
        while not self._stop.is_set():
            try:
                sock.sendto(b"\x00" * 512, (self._gateway, 53))
                self.sent += 1
                sock.recvfrom(256)
            except OSError:
                pass  # No reply is normal -- the frame still went out and was counted.
            time.sleep(self._interval)
        sock.close()

    def stop(self) -> None:
        self._stop.set()


def default_gateway() -> str:
    """Best guess at the router's address, for the prober to aim at.

    # ponytail: assumes the router is .1 of the local /24, which is true of essentially every
    # home router and wrong on any segmented network. The ceiling is mild -- an unreachable
    # target still produces transmitted frames, so the retry signal survives; only the
    # received-frame count degrades. Pass `gateway=` explicitly if the guess is wrong, or
    # read the real route via iphlpapi GetIpForwardTable if this ever needs to be general.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))
        host: str = sock.getsockname()[0]
    finally:
        sock.close()
    octets = host.split(".")
    return ".".join([*octets[:3], "1"])


class LinkMonitor:
    """Polls the statistics counters and yields normalised windows.

    Use as a context manager -- it holds a WLAN handle and a probe thread.
    """

    def __init__(self, gateway: str | None = None, probe_hz: float = 100.0) -> None:
        self._gateway = gateway or default_gateway()
        self._probe_hz = probe_hz
        self._handle = HANDLE()
        self._guid: GUID | None = None
        self._prober: _Prober | None = None

    def __enter__(self) -> LinkMonitor:
        negotiated = DWORD()
        _check(_wlanapi.WlanOpenHandle(2, None, byref(negotiated), byref(self._handle)), "open")
        iface_list = POINTER(WLAN_INTERFACE_INFO_LIST)()
        _check(_wlanapi.WlanEnumInterfaces(self._handle, None, byref(iface_list)), "enum")
        try:
            if iface_list.contents.dwNumberOfItems == 0:
                raise OSError("no WLAN interface present")
            self._guid = iface_list.contents.InterfaceInfo[0].InterfaceGuid
        finally:
            _wlanapi.WlanFreeMemory(iface_list)
        self._prober = _Prober(self._gateway, self._probe_hz)
        self._prober.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._prober is not None:
            self._prober.stop()
        _wlanapi.WlanCloseHandle(self._handle, None)

    def _raw(self) -> tuple[int, int, int, int]:
        assert self._guid is not None, "use LinkMonitor as a context manager"
        size = DWORD()
        data = c_void_p()
        opcode_value_type = ctypes.c_int()
        _check(
            _wlanapi.WlanQueryInterface(
                self._handle,
                byref(self._guid),
                WLAN_INTF_OPCODE_STATISTICS,
                None,
                byref(size),
                byref(data),
                byref(opcode_value_type),
            ),
            "WlanQueryInterface(statistics)",
        )
        try:
            stats = ctypes.cast(data, POINTER(WLAN_STATISTICS)).contents
            phy = stats.phy[0]
            return (
                int(phy.transmitted_frames),
                int(phy.received_frames),
                int(phy.retry),
                int(phy.ack_failure),
            )
        finally:
            _wlanapi.WlanFreeMemory(data)

    def windows(self, window_s: float = 0.5) -> Iterator[LinkWindow]:
        """Yield one `LinkWindow` per `window_s`, forever. Counters are cumulative since boot,
        so every window is a difference; a window with no transmitted frames is skipped
        rather than reported as a zero rate it did not measure."""
        previous = self._raw()
        previous_at = time.perf_counter()
        while True:
            time.sleep(window_s)
            current = self._raw()
            now = time.perf_counter()
            tx, rx, retry, ack = (c - p for c, p in zip(current, previous, strict=True))
            previous, previous_at_prev = current, previous_at
            previous_at = now
            if tx > 0:
                yield LinkWindow(
                    at=now,
                    duration_s=now - previous_at_prev,
                    transmitted=tx,
                    received=rx,
                    retries=retry,
                    ack_failures=ack,
                )


if __name__ == "__main__":
    with LinkMonitor() as monitor:
        print("probing the default gateway; Ctrl-C to stop")
        print(f"{'tx/s':>8} {'retry rate':>12} {'ackfail rate':>14}")
        for window in monitor.windows(window_s=1.0):
            print(
                f"{window.frames_per_second:>8.0f} "
                f"{window.retry_rate:>12.4f} {window.ack_failure_rate:>14.4f}"
            )
