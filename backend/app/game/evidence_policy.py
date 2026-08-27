"""Evidence ownership, observation and provenance are separate concerns."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.game.seed import EVIDENCE_DISCLOSED_FACT_IDS, FACT_REGISTRY
from app.models import EventLogEntry, NPCState

if TYPE_CHECKING:
    from app.game.engine import GameSession


def can_provide_evidence(session: GameSession, npc: NPCState, evidence_id: str) -> bool:
    evidence = session.evidences.get(evidence_id)
    return evidence is not None and (evidence.source_npc_id == npc.id or evidence_id in npc.observed_evidence_ids)


def visible_evidence_ids(session: GameSession, npc: NPCState) -> set[str]:
    return {evidence_id for evidence_id in session.discovered_evidence if can_provide_evidence(session, npc, evidence_id)}


def observe_evidence(session: GameSession, npc: NPCState, evidence_id: str) -> None:
    if evidence_id not in session.discovered_evidence or evidence_id not in session.evidences:
        raise ValueError("Evidence must be discovered before it can be shared.")
    if evidence_id not in npc.observed_evidence_ids:
        npc.observed_evidence_ids.append(evidence_id)


def available_fact_ids(session: GameSession, npc: NPCState) -> list[str]:
    observed_facts = [
        fact_id for evidence_id in npc.observed_evidence_ids
        if evidence_id in session.evidences
        for fact_id in EVIDENCE_DISCLOSED_FACT_IDS.get(evidence_id, ())
        if fact_id in FACT_REGISTRY and FACT_REGISTRY[fact_id].revealable
    ]
    return list(dict.fromkeys([*npc.known_fact_ids, *observed_facts]))


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
