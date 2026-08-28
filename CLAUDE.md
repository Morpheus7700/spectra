# Spectra — WiFi 3D Spatial Awareness Platform

Locate objects in 3D within a WiFi network's range. Installable PWA + website from one build.
Enterprise target: IT / facilities. Decisions: `docs/adr/`

---

# THE RULESET

Binding. Numbered so they can be cited in review ("this breaks R7"). If a rule is wrong,
change it here — do not route around it in code.

## A. How to work

**R1 — Think before coding.** State assumptions explicitly. If a request has two readings,
present both; never pick silently. If a simpler approach exists, say so before building the
complex one.

**R2 — Simplicity first.** The minimum code that solves the stated problem. No speculative
abstraction, no unrequested configurability, no error handling for impossible states. If it
is 200 lines and could be 50, rewrite it.

**R3 — Surgical changes.** Touch only what the request requires. Don't refactor what isn't
broken, don't restyle adjacent code. Mention unrelated dead code; don't delete it.

**R4 — Goal-driven execution.** Convert every task into a verifiable criterion *before*
starting. "Make localization accurate" is not a task. "Get p50 under 2 m on fixture X, then
make it pass" is.

**R5 — Evidence before assertion.** Never report work complete without running the check and
showing its output. "Should work" is not a result.

## B. Physics — not negotiable, not designable-around

**R6 — The *browser* cannot sense.** iOS has no WiFi scanning API (`NEHotspotHelper` is
entitlement-gated to hotspot login and forbidden for location). Browsers have none at all.
Architecture is therefore **something senses → server solves → client renders**. Any proposal
where the phone or browser does the sensing is wrong on its face.

Amended 2026-08-26. The original wording said "the client cannot sense" and mandated that the
sensing be *infrastructure*. That over-reached: a **native desktop collector can sense**.
`adapters/windows_wlan/` calls `wlanapi.dll` directly and reads true dBm for every BSS in
range. It is an adapter under R11 like any vendor client — it just happens to run on the same
machine as the renderer. The renderer still only renders. What R6 forbids is unchanged: no
browser, no iOS app, and no solve on the client.

Two hard platform limits found while doing this, worth recording so they are not re-litigated:
**Windows exposes no FTM/802.11mc API and Microsoft has stated no public plan to add one**, so
RSSI is the ceiling on that platform regardless of what AP you buy; and since the fall 2024
Windows release `WlanScan`/`WlanGetNetworkBssList` require precise-location consent and must be
throttled, so a continuous scan loop is a product decision, not just a polling rate.

**R7 — Floor is classified, never regressed.** With ceiling-mounted APs the anchors are
coplanar, so z has no observable gradient in the range residuals. Any z we emitted would be
a floor prior wearing metric units. `PositionEstimate` carries 2D covariance and a discrete
floor. No z regression anywhere in the emit path.

**R8 — Never fake a solve.** Degenerate geometry — under three anchors, collinear anchors,
uncertainty past threshold — is *detected* and downgraded with a recorded reason. A
plausible-looking number the data cannot support is worse than an honest refusal.

**R9 — Fit constants, never assume them.** `A` and `n` in the path-loss model are fitted per
AP. A hardcoded indoor `n = 2.0` is a defect, not a default. An uncalibrated AP is reported
as such and given a wide sigma so it degrades the fix gently instead of corrupting it.

**R10 — Uncertainty is first-class.** Every estimate carries its covariance; the renderer
draws a volume, never a bare point. Reported sigma must track observed error (gated at
calibration ratio 0.5–2.0). An overconfident display lies; a vague one merely underwhelms.

## C. Architecture

**R11 — Adapters know vendors. The engine knows geometry. Neither knows the other.**
Everything crosses that boundary as `ObservationEvent`; the engine emits `PositionEstimate`.
No shortcut where the solver reads a vendor field directly.

**R12 — `packages/engine` is pure.** No I/O, clock, network, randomness or logging side
effects. Enforced by `packages/engine/tests/test_engine_is_pure.py`, which also forbids
importing the simulator or any vendor module.

**R13 — The simulator is the measuring instrument.** It emits through the identical adapter
interface, so the engine cannot tell sim from real. Its figures prove the solver inverts
*our propagation model* — never quote them as real-world accuracy.

**R14 — Report distributions, not means.** p50, p95, max. Always alongside coverage: an
excellent p50 achieved by refusing to answer is not a result.

**R14a — Quote the null estimator beside every figure.** The null is "always answer the centre
of the space", and it needs no survey, no scan and no solver. In a 70 m² flat it scores
**p50 3.34 m, p95 5.25 m** (Monte Carlo, 400k uniform draws over 7×10 m). A number that does
not beat it is not a result, it is an expensive constant — and the smaller the space, the
harder the null is to beat. A figure quoted without its null is unreviewable.

Two specific ways a fingerprint fakes this, both of which must be gated, not hoped about:
- **Random train/test split.** Consecutive samples at one point land on both sides, so k-NN
  retrieves its own neighbour from 250 ms earlier. Inflates accuracy 2–5×. **Hold out by
  location or by a separate walk — never randomly.**
- **Regression shrinkage.** Weighted k-NN pulls toward the training centroid; with mostly
  uninformative features it silently *becomes* the null while reporting a respectable p50.
  Gate it: regress predicted coordinate on true across the held-out set. Slope ≈ 1.0 is real
  localisation; slope ≈ 0.4 means 60% of the "accuracy" is shrinkage. Report
  `std(predicted)/std(truth)` per axis alongside; below ~0.7 you are not localising.

## D. Privacy — this is a surveillance system if built carelessly

**R15 — Mode changes collection, not display.** `asset mode` vs `people mode` must branch the
collection path. A UI toggle over a database that recorded everything anyway is theatre.

**R16 — MAC addresses are personal data.** Hash with a per-tenant salt not derivable from the
tenant id. Retention has a short default and auto-purges.

**R17 — Log the reads.** The audit trail records who looked up *whose* location, append-only.
Location lookups are the sensitive operation here, not writes.

**R18 — Raise privacy implications proactively.** Do not wait to be asked.

## E. Secrets

**R19 — `.env` only.** `.gitignore` first, then `.env`. Never write a key into a tracked file,
never echo a key value into output. Enforced by `.claude/hooks/guard_secrets.py` (PreToolUse).
Council keys are build-time tooling — nothing in Spectra calls them. Retire at end of P2.

## F. Delegation — the subagent workflow is the default, not a fallback

**R20 — Route work to the specialist before doing it yourself.** These agents hold context
this file only summarises.

| Trigger | Agent |
|---|---|
| **A whole phase or milestone** | `delivery-lead` — orchestrator; spawns and sequences the rest |
| Any accuracy claim, propagation model, or figure in metres | `rf-physicist` — **consult before publishing any number** |
| Solver, filters, calibration, `packages/engine` | `positioning-engineer` |
| R3F scene, shaders, instancing, frame budget | `threed-viz-engineer` |
| `apps/web` outside the scene — types, state, PWA, panels | `frontend-engineer` |
| `apps/api` — routes, WebSocket contract, wire DTOs, ports | `api-engineer` |
| `adapters/` — vendor clients, hashing, collection policy | `adapter-engineer` |
| CI, Docker, Helm, locking, secret scanning | `devops-engineer` |
| Auth, tenancy, data model, scale | `enterprise-architect` |
| MAC/retention/audit/tenant isolation, and before any release | `security-privacy-auditor` |
| Tests, fixtures, the accuracy gate | `test-engineer` |
| **Is the suite actually catching anything?** | `mutation-tester` — standing adversary |
| **Stuck twice, or a version/licence/API fact matters** | `web-researcher` — never guess from recall |
| Contested, expensive-to-reverse decisions | `council-chair` → writes `docs/adr/` |

**R21 — Parallelise independent work.** Two or more tasks with no shared state go out
concurrently, not in sequence.

**R22 — Contested decisions get an ADR.** `/council <question> --adr <slug>`. The record keeps
the dissent section — that is the part that matters in six months.

---

## Library decisions (verified 2026-08-20 — several obvious choices are wrong)

| Need | Use | Not |
|---|---|---|
| Multilateration | `scipy.optimize.least_squares`, `loss='soft_l1'`, weights `1/σ²`, seeded from closed-form LS | no maintained package exists |
| Path loss | hand-rolled, fitted per AP (R9) | `rssi` (2018), `itur` (outdoor), `scikit-rf` (S-params) |
| Filtering | `stonesoup` or `pykalman` | `filterpy` — no release since 2018 |
| Fingerprint control | `scikit-learn` KNN — if geometry can't beat it, that's a finding | — |

`scipy.least_squares` may return a sparse matrix or `LinearOperator` for `.jac` — normalise
with `np.asarray` at the boundary. Type checking caught this; the tests did not.

## Data licences — check before vendoring

- ✅ **YorkU** and **UJIIndoorLoc** — CC BY 4.0.
  ⚠️ UJIIndoorLoc encodes "not detected" as sentinel `100`; remap to ≈ −105 dBm or every
  distance computation is silently wrong.
- ⚠️ **UVic FTM/BLE** — no licence declared. Private use only; do not redistribute.
- ❌ **SensorOrgNet RNN** — CC BY-NC-SA. Do not vendor; read the trajectory-synthesis idea only.

## Commands

```bash
uv run pytest -q                                 # full suite
uv run ruff check packages tools adapters .claude/hooks   # lint
uv run mypy packages tools adapters .claude/hooks         # types (strict, tests exempt)
uv run python -m tools.council "<q>" --adr "<slug>"
```

`uv run` deliberately, not `.venv/Scripts/python.exe` — the latter does not exist on Linux,
and the documented command must be the same string CI runs. Test counts are not recorded
here; a number baked into prose goes stale silently. Read it from the suite.

## Status

**P0 complete.** Simulator, engine and accuracy harness built and gated. 20 seeds,
per-AP calibration fitted from a simulated survey:

| | p50 | p95 | coverage | floor | zone | calibration |
|---|---|---|---|---|---|---|
| Calibrated | 1.54–2.61 m | 4.10–6.60 m | 99–100% | 100% | 73–88% | 0.62–1.06 |
| Uncalibrated | 2.5–4.1 m | 9.7–10.9 m | 70–92% | 100% | 53–64% | 0.31–0.64 |

Subject to R13 — these describe the simulator, not a building. **And they cannot be carried
forward as an expectation for a different geometry.** That table came from a simulated
multi-floor building with well-spread anchors. In a 70 m² flat the null estimator alone scores
p50 3.34 m (R14a), so 2.61 m there would be a ~0.7 m win, not the result it looks like here.

**P0.5 was never closed.** Recorded as: accuracy gate PARTIAL · refusal paths PARTIAL · CI
PARTIAL · domain-model changes NOT DONE. The accuracy marker and gate tests have since landed;
the rest has not been re-verified. There is no `docs/specs/` — the design that exists is in
`docs/adr/` and in this file.

**Now: P1-Real** — ship the platform against the hardware that exists: one fixed Windows
desktop, one router, no additions possible.

Three questions were asked and answered by measurement, in this order. Each closed a door:

1. *Can we locate the receiver?* No — one anchor, and a desktop that cannot be carried to
   survey points. Fingerprinting needs a mobile receiver; multilateration needs three anchors.
2. *Can we sense a person device-free?* No. **ADR 0002.** Two channels, six feature families,
   a positive control and a drift control. The channel wanders further on its own in five
   minutes than a human body moves it.
3. *What can this hardware honestly show?* **Range, not position.** With one receiver each AP
   sits on a shell of radius `d ± σ` centred on the PC. Shells are the deliverable; a point
   would be the plausible-looking number R8 forbids.

So the product is two views over one engine: a **live RF view** of what this hardware really
measures (~14 radios, true dBm, ~3 Hz, honest shells and visible refusals), and the
**simulated building** showing what the same engine does when real infrastructure exists
(P0-validated multilateration). R13 keeps their figures in separate tables, always.

## Standing findings

- **WebGL screenshots are unreliable through the Chrome extension.** Three consecutive
  captures came back pure black with zero console errors while the app was working
  perfectly — the DOM had every element; the canvas simply had not presented a frame yet.
  Any Playwright or agent-browser check that asserts on a screenshot needs an explicit
  frame-wait, or it will produce phantom failures. Confirm liveness by reading the DOM
  first, and never conclude "the scene is broken" from a black frame alone. (Separate
  from, and additional to, the known WebGPU capture failure on Windows.)
- **Verify from a clean `uv sync --locked` clone, not the local `.venv`.** Three
  dependencies were installed here and declared nowhere, so the documented mypy command
  passed only on this machine. The environment that is lying is the one you are standing
  in.
- **Pin `@types/*` from the registry, never from memory.** `@types/react-dom@19.2.7` does
  not exist; the install failed. Version recall is wrong often enough that `web-researcher`
  exists for exactly this.


- **ADR 0001 — floor determination:** separate classifier, not a head on the x/y regressor.
- **BSSID-membership floor vote works at 100%** with no ML. Weight by *linear power*, not dBm —
  summing logarithms is meaningless. Any learned classifier must beat this control to ship.
- **Slant-range correction matters.** RSSI measures distance to a ceiling AP; the solve is in
  plan view. At 3 m horizontal with 1.7 m height difference that is a 15% overstatement biasing
  near anchors outward. It vanishes at long range, which is why it is easy to miss.
- **`netsh wlan show networks` is a broken instrument. Never measure with it.** It reported
  **1** access point on the dev laptop where `wlanapi.dll` reported **14 across three fresh
  sweeps**, of which 6–9 are heard in any one sweep. It serves a stale,
  filtered cache and gives signal *percentage*, not dBm, so RSSI has to be reconstructed as
  `pct/2 - 100` — quantised to 2 dB, which is half the sigma budget of a good anchor thrown
  away for free. An entire session's conclusions ("no neighbour networks reachable at this
  location", and the pivot away from localisation that followed) were drawn from that artifact.
  Use `adapters/windows_wlan/scanner.py`.
- **`WlanGetNetworkBssList` is an accumulating cache, not a snapshot — filter by
  `ullHostTimestamp` or the survey is fiction.** Six consecutive calls returned 13, 16, 19, 21,
  22, 23 entries, climbing monotonically and never dropping. Carried-over entries keep a
  byte-identical `lRssi` *and* timestamp: they were remembered, not re-measured, and one call
  mixed readings spanning ~42 s. A stale entry is indistinguishable from a live one except by
  its timestamp, so unfiltered it scores long-departed APs as perfectly persistent and averages
  an RSSI the radio never took. Filtered to entries heard since the sweep was requested, the
  honest count is **6–9 BSSIDs per sweep**, ~14 unioned over three sweeps. `ullHostTimestamp`
  is a FILETIME (100 ns since 1601), verified against the system clock. An AP not heard this
  sweep is a genuine non-detection and must read as absent, never as its last known value.
- **Count physical radios, not BSSIDs.** A 14-BSSID union was ~8 devices. The 2.4/5 GHz radios
  of one box are the same anchor; a locally-administered BSSID differing only in the LA bit of
  the first octet is a virtual/guest BSS on the *same antenna*. Treating those as independent
  silently double-weights that radio in any k-NN metric or solve.
- **RSSI sigma is band-dependent and the gap is large.** Same physical router, stationary
  laptop, 8 sweeps: **σ 0.76 dB on its uncongested 5 GHz radio vs 4.47 dB on its 2.4 GHz**.
  Weight 5 GHz far above 2.4 GHz for in-space anchors — this inverts the "2.4 travels further
  so it's better" instinct.
- **σ = 0.00 at ≤ −85 dBm is a driver clamp, not stability.** Exactly-repeated floor values.
  Worse, those APs sit 1–5 dB above the detection cliff, so they are only *seen* on sweeps
  where fading helped — truncation that biases the mean upward by 1.4–4.8 dB and shortens
  fitted range by 10–31%. The bias is deterministic, so averaging never removes it and a robust
  loss never catches it. Binary seen/not-seen only; never a continuous feature, never an anchor.
- **There is exactly one anchor, and the deployment cannot move.** The dev machine is a
  desktop; the flat has one router (OUI `AC:10:07`, Arcadyan — an ISP ODM box). The other
  strong SSID is a neighbour's (`AC:15:A2`, TP-Link). Its 2.4/5 GHz BSSIDs and the
  locally-administered variant sharing the last four octets are **one radio at one location**.
  With one anchor and no mobile receiver, fingerprinting and multilateration are both out;
  R8 requires refusal, not a plausible number.
- **Windows sensing-channel inventory, measured on AX211 (do not re-derive):**
  `WlanGetNetworkBssList` 0.25 Hz (needs a scan, location-gated) · `wlan_intf_opcode_rssi`
  (0x10000102) **0 Hz — unchanged across 1787 polls and 2801 injected packets** ·
  `realtime_connection_quality` (opcode 19, real dBm, *not* location-gated) **0.10 Hz** ·
  `ulRxRate`/`wlanSignalQuality` ~0.1 Hz · **`wlan_intf_opcode_statistics` (0x10000101)
  19.5 Hz.** The RSSI opcodes are the trap: they return true dBm, so they look like the
  obvious sensing channel while responding to nothing. Only the statistics counters
  (`ullRetryCount`, `ullACKFailureCount`) move fast enough to sense anything, and they need
  injected traffic because an idle machine transmits almost nothing.
- **No FTM on Windows, ever** — Microsoft states there is no API and no public plan. CSI is
  also unavailable: no Windows path exists for Intel, and no monitor mode on Intel+Windows.
  Whether the **AX211 can do CSI on Linux is genuinely unresolved** — FeitCSI's driver fork
  contains the AX211 device IDs (`0x51F0/0x51F1/0x7AF0`) mapped to the AX210 config and the
  CSI path is gated on a firmware capability bit with no device check, but nobody has
  published a success or failure. One agent argued CNVio2 makes it architecturally impossible.
  Settle it in one evening with the FeitCSI live USB: does
  `/sys/kernel/debug/iwlwifi/*/iwlmvm/csi_enabled` exist and do chunks arrive?
- **Device-free sensing: use the associated link only.** Djukić et al. (arXiv 2308.06773)
  measured it — transmitter *inside* the room gives ~100% binary presence; transmitter
  *outside* collapses to **45–72% for one person**. Every neighbour link crosses their flat
  first, so their motion is indistinguishable from ours without geometry we do not have. It is
  also a motion sensor aimed into homes that did not consent (R15/R18). The best-performing,
  only confound-free, and only ethical stream are the same stream.
- **Variance over a window is the feature; the mean of RSSI is not.** Djukić and Ichnaea
  converge on it independently. A body both reflects (raising RSSI) and absorbs (lowering it)
  depending where it stands, so there is no one-way effect on the mean — a feature set built
  on mean shift trains beautifully and fails in deployment. Skew and kurtosis: also
  non-discriminating.
- **Room-level device-free classification is out with one receiver.** Every link shares one
  endpoint, so it is a fan, not a mesh; radio tomography is ill-posed for any voxel no link
  crosses. WiFi's 20–40 MHz bandwidth also caps range resolution at 7.5–15 m, larger than a
  flat, so room-level can never come from ranging — only from learned fingerprints. Expect
  35–60% same-day for 4–6 rooms and near-chance across days. **A second receiver (one ESP32)
  converts the fan into a mesh and is the highest value-per-rupee change available.**
- **Never split device-free data randomly.** RSSI-family signals are heavily autocorrelated;
  windows from one recording leak across a random split and report 80–95% for a detector that
  learned nothing. Split by session, ideally by day, and recalibrate the quiet baseline
  nightly — the idle floor drifted from 0.115 to 0.23 in fifteen minutes here.
- **Prefer differential features (RSSI_i − RSSI_j) to raw dBm.** Cancels common-mode NIC gain
  drift, laptop lid angle, and part of body shadowing. Costs nothing.
- **Body orientation is not a second-order effect.** 3–6 dB typical shadowing, 10–20 dB worst
  case, worse at 5 GHz. On an anchor at 6 m (gradient ~2.2 dB/m) that is **2.3 m of apparent
  position shift from turning around**. Four orientations per survey point is the defensible
  minimum, and the test set must contain orientations and days the training set does not.
