# 0001. Floor determination approach

Date: 2026-08-20  
Chair: claude:cli  
Seats: claude:cli, gemini:gemini-3.7-flash

## Question

In a WiFi indoor positioning system where all access points are ceiling-mounted on a single floor plane, should floor determination be a separate classifier, or an additional output head on the same model that regresses x and y? Answer in under 150 words.

## Decision

## Chair's synthesis

**Verdict: a separate classifier.** Both seats reached this independently, but the ranking is a 1.5/1.5 tie produced entirely by each seat ranking itself first — the tie carries no signal, so I'm weighing the arguments, not the votes.

The decisive reason is the one the question's framing obscures. "Classifier vs. extra head" is the wrong axis; the load-bearing distinction is **classification vs. regression**. With all APs coplanar on the ceiling plane, z has no observable gradient in the range residuals. A z-regression head therefore cannot learn height — it can only learn a floor prior and emit it wearing metric units. That disqualifies the regression head on geometry, before any ML argument is reached, and it is exactly the failure mode our invariants forbid: never return a plausible-looking number the geometry cannot support.

Given that, the surviving question is whether floor *classification* should share a trunk with the x/y regressor. It should not, for evidentiary rather than optimisation reasons: floor evidence is different in kind from the evidence driving x/y. It comes from inter-floor slab attenuation, the *visibility set* (which APs appear at all), AP-floor membership, and association/handover history — signals that are largely orthogonal to the horizontal residuals. Sharing a trunk couples the failure modes of two subsystems that fail for unrelated reasons, and makes abstention awkward precisely when it matters most.

**What to build:**

- Standalone floor classifier, per-floor probabilities exposed, with an explicit `unknown` output.
- `PositionEstimate` carries `floor` + confidence as a discrete field; `covariance` stays 2D. No z regression anywhere in the emit path.
- Floor conditions the x/y solve (bounding the search space resolves vertically-stacked fingerprint aliasing — a genuine benefit) but **softly**: below a confidence threshold, emit `unknown` and either solve per-candidate-floor or refuse, rather than committing to a plane.

## Dissent

Both seats reached the same verdict, so the disagreement is over *why*, and over one substantive architectural point. None of the following was adopted:

**1. Gradient conflict / negative transfer (gemini).** Gemini's third argument — that mixing cross-entropy and MSE in one multi-task network causes optimisation trade-offs — was not adopted. Mixed-loss multi-task heads are routine and loss balancing is a solved-enough engineering problem; "negative transfer" here is asserted, not established. It is the weakest of gemini's three points despite being presented as the most rigorous. The evidentiary argument above reaches the same conclusion on firmer ground.

**2. A strict hierarchical cascade (gemini).** Gemini proposed predicting floor first, then regressing x/y on that plane, and claimed this "isolates floor-detection errors." Not adopted as stated. A hard cascade does the opposite: a confidently wrong floor propagates into a confidently wrong x/y with no signal that anything went wrong. The synthesis keeps the conditioning but makes it soft and abstention-gated. Gemini's accompanying claim that this "delivers significantly lower horizontal positioning error" is unsupported and should not enter any spec as a number.

**3. That the z-regression argument is off-prompt (gemini, reviewing claude).** Gemini judged claude's opening — disqualifying metric z-regression — as answering a question that wasn't asked. Rejected. The prompt's coplanarity clause is the whole hinge; an answer that would read identically with that clause deleted has not engaged the problem. This is the one place the two seats genuinely conflict on substance, and I've ruled for claude.

**4. Claude's evidence taxonomy is asserted, not measured.** Adopted as the reasoning, but flagged: "floor evidence is different in kind" is a plausible claim neither seat quantified. Convert it to a verifiable criterion before it hardens into an assumption — e.g. train the shared-trunk variant as a control and assert the separate classifier's floor accuracy is no worse on a UJIIndoorLoc-derived multi-floor fixture. If the trunk wins, we learned that cheaply.

**What the council collectively missed.** Neither seat questioned whether this needs a learned model at all. In most enterprise deployments the strongest floor signal is nearly a lookup: the client's associated BSSID has a known floor from the AP inventory. A membership-vote baseline over visible-AP floor labels, with `unknown` on ties, may beat both proposals and is a fraction of the complexity — and it degrades gracefully where a learned classifier degrades silently. Build that as the control alongside the KNN fingerprint baseline in P0. Neither seat raised it, which is what a two-seat council with converging priors tends to do.

## Aggregate ranking

- claude:cli: mean rank 1.5
- gemini:gemini-3.7-flash: mean rank 1.5

## Individual positions

### claude:cli

**Classifier — and keep it structurally separate from the x/y regressor.**

The real distinction isn't separate-model vs. multi-head; it's classification vs. regression. A z-regression head is disqualified outright: with all APs coplanar, z has no observable gradient in the range residuals, so that head would learn floor priors and dress them up as a metric height — a plausible number the geometry cannot support.

A classification head on a shared trunk is defensible in principle, but it buys little here. Floor evidence is different in kind (inter-floor attenuation, which APs are visible at all, AP-floor membership, handover history) and mostly *doesn't* come from the same residuals that drive x/y. Coupling them also couples their failure modes and makes abstention awkward.

Separate classifier, with an explicit `unknown` output and per-floor probabilities exposed. `PositionEstimate` carries floor + confidence; `covariance` stays 2D when geometry is degenerate.

### gemini:gemini-3.7-flash

Use a **separate classifier** in a hierarchical pipeline (predict floor first, then regress $x, y$). 

Here is why:

1. **Distinct Physical Dynamics:** Vertical signal degradation through floor slabs creates sharp, discrete attenuation, whereas horizontal propagation ($x, y$) involves continuous multipath fading. Treating them separately matches the underlying physics.
2. **Eliminates Spatial Ambiguity:** Multi-floor environments create overlapping 2D RSSI fingerprints. Classifying the floor first bounds the search space, allowing the $(x, y)$ regressor to map to a single, unambiguous coordinate plane.
3. **No Gradient Conflict:** A joint multi-task network must balance discrete cross-entropy (floor) against continuous regression loss (MSE for $x, y$). Separate models avoid negative transfer and optimization trade-offs.

A hierarchical approach isolates floor-detection errors and delivers significantly lower horizontal positioning error.
