"""Tests for the pure protocol logic — no network, no API keys.

The ranking parser is the piece worth testing hardest: the implementation it is
modelled on silently fabricates an ordering when the ranking block is missing.
Ours must fail instead.
"""

import pytest

from tools.council.council import (
    RankingParseError,
    aggregate_ranks,
    consensus_strength,
    parse_ranking,
)

LABELS = {"Response A", "Response B", "Response C"}


def test_parses_well_formed_ranking():
    text = "A is good. B is weak.\n\nFINAL RANKING:\n1. Response C\n2. Response A\n3. Response B"
    assert parse_ranking(text, LABELS) == ["Response C", "Response A", "Response B"]


def test_uses_last_ranking_block_when_header_repeated():
    text = (
        "I will end with FINAL RANKING: as instructed.\n"
        "FINAL RANKING:\n1. Response B\n2. Response A\n3. Response C"
    )
    assert parse_ranking(text, LABELS)[0] == "Response B"


def test_missing_header_raises_rather_than_guessing():
    # The reference implementation regexes the whole body here and invents an order
    # from in-prose mentions. That is the bug this test exists to prevent.
    text = "Response A was thorough, though Response C had better structure overall."
    with pytest.raises(RankingParseError):
        parse_ranking(text, LABELS)


def test_header_present_but_no_valid_labels_raises():
    with pytest.raises(RankingParseError):
        parse_ranking("FINAL RANKING:\n1. The first one\n2. The second", LABELS)


def test_unknown_labels_are_discarded():
    text = "FINAL RANKING:\n1. Response Z\n2. Response A\n3. Response B"
    assert parse_ranking(text, LABELS) == ["Response A", "Response B"]


def test_duplicate_labels_are_deduplicated():
    text = "FINAL RANKING:\n1. Response A\n2. Response A\n3. Response B"
    assert parse_ranking(text, LABELS) == ["Response A", "Response B"]


def test_aggregate_orders_by_mean_rank():
    mapping = {"Response A": "seat-a", "Response B": "seat-b"}
    rankings = [
        ["Response B", "Response A"],
        ["Response B", "Response A"],
        ["Response A", "Response B"],
    ]
    result = aggregate_ranks(rankings, mapping)
    assert result[0][0] == "seat-b"
    assert result[0][1] == pytest.approx(4 / 3, abs=0.01)


def test_unanimous_agreement_scores_one():
    assert consensus_strength([["Response A"], ["Response A"], ["Response A"]]) == 1.0


def test_split_opinion_scores_below_debate_threshold():
    rankings = [["Response A"], ["Response B"], ["Response C"]]
    assert consensus_strength(rankings) < 0.8


def test_no_rankings_scores_zero():
    assert consensus_strength([]) == 0.0
