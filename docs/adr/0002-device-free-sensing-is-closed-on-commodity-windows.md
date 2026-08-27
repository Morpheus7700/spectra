# 0002. Device-free sensing is closed on commodity Windows

Date: 2026-08-27
Author: claude:cli (lead engineer)
Status: Accepted

## Question

The deployment is one fixed Windows desktop and one router, with no possibility of adding
hardware or moving either. Can this configuration detect a person who carries no device, and
if so, can it localise them?

This needed deciding rather than assuming, because the answer determines whether Spectra's
real-hardware story is "we sense people" or something else entirely, and because two of the
reference repositories that prompted the question claim exactly this capability.

## Decision

**No, and the reason is measured, not inferred. Stop attempting it on this platform.**

Both available sensing channels were tested against labelled data with a positive control and
a drift control. Both returned negative. The work is closed until the hardware changes.

The product consequence: with one receiver, each access point is somewhere on a **shell** of
radius `d ± σ` centred on the PC, and shells are what we render. A point would be the
plausible-looking number R8 forbids.

## Evidence

### The channel inventory (Intel AX211, Windows 11 26200)

Every channel Windows exposes, measured rather than assumed:

| Source | Update rate | Verdict |
|---|---|---|
| `WlanGetNetworkBssList` via `WlanScan` | 0.25 Hz nominal, **2.97 Hz** driven hard | the only spatial channel |
| `wlan_intf_opcode_rssi` (0x10000102) | **0 Hz** | returns true dBm, responds to nothing |
| `realtime_connection_quality` (opcode 19) | **0.10 Hz** | true dBm, not location-gated, still useless |
| `ulRxRate` / `wlanSignalQuality` | ~0.1 Hz | dead |
| `wlan_intf_opcode_statistics` (0x10000101) | **19.5 Hz** | the only fast channel |
| Monitor mode (Npcap) | — | absent; Intel + Windows does not expose it |

The two RSSI opcodes are the trap: both return real dBm, so both look like the obvious sensing
channel. `opcode_rssi` did not change once across 1787 polls over 20 s, nor across 2801
injected packets. They are smoothed roaming-decision values.

### Channel 1 — link statistics (retry / ACK-failure rate), ~20 Hz

Best effect size across four features: **|d| = 0.48**, against a 0.8 bar.

Diagnosis from the collected data:
- Poisson counting noise explains ~39% of the variance. The prober delivers ~24 frames per
  0.25 s window; asking for 1000 pps yields 151, because Windows' ~15 ms sleep granularity
  caps it. More frames is not available.
- Both sessions drift upward in the same shape (still 0.244/0.268/0.278, walking
  0.240/0.254/0.263). The apparent −0.41 effect *was* that drift.
- Observed rate SD identical to four decimal places in both classes: 0.1660 vs 0.1660.

### Channel 2 — RSSI variance from the scan path, ~2.2 Hz

This is the literature's validated feature (Djukić et al. arXiv 2308.06773; Ichnaea both
converge on variance over a window, and Djukić is explicit that the *mean* does not track
presence, because a body reflects and absorbs depending where it stands).

First pass reported **|d| = 0.93** and a positive verdict. **That verdict was wrong**, and the
tell was the pattern rather than the number: the effect sat entirely on `.mean` of the most
*stable* radio, while the variance features gave −0.23 and +0.18.

| | |
|---|---|
| Between-label difference (still → walking) | **+1.04 dB** |
| Within-session swing, `still` alone | **1.91 dB** |
| Within-session swing, `walking` alone | 1.36 dB |

The quiet recording wandered −59.74 / −60.03 / **−58.12** / −59.80 / −59.58 across its own five
minutes with nobody moving. That excursion is larger than the entire walking effect. **The
channel wanders further on its own than a person moves it.**

Six feature families were tried, including the two standard drift-cancellations:

| Feature | d | drift floor | net |
|---|---|---|---|
| 5 GHz mean | +0.93 | 0.52 | +0.42 |
| 5 GHz sd | −0.23 | 1.06 | −0.83 |
| 2.4 GHz mean | −0.30 | 0.84 | −0.54 |
| 2.4 GHz sd | +0.18 | 0.16 | +0.02 |
| Differential (2.4 − 5 GHz) mean | −0.49 | 0.94 | −0.44 |
| Differential sd | +0.10 | 0.32 | −0.21 |
| 5 GHz detrended abs-deviation | −0.28 | 1.06 | −0.78 |
| 5 GHz range | −0.19 | 1.22 | −1.02 |

Nothing survives its own drift floor.

### Why the negative is trustworthy

A negative result is worthless if the instrument is simply broken. It is not:

- **Positive control.** A concurrent WiFi scan moved the link channel hard, retry rate
  0.2675 → 0.1605. It responds to gross channel disturbance and not to a person.
- **Drift control.** The same effect size measured between the two halves of a *single*
  recording, where nobody changed anything. Every candidate feature was scored against it.
- **The instruments were themselves debugged first.** `netsh` reported 1 AP where the native
  API reports 14; `WlanGetNetworkBssList` returns an accumulating cache whose stale entries
  are indistinguishable from live ones except by `ullHostTimestamp`. Both were fixed before
  any measurement was trusted.

### The root cause is the sampling gap, not the method

Djukić reached ~100% binary presence with **4000 samples per 20 s window at 200 Hz**, one
transmitter inside the room, three detectors. This deployment gets **~45 samples per window at
2.2 Hz**, one detector, on a link with ~0.6 dB of usable spread against 1.9 dB of wander. That
is two orders of magnitude short. The physics is real; the instrument cannot resolve it.

## Consequences

- No presence, occupancy, motion or people-counting claim ships from Windows RSSI. `tools/sense.py`
  is retained as the experiment and its verdict logic now names drift explicitly.
- The live product renders **shells, not points** (R8, R10).
- The collection path is restricted **in code** to the associated radio (`same_radio()` against
  `associated_bssid()`). This is R15 — mode branches collection, not display. It is
  simultaneously the privacy requirement and the better measurement: Djukić measured that a
  transmitter *outside* the room collapses to 45–72% for a single person, and every neighbour
  link crosses their flat before reaching ours, so their motion is an unresolvable confound.
  The ethical choice and the accurate choice are the same choice here.
- Room-level classification is not attempted. With one receiver every link shares an endpoint,
  so it is a fan rather than a mesh, and radio tomography is ill-posed for any voxel no link
  crosses. Independently, WiFi's 20–40 MHz bandwidth caps range resolution at 7.5–15 m, which
  is larger than the flat — so room-level could never come from ranging, only from learned
  fingerprints that this sampling rate cannot support.

## What would change this decision

In descending order of value per unit cost. Each is a *specific falsifiable test*, not a wish:

1. **CSI on Linux — unresolved and cheap to settle.** FeitCSI's driver fork contains the AX211
   device IDs (`0x51F0`, `0x51F1`, `0x7AF0`) mapped to the AX210 config, and the CSI path is
   gated on a firmware capability bit (`IWL_UCODE_TLV_CAPA_CSI_REPORTING_V2`) with **no PCI
   device check**. Nobody has published a success or a failure. Counter-argument on record: the
   AX211 is CNVio2, MAC in chipset, which may make it architecturally impossible.
   **Test:** boot the FeitCSI live medium; does `/sys/kernel/debug/iwlwifi/*/iwlmvm/csi_enabled`
   exist, and do chunks arrive after writing 1? Costs an evening and touches nothing on disk.
   Payoff: ~2000 complex subcarrier readings per second instead of one scalar per scan.
2. **A second receiver.** One ESP32 (~₹400) converts the fan into a mesh, doubles the streams,
   and puts the deployment in the configuration every DFPL paper in the literature actually
   used. No RSSI-based DFPL system in the literature used fewer than two receivers.
3. **Linux monitor mode without CSI.** `iwlwifi` supports monitor mode properly, giving
   per-frame radiotap RSSI at beacon rate (~10 Hz per AP) and **per-chain** RSSI for both AX211
   antennas (`rx_status->chain_signal[0..1]`, confirmed in mainline `mvm/rxmq.c`). That is ~5×
   the sample rate plus a second antenna — the first thing that would move the sampling gap
   without buying anything.

## What was rejected, and why it matters

Two of the repositories that prompted this investigation claim device-free 3D pose from a
single radio. **`ruvnet/ruview` (91k stars) is a confirmed fraud** — its CSI parser is
`np.random.rand()`, there are no trained weights, the stars were purchased, and the audit issue
was deleted by the maintainer. **`MaliosDark/wifi-3d-fusion` is a 14-commit AI-written wrapper**
built on RTL8812AU, a chipset with no public CSI toolchain.

The real work converged independently on the same requirement: Person-in-WiFi-3D (CVPR 2024,
91.7 mm) uses **1 TX + 3 RX on Intel 5300**; `csi2pointcloud` consumes MM-Fi, also **1 TX +
3 RX**. `espectre` is genuine and works with one ESP32 plus a home router — and stops at
**binary MOTION/IDLE**, listing 3D localisation as "Research — not available".

**The claim of device-free 3D pose from a single radio is the most reliable single indicator
that a repository is fraudulent.** Recording that here because it is the kind of thing this
project will be shown again.
