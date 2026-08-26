"""The decisive experiment: does the link actually notice a person?

    uv run python -m tools.sense record still 300      # nobody moving
    uv run python -m tools.sense record walking 300    # walk around normally
    uv run python -m tools.sense compare               # separability, honestly

Everything else in this project is downstream of one question -- whether retry rate on the
single PC-to-router link separates "someone is moving" from "nobody is". This records
labelled windows and answers it with an effect size and a false-positive rate, not a vibe.

**Do not run a WiFi scan during a recording.** Microsoft is explicit that "it becomes more
difficult for a wireless interface to send and receive data packets while a scan is
occurring": the radio leaves the operating channel, frames fail, and retries spike. That
looks exactly like a person walking through the path. The slow multi-AP spatial channel and
this fast temporal channel must be recorded in separate sessions or they contaminate.

R8 applies to the answer. If the labels do not separate, that is the reported result.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CAPTURE = Path("data/flat/sense.jsonl")

# Cohen's d thresholds, conventional: 0.2 small, 0.5 medium, 0.8 large. Below ~0.8 a
# single-window detector will not be usable, because the two distributions overlap too much
# to threshold without paying for it in false positives.
USABLE_EFFECT_SIZE = 0.8


@dataclass(frozen=True, slots=True)
class SenseRow:
    label: str
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

    Reports both rates rather than accuracy, because the classes are not balanced in real
    use: a flat is empty far more often than it is occupied, so accuracy would flatter a
    detector that simply never fires.
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


def _load(path: Path) -> list[SenseRow]:
    rows: list[SenseRow] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(SenseRow(**json.loads(line)))
    return rows


def _record(path: Path, label: str, seconds: float, window_s: float) -> None:
    from adapters.windows_wlan.link import LinkMonitor

    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording '{label}' for {seconds:.0f}s in {window_s:.1f}s windows.")
    print("Do NOT run a scan in another window while this is going.\n")

    written = 0
    deadline = time.perf_counter() + seconds
    with LinkMonitor() as monitor, path.open("a", encoding="utf-8") as fh:
        for window in monitor.windows(window_s=window_s):
            row = SenseRow(
                label=label,
                at=window.at,
                duration_s=window.duration_s,
                transmitted=window.transmitted,
                received=window.received,
                retries=window.retries,
                ack_failures=window.ack_failures,
            )
            fh.write(json.dumps(asdict(row)) + "\n")
            fh.flush()
            written += 1
            remaining = deadline - time.perf_counter()
            print(
                f"  {written:>4} windows  retry {row.retry_rate:.4f}  "
                f"ackfail {row.ack_failure_rate:.4f}  {remaining:5.0f}s left",
                end="\r",
            )
            if remaining <= 0:
                break
    print(f"\n'{label}': {written} windows appended to {path}")


def _compare(path: Path) -> None:
    rows = _load(path)
    if not rows:
        print(f"{path} has no rows")
        return

    labels = sorted({r.label for r in rows})
    print(f"{len(rows)} windows across labels: {', '.join(labels)}\n")
    print(f"{'label':<12} {'n':>5} {'retry mean':>12} {'sd':>8} {'ackfail mean':>14} {'sd':>8}")
    print("-" * 64)
    for label in labels:
        subset = [r for r in rows if r.label == label]
        rr = [r.retry_rate for r in subset]
        ar = [r.ack_failure_rate for r in subset]
        sd_r = statistics.stdev(rr) if len(rr) > 1 else 0.0
        sd_a = statistics.stdev(ar) if len(ar) > 1 else 0.0
        print(
            f"{label:<12} {len(subset):>5} {statistics.fmean(rr):>12.4f} {sd_r:>8.4f} "
            f"{statistics.fmean(ar):>14.4f} {sd_a:>8.4f}"
        )

    if "still" not in labels or "walking" not in labels:
        print("\nNeed both a 'still' and a 'walking' recording to compare.")
        return

    quiet = [r.retry_rate for r in rows if r.label == "still"]
    active = [r.retry_rate for r in rows if r.label == "walking"]
    effect = cohens_d(quiet, active)
    threshold, tpr, fpr = best_threshold(quiet, active)

    print(f"\nCohen's d (still -> walking): {effect:+.2f}")
    print(f"Best single-window threshold: retry rate >= {threshold:.4f}")
    print(f"  detection rate    (TPR): {tpr:.1%}")
    print(f"  false-positive rate     : {fpr:.1%}")

    if abs(effect) < USABLE_EFFECT_SIZE:
        print(
            f"\nVERDICT: |d| < {USABLE_EFFECT_SIZE}. The distributions overlap too much for a\n"
            "single-window detector. That is the result, not a tuning problem -- report it.\n"
            "Averaging over a longer window is the only honest lever, and it costs latency."
        )
    else:
        print(
            f"\nVERDICT: |d| >= {USABLE_EFFECT_SIZE}. The link does notice. Next question is\n"
            "whether it separates *where*, which needs the slow multi-AP channel."
        )


def main(argv: Sequence[str]) -> int:
    command = argv[1] if len(argv) > 1 else "compare"
    if command == "record":
        if len(argv) < 3:
            print("usage: record <label> [seconds] [window_s]")
            return 2
        label = argv[2]
        seconds = float(argv[3]) if len(argv) > 3 else 300.0
        window_s = float(argv[4]) if len(argv) > 4 else 1.0
        _record(DEFAULT_CAPTURE, label, seconds, window_s)
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
