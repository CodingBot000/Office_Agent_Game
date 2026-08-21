from app.main import app


def test_openapi_contains_unity_client_contract() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/v1/sessions" in paths
    assert "/api/v1/sessions/{session_id}" in paths
    assert "/api/v1/sessions/{session_id}/actions" in paths
    assert "/api/v1/sessions/{session_id}/game-actions" in paths
    assert "/api/v1/sessions/{session_id}/report" in paths
    assert "/api/v1/sessions/{session_id}/reset" in paths

    action_request = schema["components"]["schemas"]["ActionRequest"]
    assert {"text", "intent_hint", "target_hint"}.issubset(action_request["properties"])

    snapshot = schema["components"]["schemas"]["GameSnapshot"]
    required = set(snapshot["required"])
    assert {
        "session_id",
        "turn",
        "current_location",
        "npcs",
        "relationships",
        "world_objects",
        "available_game_actions",
        "player_inventory",
        "game_action_traces",
        "social_events",
        "dialogue_refused_npc_ids",
        "evidences",
        "events",
        "agent_traces",
        "fallback_notices",
    }.issubset(required)

    npc_state = schema["components"]["schemas"]["NPCState"]
    assert "known_fact_ids" in npc_state["properties"]

    agent_decision = schema["components"]["schemas"]["AgentDecision"]
    assert {"grounding_type", "knowledge_refs"}.issubset(agent_decision["properties"])

    agent_trace = schema["components"]["schemas"]["AgentTrace"]
    assert {"known_fact_ids", "requested_decision"}.issubset(agent_trace["properties"])

    relationship = schema["components"]["schemas"]["RelationshipState"]
    assert {
        "source_id",
        "target_id",
        "trust",
        "tension",
        "respect",
        "fear",
        "grievance",
        "repair_stage",
        "trust_ceiling",
        "fear_floor",
    }.issubset(relationship["properties"])

    social_impact = schema["components"]["schemas"]["SocialImpactClassification"]
    assert {"action_family", "direct_target_ids", "object_id", "severity", "reason_codes"}.issubset(
        social_impact["properties"]
    )

    social_trace = schema["components"]["schemas"]["SocialEventTrace"]
    assert {"classification", "requested_classification", "policy_outcome", "guardrails", "fallback_used"}.issubset(
        social_trace["properties"]
    )

    action_response = schema["components"]["schemas"]["ActionResponse"]
    assert {"social_impact_provider", "social_impact_fallback_used"}.issubset(action_response["properties"])
    assert {"blocked", "alert"}.issubset(action_response["properties"])

    intent = schema["components"]["schemas"]["IntentClassification"]
    assert {"interaction_kind", "game_action_family"}.issubset(intent["properties"])

    world_object = schema["components"]["schemas"]["WorldObjectState"]
    assert "holder_id" in world_object["properties"]

    game_action_request = schema["components"]["schemas"]["GameActionRequest"]
    assert "action_id" in game_action_request["properties"]

    game_action_trace = schema["components"]["schemas"]["GameActionTrace"]
    assert {"action_id", "holder_before", "holder_after", "condition_before", "condition_after"}.issubset(
        game_action_trace["properties"]
    )
