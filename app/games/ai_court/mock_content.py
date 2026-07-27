"""Deterministic canned content for AI Court mock providers."""

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


LINES = {
    "prosecution": [
        "The record shows {fact}, and the prosecution intends to prove every crumb of it.",
        "Members of the jury, {fact} — and that, we submit, is no accident.",
    ],
    "defense": [
        "My client stands accused, yet {fact} proves nothing but enthusiasm.",
        "The prosecution leans on {fact}; the defense leans on common sense.",
    ],
    "witness": [
        "I was there. {fact}. I remember it like it was this morning, which it may have been.",
        "Under oath I confirm: {fact}. I regret nothing.",
    ],
}


def canned_court_turn(request: ProviderRequest) -> str:
    seed = _seed(request, _param(request, "stage", ""), _param(request, "turn_index", "0"))
    role = _param(request, "role_kind", "prosecution")
    fact_ids = json.loads(_param(request, "fact_ids", "[]"))
    used = fact_ids[seed % max(len(fact_ids), 1):][:2] if fact_ids else []
    fact_text = used[0] if used else "the record"
    template = LINES.get(role, LINES["prosecution"])[seed % 2]
    turn = {
        "spoken_line": template.format(fact=fact_text),
        "fact_ids_used": used,
        "objection_risk": round((seed % 60) / 100, 2),
        "requested_next_action": "continue",
    }
    return json.dumps(turn)


def canned_objection_ruling(request: ProviderRequest) -> str:
    seed = _seed(request)
    sustained = seed % 2 == 0
    ruling = {
        "ruling": "sustained" if sustained else "overruled",
        "explanation": (
            "The claim reaches beyond the admitted record. Strike it."
            if sustained else "Counsel is irritating but technically within the record."
        ),
        "repair_instruction": "Restate using only admitted facts." if sustained else "",
        "score_effect": 0,
    }
    return json.dumps(ruling)


def canned_evidence_ruling(request: ProviderRequest) -> str:
    seed = _seed(request)
    admitted = seed % 3 != 0
    return json.dumps({
        "decision": "admitted" if admitted else "rejected",
        "explanation": (
            "Marginally relevant and extremely funny. Admitted."
            if admitted else "This court has standards, however low. Rejected."
        ),
    })


def canned_final_ruling(request: ProviderRequest) -> str:
    verdict = _param(request, "verdict", "legally_complicated")
    return json.dumps({
        "opinion": (
            f"The jury has spoken: {verdict.replace('_', ' ')}. The court notes, "
            "for the fictional record, that the toaster showed remarkable composure."
        ),
        "supports_verdict": True,
        "precedent_holding": "An appliance's silence may not be construed as smugness.",
    })


CANNED = {
    "court_turn": canned_court_turn,
    "court_objection_ruling": canned_objection_ruling,
    "court_evidence_ruling": canned_evidence_ruling,
    "court_final_ruling": canned_final_ruling,
}
