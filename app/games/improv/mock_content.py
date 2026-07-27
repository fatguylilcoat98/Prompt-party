"""Deterministic canned content for AI Improv mock providers."""

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


LINES = [
    "Then I will marry the {thing} and inherit its debts.",
    "Yes, and the {thing} has been watching us this whole time.",
    "I accept that, which is why I brought the {thing} a lawyer.",
    "Of course — the {thing} taught me everything I know about {genre}.",
]

OFFERS = [
    "The {thing} demands a ceremony at closing time.",
    "The {thing} secretly runs the whole building.",
    "Someone owes the {thing} an apology and a receipt.",
    "The {thing} is up for a promotion nobody approved.",
]


def canned_improv_turn(request: ProviderRequest) -> str:
    seed = _seed(request, _param(request, "turn_index", "0"), _param(request, "attempt", "0"))
    state = json.loads(_param(request, "scene_state", "{}"))
    facts = state.get("established_facts", [])
    objects = state.get("active_objects", ["mysterious prop"])
    thing = objects[seed % len(objects)] if objects else "mysterious prop"
    genre = state.get("genre", "comedy")
    twist = _param(request, "twist", "")

    line = LINES[seed % len(LINES)].format(thing=thing, genre=genre)
    if twist:
        line = f"({twist}!) " + line
    turn = {
        "spoken_line": line,
        "accepted_offer": _param(request, "last_offer", "the scene as established"),
        "new_offer": OFFERS[seed % len(OFFERS)].format(thing=thing),
        "referenced_fact_ids": [facts[seed % len(facts)]["fact_id"]] if facts else [],
        "proposed_state_updates": (
            [{"type": "relationship", "value": f"{_param(request, 'character_name', 'Someone')} now owes the {thing} a favor"}]
            if seed % 2 == 0 else []
        ),
    }
    return json.dumps(turn)


def canned_improv_recap(request: ProviderRequest) -> str:
    state = json.loads(_param(request, "scene_state", "{}"))
    facts = [f["text"] for f in state.get("established_facts", [])]
    recap = "So far: " + ("; ".join(facts[:4]) if facts else "a scene is forming") + "."
    return json.dumps({
        "spoken_line": recap,
        "accepted_offer": "everything established",
        "new_offer": "",
        "referenced_fact_ids": [f["fact_id"] for f in state.get("established_facts", [])][:4],
        "proposed_state_updates": [],
    })


CANNED = {
    "improv_turn": canned_improv_turn,
    "improv_recap": canned_improv_recap,
}
