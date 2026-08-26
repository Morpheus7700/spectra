"""Windows WLAN scanner -- the sensing end of the Windows collector adapter.

Why this exists rather than parsing `netsh wlan show networks`: netsh reports a *signal
quality percentage*, not dBm, so RSSI has to be reconstructed as `pct/2 - 100` and lands
quantised to 2 dB. Worse, on this machine netsh reports **1** access point where the native
API reports **16** -- it serves a stale, filtered cache. Every measurement taken through
netsh was taken through a broken instrument.

`WlanGetNetworkBssList` hands back `WLAN_BSS_ENTRY.lRssi`, which is a LONG in real dBm,
alongside the centre frequency and the BSS timestamp.

Scanning is asynchronous: `WlanScan` requests a sweep and the driver populates the BSS list
over the next few seconds. The honest way to know it finished is
`WlanRegisterNotification` on `wlan_notification_acm_scan_complete`.

**`WlanGetNetworkBssList` returns an accumulating cache, not a snapshot of one sweep.**
Measured: six consecutive calls returned 13, 16, 19, 21, 22, 23 entries, monotonically
climbing and never dropping. Entries carried across calls keep a byte-identical `lRssi`
*and* `ullHostTimestamp` -- they were not re-measured, they were remembered. Entries in one
call spanned ~42 s of age, and the driver ages them out over roughly a minute.

Left unfiltered this is silently corrupting: a stale entry looks exactly like a live one, so
a fingerprint survey would score long-departed APs as perfectly persistent and average an
RSSI the radio never took. So every entry is stamped with the moment it was actually heard,
and `scan()` returns only entries measured after the sweep it requested. An AP the radio
did not hear this sweep is a genuine non-detection and must read as absent, not as its last
known value.

`ullHostTimestamp` is a FILETIME -- 100 ns ticks since 1601-01-01 UTC. Verified against the
system clock: the newest entry read 30.1 s old, matching the real elapsed time.

# ponytail: request-then-settle instead of the WlanRegisterNotification callback. The
# ceiling is that a fixed settle can clip a slow sweep, costing recall rather than
# correctness -- freshness filtering means a clipped sweep under-reports instead of
# inventing data. `scan_repeated` unions several sweeps to buy the recall back. Upgrade to
# the notification if one authoritative sweep is ever needed.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import POINTER, Structure, byref, c_void_p
from ctypes.wintypes import BOOL, BYTE, DWORD, HANDLE, LONG, ULONG, USHORT, WCHAR
from dataclasses import dataclass

if sys.platform != "win32":  # pragma: no cover - the adapter is Windows-only by nature
    raise ImportError("adapters.windows_wlan.scanner requires Windows (wlanapi.dll)")

ULONGLONG = ctypes.c_ulonglong
BOOLEAN = ctypes.c_ubyte

DOT11_BSS_TYPE_ANY = 3
WLAN_CLIENT_VERSION_VISTA = 2


class GUID(Structure):
    _fields_ = (
        ("Data1", DWORD),
        ("Data2", USHORT),
        ("Data3", USHORT),
        ("Data4", BYTE * 8),
    )


class DOT11_SSID(Structure):
    _fields_ = (("uSSIDLength", ULONG), ("ucSSID", ctypes.c_ubyte * 32))


class WLAN_RATE_SET(Structure):
    _fields_ = (("uRateSetLength", ULONG), ("usRateSet", USHORT * 126))


class WLAN_BSS_ENTRY(Structure):
    _fields_ = (
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", ULONG),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", ctypes.c_int),
        ("dot11BssPhyType", ctypes.c_int),
        ("lRssi", LONG),
        ("uLinkQuality", ULONG),
        ("bInRegDomain", BOOLEAN),
        ("usBeaconPeriod", USHORT),
        ("ullTimestamp", ULONGLONG),
        ("ullHostTimestamp", ULONGLONG),
        ("usCapabilityInformation", USHORT),
        ("ulChCenterFrequency", ULONG),
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", ULONG),
        ("ulIeSize", ULONG),
    )


class WLAN_BSS_LIST(Structure):
    _fields_ = (
        ("dwTotalSize", DWORD),
        ("dwNumberOfItems", DWORD),
        ("wlanBssEntries", WLAN_BSS_ENTRY * 1),
    )


class WLAN_INTERFACE_INFO(Structure):
    _fields_ = (
        ("InterfaceGuid", GUID),
        ("strInterfaceDescription", WCHAR * 256),
        ("isState", ctypes.c_int),
    )


class WLAN_INTERFACE_INFO_LIST(Structure):
    _fields_ = (
        ("dwNumberOfItems", DWORD),
        ("dwIndex", DWORD),
        ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
    )


_wlanapi = ctypes.WinDLL("wlanapi.dll")

_wlanapi.WlanOpenHandle.argtypes = (DWORD, c_void_p, POINTER(DWORD), POINTER(HANDLE))
_wlanapi.WlanOpenHandle.restype = DWORD
_wlanapi.WlanCloseHandle.argtypes = (HANDLE, c_void_p)
_wlanapi.WlanCloseHandle.restype = DWORD
_wlanapi.WlanEnumInterfaces.argtypes = (
    HANDLE,
    c_void_p,
    POINTER(POINTER(WLAN_INTERFACE_INFO_LIST)),
)
_wlanapi.WlanEnumInterfaces.restype = DWORD
_wlanapi.WlanScan.argtypes = (HANDLE, POINTER(GUID), c_void_p, c_void_p, c_void_p)
_wlanapi.WlanScan.restype = DWORD
_wlanapi.WlanGetNetworkBssList.argtypes = (
    HANDLE,
    POINTER(GUID),
    c_void_p,
    ctypes.c_int,
    BOOL,
    c_void_p,
    POINTER(POINTER(WLAN_BSS_LIST)),
)
_wlanapi.WlanGetNetworkBssList.restype = DWORD
_wlanapi.WlanFreeMemory.argtypes = (c_void_p,)
_wlanapi.WlanFreeMemory.restype = None


FILETIME_UNIX_EPOCH_OFFSET_S = 11_644_473_600
"""Seconds between 1601-01-01 (the FILETIME epoch) and 1970-01-01."""


def _filetime_now() -> int:
    """Current time as a FILETIME: 100 ns ticks since 1601-01-01 UTC."""
    return int((time.time() + FILETIME_UNIX_EPOCH_OFFSET_S) * 10_000_000)


@dataclass(frozen=True, slots=True)
class BssObservation:
    """One BSS as the radio actually measured it. `rssi_dbm` is real dBm, not a percentage."""

    ssid: str
    bssid: str
    rssi_dbm: int
    link_quality: int
    frequency_khz: int
    """Centre frequency. Divide by 1000 for MHz; >= 5_000_000 is the 5/6 GHz band."""
    host_timestamp_ft: int
    """When the radio actually heard this BSS, as a FILETIME. The guard against stale cache."""

    @property
    def band_ghz(self) -> float:
        return 2.4 if self.frequency_khz < 3_000_000 else 5.0

    def age_s(self, now_ft: int | None = None) -> float:
        """How long ago this BSS was heard. Non-zero even for a freshly returned entry."""
        return ((now_ft if now_ft is not None else _filetime_now()) - self.host_timestamp_ft) / 1e7


def _check(code: int, call: str) -> None:
    if code != 0:
        raise OSError(code, f"{call} failed with Win32 error {code}")


def _decode_ssid(raw: DOT11_SSID) -> str:
    return bytes(raw.ucSSID[: raw.uSSIDLength]).decode("utf-8", errors="replace")


def _format_bssid(raw: ctypes.Array[ctypes.c_ubyte]) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _read_bss_list(handle: HANDLE, guid: GUID, min_timestamp_ft: int) -> list[BssObservation]:
    """Read the BSS list, dropping entries the radio did not hear since `min_timestamp_ft`.

    The list is a cache, so unfiltered it mixes live readings with remembered ones. See the
    module docstring: a remembered entry is indistinguishable from a live one except by its
    timestamp, and treating it as live is how a survey silently records APs that are gone.
    """
    bss_list = POINTER(WLAN_BSS_LIST)()
    _check(
        _wlanapi.WlanGetNetworkBssList(
            handle, byref(guid), None, DOT11_BSS_TYPE_ANY, False, None, byref(bss_list)
        ),
        "WlanGetNetworkBssList",
    )
    try:
        count = bss_list.contents.dwNumberOfItems
        base = ctypes.addressof(bss_list.contents.wlanBssEntries)
        stride = ctypes.sizeof(WLAN_BSS_ENTRY)
        out: list[BssObservation] = []
        for i in range(count):
            entry = WLAN_BSS_ENTRY.from_address(base + i * stride)
            if entry.ullHostTimestamp < min_timestamp_ft:
                continue
            out.append(
                BssObservation(
                    ssid=_decode_ssid(entry.dot11Ssid),
                    bssid=_format_bssid(entry.dot11Bssid),
                    rssi_dbm=entry.lRssi,
                    link_quality=entry.uLinkQuality,
                    frequency_khz=entry.ulChCenterFrequency,
                    host_timestamp_ft=entry.ullHostTimestamp,
                )
            )
        return out
    finally:
        _wlanapi.WlanFreeMemory(bss_list)


def scan(settle_s: float = 4.0, stale_grace_s: float = 1.0) -> list[BssObservation]:
    """Request a sweep, let the radio settle, then read only what it heard *this* sweep.

    Entries older than the moment the sweep was requested are cache, not measurement, and
    are dropped -- see the module docstring. `stale_grace_s` widens that cutoff slightly to
    absorb clock skew between the driver's FILETIME stamps and ours; it is not a licence to
    admit older readings.

    One call can under-report; use `scan_repeated` for the fullest picture available.
    """
    negotiated = DWORD()
    handle = HANDLE()
    _check(
        _wlanapi.WlanOpenHandle(
            WLAN_CLIENT_VERSION_VISTA, None, byref(negotiated), byref(handle)
        ),
        "WlanOpenHandle",
    )
    try:
        iface_list = POINTER(WLAN_INTERFACE_INFO_LIST)()
        _check(_wlanapi.WlanEnumInterfaces(handle, None, byref(iface_list)), "WlanEnumInterfaces")
        try:
            if iface_list.contents.dwNumberOfItems == 0:
                raise OSError("no WLAN interface present")
            guid = iface_list.contents.InterfaceInfo[0].InterfaceGuid
        finally:
            _wlanapi.WlanFreeMemory(iface_list)

        # Stamp the cutoff *before* requesting, so anything already in the cache is excluded.
        cutoff_ft = _filetime_now() - int(stale_grace_s * 10_000_000)
        # WlanScan returns immediately; the driver fills the list over the next few seconds.
        _check(_wlanapi.WlanScan(handle, byref(guid), None, None, None), "WlanScan")
        time.sleep(settle_s)
        return _read_bss_list(handle, guid, cutoff_ft)
    finally:
        _wlanapi.WlanCloseHandle(handle, None)


def scan_repeated(sweeps: int = 3, settle_s: float = 4.0) -> list[BssObservation]:
    """Union several sweeps, keeping the strongest reading per BSSID.

    The radio visits channels in batches, so consecutive sweeps reveal different subsets.
    Strongest-wins rather than mean, because a missed channel reads as absent, not as weak,
    and averaging an absence downward would invent attenuation that was never measured.
    """
    best: dict[str, BssObservation] = {}
    for _ in range(sweeps):
        for obs in scan(settle_s=settle_s):
            seen = best.get(obs.bssid)
            if seen is None or obs.rssi_dbm > seen.rssi_dbm:
                best[obs.bssid] = obs
    return sorted(best.values(), key=lambda o: o.rssi_dbm, reverse=True)


if __name__ == "__main__":
    found = scan_repeated()
    now = _filetime_now()
    print(f"{len(found)} BSSIDs")
    for o in found:
        print(
            f"{o.rssi_dbm:>5} dBm  {o.band_ghz:>3} GHz  {o.age_s(now):>6.1f}s  "
            f"{o.bssid}  {o.ssid}"
        )
