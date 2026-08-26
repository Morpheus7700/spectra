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

# ponytail: request-then-settle instead of the notification callback. The ceiling is that
# one call can under-report -- measured BSSID counts climbed 11 -> 15 -> 16 across
# consecutive sweeps, because the radio visits channels in batches. Callers that need a
# complete picture should scan repeatedly and union the results (see `scan_repeated`).
# Upgrade to WlanRegisterNotification if a single authoritative sweep is ever needed.
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


@dataclass(frozen=True, slots=True)
class BssObservation:
    """One BSS as the radio actually measured it. `rssi_dbm` is real dBm, not a percentage."""

    ssid: str
    bssid: str
    rssi_dbm: int
    link_quality: int
    frequency_khz: int
    """Centre frequency. Divide by 1000 for MHz; >= 5_000_000 is the 5/6 GHz band."""

    @property
    def band_ghz(self) -> float:
        return 2.4 if self.frequency_khz < 3_000_000 else 5.0


def _check(code: int, call: str) -> None:
    if code != 0:
        raise OSError(code, f"{call} failed with Win32 error {code}")


def _decode_ssid(raw: DOT11_SSID) -> str:
    return bytes(raw.ucSSID[: raw.uSSIDLength]).decode("utf-8", errors="replace")


def _format_bssid(raw: ctypes.Array[ctypes.c_ubyte]) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _read_bss_list(handle: HANDLE, guid: GUID) -> list[BssObservation]:
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
            out.append(
                BssObservation(
                    ssid=_decode_ssid(entry.dot11Ssid),
                    bssid=_format_bssid(entry.dot11Bssid),
                    rssi_dbm=entry.lRssi,
                    link_quality=entry.uLinkQuality,
                    frequency_khz=entry.ulChCenterFrequency,
                )
            )
        return out
    finally:
        _wlanapi.WlanFreeMemory(bss_list)


def scan(settle_s: float = 4.0) -> list[BssObservation]:
    """Request a sweep, let the radio settle, then read the BSS list.

    One call can under-report; see the module docstring. Use `scan_repeated` when the
    caller needs the fullest picture available.
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

        # WlanScan returns immediately; the driver fills the list over the next few seconds.
        _check(_wlanapi.WlanScan(handle, byref(guid), None, None, None), "WlanScan")
        time.sleep(settle_s)
        return _read_bss_list(handle, guid)
    finally:
        _wlanapi.WlanCloseHandle(handle, None)


def scan_repeated(sweeps: int = 3, settle_s: float = 4.0) -> list[BssObservation]:
    """Union several sweeps, keeping the strongest reading per BSSID.

    The radio visits channels in batches, so consecutive sweeps reveal different subsets.
    Strongest-wins rather than mean, because a missed channel reads as absent, not as weak,
    and averaging an absence downward would invent attenuation that was never measured.
    """
    best: dict[str, BssObservation] = {}
    for i in range(sweeps):
        for obs in scan(settle_s=settle_s if i else 0.5):
            seen = best.get(obs.bssid)
            if seen is None or obs.rssi_dbm > seen.rssi_dbm:
                best[obs.bssid] = obs
    return sorted(best.values(), key=lambda o: o.rssi_dbm, reverse=True)


if __name__ == "__main__":
    found = scan_repeated()
    print(f"{len(found)} BSSIDs")
    for o in found:
        print(f"{o.rssi_dbm:>5} dBm  {o.band_ghz:>3} GHz  {o.bssid}  {o.ssid}")
