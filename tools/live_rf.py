"""Live RF shells from the one receiver this flat has. Writes `apps/web/public/live.json`.

    uv run python -m tools.live_rf

**What this can and cannot produce.** There is one receiver (this PC, fixed) and one known
anchor (the router in the flat, presenting `…32:a0` on 2.4 GHz and `…:32:a1` on 5 GHz --
one physical radio, two BSSIDs). Every other radio in range is a neighbour's box at an unknown
position. One receiver and one anchor do not determine a position: RSSI gives a *range*, and a
range from a single point is a sphere. So the output is a set of shells -- "this AP is
somewhere at 8.4 +/- 3.9 m from the PC" -- and never a point. Emitting a point here would be
R8's exact failure mode: a plausible number the data cannot support.

**The ranges are uncalibrated and say so.** `default_model()` is A = -40 dBm, n = 2.8, sigma
floored at 6 dB, and it is deliberately mediocre. Nothing in this flat has ground-truth
positions to fit A and n against, so under R9 the honest move is to publish the wide sigma
rather than a fitted-looking number. At -61 dBm that model gives 5.6 m with sigma 1.5 m; the
shell is thick because the knowledge is thin. `calibrated` is `false` in every record this
tool has ever written.

**Nothing leaves here identifiable.** BSSIDs are geolocatable through public wardriving
databases and neighbour SSIDs in this building carry people's names, so live.json is served to
a browser carrying only salted hashes (R16) and, for neighbours, no SSID at all. The salt is 32
random bytes in `data/flat/.salt`, gitignored, generated once.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import statistics
import sys
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "engine"))

from spectra_engine.ranging import default_model  # noqa: E402

from adapters.bssid import radio_key, same_radio  # noqa: E402
from tools.survey import CENSORED_DBM  # noqa: E402

OUTPUT_PATH = ROOT / "apps" / "web" / "public" / "live.json"
SALT_PATH = ROOT / "data" / "flat" / ".salt"

OBSERVER_LABEL = "This PC"
OBSERVER_BAND_NOTE = "Intel AX211"
"""The NIC these numbers were measured on. Not queried -- the machine is fixed hardware."""

SCAN_SETTLE_S = 0.3
LOOP_PERIOD_S = 1.0

MAX_AGE_S = 5.0
"""Past this, the driver is serving cache rather than measurement, so the entry is refused.

`WlanGetNetworkBssList` accumulates and ages entries out over roughly a minute (see
`adapters.windows_wlan.scanner`). `scan()` already drops anything older than the sweep it
requested, so this is the second line of the same defence and rarely fires.
"""

STALE_AGE_S = 2.0
"""Heard, but not within one loop period. Usable, flagged `stale`, not silently presented
as current."""

SMOOTH_SAMPLES = 5
SMOOTH_WINDOW_S = 10.0
"""Rolling median of up to 5 readings from the last 10 s, per BSSID.

Measured RSSI noise standing still: 5 GHz sd 0.5-0.7 dB, 2.4 GHz sd 4.2-6.7 dB. Untouched,
that 2.4 GHz spread is +/-2 m of shell radius jitter at 8 m -- visible breathing in the
render that is fading, not motion. Median rather than mean because fading dropouts are
one-sided outliers that drag a mean and leave a median alone.

**What it costs: latency.** A step change takes 3 samples (~3 s at 1 Hz) to reach the median.
That is free here and would not be elsewhere -- transmitter and receiver are both bolted down,
so there is no step change to miss. If anything in this scene ever moves, this window is the
first thing that must shrink.

The 10 s window exists so an AP that disappears for a minute and returns is not median-ed
against readings from before it left.
"""

PREFERRED_BAND_GHZ = 5.0
"""Which band to range from when one radio is heard on both. 5 GHz has ~8x lower RSSI
variance here (0.5-0.7 dB vs 4.2-6.7), and with an uncalibrated A that dominates: both bands
are equally wrong about absolute distance, and the quieter one is less wrong minute to
minute."""


@dataclass(frozen=True, slots=True)
class Reading:
    """One BSS as it will be ranged: smoothed RSSI, vendor types already left behind."""

    bssid: str
    ssid: str
    rssi_dbm: float
    band_ghz: float
    age_s: float


def radio_id(salt: bytes, bssid: str) -> str:
    """Stable, unguessable public id for the physical radio behind a BSSID.

    Hashes the `radio_key` rather than the raw BSSID so the id survives band churn: the id of
    the flat's router must not change when its 2.4 GHz BSS misses a sweep and the shell falls
    back to 5 GHz. The renderer keys off this, and a flipping id reads as an AP that vanished
    and a new one that appeared.

    Truncated to 8 hex -- 32 bits over ~10 radios, so a collision is not a practical concern,
    and the salt (32 random bytes, never served) is what makes it non-reversible. A bare
    sha256 of a BSSID is not anonymisation: the whole 48-bit space is enumerable in seconds.
    """
    return hashlib.sha256(salt + ":".join(radio_key(bssid)).encode()).hexdigest()[:8]


def smoothed_rssi(
    history: dict[str, deque[tuple[float, float]]], bssid: str, rssi_dbm: float, now: float
) -> float:
    """Append a reading and return the rolling median. See `SMOOTH_WINDOW_S` for the cost."""
    samples = history.setdefault(bssid, deque(maxlen=SMOOTH_SAMPLES))
    samples.append((now, rssi_dbm))
    return statistics.median([r for t, r in samples if now - t <= SMOOTH_WINDOW_S])


def _refusal_reason(group: Sequence[Reading]) -> str:
    """Why nothing in this radio's group could be ranged. Names the binding failure."""
    if all(r.age_s > MAX_AGE_S for r in group):
        oldest = min(r.age_s for r in group)
        return (
            f"last heard {oldest:.0f} s ago; the driver's BSS list is a cache, "
            "so this is a remembered reading, not a measurement"
        )
    return (
        f"at/below {CENSORED_DBM:.0f} dBm; truncation biases range short by up to 31%"
    )


def _label(group: Sequence[Reading], chosen: Reading, own: bool, number: int) -> str:
    """Own radio gets its real SSID. A neighbour gets a number and nothing else.

    Neighbour SSIDs in this building are people's surnames, and live.json is served to a
    browser -- an untrusted boundary. The band the range came from is named because 2.4 and
    5 GHz shells from one box have different radii from the same uncalibrated model, and a
    reader is owed the reason.
    """
    stem = f"{chosen.ssid} ({chosen.band_ghz:g} GHz)" if own else (
        f"Neighbour {chosen.band_ghz:g} GHz #{number}"
    )
    others = sorted({r.band_ghz for r in group} - {chosen.band_ghz})
    if others:
        bands = ", ".join(f"{b:g} GHz" for b in others)
        return f"{stem} -- also heard on {bands} from the same box, so same location"
    return stem


def build_snapshot(
    readings: Sequence[Reading],
    own_bssid: str | None,
    salt: bytes,
    measured_at: datetime,
) -> dict[str, Any]:
    """The whole wire payload, pure. One shell per *physical radio*, never per BSSID.

    Collapsing matters more than it looks: the flat's router answers on two BSSIDs, and two
    concentric shells around one box would draw as two independent constraints when there is
    one. That is the same lie as a fake solve, told in the renderer instead of the solver.
    """
    model = default_model()
    groups: dict[tuple[str, ...], list[Reading]] = {}
    for reading in readings:
        groups.setdefault(radio_key(reading.bssid), []).append(reading)

    # Sorted by id so neighbour numbering is deterministic across runs rather than a function
    # of whatever order the driver returned entries in.
    ordered = sorted(
        ((radio_id(salt, group[0].bssid), group) for group in groups.values()),
        key=lambda pair: pair[0],
    )

    shells: list[dict[str, Any]] = []
    refusals: list[dict[str, str]] = []
    neighbours = 0
    for identifier, group in ordered:
        own = own_bssid is not None and same_radio(group[0].bssid, own_bssid)
        if not own:
            neighbours += 1
        usable = [r for r in group if r.rssi_dbm > CENSORED_DBM and r.age_s <= MAX_AGE_S]
        if not usable:
            refusals.append({"id": identifier, "reason": _refusal_reason(group)})
            continue

        chosen = sorted(
            usable, key=lambda r: (r.band_ghz != PREFERRED_BAND_GHZ, -r.rssi_dbm)
        )[0]
        range_m = model.distance(chosen.rssi_dbm)
        shells.append(
            {
                "id": identifier,
                "label": _label(group, chosen, own, neighbours),
                "own": own,
                "band_ghz": chosen.band_ghz,
                "rssi_dbm": round(chosen.rssi_dbm, 1),
                "range_m": round(range_m, 2),
                "sigma_m": round(model.distance_sigma(range_m), 2),
                "calibrated": False,
                "stale": chosen.age_s > STALE_AGE_S,
            }
        )

    # Plain language on purpose: this file is read by a panel a non-technical person sees.
    # The engineering caveats (uncalibrated A/n, R8 refusals) are real, but said in words.
    notes = [
        "One antenna can only measure distance, not direction, so each router is a whole "
        "sphere around this computer rather than a dot.",
        "WiFi distance is rough, so every sphere is a best guess -- the fuzzier it looks, "
        "the less sure it is.",
    ]
    if refusals:
        word = "router" if len(refusals) == 1 else "routers"
        notes.append(
            f"{len(refusals)} {word} were too faint to place honestly, so they were left "
            "out rather than guessed."
        )

    return {
        "measured_at": measured_at.isoformat(),
        "observer": {"label": OBSERVER_LABEL, "band_note": OBSERVER_BAND_NOTE},
        "shells": shells,
        "refusals": refusals,
        "notes": notes,
    }


def load_salt(path: Path = SALT_PATH) -> bytes:
    """The per-install hashing salt, created once. Never served, never committed."""
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    salt = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(salt.hex(), encoding="utf-8")
    return salt


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write via temp + `os.replace`, so a fetch never lands mid-write.

    The renderer polls this file at whatever rate it likes; without the rename the browser
    would occasionally parse a truncated object and blank the scene for a frame.

    Windows makes the rename fragile in a way POSIX does not: while the dev server or the
    browser has the destination open to serve it, `os.replace` raises PermissionError
    (WinError 5). The lock is held only for the length of a read, so a few short retries clear
    it. If they do not, the temp file is removed and this sweep is skipped rather than left as
    litter -- the next sweep is a second away and publishes fresher data anyway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    tmp.unlink(missing_ok=True)


def main(seconds: float | None = None) -> int:
    from adapters.windows_wlan.link import associated_bssid
    from adapters.windows_wlan.scanner import scan

    salt = load_salt()
    try:
        own_bssid: str | None = associated_bssid()
    except OSError as exc:
        # Not associated, or the NIC is asleep. Everything heard is then a neighbour, which
        # is a correct reading of the situation, not a reason to stop.
        print(f"no association ({exc}); every radio will be treated as a neighbour")
        own_bssid = None

    history: dict[str, deque[tuple[float, float]]] = {}
    deadline = None if seconds is None else time.perf_counter() + seconds
    print(f"writing {OUTPUT_PATH} at ~{1 / LOOP_PERIOD_S:.0f} Hz; Ctrl-C to stop")
    try:
        while deadline is None or time.perf_counter() < deadline:
            started = time.perf_counter()
            try:
                observations = scan(settle_s=SCAN_SETTLE_S)
            except OSError as exc:
                # Win32 1168 (ERROR_NOT_FOUND) has been seen once mid-run when the driver
                # reset. Nothing is written: leaving the previous file alone lets the
                # renderer watch `measured_at` go stale, which is true, where re-publishing
                # the last shells with a fresh timestamp would be a lie.
                print(f"scan failed ({exc}); keeping the previous snapshot")
                time.sleep(LOOP_PERIOD_S)
                continue

            now = time.time()
            readings = [
                Reading(
                    bssid=o.bssid,
                    ssid=o.ssid,
                    rssi_dbm=smoothed_rssi(history, o.bssid, float(o.rssi_dbm), now),
                    band_ghz=o.band_ghz,
                    age_s=o.age_s(),
                )
                for o in observations
            ]
            snapshot = build_snapshot(readings, own_bssid, salt, datetime.now(UTC))
            write_atomic(OUTPUT_PATH, snapshot)
            print(
                f"{len(snapshot['shells'])} shells, {len(snapshot['refusals'])} refused",
                end="\r",
            )
            time.sleep(max(0.0, LOOP_PERIOD_S - (time.perf_counter() - started)))
    except KeyboardInterrupt:
        pass
    print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(float(sys.argv[1]) if len(sys.argv) > 1 else None))
