---
description: Convene the multi-model LLM council on a contested decision, optionally writing an ADR
argument-hint: <question> [--adr <slug>]
allowed-tools: Bash(.venv/Scripts/python.exe -m tools.council:*), Read, Write
---

Convene the council on: $ARGUMENTS

Run it with:

```
.venv/Scripts/python.exe -m tools.council "<the question>" --adr "<short-slug>"
```

Before running, sharpen the question. A council answering an under-specified question
produces confident agreement about the wrong problem. Include the real constraints in the
brief where they bear on the answer:

- coplanar APs make z geometrically unobservable, so floor is classified not regressed
- RSSI range error is heavy-tailed and grows with distance
- neither the browser nor iOS can perform WiFi sensing; the client is a viewer
- `packages/engine` is pure and does no I/O

Use the council only for decisions that are genuinely contested and expensive to reverse.
Routine implementation choices do not need three vendors and a chair.

**Do not send proprietary positioning IP through the free Gemini seat** — that tier trains
on submitted content. If the question would expose novel algorithm details, say so and ask
whether to drop the Gemini seat for this run.

After it completes, report the synthesis, and state the dissent separately and prominently.
The dissent is the part that matters in six months.
