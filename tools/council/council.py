"""Three-stage LLM council.

Stage 1  independent answers, in parallel
Stage 2  anonymised peer review and ranking
Stage 3  synthesis by a rotating chair

Two deliberate divergences from the reference implementation this is modelled on:

  * The chair ROTATES. A fixed chair also ranked itself in stage 2, which is a
    structural conflict of interest.
  * Dissent is PRESERVED. The synthesis must record what a disagreeing seat argued
    and why it was not adopted, rather than averaging it away.

And one bug deliberately not reproduced: when `FINAL RANKING:` is absent we FAIL
rather than regexing the whole response for stray "Response A" mentions, which
silently fabricates an ordering.
"""

from __future__ import annotations

import asyncio
import re
import statistics
from dataclasses import dataclass, field

from .seats import Answer, Seat, default_seats

RANK_HEADER = "FINAL RANKING:"

REVIEW_PROMPT = """You are evaluating different responses to the following question.

Question: {question}

Here are the responses from different models (anonymised):

{responses}

Your task:
1. Evaluate each response individually. For each, say what it does well and what it does poorly.
2. Then, at the very end, give a final ranking.

IMPORTANT: your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line: number, period, space, then ONLY the response label (e.g. "1. Response A")
- No other text in the ranking section

Now provide your evaluation and ranking:"""

SYNTH_PROMPT = """You are the Chair of an LLM Council. Several models answered a question
independently, then ranked each other's answers without knowing who wrote what.

Original question: {question}

STAGE 1 - Independent answers:
{stage1}

STAGE 2 - Peer reviews and rankings:
{stage2}

Aggregate ranking (lower mean rank is better):
{aggregate}

Synthesise these into one clear, well-reasoned answer. You MUST include, as a distinct
section headed "Dissent", any position a seat argued that the majority did not adopt, and
why it was not adopted. If every seat agreed, say so explicitly and state what the council
might therefore have collectively missed. Do not flatten disagreement into false consensus."""

DEBATE_PROMPT = """The council reached unusually strong agreement on this question:

{question}

The agreed position:
{consensus}

Before this is accepted, argue the strongest available case AGAINST it. Identify the
assumption most likely to be wrong, the failure mode nobody named, and what evidence would
distinguish the agreed position from your objection. Be specific and technical. If after
genuine effort you cannot construct a real objection, say so and explain why the agreement
is warranted."""


class RankingParseError(RuntimeError):
    """Raised when a reviewer's ranking block cannot be read. Never guess an order."""


@dataclass
class CouncilResult:
    question: str
    answers: list[Answer]
    reviews: list[Answer]
    aggregate: list[tuple[str, float]]
    synthesis: str
    chair: str
    debate: str | None = None
    parse_failures: list[str] = field(default_factory=list)


def _labels(n: int) -> list[str]:
    return [f"Response {chr(65 + i)}" for i in range(n)]


def parse_ranking(text: str, valid: set[str]) -> list[str]:
    """Extract an ordered list of labels. Raises rather than guessing."""
    if RANK_HEADER not in text:
        raise RankingParseError(f"no '{RANK_HEADER}' block found")
    section = text.rsplit(RANK_HEADER, 1)[1]
    found = re.findall(r"\d+\.\s*(Response [A-Z])", section)
    ordered: list[str] = []
    for label in found:
        if label in valid and label not in ordered:
            ordered.append(label)
    if not ordered:
        raise RankingParseError("ranking block present but contained no valid labels")
    return ordered


def aggregate_ranks(
    rankings: list[list[str]], label_to_seat: dict[str, str]
) -> list[tuple[str, float]]:
    positions: dict[str, list[int]] = {label: [] for label in label_to_seat}
    for ranking in rankings:
        for idx, label in enumerate(ranking, start=1):
            positions[label].append(idx)
    scored = [
        (label_to_seat[label], round(statistics.mean(pos), 2))
        for label, pos in positions.items()
        if pos
    ]
    return sorted(scored, key=lambda pair: pair[1])


def consensus_strength(rankings: list[list[str]]) -> float:
    """Fraction of reviewers agreeing on the top-ranked response. 0.0 if unrankable."""
    if not rankings:
        return 0.0
    tops = [r[0] for r in rankings if r]
    if not tops:
        return 0.0
    return max(tops.count(t) for t in set(tops)) / len(tops)


async def _gather(seats: list[Seat], prompt: str) -> list[Answer]:
    results = await asyncio.gather(*(s.ask(prompt) for s in seats))
    return [r for r in results if r is not None]


async def run_council(
    question: str,
    seats: list[Seat] | None = None,
    chair_index: int = 0,
    debate_threshold: float = 0.8,
) -> CouncilResult:
    seats = seats or default_seats()

    # Stage 1 -------------------------------------------------------------
    answers = await _gather(seats, question)
    if not answers:
        raise RuntimeError("no council seat produced an answer")
    if len(answers) == 1:
        return CouncilResult(
            question=question,
            answers=answers,
            reviews=[],
            aggregate=[(answers[0].seat, 1.0)],
            synthesis=answers[0].text,
            chair=answers[0].seat,
        )

    labels = _labels(len(answers))
    label_to_seat = {lab: a.seat for lab, a in zip(labels, answers, strict=True)}
    blob = "\n\n".join(f"{lab}:\n{a.text}" for lab, a in zip(labels, answers, strict=True))

    # Stage 2 -------------------------------------------------------------
    live = [s for s in seats if s.name in {a.seat for a in answers}]
    reviews = await _gather(live, REVIEW_PROMPT.format(question=question, responses=blob))

    rankings: list[list[str]] = []
    failures: list[str] = []
    for review in reviews:
        try:
            rankings.append(parse_ranking(review.text, set(labels)))
        except RankingParseError as exc:
            failures.append(f"{review.seat}: {exc}")

    aggregate = (
        aggregate_ranks(rankings, label_to_seat) if rankings else [(a.seat, 0.0) for a in answers]
    )

    # Optional debate round ------------------------------------------------
    debate: str | None = None
    if rankings and consensus_strength(rankings) >= debate_threshold:
        best_label = rankings[0][0]
        best = next(a for lab, a in zip(labels, answers, strict=True) if lab == best_label)
        devil = live[(chair_index + 1) % len(live)]
        got = await devil.ask(DEBATE_PROMPT.format(question=question, consensus=best.text))
        debate = got.text if got else None

    # Stage 3 -------------------------------------------------------------
    chair = live[chair_index % len(live)]
    stage1 = "\n\n".join(f"Seat: {a.seat}\nAnswer: {a.text}" for a in answers)
    stage2 = "\n\n".join(f"Seat: {r.seat}\nReview: {r.text}" for r in reviews)
    if debate:
        stage2 += f"\n\nADVERSARIAL CHALLENGE (consensus was suspiciously strong):\n{debate}"
    agg_text = "\n".join(f"{seat}: {rank}" for seat, rank in aggregate) or "unavailable"

    final = await chair.ask(
        SYNTH_PROMPT.format(question=question, stage1=stage1, stage2=stage2, aggregate=agg_text)
    )
    if final is None:
        raise RuntimeError(f"chair {chair.name} failed to synthesise")

    return CouncilResult(
        question=question,
        answers=answers,
        reviews=reviews,
        aggregate=aggregate,
        synthesis=final.text,
        chair=chair.name,
        debate=debate,
        parse_failures=failures,
    )
