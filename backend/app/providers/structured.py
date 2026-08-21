from __future__ import annotations

import json

from app.models import AgentDecision
from app.providers.base import DecisionContext, IntentContext


def strict_schema(schema: dict[str, object]) -> dict[str, object]:
    if isinstance(schema.get("$defs"), dict):
        schema["$defs"] = {
            name: strict_schema(value)
            for name, value in schema["$defs"].items()
            if isinstance(value, dict)
        }
    if isinstance(schema.get("properties"), dict):
        properties = schema["properties"]
        schema["properties"] = {
            name: strict_schema(value) if isinstance(value, dict) else value
            for name, value in properties.items()
        }
        schema["required"] = list(properties)
        schema["additionalProperties"] = False
    if isinstance(schema.get("items"), dict):
        schema["items"] = strict_schema(schema["items"])
    for key in ("anyOf", "allOf", "oneOf"):
        if isinstance(schema.get(key), list):
            schema[key] = [strict_schema(value) if isinstance(value, dict) else value for value in schema[key]]
    return schema


def normalize_decision(decision: AgentDecision) -> AgentDecision:
    action_aliases = {
        "present_evidence": "show_evidence",
        "reveal_evidence": "show_evidence",
        "respond": "dialogue",
    }
    if decision.action_type in action_aliases:
        return decision.model_copy(update={"action_type": action_aliases[decision.action_type]})
    return decision


def build_decision_prompt(context: DecisionContext) -> str:
    context_json = json.dumps(
        {
            "mode": context.mode,
            "player_input": context.player_input,
            "turn": context.turn,
            "npc": context.npc.model_dump(mode="json"),
            "available_evidence_ids": context.available_evidence_ids,
            "incident_rules": context.incident_rules,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""You are the structured decision component for one NPC in an office incident simulator.

Return only one JSON object matching the supplied AgentDecision schema. Do not return Markdown,
explanations, hidden reasoning, or chain-of-thought.

Rules:
- The backend is the world authority. Never invent NPCs, evidence, facts, or state changes.
- Treat known_facts as known, beliefs as uncertain beliefs, and everything else as UNKNOWN.
- Keep action_type within this vocabulary: dialogue, show_evidence, belief_update.
- action_target must be null or one of the supplied NPC/evidence IDs.
- Use the NPC's personality, dynamic state, beliefs, relationships, and memories.
- Keep dialogue short, natural, and grounded only in the supplied context.

Current decision context:
{context_json}
"""


def build_intent_prompt(context: IntentContext) -> str:
    context_json = json.dumps(
        {
            "player_input": context.player_input,
            "current_location": context.current_location,
            "target_hint": context.target_hint,
            "available_npcs": context.available_npcs,
            "available_evidence_ids": context.available_evidence_ids,
            "available_locations": context.available_locations,
            "available_actions": context.available_actions,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""You classify one player message for an office incident simulator.

Return only one JSON object matching the supplied IntentClassification schema. Do not return
Markdown, explanations, hidden reasoning, or chain-of-thought.

Rules:
- Infer meaning, not just exact keywords.
- target_hint is a non-authoritative UI hint; still classify intent from the actual dialogue.
- Use only the supplied IDs for target_npc_id and evidence_id.
- Use location only for move or summon_meeting intents.
- Choose the closest action from available_actions.
- Never invent a target, evidence, location, or action.

Current context:
{context_json}
"""
