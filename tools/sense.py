"""The decisive experiment: does anything this machine can measure notice a person?

    uv run python -m tools.sense record rssi still 300
    uv run python -m tools.sense record rssi walking 300
    uv run python -m tools.sense compare rssi

Two channels, because the first one that looked promising was an invention and it failed:

**`link`** -- `retry`/`ackFail` counters at ~20 Hz, normalised per transmitted frame. Fast,
and it responds to gross channel disturbance (a concurrent scan moved retry rate 0.27 -> 0.16).
It did **not** respond to a person walking around the flat: |d| = 0.48 at best, and the
apparent effect in `retry_mean` was drift, since both sessions rose ~14% over five minutes in
the same shape. Retained because it costs nothing and may separate for a stronger stimulus,
but it is not the horse to back.

**`rssi`** -- true dBm per BSSID from the scan path, driven at ~3 Hz. This is the feature the
literature actually validates. Djukic et al. (arXiv 2308.06773) and Ichnaea independently
converge on **variance of RSSI over a window**, and Djukic is explicit that the **mean does
not work**: "There is no correlation between the mean value and the presence of people." A
body reflects (raising RSSI) and absorbs (lowering it) depending where it stands, so there is
no one-way effect on the mean. Skew and kurtosis were also non-discriminating.

3 Hz was a surprise -- a scan was assumed to cost 4 s. Driven with a short settle it runs at
2.97 Hz with the associated radio fresh in 82% of sweeps, so a 20 s window holds ~60 samples.
Djukic had 4000 per window at 200 Hz and judged ~20 Hz sufficient; 3 Hz is a further order
down and that gap is real, not papered over.

## Only our own radio is ever recorded

The collection path filters to BSSIDs sharing the associated radio's identity, in code (R15:
mode must branch the *collection*, not the display). Two reasons, pointing the same way:

* Djukic measured the difference. Transmitter **inside** the room: ~100% binary presence.
  Transmitter **outside**: 45-72% for a single person. Every neighbour link crosses their flat
  before reaching ours, so their movement perturbs it the way ours does, and with unknown
  transmitter positions there is no geometric basis to separate the two.
* A variance detector on a neighbour's link is a motion sensor pointed into a home that never
  agreed to it (R18).

`record` prints exactly which BSSIDs it admitted, so a mis-grouping is visible rather than
silent.

## Two traps that manufacture a convincing lie

**Never split randomly.** These signals are heavily autocorrelated, so windows from one
recording leak across a random split and report 80-95% for a detector that learned nothing.
Rows carry a session id, windows never span a session or a label, and `compare` warns when a
label has only one session.

**Never mix the channels in one sitting.** Scanning makes the radio leave the operating
channel, which spikes retries and looks exactly like a person crossing the path -- that is
measured, not theoretical. Record `link` and `rssi` separately.

Baselines drift, so 'still' must be recorded in the same sitting as 'walking'. R8 applies to
the answer: if the labels do not separate, that is the result.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_CAPTURE = Path("data/flat/sense.jsonl")

CHANNELS = ("link", "rssi")

LINK_SAMPLE_WINDOW_S = 0.25
"""One counter delta. Fine enough that a 20 s feature window holds ~80 samples."""

RSSI_SETTLE_S = 0.3
"""Measured: yields 2.97 Hz with the associated radio fresh in 82% of sweeps."""

FEATURE_WINDOW_S = 20.0
"""Djukic's tau. Long enough to estimate a variance, short enough to be a useful latency."""

MIN_SAMPLES_PER_WINDOW = 8
"""A variance from fewer samples is not worth the decimal places it prints."""

MIN_WINDOWS_PER_LABEL = 10
"""Below this, refuse to issue a verdict at all (R8).

Found by running the tool, not by reasoning: four windows of arbitrarily labelled data
produced |d| = 1.12 and a confident "the link does notice". Cohen's d on n=2 per class is
noise with a decimal point.
"""

USABLE_EFFECT_SIZE = 0.8
"""Cohen's d: 0.2 small, 0.5 medium, 0.8 large. Below this a single-window detector overlaps
too much to threshold without paying for it in false positives."""


@dataclass(frozen=True, slots=True)
class Sample:
    """One instant on one channel. `values` is metric name -> reading."""

    label: str
    session: str
    channel: str
    at: float
    values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Window:
    """Summary statistics over one feature window, keyed `<metric>.mean` / `<metric>.sd`."""

    label: str
    session: str
    channel: str
    stats: dict[str, float] = field(default_factory=dict)


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    """Standardised difference of means, pooled SD. Sign says which label reads higher."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.variance(a), statistics.variance(b)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    difference = statistics.fmean(b) - statistics.fmean(a)
    if pooled == 0.0:
        # No spread within either group. Equal means is genuinely no effect; unequal means is
        # perfect separation, which is infinite -- not zero. Returning zero here would let a
        # quantised metric that happens to be constant within each half hide a total drift.
        return 0.0 if difference == 0.0 else math.copysign(math.inf, difference)
    return difference / pooled


def best_threshold(quiet: Sequence[float], active: Sequence[float]) -> tuple[float, float, float]:
    """Sweep every observed value as a threshold; return the one maximising TPR - FPR.

    Reports both rates rather than accuracy, because the classes are not balanced in real use:
    a flat is empty far more often than occupied, so accuracy would flatter a detector that
    simply never fires.
    """
    if not quiet or not active:
        return (0.0, 0.0, 0.0)
    best = (0.0, 0.0, 1.0)
    best_score = -2.0
    for candidate in sorted({*quiet, *active}):
        tpr = sum(1 for v in active if v >= candidate) / len(active)
        fpr = sum(1 for v in quiet if v >= candidate) / len(quiet)
        if tpr - fpr > best_score:
            best_score, best = tpr - fpr, (candidate, tpr, fpr)
    return best


def drift_floor(windows: Sequence[Window], metric: str) -> float:
    """How large an effect this metric produces from nothing at all.

    Splits each single-label session in half by time and measures the effect size *between
    its own halves*. Nobody changed behaviour in the middle of a recording, so whatever this
    returns is manufactured by drift, and a between-label effect that does not clear it is
    manufactured the same way.

    This control exists because the tool twice reported a confident win that was drift. On the
    link channel a d of -0.41 was two sessions rising in the same shape. On the rssi channel a
    d of +0.93 was a 1.04 dB between-label difference sitting inside a 1.91 dB within-session
    swing -- the quiet recording wandered further on its own than the walking effect did.
    """
    worst = 0.0
    for session, label in sorted({(w.session, w.label) for w in windows}):
        group = [
            w
            for w in windows
            if w.session == session and w.label == label and metric in w.stats
        ]
        if len(group) < 4:
            continue
        half = len(group) // 2
        first = [w.stats[metric] for w in group[:half]]
        second = [w.stats[metric] for w in group[half:]]
        worst = max(worst, abs(cohens_d(first, second)))
    return worst


def extract_windows(
    samples: Sequence[Sample], window_s: float = FEATURE_WINDOW_S
) -> list[Window]:
    """Chop each session into non-overlapping windows and summarise each.

    Windows never span a session or a label -- one straddling the moment the person started
    walking would carry both labels and belong to neither.
    """
    out: list[Window] = []
    keys = sorted({(s.session, s.label, s.channel) for s in samples})
    for session, label, channel in keys:
        subset = sorted(
            (s for s in samples if s.session == session and s.label == label),
            key=lambda s: s.at,
        )
        bucket: list[Sample] = []
        start = subset[0].at if subset else 0.0
        for sample in subset:
            if sample.at - start >= window_s and bucket:
                summary = _summarise(bucket, label, session, channel)
                if summary is not None:
                    out.append(summary)
                bucket, start = [], sample.at
            bucket.append(sample)
        if bucket:
            summary = _summarise(bucket, label, session, channel)
            if summary is not None:
                out.append(summary)
    return out


def _summarise(
    bucket: Sequence[Sample], label: str, session: str, channel: str
) -> Window | None:
    """Mean and SD per metric. A metric seen too rarely in this window is dropped, not
    imputed -- an absent AP is a non-detection, and filling it in would invent a reading."""
    by_metric: dict[str, list[float]] = {}
    for sample in bucket:
        for name, value in sample.values.items():
            by_metric.setdefault(name, []).append(value)

    stats: dict[str, float] = {}
    for name, values in by_metric.items():
        if len(values) < MIN_SAMPLES_PER_WINDOW:
            continue
        stats[f"{name}.mean"] = statistics.fmean(values)
        stats[f"{name}.sd"] = statistics.stdev(values)
    return Window(label=label, session=session, channel=channel, stats=stats) if stats else None


def _load(path: Path) -> list[Sample]:
    samples: list[Sample] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                samples.append(Sample(**json.loads(line)))
    return samples


def _record_link(label: str, session: str, seconds: float) -> Iterator[Sample]:
    from adapters.windows_wlan.link import LinkMonitor

    deadline = time.perf_counter() + seconds
    with LinkMonitor() as monitor:
        for window in monitor.windows(window_s=LINK_SAMPLE_WINDOW_S):
            yield Sample(
                label=label,
                session=session,
                channel="link",
                at=window.at,
                values={
                    "retry_rate": window.retry_rate,
                    "ack_rate": window.ack_failure_rate,
                },
            )
            if time.perf_counter() >= deadline:
                return


def _record_rssi(label: str, session: str, seconds: float) -> Iterator[Sample]:
    from adapters.bssid import same_radio
    from adapters.windows_wlan.link import associated_bssid
    from adapters.windows_wlan.scanner import scan

    own = associated_bssid()
    print(f"associated radio: {own}")
    print("admitting only BSSIDs on that radio; every neighbour is excluded in code.\n")

    announced: set[str] = set()
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        observations = [o for o in scan(settle_s=RSSI_SETTLE_S) if same_radio(o.bssid, own)]
        for o in observations:
            if o.bssid not in announced:
                announced.add(o.bssid)
                print(f"  admitted {o.bssid}  {o.band_ghz} GHz  {o.ssid}")
        if observations:
            yield Sample(
                label=label,
                session=session,
                channel="rssi",
                at=time.perf_counter(),
                values={o.bssid: float(o.rssi_dbm) for o in observations},
            )


def _record(path: Path, channel: str, label: str, seconds: float) -> None:
    session = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording '{label}' on channel '{channel}' for {seconds:.0f}s (session {session}).")
    if channel == "rssi":
        print("Scanning continuously -- your internet will be sluggish and the location")
        print("icon will stay lit. That is the cost of the measurement, not a fault.")
    print("Do NOT run the other channel in another window while this is going.\n")

    source = _record_link if channel == "link" else _record_rssi
    written = 0
    started = time.perf_counter()
    with path.open("a", encoding="utf-8") as fh:
        for sample in source(label, session, seconds):
            fh.write(json.dumps(asdict(sample)) + "\n")
            written += 1
            if written % 4 == 0:
                fh.flush()
                left = seconds - (time.perf_counter() - started)
                print(f"  {written:>5} samples, {left:5.0f}s left", end="\r")
    print(f"\n'{label}' on '{channel}': {written} samples appended to {path}")


def _compare(path: Path, channel: str) -> None:
    samples = [s for s in _load(path) if s.channel == channel]
    if not samples:
        print(f"no '{channel}' samples in {path} -- run `record {channel} <label>` first")
        return
    windows = extract_windows(samples)
    if not windows:
        print(f"{len(samples)} samples, but no complete {FEATURE_WINDOW_S:.0f}s window yet.")
        return

    print(f"channel '{channel}': {len(samples)} samples -> {len(windows)} feature windows\n")
    labels = sorted({w.label for w in windows})
    for label in labels:
        subset = [w for w in windows if w.label == label]
        sessions = len({w.session for w in subset})
        print(f"  {label:<10} {len(subset):>3} windows across {sessions} session(s)")

    if not {"still", "walking"} <= set(labels):
        print("\nNeed both a 'still' and a 'walking' recording to compare.")
        return

    quiet = [w for w in windows if w.label == "still"]
    active = [w for w in windows if w.label == "walking"]

    metrics = sorted(
        {k for w in quiet for k in w.stats} & {k for w in active for k in w.stats}
    )
    if not metrics:
        print("\nNo metric is present in both labels -- nothing comparable.")
        return

    print(f"\n{'metric':<24} {'d':>7} {'drift':>7} {'net':>7}  {'TPR':>6} {'FPR':>6}")
    print("-" * 62)
    scored: list[tuple[float, str, float, float]] = []
    for metric in metrics:
        q = [w.stats[metric] for w in quiet if metric in w.stats]
        a = [w.stats[metric] for w in active if metric in w.stats]
        d = cohens_d(q, a)
        floor = drift_floor(windows, metric)
        net = abs(d) - floor
        _, tpr, fpr = best_threshold(q, a)
        scored.append((net, metric, abs(d), floor))
        print(
            f"{metric:<24} {d:>+7.2f} {floor:>7.2f} {net:>+7.2f}  {tpr:>5.0%} {fpr:>6.0%}"
        )

    net_effect, name, effect, floor = max(scored)
    print(
        "\n'drift' is the same effect size measured between the two halves of a single\n"
        "recording, where nothing changed. 'net' is what survives it."
    )

    if len(quiet) < MIN_WINDOWS_PER_LABEL or len(active) < MIN_WINDOWS_PER_LABEL:
        needed_s = MIN_WINDOWS_PER_LABEL * FEATURE_WINDOW_S
        print(
            f"\nNO VERDICT: still={len(quiet)}, walking={len(active)} windows; "
            f"{MIN_WINDOWS_PER_LABEL} each are needed.\n"
            "The numbers above are printed for orientation and mean nothing yet. Record "
            f"~{needed_s:.0f}s per label."
        )
        return

    if len({w.session for w in quiet}) < 2 or len({w.session for w in active}) < 2:
        print(
            "\nSINGLE-SESSION WARNING: windows from one recording are autocorrelated, so\n"
            "these numbers measure repeatability, not detection. Record on a second day\n"
            "before believing them."
        )

    if effect >= USABLE_EFFECT_SIZE and net_effect < USABLE_EFFECT_SIZE:
        print(
            f"\nVERDICT: DRIFT, not detection. Best metric '{name}' gives |d| = {effect:.2f},\n"
            f"which clears the {USABLE_EFFECT_SIZE} bar -- but the same metric produces "
            f"{floor:.2f}\n"
            "between the two halves of a single recording, where nobody changed anything.\n"
            "The channel wanders further on its own than the person moves it. Reporting this\n"
            "as a detector would be exactly the plausible-looking number R8 forbids."
        )
    elif net_effect < USABLE_EFFECT_SIZE:
        print(
            f"\nVERDICT: best metric '{name}' gives |d| = {effect:.2f} against a drift floor "
            f"of {floor:.2f},\n"
            f"leaving {net_effect:+.2f} -- below {USABLE_EFFECT_SIZE}. The distributions "
            "overlap too much for a\n"
            "single-window detector. That is the result, not a tuning problem."
        )
    else:
        print(
            f"\nVERDICT: '{name}' gives |d| = {effect:.2f} against a drift floor of "
            f"{floor:.2f}.\n"
            f"Net {net_effect:+.2f} survives. Something is genuinely visible.\n"
            "Binary motion is the shippable claim. It does NOT generalise to *where* -- one\n"
            "receiver is a fan, not a mesh (R8)."
        )


def main(argv: Sequence[str]) -> int:
    command = argv[1] if len(argv) > 1 else "compare"
    if command == "record":
        if len(argv) < 4 or argv[2] not in CHANNELS:
            print(f"usage: record <{'|'.join(CHANNELS)}> <label> [seconds]")
            return 2
        _record(DEFAULT_CAPTURE, argv[2], argv[3], float(argv[4]) if len(argv) > 4 else 300.0)
        return 0
    if command == "compare":
        channel = argv[2] if len(argv) > 2 and argv[2] in CHANNELS else "rssi"
        if not DEFAULT_CAPTURE.exists():
            print(f"no capture at {DEFAULT_CAPTURE} -- run `record` first")
            return 1
        _compare(DEFAULT_CAPTURE, channel)
        return 0
    print(f"unknown command {command!r}; expected 'record' or 'compare'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
