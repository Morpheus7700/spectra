# Spectra — WiFi 3D Spatial Awareness Platform

Locate objects in 3D within a WiFi network's range. Installable PWA + website from one build.
Enterprise target: IT / facilities. Design: `docs/specs/` · Decisions: `docs/adr/`

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

**R6 — The client cannot sense.** iOS has no WiFi scanning API (`NEHotspotHelper` is
entitlement-gated to hotspot login and forbidden for location). Browsers have none at all.
Architecture is therefore **infrastructure senses → server solves → client renders**. Any
proposal where the phone or browser does the sensing is wrong on its face.

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
| Any accuracy claim, propagation model, or figure in metres | `rf-physicist` — **consult before publishing any number** |
| Solver, filters, calibration, `packages/engine` | `positioning-engineer` |
| R3F scene, shaders, PWA, frame budget | `threed-viz-engineer` |
| Auth, tenancy, API, deployment, scale | `enterprise-architect` |
| MAC/retention/audit/tenant isolation, and before any release | `security-privacy-auditor` |
| Tests, fixtures, the accuracy gate | `test-engineer` |
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
.venv/Scripts/python.exe -m pytest -q                  # full suite (148 tests)
.venv/Scripts/python.exe -m ruff check packages tools  # lint
.venv/Scripts/python.exe -m mypy packages tools        # types (strict on source)
.venv/Scripts/python.exe -m tools.council "<q>" --adr "<slug>"
uv pip install <pkg>                                   # venv at .venv/
```

## Status

**P0 complete.** Simulator, engine and accuracy harness built and gated. Three seeds,
per-AP calibration fitted from a simulated survey:

| | p50 | p95 | coverage | floor | zone | calibration |
|---|---|---|---|---|---|---|
| Calibrated | 1.7–2.6 m | 5.0–5.6 m | 100% | 100% | 81–84% | 0.93–1.37 |
| Uncalibrated | 2.5–4.1 m | 9.7–10.9 m | 70–92% | 100% | 53–64% | 0.31–0.64 |

Subject to R13 — these describe the simulator, not a building.

**Next: P1** — R3F viewer rendering the covariance ellipsoids, live WebSocket, PWA install.
`confidence_ellipse_axes()` already returns semi-major, semi-minor and rotation, so the
renderer has no geometry left to invent.

## Standing findings

- **ADR 0001 — floor determination:** separate classifier, not a head on the x/y regressor.
- **BSSID-membership floor vote works at 100%** with no ML. Weight by *linear power*, not dBm —
  summing logarithms is meaningless. Any learned classifier must beat this control to ship.
- **Slant-range correction matters.** RSSI measures distance to a ceiling AP; the solve is in
  plan view. At 3 m horizontal with 1.7 m height difference that is a 15% overstatement biasing
  near anchors outward. It vanishes at long range, which is why it is easy to miss.
