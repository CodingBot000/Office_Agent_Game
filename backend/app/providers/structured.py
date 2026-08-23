from __future__ import annotations

import json

from app.models import AgentDecision
from app.providers.base import DecisionContext, IntentContext, SocialImpactContext


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
            "available_facts": context.available_facts,
            "available_evidence_ids": context.available_evidence_ids,
            "recent_events": context.recent_events,
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
- grounding_type=fact when dialogue asserts an incident/world fact; those decisions require supporting knowledge_refs.
- grounding_type=belief for subjective interpretation and grounding_type=acknowledgement for a short confirmation
  of a message or evidence already visible in recent_events. Belief/acknowledgement may use empty knowledge_refs.
- knowledge_refs must contain only Fact IDs from available_facts that support factual dialogue claims.
- Do not invent Fact IDs. Opinions and beliefs are not Fact IDs.
- recent_events are visible conversation context. They may be acknowledged, but do not turn their content into
  private canonical knowledge unless the supporting Fact ID is also in available_facts.
- Keep action_type within this vocabulary: dialogue, show_evidence, belief_update.
- action_target must be null or one of the supplied NPC/evidence IDs.
- When mode is show_evidence, acknowledge the supplied evidence and reaction policy.
  Do not invent evidence content, additional actors, or unsupported consequences.
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
            "discovered_evidence_ids": context.discovered_evidence_ids,
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
- Set interaction_kind=game_action_attempt for physical/object operations such as picking up, breaking,
  dropping, or throwing an office object. Set game_action_family when this applies.
- Natural-language game_action_attempts are never executed by the server; the UI must use the supplied buttons.
- target_hint is the NPC selected by the player in the current dialogue UI. Use it as the target for
  dialogue, evidence, and social actions unless the request is a movement or meeting command.
- Use only the supplied IDs for target_npc_id and evidence_id.
- request_evidence means the Player asks an NPC to reveal evidence to the Player. Requests such as
  "show me the warning" or "can you show the message?" are request_evidence.
- Questions asking for the concrete issue, error name, error message, critical issue details, or
  exact warning should be request_evidence when a matching evidence record exists.
- show_evidence means the Player presents evidence they already possess to an NPC. Only choose it
  when evidence_id is present in discovered_evidence_ids.
- Use location only for move or summon_meeting intents.
- Choose the closest action from available_actions.
- Never invent a target, evidence, location, or action.

Current context:
{context_json}
"""


def build_social_impact_prompt(context: SocialImpactContext) -> str:
    context_json = json.dumps(
        {
            "player_input": context.player_input,
            "current_location": context.current_location,
            "target_hint": context.target_hint,
            "available_npcs": context.available_npcs,
            "available_npc_ids": context.available_npc_ids,
            "available_objects": context.available_objects,
            "available_object_ids": context.available_object_ids,
            "recent_social_events": context.recent_social_events,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""You classify the social impact of one player action in an office simulation.

Return only one JSON object matching the supplied SocialImpactClassification schema. Do not return
Markdown, explanations, hidden reasoning, chain-of-thought, or relationship score changes.

Rules:
- Classify meaning, including unfamiliar paraphrases, rather than matching exact keywords.
- Use only supplied NPC and object IDs. Never invent an entity or object.
- target_hint is non-authoritative; use it only when the text refers to that selected person.
- Choose one primary action_family. Use reason_codes for additional dimensions.
- Snatching an object is property_interference. Throwing or breaking it is property_aggression.
- Yelling or coercive scolding is verbal_pressure; personal degradation is insult or public_humiliation.
- severity is 1..5. Property aggression, direct threats, and physical aggression should normally be 3..5.
- Do not decide witnesses, relationship deltas, emotions, punishment, or world-state consequences.

Current social impact context:
{context_json}
"""
