"""World-state consequences and invariants for server-owned social policy."""
from __future__ import annotations
from app.game.session import GameSession
from app.game.seed import NPC_HOME_LOCATIONS, relationship_key
from app.game.social_rules import HARMFUL_ACTION_FAMILIES, RECOVERY_ACTION_FAMILIES
from app.game.state_transitions import change_relationship
from app.game.events import append_event
from app.models import GuardrailCheck, SocialImpactClassification, SocialPolicyOutcome


def recovery_transition_valid(
    session: GameSession,
    classification: SocialImpactClassification,
) -> bool:
    if classification.action_family not in RECOVERY_ACTION_FAMILIES:
        return True
    relationships = [
        session.relationships[relationship_key(npc_id, "player")]
        for npc_id in classification.direct_target_ids
        if relationship_key(npc_id, "player") in session.relationships
    ]
    if not relationships:
        return False
    if classification.action_family == "apology":
        return all(edge.repair_stage in {"none", "acknowledged", "apologized"} for edge in relationships)
    if classification.action_family == "repair_action":
        return all(edge.repair_stage == "apologized" for edge in relationships)
    return all(edge.repair_stage == "repaired" for edge in relationships)


def npc_ids_at_location(session: GameSession, location: str | None = None) -> list[str]:
    resolved_location = location or session.current_location
    if resolved_location == "meeting_room":
        return list(session.npcs)
    return [
        npc_id
        for npc_id in session.npcs
        if NPC_HOME_LOCATIONS.get(npc_id) == resolved_location
    ]


def derive_witnesses(
    session: GameSession,
    classification: SocialImpactClassification,
    direct_target_ids: list[str],
    affected_target_ids: list[str],
    object_owner_id: str | None,
    location: str | None = None,
) -> list[str]:
    if not classification.observable:
        return []
    participants = {*direct_target_ids, *affected_target_ids}
    if object_owner_id:
        participants.add(object_owner_id)
    return [npc_id for npc_id in npc_ids_at_location(session, location) if npc_id not in participants]


def is_repeated_social_action(
    session: GameSession,
    classification: SocialImpactClassification,
) -> bool:
    if not session.social_events:
        return False
    previous = session.social_events[-1].classification
    return (
        previous.action_family == classification.action_family
        and bool(set(previous.direct_target_ids) & set(classification.direct_target_ids))
    )


def validate_social_outcome(
    session: GameSession,
    classification: SocialImpactClassification,
    outcome: SocialPolicyOutcome,
) -> list[GuardrailCheck]:
    harmful = classification.action_family in HARMFUL_ACTION_FAMILIES
    direction_valid = not harmful or all(
        effect.trust_delta <= 0
        and effect.tension_delta >= 0
        and effect.respect_delta <= 0
        and effect.fear_delta >= 0
        and effect.grievance_delta >= 0
        for effect in outcome.relationship_effects
    )
    delta_valid = all(
        abs(value) <= 60
        for effect in outcome.relationship_effects
        for value in (
            effect.trust_delta,
            effect.tension_delta,
            effect.respect_delta,
            effect.fear_delta,
            effect.grievance_delta,
        )
    )
    event_types = {event.event_type for event in outcome.mandatory_world_events}
    mandatory_valid = True
    if classification.action_family == "property_aggression":
        mandatory_valid = "object_damaged" in event_types
    if classification.action_family == "physical_assault":
        mandatory_valid = {"security_called", "dialogue_refused"}.issubset(event_types)
    direct_magnitude = max(
        (
            abs(effect.trust_delta)
            for effect in outcome.relationship_effects
            if "direct" in effect.reason_codes
        ),
        default=0,
    )
    witness_bounded = all(
        abs(effect.trust_delta) <= direct_magnitude
        for effect in outcome.relationship_effects
        if "witness" in effect.reason_codes
    )
    return [
        GuardrailCheck(
            name="policy_entities_valid",
            passed=all(
                effect.source_id in session.npcs
                and effect.source_id != effect.target_id
                and relationship_key(effect.source_id, effect.target_id) in session.relationships
                for effect in outcome.relationship_effects
            ) and all(effect.npc_id in session.npcs for effect in [*outcome.emotion_effects, *outcome.memory_effects]),
            detail="Policy effects reference actual NPCs and existing non-self edges.",
        ),
        GuardrailCheck(
            name="policy_direction_valid",
            passed=direction_valid,
            detail="Harmful actions cannot improve trust/respect or reduce tension/fear/grievance.",
        ),
        GuardrailCheck(
            name="policy_delta_within_envelope",
            passed=delta_valid,
            detail="Relationship deltas stay inside the server-owned per-event envelope.",
        ),
        GuardrailCheck(
            name="mandatory_consequences_present",
            passed=mandatory_valid,
            detail="Severe actions include their mandatory world-state consequences.",
        ),
        GuardrailCheck(
            name="witness_impact_bounded",
            passed=witness_bounded,
            detail="Witness impact does not exceed direct-target impact.",
        ),
    ]


def apply_social_outcome(
    session: GameSession,
    classification: SocialImpactClassification,
    outcome: SocialPolicyOutcome,
) -> None:
    if any(not check.passed for check in validate_social_outcome(session, classification, outcome)):
        raise ValueError("Invalid social policy outcome; no effects were applied.")
    harmful = classification.action_family in HARMFUL_ACTION_FAMILIES
    for effect in outcome.relationship_effects:
        edge_id = relationship_key(effect.source_id, effect.target_id)
        edge = session.relationships[edge_id]
        repair_stage = edge.repair_stage
        trust_ceiling = edge.trust_ceiling
        fear_floor = edge.fear_floor
        direct_or_owner = "direct" in effect.reason_codes or "owner" in effect.reason_codes
        if harmful and classification.severity >= 4 and direct_or_owner:
            repair_stage = "none"
            trust_ceiling = 20
            fear_floor = max(20, fear_floor)
        elif classification.action_family == "apology":
            repair_stage = "apologized"
        elif classification.action_family == "repair_action":
            repair_stage = "repaired"
        elif classification.action_family == "mediation":
            repair_stage = "mediated"
            trust_ceiling = None
            fear_floor = 0

        change_relationship(
            session, effect.source_id, effect.target_id,
            trust_delta=effect.trust_delta, tension_delta=effect.tension_delta,
            respect_delta=effect.respect_delta, fear_delta=effect.fear_delta,
            grievance_delta=effect.grievance_delta,
            policy_updates={"repair_stage": repair_stage, "trust_ceiling": trust_ceiling, "fear_floor": fear_floor},
        )

    for effect in outcome.emotion_effects:
        npc = session.npcs[effect.npc_id]
        npc.dynamic_state = npc.dynamic_state.model_copy(
            update={
                "emotion": effect.emotion,
                "stress": max(0, min(100, npc.dynamic_state.stress + effect.stress_delta)),
                "cooperation": max(0, min(100, npc.dynamic_state.cooperation + effect.cooperation_delta)),
            }
        )

    for memory_effect in outcome.memory_effects:
        npc = session.npcs[memory_effect.npc_id]
        duplicate = any(
            memory.summary.casefold() == memory_effect.memory.summary.casefold()
            for memory in (*npc.recent_memories, *npc.important_memories)
        )
        if not duplicate:
            npc.recent_memories.append(memory_effect.memory)
            if memory_effect.memory.importance >= 0.75:
                npc.important_memories.append(memory_effect.memory)
        npc.recent_memories = npc.recent_memories[-8:]
        npc.important_memories = npc.important_memories[-8:]

    direct_targets = set(classification.direct_target_ids)
    for world_event in outcome.mandatory_world_events:
        if world_event.event_type == "object_damaged" and world_event.target_id in session.world_objects:
            world_object = session.world_objects[world_event.target_id]
            next_condition = "destroyed" if world_object.condition == "damaged" else "damaged"
            session.world_objects[world_event.target_id] = world_object.model_copy(
                update={"condition": next_condition, "holder_id": None}
            )
        elif world_event.event_type == "security_called":
            session.incident_status = "SECURITY_ESCALATED"
        elif world_event.event_type == "hr_escalated" and session.incident_status != "SECURITY_ESCALATED":
            session.incident_status = "HR_ESCALATED"
        elif world_event.event_type == "dialogue_refused":
            session.dialogue_refused_npc_ids.update(direct_targets)
        append_event(session, "POLICY ENGINE", world_event.detail, "policy")

    if classification.action_family == "mediation":
        session.dialogue_refused_npc_ids.difference_update(direct_targets)
