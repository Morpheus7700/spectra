"""Fingerprint survey capture and instrument screening.

Two commands:

    uv run python -m tools.survey capture      # walk the flat, record labelled points
    uv run python -m tools.survey screen       # per-BSSID quality report over a capture

Capture writes JSONL to data/flat/survey.jsonl, which is gitignored: a survey records
every neighbouring BSSID -- geolocatable through public wardriving databases -- and SSIDs
routinely carry people's names (R16). Raw identifiers are kept in the local capture because
they are the raw instrument reading and hashing them would make calibration undebuggable;
anything that leaves this machine must be hashed at that boundary.

Why the protocol is what it is, all measured rather than assumed:

* **Four orientations per point.** Body shadowing is 3-6 dB typical, 10-20 dB worst case.
  On an anchor at 6 m (gradient ~2.2 dB/m) that is 2.3 m of apparent position shift from
  turning around. A single-orientation fingerprint silently encodes which way you faced.
* **~12 sweeps per orientation.** Averages 4-6 dB of fast fading down to under ~1.5 dB.
* **A sweep is ~4 s**, so a point costs 3-4 minutes. That is the honest budget, not padding.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from adapters.bssid import candidate_radio_groups

DEFAULT_CAPTURE = Path("data/flat/survey.jsonl")
ORIENTATIONS: tuple[str, ...] = ("N", "E", "S", "W")
SWEEPS_PER_ORIENTATION = 12

# Below this the NIC is 1-5 dB above its detection cliff, so the AP is only *seen* on sweeps
# where fading happened to help. That truncation biases the observed mean upward by 1.4-4.8 dB
# and shortens any fitted range by 10-31%. The bias is deterministic, so averaging never
# removes it and a robust loss never catches it. Binary seen/not-seen only.
CENSORED_DBM = -85.0

# A BSSID that vanishes on a fifth of sweeps cannot anchor a fingerprint: a missing AP shifts
# the whole distance metric, and neighbour APs reboot, band-steer and change channel.
MIN_PERSISTENCE = 0.8

Tier = Literal["continuous", "binary-only", "excluded"]


@dataclass(frozen=True, slots=True)
class BssidQuality:
    """What one BSSID is worth as a feature, judged from a stationary capture."""

    bssid: str
    ssid: str
    seen: int
    sweeps: int
    mean_dbm: float
    sigma_db: float
    tier: Tier
    note: str = ""

    @property
    def persistence(self) -> float:
        return self.seen / self.sweeps if self.sweeps else 0.0


@dataclass
class SweepRecord:
    """One sweep at one point in one orientation."""

    point: str
    room: str
    x: float | None
    y: float | None
    orientation: str
    sweep: int
    at: str
    readings: dict[str, int] = field(default_factory=dict)
    ssids: dict[str, str] = field(default_factory=dict)


def screen(records: Sequence[SweepRecord]) -> list[BssidQuality]:
    """Rank every BSSID in a capture by whether it can carry information.

    Pure: no I/O, no clock. `records` is normally one stationary point, but any set works --
    persistence and sigma are computed across whatever sweeps are handed in.
    """
    sweeps = len(records)
    if sweeps == 0:
        return []

    by_bssid: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    for rec in records:
        for bssid, rssi in rec.readings.items():
            by_bssid.setdefault(bssid, []).append(rssi)
            if rec.ssids.get(bssid):
                names[bssid] = rec.ssids[bssid]

    out: list[BssidQuality] = []
    for bssid, values in by_bssid.items():
        seen = len(values)
        mean = statistics.fmean(values)
        sigma = statistics.stdev(values) if seen > 1 else 0.0
        persistence = seen / sweeps

        tier: Tier
        note = ""
        if persistence < MIN_PERSISTENCE:
            tier = "excluded"
            note = f"seen on {persistence:.0%} of sweeps, below {MIN_PERSISTENCE:.0%}"
        elif mean <= CENSORED_DBM:
            tier = "binary-only"
            note = f"at/below {CENSORED_DBM:.0f} dBm -- truncation biases the mean upward"
            if seen > 1 and sigma == 0.0:
                note += "; sigma exactly 0.00 is a driver clamp, not stability"
        else:
            tier = "continuous"

        out.append(
            BssidQuality(
                bssid=bssid,
                ssid=names.get(bssid, ""),
                seen=seen,
                sweeps=sweeps,
                mean_dbm=mean,
                sigma_db=sigma,
                tier=tier,
                note=note,
            )
        )
    return sorted(out, key=lambda q: q.mean_dbm, reverse=True)


def _load(path: Path) -> list[SweepRecord]:
    records: list[SweepRecord] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(SweepRecord(**json.loads(line)))
    return records


def _report(path: Path) -> None:
    records = _load(path)
    if not records:
        print(f"{path} has no records")
        return

    points = sorted({r.point for r in records})
    print(f"{len(records)} sweeps over {len(points)} point(s): {', '.join(points)}\n")
    qualities = screen(records)

    print(f"{'SSID':<24} {'BSSID':<19} {'seen':>7} {'mean':>7} {'sigma':>7}  tier")
    print("-" * 88)
    for q in qualities:
        print(
            f"{q.ssid[:24]:<24} {q.bssid:<19} {q.seen:>3}/{q.sweeps:<3} "
            f"{q.mean_dbm:>7.1f} {q.sigma_db:>7.2f}  {q.tier}"
        )
        if q.note:
            print(f"{'':>26}-> {q.note}")

    usable = [q for q in qualities if q.tier == "continuous"]
    print(f"\n{len(usable)} of {len(qualities)} BSSIDs usable as continuous features.")

    groups = candidate_radio_groups(q.bssid for q in qualities)
    if groups:
        print("\nCandidate same-radio groups (confirm before treating as independent):")
        for g in groups:
            print(f"  {' + '.join(g)}")


def _capture(path: Path, sweeps: int) -> None:
    from adapters.windows_wlan.scanner import scan

    path.parent.mkdir(parents=True, exist_ok=True)
    print("Survey capture. Ctrl-C to stop; every completed sweep is already on disk.\n")
    print("Fix your laptop lid angle and power state now, and keep them fixed all session.")
    print("Mark the LAPTOP ANTENNA position at each point, not your feet.\n")

    while True:
        point = input("Point label (blank to finish): ").strip()
        if not point:
            break
        room = input("  Room name: ").strip()
        coords = input("  x y in metres (blank if not measured yet): ").strip()
        x, y = (None, None)
        if coords:
            parts = coords.replace(",", " ").split()
            x, y = float(parts[0]), float(parts[1])

        for orientation in ORIENTATIONS:
            input(f"  Face {orientation}, hold still, press Enter...")
            with path.open("a", encoding="utf-8") as fh:
                for i in range(sweeps):
                    observations = scan()
                    record = SweepRecord(
                        point=point,
                        room=room,
                        x=x,
                        y=y,
                        orientation=orientation,
                        sweep=i,
                        at=datetime.now(UTC).isoformat(),
                        readings={o.bssid: o.rssi_dbm for o in observations},
                        ssids={o.bssid: o.ssid for o in observations},
                    )
                    fh.write(json.dumps(record.__dict__) + "\n")
                    fh.flush()
                    print(f"    sweep {i + 1}/{sweeps}: {len(observations)} BSSIDs", end="\r")
            print(f"    {sweeps} sweeps captured{' ' * 20}")
        print(f"  point '{point}' done\n")


def main(argv: Sequence[str]) -> int:
    command = argv[1] if len(argv) > 1 else "screen"
    path = Path(argv[2]) if len(argv) > 2 else DEFAULT_CAPTURE

    if command == "capture":
        _capture(path, SWEEPS_PER_ORIENTATION)
        return 0
    if command == "screen":
        if not path.exists():
            print(f"no capture at {path} -- run `capture` first")
            return 1
        _report(path)
        return 0
    print(f"unknown command {command!r}; expected 'capture' or 'screen'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
