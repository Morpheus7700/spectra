"""The decisive experiment: does the link actually notice a person?

    uv run python -m tools.sense record still 300      # nobody moving
    uv run python -m tools.sense record walking 300    # walk around normally
    uv run python -m tools.sense compare               # separability, honestly

Everything else is downstream of one question -- whether the single PC-to-router link
separates "someone is moving" from "nobody is". This records labelled windows and answers it
with an effect size and a false-positive rate, not a vibe. R8 applies to the answer: if the
labels do not separate, that is the reported result.

## Why this link and no other

The PC hears ~8 physical radios, but only one is inside the flat. Djukic et al. (arXiv
2308.06773) measured exactly this distinction with RSSI and three detectors in a 3.5x4.5 m
room:

* **source inside the room** -- ~100% binary presence accuracy.
* **source outside the room** -- 45-72% for a single person, across four algorithms.

Every neighbour link crosses *their* flat before it reaches ours, so their movement perturbs
it the same way ours does, and with unknown transmitter positions there is no geometric basis
to separate the two. That is unresolvable label noise on seven of eight streams.

It is also a motion sensor pointed into homes whose occupants did not consent (R15/R18). The
best-performing stream, the only confound-free one, and the only ethical one are the same
stream. We use the associated link only.

## Why these features

Djukic and Ichnaea independently converge on **variance over a window** as the feature, and
Djukic is explicit that the **mean does not work**: "There is no correlation between the mean
value and the presence of people." The physics is that a body both reflects (raising RSSI) and
absorbs (lowering it) depending on where it stands, so there is no one-way effect on the mean.
Skewness and kurtosis were also found non-discriminating.

Retry rate is not RSSI, and the asymmetry may matter here: obstruction should raise
retransmissions monotonically, where it moves RSSI in either direction. So this records both
the window mean and the window standard deviation and lets the measurement decide, rather than
assuming our channel behaves like theirs. Nothing in the literature validates retry rate as a
device-free sensing feature -- that is what the experiment is for.

## Two traps that manufacture a convincing lie

**Never split randomly.** RSSI-family signals are heavily autocorrelated, so windows from the
same recording leak across a random split and report 80-95% for a detector that has learned
nothing. Split by session, and ideally by day. `compare` refuses to report a headline number
when a label has only one session.

**Never run a scan during a recording.** Microsoft is explicit that a scan makes it "more
difficult for a wireless interface to send and receive data packets": the radio leaves the
operating channel and retries spike, which is indistinguishable from a person crossing the
path. The slow multi-AP channel and this fast one must be recorded separately.

Baselines also drift -- measured 0.115 mean in one session and 0.23 fifteen minutes later with
nothing deliberately changed -- so 'still' must be recorded in the same sitting as 'walking'.
Djukic needed nightly recalibration with a far better SNR budget than this.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_CAPTURE = Path("data/flat/sense.jsonl")

SAMPLE_WINDOW_S = 0.25
"""One counter delta. Fine enough that a 20 s feature window holds ~80 samples."""

FEATURE_WINDOW_S = 20.0
"""Djukic's tau. Long enough to estimate a variance, short enough to be a useful latency."""

USABLE_EFFECT_SIZE = 0.8
"""Cohen's d: 0.2 small, 0.5 medium, 0.8 large. Below this a single-window detector overlaps
too much to threshold without paying for it in false positives."""

MIN_WINDOWS_PER_LABEL = 10
"""Below this, refuse to issue a verdict at all (R8).

Not a stylistic threshold -- found by running the tool. Four windows of arbitrarily labelled
data produced |d| = 1.12 and a confident "the link does notice". Cohen's d on n=2 per class is
noise with a decimal point, and a 50% false-positive rate reported as "1 of 2" looks like a
measurement. At 20 s per window this asks for ~200 s per label, which the recommended 300 s
recording comfortably clears.
"""

FEATURE_NAMES = ("retry_mean", "retry_sd", "ack_mean", "ack_sd")
"""Scored side by side rather than picking one in advance. The literature says variance is
the feature and the mean is useless, but that was measured on RSSI; retry rate may not share
the sign ambiguity that makes RSSI's mean useless. The measurement decides."""


@dataclass(frozen=True, slots=True)
class SenseRow:
    """One counter delta, tagged with the recording session it came from."""

    label: str
    session: str
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


@dataclass(frozen=True, slots=True)
class Features:
    """Window statistics. `retry_sd` is the literature's feature; `retry_mean` is the bet
    that retry rate, unlike RSSI, responds monotonically to obstruction."""

    label: str
    session: str
    retry_mean: float
    retry_sd: float
    ack_mean: float
    ack_sd: float


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    """Standardised difference of means, pooled SD. Sign says which label reads higher."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.variance(a), statistics.variance(b)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    if pooled == 0.0:
        return 0.0
    return (statistics.fmean(b) - statistics.fmean(a)) / pooled


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


def extract_features(
    rows: Sequence[SenseRow], window_s: float = FEATURE_WINDOW_S
) -> list[Features]:
    """Chop each session into non-overlapping windows and summarise each.

    Windows never span sessions or labels -- a window straddling the moment the person started
    walking would carry both labels and belong to neither.
    """
    out: list[Features] = []
    keys = sorted({(r.session, r.label) for r in rows})
    for session, label in keys:
        subset = sorted(
            (r for r in rows if r.session == session and r.label == label), key=lambda r: r.at
        )
        bucket: list[SenseRow] = []
        start = subset[0].at if subset else 0.0
        for row in subset:
            if row.at - start >= window_s and len(bucket) > 1:
                out.append(_summarise(bucket, label, session))
                bucket, start = [], row.at
            bucket.append(row)
        if len(bucket) > 1:
            out.append(_summarise(bucket, label, session))
    return out


def _summarise(bucket: Sequence[SenseRow], label: str, session: str) -> Features:
    retry = [r.retry_rate for r in bucket]
    ack = [r.ack_failure_rate for r in bucket]
    return Features(
        label=label,
        session=session,
        retry_mean=statistics.fmean(retry),
        retry_sd=statistics.stdev(retry),
        ack_mean=statistics.fmean(ack),
        ack_sd=statistics.stdev(ack),
    )


def _load(path: Path) -> list[SenseRow]:
    rows: list[SenseRow] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(SenseRow(**json.loads(line)))
    return rows


def _record(path: Path, label: str, seconds: float) -> None:
    from adapters.windows_wlan.link import LinkMonitor

    session = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording '{label}' for {seconds:.0f}s (session {session}).")
    print("Do NOT run a scan in another window while this is going.\n")

    written = 0
    deadline = time.perf_counter() + seconds
    with LinkMonitor() as monitor, path.open("a", encoding="utf-8") as fh:
        for window in monitor.windows(window_s=SAMPLE_WINDOW_S):
            row = SenseRow(
                label=label,
                session=session,
                at=window.at,
                duration_s=window.duration_s,
                transmitted=window.transmitted,
                received=window.received,
                retries=window.retries,
                ack_failures=window.ack_failures,
            )
            fh.write(json.dumps(asdict(row)) + "\n")
            written += 1
            remaining = deadline - time.perf_counter()
            if written % 4 == 0:
                fh.flush()
                print(
                    f"  {written:>5} samples  retry {row.retry_rate:.3f}  "
                    f"{remaining:5.0f}s left",
                    end="\r",
                )
            if remaining <= 0:
                break
    print(f"\n'{label}': {written} samples appended to {path}")


def _report_lines(features: Sequence[Features]) -> Iterator[str]:
    labels = sorted({f.label for f in features})
    yield f"{'label':<10} {'sessions':>9} {'windows':>8} {'retry mean':>11} {'retry sd':>10}"
    yield "-" * 52
    for label in labels:
        subset = [f for f in features if f.label == label]
        sessions = len({f.session for f in subset})
        yield (
            f"{label:<10} {sessions:>9} {len(subset):>8} "
            f"{statistics.fmean([f.retry_mean for f in subset]):>11.4f} "
            f"{statistics.fmean([f.retry_sd for f in subset]):>10.4f}"
        )


def _compare(path: Path) -> None:
    rows = _load(path)
    if not rows:
        print(f"{path} has no rows")
        return
    features = extract_features(rows)
    if not features:
        print(f"{len(rows)} samples, but no complete {FEATURE_WINDOW_S:.0f}s window yet.")
        return

    print(f"{len(rows)} samples -> {len(features)} feature windows\n")
    for line in _report_lines(features):
        print(line)

    labels = {f.label for f in features}
    if not {"still", "walking"} <= labels:
        print("\nNeed both a 'still' and a 'walking' recording to compare.")
        return

    quiet = [f for f in features if f.label == "still"]
    active = [f for f in features if f.label == "walking"]

    print(f"\n{'feature':<12} {'d':>7}  {'threshold':>10} {'TPR':>7} {'FPR':>7}")
    print("-" * 48)
    scored: list[tuple[float, str]] = []
    for name in FEATURE_NAMES:
        q = [getattr(f, name) for f in quiet]
        a = [getattr(f, name) for f in active]
        d = cohens_d(q, a)
        threshold, tpr, fpr = best_threshold(q, a)
        scored.append((abs(d), name))
        print(f"{name:<12} {d:>+7.2f}  {threshold:>10.4f} {tpr:>6.0%} {fpr:>7.0%}")

    effect, name = max(scored)

    if len(quiet) < MIN_WINDOWS_PER_LABEL or len(active) < MIN_WINDOWS_PER_LABEL:
        needed_s = MIN_WINDOWS_PER_LABEL * FEATURE_WINDOW_S
        print(
            f"\nNO VERDICT: still={len(quiet)}, walking={len(active)} windows; "
            f"{MIN_WINDOWS_PER_LABEL} each are needed.\n"
            "The numbers above are printed for orientation and mean nothing yet -- an effect\n"
            f"size on this few windows is noise with a decimal point. Record ~{needed_s:.0f}s "
            "per label."
        )
        return

    quiet_sessions = len({f.session for f in quiet})
    active_sessions = len({f.session for f in active})
    if quiet_sessions < 2 or active_sessions < 2:
        print(
            f"\nSINGLE-SESSION WARNING: still={quiet_sessions} session(s), "
            f"walking={active_sessions}.\n"
            "Windows from one recording are autocorrelated, so these numbers measure\n"
            "repeatability, not detection. Record on a second day before believing them."
        )

    if effect < USABLE_EFFECT_SIZE:
        print(
            f"\nVERDICT: best feature '{name}' gives |d| = {effect:.2f}, below "
            f"{USABLE_EFFECT_SIZE}.\n"
            "The distributions overlap too much for a single-window detector. That is the\n"
            "result, not a tuning problem. Longer windows are the only honest lever and they\n"
            "cost latency."
        )
    else:
        print(
            f"\nVERDICT: best feature '{name}' gives |d| = {effect:.2f}. The link does notice.\n"
            "Binary motion on the associated link is the shippable result. It does NOT\n"
            "generalise to where -- that needs a second receiver (R8)."
        )


def main(argv: Sequence[str]) -> int:
    command = argv[1] if len(argv) > 1 else "compare"
    if command == "record":
        if len(argv) < 3:
            print("usage: record <label> [seconds]")
            return 2
        _record(DEFAULT_CAPTURE, argv[2], float(argv[3]) if len(argv) > 3 else 300.0)
        return 0
    if command == "compare":
        if not DEFAULT_CAPTURE.exists():
            print(f"no capture at {DEFAULT_CAPTURE} -- run `record` first")
            return 1
        _compare(DEFAULT_CAPTURE)
        return 0
    print(f"unknown command {command!r}; expected 'record' or 'compare'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
