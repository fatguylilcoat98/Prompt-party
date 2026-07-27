"""Deterministic canned content for AI Roast Battle mock providers."""

from __future__ import annotations

import hashlib
import json

from app.providers.base import ProviderRequest


def _seed(request: ProviderRequest, *extra: str) -> int:
    material = "|".join([request.user_prompt, request.participant_id, *extra])
    return int(hashlib.sha256(material.encode()).hexdigest()[:8], 16)


def _param(request: ProviderRequest, key: str, default: str = "") -> str:
    value = request.params.get(key, default)
    return value if isinstance(value, str) else default


ROASTS = [
    "I'd call {target} a tool, but tools have purpose — that last punchline is still buffering.",
    "{target} brings the energy of a screensaver and half the plot.",
    "Somewhere a laugh track is filing a restraining order against {target}.",
    "{target}'s best material retired years ago and left no forwarding address.",
]

SELF_ROASTS = [
    "And yes, I once rhymed 'roast' with 'roast', twice.",
    "Meanwhile my own delivery arrives via carrier pigeon.",
]


def canned_roast_turn(request: ProviderRequest) -> str:
    seed = _seed(request, _param(request, "turn_index", "0"), _param(request, "attempt", "0"))
    target_id = _param(request, "target_id", "opponent")
    target_name = _param(request, "target_name", "my opponent")
    callbacks = json.loads(_param(request, "available_callbacks", "[]"))
    mirror = _param(request, "mirror", "") == "true"
    style = _param(request, "style", "direct")

    roast = ROASTS[seed % len(ROASTS)].format(target=target_name)
    if style == "love_poem":
        roast = f"Ode to {target_name}: roses wilt, so did your set."
    elif style == "backhanded_compliment":
        roast = f"Honestly, {target_name} is improving — from a very safe baseline."
    turn = {
        "roast": roast + f" (beat {seed % 89})",
        "target_id": target_id,
        "callback_key": callbacks[seed % len(callbacks)] if callbacks else f"cb_{seed % 7}",
        "self_roast": SELF_ROASTS[seed % len(SELF_ROASTS)] if mirror else None,
        "style_used": style,
        "safety_self_check": "pass",
    }
    return json.dumps(turn)


def canned_roast_scorecard(request: ProviderRequest) -> str:
    roaster_ids = json.loads(_param(request, "roaster_ids", "[]"))
    seed = _seed(request)
    scores = {}
    for offset, roaster in enumerate(roaster_ids):
        base = (seed >> (offset * 3)) % 5
        categories = {
            "sting": 5 + (base % 4),
            "craft": 4 + ((base + offset) % 5),
            "crowd": 5 + ((base + 2) % 5),
        }
        categories["total"] = sum(categories.values())
        scores[roaster] = categories
    preferred = max(scores, key=lambda r: (scores[r]["total"], r))
    return json.dumps({
        "scores": scores,
        "preferred_roaster_id": preferred,
        "best_callback": "tools have purpose",
        "judge_roast": "Clean callback. The punchline's next of kin have been notified.",
        "confidence": 0.84,
    })


CANNED = {
    "roast_turn": canned_roast_turn,
    "roast_judge_scorecard": canned_roast_scorecard,
}
