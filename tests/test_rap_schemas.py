"""Rap Battle contracts: verse schema, scorecard validation, forced-word
matching, and 60/40 scoring with the wordplay tie order (Packet 02)."""

import pytest
from pydantic import ValidationError

from app.games.rap_battle.module import RapBattleModule
from app.games.rap_battle.schemas import (
    RapScorecard,
    RapVerse,
    forced_words_missing,
)
from app.games.shared.module import RoundRecord, TransitionError
from app.games.shared.phases import Phase

module = RapBattleModule()


def verse_data(**overrides):
    data = {
        "title": "Packet Loss",
        "bars": [
            "You stack weak bars like packets in a queue",
            "I route every punchline straight back through you",
        ],
        "response_to": "opponent line about Wi-Fi",
        "callbacks": ["router anxiety"],
        "forced_words_used": ["modem"],
        "delivery_notes": "measured, escalating",
        "safety_self_check": "pass",
    }
    data.update(overrides)
    return data


def test_spec_example_verse_validates():
    verse = RapVerse.model_validate(verse_data())
    assert len(verse.bars) == 2


def test_bars_must_be_an_array_not_a_blob():
    with pytest.raises(ValidationError):
        RapVerse.model_validate(verse_data(bars="one long unbounded blob of text"))


def test_blank_bars_rejected():
    with pytest.raises(ValidationError, match="blank"):
        RapVerse.model_validate(verse_data(bars=["real bar", "   "]))


def test_absolute_bar_cap():
    with pytest.raises(ValidationError):
        RapVerse.model_validate(verse_data(bars=["bar"] * 17))


def test_forced_word_matching_is_case_insensitive_and_normalized():
    bars = ["My MODEM, screams at midnight", "fiber optic dreams"]
    assert forced_words_missing(bars, ["modem", "fiber"]) == []
    assert forced_words_missing(bars, ["router"]) == ["router"]
    # Punctuation-attached words still match after normalization.
    assert forced_words_missing(["got that modem."], ["Modem"]) == []


def scorecard(a, b, preferred="rapper_a"):
    def cat(scores):
        return {
            "wordplay": scores[0], "aggression": scores[1],
            "entertainment": scores[2], "total": sum(scores),
        }
    return {
        "scores": {"rapper_a": cat(a), "rapper_b": cat(b)},
        "preferred_rapper_id": preferred,
        "best_bar_reference": "router with anxiety",
        "reason": "The comeback landed more directly.",
        "confidence": 0.81,
    }


def test_scorecard_total_must_validate():
    bad = scorecard([8, 7, 9], [7, 9, 8])
    bad["scores"]["rapper_a"]["total"] = 25
    with pytest.raises(ValidationError, match="category sum"):
        RapScorecard.model_validate(bad)


def test_scorecard_integer_bounds():
    with pytest.raises(ValidationError):
        RapScorecard.model_validate(scorecard([11, 7, 9], [7, 9, 8]))


def test_module_transitions_follow_shared_rule():
    module.validate_transition(Phase.CREATION_ACTIVE, Phase.PRE_REVEAL_COMMENTARY)
    with pytest.raises(TransitionError):
        module.validate_transition(Phase.CREATION_ACTIVE, Phase.SCORING)


def record(votes, judge_scores):
    return RoundRecord(
        round_id="round_1", show_id="show_1", game_id="rap_battle",
        phase=Phase.SCORING, locked_prompt="topic",
        votes=votes, judge_scores=judge_scores,
    )


def test_winner_60_40_known_numbers():
    result = module.calculate_score(
        record(
            votes={"rapper_a": 7, "rapper_b": 3},
            judge_scores={"judge_01": scorecard([6, 6, 6], [9, 9, 9], "rapper_b")},
        )
    )
    a = result.components["rapper_a"]
    assert a["audience_share"] == 0.7
    assert a["judge_normalized"] == 0.6  # 18/30
    assert a["combined"] == pytest.approx(0.6 * 0.7 + 0.4 * 0.6)
    assert result.winner_id == "rapper_a"  # 0.66 vs 0.54


def test_tie_breaks_on_wordplay_average():
    # Same combined and same audience share; wordplay decides.
    result = module.calculate_score(
        record(
            votes={"rapper_a": 5, "rapper_b": 5},
            judge_scores={
                "judge_01": scorecard([9, 5, 7], [5, 9, 7], "rapper_a"),
            },
        )
    )
    assert result.winner_id == "rapper_a"
    assert result.tie_break_used == "wordplay_average"


def test_full_tie_requires_sudden_death():
    result = module.calculate_score(
        record(
            votes={"rapper_a": 5, "rapper_b": 5},
            judge_scores={"judge_01": scorecard([7, 7, 7], [7, 7, 7])},
        )
    )
    assert result.winner_id is None
    assert result.tie_break_used == "requires_sudden_death_or_runoff"
