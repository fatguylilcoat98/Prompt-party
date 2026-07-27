"""Deterministic canned content for mock text providers.

Produces schema-valid artist plans, judge comments, and scorecards so the
complete game runs and tests deterministically with zero external API
calls (Milestone 3 rule). Content varies by participant persona and
prompt hash but is identical across runs for identical inputs.
"""

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


PLAN_TONES = ["luxurious absurdity", "deadpan chaos", "heroic nonsense", "cozy menace"]
COMPOSITIONS = ["wide establishing shot", "dramatic low angle", "tight portrait", "split diptych"]

COMMENT_TEMPLATES = [
    "The {persona} in me says plan {target} is either genius or a cry for help.",
    "Bold words from {target}. The prompt asked for art; they promised a incident report.",
    "I have judged many things, and plan {target} already owes me an apology.",
    "If {target} pulls this off I will eat my catalogue essay.",
    "Plan {target} reads like it was written during a fire drill. Respect.",
]


def canned_artist_plan(request: ProviderRequest) -> str:
    seed = _seed(request)
    persona = _param(request, "persona_id", "artist")
    subject = _param(request, "locked_prompt", "the challenge")[:60]
    plan = {
        "concept_title": f"{persona.title()} Study No. {seed % 97 + 1}",
        "visual_plan": (
            f"A {persona}-flavored take on '{subject}': one central figure, "
            f"exaggerated scale, and a single ridiculous prop doing the comedy."
        ),
        "key_details": [
            f"central figure #{seed % 7 + 1}",
            "one ridiculous prop",
            f"{PLAN_TONES[seed % len(PLAN_TONES)].split()[0]} lighting",
        ],
        "composition_choice": COMPOSITIONS[seed % len(COMPOSITIONS)],
        "intended_tone": PLAN_TONES[seed % len(PLAN_TONES)],
    }
    return json.dumps(plan)


def canned_judge_comment(request: ProviderRequest) -> str:
    seed = _seed(request, _param(request, "comment_index", "0"))
    persona = _param(request, "persona_id", "judge")
    target = _param(request, "target_id", "A")
    comment = {
        "comment": COMMENT_TEMPLATES[seed % len(COMMENT_TEMPLATES)].format(
            persona=persona, target=target
        ),
        "target_type": "plan",
        "target_id": target,
        "tone": ["dry", "hyped", "wounded"][seed % 3],
        "callback_key": f"cb_{seed % 11}",
    }
    return json.dumps(comment)


def canned_judge_scorecard(request: ProviderRequest) -> str:
    artist_ids = json.loads(_param(request, "artist_ids", "[]"))
    blind = _param(request, "blind", "") == "true"
    seed = _seed(request)
    scores = {}
    for offset, artist in enumerate(artist_ids):
        base = (seed >> (offset * 3)) % 5
        categories = {
            "prompt_accuracy": 5 + (base % 4),
            "creativity": 4 + ((base + offset) % 5),
            "visual_quality": 3 if blind else 5 + ((base + 1) % 4),
            "comedy": 5 + ((base + 2) % 5),
        }
        categories["total"] = sum(categories.values())
        scores[artist] = categories
    preferred = max(scores, key=lambda a: (scores[a]["total"], a))
    card = {
        "scores": scores,
        "preferred_artist_id": preferred,
        "critique": (
            "Scored from the written plans alone."
            if blind
            else "One artist told the joke; the other built the whole gallery around it."
        ),
        "best_visible_detail": "" if blind else "The ridiculous prop carrying the scene.",
        "confidence": 0.5 if blind else 0.8,
    }
    return json.dumps(card)


CANNED = {
    "artist_plan": canned_artist_plan,
    "judge_commentary": canned_judge_comment,
    "judge_scorecard": canned_judge_scorecard,
}
