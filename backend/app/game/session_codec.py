"""Versioned serialization and backwards-compatible session upgrades."""
from __future__ import annotations
import logging
from app.game.session import GameSession
from app.game.seed import (CANONICAL_TRUTH, FACT_REGISTRY, LEGACY_FACT_TEXT_TO_ID, STARTER_ITEM_IDS,
                           build_initial_world_objects, build_relationship_graph)
from app.game.action_registry import build_player_inventory
from app.models import (NPCState, RelationshipState, WorldObjectState, GameActionTrace, SocialEventTrace,
                        Evidence, EventLogEntry, AgentTrace, FallbackNotice, GameResult, IncidentReportRequest, ReportExtraction)

logger = logging.getLogger(__name__)
CURRENT_SESSION_SCHEMA_VERSION = 10


def serialize_session(session: GameSession) -> dict[str, object]:
    return {
        "schema_version": CURRENT_SESSION_SCHEMA_VERSION,
        "session_id": session.session_id,
        "turn": session.turn,
        "current_location": session.current_location,
        "incident_status": session.incident_status,
        "objective": session.objective,
        "npcs": {npc_id: npc.model_dump(mode="json") for npc_id, npc in session.npcs.items()},
        "relationships": {
            relationship_id: relationship.model_dump(mode="json")
            for relationship_id, relationship in session.relationships.items()
        },
        "world_objects": {
            object_id: world_object.model_dump(mode="json")
            for object_id, world_object in session.world_objects.items()
        },
        "player_inventory": build_player_inventory(session).model_dump(mode="json"),
        "game_action_traces": [trace.model_dump(mode="json") for trace in session.game_action_traces],
        "social_events": [event.model_dump(mode="json") for event in session.social_events],
        "dialogue_refused_npc_ids": sorted(session.dialogue_refused_npc_ids),
        "evidences": {evidence_id: evidence.model_dump(mode="json") for evidence_id, evidence in session.evidences.items()},
        "events": [event.model_dump(mode="json") for event in session.events],
        "agent_traces": [trace.model_dump(mode="json") for trace in session.agent_traces],
        "fallback_notices": [notice.model_dump(mode="json") for notice in session.fallback_notices],
        "discovered_evidence": sorted(session.discovered_evidence),
        "canonical_truth": session.canonical_truth,
        "completed": session.completed,
        "result": session.result.model_dump(mode="json") if session.result else None,
        "report": session.report.model_dump(mode="json") if session.report else None,
        "report_extraction": session.report_extraction.model_dump(mode="json") if session.report_extraction else None,
    }


def migrate_session_payload(payload: dict[str, object]) -> tuple[dict[str, object], bool]:
    version = int(payload.get("schema_version", 1))
    if version > CURRENT_SESSION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported future session schema version: {version}")
    if version == CURRENT_SESSION_SCHEMA_VERSION:
        return payload, False

    npc_payload = dict(payload.get("npcs", {}))
    for npc_id, raw_npc in npc_payload.items():
        if not isinstance(raw_npc, dict):
            continue
        raw_npc.setdefault("physical_state", "comatose" if raw_npc.get("is_fallen", False) else "normal")
        raw_npc.setdefault("is_fallen", raw_npc.get("physical_state") == "comatose")
        known_fact_ids = [str(item) for item in raw_npc.get("known_fact_ids", [])]
        if not known_fact_ids:
            for legacy_fact in raw_npc.get("known_facts", []):
                fact_id = LEGACY_FACT_TEXT_TO_ID.get(str(legacy_fact))
                if fact_id:
                    known_fact_ids.append(fact_id)
                else:
                    logger.warning(
                        "session_migration_unmapped_fact session_id=%s npc_id=%s fact=%s",
                        payload.get("session_id"),
                        npc_id,
                        str(legacy_fact)[:160],
                    )
        raw_npc["known_fact_ids"] = list(dict.fromkeys(known_fact_ids))
        raw_npc["known_facts"] = [
            FACT_REGISTRY[fact_id].statement
            for fact_id in raw_npc["known_fact_ids"]
            if fact_id in FACT_REGISTRY
        ]

    payload["npcs"] = npc_payload
    if version < 4:
        migrated_npcs = {
            str(npc_id): NPCState.model_validate(raw_npc)
            for npc_id, raw_npc in npc_payload.items()
            if isinstance(raw_npc, dict)
        }
        relationship_graph = build_relationship_graph(migrated_npcs)
        payload["relationships"] = {
            relationship_id: relationship.model_dump(mode="json")
            for relationship_id, relationship in relationship_graph.items()
        }
        payload["world_objects"] = {
            object_id: world_object.model_dump(mode="json")
            for object_id, world_object in build_initial_world_objects().items()
        }
        payload["social_events"] = []
        payload["player_inventory"] = {"held_object_ids": [], "max_held_objects": 1}
        payload["game_action_traces"] = []
        payload["dialogue_refused_npc_ids"] = []
    if version < 8:
        raw_world_objects = dict(payload.get("world_objects", {}))
        starter_objects = build_initial_world_objects()
        for item_id in STARTER_ITEM_IDS:
            if item_id not in raw_world_objects and item_id in starter_objects:
                raw_world_objects[item_id] = starter_objects[item_id].model_dump(mode="json")
        payload["world_objects"] = raw_world_objects
    # Repair legacy projections and violations from the pre-unified transition paths.
    for raw_edge in dict(payload.get("relationships", {})).values():
        if not isinstance(raw_edge, dict):
            continue
        ceiling = raw_edge.get("trust_ceiling")
        if ceiling is not None:
            raw_edge["trust"] = min(int(raw_edge.get("trust", 0)), int(ceiling))
        raw_edge["fear"] = max(int(raw_edge.get("fear", 0)), int(raw_edge.get("fear_floor", 0)))
        raw_npc = npc_payload.get(raw_edge.get("source_id"))
        if isinstance(raw_npc, dict) and raw_edge.get("target_id") == "player":
            raw_npc.setdefault("dynamic_state", {})["trust_toward_player"] = raw_edge.get("trust", 0)
    payload["schema_version"] = CURRENT_SESSION_SCHEMA_VERSION
    return payload, True


def deserialize_session(payload: dict[str, object]) -> GameSession:
    npc_payload = payload.get("npcs", {})
    evidence_payload = payload.get("evidences", {})
    relationship_payload = payload.get("relationships", {})
    world_object_payload = payload.get("world_objects", {})
    return GameSession(
        session_id=str(payload["session_id"]),
        revision=int(payload.get("_revision", 0)),
        turn=int(payload.get("turn", 0)),
        current_location=str(payload.get("current_location", "meeting_room")),
        incident_status=str(payload.get("incident_status", "ACTIVE")),
        objective=[str(item) for item in payload.get("objective", [])],
        npcs={str(npc_id): NPCState.model_validate(npc) for npc_id, npc in dict(npc_payload).items()},
        relationships={
            str(relationship_id): RelationshipState.model_validate(relationship)
            for relationship_id, relationship in dict(relationship_payload).items()
        },
        world_objects={
            str(object_id): WorldObjectState.model_validate(world_object)
            for object_id, world_object in dict(world_object_payload).items()
        },
        game_action_traces=[GameActionTrace.model_validate(trace) for trace in payload.get("game_action_traces", [])],
        social_events=[SocialEventTrace.model_validate(event) for event in payload.get("social_events", [])],
        dialogue_refused_npc_ids={str(item) for item in payload.get("dialogue_refused_npc_ids", [])},
        evidences={
            str(evidence_id): Evidence.model_validate(evidence)
            for evidence_id, evidence in dict(evidence_payload).items()
        },
        events=[EventLogEntry.model_validate(event) for event in payload.get("events", [])],
        agent_traces=[AgentTrace.model_validate(trace) for trace in payload.get("agent_traces", [])],
        fallback_notices=[FallbackNotice.model_validate(notice) for notice in payload.get("fallback_notices", [])],
        discovered_evidence={str(item) for item in payload.get("discovered_evidence", [])},
        canonical_truth=[str(item) for item in payload.get("canonical_truth", CANONICAL_TRUTH)],
        completed=bool(payload.get("completed", False)),
        result=GameResult.model_validate(payload["result"]) if payload.get("result") else None,
        report=IncidentReportRequest.model_validate(payload["report"]) if payload.get("report") else None,
        report_extraction=ReportExtraction.model_validate(payload["report_extraction"]) if payload.get("report_extraction") else None,
    )
