"""Game catalog: manifests plus ready-to-use default casts.

The producer console builds rounds from this catalog. Casts follow the
Participant schema (Master Spec 5.1); AI Court uses the role-as-id
convention documented in its orchestrator.
"""

from __future__ import annotations

from app.games.ai_court.module import MANIFEST as COURT_MANIFEST
from app.games.art_showdown.module import MANIFEST as ART_MANIFEST
from app.games.improv.module import MANIFEST as IMPROV_MANIFEST
from app.games.rap_battle.module import MANIFEST as RAP_MANIFEST
from app.games.roast_battle.module import MANIFEST as ROAST_MANIFEST

MANIFESTS = {
    m.game_id: m
    for m in (ART_MANIFEST, RAP_MANIFEST, COURT_MANIFEST, IMPROV_MANIFEST, ROAST_MANIFEST)
}


def _seat(pid: str, name: str, seat_type: str, persona: str,
          capabilities: list[str], provider: str = "mock_text") -> dict:
    return {
        "participant_id": pid, "display_name": name,
        "provider": provider, "model": "configured-model",
        "seat_type": seat_type, "capabilities": capabilities,
        "persona_id": persona, "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    }


TEXT = ["text_generation", "structured_output"]
VISION = ["text_generation", "vision_input", "structured_output"]

DEFAULT_CASTS: dict[str, list[dict]] = {
    "art_showdown": [
        _seat("artist_01", "Pix", "contestant", "precision",
              ["image_generation", "text_generation"], provider="mock_image"),
        _seat("artist_02", "Smudge", "contestant", "vibe",
              ["image_generation", "text_generation"], provider="mock_image"),
        _seat("judge_01", "Verdict", "commentator", "formalist", VISION),
        _seat("judge_02", "Litera", "commentator", "literalist", VISION),
        _seat("judge_03", "Wreck", "commentator", "chaos_critic", TEXT),
    ],
    "rap_battle": [
        _seat("rapper_01", "Packet Loss", "contestant", "technical", TEXT),
        _seat("rapper_02", "Sir Loops-a-Lot", "contestant", "absurdist", TEXT),
        _seat("judge_01", "Purist", "commentator", "purist", TEXT),
        _seat("judge_02", "Hype", "commentator", "hype_person", TEXT),
        _seat("judge_03", "Snark", "commentator", "snarker", TEXT),
    ],
    "ai_court": [
        _seat("judge", "Judge Gavelsworth", "commentator", "dry_procedural", TEXT),
        _seat("backup_judge", "Judge Backup", "commentator", "chaotic_backup", TEXT),
        _seat("prosecutor", "Prosecutor Zeal", "contestant", "overzealous", TEXT),
        _seat("defense", "Counsel Chill", "contestant", "unbothered", TEXT),
        _seat("w1", "Kitchen Spoon", "contestant", "chaotic_witness", TEXT),
    ],
    "improv": [
        _seat("p1", "Mara", "contestant", "grounded", TEXT),
        _seat("p2", "Boris", "contestant", "big_choices", TEXT),
        _seat("p3", "Quill", "contestant", "precise_weirdo", TEXT),
        _seat("p4", "Understudy", "contestant", "utility", TEXT),
        _seat("host_ai", "Emcee", "host", "warm_host", TEXT),
    ],
    "roast_battle": [
        _seat("roaster_01", "Scattershot", "contestant", "rapid_fire", TEXT),
        _seat("roaster_02", "Deadpan", "contestant", "surgical", TEXT),
        _seat("judge_01", "Wounded", "commentator", "wounded", TEXT),
        _seat("judge_02", "Technical", "commentator", "technical", TEXT),
        _seat("judge_03", "Chaotic", "commentator", "chaotic", TEXT),
    ],
}


def catalog() -> list[dict]:
    games = []
    for game_id, manifest in MANIFESTS.items():
        games.append({
            "game_id": game_id,
            "display_name": manifest.display_name,
            "version": manifest.version,
            "phases": {p.value: label for p, label in manifest.phases.items()},
            "actions": {
                p.value: groups for p, groups in manifest.actions.items()
            },
            "modifiers": [
                {
                    "modifier_id": m.modifier_id,
                    "display_name": m.display_name,
                    "allowed_phases": [p.value for p in m.allowed_phases],
                    "effect_type": m.effect.type,
                }
                for m in manifest.modifiers
            ],
            "scoring": manifest.scoring,
            "default_cast": DEFAULT_CASTS[game_id],
        })
    return games
