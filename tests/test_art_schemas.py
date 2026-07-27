"""Art Showdown contracts: plan limits, scorecard validation, 60/40
scoring and the spec tie order (Packet 01 sections 7, 10, 11)."""

import pytest
from pydantic import ValidationError

from app.games.art_showdown.module import ArtShowdownModule
from app.games.art_showdown.schemas import ArtistPlan, JudgeScorecard
from app.games.shared.module import RoundRecord, TransitionError
from app.games.shared.phases import Phase

module = ArtShowdownModule()


def plan_data(**overrides):
    data = {
        "concept_title": "The Feathered Penthouse",
        "visual_plan": "A tuxedo raccoon checks pigeons into a marble lobby.",
        "key_details": ["raccoon manager", "pigeon guests"],
        "composition_choice": "wide lobby scene",
        "intended_tone": "luxurious absurdity",
    }
    data.update(overrides)
    return data


def test_spec_example_plan_validates():
    ArtistPlan.model_validate(plan_data())


def test_plan_word_limit_enforced():
    with pytest.raises(ValidationError, match="60 words"):
        ArtistPlan.model_validate(plan_data(visual_plan="word " * 61))


def test_plan_cannot_claim_existing_image():
    with pytest.raises(ValidationError, match="already exists"):
        ArtistPlan.model_validate(
            plan_data(visual_plan="In the image you can already see the raccoon.")
        )


def test_plan_key_details_capped_at_four():
    with pytest.raises(ValidationError):
        ArtistPlan.model_validate(plan_data(key_details=["a", "b", "c", "d", "e"]))


def scorecard(a_scores, b_scores, preferred="artist_a"):
    def cat(scores):
        return {
            "prompt_accuracy": scores[0], "creativity": scores[1],
            "visual_quality": scores[2], "comedy": scores[3],
            "total": sum(scores),
        }
    return {
        "scores": {"artist_a": cat(a_scores), "artist_b": cat(b_scores)},
        "preferred_artist_id": preferred,
        "critique": "One built a joke, one built a monument.",
        "best_visible_detail": "The pigeon concierge.",
        "confidence": 0.8,
    }


def test_scorecard_total_must_equal_sum():
    bad = scorecard([8, 7, 6, 9], [6, 9, 8, 7])
    bad["scores"]["artist_a"]["total"] = 31
    with pytest.raises(ValidationError, match="category sum"):
        JudgeScorecard.model_validate(bad)


def test_scorecard_categories_bounded():
    with pytest.raises(ValidationError):
        JudgeScorecard.model_validate(scorecard([11, 7, 6, 9], [6, 9, 8, 7]))


def test_module_transition_validation_matches_shared_rule():
    module.validate_transition(Phase.LOBBY, Phase.SUBMISSION_OPEN)
    with pytest.raises(TransitionError):
        module.validate_transition(Phase.LOBBY, Phase.REVEAL)


def record(votes, judge_scores):
    return RoundRecord(
        round_id="round_1", show_id="show_1", game_id="art_showdown",
        phase=Phase.SCORING, locked_prompt="x",
        votes=votes, judge_scores=judge_scores,
    )


def test_winner_calculation_known_numbers():
    # Audience: 60/40 split for artist_a. Judges: artist_b ahead 32 vs 28.
    result = module.calculate_score(
        record(
            votes={"artist_a": 6, "artist_b": 4},
            judge_scores={"judge_01": scorecard([7, 7, 7, 7], [8, 8, 8, 8], "artist_b")},
        )
    )
    a = result.components["artist_a"]
    assert a["audience_share"] == 0.6
    assert a["judge_normalized"] == 0.7  # 28/40
    assert a["combined"] == pytest.approx(0.6 * 0.6 + 0.4 * 0.7)
    b = result.components["artist_b"]
    assert b["combined"] == pytest.approx(0.6 * 0.4 + 0.4 * 0.8)
    assert result.winner_id == "artist_a"  # 0.64 vs 0.56
    assert result.is_draw is False


def test_tie_breaks_by_audience_share():
    # Equal combined: a has more votes, b has better judges.
    result = module.calculate_score(
        record(
            votes={"artist_a": 2, "artist_b": 1},
            judge_scores={
                "judge_01": scorecard([5, 5, 5, 5], [7, 7, 7, 7], "artist_b"),
            },
        )
    )
    # combined a = .6*(2/3)+.4*.5 = .6; combined b = .6*(1/3)+.4*.7 = .48
    assert result.winner_id == "artist_a"


def test_exact_tie_reports_unresolved_for_sudden_death():
    result = module.calculate_score(
        record(
            votes={"artist_a": 5, "artist_b": 5},
            judge_scores={"judge_01": scorecard([7, 7, 7, 7], [7, 7, 7, 7])},
        )
    )
    assert result.winner_id is None
    assert result.is_draw is True
    assert result.tie_break_used == "requires_sudden_death_or_producer"


def test_abstained_judges_are_reweighted_out():
    result = module.calculate_score(
        record(
            votes={"artist_a": 1, "artist_b": 0},
            judge_scores={
                "judge_01": scorecard([5, 5, 5, 5], [9, 9, 9, 9], "artist_b"),
                "judge_02": {**scorecard([9, 9, 9, 9], [5, 5, 5, 5]), "abstained": True},
            },
        )
    )
    # Only judge_01 counts: b judge_norm 0.9 vs a 0.5; a has all votes.
    assert result.components["artist_b"]["judge_normalized"] == 0.9
    assert result.winner_id == "artist_a"  # 0.6*1+0.4*0.5=0.8 vs 0.36


def test_zero_votes_falls_back_to_judges():
    result = module.calculate_score(
        record(
            votes={"artist_a": 0, "artist_b": 0},
            judge_scores={"judge_01": scorecard([5, 5, 5, 5], [8, 8, 8, 8], "artist_b")},
        )
    )
    assert result.winner_id == "artist_b"
