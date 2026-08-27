"""Evidence ownership, observation and provenance are separate concerns."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.game.seed import EVIDENCE_DISCLOSED_FACT_IDS, FACT_REGISTRY
from app.models import Belief, Evidence, EventLogEntry, Memory, NPCState

if TYPE_CHECKING:
    from app.game.session import GameSession


def can_provide_evidence(session: GameSession, npc: NPCState, evidence_id: str) -> bool:
    evidence = session.evidences.get(evidence_id)
    return evidence is not None and (evidence.source_npc_id == npc.id or evidence_id in npc.observed_evidence_ids)


def shareable_evidence_ids(session: GameSession, npc: NPCState) -> set[str]:
    """Documents this NPC can provide, independent of the player's inventory."""
    return {evidence_id for evidence_id in session.evidences if can_provide_evidence(session, npc, evidence_id)}


def visible_evidence_ids(session: GameSession, npc: NPCState) -> set[str]:
    return session.discovered_evidence & shareable_evidence_ids(session, npc)


def observe_evidence(session: GameSession, npc: NPCState, evidence_id: str) -> None:
    if evidence_id not in session.discovered_evidence or evidence_id not in session.evidences:
        raise ValueError("Evidence must be discovered before it can be shared.")
    if evidence_id not in npc.observed_evidence_ids:
        npc.observed_evidence_ids.append(evidence_id)


def available_fact_ids(session: GameSession, npc: NPCState) -> list[str]:
    document_facts = [
        fact_id for evidence_id in sorted(shareable_evidence_ids(session, npc))
        if evidence_id in session.evidences
        for fact_id in EVIDENCE_DISCLOSED_FACT_IDS.get(evidence_id, ())
        if fact_id in FACT_REGISTRY and FACT_REGISTRY[fact_id].revealable
    ]
    return list(dict.fromkeys([*npc.known_fact_ids, *document_facts]))


def evidence_id_from_event(session: GameSession, event: EventLogEntry) -> str | None:
    if event.evidence_id is not None:
        return event.evidence_id if event.evidence_id in session.evidences else None
    if event.event_type != "evidence" or event.evidence_operation is not None:
        return None
    # Read-only compatibility for pre-metadata events. New writes always use IDs.
    normalized = event.message.casefold()
    return next((eid for eid, evidence in session.evidences.items()
                 if eid.casefold() in normalized or evidence.title.casefold() in normalized), None)


def latest_evidence_id(session: GameSession) -> str | None:
    for event in reversed(session.events):
        evidence_id = evidence_id_from_event(session, event)
        if evidence_id in session.discovered_evidence:
            return evidence_id
    return next(iter(sorted(session.discovered_evidence)), None)


def presentation_count(session: GameSession, npc_id: str, evidence_id: str) -> int:
    return sum(event.evidence_operation == "presented" and event.recipient_npc_id == npc_id
               and event.evidence_id == evidence_id for event in session.events)


def evidence_presentation_policy(
    session: GameSession,
    npc: NPCState,
    evidence: Evidence,
    presentation_count: int,
) -> dict[str, object]:
    if presentation_count > 0:
        return {
            "reaction_type": "repeated_presentation",
            "emotion": npc.dynamic_state.emotion,
            "stress_delta": 0,
            "trust_delta": 0,
            "cooperation_delta": 0,
            "belief_updates": [],
            "memory_candidate": None,
            "fallback_dialogue": "이 증거는 이미 확인했습니다. 같은 내용을 다시 제시해도 판단은 달라지지 않습니다.",
        }

    if evidence.source_npc_id == npc.id:
        return {
            "reaction_type": "same_source_acknowledgement",
            "emotion": npc.dynamic_state.emotion,
            "stress_delta": 0,
            "trust_delta": 0,
            "cooperation_delta": 1,
            "belief_updates": [],
            "memory_candidate": Memory(
                summary=f"Player asked {npc.name} to confirm the evidence they provided.",
                importance=0.55,
                turn=session.turn,
            ),
            "fallback_dialogue": "이 메시지는 제가 보낸 경고입니다. 이미 알고 있는 내용이니, 어떻게 처리됐는지 확인해 주세요.",
        }

    if npc.id == "backend_01" and evidence.id == "qa_warning_message":
        belief = Belief(
            subject="incident",
            belief="The ignored QA warning and API schema change jointly enabled the outage.",
            confidence=0.85,
        )
        return {
            "reaction_type": "accountability_pressure",
            "emotion": "uneasy",
            "stress_delta": 8,
            "trust_delta": 3,
            "cooperation_delta": 1,
            "belief_updates": [belief],
            "memory_candidate": Memory(
                summary="Player presented the QA warning message during the incident review.",
                importance=0.7,
                turn=session.turn,
            ),
            "fallback_dialogue": "QA 경고가 있었던 것은 확인했습니다. 제가 API 응답 스키마를 변경한 상태에서 배포를 진행했고, 당시 판단 과정을 다시 검토하겠습니다.",
        }

    if npc.id == "frontend_01":
        return {
            "reaction_type": "cross_role_review",
            "emotion": "focused",
            "stress_delta": 3,
            "trust_delta": 1,
            "cooperation_delta": 3,
            "belief_updates": [],
            "memory_candidate": Memory(
                summary="Player presented QA evidence for cross-role API review.",
                importance=0.65,
                turn=session.turn,
            ),
            "fallback_dialogue": "QA 경고와 API 변경 내용을 함께 확인해 보겠습니다. 프론트엔드 반영 시점도 다시 점검하겠습니다.",
        }

    if npc.id == "pm_01":
        return {
            "reaction_type": "accountability_pressure",
            "emotion": "concerned",
            "stress_delta": 4,
            "trust_delta": 1,
            "cooperation_delta": 2,
            "belief_updates": [],
            "memory_candidate": Memory(
                summary="Player presented QA evidence about the deployment decision.",
                importance=0.65,
                turn=session.turn,
            ),
            "fallback_dialogue": "배포 전에 이런 경고가 있었다면 일정과 승인 과정에서 검토했어야 합니다.",
        }

    return {
        "reaction_type": "cross_role_review",
        "emotion": "uneasy",
        "stress_delta": 2,
        "trust_delta": 1,
        "cooperation_delta": 1,
        "belief_updates": [],
        "memory_candidate": Memory(
            summary=f"Player presented {evidence.title} during the incident review.",
            importance=0.6,
            turn=session.turn,
        ),
        "fallback_dialogue": "제시된 증거를 확인했습니다. 이 내용이 어떻게 처리됐는지 함께 확인해 보겠습니다.",
    }
