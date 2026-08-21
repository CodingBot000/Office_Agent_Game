from copy import deepcopy

from app.models import (
    Belief,
    DynamicState,
    Evidence,
    FactDefinition,
    NPCState,
    Personality,
    Relationship,
    RelationshipState,
    WorldObjectDefinition,
    WorldObjectState,
)


FACT_DEFINITIONS = [
    FactDefinition(id="pm_moved_release_date", statement="PM moved the release date one day earlier.", category="canonical", source_evidence_ids=["release_timeline"]),
    FactDefinition(id="team_lead_required_release_today", statement="The team lead required deployment today.", category="canonical", source_evidence_ids=["release_timeline"]),
    FactDefinition(id="business_requested_shorter_schedule", statement="Business stakeholders asked for a shorter schedule.", category="canonical", source_evidence_ids=["release_timeline"]),
    FactDefinition(id="backend_changed_api_schema", statement="Backend Developer changed the API response schema.", category="canonical", source_evidence_ids=["api_schema_diff"]),
    FactDefinition(id="backend_executed_deployment", statement="Backend Developer deployed the release.", category="canonical", source_evidence_ids=["release_timeline"]),
    FactDefinition(id="backend_knew_deploy_risk", statement="Backend Developer knew there was deployment risk.", category="canonical", source_evidence_ids=["qa_warning_message"]),
    FactDefinition(id="frontend_received_change_late", statement="Frontend Developer received the API change late.", category="canonical", source_evidence_ids=["api_schema_diff"]),
    FactDefinition(id="frontend_local_check_passed", statement="Frontend Developer's last local check passed.", category="evidence"),
    FactDefinition(id="qa_found_critical_issue", statement="QA found a critical issue before deployment.", category="evidence", source_evidence_ids=["qa_warning_message"]),
    FactDefinition(id="qa_sent_warning", statement="QA sent a warning message before deployment.", category="evidence", source_evidence_ids=["qa_warning_message"]),
    FactDefinition(id="warning_recommended_deploy_block", statement="The QA warning recommended blocking deployment.", category="evidence", source_evidence_ids=["qa_warning_message"]),
    FactDefinition(id="qa_has_no_deploy_permission", statement="QA does not have deployment permission.", category="canonical"),
    FactDefinition(id="team_lead_did_not_confirm_warning", statement="The team lead did not confirm the QA warning before deployment.", category="canonical", source_evidence_ids=["qa_warning_message"]),
    FactDefinition(id="frontend_requests_failed_after_deploy", statement="Frontend requests failed after deployment.", category="canonical", source_evidence_ids=["api_schema_diff"]),
    FactDefinition(id="outage_caused_by_schema_mismatch", statement="The outage was caused by an API schema mismatch.", category="canonical", source_evidence_ids=["api_schema_diff"]),
]

FACT_REGISTRY = {fact.id: fact for fact in FACT_DEFINITIONS}
CANONICAL_TRUTH = [fact.statement for fact in FACT_DEFINITIONS if fact.category == "canonical"]

LEGACY_FACT_TEXT_TO_ID = {
    "I deployed the release.": "backend_executed_deployment",
    "I changed the API response schema.": "backend_changed_api_schema",
    "The schedule was shortened.": "business_requested_shorter_schedule",
    "QA mentioned a risk before deployment.": "backend_knew_deploy_risk",
    "The API change was shared late.": "frontend_received_change_late",
    "My code passed the last local check.": "frontend_local_check_passed",
    "I found a critical issue before deployment.": "qa_found_critical_issue",
    "I sent a warning message.": "qa_sent_warning",
    "I do not have deployment permission.": "qa_has_no_deploy_permission",
    "The release date moved one day earlier.": "pm_moved_release_date",
    "Business stakeholders asked for a shorter schedule.": "business_requested_shorter_schedule",
}

INCIDENT_RULES = [
    "A critical QA issue must block production deployment until it is resolved.",
    "Production deployment requires confirmation from the responsible developer and team lead.",
]

NPC_HOME_LOCATIONS = {
    "backend_01": "dev_area",
    "frontend_01": "dev_area",
    "qa_01": "qa_desk",
    "pm_01": "pm_desk",
}

WORLD_OBJECT_DEFINITIONS = [
    WorldObjectDefinition(id="backend_keyboard", name="Backend keyboard", owner_id="backend_01", location="dev_area"),
    WorldObjectDefinition(id="frontend_keyboard", name="Frontend keyboard", owner_id="frontend_01", location="dev_area"),
    WorldObjectDefinition(id="qa_keyboard", name="QA keyboard", owner_id="qa_01", location="qa_desk"),
    WorldObjectDefinition(id="pm_keyboard", name="PM keyboard", owner_id="pm_01", location="pm_desk"),
    WorldObjectDefinition(
        id="meeting_room_monitor",
        name="Meeting room monitor",
        location="meeting_room",
        portable=False,
    ),
    WorldObjectDefinition(id="release_document", name="Release document", owner_id="pm_01", location="pm_desk"),
    WorldObjectDefinition(id="qa_warning_printout", name="QA warning printout", owner_id="qa_01", location="qa_desk"),
]

WORLD_OBJECT_REGISTRY = {item.id: item for item in WORLD_OBJECT_DEFINITIONS}


def relationship_key(source_id: str, target_id: str) -> str:
    return f"{source_id}->{target_id}"


def fact_statements(fact_ids: list[str]) -> list[str]:
    return [FACT_REGISTRY[fact_id].statement for fact_id in fact_ids]


def build_initial_npcs() -> dict[str, NPCState]:
    backend_fact_ids = [
        "backend_executed_deployment",
        "backend_changed_api_schema",
        "business_requested_shorter_schedule",
        "backend_knew_deploy_risk",
    ]
    frontend_fact_ids = ["frontend_received_change_late", "frontend_local_check_passed"]
    qa_fact_ids = [
        "qa_found_critical_issue",
        "qa_sent_warning",
        "warning_recommended_deploy_block",
        "qa_has_no_deploy_permission",
    ]
    pm_fact_ids = ["pm_moved_release_date", "business_requested_shorter_schedule"]
    return {
        "backend_01": NPCState(
            id="backend_01",
            name="Backend Developer",
            role="Backend Developer",
            personality=Personality(assertiveness=72, cooperativeness=48, risk_aversion=35, blame_sensitivity=75),
            dynamic_state=DynamicState(emotion="tense", stress=45, trust_toward_player=10, cooperation=55),
            known_fact_ids=backend_fact_ids,
            known_facts=fact_statements(backend_fact_ids),
            beliefs=[Belief(subject="incident", belief="Schedule pressure is the root cause.", confidence=0.7)],
            relationships=[
                Relationship(target_npc_id="qa_01", trust=10, tension=45),
                Relationship(target_npc_id="frontend_01", trust=25, tension=30),
            ],
        ),
        "frontend_01": NPCState(
            id="frontend_01",
            name="Frontend Developer",
            role="Frontend Developer",
            personality=Personality(assertiveness=45, cooperativeness=70, risk_aversion=65, blame_sensitivity=45),
            dynamic_state=DynamicState(emotion="worried", stress=35, trust_toward_player=20, cooperation=70),
            known_fact_ids=frontend_fact_ids,
            known_facts=fact_statements(frontend_fact_ids),
            beliefs=[Belief(subject="incident", belief="The change-sharing process failed.", confidence=0.8)],
            relationships=[Relationship(target_npc_id="backend_01", trust=15, tension=40)],
        ),
        "qa_01": NPCState(
            id="qa_01",
            name="QA Engineer",
            role="QA Engineer",
            personality=Personality(assertiveness=65, cooperativeness=60, risk_aversion=90, blame_sensitivity=80),
            dynamic_state=DynamicState(emotion="guarded", stress=55, trust_toward_player=15, cooperation=65),
            known_fact_ids=qa_fact_ids,
            known_facts=fact_statements(qa_fact_ids),
            beliefs=[Belief(subject="incident", belief="The warning was ignored before deployment.", confidence=0.85)],
            relationships=[Relationship(target_npc_id="backend_01", trust=0, tension=60)],
        ),
        "pm_01": NPCState(
            id="pm_01",
            name="PM / Planner",
            role="PM / Planner",
            personality=Personality(assertiveness=68, cooperativeness=55, risk_aversion=40, blame_sensitivity=60),
            dynamic_state=DynamicState(emotion="urgent", stress=50, trust_toward_player=5, cooperation=60),
            known_fact_ids=pm_fact_ids,
            known_facts=fact_statements(pm_fact_ids),
            beliefs=[Belief(subject="incident", belief="The technical team did not protect deployment quality.", confidence=0.65)],
        ),
    }


def build_initial_evidence() -> dict[str, Evidence]:
    return {
        "qa_warning_message": Evidence(
            id="qa_warning_message",
            title="QA warning message",
            summary="Critical issue report sent 20 minutes before deployment.",
            content=(
                "[16:40] QA: Critical — API response mismatch found in production-like test. "
                "Recommend blocking deployment until the contract is verified."
            ),
            source_npc_id="qa_01",
        ),
        "api_schema_diff": Evidence(
            id="api_schema_diff",
            title="API schema diff",
            summary="The response field contract changed in the release branch.",
            content=(
                "response.data.items changed to response.payload.items. "
                "Frontend consumer update was not included in the same release."
            ),
            source_npc_id="backend_01",
        ),
        "release_timeline": Evidence(
            id="release_timeline",
            title="Release timeline",
            summary="Schedule change and deployment milestones.",
            content=(
                "Release moved one day earlier. QA warning at 16:40. "
                "Production deployment began at 17:00."
            ),
            source_npc_id="pm_01",
        ),
    }


def build_relationship_graph(npcs: dict[str, NPCState]) -> dict[str, RelationshipState]:
    graph: dict[str, RelationshipState] = {}
    entity_ids = ["player", *npcs]
    legacy_by_source = {
        npc.id: {relationship.target_npc_id: relationship for relationship in npc.relationships}
        for npc in npcs.values()
    }
    for source_id in entity_ids:
        for target_id in entity_ids:
            if source_id == target_id:
                continue
            legacy = legacy_by_source.get(source_id, {}).get(target_id)
            if source_id in npcs and target_id == "player":
                trust = npcs[source_id].dynamic_state.trust_toward_player
                tension = 0
            elif legacy is not None:
                trust = legacy.trust
                tension = legacy.tension
            else:
                trust = 0
                tension = 0
            graph[relationship_key(source_id, target_id)] = RelationshipState(
                source_id=source_id,
                target_id=target_id,
                trust=trust,
                tension=tension,
                respect=max(-100, min(100, trust // 2)),
                fear=0,
                grievance=min(100, tension // 3),
            )
    return graph


def build_initial_world_objects() -> dict[str, WorldObjectState]:
    return {
        definition.id: WorldObjectState(**definition.model_dump())
        for definition in WORLD_OBJECT_DEFINITIONS
    }


def clone_npcs() -> dict[str, NPCState]:
    return deepcopy(build_initial_npcs())


def clone_evidence() -> dict[str, Evidence]:
    return deepcopy(build_initial_evidence())


def clone_relationships() -> dict[str, RelationshipState]:
    return deepcopy(build_relationship_graph(build_initial_npcs()))


def clone_world_objects() -> dict[str, WorldObjectState]:
    return deepcopy(build_initial_world_objects())
