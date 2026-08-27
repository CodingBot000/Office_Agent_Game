"""NPC-visible context and candidate validation, without provider execution."""
from __future__ import annotations
import re
from app.game.session import GameSession
from app.game.seed import FACT_REGISTRY, INCIDENT_RULES, RESPONSIBILITY_FACT_IDS, KNOWN_FACT_CONTRADICTION_PATTERNS, relationship_key
from app.game.evidence_policy import available_fact_ids, can_provide_evidence, visible_evidence_ids, evidence_id_from_event
from app.models import NPCState, IntentClassification, SocialImpactClassification, SocialPolicyOutcome, AgentDecision, GuardrailCheck
from app.providers.base import DecisionContext

ALLOWED_AGENT_ACTION_TYPES = {"dialogue", "show_evidence", "belief_update"}


def build_decision_context(
    session: GameSession, npc: NPCState, mode: str, player_input: str,
    intent: IntentClassification | None = None, *,
    social_classification: SocialImpactClassification | None = None,
    social_outcome: SocialPolicyOutcome | None = None,
) -> DecisionContext:
    question_type = intent.question_type if intent is not None else "none"
    reference_scope = intent.reference_scope if intent is not None else "none"
    referenced_evidence_id = intent.evidence_id if intent is not None else None
    visible_ids = visible_evidence_ids(session, npc)
    referenced_evidence = session.evidences.get(referenced_evidence_id or "") if referenced_evidence_id in visible_ids else None
    fact_ids = available_fact_ids(session, npc)
    context_npc = npc.model_copy(deep=True, update={
        "known_fact_ids": fact_ids,
        "known_facts": [FACT_REGISTRY[fact_id].statement for fact_id in fact_ids if fact_id in FACT_REGISTRY],
    })
    edge = session.relationships[relationship_key(npc.id, "player")]
    required_kind = "refusal" if npc.id in session.dialogue_refused_npc_ids else "recovery_pending" if edge.trust_ceiling is not None else "reply"
    context = DecisionContext(
        mode=mode,
        social_classification=social_classification,
        social_outcome=social_outcome,
        required_response_kind=required_kind,
        player_input=player_input,
        turn=session.turn,
        npc=context_npc,
        target_npc_id=npc.id,
        available_facts=tuple(
            f"{fact_id}: {FACT_REGISTRY[fact_id].statement}"
            for fact_id in fact_ids
            if fact_id in FACT_REGISTRY
        ),
        available_evidence_ids=tuple(session.evidences),
        recent_events=decision_recent_events(session, mode, npc),
        visible_evidences=tuple(session.evidences[eid].model_copy(deep=True) for eid in sorted(visible_ids)),
        available_npcs=tuple(f"{item.id}: {item.name} ({item.role})" for item in session.npcs.values()),
        incident_rules=tuple(INCIDENT_RULES),
        question_type=question_type,
        reference_scope=reference_scope,
        discovered_evidence_ids=tuple(sorted(session.discovered_evidence)),
        referenced_evidence_id=referenced_evidence.id if referenced_evidence is not None else None,
        referenced_evidence_title=referenced_evidence.title if referenced_evidence is not None else None,
        referenced_evidence_summary=referenced_evidence.summary if referenced_evidence is not None else None,
        referenced_evidence_content=referenced_evidence.content if referenced_evidence is not None else None,
        responsibility_map=tuple(
            f"{fact_id}: {FACT_REGISTRY[fact_id].statement}"
            for fact_id in RESPONSIBILITY_FACT_IDS
        ),
    )
    return context


def decision_recent_events(session: GameSession, mode: str, npc: NPCState) -> tuple[str, ...]:
    visible = visible_evidence_ids(session, npc)
    events = []
    for event in session.events[-16:]:
        if event.recipient_npc_id not in {None, npc.id}:
            continue
        if event.actor_id not in {None, npc.id}:
            continue
        if event.event_type == "fallback":
            continue
        if event.event_type == "evidence" and evidence_id_from_event(session, event) not in visible:
            continue
        events.append(f"TURN {event.turn} · {event.actor}: {event.message}")
    return tuple(events[-8:])


def contains_evidence_leak(
    session: GameSession,
    dialogue: str,
    allowed_evidence_ids: set[str] | None = None,
) -> bool:
    allowed_evidence_ids = allowed_evidence_ids or set()
    normalized = dialogue.casefold()
    for evidence_id, evidence in session.evidences.items():
        if evidence_id in allowed_evidence_ids:
            continue
        if evidence.content and len(evidence.content) >= 24:
            content_prefix = evidence.content[:24].casefold()
            if content_prefix in normalized:
                return True
    return False


def contains_known_fact_contradiction(npc: NPCState, dialogue: str) -> bool:
    """Reject evidence reactions that explicitly deny canonical NPC facts."""

    normalized = dialogue.casefold()
    for fact_id in npc.known_fact_ids:
        patterns = KNOWN_FACT_CONTRADICTION_PATTERNS.get(fact_id, ())
        if any(re.search(pattern, normalized) for pattern in patterns):
            return True
    return False


def validate_decision(session: GameSession, npc: NPCState, decision: AgentDecision) -> list[GuardrailCheck]:
    action_targets = set(session.npcs) | {None}
    if decision.action_type == "show_evidence":
        action_targets = {eid for eid in session.evidences if can_provide_evidence(session, npc, eid)}
    elif decision.action_target in session.evidences:
        action_targets |= visible_evidence_ids(session, npc)
    belief_subjects = set(session.npcs) | {"player", "incident"}
    return [
        GuardrailCheck(name="evidence_refs_visible", passed=set(decision.evidence_refs).issubset(visible_evidence_ids(session, npc)),
                       detail="Evidence claims only cite documents visible to this NPC and player."),
        GuardrailCheck(name="contact_npcs_available", passed=all(target in session.npcs for target in decision.contact_npc_ids),
                       detail="Suggested contacts are actual available NPCs."),
        GuardrailCheck(
            name="npc_exists",
            passed=decision.npc_id == npc.id and npc.id in session.npcs,
            detail="NPC exists in the current session.",
        ),
        GuardrailCheck(
            name="evidence_exists",
            passed=decision.action_target in action_targets,
            detail="Action target is a known NPC, evidence, or empty target.",
        ),
        GuardrailCheck(
            name="action_type_allowed",
            passed=decision.action_type in ALLOWED_AGENT_ACTION_TYPES,
            detail="Decision action type is in the server-owned action vocabulary.",
        ),
        GuardrailCheck(
            name="belief_subjects_valid",
            passed=all(belief.subject in belief_subjects for belief in decision.belief_updates),
            detail="Belief updates reference a known NPC, player, or incident.",
        ),
        GuardrailCheck(
            name="relationship_targets_valid",
            passed=all(
                update.target_npc_id in session.npcs
                and update.target_npc_id != npc.id
                and relationship_key(npc.id, update.target_npc_id) in session.relationships
                for update in decision.relationship_updates
            ) and len({update.target_npc_id for update in decision.relationship_updates}) == len(decision.relationship_updates),
            detail="Relationship updates reference NPCs in the current session.",
        ),
        GuardrailCheck(
            name="knowledge_refs_exist",
            passed=all(fact_id in FACT_REGISTRY for fact_id in decision.knowledge_refs),
            detail="Knowledge references exist in the server-owned Fact Registry.",
        ),
        GuardrailCheck(
            name="knowledge_refs_present",
            passed=decision.grounding_type != "fact" or bool(decision.knowledge_refs),
            detail="Fact-grounded dialogue includes a reference; belief and acknowledgement may omit it.",
        ),
        GuardrailCheck(
            name="knowledge_refs_known_by_npc",
            passed=all(fact_id in available_fact_ids(session, npc) for fact_id in decision.knowledge_refs),
            detail="Knowledge references are inside the NPC knowledge boundary.",
        ),
        GuardrailCheck(
            name="knowledge_refs_evidence_valid",
            passed=all(
                FACT_REGISTRY[fact_id].revealable
                and all(evidence_id in session.evidences for evidence_id in FACT_REGISTRY[fact_id].source_evidence_ids)
                for fact_id in decision.knowledge_refs
                if fact_id in FACT_REGISTRY
            ),
            detail="Knowledge references are revealable and their evidence exists in the current world state.",
        ),
        GuardrailCheck(
            name="state_ranges_valid",
            passed=all(-100 <= value <= 100 for value in (decision.trust_delta, decision.stress_delta, decision.cooperation_delta)),
            detail="Decision deltas are within the allowed range.",
        ),
    ]
