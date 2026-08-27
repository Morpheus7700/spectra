# Spectra

**WiFi 3D spatial awareness.** A positioning engine, a simulator that acts as its measuring
instrument, and a 3D viewer — plus a live adapter that reads the radio environment around a
real Windows machine.

Two views over one engine:

- **Live RF** — what this hardware actually measures. ~14 surrounding radios, true dBm at
  ~3 Hz, each rendered as an uncertainty *shell* rather than a point, with refusals shown
  rather than hidden.
- **Simulated building** — a three-floor office where real multilateration runs, demonstrating
  what the same engine does when the infrastructure exists to support it.

They are never mixed. Simulator figures describe the simulator (R13).

---

## Run it

```bash
uv sync --locked                 # Python side
cd apps/web && npm install       # web side
```

Two processes:

```bash
# 1. the live collector -- writes apps/web/public/live.json at ~1 Hz
uv run python -m tools.live_rf

# 2. the viewer
cd apps/web && npm run dev
```

Windows only for the live collector: it calls `wlanapi.dll` directly. The simulated view runs
anywhere.

> The collector scans continuously. Your connection will feel slower and the Windows location
> indicator will stay lit — that is the cost of the measurement, not a fault. Scanning requires
> precise-location consent; without it `WlanScan` returns `ERROR_ACCESS_DENIED`.

## Verify it

```bash
uv run pytest -q                                          # full suite
uv run ruff check packages tools adapters .claude/hooks   # lint
uv run mypy packages tools adapters .claude/hooks         # types (strict)
cd apps/web && npm run typecheck
```

Verify from a clean `uv sync --locked` clone rather than the local `.venv` — three
dependencies once passed only on one machine.

---

## What it can and cannot do

This matters more than the feature list, because the interesting claims in this field are
mostly false.

**Can:** measure every reachable radio at true dBm; estimate range with honest uncertainty;
refuse when the data cannot support an answer; multilaterate properly when ≥3 known anchors
exist (validated in simulation: p50 1.54–2.61 m, p95 4.10–6.60 m, coverage 99–100%, 20 seeds).

**Cannot, on one receiver:** produce a position. One AP yields one distance, which is a sphere.
That is geometry, not a missing feature.

**Cannot, on commodity Windows:** sense a person who carries no device. Tested properly and
written up in [ADR 0002](docs/adr/0002-device-free-sensing-is-closed-on-commodity-windows.md) —
two channels, six feature families, with a positive control and a drift control. The link
wanders further on its own in five minutes than a human body moves it. The gap is sampling
rate: the published work uses ~4000 samples per window at 200 Hz across three receivers; this
gets ~45 at 2.2 Hz across one.

**A note on the field.** Repositories claiming device-free 3D human pose from a *single* radio
are, in every case examined here, fraudulent — one had a CSI parser that returned
`np.random.rand()` and 91k purchased stars. The genuine work (Person-in-WiFi-3D, CVPR 2024;
MM-Fi) independently converged on **1 transmitter + 3 receivers with CSI-capable NICs**. Treat
the single-radio claim as the tell.

## Layout

| Path | What lives there |
|---|---|
| `packages/core` | Domain models. `ObservationEvent` in, `PositionEstimate` out. |
| `packages/engine` | Geometry only — ranging, multilateration, floor, zones, accuracy. Purity enforced by test. |
| `packages/sim` | The measuring instrument. Emits through the identical adapter interface. |
| `adapters/windows_wlan` | Native `wlanapi.dll` scanner and link statistics. |
| `apps/web` | React Three Fiber viewer. |
| `tools/` | Live collector, survey capture, sensing experiments, ADR council. |
| `docs/adr/` | Decisions, with the dissent kept. |

`CLAUDE.md` carries the binding ruleset and the standing findings — read it before changing
anything. The findings are there because each one cost real time to learn: `netsh` reports 1
access point where the native API reports 14; the BSS list is an accumulating cache whose stale
entries are indistinguishable from live readings except by timestamp; two Windows RSSI APIs
return true dBm and respond to nothing at all.
