"""AI Court flow: Packet 03 acceptance tests with deterministic mocks."""

import json

import pytest
from pydantic import ValidationError

from app.games.ai_court.module import AiCourtModule
from app.games.ai_court.schemas import CaseRecord, Precedent
from app.games.shared.module import RoundRecord
from app.games.shared.orchestrator import ActionError
from app.games.shared.phases import Phase
from tests.art_fixtures import PRODUCER, advance_to
from tests.court_fixtures import CASE_SETUP, case_built_round, run_trial, to_jury

pytestmark = pytest.mark.anyio


# -- schemas and fiction markers (acceptance 1) -------------------------------

async def test_case_record_cannot_be_non_fictional():
    with pytest.raises(ValidationError):
        CaseRecord.model_validate({
            "case_id": "case_1", "title": "Real Case", "charge": "actual crime",
            "fictional_notice": False,
        })


async def test_precedent_flags_are_forced():
    with pytest.raises(ValidationError):
        Precedent.model_validate({
            "precedent_id": "p1", "case_id": "c1", "holding": "x",
            "scope": "worldwide", "clearly_fictional": True,
        })
    with pytest.raises(ValidationError):
        Precedent.model_validate({
            "precedent_id": "p1", "case_id": "c1", "holding": "x",
            "scope": "prompt_party_only", "clearly_fictional": False,
        })


async def test_case_marked_fictional_in_public_state_and_replay(harness):
    ids = case_built_round(harness)
    public = harness.store.list_events(ids["show_id"])
    created = [s for s in public if s.envelope.event_type == "case.created"][0]
    assert created.envelope.payload["fictional_notice"] is True
    assert created.envelope.payload["case"]["fictional_notice"] is True
    replay = harness.court.replay(ids["round_id"])
    assert replay["fictional_notice"] is True
    assert replay["case"]["fictional_notice"] is True


# -- trial flow ---------------------------------------------------------------

async def test_opening_order_prosecution_first(harness):
    ids = case_built_round(harness)
    with pytest.raises(ActionError, match="prosecution"):
        await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    result = await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    assert result["stage"] == "opening"
    with pytest.raises(ActionError, match="already delivered"):
        await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")


async def test_witnesses_cannot_speak_in_openings(harness):
    ids = case_built_round(harness)
    with pytest.raises(ActionError, match="witnesses do not speak"):
        await harness.court.request_turn(ids["round_id"], PRODUCER, "w1")


async def test_lawyer_citing_unknown_fact_is_flagged_not_blocked(harness):
    ids = case_built_round(harness)
    harness.mock_text.canned["court_turn"] = json.dumps({
        "spoken_line": "The secret note proves everything!",
        "fact_ids_used": ["secret_note"],
        "objection_risk": 0.9,
        "requested_next_action": "continue",
    })
    result = await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    assert result["flags"]["cites_unrecorded_fact"] == ["secret_note"]
    public = harness.store.list_events(ids["show_id"])
    turn_events = [s for s in public if s.envelope.event_type == "trial.turn"]
    assert turn_events[0].envelope.payload["flags"]["cites_unrecorded_fact"] == ["secret_note"]


# -- objections (acceptance 4, 5) --------------------------------------------

async def _with_opening(harness):
    ids = case_built_round(harness)
    turn = await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    return ids, turn


async def test_objection_ruling_uses_validated_code(harness):
    ids, turn = await _with_opening(harness)
    with pytest.raises(ActionError, match="unknown objection type"):
        harness.court.raise_objection(
            ids["round_id"], PRODUCER, "defense", "vibes", turn["turn_id"], "bad vibes"
        )
    objection = harness.court.raise_objection(
        ids["round_id"], PRODUCER, "defense", "facts_not_in_evidence",
        turn["turn_id"], "The note is not in the record.",
    )
    with pytest.raises(ActionError, match="invalid ruling code"):
        await harness.court.rule_objection(
            ids["round_id"], PRODUCER, objection["objection_id"],
            override_ruling="dismissed_with_prejudice",
        )
    ruling = await harness.court.rule_objection(
        ids["round_id"], PRODUCER, objection["objection_id"],
        override_ruling="sustained", override_reason="beyond the record",
    )
    assert ruling["ruling"]["ruling"] == "sustained"
    public = harness.store.list_events(ids["show_id"])
    assert "objection.ruling_overridden" in [s.envelope.event_type for s in public]


async def test_pending_objection_pauses_turns(harness):
    ids, turn = await _with_opening(harness)
    harness.court.raise_objection(
        ids["round_id"], PRODUCER, "defense", "speculation", turn["turn_id"], "guessing"
    )
    with pytest.raises(ActionError, match="objection is pending"):
        await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")


async def test_sustained_objection_repairs_but_preserves_audit_trail(harness):
    ids, turn = await _with_opening(harness)
    objection = harness.court.raise_objection(
        ids["round_id"], PRODUCER, "defense", "facts_not_in_evidence",
        turn["turn_id"], "not in the record",
    )
    await harness.court.rule_objection(
        ids["round_id"], PRODUCER, objection["objection_id"],
        override_ruling="sustained",
    )
    replay = harness.court.replay(ids["round_id"])
    struck = [t for t in replay["turns"] if t["turn_id"] == turn["turn_id"]]
    assert struck[0]["struck"] is True
    assert struck[0]["output"]["spoken_line"]  # line preserved, not erased
    # The struck role must repair before anyone else speaks.
    with pytest.raises(ActionError, match="repair"):
        await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    repair = await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    assert repair["repair"] is True


# -- evidence (acceptance 2) --------------------------------------------------

async def test_unapproved_evidence_never_enters_admitted_record(harness):
    ids = case_built_round(harness)
    from app.controller.engine import Actor
    from app.games.shared.schemas import ActorType

    with pytest.raises(ActionError, match="producer approval"):
        harness.court.propose_evidence(
            ids["round_id"], Actor(ActorType.AUDIENCE, "aud_1"), "a suspicious crumb"
        )
    proposed = harness.court.propose_evidence(
        ids["round_id"], PRODUCER, "a toaster manual with a suspicious dog-ear"
    )
    replay = harness.court.replay(ids["round_id"])
    statuses = {e["evidence_id"]: e["status"] for e in replay["case"]["evidence"]}
    assert statuses[proposed["evidence"]["evidence_id"]] == "pending"
    case = CaseRecord.model_validate(replay["case"])
    assert case.admitted_record()["admitted_evidence"] == []

    ruled = await harness.court.rule_evidence(
        ids["round_id"], PRODUCER, proposed["evidence"]["evidence_id"]
    )
    assert ruled["decision"] in {"admitted", "rejected"}


# -- contradictions (acceptance 6) --------------------------------------------

async def test_witness_contradiction_stays_visible(harness):
    ids = case_built_round(harness)
    await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    harness.court.advance_stage(ids["round_id"], PRODUCER)
    testimony = await harness.court.request_turn(ids["round_id"], PRODUCER, "w1")
    harness.court.flag_contradiction(
        ids["round_id"], PRODUCER, testimony["turn_id"],
        "Claimed to be on the counter and in the drawer simultaneously.",
    )
    public = harness.store.list_events(ids["show_id"])
    assert "testimony.contradiction" in [s.envelope.event_type for s in public]
    replay = harness.court.replay(ids["round_id"])
    flagged = [t for t in replay["turns"] if t["turn_id"] == testimony["turn_id"]]
    assert "contradiction" in flagged[0]["flags"]


# -- recusal (acceptance 9) ---------------------------------------------------

async def test_recused_judge_cannot_rule_without_backup(harness):
    ids, turn = await _with_opening(harness)
    harness.court.recuse(ids["round_id"], PRODUCER)
    objection = harness.court.raise_objection(
        ids["round_id"], PRODUCER, "defense", "relevance", turn["turn_id"], "why"
    )
    with pytest.raises(ActionError, match="recused"):
        await harness.court.rule_objection(
            ids["round_id"], PRODUCER, objection["objection_id"]
        )


async def test_backup_judge_can_rule_after_recusal(harness):
    ids, turn = await _with_opening(harness)
    harness.court.recuse(ids["round_id"], PRODUCER, backup_judge_id="backup_judge")
    objection = harness.court.raise_objection(
        ids["round_id"], PRODUCER, "defense", "relevance", turn["turn_id"], "why"
    )
    ruling = await harness.court.rule_objection(
        ids["round_id"], PRODUCER, objection["objection_id"]
    )
    assert ruling["ruling"]["ruling"] in {"sustained", "overruled", "reserved"}


# -- jury and verdict (acceptance 7) ------------------------------------------

async def test_jury_verdict_is_primary_and_judge_cannot_replace_it(harness):
    ids = case_built_round(harness)
    await to_jury(harness, ids, votes=("guilty", "guilty", "not_guilty"))
    result = harness.court.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
    assert result["winner_id"] == "guilty"

    ruling = await harness.court.final_ruling(ids["round_id"], PRODUCER)
    assert ruling["ruling"]["opinion"]
    # The AI judge's ruling did not change the official verdict.
    assert harness.court.replay(ids["round_id"])["result"]["winner_id"] == "guilty"


async def test_jury_tie_requires_solomonic_producer_choice():
    module = AiCourtModule()
    record = RoundRecord(
        round_id="r", show_id="s", game_id="ai_court", phase=Phase.SCORING,
        votes={"guilty": 2, "not_guilty": 2, "legally_complicated": 0},
    )
    result = module.calculate_score(record)
    assert result.winner_id is None
    assert result.tie_break_used == "solomonic_ruling_then_producer_choice"


# -- precedent (acceptance 8) -------------------------------------------------

async def test_precedent_scoped_to_show(harness):
    ids = case_built_round(harness)
    await to_jury(harness, ids)
    harness.court.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
    precedent = harness.court.create_precedent(
        ids["round_id"], PRODUCER,
        "A toaster note is admissible only when written before the bagel enters the slot.",
    )["precedent"]
    assert precedent["scope"] == "prompt_party_only"
    assert precedent["clearly_fictional"] is True

    # A later round in the SAME show may cite it...
    ids2 = make_second_round(harness, ids["show_id"])
    case2 = harness.court.build_case(
        ids2["round_id"], PRODUCER, **CASE_SETUP,
        prior_precedent_ids=[precedent["precedent_id"]],
    )
    assert case2["case"]["prior_precedent_ids"] == [precedent["precedent_id"]]

    # ...but an unknown/foreign precedent id is rejected.
    ids3 = make_second_round(harness, ids["show_id"])
    with pytest.raises(ActionError, match="not found in this show"):
        harness.court.build_case(
            ids3["round_id"], PRODUCER, **CASE_SETUP,
            prior_precedent_ids=["prec_from_another_universe"],
        )


def make_second_round(harness, show_id):
    from tests.court_fixtures import COURT_CAST

    round_ = harness.controller.create_round(show_id, "ai_court", PRODUCER, cast=COURT_CAST)
    ids = {"show_id": show_id, "round_id": round_["round_id"]}
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(show_id, "again")
    sub = harness.moderation.submit(
        ids["round_id"], member["session_id"], "The People v. The Blender",
        submission_type="case_premise",
    )
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(sub["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], sub["submission_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.ROUND_INTRO)
    return ids


# -- five trials (acceptance 10) ----------------------------------------------

async def test_five_trials_no_case_record_leakage(harness):
    outcomes = []
    for i in range(5):
        ids = case_built_round(harness, premise=f"The People v. Appliance {i}")
        await to_jury(harness, ids)
        harness.court.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
        outcomes.append(ids)

    for i, ids in enumerate(outcomes):
        replay = harness.court.replay(ids["round_id"])
        assert replay["locked_prompt"] == f"The People v. Appliance {i}"
        assert replay["case"]["title"] == f"The People v. Appliance {i}"
        # Case facts belong to this round only, unchanged by later trials.
        fact_texts = [f["text"] for f in replay["case"]["stipulated_facts"]]
        assert fact_texts == CASE_SETUP["stipulated_facts"]
        assert replay["result"]["winner_id"] == "guilty"
        assert len(replay["turns"]) == 6
