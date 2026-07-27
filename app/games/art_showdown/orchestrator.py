"""AI Art Showdown round orchestrator (Packet 01).

Runs the game's AI actions — plans, generation, commentary, scoring —
and the reveal/winner flow. It executes *within* phases; advancing the
official phase remains a controller/producer action (Authority rules).
Provider failures become recorded failures plus themed public states,
never raw exceptions (section 9 truth rule + Failure Recovery table).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.audience.voting import VotingService
from app.controller.engine import Actor, GameStateController, NotFoundError
from app.events.store import EventStore
from app.games.art_showdown.module import ArtShowdownModule, GAME_ID
from app.games.art_showdown.schemas import (
    ArtistPlan,
    JudgeComment,
    JudgeScorecard,
    comment_claims_image,
)
from app.games.shared.module import RoundRecord
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType, Capability, EventEnvelope, new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import AIRequestRecord, ErrorRecord, MediaAsset, Round
from app.providers.base import Operation, ProviderRequest, RequestStatus
from app.providers.registry import ProviderRegistry

# Single ActionError class shared by every game orchestrator (the API
# layer maps it to HTTP 422); re-exported here for compatibility.
from app.games.shared.orchestrator import ActionError  # noqa: F401


#: Themed generation status labels (section 8: no fake percentages).
GENERATION_LABELS = {
    "queued": "Stretching the canvas...",
    "generating": "Summoning pigments...",
    "complete": "Canvas ready",
    "failed": "The easel has wobbled",
}

MAX_COMMENTS_PER_JUDGE = 4
MAX_CONSECUTIVE_SAME_JUDGE = 2


class ArtShowdownOrchestrator:
    #: Producer actions callable via POST /rounds/{id}/actions/{action_id}.
    ACTIONS = frozenset({
        "request_plans", "start_generation", "retry_generation",
        "request_commentary", "reveal", "request_scores",
    })

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_store: EventStore,
        providers: ProviderRegistry,
        controller: GameStateController,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._sessions = session_factory
        self._events = event_store
        self._providers = providers
        self._controller = controller
        self._clock = clock
        self._new_id = id_factory
        self.module = ArtShowdownModule()

    # -- helpers ---------------------------------------------------------

    def _get_round(self, session: Session, round_id: str) -> Round:
        round_ = session.get(Round, round_id)
        if round_ is None or round_.game_id != GAME_ID:
            raise NotFoundError(f"art_showdown round {round_id!r} not found")
        return round_

    @staticmethod
    def _require_phase(round_: Round, *phases: Phase) -> None:
        if Phase(round_.phase) not in phases:
            allowed = "/".join(p.value for p in phases)
            raise ActionError(
                f"action requires phase {allowed} (round is in {round_.phase})"
            )

    def _provider_for(self, participant: dict, operation: Operation):
        """Participant schema (Master Spec 5.1) has one provider field, but
        a seat may need both text and image operations. Smallest safe
        interpretation: use the participant's provider when it supports the
        operation, else the first registered provider that does."""
        preferred = self._providers.get(participant["provider"])
        if preferred.supports(operation):
            return preferred
        return self._providers.first_supporting(operation)

    @staticmethod
    def _cast(round_: Round, seat_type: str) -> list[dict]:
        return [
            p for p in (round_.cast or [])
            if p.get("seat_type") == seat_type and p.get("enabled", True)
        ]

    def _emit(self, session: Session, round_: Round, event_type: str,
              actor_type: ActorType, actor_id: str, public: bool, payload: dict) -> None:
        self._events.append(
            session,
            EventEnvelope(
                event_id=self._new_id("evt"),
                event_type=event_type,
                show_id=round_.show_id,
                round_id=round_.round_id,
                game_id=GAME_ID,
                phase=Phase(round_.phase),
                actor_type=actor_type,
                actor_id=actor_id,
                timestamp=self._clock(),
                public=public,
                payload=payload,
            ),
        )

    def _record_ai_call(self, session: Session, round_id: str, request: ProviderRequest,
                        response, parse_status: str | None, retry_count: int) -> None:
        session.add(
            AIRequestRecord(
                request_id=request.request_id,
                round_id=round_id,
                participant_id=request.participant_id,
                operation=request.operation.value,
                template_id=request.template_id,
                template_version=request.template_version,
                provider=response.provider,
                model=response.model,
                status=response.status.value,
                parse_status=parse_status,
                latency_ms=response.latency_ms,
                usage=response.usage,
                retry_count=retry_count,
                created_at=self._clock(),
            )
        )

    def _update_data(self, session: Session, round_: Round, **changes) -> None:
        round_.data = {**(round_.data or {}), **changes}
        round_.updated_at = self._clock()

    def _record_failure(self, session: Session, round_: Round, *, error_type: str,
                        detail: str, provider: str | None = None, model: str | None = None,
                        operation: str | None = None, retry_count: int = 0,
                        recovery_action: str | None = None) -> str:
        """Record a failure inside the current transaction (Master Spec
        section 9): private errors ledger + private event; raw detail
        never reaches a public surface."""
        error_id = self._new_id("err")
        session.add(
            ErrorRecord(
                error_id=error_id, show_id=round_.show_id, round_id=round_.round_id,
                provider=provider, model=model, operation=operation,
                error_type=error_type, detail=detail, retry_count=retry_count,
                recovery_action=recovery_action, created_at=self._clock(),
            )
        )
        self._emit(
            session, round_, "failure.recorded",
            ActorType.CONTROLLER, "controller", public=False,
            payload={"error_id": error_id, "error_type": error_type,
                     "operation": operation, "retry_count": retry_count,
                     "recovery_action": recovery_action, "detail": detail},
        )
        return error_id

    # -- artist planning (section 7) ------------------------------------

    async def request_plans(self, round_id: str, actor: Actor) -> dict:
        plans: dict[str, dict] = {}
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CONTESTANT_PLANNING)
            if not round_.locked_prompt:
                raise ActionError("no locked prompt; lock a submission first")
            artists = self._cast(round_, "contestant")
            if len(artists) < 2:
                raise ActionError("art_showdown needs at least two artists in the cast")
            modifier = (round_.data or {}).get("active_modifier")

            for artist in artists:
                plan, request, response, parse_status, retries = await self._plan_for(
                    artist, round_.locked_prompt, modifier
                )
                self._record_ai_call(session, round_id, request, response, parse_status, retries)
                if plan is None:
                    self._record_failure(
                        session, round_,
                        error_type="plan_failed", detail=parse_status or response.error_detail or "",
                        provider=response.provider, model=response.model,
                        operation="text_generation", retry_count=retries,
                        recovery_action="artist_continues_without_plan",
                    )
                    continue
                plans[artist["participant_id"]] = plan.model_dump()
                self._emit(
                    session, round_, "artist.plan_created",
                    ActorType.AI, artist["participant_id"], public=True,
                    payload={"plan": plan.model_dump()},
                )
            self._update_data(session, round_, plans=plans)
        return {"round_id": round_id, "plans": plans}

    async def _plan_for(self, artist: dict, locked_prompt: str, modifier: dict | None):
        provider = self._provider_for(artist, Operation.TEXT_GENERATION)
        base_params = {
            "persona_id": artist.get("persona_id", "artist"),
            "locked_prompt": locked_prompt,
        }
        retries = 0
        request = None
        response = None
        for attempt in range(2):  # one repair attempt (section 7 contract)
            request = ProviderRequest(
                request_id=self._new_id("req"),
                participant_id=artist["participant_id"],
                operation=Operation.TEXT_GENERATION,
                template_id="artist_plan",
                template_version=self.module.manifest.prompts["artist_plan"],
                system_prompt="You are an AI art contestant in a live comedy game show.",
                user_prompt=(
                    f"CHALLENGE: {locked_prompt}\n"
                    f"PERSONA: {artist.get('persona_id', 'artist')}\n"
                    f"ACTIVE MODIFIER: {(modifier or {}).get('modifier_id', 'none')}\n"
                    "Return JSON only, <=60 words visual plan."
                ),
                params={**base_params, "attempt": str(attempt)},
                timeout_seconds=artist.get("timeout_seconds", 45),
            )
            response = await provider.generate(request)
            retries = attempt
            if response.status is not RequestStatus.OK:
                continue
            try:
                return ArtistPlan.model_validate_json(response.text), request, response, "ok", retries
            except Exception as exc:
                parse_error = f"invalid_plan: {exc}"
                if attempt == 1:
                    return None, request, response, parse_error, retries
        return None, request, response, response.error_detail or "provider_failed", retries

    # -- image generation (section 8) ------------------------------------

    async def start_generation(self, round_id: str, actor: Actor, only_failed: bool = False) -> dict:
        results: dict[str, dict] = {}
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            if not round_.locked_prompt:
                raise ActionError("no locked prompt; cannot generate")
            generation = dict((round_.data or {}).get("generation", {}))
            plans = (round_.data or {}).get("plans", {})

            for artist in self._cast(round_, "contestant"):
                artist_id = artist["participant_id"]
                current = generation.get(artist_id, {})
                if current.get("status") == "complete":
                    # Never duplicate a successful generation (acceptance 5).
                    results[artist_id] = current
                    continue
                if only_failed and current.get("status") not in {"failed", None} and current:
                    results[artist_id] = current
                    continue

                plan = plans.get(artist_id, {})
                enrichment = plan.get("visual_plan", "")
                provider = self._provider_for(artist, Operation.IMAGE_GENERATION)
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=artist_id,
                    operation=Operation.IMAGE_GENERATION,
                    template_id="image_generation",
                    template_version="1.0",
                    # The artist plan enriches but never replaces the
                    # audience prompt (section 8).
                    user_prompt=f"{round_.locked_prompt}\nArtist plan: {enrichment}",
                    params={"aspect_ratio": "16:9"},
                    timeout_seconds=artist.get("timeout_seconds", 120),
                )
                self._emit(
                    session, round_, "generation.status",
                    ActorType.CONTROLLER, "controller", public=True,
                    payload={"artist_id": artist_id, "status": "generating",
                             "label": GENERATION_LABELS["generating"]},
                )
                response = await provider.generate(request)
                retry_count = current.get("attempts", 0)
                self._record_ai_call(session, round_id, request, response, None, retry_count)

                if response.status is RequestStatus.OK and response.media is not None:
                    asset_id = self._new_id("asset")
                    session.add(
                        MediaAsset(
                            asset_id=asset_id,
                            round_id=round_id,
                            media_type="image",
                            path=response.media.path,
                            mime_type=response.media.mime_type,
                            checksum_sha256=response.media.checksum_sha256,
                            width=response.media.width,
                            height=response.media.height,
                            moderation_status="approved",
                            created_at=self._clock(),
                        )
                    )
                    generation[artist_id] = {
                        "status": "complete", "asset_id": asset_id,
                        "label": GENERATION_LABELS["complete"],
                        "attempts": retry_count + 1,
                    }
                    self._emit(
                        session, round_, "generation.status",
                        ActorType.CONTROLLER, "controller", public=True,
                        payload={"artist_id": artist_id, "status": "complete",
                                 "label": GENERATION_LABELS["complete"]},
                    )
                else:
                    self._record_failure(
                        session, round_,
                        error_type=f"image_{response.status.value}",
                        detail=response.error_detail or "no media returned",
                        provider=response.provider, model=response.model,
                        operation="image_generation", retry_count=retry_count,
                        recovery_action="producer_may_retry",
                    )
                    generation[artist_id] = {
                        "status": "failed",
                        "label": GENERATION_LABELS["failed"],
                        "attempts": retry_count + 1,
                    }
                    # Honest themed failure state, no raw detail (section 9).
                    self._emit(
                        session, round_, "generation.status",
                        ActorType.CONTROLLER, "controller", public=True,
                        payload={"artist_id": artist_id, "status": "failed",
                                 "label": GENERATION_LABELS["failed"]},
                    )
                results[artist_id] = generation[artist_id]
            self._update_data(session, round_, generation=generation)
        return {"round_id": round_id, "generation": results}

    async def retry_generation(self, round_id: str, actor: Actor) -> dict:
        return await self.start_generation(round_id, actor, only_failed=True)

    # -- commentary (section 9) ------------------------------------------

    async def request_commentary(self, round_id: str, actor: Actor, judge_id: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.PRE_REVEAL_COMMENTARY)
            judges = {j["participant_id"]: j for j in self._cast(round_, "commentator")}
            if judge_id not in judges:
                raise ActionError(f"{judge_id!r} is not a judge in this round")
            commentary = list((round_.data or {}).get("commentary", []))

            per_judge = sum(1 for c in commentary if c["judge_id"] == judge_id)
            if per_judge >= MAX_COMMENTS_PER_JUDGE:
                raise ActionError(f"{judge_id} reached {MAX_COMMENTS_PER_JUDGE} pre-reveal comments")
            recent = [c["judge_id"] for c in commentary[-MAX_CONSECUTIVE_SAME_JUDGE:]]
            if len(recent) == MAX_CONSECUTIVE_SAME_JUDGE and all(j == judge_id for j in recent):
                raise ActionError(
                    f"no more than {MAX_CONSECUTIVE_SAME_JUDGE} consecutive comments "
                    "from the same judge"
                )

            judge = judges[judge_id]
            plans = (round_.data or {}).get("plans", {})
            target_id = sorted(plans)[len(commentary) % len(plans)] if plans else "prompt"
            comment, request, response, parse_status, retries = await self._comment_for(
                judge, round_, target_id, existing=[c["comment"]["comment"] for c in commentary],
                comment_index=len(commentary),
            )
            self._record_ai_call(session, round_id, request, response, parse_status, retries)
            if comment is None:
                # Persist the failure record by leaving this transaction
                # cleanly, then reject the action.
                self._record_failure(
                    session, round_,
                    error_type="commentary_failed",
                    detail=parse_status or response.error_detail or "",
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="skip_comment",
                )
            else:
                commentary.append({"judge_id": judge_id, "comment": comment.model_dump()})
                self._update_data(session, round_, commentary=commentary)
                self._emit(
                    session, round_, "judge.commentary_created",
                    ActorType.AI, judge_id, public=True,
                    payload={"comment": comment.model_dump()},
                )
        if comment is None:
            raise ActionError("commentary failed; judge stays quiet this beat")
        return {"round_id": round_id, "judge_id": judge_id, "comment": comment.model_dump()}

    async def _comment_for(self, judge: dict, round_: Round, target_id: str,
                           existing: list[str], comment_index: int):
        provider = self._provider_for(judge, Operation.TEXT_GENERATION)
        retries = 0
        request = response = None
        for attempt in range(2):  # near-duplicates regenerate once (section 9)
            request = ProviderRequest(
                request_id=self._new_id("req"),
                participant_id=judge["participant_id"],
                operation=Operation.TEXT_GENERATION,
                template_id="judge_commentary",
                template_version=self.module.manifest.prompts["judge_commentary"],
                system_prompt=f"You are {judge.get('display_name')} in AI Art Showdown.",
                user_prompt=(
                    f"Current phase: PRE_REVEAL_COMMENTARY\n"
                    f"Challenge: {round_.locked_prompt}\n"
                    "React to the prompt, plans, or another judge in <=45 words. "
                    "Never claim to see an image before reveal. Return JSON."
                ),
                params={
                    "persona_id": judge.get("persona_id", "judge"),
                    "target_id": target_id,
                    "comment_index": str(comment_index + attempt * 100),
                },
                timeout_seconds=judge.get("timeout_seconds", 30),
            )
            response = await provider.generate(request)
            retries = attempt
            if response.status is not RequestStatus.OK:
                continue
            try:
                comment = JudgeComment.model_validate_json(response.text)
            except Exception as exc:
                if attempt == 1:
                    return None, request, response, f"invalid_comment: {exc}", retries
                continue
            if comment_claims_image(comment.comment):
                # Pre-reveal image claims are rejected and regenerated once.
                if attempt == 1:
                    return None, request, response, "image_claim_before_reveal", retries
                continue
            if comment.comment in existing:
                if attempt == 1:
                    return None, request, response, "duplicate_punchline", retries
                continue
            return comment, request, response, "ok", retries
        return None, request, response, response.error_detail or "provider_failed", retries

    # -- reveal (section 8 validation) -----------------------------------

    def reveal(self, round_id: str, actor: Actor, artist_ids: list[str] | None = None) -> dict:
        revealed: list[dict] = []
        invalid: str | None = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.REVEAL)
            generation = (round_.data or {}).get("generation", {})
            targets = artist_ids or [
                a for a, g in generation.items() if g.get("status") == "complete"
            ]
            if not targets:
                raise ActionError("no completed artwork available to reveal")
            for artist_id in targets:
                info = generation.get(artist_id, {})
                if info.get("status") != "complete":
                    raise ActionError(f"{artist_id} has no completed artwork")
                asset = session.get(MediaAsset, info["asset_id"])
                problem = self._validate_asset(asset)
                if problem:
                    # Persist the failure, reveal nothing at all.
                    self._record_failure(
                        session, round_,
                        error_type="invalid_media_asset", detail=problem,
                        operation="reveal", recovery_action="quarantine_and_retry",
                    )
                    invalid = artist_id
                    revealed = []
                    break
                revealed.append({"artist_id": artist_id, "asset_id": asset.asset_id,
                                 "url": f"/api/media/{asset.asset_id}"})
            if invalid is None:
                self._update_data(session, round_, revealed=[r["artist_id"] for r in revealed])
                for item in revealed:
                    self._emit(
                        session, round_, "artwork.revealed",
                        actor.actor_type, actor.actor_id, public=True, payload=item,
                    )
        if invalid is not None:
            raise ActionError(
                f"artwork for {invalid} failed validation and cannot be revealed"
            )
        return {"round_id": round_id, "revealed": revealed}

    @staticmethod
    def _validate_asset(asset: MediaAsset | None) -> str | None:
        """File existence, MIME, checksum, and moderation gate before any
        public reveal (section 8)."""
        if asset is None:
            return "asset record missing"
        path = Path(asset.path)
        if not path.exists():
            return f"file missing: {asset.path}"
        if asset.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            return f"disallowed mime type: {asset.mime_type}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != asset.checksum_sha256:
            return "checksum mismatch"
        if asset.moderation_status != "approved":
            return f"moderation status {asset.moderation_status!r}"
        return None

    # -- final judging (section 10) --------------------------------------

    async def request_scores(self, round_id: str, actor: Actor, judge_id: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.FINAL_JUDGING)
            judges = {j["participant_id"]: j for j in self._cast(round_, "commentator")}
            if judge_id not in judges:
                raise ActionError(f"{judge_id!r} is not a judge in this round")
            judge = judges[judge_id]
            revealed = (round_.data or {}).get("revealed", [])
            if not revealed:
                raise ActionError("no revealed artwork to judge")
            # A judge without vision access scores blind and is labeled so
            # (section 10: no visual claims without vision).
            blind = Capability.VISION_INPUT.value not in judge.get("capabilities", [])

            provider = self._provider_for(judge, Operation.TEXT_GENERATION)
            retries = 0
            card = request = response = None
            parse_status = "provider_failed"
            for attempt in range(2):
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=judge_id,
                    operation=Operation.TEXT_GENERATION,
                    template_id="judge_scorecard",
                    template_version=self.module.manifest.prompts["judge_scorecard"],
                    system_prompt=f"You are {judge.get('display_name')} judging AI Art Showdown.",
                    user_prompt=(
                        f"Challenge: {round_.locked_prompt}\n"
                        + ("Score from plans only; you cannot see the images.\n" if blind
                           else "Score the revealed images with grounded critique.\n")
                        + "Integer categories 1-10, totals must equal sums. Return JSON."
                    ),
                    params={
                        "persona_id": judge.get("persona_id", "judge"),
                        "artist_ids": json.dumps(sorted(revealed)),
                        "blind": "true" if blind else "false",
                        "attempt": str(attempt),
                    },
                    timeout_seconds=judge.get("timeout_seconds", 60),
                )
                response = await provider.generate(request)
                retries = attempt
                if response.status is not RequestStatus.OK:
                    continue
                try:
                    card = JudgeScorecard.model_validate_json(response.text)
                    parse_status = "ok"
                    break
                except Exception as exc:
                    parse_status = f"invalid_scorecard: {exc}"
            self._record_ai_call(session, round_id, request, response, parse_status, retries)
            if card is None:
                # Persist the failure by committing this transaction, then
                # reject the action.
                self._record_failure(
                    session, round_,
                    error_type="scorecard_failed", detail=parse_status,
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="reweight_remaining_valid_judges",
                )
            else:
                judge_scores = dict((round_.data or {}).get("judge_scores", {}))
                judge_scores[judge_id] = {**card.model_dump(), "blind": blind}
                self._update_data(session, round_, judge_scores=judge_scores)
                self._emit(
                    session, round_, "judge.scorecard_created",
                    ActorType.AI, judge_id, public=True,
                    payload={"scorecard": card.model_dump(), "blind": blind},
                )
        if card is None:
            raise ActionError("scorecard failed; remaining judges are reweighted")
        return {"round_id": round_id, "judge_id": judge_id,
                "scorecard": card.model_dump(), "blind": blind}

    # -- winner (section 11) ---------------------------------------------

    def calculate_winner(self, round_id: str, actor: Actor, voting: VotingService) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            data = round_.data or {}
            vote_state = data.get("voting", {})
            if vote_state.get("status") != "closed":
                raise ActionError("voting must be closed before calculating the winner")
            record = RoundRecord(
                round_id=round_id, show_id=round_.show_id, game_id=GAME_ID,
                phase=Phase(round_.phase), locked_prompt=round_.locked_prompt,
                votes=vote_state.get("tally", {}),
                judge_scores=data.get("judge_scores", {}),
            )
            score = self.module.calculate_score(record)
            result = {
                "winner_id": score.winner_id,
                "is_draw": score.is_draw,
                "components": score.components,
                "tie_break_used": score.tie_break_used,
                "official": True,
            }
            round_.result = result
            round_.updated_at = self._clock()
            self._emit(
                session, round_, "round.winner_calculated",
                actor.actor_type, actor.actor_id, public=True, payload=result,
            )
        return {"round_id": round_id, **result}

    def override_winner(self, round_id: str, actor: Actor, winner_id: str, reason: str) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("winner override is producer-only")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            previous = round_.result or {}
            round_.result = {
                **previous, "winner_id": winner_id, "is_draw": False,
                "overridden": True, "override_reason": reason, "official": True,
            }
            round_.updated_at = self._clock()
            # Overrides are always audited (Master Spec section 15).
            self._emit(
                session, round_, "round.winner_overridden",
                actor.actor_type, actor.actor_id, public=True,
                payload={"winner_id": winner_id, "reason": reason,
                         "previous_winner_id": previous.get("winner_id")},
            )
        return {"round_id": round_id, "winner_id": winner_id, "overridden": True}

    # -- replay record ----------------------------------------------------

    def replay(self, round_id: str) -> dict:
        """Public-safe replay record: prompt, outputs, commentary, scores,
        votes, failures and recoveries — no secrets, no raw provider data
        (Master Spec sections 3 and 15)."""
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            data = round_.data or {}
            from app.persistence.models import ErrorRecord

            failures = [
                {
                    "error_type": e.error_type,
                    "operation": e.operation,
                    "retry_count": e.retry_count,
                    "recovery_action": e.recovery_action,
                }
                for e in session.query(ErrorRecord)
                .filter(ErrorRecord.round_id == round_id)
                .order_by(ErrorRecord.created_at)
                .all()
            ]
            timeline = [
                {"seq": s.seq, "event_type": s.envelope.event_type,
                 "actor_id": s.envelope.actor_id, "payload": s.envelope.payload}
                for s in self._events.list_events(round_.show_id, public_only=True)
                if s.envelope.round_id == round_id
            ]
        return {
            "round_id": round_id,
            "game_id": GAME_ID,
            "phase": round_.phase,
            "locked_prompt": round_.locked_prompt,
            "cast": round_.cast,
            "plans": data.get("plans", {}),
            "generation": data.get("generation", {}),
            "commentary": data.get("commentary", []),
            "judge_scores": data.get("judge_scores", {}),
            "votes": data.get("voting", {}).get("tally", {}),
            "result": round_.result,
            "failures": failures,
            "timeline": timeline,
        }
