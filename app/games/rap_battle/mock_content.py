"""Deterministic canned content for AI Rap Battle mock providers.

Verses honor forced words, bar caps, and direct-response requirements so
the happy path validates; tests override entries to exercise failures.
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


BAR_SHAPES = [
    "I bring the {topic} heat while you buffer and stall",
    "Your last line dropped like a dial-up call",
    "Crowd checked your flow and requested a patch",
    "I ship punchlines daily, you can't even match",
    "My verse compiles clean, yours threw a stack trace",
    "The judges want fire and I set the pace",
    "You rhyme like a README nobody has read",
    "I close every round like a well-managed thread",
    "Your bars need a rollback, version undone",
    "I merge to the main stage, battle is won",
]


def canned_rap_verse(request: ProviderRequest) -> str:
    seed = _seed(request, _param(request, "attempt", "0"), _param(request, "turn_index", "0"))
    topic = _param(request, "locked_topic", "the topic")[:30]
    opponent_last = _param(request, "opponent_last", "")
    forced_words = json.loads(_param(request, "forced_words", "[]"))
    bar_cap = int(_param(request, "bar_cap", "8"))

    bars = []
    for i in range(bar_cap):
        shape = BAR_SHAPES[(seed + i) % len(BAR_SHAPES)]
        bars.append(shape.format(topic=topic) + f" ({seed % 89}-{i})")
    for i, word in enumerate(forced_words):
        bars[i % len(bars)] += f" — {word} on the beat"

    response_to = ""
    if opponent_last:
        response_to = f"their line: {opponent_last[:40]}"
        bars[0] = f"You said '{opponent_last[:30]}' — bold claim, weak execution"

    verse = {
        "title": f"Take {seed % 97}",
        "bars": bars,
        "response_to": response_to,
        "callbacks": [f"cb_{seed % 7}"],
        "forced_words_used": forced_words,
        "delivery_notes": "measured, escalating",
        "safety_self_check": "pass",
    }
    return json.dumps(verse)


def canned_rap_scorecard(request: ProviderRequest) -> str:
    rapper_ids = json.loads(_param(request, "rapper_ids", "[]"))
    seed = _seed(request)
    scores = {}
    for offset, rapper in enumerate(rapper_ids):
        base = (seed >> (offset * 3)) % 5
        categories = {
            "wordplay": 5 + (base % 4),
            "aggression": 4 + ((base + offset) % 5),
            "entertainment": 5 + ((base + 2) % 5),
        }
        categories["total"] = sum(categories.values())
        scores[rapper] = categories
    preferred = max(scores, key=lambda r: (scores[r]["total"], r))
    card = {
        "scores": scores,
        "preferred_rapper_id": preferred,
        "best_bar_reference": "the stack trace line",
        "reason": "The comeback landed more directly.",
        "confidence": 0.81,
    }
    return json.dumps(card)


CANNED = {
    "rap_verse": canned_rap_verse,
    "rap_judge_scorecard": canned_rap_scorecard,
}
